import { useId } from 'react'
import { DATA_END, svgId } from './geometry'
import { HatchPattern } from './Hatch'

/** The share bar's thickness, small enough to sit in a table row. */
export const SHARE_HEIGHT = 8

/**
 * A row's share of the largest row, drawn beside its number in a table. The number
 * in the cell says the value, so the bar is hidden from assistive technology; a value
 * that was not reported is hatched across the track, never an empty bar.
 */
export function ShareBar({ value, max }: { value: number | null; max: number }) {
  const pattern = svgId(useId())
  if (value === null) {
    return (
      <svg aria-hidden="true" className="block w-full" data-share="unreported" height={SHARE_HEIGHT}>
        <defs>
          <HatchPattern id={pattern} />
        </defs>
        <rect fill={`url(#${pattern})`} height={SHARE_HEIGHT} width="100%" />
      </svg>
    )
  }
  const share = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0
  return (
    <div aria-hidden="true" className="h-[8px] w-full" data-share={share.toFixed(4)}>
      {share > 0 ? (
        <div
          className="h-full bg-figure-mark"
          style={{
            width: `${share * 100}%`,
            minWidth: 1,
            borderTopRightRadius: Math.min(DATA_END, SHARE_HEIGHT / 2),
            borderBottomRightRadius: Math.min(DATA_END, SHARE_HEIGHT / 2),
          }}
        />
      ) : null}
    </div>
  )
}
