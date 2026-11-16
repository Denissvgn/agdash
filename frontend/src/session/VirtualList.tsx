import { useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type Ref } from 'react'

/** What a list's owner can ask of it. */
export type VirtualListHandle = { scrollToIndex: (index: number) => void }

const OVERSCAN_PX = 800
const NEAR_END = 30

/**
 * A scrolling list that renders only the rows near the viewport, so a session of tens of
 * thousands of events stays light. Rows may be any height: each is measured once drawn,
 * and when a row above the viewport turns out taller or shorter than estimated, the
 * scroll position moves with it, so what the reader looks at stays put.
 */
export function VirtualList({
  count,
  total,
  estimate,
  label,
  busy,
  renderRow,
  onNearEnd,
  onRange,
  handle,
  className = '',
}: {
  count: number
  /** All rows there will be, loaded or not; announced to assistive technology. */
  total: number
  estimate: number
  label: string
  busy: boolean
  renderRow: (index: number) => ReactNode
  onNearEnd: () => void
  /** The first and last rows at least partly in view, whenever they change. */
  onRange?: (first: number, last: number) => void
  handle?: Ref<VirtualListHandle>
  className?: string
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const observer = useRef<ResizeObserver | null>(null)
  const firstShown = useRef(0)
  const measured = useRef(new Map<number, number>())
  const known = useRef(new Map<number, number>())
  const frame = useRef<number | null>(null)
  // Measured heights, 0 for a row not drawn yet. Replaced at most once a frame.
  const [sizes, setSizes] = useState(() => new Float64Array(0))
  const [view, setView] = useState({ top: 0, height: 0 })

  const offsets = useMemo(() => {
    const found = new Float64Array(count + 1)
    for (let index = 0; index < count; index += 1) {
      found[index + 1] = found[index] + (sizes[index] || estimate)
    }
    return found
  }, [count, estimate, sizes])

  const rowAt = useCallback(
    (offset: number) => {
      let low = 0
      let high = count
      while (low < high) {
        const middle = (low + high) >> 1
        if (offsets[middle + 1] <= offset) low = middle + 1
        else high = middle
      }
      return low
    },
    [count, offsets],
  )

  const height = view.height || 800 // before layout, and in environments without one
  const start = Math.max(0, rowAt(view.top - OVERSCAN_PX))
  const end = Math.min(count, rowAt(view.top + height + OVERSCAN_PX) + 1)
  const first = rowAt(view.top)

  useLayoutEffect(() => {
    firstShown.current = first
  }, [first])

  const last = Math.max(first, Math.min(count - 1, rowAt(view.top + height - 1)))
  useEffect(() => {
    if (count > 0) onRange?.(first, last)
  }, [count, first, last, onRange])

  useImperativeHandle(
    handle,
    () => ({
      scrollToIndex: (index: number) => {
        const element = scroller.current
        if (element) element.scrollTop = offsets[Math.max(0, Math.min(count, index))]
      },
    }),
    [count, offsets],
  )

  useEffect(() => {
    if (count > 0 && end >= count - NEAR_END) onNearEnd()
  }, [count, end, onNearEnd])

  useLayoutEffect(() => {
    const element = scroller.current
    if (!element) return
    const update = () => setView({ top: element.scrollTop, height: element.clientHeight })
    update()
    element.addEventListener('scroll', update, { passive: true })
    const resize = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update)
    resize?.observe(element)
    return () => {
      element.removeEventListener('scroll', update)
      resize?.disconnect()
    }
  }, [])

  // A layout effect, so the observer exists before the first paint; rows drawn before it
  // existed are picked up here.
  useLayoutEffect(() => {
    if (typeof ResizeObserver === 'undefined') return
    const flush = () => {
      frame.current = null
      const updates = new Map(measured.current)
      measured.current.clear()
      setSizes((previous) => {
        const length = Math.max(previous.length, ...updates.keys()) + 1
        const next = new Float64Array(length)
        next.set(previous)
        for (const [index, value] of updates) next[index] = value
        return next
      })
    }
    const watching = new ResizeObserver((entries) => {
      let shift = 0
      for (const entry of entries) {
        const element = entry.target as HTMLElement
        const index = Number(element.dataset.index)
        const value = element.offsetHeight
        const before = known.current.get(index) ?? estimate
        if (!value || value === before) continue
        known.current.set(index, value)
        measured.current.set(index, value)
        if (index < firstShown.current) shift += value - before
      }
      if (shift && scroller.current) scroller.current.scrollTop += shift
      if (measured.current.size && frame.current === null) frame.current = requestAnimationFrame(flush)
    })
    observer.current = watching
    scroller.current?.querySelectorAll<HTMLElement>('[data-index]').forEach((row) => watching.observe(row))
    return () => {
      watching.disconnect()
      observer.current = null
      if (frame.current !== null) cancelAnimationFrame(frame.current)
    }
  }, [estimate])

  const measure = useCallback((element: HTMLElement | null) => {
    if (element) observer.current?.observe(element)
  }, [])

  const rows = []
  for (let index = start; index < end; index += 1) {
    rows.push(
      <div
        key={index}
        ref={measure}
        aria-posinset={index + 1}
        aria-setsize={total}
        className="absolute left-0 right-0"
        data-index={index}
        role="listitem"
        style={{ top: offsets[index] }}
      >
        {renderRow(index)}
      </div>,
    )
  }

  return (
    <div
      ref={scroller}
      aria-busy={busy}
      aria-label={label}
      className={`overflow-y-auto ${className}`}
      role="region"
      // A scrolling region is focusable so it can be scrolled from the keyboard.
      tabIndex={0}
    >
      <div className="relative" role="list" style={{ height: offsets[count] }}>
        {rows}
      </div>
    </div>
  )
}
