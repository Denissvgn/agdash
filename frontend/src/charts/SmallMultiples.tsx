import { area, line } from 'd3-shape'
import { useTranslation } from 'react-i18next'
import { foldSeries, unreportedRuns, type Reported, type TimeData } from './data'
import { Figure, type FigureStatus, type LegendEntry } from './Figure'
import { formatCompact, formatTick, formatValue as formatWhole, formatWhen } from './format'
import { AREA_OPACITY, barThickness, crisp, HAIRLINE, labelWidth, LINE, verticalBar } from './geometry'
import { NotReportedBand } from './Hatch'
import { FILL } from './ink'
import { Plot, type Mark } from './Plot'
import { timeTable } from './time'
import { valueScale } from './scales'
import { useWidth } from './useWidth'

/** At most this many panels; past it the rest fold into Other. */
export const MAX_PANELS = 6
const PANEL = 104
const NAME = 22
const AXIS = 16
const GAP_X = 20
const GAP_Y = 16

type Point = [number, Reported]

/**
 * One small panel per entity, all on one value scale, so a harness, model or tool is
 * told apart by its place and name and compared by height. Steps or columns.
 */
export function SmallMultiples({
  title,
  caption,
  data,
  status = 'ready',
  onRetry,
  rowHeader,
  formatValue = formatWhole,
  variant = 'step',
}: {
  title: string
  caption?: string
  data?: TimeData
  status?: FigureStatus
  onRetry?: () => void
  rowHeader: string
  formatValue?: (value: number) => string
  variant?: 'step' | 'columns'
}) {
  const { t } = useTranslation()
  const [ref, width] = useWidth()
  const folded = data ? { ...data, series: foldSeries(data.series, MAX_PANELS, t('charts.other')) } : undefined
  const ready = !!folded && folded.buckets.length > 0 && folded.series.length > 0
  const shown: FigureStatus = status === 'ready' && !ready ? 'empty' : status
  const columns = width >= 900 ? 3 : width >= 520 ? 2 : 1
  const rows = Math.ceil((folded?.series.length || 2) / columns)
  const height = rows * PANEL + (rows - 1) * GAP_Y

  let plot = null
  let legend: LegendEntry[] = []
  if (folded && ready) {
    const n = folded.buckets.length
    const all = folded.series.flatMap((series) => series.values)
    const panelWidth = (width - GAP_X * (columns - 1)) / columns
    const top = NAME
    const bottom = PANEL - AXIS
    const y = valueScale(all, [bottom, top + 4])
    const peak = y.domain()[1]
    const left = Math.max(labelWidth(formatCompact(peak)), 28)
    const slot = (panelWidth - left) / n
    const thickness = barThickness(slot)
    const origin = (p: number) => ({ x: (p % columns) * (panelWidth + GAP_X), y: Math.floor(p / columns) * (PANEL + GAP_Y) })
    const centre = (i: number) => left + i * slot + slot / 2
    const describe = (value: Reported) => (value === null ? t('charts.notReported') : formatValue(value))
    const hatched = all.some((value) => value === null)
    legend = hatched ? [{ key: 'hatch', label: t('charts.notReported'), swatch: 'hatch' }] : []

    const marks: Mark[] = folded.series.flatMap((series, p) =>
      series.values.map((value, i) => ({
        x: origin(p).x + centre(i),
        y: origin(p).y + (value === null ? (top + bottom) / 2 : y(value)),
        value: describe(value),
        name: `${series.name}, ${formatWhen(folded.buckets[i], folded.step)}`,
      })),
    )
    const points = (values: Reported[]): Point[] =>
      values.flatMap((value, i): Point[] => [
        [left + i * slot, value],
        [left + (i + 1) * slot, value],
      ])
    const stroke = line<Point>()
      .defined((point) => point[1] !== null)
      .x((point) => point[0])
      .y((point) => y(point[1] ?? 0))
    const fill = area<Point>()
      .defined((point) => point[1] !== null)
      .x((point) => point[0])
      .y0(y(0))
      .y1((point) => y(point[1] ?? 0))

    plot = (
      <div ref={ref} className="min-w-0">
        <Plot
          crosshair={(index) => {
            const p = Math.floor(index / n)
            const o = origin(p)
            return { x: o.x + centre(index % n), top: o.y + top, bottom: o.y + bottom }
          }}
          height={height}
          label={title}
          marks={marks}
          pick={(px, py) => {
            const p = Math.floor(py / (PANEL + GAP_Y)) * columns + Math.floor(px / (panelWidth + GAP_X))
            if (p < 0 || p >= folded.series.length) return null
            const i = Math.floor((px - origin(p).x - left) / slot)
            return i >= 0 && i < n ? p * n + i : null
          }}
          stride={n}
          width={width}
        >
          {({ active, mark, hatch }) =>
            folded.series.map((series, p) => {
              const o = origin(p)
              return (
                <g key={series.id} transform={`translate(${o.x},${o.y})`}>
                  <text className="fill-primary-ink text-label font-medium" data-figure-data="" x={0} y={13}>
                    {series.name}
                  </text>
                  <g aria-hidden="true" data-figure-data="">
                    <line className="stroke-hairline" strokeWidth={HAIRLINE} x1={left} x2={panelWidth} y1={crisp(y(peak))} y2={crisp(y(peak))} />
                    <text className="fill-secondary-ink text-counter tabular-nums" dominantBaseline="middle" textAnchor="end" x={left - 6} y={y(peak)}>
                      {formatCompact(peak)}
                    </text>
                    <line className="stroke-strong-rule" strokeWidth={HAIRLINE} x1={left} x2={panelWidth} y1={crisp(bottom)} y2={crisp(bottom)} />
                    <text className="fill-secondary-ink text-counter tabular-nums" x={left} y={bottom + 13}>
                      {formatTick(folded.buckets[0], folded.step)}
                    </text>
                    <text className="fill-secondary-ink text-counter tabular-nums" textAnchor="end" x={panelWidth} y={bottom + 13}>
                      {formatTick(folded.buckets[n - 1], folded.step)}
                    </text>
                  </g>
                  {unreportedRuns(series.values).map(([first, last]) => (
                    <NotReportedBand
                      key={first}
                      height={bottom - top}
                      labelled={false}
                      pattern={hatch}
                      width={(last - first + 1) * slot}
                      x={left + first * slot}
                      y={top}
                    />
                  ))}
                  {variant === 'step' ? (
                    <>
                      <path className={FILL.mark} d={fill(points(series.values)) ?? ''} fillOpacity={AREA_OPACITY} />
                      <path
                        className="stroke-figure-mark"
                        d={stroke(points(series.values)) ?? ''}
                        fill="none"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={LINE}
                      />
                    </>
                  ) : (
                    series.values.map((value, i) =>
                      value === null || value <= 0 ? null : (
                        <path
                          key={i}
                          className={active === p * n + i ? FILL['mark-hover'] : FILL.mark}
                          d={verticalBar(centre(i) - thickness / 2, y(0), y(value), thickness)}
                        />
                      ),
                    )
                  )}
                  {series.values.map((_, i) => (
                    <rect
                      key={i}
                      {...mark(p * n + i)}
                      fill="transparent"
                      height={bottom - top}
                      width={slot}
                      x={left + i * slot}
                      y={top}
                    />
                  ))}
                </g>
              )
            })
          }
        </Plot>
      </div>
    )
  }

  return (
    <Figure
      caption={caption}
      height={height}
      legend={legend}
      status={shown}
      table={folded ? timeTable(folded, rowHeader, formatValue) : undefined}
      title={title}
      onRetry={onRetry}
    >
      {plot}
    </Figure>
  )
}
