import { useTranslation } from 'react-i18next'
import { HorizontalGrid, TimeLabels } from './Axes'
import { foldSeries, MAX_SERIES, SERIES_INK, unreportedRuns, type Reported, type TimeData } from './data'
import { Figure, type FigureStatus, type LegendEntry } from './Figure'
import { formatCompact, formatValue as formatWhole } from './format'
import { barThickness, stackSegments, verticalBar } from './geometry'
import { NotReportedBand } from './Hatch'
import { FILL } from './ink'
import { Plot } from './Plot'
import { bucketMarks, bucketTotals, timeFrame, timeTable } from './time'
import { useWidth } from './useWidth'

/**
 * Columns over time buckets. Up to three series stack, parted by a 2px gap, told
 * apart by ink step and named in the legend; past three, the rest fold into Other.
 */
export function ColumnChart({
  title,
  caption,
  data,
  status = 'ready',
  onRetry,
  rowHeader,
  formatValue = formatWhole,
  height = 220,
}: {
  title: string
  caption?: string
  data?: TimeData
  status?: FigureStatus
  onRetry?: () => void
  rowHeader: string
  formatValue?: (value: number) => string
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
    const totals = bucketTotals(folded)
    const frame = timeFrame(folded, totals, width, height)
    const thickness = barThickness(frame.slot)
    const runs = unreportedRuns(totals)
    const describe = (value: Reported) => (value === null ? t('charts.notReported') : formatValue(value))
    const marks = bucketMarks(
      folded,
      frame.centre,
      (i) => (totals[i] === null ? (frame.top + frame.bottom) / 2 : frame.y(totals[i] ?? 0)),
      describe,
    )
    if (many && marks.length) {
      marks.forEach((mark, i) => {
        mark.value = totals[i] === null ? t('charts.notReported') : formatValue(totals[i] ?? 0)
      })
    }
    legend = [
      ...(many
        ? folded.series.map((series, i) => ({ key: series.id, label: series.name, swatch: SERIES_INK[i] }))
        : []),
      ...(runs.length ? [{ key: 'hatch', label: t('charts.notReported'), swatch: 'hatch' as const }] : []),
    ]
    plot = (
      <div ref={ref} className="min-w-0">
        <Plot
          crosshair={many ? (i) => ({ x: frame.centre(i), top: frame.top, bottom: frame.bottom }) : undefined}
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
              <HorizontalGrid format={formatCompact} left={frame.left} right={frame.right} ticks={frame.ticks} y={frame.y} />
              {runs.map(([first, last]) => (
                <NotReportedBand
                  key={first}
                  height={frame.bottom - frame.top}
                  pattern={hatch}
                  width={(last - first + 1) * frame.slot - 2}
                  x={frame.start(first) + 1}
                  y={frame.top}
                />
              ))}
              {folded.buckets.map((at, i) => {
                const x = frame.centre(i) - thickness / 2
                const values = folded.series.map((series) => series.values[i])
                return (
                  <g key={at.toISOString()}>
                    {stackSegments(values, frame.y).map((segment) => (
                      <path
                        key={segment.index}
                        className={
                          many ? FILL[SERIES_INK[segment.index]] : active === i ? FILL['mark-hover'] : FILL.mark
                        }
                        d={verticalBar(x, segment.y0, segment.y1, thickness, segment.top)}
                      />
                    ))}
                    <rect {...mark(i)} fill="transparent" height={frame.bottom - frame.top} width={frame.slot} x={frame.start(i)} y={frame.top} />
                  </g>
                )
              })}
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
