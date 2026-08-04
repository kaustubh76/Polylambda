import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Ablation } from '../sections/Ablation'
import { ThemeProvider } from '../components/Theme'

// The Edge-proof section now LEADS with the clean-USD backtest (api.backtest) and DEMOTES the
// counterfactual sweep (api.ablation). It fires both fetches, so route the mock by URL. Mount for
// real — a compile check can't catch a runtime throw on an undefined field or an empty chart.

const BACKTEST = {
  available: true, run_date: '2026-08-04',
  meta: { n_disputes_with_paths: 1200, n_controls_with_paths: 3600, lambda_star_frozen: 0.002 },
  lambda_star_grid: [0.002, 0.01],
  arms: [
    { arm: 'lambda_jump', arm_label: 'λ-jump', points: [
      { lambda_star: 0.002, pnl_usd: -700, sharpe_cross: -0.2, sharpe_daily_ann: -0.8, max_drawdown_usd: 720, win_rate: 0.25, n_fills: 40, n_exits: 6 },
      { lambda_star: 0.01, pnl_usd: -820, sharpe_cross: -0.25, sharpe_daily_ann: -0.9, max_drawdown_usd: 840, win_rate: 0.22, n_fills: 40, n_exits: 3 }] },
    { arm: 'diffusion_only', arm_label: 'diffusion', points: [
      { lambda_star: 0.002, pnl_usd: -900, sharpe_cross: -0.3, sharpe_daily_ann: -1, max_drawdown_usd: 950, win_rate: 0.2, n_fills: 40, n_exits: 0 },
      { lambda_star: 0.01, pnl_usd: -900, sharpe_cross: -0.3, sharpe_daily_ann: -1, max_drawdown_usd: 950, win_rate: 0.2, n_fills: 40, n_exits: 0 }] },
  ],
  headline: {
    lambda_star_frozen: 0.002,
    at_frozen: [
      { arm: 'lambda_jump', arm_label: 'λ-jump', lambda_star: 0.002, pnl_usd: -700, sharpe_cross: -0.2, sharpe_daily_ann: -0.8, max_drawdown_usd: 720, win_rate: 0.25, n_fills: 40, n_exits: 6 },
      { arm: 'diffusion_only', arm_label: 'diffusion', lambda_star: 0.002, pnl_usd: -900, sharpe_cross: -0.3, sharpe_daily_ann: -1, max_drawdown_usd: 950, win_rate: 0.2, n_fills: 40, n_exits: 0 },
    ],
    delta_jump_minus_diffusion: { pnl_usd: 200, ci_low: 60, ci_high: 340, n_bootstrap: 1000 },
    best_arm: 'lambda_jump',
  },
  caveat: 'Clean-USD P&L: cash + inventory·mark, NO reward income folded in.',
}

const ABLATION = {
  source: 'replay',
  meta: { n_disputes: 1409, n_controls: 741, span: '2022-2026', lambda_star_frozen: 0.002, run_date: '2026-07-11' },
  lambda_star_grid: [0.002, 0.01],
  arms: [
    { arm: 'lambda_jump', arm_label: 'λ-jump', points: [{ lambda_star: 0.002, pnl_net_of_rewards: 22708, sharpe: 0.28 }, { lambda_star: 0.01, pnl_net_of_rewards: 21000, sharpe: 0.27 }] },
    { arm: 'diffusion_only', arm_label: 'diffusion', points: [{ lambda_star: 0.002, pnl_net_of_rewards: 20746, sharpe: 0.26 }, { lambda_star: 0.01, pnl_net_of_rewards: 20746, sharpe: 0.26 }] },
  ],
  headline: 'Reward-aware surgical exit is the edge.', caveat: 'Powered historical counterfactual.',
}

const routeFetch = (routes: Record<string, unknown>) =>
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const key = Object.keys(routes).find((k) => String(url).includes(k))
    return Promise.resolve({ ok: true, json: () => Promise.resolve(key ? routes[key] : {}) })
  }))

const wrap = (ui: React.ReactNode) => render(<ThemeProvider>{ui}</ThemeProvider>)

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Ablation edge-proof section', () => {
  it('leads with the clean-USD backtest Δ edge + CI and honest per-arm P&L', async () => {
    routeFetch({ '/backtest': BACKTEST, '/ablation': ABLATION })
    wrap(<Ablation />)
    // the edge number (Δ jump − diffusion = +$200) — appears in the hero and the caption
    await waitFor(() => expect(screen.getAllByText(/\+\$200/).length).toBeGreaterThan(0))
    expect(screen.getAllByText(/always-hold · the edge/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/95% CI/).length).toBeGreaterThan(0)
    // absolute P&L is shown honestly, negatives and all (λ-jump = −$700, diffusion = −$900)
    expect(screen.getAllByText(/−\$700/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/−\$900/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/2026-08-04/).length).toBeGreaterThan(0)  // run-date provenance
  })

  it('demotes the counterfactual sweep and labels it as not a fillable P&L', async () => {
    routeFetch({ '/backtest': BACKTEST, '/ablation': ABLATION })
    wrap(<Ablation />)
    await waitFor(() => expect(screen.getByText(/Exit-policy sensitivity/)).toBeInTheDocument())
    expect(screen.getByText(/not a fillable P&L/)).toBeInTheDocument()
  })

  it('degrades gracefully when the backtest artifact is absent', async () => {
    routeFetch({ '/backtest': { available: false, note: 'no committed backtest artifact yet — run forwardtest.replay_full' }, '/ablation': ABLATION })
    wrap(<Ablation />)
    await waitFor(() => expect(screen.getByText(/no committed backtest artifact/)).toBeInTheDocument())
    // the counterfactual still renders beneath
    expect(screen.getByText(/Exit-policy sensitivity/)).toBeInTheDocument()
  })
})
