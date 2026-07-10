import { describe, expect, it, vi } from 'vitest'
import { toCsv, downloadJson, download } from '../export'

describe('toCsv', () => {
  it('renders header + rows in column order', () => {
    expect(toCsv([{ a: '1', b: '2' }], ['a', 'b'])).toBe('a,b\n1,2')
  })
  it('quotes cells containing commas, quotes, or newlines', () => {
    const csv = toCsv([{ a: 'x', b: 'y,z' }, { a: 'q"q', b: 'line\nbreak' }], ['a', 'b'])
    expect(csv).toBe('a,b\nx,"y,z"\n"q""q","line\nbreak"')
  })
  it('defaults columns to the first row keys and blanks null/undefined', () => {
    expect(toCsv([{ a: 1, b: null }] as any)).toBe('a,b\n1,')
  })
  it('returns empty string for no rows', () => {
    expect(toCsv([])).toBe('')
  })
})

describe('download helpers', () => {
  it('creates and clicks an anchor with a blob url', () => {
    const click = vi.fn()
    const orig = document.createElement.bind(document)
    const spy = vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      const el = orig(tag) as HTMLAnchorElement
      if (tag === 'a') el.click = click
      return el
    })
    ;(URL as any).createObjectURL = vi.fn(() => 'blob:x')
    ;(URL as any).revokeObjectURL = vi.fn()
    download('f.txt', 'hi')
    expect(click).toHaveBeenCalledOnce()
    expect((URL as any).createObjectURL).toHaveBeenCalledOnce()
    spy.mockRestore()
  })
  it('downloadJson serializes pretty JSON', () => {
    const createEl = vi.spyOn(document, 'createElement')
    ;(URL as any).createObjectURL = vi.fn(() => 'blob:x')
    ;(URL as any).revokeObjectURL = vi.fn()
    let captured: BlobPart[] = []
    const OrigBlob = global.Blob
    ;(global as any).Blob = class extends OrigBlob {
      constructor(parts: BlobPart[], opts?: BlobPropertyBag) { super(parts, opts); captured = parts }
    }
    downloadJson('f.json', { a: 1 })
    expect(String(captured[0])).toContain('"a": 1')
    ;(global as any).Blob = OrigBlob
    createEl.mockRestore()
  })
})
