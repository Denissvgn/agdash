import { useTranslation } from 'react-i18next'
import { useDocumentTitle } from '../shell/useDocumentTitle'

/** A page's one Display heading and its purpose line, which also names the window. */
export function PageHeader({ titleKey, purposeKey }: { titleKey: string; purposeKey: string }) {
  const { t } = useTranslation()
  useDocumentTitle(t(titleKey))
  return (
    <header className="flex flex-col gap-xs">
      <h1 className="text-display text-primary-ink" id="page-title">
        {t(titleKey)}
      </h1>
      <p className="max-w-[70ch] text-body text-secondary-ink">{t(purposeKey)}</p>
    </header>
  )
}
