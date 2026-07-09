import type { ReactNode } from 'react'

// ---- Section wrapper: anchor + header + body -------------------------------------------------
export function Section({ id, kicker, title, subtitle, children, right }: {
  id: string; kicker?: string; title: string; subtitle?: ReactNode; children: ReactNode; right?: ReactNode
}) {
  return (
    <section id={id} className="scroll-mt-28 animate-fade-up">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          {kicker && <div className="label mb-1 text-sig">{kicker}</div>}
          <h2 className="text-xl font-semibold tracking-tight text-ink">{title}</h2>
          {subtitle && <p className="mt-1 max-w-2xl text-sm text-ink-2">{subtitle}</p>}
        </div>
        {right}
      </div>
      {children}
    </section>
  )
}

export function Panel({ children, className = '', pad = true }: { children: ReactNode; className?: string; pad?: boolean }) {
  return <div className={`panel ${pad ? 'p-5' : ''} ${className}`}>{children}</div>
}

// ---- Stat tile -------------------------------------------------------------------------------
export function Stat({ label, value, sub, accent = false, tone }: {
  label: string; value: ReactNode; sub?: ReactNode; accent?: boolean; tone?: 'profit' | 'loss' | 'warn'
}) {
  const toneCls = tone === 'profit' ? 'text-profit' : tone === 'loss' ? 'text-loss' : tone === 'warn' ? 'text-warn' : accent ? 'text-sig' : 'text-ink'
  return (
    <div className="panel p-4">
      <div className="label">{label}</div>
      <div className={`num mt-1.5 text-2xl font-semibold ${toneCls}`}>{value}</div>
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
  return <div className={`animate-pulse2 rounded-md bg-elevated/70 ${className}`} />
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

export function ErrorBox({ error }: { error: string }) {
  return <div className="rounded-lg border border-crit/30 bg-crit/10 p-3 text-sm text-loss">⚠ {error}</div>
}

export function Async<T>({ q, skeleton, children }: {
  q: { data: T | null; error: string | null; loading: boolean }
  skeleton?: ReactNode; children: (d: T) => ReactNode
}) {
  if (q.error) return <ErrorBox error={q.error} />
  if (q.loading || !q.data) return <>{skeleton ?? <Loading />}</>
  return <>{children(q.data)}</>
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
