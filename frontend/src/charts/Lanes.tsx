import { scaleTime } from 'd3-scale'
import { useTranslation } from 'react-i18next'
import { BREAK, compressedScale } from './compressed'
import { Figure, type FigureStatus } from './Figure'
import { formatDay, formatMoment, formatTick } from './format'
import { crisp, DOT, HAIRLINE, labelWidth, LINE, RING } from './geometry'
import { FILL } from './ink'
import { rowNeighbours } from './navigation'
import { Plot, type Mark } from './Plot'
import { useDuration } from './useDuration'
import { useWidth } from './useWidth'

export type LaneItem = { id: string; label: string; start: Date; end?: Date }
export type Lane = { id: string; name: string; items: LaneItem[] }

const LANE = 32
const TOP = 20
const AXIS = 30
const SPAN = 12
const HOUR = 3_600_000

type Placed = { lane: number; item: LaneItem; x0: number; x1: number; y: number }

/**
 * Lanes on one time axis: a span for each stretch of work, a dot for each moment.
 * Idle gaps longer than the threshold are squeezed into a marked break, so several
 * days still fit; the "now" line is in the planning thread.
 */
export function Lanes({
  title,
  caption,
  lanes,
  now,
  threshold = 30 * 60_000,
  tickSpacing = 110,
  status = 'ready',
  onRetry,
  rowHeader,
  emptyTitle,
  emptyBody,
}: {
  title: string
  caption?: string
  emptyTitle?: string
  emptyBody?: string
  lanes?: Lane[]
  now?: Date
  /** Idle gaps longer than this, in milliseconds, are compressed. */
  threshold?: number
  /** About one time label per this many pixels of a stretch; wider spacing gives weekly days over a month. */
  tickSpacing?: number
  status?: FigureStatus
  onRetry?: () => void
  rowHeader: string
}) {
  const { t } = useTranslation()
  const duration = useDuration()
  const [ref, width] = useWidth()
  const rows = (lanes ?? []).filter((lane) => lane.items.length)
  const shown: FigureStatus = status === 'ready' && !rows.length ? 'empty' : status
  const height = TOP + (rows.length || 3) * LANE + AXIS

  let plot = null
  let gapRows: { key: string; header: string; cells: string[] }[] = []
  if (rows.length) {
    const left = Math.min(Math.max(...rows.map((lane) => labelWidth(lane.name)), 64), Math.max(64, Math.floor(width * 0.3)))
    const right = width - 12
    const bottom = TOP + rows.length * LANE
    const times: [number, number][] = rows.flatMap((lane) =>
      lane.items.map((item): [number, number] => [item.start.getTime(), (item.end ?? item.start).getTime()]),
    )
    if (now) times.push([now.getTime(), now.getTime()])
    const x = compressedScale(times, threshold, [left + DOT, right - DOT])
    const placed: Placed[] = rows.flatMap((lane, l) =>
      [...lane.items]
        .sort((a, b) => a.start.getTime() - b.start.getTime())
        .map((item) => ({
          lane: l,
          item,
          x0: x(item.start.getTime()),
          x1: x((item.end ?? item.start).getTime()),
          y: TOP + l * LANE + LANE / 2,
        })),
    )
    const describe = (p: Placed): Mark => {
      const lane = rows[p.lane].name
      const end = p.item.end
      return {
        x: end ? (p.x0 + p.x1) / 2 : p.x0,
        y: p.y,
        value: p.item.label,
        name: end
          ? t('charts.lanes.span', {
              lane,
              from: formatMoment(p.item.start),
              to: formatMoment(end),
              duration: duration(end.getTime() - p.item.start.getTime()),
            })
          : t('charts.lanes.moment', { lane, at: formatMoment(p.item.start) }),
      }
    }
    const marks: Mark[] = [
      ...placed.map(describe),
      ...x.gaps.map((gap) => ({
        x: gap.x,
        y: bottom,
        value: t('charts.lanes.gap', { duration: duration(gap.to - gap.from) }),
        name: t('charts.lanes.gapName', { from: formatMoment(new Date(gap.from)), to: formatMoment(new Date(gap.to)) }),
      })),
    ]
    // Up and down move to the nearest mark in time on the lane above or below.
    const navigate = rowNeighbours(
      placed.map((p) => p.lane),
      placed.map((_, i) => marks[i].x),
    )
    const pick = (px: number, py: number) => {
      if (py > bottom) {
        const gap = x.gaps.findIndex((g) => Math.abs(g.x - px) <= BREAK)
        return gap >= 0 ? placed.length + gap : null
      }
      const lane = Math.floor((py - TOP) / LANE)
      let best: number | null = null
      let distance = 12
      placed.forEach((p, i) => {
        if (p.lane !== lane) return
        const d = px < p.x0 ? p.x0 - px : px > p.x1 ? px - p.x1 : 0
        if (d <= distance) {
          distance = d
          best = i
        }
      })
      return best
    }
    gapRows = x.gaps.map((gap) => ({
      key: `gap-${gap.from}`,
      header: t('charts.lanes.gapRow'),
      cells: ['', formatMoment(new Date(gap.from)), formatMoment(new Date(gap.to)), duration(gap.to - gap.from)],
    }))
    const candidates = x.stretches.flatMap((stretch, s) => {
      const span = stretch.right - stretch.left
      // A stretch on a new day is labelled with the day, one later the same day with its time.
      const start = new Date(stretch.from)
      const previous = s > 0 ? new Date(x.stretches[s - 1].to) : null
      const newDay = !previous || previous.toDateString() !== start.toDateString()
      const first = { key: `s${s}`, x: stretch.left, at: start, day: newDay }
      const inner = scaleTime()
        .domain([new Date(stretch.from), new Date(stretch.to)])
        .ticks(Math.max(0, Math.floor(span / tickSpacing) - 1))
        .map((at) => ({ key: `s${s}-${at.getTime()}`, x: x(at.getTime()), at, day: false }))
        .filter((tick) => tick.x - stretch.left >= 100 && stretch.right - tick.x >= 24)
      return s === 0 || span >= 44 ? [first, ...inner] : inner
    })
    // A label names its day whenever the day differs from the label before it, so no time
    // is read against the wrong day; one that would touch the label before it is dropped.
    const ticks = candidates.reduce<{ key: string; x: number; at: Date; text: string }[]>((kept, tick) => {
      const last = kept[kept.length - 1]
      const day = tick.day || !last || last.at.toDateString() !== tick.at.toDateString()
      const text = day ? formatDay(tick.at) : formatTick(tick.at, HOUR)
      return !last || tick.x - last.x >= labelWidth(last.text) + 12 ? [...kept, { key: tick.key, x: tick.x, at: tick.at, text }] : kept
    }, [])

    plot = (
      <div ref={ref} className="min-w-0">
        <Plot
          crosshair={(i) => (i < placed.length ? { x: marks[i].x, top: TOP, bottom } : null)}
          height={height}
          label={title}
          marks={marks}
          navigate={navigate}
          pick={pick}
          width={width}
        >
          {({ active, mark }) => (
            <>
              <g aria-hidden="true">
                {rows.map((lane, l) => (
                  <g key={lane.id}>
                    <line
                      className="stroke-hairline"
                      strokeWidth={HAIRLINE}
                      x1={left}
                      x2={right}
                      y1={crisp(TOP + (l + 1) * LANE)}
                      y2={crisp(TOP + (l + 1) * LANE)}
                    />
                    <text
                      className="fill-primary-ink text-counter"
                      data-figure-data=""
                      dominantBaseline="middle"
                      textAnchor="end"
                      x={left - 8}
                      y={TOP + l * LANE + LANE / 2}
                    >
                      {lane.name.length > 22 ? `${lane.name.slice(0, 21)}…` : lane.name}
                    </text>
                  </g>
                ))}
                {x.stretches.map((stretch, s) => (
                  <line
                    key={s}
                    className="stroke-strong-rule"
                    strokeWidth={HAIRLINE}
                    x1={stretch.left}
                    x2={stretch.right}
                    y1={crisp(bottom)}
                    y2={crisp(bottom)}
                  />
                ))}
                <g data-figure-data="">
                  {ticks.map((tick) => (
                    <text key={tick.key} className="fill-secondary-ink text-counter tabular-nums" x={tick.x} y={bottom + 16}>
                      {tick.text}
                    </text>
                  ))}
                </g>
              </g>
              {x.gaps.map((gap, g) => (
                <g key={gap.from} {...mark(placed.length + g)}>
                  <rect className="fill-canvas-mist" height={bottom - TOP} width={BREAK - 4} x={gap.x - (BREAK - 4) / 2} y={TOP} />
                  {/* The break in the axis: two slanted strokes. */}
                  {[-3, 3].map((dx) => (
                    <line
                      key={dx}
                      className={active === placed.length + g ? 'stroke-primary-ink' : 'stroke-strong-rule'}
                      strokeWidth={HAIRLINE}
                      x1={gap.x + dx - 3}
                      x2={gap.x + dx + 3}
                      y1={bottom + 5}
                      y2={bottom - 5}
                    />
                  ))}
                </g>
              ))}
              {/* The now line sits under the marks, so a session live now keeps its ring on it. */}
              {now ? (
                <g aria-hidden="true">
                  <line
                    className="stroke-planning-thread"
                    strokeWidth={LINE}
                    x1={x(now.getTime())}
                    x2={x(now.getTime())}
                    y1={TOP - 4}
                    y2={bottom}
                  />
                  <text className="fill-planning-thread text-counter font-medium" textAnchor="middle" x={x(now.getTime())} y={TOP - 8}>
                    {t('charts.lanes.now')}
                  </text>
                </g>
              ) : null}
              {placed.map((p, i) =>
                p.item.end ? (
                  <rect
                    key={`${p.lane}-${p.item.id}`}
                    {...mark(i)}
                    className={active === i ? FILL['mark-hover'] : FILL.mark}
                    height={SPAN}
                    rx={2}
                    width={Math.max(3, p.x1 - p.x0)}
                    x={p.x0}
                    y={p.y - SPAN / 2}
                  />
                ) : (
                  <circle
                    key={`${p.lane}-${p.item.id}`}
                    {...mark(i)}
                    className={`${active === i ? FILL['mark-hover'] : FILL['ink-7']} stroke-working-paper`}
                    cx={p.x0}
                    cy={p.y}
                    r={DOT / 2 + (active === i ? 1 : 0)}
                    strokeWidth={RING}
                  />
                ),
              )}
            </>
          )}
        </Plot>
      </div>
    )
  }

  return (
    <Figure
      caption={caption}
      emptyBody={emptyBody}
      emptyTitle={emptyTitle}
      height={height}
      status={shown}
      table={
        rows.length
          ? {
              rowHeader,
              columns: [
                { key: 'what', label: t('charts.lanes.what') },
                { key: 'start', label: t('charts.lanes.start') },
                { key: 'end', label: t('charts.lanes.end') },
                { key: 'duration', label: t('charts.lanes.duration') },
              ],
              rows: rows.flatMap((lane) =>
                [...lane.items]
                  .sort((a, b) => a.start.getTime() - b.start.getTime())
                  .map((item) => ({
                    key: `${lane.id}-${item.id}`,
                    header: lane.name,
                    cells: [
                      item.label,
                      formatMoment(item.start),
                      item.end ? formatMoment(item.end) : t('charts.lanes.instant'),
                      item.end ? duration(item.end.getTime() - item.start.getTime()) : t('charts.lanes.instant'),
                    ],
                  })),
              ).concat(gapRows),
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
