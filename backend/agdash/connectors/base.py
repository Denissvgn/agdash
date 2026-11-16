"""What every tracker connector offers agdash, so another tracker can be added later.

A connector reads. It pairs once with a server, keeps its credentials in
agdash's secret store, reports whether the pairing still works, and opens a
client that can only send read requests.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session as DbSession

from agdash.db.models import Connection
from agdash.platform.secrets import SecretStore


@dataclass(frozen=True)
class Access:
    """What a paired connection can see, as the server describes it."""

    mode: str  # how the server lets agdash in, such as "session" or "open"
    authenticated: bool
    principal: str | None = None
    workspace_role: str | None = None
    projects: int | None = None
    run_traces: bool = False  # whether a second credential for run detail is stored
    session_access: str | None = None  # "observe" (read-only on the server) or "full"
    expires_at: datetime | None = None
    problem: str | None = None  # why the connection does not work, when it does not
    notes: tuple[str, ...] = field(default=())


class Connector(Protocol):
    kind: str

    def access(self, db: DbSession, secrets: SecretStore, connection: Connection) -> Access:
        """Whether the stored pairing works, without changing anything on the server."""
        ...

    def forget(self, db: DbSession, secrets: SecretStore, connection: Connection) -> None:
        """Remove the connection and its stored credentials from agdash."""
        ...
