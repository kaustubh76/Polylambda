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
  endpoint?: string
  error?: string
  refresh: () => void
}

const Ctx = createContext<LiveStatusValue | null>(null)
const POLL_MS = 10000

export function LiveStatusProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ up: boolean; latency?: number; headTs?: number | null; headId?: string | null; endpoint?: string; error?: string } | null>(null)
  const fails = useRef(0)
  const alive = useRef(true)

  const tick = useCallback((): Promise<boolean> => {
    return api.liveStatus()
      .then((r) => {
        if (!alive.current) return true
        fails.current = 0
        setState({ up: r.reachable, latency: r.latency_ms, headTs: r.head_ts, headId: r.head_id, endpoint: r.endpoint, error: r.error })
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
