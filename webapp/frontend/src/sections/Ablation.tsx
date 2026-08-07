import { useState } from 'react'
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, useApi } from '../api/client'
import type { Backtest } from '../api/client'
import { useInViewOnce } from '../lib/motion'
import { useColors } from '../components/Theme'
import type { Colors } from '../lib/theme'
import { int } from '../lib/format'
import { Async, Caveat, Panel, Pill, Section, SourceTag, Stat } from '../components/ui'

const ARM_SHORT: Record<string, string> = {
  lambda_jump: 'λ-jump (surgical exit)', diffusion_only: 'diffusion (hold)',
  lambda_select: 'λ-select (blanket avoid)', lambda_jump_hazard: 'λ-jump · hazard',
}
const armColor = (C: Colors, ARM_COLORS: Record<string, string>, arm: string, i: number) =>
  ARM_COLORS[arm] || C.series[i % C.series.length]

// signed USD, thousands-separated, no decimals (the clean-USD P&L is dollars, not cents)
const usd = (v: number) => `${v < 0 ? '−' : '+'}$${Math.abs(Math.round(v)).toLocaleString('en-US')}`
const usdAxis = (v: number) => `$${Math.round(v).toLocaleString('en-US')}`

export function Ablation() {
  const { C, ARM_COLORS } = useColors()
  const [live, setLive] = useState(false)
  const [nonce, setNonce] = useState(0)
  // nonce makes EVERY "run live replay" click refetch — the old [live]-only dep meant the second
  // click (true→true) was a no-op, so the button appeared dead after the first press.
  const q = useApi(() => api.ablation(live), [live, nonce])
  const bt = useApi(() => api.backtest(), [])
  const runLive = () => { setLive(true); setNonce((n) => n + 1) }
  return (
    <Section id="ablation" kicker="the edge proof · forwardtest.replay_full (clean USD)"
      title="Backtested P&L"
      subtitle="Production quoter + exit gate replayed over the real tape with queue-pessimistic fills. Clean USD (cash + inventory·mark), no reward income, no hindsight."
      right={bt.data?.available && bt.data.run_date ? <Pill dot color="var(--sig)">backtest · {bt.data.run_date}</Pill> : undefined}>
      <div className="space-y-6">
        {/* ── PRIMARY: the real clean-USD backtest P&L ─────────────────────────────────── */}
        <Async q={bt}>{(d) => <BacktestPanel d={d} C={C} ARM_COLORS={ARM_COLORS} />}</Async>

        {/* ── SECONDARY (demoted): the counterfactual exit-policy sensitivity sweep ─────── */}
        <div>
          <div className="mb-2 flex items-center gap-2">
            <span className="text-sm font-semibold text-ink-2">Exit-policy sensitivity vs λ*</span>
            <span className="text-2xs text-muted">counterfactual, net of forgone rewards (not a fillable P&L)</span>
            <div className="ml-auto flex items-center gap-2">
              {q.data?.source && <SourceTag source={q.data.source} />}
              <button className="btn !py-1 text-2xs" disabled={q.loading} onClick={runLive}>
                {q.loading && live ? 'running…' : '↻ run live replay'}
              </button>
            </div>
          </div>
          <Async q={q}>{(d) => {
            const arms = d.arms.map((a) => a.arm)
            const pnl = d.lambda_star_grid.map((ls) => {
              const row: any = { ls }
              d.arms.forEach((a) => { row[a.arm] = a.points.find((p) => p.lambda_star === ls)?.pnl_net_of_rewards })
              return row
            })
            const sharpe = d.lambda_star_grid.map((ls) => {
              const row: any = { ls }
              d.arms.forEach((a) => { row[a.arm] = a.points.find((p) => p.lambda_star === ls)?.sharpe })
              return row
            })
            return (
              <div className="space-y-4">
                <Panel>
                  <div className="mb-3 text-sm">
                    <span className="font-semibold text-ink-2">{d.headline}</span>
                    <span className="ml-2 text-2xs text-muted">{int(d.meta.n_disputes as number)} disputes · {int(d.meta.n_controls as number)} matched controls · {d.meta.span}{d.meta.run_date ? ` · powered replay ${d.meta.run_date}` : ''}</span>
                  </div>
                  <div className="grid gap-5 md:grid-cols-2">
                    <MiniChart title="Counterfactual $ (net of forgone rewards)" data={pnl} arms={arms} frozen={Number(d.meta.lambda_star_frozen)} fmt={(v) => `$${(v / 1000).toFixed(0)}k`} />
                    <MiniChart title="Sharpe" data={sharpe} arms={arms} frozen={Number(d.meta.lambda_star_frozen)} fmt={(v) => v.toFixed(2)} />
                  </div>
                  <div className="mt-3 flex flex-wrap gap-4 text-xs">
                    {arms.map((a, i) => (
                      <span key={a} className="flex items-center gap-1.5 text-ink-2">
                        <span className="h-2 w-3 rounded-sm" style={{ background: armColor(C, ARM_COLORS, a, i) }} />{ARM_SHORT[a] || a}
                      </span>
                    ))}
                    <span className="ml-auto flex items-center gap-1.5 text-muted"><span className="h-3 w-px bg-warn" />frozen λ*={String(d.meta.lambda_star_frozen)}</span>
                  </div>
                </Panel>
                {live && d.live_error && (
                  <Caveat kind="note">
                    Live replay unavailable here; showing the {d.source === 'replay' ? 'committed real replay artifact' : 'published curve'} instead. Reason: <span className="font-mono">{d.live_error}</span>
                  </Caveat>
                )}
                <Caveat kind="underpowered">{d.caveat} The arms converge at high λ*: a sanity check that the exit stops mattering once it never fires.</Caveat>
              </div>
            )
          }}</Async>
        </div>
      </div>
    </Section>
  )
}

function BacktestPanel({ d, C, ARM_COLORS }: { d: Backtest; C: Colors; ARM_COLORS: Record<string, string> }) {
  if (!d.available || !d.headline) {
    return <Caveat kind="null">{d.note || 'No committed backtest artifact yet; run forwardtest.replay_full to generate the clean-USD P&L.'}</Caveat>
  }
  const h = d.headline
  const delta = h.delta_jump_minus_diffusion
  const meta = d.meta || {}
  const nDisp = meta.n_disputes_with_paths as number | undefined
  const nCtrl = meta.n_controls_with_paths as number | undefined
  const grid = d.lambda_star_grid || []
  const arms = (d.arms || []).map((a) => a.arm)
  const pnlSeries = grid.map((ls) => {
    const row: any = { ls }
    ;(d.arms || []).forEach((a) => { row[a.arm] = a.points.find((p) => p.lambda_star === ls)?.pnl_usd })
    return row
  })
  return (
    <div className="space-y-4">
      {/* the edge — the single number this whole section exists to establish */}
      {delta && (
        <Panel>
          <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
            <div>
              <div className="label">λ-jump minus always-hold · the edge</div>
              <div className={`num mt-1 text-4xl font-semibold ${delta.pnl_usd >= 0 ? 'text-profit' : 'text-loss'}`}>{usd(delta.pnl_usd)}</div>
              <div className="mt-1.5 flex flex-wrap items-center gap-2 text-2xs text-muted">
                {delta.ci_low != null && delta.ci_high != null &&
                  <Pill dot color={delta.ci_low > 0 ? 'var(--profit)' : delta.ci_high < 0 ? 'var(--loss)' : 'var(--warn)'}>
                    95% CI [{usd(delta.ci_low)}, {usd(delta.ci_high)}]
                  </Pill>}
                <span>clean USD over {nDisp != null ? int(nDisp) : '—'} disputes + {nCtrl != null ? int(nCtrl) : '—'} controls · frozen λ*={String(h.lambda_star_frozen)}</span>
              </div>
            </div>
            <p className="max-w-sm text-2xs leading-relaxed text-muted">
              {delta.pnl_usd >= 0
                ? <>The reward-aware exit avoids <b className="text-profit">{usd(delta.pnl_usd)}</b> of directional loss vs holding{delta.ci_low != null && delta.ci_low > 0 ? '; CI above zero, so the term earns its keep' : delta.ci_low != null && delta.ci_high != null && delta.ci_low <= 0 ? '; CI includes zero, so read the edge as directional, not proven' : ''}.</>
                : <>This run: the exit came out <b className="text-loss">{usd(delta.pnl_usd)}</b> vs holding; the term didn't pay here, reported honestly rather than tuned away.</>}
            </p>
          </div>
        </Panel>
      )}

      {/* the actual P&L per arm at the frozen threshold — shown honestly, negatives and all */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {h.at_frozen.map((r) => (
          <Stat key={r.arm} label={ARM_SHORT[r.arm] || r.arm_label || r.arm}
            value={usd(r.pnl_usd)} tone={r.pnl_usd >= 0 ? 'profit' : 'loss'}
            sub={`Sharpe ${r.sharpe_cross.toFixed(2)} · maxDD $${Math.round(r.max_drawdown_usd).toLocaleString('en-US')} · win ${(r.win_rate * 100).toFixed(0)}% · ${int(r.n_fills)} fills`} />
        ))}
      </div>

      {/* P&L across the λ* grid (the full curve, not one tuned point) */}
      <Panel>
        <MiniChart title="Backtest P&L (USD) across λ*" data={pnlSeries} arms={arms}
          frozen={Number(h.lambda_star_frozen)} fmt={usdAxis} valueFmt={usd} />
        <div className="mt-3 flex flex-wrap gap-4 text-xs">
          {arms.map((a, i) => (
            <span key={a} className="flex items-center gap-1.5 text-ink-2">
              <span className="h-2 w-3 rounded-sm" style={{ background: armColor(C, ARM_COLORS, a, i) }} />{ARM_SHORT[a] || a}
            </span>
          ))}
          <span className="ml-auto flex items-center gap-1.5 text-muted"><span className="h-3 w-px bg-warn" />frozen λ*={String(h.lambda_star_frozen)}</span>
        </div>
      </Panel>

      {d.caveat && <Caveat kind="calibration">{d.caveat}</Caveat>}
    </div>
  )
}

function MiniChart({ title, data, arms, frozen, fmt, valueFmt }: { title: string; data: any[]; arms: string[]; frozen: number; fmt: (v: number) => string; valueFmt?: (v: number) => string }) {
  const { C, ARM_COLORS } = useColors()
  const [ref, inView] = useInViewOnce<HTMLDivElement>()
  return (
    <div>
      <div className="mb-1 text-2xs text-muted">{title}</div>
      <div className="h-[230px] w-full" ref={ref}>
        <ResponsiveContainer>
          <LineChart data={data} margin={{ left: 6, right: 12, top: 8, bottom: 4 }}>
            <CartesianGrid stroke={C.line} vertical={false} />
            <XAxis dataKey="ls" type="number" scale="log" domain={['auto', 'auto']}
              ticks={[0.0005, 0.002, 0.01]} tickFormatter={(v) => `${v}`}
              stroke={C.axis} tick={{ fill: C.muted, fontSize: 10 }} tickLine={false} />
            <YAxis tickFormatter={fmt} stroke={C.axis} tick={{ fill: C.muted, fontSize: 10 }} tickLine={false} width={44} />
            <Tooltip content={<Tip fmt={valueFmt || fmt} />} />
            <ReferenceLine x={frozen} stroke={C.warn} strokeDasharray="3 3" />
            {arms.map((a, i) => (
              <Line key={a} type="monotone" dataKey={a} stroke={armColor(C, ARM_COLORS, a, i)} strokeWidth={2}
                dot={{ r: 3, fill: armColor(C, ARM_COLORS, a, i), strokeWidth: 0 }}
                isAnimationActive={inView} animationDuration={700} animationEasing="ease-out" />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function Tip({ active, payload, label, fmt }: any) {
  const { C, ARM_COLORS } = useColors()
  if (!active || !payload?.length) return null
  return (
    <div className="panel p-2.5 text-xs num">
      <div className="mb-1 text-2xs text-muted">λ* = {label}</div>
      {payload.map((p: any) => (
        <div key={p.dataKey} style={{ color: ARM_COLORS[p.dataKey] || C.ink2 }}>{ARM_SHORT[p.dataKey] || p.dataKey}: {p.value == null ? '—' : fmt(p.value)}</div>
      ))}
    </div>
  )
}
