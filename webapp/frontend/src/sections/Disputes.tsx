import { useEffect, useMemo, useState } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from 'recharts'
import { api, useApi } from '../api/client'
import { useDebounced } from '../lib/useDebounced'
import { useInViewOnce } from '../lib/motion'
import { downloadCsv } from '../lib/export'
import { readQueryParam, writeQuery } from '../lib/urlState'
import { useToast } from '../components/Toast'
import { useColors } from '../components/Theme'
import type { Colors } from '../lib/theme'
import { fixed, int, short } from '../lib/format'
import { Async, CopyButton, KV, Modal, Panel, Section } from '../components/ui'

const CSV_COLS = ['conditionId', 'marketName', 'category', 'adapter', 'disputeDate', 'proposedOutcome',
  'preDisputePrice', 'postDisputePrice', 'realizedJumpLogit', 'disputer', 'proposer', 'round']

interface DisputeRow {
  marketName?: string; conditionId: string; category: string; adapter: string; disputeDate: string
  proposedOutcome: string; preDisputePrice: number | null; postDisputePrice: number | null; realizedJumpLogit: number | null
  disputer?: string | null; proposer?: string | null; round?: number | null
  // HF market context (dispute_market_context.json) — enrichment for the detail view
  hfResolved?: boolean | null; hfResolvedOutcome?: string | null; hfEndDate?: string | null
}

const ADAPTER_LABEL = (a: string) => (a?.startsWith('0x') ? 'legacy' : a)
const outcomeColor = (C: Colors, o?: string): string =>
  (({ YES: C.profit, NO: C.loss, UNRESOLVABLE: C.warn, OTHER: C.muted } as Record<string, string>)[o || ''] || C.muted)
const scanAddr = (a: string) => `https://polygonscan.com/address/${a}`

type Filters = { category?: string; adapter?: string; year?: string }
type SortKey = 'date' | 'prepost' | 'jump'
type Sort = { key: SortKey; dir: 'asc' | 'desc' }
// map the UI sort keys to the real dataframe columns the backend sorts on (full-dataset, server-side)
const SORT_COL: Record<SortKey, string> = { date: 'disputeDate', prepost: 'postDisputePrice', jump: 'realizedJumpLogit' }
const SORT_KEYS: SortKey[] = ['date', 'prepost', 'jump']
const initSort = (): Sort | null => {
  const k = readQueryParam('sort') as SortKey | undefined
  if (k && SORT_KEYS.includes(k)) return { key: k, dir: readQueryParam('dir') === 'asc' ? 'asc' : 'desc' }
  return null
}

export function Disputes() {
  const { C } = useColors()
  const [search, setSearch] = useState(() => readQueryParam('q') ?? '')
  const dq = useDebounced(search, 300)
  const [f, setF] = useState<Filters>(() => ({ category: readQueryParam('cat'), adapter: readQueryParam('adapter'), year: readQueryParam('year') }))
  const [page, setPage] = useState(0)
  const [sort, setSort] = useState<Sort | null>(initSort)
  const [detail, setDetail] = useState<DisputeRow | null>(null)
  const [exporting, setExporting] = useState(false)
  const toast = useToast()
  const limit = 25

  // export the full filtered set (up to the backend cap), not just the current page
  const exportCsv = async () => {
    setExporting(true)
    try {
      const p = new URLSearchParams(qs)
      p.set('limit', '200'); p.set('offset', '0')
      const res = await api.disputes(`?${p.toString()}`)
      downloadCsv('polylambda-disputes.csv', res.rows as Record<string, unknown>[], CSV_COLS)
      toast.info(`exported ${res.rows.length} disputes to CSV`)
    } catch (e: any) {
      toast.error('export failed', { message: String(e?.message || e) })
    } finally { setExporting(false) }
  }

  // any filter/search/sort change resets to page 1
  useEffect(() => { setPage(0) }, [dq, f, sort])

  // reflect filters/sort into the URL for shareable deep-links
  useEffect(() => {
    writeQuery({ q: dq || undefined, cat: f.category, adapter: f.adapter, year: f.year,
      sort: sort?.key, dir: sort ? sort.dir : undefined })
  }, [dq, f, sort])

  const qs = useMemo(() => {
    const p = new URLSearchParams()
    if (f.category) p.set('category', f.category)
    if (f.adapter) p.set('adapter', f.adapter)
    if (f.year) p.set('year', f.year)
    if (dq) p.set('q', dq)
    if (sort) { p.set('sort', SORT_COL[sort.key]); p.set('desc', String(sort.dir === 'desc')) }
    p.set('limit', String(limit)); p.set('offset', String(page * limit))
    return `?${p.toString()}`
  }, [f, dq, sort, page])
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
      subtitle="The net-new UMA OptimisticOracle dispute layer (not in the HF dataset), 100% joinable across all adapters, enriched with real market titles. Sort any column across the full dataset; click a row for the full record.">
      <Async q={q}>{(d) => {
        const rows = d.rows as DisputeRow[]
        const from = d.total === 0 ? 0 : page * limit + 1
        const to = Math.min((page + 1) * limit, d.total)
        return (
          <Panel pad={false}>
            {/* filter bar */}
            <div className="flex flex-wrap items-center gap-2 border-b border-line p-4">
              <input id="disputes-search" className="field max-w-xs flex-1" placeholder="search title / conditionId / disputer…  ( / )"
                aria-label="search disputes" value={search} onChange={(e) => setSearch(e.target.value)} />
              <Facet label="category" value={f.category} options={d.facets.category} onPick={(v) => setFilter({ category: v })} />
              <Facet label="adapter" value={f.adapter} options={d.facets.adapter} labelFn={ADAPTER_LABEL} onPick={(v) => setFilter({ adapter: v })} />
              <select className="field w-auto" value={f.year ?? ''} onChange={(e) => setFilter({ year: e.target.value || undefined })} aria-label="filter by year">
                <option value="">all years</option>
                {Object.keys(d.facets.year).sort().map((y) => <option key={y} value={y}>{y} ({d.facets.year[y]})</option>)}
              </select>
              <span className="num ml-auto text-2xs text-muted">{d.total === 0 ? '0 rows' : `${from}–${to} of ${int(d.total)}`}</span>
              <button onClick={exportCsv} disabled={exporting || d.total === 0} className="btn !py-1 text-2xs" aria-label="Export filtered disputes to CSV">
                {exporting ? '…' : '⭳ CSV'}
              </button>
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
                    <tr key={i} onClick={() => setDetail(r)}
                      className="cursor-pointer border-b border-line/40 transition hover:bg-elevated/40">
                      <td className="max-w-[320px] px-4 py-2 font-sans text-ink-2">
                        <div className="flex items-center gap-1.5">
                          <span className="truncate" title={r.marketName || r.conditionId}>
                            {r.marketName || <span className="font-mono text-muted">{short(r.conditionId, 10, 6)}</span>}
                          </span>
                          {r.conditionId && <span onClick={(e) => e.stopPropagation()}><CopyButton value={r.conditionId} label="Copy conditionId" className="shrink-0" /></span>}
                        </div>
                      </td>
                      <td className="px-2 capitalize text-muted">{r.category}</td>
                      <td className="px-2 text-muted">{ADAPTER_LABEL(r.adapter)}</td>
                      <td className="px-2 text-muted">{r.disputeDate}</td>
                      <td className="px-2"><span style={{ color: outcomeColor(C, r.proposedOutcome) }}>{r.proposedOutcome ?? '—'}</span></td>
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
              <span className="num text-muted">page {page + 1} / {Math.max(1, Math.ceil(d.total / limit))}</span>
              <button className="btn" disabled={(page + 1) * limit >= d.total} onClick={() => setPage((p) => p + 1)}>next →</button>
            </div>
          </Panel>
        )
      }}</Async>

      <DisputeAnatomy />
      <DisputeDetail row={detail} onClose={() => setDetail(null)} />
    </Section>
  )
}

// distributions over the full released parquet — jump magnitude, price impact, outcome mix
function DisputeAnatomy() {
  const { C } = useColors()
  const q = useApi(api.disputeAnalytics, [])
  const [hRef, hIn] = useInViewOnce<HTMLDivElement>()
  return (
    <Async q={q}>{(d) => (
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel>
          <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <div className="label text-sig">jump-magnitude distribution · |realized logit|</div>
            {d.jump_stats && <span className="num text-2xs text-muted">mean {fixed(d.jump_stats.mean, 3)} · median {fixed(d.jump_stats.median, 3)} · n {int(d.jump_stats.n)}</span>}
          </div>
          <div className="h-[220px] w-full" ref={hRef}>
            <ResponsiveContainer>
              <BarChart data={d.histogram ?? []} margin={{ left: 2, right: 8, top: 8, bottom: 4 }}>
                <CartesianGrid stroke={C.line} vertical={false} />
                <XAxis dataKey="x0" tickFormatter={(v) => Number(v).toFixed(1)} stroke={C.axis} tick={{ fill: C.muted, fontSize: 10 }} tickLine={false} />
                <YAxis stroke={C.axis} tick={{ fill: C.muted, fontSize: 10 }} tickLine={false} width={36} />
                <Tooltip cursor={{ fill: C.elevated }} content={({ active, payload }: any) => active && payload?.length ? (
                  <div className="panel p-2 text-xs num"><span className="text-muted">|logit| {fixed(payload[0].payload.x0, 2)}–{fixed(payload[0].payload.x1, 2)}</span> · <span className="text-sig">{payload[0].payload.n}</span></div>
                ) : null} />
                <Bar dataKey="n" fill={C.sig} radius={[2, 2, 0, 0]} isAnimationActive={hIn} animationDuration={700} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel>
          <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <div className="label text-sig">price impact · pre → post dispute</div>
            <span className="num text-2xs text-muted">{d.by_outcome ? Object.entries(d.by_outcome).map(([k, v]) => `${k} ${v}`).join(' · ') : ''}</span>
          </div>
          <div className="h-[220px] w-full">
            <ResponsiveContainer>
              <ScatterChart margin={{ left: 2, right: 8, top: 8, bottom: 4 }}>
                <CartesianGrid stroke={C.line} />
                <XAxis type="number" dataKey="pre" name="pre" domain={[0, 1]} stroke={C.axis} tick={{ fill: C.muted, fontSize: 10 }} tickLine={false}
                  label={{ value: 'pre-dispute price', fill: C.muted, fontSize: 10, position: 'insideBottom', offset: -2 }} />
                <YAxis type="number" dataKey="post" name="post" domain={[0, 1]} stroke={C.axis} tick={{ fill: C.muted, fontSize: 10 }} tickLine={false} width={36} />
                <ZAxis range={[14, 14]} />
                <Tooltip cursor={{ stroke: C.line }} content={({ active, payload }: any) => active && payload?.length ? (
                  <div className="panel p-2 text-xs num text-muted">pre {payload[0].payload.pre.toFixed(2)} → post {payload[0].payload.post.toFixed(2)}</div>
                ) : null} />
                <Scatter data={d.scatter ?? []} fill={C.series[3]} fillOpacity={0.4} isAnimationActive={false} />
              </ScatterChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-1 text-2xs text-muted">Points off the diagonal are the dispute jump — how far the market re-priced once the dispute landed.</p>
        </Panel>
      </div>
    )}</Async>
  )
}

function DisputeDetail({ row, onClose }: { row: DisputeRow | null; onClose: () => void }) {
  const { C } = useColors()
  return (
    <Modal open={!!row} onClose={onClose} labelledBy="dispute-detail-title">
      {row && (
        <div>
          <h3 id="dispute-detail-title" className="mb-1 text-base font-semibold text-ink">{row.marketName || 'Dispute'}</h3>
          <div className="mb-3 flex items-center gap-1.5 text-2xs text-muted">
            <span className="num truncate">{short(row.conditionId, 14, 10)}</span>
            <CopyButton value={row.conditionId} label="Copy conditionId" />
          </div>
          <div className="space-y-0.5">
            <KV k="category" v={<span className="capitalize">{row.category}</span>} mono={false} />
            <KV k="adapter" v={ADAPTER_LABEL(row.adapter)} mono={false} />
            <KV k="dispute date" v={row.disputeDate} />
            <KV k="proposed outcome" v={<span style={{ color: outcomeColor(C, row.proposedOutcome) }}>{row.proposedOutcome ?? '—'}</span>} mono={false} />
            <KV k="pre → post price" v={row.preDisputePrice != null ? `${fixed(row.preDisputePrice, 3)} → ${fixed(row.postDisputePrice, 3)}` : '—'} />
            <KV k="realized jump (logit)" v={row.realizedJumpLogit != null ? fixed(row.realizedJumpLogit, 3) : '—'} />
            {row.round != null && <KV k="dispute round" v={row.round} />}
            {(row.hfResolvedOutcome || row.hfEndDate) && (
              <KV k="HF resolution" mono={false} v={
                <span>
                  {row.hfResolvedOutcome
                    ? <span style={{ color: outcomeColor(C, row.hfResolvedOutcome) }}>{row.hfResolvedOutcome}</span>
                    : <span className="text-muted">{row.hfResolved ? 'resolved' : 'open'}</span>}
                  {row.hfEndDate && <span className="text-muted"> · ends {row.hfEndDate}</span>}
                </span>
              } />
            )}
            <KV k="proposer" v={<AddrCell addr={row.proposer} />} mono={false} />
            <KV k="disputer" v={<AddrCell addr={row.disputer} />} mono={false} />
          </div>
          <div className="mt-4 flex justify-end"><button className="btn" onClick={onClose}>Close</button></div>
        </div>
      )}
    </Modal>
  )
}

function AddrCell({ addr }: { addr?: string | null }) {
  if (!addr) return <span className="text-muted">—</span>
  return (
    <span className="inline-flex items-center gap-1.5">
      <a href={scanAddr(addr)} target="_blank" rel="noreferrer" className="num text-ink-2 link-underline">{short(addr, 6, 4)} ↗</a>
      <CopyButton value={addr} label="Copy address" />
    </span>
  )
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
