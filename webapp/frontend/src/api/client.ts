import { useCallback, useEffect, useRef, useState } from 'react'

const BASE = '/api'

// Gateway errors (Render cold start / restart) and network failures are transient — one-shot GETs
// retry through them so a page load during the boot window self-heals instead of leaving a dead panel.
// On the FREE tier a cold wake takes ~30–60s (whole app down until uvicorn binds), so the budget is 8
// retries with a CAPPED backoff (1+2+4+8+10+10+10+10 ≈ 55s) — enough to ride a wake. Uncapped
// 2**attempt would balloon to minutes. During the retries the useApi card stays in `loading` (shows a
// skeleton, not an error), so a cold wake self-heals invisibly. POSTs are never retried (the
// engine-signed testnet writes are not idempotent); polled GETs pass retries: 0 (their poll loop is the
// retry). Non-gateway failures (4xx/500) throw immediately — only 502/503/504 + network errors retry.
const RETRYABLE_STATUS = new Set([502, 503, 504])
const RETRY_BACKOFF_CAP_MS = 10_000
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
const backoff = (attempt: number) => Math.min(1000 * 2 ** attempt, RETRY_BACKOFF_CAP_MS) + Math.random() * 250

export async function req<T>(path: string, init?: RequestInit, opts?: { retries?: number }): Promise<T> {
  const isGet = !init?.method || init.method.toUpperCase() === 'GET'
  const retries = isGet ? opts?.retries ?? 8 : 0
  for (let attempt = 0; ; attempt++) {
    let res: Response
    try {
      res = await fetch(BASE + path, {
        ...init,
        headers: { 'content-type': 'application/json', ...(init?.headers || {}) },
      })
    } catch (e) {
      if (attempt < retries) { await sleep(backoff(attempt)); continue }
      throw e
    }
    if (!res.ok) {
      if (RETRYABLE_STATUS.has(res.status) && attempt < retries) {
        await sleep(backoff(attempt))
        continue
      }
      const body = await res.json().catch(() => ({}))
      throw new Error((body as any).detail || `${res.status} ${res.statusText}`)
    }
    return res.json() as Promise<T>
  }
}

export const api = {
  overview: () => req<Overview>('/overview'),
  baserates: () => req<BaseRates>('/baserates'),
  score: (body: ScoreReq) => req<ScoreResp>('/lambda/score', { method: 'POST', body: JSON.stringify(body) }),
  session: (body: SessionReq) => req<SessionResp>('/session/run', { method: 'POST', body: JSON.stringify(body) }),
  ablation: (live = false) => req<Ablation>(`/ablation${live ? '?live=1' : ''}`),
  hazard: () => req<Hazard>('/hazard'),
  disputes: (qs: string) => req<Disputes>(`/disputes${qs}`),
  recon: () => req<Recon>('/recon'),
  reconLive: () => req<Recon>('/recon/live'),
  sigma: () => req<Sigma>('/sigma'),
  proposers: (limit = 15) => req<Proposers>(`/proposers?limit=${limit}`),
  disputeAnalytics: (bins = 24, category?: string, adapter?: string) =>
    req<DisputeAnalytics>(`/disputes/analytics?bins=${bins}`
      + (category ? `&category=${encodeURIComponent(category)}` : '')
      + (adapter ? `&adapter=${encodeURIComponent(adapter)}` : '')),
  hfOverview: (live = false) => req<HfOverview>(`/hf/overview${live ? '?live=1' : ''}`),
  hfMarkets: (qs = '') => req<HfMarkets>(`/hf/markets${qs}`),
  quoteCurve: (category: string, price: number, horizon_days: number) =>
    req<QuoteCurve>(`/quote-curve?category=${encodeURIComponent(category)}&price=${price}&horizon_days=${horizon_days}`),
  // polled endpoints: retries: 0 — the poll loop (with its failure backoff) is the retry
  liveStatus: () => req<LiveStatus>('/live/status', undefined, { retries: 0 }),
  liveDisputes: (limit = 25) => req<LiveDisputes>(`/live/disputes?limit=${limit}`, undefined, { retries: 0 }),
  // testnet fleet + keeper (continuous engine)
  tnFleet: () => req<TnFleet>('/testnet/fleet', undefined, { retries: 0 }),
  tnKeeper: () => req<TnKeeper>('/testnet/keeper', undefined, { retries: 0 }),
  tnKeeperRun: (ticks = 10) => req<{ started: boolean; running: boolean }>('/testnet/keeper/run', { method: 'POST', body: JSON.stringify({ ticks }) }),
  tnKeeperStart: () => req<{ started: boolean; running: boolean }>('/testnet/keeper/start', { method: 'POST', body: '{}' }),
  tnKeeperStop: () => req<{ stopped: boolean; running: boolean }>('/testnet/keeper/stop', { method: 'POST', body: '{}' }),
  tnKill: () => req<TnRisk>('/testnet/kill', { method: 'POST', body: '{}' }),
  tnUnkill: () => req<TnRisk & { removed: boolean }>('/testnet/unkill', { method: 'POST', body: '{}' }),
  tnRisk: () => req<TnRisk>('/testnet/risk', undefined, { retries: 0 }),
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

// self-scheduling poller: never overlaps ticks (skips while one is in flight), and after 2+
// consecutive failures stretches the delay ×4 (capped at 30s) so an unreachable backend — e.g. a
// Render cold start — isn't hammered at full rate. The tick reports success/failure by returning
// a boolean (a thrown error also counts as failure).
export function usePoll(tick: () => Promise<boolean | void>, baseMs: number, startDelayMs = 0): void {
  const tickRef = useRef(tick)
  tickRef.current = tick
  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout>
    let fails = 0
    const run = async () => {
      let ok = true
      try { ok = (await tickRef.current()) !== false } catch { ok = false }
      if (!alive) return
      fails = ok ? 0 : fails + 1
      const delay = fails >= 2 ? Math.min(baseMs * 4, 30000) : baseMs
      timer = setTimeout(run, delay)
    }
    timer = setTimeout(run, startDelayMs)
    return () => { alive = false; clearTimeout(timer) }
  }, [baseMs, startDelayMs])
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

export interface SessionReq { scenario: string; category?: string; entry_price?: number; inventory?: number; dispute_tick?: number; gap_logit?: number; n_ticks?: number; n_markets?: number; seed?: number; source?: string; hazard?: boolean }
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
  market_source?: string
  hazard?: boolean
}

export interface AblationArm { arm: string; arm_label: string; points: { lambda_star: number; pnl_net_of_rewards: number; sharpe: number }[] }
export interface Ablation { source: string; meta: Record<string, number | string>; lambda_star_grid: number[]; arms: AblationArm[]; headline: string; caveat: string; live_error?: string }

export interface HazardCardT { label: string; coef: number[]; intercept: number; offset: number; feature_order: string[]; holdout_auc: number; brier: number; n: number; positives: number; natural_rate: number; discriminates: boolean; trained_at?: string }
export interface Hazard { deployed: HazardCardT | null; matched: HazardCardT | null; matched_eval: HazardCardT | null; caveat: string; null_finding: string }

export interface Disputes { total: number; rows: Record<string, any>[]; columns: string[]; facets: { category: Record<string, number>; adapter: Record<string, number>; year: Record<string, number> } }
export interface Recon { recon: Record<string, any>; by_adapter: Record<string, number>; by_category: Record<string, number>; total_disputes: number; hf_joinable_pct: number; note: string; source?: string; mismatches?: number; live_error?: string }
export interface SigmaPoint { category: string; price: number; sigma: number }
export interface Sigma { points: SigmaPoint[]; categories: string[]; n: number; note: string }

export interface Proposers { rows: { proposer: string; disputes: number }[]; total_proposers: number; note: string }
export interface DisputeAnalytics {
  n: number
  histogram?: { x0: number; x1: number; n: number }[]
  jump_stats?: { mean: number; median: number; sd: number; n: number }
  scatter?: { pre: number; post: number }[]
  by_round?: Record<string, number>
  by_outcome?: Record<string, number>
  category?: string | null
  adapter?: string | null
}
export interface QuoteCurve {
  points: { inventory: number; bid: number; ask: number; mid: number }[]
  mid: number; sigma: number; lambda_jump: number; category: string; horizon_days: number
}

export interface HfOverview {
  resolution: { YES: number; NO: number; tie: number; resolved: number; unresolved: number; total: number }
  markets_by_year: { year: string; n: number }[]
  fills_by_year: { year: string; n: number }[]
  by_category: { category: string; n_markets: number; n_resolved: number }[]
  coverage: { repo: string; total_conditions: number; resolved_conditions: number; total_fills: number; fills_source?: string; market_date_min: string; market_date_max: string; cutoff_block: number }
  built_at?: string; source?: string; note?: string; live_error?: string
}
export interface HfMarketRow { conditionId: string; marketName: string; marketSlug: string; category: string; startDate: string | null; endDate: string | null; resolved: boolean; resolvedOutcome: string | null; volume: number | null; trades: number | null }
export interface HfMarkets { total: number; rows: HfMarketRow[]; categories: string[]; n_cached: number; has_volume?: boolean; built_at?: string; note: string }

export interface LiveStatus { reachable: boolean; endpoint: string; source?: string; latency_ms?: number; head_ts?: number | null; head_id?: string | null; head_age_seconds?: number | null; chain_head_ts?: number | null; error?: string }
export interface LiveDispute { id: string; round: number | null; disputeTs: number; disputer: string | null; proposedOutcome: string | null; proposer: string | null; conditionId: string | null; marketStatus: string | null; finalOutcome: string | null; outcomeSlotCount: number | null; adapter?: string | null; marketName?: string | null; category?: string | null }
export interface LiveDisputes { reachable: boolean; disputes: LiveDispute[]; source?: string; latency_ms?: number; endpoint: string; error?: string; warming?: boolean; built_at?: number | null; scan_error?: string }

// ---- testnet fleet + keeper (the continuous testnet execution engine) ------------------------
// on-chain snapshot fields shared by every fleet-market row (from ChainReader.snapshot)
export interface TnMarket {
  deployed: boolean; bid: number; ask: number; max_trade?: number; quote_ts?: number
  disputed?: boolean; resolved?: boolean; yes_won?: boolean; total_yes?: number
  escrow_usdc?: number; category?: string | null; lambda_jump?: number; sigma?: number
}
export interface TnFleetMarket extends TnMarket {
  address: string; category: string; token_id: string; tracks_cid: string | null
  end_date_ts: number; keeper_managed: boolean; label: string; explorer: string; error?: string
}
export interface TnFleet { reachable: boolean; markets: TnFleetMarket[]; explorer?: string; note?: string; error?: string }
export interface TnRisk {
  day: string; tx_count: number; gas_pol: number; daily_loss_usd: number; gross_exposure: number
  consecutive_errors: number; killed: boolean; halted: boolean; halt_reason: string
  limits: { max_daily_loss_usd: number; portfolio_gross_cap: number; max_tx_per_day: number; max_gas_pol_per_day: number; max_consecutive_errors: number }
}
export interface TnKeeperMarket { cid: string; token_id: string; arm: string; category: string; inventory: number; cash: number; mark_mid: number | null; equity_mark: number; pnl: number; n_exits: number }
export interface TnKeeper {
  running: boolean; ticks_done: number; last_tick_ts: number; interval_s: number
  out_path: string | null; last_error: string; n_markets: number
  autostart?: boolean; engine_ready?: boolean
  risk?: TnRisk; markets?: TnKeeperMarket[]
  engine?: { address: string; pol?: number; usdc?: number; error?: string }
  clob?: { tx_count: number; last_tx?: { kind: string; market: string; tx: string } | null; last_denied?: string }
  detector?: { confirmations: number; cached_disputes: number; watched: number; error: string }
}
