import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, m } from 'framer-motion'
import type { Address } from 'viem'
import { api, usePoll, type TnEvent, type TnMarket, type TnPosition, type TnStatus } from '../api/client'
import { readAllowance, useWallet } from '../lib/wallet'
import { useToast } from '../components/Toast'
import { FAUCETS, addressUrl, txUrl } from '../lib/testnet'
import { useColors } from '../components/Theme'
import type { Colors } from '../lib/theme'
import { ago, num, short, usd } from '../lib/format'
import { Caveat, ConfirmDialog, CopyButton, Panel, Section, Stat } from '../components/ui'

const POLL_MS = 5000
type ConfirmSpec = { title: string; body: React.ReactNode; confirmLabel: string; tone: 'warn' | 'default'; act: () => void }

export function LiveTestnet() {
  const { C } = useColors()
  const w = useWallet()
  const toast = useToast()
  const [status, setStatus] = useState<TnStatus | null>(null)
  const [market, setMarket] = useState<TnMarket | null>(null)
  const [pos, setPos] = useState<TnPosition | null>(null)
  const [events, setEvents] = useState<TnEvent[]>([])
  const [feedNote, setFeedNote] = useState<string | undefined>(undefined)
  const [allowance, setAllowance] = useState<number>(0)
  const [now, setNow] = useState(Date.now())
  const [size, setSize] = useState('0.25') // must be <= the contract's maxTrade (0.5); validated below
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState<ConfirmSpec | null>(null)

  const marketAddr = (status?.market_address || null) as Address | null
  const bal = w.balances

  // --- polling: backend market/position/events + client-side allowance ---
  // usePoll skips overlapping ticks and backs off while the backend is unreachable (cold start).
  const refreshChain = useCallback(async (): Promise<boolean> => {
    try {
      const [s, m, e] = await Promise.all([api.tnStatus(), api.tnMarket(), api.tnEvents(30)])
      setStatus(s); setMarket(m); setEvents(e.events || []); setFeedNote(e.note)
      if (w.address && s.market_address) {
        api.tnPosition(w.address).then(setPos).catch(() => {})
      }
      return true
    } catch { return false /* keep last good */ }
  }, [w.address])

  const refreshAllowance = useCallback(async () => {
    if (!w.address || !w.onAmoy || !marketAddr) return
    try { setAllowance(+(await readAllowance(w.address as Address, marketAddr))) } catch { /* transient */ }
  }, [w.address, w.onAmoy, marketAddr])

  usePoll(refreshChain, POLL_MS)
  useEffect(() => { refreshAllowance() }, [refreshAllowance])
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t) }, [])

  // --- readiness gating for the onboarding stepper ---
  const hasGas = !!bal && +bal.pol > 0
  const hasUsdc = !!bal && +bal.usdc > 0
  const approved = allowance > 0
  const deployed = !!market?.deployed
  const tradable = w.onAmoy && deployed && !market?.resolved

  // validate the trade against the on-chain limits so we never send a tx that reverts on-chain
  // ("size" > maxTrade, unapproved, or over-balance) — the buttons explain why they're disabled.
  const maxTrade = market?.max_trade ?? 0
  const nSize = Number(size) || 0
  const buyCost = nSize * (market?.ask ?? 0)    // USDC you pay to buy `nSize` YES at the ask
  const sellValue = nSize * (market?.bid ?? 0)  // USDC you receive selling `nSize` YES at the bid
  const usdcBal = +(bal?.usdc ?? 0)
  const canBuy = tradable && approved && nSize > 0 && nSize <= maxTrade && buyCost <= usdcBal
  const canSell = tradable && nSize > 0 && nSize <= (pos?.shares ?? 0)
  const tradeMsg = nSize > maxTrade ? `Max ${maxTrade} YES per trade.`
    : (nSize > 0 && buyCost > usdcBal) ? `Buy needs ${buyCost.toFixed(2)} USDC (you have ${usdcBal.toFixed(2)}).`
    : !approved ? 'Approve the market once to enable buys.'
    : ''

  // derive net invested + P&L from the user's on-chain Traded events
  const mine = useMemo(() => events.filter((e) => e.type === 'Traded' && e.user?.toLowerCase() === w.address?.toLowerCase()), [events, w.address])
  const netInvested = mine.reduce((s, e) => s + (e.buy ? (e.usdc || 0) : -(e.usdc || 0)), 0)
  const markValue = pos?.mark_value ?? 0
  const pnl = markValue - netInvested

  // --- tx runners → all feedback goes through the global toast stack ---
  const run = async (fn: () => Promise<`0x${string}`>, note: string) => {
    setBusy(true)
    const id = toast.pending(`${note}`, 'confirm in your wallet…')
    try {
      const hash = await fn()
      toast.update(id, { variant: 'success', title: `${note} confirmed`, message: short(hash, 10, 8), href: txUrl(hash), hrefLabel: 'view on Amoyscan ↗' })
      setTimeout(() => { refreshChain(); w.refreshBalances(); refreshAllowance() }, 1200)
    } catch (e: any) {
      toast.update(id, { variant: 'error', title: `${note} failed`, message: e?.shortMessage || e?.details || e?.message || 'transaction failed' })
    } finally { setBusy(false) }
  }
  const runApi = async (fn: () => Promise<any>, note: string, onOk?: (r: any) => void) => {
    setBusy(true)
    const id = toast.pending(`${note}…`)
    try {
      const r = await fn()
      onOk?.(r)
      toast.update(id, { variant: 'success', title: `${note} confirmed`, message: r.tx ? short(r.tx, 10, 8) : undefined, href: r.tx ? txUrl(r.tx) : undefined, hrefLabel: 'view ↗' })
      setTimeout(refreshChain, 1200)
    } catch (e: any) {
      toast.update(id, { variant: 'error', title: `${note} failed`, message: String(e?.message || e) })
    } finally { setBusy(false) }
  }

  // re-quote returns the fresh quote — reflect it instantly instead of waiting for the next poll
  const reQuote = () => runApi(() => api.tnEngineQuote({}), 'engine re-quote', (r) => {
    if (r?.bid != null && r?.ask != null) {
      setMarket((mkt) => mkt ? { ...mkt, bid: r.bid, ask: r.ask, lambda_jump: r.lambda_jump ?? mkt.lambda_jump, sigma: r.sigma ?? mkt.sigma, quote_ts: Math.floor(Date.now() / 1000) } : mkt)
    }
  })

  const engineDown = status && status.reachable && !status.engine_ready
  const mid = market ? (market.bid + market.ask) / 2 : 0

  return (
    <Section id="trade" kicker="live on-chain · Polygon Amoy · the engine is the market maker"
      title="Trade against the PolyLambda engine — on-chain"
      subtitle="The backend engine posts real two-sided quotes from the live estimators; you buy/sell YES with real test-USDC settlement; the λ-dispute-defense fires on-chain. Every action is a real Amoy transaction."
      right={
        <span className={`chip ${tradable ? 'border-sig/40 text-sig' : 'border-warn/50 text-warn'}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${tradable ? 'animate-pulse2' : ''}`} style={{ background: tradable ? C.sig : C.warn }} />
          {market?.resolved ? 'resolved' : market?.disputed ? 'disputed' : deployed ? (tradable ? 'live market' : 'connect to trade') : 'market deploying'}
        </span>
      }>

      {engineDown && <div className="mb-4"><Caveat kind="note">The backend engine wallet isn't configured on this instance yet — quotes/dispute/resolve are disabled, but reads work.</Caveat></div>}

      {/* ---- onboarding stepper (until ready) ---- */}
      {!tradable || !hasUsdc || !approved ? (
        <Panel className="mb-4">
          <div className="label mb-3 text-sig">get set up · 4 steps</div>
          <ol className="space-y-2.5">
            <Step n={1} done={!!w.address} title="Connect a wallet"
              action={!w.installed ? <a className="btn btn-primary" href="https://metamask.io/download/" target="_blank" rel="noreferrer">Install MetaMask</a>
                : !w.address ? <button className="btn btn-primary" onClick={w.connect} disabled={w.connecting}>{w.connecting ? 'connecting…' : 'Connect'}</button>
                : <span className="chip">{short(w.address, 6, 4)}</span>} />
            <Step n={2} done={w.onAmoy} title="Switch to Polygon Amoy"
              action={w.address && !w.onAmoy ? <button className="btn btn-primary" onClick={() => w.ensureAmoy().catch(() => {})}>Switch network</button> : w.onAmoy ? <span className="chip text-sig">on Amoy</span> : null} />
            <Step n={3} done={hasGas && hasUsdc} title="Fund test POL (gas) + test USDC"
              action={w.onAmoy ? <div className="flex flex-wrap items-center gap-2">
                <span className="chip">{bal ? `${num(+bal.pol, 3)} POL` : '—'}</span>
                <span className="chip">{bal ? `${num(+bal.usdc, 2)} USDC` : '—'}</span>
                <a className="btn !py-1.5 text-2xs" href={FAUCETS.pol} target="_blank" rel="noreferrer">POL faucet ↗</a>
                <a className="btn !py-1.5 text-2xs" href={FAUCETS.usdc} target="_blank" rel="noreferrer">USDC faucet ↗</a>
                <button className="text-2xs text-muted hover:text-ink-2" onClick={() => w.refreshBalances()} aria-label="Refresh balances">↻</button>
              </div> : null} />
            <Step n={4} done={approved} title="Approve the market to settle your USDC"
              action={w.onAmoy && hasUsdc && marketAddr ? (approved ? <span className="chip text-sig">approved · {num(allowance, 0)} USDC</span>
                : <button className="btn btn-primary" onClick={() => run(() => w.approveToken(marketAddr, '1000'), 'approve market')} disabled={busy}>Approve</button>) : null} />
          </ol>
        </Panel>
      ) : null}

      {/* ---- the live market ---- */}
      {deployed && (
        <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
          {/* engine quote + trade */}
          <div className="space-y-4">
            <Panel>
              <div className="mb-2 flex items-center justify-between">
                <div className="label text-sig">engine quote · YES · {market?.category ?? '—'}</div>
                <div className="flex items-center gap-2 text-2xs text-muted">
                  <span>λ {market ? (market.lambda_jump! * 100).toFixed(2) : '—'}% · σ {market ? market.sigma!.toFixed(3) : '—'} · {ago(market?.quote_ts, now)}</span>
                  <button className="btn !py-1 !px-2 text-2xs" disabled={!!engineDown || busy} onClick={reQuote} aria-label="Request a fresh engine quote">↻ re-quote</button>
                </div>
              </div>
              <QuoteBar bid={market!.bid} ask={market!.ask} />
              <div className="mt-3 grid grid-cols-3 gap-3 text-center">
                <div><div className="num text-lg text-loss">{market!.bid.toFixed(3)}</div><div className="text-2xs text-muted">bid (engine buys)</div></div>
                <div><div className="num text-lg text-ink">{mid.toFixed(3)}</div><div className="text-2xs text-muted">mid</div></div>
                <div><div className="num text-lg text-profit">{market!.ask.toFixed(3)}</div><div className="text-2xs text-muted">ask (engine sells)</div></div>
              </div>
            </Panel>

            {/* trade panel */}
            <Panel>
              <div className="mb-2 flex items-center justify-between">
                <div className="label text-sig">trade</div>
                <div className="text-2xs text-muted">max {market?.max_trade ?? 0} YES/trade</div>
              </div>
              {market?.disputed || market?.resolved ? (
                <div className="rounded-lg border border-warn/30 bg-warn/10 p-3 text-sm text-warn">
                  {market.resolved ? `Market resolved — ${market.yes_won ? 'YES won' : 'NO won'}. Redeem your position below.` : 'Dispute flagged — the engine pulled its ask. New buys are halted (the λ-defense).'}
                </div>
              ) : (
                <div>
                  <div className="flex flex-wrap items-end gap-3">
                    <label className="min-w-[120px] flex-1">
                      <div className="label mb-1">size (YES shares · max {maxTrade})</div>
                      <input className="field num" value={size} inputMode="decimal" aria-label="Trade size in YES shares" onChange={(e) => setSize(e.target.value.replace(/[^0-9.]/g, ''))} />
                    </label>
                    <button className="btn btn-primary" disabled={!canBuy || busy}
                      onClick={() => run(() => w.buyYes(marketAddr!, size || '0'), `buy ${size} YES · ${buyCost.toFixed(2)} USDC`)}>
                      Buy {nSize || 0} YES · {buyCost.toFixed(2)} USDC
                    </button>
                    <button className="btn" disabled={!canSell || busy}
                      onClick={() => run(() => w.sellYes(marketAddr!, size || '0'), `sell ${size} YES · ${sellValue.toFixed(2)} USDC`)}>
                      Sell {nSize || 0} YES · {sellValue.toFixed(2)} USDC
                    </button>
                  </div>
                  {tradeMsg && <div className="mt-2 text-2xs text-warn">{tradeMsg}</div>}
                </div>
              )}
              {market?.resolved && (pos?.shares ?? 0) > 0 && (
                <button className="btn btn-primary mt-3" disabled={busy} onClick={() => run(() => w.redeem(marketAddr!), 'redeem')}>
                  Redeem {num(pos!.shares, 2)} YES → {market.yes_won ? usd(pos!.shares) : '$0'}
                </button>
              )}
            </Panel>

            {/* the λ-defense controls */}
            <Panel>
              <div className="label mb-2 text-warn">λ-dispute-defense (engine-signed)</div>
              <p className="mb-3 text-sm text-ink-2">Simulate a dispute: the engine flags it on-chain and pulls its ask — halting the dangerous side. Then resolve the market so positions settle.</p>
              <div className="flex flex-wrap gap-2">
                <button className="btn" disabled={!!engineDown || market?.disputed || market?.resolved || busy} aria-label="Trigger a dispute on-chain"
                  onClick={() => setConfirm({ title: 'Trigger a dispute?', tone: 'warn', confirmLabel: 'Trigger dispute',
                    body: 'The engine flags a dispute on-chain and pulls its ask — halting new buys (the λ-defense). This is a real Amoy transaction.',
                    act: () => runApi(api.tnDispute, 'flag dispute') })}>⚠ Trigger dispute</button>
                <button className="btn" disabled={!!engineDown || market?.resolved || busy}
                  onClick={() => setConfirm({ title: 'Resolve this market YES?', tone: 'default', confirmLabel: 'Resolve YES',
                    body: 'Settles every position with YES as the winning outcome. Irreversible on-chain.', act: () => runApi(() => api.tnResolve(true), 'resolve YES') })}>Resolve YES</button>
                <button className="btn" disabled={!!engineDown || market?.resolved || busy}
                  onClick={() => setConfirm({ title: 'Resolve this market NO?', tone: 'default', confirmLabel: 'Resolve NO',
                    body: 'Settles every position with NO as the winning outcome. Irreversible on-chain.', act: () => runApi(() => api.tnResolve(false), 'resolve NO') })}>Resolve NO</button>
              </div>
            </Panel>
          </div>

          {/* position + activity rail */}
          <div className="space-y-4 self-start">
            <div className="grid grid-cols-2 gap-3">
              <Stat label="Your YES" value={pos?.shares ?? 0} format={(n) => num(n, 2)} accent sub={`mark ${mid.toFixed(3)}`} />
              <Stat label="P&L" value={pnl} format={(n) => usd(n)} tone={pnl >= 0 ? 'profit' : 'loss'} sub={`invested ${usd(netInvested)}`} />
            </div>
            <Panel className="!p-3">
              <div className="mb-1 flex items-center justify-between"><span className="label">engine wallet</span><span className="num text-2xs text-muted">{status?.engine_pol != null ? `${num(status.engine_pol, 3)} POL` : '—'}</span></div>
              {status?.engine && (
                <div className="flex items-center gap-1.5">
                  <a className="break-all font-mono text-2xs text-ink-2 link-underline" href={addressUrl(status.engine)} target="_blank" rel="noreferrer">{short(status.engine, 8, 6)} ↗</a>
                  <CopyButton value={status.engine} label="Copy engine address" />
                </div>
              )}
              <div className="num mt-2 flex justify-between border-t border-line pt-2 text-2xs text-muted"><span>escrow</span><span>{usd(market?.escrow_usdc ?? 0)}</span></div>
              <div className="num flex justify-between text-2xs text-muted"><span>open interest</span><span>{num(market?.total_yes ?? 0, 2)} YES</span></div>
              {status?.block != null && <div className="num flex justify-between text-2xs text-muted"><span>chain block</span><span>#{status.block}</span></div>}
              {marketAddr && (
                <div className="mt-1 flex items-center gap-1.5">
                  <a className="text-2xs text-muted link-underline" href={addressUrl(marketAddr)} target="_blank" rel="noreferrer">market {short(marketAddr, 6, 4)} ↗</a>
                  <CopyButton value={marketAddr} label="Copy market address" />
                </div>
              )}
            </Panel>

            {/* on-chain activity feed */}
            <ActivityFeed events={events} note={feedNote} />
          </div>
        </div>
      )}

      <div className="mt-4">
        <Caveat kind="note">
          <b>Testnet.</b> Real transactions on Polygon Amoy (chain 80002) with valueless test tokens; the market is a
          minimal on-chain contract where the PolyLambda engine wallet is the MM. Polymarket's real CLOB is mainnet-only —
          this is the on-chain demonstration of the same engine + λ-defense. Contract, engine wallet, and every tx link to Amoyscan.
        </Caveat>
      </div>

      <ConfirmDialog open={!!confirm} onClose={() => setConfirm(null)} busy={busy}
        title={confirm?.title ?? ''} body={confirm?.body} confirmLabel={confirm?.confirmLabel} tone={confirm?.tone}
        onConfirm={() => { confirm?.act(); setConfirm(null) }} />
    </Section>
  )
}

function Step({ n, done, title, action }: { n: number; done: boolean; title: string; action: React.ReactNode }) {
  return (
    <li className="flex flex-wrap items-center gap-3">
      <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-full border text-2xs font-semibold ${done ? 'border-sig/50 bg-sig/15 text-sig' : 'border-line bg-elevated text-muted'}`}>{done ? '✓' : n}</span>
      <span className={`text-sm ${done ? 'text-ink-2 line-through decoration-line' : 'text-ink'}`}>{title}</span>
      <span className="ml-auto">{action}</span>
    </li>
  )
}

function QuoteBar({ bid, ask }: { bid: number; ask: number }) {
  const { C } = useColors()
  const pos = (x: number) => `${Math.min(Math.max(x, 0), 1) * 100}%`
  return (
    <div className="relative mt-1 h-9">
      <div className="absolute inset-x-0 top-4 h-1 rounded-full bg-bg" />
      <div className="absolute top-4 h-1 rounded-full" style={{ left: pos(bid), width: pos(ask - bid), background: 'linear-gradient(90deg, rgb(var(--loss) / 0.6), rgb(var(--profit) / 0.6))' }} />
      {([['bid', bid, C.loss], ['ask', ask, C.profit]] as const).map(([lbl, x, col]) => (
        <div key={lbl} className="absolute -translate-x-1/2" style={{ left: pos(x) }}>
          <div className="mx-auto h-5 w-px" style={{ background: col }} />
          <div className="num mt-0.5 text-[10px]" style={{ color: col }}>{lbl}</div>
        </div>
      ))}
    </div>
  )
}

const evColor = (C: Colors, t: string): string =>
  (({ Traded: C.sig, QuotePosted: C.series[1], Disputed: C.warn, Resolved: C.serious, Redeemed: C.profit, Collateral: C.muted } as Record<string, string>)[t] || C.muted)

// activity feed with new-event highlight + an unread pill when scrolled away from the top
function ActivityFeed({ events, note }: { events: TnEvent[]; note?: string }) {
  const seen = useRef<Set<string>>(new Set())
  const [fresh, setFresh] = useState<Set<string>>(new Set())
  const [unread, setUnread] = useState(0)
  const scrollRef = useRef<HTMLDivElement>(null)
  const evKey = (e: TnEvent) => `${e.tx}-${e.type}-${e.block}`

  useEffect(() => {
    const incoming = events.map(evKey)
    const newly = incoming.filter((k) => seen.current.size > 0 && !seen.current.has(k))
    incoming.forEach((k) => seen.current.add(k))
    if (newly.length) {
      setFresh(new Set(newly))
      if ((scrollRef.current?.scrollTop ?? 0) > 8) setUnread((c) => c + newly.length)
      const t = setTimeout(() => setFresh(new Set()), 2500)
      return () => clearTimeout(t)
    }
  }, [events])

  const toTop = () => { scrollRef.current?.scrollTo({ top: 0, behavior: 'smooth' }); setUnread(0) }

  return (
    <Panel pad={false} className="overflow-hidden">
      <div className="flex items-center justify-between border-b border-line px-4 py-2 text-2xs text-muted">
        <span>on-chain activity</span>
        {unread > 0 && (
          <button onClick={toTop} className="chip !py-0.5 border-sig/40 text-sig" aria-label={`${unread} new events, scroll to top`}>↑ {unread} new</button>
        )}
      </div>
      <div ref={scrollRef} onScroll={(e) => { if ((e.target as HTMLDivElement).scrollTop < 8) setUnread(0) }}
        className="max-h-[300px] divide-y divide-line/50 overflow-y-auto">
        {events.length === 0 && <div className="p-4 text-sm text-muted">{note || 'no on-chain activity yet'}</div>}
        {events.length > 0 && note && <div className="px-4 py-2 text-2xs text-warn">⚠ {note}</div>}
        <AnimatePresence initial={false}>
          {events.map((e) => <FeedRow key={evKey(e)} e={e} fresh={fresh.has(evKey(e))} />)}
        </AnimatePresence>
      </div>
    </Panel>
  )
}

function FeedRow({ e, fresh }: { e: TnEvent; fresh: boolean }) {
  const { C } = useColors()
  const col = evColor(C, e.type)
  const label = e.type === 'Traded' ? `${e.buy ? 'BUY' : 'SELL'} ${num(e.size || 0, 2)} YES · ${usd(e.usdc || 0)}`
    : e.type === 'QuotePosted' ? `quote ${e.bid?.toFixed(3)}/${e.ask?.toFixed(3)}${e.category ? ` · ${e.category}` : ''}${e.lambda_jump != null ? ` · λ ${(e.lambda_jump * 100).toFixed(1)}%` : ''}`
    : e.type === 'Resolved' ? `resolved ${e.yes_won ? 'YES' : 'NO'}`
    : e.type === 'Redeemed' ? `redeem ${usd(e.payout || 0)}`
    : e.type === 'Collateral' ? `collateral +${usd(e.amount || 0)}` : e.type
  return (
    <m.a href={txUrl(e.tx)} target="_blank" rel="noreferrer" layout
      initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
      className={`flex items-center gap-2 px-4 py-2 text-xs transition ${fresh ? 'animate-flash-up' : 'hover:bg-elevated/40'}`}>
      <span className="rounded px-1.5 py-0.5 text-2xs" style={{ background: `${col}1f`, color: col }}>{e.type}</span>
      <span className="num truncate text-ink-2">{label}</span>
      <span className="num ml-auto shrink-0 text-2xs text-muted">#{e.block}</span>
    </m.a>
  )
}
