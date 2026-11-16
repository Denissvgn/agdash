import { useId, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { crisp, HAIRLINE, svgId } from './geometry'
import { HatchPattern } from './Hatch'

/** One mark a person can reach: where its tooltip points, and what it says. */
export type Mark = {
  x: number
  y: number
  /** The value, first in the tooltip. */
  value: string
  /** What the value belongs to, after it. */
  name: string
  /** Extra lines, such as each series in a stacked column. */
  rows?: { value: string; name: string }[]
}

export type MarkProps = { id: string; role: 'img'; 'aria-label': string; 'data-x': number; 'data-y': number }

export type PlotState = {
  /** The mark under the pointer or keyboard focus, if any. */
  active: number | null
  /** Props that make an SVG element the accessible mark ``index``. */
  mark: (index: number) => MarkProps
  /** The id of the not-reported hatch pattern. */
  hatch: string
}

const TOOLTIP_GAP = 10

/**
 * The drawing area of one figure: one tab stop, whose arrow keys move between its
 * marks and show the same tooltip as hover. Nothing in it animates.
 */
export function Plot({
  label,
  width,
  height,
  marks,
  pick,
  stride,
  navigate,
  crosshair,
  children,
}: {
  label: string
  width: number
  height: number
  marks: Mark[]
  /** The mark nearest a pointer position in the plot, if any. */
  pick: (x: number, y: number) => number | null
  /** Up and down move this many marks, such as from one small multiple to the next. */
  stride?: number
  /** Where an arrow key goes from a mark, for figures laid out other than in a row; undefined keeps the default. */
  navigate?: (from: number, key: string) => number | null | undefined
  /** Where the crosshair runs for a mark, on figures that have one. */
  crosshair?: (index: number) => { x: number; top: number; bottom: number } | null
  children: (state: PlotState) => ReactNode
}) {
  const { t } = useTranslation()
  const base = svgId(useId())
  const [active, setActive] = useState<number | null>(null)
  const current = active !== null && active < marks.length ? active : null
  const markId = (index: number) => `${base}-mark-${index}`
  const accessible = (mark: Mark) =>
    t('charts.mark', {
      name: mark.name,
      value: mark.rows?.length ? mark.rows.map((row) => `${row.name} ${row.value}`).join(', ') : mark.value,
    })

  const move = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!marks.length) return
    const last = marks.length - 1
    const next: Record<string, (from: number) => number | null> = {
      ArrowRight: (from) => Math.min(last, from + 1),
      ArrowLeft: (from) => (from < 0 ? last : Math.max(0, from - 1)),
      ArrowDown: (from) => Math.min(last, stride ? Math.max(0, from) + stride : from + 1),
      ArrowUp: (from) => (from < 0 ? last : Math.max(0, from - (stride ?? 1))),
      Home: () => 0,
      End: () => last,
      Escape: () => null,
    }
    const step = next[event.key]
    if (!step) return
    event.preventDefault()
    setActive((value) => {
      const from = value !== null && value < marks.length ? value : -1
      const custom = from >= 0 && navigate ? navigate(from, event.key) : undefined
      return custom === undefined ? step(from) : custom
    })
  }

  const point = (event: PointerEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect()
    setActive(pick(event.clientX - box.left, event.clientY - box.top))
  }

  const shown = current === null ? null : marks[current]
  const line = current === null || !crosshair ? null : crosshair(current)
  const hint = `${base}-keys`

  return (
    // A figure is one tab stop, and its arrow keys move between its marks (DESIGN.md, Figures).
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
    <div
      aria-activedescendant={current === null ? undefined : markId(current)}
      aria-describedby={hint}
      aria-label={label}
      aria-roledescription={t('charts.roledescription')}
      className="relative outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-planning-thread"
      role="group"
      style={{ height }}
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
      tabIndex={0}
      onBlur={() => setActive(null)}
      onFocus={() => setActive((value) => (value === null && marks.length ? 0 : value))}
      onKeyDown={move}
      onPointerLeave={(event) => {
        if (document.activeElement !== event.currentTarget) setActive(null)
      }}
      onPointerMove={point}
    >
      <span className="sr-only" id={hint}>
        {t('charts.keys')}
      </span>
      <svg className="block overflow-visible" height={height} width={width}>
        <defs>
          <HatchPattern id={`${base}-hatch`} />
        </defs>
        {line ? (
          <line
            className="stroke-strong-rule"
            strokeWidth={HAIRLINE}
            x1={crisp(line.x)}
            x2={crisp(line.x)}
            y1={line.top}
            y2={line.bottom}
          />
        ) : null}
        {children({
          active: current,
          hatch: `${base}-hatch`,
          mark: (index) => ({
            id: markId(index),
            role: 'img',
            'aria-label': accessible(marks[index]),
            // Where the mark's tooltip points, in the plot's own coordinates.
            'data-x': marks[index].x,
            'data-y': marks[index].y,
          }),
        })}
      </svg>
      {shown ? <Tooltip mark={shown} width={width} /> : null}
    </div>
  )
}

/** The value first in primary ink, then what it belongs to in secondary ink. */
function Tooltip({ mark, width }: { mark: Mark; width: number }) {
  const right = mark.x > width * 0.6
  const below = mark.y < 56
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute z-10 flex max-w-[16rem] flex-col gap-[2px] rounded-surface border border-hairline bg-working-paper px-sm py-[6px] shadow-popover"
      data-figure-data=""
      style={{
        left: right ? undefined : mark.x + TOOLTIP_GAP,
        right: right ? width - mark.x + TOOLTIP_GAP : undefined,
        top: below ? mark.y + TOOLTIP_GAP : undefined,
        bottom: below ? undefined : `calc(100% - ${mark.y - TOOLTIP_GAP}px)`,
      }}
    >
      {mark.rows?.length ? (
        <>
          <span className="text-metadata text-secondary-ink">{mark.name}</span>
          {mark.rows.map((row) => (
            <span key={row.name} className="flex items-baseline gap-sm whitespace-nowrap text-label tabular-nums">
              <span className="font-semibold text-primary-ink">{row.value}</span>
              <span className="text-secondary-ink">{row.name}</span>
            </span>
          ))}
        </>
      ) : (
        <span className="flex items-baseline gap-sm whitespace-nowrap text-label tabular-nums">
          <span className="font-semibold text-primary-ink">{mark.value}</span>
          <span className="text-secondary-ink">{mark.name}</span>
        </span>
      )}
    </div>
  )
}
