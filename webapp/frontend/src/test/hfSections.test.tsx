import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { HfDataset } from '../sections/HfDataset'
import { HfMarkets } from '../sections/HfMarkets'
import { ThemeProvider } from '../components/Theme'

// These sections are new and chart-heavy (recharts Pie/Bar). Compile-time checks can't catch a runtime
// render throw (undefined field access, a chart choking on empty data), so mount them for real against
// mocked API payloads shaped exactly like the backend returns.
const OVERVIEW = {
  resolution: { YES: 398356, NO: 580992, tie: 13137, resolved: 992485, unresolved: 124667, total: 1117152 },
  markets_by_year: [{ year: '2025', n: 500000 }, { year: '2026', n: 400000 }],
  fills_by_year: [{ year: '2025', n: 241199667 }, { year: '2026', n: 873548669 }],
  by_category: [{ category: 'other', n_markets: 625720, n_resolved: 563325 },
                { category: 'crypto', n_markets: 170677, n_resolved: 150000 }],
  coverage: {
    repo: 'moose-code/polymarket-onchain-v1', total_conditions: 1117152, resolved_conditions: 992485,
    total_fills: 1172658611, fills_source: 'computed', market_date_min: '2020-01-01',
    market_date_max: '2026-04-24', cutoff_block: 85948287,
  },
  built_at: '2026-07-16', source: 'cache', note: 'computed from HF.',
}
const MARKETS = {
  total: 2, categories: ['other', 'crypto'], n_cached: 1000, has_volume: true, built_at: '2026-07-16',
  note: 'top by volume', rows: [
    { conditionId: '0xabc123', marketName: 'Will Donald Trump win the 2024 US Presidential Election',
      marketSlug: 'trump-2024', category: 'politics', startDate: '2024-01-01', endDate: '2024-11-05',
      resolved: true, resolvedOutcome: 'YES', volume: 1636188222, trades: 5109619 },
    { conditionId: '0xdef456', marketName: 'A market with no volume yet', marketSlug: 'no-vol',
      category: 'other', startDate: '2026-07-01', endDate: null, resolved: false,
      resolvedOutcome: null, volume: null, trades: null },
  ],
}

const mockFetch = (payload: unknown) =>
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve(payload) })))

const wrap = (ui: React.ReactNode) => render(<ThemeProvider>{ui}</ThemeProvider>)

describe('HF sections render', () => {
  beforeEach(() => vi.unstubAllGlobals())

  it('HfDataset renders the resolution mix, real fill counts and provenance', async () => {
    mockFetch(OVERVIEW)
    wrap(<HfDataset />)
    // headline coverage tiles + the computed fills-by-year story
    expect(await screen.findByText(/HF dataset/i)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText(/resolution outcomes/i)).toBeInTheDocument())
    expect(screen.getByText(/CLOB fill tape/i)).toBeInTheDocument()
    // provenance: computed (not the documented-constant fallback) + build date
    expect(screen.getByText(/counted from the tape/i)).toBeInTheDocument()
    expect(screen.getByText(/2026-07-16/)).toBeInTheDocument()
    // 2026 is ~74% of the tape — the share callout is computed, not hardcoded
    expect(screen.getByText(/2026 is 74% of the whole tape/i)).toBeInTheDocument()
  })

  it('HfDataset survives a payload with no fills_by_year (tokenless build)', async () => {
    mockFetch({ ...OVERVIEW, fills_by_year: [], coverage: { ...OVERVIEW.coverage, fills_source: 'published' } })
    wrap(<HfDataset />)
    await waitFor(() => expect(screen.getByText(/resolution outcomes/i)).toBeInTheDocument())
    // falls back to the markets-by-year framing rather than throwing
    expect(screen.getByText(/fill-tape counts need an HF token/i)).toBeInTheDocument()
  })

  it('HfMarkets renders volume/trades columns and compact values', async () => {
    mockFetch(MARKETS)
    wrap(<HfMarkets />)
    expect(await screen.findByText(/Will Donald Trump win the 2024 US Presidential Election/)).toBeInTheDocument()
    // compact money/counts, not raw 1636188222
    expect(screen.getByText('$1.6B')).toBeInTheDocument()
    expect(screen.getByText('5M')).toBeInTheDocument()
    // a volume-less row degrades to an em-dash instead of NaN/undefined
    expect(screen.getByText(/A market with no volume yet/)).toBeInTheDocument()
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
    // resolution surfaced
    expect(screen.getByText('YES')).toBeInTheDocument()
    expect(screen.getByText('open')).toBeInTheDocument()
  })
})
