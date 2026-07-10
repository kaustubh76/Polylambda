import { useEffect, useState } from 'react'

// ---- localStorage-backed state (persist UI across reloads) -----------------------------------
export function usePersistentState<T>(key: string, initial: T): [T, (v: T | ((p: T) => T)) => void] {
  const [v, setV] = useState<T>(() => {
    try { const raw = localStorage.getItem(key); return raw != null ? (JSON.parse(raw) as T) : initial } catch { return initial }
  })
  useEffect(() => {
    try { localStorage.setItem(key, JSON.stringify(v)) } catch { /* quota / private mode */ }
  }, [key, v])
  return [v, setV]
}

// ---- URL query helpers (shareable deep-links) ------------------------------------------------
export function readQueryParam(key: string): string | undefined {
  if (typeof location === 'undefined') return undefined
  return new URLSearchParams(location.search).get(key) ?? undefined
}

// merge a patch into the URL query without a navigation (empty/undefined values are removed)
export function writeQuery(patch: Record<string, string | undefined | null>) {
  if (typeof location === 'undefined') return
  const p = new URLSearchParams(location.search)
  for (const [k, val] of Object.entries(patch)) {
    if (val == null || val === '') p.delete(k)
    else p.set(k, val)
  }
  const qs = p.toString()
  history.replaceState(null, '', `${location.pathname}${qs ? `?${qs}` : ''}${location.hash}`)
}
