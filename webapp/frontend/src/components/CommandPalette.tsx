import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, m } from 'framer-motion'

export interface Command {
  id: string
  label: string
  hint?: string
  group?: string
  run: () => void
}

// ⌘K / Ctrl-K quick launcher over sections + quick actions. Self-manages its open state and the
// global hotkey; parent just supplies the command list.
export function CommandPalette({ commands }: { commands: Command[] }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setOpen((o) => !o) }
      else if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => { if (open) { setQuery(''); setActive(0); setTimeout(() => inputRef.current?.focus(), 0) } }, [open])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return commands
    // simple subsequence fuzzy match on label + hint
    return commands.filter((c) => {
      const hay = `${c.label} ${c.hint ?? ''} ${c.group ?? ''}`.toLowerCase()
      let i = 0
      for (const ch of q) { i = hay.indexOf(ch, i); if (i === -1) return false; i++ }
      return true
    })
  }, [commands, query])

  useEffect(() => { setActive((a) => Math.min(a, Math.max(0, filtered.length - 1))) }, [filtered.length])

  const choose = (c?: Command) => { if (c) { c.run(); setOpen(false) } }

  const onListKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, filtered.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); choose(filtered[active]) }
  }

  if (typeof document === 'undefined') return null
  return createPortal(
    <AnimatePresence>
      {open && (
        <m.div className="fixed inset-0 z-[95] flex items-start justify-center p-4 pt-[12vh]"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setOpen(false)} />
          <m.div role="dialog" aria-modal="true" aria-label="Command palette"
            initial={{ opacity: 0, y: 10, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 6, scale: 0.98 }}
            transition={{ type: 'spring', stiffness: 460, damping: 34 }}
            onKeyDown={onListKey}
            className="panel relative z-10 w-[min(94vw,540px)] overflow-hidden p-0">
            <div className="flex items-center gap-2 border-b border-line px-4 py-3">
              <span className="text-muted" aria-hidden>⌘K</span>
              <input ref={inputRef} value={query} onChange={(e) => setQuery(e.target.value)}
                placeholder="Jump to a section or run an action…"
                className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted" />
            </div>
            <ul className="max-h-[52vh] overflow-y-auto py-1.5">
              {filtered.length === 0 && <li className="px-4 py-6 text-center text-sm text-muted">no matches</li>}
              {filtered.map((c, i) => (
                <li key={c.id}>
                  <button onMouseEnter={() => setActive(i)} onClick={() => choose(c)}
                    className={`flex w-full items-center gap-3 px-4 py-2 text-left text-sm transition-colors ${
                      i === active ? 'bg-sig/10 text-sig' : 'text-ink-2 hover:bg-elevated/40'}`}>
                    {c.group && <span className="w-16 shrink-0 text-2xs uppercase tracking-wide text-muted">{c.group}</span>}
                    <span className="flex-1 truncate">{c.label}</span>
                    {c.hint && <span className="num shrink-0 text-2xs text-muted">{c.hint}</span>}
                  </button>
                </li>
              ))}
            </ul>
            <div className="flex items-center gap-3 border-t border-line px-4 py-2 text-2xs text-muted">
              <span>↑↓ navigate</span><span>↵ select</span><span>esc close</span>
            </div>
          </m.div>
        </m.div>
      )}
    </AnimatePresence>,
    document.body,
  )
}
