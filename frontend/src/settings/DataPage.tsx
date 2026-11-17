import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { getJson } from '../api/client'
import type { ArchiveSize, StorageReport } from '../api/types'
import { formatWhen } from '../history/format'
import { PageHeader } from '../history/PageHeader'
import { ErrorNotice, LoadingRows } from '../history/SessionList'
import { Section } from '../workchord/WorkChordPage'
import { formatCount, formatDay, useSize } from './format'
import { PathText } from './PathText'

const BASE = 'workspaces.settings.destinations.data'
// The archive's size takes seconds to measure on a large store, so it is not asked again
// each time the page is opened.
const ARCHIVE_FRESH_MS = 5 * 60_000
// The limit the commands show while none is set.
const EXAMPLE_DAYS = 90
const FORMATS = ['markdown', 'jsonl', 'raw'] as const

/** A setting's or a command's own name, as the service and its command line take it. */
function Code({ children }: { children: string }) {
  return (
    <code className="data inline-block max-w-full rounded-compact bg-canvas-mist px-sm py-[3px] font-mono text-[0.8125rem] text-primary-ink [overflow-wrap:anywhere]">
      {children}
    </code>
  )
}

/** Terms and their values: Label terms in secondary ink beside Body values, stacked on a phone. */
function Facts({ children }: { children: ReactNode }) {
  return (
    <dl className="grid max-w-[70ch] items-baseline gap-y-xs text-body roomy:grid-cols-[minmax(10rem,auto)_1fr] roomy:gap-x-lg roomy:gap-y-sm">
      {children}
    </dl>
  )
}

function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="contents">
      <dt className="text-label text-secondary-ink">{term}</dt>
      <dd className="mb-sm flex min-w-0 flex-col items-start gap-xs text-primary-ink roomy:mb-0">{children}</dd>
    </div>
  )
}

/** Commands a person runs themself, each beside what it does. */
function Commands({ title, rows }: { title: string; rows: { command: string; does: string }[] }) {
  return (
    <div className="flex flex-col gap-sm border-t border-hairline pt-md">
      <h3 className="text-body font-semibold text-primary-ink">{title}</h3>
      <ul className="flex flex-col gap-sm">
        {rows.map((row) => (
          <li key={row.command} className="flex flex-col items-start gap-xs">
            <Code>{row.command}</Code>
            <span className="max-w-[70ch] text-label text-secondary-ink">{row.does}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function StoreSection({ report }: { report: StorageReport }) {
  const { t } = useTranslation()
  const size = useSize()
  const { store, archive } = report
  // Asked for apart from the rest, and only when there is something to measure.
  const measured = useQuery({
    queryKey: ['storage', 'archive'],
    queryFn: ({ signal }) => getJson<ArchiveSize>('/api/storage/archive', {}, signal),
    enabled: archive.chunks > 0,
    staleTime: ARCHIVE_FRESH_MS,
  })
  const archived =
    archive.chunks === 0
      ? t('storage.archive.empty')
      : measured.isPending
        ? t('storage.archive.measuring')
        : measured.isError
          ? t('storage.archive.unmeasured')
          : t('storage.archive.size', { size: size(measured.data.bytes) })
  return (
    <Section labelledBy="store-title" title={t('storage.store.title')}>
      <p className="max-w-[70ch] text-body text-secondary-ink">{t('storage.store.lead')}</p>
      <Facts>
        <Fact term={t('storage.store.location')}>
          {store.file ? (
            <PathText className="text-[0.8125rem]" path={store.file} />
          ) : (
            t('storage.store.notAFile')
          )}
        </Fact>
        {store.bytes !== null && (
          <Fact term={t('storage.store.size')}>
            <span className="data">{size(store.bytes)}</span>
          </Fact>
        )}
        <Fact term={t('storage.store.holds')}>
          <span className="data">
            {store.first_started_at
              ? t('storage.store.sessionsSince', {
                  count: store.sessions,
                  shown: formatCount(store.sessions),
                  since: formatDay(store.first_started_at),
                })
              : t('storage.store.sessions', { count: store.sessions, shown: formatCount(store.sessions) })}
          </span>
        </Fact>
        <Fact term={t('storage.archive.term')}>
          <span aria-live="polite" className="data">
            {t(archive.enabled ? 'storage.archive.on' : 'storage.archive.off')} {archived}
          </span>
          <span className="text-label text-secondary-ink">{t('storage.archive.about')}</span>
          {/* Either way the setting behind it is named: what turns it off, or what turned it off. */}
          <span className="text-label text-secondary-ink">{t(archive.enabled ? 'storage.archive.stop' : 'storage.archive.start')}</span>
          <Code>{t(archive.enabled ? 'storage.names.keepRawOff' : 'storage.names.keepRaw')}</Code>
        </Fact>
      </Facts>
    </Section>
  )
}

function RetentionSection({ retention }: { retention: StorageReport['retention'] }) {
  const { t } = useTranslation()
  const { days, cutoff, removable_sessions: removable, kept_alive: kept } = retention
  const before = formatWhen(cutoff)
  const shown = days ?? EXAMPLE_DAYS
  return (
    <Section labelledBy="retention-title" title={t('storage.retention.title')}>
      <p className="max-w-[70ch] text-body text-primary-ink">
        {days === null ? t('storage.retention.off') : t('storage.retention.on', { count: days })}
      </p>
      <Facts>
        <Fact term={t('storage.retention.setBy')}>
          <Code>{days === null ? t('storage.names.retention') : t('storage.names.retentionDays', { days })}</Code>
          <span className="text-label text-secondary-ink">
            {t(days === null ? 'storage.retention.unsetWhere' : 'storage.retention.setWhere')}
          </span>
        </Fact>
        {days !== null && removable !== null && before && (
          <Fact term={t('storage.retention.wouldRemove')}>
            <span className="data">
              {removable === 0
                ? t('storage.retention.nothing', { before })
                : t('storage.retention.removable', { count: removable, shown: formatCount(removable), before })}
            </span>
          </Fact>
        )}
        {days !== null && kept !== null && kept > 0 && (
          <Fact term={t('storage.retention.keeps')}>
            <span className="data">{t('storage.retention.kept', { count: kept, shown: formatCount(kept) })}</span>
          </Fact>
        )}
      </Facts>
      <Commands
        rows={[
          {
            command: t('storage.names.preview', { days: shown }),
            does: days === null ? t('storage.retention.previewExample', { count: shown }) : t('storage.retention.preview'),
          },
          { command: t('storage.names.apply', { days: shown }), does: t('storage.retention.apply') },
        ]}
        title={t('storage.retention.commands')}
      />
    </Section>
  )
}

function ExportSection() {
  const { t } = useTranslation()
  return (
    <Section labelledBy="export-title" title={t('storage.export.title')}>
      <p className="max-w-[70ch] text-body text-secondary-ink">{t('storage.export.lead')}</p>
      <Facts>
        {FORMATS.map((format) => (
          <Fact key={format} term={t(`session.export.formats.${format}`)}>
            {t(`storage.export.formats.${format}`)}
          </Fact>
        ))}
      </Facts>
      <p className="max-w-[70ch] text-label text-secondary-ink">{t('storage.export.redacted')}</p>
      <div>
        <Link className="button-secondary" to="/history">
          {t('storage.export.choose')}
        </Link>
      </div>
      <Commands
        rows={[{ command: t('storage.names.exportSession'), does: t('storage.export.command') }]}
        title={t('storage.export.commands')}
      />
    </Section>
  )
}

/** What agdash keeps and for how long, and how a session leaves it: only by export. */
export function DataPage() {
  const { t } = useTranslation()
  const report = useQuery({
    queryKey: ['storage'],
    queryFn: ({ signal }) => getJson<StorageReport>('/api/storage', {}, signal),
  })
  return (
    <article aria-labelledby="page-title" className="flex flex-col gap-page-gap">
      <PageHeader purposeKey={`${BASE}.purpose`} titleKey={`${BASE}.title`} />
      {report.isPending ? (
        <LoadingRows label={t('storage.loading')} />
      ) : report.isError ? (
        <ErrorNotice error={report.error} scope="storage.error" onRetry={() => void report.refetch()} />
      ) : (
        <>
          <StoreSection report={report.data} />
          <RetentionSection retention={report.data.retention} />
        </>
      )}
      <ExportSection />
    </article>
  )
}
