import type { SessionOverview, SessionState, StateHistory } from '../api/types'

/**
 * A session's overview as lanes on one time axis, ready to draw: pure, so it is
 * tested and timed without a browser.
 */

export type Bucket = {
  start: number
  end: number
  prompts: number
  replies: number
  /** Thinking records whose reasoning the harness kept (in full or as a summary). */
  thoughtsKept: number
  /** Thinking records whose reasoning it did not keep, or did not say. */
  thoughtsNotKept: number
  toolCalls: number
  toolErrors: number
}

export type Span = { start: number; end: number }
export type SubagentSpan = Span & { id: number; title: string | null; harness: string }
export type Marker = { at: number; kind: 'model_change' | 'compaction'; label: string }
export type StateSpan = Span & { state: SessionState }
/** A turn's tokens, or null where the turn reported no usage: never a zero. */
export type TurnSpan = Span & { index: number; tokens: number | null; messages: number }

export type OverviewModel = {
  start: number
  end: number
  bucketMs: number
  buckets: Bucket[]
  subagents: SubagentSpan[]
  markers: Marker[]
  states: StateSpan[]
  turns: TurnSpan[]
  /** The times that were busy, as [from, to] pairs, for compressing the gaps between. */
  busy: [number, number][]
  /** Idle gaps longer than this are compressed. */
  threshold: number
  max: { prompts: number; replies: number; thoughts: number; toolCalls: number; tokens: number }
}

const MINUTE = 60_000
/** Idle stretches longer than this, or than three buckets, are compressed. */
export const GAP_THRESHOLD = 30 * MINUTE

const TOKENS = ['input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens'] as const

function tokensOf(turn: SessionOverview['turns'][number]): number | null {
  const reported = TOKENS.map((key) => turn[key]).filter((value): value is number => value !== null)
  return reported.length ? reported.reduce((a, b) => a + b, 0) : null
}

const time = (value: string | null | undefined) => (value ? Date.parse(value) : NaN)

export function overviewModel(overview: SessionOverview, states: StateHistory | undefined, now = Date.now()): OverviewModel | null {
  const buckets: Bucket[] = overview.buckets.map((bucket, i) => {
    const activity = overview.activity[i]
    const thoughts = activity.thoughts
    return {
      start: time(bucket.start),
      end: time(bucket.end),
      prompts: activity.prompts,
      replies: activity.replies,
      thoughtsKept: thoughts.full + thoughts.summary,
      thoughtsNotKept: thoughts.encrypted + thoughts.absent + thoughts.not_recorded,
      toolCalls: activity.tool_calls,
      toolErrors: activity.tool_errors,
    }
  })
  if (!buckets.length) return null
  const start = buckets[0].start
  const lastBucket = buckets[buckets.length - 1].end
  const bucketMs = buckets[0].end - buckets[0].start

  const subagents: SubagentSpan[] = overview.subagents.flatMap((agent) => {
    const from = time(agent.start)
    if (Number.isNaN(from)) return []
    const to = time(agent.end)
    return [{ id: agent.session_id, title: agent.title, harness: agent.harness, start: from, end: Number.isNaN(to) ? from : Math.max(from, to) }]
  })
  const markers: Marker[] = overview.markers.flatMap((marker) => {
    const at = time(marker.at)
    if (Number.isNaN(at)) return []
    const label = marker.kind === 'model_change' ? (marker.model ?? '') : (marker.trigger ?? '')
    return [{ at, kind: marker.kind, label }]
  })
  const dated = overview.turns.filter((turn) => !Number.isNaN(time(turn.started_at)))
  const end = Math.max(lastBucket, ...subagents.map((agent) => agent.end), ...markers.map((marker) => marker.at))
  const turns: TurnSpan[] = dated.map((turn, i) => {
    const from = time(turn.started_at)
    const next = dated[i + 1] ? time(dated[i + 1].started_at) : end
    return { index: turn.index, start: from, end: Math.max(from, next), tokens: tokensOf(turn), messages: turn.messages }
  })

  // States over the session, from its recorded changes; the last runs to the end.
  const changes = states?.changes ?? []
  const stateSpans: StateSpan[] = changes
    .map((change, i) => ({
      state: change.state,
      start: Math.max(start, time(change.changed_at)),
      end: Math.min(Math.max(end, now), i + 1 < changes.length ? time(changes[i + 1].changed_at) : end),
    }))
    // Ended is drawn as a cap at the moment it began, so it is kept even with no length.
    .filter((span) => span.end > span.start || span.state === 'ended')

  const busy: [number, number][] = [
    ...buckets
      .filter((b) => b.prompts || b.replies || b.thoughtsKept || b.thoughtsNotKept || b.toolCalls)
      .map((b): [number, number] => [b.start, b.end]),
    ...subagents.map((agent): [number, number] => [agent.start, agent.end]),
    ...markers.map((marker): [number, number] => [marker.at, marker.at]),
    ...turns.map((turn): [number, number] => [turn.start, turn.start]),
  ]
  if (!busy.length) busy.push([start, end])

  const most = (values: number[]) => Math.max(1, ...values)
  return {
    start,
    end,
    bucketMs,
    buckets,
    subagents,
    markers,
    states: stateSpans,
    turns,
    busy,
    threshold: Math.max(GAP_THRESHOLD, 3 * bucketMs),
    max: {
      prompts: most(buckets.map((b) => b.prompts)),
      replies: most(buckets.map((b) => b.replies)),
      thoughts: most(buckets.map((b) => b.thoughtsKept + b.thoughtsNotKept)),
      toolCalls: most(buckets.map((b) => b.toolCalls)),
      tokens: most(turns.map((turn) => turn.tokens ?? 0)),
    },
  }
}
