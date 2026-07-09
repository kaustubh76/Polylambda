import { useCallback, useEffect, useRef, useState } from 'react'

const BASE = '/api'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    ...init,
    headers: { 'content-type': 'application/json', ...(init?.headers || {}) },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error((body as any).detail || `${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  overview: () => req<Overview>('/overview'),
  baserates: () => req<BaseRates>('/baserates'),
  score: (body: ScoreReq) => req<ScoreResp>('/lambda/score', { method: 'POST', body: JSON.stringify(body) }),
  session: (body: SessionReq) => req<SessionResp>('/session/run', { method: 'POST', body: JSON.stringify(body) }),
  ablation: () => req<Ablation>('/ablation'),
  hazard: () => req<Hazard>('/hazard'),
  disputes: (qs: string) => req<Disputes>(`/disputes${qs}`),
  recon: () => req<Recon>('/recon'),
  sigma: () => req<Sigma>('/sigma'),
  liveStatus: () => req<LiveStatus>('/live/status'),
  liveDisputes: (limit = 25) => req<LiveDisputes>(`/live/disputes?limit=${limit}`),
  // testnet (on-chain PolyLambda market, Polygon Amoy)
  tnStatus: () => req<TnStatus>('/testnet/status'),
  tnMarket: () => req<TnMarket>('/testnet/market'),
  tnPosition: (address: string) => req<TnPosition>(`/testnet/position?address=${address}`),
  tnEvents: (limit = 30) => req<TnEvents>(`/testnet/events?limit=${limit}`),
  tnEngineQuote: (body: { price?: number; category?: string }) => req<TnTx>('/testnet/engine-quote', { method: 'POST', body: JSON.stringify(body) }),
  tnDispute: () => req<TnTx>('/testnet/dispute', { method: 'POST', body: '{}' }),
  tnResolve: (yes_won: boolean) => req<TnTx>('/testnet/resolve', { method: 'POST', body: JSON.stringify({ yes_won }) }),
}

// --- a tiny fetch hook (no react-query dep) ---------------------------------------------------
export function useApi<T>(fn: () => Promise<T>, deps: unknown[] = []): {
  data: T | null; error: string | null; loading: boolean; reload: () => void
} {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const fnRef = useRef(fn)
  fnRef.current = fn
  const reload = useCallback(() => {
    setLoading(true); setError(null)
    fnRef.current().then(setData).catch((e) => setError(String(e.message || e))).finally(() => setLoading(false))
  }, [])
  useEffect(() => { reload() }, deps) // eslint-disable-line react-hooks/exhaustive-deps
  return { data, error, loading, reload }
}

// mutation-style hook for POST actions the user triggers
export function useAction<T, A>(fn: (arg: A) => Promise<T>): {
  run: (arg: A) => Promise<void>; data: T | null; error: string | null; loading: boolean
} {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const run = useCallback(async (arg: A) => {
    setLoading(true); setError(null)
    try { setData(await fn(arg)) } catch (e: any) { setError(String(e.message || e)) } finally { setLoading(false) }
  }, [fn])
  return { run, data, error, loading }
}

// ---- response types --------------------------------------------------------------------------
export interface Tile { label: string; value: number; fmt: string; sub: string }
export interface Overview {
  thesis: string; thesis_nuance: string; jump_diffusion: string; mode: string; positioning: string
  tiles: Tile[]; frozen_params: Record<string, number | string>; frozen_params_source: string
  dataset: { total_disputes: number; hf_joinable_pct: number; by_year: Record<string, number>
    by_adapter: Record<string, number>; date_min: string; date_max: string }
}
export interface BaseRateRow { category: string; disputes: number; resolved: number; rate: number; ci_low: number; ci_high: number }
export interface BaseRates { source: string; rows: BaseRateRow[]; headline: string }

export interface ScoreReq { category: string; fill_count: number; price: number; proposer?: string | null; inventory: number; horizon_days: number }
export interface ScoreResp {
  inputs: ScoreReq
  features: { category_base_rate: number; market_size: number; proposer_reliability: number; latency_anomaly: number }
  base_rate: { rate: number; ci_low: number; ci_high: number; disputes: number; resolved: number }
  lambda: { lambda_select: number; lambda_jump: number; jump_drift: number; e_loss: number; ci_low: number; ci_high: number; model: string }
  quote: { mid: number; bid: number; ask: number; spread: number; sigma: number; diffusion_logit: number; jump_logit: number; jump_share: number }
  exit_gate: { lambda_jump: number; lambda_star: number; e_jump_loss_usd: number; forgone_rewards: number; spread_cost: number; would_exit: boolean; reason: string }
}

export interface SessionReq { scenario: string; category?: string; entry_price?: number; inventory?: number; dispute_tick?: number; gap_logit?: number; n_ticks?: number; n_markets?: number; seed?: number }
export interface DDPoint { i: number; mid: number; inventory: number; equity: number; cash: number }
export interface ExitEvent { cid: string; trigger: string; inventory_before: number; inventory_after: number; exit_price: number; haircut_paid: number; lambda_jump: number; lambda_star: number; e_jump_loss: number; forgone_rewards: number }
export interface SessionResp {
  simulated: boolean; scenario: string
  params?: Record<string, number | string>
  series: Record<string, any[]> | { lambda_on: DDPoint[]; lambda_off: DDPoint[] }
  exits?: ExitEvent[]
  summary?: any
  narrative?: string
  quotes?: Record<string, any[]>
  n_fills?: number
}

export interface AblationArm { arm: string; arm_label: string; points: { lambda_star: number; pnl_net_of_rewards: number; sharpe: number }[] }
export interface Ablation { source: string; meta: Record<string, number | string>; lambda_star_grid: number[]; arms: AblationArm[]; headline: string; caveat: string }

export interface HazardCardT { label: string; coef: number[]; intercept: number; offset: number; feature_order: string[]; holdout_auc: number; brier: number; n: number; positives: number; natural_rate: number; discriminates: boolean }
export interface Hazard { deployed: HazardCardT | null; matched: HazardCardT | null; matched_eval: HazardCardT | null; caveat: string; null_finding: string }

export interface Disputes { total: number; rows: Record<string, any>[]; columns: string[]; facets: { category: Record<string, number>; adapter: Record<string, number>; year: Record<string, number> } }
export interface Recon { recon: Record<string, any>; by_adapter: Record<string, number>; by_category: Record<string, number>; total_disputes: number; hf_joinable_pct: number; note: string }
export interface SigmaPoint { category: string; price: number; sigma: number }
export interface Sigma { points: SigmaPoint[]; categories: string[]; n: number; note: string }

export interface LiveStatus { reachable: boolean; endpoint: string; latency_ms?: number; head_ts?: number | null; head_id?: string | null; error?: string }
export interface LiveDispute { id: string; round: number | null; disputeTs: number; disputer: string | null; proposedOutcome: string | null; proposer: string | null; conditionId: string | null; marketStatus: string | null; finalOutcome: string | null; outcomeSlotCount: number | null }
export interface LiveDisputes { reachable: boolean; disputes: LiveDispute[]; latency_ms?: number; endpoint: string; error?: string }

// ---- testnet (Polygon Amoy on-chain market) --------------------------------------------------
export interface TnMarket {
  deployed: boolean; bid: number; ask: number; max_trade?: number; quote_ts?: number
  disputed?: boolean; resolved?: boolean; yes_won?: boolean; total_yes?: number
  escrow_usdc?: number; category?: string | null; lambda_jump?: number; sigma?: number
}
export interface TnStatus {
  reachable: boolean; chain_id?: number; block?: number; engine?: string | null; engine_pol?: number | null
  engine_ready?: boolean; market_address?: string | null; usdc?: string; explorer?: string
  market?: TnMarket; error?: string
}
export interface TnPosition {
  reachable: boolean; address?: string; shares: number; mark?: number; mark_value?: number
  usdc?: number; disputed?: boolean; resolved?: boolean; yes_won?: boolean
}
export interface TnEvent {
  type: string; block: number; log_index: number; tx: string
  user?: string; buy?: boolean; size?: number; usdc?: number; bid?: number; ask?: number
  category?: string; lambda_jump?: number; yes_won?: boolean; payout?: number; amount?: number
}
export interface TnEvents { reachable: boolean; events: TnEvent[]; explorer?: string }
export interface TnTx { tx: string; explorer: string; bid?: number; ask?: number; category?: string; lambda_jump?: number; sigma?: number; yesWon?: boolean }
