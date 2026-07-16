import { useState } from 'react'
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, useApi } from '../api/client'
import { useInViewOnce } from '../lib/motion'
import { useColors } from '../components/Theme'
import type { Colors } from '../lib/theme'
import { int } from '../lib/format'
import { Async, Caveat, Panel, Section, SourceTag } from '../components/ui'

const ARM_SHORT: Record<string, string> = {
  lambda_jump: 'λ-jump (surgical exit)', diffusion_only: 'diffusion (hold)',
  lambda_select: 'λ-select (blanket avoid)', lambda_jump_hazard: 'λ-jump · hazard',
}
const armColor = (C: Colors, ARM_COLORS: Record<string, string>, arm: string, i: number) =>
  ARM_COLORS[arm] || C.series[i % C.series.length]

export function Ablation() {
  const { C, ARM_COLORS } = useColors()
  const [live, setLive] = useState(false)
  const [nonce, setNonce] = useState(0)
  // nonce makes EVERY "run live replay" click refetch — the old [live]-only dep meant the second
  // click (true→true) was a no-op, so the button appeared dead after the first press.
  const q = useApi(() => api.ablation(live), [live, nonce])
  const runLive = () => { setLive(true); setNonce((n) => n + 1) }
  return (
    <Section id="ablation" kicker="the primary edge proof · replay_ablation"
      title="λ* sensitivity — surgical exit vs blanket avoidance"
      subtitle="A powered historical counterfactual over real disputes + matched controls, net of forgone rewards. Publish the whole curve, not one tuned point."
      right={
        <div className="flex items-center gap-2">
          {q.data?.source && <SourceTag source={q.data.source} />}
          <button className="btn !py-1 text-2xs" disabled={q.loading} onClick={runLive}>
            {q.loading && live ? 'running…' : '↻ run live replay'}
          </button>
        </div>
      }>
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
                <span className="font-semibold text-sig">{d.headline}</span>
                <span className="ml-2 text-2xs text-muted">{int(d.meta.n_disputes as number)} disputes · {int(d.meta.n_controls as number)} matched controls · {d.meta.span}</span>
              </div>
              <div className="grid gap-5 md:grid-cols-2">
                <MiniChart title="Net P&L (USD, net of forgone rewards)" data={pnl} arms={arms} frozen={Number(d.meta.lambda_star_frozen)} fmt={(v) => `$${(v / 1000).toFixed(0)}k`} />
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
                Live replay unavailable on this host — showing the {d.source === 'replay' ? 'committed real replay artifact' : 'published curve'} instead. Reason: <span className="font-mono">{d.live_error}</span>
              </Caveat>
            )}
            <Caveat kind="underpowered">{d.caveat} The arms converge at high λ* — a clean sanity check that the exit threshold stops mattering once it never fires.</Caveat>
          </div>
        )
      }}</Async>
    </Section>
  )
}

function MiniChart({ title, data, arms, frozen, fmt }: { title: string; data: any[]; arms: string[]; frozen: number; fmt: (v: number) => string }) {
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
            <Tooltip content={<Tip fmt={fmt} />} />
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
        <div key={p.dataKey} style={{ color: ARM_COLORS[p.dataKey] || C.ink2 }}>{ARM_SHORT[p.dataKey] || p.dataKey}: {fmt(p.value)}</div>
      ))}
    </div>
  )
}
