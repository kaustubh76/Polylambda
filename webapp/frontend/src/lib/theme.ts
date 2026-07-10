// Color values for Recharts / inline SVG (which need JS strings, not Tailwind classes). This is
// the JS-side mirror of the CSS variables in index.css — kept per-theme so charts recolor on
// toggle. Consume via useColors() (components/Theme.tsx), never the raw objects, so the values
// track the active theme. Series hues are the dataviz skill's validated categorical set (dark +
// light columns, each validated against its own surface).
export type ThemeName = 'dark' | 'light'

export interface Colors {
  bg: string; surface: string; elevated: string; line: string; axis: string
  ink: string; ink2: string; muted: string
  sig: string; sigDim: string
  series: string[]
  good: string; warn: string; serious: string; crit: string; loss: string; profit: string
}

const DARK = {
  C: {
    bg: '#0a0b0e', surface: '#14161c', elevated: '#1a1d26', line: '#23262f', axis: '#2f3340',
    ink: '#f2f4f8', ink2: '#aab0bd', muted: '#6b7280',
    sig: '#24c98a', sigDim: '#199e70',
    series: ['#199e70', '#3987e5', '#e66767', '#c98500', '#9085e9', '#d95926', '#d55181', '#008300'],
    good: '#0ca30c', warn: '#fab219', serious: '#ec835a', crit: '#d03b3b', loss: '#e66767', profit: '#22c58a',
  } as Colors,
  CATEGORY_COLORS: {
    politics: '#3987e5', crypto: '#c98500', sports: '#199e70', 'tech-ai': '#9085e9',
    geopolitics: '#d55181', entertainment: '#d95926', economics: '#e66767', other: '#6b7280',
  } as Record<string, string>,
  ARM_COLORS: {
    lambda_jump: '#199e70', diffusion_only: '#3987e5', lambda_select: '#e66767',
    lambda_on: '#24c98a', lambda_off: '#e66767',
  } as Record<string, string>,
}

const LIGHT = {
  C: {
    bg: '#f6f7f9', surface: '#ffffff', elevated: '#eef1f5', line: '#e3e6ec', axis: '#cbd0d8',
    ink: '#12151b', ink2: '#454b57', muted: '#5f6673',
    sig: '#097a55', sigDim: '#066b47',
    series: ['#1baf7a', '#2a78d6', '#e34948', '#eda100', '#4a3aa7', '#eb6834', '#e87ba4', '#008300'],
    good: '#067a2f', warn: '#9a6a00', serious: '#b4471f', crit: '#c72d24', loss: '#c92f2b', profit: '#067a43',
  } as Colors,
  CATEGORY_COLORS: {
    politics: '#2a78d6', crypto: '#eda100', sports: '#1baf7a', 'tech-ai': '#4a3aa7',
    geopolitics: '#e87ba4', entertainment: '#eb6834', economics: '#e34948', other: '#5f6673',
  } as Record<string, string>,
  ARM_COLORS: {
    lambda_jump: '#1baf7a', diffusion_only: '#2a78d6', lambda_select: '#e34948',
    lambda_on: '#097a55', lambda_off: '#e34948',
  } as Record<string, string>,
}

export interface ThemeColors {
  C: Colors
  CATEGORY_COLORS: Record<string, string>
  ARM_COLORS: Record<string, string>
  // shared Recharts chrome, derived from the active palette
  AXIS: { stroke: string; tick: { fill: string; fontSize: number }; tickLine: boolean }
  GRID: { stroke: string; strokeDasharray: string }
}

export function getColors(theme: ThemeName): ThemeColors {
  const p = theme === 'light' ? LIGHT : DARK
  return {
    ...p,
    AXIS: { stroke: p.C.axis, tick: { fill: p.C.muted, fontSize: 11 }, tickLine: false },
    GRID: { stroke: p.C.line, strokeDasharray: '0' },
  }
}
