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
  /** Why the state was chosen, such as process_running; null when nothing was recorded. */
  state_basis?: string | null
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

export type UsageGroup = 'harness' | 'model' | 'folder'

/** Null means the harness did not report the number; it is never a zero. */
export interface UsageRow {
  key: string | null
  sessions: number
  sessions_with_usage: number
  messages: number
  input_tokens: number | null
  output_tokens: number | null
  cache_read_tokens: number | null
  cache_write_tokens: number | null
  reasoning_tokens: number | null
  reported_cost: number | null
  duration_seconds: number | null
  sessions_with_duration: number | null
  /** Per number, how many sessions reported it. */
  reported_by?: Record<string, number>
}

export interface UsageReport {
  group: UsageGroup
  rows: UsageRow[]
  total: UsageRow
  sessions_without_usage: number
}

export interface ToolRow {
  name: string
  calls: number
  sessions: number
  outcomes_known: number
  errors: number
}

export interface ToolReport {
  rows: ToolRow[]
  total_calls: number
  total_sessions: number
  truncated: boolean
  outcomes_known: number
  errors: number
}

// -- the WorkChord connector --------------------------------------------------------

export interface SyncedObject {
  kind: string
  state: 'ok' | 'unavailable' | 'unparseable' | 'error'
  detail: string | null
  rows: number | null
  checked_at: string
  synced_at: string | null
}

export interface WorkChordConnection {
  id: number
  name: string | null
  server: string
  auth_mode: string | null
  credential: 'read_only_session' | 'full_session' | 'none'
  agent_key: boolean
  publish: boolean
  sync_state: 'never' | 'ok' | 'error'
  last_synced_at: string | null
  last_error: string | null
  objects: SyncedObject[]
}

export interface Pairing {
  status: 'connected' | 'waiting'
  connection_id: number | null
  pairing_id: string | null
  verification_code: string | null
  verification_url: string | null
  expires_at: string | null
  read_only: boolean | null
}

export interface ProjectOption {
  id: number
  name: string
}

export interface MappingRule {
  id: number
  match: 'folder' | 'repository' | 'launcher' | 'session'
  value: string | null
  session_id: number | null
  target: 'project' | 'actor' | 'runtime_ref'
  target_value: string
  target_name: string | null
  harness: string | null
  note: string | null
}

export type MappingBasis = 'capabilities' | 'run' | 'runtime_ref' | 'task' | 'assignment' | 'rule' | 'manual'

export interface MappingEvidence {
  basis: MappingBasis
  target: number
  name: string | null
  evidence: Record<string, unknown>
}

export interface UnmappedItem {
  session_id: number
  harness: string
  title: string | null
  cwd: string | null
  state: SessionState
  last_activity_at: string | null
  connection_id: number
  reason: 'no evidence' | 'conflicting evidence'
  suggestion: number | null
  evidence: MappingEvidence[]
}

export interface UnmappedQueue {
  items: UnmappedItem[]
  total: number
}
