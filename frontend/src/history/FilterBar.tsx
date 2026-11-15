import { ChevronDown, X } from 'lucide-react'
import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Facets } from '../api/types'
import { isFiltered, NO_FILTERS, type Filters } from './filters'

// DESIGN.md: at 1180px and below the layout narrows; the fields fold into a disclosure there.
const WIDE = '(min-width: 1181px)'

function useWide(): boolean {
  const [query] = useState(() => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(WIDE) : null))
  const [wide, setWide] = useState(query?.matches ?? true)
  useEffect(() => {
    if (!query) return
    const update = () => setWide(query.matches)
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [query])
  return wide
}

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
  const wide = useWide()
  const narrowed = [filters.model, filters.folder, filters.auth, filters.from, filters.to].filter(Boolean).length
  const [open, setOpen] = useState(narrowed > 0)

  return (
    <section aria-label={t('history.filters.label')} className="flex flex-col gap-md">
      <div aria-label={t('history.filters.harness')} className="flex flex-wrap items-center gap-sm" role="group">
        <span aria-hidden="true" className="text-label text-secondary-ink">
          {t('history.filters.harness')}
        </span>
        {(facets?.harnesses ?? []).map(({ value, count }) => {
          const pressed = filters.harness.includes(value)
          return (
            <button
              key={value}
              aria-pressed={pressed}
              className="toggle-chip"
              type="button"
              onClick={() => toggle(value)}
            >
              {t(`history.harnesses.${value}`, { defaultValue: value })}
              {showCounts && <span className="data text-[0.75rem]">{count}</span>}
            </button>
          )
        })}
      </div>

      <details
        className="group"
        open={wide || open || narrowed > 0}
        // Only the person's own toggles count; the wide layout opening it is not a choice.
        onToggle={(event) => {
          if (!wide) setOpen((event.target as HTMLDetailsElement).open)
        }}
      >
        {/* The summary keeps its default display, so it stays a disclosure to assistive technology. */}
        <summary className="w-fit cursor-pointer list-none rounded-control wide:hidden [&::-webkit-details-marker]:hidden">
          <span className="button-ghost">
            <ChevronDown
              aria-hidden="true"
              className="transition-transform duration-state group-open:rotate-180"
              size={16}
              strokeWidth={1.75}
            />
            {narrowed ? t('history.filters.moreActive', { count: narrowed }) : t('history.filters.more')}
          </span>
        </summary>
      <div className="mt-sm grid grid-cols-1 gap-md roomy:grid-cols-2 wide:mt-0 wide:grid-cols-[repeat(3,minmax(0,1fr))_minmax(0,1.5fr)]">
        <Field id={`${id}-model`} label={t('history.filters.model')}>
          <select
            className="field"
            id={`${id}-model`}
            value={filters.model}
            onChange={(event) => set({ model: event.target.value })}
          >
            <option value="">{t('history.filters.anyModel')}</option>
            {models.map(({ value }) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </Field>
        <Field id={`${id}-folder`} label={t('history.filters.folder')}>
          <select
            className="field"
            id={`${id}-folder`}
            value={filters.folder}
            onChange={(event) => set({ folder: event.target.value })}
          >
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
        <fieldset className="grid min-w-0 grid-cols-2 gap-md roomy:col-span-2 wide:col-span-2">
          <legend className="sr-only">{t('history.filters.started')}</legend>
          <Field id={`${id}-from`} label={t('history.filters.from')}>
            <input
              className="field data"
              id={`${id}-from`}
              max={filters.to || undefined}
              type="date"
              value={filters.from}
              onChange={(event) => set({ from: event.target.value })}
            />
          </Field>
          <Field id={`${id}-to`} label={t('history.filters.to')}>
            <input
              className="field data"
              id={`${id}-to`}
              min={filters.from || undefined}
              type="date"
              value={filters.to}
              onChange={(event) => set({ to: event.target.value })}
            />
          </Field>
        </fieldset>
      </div>
      </details>

      {isFiltered(filters) && (
        <div>
          <button className="button-ghost" type="button" onClick={() => onChange(NO_FILTERS)}>
            <X aria-hidden="true" size={16} strokeWidth={1.75} />
            {t('history.filters.clear')}
          </button>
        </div>
      )}
    </section>
  )
}
