import { useMutation, useQueryClient } from '@tanstack/react-query'
import { TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { sendJson } from '../api/client'
import type { StorageReport } from '../api/types'
import { formatWhen } from '../history/format'
import { formatCount, useSize } from './format'

/**
 * Taking encoded data out of what was archived before: asked for in two steps, since it
 * cannot be undone, and followed while the service does it.
 */
export function TrimArchive({ archive }: { archive: StorageReport['archive'] }) {
  const { t } = useTranslation()
  const size = useSize()
  const client = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const opener = useRef<HTMLButtonElement>(null)
  const leave = useRef<HTMLButtonElement>(null)
  const ask = useMutation({
    mutationFn: () => sendJson<StorageReport>('POST', '/api/storage/archive/trim', {}),
    onSuccess: (answer) => {
      if (answer) client.setQueryData(['storage'], answer)
      setConfirming(false)
    },
  })
  // The safe answer has the focus when the question opens.
  useEffect(() => {
    if (confirming) leave.current?.focus()
  }, [confirming])
  const count = archive.untrimmed_chunks
  const shown = formatCount(count)
  const last = archive.last_trim
  const cancel = () => {
    setConfirming(false)
    ask.reset()
    window.requestAnimationFrame(() => opener.current?.focus())
  }
  return (
    <>
      {archive.trimming ? (
        <span aria-live="polite" className="data" role="status">
          {t('storage.encoded.trimming', { count, shown })}
        </span>
      ) : count === 0 ? null : confirming ? (
        <div className="flex max-w-[70ch] flex-col gap-sm rounded-surface border border-strong-rule bg-canvas-mist p-md">
          <p className="flex items-start gap-sm text-body font-semibold text-primary-ink">
            <TriangleAlert aria-hidden="true" className="mt-[2px] shrink-0 text-feedback-danger" size={16} strokeWidth={1.75} />
            <span className="data">{t('storage.encoded.confirm', { count, shown })}</span>
          </p>
          <p className="text-label text-secondary-ink">{t('storage.encoded.confirmBody')}</p>
          <div className="flex flex-wrap items-center gap-sm">
            <button aria-busy={ask.isPending} className="button-primary" disabled={ask.isPending} type="button" onClick={() => ask.mutate()}>
              {t('storage.encoded.go')}
            </button>
            <button ref={leave} className="button-secondary" disabled={ask.isPending} type="button" onClick={cancel}>
              {t('storage.encoded.leave')}
            </button>
          </div>
          <p aria-live="polite" className="text-label text-secondary-ink" role="status">
            {ask.isError ? t('storage.encoded.failed') : ''}
          </p>
        </div>
      ) : (
        <>
          <span className="data">{t('storage.encoded.waiting', { count, shown })}</span>
          <button ref={opener} className="button-secondary" type="button" onClick={() => setConfirming(true)}>
            {t('storage.encoded.trim')}
          </button>
        </>
      )}
      {last && !archive.trimming && (
        <span className="data text-label text-secondary-ink">
          {last.saved_bytes > 0
            ? t('storage.encoded.last', { when: formatWhen(last.at), size: size(last.saved_bytes) })
            : t('storage.encoded.lastNone', { when: formatWhen(last.at) })}
        </span>
      )}
    </>
  )
}
