import { useEffect, useState } from 'react'
import { api, useApi } from './api/client'
import { Ablation } from './sections/Ablation'
import { BaseRates } from './sections/BaseRates'
import { Disputes } from './sections/Disputes'
import { HazardCard } from './sections/HazardCard'
import { Hero } from './sections/Hero'
import { PaperSession } from './sections/PaperSession'
import { Recon } from './sections/Recon'
import { ScoreMarket } from './sections/ScoreMarket'
import { SigmaSurface } from './sections/SigmaSurface'

const NAV = [
  { id: 'overview', label: 'Overview' },
  { id: 'baserates', label: 'λ signal' },
  { id: 'score', label: 'Score a market' },
  { id: 'session', label: 'Paper engine' },
  { id: 'ablation', label: 'Edge proof' },
  { id: 'hazard', label: 'Model card' },
  { id: 'disputes', label: 'Disputes' },
  { id: 'recon', label: 'Integrity' },
  { id: 'sigma', label: 'σ surface' },
]

function useScrollSpy(ids: string[]) {
  const [active, setActive] = useState(ids[0])
  useEffect(() => {
    const obs = new IntersectionObserver(
      (entries) => {
        const vis = entries.filter((e) => e.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)
        if (vis[0]) setActive(vis[0].target.id)
      },
      { rootMargin: '-45% 0px -50% 0px', threshold: [0, 0.25, 0.5, 1] },
    )
    ids.forEach((id) => { const el = document.getElementById(id); if (el) obs.observe(el) })
    return () => obs.disconnect()
  }, [ids])
  return active
}

export default function App() {
  const overview = useApi(api.overview, [])
  const active = useScrollSpy(NAV.map((n) => n.id))
  const mode = overview.data?.mode ?? 'paper'

  return (
    <div className="min-h-full">
      {/* --- top header --- */}
      <header className="sticky top-0 z-40 border-b border-line bg-bg/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl items-center gap-4 px-5 py-3">
          <a href="#overview" className="flex items-center gap-2.5">
            <span className="grid h-8 w-8 place-items-center rounded-lg border border-sig/30 bg-sig/10 font-mono text-lg font-bold text-sig shadow-glow">λ</span>
            <span className="text-[15px] font-semibold tracking-tight text-ink">PolyLambda</span>
          </a>
          <span className="hidden text-2xs text-muted md:inline">dispute-aware market making for Polymarket</span>
          <div className="ml-auto flex items-center gap-2">
            <span className="chip">
              <span className="h-1.5 w-1.5 animate-pulse2 rounded-full bg-sig" />
              MODE · {mode}
            </span>
            <span className="chip hidden sm:inline-flex">paper-only · simulated</span>
          </div>
        </div>
        {/* --- section nav --- */}
        <nav className="mx-auto max-w-7xl overflow-x-auto px-5">
          <ul className="flex gap-1 pb-2 text-sm">
            {NAV.map((n) => (
              <li key={n.id}>
                <a href={`#${n.id}`}
                  className={`inline-block whitespace-nowrap rounded-md px-2.5 py-1 transition ${
                    active === n.id ? 'bg-sig/10 text-sig' : 'text-muted hover:text-ink-2'
                  }`}>{n.label}</a>
              </li>
            ))}
          </ul>
        </nav>
      </header>

      {/* --- body --- */}
      <main className="mx-auto max-w-7xl space-y-16 px-5 py-10">
        <Hero q={overview} />
        <BaseRates />
        <ScoreMarket />
        <PaperSession />
        <Ablation />
        <HazardCard />
        <Disputes />
        <Recon />
        <SigmaSurface />
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto max-w-7xl px-5 py-8 text-2xs leading-relaxed text-muted">
          <p className="mb-1">
            <span className="font-mono text-sig">λ PolyLambda</span> — a thin, read-only dashboard wired to the
            real engine (estimators · execution · forward-test). Every figure is computed by the actual
            code or read from a shipped artifact; the paper engine is deterministic and network-free.
          </p>
          <p>Live trading is jurisdiction-gated and out of scope for v1 — this MVP is paper-mode only, and every simulated figure is stamped <span className="font-mono">simulated: true</span>.</p>
        </div>
      </footer>
    </div>
  )
}
