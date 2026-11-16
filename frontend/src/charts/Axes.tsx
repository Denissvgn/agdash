import { crisp, HAIRLINE } from './geometry'

/**
 * Value gridlines across the plot, labelled at the left edge: hairlines, with the
 * baseline at zero in strong rule.
 */
export function HorizontalGrid({
  ticks,
  y,
  left,
  right,
  format,
}: {
  ticks: number[]
  y: (value: number) => number
  left: number
  right: number
  format: (value: number) => string
}) {
  return (
    <g aria-hidden="true" data-figure-data="">
      {ticks.map((tick) => (
        <g key={tick}>
          <line
            className={tick === 0 ? 'stroke-strong-rule' : 'stroke-hairline'}
            strokeWidth={HAIRLINE}
            x1={left}
            x2={right}
            y1={crisp(y(tick))}
            y2={crisp(y(tick))}
          />
          <text className="fill-secondary-ink text-counter tabular-nums" dominantBaseline="middle" textAnchor="end" x={left - 6} y={y(tick)}>
            {format(tick)}
          </text>
        </g>
      ))}
    </g>
  )
}

/** Value gridlines down the plot, labelled under it; the baseline at zero in strong rule. */
export function VerticalGrid({
  ticks,
  x,
  top,
  bottom,
  format,
}: {
  ticks: number[]
  x: (value: number) => number
  top: number
  bottom: number
  format: (value: number) => string
}) {
  return (
    <g aria-hidden="true" data-figure-data="">
      {ticks.map((tick) => (
        <g key={tick}>
          <line
            className={tick === 0 ? 'stroke-strong-rule' : 'stroke-hairline'}
            strokeWidth={HAIRLINE}
            x1={crisp(x(tick))}
            x2={crisp(x(tick))}
            y1={top}
            y2={bottom}
          />
          <text className="fill-secondary-ink text-counter tabular-nums" textAnchor="middle" x={x(tick)} y={bottom + 14}>
            {format(tick)}
          </text>
        </g>
      ))}
    </g>
  )
}

/** Labels along a time axis, centred on the positions given. */
export function TimeLabels({ labels, y }: { labels: { key: string; x: number; text: string }[]; y: number }) {
  return (
    <g aria-hidden="true" data-figure-data="">
      {labels.map((label) => (
        <text key={label.key} className="fill-secondary-ink text-counter tabular-nums" textAnchor="middle" x={label.x} y={y}>
          {label.text}
        </text>
      ))}
    </g>
  )
}
