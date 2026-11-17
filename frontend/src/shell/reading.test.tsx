import { QueryClientProvider, useQuery } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { newQueryClient } from '../api/queryClient'
import type { Reading } from '../api/types'
import '../i18n'
import { ReadingLine } from './ReadingLine'

type Answer = Partial<Reading> | 'offline'

/** The service's answers to /api/reading, in turn; the last one repeats. */
function serve(answers: Answer[]) {
  const asked = { reading: 0, list: 0 }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string) => {
      if (!input.startsWith('/api/reading')) {
        asked.list += 1
        return new Response('[]')
      }
      const answer = answers[Math.min(asked.reading, answers.length - 1)]
      asked.reading += 1
      if (answer === 'offline') throw new TypeError('network down')
      return new Response(JSON.stringify(answer))
    }),
  )
  return asked
}

/** A list fetched beside the line, as a page's own queries are. */
function List() {
  useQuery({ queryKey: ['list'], queryFn: () => fetch('/api/list').then((answer) => answer.json()) })
  return null
}

function show() {
  render(
    <QueryClientProvider client={newQueryClient()}>
      <List />
      <ReadingLine />
    </QueryClientProvider>,
  )
}

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }))
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const line = () => document.querySelector('[data-reading]')

test('while the service catches up, the line says how far it is and that lists may be short', async () => {
  serve([{ known: true, done: 12, total: 6383, finished: false }])
  show()
  await waitFor(() => expect(line()).not.toBeNull())
  expect(line()).toHaveTextContent('Still catching up on session files: 12 of 6,383 done.')
  expect(line()).toHaveTextContent('Running sessions come first, then the newest')
  expect(line()).toHaveTextContent('lists may be incomplete until this finishes')
})

test('before any file is counted, it says the files are being looked for', async () => {
  serve([{ known: true, done: 0, total: 0, finished: false }])
  show()
  await waitFor(() => expect(line()).toHaveTextContent('Looking for session files.'))
})

test('the counting is not read out: one announcement when it is noticed, one when it is over', async () => {
  serve([
    { known: true, done: 1, total: 9, finished: false },
    { known: true, done: 5, total: 9, finished: false },
    { known: true, done: 9, total: 9, finished: true },
  ])
  show()
  const status = screen.getByRole('status')
  expect(status).toBeEmptyDOMElement()
  await waitFor(() => expect(status).toHaveTextContent('agdash is still catching up on session files.'))
  const announced = status.textContent
  expect(line()).not.toHaveAttribute('role')
  expect(line()?.closest('[aria-live]')).toBeNull()
  await act(() => vi.advanceTimersByTimeAsync(2_000))
  await waitFor(() => expect(line()).toHaveTextContent('5 of 9 done'))
  expect(status.textContent).toBe(announced)
  await act(() => vi.advanceTimersByTimeAsync(2_000))
  await waitFor(() => expect(status).toHaveTextContent('agdash has caught up on session files.'))
  expect(line()).toBeNull()
})

test.each<[string, Answer]>([
  ['the catching up is finished', { known: true, done: 9, total: 9, finished: true }],
  ['the service cannot tell', { known: false }],
  ['the service does not answer', 'offline'],
])('nothing shows or is announced when %s, and it is not asked again', async (_, answer) => {
  const asked = serve([answer])
  show()
  await waitFor(() => expect(asked.reading).toBe(1))
  await act(() => vi.advanceTimersByTimeAsync(10_000))
  expect(line()).toBeNull()
  expect(screen.getByRole('status')).toBeEmptyDOMElement()
  expect(asked.reading).toBe(1)
})

test('when it finishes the line goes, and what was fetched meanwhile is fetched again', async () => {
  const asked = serve([
    { known: true, done: 1, total: 3, finished: false },
    { known: true, done: 3, total: 3, finished: true },
  ])
  show()
  await waitFor(() => expect(line()).not.toBeNull())
  expect(asked.list).toBe(1)
  await act(() => vi.advanceTimersByTimeAsync(2_000))
  await waitFor(() => expect(line()).toBeNull())
  await waitFor(() => expect(asked.list).toBe(2))
  await act(() => vi.advanceTimersByTimeAsync(10_000))
  expect(asked.reading).toBe(2)
})
