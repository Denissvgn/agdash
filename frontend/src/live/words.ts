import type { KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import type { LiveActivity } from '../api/types'
import { useDuration } from '../charts/useDuration'

type LiveRow = LiveActivity['sessions'][number]

/** A session's states over its last 30 minutes in words: "Waiting for 20 min, then Active for 10 min". */
export function useStateWords(): (row: LiveRow) => string {
  const { t } = useTranslation()
  const duration = useDuration()
  return (row) =>
    row.states
      .map((span) => t('live.strips.span', { state: t(`charts.states.${span.state}`), duration: duration(Date.parse(span.end) - Date.parse(span.start)) }))
      .join(t('live.strips.then')) || t('live.strips.noStates')
}

/** A session's last 30 minutes in words, for where its strips are hidden from assistive technology. */
export function useStripWords(): (row: LiveRow) => string {
  const { t } = useTranslation()
  const states = useStateWords()
  return (row) => t('live.strips.summary', { count: row.total, states: states(row) })
}

/** How long ago, as short as a slim row needs it: "now", "4m", "2h", "3d". */
export function useShortAgo(): (instant: number, now: number) => string {
  const { t } = useTranslation()
  return (instant, now) => {
    const minutes = Math.round((now - instant) / 60_000)
    if (minutes < 1) return t('live.ago.now')
    if (minutes < 60) return t('live.ago.minutes', { count: minutes })
    const hours = Math.round(minutes / 60)
    if (hours < 48) return t('live.ago.hours', { count: hours })
    return t('live.ago.days', { count: Math.round(hours / 24) })
  }
}

/**
 * Keys for a session's link on the board: up and down move through the sessions in the
 * order they are listed and take the details panel along; Home and End jump to the ends.
 */
export function useRowKeys(): (event: KeyboardEvent<HTMLAnchorElement>) => void {
  const navigate = useNavigate()
  return (event) => {
    const step = { ArrowDown: 1, ArrowUp: -1, Home: -Infinity, End: Infinity }[event.key]
    if (step === undefined) return
    const board = event.currentTarget.closest('[data-roster]')
    // Rows inside a closed fold are not on the screen.
    const rows = [...(board?.querySelectorAll<HTMLAnchorElement>('a[data-row]') ?? [])].filter((row) => !row.closest('details:not([open])'))
    const at = rows.indexOf(event.currentTarget)
    const next = rows[Math.max(0, Math.min(rows.length - 1, at + step))]
    if (at < 0 || !next || next === rows[at]) return
    event.preventDefault()
    next.focus()
    navigate(next.getAttribute('href') ?? '', { replace: true })
  }
}
