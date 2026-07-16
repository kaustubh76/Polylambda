import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, usePoll } from '../api/client'

// One shared /live/status poller for the whole app (header pill + live-indexer section) instead
// of two independent timers. Sticky-live: only flips to down after 2 consecutive failures.
export interface LiveStatusValue {
  up: boolean
  connecting: boolean
  latency?: number
  headTs?: number | null
  headId?: string | null
  headAgeSeconds?: number | null    // server-computed; chain-head age for the RPC source
  source?: string                   // "envio" | "rpc"
  endpoint?: string
  error?: string
  refresh: () => void
}

const Ctx = createContext<LiveStatusValue | null>(null)
const POLL_MS = 10000

// Freshness is derived from head age (now − head_ts), NOT reachability — a stopped-but-reachable
// indexer (our exact dev-deploy failure mode) must read "stale", never "LIVE".
const FRESH_MAX_S = 15 * 60          // ≤15 min behind head → genuinely live
const SYNCING_MAX_S = 2 * 86400      // ≤2 days behind → catching up
export type Freshness = 'live' | 'syncing' | 'stale' | 'unknown'

export function freshnessFromAge(ageSec?: number | null): { state: Freshness; ageSec: number | null; behind: string } {
  if (ageSec == null) return { state: 'unknown', ageSec: null, behind: '' }
  const a = Math.max(0, Math.floor(ageSec))
  const state: Freshness = a <= FRESH_MAX_S ? 'live' : a <= SYNCING_MAX_S ? 'syncing' : 'stale'
  const d = Math.floor(a / 86400)
  const h = Math.floor((a % 86400) / 3600)
  const m = Math.floor((a % 3600) / 60)
  const behind = d >= 1 ? `${d}d behind` : h >= 1 ? `${h}h behind` : `${m}m behind`
  return { state, ageSec: a, behind }
}

// Freshness from a head timestamp (client clock). Prefer freshnessFromAge with the server's
// head_age_seconds when available — for the RPC source that age is the CHAIN-head age (proves tip),
// whereas headTs here is the latest-dispute time, which is intentionally allowed to lag.
export function freshnessOf(headTs?: number | null, nowMs: number = Date.now()): { state: Freshness; ageSec: number | null; behind: string } {
  if (headTs == null) return { state: 'unknown', ageSec: null, behind: '' }
  return freshnessFromAge(Math.floor(nowMs / 1000) - headTs)
}

export function LiveStatusProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ up: boolean; latency?: number; headTs?: number | null; headId?: string | null; headAgeSeconds?: number | null; source?: string; endpoint?: string; error?: string } | null>(null)
  const fails = useRef(0)
  const alive = useRef(true)

  const tick = useCallback((): Promise<boolean> => {
    return api.liveStatus()
      .then((r) => {
        if (!alive.current) return true
        fails.current = 0
        setState({ up: r.reachable, latency: r.latency_ms, headTs: r.head_ts, headId: r.head_id, headAgeSeconds: r.head_age_seconds, source: r.source, endpoint: r.endpoint, error: r.error })
        return true
      })
      .catch(() => {
        if (alive.current && ++fails.current >= 2) setState((p) => ({ ...(p || {}), up: false }))
        return false
      })
  }, [])

  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])
  usePoll(tick, POLL_MS, 1400)

  const value: LiveStatusValue = {
    up: !!state?.up,
    connecting: state == null,
    latency: state?.latency,
    headTs: state?.headTs,
    headId: state?.headId,
    headAgeSeconds: state?.headAgeSeconds,
    source: state?.source,
    endpoint: state?.endpoint,
    error: state?.error,
    refresh: () => { tick() },
  }
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useLiveStatus(): LiveStatusValue {
  const c = useContext(Ctx)
  if (!c) throw new Error('useLiveStatus must be used within <LiveStatusProvider>')
  return c
}
