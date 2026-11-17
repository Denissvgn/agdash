import { useTranslation } from 'react-i18next'

const MARKUP = /<[^>]{0,200}>/g
const LIMIT = 140

type Named = { title?: string | null; initial_task?: string | null }

/** How a prompt began: its first line that says something, without markup, cut to a title's length. */
export function firstWords(task: string | null | undefined): string | null {
  for (const line of (task ?? '').split('\n')) {
    const text = line.replace(MARKUP, ' ').replace(/\s+/g, ' ').trim()
    if (text) return text.length > LIMIT ? `${text.slice(0, LIMIT - 1)}…` : text
  }
  return null
}

/** What to call a session without the catalogue: its title, else how its first prompt began. */
export function nameOf(session: Named): string | null {
  return session.title || firstWords(session.initial_task)
}

/**
 * What to call a session: its title, else how its first prompt began, else that it has
 * none. Most sessions an agent starts never get a title, and a list of "Untitled
 * session" tells nothing apart.
 */
export function useSessionName(): (session: Named) => string {
  const { t } = useTranslation()
  return (session) => nameOf(session) ?? t('history.list.untitled')
}
