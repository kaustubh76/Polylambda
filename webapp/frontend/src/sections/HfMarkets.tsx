import { useEffect, useMemo, useState } from 'react'
import { api, useApi, type HfMarketRow } from '../api/client'
import { useDebounced } from '../lib/useDebounced'
import { useColors } from '../components/Theme'
import type { Colors } from '../lib/theme'
import { int, short } from '../lib/format'
import { Async, CopyButton, Panel, Section } from '../components/ui'

type SortKey = 'startDate' | 'endDate' | 'category'
const outcomeColor = (C: Colors, o?: string | null): string =>
  (({ YES: C.profit, NO: C.loss, TIE: C.warn, MULTI: C.muted } as Record<string, string>)[o || ''] || C.muted)

// A browsable window over the HF market universe (recent markets, resolution status, category) —
// the market_data ⋈ condition join surfaced directly. No indexer, just the shipped HF-derived cache.
export function HfMarkets() {
  const { C } = useColors()
  const [search, setSearch] = useState('')
  const dq = useDebounced(search, 300)
  const [category, setCategory] = useState<string | undefined>()
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({ key: 'startDate', dir: 'desc' })
  const [page, setPage] = useState(0)
  const limit = 25

  useEffect(() => { setPage(0) }, [dq, category, sort])

  const qs = useMemo(() => {
    const p = new URLSearchParams()
    if (dq) p.set('q', dq)
    if (category) p.set('category', category)
    p.set('sort', sort.key); p.set('desc', String(sort.dir === 'desc'))
    p.set('limit', String(limit)); p.set('offset', String(page * limit))
    return `?${p.toString()}`
  }, [dq, category, sort, page])
  const q = useApi(() => api.hfMarkets(qs), [qs])
  const toggleSort = (key: SortKey) => setSort((s) => (s.key === key ? { key, dir: s.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: 'desc' }))

  return (
    <Section id="hfmarkets" kicker="HF market_data ⋈ condition"
      title="Market browser"
      subtitle="Browse the most recent Polymarket markets straight from the HF dataset — creation date, category, and on-chain resolution — the market universe the dispute layer sits on top of.">
      <Async q={q}>{(d) => {
        const rows = d.rows
        const from = d.total === 0 ? 0 : page * limit + 1
        const to = Math.min((page + 1) * limit, d.total)
        return (
          <Panel pad={false}>
            <div className="flex flex-wrap items-center gap-2 border-b border-line p-4">
              <input className="field max-w-xs flex-1" placeholder="search market / slug / conditionId…"
                aria-label="search markets" value={search} onChange={(e) => setSearch(e.target.value)} />
              <select className="field w-auto capitalize" value={category ?? ''} aria-label="filter by category"
                onChange={(e) => setCategory(e.target.value || undefined)}>
                <option value="">all categories</option>
                {d.categories.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
              <span className="num ml-auto text-2xs text-muted">
                {d.total === 0 ? '0' : `${from}–${to} of ${int(d.total)}`} · newest {int(d.n_cached)} cached
              </span>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-left text-xs">
                <thead className="text-2xs uppercase tracking-wide text-muted">
                  <tr className="border-b border-line">
                    <th className="px-4 py-2">market</th>
                    <SortTh label="category" k="category" sort={sort} onSort={toggleSort} />
                    <SortTh label="created" k="startDate" sort={sort} onSort={toggleSort} />
                    <SortTh label="ends" k="endDate" sort={sort} onSort={toggleSort} />
                    <th className="px-2">resolution</th>
                  </tr>
                </thead>
                <tbody className="num">
                  {rows.map((r: HfMarketRow) => (
                    <tr key={r.conditionId} className="border-b border-line/40 transition hover:bg-elevated/40">
                      <td className="max-w-[360px] px-4 py-2 font-sans text-ink-2">
                        <div className="flex items-center gap-1.5">
                          <span className="truncate" title={r.marketName}>{r.marketName || <span className="font-mono text-muted">{short(r.conditionId, 10, 6)}</span>}</span>
                          {r.conditionId && <CopyButton value={r.conditionId} label="Copy conditionId" className="shrink-0" />}
                        </div>
                      </td>
                      <td className="px-2 capitalize text-muted">{r.category}</td>
                      <td className="px-2 text-muted">{r.startDate ?? '—'}</td>
                      <td className="px-2 text-muted">{r.endDate ?? '—'}</td>
                      <td className="px-2">
                        {r.resolved
                          ? <span style={{ color: outcomeColor(C, r.resolvedOutcome) }}>{r.resolvedOutcome ?? 'resolved'}</span>
                          : <span className="text-muted">open</span>}
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr><td colSpan={5} className="px-4 py-10 text-center text-sm text-muted">no markets match.</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            <div className="flex items-center justify-between p-3 text-xs">
              <button className="btn" disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>← prev</button>
              <span className="num text-muted">page {page + 1} / {Math.max(1, Math.ceil(d.total / limit))}</span>
              <button className="btn" disabled={(page + 1) * limit >= d.total} onClick={() => setPage((p) => p + 1)}>next →</button>
            </div>
          </Panel>
        )
      }}</Async>
    </Section>
  )
}

function SortTh({ label, k, sort, onSort }: { label: string; k: SortKey; sort: { key: SortKey; dir: 'asc' | 'desc' }; onSort: (k: SortKey) => void }) {
  const on = sort.key === k
  return (
    <th className="px-2">
      <button onClick={() => onSort(k)} aria-label={`sort by ${label}`}
        className={`inline-flex items-center gap-1 uppercase tracking-wide transition-colors hover:text-ink-2 ${on ? 'text-sig' : ''}`}>
        {label}<span aria-hidden className="text-[9px]">{on ? (sort.dir === 'desc' ? '▼' : '▲') : '⇅'}</span>
      </button>
    </th>
  )
}
