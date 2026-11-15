import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'

/** The window and tab title, from the catalogue: "<page> · agdash". */
export function useDocumentTitle(page: string) {
  const { t } = useTranslation()
  useEffect(() => {
    document.title = t('shell.documentTitle', { page, app: t('app.name') })
  }, [page, t])
}
