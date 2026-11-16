import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import type { Reported } from './data'
import { Figure, type FigureStatus } from './Figure'
import { formatCompact, formatValue as formatWhole } from './format'
import { GAP, HAIRLINE, labelWidth, svgId } from './geometry'
import { HEAT_STEPS, heatBounds, heatStep, niceTop } from './heat'
import { NotReportedBand } from './Hatch'
import { FILL, type Ink } from './ink'
import { Plot, type Mark } from './Plot'
import { labelEvery } from './scales'
import { useWidth } from './useWidth'

export type HeatmapData = {
  rows: { id: string; name: string }[]
  columns: { id: string; name: string }[]
  /** values[row][column]; null where nothing was reported. */
  values: Reported[][]
}

const TOP = 20
const MAX_CELL = 28
const MIN_CELL = 12
/**
 * A grid of cells in the ink ramp, darker for more, with a scale legend; a cell
 * that was not reported is hatched. Arrow keys move along a row, up and down
 * between rows.
 */
export function Heatmap({
  title,
  caption,
  data,
  status = 'ready',
  onRetry,
  rowHeader,
  formatValue = formatWhole,
}: {
  title: string
  caption?: string
  data?: HeatmapData
  status?: FigureStatus
  onRetry?: () => void
  rowHeader: string
  formatValue?: (value: number) => string
}) {
  const { t } = useTranslation()
  const [ref, width] = useWidth()
  const scaleHatch = svgId(useId())
  const ready = !!data && data.rows.length > 0 && data.columns.length > 0
  const shown: FigureStatus = status === 'ready' && !ready ? 'empty' : status
  const left = ready ? Math.min(Math.max(...data.rows.map((row) => labelWidth(row.name)), 40), Math.max(40, Math.floor(width * 0.3))) : 40
  const cell = ready ? Math.max(MIN_CELL, Math.min(MAX_CELL, (width - left) / data.columns.length)) : MAX_CELL
  const height = TOP + (ready ? data.rows.length : 7) * cell
  // The top of the scale, rounded up so each of the six bins has a readable bound.
  const max = ready ? niceTop(Math.max(0, ...data.values.flat().map((value) => value ?? 0))) : 0

  let plot = null
  let scale = null
  if (data && ready) {
    const n = data.columns.length
    const every = labelEvery(n, n * cell, 40)
    const marks: Mark[] = data.rows.flatMap((row, r) =>
      data.columns.map((column, c) => {
        const value = data.values[r]?.[c] ?? null
        return {
          x: left + c * cell + cell / 2,
          y: TOP + r * cell + cell / 2,
          value: value === null ? t('charts.notReported') : formatValue(value),
          name: t('charts.heatmap.cell', { row: row.name, column: column.name }),
        }
      }),
    )
    plot = (
      <div ref={ref} className="min-w-0">
        <Plot
          height={height}
          label={title}
          marks={marks}
          pick={(px, py) => {
            const c = Math.floor((px - left) / cell)
            const r = Math.floor((py - TOP) / cell)
            return c >= 0 && c < n && r >= 0 && r < data.rows.length ? r * n + c : null
          }}
          stride={n}
          width={width}
        >
          {({ active, mark, hatch }) => (
            <>
              <g aria-hidden="true" data-figure-data="">
                {data.columns.map((column, c) =>
                  c % every === 0 ? (
                    <text key={column.id} className="fill-secondary-ink text-counter tabular-nums" textAnchor="middle" x={left + c * cell + cell / 2} y={TOP - 7}>
                      {column.name}
                    </text>
                  ) : null,
                )}
                {data.rows.map((row, r) => (
                  <text
                    key={row.id}
                    className="fill-primary-ink text-counter"
                    dominantBaseline="middle"
                    textAnchor="end"
                    x={left - 8}
                    y={TOP + r * cell + cell / 2}
                  >
                    {row.name}
                  </text>
                ))}
              </g>
              {data.rows.map((row, r) =>
                data.columns.map((column, c) => {
                  const value = data.values[r]?.[c] ?? null
                  const i = r * n + c
                  const x = left + c * cell + GAP / 2
                  const y = TOP + r * cell + GAP / 2
                  return (
                    <g key={`${row.id}-${column.id}`} {...mark(i)}>
                      {value === null ? (
                        <NotReportedBand height={cell - GAP} labelled={false} pattern={hatch} width={cell - GAP} x={x} y={y} />
                      ) : (
                        <rect className={FILL[heatStep(value, max)]} height={cell - GAP} rx={2} width={cell - GAP} x={x} y={y} />
                      )}
                      {active === i ? (
                        <rect
                          className="stroke-primary-ink"
                          fill="none"
                          height={cell - GAP + 2}
                          rx={3}
                          strokeWidth={HAIRLINE * 2}
                          width={cell - GAP + 2}
                          x={x - 1}
                          y={y - 1}
                        />
                      ) : null}
                    </g>
                  )
                }),
              )}
            </>
          )}
        </Plot>
      </div>
    )
    const bounds = heatBounds(max)
    const unreported = data.values.flat().some((value) => value === null)
    scale = (
      <ul aria-label={t('charts.heatmap.scale')} className="flex flex-wrap items-end gap-x-[2px] gap-y-xs text-counter text-secondary-ink">
        {HEAT_STEPS.map((step, i) => (
          <li key={step} className="flex w-[40px] flex-col gap-[2px]">
            <span aria-hidden="true" className={`block h-[10px] rounded-[2px] ${BG[step]}`} />
            <span className="tabular-nums" data-figure-data="">
              {i === 0 ? formatCompact(0) : t('charts.heatmap.upTo', { value: formatCompact(bounds[i]) })}
            </span>
          </li>
        ))}
        {unreported ? (
          <li className="ml-md flex flex-col gap-[2px]">
            <svg aria-hidden="true" height={10} width={40}>
              <defs>
                <pattern height={6} id={scaleHatch} patternTransform="rotate(45)" patternUnits="userSpaceOnUse" width={6}>
                  <line className="stroke-strong-rule" strokeOpacity={0.35} strokeWidth={HAIRLINE} x1={0.5} x2={0.5} y1={0} y2={6} />
                </pattern>
              </defs>
              <rect fill={`url(#${scaleHatch})`} height={10} rx={2} width={40} />
            </svg>
            <span>{t('charts.notReported')}</span>
          </li>
        ) : null}
      </ul>
    )
  }

  return (
    <Figure
      caption={caption}
      height={height}
      status={shown}
      table={
        data && ready
          ? {
              rowHeader,
              columns: data.columns.map((column) => ({ key: column.id, label: column.name })),
              rows: data.rows.map((row, r) => ({
                key: row.id,
                header: row.name,
                cells: data.columns.map((_, c) => {
                  const value = data.values[r]?.[c] ?? null
                  return value === null ? null : formatValue(value)
                }),
              })),
            }
          : undefined
      }
      title={title}
      onRetry={onRetry}
    >
      {plot ? (
        <div className="flex flex-col gap-sm">
          {plot}
          {scale}
        </div>
      ) : null}
    </Figure>
  )
}

const BG: Record<Ink, string> = {
  'ink-1': 'bg-figure-ink-1',
  'ink-2': 'bg-figure-ink-2',
  'ink-3': 'bg-figure-ink-3',
  'ink-4': 'bg-figure-ink-4',
  'ink-5': 'bg-figure-ink-5',
  'ink-6': 'bg-figure-ink-6',
  'ink-7': 'bg-figure-ink-7',
}
