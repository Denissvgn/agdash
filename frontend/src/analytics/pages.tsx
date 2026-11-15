import { useQuery } from '@tanstack/react-query'
import { useId, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { getJson } from '../api/client'
import type { ToolReport, UsageGroup, UsageReport, UsageRow } from '../api/types'
import { FilterBar } from '../history/FilterBar'
import { apiParams, isFiltered } from '../history/filters'
import { useAddressFilters, useFacets } from '../history/hooks'
import { PageHeader } from '../history/PageHeader'
import { ErrorNotice, LoadingRows, Notice } from '../history/SessionList'
import { formatCost, formatCount, splitDuration } from './format'

const GROUPS: UsageGroup[] = ['harness', 'model', 'folder']
const TOKENS = ['input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens'] as const

/** The share of sessions a figure covers, when it is not all of them. */
function Coverage({ count, total }: { count: number | null | undefined; total: number }) {
  const { t } = useTranslation()
  if (count === null || count === undefined || count >= total) return null
  return <span className="block text-[0.75rem] font-normal text-secondary-ink">{t('analytics.usage.from', { count, total })}</span>
}

/**
 * A number the harness reported, or a dash that says it did not; never a zero in
 * its place. A sum built from only some sessions says how many.
 */
function Value({
  value,
  format = formatCount,
  covered,
  of,
}: {
  value: number | null
  format?: (value: number) => string
  covered?: number
  of?: number
}) {
  const { t } = useTranslation()
  if (value === null) {
    return (
      <>
        <span aria-hidden="true" className="text-secondary-ink">
          —
        </span>
        <span className="sr-only">{t('analytics.notReported')}</span>
      </>
    )
  }
  return (
    <>
      {format(value)}
      {of !== undefined && <Coverage count={covered} total={of} />}
    </>
  )
}

function Duration({ row }: { row: UsageRow }) {
  const { t } = useTranslation()
  if (row.duration_seconds === null) return <Value value={null} />
  const { hours, minutes } = splitDuration(row.duration_seconds)
  return (
    <>
      {hours ? t('analytics.duration.hours', { hours, minutes }) : t('analytics.duration.minutes', { minutes })}
      <Coverage count={row.sessions_with_duration} total={row.sessions} />
    </>
  )
}

function GroupSwitch({ group, onChange }: { group: UsageGroup; onChange: (group: UsageGroup) => void }) {
  const { t } = useTranslation()
  const name = useId()
  return (
    <fieldset className="flex flex-wrap items-center gap-sm">
      <legend className="float-left mr-sm text-label text-secondary-ink">{t('analytics.groupBy')}</legend>
      {GROUPS.map((value) => (
        <label key={value} className="toggle-chip relative cursor-pointer has-[:checked]:border-planning-thread has-[:checked]:bg-planning-thread-soft has-[:checked]:text-planning-thread has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-planning-thread">
          <input
            checked={group === value}
            className="sr-only"
            name={name}
            type="radio"
            value={value}
            onChange={() => onChange(value)}
          />
          {t(`analytics.groups.${value}`)}
        </label>
      ))}
    </fieldset>
  )
}

function GroupKey({ group, value }: { group: UsageGroup; value: string | null }) {
  const { t } = useTranslation()
  if (value === null) return <span className="text-secondary-ink">{t('analytics.notRecorded')}</span>
  if (group === 'harness') return <>{t(`history.harnesses.${value}`, { defaultValue: value })}</>
  if (group === 'folder') {
    return (
      <span className="data block max-w-[28ch] truncate text-left font-mono text-[0.75rem] [direction:rtl]">
        <bdi>{value}</bdi>
      </span>
    )
  }
  return <>{value}</>
}

function Sessions({ row }: { row: UsageRow }) {
  const { t } = useTranslation()
  const partial = row.sessions_with_usage < row.sessions
  return (
    <>
      {formatCount(row.sessions)}
      {partial && (
        <span className="block text-[0.75rem] text-secondary-ink">
          {t('analytics.usage.reported', { count: row.sessions_with_usage })}
        </span>
      )}
    </>
  )
}

const cell = 'whitespace-nowrap px-[12px] py-sm text-right align-top'
const headBase = 'whitespace-nowrap px-[12px] py-sm align-bottom text-label font-medium text-secondary-ink'
// The first column stays in view when a narrow window scrolls the table sideways.
const sticky = 'sticky left-0 z-[1] bg-working-paper'
const rowHead = `whitespace-nowrap px-[12px] py-sm text-left align-top font-normal ${sticky}`
const head = `${headBase} text-right`
const headFirst = `${headBase} text-left ${sticky}`

/** A table box that scrolls sideways on narrow windows and can be scrolled from the keyboard. */
function TableBox({ captionId, children }: { captionId: string; children: ReactNode }) {
  return (
    <div
      aria-labelledby={captionId}
      className="relative overflow-x-auto rounded-surface border border-hairline bg-working-paper shadow-resting"
      role="region"
      tabIndex={0}
    >
      {children}
    </div>
  )
}

function UsageTable({ report }: { report: UsageReport }) {
  const { t } = useTranslation()
  const captionId = useId()
  // Time belongs to sessions, not models; the note under the table says so.
  const showTime = report.group !== 'model'
  const row = (item: UsageRow, total = false) => (
    <tr key={total ? 'total' : (item.key ?? 'none')} className={total ? 'font-semibold' : undefined}>
      <th className={rowHead} scope="row">
        {total ? <span className="font-semibold">{t('analytics.total')}</span> : <GroupKey group={report.group} value={item.key} />}
      </th>
      <td className={cell}>
        <Sessions row={item} />
      </td>
      {TOKENS.map((metric) => (
        <td key={metric} className={cell}>
          <Value covered={item.reported_by?.[metric]} of={item.sessions_with_usage} value={item[metric]} />
        </td>
      ))}
      <td className={cell}>
        <Value
          covered={item.reported_by?.reported_cost}
          format={formatCost}
          of={item.sessions_with_usage}
          value={item.reported_cost}
        />
      </td>
      {showTime && (
        <td className={cell}>
          <Duration row={item} />
        </td>
      )}
    </tr>
  )
  return (
    <TableBox captionId={captionId}>
      <table className="data w-full border-collapse text-body">
        <caption className="sr-only" id={captionId}>
          {t('analytics.usage.caption', { group: t(`analytics.groups.${report.group}`) })}
        </caption>
        <thead className="border-b border-hairline">
          <tr>
            <th className={headFirst} rowSpan={2} scope="col">
              {t(`analytics.groups.${report.group}`)}
            </th>
            <th className={head} rowSpan={2} scope="col">
              {t('analytics.usage.sessions')}
            </th>
            <th className={`${headBase} pb-0 text-center`} colSpan={TOKENS.length} scope="colgroup">
              {t('analytics.usage.tokens')}
            </th>
            <th className={head} rowSpan={2} scope="col">
              {t('analytics.usage.cost')}
            </th>
            {showTime && (
              <th className={head} rowSpan={2} scope="col">
                {t('analytics.usage.time')}
              </th>
            )}
          </tr>
          <tr>
            {TOKENS.map((metric) => (
              <th key={metric} className={head} scope="col">
                {t(`analytics.usage.${metric}`)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-hairline">{report.rows.map((item) => row(item))}</tbody>
        <tfoot className="border-t border-strong-rule">{row(report.total, true)}</tfoot>
      </table>
    </TableBox>
  )
}

export function UsagePage() {
  const { t } = useTranslation()
  const [filters, setFilters, search] = useAddressFilters()
  const [, setSearch] = useSearchParams()
  const facets = useFacets()
  const group = (GROUPS as string[]).includes(search.get('group') ?? '') ? (search.get('group') as UsageGroup) : 'harness'
  const report = useQuery({
    queryKey: ['analytics-usage', group, filters],
    queryFn: ({ signal }) => getJson<UsageReport>('/api/analytics/usage', { ...apiParams(filters), group }, signal),
  })
  const changeGroup = (next: UsageGroup) => {
    const params = new URLSearchParams(search)
    if (next === 'harness') params.delete('group')
    else params.set('group', next)
    setSearch(params)
  }
  const base = 'workspaces.analytics.destinations.usage'
  const data = report.data
  return (
    <article aria-labelledby="page-title" className="flex flex-col gap-page-gap">
      <PageHeader purposeKey={`${base}.purpose`} titleKey={`${base}.title`} />
      <FilterBar facets={facets.data} filters={filters} onChange={setFilters} />
      <GroupSwitch group={group} onChange={changeGroup} />
      {report.isPending ? (
        <LoadingRows label={t('analytics.loading')} />
      ) : report.isError ? (
        <ErrorNotice error={report.error} onRetry={() => void report.refetch()} />
      ) : !data || data.total.sessions === 0 ? (
        isFiltered(filters) ? (
          <Notice body={t('history.empty.filteredBody')} title={t('history.empty.filteredTitle')} />
        ) : (
          <Notice body={t('history.empty.noneBody')} title={t('history.empty.noneTitle')} />
        )
      ) : (
        <>
          <UsageTable report={data} />
          <ul className="flex max-w-[80ch] flex-col gap-xs text-label text-secondary-ink">
            <li>{t('analytics.usage.noteDash')}</li>
            {data.sessions_without_usage > 0 && (
              <li>{t('analytics.usage.noteUnreported', { count: data.sessions_without_usage })}</li>
            )}
            {group === 'model' ? <li>{t('analytics.usage.noteModelTime')}</li> : <li>{t('analytics.usage.noteElapsed')}</li>}
          </ul>
        </>
      )}
    </article>
  )
}

export function ToolsPage() {
  const { t } = useTranslation()
  const captionId = useId()
  const [filters, setFilters] = useAddressFilters()
  const facets = useFacets()
  const report = useQuery({
    queryKey: ['analytics-tools', filters],
    queryFn: ({ signal }) => getJson<ToolReport>('/api/analytics/tools', apiParams(filters), signal),
  })
  const base = 'workspaces.analytics.destinations.tools'
  const data = report.data
  return (
    <article aria-labelledby="page-title" className="flex flex-col gap-page-gap">
      <PageHeader purposeKey={`${base}.purpose`} titleKey={`${base}.title`} />
      <FilterBar facets={facets.data} filters={filters} onChange={setFilters} />
      {report.isPending ? (
        <LoadingRows label={t('analytics.loading')} />
      ) : report.isError ? (
        <ErrorNotice error={report.error} onRetry={() => void report.refetch()} />
      ) : !data || data.total_calls === 0 ? (
        <Notice body={t('analytics.tools.noneBody')} title={t('analytics.tools.noneTitle')} />
      ) : (
        <>
          <TableBox captionId={captionId}>
            <table className="data w-full border-collapse text-body">
              <caption className="sr-only" id={captionId}>
                {t('analytics.tools.caption')}
              </caption>
              <thead className="border-b border-hairline">
                <tr>
                  <th className={headFirst} scope="col">
                    {t('analytics.tools.tool')}
                  </th>
                  <th className={head} scope="col">
                    {t('analytics.tools.calls')}
                  </th>
                  <th className={head} scope="col">
                    {t('analytics.tools.sessions')}
                  </th>
                  <th className={head} scope="col">
                    {t('analytics.tools.errors')}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline">
                {data.rows.map((tool) => (
                  <tr key={tool.name}>
                    <th className={`${rowHead} font-mono text-[0.8125rem]`} scope="row">
                      {tool.name}
                    </th>
                    <td className={cell}>{formatCount(tool.calls)}</td>
                    <td className={cell}>{formatCount(tool.sessions)}</td>
                    <td className={cell}>
                      {tool.outcomes_known ? (
                        <>
                          {formatCount(tool.errors)}
                          <span className="block text-[0.75rem] text-secondary-ink">
                            {t('analytics.tools.matched', { count: tool.outcomes_known })}
                          </span>
                        </>
                      ) : (
                        <Value value={null} />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="border-t border-strong-rule font-semibold">
                <tr>
                  <th className={`${rowHead} font-semibold`} scope="row">
                    {t('analytics.total')}
                  </th>
                  <td className={cell}>{formatCount(data.total_calls)}</td>
                  <td className={cell}>{formatCount(data.total_sessions)}</td>
                  <td className={cell}>
                    {data.outcomes_known ? (
                      <>
                        {formatCount(data.errors)}
                        <span className="block text-[0.75rem] font-normal text-secondary-ink">
                          {t('analytics.tools.matched', { count: data.outcomes_known })}
                        </span>
                      </>
                    ) : (
                      <Value value={null} />
                    )}
                  </td>
                </tr>
              </tfoot>
            </table>
          </TableBox>
          <ul className="flex max-w-[80ch] flex-col gap-xs text-label text-secondary-ink">
            <li>{t('analytics.tools.noteErrors')}</li>
            {data.truncated && <li>{t('analytics.tools.noteTruncated', { count: data.rows.length })}</li>}
          </ul>
        </>
      )}
    </article>
  )
}
