import { useInfiniteQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { useId, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { getJson } from '../api/client'
import type { Page, SessionHit, SessionSummary } from '../api/types'
import { FilterBar } from './FilterBar'
import { apiParams, isFiltered } from './filters'
import { useAddressFilters, useFacets } from './hooks'
import { PageHeader } from './PageHeader'
import { ErrorNotice, LoadingRows, Notice, SessionRows, ShowMore } from './SessionList'

const PAGE_SIZE = 30


export function SessionsPage() {
  const { t } = useTranslation()
  const [filters, setFilters] = useAddressFilters()
  const facets = useFacets()
  const sessions = useInfiniteQuery({
    queryKey: ['sessions', filters],
    queryFn: ({ pageParam, signal }) =>
      getJson<Page<SessionSummary>>(
        '/api/sessions',
        { ...apiParams(filters), cursor: pageParam, limit: PAGE_SIZE },
        signal,
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  })
  const items = sessions.data?.pages.flatMap((page) => page.items) ?? []
  const label = t('history.list.label')
  const base = 'workspaces.history.destinations.sessions'

  return (
    <article aria-labelledby="page-title" className="flex flex-col gap-page-gap">
      <PageHeader purposeKey={`${base}.purpose`} titleKey={`${base}.title`} />
      <FilterBar facets={facets.data} filters={filters} onChange={setFilters} />
      {sessions.isPending ? (
        <LoadingRows label={t('history.list.loading')} />
      ) : sessions.isError ? (
        <ErrorNotice error={sessions.error} onRetry={() => void sessions.refetch()} />
      ) : items.length === 0 ? (
        isFiltered(filters) ? (
          <Notice body={t('history.empty.filteredBody')} title={t('history.empty.filteredTitle')} />
        ) : (
          <Notice body={t('history.empty.noneBody')} title={t('history.empty.noneTitle')} />
        )
      ) : (
        <>
          <SessionRows label={label} sessions={items} />
          {sessions.hasNextPage && (
            <ShowMore busy={sessions.isFetchingNextPage} onClick={() => void sessions.fetchNextPage()} />
          )}
        </>
      )}
    </article>
  )
}

/** The query box. Keyed by the query, so Back and Forward show the query in the address. */
function SearchForm({ query, onSearch }: { query: string; onSearch: (query: string) => void }) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState(query)
  const inputId = useId()
  const submit = (event: FormEvent) => {
    event.preventDefault()
    onSearch(draft.trim())
  }
  return (
    <form className="flex flex-col gap-xs" role="search" onSubmit={submit}>
      <label className="text-label text-secondary-ink" htmlFor={inputId}>
        {t('history.search.label')}
      </label>
      <div className="flex flex-col gap-sm roomy:flex-row">
        <input
          autoComplete="off"
          className="field min-w-0 roomy:flex-1"
          id={inputId}
          placeholder={t('history.search.placeholder')}
          type="search"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button className="button-primary" type="submit">
          <Search aria-hidden="true" size={16} strokeWidth={1.75} />
          {t('history.search.submit')}
        </button>
      </div>
    </form>
  )
}

export function SearchPage() {
  const { t } = useTranslation()
  const [filters, setFilters, search] = useAddressFilters()
  const [, setSearch] = useSearchParams()
  const facets = useFacets()
  const query = (search.get('q') ?? '').trim()
  const results = useInfiniteQuery({
    queryKey: ['session-search', query, filters],
    queryFn: ({ pageParam, signal }) =>
      getJson<Page<SessionHit>>(
        '/api/search/sessions',
        { ...apiParams(filters), q: query, cursor: pageParam, limit: 20 },
        signal,
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: query.length > 0,
  })
  const items = results.data?.pages.flatMap((page) => page.items) ?? []
  const base = 'workspaces.history.destinations.search'

  const onSearch = (value: string) => {
    const next = new URLSearchParams(search)
    if (value) next.set('q', value)
    else next.delete('q')
    setSearch(next)
  }

  return (
    <article aria-labelledby="page-title" className="flex flex-col gap-page-gap">
      <PageHeader purposeKey={`${base}.purpose`} titleKey={`${base}.title`} />
      <SearchForm key={query} query={query} onSearch={onSearch} />
      <FilterBar facets={facets.data} filters={filters} showCounts={false} onChange={setFilters} />
      {/* One status region that stays mounted, so the outcome of a search is announced. */}
      <p aria-live="polite" className="sr-only" role="status">
        {!query || results.isPending
          ? ''
          : results.isError
            ? t('history.search.failed')
            : items.length
              ? t('history.search.results', { query })
              : t('history.search.noneTitle', { query })}
      </p>
      {!query ? (
        <Notice body={t('history.search.promptBody')} title={t('history.search.promptTitle')} />
      ) : results.isPending ? (
        <LoadingRows label={t('history.list.loading')} />
      ) : results.isError ? (
        <ErrorNotice error={results.error} onRetry={() => void results.refetch()} />
      ) : items.length === 0 ? (
        <Notice body={t('history.search.noneBody')} title={t('history.search.noneTitle', { query })} />
      ) : (
        <>
          <SessionRows label={t('history.search.results', { query })} sessions={items} />
          {results.hasNextPage && (
            <ShowMore busy={results.isFetchingNextPage} onClick={() => void results.fetchNextPage()} />
          )}
        </>
      )}
    </article>
  )
}
