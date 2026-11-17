import { useTranslation } from 'react-i18next'

const whole = new Intl.NumberFormat('en')
const tenths = new Intl.NumberFormat('en', { maximumFractionDigits: 1 })
const day = new Intl.DateTimeFormat('en', { dateStyle: 'medium' })

export const formatCount = (value: number) => whole.format(value)

/** A day in the reader's time zone, for "since" and "first read". */
export const formatDay = (value: string) => day.format(new Date(value))

export const SIZE_UNITS = ['bytes', 'kB', 'MB', 'GB', 'TB'] as const
export type SizeUnit = (typeof SIZE_UNITS)[number]

/**
 * A size in the largest unit that keeps it at one or more, in steps of 1000 as file
 * managers count. The catalogue puts the unit's word around the number.
 */
export function sizeParts(bytes: number): { unit: SizeUnit; value: string } {
  let value = bytes
  let step = 0
  while (value >= 1000 && step < SIZE_UNITS.length - 1) {
    value /= 1000
    step += 1
  }
  return { unit: SIZE_UNITS[step], value: step === 0 ? whole.format(value) : tenths.format(value) }
}

/** A size in words, such as "412 MB", with the unit's word from the catalogue. */
export function useSize(): (bytes: number) => string {
  const { t } = useTranslation()
  return (bytes) => {
    const { unit, value } = sizeParts(bytes)
    return t(`units.${unit}`, { value })
  }
}
