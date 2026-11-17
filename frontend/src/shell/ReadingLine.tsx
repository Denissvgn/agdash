import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleDashed } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getJson } from '../api/client'
import type { Reading } from '../api/types'

const ASK_AGAIN_MS = 2000
const count = new Intl.NumberFormat('en')

/**
 * Says so while the service is still catching up with the session files that were there
 * when it started, so a short list is not taken for the whole. Shows nothing once that
 * is finished, or when the service cannot tell.
 */
export function ReadingLine() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const reading = useQuery({
    queryKey: ['reading'],
    queryFn: ({ signal }) => getJson<Reading>('/api/reading', {}, signal),
    refetchInterval: ({ state }) => (state.data?.known && !state.data.finished ? ASK_AGAIN_MS : false),
    retry: false,
  })
  const underway = reading.data?.known === true && !reading.data.finished
  // Everything fetched while files were still unread is short: ask again once they are read.
  const was = useRef(false)
  const [caughtUp, setCaughtUp] = useState(false)
  useEffect(() => {
    if (was.current && !underway) {
      setCaughtUp(true)
      void client.invalidateQueries({ predicate: (query) => query.queryKey[0] !== 'reading' })
    }
    was.current = underway
  }, [underway, client])

  return (
    <>
      {/* The counts change every two seconds, so the line itself is not a live region:
          this one speaks once when the catching up is noticed, and once when it is over. */}
      <p aria-live="polite" className="sr-only" role="status">
        {underway ? t('reading.announce') : caughtUp ? t('reading.finished') : ''}
      </p>
      {underway && reading.data && (
        <p className="flex items-start gap-xs text-label tabular-nums text-primary-ink" data-reading="">
          <CircleDashed aria-hidden="true" className="mt-[2px] shrink-0 text-status-planned" size={14} strokeWidth={1.75} />
          {reading.data.total > 0
            ? t('reading.underway', { done: count.format(reading.data.done), total: count.format(reading.data.total) })
            : t('reading.starting')}
        </p>
      )}
    </>
  )
}
