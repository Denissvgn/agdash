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
 * arrow key moves the whole window, and the pointer drags an edge or the window.
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
}) {
  const { t } = useTranslation()
  const [ref, width] = useWidth()
  const hint = useId()
  const drag = useRef<{ part: 'start' | 'end' | 'window'; offset: number } | null>(null)
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
      const part = near(edge(start)) ? 'start' : near(edge(end + 1)) ? 'end' : at >= start && at <= end ? 'window' : null
      if (part) {
        drag.current = { part, offset: at - start }
      } else {
        // Outside the window: centre the window there and carry on dragging it.
        const half = Math.floor((end - start) / 2)
        set(shiftRange([start, end], at - half - start, last))
        drag.current = { part: 'window', offset: half }
      }
      event.currentTarget.setPointerCapture?.(event.pointerId)
    }
    const move = (event: PointerEvent<HTMLDivElement>) => {
      if (!drag.current) return
      const at = bucketAt(local(event))
      if (drag.current.part === 'start') set([Math.min(at, end), end])
      else if (drag.current.part === 'end') set([start, Math.max(at, start)])
      else set(shiftRange([start, end], at - drag.current.offset - start, last))
    }
    const up = () => {
      drag.current = null
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
          className="absolute top-0 flex h-full w-[16px] -translate-x-1/2 cursor-ew-resize items-center justify-center rounded-compact outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-planning-thread"
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
        <div className="relative touch-none select-none" style={{ height }} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}>
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
          {t('charts.selection.value', { from: when(start), to: when(end) })}
        </p>
      </div>
    )
  }

  const [start, end] = data && data.buckets.length ? clampRange(value, data.buckets.length - 1) : value
  return (
    <Figure
      caption={caption}
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
