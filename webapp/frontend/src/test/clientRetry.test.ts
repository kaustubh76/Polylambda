import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, req } from '../api/client'

// The retry policy exists for host cold starts (gateway 502/503/504 before uvicorn binds):
// one-shot GETs heal through the window, POSTs (engine-signed txns) and 4xx never retry.

const ok = (body: unknown) => ({ ok: true, status: 200, json: () => Promise.resolve(body) })
const bad = (status: number) => ({ ok: false, status, statusText: 'err', json: () => Promise.resolve({}) })

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.useFakeTimers()
  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('req retry policy', () => {
  it('retries a GET through 502s and resolves on success', async () => {
    fetchMock
      .mockResolvedValueOnce(bad(502))
      .mockResolvedValueOnce(bad(503))
      .mockResolvedValueOnce(ok({ fine: true }))
    const p = req<{ fine: boolean }>('/overview')
    await vi.advanceTimersByTimeAsync(10_000) // covers 1s + 2s backoff (+ jitter)
    await expect(p).resolves.toEqual({ fine: true })
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('retries a GET through network errors', async () => {
    fetchMock
      .mockRejectedValueOnce(new Error('connection refused'))
      .mockResolvedValueOnce(ok({ up: 1 }))
    const p = req('/baserates')
    await vi.advanceTimersByTimeAsync(5_000)
    await expect(p).resolves.toEqual({ up: 1 })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('gives up after exhausting retries', async () => {
    fetchMock.mockResolvedValue(bad(502))
    const p = req('/overview')
    p.catch(() => {}) // avoid unhandled-rejection noise while timers advance
    await vi.advanceTimersByTimeAsync(30_000)
    await expect(p).rejects.toThrow('502')
    expect(fetchMock).toHaveBeenCalledTimes(4) // initial + 3 retries
  })

  it('never retries a POST (engine txns are not idempotent)', async () => {
    fetchMock.mockResolvedValue(bad(502))
    await expect(req('/testnet/engine-quote', { method: 'POST', body: '{}' })).rejects.toThrow('502')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('does not retry 4xx', async () => {
    fetchMock.mockResolvedValue(bad(404))
    await expect(req('/nope')).rejects.toThrow('404')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('honors retries: 0 for polled endpoints', async () => {
    fetchMock.mockResolvedValue(bad(502))
    await expect(req('/live/status', undefined, { retries: 0 })).rejects.toThrow('502')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

describe('disputeAnalytics scoping', () => {
  // the anatomy graphs re-fetch scoped to the explorer's category/adapter filter — the query string
  // must carry them so the backend returns the subset (complaint #5: the graphs must respond to input)
  it('builds an unscoped URL when no filter is set', async () => {
    fetchMock.mockResolvedValue(ok({ n: 0 }))
    await api.disputeAnalytics()
    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toContain('/disputes/analytics?bins=24')
    expect(url).not.toContain('category=')
    expect(url).not.toContain('adapter=')
  })

  it('appends category and adapter (URI-encoded) when scoped', async () => {
    fetchMock.mockResolvedValue(ok({ n: 1 }))
    await api.disputeAnalytics(24, 'politics', 'v2')
    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toContain('category=politics')
    expect(url).toContain('adapter=v2')
  })
})
