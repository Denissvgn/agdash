/** Number and time formats for figure axes, tooltips and table twins (English). */

const compact = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 })
const whole = new Intl.NumberFormat('en', { maximumFractionDigits: 2 })
const day = new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric' })
const month = new Intl.DateTimeFormat('en', { month: 'short', year: 'numeric' })
const clock = new Intl.DateTimeFormat('en', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
const stamp = new Intl.DateTimeFormat('en', {
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

/** An axis label: 1.2K, 34M, 0.5. */
export const formatCompact = (value: number) => compact.format(value)
/** A tooltip or table value: grouped digits, 1,234,567. */
export const formatValue = (value: number) => whole.format(value)

/**
 * A time-axis label at the coarsest unit that changes between ticks: a month for
 * ticks a month apart, a day for ticks at midnight, a clock time otherwise.
 */
export function formatTick(at: Date, step: number): string {
  if (step >= 28 * 86_400_000) return month.format(at)
  if (at.getHours() === 0 && at.getMinutes() === 0) return day.format(at)
  return clock.format(at)
}

/** A time for a tooltip or table row: a day, or a day and time when the step is under a day. */
export function formatWhen(at: Date, step: number): string {
  return step >= 86_400_000 ? day.format(at) : stamp.format(at)
}

/** A moment, with its day: Oct 5, 14:05. */
export const formatMoment = (at: Date) => stamp.format(at)

/** Hours and minutes of a span, for a catalogue string to put words around. */
export function splitSpan(milliseconds: number): { hours: number; minutes: number } {
  const minutes = Math.round(milliseconds / 60_000)
  return { hours: Math.floor(minutes / 60), minutes: minutes % 60 }
}

/** A day on its own: Oct 5. */
export const formatDay = (at: Date) => day.format(at)
