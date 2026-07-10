import { useState } from 'react'
import { api, useApi, type BaseRateRow } from '../api/client'
import { useInViewOnce } from '../lib/motion'
import { useColors } from '../components/Theme'
import { int, pct } from '../lib/format'
import { Async, Panel, Section, SourceTag } from '../components/ui'

// Hand-built horizontal bar chart with Wilson-CI whiskers (dataviz skill: build marks in plain
// HTML). Linear scale so the ~22× ordering reads as bar-length; CI drawn as an inked whisker.
export function BaseRates() {
  const q = useApi(api.baserates, [])
  const [barsRef, grown] = useInViewOnce<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  return (
    <Section id="baserates" kicker="the λ_select signal"
      title="Category dispute base rates"
      subtitle="Disputes ÷ resolved markets per category, with Wilson 95% intervals — the honest per-market dispute prior that drives market selection & sizing."
      right={q.data && <SourceTag source={q.data.source} />}>
      <Async q={q}>{(d) => {
        const rows = d.rows
        const maxCi = Math.max(...rows.map((r) => r.ci_high))
        const domain = Math.ceil(maxCi * 100 * 2) / 2 // nice max in %, e.g. 3.0
        const ticks = Array.from({ length: Math.round(domain / 0.5) + 1 }, (_, i) => i * 0.5)
        const x = (frac: number) => `${(frac * 100 / domain) * 100}%`
        return (
          <Panel>
            <div className="mb-4 text-sm text-ink-2">
              <span className="font-semibold text-sig">{d.headline}</span> — the single most legible edge the strategy has.
            </div>
            {/* axis ticks */}
            <div className="relative mb-1 ml-[112px] mr-[64px] h-4 text-2xs text-muted">
              {ticks.map((t) => (
                <span key={t} className="absolute -translate-x-1/2 num" style={{ left: x(t / 100) }}>{t}%</span>
              ))}
            </div>
            <div className="space-y-1.5" ref={barsRef}>
              {rows.map((r, i) => (
                <Row key={r.category} r={r} x={x} active={hover === i} grown={grown} idx={i}
                  onEnter={() => setHover(i)} onLeave={() => setHover(null)} ticks={ticks} />
              ))}
            </div>
            {hover != null && <Tip r={rows[hover]} />}
            <p className="mt-3 text-2xs text-muted">
              Bar length = point estimate; the inked whisker is the Wilson 95% interval; color follows the category
              (stable), not its rank. Hover a row for exact counts.
            </p>
          </Panel>
        )
      }}</Async>
    </Section>
  )
}

function Row({ r, x, active, grown, idx, onEnter, onLeave, ticks }: {
  r: BaseRateRow; x: (f: number) => string; active: boolean; grown: boolean; idx: number
  onEnter: () => void; onLeave: () => void; ticks: number[]
}) {
  const { C, CATEGORY_COLORS } = useColors()
  const col = CATEGORY_COLORS[r.category] || C.sig
  return (
    <div className={`grid grid-cols-[104px_1fr_60px] items-center gap-2 rounded-md py-1 transition ${active ? 'bg-elevated/50' : ''}`}
      onMouseEnter={onEnter} onMouseLeave={onLeave}>
      <div className="flex items-center justify-end gap-1.5 pr-1 text-xs capitalize text-ink-2">
        <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: col }} />{r.category}
      </div>
      <div className="relative h-6 rounded bg-bg">
        {/* gridlines */}
        {ticks.slice(1).map((t) => (
          <div key={t} className="absolute top-0 h-full w-px bg-line/60" style={{ left: x(t / 100) }} />
        ))}
        {/* bar — grows in left→right, staggered by row, when scrolled into view */}
        <div className="absolute top-1 h-4 rounded-r" style={{
          width: grown ? x(r.rate) : '0%', background: col, opacity: active ? 1 : 0.85,
          transition: `width 0.7s cubic-bezier(0.16,1,0.3,1) ${idx * 55}ms`,
        }} />
        {/* CI whisker */}
        <div className="absolute top-1/2 h-px -translate-y-1/2" style={{ left: x(r.ci_low), width: `calc(${x(r.ci_high)} - ${x(r.ci_low)})`, background: C.ink2 }} />
        <div className="absolute top-1/2 h-2 w-px -translate-y-1/2" style={{ left: x(r.ci_low), background: C.ink2 }} />
        <div className="absolute top-1/2 h-2 w-px -translate-y-1/2" style={{ left: x(r.ci_high), background: C.ink2 }} />
      </div>
      <div className="num text-right text-xs" style={{ color: col }}>{pct(r.rate, r.rate < 0.01 ? 3 : 2)}</div>
    </div>
  )
}

function Tip({ r }: { r: BaseRateRow }) {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-line bg-elevated/50 p-2.5 text-2xs">
      <span className="font-semibold capitalize text-ink">{r.category}</span>
      <span className="num text-sig">{pct(r.rate, 3)}</span>
      <span className="num text-muted">Wilson 95%: [{pct(r.ci_low, 2)}, {pct(r.ci_high, 2)}]</span>
      <span className="num text-ink-2">{int(r.disputes)} disputes / {int(r.resolved)} resolved</span>
    </div>
  )
}
