import { useTranslation } from 'react-i18next'
import { splitSpan } from './format'

/** A span of time in words from the catalogue: "3 h 20 min", "15 min", "under a minute". */
export function useDuration() {
  const { t } = useTranslation()
  return (milliseconds: number) => {
    const { hours, minutes } = splitSpan(milliseconds)
    if (hours) return t('charts.duration.hours', { hours, minutes })
    if (minutes) return t('charts.duration.minutes', { minutes })
    return t('charts.duration.under')
  }
}
