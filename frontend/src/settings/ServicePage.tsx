import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { getJson } from '../api/client'
import type { ServicePoint, ServiceRange, ServiceUsageReport } from '../api/types'
import { BarChart, LineChart, type FigureStatus, type TimeData } from '../charts'
import { formatWhen } from '../history/format'
import { PageHeader } from '../history/PageHeader'
import { ErrorNotice, LoadingRows } from '../history/SessionList'
import { Choice } from '../shell/Choice'
import { Section } from '../workchord/WorkChordPage'
import { Fact, Facts } from './Facts'
import { formatCount, useSize } from './format'

const BASE = 'workspaces.settings.destinations.service'
const RANGES: readonly ServiceRange[] = ['hour', 'day', 'quarter']
// The service samples itself every ten seconds; the longer ranges change by the minute.
const FOLLOW_MS: Record<ServiceRange, number> = { hour: 10_000, day: 60_000, quarter: 60_000 }
const PARTS = ['ingest', 'probe', 'watch', 'mirror', 'retention', 'api', 'monitor', 'other']
const tenths = new Intl.NumberFormat('en', { maximumFractionDigits: 1 })

/** The formatters the page's figures and facts share: a share of one core, a size, a rate. */
function useFormats() {
  const { t } = useTranslation()
  const size = useSize()
  return {
    size,
    percent: (value: number) => t('service.percent', { value: tenths.format(value) }),
    rate: (value: number) => t('service.rate', { size: size(value) }),
  }
}

/** One measure of the points as a series over their steps. */
function series(report: ServiceUsageReport, measures: { id: string; name: string; of: (point: ServicePoint) => number | null }[]): TimeData {
  return {
    buckets: report.points.map((point) => new Date(point.at)),
    step: report.step_seconds * 1000,
    series: measures.map((measure) => ({ id: measure.id, name: measure.name, values: report.points.map(measure.of) })),
  }
}

function NowSection({ report }: { report: ServiceUsageReport }) {
  const { t } = useTranslation()
  const { size, percent, rate } = useFormats()
  const now = report.now
  const reported = (value: number | null, format: (value: number) => string) =>
    value === null ? <span className="text-secondary-ink">{t('service.notReported')}</span> : <span className="data">{format(value)}</span>
  return (
    <Section labelledBy="now-title" title={t('service.now.title')}>
      {now === null ? (
        <p className="max-w-[70ch] text-body text-secondary-ink">{t(report.known ? 'service.now.soon' : 'service.now.never')}</p>
      ) : (
        <>
          <p className="max-w-[70ch] text-body text-secondary-ink">
            {t(report.known ? 'service.now.lead' : 'service.now.kept', { when: formatWhen(now.at) })}
          </p>
          <Facts>
            <Fact term={t('service.now.cpu')}>
              {reported(now.cpu_percent, percent)}
              {report.cpus !== null && (
                <span className="text-label text-secondary-ink">
                  {t('service.now.machine', { count: report.cpus, shown: formatCount(report.cpus) })}
                </span>
              )}
            </Fact>
            <Fact term={t('service.now.memory')}>
              {reported(now.footprint_bytes, size)}
              {now.resident_bytes !== null && (
                <span className="text-label text-secondary-ink">{t('service.now.resident', { size: size(now.resident_bytes) })}</span>
              )}
            </Fact>
            <Fact term={t('service.now.read')}>{reported(now.read_bytes_per_second, rate)}</Fact>
            <Fact term={t('service.now.written')}>{reported(now.written_bytes_per_second, rate)}</Fact>
            <Fact term={t('service.now.store')}>
              {reported(now.store_bytes, size)}
              {now.free_bytes !== null && (
                <span className="text-label text-secondary-ink">{t('service.now.free', { size: size(now.free_bytes) })}</span>
              )}
            </Fact>
            {report.since && (
              <Fact term={t('service.now.since')}>
                <span className="data">{formatWhen(report.since)}</span>
              </Fact>
            )}
          </Facts>
        </>
      )}
    </Section>
  )
}

/** What the agdash service itself costs this machine: now, over time, and by part. */
export function ServicePage() {
  const { t } = useTranslation()
  const { size, percent, rate } = useFormats()
  const [search, setSearch] = useSearchParams()
  const asked = search.get('range') as ServiceRange | null
  const range: ServiceRange = asked && RANGES.includes(asked) ? asked : 'hour'
  const usage = useQuery({
    queryKey: ['service', 'usage', range],
    queryFn: ({ signal }) => getJson<ServiceUsageReport>('/api/service/usage', { range }, signal),
    placeholderData: keepPreviousData,
    refetchInterval: FOLLOW_MS[range],
  })
  // The hour, the default, leaves the address, so the plain page has a plain address.
  const choose = (next: ServiceRange) => {
    const params = new URLSearchParams(search)
    if (next === 'hour') params.delete('range')
    else params.set('range', next)
    setSearch(params)
  }
  const report = usage.data
  const drawn = report && !usage.isPlaceholderData ? report : undefined
  const status: FigureStatus = usage.isError ? 'error' : usage.isFetching && !drawn ? 'loading' : 'ready'
  const retry = () => void usage.refetch()
  const figure = { rowHeader: t('service.over.time'), status, onRetry: retry }
  return (
    <article aria-labelledby="page-title" className="flex flex-col gap-page-gap">
      <PageHeader purposeKey={`${BASE}.purpose`} titleKey={`${BASE}.title`} />
      {usage.isPending ? (
        <LoadingRows label={t('service.loading')} />
      ) : !report ? (
        <ErrorNotice error={usage.error} scope="service.error" onRetry={retry} />
      ) : (
        <>
          <NowSection report={report} />
          <Section labelledBy="over-title" title={t('service.over.title')}>
            <Choice
              label={(option) => t(`service.over.ranges.${option}`)}
              legend={t('service.over.range')}
              options={RANGES}
              value={range}
              onChange={choose}
            />
            <LineChart
              {...figure}
              caption={t('service.over.cpu.caption')}
              data={drawn && series(drawn, [{ id: 'cpu', name: t('service.now.cpu'), of: (point) => point.cpu_percent }])}
              formatAxis={(value) => tenths.format(value)}
              formatValue={percent}
              title={t('service.over.cpu.title')}
            />
            <LineChart
              {...figure}
              caption={t('service.over.memory.caption')}
              data={drawn && series(drawn, [{ id: 'memory', name: t('service.now.memory'), of: (point) => point.footprint_bytes }])}
              formatAxis={size}
              formatValue={size}
              title={t('service.over.memory.title')}
            />
            {/* Apart, each on its own scale: what is written is a small fraction of what is read. */}
            <LineChart
              {...figure}
              caption={t('service.over.read.caption')}
              data={drawn && series(drawn, [{ id: 'read', name: t('service.now.read'), of: (point) => point.read_bytes_per_second }])}
              formatAxis={size}
              formatValue={rate}
              title={t('service.over.read.title')}
            />
            <LineChart
              {...figure}
              caption={t('service.over.written.caption')}
              data={drawn && series(drawn, [{ id: 'written', name: t('service.now.written'), of: (point) => point.written_bytes_per_second }])}
              formatAxis={size}
              formatValue={rate}
              title={t('service.over.written.title')}
            />
            <LineChart
              {...figure}
              caption={t('service.over.store.caption')}
              data={drawn && series(drawn, [{ id: 'store', name: t('service.now.store'), of: (point) => point.store_bytes }])}
              formatAxis={size}
              formatValue={size}
              title={t('service.over.store.title')}
            />
          </Section>
          <Section labelledBy="parts-title" title={t('service.parts.title')}>
            <p className="max-w-[70ch] text-body text-secondary-ink">{t('service.parts.lead')}</p>
            <BarChart
              caption={t(`service.parts.captions.${range}`)}
              categories={drawn?.parts.map((part) => ({
                id: part.part,
                name: t(`service.parts.names.${PARTS.includes(part.part) ? part.part : 'other'}`),
                value: part.cpu_percent,
              }))}
              formatValue={percent}
              rowHeader={t('service.parts.part')}
              status={status}
              title={t('service.parts.figure')}
              valueHeader={t('service.parts.value')}
              onRetry={retry}
            />
          </Section>
        </>
      )}
    </article>
  )
}
