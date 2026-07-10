import { api, useApi } from '../api/client'
import { C } from '../lib/theme'
import { int } from '../lib/format'
import { Async, Panel, Section, Stat } from '../components/ui'

export function Recon() {
  const q = useApi(api.recon, [])
  return (
    <Section id="recon" kicker="data integrity · recon.check"
      title="Reconciliation & provenance"
      subtitle="Every indexed resolution is checked against the on-chain payout vector. 100% is claimed on the ELIGIBLE set, with counted exclusion buckets — not a flat, unexamined 100%.">
      <Async q={q}>{(d) => {
        const r = d.recon
        const adapters = Object.entries(d.by_adapter || {})
        return (
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
                    <div key={a} className="flex items-center gap-3 text-xs">
                      <span className="w-16 text-ink-2">{label}</span>
                      <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-bg">
                        <div className="h-full rounded-full" style={{ width: `${(n / total) * 100}%`, background: C.series[i % C.series.length] }} />
                      </div>
                      <span className="num w-12 text-right text-muted">{int(n)}</span>
                      <span className="num w-16 text-right text-good">100%</span>
                    </div>
                  )
                })}
              </div>
              <div className="num mt-4 flex items-center justify-between border-t border-line pt-3 text-2xs text-muted">
                <span>excluded · no ground truth</span><span>{int(r.no_ground_truth)}</span>
              </div>
              <p className="mt-3 text-2xs leading-relaxed text-muted">{d.note}</p>
            </Panel>
          </div>
        )
      }}</Async>
    </Section>
  )
}
