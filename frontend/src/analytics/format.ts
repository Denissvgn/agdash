const whole = new Intl.NumberFormat('en')
const money = new Intl.NumberFormat('en', { style: 'currency', currency: 'USD', maximumFractionDigits: 4 })

export const formatCount = (value: number) => whole.format(value)
export const formatCost = (value: number) => money.format(value)

const compactMoney = new Intl.NumberFormat('en', { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1 })
/** An axis label for cost: $5, $1.2K. */
export const formatCostCompact = (value: number) => compactMoney.format(value)

/** Hours and minutes of a duration, for a catalogue string to put words around. */
export function splitDuration(seconds: number): { hours: number; minutes: number } {
  const minutes = Math.round(seconds / 60)
  return { hours: Math.floor(minutes / 60), minutes: minutes % 60 }
}
