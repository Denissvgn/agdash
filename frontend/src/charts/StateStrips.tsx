import { scaleTime } from 'd3-scale'
import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import { Figure, type FigureStatus, type LegendEntry } from './Figure'
import { formatMoment, formatTick } from './format'
import { crisp, GAP, HAIRLINE, labelWidth, svgId } from './geometry'
import { rowNeighbours } from './navigation'
import { Plot, type Mark } from './Plot'
import { stateBlock, STATE_FORMS, STRIP_STATES, type StripState } from './states'
import { useDuration } from './useDuration'
import { useWidth } from './useWidth'

export type StateSegment = { state: StripState; start: Date; end: Date }
export type Strip = { id: string; name: string; segments: StateSegment[] }

const ROW = 32
const STRIP = 18
const TOP = 6
const AXIS = 26
const UNKNOWN_SPACING = 4

/** The unknown state's texture: planned-slate lines at 45°, closer than the not-reported hatch. */
export function UnknownPattern({ id }: { id: string }) {
  return (
    <pattern height={UNKNOWN_SPACING} id={id} patternTransform="rotate(45)" patternUnits="userSpaceOnUse" width={UNKNOWN_SPACING}>
      <line className="stroke-status-planned" strokeWidth={HAIRLINE} x1={0.5} x2={0.5} y1={0} y2={UNKNOWN_SPACING} />
    </pattern>
  )
}

/** A state's form at legend size. */
function StateSwatch({ state, pattern }: { state: StripState; pattern: string }) {
  const block = stateBlock(state, 1, 11, 12, 12)
  const form = STATE_FORMS[state]
  return (
    <>
      <defs>
        <UnknownPattern id={pattern} />
      </defs>
      <rect
        className={form.hatched ? undefined : form.fill}
        fill={form.hatched ? `url(#${pattern})` : undefined}
        height={block.height}
        width={form.cap ?? 10}
        x={form.cap ? 5 : 1}
        y={block.y}
      />
      {form.hatched ? <rect className="stroke-status-planned" fill="none" height={block.height - 1} strokeWidth={HAIRLINE} width={9} x={1.5} y={block.y + 0.5} /> : null}
    </>
  )
}

/**
 * One strip per session on a shared time axis, showing its state over time. Active
 * is a full block in active cyan; waiting two thirds and idle one third in slate;
 * unknown a full slate block hatched; ended a 2px violet cap. The state's word is in
 * the legend and the tooltip.
 */
export function StateStrips({
  title,
  caption,
  strips,
  status = 'ready',
  onRetry,
  rowHeader,
}: {
  title: string
  caption?: string
  strips?: Strip[]
  status?: FigureStatus
  onRetry?: () => void
  rowHeader: string
}) {
  const { t } = useTranslation()
  const duration = useDuration()
  const [ref, width] = useWidth()
  const base = svgId(useId())
  const rows = (strips ?? []).filter((strip) => strip.segments.length)
  const shown: FigureStatus = status === 'ready' && !rows.length ? 'empty' : status
  const height = TOP + (rows.length || 3) * ROW + AXIS
  const word = (state: StripState) => t(`charts.states.${state}`)
  const legend: LegendEntry[] = STRIP_STATES.map((state) => ({
    key: state,
    label: word(state),
    swatch: { draw: () => <StateSwatch pattern={`${base}-legend-${state}`} state={state} /> },
  }))

  let plot = null
  if (rows.length) {
    const left = Math.min(Math.max(...rows.map((strip) => labelWidth(strip.name)), 64), Math.max(64, Math.floor(width * 0.3)))
    const right = width - 8
    const bottom = TOP + rows.length * ROW
    const all = rows.flatMap((strip) => strip.segments)
    const first = Math.min(...all.map((segment) => segment.start.getTime()))
    const last = Math.max(...all.map((segment) => segment.end.getTime()))
    const x = scaleTime()
      .domain([new Date(first), new Date(last > first ? last : first + 60_000)])
      .range([left, right])
    const ticks = x.ticks(Math.max(2, Math.floor((right - left) / 110)))
    const step = ticks.length > 1 ? ticks[1].getTime() - ticks[0].getTime() : 3_600_000
    const placed = rows.flatMap((strip, r) =>
      strip.segments.map((segment, s) => {
        const following = strip.segments[s + 1]
        const x0 = x(segment.start)
        // Touching fills are parted by the gap; the last keeps its full length.
        const x1 = x(segment.end) - (following && following.start.getTime() <= segment.end.getTime() ? GAP : 0)
        const baseline = TOP + r * ROW + ROW / 2 + STRIP / 2
        return { row: r, strip, segment, block: stateBlock(segment.state, x0, x1, baseline, STRIP) }
      }),
    )
    const marks: Mark[] = placed.map(({ strip, segment, block }) => ({
      x: block.x + block.width / 2,
      y: block.y,
      value: word(segment.state),
      name: t('charts.strips.span', {
        strip: strip.name,
        from: formatMoment(segment.start),
        to: formatMoment(segment.end),
        duration: duration(segment.end.getTime() - segment.start.getTime()),
      }),
    }))

    plot = (
      <div ref={ref} className="min-w-0">
        <Plot
          height={height}
          label={title}
          marks={marks}
          navigate={rowNeighbours(
            placed.map((p) => p.row),
            marks.map((m) => m.x),
          )}
          pick={(px, py) => {
            const row = Math.floor((py - TOP) / ROW)
            const found = placed.findIndex(
              (p) => p.row === row && px >= p.block.x - 2 && px <= p.block.x + Math.max(p.block.width, 6) + 2,
            )
            return found >= 0 ? found : null
          }}
          width={width}
        >
          {({ active, mark }) => (
            <>
              <defs>
                <UnknownPattern id={`${base}-unknown`} />
              </defs>
              <g aria-hidden="true">
                {rows.map((strip, r) => (
                  <g key={strip.id}>
                    <line
                      className="stroke-hairline"
                      strokeWidth={HAIRLINE}
                      x1={left}
                      x2={right}
                      y1={crisp(TOP + r * ROW + ROW / 2 + STRIP / 2)}
                      y2={crisp(TOP + r * ROW + ROW / 2 + STRIP / 2)}
                    />
                    <text
                      className="fill-primary-ink text-counter"
                      data-figure-data=""
                      dominantBaseline="middle"
                      textAnchor="end"
                      x={left - 8}
                      y={TOP + r * ROW + ROW / 2}
                    >
                      {strip.name.length > 22 ? `${strip.name.slice(0, 21)}…` : strip.name}
                    </text>
                  </g>
                ))}
                <line className="stroke-strong-rule" strokeWidth={HAIRLINE} x1={left} x2={right} y1={crisp(bottom)} y2={crisp(bottom)} />
                <g data-figure-data="">
                  {ticks.map((tick) => (
                    <text key={tick.getTime()} className="fill-secondary-ink text-counter tabular-nums" textAnchor="middle" x={x(tick)} y={bottom + 16}>
                      {formatTick(tick, step)}
                    </text>
                  ))}
                </g>
              </g>
              {placed.map(({ segment, block }, i) => (
                <g key={`${i}`} {...mark(i)} data-state={segment.state}>
                  <rect
                    className={block.form.hatched ? undefined : block.form.fill}
                    fill={block.form.hatched ? `url(#${base}-unknown)` : undefined}
                    height={block.height}
                    width={block.width}
                    x={block.x}
                    y={block.y}
                  />
                  {block.form.hatched ? (
                    <rect
                      className="stroke-status-planned"
                      fill="none"
                      height={Math.max(0, block.height - 1)}
                      strokeWidth={HAIRLINE}
                      width={Math.max(0, block.width - 1)}
                      x={block.x + 0.5}
                      y={block.y + 0.5}
                    />
                  ) : null}
                  {active === i ? (
                    <rect
                      className="stroke-primary-ink"
                      fill="none"
                      height={STRIP + 3}
                      rx={2}
                      strokeWidth={HAIRLINE}
                      width={block.width + 3}
                      x={block.x - 1.5}
                      y={block.y + block.height - STRIP - 1.5}
                    />
                  ) : null}
                </g>
              ))}
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
      table={
        rows.length
          ? {
              rowHeader,
              columns: [
                { key: 'state', label: t('charts.strips.state') },
                { key: 'start', label: t('charts.lanes.start') },
                { key: 'end', label: t('charts.lanes.end') },
                { key: 'duration', label: t('charts.lanes.duration') },
              ],
              rows: rows.flatMap((strip) =>
                strip.segments.map((segment, s) => ({
                  key: `${strip.id}-${s}`,
                  header: strip.name,
                  cells: [
                    word(segment.state),
                    formatMoment(segment.start),
                    formatMoment(segment.end),
                    duration(segment.end.getTime() - segment.start.getTime()),
                  ],
                })),
              ),
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
