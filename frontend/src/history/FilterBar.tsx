import { ChevronDown, X } from 'lucide-react'
import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Facets } from '../api/types'
import { CHOICE_PILL } from '../shell/Choice'
import { datesOf, isFiltered, NO_FILTERS, PRESETS, type Filters, type Preset } from './filters'

interface Props {
  filters: Filters
  facets: Facets | undefined
  onChange: (filters: Filters) => void
  /** Session counts on the harness pills; off where they would not describe the results. */
  showCounts?: boolean
}

function Field({ label, children, id }: { label: string; children: React.ReactNode; id: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-xs">
      <label className="text-label text-secondary-ink" htmlFor={id}>
        {label}
      </label>
      {children}
    </div>
  )
}

/**
 * Filters for a session list. Every filter narrows the list; they combine, and
 * the page keeps them in its address.
 */
export function FilterBar({ filters, facets, onChange, showCounts = true }: Props) {
  const { t } = useTranslation()
  const id = useId()
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch })
  const toggle = (harness: string) =>
    set({
      harness: filters.harness.includes(harness)
        ? filters.harness.filter((item) => item !== harness)
        : [...filters.harness, harness],
    })
  // A value in the address that the facets no longer list stays selectable, so it can be cleared.
  const withCurrent = (values: { value: string; count: number }[] = [], current: string) =>
    current && !values.some((item) => item.value === current) ? [{ value: current, count: 0 }, ...values] : values
  const models = withCurrent(facets?.models, filters.model)
  const folders = withCurrent(facets?.folders, filters.folder)
  const auths = withCurrent(facets?.auth_methods, filters.auth)
  // What sits behind "More filters"; the harness and the period are always in view.
  const narrowed = [filters.model, filters.folder, filters.auth, filters.from || filters.to].filter(Boolean).length
  const dates = datesOf(filters)
  const preset: Preset | 'all' | '' = filters.range || (!filters.from && !filters.to ? 'all' : '')
  // Editing a date turns a preset into the fixed days it stood for.
  const setDay = (key: 'from' | 'to', value: string) => set({ ...dates, [key]: value, range: '' })
  // Open on arrival when a filter is set behind it; after that the person's own toggle
  // decides, and the button says how many are set either way.
  const [open, setOpen] = useState(narrowed > 0)

  return (
    <section aria-label={t('history.filters.label')} className="flex flex-col gap-md">
      <div className="flex flex-wrap items-center gap-x-lg gap-y-sm">
        <div aria-label={t('history.filters.harness')} className="flex flex-wrap items-center gap-sm" role="group">
          <span aria-hidden="true" className="text-label text-secondary-ink">
            {t('history.filters.harness')}
          </span>
          {(facets?.harnesses ?? []).map(({ value, count }) => {
            const pressed = filters.harness.includes(value)
            return (
              <button key={value} aria-pressed={pressed} className="toggle-chip" type="button" onClick={() => toggle(value)}>
                {t(`history.harnesses.${value}`, { defaultValue: value })}
                {showCounts && <span className="data text-[0.75rem]">{count}</span>}
              </button>
            )
          })}
        </div>
        <div aria-labelledby={`${id}-period`} className="flex flex-wrap items-center gap-sm" role="radiogroup">
          <span className="text-label text-secondary-ink" id={`${id}-period`}>
            {t('history.filters.presets')}
          </span>
          {[...PRESETS, 'all' as const].map((value) => (
            <label key={value} className={CHOICE_PILL}>
              <input
                checked={preset === value}
                className="sr-only"
                name={`${id}-preset`}
                type="radio"
                value={value}
                onChange={() => set(value === 'all' ? { range: '', from: '', to: '' } : { range: value, from: '', to: '' })}
              />
              {value === 'all' ? t('history.filters.allTime') : t('history.filters.lastDays', { count: Number(value) })}
            </label>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-sm">
          <button
            aria-controls={`${id}-more`}
            aria-expanded={open}
            // Pulled left by its own padding, so its words line up with the labels when it wraps.
            className="button-ghost -ml-sm"
            type="button"
            onClick={() => setOpen(!open)}
          >
            <ChevronDown aria-hidden="true" className={open ? 'rotate-180' : ''} size={16} strokeWidth={1.75} />
            {narrowed ? t('history.filters.moreActive', { count: narrowed }) : t('history.filters.more')}
          </button>
          {isFiltered(filters) && (
            <button className="button-ghost" type="button" onClick={() => onChange(NO_FILTERS)}>
              <X aria-hidden="true" size={16} strokeWidth={1.75} />
              {t('history.filters.clear')}
            </button>
          )}
        </div>
      </div>

      <div
        // The class as well as the attribute: a display utility would outrank the attribute alone.
        className={open ? 'grid grid-cols-1 gap-md roomy:grid-cols-2 wide:grid-cols-[repeat(3,minmax(0,1fr))_minmax(0,1.5fr)]' : 'hidden'}
        hidden={!open}
        id={`${id}-more`}
      >
        <Field id={`${id}-model`} label={t('history.filters.model')}>
          <select className="field" id={`${id}-model`} value={filters.model} onChange={(event) => set({ model: event.target.value })}>
            <option value="">{t('history.filters.anyModel')}</option>
            {models.map(({ value }) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </Field>
        <Field id={`${id}-folder`} label={t('history.filters.folder')}>
          <select className="field" id={`${id}-folder`} value={filters.folder} onChange={(event) => set({ folder: event.target.value })}>
            <option value="">{t('history.filters.anyFolder')}</option>
            {folders.map(({ value }) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </Field>
        <Field id={`${id}-auth`} label={t('history.filters.auth')}>
          <select
            aria-describedby={auths.length ? undefined : `${id}-auth-none`}
            className="field"
            disabled={!auths.length}
            id={`${id}-auth`}
            value={filters.auth}
            onChange={(event) => set({ auth: event.target.value })}
          >
            <option value="">{t('history.filters.anyAuth')}</option>
            {auths.map(({ value }) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
          {!auths.length && (
            <span className="text-[0.75rem] text-secondary-ink" id={`${id}-auth-none`}>
              {t('history.filters.authNone')}
            </span>
          )}
        </Field>
        <fieldset className="grid min-w-0 grid-cols-2 gap-x-md gap-y-sm roomy:col-span-2 wide:col-span-1">
          <legend className="sr-only">{t('history.filters.started')}</legend>
          <Field id={`${id}-from`} label={t('history.filters.from')}>
            <input
              className="field data"
              id={`${id}-from`}
              max={dates.to || undefined}
              type="date"
              value={dates.from}
              onChange={(event) => setDay('from', event.target.value)}
            />
          </Field>
          <Field id={`${id}-to`} label={t('history.filters.to')}>
            <input
              className="field data"
              id={`${id}-to`}
              min={dates.from || undefined}
              type="date"
              value={dates.to}
              onChange={(event) => setDay('to', event.target.value)}
            />
          </Field>
        </fieldset>
      </div>
    </section>
  )
}
