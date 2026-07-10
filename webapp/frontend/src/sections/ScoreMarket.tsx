import { useEffect, useState } from 'react'
import { api, useAction, type ScoreReq, type ScoreResp } from '../api/client'
import { C } from '../lib/theme'
import { fixed, num, pct, usd } from '../lib/format'
import { Caveat, Drift, ErrorBox, KV, Loading, Panel, Section } from '../components/ui'

const CATS = ['politics', 'entertainment', 'economics', 'geopolitics', 'tech-ai', 'sports', 'other', 'crypto']
const DEFAULTS: ScoreReq = { category: 'politics', fill_count: 800, price: 0.62, inventory: 60, horizon_days: 5, proposer: null }
const isAddr = (s: string) => /^0x[0-9a-fA-F]{40}$/.test(s.trim())

function Slider({ label, value, min, max, step, onChange, fmt }: {
  label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void; fmt: (v: number) => string
}) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className="label">{label}</span>
        <span className="num text-sm text-sig">{fmt(value)}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
        aria-label={label} aria-valuetext={fmt(value)}
        onChange={(e) => onChange(+e.target.value)}
        className="h-2 w-full cursor-pointer appearance-none rounded-full bg-line accent-sig" />
    </div>
  )
}

export function ScoreMarket() {
  const [req, setReq] = useState<ScoreReq>(DEFAULTS)
  const { run, data, error, loading } = useAction(api.score)
  const set = (patch: Partial<ScoreReq>) => setReq((r) => ({ ...r, ...patch }))
  const proposerInvalid = !!req.proposer && !isAddr(req.proposer)
  const atDefaults = JSON.stringify(req) === JSON.stringify(DEFAULTS)

  useEffect(() => {
    if (proposerInvalid) return // don't fire on an in-progress malformed address
    const t = setTimeout(() => run(req), 250)
    return () => clearTimeout(t)
  }, [req, proposerInvalid]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Section id="score" kicker="live λ engine · wired to estimate_lambda()"
      title="Score a market"
      subtitle="Feed a market's point-in-time-safe features to the real estimator — it returns the two λ signals, an Avellaneda–Stoikov quote, and the reward-aware exit verdict. Nothing here is mocked.">
      <div className="grid gap-5 lg:grid-cols-[320px_1fr]">
        {/* --- inputs --- */}
        <Panel className="space-y-4 self-start">
          <div className="flex items-center justify-between">
            <span className="label">category</span>
            <button onClick={() => setReq(DEFAULTS)} disabled={atDefaults}
              className="text-2xs text-muted transition-colors hover:text-sig disabled:opacity-40">↺ reset</button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {CATS.map((c) => (
              <button key={c} onClick={() => set({ category: c })} aria-pressed={req.category === c}
                className={`chip capitalize ${req.category === c ? 'border-sig/50 bg-sig/10 text-sig' : ''}`}>{c}</button>
            ))}
          </div>
          <Slider label="price (YES mid)" value={req.price} min={0.02} max={0.98} step={0.01} onChange={(v) => set({ price: v })} fmt={(v) => v.toFixed(2)} />
          <Slider label="market size (fills)" value={req.fill_count} min={0} max={5000} step={50} onChange={(v) => set({ fill_count: v })} fmt={(v) => num(v, 0)} />
          <Slider label="inventory (tokens)" value={req.inventory} min={-300} max={300} step={10} onChange={(v) => set({ inventory: v })} fmt={(v) => num(v, 0)} />
          <Slider label="horizon (days to resolve)" value={req.horizon_days} min={0.25} max={30} step={0.25} onChange={(v) => set({ horizon_days: v })} fmt={(v) => `${v}d`} />
          <div>
            <span className="label">proposer (optional)</span>
            <input className={`field mt-1.5 font-mono text-xs ${proposerInvalid ? '!border-loss/60' : ''}`} placeholder="0x… address (reliability feature)"
              aria-label="proposer address" aria-invalid={proposerInvalid}
              value={req.proposer ?? ''} onChange={(e) => set({ proposer: e.target.value || null })} />
            {proposerInvalid && <div className="mt-1 text-2xs text-loss">enter a full 0x… 40-hex address, or clear the field</div>}
          </div>
          <div className="text-2xs text-muted">Features assemble via the real <span className="font-mono text-ink-2">market_size / proposer_reliability</span> transforms; latency_anomaly is unbuildable (no proposedAt) → 0.</div>
        </Panel>

        {/* --- outputs --- */}
        <div className={`space-y-4 transition ${loading && data ? 'opacity-60' : ''}`}>
          {error && !data && <ErrorBox error={error} onRetry={() => run(req)} />}
          {data && <Outputs d={data} />}
          {!data && !error && <Panel><Loading label="scoring the market" /></Panel>}
        </div>
      </div>
    </Section>
  )
}

function Outputs({ d }: { d: ScoreResp }) {
  const jumpPct = Math.round(d.quote.jump_share * 100)
  return (
    <>
      <div className="grid gap-4 md:grid-cols-3">
        {/* λ signals */}
        <Panel className="md:col-span-1">
          <div className="label mb-2 text-sig">λ signals</div>
          <div className="mb-3">
            <div className="text-2xs text-muted">λ_select · dispute-proneness</div>
            <div className="num text-2xl font-semibold text-ink">{pct(d.lambda.lambda_select, 3)}</div>
            <div className="num text-2xs text-muted">95% CI [{pct(d.lambda.ci_low, 2)}, {pct(d.lambda.ci_high, 2)}]</div>
          </div>
          <div>
            <div className="text-2xs text-muted">λ_jump · jump intensity <span className="rounded bg-elevated px-1 text-[10px] text-sig">{d.lambda.model}</span></div>
            <div className="num text-2xl font-semibold text-sig">{pct(d.lambda.lambda_jump, 3)}</div>
            <div className="mt-1 text-2xs"><Drift v={d.lambda.jump_drift} /> · E[loss|jump] {fixed(d.lambda.e_loss, 4)} logit</div>
          </div>
        </Panel>

        {/* A-S quote */}
        <Panel className="md:col-span-2">
          <div className="mb-2 flex items-center justify-between">
            <div className="label text-sig">Avellaneda–Stoikov quote</div>
            <div className="num text-2xs text-muted">σ={fixed(d.quote.sigma, 4)} · logit-space</div>
          </div>
          <QuoteBar mid={d.quote.mid} bid={d.quote.bid} ask={d.quote.ask} />
          <div className="mt-4 grid grid-cols-3 gap-3 text-center">
            <div><div className="num text-lg text-loss">{d.quote.bid.toFixed(3)}</div><div className="text-2xs text-muted">bid</div></div>
            <div><div className="num text-lg text-ink">{d.quote.mid.toFixed(3)}</div><div className="text-2xs text-muted">mid</div></div>
            <div><div className="num text-lg text-profit">{d.quote.ask.toFixed(3)}</div><div className="text-2xs text-muted">ask</div></div>
          </div>
          <div className="mt-3 border-t border-line pt-3">
            <div className="mb-1 flex items-center justify-between text-2xs text-muted">
              <span>spread decomposition (logit)</span>
              <span>jump premium · {jumpPct}%</span>
            </div>
            <div className="flex h-2 overflow-hidden rounded-full bg-bg">
              <div style={{ width: `${100 - jumpPct}%`, background: C.series[1] }} title="diffusion (A-S)" />
              <div style={{ width: `${jumpPct}%`, background: C.warn }} title="jump premium (κ·λ·E[loss])" />
            </div>
            <div className="mt-1 flex justify-between text-2xs">
              <span style={{ color: C.series[1] }}>diffusion {fixed(d.quote.diffusion_logit, 4)}</span>
              <span style={{ color: C.warn }}>jump {fixed(d.quote.jump_logit, 5)}</span>
            </div>
          </div>
        </Panel>
      </div>

      {/* exit gate */}
      <Panel>
        <div className="mb-3 flex items-center justify-between">
          <div className="label text-sig">reward-aware exit gate · should_exit()</div>
          <span className={`chip ${d.exit_gate.would_exit ? 'border-warn/50 text-warn' : 'border-good/40 text-good'}`}>
            <span className="h-1.5 w-1.5 rounded-full" style={{ background: d.exit_gate.would_exit ? C.warn : C.good }} />
            {d.exit_gate.would_exit ? 'EXIT — flatten the danger window' : 'HOLD — keep farming rewards'}
          </span>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <KV k="λ_jump  vs  λ*" v={<span><span className="text-sig">{fixed(d.exit_gate.lambda_jump, 4)}</span> vs {fixed(d.exit_gate.lambda_star, 4)}</span>} />
            <KV k="E[jump loss]" v={<span className="text-loss">{usd(d.exit_gate.e_jump_loss_usd)}</span>} />
            <KV k="forgone rewards" v={usd(d.exit_gate.forgone_rewards)} />
            <KV k="exit haircut" v={usd(d.exit_gate.spread_cost)} />
          </div>
          <div className="flex flex-col justify-center rounded-lg border border-line bg-bg/50 p-3">
            <div className="text-2xs text-muted">gate rule</div>
            <div className="num my-1 text-xs text-ink-2">(proposal ∨ λ_jump &gt; λ*) ∧ (E[loss] &gt; forgone + haircut)</div>
            <div className="text-xs leading-relaxed text-ink-2">{d.exit_gate.reason}</div>
          </div>
        </div>
        <div className="mt-3">
          <Caveat kind="calibration">
            At an at-rest market the base-rate λ makes E[loss] tiny vs continuous reward income → the gate honestly
            says <b>hold</b>. The exit fires when a live proposal is actually detected (λ_jump → posterior≈1) — see the paper engine below.
          </Caveat>
        </div>
      </Panel>
    </>
  )
}

function QuoteBar({ mid, bid, ask }: { mid: number; bid: number; ask: number }) {
  // fixed 0..1 price axis with bid/mid/ask ticks
  const pos = (x: number) => `${x * 100}%`
  return (
    <div className="relative mt-2 h-9">
      <div className="absolute inset-x-0 top-4 h-1 rounded-full bg-bg" />
      <div className="absolute top-4 h-1 rounded-full" style={{ left: pos(bid), width: pos(ask - bid), background: 'linear-gradient(90deg,#e6676799,#22c58a99)' }} />
      {[['bid', bid, C.loss], ['mid', mid, C.ink], ['ask', ask, C.profit]].map(([lbl, x, col]) => (
        <div key={lbl as string} className="absolute -translate-x-1/2" style={{ left: pos(x as number) }}>
          <div className="mx-auto h-5 w-px" style={{ background: col as string }} />
          <div className="num mt-0.5 text-[10px]" style={{ color: col as string }}>{lbl}</div>
        </div>
      ))}
    </div>
  )
}
