import { useEffect, useState } from 'react'

// Returns `value` delayed by `ms`. Generalizes the inline setTimeout pattern used in
// ScoreMarket — feed a fast-changing input (e.g. a search box) and read the debounced output.
export function useDebounced<T>(value: T, ms = 250): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return debounced
}
