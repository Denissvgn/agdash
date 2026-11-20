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
  /**
   * What the harness says the session is: a subagent another session started, or the
   * harness's own review of what an agent is about to do. Null where it says nothing.
   */
  kind?: 'subagent' | 'review' | null
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
  /** How the parent link is known: harness_record or process_tree. */
  parent_basis?: string | null
  initial_task: string | null
  event_count: number
  child_count: number
}

/** One session over time: activity per bucket, markers, subagents, and usage per turn. */
export interface SessionOverview {
  session_id: number
  time_zone: string
  bucket: string
  buckets: { start: string; end: string }[]
  activity: {
    prompts: number
    replies: number
    thoughts: { full: number; summary: number; encrypted: number; absent: number; not_recorded: number }
    tool_calls: number
    tool_errors: number
  }[]
  undated_events: number
  markers: {
    kind: 'model_change' | 'compaction'
    sequence: number
    at: string | null
    model: string | null
    previous_model: string | null
    effort: string | null
    trigger: string | null
    tokens_before: number | null
    tokens_after: number | null
  }[]
  markers_omitted: number
  subagents: {
    session_id: number
    harness: string
    parent_basis: string | null
    title: string | null
    state: SessionState
    start: string | null
    end: string | null
    ended: boolean
  }[]
  subagents_omitted: number
  turns: {
    index: number
    prompt_sequence: number | null
    started_at: string | null
    messages: number
    input_tokens: number | null
    output_tokens: number | null
    cache_read_tokens: number | null
    cache_write_tokens: number | null
    reasoning_tokens: number | null
    reported_cost: number | null
    reported_by: Record<string, number>
  }[]
}

/** A session's changes of state, oldest first. */
export interface StateHistory {
  session_id: number
  state: SessionState
  changes: { state: SessionState; basis: string; previous: SessionState | null; changed_at: string }[]
  earlier_omitted: number
}

/**
 * How far the service's read of what the harnesses had written when it started has got.
 * `known` is false when the service cannot tell; until `finished`, lists are short.
 */
export interface Reading {
  known: boolean
  done: number
  total: number
  finished: boolean
}

/** A place a harness is looked for on this machine. */
export interface SourceLocation {
  path: string
  /** records: a folder of session records. settings: a file read only for the sign-in method. */
  role: 'records' | 'settings'
  found: boolean
}

/** One format version of a harness, and what in it was not understood. */
export interface FormatHealth {
  /** Null for records that named no version. */
  version: string | null
  unknown_records: number
  unreadable_lines: number
  first_seen_at: string
  last_seen_at: string
  /** Something was not understood and the format was read recently: it may have changed. */
  recent: boolean
}

/** What agdash reads of one harness on this machine. */
export interface Source {
  harness: string
  found: boolean
  locations: SourceLocation[]
  files: number
  bytes_read: number
  last_read_at: string | null
  sessions: number
  format_version: string | null
  /** Most recently read first. */
  formats: FormatHealth[]
}

export interface SourcesReport {
  sources: Source[]
}

/** Where a setting's value comes from: saved on the Data page, the service's environment, or neither. */
export type SettingSource = 'page' | 'environment' | 'default'

/** What agdash keeps: its store, the archive of original lines, and retention. */
export interface StorageReport {
  store: { file: string | null; bytes: number | null; sessions: number; first_started_at: string | null }
  archive: {
    enabled: boolean
    source: SettingSource
    chunks: number
    /** Whether long runs of encoded data stay in the lines archived from now on. */
    keep_encoded: boolean
    keep_encoded_source: SettingSource
    /** Chunks archived before, not yet looked at for encoded data. */
    untrimmed_chunks: number
    trimming: boolean
    last_trim: { at: string; through: number; chunks: number; saved_bytes: number } | null
  }
  retention: {
    /** Null keeps everything. */
    days: number | null
    source: SettingSource
    /** What the limit would remove now; null without one. */
    cutoff: string | null
    removable_sessions: number | null
    kept_alive: number | null
    /** Whether the service removes by itself, once a day. */
    automatic: boolean
    running: boolean
    next_removal_at: string | null
    last_removal: { at: string; sessions: number; events: number; automatic: boolean; error: string | null } | null
  }
}

/** What a limit would remove, before it is saved. */
export interface RetentionPreview {
  days: number
  cutoff: string
  removable_sessions: number
  kept_alive: number
}

/** How much the archive of original lines holds, compressed as stored. */
export interface ArchiveSize {
  chunks: number
  bytes: number
}

/** What the store's file is made of. A part that cannot be told is null. */
export interface StoreParts {
  bytes: number | null
  archive_bytes: number
  free_bytes: number | null
  rest_bytes: number | null
  /** So much is free that the service compacts the store when it next starts. */
  compacts_at_start: boolean
  /** The store gives free room back to the disk as the service runs. */
  releases: boolean
}

export type ServiceRange = 'hour' | 'day' | 'quarter'

/** What the service itself used over one step; null where nothing was measured. */
export interface ServicePoint {
  at: string
  cpu_percent: number | null
  footprint_bytes: number | null
  read_bytes_per_second: number | null
  written_bytes_per_second: number | null
  store_bytes: number | null
}

/** What the service itself uses: its own CPU, memory, storage traffic and store size. */
export interface ServiceUsageReport {
  /** Whether this process samples itself; if not, only what was kept is shown. */
  known: boolean
  since: string | null
  cpus: number | null
  range: ServiceRange
  step_seconds: number
  measured_seconds: number
  now: {
    at: string
    cpu_percent: number | null
    footprint_bytes: number | null
    resident_bytes: number | null
    read_bytes_per_second: number | null
    written_bytes_per_second: number | null
    store_bytes: number | null
    free_bytes: number | null
  } | null
  points: ServicePoint[]
  parts: { part: string; cpu_seconds: number; cpu_percent: number }[]
}

/** The last 30 minutes of every live session: events per minute, and its states. */
export interface LiveActivity {
  minutes: string[]
  until: string
  sessions: {
    id: number
    harness: string
    state: SessionState
    events: number[]
    total: number
    states: { state: SessionState; start: string; end: string }[]
  }[]
  omitted: number
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

/** One bucket of a series; a number nobody reported is null, never zero. */
export interface SeriesPoint {
  input_tokens: number | null
  output_tokens: number | null
  cache_read_tokens: number | null
  cache_write_tokens: number | null
  reasoning_tokens: number | null
  reported_cost: number | null
  messages: number
  /** Sessions active in the bucket; null when grouping by model. */
  sessions: number | null
  active_minutes: number | null
  sessions_with_usage: number
  reported_by: Record<string, number>
}

export type SeriesBucket = 'hour' | 'day' | 'week'

export interface SeriesReport {
  bucket: SeriesBucket
  group: UsageGroup
  time_zone: string
  buckets: { start: string; end: string }[]
  series: { key: string | null; points: SeriesPoint[]; total: SeriesPoint }[]
  total: { key: string | null; points: SeriesPoint[]; total: SeriesPoint }
  undated_usage: number
}

/** When sessions were active: an hour-of-week grid, and sessions at once per bucket. */
export interface ActivityReport {
  time_zone: string
  bucket: 'quarter-hour' | 'hour' | 'day'
  /** 0 is Monday. */
  hour_of_week: { weekday: number; hour: number; session_hours: number }[]
  buckets: { start: string; end: string }[]
  parallel: { key: string | null; sessions: number[] }[]
  total: { key: string | null; sessions: number[] }
}

/** Sessions started per local day, every day from the first start to the last. */
export interface StartedReport {
  time_zone: string
  days: { day: string; sessions: number }[]
  undated: number
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

// -- one session --------------------------------------------------------------------

export type ThoughtsCoverage = 'full' | 'summary' | 'encrypted' | 'absent'

export interface EventOut {
  id: number
  session_id: number
  sequence: number
  occurred_at: string | null
  kind: EventKind
  payload: Record<string, unknown>
  thoughts_coverage: ThoughtsCoverage | null
}

export interface SessionDetail extends SessionSummary {
  initial_task: string | null
  permission_mode: string | null
  parent_basis: string | null
  children: number[]
  events_by_kind: Record<string, number>
}

// -- the board by project -----------------------------------------------------------

export type FlagCode = 'run_without_session' | 'claim_expired' | 'model_mismatch' | 'no_task'

export interface AttentionFlag {
  code: FlagCode
  session_id: number | null
  run_id: number | null
  task_id: number | null
  task_key: string | null
  actor: string | null
  observed: string | null
  bound: string | null
  since: string | null
}

export interface BoardCard {
  id: number
  harness: string
  title: string | null
  state: SessionState
  cwd: string | null
  last_activity_at: string | null
  parent_session_id: number | null
  project_basis: MappingBasis | null
  actor: { id: number; name: string; role: string | null; basis: MappingBasis | null } | null
  task: {
    id: number
    key: string | null
    title: string
    status: string | null
    iteration: string | null
    claim_expires_at: string | null
  } | null
  run: {
    id: number
    status: string | null
    bound_model: string | null
    declared_usage: { quantities?: Record<string, number> } | null
    heartbeat_at: string | null
  } | null
  work: 'execution' | 'review' | null
  observed_models: string[]
  observed_usage: { messages: number; input_tokens: number | null; output_tokens: number | null }
  flags: FlagCode[]
  subagents: BoardCard[]
}

export interface BoardAgent {
  id: number
  name: string
  role: string | null
  live_sessions: number
  assigned: boolean
  /** Current assignments in the project: what was declared. */
  assignments?: number
  /** Live sessions mapped to the agent here, by the basis of the agent mapping. */
  observed_bases?: Record<string, number>
}

/** Sessions active in the last 7 or 30 days, in the lane of the project they map to. */
export interface ProjectActivity {
  connection: { id: number; name: string | null; server: string } | null
  days: number
  since: string
  until: string
  lanes: {
    project: { id: number; name: string } | null
    sessions: { id: number; harness: string; title: string | null; state: SessionState; parent_session_id: number | null; start: string | null; end: string | null }[]
  }[]
  omitted: number
}

/** How one mapping was decided: mapped, conflict or unmapped, with what points where. */
export interface MappingResolution {
  state: 'mapped' | 'conflict' | 'unmapped'
  target: number | null
  name: string | null
  basis: string | null
  suggestion: number | null
  evidence: MappingEvidence[]
}

/** A session's WorkChord runs, actor and project, per connection. */
export interface SessionMapping {
  session_id: number
  connections: {
    connection_id: number
    server: string
    runs: {
      basis: 'tool_call' | 'trace_id' | 'mcp_config' | 'heuristic'
      confidence: number
      suggestion: boolean
      run_id: number | null
      task_id: number | null
      task_key: string | null
      task_title: string | null
      evidence: Record<string, unknown>
    }[]
    actor: MappingResolution
    project: MappingResolution
  }[]
}

export interface ProjectGroup {
  project: { id: number; name: string; status: string | null; health: string | null } | null
  reason: 'mapped' | 'unmapped'
  agents: BoardAgent[]
  sessions: BoardCard[]
}

export interface ProjectBoard {
  connection: { id: number; name: string | null; server: string } | null
  connections: { id: number; name: string | null; server: string }[]
  groups: ProjectGroup[]
  flags: AttentionFlag[]
  generated_at: string
}
