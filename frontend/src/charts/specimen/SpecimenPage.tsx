import { useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { BarChart } from '../BarChart'
import { ColumnChart } from '../ColumnChart'
import type { FigureStatus } from '../Figure'
import { formatValue } from '../format'
import { LineChart } from '../LineChart'
import { ShareBar } from '../ShareBar'
import { Heatmap } from '../Heatmap'
import { Lanes } from '../Lanes'
import type { Range } from '../range'
import { SelectionWindow } from '../SelectionWindow'
import { SmallMultiples } from '../SmallMultiples'
import { StateStrips } from '../StateStrips'
import { Tree } from '../Tree'
import { agentTree, categories, daily, harnesses, heatmap, hourly, lanes, manyNames, strips } from './samples'

const STATES = ['ready', 'held', 'loading', 'empty', 'unreported', 'error', 'dense'] as const
type State = (typeof STATES)[number]
/** Figures with no number to leave unreported skip that state. */
const PLAIN_STATES = STATES.filter((state) => state !== 'unreported')
const nothing = (state: State, status: FigureStatus) => state === 'loading' || state === 'error' || status === 'loading'

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

function LanesSample({ state }: { state: State }) {
  const { t } = useTranslation()
  const status = useHeld(state)
  const sample = state === 'empty' || nothing(state, status) ? undefined : lanes(state === 'dense')
  return (
    <Lanes
      caption={t(state === 'dense' ? 'charts.specimen.lanesDenseCaption' : 'charts.specimen.lanesCaption')}
      lanes={state === 'empty' ? [] : sample?.lanes}
      now={sample?.now}
      rowHeader={t('charts.specimen.agent')}
      status={status}
      title={t(`charts.specimen.states.${state}`)}
      onRetry={() => undefined}
    />
  )
}

function StripsSample({ state }: { state: State }) {
  const { t } = useTranslation()
  const status = useHeld(state)
  return (
    <StateStrips
      caption={t('charts.specimen.stripsCaption')}
      rowHeader={t('charts.specimen.session')}
      status={status}
      strips={state === 'empty' ? [] : nothing(state, status) ? undefined : strips(state === 'dense')}
      title={t(`charts.specimen.states.${state}`)}
      onRetry={() => undefined}
    />
  )
}

function HeatmapSample({ state }: { state: State }) {
  const { t } = useTranslation()
  const status = useHeld(state)
  return (
    <Heatmap
      caption={t(state === 'dense' ? 'charts.specimen.heatmapDenseCaption' : 'charts.specimen.heatmapCaption')}
      data={
        state === 'empty'
          ? { rows: [], columns: [], values: [] }
          : nothing(state, status)
            ? undefined
            : heatmap({ dense: state === 'dense', unreported: state === 'unreported' })
      }
      rowHeader={t(state === 'dense' ? 'charts.specimen.tool' : 'charts.specimen.weekday')}
      status={status}
      title={t(`charts.specimen.states.${state}`)}
      onRetry={() => undefined}
    />
  )
}

function TreeSample({ state }: { state: State }) {
  const { t } = useTranslation()
  const status = useHeld(state)
  return (
    <Tree
      caption={t('charts.specimen.treeCaption')}
      root={state === 'empty' || nothing(state, status) ? undefined : agentTree(state === 'dense')}
      rowHeader={t('charts.specimen.agent')}
      status={state === 'empty' ? 'ready' : status}
      title={t(`charts.specimen.states.${state}`)}
      onRetry={() => undefined}
    />
  )
}

function SelectionSample({ state }: { state: State }) {
  const { t } = useTranslation()
  const status = useHeld(state)
  const [range, setRange] = useState<Range>(state === 'dense' ? [120, 150] : [3, 8])
  const data =
    state === 'empty'
      ? { ...daily(), series: [] }
      : nothing(state, status)
        ? undefined
        : state === 'dense'
          ? daily(harnesses.slice(0, 1), { buckets: 180, seed: 6 })
          : state === 'unreported'
            ? daily(harnesses.slice(0, 1), { gaps: [[5, 6]] })
            : daily()
  return (
    <SelectionWindow
      caption={t('charts.specimen.selectionCaption')}
      data={data}
      rowHeader={t('charts.specimen.day')}
      status={status}
      title={t(`charts.specimen.states.${state}`)}
      value={range}
      onChange={setRange}
      onRetry={() => undefined}
    />
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
      <Section title={t('charts.specimen.lanes')}>
        {PLAIN_STATES.map((state) => (
          <LanesSample key={state} state={state} />
        ))}
      </Section>
      <Section title={t('charts.specimen.strips')}>
        {PLAIN_STATES.map((state) => (
          <StripsSample key={state} state={state} />
        ))}
      </Section>
      <Section title={t('charts.specimen.heatmap')}>
        {STATES.map((state) => (
          <HeatmapSample key={state} state={state} />
        ))}
      </Section>
      <Section title={t('charts.specimen.tree')}>
        {PLAIN_STATES.map((state) => (
          <TreeSample key={state} state={state} />
        ))}
      </Section>
      <Section title={t('charts.specimen.selection')}>
        {STATES.map((state) => (
          <SelectionSample key={state} state={state} />
        ))}
      </Section>
      <section className="flex flex-col gap-md">
        <h2 className="text-headline text-primary-ink">{t('charts.specimen.shares')}</h2>
        <ShareBars />
      </section>
    </main>
  )
}
