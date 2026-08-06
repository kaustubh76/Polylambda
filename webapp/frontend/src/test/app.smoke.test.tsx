import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { THESIS_TITLE } from '../lib/canon'

// The app is gated on /api/health (HealthGate defers all data fetches until the backend is up).
// Resolve /api/health so the gate opens; reject every other endpoint — each consumer
// (useApi/useAction/pollers) degrades to skeleton/error, so a rejecting data network is the cleanest
// way to assert "the shell + providers mount without throwing" once past the gate.
beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string) =>
    String(url).includes('/health')
      ? Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) } as Response)
      : Promise.reject(new Error('offline'))))
})

describe('<App/> smoke', () => {
  it('opens the health gate then mounts the shell (providers, header, eager sections)', async () => {
    render(<App />)
    // the health gate shows a waking splash on the initial render (sync), then mounts AppInner
    // once /api/health 200s (async microtask)
    expect(screen.getByText(/Waking the server/i)).toBeInTheDocument()
    expect(await screen.findByText('PolyLambda')).toBeInTheDocument()
    // the section nav renders every entry
    expect(await screen.findByRole('link', { name: 'Disputes' })).toBeInTheDocument()
    // an eager, above-the-fold section header is present (the Hero). The title is IMPORTED from
    // the canon module rather than typed as a literal — a hard-coded copy assertion here is what
    // let a stale thesis ride green CI for months. tests/test_docs_canon.py owns whether the copy
    // is correct; this test only owns whether it mounts.
    expect(await screen.findByRole('heading', { name: THESIS_TITLE })).toBeInTheDocument()
  })
})
