import { describe, expect, it } from 'vitest'
import { ago, int, num, pct, short, signed, usd } from '../format'

describe('format helpers', () => {
  it('pct', () => {
    expect(pct(0.1234)).toBe('12.34%')
    expect(pct(0.1, 1)).toBe('10.0%')
    expect(pct(null)).toBe('—')
  })
  it('num / int', () => {
    expect(num(1234.5, 1)).toBe('1,234.5')
    expect(int(1234.6)).toBe('1,235')
    expect(num(undefined)).toBe('—')
  })
  it('usd handles sign with a unicode minus', () => {
    expect(usd(12.5)).toBe('$12.50')
    expect(usd(-3)).toBe('−$3.00')
    expect(usd(null)).toBe('—')
  })
  it('signed', () => {
    expect(signed(2)).toBe('+2.00')
    expect(signed(-2)).toBe('−2.00')
  })
  it('short truncates the middle of long strings', () => {
    expect(short('0x1234567890abcdef', 6, 4)).toBe('0x1234…cdef')
    expect(short('0xshort')).toBe('0xshort')
    expect(short(null)).toBe('—')
  })
  it('ago', () => {
    const now = 1_000_000_000_000
    expect(ago(now / 1000 - 30, now)).toBe('30s ago')
    expect(ago(now / 1000 - 120, now)).toBe('2m ago')
    expect(ago(now / 1000 - 7200, now)).toBe('2h ago')
    expect(ago(null)).toBe('—')
  })
})
