import { CircleAlert, CircleDashed } from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { HAIRLINE, HATCH_OPACITY, HATCH_SPACING, svgId } from './geometry'
import { FILL, STROKE, type Ink } from './ink'
import { TableTwin, type TableTwinData } from './TableTwin'

export type FigureStatus = 'ready' | 'loading' | 'empty' | 'error'

/** A legend entry: a block or a line in an ink step, or the not-reported hatch. */
export type LegendEntry = {
  key: string
  label: string
  swatch: Ink | 'hatch' | Swatch
  kind?: 'block' | 'line'
  dash?: string
  cap?: 'round' | 'butt'
  /** Names a form, such as how a link is known, not a series: shown even when it is the only entry. */
  form?: boolean
}

/** A legend swatch drawn by the figure itself, such as a state's form. */
export type Swatch = { draw: () => ReactNode }

type Content = { plot: ReactNode; table?: TableTwinData }

/**
 * The frame every figure sits in: a title, a caption, a legend where two or more
 * series share the figure (or where an entry names a form), the drawing, and its
 * table twin behind a disclosure.
 * While new data loads it keeps showing the last drawing; an empty or failed figure
 * says so in words.
 */
export function Figure({
  title,
  caption,
  legend = [],
  status = 'ready',
  height,
  onRetry,
  emptyTitle,
  emptyBody,
  table,
  children,
}: {
  title: string
  caption?: string
  legend?: LegendEntry[]
  status?: FigureStatus
  /** The drawing's height, which every state keeps so the page does not jump. */
  height: number
  onRetry?: () => void
  emptyTitle?: string
  emptyBody?: string
  table?: TableTwinData
  children?: ReactNode
}) {
  const { t } = useTranslation()
  const ready: Content | null = status === 'ready' && children ? { plot: children, table } : null
  // The last drawing, kept while the next one loads.
  const [held, setHeld] = useState<Content | null>(ready)
  if (ready && (ready.plot !== held?.plot || ready.table !== held?.table)) setHeld(ready)
  const shown = ready ?? (status === 'loading' ? held : null)
  const showLegend = shown && (legend.filter((entry) => entry.swatch !== 'hatch').length >= 2 || legend.some((entry) => entry.swatch === 'hatch' || entry.form))

  return (
    <figure className="flex min-w-0 flex-col gap-sm rounded-surface border border-hairline bg-working-paper p-md shadow-resting">
      <figcaption className="flex flex-col gap-xs">
        <div className="flex items-start justify-between gap-sm">
          <h3 className="text-title text-primary-ink">{title}</h3>
          {status === 'loading' && shown ? (
            <span className="shrink-0 rounded-compact bg-canvas-mist px-xs text-metadata text-secondary-ink" role="status">
              {t('charts.updating')}
            </span>
          ) : null}
        </div>
        {caption ? <p className="text-metadata text-secondary-ink">{caption}</p> : null}
      </figcaption>
      {showLegend ? <Legend entries={legend} /> : null}
      <div aria-busy={status === 'loading'} className="min-w-0" style={{ minHeight: height }}>
        {status === 'error' ? (
          <FigureNotice body={t('charts.error.body')} height={height} title={t('charts.error.title')} tone="problem">
            {onRetry ? (
              <button className="button-secondary" type="button" onClick={onRetry}>
                {t('charts.error.retry')}
              </button>
            ) : null}
          </FigureNotice>
        ) : status === 'empty' ? (
          <FigureNotice body={emptyBody ?? t('charts.empty.body')} height={height} title={emptyTitle ?? t('charts.empty.title')} />
        ) : shown ? (
          shown.plot
        ) : (
          <div
            aria-label={t('charts.loading')}
            className="rounded-surface bg-canvas-mist"
            role="status"
            style={{ height }}
          />
        )}
      </div>
      {shown?.table && status !== 'error' && status !== 'empty' ? <TableTwin {...shown.table} title={title} /> : null}
    </figure>
  )
}

function FigureNotice({
  title,
  body,
  tone = 'quiet',
  height,
  children,
}: {
  title: string
  body: string
  tone?: 'quiet' | 'problem'
  height: number
  children?: ReactNode
}) {
  const Icon = tone === 'problem' ? CircleAlert : CircleDashed
  return (
    <div
      className="flex flex-col items-start justify-center gap-sm rounded-surface bg-canvas-mist p-md"
      role={tone === 'problem' ? 'alert' : undefined}
      style={{ minHeight: height }}
    >
      <p className="flex items-center gap-sm text-label font-semibold text-primary-ink">
        <Icon
          aria-hidden="true"
          className={tone === 'problem' ? 'text-feedback-warning' : 'text-status-planned'}
          size={16}
          strokeWidth={1.75}
        />
        {title}
      </p>
      <p className="max-w-[36rem] text-metadata text-secondary-ink">{body}</p>
      {children}
    </div>
  )
}

/** Names for what the figure tells apart: ink step and label, never a hue. */
function Legend({ entries }: { entries: LegendEntry[] }) {
  const { t } = useTranslation()
  const pattern = svgId(useId())
  return (
    <ul aria-label={t('charts.legend')} className="flex flex-wrap gap-x-md gap-y-xs text-metadata text-secondary-ink">
      {entries.map((entry) => (
        <li key={entry.key} className="flex items-center gap-xs">
          <svg aria-hidden="true" className="shrink-0" height={12} width={entry.kind === 'line' ? 16 : 12}>
            {typeof entry.swatch === 'object' ? (
              entry.swatch.draw()
            ) : entry.swatch === 'hatch' ? (
              <>
                <defs>
                  <pattern height={HATCH_SPACING} id={pattern} patternTransform="rotate(45)" patternUnits="userSpaceOnUse" width={HATCH_SPACING}>
                    <line className="stroke-strong-rule" strokeOpacity={HATCH_OPACITY} strokeWidth={HAIRLINE} x1={0.5} x2={0.5} y1={0} y2={HATCH_SPACING} />
                  </pattern>
                </defs>
                <rect fill={`url(#${pattern})`} height={12} rx={2} width={12} />
              </>
            ) : entry.kind === 'line' ? (
              <line
                className={STROKE[entry.swatch]}
                strokeDasharray={entry.dash}
                strokeLinecap={entry.cap ?? (entry.dash ? 'butt' : 'round')}
                strokeWidth={2}
                x1={1}
                x2={entry.dash ? 16 : 15}
                y1={6}
                y2={6}
              />
            ) : (
              <rect className={FILL[entry.swatch]} height={12} rx={2} width={12} />
            )}
          </svg>
          <span data-figure-data="">{entry.label}</span>
        </li>
      ))}
    </ul>
  )
}
