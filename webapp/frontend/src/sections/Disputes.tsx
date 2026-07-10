import { useEffect, useMemo, useState } from 'react'
import { api, useApi } from '../api/client'
import { useDebounced } from '../lib/useDebounced'
import { C } from '../lib/theme'
import { fixed, int, short } from '../lib/format'
import { Async, CopyButton, Panel, Section } from '../components/ui'

interface DisputeRow {
  marketName?: string; conditionId: string; category: string; adapter: string; disputeDate: string
  proposedOutcome: string; preDisputePrice: number | null; postDisputePrice: number | null; realizedJumpLogit: number | null
}

const ADAPTER_LABEL = (a: string) => (a?.startsWith('0x') ? 'legacy' : a)
const OUTCOME_COLOR: Record<string, string> = { YES: C.profit, NO: C.loss, UNRESOLVABLE: C.warn, OTHER: C.muted }

type Filters = { category?: string; adapter?: string; year?: string }
type SortKey = 'date' | 'prepost' | 'jump'
type Sort = { key: SortKey; dir: 'asc' | 'desc' }

// client-side sort of the loaded page (nulls always sink to the bottom)
const ACCESS: Record<SortKey, (r: DisputeRow) => number | null> = {
  date: (r) => (r.disputeDate ? Date.parse(r.disputeDate) : null),
  prepost: (r) => r.postDisputePrice ?? null,
  jump: (r) => (r.realizedJumpLogit != null ? Math.abs(r.realizedJumpLogit) : null),
}

export function Disputes() {
  const [search, setSearch] = useState('')
  const dq = useDebounced(search, 300)
  const [f, setF] = useState<Filters>({})
  const [page, setPage] = useState(0)
  const [sort, setSort] = useState<Sort | null>(null)
  const limit = 25

  // any filter/search change resets to page 1
  useEffect(() => { setPage(0) }, [dq, f])

  const qs = useMemo(() => {
    const p = new URLSearchParams()
    if (f.category) p.set('category', f.category)
    if (f.adapter) p.set('adapter', f.adapter)
    if (f.year) p.set('year', f.year)
    if (dq) p.set('q', dq)
    p.set('limit', String(limit)); p.set('offset', String(page * limit))
    return `?${p.toString()}`
  }, [f, dq, page])
  const q = useApi(() => api.disputes(qs), [qs])
  const setFilter = (patch: Partial<Filters>) => setF((prev) => ({ ...prev, ...patch }))

  const activeChips = [
    f.category && { k: 'category' as const, label: `cat · ${f.category}` },
    f.adapter && { k: 'adapter' as const, label: `adapter · ${ADAPTER_LABEL(f.adapter)}` },
    f.year && { k: 'year' as const, label: `year · ${f.year}` },
    dq ? { k: 'q' as const, label: `“${dq}”` } : null,
  ].filter(Boolean) as { k: 'category' | 'adapter' | 'year' | 'q'; label: string }[]

  const clearOne = (k: string) => { if (k === 'q') setSearch(''); else setFilter({ [k]: undefined } as Partial<Filters>) }
  const clearAll = () => { setSearch(''); setF({}); setSort(null) }
  const toggleSort = (key: SortKey) => setSort((s) => (s?.key === key ? (s.dir === 'desc' ? { key, dir: 'asc' } : null) : { key, dir: 'desc' }))

  return (
    <Section id="disputes" kicker="the released dataset · polymarket-oov2-disputes-v1"
      title="Disputes explorer"
      subtitle="1,794 UMA OptimisticOracle disputes — the net-new dispute layer (not in the HF dataset), 100% joinable across all adapters, enriched with real market titles.">
      <Async q={q}>{(d) => {
        const all = d.rows as DisputeRow[]
        const rows = sort ? sortRows(all, sort) : all
        const from = d.total === 0 ? 0 : page * limit + 1
        const to = Math.min((page + 1) * limit, d.total)
        return (
          <Panel pad={false}>
            {/* filter bar */}
            <div className="flex flex-wrap items-center gap-2 border-b border-line p-4">
              <input className="field max-w-xs flex-1" placeholder="search title / conditionId / disputer…"
                aria-label="search disputes" value={search} onChange={(e) => setSearch(e.target.value)} />
              <Facet label="category" value={f.category} options={d.facets.category} onPick={(v) => setFilter({ category: v })} />
              <Facet label="adapter" value={f.adapter} options={d.facets.adapter} labelFn={ADAPTER_LABEL} onPick={(v) => setFilter({ adapter: v })} />
              <select className="field w-auto" value={f.year ?? ''} onChange={(e) => setFilter({ year: e.target.value || undefined })} aria-label="filter by year">
                <option value="">all years</option>
                {Object.keys(d.facets.year).sort().map((y) => <option key={y} value={y}>{y} ({d.facets.year[y]})</option>)}
              </select>
              <span className="num ml-auto text-2xs text-muted">{d.total === 0 ? '0 rows' : `${from}–${to} of ${int(d.total)}`}</span>
            </div>

            {/* active filters */}
            {activeChips.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5 border-b border-line/60 px-4 py-2">
                {activeChips.map((c) => (
                  <button key={c.k} onClick={() => clearOne(c.k)} className="chip capitalize hover:border-loss/50 hover:text-loss" aria-label={`remove filter ${c.label}`}>
                    {c.label} <span aria-hidden>✕</span>
                  </button>
                ))}
                <button onClick={clearAll} className="text-2xs text-muted underline decoration-line underline-offset-2 hover:text-sig">clear all</button>
              </div>
            )}

            {/* table */}
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] text-left text-xs">
                <thead className="sticky top-0 bg-surface text-2xs uppercase tracking-wide text-muted">
                  <tr className="border-b border-line">
                    <th className="px-4 py-2">market</th><th className="px-2">cat</th><th className="px-2">adapter</th>
                    <SortTh label="date" k="date" sort={sort} onSort={toggleSort} />
                    <th className="px-2">proposed</th>
                    <SortTh label="pre→post" k="prepost" sort={sort} onSort={toggleSort} align="right" />
                    <SortTh label="jump (logit)" k="jump" sort={sort} onSort={toggleSort} align="right" />
                  </tr>
                </thead>
                <tbody className="num">
                  {rows.map((r, i) => (
                    <tr key={i} className="border-b border-line/40 transition hover:bg-elevated/40">
                      <td className="max-w-[320px] px-4 py-2 font-sans text-ink-2">
                        <div className="flex items-center gap-1.5">
                          <span className="truncate" title={r.marketName || r.conditionId}>
                            {r.marketName || <span className="font-mono text-muted">{short(r.conditionId, 10, 6)}</span>}
                          </span>
                          {r.conditionId && <CopyButton value={r.conditionId} label="Copy conditionId" className="shrink-0" />}
                        </div>
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
                  {d.rows.length === 0 && (
                    <tr><td colSpan={7} className="px-4 py-10 text-center text-sm text-muted">
                      no disputes match these filters.{' '}
                      {activeChips.length > 0 && <button onClick={clearAll} className="text-sig underline decoration-sig/40 underline-offset-2 hover:decoration-sig">Clear filters</button>}
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* pagination */}
            <div className="flex items-center justify-between p-3 text-xs">
              <button className="btn" disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>← prev</button>
              <span className="num text-muted">page {page + 1} / {Math.max(1, Math.ceil(d.total / limit))}{sort && <span className="ml-2 text-2xs">· sorted (this page)</span>}</span>
              <button className="btn" disabled={(page + 1) * limit >= d.total} onClick={() => setPage((p) => p + 1)}>next →</button>
            </div>
          </Panel>
        )
      }}</Async>
    </Section>
  )
}

function sortRows(rows: DisputeRow[], sort: Sort): DisputeRow[] {
  const acc = ACCESS[sort.key]
  const mul = sort.dir === 'asc' ? 1 : -1
  return [...rows].sort((a, b) => {
    const va = acc(a), vb = acc(b)
    if (va == null && vb == null) return 0
    if (va == null) return 1 // nulls last
    if (vb == null) return -1
    return (va - vb) * mul
  })
}

function SortTh({ label, k, sort, onSort, align = 'left' }: {
  label: string; k: SortKey; sort: Sort | null; onSort: (k: SortKey) => void; align?: 'left' | 'right'
}) {
  const on = sort?.key === k
  return (
    <th className={`px-2 ${align === 'right' ? 'text-right' : ''}`}>
      <button onClick={() => onSort(k)} aria-label={`sort by ${label}`}
        className={`inline-flex items-center gap-1 uppercase tracking-wide transition-colors hover:text-ink-2 ${on ? 'text-sig' : ''}`}>
        {label}<span aria-hidden className="text-[9px]">{on ? (sort!.dir === 'desc' ? '▼' : '▲') : '⇅'}</span>
      </button>
    </th>
  )
}

function Facet({ label, value, options, onPick, labelFn }: {
  label: string; value?: string; options: Record<string, number>; onPick: (v?: string) => void; labelFn?: (v: string) => string
}) {
  return (
    <select className="field w-auto capitalize" value={value ?? ''} onChange={(e) => onPick(e.target.value || undefined)} aria-label={`filter by ${label}`}>
      <option value="">all {label}</option>
      {Object.entries(options).filter(([k]) => k !== 'null').sort((a, b) => b[1] - a[1]).map(([k, n]) => (
        <option key={k} value={k}>{labelFn ? labelFn(k) : k} ({n})</option>
      ))}
    </select>
  )
}
