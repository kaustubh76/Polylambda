// Shared motion layer for the whole app. framer-motion is used surgically; the reveal /
// stagger / count-up primitives here are wired into the Section + Async choke points so all
// 11 sections inherit fluid entrance + load transitions with near-zero per-section edits.
//
// CRITICAL: the global prefers-reduced-motion rule in index.css only neutralizes CSS
// animations — it does NOT stop framer-motion (rAF-driven inline styles). Every effect here
// consults useReducedMotion() (via useMotionReady) so reduced-motion collapses to instant.
import { createElement, useEffect, useRef, useState, type ReactNode } from 'react'
import {
  animate, m, useInView, useReducedMotion, type Variants,
} from 'framer-motion'

// true when transform-based motion should apply; false under prefers-reduced-motion.
export function useMotionReady() {
  return !useReducedMotion()
}

// ---- Variants -------------------------------------------------------------------------------
export const staggerContainer: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.06, delayChildren: 0.04 } },
}

export function reveal(distance = 12): Variants {
  return {
    hidden: { opacity: 0, y: distance },
    show: { opacity: 1, y: 0, transition: { duration: 0.42, ease: [0.16, 1, 0.3, 1] } },
  }
}
export const revealUp = reveal(12)

// Reduced-motion variants: opacity only, instant — no transform, no perceptible delay.
const staticContainer: Variants = { hidden: {}, show: { transition: { staggerChildren: 0 } } }
const staticReveal: Variants = { hidden: { opacity: 1 }, show: { opacity: 1 } }

// ---- <Stagger> : an in-view container that staggers its <Reveal> children (once) -----------
export function Stagger({ children, className, as = 'div', amount = 0.15 }: {
  children: ReactNode; className?: string; as?: 'div' | 'ul' | 'ol' | 'section'; amount?: number
}) {
  const ready = useMotionReady()
  return createElement(
    (m as any)[as],
    {
      className,
      initial: 'hidden',
      whileInView: 'show',
      viewport: { once: true, margin: '-10% 0px', amount },
      variants: ready ? staggerContainer : staticContainer,
    },
    children,
  )
}

// ---- <Reveal> : one element that rises + fades in as its parent stagger fires --------------
export function Reveal({ children, className, as = 'div', distance = 12 }: {
  children: ReactNode; className?: string; as?: 'div' | 'li' | 'span' | 'tr'; distance?: number
}) {
  const ready = useMotionReady()
  return createElement(
    (m as any)[as],
    { className, variants: ready ? reveal(distance) : staticReveal },
    children,
  )
}

// Returns true once the element has scrolled into view (stays true). Handy for gating a
// recharts draw-in animation so the line draws as it enters the viewport, exactly once.
export function useInViewOnce<T extends Element = HTMLDivElement>() {
  const ref = useRef<T>(null)
  const inView = useInView(ref, { once: true, margin: '-8% 0px' })
  return [ref, inView] as const
}

// ---- <AnimatedNumber> : count-up + smooth re-tween when a (polled) value changes -----------
export function AnimatedNumber({ value, format, className }: {
  value: number; format: (n: number) => string; className?: string
}) {
  const ready = useMotionReady()
  const [text, setText] = useState(() => format(value))
  const prev = useRef(value)

  useEffect(() => {
    if (!ready) { setText(format(value)); prev.current = value; return }
    const controls = animate(prev.current, value, {
      duration: 0.6,
      ease: 'easeOut',
      onUpdate: (v) => setText(format(v)),
    })
    prev.current = value
    return () => controls.stop()
    // format is stable at call sites; value drives the tween
  }, [value, ready]) // eslint-disable-line react-hooks/exhaustive-deps

  return createElement('span', { className }, text)
}
