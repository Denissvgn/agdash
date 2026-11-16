import { useCallback, useState } from 'react'

/** The width a figure has to draw in, following its box as the window changes. */
export function useWidth(fallback = 640): [(node: HTMLDivElement | null) => (() => void) | undefined, number] {
  const [width, setWidth] = useState(fallback)
  const ref = useCallback(
    (node: HTMLDivElement | null) => {
      if (!node) return undefined
      const measured = Math.floor(node.getBoundingClientRect().width)
      if (measured > 0) setWidth(measured)
      if (typeof ResizeObserver === 'undefined') return undefined
      const observer = new ResizeObserver(([entry]) => {
        const next = Math.floor(entry.contentRect.width)
        if (next > 0) setWidth(next)
      })
      observer.observe(node)
      return () => observer.disconnect()
    },
    [],
  )
  return [ref, width]
}
