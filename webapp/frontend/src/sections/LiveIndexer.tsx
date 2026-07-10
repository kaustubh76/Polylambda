import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, m } from 'framer-motion'
import { api, type LiveDispute, type LiveDisputes, type LiveStatus } from '../api/client'
import { C } from '../lib/theme'
import { ago, short } from '../lib/format'
import { Caveat, CopyButton, ErrorBox, Panel, Section, Stat } from '../components/ui'

const OUTCOME_COLOR: Record<string, string> = { YES: C.profit, NO: C.loss, UNRESOLVABLE: C.warn, OTHER: C.muted }
const POLL_MS = 5000

export function LiveIndexer() {
  const [status, setStatus] = useState<LiveStatus | null>(null) // last GOOD status (kept through blips)
  const [feed, setFeed] = useState<LiveDisputes | null>(null)   // last GOOD feed
  const [now, setNow] = useState(Date.now())
  const [fresh, setFresh] = useState<Set<string>>(new Set())
  const [fails, setFails] = useState(0)
  const seen = useRef<Set<string>>(new Set())
  const tickRef = useRef<() => void>()

  useEffect(() => {
    let alive = true
    // status and feed are fetched INDEPENDENTLY so one hiccup never blanks the other, and the last
    // good value is kept — resilient to the free-tier CPU starving the outbound GraphQL under load.
    const tick = async () => {
      const [sRes, fRes] = await Promise.allSettled([api.liveStatus(), api.liveDisputes(30)])
      if (!alive) return
      let ok = false
      if (sRes.status === 'fulfilled') { setStatus(sRes.value); ok = ok || !!sRes.value.reachable }
      if (fRes.status === 'fulfilled' && fRes.value.reachable) {
        ok = true
        setFeed(fRes.value)
        const incoming = fRes.value.disputes.map((d) => d.id)
        const newly = incoming.filter((id) => seen.current.size > 0 && !seen.current.has(id))
        incoming.forEach((id) => seen.current.add(id))
        if (newly.length) { setFresh(new Set(newly)); setTimeout(() => alive && setFresh(new Set()), 2500) }
      }
      setFails((prev) => (ok ? 0 : prev + 1))
    }
    tickRef.current = tick
    // stagger the first poll so the other ~8 section fetches clear first on a weak instance
    const start = setTimeout(tick, 1200)
    const poll = setInterval(tick, POLL_MS)
    const clock = setInterval(() => alive && setNow(Date.now()), 1000)
    return () => { alive = false; clearTimeout(start); clearInterval(poll); clearInterval(clock) }
  }, [])

  const everConnected = status?.reachable || (feed?.reachable ?? false)
  const connecting = !everConnected && fails < 2       // still trying — not yet "offline"
  const up = everConnected && fails < 3                // sticky-live: tolerate transient blips
  const latency = status?.latency_ms ?? feed?.latency_ms
  const subSecond = latency != null && latency < 1000

  return (
    <Section id="live" kicker="fully live · hosted Envio HyperIndex"
      title="Live dispute stream"
      subtitle="Straight from the deployed indexer over GraphQL — the OOv2 dispute lifecycle as it lands on-chain, polled every 5s. The released snapshot above is the frozen, HF-enriched cut of exactly this feed."
      right={
        <span className={`chip ${up ? 'border-sig/40 text-sig' : connecting ? '' : 'border-warn/50 text-warn'}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${up || connecting ? 'animate-pulse2' : ''}`} style={{ background: up ? C.sig : connecting ? C.muted : C.warn }} />
          {up ? 'LIVE' : connecting ? 'connecting…' : 'indexer offline'}
        </span>
      }>
      {!connecting && !up && (
        <ErrorBox error={`Indexer unreachable — the live feed is optional; the rest of the dashboard runs off the shipped snapshot. ${status?.error || ''}`}
          onRetry={() => tickRef.current?.()} />
      )}

      {up && (
        <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
          {/* status rail */}
          <div className="space-y-3 self-start">
            <Stat label="Query latency" value={latency != null ? `${latency.toFixed(0)} ms` : '—'}
              tone={subSecond ? 'profit' : 'warn'} accent={subSecond}
              sub={subSecond ? 'sub-second round-trip' : 'round-trip to the indexer'} />
            <Stat label="Indexer head" value={ago(status?.head_ts, now)} sub="latest dispute indexed" />
            <Panel className="!p-3">
              <div className="label mb-1">endpoint</div>
              <div className="break-all font-mono text-2xs text-ink-2">{prettyEndpoint(status?.endpoint || feed?.endpoint || '')}</div>
              <div className="mt-2 flex items-center gap-1.5 text-2xs text-muted">
                <span className="h-1.5 w-1.5 animate-pulse2 rounded-full bg-sig" /> polling every 5s · GraphQL
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
              {feed && feed.disputes.length === 0 && <div className="p-6 text-sm text-muted">no disputes returned</div>}
            </div>
          </Panel>
        </div>
      )}

      {connecting && <Panel><div className="flex items-center gap-2 p-4 text-sm text-muted"><span className="h-2 w-2 animate-pulse2 rounded-full bg-sig" />connecting to the indexer…</div></Panel>}

      <div className="mt-4">
        <Caveat kind="note">
          Live reads hit the hosted Envio dev deploy (coverage-capped, non-authoritative) — the released
          parquet remains the audited source of record. Point <span className="font-mono">INDEXER_GRAPHQL_URL</span> at
          your own production indexer to swap it.
        </Caveat>
      </div>
    </Section>
  )
}

function Row({ d, now, fresh }: { d: LiveDispute; now: number; fresh: boolean }) {
  const oc = OUTCOME_COLOR[d.proposedOutcome || 'OTHER'] || C.muted
  return (
    <m.div layout initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
      className={`grid grid-cols-[76px_1fr_auto] items-center gap-3 px-4 py-2.5 text-xs transition ${fresh ? 'animate-flash-up' : 'hover:bg-elevated/40'}`}>
      <span className="num text-muted">{ago(d.disputeTs, now)}</span>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="rounded px-1.5 py-0.5 text-2xs font-medium" style={{ background: `${oc}1f`, color: oc }}>
            proposed {d.proposedOutcome ?? '—'}
          </span>
          <span className="num truncate text-2xs text-muted" title={d.conditionId || ''}>{short(d.conditionId, 8, 6)}</span>
          {d.conditionId && <CopyButton value={d.conditionId} label="Copy conditionId" />}
          {d.round != null && d.round > 1 && <span className="chip !py-0.5 !text-[10px]">round {d.round}</span>}
        </div>
        <div className="num mt-0.5 flex flex-wrap items-center gap-x-1 text-2xs text-muted">
          <span>proposer {short(d.proposer, 6, 4)}</span>{d.proposer && <CopyButton value={d.proposer} label="Copy proposer address" />}
          <span>· disputed by {short(d.disputer, 6, 4)}</span>{d.disputer && <CopyButton value={d.disputer} label="Copy disputer address" />}
        </div>
      </div>
      <span className="num rounded px-1.5 py-0.5 text-2xs" style={{ color: d.marketStatus === 'RESOLVED' ? C.ink2 : C.warn }}>
        {d.marketStatus ?? '—'}
      </span>
    </m.div>
  )
}

function prettyEndpoint(url: string): string {
  return url.replace(/^https?:\/\//, '')
}
