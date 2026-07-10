import { useState } from 'react'
import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from 'recharts'
import { api, useApi, type SigmaPoint } from '../api/client'
import { useInViewOnce } from '../lib/motion'
import { CATEGORY_COLORS, C } from '../lib/theme'
import { Async, Panel, Section } from '../components/ui'

export function SigmaSurface() {
  const q = useApi(api.sigma, [])
  const [chartRef, chartIn] = useInViewOnce<HTMLDivElement>()
  const [off, setOff] = useState<Set<string>>(new Set())
  const toggle = (c: string) => setOff((s) => { const n = new Set(s); n.has(c) ? n.delete(c) : n.add(c); return n })

  return (
    <Section id="sigma" kicker="belief-volatility · the σ estimator's prior"
      title="σ surface — volatility by category × price"
      subtitle="The shrink target for per-market belief-volatility: how fast log-odds move, stratified by category and price level. Drives the diffusion half of the spread.">
      <Async q={q}>{(d) => {
        const byCat: Record<string, SigmaPoint[]> = {}
        d.points.forEach((p) => { (byCat[p.category] ??= []).push(p) })
        return (
          <Panel>
            <div className="mb-3 flex flex-wrap gap-1.5">
              {d.categories.map((c) => (
                <button key={c} onClick={() => toggle(c)}
                  className={`chip capitalize ${off.has(c) ? 'opacity-40' : ''}`}>
                  <span className="h-2 w-2 rounded-full" style={{ background: CATEGORY_COLORS[c] || C.muted }} />{c}
                </button>
              ))}
            </div>
            <div className="h-[340px] w-full" ref={chartRef}>
              <ResponsiveContainer>
                <ScatterChart margin={{ left: 6, right: 16, top: 8, bottom: 16 }}>
                  <CartesianGrid stroke={C.line} />
                  <XAxis type="number" dataKey="price" name="price" domain={[0, 1]} tickFormatter={(v) => v.toFixed(1)}
                    stroke={C.axis} tick={{ fill: C.muted, fontSize: 11 }} tickLine={false}
                    label={{ value: 'price (YES)', fill: C.muted, fontSize: 10, position: 'insideBottom', offset: -8 }} />
                  <YAxis type="number" dataKey="sigma" name="σ" scale="log" domain={[0.001, 2]} allowDataOverflow
                    tickFormatter={(v) => v.toString()} stroke={C.axis} tick={{ fill: C.muted, fontSize: 11 }} tickLine={false}
                    label={{ value: 'σ (logit)', angle: -90, fill: C.muted, fontSize: 10, position: 'insideLeft' }} />
                  <ZAxis range={[24, 24]} />
                  <Tooltip content={<SP />} cursor={{ stroke: C.line }} />
                  {d.categories.filter((c) => !off.has(c)).map((c) => (
                    <Scatter key={c} name={c} data={byCat[c]} fill={CATEGORY_COLORS[c] || C.muted} fillOpacity={0.55}
                      isAnimationActive={chartIn} animationDuration={600} animationEasing="ease-out" />
                  ))}
                </ScatterChart>
              </ResponsiveContainer>
            </div>
            <p className="mt-2 text-2xs text-muted">{d.note} · {d.n} prior samples · toggle categories above.</p>
          </Panel>
        )
      }}</Async>
    </Section>
  )
}

function SP({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload as SigmaPoint
  return (
    <div className="panel p-2.5 text-xs num">
      <div className="capitalize" style={{ color: CATEGORY_COLORS[p.category] || C.ink }}>{p.category}</div>
      <div className="text-muted">price {p.price.toFixed(3)} · σ {p.sigma.toFixed(4)}</div>
    </div>
  )
}
