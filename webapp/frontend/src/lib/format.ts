export const pct = (x: number | null | undefined, dp = 2): string =>
  x == null ? '—' : `${(x * 100).toFixed(dp)}%`

export const pct1 = (x: number | null | undefined): string => pct(x, 1)

export const num = (x: number | null | undefined, dp = 2): string =>
  x == null ? '—' : x.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp })

export const int = (x: number | null | undefined): string =>
  x == null ? '—' : Math.round(x).toLocaleString('en-US')

export const usd = (x: number | null | undefined, dp = 2): string =>
  x == null ? '—' : `${x < 0 ? '−' : ''}$${Math.abs(x).toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp })}`

export const signed = (x: number | null | undefined, dp = 2): string =>
  x == null ? '—' : `${x >= 0 ? '+' : '−'}${Math.abs(x).toFixed(dp)}`

export const fixed = (x: number | null | undefined, dp = 4): string =>
  x == null ? '—' : x.toFixed(dp)

// compact addresses / condition ids
export const short = (s: string | null | undefined, head = 6, tail = 4): string =>
  !s ? '—' : s.length <= head + tail + 2 ? s : `${s.slice(0, head)}…${s.slice(-tail)}`

// relative time from a unix-seconds timestamp ("12s ago", "4m ago", "3h ago", "2d ago")
export const ago = (tsSec: number | null | undefined, nowMs = Date.now()): string => {
  if (tsSec == null) return '—'
  const s = Math.max(0, Math.floor(nowMs / 1000 - tsSec))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
