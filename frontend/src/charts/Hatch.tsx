import { useTranslation } from 'react-i18next'
import { HATCH_OPACITY, HATCH_SPACING, HAIRLINE } from './geometry'

/** The not-reported hatch: 1px strong-rule lines at 45°, 6px apart, at 35%. */
export function HatchPattern({ id }: { id: string }) {
  return (
    <pattern height={HATCH_SPACING} id={id} patternTransform="rotate(45)" patternUnits="userSpaceOnUse" width={HATCH_SPACING}>
      <line
        className="stroke-strong-rule"
        strokeOpacity={HATCH_OPACITY}
        strokeWidth={HAIRLINE}
        x1={HAIRLINE / 2}
        x2={HAIRLINE / 2}
        y1={0}
        y2={HATCH_SPACING}
      />
    </pattern>
  )
}

/**
 * A stretch that was not reported: hatched, and labelled in words when there is room,
 * so it never reads as a zero.
 */
export function NotReportedBand({
  pattern,
  x,
  y,
  width,
  height,
  labelled = true,
}: {
  pattern: string
  x: number
  y: number
  width: number
  height: number
  labelled?: boolean
}) {
  const { t } = useTranslation()
  if (width <= 0 || height <= 0) return null
  const label = t('charts.notReported')
  const fits = labelled && width >= label.length * 6 + 8 && height >= 14
  return (
    <g>
      <rect fill={`url(#${pattern})`} height={height} width={width} x={x} y={y} />
      {fits ? (
        <text className="fill-secondary-ink text-counter" dominantBaseline="middle" x={x + 4} y={y + Math.min(height / 2, 10)}>
          {label}
        </text>
      ) : null}
    </g>
  )
}
