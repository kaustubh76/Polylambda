import { useCallback, useEffect, useMemo, useState } from 'react'
import type { Address } from 'viem'
import { api, type TnEvent, type TnMarket, type TnPosition, type TnStatus } from '../api/client'
import { readAllowance, readBalances, useWallet, type Balances } from '../lib/wallet'
import { FAUCETS, addressUrl, txUrl } from '../lib/testnet'
import { C } from '../lib/theme'
import { ago, num, short, usd } from '../lib/format'
import { Caveat, ErrorBox, Panel, Section, Stat } from '../components/ui'

type Tx = { state: 'idle' | 'pending' | 'ok' | 'err'; hash?: string; msg?: string; note?: string }
const POLL_MS = 5000

export function LiveTestnet() {
  const w = useWallet()
  const [status, setStatus] = useState<TnStatus | null>(null)
  const [market, setMarket] = useState<TnMarket | null>(null)
  const [pos, setPos] = useState<TnPosition | null>(null)
  const [events, setEvents] = useState<TnEvent[]>([])
  const [bal, setBal] = useState<Balances | null>(null)
  const [allowance, setAllowance] = useState<number>(0)
  const [now, setNow] = useState(Date.now())
  const [size, setSize] = useState('1')
  const [tx, setTx] = useState<Tx>({ state: 'idle' })

  const marketAddr = (status?.market_address || null) as Address | null

  // --- polling: backend market/position/events + client-side balances/allowance ---
  const refreshChain = useCallback(async () => {
    try {
      const [s, m, e] = await Promise.all([api.tnStatus(), api.tnMarket(), api.tnEvents(30)])
      setStatus(s); setMarket(m); setEvents(e.events || [])
      if (w.address && s.market_address) {
        api.tnPosition(w.address).then(setPos).catch(() => {})
      }
    } catch { /* keep last good */ }
  }, [w.address])

  const refreshWallet = useCallback(async () => {
    if (!w.address || !w.onAmoy) return
    try {
      const b = await readBalances(w.address as Address); setBal(b)
      if (marketAddr) setAllowance(+(await readAllowance(w.address as Address, marketAddr)))
    } catch { /* transient */ }
  }, [w.address, w.onAmoy, marketAddr])

  useEffect(() => {
    refreshChain(); const p = setInterval(refreshChain, POLL_MS); return () => clearInterval(p)
  }, [refreshChain])
  useEffect(() => { refreshWallet() }, [refreshWallet])
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t) }, [])

  // --- readiness gating for the onboarding stepper ---
  const hasGas = !!bal && +bal.pol > 0
  const hasUsdc = !!bal && +bal.usdc > 0
  const approved = allowance > 0
  const deployed = !!market?.deployed
  const tradable = w.onAmoy && deployed && !market?.resolved

  // derive net invested + P&L from the user's on-chain Traded events
  const mine = useMemo(() => events.filter((e) => e.type === 'Traded' && e.user?.toLowerCase() === w.address?.toLowerCase()), [events, w.address])
  const netInvested = mine.reduce((s, e) => s + (e.buy ? (e.usdc || 0) : -(e.usdc || 0)), 0)
  const markValue = pos?.mark_value ?? 0
  const pnl = markValue - netInvested

  const run = async (fn: () => Promise<`0x${string}`>, note: string) => {
    setTx({ state: 'pending', note })
    try { const hash = await fn(); setTx({ state: 'ok', hash, note }); setTimeout(() => { refreshChain(); refreshWallet() }, 1500) }
    catch (e: any) { setTx({ state: 'err', msg: e?.shortMessage || e?.details || e?.message || 'transaction failed', note }) }
  }
  const runApi = async (fn: () => Promise<{ tx: string }>, note: string) => {
    setTx({ state: 'pending', note })
    try { const r = await fn(); setTx({ state: 'ok', hash: r.tx, note }); setTimeout(refreshChain, 1500) }
    catch (e: any) { setTx({ state: 'err', msg: String(e?.message || e), note }) }
  }

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
              action={w.address && !w.onAmoy ? <button className="btn btn-primary" onClick={w.ensureAmoy}>Switch network</button> : w.onAmoy ? <span className="chip text-sig">on Amoy</span> : null} />
            <Step n={3} done={hasGas && hasUsdc} title="Fund test POL (gas) + test USDC"
              action={w.onAmoy ? <div className="flex flex-wrap items-center gap-2">
                <span className="chip">{bal ? `${num(+bal.pol, 3)} POL` : '—'}</span>
                <span className="chip">{bal ? `${num(+bal.usdc, 2)} USDC` : '—'}</span>
                <a className="btn !py-1.5 text-2xs" href={FAUCETS.pol} target="_blank" rel="noreferrer">POL faucet ↗</a>
                <a className="btn !py-1.5 text-2xs" href={FAUCETS.usdc} target="_blank" rel="noreferrer">USDC faucet ↗</a>
                <button className="text-2xs text-muted hover:text-ink-2" onClick={refreshWallet}>↻</button>
              </div> : null} />
            <Step n={4} done={approved} title="Approve the market to settle your USDC"
              action={w.onAmoy && hasUsdc && marketAddr ? (approved ? <span className="chip text-sig">approved · {num(allowance, 0)} USDC</span>
                : <button className="btn btn-primary" onClick={() => run(() => w.approveToken(marketAddr, '1000'), 'approve market')} disabled={tx.state === 'pending'}>Approve</button>) : null} />
          </ol>
          {tx.state === 'err' && <div className="mt-3"><ErrorBox error={`${tx.note}: ${tx.msg}`} /></div>}
          {tx.state === 'ok' && tx.hash && <TxOk tx={tx} />}
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
                  <button className="btn !py-1 !px-2 text-2xs" disabled={!!engineDown || tx.state === 'pending'} onClick={() => runApi(api.tnEngineQuote.bind(null, {}), 'engine re-quote')}>↻ re-quote</button>
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
                <div className="flex flex-wrap items-end gap-3">
                  <label className="min-w-[120px] flex-1">
                    <div className="label mb-1">size (YES shares)</div>
                    <input className="field num" value={size} inputMode="decimal" onChange={(e) => setSize(e.target.value.replace(/[^0-9.]/g, ''))} />
                  </label>
                  <button className="btn btn-primary" disabled={!tradable || tx.state === 'pending'}
                    onClick={() => run(() => w.buyYes(marketAddr!, size || '0'), `buy ${size} YES`)}>
                    Buy YES · {(Number(size || 0) * (market?.ask || 0)).toFixed(2)} USDC
                  </button>
                  <button className="btn" disabled={!tradable || tx.state === 'pending' || (pos?.shares ?? 0) <= 0}
                    onClick={() => run(() => w.sellYes(marketAddr!, size || '0'), `sell ${size} YES`)}>
                    Sell YES · {(Number(size || 0) * (market?.bid || 0)).toFixed(2)}
                  </button>
                </div>
              )}
              {market?.resolved && (pos?.shares ?? 0) > 0 && (
                <button className="btn btn-primary mt-3" disabled={tx.state === 'pending'} onClick={() => run(() => w.redeem(marketAddr!), 'redeem')}>
                  Redeem {num(pos!.shares, 2)} YES → {market.yes_won ? usd(pos!.shares) : '$0'}
                </button>
              )}
              {tx.state === 'pending' && <div className="mt-3 flex items-center gap-2 text-sm text-muted"><span className="h-2 w-2 animate-pulse2 rounded-full bg-sig" />{tx.note} · confirm in wallet…</div>}
              {tx.state === 'ok' && tx.hash && <TxOk tx={tx} />}
              {tx.state === 'err' && <div className="mt-3"><ErrorBox error={`${tx.note}: ${tx.msg}`} /></div>}
            </Panel>

            {/* the λ-defense controls */}
            <Panel>
              <div className="label mb-2 text-warn">λ-dispute-defense (engine-signed)</div>
              <p className="mb-3 text-sm text-ink-2">Simulate a dispute: the engine flags it on-chain and pulls its ask — halting the dangerous side. Then resolve the market so positions settle.</p>
              <div className="flex flex-wrap gap-2">
                <button className="btn" disabled={!!engineDown || market?.disputed || market?.resolved || tx.state === 'pending'} onClick={() => runApi(api.tnDispute, 'flag dispute')}>⚠ Trigger dispute</button>
                <button className="btn" disabled={!!engineDown || market?.resolved || tx.state === 'pending'} onClick={() => runApi(() => api.tnResolve(true), 'resolve YES')}>Resolve YES</button>
                <button className="btn" disabled={!!engineDown || market?.resolved || tx.state === 'pending'} onClick={() => runApi(() => api.tnResolve(false), 'resolve NO')}>Resolve NO</button>
              </div>
            </Panel>
          </div>

          {/* position + activity rail */}
          <div className="space-y-4 self-start">
            <div className="grid grid-cols-2 gap-3">
              <Stat label="Your YES" value={num(pos?.shares ?? 0, 2)} accent sub={`mark ${mid.toFixed(3)}`} />
              <Stat label="P&L" value={usd(pnl)} tone={pnl >= 0 ? 'profit' : 'loss'} sub={`invested ${usd(netInvested)}`} />
            </div>
            <Panel className="!p-3">
              <div className="mb-1 flex items-center justify-between"><span className="label">engine wallet</span><span className="num text-2xs text-muted">{status?.engine_pol != null ? `${num(status.engine_pol, 3)} POL` : '—'}</span></div>
              {status?.engine && <a className="break-all font-mono text-2xs text-ink-2 link-underline" href={addressUrl(status.engine)} target="_blank" rel="noreferrer">{short(status.engine, 8, 6)} ↗</a>}
              <div className="num mt-2 flex justify-between border-t border-line pt-2 text-2xs text-muted"><span>escrow</span><span>{usd(market?.escrow_usdc ?? 0)}</span></div>
              {marketAddr && <a className="mt-1 block text-2xs text-muted link-underline" href={addressUrl(marketAddr)} target="_blank" rel="noreferrer">market {short(marketAddr, 6, 4)} ↗</a>}
            </Panel>

            {/* on-chain activity feed */}
            <Panel pad={false} className="overflow-hidden">
              <div className="border-b border-line px-4 py-2 text-2xs text-muted">on-chain activity</div>
              <div className="max-h-[300px] divide-y divide-line/50 overflow-y-auto">
                {events.length === 0 && <div className="p-4 text-sm text-muted">no on-chain activity yet</div>}
                {events.map((e, i) => <FeedRow key={`${e.tx}-${i}`} e={e} />)}
              </div>
            </Panel>
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
  const pos = (x: number) => `${Math.min(Math.max(x, 0), 1) * 100}%`
  return (
    <div className="relative mt-1 h-9">
      <div className="absolute inset-x-0 top-4 h-1 rounded-full bg-bg" />
      <div className="absolute top-4 h-1 rounded-full" style={{ left: pos(bid), width: pos(ask - bid), background: 'linear-gradient(90deg,#e6676799,#22c58a99)' }} />
      {([['bid', bid, C.loss], ['ask', ask, C.profit]] as const).map(([lbl, x, col]) => (
        <div key={lbl} className="absolute -translate-x-1/2" style={{ left: pos(x) }}>
          <div className="mx-auto h-5 w-px" style={{ background: col }} />
          <div className="num mt-0.5 text-[10px]" style={{ color: col }}>{lbl}</div>
        </div>
      ))}
    </div>
  )
}

function TxOk({ tx }: { tx: Tx }) {
  return (
    <div className="mt-3 rounded-lg border border-good/30 bg-good/10 p-2.5 text-sm">
      <span className="text-good">✓ {tx.note} confirmed on-chain.</span>{' '}
      <a href={txUrl(tx.hash!)} target="_blank" rel="noreferrer" className="num link-underline text-ink-2">{short(tx.hash!, 10, 8)} ↗</a>
    </div>
  )
}

const EV_COLOR: Record<string, string> = { Traded: C.sig, QuotePosted: C.series[1], Disputed: C.warn, Resolved: C.serious, Redeemed: C.profit, Collateral: C.muted }
function FeedRow({ e }: { e: TnEvent }) {
  const label = e.type === 'Traded' ? `${e.buy ? 'BUY' : 'SELL'} ${num(e.size || 0, 2)} YES · ${usd(e.usdc || 0)}`
    : e.type === 'QuotePosted' ? `quote ${e.bid?.toFixed(3)}/${e.ask?.toFixed(3)}`
    : e.type === 'Resolved' ? `resolved ${e.yes_won ? 'YES' : 'NO'}`
    : e.type === 'Redeemed' ? `redeem ${usd(e.payout || 0)}`
    : e.type === 'Collateral' ? `collateral +${usd(e.amount || 0)}` : e.type
  return (
    <a href={txUrl(e.tx)} target="_blank" rel="noreferrer" className="flex items-center gap-2 px-4 py-2 text-xs transition hover:bg-elevated/40">
      <span className="rounded px-1.5 py-0.5 text-2xs" style={{ background: `${EV_COLOR[e.type] || C.muted}1f`, color: EV_COLOR[e.type] || C.muted }}>{e.type}</span>
      <span className="num truncate text-ink-2">{label}</span>
      <span className="num ml-auto shrink-0 text-2xs text-muted">#{e.block}</span>
    </a>
  )
}
