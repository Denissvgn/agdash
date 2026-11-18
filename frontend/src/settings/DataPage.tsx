import { useIsFetching, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { getJson, sendJson } from '../api/client'
import type { ArchiveSize, RetentionPreview, StorageReport } from '../api/types'
import { formatWhen } from '../history/format'
import { PageHeader } from '../history/PageHeader'
import { ErrorNotice, LoadingRows } from '../history/SessionList'
import { Choice } from '../shell/Choice'
import { Section } from '../workchord/WorkChordPage'
import { Code, Fact, Facts } from './Facts'
import { formatCount, formatDay, useSize } from './format'
import { PathText } from './PathText'
import { StoreParts } from './StoreParts'
import { TrimArchive } from './TrimArchive'

const BASE = 'workspaces.settings.destinations.data'
// The archive's size takes seconds to measure on a large store, so it is not asked again
// each time the page is opened.
const ARCHIVE_FRESH_MS = 5 * 60_000
// The limit the commands show while none is set.
const EXAMPLE_DAYS = 90
// The limits offered; one saved elsewhere, such as where the service starts, joins them.
const LIMITS = [30, 90, 180, 365]
const EVERYTHING = 'everything'
// How often the page asks again while sessions are being removed.
const FOLLOW_MS = 2000
const STORAGE = ['storage'] as const
const FORMATS = ['markdown', 'jsonl', 'raw'] as const

type Retention = StorageReport['retention']

/** Save a setting; the service answers with everything the page shows, as it now is. */
function useSave() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: { retention_days?: number | null; keep_raw?: boolean; keep_encoded?: boolean }) =>
      sendJson<StorageReport>('POST', '/api/storage/settings', body),
    onSuccess: (answer) => {
      if (answer) client.setQueryData(STORAGE, answer)
    },
  })
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
  const keep = useSave()
  const encoded = useSave()
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
          {/* Saved as it is changed: it removes nothing, and holds from the next lines read. */}
          <label className="flex cursor-pointer items-center gap-sm pointer-coarse:min-h-touch">
            <input
              checked={archive.enabled}
              className="size-[16px] shrink-0 cursor-pointer accent-planning-thread"
              disabled={keep.isPending}
              type="checkbox"
              onChange={(event) => keep.mutate({ keep_raw: event.target.checked })}
            />
            {t('storage.archive.keep')}
          </label>
          <span aria-live="polite" className="data">
            {t(archive.enabled ? 'storage.archive.on' : 'storage.archive.off')} {archived}
          </span>
          <span className="text-label text-secondary-ink">{t('storage.archive.about')}</span>
          {archive.source === 'environment' && (
            <span className="flex flex-wrap items-baseline gap-x-sm text-label text-secondary-ink">
              {t('storage.archive.started')} <Code>{t('storage.names.keepRaw')}</Code>
            </span>
          )}
          <span aria-live="polite" className="text-label text-secondary-ink" role="status">
            {keep.isError ? t('storage.archive.failed') : keep.isSuccess ? t('storage.archive.saved') : ''}
          </span>
        </Fact>
        <Fact term={t('storage.encoded.term')}>
          <label className="flex cursor-pointer items-center gap-sm pointer-coarse:min-h-touch">
            <input
              checked={archive.keep_encoded}
              className="size-[16px] shrink-0 cursor-pointer accent-planning-thread"
              disabled={encoded.isPending}
              type="checkbox"
              onChange={(event) => encoded.mutate({ keep_encoded: event.target.checked })}
            />
            {t('storage.encoded.keep')}
          </label>
          <span className="data">{t(archive.keep_encoded ? 'storage.encoded.on' : 'storage.encoded.off')}</span>
          <span className="text-label text-secondary-ink">{t('storage.encoded.about')}</span>
          {archive.keep_encoded_source === 'environment' && (
            <span className="flex flex-wrap items-baseline gap-x-sm text-label text-secondary-ink">
              {t('storage.archive.started')} <Code>{t('storage.names.keepEncoded')}</Code>
            </span>
          )}
          <span aria-live="polite" className="text-label text-secondary-ink" role="status">
            {encoded.isError ? t('storage.archive.failed') : encoded.isSuccess ? t('storage.encoded.saved') : ''}
          </span>
          {!archive.keep_encoded && <TrimArchive archive={archive} />}
        </Fact>
      </Facts>
      {store.bytes !== null && <StoreParts />}
    </Section>
  )
}

/** What a limit would remove, said the same way before it is saved and after. */
function useReach() {
  const { t } = useTranslation()
  return (reach: { cutoff: string | null; removable_sessions: number | null }) => {
    const before = formatWhen(reach.cutoff)
    if (reach.removable_sessions === null || !before) return null
    return reach.removable_sessions === 0
      ? t('storage.retention.nothing', { before })
      : t('storage.retention.removable', { count: reach.removable_sessions, shown: formatCount(reach.removable_sessions), before })
  }
}

/** The limit, as pills, with what a new one would remove shown before it is saved. */
function LimitChoice({ retention }: { retention: Retention }) {
  const { t } = useTranslation()
  const reach = useReach()
  const save = useSave()
  // Undefined while the choice is the saved one; a number or null once another is picked.
  const [picked, setPicked] = useState<number | null | undefined>(undefined)
  const chosen = picked === undefined ? retention.days : picked
  const changed = chosen !== retention.days
  const limits = [...new Set([...LIMITS, ...(retention.days === null ? [] : [retention.days])])].sort((a, b) => a - b)
  const trial = useQuery({
    queryKey: ['storage', 'preview', chosen],
    queryFn: ({ signal }) => getJson<RetentionPreview>('/api/storage/retention/preview', { days: chosen }, signal),
    enabled: changed && chosen !== null,
  })
  const next = formatWhen(retention.next_removal_at)
  // "Saved, nothing was removed" stops being true the moment a removal starts.
  const { running } = retention
  const { reset } = save
  useEffect(() => {
    if (running) reset()
  }, [running, reset])
  return (
    <div className="flex flex-col gap-sm">
      <Choice
        label={(option) => (option === EVERYTHING ? t('storage.retention.everything') : t('storage.retention.days', { count: Number(option) }))}
        legend={t('storage.retention.keep')}
        options={[EVERYTHING, ...limits.map(String)]}
        value={chosen === null ? EVERYTHING : String(chosen)}
        onChange={(option) => {
          const days = option === EVERYTHING ? null : Number(option)
          save.reset()
          setPicked(days === retention.days ? undefined : days)
        }}
      />
      {changed && (
        <div className="flex max-w-[70ch] flex-col gap-sm rounded-surface border border-strong-rule bg-canvas-mist p-md">
          {chosen !== null && (
            <p className="text-label text-secondary-ink">
              {t('storage.retention.trial', { limit: t('storage.retention.days', { count: chosen }) })}
            </p>
          )}
          <p aria-live="polite" className="data text-body text-primary-ink">
            {chosen === null
              ? t('storage.retention.trialEverything')
              : trial.isPending || trial.isRefetching
                ? t('storage.retention.trialPending')
                : trial.isError
                  ? t('storage.retention.trialFailed')
                  : reach(trial.data)}
          </p>
          <p className="text-label text-secondary-ink">
            {t(chosen === null ? 'storage.retention.saveEverything' : retention.automatic ? 'storage.retention.saveNote' : 'storage.retention.saveNoteManual')}
          </p>
          <div className="flex flex-wrap items-center gap-sm">
            <button
              aria-busy={save.isPending}
              className="button-primary"
              // A limit is saved only once what it would remove has been shown.
              disabled={save.isPending || (chosen !== null && (!trial.isSuccess || trial.isRefetching))}
              type="button"
              onClick={() => save.mutate({ retention_days: chosen }, { onSuccess: () => setPicked(undefined) })}
            >
              {save.isPending ? t('storage.retention.saving') : t('storage.retention.save')}
            </button>
            {chosen !== null && trial.isError && !trial.isRefetching && (
              <button className="button-secondary" type="button" onClick={() => void trial.refetch()}>
                {t('storage.retention.trialRetry')}
              </button>
            )}
            <button className="button-secondary" disabled={save.isPending} type="button" onClick={() => setPicked(undefined)}>
              {t('storage.retention.cancel')}
            </button>
          </div>
        </div>
      )}
      <p aria-live="polite" className="data text-label text-secondary-ink" role="status">
        {save.isError
          ? t('storage.retention.saveFailed')
          : save.isSuccess && !changed
            ? retention.days !== null && next
              ? t('storage.retention.savedNext', { when: next })
              : t('storage.retention.saved')
            : ''}
      </p>
    </div>
  )
}

/** Removing what is past the limit now: asked for in two steps, and followed while it runs. */
function RemoveNow({ retention }: { retention: Retention }) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const opener = useRef<HTMLButtonElement>(null)
  const keepThem = useRef<HTMLButtonElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  // The focus going back to the opener after "keep them", while it has not happened yet.
  const returning = useRef(0)
  // The question shows what the store holds now, not what it held when the page opened.
  const checking = useIsFetching({ queryKey: STORAGE, exact: true }) > 0
  const ask = useMutation({
    // The time shown goes with the request: nothing last active after it is removed.
    mutationFn: (before: string | null) => sendJson<StorageReport>('POST', '/api/storage/retention/removal', { before }),
    onSuccess: (answer) => {
      if (answer) client.setQueryData(STORAGE, answer)
      setConfirming(false)
      window.cancelAnimationFrame(returning.current)
      // The question is gone; the focus stays in its place rather than falling to the page.
      heading.current?.focus()
    },
  })
  const open = () => {
    // Asked again before the focus was back: it belongs to the question now.
    window.cancelAnimationFrame(returning.current)
    setConfirming(true)
    void client.refetchQueries({ queryKey: STORAGE, exact: true })
  }
  // The safe answer has the focus when the question opens.
  useEffect(() => {
    if (confirming) keepThem.current?.focus()
  }, [confirming])
  const count = retention.removable_sessions ?? 0
  const shown = formatCount(count)
  const before = formatWhen(retention.cutoff)
  const cancel = () => {
    setConfirming(false)
    ask.reset()
    // Back to where the question was asked from, once it is there again.
    returning.current = window.requestAnimationFrame(() => opener.current?.focus())
  }
  return (
    <div className="flex flex-col gap-sm border-t border-hairline pt-md">
      <h3 ref={heading} className="rounded-compact text-body font-semibold text-primary-ink" tabIndex={-1}>
        {t('storage.retention.now')}
      </h3>
      {retention.running ? null : count === 0 ? (
        <p className="text-label text-secondary-ink">{t('storage.retention.nothingNow')}</p>
      ) : confirming ? (
        <div className="flex max-w-[70ch] flex-col gap-sm rounded-surface border border-strong-rule bg-canvas-mist p-md">
          <p className="flex items-start gap-sm text-body font-semibold text-primary-ink">
            <TriangleAlert aria-hidden="true" className="mt-[2px] shrink-0 text-feedback-danger" size={16} strokeWidth={1.75} />
            <span className="data">{t('storage.retention.confirm', { count, shown, before })}</span>
          </p>
          <p className="text-label text-secondary-ink">{t('storage.retention.confirmBody')}</p>
          <div className="flex flex-wrap items-center gap-sm">
            <button
              aria-busy={ask.isPending || checking}
              className="button-primary"
              disabled={ask.isPending || checking}
              type="button"
              onClick={() => ask.mutate(retention.cutoff)}
            >
              <span className="data">{t('storage.retention.remove', { count, shown })}</span>
            </button>
            <button ref={keepThem} className="button-secondary" disabled={ask.isPending} type="button" onClick={cancel}>
              {t('storage.retention.keepThem')}
            </button>
          </div>
        </div>
      ) : (
        <div>
          <button ref={opener} className="button-secondary" type="button" onClick={open}>
            <span className="data">{t('storage.retention.removeNow', { count, shown })}</span>
          </button>
        </div>
      )}
      <p aria-live="polite" className="text-label text-primary-ink" role="status">
        {retention.running ? t('storage.retention.removing') : ask.isError ? t('storage.retention.askFailed') : ''}
      </p>
    </div>
  )
}

function RetentionSection({ retention }: { retention: Retention }) {
  const { t } = useTranslation()
  const reach = useReach()
  const { days, kept_alive: kept, last_removal: last } = retention
  const next = formatWhen(retention.next_removal_at)
  const removes = reach(retention)
  const lastWhen = last ? formatWhen(last.at) : null
  return (
    <Section labelledBy="retention-title" title={t('storage.retention.title')}>
      <p className="max-w-[70ch] text-body text-primary-ink">
        {days === null
          ? t('storage.retention.off')
          : t(retention.automatic ? 'storage.retention.on' : 'storage.retention.onManual', { count: days })}
      </p>
      <LimitChoice retention={retention} />
      <Facts>
        <Fact term={t('storage.retention.setBy')}>
          {retention.source === 'environment' ? (
            <>
              <Code>{days === null ? t('storage.names.retention') : t('storage.names.retentionDays', { days })}</Code>
              <span className="text-label text-secondary-ink">{t('storage.retention.setWhere')}</span>
            </>
          ) : (
            t(retention.source === 'page' ? 'storage.retention.setHere' : 'storage.retention.setNowhere')
          )}
        </Fact>
        {days !== null && removes && (
          <Fact term={t('storage.retention.wouldRemove')}>
            <span className="data">{removes}</span>
          </Fact>
        )}
        {days !== null && kept !== null && kept > 0 && (
          <Fact term={t('storage.retention.keeps')}>
            <span className="data">{t('storage.retention.kept', { count: kept, shown: formatCount(kept) })}</span>
          </Fact>
        )}
        {days !== null && (
          <Fact term={t('storage.retention.removed')}>
            <span className="data">
              {retention.automatic && next ? t('storage.retention.daily', { when: next }) : t('storage.retention.byCommand')}
            </span>
          </Fact>
        )}
        {(days !== null || last) && (
          <Fact term={t('storage.retention.last')}>
            <span aria-live="polite" className="data">
              {!last || !lastWhen
                ? t('storage.retention.never')
                : last.error
                  ? t('storage.retention.lastFailed', { when: lastWhen, error: last.error })
                  : last.sessions === 0
                    ? t('storage.retention.lastNothing', { when: lastWhen })
                    : t('storage.retention.lastRemoved', {
                        when: lastWhen,
                        sessions: t('storage.retention.sessions', { count: last.sessions, shown: formatCount(last.sessions) }),
                        events: t('storage.retention.events', { count: last.events, shown: formatCount(last.events) }),
                      })}
            </span>
            <span className="text-label text-secondary-ink">{t('storage.retention.fileStays')}</span>
          </Fact>
        )}
      </Facts>
      {days !== null && retention.automatic && <RemoveNow retention={retention} />}
      <Commands
        rows={
          days === null
            ? [
                {
                  command: t('storage.names.preview', { days: EXAMPLE_DAYS }),
                  does: t('storage.retention.previewExample', { count: EXAMPLE_DAYS }),
                },
              ]
            : [
                { command: t('storage.names.previewSaved'), does: t('storage.retention.preview') },
                { command: t('storage.names.applySaved'), does: t('storage.retention.apply') },
              ]
        }
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
  const client = useQueryClient()
  const report = useQuery({
    queryKey: STORAGE,
    queryFn: ({ signal }) => getJson<StorageReport>('/api/storage', {}, signal),
    // While sessions are being removed the page follows it, and stops asking once it is over.
    refetchInterval: ({ state }) => (state.data?.retention.running || state.data?.archive.trimming ? FOLLOW_MS : false),
  })
  // Once a removal is over, every list fetched before it holds sessions that are gone.
  const running = report.data?.retention.running === true
  const was = useRef(false)
  useEffect(() => {
    if (was.current && !running) {
      void client.invalidateQueries({ predicate: (query) => query.queryKey.length > 1 || query.queryKey[0] !== STORAGE[0] })
    }
    was.current = running
  }, [running, client])
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
