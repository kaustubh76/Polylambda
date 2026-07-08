import type { Config } from 'tailwindcss'

// Quant-terminal dark theme. Series hues are the dataviz skill's validated dark categorical set
// (validated against surface #14161c: all pass, worst adjacent CVD ΔE 13.4). Status hues are the
// skill's fixed status palette. Text always wears ink tokens, never a series hue.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0a0b0e',           // page plane
        surface: '#14161c',      // panel / chart surface (the validation surface)
        elevated: '#1a1d26',     // raised controls / hover
        line: '#23262f',         // hairline gridlines / borders
        axis: '#2f3340',         // baseline / axis
        ink: '#f2f4f8',          // primary text
        'ink-2': '#aab0bd',      // secondary text
        muted: '#6b7280',        // axis labels / faint
        // λ brand accent (single-series & UI; a brighter tint of the aqua series hue)
        sig: { DEFAULT: '#24c98a', dim: '#199e70', glow: '#3ee0a0' },
        // fixed categorical order (charts with ≥2 series) — never cycled
        s1: '#199e70', s2: '#3987e5', s3: '#e66767', s4: '#c98500',
        s5: '#9085e9', s6: '#d95926', s7: '#d55181', s8: '#008300',
        // status (reserved; always paired with icon/label)
        good: '#0ca30c', warn: '#fab219', serious: '#ec835a', crit: '#d03b3b',
        loss: '#e66767', profit: '#22c58a',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      boxShadow: {
        panel: '0 1px 0 rgba(255,255,255,0.03) inset, 0 8px 30px rgba(0,0,0,0.35)',
        glow: '0 0 0 1px rgba(36,201,138,0.25), 0 0 24px rgba(36,201,138,0.12)',
      },
      keyframes: {
        'fade-up': { '0%': { opacity: '0', transform: 'translateY(6px)' }, '100%': { opacity: '1', transform: 'translateY(0)' } },
        pulse2: { '0%,100%': { opacity: '1' }, '50%': { opacity: '0.35' } },
      },
      animation: {
        'fade-up': 'fade-up 0.4s ease-out both',
        pulse2: 'pulse2 1.6s ease-in-out infinite',
      },
    },
  },
  plugins: [],
} satisfies Config
