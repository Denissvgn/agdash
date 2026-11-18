import { ArrowRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { HOME_PATH } from '../shell/navigation'
import { useDocumentTitle } from '../shell/useDocumentTitle'

export function NotFoundPage() {
  const { t } = useTranslation()
  useDocumentTitle(t('notFound.title'))
  return (
    <article aria-labelledby="page-title" className="flex flex-col gap-md">
      <h1 id="page-title" className="text-display text-primary-ink">
        {t('notFound.title')}
      </h1>
      <p className="text-body text-secondary-ink">{t('notFound.body')}</p>
      <p>
        <Link
          to={HOME_PATH}
          className="inline-flex h-control items-center gap-sm rounded-control bg-primary-ink px-[12px] text-body text-working-paper transition-colors duration-state hover:bg-conductor-panel dark:hover:bg-secondary-ink pointer-coarse:h-touch"
        >
          {t('notFound.action')}
          <ArrowRight aria-hidden="true" size={16} strokeWidth={1.75} />
        </Link>
      </p>
    </article>
  )
}
