import { area, line } from 'd3-shape'
import { useTranslation } from 'react-i18next'
import { HorizontalGrid, TimeLabels } from './Axes'
import { foldSeries, MAX_SERIES, SERIES_INK, unreportedRuns, type Reported, type TimeData } from './data'
import { Figure, type FigureStatus, type LegendEntry } from './Figure'
import { formatCompact, formatValue as formatWhole } from './format'
import { AREA_OPACITY, DOT, LINE, RING } from './geometry'
import { NotReportedBand } from './Hatch'
import { FILL, STROKE, type Ink } from './ink'
import { Plot } from './Plot'
import { bucketMarks, timeFrame, timeTable } from './time'
import { useWidth } from './useWidth'

type Point = [number, Reported]

const LABEL_ROOM = 96
const LABEL_GAP = 12

/** Per bucket, the largest value any series reported, or null when none did. */
function peaks(data: TimeData): Reported[] {
  return data.buckets.map((_, i) => {
    const reported = data.series.map((series) => series.values[i]).filter((value): value is number => value !== null)
    return reported.length ? Math.max(...reported) : null
  })
}

/** Labels at the lines' ends, pushed apart so none overlap. */
function spread(labels: { id: string; y: number }[], top: number, bottom: number) {
  const sorted = [...labels].sort((a, b) => a.y - b.y)
  sorted.forEach((label, i) => {
    if (i > 0) label.y = Math.max(label.y, sorted[i - 1].y + LABEL_GAP)
  })
  const overflow = (sorted[sorted.length - 1]?.y ?? 0) - bottom
  if (overflow > 0) sorted.forEach((label) => (label.y = Math.max(top, label.y - overflow)))
  return new Map(sorted.map((label) => [label.id, label.y]))
}

/**
 * Lines over time, or step areas for counts held over a bucket. A gap in the line is
 * a stretch that was not reported; where no series reported, the stretch is hatched.
 * Up to three series, by ink step, named in the legend and at the lines' ends.
 */
export function LineChart({
  title,
  caption,
  data,
  status = 'ready',
  onRetry,
  rowHeader,
  formatValue = formatWhole,
  formatAxis = formatCompact,
  variant = 'line',
  height = 220,
}: {
  title: string
  caption?: string
  data?: TimeData
  status?: FigureStatus
  onRetry?: () => void
  rowHeader: string
  formatValue?: (value: number) => string
  /** The value axis's labels, short: a unit other than a count needs its own. */
  formatAxis?: (value: number) => string
  variant?: 'line' | 'step'
  height?: number
}) {
  const { t } = useTranslation()
  const [ref, width] = useWidth()
  const folded = data ? { ...data, series: foldSeries(data.series, MAX_SERIES, t('charts.other')) } : undefined
  const shown: FigureStatus = status === 'ready' && (!folded || !folded.buckets.length || !folded.series.length) ? 'empty' : status

  let plot = null
  let legend: LegendEntry[] = []
  if (folded && folded.buckets.length && folded.series.length) {
    const many = folded.series.length > 1
    const labelled = many && width >= 480
    const tops = peaks(folded)
    const frame = timeFrame(folded, tops, width, height, labelled ? LABEL_ROOM : 8)
    const runs = unreportedRuns(tops)
    const ink = (i: number): Ink => (many ? SERIES_INK[i] : 'ink-6')
    const step = variant === 'step'
    const at = (i: number) => (step ? frame.start(i) + frame.slot / 2 : frame.centre(i))
    const points = (values: Reported[]): Point[] =>
      step
        ? values.flatMap((value, i): Point[] => [
            [frame.start(i), value],
            [frame.start(i + 1), value],
          ])
        : values.map((value, i): Point => [frame.centre(i), value])
    const y = (value: Reported) => frame.y(value ?? 0)
    const path = line<Point>()
      .defined((point) => point[1] !== null)
      .x((point) => point[0])
      .y((point) => y(point[1]))
    const fill = area<Point>()
      .defined((point) => point[1] !== null)
      .x((point) => point[0])
      .y0(frame.y(0))
      .y1((point) => y(point[1]))
    const describe = (value: Reported) => (value === null ? t('charts.notReported') : formatValue(value))
    const marks = bucketMarks(
      folded,
      at,
      (i) => (tops[i] === null ? (frame.top + frame.bottom) / 2 : frame.y(tops[i] ?? 0)),
      describe,
    )
    const ends = labelled
      ? spread(
          folded.series.flatMap((series) => {
            const last = series.values.reduce<number>((found, value, i) => (value === null ? found : i), -1)
            return last < 0 ? [] : [{ id: series.id, y: frame.y(series.values[last] ?? 0) }]
          }),
          frame.top,
          frame.bottom,
        )
      : new Map<string, number>()
    legend = [
      ...(many
        ? folded.series.map((series, i) => ({ key: series.id, label: series.name, swatch: ink(i), kind: 'line' as const }))
        : []),
      ...(runs.length ? [{ key: 'hatch', label: t('charts.notReported'), swatch: 'hatch' as const }] : []),
    ]
    plot = (
      <div ref={ref} className="min-w-0">
        <Plot
          crosshair={(i) => ({ x: at(i), top: frame.top, bottom: frame.bottom })}
          height={height}
          label={title}
          marks={marks}
          pick={(px) => {
            const i = Math.floor((px - frame.left) / frame.slot)
            return i >= 0 && i < folded.buckets.length ? i : null
          }}
          width={width}
        >
          {({ active, mark, hatch }) => (
            <>
              <HorizontalGrid format={formatAxis} left={frame.left} right={frame.right} ticks={frame.ticks} y={frame.y} />
              {runs.map(([first, last]) => (
                <NotReportedBand
                  key={first}
                  height={frame.bottom - frame.top}
                  pattern={hatch}
                  width={(last - first + 1) * frame.slot}
                  x={frame.start(first)}
                  y={frame.top}
                />
              ))}
              {/* The first series draws last, on top; Other and context lines sit behind. */}
              {[...folded.series.entries()].reverse().map(([s, series]) => {
                const pts = points(series.values)
                return (
                  <g key={series.id}>
                    {step ? <path className={FILL[ink(s)]} d={fill(pts) ?? ''} fillOpacity={AREA_OPACITY} /> : null}
                    <path
                      className={STROKE[ink(s)]}
                      d={path(pts) ?? ''}
                      fill="none"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={LINE}
                    />
                    {series.values.map((value, i) => {
                      const alone =
                        value !== null && (series.values[i - 1] ?? null) === null && (series.values[i + 1] ?? null) === null
                      return (alone && !step) || (active === i && value !== null) ? (
                        <circle
                          key={i}
                          className={`${FILL[ink(s)]} stroke-working-paper`}
                          cx={at(i)}
                          cy={y(value)}
                          r={DOT / 2}
                          strokeWidth={RING}
                        />
                      ) : null
                    })}
                    {ends.has(series.id) ? (
                      <text
                        className="fill-secondary-ink text-counter"
                        data-figure-data=""
                        dominantBaseline="middle"
                        x={frame.right + 8}
                        y={ends.get(series.id)}
                      >
                        {series.name.length > 14 ? `${series.name.slice(0, 13)}…` : series.name}
                      </text>
                    ) : null}
                  </g>
                )
              })}
              {folded.buckets.map((bucket, i) => (
                <rect
                  key={bucket.toISOString()}
                  {...mark(i)}
                  fill="transparent"
                  height={frame.bottom - frame.top}
                  width={frame.slot}
                  x={frame.start(i)}
                  y={frame.top}
                />
              ))}
              <TimeLabels labels={frame.labels} y={frame.bottom + 15} />
            </>
          )}
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
