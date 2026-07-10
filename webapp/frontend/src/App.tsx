import { useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, LazyMotion, domMax, m, MotionConfig } from 'framer-motion'
import { api, useApi } from './api/client'
import { Ablation } from './sections/Ablation'
import { BaseRates } from './sections/BaseRates'
import { Disputes } from './sections/Disputes'
import { HazardCard } from './sections/HazardCard'
import { Hero } from './sections/Hero'
import { LiveIndexer } from './sections/LiveIndexer'
import { PaperSession } from './sections/PaperSession'
import { Recon } from './sections/Recon'
import { ScoreMarket } from './sections/ScoreMarket'
import { SigmaSurface } from './sections/SigmaSurface'
import { LiveTestnet } from './sections/LiveTestnet'
import { WalletProvider, useWallet } from './lib/wallet'
import { ToastProvider, useToast } from './components/Toast'
import { CommandPalette, type Command } from './components/CommandPalette'
import { CopyButton } from './components/ui'
import { addressUrl } from './lib/testnet'
import { short } from './lib/format'

const NAV = [
  { id: 'overview', label: 'Overview' },
  { id: 'trade', label: 'Live testnet' },
  { id: 'baserates', label: 'λ signal' },
  { id: 'score', label: 'Score a market' },
  { id: 'session', label: 'Paper engine' },
  { id: 'ablation', label: 'Edge proof' },
  { id: 'hazard', label: 'Model card' },
  { id: 'disputes', label: 'Disputes' },
  { id: 'live', label: 'Live indexer' },
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

function LivePill() {
  const [s, setS] = useState<{ up: boolean; ms?: number } | null>(null)
  const fails = useRef(0)
  useEffect(() => {
    let alive = true
    // keep the last good state; only flip to "down" after 2 consecutive failures (free-tier blips)
    const tick = () => api.liveStatus()
      .then((r) => { if (alive) { fails.current = 0; setS({ up: r.reachable, ms: r.latency_ms }) } })
      .catch(() => { if (alive && ++fails.current >= 2) setS((p) => ({ up: false, ms: p?.ms })) })
    const start = setTimeout(tick, 1500)
    const t = setInterval(tick, 10000)
    return () => { alive = false; clearTimeout(start); clearInterval(t) }
  }, [])
  if (!s) return null
  return (
    <a href="#live" aria-live="polite" className={`chip ${s.up ? 'border-sig/40 text-sig' : 'border-warn/50 text-warn'}`} title="hosted Envio HyperIndex">
      <span className={`h-1.5 w-1.5 rounded-full ${s.up ? 'animate-pulse2' : ''}`} style={{ background: s.up ? '#24c98a' : '#fab219' }} />
      {s.up ? <>LIVE{s.ms != null && s.ms < 1000 ? ` · ${s.ms.toFixed(0)}ms` : ''}</> : 'indexer down'}
    </a>
  )
}

// header pending-tx indicator — surfaces any signing/mining tx globally so it's never lost on scroll
function PendingIndicator() {
  const { pendingCount } = useToast()
  return (
    <AnimatePresence>
      {pendingCount > 0 && (
        <m.a href="#trade" initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.9 }}
          aria-live="polite" className="chip border-warn/50 text-warn" title="transactions awaiting confirmation">
          <span className="h-1.5 w-1.5 animate-pulse2 rounded-full bg-warn" />
          {pendingCount} pending
        </m.a>
      )}
    </AnimatePresence>
  )
}

function AccountMenu() {
  const w = useWallet()
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  // surface wallet errors (connect rejection, network switch) as toasts
  useEffect(() => { if (w.error) { toast.error(w.error); w.clearError() } }, [w.error]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  if (!w.address) {
    return (
      <button className="chip hover:border-sig/40 hover:text-sig" onClick={w.connect} disabled={w.connecting} aria-label="Connect wallet">
        <span className="h-1.5 w-1.5 rounded-full" style={{ background: '#6b7280' }} />
        {w.connecting ? 'connecting…' : 'Connect'}
      </button>
    )
  }
  const color = w.onAmoy ? '#24c98a' : '#fab219'
  return (
    <div className="relative" ref={ref}>
      <button aria-haspopup="menu" aria-expanded={open} aria-live="polite" onClick={() => setOpen((o) => !o)}
        className={`chip ${w.onAmoy ? 'border-sig/40 text-sig' : 'border-warn/50 text-warn'}`}
        title={w.onAmoy ? 'Polygon Amoy' : 'Wrong network — switch to Amoy'}>
        <span className={`h-1.5 w-1.5 rounded-full ${w.onAmoy ? 'animate-pulse2' : ''}`} style={{ background: color }} />
        {w.onAmoy ? short(w.address, 4, 4) : 'wrong network'}
      </button>
      {open && (
        <div role="menu" className="panel absolute right-0 z-50 mt-2 w-56 p-1.5 text-sm">
          <div className="flex items-center justify-between px-2 py-1.5">
            <span className="num text-2xs text-ink-2">{short(w.address, 6, 6)}</span>
            <CopyButton value={w.address} label="Copy wallet address" />
          </div>
          <div className="my-1 border-t border-line" />
          {!w.onAmoy && (
            <button role="menuitem" onClick={() => { w.ensureAmoy().catch(() => {}); setOpen(false) }}
              className="block w-full rounded px-2 py-1.5 text-left text-warn transition-colors hover:bg-elevated/50">⚠ Switch to Amoy</button>
          )}
          <a role="menuitem" href={addressUrl(w.address)} target="_blank" rel="noreferrer" onClick={() => setOpen(false)}
            className="block w-full rounded px-2 py-1.5 text-left text-ink-2 transition-colors hover:bg-elevated/50">View on Amoyscan ↗</a>
          <button role="menuitem" onClick={() => { w.disconnect(); setOpen(false) }}
            className="block w-full rounded px-2 py-1.5 text-left text-ink-2 transition-colors hover:bg-elevated/50 hover:text-loss">Disconnect</button>
        </div>
      )}
    </div>
  )
}

function ScrollToTop() {
  const [show, setShow] = useState(false)
  useEffect(() => {
    const onScroll = () => setShow(window.scrollY > 700)
    window.addEventListener('scroll', onScroll, { passive: true }); onScroll()
    return () => window.removeEventListener('scroll', onScroll)
  }, [])
  return (
    <AnimatePresence>
      {show && (
        <m.button initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 10 }}
          onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })} aria-label="Scroll to top"
          className="fixed bottom-4 left-4 z-50 grid h-10 w-10 place-items-center rounded-full border border-line bg-elevated text-ink-2 shadow-panel transition-colors hover:border-sig/50 hover:text-sig">
          ↑
        </m.button>
      )}
    </AnimatePresence>
  )
}

function AppInner() {
  const overview = useApi(api.overview, [])
  const active = useScrollSpy(NAV.map((n) => n.id))
  const mode = overview.data?.mode ?? 'paper'
  const w = useWallet()
  const navRef = useRef<HTMLUListElement>(null)

  // keep the active nav tab scrolled into view (matters on mobile where the row overflows)
  useEffect(() => {
    navRef.current?.querySelector<HTMLElement>(`a[href="#${active}"]`)
      ?.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' })
  }, [active])

  const commands = useMemo<Command[]>(() => {
    const goto = NAV.map((n) => ({
      id: `go-${n.id}`, group: 'Go to', label: n.label,
      run: () => document.getElementById(n.id)?.scrollIntoView({ behavior: 'smooth' }),
    }))
    const wallet: Command[] = w.address
      ? [
          { id: 'wallet-copy', group: 'Wallet', label: 'Copy wallet address', run: () => navigator.clipboard?.writeText(w.address!) },
          { id: 'wallet-disconnect', group: 'Wallet', label: 'Disconnect wallet', run: w.disconnect },
        ]
      : [{ id: 'wallet-connect', group: 'Wallet', label: 'Connect wallet', run: () => { w.connect() } }]
    return [...goto, ...wallet, { id: 'scroll-top', group: 'Page', label: 'Scroll to top', run: () => window.scrollTo({ top: 0, behavior: 'smooth' }) }]
  }, [w.address]) // eslint-disable-line react-hooks/exhaustive-deps

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
            <button onClick={() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true }))}
              className="chip hidden text-muted hover:border-sig/40 hover:text-sig lg:inline-flex" aria-label="Open command palette" title="Command palette">
              ⌘K
            </button>
            <PendingIndicator />
            <AccountMenu />
            <LivePill />
            <span className="chip hidden sm:inline-flex">
              <span className="h-1.5 w-1.5 animate-pulse2 rounded-full bg-sig" />
              MODE · {mode}
            </span>
          </div>
        </div>
        {/* --- section nav --- */}
        <nav className="mx-auto max-w-7xl overflow-x-auto px-5"
          style={{ WebkitMaskImage: 'linear-gradient(to right, transparent 0, #000 20px, #000 calc(100% - 20px), transparent 100%)', maskImage: 'linear-gradient(to right, transparent 0, #000 20px, #000 calc(100% - 20px), transparent 100%)' }}>
          <ul ref={navRef} className="flex gap-1 pb-2 text-sm">
            {NAV.map((n) => (
              <li key={n.id} className="relative">
                {active === n.id && (
                  <m.span layoutId="nav-pill" className="absolute inset-0 rounded-md bg-sig/10"
                    transition={{ type: 'spring', stiffness: 500, damping: 40 }} />
                )}
                <a href={`#${n.id}`} aria-current={active === n.id ? 'true' : undefined}
                  className={`relative inline-block whitespace-nowrap rounded-md px-2.5 py-1 transition-colors ${
                    active === n.id ? 'text-sig' : 'text-muted hover:text-ink-2'
                  }`}>{n.label}</a>
              </li>
            ))}
          </ul>
        </nav>
      </header>

      {/* --- body --- */}
      <main className="mx-auto max-w-7xl space-y-16 px-5 py-10">
        <Hero q={overview} />
        <LiveTestnet />
        <BaseRates />
        <ScoreMarket />
        <PaperSession />
        <Ablation />
        <HazardCard />
        <Disputes />
        <LiveIndexer />
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
          <p>Live trading is jurisdiction-gated and out of scope for v1 — this MVP is paper-mode only, and every simulated figure is stamped <span className="font-mono">simulated: true</span>. The testnet wallet signs on Polygon Amoy — play money, no keys server-side.</p>
          <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 border-t border-line pt-4 text-ink-2">
            <a className="link-underline hover:text-sig" href="https://github.com/kaustubh76/Polylambda" target="_blank" rel="noreferrer">GitHub ↗</a>
            <a className="link-underline hover:text-sig" href="https://indexer.dev.hyperindex.xyz/0638687/v1/graphql" target="_blank" rel="noreferrer">Envio indexer (GraphQL) ↗</a>
            <a className="link-underline hover:text-sig" href="https://huggingface.co/datasets/moose-code/polymarket-onchain-v1" target="_blank" rel="noreferrer">HF dataset ↗</a>
            <a className="link-underline hover:text-sig" href="https://amoy.polygonscan.com" target="_blank" rel="noreferrer">Amoy explorer ↗</a>
            <span className="ml-auto text-muted">built for the Polymarket Builders Program</span>
          </div>
        </div>
      </footer>

      <ScrollToTop />
      <CommandPalette commands={commands} />
    </div>
  )
}

export default function App() {
  return (
    <LazyMotion features={domMax}>
      <MotionConfig reducedMotion="user">
        <WalletProvider>
          <ToastProvider>
            <AppInner />
          </ToastProvider>
        </WalletProvider>
      </MotionConfig>
    </LazyMotion>
  )
}
