import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'

// the app fires a lot of fetches on mount (overview, live status, fleet/keeper polls). Reject them all —
// every consumer (useApi/useAction/pollers) is written to degrade to skeleton/error, so a rejecting
// network is the cleanest way to assert "the shell + providers mount without throwing".
beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('offline'))))
})

describe('<App/> smoke', () => {
  it('mounts the shell (providers, header, eager sections) without throwing', () => {
    render(<App />)
    expect(screen.getByText('PolyLambda')).toBeInTheDocument()
    // the section nav renders every entry
    expect(screen.getByRole('link', { name: 'Disputes' })).toBeInTheDocument()
    // an eager, above-the-fold section header is present (the Hero)
    expect(screen.getByText(/Treat disputes as jumps/i)).toBeInTheDocument()
  })
})
