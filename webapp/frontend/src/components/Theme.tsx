import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { getColors, type ThemeColors, type ThemeName } from '../lib/theme'

// Single source of truth for light/dark. The inline <head> script in index.html applies the theme
// pre-paint (no FOUC); this provider owns the state afterwards, mirrors it to
// documentElement[data-theme]/colorScheme + localStorage, and hands charts a theme-aware palette.
const STORAGE_KEY = 'pl:theme'

interface ThemeCtx { theme: ThemeName; setTheme: (t: ThemeName) => void; toggle: () => void }
const Ctx = createContext<ThemeCtx | null>(null)

function initialTheme(): ThemeName {
  if (typeof document !== 'undefined') {
    const attr = document.documentElement.dataset.theme // set by the pre-paint script
    if (attr === 'light' || attr === 'dark') return attr
  }
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'light' || saved === 'dark') return saved
  } catch { /* ignore */ }
  if (typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: light)').matches) return 'light'
  return 'dark'
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeName>(initialTheme)

  useEffect(() => {
    const el = document.documentElement
    el.dataset.theme = theme
    el.style.colorScheme = theme
    try { localStorage.setItem(STORAGE_KEY, theme) } catch { /* ignore */ }
  }, [theme])

  const setTheme = useCallback((t: ThemeName) => setThemeState(t), [])
  const toggle = useCallback(() => setThemeState((t) => (t === 'dark' ? 'light' : 'dark')), [])
  const value = useMemo(() => ({ theme, setTheme, toggle }), [theme, setTheme, toggle])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useTheme(): ThemeCtx {
  const c = useContext(Ctx)
  if (!c) throw new Error('useTheme must be used within <ThemeProvider>')
  return c
}

// the palette for the active theme — charts/inline-SVG read colors from here so they recolor on
// toggle. Memoized per theme.
export function useColors(): ThemeColors {
  const { theme } = useTheme()
  return useMemo(() => getColors(theme), [theme])
}
