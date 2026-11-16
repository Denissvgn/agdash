import { useQuery } from '@tanstack/react-query'
import { useId, useMemo, useRef, type KeyboardEvent, type PointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { getJson } from '../api/client'
import type { SessionDetail, SessionOverview, StateHistory } from '../api/types'
import { Figure, type FigureStatus } from '../charts'
import type { LegendEntry } from '../charts/Figure'
import { BREAK, compressedScale } from '../charts/compressed'
import { formatCompact, formatDay, formatMoment, formatTick, formatValue } from '../charts/format'
import { crisp, DOT, GAP, HAIRLINE, RING, svgId, verticalBar } from '../charts/geometry'
import { NotReportedBand } from '../charts/Hatch'
import { FILL } from '../charts/ink'
import { Plot, type Mark } from '../charts/Plot'
import { UnknownPattern } from '../charts/StateStrips'
import { stateBlock, STATE_FORMS, STRIP_STATES } from '../charts/states'
import { useWidth } from '../charts/useWidth'
import { overviewModel, type OverviewModel } from './overviewModel'

/** The lane names' column; narrower on a phone, so the drawing keeps its width. */
const leftFor = (width: number) => (width < 520 ? 84 : 112)
const LANE = 20
const TOKENS = 36
const TOP = 6
const AXIS = 26
const PART_SPACING = 4
const LANES = ['prompts', 'replies', 'thoughts', 'tools', 'subagents', 'models', 'compactions', 'state', 'tokens'] as const
type Lane = (typeof LANES)[number]
const HOUR = 3_600_000

const zone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
const laneTop = (lane: Lane) => TOP + LANES.indexOf(lane) * LANE
const laneHeight = (lane: Lane) => (lane === 'tokens' ? TOKENS : LANE)

/** The texture for reasoning not kept and for failed tool calls: mark-ink lines at 135°. */
function PartPattern({ id }: { id: string }) {
  return (
    <pattern height={PART_SPACING} id={id} patternTransform="rotate(-45)" patternUnits="userSpaceOnUse" width={PART_SPACING}>
      <line className="stroke-figure-mark" strokeWidth={HAIRLINE} x1={0.5} x2={0.5} y1={0} y2={PART_SPACING} />
    </pattern>
  )
}

/** A column in a count lane, with an optional part at its top in the part texture. */
function Column({ x0, x1, lane, share, part, pattern }: { x0: number; x1: number; lane: Lane; share: number; part: number; pattern: string }) {
  if (share <= 0) return null
  const base = laneTop(lane) + LANE - 3
  const tall = Math.max(2, Math.round(share * (LANE - 6)))
  const width = Math.max(1, x1 - x0)
  const partTall = part > 0 ? Math.max(1, Math.round(tall * part)) : 0
  return (
    <g>
      {tall - partTall > 0 ? (
        <path className={FILL.mark} d={verticalBar(x0, base, base - (tall - partTall), width, partTall === 0)} />
      ) : null}
      {partTall > 0 ? (
        <>
          <path className={FILL['ink-2']} d={verticalBar(x0, base - (tall - partTall), base - tall, width)} />
          <path d={verticalBar(x0, base - (tall - partTall), base - tall, width)} fill={`url(#${pattern})`} />
        </>
      ) : null}
    </g>
  )
}

/**
 * The whole session above its timeline: lanes for prompts, replies, thoughts, tool
 * calls, subagents, model changes, compactions, state and tokens per turn, on one
 * time axis with long idle gaps compressed. A planning-thread window marks the part
 * of the list in view; moving it scrolls the list there.
 */
export function SessionOverviewFigure({
  model,
  view,
  onSeek,
  status = 'ready',
  onRetry,
}: {
  model: OverviewModel | null
  /** The times of the first and last events in view in the list. */
  view: [number, number] | null
  /** Scroll the list to the first event at or after this time. */
  onSeek: (at: number) => void
  status?: FigureStatus
  onRetry?: () => void
}) {
  const { t } = useTranslation()
  const [ref, width] = useWidth()
  const ids = svgId(useId())
  const drag = useRef(false)
  const height = TOP + LANES.reduce((sum, lane) => sum + laneHeight(lane), 0) + AXIS
  const word = (state: string) => t(`charts.states.${state}`)

  const legend: LegendEntry[] = [
    {
      key: 'part',
      label: t('session.overview.partKey'),
      swatch: {
        draw: () => (
          <>
            <defs>
              <PartPattern id={`${ids}-legend-part`} />
            </defs>
            <rect className={FILL['ink-2']} height={12} rx={2} width={12} />
            <rect fill={`url(#${ids}-legend-part)`} height={12} rx={2} width={12} />
          </>
        ),
      },
    },
    { key: 'hatch', label: t('charts.notReported'), swatch: 'hatch' },
    ...STRIP_STATES.map((state) => ({
      key: state,
      label: word(state),
      swatch: {
        draw: () => {
          const block = stateBlock(state, 1, 11, 12, 12)
          const form = STATE_FORMS[state]
          return (
            <>
              <defs>
                <UnknownPattern id={`${ids}-legend-${state}`} />
              </defs>
              <rect
                className={form.hatched ? undefined : form.fill}
                fill={form.hatched ? `url(#${ids}-legend-${state})` : undefined}
                height={block.height}
                width={form.cap ?? 10}
                x={form.cap ? 5 : 1}
                y={block.y}
              />
            </>
          )
        },
      },
    })),
  ]

  const drawing = useMemo(() => {
    if (!model) return null
    const LEFT = leftFor(width)
    const right = Math.max(LEFT + 40, width - 8)
    const x = compressedScale(model.busy, model.threshold, [LEFT, right])
    const columns = (from: number, to: number) => [x(from), Math.max(x(from) + 1, x(to) - GAP)] as const
    const marks: Mark[] = model.buckets.map((bucket) => {
      const turns = model.turns.filter((turn) => turn.start >= bucket.start && turn.start < bucket.end)
      const tokens = turns.reduce<number | null>((sum, turn) => (turn.tokens === null ? sum : (sum ?? 0) + turn.tokens), null)
      return {
        x: (x(bucket.start) + x(bucket.end)) / 2,
        y: TOP,
        value: '',
        name: formatMoment(new Date(bucket.start)),
        rows: [
          { name: t('session.overview.lanes.prompts'), value: formatValue(bucket.prompts) },
          { name: t('session.overview.lanes.replies'), value: formatValue(bucket.replies) },
          {
            name: t('session.overview.thoughts'),
            value: t('session.overview.thoughtsValue', { count: bucket.thoughtsKept + bucket.thoughtsNotKept, kept: bucket.thoughtsKept }),
          },
          {
            name: t('session.overview.lanes.tools'),
            value: t('session.overview.toolsValue', { count: bucket.toolCalls, errors: bucket.toolErrors }),
          },
          ...(turns.length
            ? [{ name: t('session.overview.lanes.tokens'), value: tokens === null ? t('charts.notReported') : formatValue(tokens) }]
            : []),
        ],
      }
    })
    const bucketAt = (px: number) => {
      const found = model.buckets.findIndex((bucket) => px >= x(bucket.start) && px <= x(bucket.end))
      return found < 0 ? null : found
    }
    const ticks = x.stretches.flatMap((stretch, s) => {
      const span = stretch.right - stretch.left
      const from = new Date(stretch.from)
      const previous = s > 0 ? new Date(x.stretches[s - 1].to) : null
      const newDay = !previous || previous.toDateString() !== from.toDateString()
      return s === 0 || span >= 44 ? [{ key: `s${s}`, x: stretch.left, text: newDay ? formatDay(from) : formatTick(from, HOUR) }] : []
    })
    return { x, right, marks, bucketAt, columns, ticks, LEFT }
  }, [model, width, t])

  let plot = null
  if (model && drawing) {
    const { x, right, marks, bucketAt, columns, ticks, LEFT } = drawing
    const bottom = height - AXIS
    const [view0, view1] = view ?? [model.start, model.start]
    const wx0 = x(view0)
    const wx1 = Math.max(wx0 + 4, x(view1))
    const seekAt = (event: PointerEvent<HTMLElement>, offset = 0) => {
      const box = event.currentTarget.closest('[data-overview]')!.getBoundingClientRect()
      onSeek(x.invert(event.clientX - box.left - offset))
    }
    const keys = (event: KeyboardEvent<HTMLDivElement>) => {
      const steps: Record<string, number> = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1, PageDown: -10, PageUp: 10 }
      const at = model.buckets.findIndex((bucket) => bucket.end > view0)
      const current = at < 0 ? model.buckets.length - 1 : at
      let next: number | null = null
      if (event.key in steps) next = Math.max(0, Math.min(model.buckets.length - 1, current + steps[event.key]))
      else if (event.key === 'Home') next = 0
      else if (event.key === 'End') next = model.buckets.length - 1
      if (next === null) return
      event.preventDefault()
      onSeek(model.buckets[next].start)
    }

    plot = (
      <div ref={ref} className="relative min-w-0" data-overview="">
        {/* A press on the drawing moves the list to that moment. */}
        <div
          onPointerUp={(event) => {
            if (!drag.current && event.button === 0) seekAt(event)
          }}
        >
          <Plot
            crosshair={(i) => ({ x: marks[i].x, top: TOP, bottom })}
            height={height}
            label={t('session.overview.title')}
            marks={marks}
            pick={(px) => bucketAt(px)}
            width={drawing.right + 8}
          >
            {({ mark, hatch }) => (
              <>
                <defs>
                  <PartPattern id={`${ids}-part`} />
                  <UnknownPattern id={`${ids}-unknown`} />
                </defs>
                {/* The part of the session in view in the list, behind the marks. */}
                <rect className="fill-planning-thread-soft" height={bottom - TOP} width={wx1 - wx0} x={wx0} y={TOP} />
                <g aria-hidden="true">
                  {LANES.map((lane) => (
                    <g key={lane}>
                      <text className="fill-secondary-ink text-counter" dominantBaseline="middle" x={0} y={laneTop(lane) + (lane === 'tokens' ? 10 : laneHeight(lane) / 2)}>
                        {t(`session.overview.lanes.${lane}`)}
                      </text>
                      <line
                        className="stroke-hairline"
                        strokeWidth={HAIRLINE}
                        x1={LEFT}
                        x2={right}
                        y1={crisp(laneTop(lane) + laneHeight(lane))}
                        y2={crisp(laneTop(lane) + laneHeight(lane))}
                      />
                    </g>
                  ))}
                  {x.gaps.map((gap) => (
                    <g key={gap.from} data-gap={`${gap.from}-${gap.to}`}>
                      <rect className="fill-canvas-mist" height={bottom - TOP} width={BREAK - 4} x={gap.x - (BREAK - 4) / 2} y={TOP} />
                      {[-3, 3].map((dx) => (
                        <line key={dx} className="stroke-strong-rule" strokeWidth={HAIRLINE} x1={gap.x + dx - 3} x2={gap.x + dx + 3} y1={bottom + 5} y2={bottom - 5} />
                      ))}
                    </g>
                  ))}
                  {x.stretches.map((stretch, s) => (
                    <line key={s} className="stroke-strong-rule" strokeWidth={HAIRLINE} x1={stretch.left} x2={stretch.right} y1={crisp(bottom)} y2={crisp(bottom)} />
                  ))}
                  <g data-figure-data="">
                    {ticks.map((tick) => (
                      <text key={tick.key} className="fill-secondary-ink text-counter tabular-nums" x={tick.x} y={bottom + 16}>
                        {tick.text}
                      </text>
                    ))}
                  </g>
                </g>
                <g aria-hidden="true" data-lane="counts">
                  {model.buckets.map((bucket) => {
                    const [x0, x1] = columns(bucket.start, bucket.end)
                    const thoughts = bucket.thoughtsKept + bucket.thoughtsNotKept
                    return (
                      <g key={bucket.start}>
                        <Column lane="prompts" part={0} pattern={`${ids}-part`} share={bucket.prompts / model.max.prompts} x0={x0} x1={x1} />
                        <Column lane="replies" part={0} pattern={`${ids}-part`} share={bucket.replies / model.max.replies} x0={x0} x1={x1} />
                        <Column
                          lane="thoughts"
                          part={thoughts ? bucket.thoughtsNotKept / thoughts : 0}
                          pattern={`${ids}-part`}
                          share={thoughts / model.max.thoughts}
                          x0={x0}
                          x1={x1}
                        />
                        <Column
                          lane="tools"
                          part={bucket.toolCalls ? bucket.toolErrors / bucket.toolCalls : 0}
                          pattern={`${ids}-part`}
                          share={bucket.toolCalls / model.max.toolCalls}
                          x0={x0}
                          x1={x1}
                        />
                      </g>
                    )
                  })}
                </g>
                <g aria-hidden="true" data-lane="subagents">
                  {model.subagents.map((agent, i) => (
                    <rect
                      key={agent.id}
                      className={FILL.mark}
                      height={6}
                      rx={2}
                      width={Math.max(2, x(agent.end) - x(agent.start))}
                      x={x(agent.start)}
                      y={laneTop('subagents') + 3 + (i % 2) * 8}
                    />
                  ))}
                </g>
                <g aria-hidden="true" data-lane="markers">
                  {model.markers.map((marker, i) =>
                    marker.kind === 'model_change' ? (
                      <circle
                        key={`m${i}`}
                        className={`${FILL['ink-7']} stroke-working-paper`}
                        cx={x(marker.at)}
                        cy={laneTop('models') + LANE / 2}
                        r={DOT / 2}
                        strokeWidth={RING}
                      />
                    ) : (
                      <rect
                        key={`c${i}`}
                        className={`${FILL['ink-7']} stroke-working-paper`}
                        height={DOT}
                        strokeWidth={RING}
                        width={DOT}
                        x={x(marker.at) - DOT / 2}
                        y={laneTop('compactions') + (LANE - DOT) / 2}
                      />
                    ),
                  )}
                </g>
                <g aria-hidden="true" data-lane="state">
                  {model.states.map((span, i) => {
                    const following = model.states[i + 1]
                    const block = stateBlock(span.state, x(span.start), x(span.end) - (following ? GAP : 0), laneTop('state') + LANE - 3, LANE - 6)
                    return (
                      <rect
                        key={span.start}
                        className={block.form.hatched ? undefined : block.form.fill}
                        data-state={span.state}
                        fill={block.form.hatched ? `url(#${ids}-unknown)` : undefined}
                        height={block.height}
                        width={block.width}
                        x={block.x}
                        y={block.y}
                      />
                    )
                  })}
                </g>
                <g aria-hidden="true" data-lane="tokens">
                  {model.turns.map((turn) => {
                    // A turn that runs into a compressed gap stops where the gap begins.
                    const gap = x.gaps.find((g) => g.from >= turn.start && g.from < turn.end)
                    const [x0, x1] = columns(turn.start, gap ? gap.from : turn.end)
                    const base = laneTop('tokens') + TOKENS - 3
                    return turn.tokens === null ? (
                      <g key={turn.start} data-turn="unreported">
                        <NotReportedBand height={TOKENS - 6} labelled={false} pattern={hatch} width={x1 - x0} x={x0} y={base - (TOKENS - 6)} />
                      </g>
                    ) : (
                      <path
                        key={turn.start}
                        className={FILL.mark}
                        d={verticalBar(x0, base, base - Math.max(2, Math.round((turn.tokens / model.max.tokens) * (TOKENS - 6))), x1 - x0)}
                        data-turn={turn.tokens}
                      />
                    )
                  })}
                  {/* The lane's scale, under its name, clear of the columns. */}
                  <text className="fill-secondary-ink text-counter tabular-nums" data-figure-data="" x={0} y={laneTop('tokens') + TOKENS - 4}>
                    {t('session.overview.upTo', { value: formatCompact(model.max.tokens) })}
                  </text>
                </g>
                <rect
                  className="stroke-planning-thread"
                  fill="none"
                  height={bottom - TOP}
                  strokeWidth={HAIRLINE * 2}
                  width={wx1 - wx0}
                  x={wx0}
                  y={TOP}
                />
                {model.buckets.map((bucket, i) => (
                  <rect key={bucket.start} {...mark(i)} fill="transparent" height={bottom - TOP} width={Math.max(1, x(bucket.end) - x(bucket.start))} x={x(bucket.start)} y={TOP} />
                ))}
              </>
            )}
          </Plot>
        </div>
        <div
          aria-label={t('session.overview.window')}
          aria-valuemax={model.end}
          aria-valuemin={model.start}
          aria-valuenow={Math.round(view0)}
          aria-valuetext={t('session.overview.windowValue', { from: formatMoment(new Date(view0)), to: formatMoment(new Date(view1)) })}
          className="absolute cursor-grab touch-none rounded-compact outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-planning-thread"
          role="slider"
          style={{ left: wx0, top: TOP, width: wx1 - wx0, height: height - AXIS - TOP }}
          tabIndex={0}
          onKeyDown={keys}
          onPointerDown={(event) => {
            drag.current = true
            event.currentTarget.setPointerCapture?.(event.pointerId)
            event.stopPropagation()
          }}
          onPointerMove={(event) => {
            if (drag.current) seekAt(event, (wx1 - wx0) / 2)
          }}
          onPointerUp={(event) => {
            drag.current = false
            event.stopPropagation()
          }}
        />
      </div>
    )
  }

  return (
    <Figure
      caption={t('session.overview.caption')}
      height={height}
      legend={legend}
      status={status}
      table={model ? overviewTable(model, t) : undefined}
      title={t('session.overview.title')}
      onRetry={onRetry}
    >
      {plot}
    </Figure>
  )
}

/** The overview's twin: one row per bucket, the same counts the lanes draw. */
function overviewTable(model: OverviewModel, t: (key: string, options?: Record<string, unknown>) => string) {
  const stateAt = (at: number) => model.states.find((span) => span.start <= at && at < span.end)?.state
  return {
    rowHeader: t('session.overview.table.when'),
    columns: [
      'prompts',
      'replies',
      'thoughts',
      'notKept',
      'tools',
      'errors',
      'subagents',
      'models',
      'compactions',
      'tokens',
      'state',
    ].map((key) => ({ key, label: t(`session.overview.table.${key}`) })),
    rows: model.buckets.map((bucket) => {
      const inside = (at: number) => at >= bucket.start && at < bucket.end
      const turns = model.turns.filter((turn) => inside(turn.start))
      const tokens = turns.reduce<number | null>((sum, turn) => (turn.tokens === null ? sum : (sum ?? 0) + turn.tokens), null)
      const state = stateAt(bucket.start)
      return {
        key: String(bucket.start),
        header: formatMoment(new Date(bucket.start)),
        cells: [
          formatValue(bucket.prompts),
          formatValue(bucket.replies),
          formatValue(bucket.thoughtsKept + bucket.thoughtsNotKept),
          formatValue(bucket.thoughtsNotKept),
          formatValue(bucket.toolCalls),
          formatValue(bucket.toolErrors),
          formatValue(model.subagents.filter((agent) => inside(agent.start)).length),
          formatValue(model.markers.filter((marker) => marker.kind === 'model_change' && inside(marker.at)).length),
          formatValue(model.markers.filter((marker) => marker.kind === 'compaction' && inside(marker.at)).length),
          turns.length ? (tokens === null ? null : formatValue(tokens)) : '',
          state ? t(`charts.states.${state}`) : '',
        ],
      }
    }),
  }
}

/** The overview of a session, fetched, above its timeline. */
export function SessionOverview({
  session,
  view,
  onSeek,
}: {
  session: SessionDetail
  view: [number, number] | null
  onSeek: (at: number) => void
}) {
  const timeZone = zone()
  const overview = useQuery({
    queryKey: ['session-overview', session.id, timeZone],
    queryFn: ({ signal }) => getJson<SessionOverview>(`/api/sessions/${session.id}/overview`, { time_zone: timeZone }, signal),
  })
  const states = useQuery({
    queryKey: ['session-states', session.id],
    queryFn: ({ signal }) => getJson<StateHistory>(`/api/sessions/${session.id}/states`, {}, signal),
  })
  const model = useMemo(() => (overview.data ? overviewModel(overview.data, states.data) : null), [overview.data, states.data])
  const status: FigureStatus = overview.isError ? 'error' : overview.isPending ? 'loading' : model ? 'ready' : 'empty'
  return <SessionOverviewFigure model={model} status={status} view={view} onRetry={() => void overview.refetch()} onSeek={onSeek} />
}

