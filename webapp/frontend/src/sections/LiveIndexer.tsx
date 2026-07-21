import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, m } from 'framer-motion'
import { api, usePoll, type LiveDispute, type LiveDisputes } from '../api/client'
import { useLiveStatus, freshnessFromAge } from '../components/LiveStatus'
import { useColors } from '../components/Theme'
import type { Colors } from '../lib/theme'
import { ago, short } from '../lib/format'
import { Caveat, CopyButton, ErrorBox, Panel, Section, Stat } from '../components/ui'

const outcomeColor = (C: Colors, o?: string): string =>
  (({ YES: C.profit, NO: C.loss, UNRESOLVABLE: C.warn, OTHER: C.muted } as Record<string, string>)[o || ''] || C.muted)
const POLL_MS = 5000

export function LiveIndexer() {
  const { C } = useColors()
  const live = useLiveStatus()                                   // shared /live/status poller
  const [feed, setFeed] = useState<LiveDisputes | null>(null)   // last GOOD feed (polled here)
  const [now, setNow] = useState(Date.now())
  const [fresh, setFresh] = useState<Set<string>>(new Set())
  const [fails, setFails] = useState(0)
  const seen = useRef<Set<string>>(new Set())
  const tickRef = useRef<() => void>()

  const aliveRef = useRef(true)
  useEffect(() => {
    aliveRef.current = true
    const clock = setInterval(() => aliveRef.current && setNow(Date.now()), 1000)
    return () => { aliveRef.current = false; clearInterval(clock) }
  }, [])

  // only the disputes feed is polled here now; reachability/latency/head come from the shared
  // LiveStatus context (one status poller for the whole app). Last good feed is kept through blips;
  // usePoll backs the interval off while the backend is unreachable (e.g. a host cold start).
  const tick = async (): Promise<boolean> => {
    try {
      const f = await api.liveDisputes(50)
      if (!aliveRef.current) return true
      if (f.reachable) {
        setFeed(f); setFails(0)
        const incoming = f.disputes.map((d) => d.id)
        const newly = incoming.filter((id) => seen.current.size > 0 && !seen.current.has(id))
        incoming.forEach((id) => seen.current.add(id))
        if (newly.length) { setFresh(new Set(newly)); setTimeout(() => aliveRef.current && setFresh(new Set()), 2500) }
        return true
      }
      setFails((p) => p + 1)
      return false
    } catch {
      if (aliveRef.current) setFails((p) => p + 1)
      return false
    }
  }
  tickRef.current = tick
  usePoll(tick, POLL_MS, 1200)

  const everConnected = live.up || (feed?.reachable ?? false)
  const connecting = (live.connecting && !feed) || (!everConnected && fails < 2)
  const up = everConnected && fails < 3                // reachable (sticky: tolerate transient blips)
  const latency = live.latency ?? feed?.latency_ms
  const subSecond = latency != null && latency < 1000
  const status = { head_ts: live.headTs, head_id: live.headId, endpoint: live.endpoint, error: live.error }
  // freshness gates the LIVE badge on the SERVER head age (chain-head age for the keyless-RPC source,
  // proving we're at tip), not just reachability. For RPC, `head_ts` is the latest DISPUTE (shown
  // separately) which is allowed to lag when disputes are quiet.
  const f = freshnessFromAge(live.headAgeSeconds)
  const headFresh = up && f.state === 'live'
  const isRpc = live.source === 'rpc'

  return (
    <Section id="live" kicker={isRpc ? 'keyless Polygon RPC · eth_getLogs' : 'hosted Envio HyperIndex · GraphQL'}
      title="Live dispute stream"
      subtitle="OOv2 DisputePrice events straight from Polygon — by default a keyless public-RPC log scan (no indexer, no paid service), or a hosted Envio indexer when one is configured. The LIVE badge reflects CHAIN-HEAD freshness (we're at tip); disputes are sparse, so the latest one can be days old while the feed is live."
      right={
        <span className={`chip ${headFresh ? 'border-sig/40 text-sig' : connecting ? '' : up ? 'border-warn/50 text-warn' : 'border-warn/50 text-warn'}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${headFresh || connecting ? 'animate-pulse2' : ''}`} style={{ background: headFresh ? C.sig : connecting ? C.muted : C.warn }} />
          {headFresh ? 'LIVE'
            : connecting ? 'connecting…'
            : up && f.state === 'syncing' ? `syncing · ${f.behind}`
            : up && f.state === 'stale' ? `stale · ${f.behind}`
            : up ? 'reachable'
            : 'indexer offline'}
        </span>
      }>
      {!connecting && !up && (
        <ErrorBox error={`Indexer unreachable — the live feed is optional; the rest of the dashboard runs off the shipped snapshot. ${status?.error || ''}`}
          onRetry={() => { tickRef.current?.(); live.refresh() }} />
      )}

      {up && !headFresh && (
        <div className="mb-4">
          <Caveat kind="underpowered">
            The source is reachable but its head is <span className="font-mono">{f.behind}</span> the chain tip
            {isRpc ? ' — the RPC scan has not reached head yet' : ' — this indexer has stopped advancing'}. The
            disputes below are real but may not be current.
          </Caveat>
        </div>
      )}

      {up && (
        <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
          {/* status rail */}
          <div className="space-y-3 self-start">
            <Stat label="Query latency" value={latency != null ? `${latency.toFixed(0)} ms` : '—'}
              tone={subSecond ? 'profit' : 'warn'} accent={subSecond}
              sub={subSecond ? 'sub-second round-trip' : 'round-trip to the indexer'} />
            <Stat label="Latest dispute" value={ago(status?.head_ts, now)} sub={headFresh ? 'source at chain head' : 'newest OOv2 dispute'} />
            <Panel className="!p-3">
              <div className="label mb-1">source · {isRpc ? 'keyless RPC' : 'Envio GraphQL'}</div>
              <div className="break-all font-mono text-2xs text-ink-2">{prettyEndpoint(status?.endpoint || feed?.endpoint || '')}</div>
              {status?.head_id && (
                <div className="mt-1.5 flex items-center gap-1.5 text-2xs text-muted">
                  <span>head</span><span className="num truncate text-ink-2" title={status.head_id}>{short(status.head_id, 8, 6)}</span>
                  <CopyButton value={status.head_id} label="Copy head id" />
                </div>
              )}
              <div className="mt-2 flex items-center gap-1.5 text-2xs text-muted">
                <span className="h-1.5 w-1.5 animate-pulse2 rounded-full bg-sig" /> polling every 5s · {isRpc ? 'eth_getLogs' : 'GraphQL'}
              </div>
            </Panel>
          </div>

          {/* feed */}
          <Panel pad={false} className="overflow-hidden">
            <div className="flex items-center justify-between border-b border-line px-4 py-2 text-2xs text-muted">
              <span>latest disputes · newest first</span>
              <span className="num">{feed?.disputes.length ?? 0} shown</span>
            </div>
            <div className="max-h-[420px] divide-y divide-line/50 overflow-y-auto">
              <AnimatePresence initial={false}>
                {(feed?.disputes ?? []).map((d) => <Row key={d.id} d={d} now={now} fresh={fresh.has(d.id)} />)}
              </AnimatePresence>
              {feed && feed.disputes.length === 0 && (
                feed.warming
                  ? <div className="flex items-center gap-2 p-6 text-sm text-muted"><span className="h-2 w-2 animate-pulse2 rounded-full bg-sig" />scanning the chain for recent disputes…</div>
                  : <div className="p-6 text-sm text-muted">no disputes returned</div>
              )}
            </div>
          </Panel>
        </div>
      )}

      {connecting && <Panel><div className="flex items-center gap-2 p-4 text-sm text-muted"><span className="h-2 w-2 animate-pulse2 rounded-full bg-sig" />connecting to the indexer…</div></Panel>}

      <div className="mt-4">
        <Caveat kind="note">
          {isRpc
            ? <>Live reads scan OOv2 <span className="font-mono">DisputePrice</span> logs over a keyless public Polygon RPC — no indexer, no paid service. V2/Legacy conditionIds derive from the ancillary data; NegRisk ones are recovered on-chain via the NegRisk operator's <span className="font-mono">QuestionPrepared</span> event, so those markets resolve to real names too. Markets created after the HF snapshot have no name yet — HF has no record of them. The released parquet remains the audited source of record. Set <span className="font-mono">INDEXER_GRAPHQL_URL</span> to use a hosted indexer instead.</>
            : <>Live reads hit the configured Envio indexer — the released parquet remains the audited source of record.</>}
        </Caveat>
      </div>
    </Section>
  )
}

function Row({ d, now, fresh }: { d: LiveDispute; now: number; fresh: boolean }) {
  const { C } = useColors()
  const oc = outcomeColor(C, d.proposedOutcome || 'OTHER')
  return (
    <m.div layout initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
      className={`grid grid-cols-[76px_1fr_auto] items-center gap-3 px-4 py-2.5 text-xs transition ${fresh ? 'animate-flash-up' : 'hover:bg-elevated/40'}`}>
      <span className="num text-muted">{ago(d.disputeTs, now)}</span>
      <div className="min-w-0">
        {d.marketName && (
          <div className="truncate font-sans text-xs text-ink-2" title={d.marketName}>{d.marketName}</div>
        )}
        <div className="flex items-center gap-2">
          <span className="rounded px-1.5 py-0.5 text-2xs font-medium" style={{ background: `${oc}1f`, color: oc }}>
            proposed {d.proposedOutcome ?? '—'}
          </span>
          {d.adapter && <span className="chip !py-0.5 !text-[10px] capitalize">{d.adapter}</span>}
          <span className="num truncate text-2xs text-muted" title={d.conditionId || ''}>{short(d.conditionId, 8, 6)}</span>
          {d.conditionId && <CopyButton value={d.conditionId} label="Copy conditionId" />}
          {d.round != null && d.round > 1 && <span className="chip !py-0.5 !text-[10px]">round {d.round}</span>}
        </div>
        <div className="num mt-0.5 flex flex-wrap items-center gap-x-1 text-2xs text-muted">
          <span>proposer {short(d.proposer, 6, 4)}</span>{d.proposer && <CopyButton value={d.proposer} label="Copy proposer address" />}
          <span>· disputed by {short(d.disputer, 6, 4)}</span>{d.disputer && <CopyButton value={d.disputer} label="Copy disputer address" />}
          {d.outcomeSlotCount != null && <span>· {d.outcomeSlotCount} slots</span>}
        </div>
      </div>
      <div className="text-right">
        <span className="num rounded px-1.5 py-0.5 text-2xs" style={{ color: d.marketStatus === 'RESOLVED' ? C.ink2 : C.warn }}>
          {d.marketStatus ?? '—'}
        </span>
        {d.finalOutcome && (
          <div className="num mt-0.5 text-2xs" style={{ color: outcomeColor(C, d.finalOutcome) }}>final {d.finalOutcome}</div>
        )}
      </div>
    </m.div>
  )
}

function prettyEndpoint(url: string): string {
  return url.replace(/^https?:\/\//, '')
}
