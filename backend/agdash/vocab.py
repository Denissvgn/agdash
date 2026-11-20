"""Closed vocabularies shared by the store, the event contract and the API.

Values are lower-case strings and are what gets stored. A member is never
renamed or reused; new members are only added.
"""

from enum import StrEnum


class Harness(StrEnum):
    """Harnesses with a built-in adapter.

    The ``harness`` columns are plain strings, not bound to this enum, so a new
    adapter does not need a schema change.
    """

    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    ANTIGRAVITY = "antigravity"
    PI = "pi"


class Surface(StrEnum):
    """How a session is being driven."""

    CLI = "cli"
    DESKTOP = "desktop"
    IDE = "ide"
    EXEC = "exec"


class SessionState(StrEnum):
    """Derived liveness of a session. No evidence means ``unknown``, never ``ended``."""

    ACTIVE = "active"
    WAITING = "waiting"
    IDLE = "idle"
    ENDED = "ended"
    UNKNOWN = "unknown"


class StateBasis(StrEnum):
    """Why a session's state was chosen. Stored with every state."""

    PROCESS_EXITED = "process_exited"
    HARNESS_CLOSED = "harness_closed"
    END_RECORD = "end_record"
    ARCHIVED = "archived"
    HARNESS_BUSY = "harness_busy"
    HARNESS_WAITING = "harness_waiting"
    HARNESS_IDLE = "harness_idle"
    MID_TURN = "mid_turn"
    TURN_ENDED = "turn_ended"
    QUIET = "quiet"
    PROCESS_RUNNING = "process_running"
    RECENT_WRITES = "recent_writes"
    PROCESS_HIDDEN = "process_hidden"
    NO_EVIDENCE = "no_evidence"


class ParentBasis(StrEnum):
    """Why a session is recorded as the child of another."""

    HARNESS_RECORD = "harness_record"
    PROCESS_TREE = "process_tree"


class SessionKind(StrEnum):
    """What a session is, where its harness says so. One that says nothing has no kind."""

    SUBAGENT = "subagent"  # an agent that another session started
    # The harness's own check of what an agent is about to do. It runs a model and
    # uses tokens, but nobody started it and it does no work of its own: not an agent.
    REVIEW = "review"


class EventKind(StrEnum):
    USER_PROMPT = "user_prompt"
    ASSISTANT_TEXT = "assistant_text"
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SUBAGENT = "subagent"
    MODEL_CHANGE = "model_change"
    COMPACTION = "compaction"
    STATUS = "status"
    USAGE = "usage"


class ThoughtsCoverage(StrEnum):
    """How much of the model's reasoning a harness left on disk."""

    FULL = "full"
    SUMMARY = "summary"
    ENCRYPTED = "encrypted"
    ABSENT = "absent"


class UsageScope(StrEnum):
    """Whether usage numbers cover one message or the session so far."""

    MESSAGE = "message"
    SESSION_TOTAL = "session_total"


class SyncState(StrEnum):
    NEVER = "never"
    OK = "ok"
    ERROR = "error"


class AuthMethod(StrEnum):
    """How a harness signs in to its model provider."""

    SUBSCRIPTION = "subscription"  # the vendor's own account: Claude, ChatGPT, Google
    API_KEY = "api_key"
    CUSTOM_ENDPOINT = "custom_endpoint"  # another base URL or provider than the vendor's
    CLOUD_PROVIDER = "cloud_provider"  # Amazon Bedrock or Google Vertex AI


class AuthBasis(StrEnum):
    """What a session's auth method rests on.

    ``transcript``: the session's own records name its provider. ``config``: the
    harness's settings said so while the session was active; a key set only in
    the agent's environment is not visible there.
    """

    TRANSCRIPT = "transcript"
    CONFIG = "config"


class MirrorObject(StrEnum):
    """The kinds of WorkChord object agdash keeps a local copy of."""

    PROJECTS = "projects"
    TASKS = "tasks"
    PIPELINE = "pipeline"
    ASSIGNMENTS = "assignments"
    ACTORS = "actors"
    RUNS = "runs"


class MirrorState(StrEnum):
    """How the last sync of one kind of object went.

    ``unavailable``: the stored credentials may not read it. ``unparseable``: the
    server answered in a shape agdash does not know, so the copy is left as it
    was rather than filled with a guess. ``error``: the server could not be
    reached or failed; the next sync retries.
    """

    OK = "ok"
    UNAVAILABLE = "unavailable"
    UNPARSEABLE = "unparseable"
    ERROR = "error"


class RunLinkBasis(StrEnum):
    """Evidence for a session-to-run link, strongest first."""

    TOOL_CALL = "tool_call"
    TRACE_ID = "trace_id"
    MCP_CONFIG = "mcp_config"
    HEURISTIC = "heuristic"


class IdentityLinkBasis(StrEnum):
    """Evidence for a session-to-actor link, strongest first."""

    CAPABILITIES = "capabilities"
    RUN = "run"
    RUNTIME_REF = "runtime_ref"
    MANUAL = "manual"


class ProjectLinkBasis(StrEnum):
    """Evidence for a session-to-project link, strongest first."""

    TASK = "task"
    ASSIGNMENT = "assignment"
    RULE = "rule"
    MANUAL = "manual"


class RuleMatchKind(StrEnum):
    """What a mapping rule matches on a session. ``session`` is a manual pin."""

    FOLDER = "folder"
    REPOSITORY = "repository"
    LAUNCHER = "launcher"
    SESSION = "session"


class RuleTargetKind(StrEnum):
    """What a mapping rule resolves a matching session to."""

    PROJECT = "project"
    ACTOR = "actor"
    RUNTIME_REF = "runtime_ref"
