import { useEffect, useMemo, useRef, useState } from 'react'
import { m } from 'framer-motion'
import {
  CartesianGrid, Line, LineChart, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api, useAction, type DDPoint, type SessionReq } from '../api/client'
import { useDebounced } from '../lib/useDebounced'
import { useInViewOnce } from '../lib/motion'
import { useColors } from '../components/Theme'
import { fixed, num, short, signed, usd } from '../lib/format'
import { Caveat, ErrorBox, Loading, Panel, Section, Stat } from '../components/ui'

type Tab = 'dispute_defense' | 'live_quoting'
const CATS = ['politics', 'crypto', 'sports', 'geopolitics', 'tech-ai', 'economics', 'entertainment']

export function PaperSession() {
  const [tab, setTab] = useState<Tab>('dispute_defense')
  return (
    <Section id="session" kicker="the working engine · runner.run + real should_exit()"
      title="Paper forward-test — watch the engine defend"
      subtitle="A deterministic, network-free paper session driven by the actual execution loop. The dispute-defense A/B is an honest illustration of the exit mechanism; the powered edge claim lives in the ablation below."
      right={
        <div className="flex gap-1 rounded-lg border border-line bg-elevated p-1 text-xs">
          {(['dispute_defense', 'live_quoting'] as Tab[]).map((t) => (
            <button key={t} onClick={() => setTab(t)}
              className={`relative rounded-md px-3 py-1.5 transition-colors ${tab === t ? 'text-sig' : 'text-muted hover:text-ink-2'}`}>
              {tab === t && (
                <m.span layoutId="session-tab" className="absolute inset-0 rounded-md bg-sig/15"
                  transition={{ type: 'spring', stiffness: 500, damping: 40 }} />
              )}
              <span className="relative">{t === 'dispute_defense' ? 'Dispute defense' : 'Raw quoting loop'}</span>
            </button>
          ))}
        </div>
      }>
      {tab === 'dispute_defense' ? <DisputeDefense /> : <LiveQuoting />}
    </Section>
  )
}

// ============================================================================================
// Dispute-defense A/B (the centerpiece)
// ============================================================================================
function DisputeDefense() {
  const { C } = useColors()
  const [cfg, setCfg] = useState<SessionReq>({ scenario: 'dispute_defense', category: 'politics', entry_price: 0.62, inventory: 100, dispute_tick: 5, gap_logit: -1.35, n_ticks: 13 })
  const { run, data, error, loading } = useAction(api.session)
  const [frame, setFrame] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [showAllExits, setShowAllExits] = useState(false)
  const timer = useRef<number | null>(null)

  // recompute automatically when any control changes (debounced) — the graph tracks the sliders,
  // not just the Run button. The debounced snapshot also drives the initial mount run.
  const cfgKey = useDebounced(cfg, 350)
  useEffect(() => { run(cfgKey) }, [cfgKey]) // eslint-disable-line react-hooks/exhaustive-deps

  // when new data arrives, reset & auto-play the animation
  useEffect(() => {
    if (data) { setFrame(0); setPlaying(true) }
  }, [data])

  const series = data?.series as { lambda_on: DDPoint[]; lambda_off: DDPoint[] } | undefined
  const n = series?.lambda_on.length ?? 0

  useEffect(() => {
    if (!playing || n === 0) return
    timer.current = window.setInterval(() => {
      setFrame((f) => { if (f >= n - 1) { setPlaying(false); return f } return f + 1 })
    }, 380)
    return () => { if (timer.current) window.clearInterval(timer.current) }
  }, [playing, n])

  const merged = useMemo(() => {
    if (!series) return []
    return series.lambda_on.map((p, i) => ({
      i: p.i, mid: p.mid,
      on: p.equity, off: series.lambda_off[i]?.equity ?? null,
      on_inv: p.inventory, off_inv: series.lambda_off[i]?.inventory ?? null,
    }))
  }, [series])
  const shown = merged.slice(0, frame + 1)

  const p = (data?.params ?? {}) as any
  const disputeT = p.dispute_tick ?? cfg.dispute_tick
  const gapT = p.gap_tick ?? (Number(disputeT) + 1)
  const s = data?.summary

  return (
    <div className="space-y-4">
      {/* controls */}
      <Panel pad className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="label mr-1">category</span>
          {CATS.map((c) => (
            <button key={c} onClick={() => setCfg({ ...cfg, category: c })} aria-pressed={cfg.category === c}
              className={`chip capitalize ${cfg.category === c ? 'border-sig/50 bg-sig/10 text-sig' : ''}`}>{c}</button>
          ))}
        </div>
        <div className="flex flex-wrap items-end gap-4">
          <Ctl label="entry price" v={cfg.entry_price!} min={0.3} max={0.9} step={0.01} on={(v) => setCfg({ ...cfg, entry_price: v })} fmt={(v) => v.toFixed(2)} />
          <Ctl label="position size" v={cfg.inventory!} min={20} max={300} step={10} on={(v) => setCfg({ ...cfg, inventory: v })} fmt={(v) => num(v, 0)} />
          <Ctl label="dispute at tick" v={cfg.dispute_tick!} min={2} max={9} step={1} on={(v) => setCfg({ ...cfg, dispute_tick: v })} fmt={(v) => `${v}`} />
          <Ctl label="jump size (logit)" v={cfg.gap_logit!} min={-2} max={-0.4} step={0.05} on={(v) => setCfg({ ...cfg, gap_logit: v })} fmt={(v) => v.toFixed(2)} />
          <Ctl label="ticks" v={cfg.n_ticks!} min={8} max={40} step={1} on={(v) => setCfg({ ...cfg, n_ticks: v })} fmt={(v) => `${v}`} />
          <button className="btn btn-primary ml-auto" disabled={loading} onClick={() => run(cfg)}>
            {loading ? 'running…' : '▶ Run session'}
          </button>
          {n > 0 && !loading && (
            <button className="btn" onClick={() => { setFrame(0); setPlaying(true) }}>↻ Replay</button>
          )}
        </div>
      </Panel>

      {error && <ErrorBox error={error} />}
      {loading && !data && <Panel><Loading label="running the paper engine" /></Panel>}

      {data && series && (
        <>
          <div className="grid gap-4 lg:grid-cols-[1fr_300px]">
            <Panel>
              <div className="mb-2 flex items-center gap-4 text-xs">
                <Legend color={C.profit} label="λ-ON · reward-aware exit" />
                <Legend color={C.loss} label="λ-OFF · holds through" />
                <span className="ml-auto num text-2xs text-muted">tick {frame}/{n - 1}</span>
              </div>
              <div className="h-[300px] w-full">
                <ResponsiveContainer>
                  <LineChart data={shown} margin={{ left: 4, right: 16, top: 8, bottom: 4 }}>
                    <CartesianGrid stroke={C.line} vertical={false} />
                    <XAxis type="number" dataKey="i" domain={[0, n - 1]} allowDecimals={false}
                      stroke={C.axis} tick={{ fill: C.muted, fontSize: 11 }} tickLine={false}
                      label={{ value: 'tick', fill: C.muted, fontSize: 10, position: 'insideBottomRight', offset: -2 }} />
                    <YAxis stroke={C.axis} tick={{ fill: C.muted, fontSize: 11 }} tickLine={false}
                      tickFormatter={(v) => `$${v}`} width={48} />
                    <Tooltip content={<EqTip />} />
                    {frame >= disputeT && <ReferenceLine x={disputeT} stroke={C.warn} strokeDasharray="3 3"
                      label={{ value: 'dispute', fill: C.warn, fontSize: 10, position: 'top' }} />}
                    {frame >= gapT && <ReferenceLine x={gapT} stroke={C.serious} strokeDasharray="2 2"
                      label={{ value: 'gap', fill: C.serious, fontSize: 10, position: 'top' }} />}
                    <ReferenceLine y={0} stroke={C.axis} />
                    <Line type="monotone" dataKey="off" stroke={C.loss} strokeWidth={2} dot={false} isAnimationActive={false} name="λ-OFF" />
                    <Line type="monotone" dataKey="on" stroke={C.profit} strokeWidth={2} dot={false} isAnimationActive={false} name="λ-ON" />
                    {/* leading-edge marker: a glowing dot tracking the advancing tick (the replay IS the animation) */}
                    {shown.length > 0 && (
                      <ReferenceDot x={shown[shown.length - 1].i} y={shown[shown.length - 1].on} r={4}
                        fill={C.profit} stroke={C.bg} strokeWidth={2} isFront ifOverflow="extendDomain" />
                    )}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </Panel>

            {/* summary tiles */}
            <div className="space-y-3">
              <Stat label="Capital protected" value={s?.protected ?? 0} format={(n) => usd(n)} tone="profit"
                sub={`${s?.loss_reduction_pct}% smaller loss vs holding`} />
              <div className="grid grid-cols-2 gap-3">
                <Stat label="λ-ON final" value={s?.on_final_equity ?? 0} format={(n) => usd(n)} tone={s?.on_final_equity >= 0 ? 'profit' : 'loss'} />
                <Stat label="λ-OFF final" value={s?.off_final_equity ?? 0} format={(n) => usd(n)} tone="loss" />
              </div>
              <Stat label="Exit events fired" value={s?.n_exits ?? 0} format={(n) => String(Math.round(n))} sub="real should_exit() triggers" />
            </div>
          </div>

          <Panel>
            <div className="mb-2 text-sm text-ink-2">{data.narrative}</div>
            {data.exits && data.exits.length > 0 && (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[680px] text-left text-xs">
                  <thead className="text-2xs uppercase tracking-wide text-muted">
                    <tr className="border-b border-line">
                      <th className="py-1.5 pr-3">market</th><th className="pr-3">trigger</th>
                      <th className="pr-3">λ_jump vs λ*</th><th className="pr-3">inventory</th>
                      <th className="pr-3">exit px</th><th className="pr-3">haircut</th>
                      <th className="pr-3">E[loss]</th><th>forgone</th>
                    </tr>
                  </thead>
                  <tbody className="num">
                    {(showAllExits ? data.exits : data.exits.slice(0, 6)).map((e, i) => (
                      <tr key={i} className="border-b border-line/50 text-ink-2">
                        <td className="py-1.5 pr-3 text-muted">{e.cid ? short(e.cid, 6, 4) : '—'}</td>
                        <td className="pr-3"><span className="rounded bg-warn/15 px-1.5 py-0.5 text-warn">{e.trigger}</span></td>
                        <td className="pr-3"><span className="text-sig">{e.lambda_jump != null ? fixed(e.lambda_jump, 4) : '—'}</span> <span className="text-muted">/ {e.lambda_star != null ? fixed(e.lambda_star, 4) : '—'}</span></td>
                        <td className="pr-3">{num(e.inventory_before, 0)} → {num(e.inventory_after, 0)}</td>
                        <td className="pr-3">{e.exit_price.toFixed(3)}</td>
                        <td className="pr-3 text-loss">{usd(e.haircut_paid)}</td>
                        <td className="pr-3">{e.e_jump_loss.toFixed(2)}</td>
                        <td>{e.forgone_rewards.toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {data.exits.length > 6 && (
                  <button onClick={() => setShowAllExits((v) => !v)} className="mt-2 text-2xs text-sig underline decoration-sig/40 underline-offset-2 hover:decoration-sig">
                    {showAllExits ? 'show fewer' : `show all ${data.exits.length} exits`}
                  </button>
                )}
              </div>
            )}
            <div className="mt-3">
              <Caveat kind="underpowered">
                Illustrative scenario (<span className="font-mono">simulated: true</span>): the book is frozen and a
                single {Math.abs(Number(cfg.gap_logit)).toFixed(2)}-logit gap is injected so the exit mechanism is
                isolated cleanly. The λ math, the σ, the quote and the exit gate are all the production code.
              </Caveat>
            </div>
          </Panel>
        </>
      )}
    </div>
  )
}

// ============================================================================================
// Raw quoting loop (secondary)
// ============================================================================================
function LiveQuoting() {
  const { C } = useColors()
  const [n, setN] = useState(30)
  const [seed, setSeed] = useState(7)
  const [nMarkets, setNMarkets] = useState(4)
  const [realMarkets, setRealMarkets] = useState(false)
  const [hazard, setHazard] = useState(false)
  const [chartRef, chartIn] = useInViewOnce<HTMLDivElement>()
  const { run, data, error, loading } = useAction(api.session)
  const go = () => run({ scenario: 'live_quoting', n_ticks: n, n_markets: nMarkets, seed, source: realMarkets ? 'data' : 'synthetic', hazard: realMarkets && hazard })
  // re-run automatically (debounced) whenever any control changes; also drives the mount run
  const inputsKey = useDebounced(`${n}|${nMarkets}|${seed}|${realMarkets}|${hazard}`, 350)
  useEffect(() => { go() }, [inputsKey]) // eslint-disable-line react-hooks/exhaustive-deps
  const series = (data?.series ?? {}) as Record<string, any[]>
  const quotes = (data?.quotes ?? {}) as Record<string, any[]>
  const cids = Object.keys(series)
  const [sel, setSel] = useState<string | null>(null)
  const cid = sel && series[sel] ? sel : cids[0]

  // merge the engine's OWN posted quotes (bid/ask + risk_scale/pos_cap) onto the synthetic book by tick
  const rows = useMemo(() => {
    const s = cid ? series[cid] : []
    const q = cid ? quotes[cid] : undefined
    if (!q) return s
    return s.map((r: any, i: number) => ({ ...r, e_bid: q[i]?.bid ?? null, e_ask: q[i]?.ask ?? null }))
  }, [cid, series, quotes])
  const lastQ = cid && quotes[cid]?.length ? quotes[cid][quotes[cid].length - 1] : null

  return (
    <div className="space-y-4">
      <Panel pad className="flex flex-wrap items-end gap-4">
        <Ctl label="ticks" v={n} min={10} max={60} step={5} on={setN} fmt={(v) => `${v}`} />
        <Ctl label="markets" v={nMarkets} min={2} max={6} step={1} on={setNMarkets} fmt={(v) => `${v}`} />
        <Ctl label="seed" v={seed} min={1} max={99} step={1} on={setSeed} fmt={(v) => `${v}`} />
        <div className="flex flex-col gap-1.5">
          <button onClick={() => setRealMarkets((v) => !v)} aria-pressed={realMarkets}
            className={`chip ${realMarkets ? 'border-sig/50 bg-sig/10 text-sig' : ''}`}>real markets</button>
          <button onClick={() => setHazard((v) => !v)} aria-pressed={hazard} disabled={!realMarkets}
            className={`chip ${hazard && realMarkets ? 'border-sig/50 bg-sig/10 text-sig' : ''} ${!realMarkets ? 'opacity-40' : ''}`}>hazard λ</button>
        </div>
        <button className="btn btn-primary" disabled={loading} onClick={go}>
          {loading ? 'running…' : '▶ Run quoting loop'}
        </button>
        <div className="ml-auto flex flex-wrap gap-1">
          {cids.map((c, i) => (
            <button key={c} onClick={() => setSel(c)}
              className={`chip ${cid === c ? 'border-sig/50 text-sig' : ''}`} style={{ borderColor: cid === c ? undefined : C.line }}>
              mkt {i}
            </button>
          ))}
        </div>
      </Panel>
      {error && <ErrorBox error={error} />}
      {data && (
        <Panel>
          <div className="mb-2 flex flex-wrap items-center gap-4 text-xs text-ink-2">
            <Legend color={C.series[1]} label="mid (belief)" />
            <Legend color={C.sig} label="engine bid/ask" dashed />
            <Legend color={C.muted} label={data.market_source === 'data' ? 'real book' : 'synthetic book'} />
            {data.market_source === 'data' && <span className="chip !py-0.5 border-sig/40 text-sig">real markets{data.hazard ? ' · hazard λ' : ''}</span>}
            {lastQ && (
              <span className="num text-2xs text-muted">risk_scale {fixed(lastQ.risk_scale ?? 1, 2)} · pos_cap {num(lastQ.pos_cap ?? 0, 0)}</span>
            )}
            <span className="ml-auto num text-2xs text-muted">{data.n_fills} fills · driftless synthetic book → quoting-behavior view, not a P&L race</span>
          </div>
          <div className="h-[300px] w-full" ref={chartRef}>
            <ResponsiveContainer>
              <LineChart data={rows} margin={{ left: 4, right: 12, top: 8, bottom: 4 }}>
                <CartesianGrid stroke={C.line} vertical={false} />
                <XAxis dataKey="i" stroke={C.axis} tick={{ fill: C.muted, fontSize: 11 }} tickLine={false} />
                <YAxis domain={[0, 1]} stroke={C.axis} tick={{ fill: C.muted, fontSize: 11 }} tickLine={false} width={40} />
                <Tooltip content={<QuoteTip />} />
                <Line type="monotone" dataKey="best_ask" stroke={C.muted} strokeWidth={1} dot={false} opacity={0.45} isAnimationActive={chartIn} animationDuration={700} animationEasing="ease-out" />
                <Line type="monotone" dataKey="best_bid" stroke={C.muted} strokeWidth={1} dot={false} opacity={0.45} isAnimationActive={chartIn} animationDuration={700} animationEasing="ease-out" />
                <Line type="monotone" dataKey="e_ask" stroke={C.sig} strokeWidth={1.5} dot={false} strokeDasharray="4 3" connectNulls isAnimationActive={chartIn} animationDuration={800} animationEasing="ease-out" />
                <Line type="monotone" dataKey="e_bid" stroke={C.sig} strokeWidth={1.5} dot={false} strokeDasharray="4 3" connectNulls isAnimationActive={chartIn} animationDuration={800} animationEasing="ease-out" />
                <Line type="monotone" dataKey="mid" stroke={C.series[1]} strokeWidth={2} dot={false} isAnimationActive={chartIn} animationDuration={800} animationEasing="ease-out" />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-2 text-2xs text-muted">The real <span className="font-mono text-ink-2">runner.run(mode="paper")</span> quoting a random-walking mid across {nMarkets} markets — the dashed <span className="text-sig">engine bid/ask</span> is the strategy's own two-sided quote (with inventory-scaled <span className="font-mono">risk_scale</span>/<span className="font-mono">pos_cap</span>), not the synthetic book.</p>
        </Panel>
      )}
    </div>
  )
}

// ---- small pieces ----------------------------------------------------------------------------
function Ctl({ label, v, min, max, step, on, fmt }: { label: string; v: number; min: number; max: number; step: number; on: (v: number) => void; fmt: (v: number) => string }) {
  return (
    <label className="min-w-[130px] flex-1">
      <div className="mb-1 flex justify-between"><span className="label">{label}</span><span className="num text-xs text-sig">{fmt(v)}</span></div>
      <input type="range" min={min} max={max} step={step} value={v} onChange={(e) => on(+e.target.value)}
        className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-line accent-sig" />
    </label>
  )
}
function Legend({ color, label, dashed }: { color: string; label: string; dashed?: boolean }) {
  return (
    <span className="flex items-center gap-1.5 text-ink-2">
      <span className="h-2 w-3 rounded-sm" style={dashed ? { border: `1.5px dashed ${color}` } : { background: color }} />{label}
    </span>
  )
}
function EqTip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div className="panel p-2.5 text-xs num">
      <div className="mb-1 text-2xs text-muted">tick {label} · mid {d.mid?.toFixed(3)}</div>
      <div className="text-profit">λ-ON {signed(d.on)} · {num(d.on_inv, 0)} tok</div>
      <div className="text-loss">λ-OFF {signed(d.off)} · {num(d.off_inv, 0)} tok</div>
    </div>
  )
}
function QuoteTip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div className="panel p-2.5 text-xs num">
      <div className="mb-1 text-2xs text-muted">tick {label}</div>
      <div className="text-ink">mid {d.mid?.toFixed(3)}</div>
      <div className="text-muted">bid {d.best_bid?.toFixed(3)} · ask {d.best_ask?.toFixed(3)} · σ {d.sigma?.toFixed(3)}</div>
    </div>
  )
}
