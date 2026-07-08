import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, useApi } from '../api/client'
import { ARM_COLORS, C } from '../lib/theme'
import { int } from '../lib/format'
import { Async, Caveat, Panel, Section } from '../components/ui'

const ARMS = ['lambda_jump', 'diffusion_only', 'lambda_select'] as const
const ARM_SHORT: Record<string, string> = { lambda_jump: 'λ-jump (surgical exit)', diffusion_only: 'diffusion (hold)', lambda_select: 'λ-select (blanket avoid)' }

export function Ablation() {
  const q = useApi(api.ablation, [])
  return (
    <Section id="ablation" kicker="the primary edge proof · replay_ablation"
      title="λ* sensitivity — surgical exit vs blanket avoidance"
      subtitle="A powered historical counterfactual over real disputes + matched controls, net of forgone rewards. Publish the whole curve, not one tuned point.">
      <Async q={q}>{(d) => {
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
                <MiniChart title="Net P&L (USD, net of forgone rewards)" data={pnl} frozen={Number(d.meta.lambda_star_frozen)} fmt={(v) => `$${(v / 1000).toFixed(0)}k`} />
                <MiniChart title="Sharpe" data={sharpe} frozen={Number(d.meta.lambda_star_frozen)} fmt={(v) => v.toFixed(2)} />
              </div>
              <div className="mt-3 flex flex-wrap gap-4 text-xs">
                {ARMS.map((a) => (
                  <span key={a} className="flex items-center gap-1.5 text-ink-2">
                    <span className="h-2 w-3 rounded-sm" style={{ background: ARM_COLORS[a] }} />{ARM_SHORT[a]}
                  </span>
                ))}
                <span className="ml-auto flex items-center gap-1.5 text-muted"><span className="h-3 w-px bg-warn" />frozen λ*=0.002</span>
              </div>
            </Panel>
            <Caveat kind="underpowered">{d.caveat} The arms converge at high λ* — a clean sanity check that the exit threshold stops mattering once it never fires.</Caveat>
          </div>
        )
      }}</Async>
    </Section>
  )
}

function MiniChart({ title, data, frozen, fmt }: { title: string; data: any[]; frozen: number; fmt: (v: number) => string }) {
  return (
    <div>
      <div className="mb-1 text-2xs text-muted">{title}</div>
      <div className="h-[230px] w-full">
        <ResponsiveContainer>
          <LineChart data={data} margin={{ left: 6, right: 12, top: 8, bottom: 4 }}>
            <CartesianGrid stroke={C.line} vertical={false} />
            <XAxis dataKey="ls" type="number" scale="log" domain={['auto', 'auto']}
              ticks={[0.0005, 0.002, 0.01]} tickFormatter={(v) => `${v}`}
              stroke={C.axis} tick={{ fill: C.muted, fontSize: 10 }} tickLine={false} />
            <YAxis tickFormatter={fmt} stroke={C.axis} tick={{ fill: C.muted, fontSize: 10 }} tickLine={false} width={44} />
            <Tooltip content={<Tip fmt={fmt} />} />
            <ReferenceLine x={frozen} stroke={C.warn} strokeDasharray="3 3" />
            {ARMS.map((a) => (
              <Line key={a} type="monotone" dataKey={a} stroke={ARM_COLORS[a]} strokeWidth={2}
                dot={{ r: 3, fill: ARM_COLORS[a], strokeWidth: 0 }} isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function Tip({ active, payload, label, fmt }: any) {
  if (!active || !payload?.length) return null
  return (
    <div className="panel p-2.5 text-xs num">
      <div className="mb-1 text-2xs text-muted">λ* = {label}</div>
      {payload.map((p: any) => (
        <div key={p.dataKey} style={{ color: ARM_COLORS[p.dataKey] }}>{ARM_SHORT[p.dataKey]}: {fmt(p.value)}</div>
      ))}
    </div>
  )
}
