import { useMutation } from '@tanstack/react-query'
import { ChevronDown, Download } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { ApiError, getBlob } from '../api/client'
import { useSize } from '../settings/format'

// The service's own names for an export's file, by format.
const EXTENSION = { markdown: '.md', jsonl: '.jsonl', raw: '.raw.jsonl' } as const
type Format = keyof typeof EXTENSION
const FORMATS = Object.keys(EXTENSION) as Format[]
// How long the saved file's address is kept: the browser reads it as the click is handled.
const RELEASE_MS = 60_000

/** Hand a file to the browser, or to the desktop window, to save with the person's downloads. */
function save(file: Blob, name: string) {
  const address = URL.createObjectURL(file)
  const link = document.createElement('a')
  link.href = address
  link.download = name
  link.hidden = true
  document.body.append(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(address), RELEASE_MS)
}

type Outcome = { name: string; bytes: number; saved: boolean }

/** The button on a session's title line that opens its export choices. */
export function ExportToggle({ open, controls, onToggle }: { open: boolean; controls: string; onToggle: () => void }) {
  const { t } = useTranslation()
  return (
    // On the title's line at its right edge; below 520px it takes its own line, at the left.
    <button aria-controls={controls} aria-expanded={open} className="button-secondary roomy:ml-auto" type="button" onClick={onToggle}>
      <Download aria-hidden="true" size={16} strokeWidth={1.75} />
      {t('session.export.open')}
      <ChevronDown aria-hidden="true" className={open ? 'rotate-180' : ''} size={16} strokeWidth={1.75} />
    </button>
  )
}

/** One session as a file: Markdown to read, JSONL for scripts, or the harness's own lines. */
export function ExportPanel({ id, open, sessionId }: { id: string; open: boolean; sessionId: number }) {
  const { t } = useTranslation()
  const size = useSize()
  const exporting = useMutation({
    mutationFn: async (format: Format): Promise<Outcome> => {
      const name = `agdash-session-${sessionId}${EXTENSION[format]}`
      const file = await getBlob(`/api/sessions/${sessionId}/export`, { format })
      // A session read while original lines were not kept has none to give.
      const saved = file.size > 0
      if (saved) save(file, name)
      return { name, bytes: file.size, saved }
    },
  })
  const done = exporting.data
  const status = exporting.isPending ? (
    t('session.export.preparing')
  ) : exporting.isError ? (
    t(exporting.error instanceof ApiError && exporting.error.kind === 'missing' ? 'session.export.gone' : 'session.export.failed')
  ) : done?.saved ? (
    // The file's name is an identifier, so it is set as one.
    <>
      {t('session.export.done')} <span className="font-mono text-[0.75rem]">{done.name}</span>{' '}
      <span className="data">{t('session.export.sized', { size: size(done.bytes) })}</span>
    </>
  ) : done ? (
    t('session.export.noLines')
  ) : (
    ''
  )
  return (
    <div
      aria-labelledby={`${id}-label`}
      // The class as well as the attribute: a display utility would outrank the attribute alone.
      className={open ? 'flex flex-col gap-sm rounded-surface border border-hairline bg-working-paper p-md' : 'hidden'}
      hidden={!open}
      id={id}
      role="group"
    >
      <div className="flex flex-wrap items-center gap-sm">
        <span className="text-label text-secondary-ink" id={`${id}-label`}>
          {t('session.export.label')}
        </span>
        {FORMATS.map((format) => (
          <button
            key={format}
            aria-busy={exporting.isPending && exporting.variables === format}
            className="button-secondary"
            disabled={exporting.isPending}
            type="button"
            onClick={() => exporting.mutate(format)}
          >
            {t(`session.export.formats.${format}`)}
          </button>
        ))}
      </div>
      <p className="max-w-[70ch] text-label text-secondary-ink">{t('session.export.hint')}</p>
      <p aria-live="polite" className="text-label text-primary-ink" role="status">
        {status}
      </p>
    </div>
  )
}
