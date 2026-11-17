import { useTranslation } from 'react-i18next'
import { VerticalGrid } from './Axes'
import { Figure, type FigureStatus, type LegendEntry } from './Figure'
import { formatCompact, formatValue } from './format'
import { crisp, DOT, LINE, RING, labelWidth } from './geometry'
import { FILL } from './ink'
import { Plot, type Mark } from './Plot'
import { valueScale, valueTicks } from './scales'
import { useWidth } from './useWidth'

export type DumbbellRow = {
  id: string
  name: string
  /** What was planned, such as an agent's assignments. */
  declared: number
  /** What was seen, such as the sessions mapped to the agent. */
  observed: number
  /** Words after the values in the tooltip and table, such as how each was observed. */
  detail?: string
}

const ROW = 30
const TOP = 6
const AXIS = 22
/** Counts are small: the scale spans at least this far, so one or two sit near the names, not at the far edge. */
const MIN_TOP = 4

/**
 * The declared mark: a ring in the mark ink, wider than the dot so that when the two
 * values match the observed dot sits inside it. The observed mark: a filled dot, a step darker.
 */
const RING_R = DOT / 2 + 3

function Declared({ x, y, active }: { x: number; y: number; active: boolean }) {
  return <circle className={`fill-working-paper ${active ? 'stroke-figure-mark-hover' : 'stroke-figure-mark'}`} cx={x} cy={y} r={RING_R} strokeWidth={LINE} />
}

function Observed({ x, y, active }: { x: number; y: number; active: boolean }) {
  return <circle className={`${active ? FILL['mark-hover'] : FILL['ink-7']} stroke-working-paper`} cx={x} cy={y} r={DOT / 2 + (active ? 1 : 0)} strokeWidth={RING} />
}

/**
 * Two values per row on one scale, joined by a line: what was declared (a ring)
 * against what was observed (a dot). Identity is the row's name, never a hue.
 */
export function Dumbbell({
  title,
  caption,
  rows,
  status = 'ready',
  rowHeader,
  declaredLabel,
  observedLabel,
  detailLabel,
  valueText,
}: {
  title: string
  caption?: string
  rows?: DumbbellRow[]
  status?: FigureStatus
  rowHeader: string
  declaredLabel: string
  observedLabel: string
  detailLabel: string
  /** The pair in words at the end of each row, such as "2 assigned · 3 live". */
  valueText: (row: DumbbellRow) => string
}) {
  const { t } = useTranslation()
  const [ref, width] = useWidth()
  const list = rows ?? []
  const shown: FigureStatus = status === 'ready' && !list.length ? 'empty' : status
  const height = TOP + (list.length || 2) * ROW + AXIS
  const legend: LegendEntry[] = [
    {
      key: 'declared',
      label: declaredLabel,
      swatch: { draw: () => <circle className="fill-working-paper stroke-figure-mark" cx={6} cy={6} r={4} strokeWidth={LINE} /> },
    },
    { key: 'observed', label: observedLabel, swatch: { draw: () => <circle className={FILL['ink-7']} cx={6} cy={6} r={4} /> } },
  ]

  let plot = null
  if (list.length) {
    const left = Math.min(Math.max(...list.map((row) => labelWidth(row.name)), 64), Math.max(64, Math.floor(width * 0.35)))
    const room = Math.max(...list.map((row) => labelWidth(valueText(row)))) + RING_R + 8
    const right = Math.max(left + 48, width - 16 - room)
    const x = valueScale([...list.flatMap((row) => [row.declared, row.observed]), MIN_TOP], [left, right])
    const ticks = valueTicks(x, right - left).filter(Number.isInteger)
    const bottom = TOP + list.length * ROW
    const marks: Mark[] = list.map((row, i) => ({
      x: x(Math.max(row.declared, row.observed)),
      y: TOP + i * ROW + ROW / 2,
      name: row.name,
      value: '',
      rows: [
        { name: declaredLabel, value: formatValue(row.declared) },
        { name: observedLabel, value: row.detail ? `${formatValue(row.observed)} (${row.detail})` : formatValue(row.observed) },
      ],
    }))
    plot = (
      <div ref={ref} className="min-w-0">
        <Plot
          height={height}
          label={title}
          marks={marks}
          pick={(_, py) => {
            const i = Math.floor((py - TOP) / ROW)
            return i >= 0 && i < list.length ? i : null
          }}
          width={width}
        >
          {({ active, mark }) => (
            <>
              <VerticalGrid bottom={bottom} format={formatCompact} ticks={ticks} top={TOP} x={x} />
              {list.map((row, i) => {
                const y = TOP + i * ROW + ROW / 2
                return (
                  <g key={row.id} {...mark(i)} data-declared={row.declared} data-observed={row.observed}>
                    <text className="fill-primary-ink text-counter" data-figure-data="" dominantBaseline="middle" textAnchor="end" x={left - 10} y={y}>
                      {row.name.length > 24 ? `${row.name.slice(0, 23)}…` : row.name}
                    </text>
                    <line className="stroke-figure-ink-3" strokeLinecap="round" strokeWidth={LINE} x1={x(row.declared)} x2={x(row.observed)} y1={crisp(y)} y2={crisp(y)} />
                    <Declared active={active === i} x={x(row.declared)} y={y} />
                    <Observed active={active === i} x={x(row.observed)} y={y} />
                    <text
                      className="fill-secondary-ink text-counter tabular-nums"
                      data-figure-data=""
                      dominantBaseline="middle"
                      x={x(Math.max(row.declared, row.observed)) + RING_R + 8}
                      y={y}
                    >
                      {valueText(row)}
                    </text>
                  </g>
                )
              })}
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
      table={{
        rowHeader,
        columns: [
          { key: 'declared', label: declaredLabel },
          { key: 'observed', label: observedLabel },
          { key: 'detail', label: detailLabel },
        ],
        rows: list.map((row) => ({ key: row.id, header: row.name, cells: [formatValue(row.declared), formatValue(row.observed), row.detail ?? t('charts.table.dash')] })),
      }}
      title={title}
    >
      {plot}
    </Figure>
  )
}
