import { useMemo, useState } from 'react'
import { api, useApi } from '../api/client'
import { C } from '../lib/theme'
import { fixed, int, short } from '../lib/format'
import { Async, Panel, Section } from '../components/ui'

const ADAPTER_LABEL = (a: string) => (a?.startsWith('0x') ? 'legacy' : a)
const OUTCOME_COLOR: Record<string, string> = { YES: C.profit, NO: C.loss, UNRESOLVABLE: C.warn, OTHER: C.muted }

export function Disputes() {
  const [f, setF] = useState<{ category?: string; adapter?: string; year?: string; q: string }>({ q: '' })
  const [page, setPage] = useState(0)
  const limit = 25
  const qs = useMemo(() => {
    const p = new URLSearchParams()
    if (f.category) p.set('category', f.category)
    if (f.adapter) p.set('adapter', f.adapter)
    if (f.year) p.set('year', f.year)
    if (f.q) p.set('q', f.q)
    p.set('limit', String(limit)); p.set('offset', String(page * limit))
    return `?${p.toString()}`
  }, [f, page])
  const q = useApi(() => api.disputes(qs), [qs])
  const setFilter = (patch: Partial<typeof f>) => { setPage(0); setF({ ...f, ...patch }) }

  return (
    <Section id="disputes" kicker="the released dataset · polymarket-oov2-disputes-v1"
      title="Disputes explorer"
      subtitle="1,794 UMA OptimisticOracle disputes — the net-new dispute layer (not in the HF dataset), 100% joinable across all adapters, enriched with real market titles.">
      <Async q={q}>{(d) => (
        <Panel pad={false}>
          {/* filter bar */}
          <div className="flex flex-wrap items-center gap-2 border-b border-line p-4">
            <input className="field max-w-xs flex-1" placeholder="search title / conditionId / disputer…"
              value={f.q} onChange={(e) => setFilter({ q: e.target.value })} />
            <Facet label="category" value={f.category} options={d.facets.category} onPick={(v) => setFilter({ category: v })} />
            <Facet label="adapter" value={f.adapter} options={d.facets.adapter} labelFn={ADAPTER_LABEL} onPick={(v) => setFilter({ adapter: v })} />
            <select className="field w-auto" value={f.year ?? ''} onChange={(e) => setFilter({ year: e.target.value || undefined })}>
              <option value="">all years</option>
              {Object.keys(d.facets.year).sort().map((y) => <option key={y} value={y}>{y} ({d.facets.year[y]})</option>)}
            </select>
            <span className="num ml-auto text-2xs text-muted">{int(d.total)} rows</span>
          </div>

          {/* table */}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-left text-xs">
              <thead className="sticky top-0 bg-surface text-2xs uppercase tracking-wide text-muted">
                <tr className="border-b border-line">
                  <th className="px-4 py-2">market</th><th className="px-2">cat</th><th className="px-2">adapter</th>
                  <th className="px-2">date</th><th className="px-2">proposed</th>
                  <th className="px-2 text-right">pre→post</th><th className="px-2 text-right">jump (logit)</th>
                </tr>
              </thead>
              <tbody className="num">
                {d.rows.map((r, i) => (
                  <tr key={i} className="border-b border-line/40 transition hover:bg-elevated/40">
                    <td className="max-w-[320px] truncate px-4 py-2 font-sans text-ink-2" title={r.marketName || r.conditionId}>
                      {r.marketName || <span className="font-mono text-muted">{short(r.conditionId, 10, 6)}</span>}
                    </td>
                    <td className="px-2 capitalize text-muted">{r.category}</td>
                    <td className="px-2 text-muted">{ADAPTER_LABEL(r.adapter)}</td>
                    <td className="px-2 text-muted">{r.disputeDate}</td>
                    <td className="px-2"><span style={{ color: OUTCOME_COLOR[r.proposedOutcome] || C.muted }}>{r.proposedOutcome ?? '—'}</span></td>
                    <td className="px-2 text-right text-ink-2">{r.preDisputePrice != null ? `${fixed(r.preDisputePrice, 2)}→${fixed(r.postDisputePrice, 2)}` : '—'}</td>
                    <td className="px-2 text-right" style={{ color: r.realizedJumpLogit == null ? C.muted : Math.abs(r.realizedJumpLogit) > 0.5 ? C.warn : C.ink2 }}>
                      {r.realizedJumpLogit != null ? fixed(r.realizedJumpLogit, 2) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* pagination */}
          <div className="flex items-center justify-between p-3 text-xs">
            <button className="btn" disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>← prev</button>
            <span className="num text-muted">page {page + 1} / {Math.max(1, Math.ceil(d.total / limit))}</span>
            <button className="btn" disabled={(page + 1) * limit >= d.total} onClick={() => setPage((p) => p + 1)}>next →</button>
          </div>
        </Panel>
      )}</Async>
    </Section>
  )
}

function Facet({ label, value, options, onPick, labelFn }: {
  label: string; value?: string; options: Record<string, number>; onPick: (v?: string) => void; labelFn?: (v: string) => string
}) {
  return (
    <select className="field w-auto capitalize" value={value ?? ''} onChange={(e) => onPick(e.target.value || undefined)}>
      <option value="">all {label}</option>
      {Object.entries(options).filter(([k]) => k !== 'null').sort((a, b) => b[1] - a[1]).map(([k, n]) => (
        <option key={k} value={k}>{labelFn ? labelFn(k) : k} ({n})</option>
      ))}
    </select>
  )
}
