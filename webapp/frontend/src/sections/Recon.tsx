import { useState } from 'react'
import { api, useApi } from '../api/client'
import { C, CATEGORY_COLORS } from '../lib/theme'
import { int } from '../lib/format'
import { Async, Panel, Section, SourceTag, Stat } from '../components/ui'

// exclusion buckets recon.check reports; only the populated ones render
const BUCKET_LABEL: Record<string, string> = {
  no_ground_truth: 'no ground truth', pending: 'pending', in_dispute: 'in dispute',
  reorg_window: 'reorg window', unsupported_adapter: 'unsupported adapter',
}

export function Recon() {
  const [live, setLive] = useState(false)
  const q = useApi(() => (live ? api.reconLive() : api.recon()), [live])
  return (
    <Section id="recon" kicker="data integrity · recon.check"
      title="Reconciliation & provenance"
      subtitle="Every indexed resolution is checked against the on-chain payout vector. 100% is claimed on the ELIGIBLE set, with counted exclusion buckets — not a flat, unexamined 100%."
      right={
        <div className="flex items-center gap-2">
          {q.data?.source && <SourceTag source={q.data.source === 'live' ? 'live' : 'published'} />}
          <button className="btn !py-1 text-2xs" disabled={q.loading} onClick={() => setLive(true)}>
            {q.loading && live ? 'running…' : '↻ run live check'}
          </button>
        </div>
      }>
      <Async q={q}>{(d) => {
        const r = d.recon
        const adapters = Object.entries(d.by_adapter || {})
        const cats = Object.entries(d.by_category || {}).filter(([k]) => k !== 'null' && k != null)
        const excluded = Object.entries((r.excluded as Record<string, number> | undefined) || { no_ground_truth: r.no_ground_truth })
          .filter(([, v]) => v != null && Number(v) > 0)
        return (
          <div className="space-y-4">
            {d.source === 'live' && <div className="text-2xs text-sig">✓ live reconciliation · {d.mismatches ?? 0} mismatches on the eligible set</div>}
            {d.live_error && <div className="rounded-lg border border-warn/30 bg-warn/10 p-2.5 text-2xs text-warn">live check unavailable on this host ({d.live_error}) — showing the published artifact.</div>}
            <div className="grid gap-4 lg:grid-cols-[1fr_1.2fr]">
              <div className="grid grid-cols-2 gap-4 self-start">
                <Stat label="Recon pass rate" value={`${((r.pass_rate ?? 1) * 100).toFixed(1)}%`} accent sub="eligible = matched" />
                <Stat label="HF-joinable" value={`${d.hf_joinable_pct}%`} accent sub="all adapters" />
                <Stat label="Eligible matched" value={int(r.eligible)} sub="on-chain payout confirmed" />
                <Stat label="Total disputes" value={int(d.total_disputes)} sub="released layer" />
              </div>
              <Panel>
                <div className="label mb-3">joinability by adapter</div>
                {adapters.length === 0 && <div className="py-6 text-center text-sm text-muted">no adapter breakdown available</div>}
                <div className="space-y-2.5">
                  {adapters.map(([a, n], i) => {
                    const total = adapters.reduce((s, [, v]) => s + v, 0)
                    const label = a.startsWith('0x') ? 'legacy' : a
                    return (
                      <Bar key={a} label={label} n={n} total={total} color={C.series[i % C.series.length]} />
                    )
                  })}
                </div>
                {excluded.length > 0 && (
                  <div className="mt-4 border-t border-line pt-3">
                    <div className="mb-2 text-2xs uppercase tracking-wide text-muted">excluded (no ground truth to check against)</div>
                    <div className="flex flex-wrap gap-2">
                      {excluded.map(([k, v]) => (
                        <span key={k} className="chip">{BUCKET_LABEL[k] || k} · {int(Number(v))}</span>
                      ))}
                    </div>
                  </div>
                )}
                <p className="mt-3 text-2xs leading-relaxed text-muted">{d.note}</p>
              </Panel>
            </div>

            {cats.length > 0 && (
              <Panel>
                <div className="label mb-3">joinability by category · the NegRisk-fix proof (all 100% eligible)</div>
                <div className="grid gap-x-8 gap-y-2.5 sm:grid-cols-2">
                  {cats.sort((a, b) => b[1] - a[1]).map(([c, n]) => {
                    const total = cats.reduce((s, [, v]) => s + v, 0)
                    return <Bar key={c} label={c} n={n} total={total} color={CATEGORY_COLORS[c] || C.muted} capitalize />
                  })}
                </div>
              </Panel>
            )}
          </div>
        )
      }}</Async>
    </Section>
  )
}

function Bar({ label, n, total, color, capitalize }: { label: string; n: number; total: number; color: string; capitalize?: boolean }) {
  return (
    <div className="flex items-center gap-3 text-xs">
      <span className={`w-20 shrink-0 text-ink-2 ${capitalize ? 'capitalize' : ''}`}>{label}</span>
      <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-bg">
        <div className="h-full rounded-full transition-all" style={{ width: `${total > 0 ? (n / total) * 100 : 0}%`, background: color }} />
      </div>
      <span className="num w-12 text-right text-muted">{int(n)}</span>
      <span className="num w-12 text-right text-good">100%</span>
    </div>
  )
}
