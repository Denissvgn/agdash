import { useId, useRef, type KeyboardEvent, type PointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { TimeData } from './data'
import { Figure, type FigureStatus } from './Figure'
import { formatCompact, formatValue as formatWhole, formatWhen } from './format'
import { barThickness, crisp, HAIRLINE, labelWidth, verticalBar } from './geometry'
import { FILL } from './ink'
import { clampRange, shiftRange, type Range } from './range'
import { labelEvery, valueScale } from './scales'
import { useWidth } from './useWidth'

const TOP = 6
const AXIS = 20
const HANDLE = 8
const PAGE = 7

/**
 * An overview of a time series with a window over part of it, in the planning
 * thread. Its two edges are sliders: the arrow keys move an edge, Shift with an
 * arrow key moves the whole window. The pointer drags an edge or the window, draws
 * a new range from a press outside it, picks one bucket with a click, and extends
 * the range to the bucket a shift-click lands on.
 */
export function SelectionWindow({
  title,
  caption,
  data,
  value,
  onChange,
  status = 'ready',
  onRetry,
  rowHeader,
  formatValue = formatWhole,
  height = 96,
  emptyTitle,
  emptyBody,
  whole,
}: {
  title: string
  caption?: string
  /** One series; its buckets are the steps the window moves by. */
  data?: TimeData
  value: Range
  onChange: (range: Range) => void
  status?: FigureStatus
  onRetry?: () => void
  rowHeader: string
  formatValue?: (value: number) => string
  height?: number
  emptyTitle?: string
  emptyBody?: string
  /** Words for the line under the figure when the range covers every step, such as "All time: …". */
  whole?: (from: string, to: string) => string
}) {
  const { t } = useTranslation()
  const [ref, width] = useWidth()
  const hint = useId()
  // What a press is doing: resizing an edge, moving the window, or drawing a new range
  // from an anchor; ``moved`` tells a click from a drag.
  const drag = useRef<{ part: 'start' | 'end' | 'window' | 'draw'; at: number; moved: boolean } | null>(null)
  const series = data?.series[0]
  const ready = !!data && !!series && data.buckets.length > 0
  const shown: FigureStatus = status === 'ready' && !ready ? 'empty' : status

  let plot = null
  if (data && series && ready) {
    const n = data.buckets.length
    const last = n - 1
    const [start, end] = clampRange(value, last)
    const bottom = height - AXIS
    const y = valueScale(series.values, [bottom, TOP])
    const left = Math.max(labelWidth(formatCompact(y.domain()[1])), 28)
    const right = width - HANDLE
    const slot = (right - left) / n
    const edge = (i: number) => left + i * slot
    const thickness = barThickness(slot)
    const every = labelEvery(n, right - left, 72)
    const when = (i: number) => formatWhen(data.buckets[i], data.step)
    const bucketAt = (px: number) => Math.max(0, Math.min(last, Math.floor((px - left) / slot)))

    const set = (range: Range) => {
      const next = clampRange(range, last)
      if (next[0] !== start || next[1] !== end) onChange(next)
    }
    const keys = (part: 'start' | 'end') => (event: KeyboardEvent<HTMLDivElement>) => {
      const moves: Record<string, number> = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1, PageDown: -PAGE, PageUp: PAGE }
      if (event.key in moves) {
        event.preventDefault()
        const by = moves[event.key]
        if (event.shiftKey) set(shiftRange([start, end], by, last))
        else if (part === 'start') set([Math.min(end, start + by), end])
        else set([start, Math.max(start, end + by)])
      } else if (event.key === 'Home' || event.key === 'End') {
        event.preventDefault()
        const to = event.key === 'Home' ? 0 : last
        set(part === 'start' ? [Math.min(to, end), end] : [start, Math.max(to, start)])
      }
    }
    const local = (event: PointerEvent<HTMLDivElement>) => event.clientX - event.currentTarget.getBoundingClientRect().left
    const down = (event: PointerEvent<HTMLDivElement>) => {
      const px = local(event)
      const at = bucketAt(px)
      const near = (x: number) => Math.abs(px - x) <= HANDLE
      if (event.shiftKey) {
        // Shift-click extends the range to the day pressed, from whichever end is nearer.
        const part = at < start ? 'start' : 'end'
        set(part === 'start' ? [at, end] : [start, Math.max(at, start)])
        drag.current = { part, at, moved: true }
      } else if (near(edge(start))) drag.current = { part: 'start', at, moved: true }
      else if (near(edge(end + 1))) drag.current = { part: 'end', at, moved: true }
      // A window over every bucket cannot move, so a press inside it draws a new range.
      else if (at >= start && at <= end && (start > 0 || end < last)) drag.current = { part: 'window', at, moved: false }
      else {
        // Outside the window: a new range starts here, a single day until the pointer moves.
        set([at, at])
        drag.current = { part: 'draw', at, moved: false }
      }
      event.currentTarget.setPointerCapture?.(event.pointerId)
    }
    const move = (event: PointerEvent<HTMLDivElement>) => {
      const press = drag.current
      if (!press) return
      const at = bucketAt(local(event))
      if (at !== press.at) press.moved = true
      if (press.part === 'start') set([Math.min(at, end), end])
      else if (press.part === 'end') set([start, Math.max(at, start)])
      else if (press.part === 'draw') set([Math.min(press.at, at), Math.max(press.at, at)])
      else if (press.moved) {
        set(shiftRange([start, end], at - press.at, last))
        press.at = at
      }
    }
    const up = (event: PointerEvent<HTMLDivElement>) => {
      const press = drag.current
      drag.current = null
      // A click inside the window, with no drag, picks that one day.
      if (press?.part === 'window' && !press.moved) {
        const at = bucketAt(local(event))
        set([at, at])
      }
    }
    const thumb = (part: 'start' | 'end') => {
      const at = part === 'start' ? start : end
      return (
        <div
          aria-describedby={hint}
          aria-label={t(part === 'start' ? 'charts.selection.start' : 'charts.selection.end')}
          aria-valuemax={last}
          aria-valuemin={0}
          aria-valuenow={at}
          aria-valuetext={when(at)}
          className="absolute top-0 flex h-full w-[16px] -translate-x-1/2 pointer-coarse:w-touch cursor-ew-resize items-center justify-center rounded-compact focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-planning-thread"
          role="slider"
          style={{ left: part === 'start' ? edge(start) : edge(end + 1) }}
          tabIndex={0}
          onKeyDown={keys(part)}
        >
          <span className="block h-[28px] w-[6px] rounded-full bg-planning-thread" />
        </div>
      )
    }

    plot = (
      <div ref={ref} className="min-w-0">
        <div className="relative touch-none select-none" style={{ height }} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={() => (drag.current = null)}>
          <svg aria-hidden="true" className="block" height={height} width={width}>
            <rect className="fill-planning-thread-soft" height={bottom - TOP} width={edge(end + 1) - edge(start)} x={edge(start)} y={TOP} />
            <line className="stroke-strong-rule" strokeWidth={HAIRLINE} x1={left} x2={right} y1={crisp(bottom)} y2={crisp(bottom)} />
            <text className="fill-secondary-ink text-counter tabular-nums" data-figure-data="" dominantBaseline="middle" textAnchor="end" x={left - 6} y={y(y.domain()[1])}>
              {formatCompact(y.domain()[1])}
            </text>
            {series.values.map((v, i) =>
              v === null || v <= 0 ? null : (
                <path
                  key={i}
                  className={i >= start && i <= end ? FILL.mark : FILL.context}
                  d={verticalBar(edge(i) + (slot - thickness) / 2, bottom, y(v), thickness)}
                />
              ),
            )}
            <rect
              className="stroke-planning-thread"
              fill="none"
              height={bottom - TOP}
              strokeWidth={HAIRLINE * 2}
              width={Math.max(0, edge(end + 1) - edge(start))}
              x={edge(start)}
              y={TOP}
            />
            <g data-figure-data="">
              {data.buckets.map((at, i) =>
                i % every === 0 ? (
                  <text key={at.toISOString()} className="fill-secondary-ink text-counter tabular-nums" textAnchor="middle" x={edge(i) + slot / 2} y={bottom + 14}>
                    {formatWhen(at, Math.max(data.step, 86_400_000))}
                  </text>
                ) : null,
              )}
            </g>
          </svg>
          {thumb('start')}
          {thumb('end')}
        </div>
        <span className="sr-only" id={hint}>
          {t('charts.selection.keys')}
        </span>
        <p aria-live="polite" className="mt-xs text-metadata text-secondary-ink" data-figure-data="">
          {whole && start === 0 && end === last ? whole(when(start), when(end)) : t('charts.selection.value', { from: when(start), to: when(end) })}
        </p>
      </div>
    )
  }

  const [start, end] = data && data.buckets.length ? clampRange(value, data.buckets.length - 1) : value
  return (
    <Figure
      caption={caption}
      emptyBody={emptyBody}
      emptyTitle={emptyTitle}
      height={height + 24}
      status={shown}
      table={
        data && series && ready
          ? {
              rowHeader,
              columns: [
                { key: 'value', label: series.name },
                { key: 'inside', label: t('charts.selection.inside') },
              ],
              rows: data.buckets.map((at, i) => ({
                key: at.toISOString(),
                header: formatWhen(at, data.step),
                cells: [
                  series.values[i] === null ? null : formatValue(series.values[i] ?? 0),
                  t(i >= start && i <= end ? 'charts.selection.yes' : 'charts.selection.no'),
                ],
              })),
            }
          : undefined
      }
      title={title}
      onRetry={onRetry}
    >
      {plot}
    </Figure>
  )
}
