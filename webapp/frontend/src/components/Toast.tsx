import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, m } from 'framer-motion'

export type ToastVariant = 'pending' | 'success' | 'error' | 'info'

export interface Toast {
  id: string
  variant: ToastVariant
  title: string
  message?: string
  href?: string
  hrefLabel?: string
}

type ToastInput = Omit<Toast, 'id'>

interface ToastApi {
  push: (t: ToastInput) => string
  update: (id: string, patch: Partial<ToastInput>) => void
  dismiss: (id: string) => void
  pending: (title: string, message?: string) => string
  success: (title: string, opts?: Partial<ToastInput>) => string
  error: (title: string, opts?: Partial<ToastInput>) => string
  info: (title: string, opts?: Partial<ToastInput>) => string
  pendingCount: number
}

const ToastContext = createContext<ToastApi | null>(null)

const AUTO_DISMISS_MS = 6500
// ids don't need crypto-randomness; Math.random/Date.now are avoided elsewhere but fine in the browser
let seq = 0
const nextId = () => `t${++seq}`

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({})

  const clearTimer = useCallback((id: string) => {
    const t = timers.current[id]
    if (t) { clearTimeout(t); delete timers.current[id] }
  }, [])

  const dismiss = useCallback((id: string) => {
    clearTimer(id)
    setToasts((ts) => ts.filter((t) => t.id !== id))
  }, [clearTimer])

  const arm = useCallback((id: string, variant: ToastVariant) => {
    clearTimer(id)
    if (variant !== 'pending') timers.current[id] = setTimeout(() => dismiss(id), AUTO_DISMISS_MS)
  }, [clearTimer, dismiss])

  const push = useCallback((t: ToastInput) => {
    const id = nextId()
    setToasts((ts) => [...ts, { ...t, id }])
    arm(id, t.variant)
    return id
  }, [arm])

  const update = useCallback((id: string, patch: Partial<ToastInput>) => {
    setToasts((ts) => ts.map((t) => (t.id === id ? { ...t, ...patch } : t)))
    if (patch.variant) arm(id, patch.variant)
  }, [arm])

  const api = useMemo<ToastApi>(() => ({
    push, update, dismiss,
    pending: (title, message) => push({ variant: 'pending', title, message }),
    success: (title, opts) => push({ variant: 'success', title, ...opts }),
    error: (title, opts) => push({ variant: 'error', title, ...opts }),
    info: (title, opts) => push({ variant: 'info', title, ...opts }),
    pendingCount: toasts.filter((t) => t.variant === 'pending').length,
  }), [push, update, dismiss, toasts])

  return (
    <ToastContext.Provider value={api}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  )
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within <ToastProvider>')
  return ctx
}

// theme-aware status dot classes (follow the CSS-var tokens across light/dark)
const DOT: Record<ToastVariant, string> = {
  pending: 'bg-warn', success: 'bg-profit', error: 'bg-loss', info: 'bg-muted',
}

function ToastViewport({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: string) => void }) {
  if (typeof document === 'undefined') return null
  return createPortal(
    <div aria-live="polite" aria-relevant="additions text"
      className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-[min(92vw,360px)] flex-col gap-2">
      <AnimatePresence initial={false}>
        {toasts.map((t) => (
          <m.div key={t.id} layout
            initial={{ opacity: 0, y: 12, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, x: 24, scale: 0.98 }}
            transition={{ type: 'spring', stiffness: 480, damping: 38 }}
            className="panel pointer-events-auto flex items-start gap-3 p-3 shadow-panel">
            <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${DOT[t.variant]} ${t.variant === 'pending' ? 'animate-pulse2' : ''}`} />
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium text-ink">{t.title}</div>
              {t.message && <div className="mt-0.5 break-words text-2xs text-ink-2">{t.message}</div>}
              {t.href && (
                <a href={t.href} target="_blank" rel="noreferrer"
                  className="mt-1 inline-block text-2xs text-sig link-underline">{t.hrefLabel ?? 'view ↗'}</a>
              )}
            </div>
            <button onClick={() => onDismiss(t.id)} aria-label="Dismiss notification"
              className="-mr-1 -mt-1 shrink-0 rounded p-1 text-muted transition-colors hover:text-ink">✕</button>
          </m.div>
        ))}
      </AnimatePresence>
    </div>,
    document.body,
  )
}
