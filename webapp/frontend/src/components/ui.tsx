import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, m } from 'framer-motion'
import { AnimatedNumber, revealUp, staggerContainer } from '../lib/motion'

// ---- Section wrapper: anchor + header + body -------------------------------------------------
// The <section> is a scroll-triggered stagger container: its header (kicker/title/subtitle) and
// body reveal in sequence as the section enters view, once. Every one of the 11 sections gets
// this for free. Reduced-motion is handled by <MotionConfig reducedMotion="user"> at the root
// (transforms are stripped; a gentle opacity fade remains).
export function Section({ id, kicker, title, subtitle, children, right }: {
  id: string; kicker?: string; title: string; subtitle?: ReactNode; children: ReactNode; right?: ReactNode
}) {
  return (
    <m.section id={id} className="scroll-mt-28"
      initial="hidden" whileInView="show" viewport={{ once: true, margin: '-12% 0px' }}
      variants={staggerContainer}>
      <m.div className="mb-4 flex flex-wrap items-end justify-between gap-3" variants={revealUp}>
        <div>
          {kicker && <div className="label mb-1 text-sig">{kicker}</div>}
          <h2 className="text-xl font-semibold tracking-tight text-ink">{title}</h2>
          {subtitle && <p className="mt-1 max-w-2xl text-sm text-ink-2">{subtitle}</p>}
        </div>
        {right}
      </m.div>
      <m.div variants={revealUp}>{children}</m.div>
    </m.section>
  )
}

export function Panel({ children, className = '', pad = true, hoverable = false, reveal = false }: {
  children: ReactNode; className?: string; pad?: boolean; hoverable?: boolean; reveal?: boolean
}) {
  const cls = `panel ${hoverable ? 'panel-interactive' : ''} ${pad ? 'p-5' : ''} ${className}`
  if (reveal) {
    return (
      <m.div className={cls} initial="hidden" whileInView="show"
        viewport={{ once: true, margin: '-8% 0px' }} variants={revealUp}>{children}</m.div>
    )
  }
  return <div className={cls}>{children}</div>
}

// ---- Stat tile -------------------------------------------------------------------------------
export function Stat({ label, value, sub, accent = false, tone, format, hoverable = true }: {
  label: string; value: ReactNode; sub?: ReactNode; accent?: boolean; tone?: 'profit' | 'loss' | 'warn'
  // pass a raw number + format to get the count-up / smooth-on-poll animation
  format?: (n: number) => string; hoverable?: boolean
}) {
  const toneCls = tone === 'profit' ? 'text-profit' : tone === 'loss' ? 'text-loss' : tone === 'warn' ? 'text-warn' : accent ? 'text-sig' : 'text-ink'
  const body = format && typeof value === 'number'
    ? <AnimatedNumber value={value} format={format} />
    : value
  return (
    <div className={`panel p-4 ${hoverable ? 'panel-interactive' : ''}`}>
      <div className="label">{label}</div>
      <div className={`num mt-1.5 text-2xl font-semibold ${toneCls}`}>{body}</div>
      {sub && <div className="mt-1 text-2xs text-muted">{sub}</div>}
    </div>
  )
}

// ---- Pill / chip -----------------------------------------------------------------------------
export function Pill({ children, dot, color = '#6b7280' }: { children: ReactNode; dot?: boolean; color?: string }) {
  return (
    <span className="chip">
      {dot && <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />}
      {children}
    </span>
  )
}

export function SourceTag({ source }: { source: string }) {
  const live = source === 'live'
  return (
    <Pill dot color={live ? '#24c98a' : '#fab219'}>
      {live ? 'live-computed' : 'published'}
    </Pill>
  )
}

// ---- Caveat / methodology note (honesty-as-a-feature) ----------------------------------------
export function Caveat({ kind = 'note', children }: { kind?: 'note' | 'null' | 'underpowered' | 'calibration'; children: ReactNode }) {
  const map = {
    note: { c: '#6b7280', t: 'note' },
    null: { c: '#fab219', t: 'null result' },
    underpowered: { c: '#fab219', t: 'underpowered' },
    calibration: { c: '#ec835a', t: 'calibration-limited' },
  }[kind]
  return (
    <div className="flex gap-2.5 rounded-lg border border-line bg-elevated/40 p-3 text-2xs leading-relaxed text-ink-2">
      <span className="mt-px shrink-0 rounded px-1.5 py-0.5 font-mono uppercase tracking-wide"
        style={{ background: `${map.c}1f`, color: map.c }}>{map.t}</span>
      <span>{children}</span>
    </div>
  )
}

// ---- misc ------------------------------------------------------------------------------------
export function Loading({ label = 'loading' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 p-6 text-sm text-muted">
      <span className="h-2 w-2 animate-pulse2 rounded-full bg-sig" />
      {label}…
    </div>
  )
}

// shimmer placeholder sized to final layout — kills the collapse-then-snap layout shift
export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`skeleton ${className}`} />
}

// a panel-shaped skeleton that reserves a section's height while its data loads
export function PanelSkeleton({ h = 'h-40', lines }: { h?: string; lines?: number }) {
  return (
    <div className={`panel p-5 ${lines ? '' : h}`}>
      {lines
        ? <div className="space-y-2.5">{Array.from({ length: lines }).map((_, i) => (
            <Skeleton key={i} className={`h-3.5 ${i % 3 === 0 ? 'w-2/3' : i % 3 === 1 ? 'w-5/6' : 'w-1/2'}`} />))}</div>
        : null}
    </div>
  )
}

export function ErrorBox({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-crit/30 bg-crit/10 p-3 text-sm text-loss">
      <span className="min-w-0 flex-1">⚠ {error}</span>
      {onRetry && (
        <button onClick={onRetry} className="btn !py-1 text-2xs text-ink">↻ Retry</button>
      )}
    </div>
  )
}

// Stale-while-revalidate: once `data` exists we keep rendering it (with a subtle corner spinner
// while re-fetching) instead of tearing it down to a skeleton — this preserves input focus and
// avoids the flash on every refetch. The skeleton only shows on the very first load (data===null).
export function Async<T>({ q, skeleton, children }: {
  q: { data: T | null; error: string | null; loading: boolean; reload?: () => void }
  skeleton?: ReactNode; children: (d: T) => ReactNode
}) {
  // hard error with no data to fall back on → error box (+ retry if the hook exposes reload)
  if (q.error && q.data == null) {
    return (
      <AnimatePresence mode="wait" initial={false}>
        <m.div key="err" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <ErrorBox error={q.error} onRetry={q.reload} />
        </m.div>
      </AnimatePresence>
    )
  }
  if (q.data != null) {
    return (
      <div className="relative">
        {q.loading && (
          <span aria-hidden className="absolute -top-1 right-0 z-10 flex items-center gap-1 text-2xs text-muted">
            <span className="h-1.5 w-1.5 animate-pulse2 rounded-full bg-sig" />updating
          </span>
        )}
        <m.div key="data" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}>
          {children(q.data)}
        </m.div>
      </div>
    )
  }
  // first load
  return (
    <AnimatePresence mode="wait" initial={false}>
      <m.div key="load" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }}>
        {skeleton ?? <Loading />}
      </m.div>
    </AnimatePresence>
  )
}

// ---- Copy-to-clipboard ------------------------------------------------------------------------
export function useCopy(timeout = 1400) {
  const [copied, setCopied] = useState(false)
  const t = useRef<ReturnType<typeof setTimeout>>()
  const copy = useCallback((text: string) => {
    navigator.clipboard?.writeText(text).then(() => {
      setCopied(true)
      if (t.current) clearTimeout(t.current)
      t.current = setTimeout(() => setCopied(false), timeout)
    }).catch(() => {})
  }, [timeout])
  useEffect(() => () => { if (t.current) clearTimeout(t.current) }, [])
  return { copied, copy }
}

// small inline copy affordance — shows a check for a moment after copying
export function CopyButton({ value, label = 'Copy', className = '' }: { value: string; label?: string; className?: string }) {
  const { copied, copy } = useCopy()
  return (
    <button type="button" onClick={() => copy(value)} aria-label={copied ? 'Copied' : label}
      className={`inline-flex items-center rounded p-0.5 text-muted transition-colors hover:text-sig ${className}`}>
      {copied ? <span className="text-sig">✓</span> : <span aria-hidden>⧉</span>}
    </button>
  )
}

// ---- Modal / ConfirmDialog (portal, focus-trapped, Esc + backdrop to close) -------------------
export function Modal({ open, onClose, children, labelledBy }: {
  open: boolean; onClose: () => void; children: ReactNode; labelledBy?: string
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    const prevFocus = document.activeElement as HTMLElement | null
    // move focus into the dialog
    const t = setTimeout(() => panelRef.current?.focus(), 0)
    return () => { document.removeEventListener('keydown', onKey); clearTimeout(t); prevFocus?.focus?.() }
  }, [open, onClose])
  if (typeof document === 'undefined') return null
  return createPortal(
    <AnimatePresence>
      {open && (
        <m.div className="fixed inset-0 z-[90] flex items-center justify-center p-4"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
          <m.div ref={panelRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={labelledBy}
            initial={{ opacity: 0, y: 12, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={{ type: 'spring', stiffness: 420, damping: 34 }}
            className="panel relative z-10 w-[min(94vw,440px)] p-5 outline-none">
            {children}
          </m.div>
        </m.div>
      )}
    </AnimatePresence>,
    document.body,
  )
}

export function ConfirmDialog({ open, onClose, onConfirm, title, body, confirmLabel = 'Confirm', tone = 'default', busy = false }: {
  open: boolean; onClose: () => void; onConfirm: () => void; title: string; body: ReactNode
  confirmLabel?: string; tone?: 'default' | 'warn'; busy?: boolean
}) {
  return (
    <Modal open={open} onClose={onClose} labelledBy="confirm-title">
      <h3 id="confirm-title" className="text-base font-semibold text-ink">{title}</h3>
      <div className="mt-2 text-sm leading-relaxed text-ink-2">{body}</div>
      <div className="mt-5 flex justify-end gap-2">
        <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
        <button className={`btn ${tone === 'warn' ? 'border-warn/50 text-warn hover:bg-warn/10' : 'btn-primary'}`}
          onClick={onConfirm} disabled={busy}>{busy ? 'working…' : confirmLabel}</button>
      </div>
    </Modal>
  )
}

export function KV({ k, v, mono = true }: { k: ReactNode; v: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-line/60 py-1.5 last:border-0">
      <span className="text-2xs text-muted">{k}</span>
      <span className={`text-sm text-ink-2 ${mono ? 'num' : ''}`}>{v}</span>
    </div>
  )
}

// tiny arrow for directional drift
export function Drift({ v }: { v: number }) {
  if (Math.abs(v) < 1e-9) return <span className="text-muted">→ neutral</span>
  const up = v > 0
  return (
    <span className={up ? 'text-profit' : 'text-loss'}>
      {up ? '↑ toward YES' : '↓ toward NO'}
    </span>
  )
}
