import { useQuery } from '@tanstack/react-query'
import { ChevronRight, CircleAlert } from 'lucide-react'
import type { TFunction } from 'i18next'
import { useId, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { getJson } from '../api/client'
import type { FormatHealth, Source, SourcesReport } from '../api/types'
import { formatWhen } from '../history/format'
import { PageHeader } from '../history/PageHeader'
import { ErrorNotice, LoadingRows } from '../history/SessionList'
import { ReadingLine } from '../shell/ReadingLine'
import { formatCount, formatDay, useSize } from './format'
import { PathText } from './PathText'

const BASE = 'workspaces.settings.destinations.sources'
const NOTES = ['readOnly', 'signIn', 'elsewhere', 'notUnderstood'] as const
// The harnesses' own settings that move their folders, named under the note about them.
const FOLDER_SETTINGS = ['claude_code', 'codex'] as const
const mono = 'font-mono text-[0.75rem]'

const troubled = (format: FormatHealth) => format.unknown_records > 0 || format.unreadable_lines > 0

/** "3 records of an unknown kind and 2 unreadable lines", or the one of them there is. */
function notUnderstood(t: TFunction, formats: FormatHealth[]): string {
  const records = formats.reduce((sum, format) => sum + format.unknown_records, 0)
  const lines = formats.reduce((sum, format) => sum + format.unreadable_lines, 0)
  const parts = [
    records > 0 ? t('sources.records', { count: records, shown: formatCount(records) }) : null,
    lines > 0 ? t('sources.lines', { count: lines, shown: formatCount(lines) }) : null,
  ].filter((part): part is string => part !== null)
  return parts.length === 2 ? t('sources.both', { first: parts[0], second: parts[1] }) : (parts[0] ?? '')
}

/** Found or not, as a state chip: cyan while agdash reads it, slate when there is nothing to. */
function FoundChip({ found }: { found: boolean }) {
  const { t } = useTranslation()
  return (
    <span className="inline-flex h-[22px] shrink-0 items-center gap-xs rounded-full border border-hairline bg-working-paper px-sm text-label text-secondary-ink">
      <span aria-hidden="true" className={`size-[6px] shrink-0 rounded-full ${found ? 'bg-status-active' : 'bg-status-planned'}`} />
      {t(found ? 'sources.found' : 'sources.missing')}
    </span>
  )
}

const cell = 'whitespace-nowrap px-[12px] py-xs text-right align-top'
const headBase = 'whitespace-nowrap px-[12px] py-xs align-bottom text-label font-medium text-secondary-ink'
const head = `${headBase} text-right`

/** Each format version in which something was not understood: the evidence behind the line above it. */
function FormatTable({ harness, formats }: { harness: string; formats: FormatHealth[] }) {
  const { t } = useTranslation()
  const captionId = useId()
  const version = (format: FormatHealth) =>
    format.version === null ? (
      <span className="text-secondary-ink">{t('sources.formats.noVersion')}</span>
    ) : (
      <span className={mono}>{format.version}</span>
    )
  return (
    <>
      {/* On a phone five columns do not fit: each format is its own short list instead. */}
      <ul aria-label={t('sources.formats.caption', { harness })} className="mt-xs flex flex-col gap-sm roomy:hidden">
        {formats.map((format) => (
          <li key={format.version ?? ''} className="rounded-control border border-hairline px-[12px] py-sm">
            <p className="text-primary-ink">{version(format)}</p>
            <dl className="data mt-xs grid grid-cols-[1fr_auto] gap-x-md gap-y-[2px]">
              <dt>{t('sources.formats.records')}</dt>
              <dd className="text-right text-primary-ink">{formatCount(format.unknown_records)}</dd>
              <dt>{t('sources.formats.lines')}</dt>
              <dd className="text-right text-primary-ink">{formatCount(format.unreadable_lines)}</dd>
              <dt>{t('sources.formats.first')}</dt>
              <dd className="text-right">{formatDay(format.first_seen_at)}</dd>
              <dt>{t('sources.formats.last')}</dt>
              <dd className="text-right">{formatDay(format.last_seen_at)}</dd>
            </dl>
          </li>
        ))}
      </ul>
    <div
      aria-labelledby={captionId}
      className="mt-xs hidden max-w-[44rem] overflow-x-auto rounded-control border border-hairline roomy:block"
      role="region"
      tabIndex={0}
    >
      <table className="data w-full border-collapse text-label">
        <caption className="sr-only" id={captionId}>
          {t('sources.formats.caption', { harness })}
        </caption>
        <thead className="border-b border-hairline">
          <tr>
            <th className={`${headBase} text-left`} scope="col">
              {t('sources.formats.version')}
            </th>
            <th className={head} scope="col">
              {t('sources.formats.records')}
            </th>
            <th className={head} scope="col">
              {t('sources.formats.lines')}
            </th>
            <th className={head} scope="col">
              {t('sources.formats.first')}
            </th>
            <th className={head} scope="col">
              {t('sources.formats.last')}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-hairline">
          {formats.map((format) => (
            <tr key={format.version ?? ''}>
              <th className="whitespace-nowrap px-[12px] py-xs text-left align-top font-normal text-primary-ink" scope="row">
                {version(format)}
              </th>
              <td className={`${cell} text-primary-ink`}>{formatCount(format.unknown_records)}</td>
              <td className={`${cell} text-primary-ink`}>{formatCount(format.unreadable_lines)}</td>
              <td className={`${cell} text-secondary-ink`}>{formatDay(format.first_seen_at)}</td>
              <td className={`${cell} text-secondary-ink`}>{formatDay(format.last_seen_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    </>
  )
}

function SourceRow({ source }: { source: Source }) {
  const { t } = useTranslation()
  const size = useSize()
  const harness = t(`history.harnesses.${source.harness}`, { defaultValue: source.harness })
  const lastRead = formatWhen(source.last_read_at)
  const unclear = source.formats.filter(troubled)
  const recent = unclear.filter((format) => format.recent)
  const older = unclear.filter((format) => !format.recent)
  const since = recent.length
    ? formatDay(recent.reduce((first, format) => (format.first_seen_at < first ? format.first_seen_at : first), recent[0].first_seen_at))
    : null
  const read = source.files > 0
  const sessions = { count: source.sessions, shown: formatCount(source.sessions) }
  const facts: [string, ReactNode][] = []
  if (read) facts.push(['files', t('sources.files', { count: source.files, shown: formatCount(source.files) })], ['size', size(source.bytes_read)])
  // A harness whose folder is gone still has what was read of it: said as kept, not as found.
  if (source.sessions > 0) facts.push(['sessions', t(source.found ? 'sources.sessions' : 'sources.sessionsKept', sessions)])
  if (read) {
    facts.push([
      'format',
      source.format_version ? (
        <>
          {t('sources.format')} <span className={mono}>{source.format_version}</span>
        </>
      ) : (
        t('sources.formatUnknown')
      ),
    ])
  }
  if (read && unclear.length === 0) facts.push(['understood', t('sources.understood')])
  if (!read && source.sessions === 0) facts.push(['nothing', t(source.found ? 'sources.nothingRead' : 'sources.notHere')])

  return (
    <li className="flex flex-col gap-xs px-md py-[12px]">
      <div className="flex flex-wrap items-center gap-x-sm gap-y-xs">
        <FoundChip found={source.found} />
        <h2 className="min-w-[min(100%,10rem)] flex-1 text-body font-semibold text-primary-ink">{harness}</h2>
        {read && (
          <span className="data shrink-0 text-label text-secondary-ink">
            {lastRead ? t('sources.lastRead', { when: lastRead }) : t('sources.neverRead')}
          </span>
        )}
      </div>
      <p className="flex flex-wrap items-baseline gap-x-sm text-label text-secondary-ink">
        {facts.map(([name, fact], index) => (
          <span key={name} className="contents">
            {index > 0 && <span aria-hidden="true">·</span>}
            <span className="data">{fact}</span>
          </span>
        ))}
      </p>
      {source.locations.length > 0 && (
        <ul aria-label={t('sources.places', { harness })} className="flex flex-col gap-[2px]">
          {source.locations.map((place) => (
            <li key={place.path} className="flex flex-wrap items-baseline gap-x-sm">
              <PathText className={`text-[0.75rem] ${place.found ? 'text-primary-ink' : 'text-secondary-ink'}`} path={place.path} />
              {(place.role === 'settings' || !place.found) && (
                <span className="text-label text-secondary-ink">
                  {t(place.found ? 'sources.place.settings' : place.role === 'settings' ? 'sources.place.settingsMissing' : 'sources.place.missing')}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {recent.length > 0 && since && (
        <p className="flex max-w-[80ch] items-start gap-xs text-label text-primary-ink">
          <CircleAlert aria-hidden="true" className="mt-[1px] shrink-0 text-feedback-warning" size={14} strokeWidth={1.75} />
          {t('sources.recent', { what: notUnderstood(t, recent), since, harness })}
        </p>
      )}
      {older.length > 0 && (
        <p className="max-w-[80ch] text-label text-secondary-ink">{t('sources.older', { what: notUnderstood(t, older) })}</p>
      )}
      {unclear.length > 0 && (
        <details className="group text-label text-secondary-ink">
          <summary className="flex w-fit cursor-pointer list-none items-center gap-xs rounded-compact pointer-coarse:min-h-touch [&::-webkit-details-marker]:hidden">
            <ChevronRight aria-hidden="true" className="shrink-0 group-open:rotate-90" size={14} strokeWidth={1.75} />
            {t('sources.formats.summary')}
          </summary>
          <FormatTable formats={unclear} harness={harness} />
        </details>
      )}
    </li>
  )
}

function Found({ sources }: { sources: Source[] }) {
  const { t } = useTranslation()
  const found = sources.filter((source) => source.found).length
  const changed = sources.filter((source) => source.formats.some((format) => format.recent)).length
  return (
    <>
      <p className="data text-body text-primary-ink">
        {t('sources.summary', { count: sources.length, found })}
        {changed > 0 && <> {t('sources.changed', { count: changed })}</>}
      </p>
      <ul
        aria-label={t('sources.list')}
        className="divide-y divide-hairline overflow-hidden rounded-surface border border-hairline bg-working-paper shadow-resting"
      >
        {sources.map((source) => (
          <SourceRow key={source.harness} source={source} />
        ))}
      </ul>
      <ul className="flex max-w-[80ch] flex-col gap-sm text-label text-secondary-ink">
        {NOTES.map((note) => (
          <li key={note}>
            {t(`sources.notes.${note}`)}
            {note === 'elsewhere' &&
              FOLDER_SETTINGS.map((harness, index) => (
                <span key={harness}>
                  {index > 0 ? ` ${t('sources.or')} ` : ' '}
                  <span className={`text-primary-ink ${mono}`}>{t(`sources.settings.${harness}`)}</span>
                </span>
              ))}
          </li>
        ))}
      </ul>
    </>
  )
}

/** What agdash reads on this machine: each harness, where it was looked for, and how it went. */
export function SourcesPage() {
  const { t } = useTranslation()
  const report = useQuery({
    queryKey: ['sources'],
    queryFn: ({ signal }) => getJson<SourcesReport>('/api/sources', {}, signal),
  })
  return (
    <article aria-labelledby="page-title" className="flex flex-col gap-page-gap">
      <PageHeader purposeKey={`${BASE}.purpose`} titleKey={`${BASE}.title`} />
      <ReadingLine />
      {report.isPending ? (
        <LoadingRows label={t('sources.loading')} />
      ) : report.isError ? (
        <ErrorNotice error={report.error} scope="sources.error" onRetry={() => void report.refetch()} />
      ) : (
        <Found sources={report.data.sources} />
      )}
    </article>
  )
}
