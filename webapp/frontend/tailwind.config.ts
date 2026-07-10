import type { Config } from 'tailwindcss'

// Quant-terminal theme, now dual dark/light. Colors resolve through CSS variables (RGB channels
// defined in src/index.css: :root = dark, [data-theme="light"] = light) so every token — and its
// /opacity modifiers (bg-sig/10, border-line/60) — swaps by theme. Series hues are the dataviz
// skill's validated categorical set (dark + light columns, validated per surface). Text always
// wears ink tokens, never a series hue.
const v = (name: string) => `rgb(var(--${name}) / <alpha-value>)`
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: ['selector', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: v('bg'),             // page plane
        surface: v('surface'),   // panel / chart surface
        elevated: v('elevated'), // raised controls / hover
        line: v('line'),         // hairline gridlines / borders
        axis: v('axis'),         // baseline / axis
        ink: v('ink'),           // primary text
        'ink-2': v('ink-2'),     // secondary text
        muted: v('muted'),       // axis labels / faint
        // λ brand accent
        sig: { DEFAULT: v('sig'), dim: v('sig-dim'), glow: v('sig-glow') },
        // fixed categorical order (charts with ≥2 series) — never cycled
        s1: v('s1'), s2: v('s2'), s3: v('s3'), s4: v('s4'),
        s5: v('s5'), s6: v('s6'), s7: v('s7'), s8: v('s8'),
        // status (reserved; always paired with icon/label)
        good: v('good'), warn: v('warn'), serious: v('serious'), crit: v('crit'),
        loss: v('loss'), profit: v('profit'),
        // modal backdrop scrim base (used as scrim/60); channels swap per theme
        scrim: v('scrim'),
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      boxShadow: {
        // theme-tuned via CSS vars (dark = heavy black drop; light = soft neutral elevation)
        panel: 'var(--shadow-panel)',
        glow: 'var(--shadow-glow)',
        'glow-soft': 'var(--shadow-glow-soft)',
      },
      keyframes: {
        'fade-up': { '0%': { opacity: '0', transform: 'translateY(6px)' }, '100%': { opacity: '1', transform: 'translateY(0)' } },
        pulse2: { '0%,100%': { opacity: '1' }, '50%': { opacity: '0.35' } },
        // premium skeleton: a light band sweeping across the placeholder
        shimmer: { '0%': { backgroundPosition: '200% 0' }, '100%': { backgroundPosition: '-200% 0' } },
        // slow ambient drift for the hero aurora layer
        'aurora-drift': {
          '0%,100%': { transform: 'translate3d(0,0,0) rotate(0deg)' },
          '33%': { transform: 'translate3d(3%,-2%,0) rotate(4deg)' },
          '66%': { transform: 'translate3d(-2%,2%,0) rotate(-3deg)' },
        },
        // brief tint sweep when a live value ticks up / down (theme-aware)
        'flash-up': { '0%': { backgroundColor: 'rgb(var(--profit) / 0.16)' }, '100%': { backgroundColor: 'transparent' } },
        'flash-down': { '0%': { backgroundColor: 'rgb(var(--loss) / 0.16)' }, '100%': { backgroundColor: 'transparent' } },
        breathe: { '0%,100%': { opacity: '1' }, '50%': { opacity: '0.72' } },
      },
      animation: {
        'fade-up': 'fade-up 0.4s ease-out both',
        pulse2: 'pulse2 1.6s ease-in-out infinite',
        shimmer: 'shimmer 1.6s linear infinite',
        'aurora-drift': 'aurora-drift 24s ease-in-out infinite',
        'flash-up': 'flash-up 0.9s ease-out',
        'flash-down': 'flash-down 0.9s ease-out',
        breathe: 'breathe 3.2s ease-in-out infinite',
      },
    },
  },
  plugins: [],
} satisfies Config
