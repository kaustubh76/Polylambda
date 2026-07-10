import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { readQueryParam, writeQuery, usePersistentState } from '../urlState'

beforeEach(() => {
  history.replaceState(null, '', '/')
  localStorage.clear()
})

describe('query helpers', () => {
  it('reads a param from the URL', () => {
    history.replaceState(null, '', '/?q=hello&cat=politics')
    expect(readQueryParam('q')).toBe('hello')
    expect(readQueryParam('cat')).toBe('politics')
    expect(readQueryParam('missing')).toBeUndefined()
  })
  it('writes/merges params and removes empty ones', () => {
    writeQuery({ q: 'x', cat: 'crypto' })
    expect(location.search).toContain('q=x')
    expect(location.search).toContain('cat=crypto')
    writeQuery({ q: undefined })          // remove q, keep cat
    expect(readQueryParam('q')).toBeUndefined()
    expect(readQueryParam('cat')).toBe('crypto')
  })
})

describe('usePersistentState', () => {
  it('hydrates from initial and persists to localStorage', () => {
    const { result } = renderHook(() => usePersistentState('pl:test', { n: 1 }))
    expect(result.current[0]).toEqual({ n: 1 })
    act(() => result.current[1]({ n: 2 }))
    expect(result.current[0]).toEqual({ n: 2 })
    expect(JSON.parse(localStorage.getItem('pl:test')!)).toEqual({ n: 2 })
  })
  it('rehydrates a previously stored value', () => {
    localStorage.setItem('pl:test2', JSON.stringify(['a', 'b']))
    const { result } = renderHook(() => usePersistentState<string[]>('pl:test2', []))
    expect(result.current[0]).toEqual(['a', 'b'])
  })
})
