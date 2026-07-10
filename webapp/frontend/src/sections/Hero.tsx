import type { Overview } from '../api/client'
import { int, num, pct1 } from '../lib/format'
import { AnimatedNumber, Reveal, Stagger } from '../lib/motion'
import { Async, Caveat, Panel, PanelSkeleton, Section, Skeleton } from '../components/ui'

function fmtTile(v: number, fmt: string) {
  if (fmt === 'int') return int(v)
  if (fmt === 'pct') return `${num(v, 1)}%`
  if (fmt === 'num4') return num(v, 4)
  return num(v, 3)
}

export function Hero({ q }: { q: { data: Overview | null; error: string | null; loading: boolean } }) {
  return (
    <Section id="overview" kicker="Polymarket Builders Program · research MVP"
      title="Treat disputes as jumps — and exit before they lock your capital.">
      <Async q={q} skeleton={<HeroSkeleton />}>{(d) => (
        <div className="relative grid gap-5 lg:grid-cols-[1.15fr_1fr]">
          {/* on-theme aurora: two slow-drifting brand-hue radial glows behind the panels */}
          <div aria-hidden className="pointer-events-none absolute -inset-8 -z-10 overflow-hidden">
            <div className="absolute left-[8%] top-[-20%] h-72 w-72 animate-aurora-drift rounded-full blur-3xl"
              style={{ background: 'radial-gradient(circle, rgba(36,201,138,0.16), transparent 65%)' }} />
            <div className="absolute right-[6%] top-[10%] h-80 w-80 animate-aurora-drift rounded-full blur-3xl"
              style={{ background: 'radial-gradient(circle, rgba(57,135,229,0.13), transparent 65%)', animationDelay: '-8s' }} />
          </div>
          <Panel className="flex flex-col justify-between">
            <div>
              <p className="text-[15px] leading-relaxed text-ink-2">{d.thesis}</p>
              <div className="my-5 flex items-center gap-3">
                <span className="animate-breathe rounded-lg border border-sig/25 bg-bg px-4 py-2 font-mono text-lg text-sig shadow-glow">
                  {d.jump_diffusion}
                </span>
                <span className="text-2xs text-muted">log-odds jump-diffusion:<br />drift + belief-vol σ + dispute jumps λ</span>
              </div>
              <Caveat kind="note">{d.thesis_nuance}</Caveat>
            </div>
            <div className="mt-5 flex flex-wrap gap-2">
              <a href="#trade" className="btn btn-primary">Trade on testnet →</a>
              <a href="#score" className="btn">Score a market</a>
              <a href="#session" className="btn">Watch the engine defend</a>
            </div>
            <div className="mt-3 flex flex-wrap gap-2 text-2xs">
              <span className="chip">positioning · {d.positioning}</span>
              <span className="chip">{int(d.dataset.total_disputes)} disputes · {d.dataset.date_min} → {d.dataset.date_max}</span>
              <span className="chip">{pct1(d.dataset.hf_joinable_pct / 100)} HF-joinable</span>
              {Object.entries(d.dataset.by_adapter).map(([k, v]) => (
                <span key={k} className="chip">{k} · {int(v)}</span>
              ))}
            </div>
          </Panel>

          <Stagger className="grid grid-cols-2 gap-4 self-start">
            {d.tiles.map((t) => (
              <Reveal key={t.label} className="panel panel-interactive p-4">
                <div className="label">{t.label}</div>
                <div className="num mt-1.5 text-2xl font-semibold text-sig">
                  <AnimatedNumber value={t.value} format={(n) => fmtTile(n, t.fmt)} />
                </div>
                <div className="mt-1 text-2xs leading-snug text-muted">{t.sub}</div>
              </Reveal>
            ))}
          </Stagger>
        </div>
      )}</Async>
    </Section>
  )
}

function HeroSkeleton() {
  return (
    <div className="grid gap-5 lg:grid-cols-[1.15fr_1fr]">
      <PanelSkeleton lines={7} />
      <div className="grid grid-cols-2 gap-4 self-start">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="panel space-y-2 p-4">
            <Skeleton className="h-3 w-1/2" /><Skeleton className="h-6 w-3/4" /><Skeleton className="h-2.5 w-2/3" />
          </div>
        ))}
      </div>
    </div>
  )
}
