import { useQuery } from '@tanstack/react-query'
import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import { getJson } from '../api/client'
import type { StoreParts as Parts } from '../api/types'
import { ShareBar } from '../charts'
import { useSize } from './format'

// Measuring walks the archive, so it is not asked again each time the page is opened.
const FRESH_MS = 5 * 60_000
const ROWS = [
  ['rest', 'rest_bytes'],
  ['archive', 'archive_bytes'],
  ['free', 'free_bytes'],
] as const
const share = new Intl.NumberFormat('en', { style: 'percent', maximumFractionDigits: 0 })
const head = 'whitespace-nowrap px-[12px] py-sm align-bottom text-label font-medium text-secondary-ink'
const cell = 'whitespace-nowrap px-[12px] py-sm text-right align-top'

/** What the store's file is made of, largest first: a small ledger with a share bar per row. */
export function StoreParts() {
  const { t } = useTranslation()
  const size = useSize()
  const captionId = useId()
  const measured = useQuery({
    queryKey: ['storage', 'parts'],
    queryFn: ({ signal }) => getJson<Parts>('/api/storage/parts', {}, signal),
    staleTime: FRESH_MS,
  })
  if (measured.isPending) return <p className="text-label text-secondary-ink">{t('storage.parts.measuring')}</p>
  if (measured.isError) return <p className="text-label text-secondary-ink">{t('storage.parts.unmeasured')}</p>
  const parts = measured.data
  const total = parts.bytes
  if (total === null || total === 0) return null
  const rows = ROWS.map(([id, key]) => ({ id, bytes: parts[key] }))
    .filter((row): row is { id: (typeof ROWS)[number][0]; bytes: number } => row.bytes !== null)
    .sort((a, b) => b.bytes - a.bytes)
  const largest = Math.max(0, ...rows.map((row) => row.bytes))
  return (
    <div className="flex flex-col gap-sm border-t border-hairline pt-md">
      <h3 className="text-body font-semibold text-primary-ink" id={captionId}>
        {t('storage.parts.title')}
      </h3>
      <div aria-labelledby={captionId} className="max-w-[70ch] overflow-x-auto" role="region" tabIndex={0}>
        <table className="data w-full border-collapse text-body">
          <thead className="border-b border-hairline">
            <tr>
              <th className={`${head} text-left`} scope="col">
                {t('storage.parts.part')}
              </th>
              <th className={`${head} text-right`} scope="col">
                {t('storage.parts.size')}
              </th>
              <th className={`${head} text-right`} scope="col">
                {t('storage.parts.share')}
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-hairline">
            {rows.map((row) => (
              <tr key={row.id}>
                <th className="px-[12px] py-sm text-left align-top font-normal" scope="row">
                  <span className="flex min-w-[8rem] flex-col gap-xs">
                    {t(`storage.parts.names.${row.id}`)}
                    <ShareBar max={largest} value={row.bytes} />
                  </span>
                </th>
                <td className={cell}>{size(row.bytes)}</td>
                <td className={cell}>{share.format(row.bytes / total)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="max-w-[70ch] text-label text-secondary-ink">
        {t('storage.parts.note')}{' '}
        {parts.compacts_at_start ? t('storage.parts.atStart') : parts.releases ? t('storage.parts.released') : ''}
      </p>
    </div>
  )
}
