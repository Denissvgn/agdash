/** Shapes the read API returns; see the service's OpenAPI document. */

export type SessionState = 'active' | 'waiting' | 'idle' | 'ended' | 'unknown'
export type Surface = 'cli' | 'desktop' | 'ide' | 'exec'
export type EventKind =
  | 'user_prompt'
  | 'assistant_text'
  | 'thinking'
  | 'tool_call'
  | 'tool_result'
  | 'subagent'
  | 'model_change'
  | 'compaction'
  | 'status'
  | 'usage'

export interface Page<T> {
  items: T[]
  next_cursor: string | null
}

export interface SessionSummary {
  id: number
  harness: string
  native_id: string
  surface: Surface | null
  title: string | null
  cwd: string | null
  branch: string | null
  state: SessionState
  started_at: string | null
  last_activity_at: string | null
  ended_at: string | null
  models: string[]
  effort: string | null
  parent_session_id: number | null
  initial_task: string | null
  event_count: number
  child_count: number
}

export interface SnippetPart {
  text: string
  match: boolean
}

export interface Excerpt {
  event_id: number
  sequence: number
  kind: EventKind
  occurred_at: string | null
  snippet: SnippetPart[]
}

export interface SessionHit extends SessionSummary {
  hits: number
  excerpts: Excerpt[]
}

export interface FacetValue {
  value: string
  count: number
}

export interface Facets {
  harnesses: FacetValue[]
  surfaces: FacetValue[]
  states: FacetValue[]
  models: FacetValue[]
  folders: FacetValue[]
  auth_methods: FacetValue[]
  first_started_at: string | null
  last_started_at: string | null
}
