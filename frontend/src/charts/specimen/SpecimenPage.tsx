import { useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { BarChart } from '../BarChart'
import { ColumnChart } from '../ColumnChart'
import type { FigureStatus } from '../Figure'
import { formatValue } from '../format'
import { LineChart } from '../LineChart'
import { ShareBar } from '../ShareBar'
import { SmallMultiples } from '../SmallMultiples'
import { categories, daily, harnesses, hourly, manyNames } from './samples'

const STATES = ['ready', 'held', 'loading', 'empty', 'unreported', 'error', 'dense'] as const
type State = (typeof STATES)[number]

/** Shows a figure drawn, then loading with nothing new yet, so the held drawing is on view. */
function useHeld(state: State): FigureStatus {
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    if (state !== 'held') return undefined
    const timer = window.setTimeout(() => setLoading(true), 0)
    return () => window.clearTimeout(timer)
  }, [state])
  if (state === 'held') return loading ? 'loading' : 'ready'
  if (state === 'loading' || state === 'error') return state
  return 'ready'
}

function Bars({ state }: { state: State }) {
  const { t } = useTranslation()
  const status = useHeld(state)
  const data =
    state === 'empty' ? [] : state === 'loading' || state === 'error' || status === 'loading' ? undefined : categories(state === 'dense', state === 'unreported')
  return (
    <BarChart
      caption={t('charts.specimen.barsCaption')}
      categories={data}
      rowHeader={t('charts.specimen.tool')}
      selected={state === 'ready' ? 'Edit' : undefined}
      status={status}
      title={t(`charts.specimen.states.${state}`)}
      valueHeader={t('charts.specimen.calls')}
      onRetry={() => undefined}
    />
  )
}

function Columns({ state }: { state: State }) {
  const { t } = useTranslation()
  const status = useHeld(state)
  const data =
    state === 'empty'
      ? { ...daily(), series: [] }
      : state === 'loading' || state === 'error' || status === 'loading'
        ? undefined
        : state === 'dense'
          ? daily(manyNames, { buckets: 90, seed: 4 })
          : state === 'unreported'
            ? daily(harnesses.slice(0, 2), { gaps: [[4, 6]] })
            : daily(harnesses.slice(0, state === 'ready' ? 3 : 1))
  return (
    <ColumnChart
      caption={t('charts.specimen.columnsCaption')}
      data={data}
      rowHeader={t('charts.specimen.day')}
      status={status}
      title={t(`charts.specimen.states.${state}`)}
      onRetry={() => undefined}
    />
  )
}

function Lines({ state, variant }: { state: State; variant: 'line' | 'step' }) {
  const { t } = useTranslation()
  const status = useHeld(state)
  const data =
    state === 'empty'
      ? { ...daily(), series: [] }
      : state === 'loading' || state === 'error' || status === 'loading'
        ? undefined
        : state === 'dense'
          ? variant === 'line'
            ? daily(manyNames, { buckets: 90, seed: 9 })
            : hourly()
          : state === 'unreported'
            ? daily(harnesses.slice(0, variant === 'line' ? 2 : 1), { gaps: [[3, 5], [9, 9]] })
            : daily(harnesses.slice(0, variant === 'line' ? 2 : 1))
  return (
    <LineChart
      caption={t(variant === 'line' ? 'charts.specimen.linesCaption' : 'charts.specimen.stepsCaption')}
      data={data}
      rowHeader={t('charts.specimen.day')}
      status={status}
      title={t(`charts.specimen.states.${state}`)}
      variant={variant}
      onRetry={() => undefined}
    />
  )
}

function Multiples({ state }: { state: State }) {
  const { t } = useTranslation()
  const status = useHeld(state)
  const data =
    state === 'empty'
      ? { ...daily(), series: [] }
      : state === 'loading' || state === 'error' || status === 'loading'
        ? undefined
        : state === 'dense'
          ? daily(manyNames, { buckets: 60, seed: 2 })
          : state === 'unreported'
            ? daily(harnesses, { gaps: [[2, 4]] })
            : daily(harnesses)
  return (
    <SmallMultiples
      caption={t('charts.specimen.multiplesCaption')}
      data={data}
      rowHeader={t('charts.specimen.day')}
      status={status}
      title={t(`charts.specimen.states.${state}`)}
      variant={state === 'dense' ? 'columns' : 'step'}
      onRetry={() => undefined}
    />
  )
}

function ShareBars() {
  const { t } = useTranslation()
  const rows = categories(false, true).slice(0, 6)
  const max = Math.max(...rows.map((row) => row.value ?? 0))
  return (
    <div className="overflow-x-auto rounded-surface border border-hairline bg-working-paper shadow-resting">
      <table className="w-full border-collapse text-left text-label tabular-nums">
        <caption className="sr-only">{t('charts.specimen.shareCaption')}</caption>
        <thead>
          <tr className="border-b border-hairline">
            <th className="px-sm py-xs font-medium text-secondary-ink" scope="col">
              {t('charts.specimen.tool')}
            </th>
            <th className="px-sm py-xs text-right font-medium text-secondary-ink" scope="col">
              {t('charts.specimen.calls')}
            </th>
            <th className="w-1/2 px-sm py-xs font-medium text-secondary-ink" scope="col">
              {t('charts.specimen.share')}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="border-b border-hairline last:border-b-0">
              <th className="px-sm py-xs font-normal text-primary-ink" scope="row">
                <span data-figure-data="">{row.name}</span>
              </th>
              <td className="px-sm py-xs text-right text-primary-ink">
                {row.value === null ? (
                  <>
                    <span aria-hidden="true" className="text-secondary-ink">
                      {t('charts.table.dash')}
                    </span>
                    <span className="sr-only">{t('charts.table.notReported')}</span>
                  </>
                ) : (
                  <span data-figure-data="">{formatValue(row.value)}</span>
                )}
              </td>
              <td className="px-sm py-xs">
                <ShareBar max={max} value={row.value} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-md">
      <h2 className="text-headline text-primary-ink">{title}</h2>
      <div className="grid grid-cols-1 gap-md wide:grid-cols-2">{children}</div>
    </section>
  )
}

/**
 * Every chart-kit component in every state, from synthetic data. A development page
 * only: it is not part of the built app.
 */
export function SpecimenPage() {
  const { t } = useTranslation()
  return (
    <main className="mx-auto flex max-w-page flex-col gap-xl px-phone-x py-lg roomy:px-page-x">
      <header className="flex flex-col gap-xs">
        <h1 className="text-display text-primary-ink">{t('charts.specimen.title')}</h1>
        <p className="max-w-[44rem] text-body text-secondary-ink">{t('charts.specimen.intro')}</p>
      </header>
      <Section title={t('charts.specimen.bars')}>
        {STATES.map((state) => (
          <Bars key={state} state={state} />
        ))}
      </Section>
      <Section title={t('charts.specimen.columns')}>
        {STATES.map((state) => (
          <Columns key={state} state={state} />
        ))}
      </Section>
      <Section title={t('charts.specimen.lines')}>
        {STATES.map((state) => (
          <Lines key={state} state={state} variant="line" />
        ))}
      </Section>
      <Section title={t('charts.specimen.steps')}>
        {STATES.map((state) => (
          <Lines key={state} state={state} variant="step" />
        ))}
      </Section>
      <section className="flex flex-col gap-md">
        <h2 className="text-headline text-primary-ink">{t('charts.specimen.multiples')}</h2>
        <div className="flex flex-col gap-md">
          {STATES.map((state) => (
            <Multiples key={state} state={state} />
          ))}
        </div>
      </section>
      <section className="flex flex-col gap-md">
        <h2 className="text-headline text-primary-ink">{t('charts.specimen.shares')}</h2>
        <ShareBars />
      </section>
    </main>
  )
}
