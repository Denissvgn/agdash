import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import { VerticalGrid } from './Axes'
import { hasUnreported, MAX_BARS, OTHER, rankCategories, type Category } from './data'
import { Figure, type FigureStatus, type LegendEntry } from './Figure'
import { formatCompact, formatValue as formatWhole } from './format'
import { barThickness, GAP, HAIRLINE, horizontalBar, labelWidth, svgId } from './geometry'
import { NotReportedBand } from './Hatch'
import { FILL } from './ink'
import { Plot, type Mark } from './Plot'
import { bandScale, valueScale, valueTicks } from './scales'
import { useWidth } from './useWidth'

const ROW = 32
const TOP = 4
const AXIS = 22
const VALUE_ROOM = 48
/** Room for the value and the part's count, such as "1.2K · 30 errors". */
const PART_ROOM = 120
/** The part's texture: mark-ink lines at 135°, closer than the not-reported hatch. */
const PART_SPACING = 4

function PartPattern({ id }: { id: string }) {
  return (
    <pattern height={PART_SPACING} id={id} patternTransform="rotate(-45)" patternUnits="userSpaceOnUse" width={PART_SPACING}>
      <line className="stroke-figure-mark" strokeWidth={HAIRLINE} x1={0.5} x2={0.5} y1={0} y2={PART_SPACING} />
    </pattern>
  )
}

/**
 * Ranked horizontal bars, one row per entity, largest first: identity by row label,
 * magnitude in ink. Past twelve rows the tail folds into Other.
 */
export function BarChart({
  title,
  caption,
  categories,
  status = 'ready',
  onRetry,
  rowHeader,
  valueHeader,
  formatValue = formatWhole,
  selected,
  max = MAX_BARS,
  part,
  twin = true,
}: {
  title: string
  caption?: string
  categories?: Category[]
  status?: FigureStatus
  onRetry?: () => void
  rowHeader: string
  valueHeader: string
  formatValue?: (value: number) => string
  /** The entity in view elsewhere on the page, marked in the planning thread. */
  selected?: string
  max?: number
  /**
   * Names for a part of each bar drawn apart at its end, such as failed calls: the
   * whole value's name and the part's for the legend, and a label for the part's count.
   */
  part?: { valueName: string; name: string; label: (count: number) => string }
  /** False when a table already on the page lists the same values. */
  twin?: boolean
}) {
  const { t } = useTranslation()
  const [ref, width] = useWidth()
  const partPattern = svgId(useId())
  const rows = categories ? rankCategories(categories, max, t('charts.other')) : []
  const shown: FigureStatus = status === 'ready' && !rows.length ? 'empty' : status
  const height = TOP + (rows.length || 4) * ROW + AXIS
  const unreported = hasUnreported(rows.map((row) => row.value))

  const left = Math.min(Math.max(...rows.map((row) => labelWidth(row.name)), 72), Math.max(72, Math.floor(width * 0.4)))
  const parted = !!part && rows.some((row) => (row.part ?? 0) > 0)
  const right = width - (parted ? PART_ROOM : VALUE_ROOM)
  const x = valueScale(
    rows.map((row) => row.value),
    [left, Math.max(left + 1, right)],
  )
  const ticks = valueTicks(x, right - left)
  const y = bandScale(
    rows.map((row) => row.id),
    [TOP, TOP + rows.length * ROW],
    0.25,
  )
  const thickness = barThickness(y.bandwidth() + 2)
  const offset = (y.bandwidth() - thickness) / 2
  const chars = Math.max(4, Math.floor((left - 12) / 6.4))
  const clip = (name: string) => (name.length > chars ? `${name.slice(0, chars - 1)}…` : name)
  const describe = (row: Category) =>
    row.value === null
      ? t('charts.notReported')
      : row.detail
        ? `${formatValue(row.value)}, ${row.detail}`
        : formatValue(row.value)
  const legend: LegendEntry[] = [
    ...(parted && part
      ? [
          { key: 'value', label: part.valueName, swatch: 'ink-6' as const },
          {
            key: 'part',
            label: part.name,
            swatch: {
              draw: () => (
                <>
                  <defs>
                    <PartPattern id={`${partPattern}-legend`} />
                  </defs>
                  <rect className={FILL['ink-2']} height={12} rx={2} width={12} />
                  <rect fill={`url(#${partPattern}-legend)`} height={12} rx={2} width={12} />
                </>
              ),
            },
          },
        ]
      : []),
    ...(unreported ? [{ key: 'hatch', label: t('charts.notReported'), swatch: 'hatch' as const }] : []),
  ]

  const marks: Mark[] = rows.map((row) => ({
    x: row.value === null ? left : x(row.value),
    y: (y(row.id) ?? 0) + y.bandwidth() / 2,
    value: describe(row),
    name: row.name,
  }))

  return (
    <Figure
      caption={caption}
      height={height}
      legend={legend}
      status={shown}
      table={
        twin
          ? {
        rowHeader,
        columns: [{ key: 'value', label: valueHeader }],
        rows: rows.map((row) => ({
          key: row.id,
          header: row.name,
          cells: [row.value === null ? null : formatValue(row.value)],
        })),
            }
          : undefined
      }
      title={title}
      onRetry={onRetry}
    >
      {rows.length ? (
        <div ref={ref} className="min-w-0">
          <Plot
            height={height}
            label={title}
            marks={marks}
            pick={(_, py) => {
              const index = Math.floor((py - TOP) / ROW)
              return index >= 0 && index < rows.length ? index : null
            }}
            width={width}
          >
            {({ active, mark, hatch }) => (
              <>
                <defs>
                  <PartPattern id={partPattern} />
                </defs>
                <VerticalGrid bottom={TOP + rows.length * ROW} format={formatCompact} ticks={ticks} top={TOP} x={x} />
                {rows.map((row, i) => {
                  const top = (y(row.id) ?? 0) + offset
                  const middle = top + thickness / 2
                  const fill =
                    row.id === selected ? FILL.selected : active === i ? FILL['mark-hover'] : row.id === OTHER ? FILL.other : FILL.mark
                  return (
                    <g key={row.id}>
                      <text
                        className="fill-primary-ink text-counter"
                        data-figure-data=""
                        dominantBaseline="middle"
                        textAnchor="end"
                        x={left - 8}
                        y={middle}
                      >
                        {clip(row.name)}
                      </text>
                      {row.value === null ? (
                        <g {...mark(i)}>
                          <NotReportedBand height={thickness} pattern={hatch} width={right - left} x={left} y={top} />
                        </g>
                      ) : (
                        <>
                          {part && (row.part ?? 0) > 0 ? (
                            <g {...mark(i)} data-part={row.part ?? 0}>
                              {/* The whole, square at both ends, then the gap, then the part with the data end. */}
                              {x(row.value - (row.part ?? 0)) - GAP > left ? (
                                <path className={fill} d={horizontalBar(left, x(row.value - (row.part ?? 0)) - GAP, top, thickness, false)} />
                              ) : null}
                              <path className={FILL['ink-2']} d={horizontalBar(x(row.value - (row.part ?? 0)), x(row.value), top, thickness)} />
                              <path
                                d={horizontalBar(x(row.value - (row.part ?? 0)), x(row.value), top, thickness)}
                                fill={`url(#${partPattern})`}
                              />
                            </g>
                          ) : (
                            <path {...mark(i)} className={fill} d={horizontalBar(left, x(row.value), top, thickness)} />
                          )}
                          <text
                            className="fill-secondary-ink text-counter tabular-nums"
                            data-figure-data=""
                            dominantBaseline="middle"
                            x={x(row.value) + 6}
                            y={middle}
                          >
                            {part && (row.part ?? 0) > 0
                              ? `${formatCompact(row.value)} · ${part.label(row.part ?? 0)}`
                              : formatCompact(row.value)}
                          </text>
                        </>
                      )}
                    </g>
                  )
                })}
              </>
            )}
          </Plot>
        </div>
      ) : null}
    </Figure>
  )
}
