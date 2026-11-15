import { CircleDashed } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { destinationKey, type Destination, type Workspace } from '../shell/navigation'
import { useDocumentTitle } from '../shell/useDocumentTitle'

interface Props {
  workspace: Workspace
  destination: Destination
}

/**
 * One destination's page. Until its data source exists the page says so plainly:
 * what will appear here and what it waits on, rather than an empty frame.
 */
export function DestinationPage({ workspace, destination }: Props) {
  const { t } = useTranslation()
  const key = destinationKey(workspace, destination)
  useDocumentTitle(t(`${key}.title`))
  return (
    <article aria-labelledby="page-title" className="flex flex-col gap-page-gap">
      <header className="flex flex-col gap-xs">
        <h1 id="page-title" className="text-display text-primary-ink">
          {t(`${key}.title`)}
        </h1>
        <p className="max-w-[70ch] text-body text-secondary-ink">{t(`${key}.purpose`)}</p>
      </header>

      <section
        aria-labelledby="unavailable-title"
        className="rounded-surface border border-hairline bg-working-paper p-[20px] shadow-resting"
      >
        <h2 id="unavailable-title" className="flex items-center gap-sm text-title text-primary-ink">
          <CircleDashed aria-hidden="true" size={18} strokeWidth={1.75} className="text-status-planned" />
          {t('unavailable.title')}
        </h2>
        <dl className="mt-md grid max-w-[70ch] gap-y-xs text-body roomy:grid-cols-[minmax(9rem,auto)_1fr] roomy:gap-x-lg roomy:gap-y-md">
          <dt className="text-label text-secondary-ink">{t('unavailable.what')}</dt>
          <dd className="mb-md text-primary-ink roomy:mb-0">{t(`${key}.what`)}</dd>
          <dt className="text-label text-secondary-ink">{t('unavailable.waitsOn')}</dt>
          <dd className="text-primary-ink">{t(`${key}.waitsOn`)}</dd>
        </dl>
      </section>
    </article>
  )
}
