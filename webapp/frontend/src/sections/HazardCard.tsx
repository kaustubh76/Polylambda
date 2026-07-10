import { api, useApi, type Hazard, type HazardCardT } from '../api/client'
import { useColors } from '../components/Theme'
import { fixed, int, short } from '../lib/format'
import { Async, Caveat, CopyButton, Panel, Section } from '../components/ui'

const FEAT_SHORT: Record<string, string> = {
  category_base_rate: 'cat base rate', market_size: 'market size',
  proposer_reliability: 'proposer rep', latency_anomaly: 'latency',
}
const FEAT_FULL: Record<string, string> = {
  category_base_rate: 'category_base_rate — the category’s historical dispute rate',
  market_size: 'market_size — fill count / liquidity proxy',
  proposer_reliability: 'proposer_reliability — the proposer’s prior track record',
  latency_anomaly: 'latency_anomaly — proposal timing anomaly (unbuildable here → 0)',
}

export function HazardCard() {
  const q = useApi(api.hazard, [])
  return (
    <Section id="hazard" kicker="the structural model · honest by construction"
      title="Dispute hazard model — and the null we kept"
      subtitle="A class-weighted logistic on point-in-time-safe features, prior-corrected to the ~1% natural rate. Reported by discrimination (held-out AUC), because disputes are too rare to calibrate.">
      <Async q={q}>{(d: Hazard) => (
        <div className="space-y-4">
          <div className="grid gap-4 md:grid-cols-3">
            <ModelCard card={d.deployed} highlight />
            <ModelCard card={d.matched} />
            <ModelCard card={d.matched_eval} nullish />
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel>
              <div className="label mb-2 text-warn">the null result</div>
              <p className="text-sm leading-relaxed text-ink-2">{d.null_finding}</p>
            </Panel>
            <Caveat kind="calibration">{d.caveat}</Caveat>
          </div>
          <ProposerLeaderboard />
        </div>
      )}</Async>
    </Section>
  )
}

// the raw proposer signal, before the CEM-matched null — a "most dispute-prone proposers" ladder
function ProposerLeaderboard() {
  const { C } = useColors()
  const q = useApi(api.proposers, [])
  return (
    <Async q={q}>{(d) => {
      const max = Math.max(...d.rows.map((r) => r.disputes), 1)
      return (
        <Panel>
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
            <div className="label text-sig">most dispute-prone proposers · raw signal</div>
            <span className="text-2xs text-muted">{int(d.total_proposers)} proposers total</span>
          </div>
          <div className="grid gap-x-8 gap-y-1.5 sm:grid-cols-2">
            {d.rows.map((r, i) => (
              <div key={r.proposer} className="flex items-center gap-2 text-xs">
                <span className="w-4 shrink-0 text-right text-2xs text-muted">{i + 1}</span>
                <a href={`https://polygonscan.com/address/${r.proposer}`} target="_blank" rel="noreferrer"
                  className="num w-24 shrink-0 text-ink-2 link-underline">{short(r.proposer, 5, 4)}</a>
                <CopyButton value={r.proposer} label="Copy proposer address" />
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-bg">
                  <div className="h-full rounded-full" style={{ width: `${(r.disputes / max) * 100}%`, background: C.series[3] }} />
                </div>
                <span className="num w-8 text-right text-muted">{r.disputes}</span>
              </div>
            ))}
          </div>
          <p className="mt-3 text-2xs leading-relaxed text-muted">{d.note}</p>
        </Panel>
      )
    }}</Async>
  )
}

function ModelCard({ card, highlight, nullish }: { card: HazardCardT | null; highlight?: boolean; nullish?: boolean }) {
  const { C } = useColors()
  if (!card) return <Panel><div className="text-sm text-muted">unavailable</div></Panel>
  const auc = card.holdout_auc ?? 0.5
  const discr = card.discriminates
  const maxAbs = Math.max(...card.coef.map((c) => Math.abs(c)), 0.001)
  return (
    <Panel className={highlight ? 'ring-1 ring-sig/25' : ''}>
      <div className="flex items-start justify-between">
        <div className="text-sm font-medium text-ink">{card.label}</div>
        <span className={`chip ${discr ? 'border-good/40 text-good' : 'border-warn/50 text-warn'}`}>
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: discr ? C.good : C.warn }} />
          {discr ? 'discriminates' : 'coin-flip'}
        </span>
      </div>
      <div className="my-3 flex items-end gap-2">
        <span className="num text-3xl font-semibold" style={{ color: nullish ? C.warn : highlight ? C.sig : C.ink }}>{auc.toFixed(3)}</span>
        <span className="mb-1 text-2xs text-muted">held-out AUC</span>
      </div>
      <div className="mb-1.5 flex items-center justify-between text-[10px] text-muted">
        <span>coefficient (logit)</span>
        <span className="flex items-center gap-2">
          <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-sm" style={{ background: C.loss }} />−</span>
          <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-sm" style={{ background: C.series[1] }} />+</span>
        </span>
      </div>
      <div className="space-y-1.5">
        {card.feature_order.map((f, i) => {
          const c = card.coef[i]
          const w = (Math.abs(c) / maxAbs) * 50
          return (
            <div key={f} className="flex items-center gap-2 text-2xs">
              <span className="w-24 shrink-0 cursor-help text-muted" title={FEAT_FULL[f] || f}>{FEAT_SHORT[f] || f}</span>
              <div className="relative h-3 flex-1 rounded-sm bg-bg">
                <div className="absolute left-1/2 top-0 h-full w-px bg-line" />
                <div className="absolute top-0 h-full rounded-sm" style={{
                  width: `${w}%`, [c >= 0 ? 'left' : 'right']: '50%',
                  background: Math.abs(c) < 1e-9 ? C.muted : c >= 0 ? C.series[1] : C.loss,
                }} />
              </div>
              <span className="num w-12 text-right text-ink-2">{fixed(c, 2)}</span>
            </div>
          )
        })}
      </div>
      <div className="num mt-3 flex justify-between border-t border-line pt-2 text-2xs text-muted">
        <span>n={int(card.n)}</span><span>pos={int(card.positives)}</span><span>Brier {fixed(card.brier, 3)}</span>
      </div>
      <div className="num mt-1.5 flex justify-between text-2xs text-muted">
        <span title="prior-corrected logistic intercept">intercept {fixed(card.intercept, 2)}</span>
        <span title="log-prior offset to the ~1% natural rate">offset {fixed(card.offset, 2)}</span>
        <span title="base dispute rate the model is corrected to">nat. rate {card.natural_rate != null ? `${(card.natural_rate * 100).toFixed(2)}%` : '—'}</span>
      </div>
    </Panel>
  )
}
