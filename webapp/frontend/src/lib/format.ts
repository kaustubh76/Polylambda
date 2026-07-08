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
