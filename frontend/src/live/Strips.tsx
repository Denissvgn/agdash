import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import type { LiveActivity } from '../api/types'
import { GAP } from '../charts/geometry'
import { heatStep, type HeatScale } from '../charts/heat'
import { FILL } from '../charts/ink'
import { UnknownPattern } from '../charts/StateStrips'
import { stateBlock, STATE_FORMS, STRIP_STATES, type StripState } from '../charts/states'
import { useDuration } from '../charts/useDuration'

export type LiveRow = LiveActivity['sessions'][number]

const CELL = 5
const MINUTES = 30
export const STRIP_WIDTH = MINUTES * (CELL + GAP) - GAP
const HEIGHT = 12

/** Events per minute, oldest on the left: one cell a minute in the ink ramp, darker for more. */
function EventStrip({ events, scale }: { events: number[]; scale: HeatScale }) {
  return (
    <svg aria-hidden="true" className="block shrink-0" data-strip="events" height={HEIGHT} width={STRIP_WIDTH}>
      {events.map((count, i) => (
        <rect key={i} className={FILL[heatStep(count, scale)]} height={HEIGHT} rx={1} width={CELL} x={i * (CELL + GAP)} />
      ))}
    </svg>
  )
}

/** The session's states over the same 30 minutes, by the state strips' forms. */
function StateStrip({ row, from, until }: { row: LiveRow; from: number; until: number }) {
  const pattern = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  const x = (at: string) => ((Math.min(until, Math.max(from, Date.parse(at))) - from) / (until - from)) * STRIP_WIDTH
  return (
    <svg aria-hidden="true" className="block shrink-0" data-strip="states" height={HEIGHT} width={STRIP_WIDTH}>
      <defs>
        <UnknownPattern id={`strip-${pattern}`} />
      </defs>
      <line className="stroke-hairline" x1={0} x2={STRIP_WIDTH} y1={HEIGHT - 0.5} y2={HEIGHT - 0.5} />
      {row.states.map((span, i) => {
        const following = row.states[i + 1]
        const x0 = x(span.start)
        const x1 = x(span.end) - (following ? GAP : 0)
        const block = stateBlock(span.state as StripState, x0, x1, HEIGHT, HEIGHT)
        return (
          <rect
            key={span.start}
            className={block.form.hatched ? undefined : block.form.fill}
            data-state={span.state}
            fill={block.form.hatched ? `url(#strip-${pattern})` : undefined}
            height={block.height}
            width={block.width}
            x={block.x}
            y={block.y}
          />
        )
      })}
    </svg>
  )
}

/**
 * A live session's last 30 minutes in its row: events per minute as an ink strip and
 * its states as a state strip, with the same in words for assistive technology. A
 * session that wrote nothing in the window says so rather than drawing empty cells.
 */
export function RowStrips({ row, scale, from, until }: { row: LiveRow; scale: HeatScale; from: number; until: number }) {
  const { t } = useTranslation()
  const duration = useDuration()
  const states = row.states
    .map((span) => t('live.strips.span', { state: t(`charts.states.${span.state}`), duration: duration(Date.parse(span.end) - Date.parse(span.start)) }))
    .join(t('live.strips.then'))
  return (
    <div className="flex flex-col gap-xs">
      <p className="sr-only">
        {t('live.strips.summary', { count: row.total, states: states || t('live.strips.noStates') })}
      </p>
      <div aria-hidden="true" className="flex flex-wrap items-center gap-x-md gap-y-xs text-label text-secondary-ink">
        {/* A fixed width on wider screens, so the state strips line up from row to row. */}
        <span className="flex flex-wrap items-center gap-x-sm gap-y-xs roomy:w-[calc(3.5rem+210px+7rem+16px)]">
          <span className="w-[3.5rem] shrink-0">{t('live.strips.events')}</span>
          {row.total > 0 ? (
            <>
              <EventStrip events={row.events} scale={scale} />
              <span className="data whitespace-nowrap">{t('live.strips.count', { count: row.total })}</span>
            </>
          ) : (
            <span data-strip="quiet">{t('live.strips.quiet')}</span>
          )}
        </span>
        {row.states.length > 0 ? (
          <span className="flex flex-wrap items-center gap-x-sm gap-y-xs">
            <span className="w-[3.5rem] shrink-0">{t('live.strips.states')}</span>
            <StateStrip from={from} row={row} until={until} />
          </span>
        ) : null}
      </div>
    </div>
  )
}

/** What the rows' strips mean: the window, the ink ramp for events, and the five states' forms. */
export function StripsKey({ scale }: { scale: HeatScale }) {
  const { t } = useTranslation()
  const pattern = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  return (
    <div className="flex flex-col gap-xs text-label text-secondary-ink">
      <p>{t('live.strips.key')}</p>
      <ul aria-label={t('charts.legend')} className="flex flex-wrap items-center gap-x-md gap-y-xs">
        <li className="flex items-center gap-xs">
          <svg aria-hidden="true" height={12} width={scale.steps.length * (CELL + GAP)}>
            {scale.steps.map((step, i) => (
              <rect key={step} className={FILL[step]} height={12} rx={1} width={CELL} x={i * (CELL + GAP)} />
            ))}
          </svg>
          {t('live.strips.eventsKey')}
        </li>
        {STRIP_STATES.map((state) => {
          const block = stateBlock(state, 1, 11, 12, 12)
          const form = STATE_FORMS[state]
          return (
            <li key={state} className="flex items-center gap-xs">
              <svg aria-hidden="true" height={12} width={12}>
                <defs>
                  <UnknownPattern id={`key-${pattern}-${state}`} />
                </defs>
                <rect
                  className={form.hatched ? undefined : form.fill}
                  fill={form.hatched ? `url(#key-${pattern}-${state})` : undefined}
                  height={block.height}
                  width={form.cap ?? 10}
                  x={form.cap ? 5 : 1}
                  y={block.y}
                />
              </svg>
              {t(`charts.states.${state}`)}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
