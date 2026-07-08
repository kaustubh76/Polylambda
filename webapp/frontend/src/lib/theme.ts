// Color tokens for Recharts (which needs JS values, not Tailwind classes).
// Mirror of tailwind.config.ts — keep in sync. Series hues are the dataviz validated set.
export const C = {
  bg: '#0a0b0e',
  surface: '#14161c',
  elevated: '#1a1d26',
  line: '#23262f',
  axis: '#2f3340',
  ink: '#f2f4f8',
  ink2: '#aab0bd',
  muted: '#6b7280',
  sig: '#24c98a',
  sigDim: '#199e70',
  // fixed categorical order — never cycle
  series: ['#199e70', '#3987e5', '#e66767', '#c98500', '#9085e9', '#d95926', '#d55181', '#008300'],
  good: '#0ca30c',
  warn: '#fab219',
  serious: '#ec835a',
  crit: '#d03b3b',
  loss: '#e66767',
  profit: '#22c58a',
} as const

// stable category → hue map (color follows the entity, not its rank).
export const CATEGORY_COLORS: Record<string, string> = {
  politics: '#3987e5',
  crypto: '#c98500',
  sports: '#199e70',
  'tech-ai': '#9085e9',
  geopolitics: '#d55181',
  entertainment: '#d95926',
  economics: '#e66767',
  other: '#6b7280',
}

export const ARM_COLORS: Record<string, string> = {
  lambda_jump: '#199e70',
  diffusion_only: '#3987e5',
  lambda_select: '#e66767',
  lambda_on: '#24c98a',
  lambda_off: '#e66767',
}

// shared Recharts chrome
export const AXIS = { stroke: C.axis, tick: { fill: C.muted, fontSize: 11 }, tickLine: false }
export const GRID = { stroke: C.line, strokeDasharray: '0' }
