// tiny client-side CSV/JSON export helpers — no deps, browser-only

function csvCell(v: unknown): string {
  if (v == null) return ''
  const s = String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

// rows → CSV string. columns defaults to the union of keys in row order of the first row.
export function toCsv(rows: Record<string, unknown>[], columns?: string[]): string {
  if (!rows.length) return ''
  const cols = columns ?? Object.keys(rows[0])
  const head = cols.join(',')
  const body = rows.map((r) => cols.map((c) => csvCell(r[c])).join(',')).join('\n')
  return `${head}\n${body}`
}

export function download(filename: string, content: string, mime = 'text/plain') {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

export const downloadCsv = (filename: string, rows: Record<string, unknown>[], columns?: string[]) =>
  download(filename, toCsv(rows, columns), 'text/csv')

export const downloadJson = (filename: string, data: unknown) =>
  download(filename, JSON.stringify(data, null, 2), 'application/json')
