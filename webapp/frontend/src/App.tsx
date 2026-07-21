import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { AnimatePresence, LazyMotion, domMax, m, MotionConfig } from 'framer-motion'
import { api, req, useApi } from './api/client'
import { BaseRates } from './sections/BaseRates'
import { Hero } from './sections/Hero'
// below-the-fold sections are code-split + viewport-deferred so recharts and their code load on
// scroll, not up front (see DeferSection). Named exports → default-shaped for React.lazy.
const FleetStatus = lazy(() => import('./sections/FleetStatus').then((m) => ({ default: m.FleetStatus })))
const ScoreMarket = lazy(() => import('./sections/ScoreMarket').then((m) => ({ default: m.ScoreMarket })))
const PaperSession = lazy(() => import('./sections/PaperSession').then((m) => ({ default: m.PaperSession })))
const Ablation = lazy(() => import('./sections/Ablation').then((m) => ({ default: m.Ablation })))
const HazardCard = lazy(() => import('./sections/HazardCard').then((m) => ({ default: m.HazardCard })))
const Disputes = lazy(() => import('./sections/Disputes').then((m) => ({ default: m.Disputes })))
const LiveIndexer = lazy(() => import('./sections/LiveIndexer').then((m) => ({ default: m.LiveIndexer })))
const Recon = lazy(() => import('./sections/Recon').then((m) => ({ default: m.Recon })))
const SigmaSurface = lazy(() => import('./sections/SigmaSurface').then((m) => ({ default: m.SigmaSurface })))
const HfDataset = lazy(() => import('./sections/HfDataset').then((m) => ({ default: m.HfDataset })))
const HfMarkets = lazy(() => import('./sections/HfMarkets').then((m) => ({ default: m.HfMarkets })))
import { WalletProvider, useWallet } from './lib/wallet'
import { ToastProvider, useToast } from './components/Toast'
import { LiveStatusProvider, useLiveStatus, freshnessFromAge } from './components/LiveStatus'
import { ThemeProvider, useTheme } from './components/Theme'
import { CommandPalette, type Command } from './components/CommandPalette'
import { CopyButton, PanelSkeleton } from './components/ui'
import { addressUrl } from './lib/testnet'
import { short } from './lib/format'

const NAV = [
  { id: 'overview', label: 'Overview' },
  { id: 'fleet', label: 'Fleet & keeper' },
  { id: 'baserates', label: 'λ signal' },
  { id: 'score', label: 'Score a market' },
  { id: 'session', label: 'Paper engine' },
  { id: 'ablation', label: 'Edge proof' },
  { id: 'hazard', label: 'Model card' },
  { id: 'disputes', label: 'Disputes' },
  { id: 'live', label: 'Live indexer' },
  { id: 'recon', label: 'Integrity' },
  { id: 'sigma', label: 'σ surface' },
  { id: 'hfdata', label: 'HF dataset' },
  { id: 'hfmarkets', label: 'Markets' },
]
// `g`-then-letter jump keys (shown as hints in the command palette)
const GOTO_KEYS: Record<string, string> = {
  overview: 'o', baserates: 'b', score: 's', session: 'e', ablation: 'a',
  hazard: 'h', disputes: 'd', live: 'i', recon: 'r', sigma: 'v', hfdata: 'f', hfmarkets: 'm',
}

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
    // lazy/deferred sections swap their id-bearing node (skeleton → loaded), so re-observe on DOM
    // changes (debounced via rAF) rather than a one-shot observe.
    let scheduled = false
    const rescan = () => {
      scheduled = false
      obs.disconnect()
      ids.forEach((id) => { const el = document.getElementById(id); if (el) obs.observe(el) })
    }
    rescan()
    const mo = new MutationObserver(() => { if (!scheduled) { scheduled = true; requestAnimationFrame(rescan) } })
    mo.observe(document.body, { childList: true, subtree: true })
    return () => { obs.disconnect(); mo.disconnect() }
  }, [ids])
  return active
}

// Viewport-deferred, code-split section: renders a height-reserving skeleton until it nears the
// viewport, then streams in the lazy chunk. The id lives on whichever node is mounted (skeleton or
// loaded Section) — never both — so anchors (#id / g-jumps) and the scroll-spy always resolve.
function DeferSection({ id, lines = 6, children }: { id: string; lines?: number; children: ReactNode }) {
  const [show, setShow] = useState(false)
  const ref = useRef<HTMLElement>(null)
  useEffect(() => {
    if (show) return
    const el = ref.current
    if (!el) return
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) setShow(true) }, { rootMargin: '800px 0px' })
    io.observe(el)
    return () => io.disconnect()
  }, [show])
  const skeleton = <PanelSkeleton lines={lines} />
  if (!show) return <section id={id} ref={ref} className="scroll-mt-28">{skeleton}</section>
  return <Suspense fallback={<section id={id} className="scroll-mt-28">{skeleton}</section>}>{children}</Suspense>
}

function LivePill() {
  const s = useLiveStatus()
  if (s.connecting) return null
  // LIVE is gated on head FRESHNESS (server head_age_seconds — chain-head age for the RPC source),
  // not just reachability: a reachable-but-stale source reads "Nd behind" (amber), not a green LIVE.
  const f = s.up ? freshnessFromAge(s.headAgeSeconds) : { state: 'unknown' as const, behind: '' }
  const live = f.state === 'live'
  const label = !s.up ? 'indexer down'
    : live ? <>LIVE{s.latency != null && s.latency < 1000 ? ` · ${s.latency.toFixed(0)}ms` : ''}</>
    : f.state === 'syncing' ? `syncing · ${f.behind}`
    : f.state === 'stale' ? `stale · ${f.behind}`
    : 'indexer'
  return (
    <a href="#live" aria-live="polite" className={`chip ${live ? 'border-sig/40 text-sig' : 'border-warn/50 text-warn'}`} title="hosted Envio HyperIndex">
      <span className={`h-1.5 w-1.5 rounded-full ${live ? 'bg-sig animate-pulse2' : 'bg-warn'}`} />
      {label}
    </a>
  )
}

// header pending-tx indicator — surfaces any signing/mining tx globally so it's never lost on scroll
function PendingIndicator() {
  const { pendingCount } = useToast()
  return (
    <AnimatePresence>
      {pendingCount > 0 && (
        <m.a href="#fleet" initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.9 }}
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
        <span className="h-1.5 w-1.5 rounded-full bg-muted" />
        {w.connecting ? 'connecting…' : 'Connect'}
      </button>
    )
  }
  return (
    <div className="relative" ref={ref}>
      <button aria-haspopup="menu" aria-expanded={open} aria-live="polite" onClick={() => setOpen((o) => !o)}
        className={`chip ${w.onAmoy ? 'border-sig/40 text-sig' : 'border-warn/50 text-warn'}`}
        title={w.onAmoy ? 'Polygon Amoy' : 'Wrong network — switch to Amoy'}>
        <span className={`h-1.5 w-1.5 rounded-full ${w.onAmoy ? 'bg-sig animate-pulse2' : 'bg-warn'}`} />
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

  // keyboard shortcuts: `/` focuses the disputes search, `g`+letter jumps to a section
  useEffect(() => {
    let gPending = false
    let gTimer: ReturnType<typeof setTimeout> | undefined
    const isTyping = (el: HTMLElement | null) =>
      !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable)
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const t = e.target as HTMLElement
      if (e.key === '/' && !isTyping(t)) { e.preventDefault(); document.getElementById('disputes-search')?.focus(); return }
      if (isTyping(t)) return
      if (e.key === 'g') { gPending = true; clearTimeout(gTimer); gTimer = setTimeout(() => { gPending = false }, 1200); return }
      if (gPending) {
        gPending = false
        const id = Object.entries(GOTO_KEYS).find(([, k]) => k === e.key.toLowerCase())?.[0]
        if (id) { e.preventDefault(); document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' }) }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('keydown', onKey); clearTimeout(gTimer) }
  }, [])

  const commands = useMemo<Command[]>(() => {
    const goto = NAV.map((n) => ({
      id: `go-${n.id}`, group: 'Go to', label: n.label, hint: `g ${GOTO_KEYS[n.id]}`,
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
            <ThemeToggle />
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
        {/* above the fold: eager */}
        <Hero q={overview} />
        <DeferSection id="fleet" lines={7}><FleetStatus /></DeferSection>
        <BaseRates />
        {/* below the fold: code-split + viewport-deferred (recharts loads on scroll) */}
        <DeferSection id="score" lines={7}><ScoreMarket /></DeferSection>
        <DeferSection id="session" lines={8}><PaperSession /></DeferSection>
        <DeferSection id="ablation" lines={6}><Ablation /></DeferSection>
        <DeferSection id="hazard" lines={6}><HazardCard /></DeferSection>
        <DeferSection id="disputes" lines={8}><Disputes /></DeferSection>
        <DeferSection id="live" lines={6}><LiveIndexer /></DeferSection>
        <DeferSection id="recon" lines={5}><Recon /></DeferSection>
        <DeferSection id="sigma" lines={6}><SigmaSurface /></DeferSection>
        <DeferSection id="hfdata" lines={7}><HfDataset /></DeferSection>
        <DeferSection id="hfmarkets" lines={8}><HfMarkets /></DeferSection>
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

// On the free tier the whole app is asleep between visits; the Render gateway 502s every request
// until uvicorn binds. Rather than fire ~6 data endpoints into that window (each retry logging a
// console 502), we gate the fetching subtree on /api/health — the one cheap route that returns 200
// the instant the port binds. `req` already rides 502/503/504 with capped backoff, so this resolves
// the moment the backend is up, and NO data request is issued before then. A genuine cold wake shows
// this splash for ~30–60s instead of a flood of broken cards.
function HealthGate({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false)
  const [stalled, setStalled] = useState(false)
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    let alive = true
    setStalled(false)
    req('/health', undefined, { retries: 12 })
      .then(() => { if (alive) setReady(true) })
      .catch(() => { if (alive) setStalled(true) })   // exhausted the ~95s budget — offer a manual retry
    return () => { alive = false }
  }, [nonce])

  if (ready) return <>{children}</>
  return (
    <div className="grid min-h-screen place-items-center px-6">
      <div className="flex max-w-sm flex-col items-center gap-4 text-center">
        <m.span className="font-mono text-3xl text-sig"
          animate={stalled ? {} : { opacity: [0.4, 1, 0.4] }}
          transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }}>λ</m.span>
        {stalled ? (
          <>
            <p className="text-sm text-ink-2">The server is taking longer than usual to wake.</p>
            <button onClick={() => setNonce((n) => n + 1)}
              className="chip border-sig/40 text-sig hover:bg-sig/10">Retry</button>
          </>
        ) : (
          <>
            <p className="text-sm text-ink-2">Waking the server…</p>
            <p className="text-2xs text-muted">The first load after an idle period can take ~30–60s on the free tier. Hang tight — this only happens once.</p>
          </>
        )}
      </div>
    </div>
  )
}

export default function App() {
  return (
    <ThemeProvider>
      <LazyMotion features={domMax}>
        <MotionConfig reducedMotion="user">
          <WalletProvider>
            <ToastProvider>
              <HealthGate>
                <LiveStatusProvider>
                  <AppInner />
                </LiveStatusProvider>
              </HealthGate>
            </ToastProvider>
          </WalletProvider>
        </MotionConfig>
      </LazyMotion>
    </ThemeProvider>
  )
}

function ThemeToggle() {
  const { theme, toggle } = useTheme()
  const dark = theme === 'dark'
  return (
    <button onClick={toggle} className="chip hover:border-sig/40 hover:text-sig"
      aria-label={dark ? 'Switch to light theme' : 'Switch to dark theme'} title={dark ? 'Light mode' : 'Dark mode'}>
      <span aria-hidden>{dark ? '☾' : '☀'}</span>
    </button>
  )
}
