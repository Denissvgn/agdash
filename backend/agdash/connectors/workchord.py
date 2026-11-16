"""The WorkChord connection: pairing, stored credentials and a read-only client.

How agdash gets in depends on the server's access mode, which it reports at
``GET /api/auth/me``:

* **managed** — agdash pairs through WorkChord's native-connection flow. It
  starts a connection with an S256 proof, the person opens the verification
  page in their browser, signs in, compares the code and approves; agdash then
  exchanges its private verifier for a bearer session of that person. The
  session sees what the person can see. It is not read-only on the server, so
  the client below can only send GET requests.
* **trusted_local** — the server lets local readers in without a credential,
  and has no signed-in person to approve a pairing. agdash connects with no
  credential at all.

A second, optional credential, an agent key with ``planning:read``, reads what
a person's session cannot: runs, the actor roster and model bindings, and on
older servers assignments too. WorkChord refuses a request that carries
credentials of two different principals, so the agent key is only ever sent,
alone, to those agent endpoints. docs/workchord-access.md lists who may read what.

Credentials live in agdash's secret store under names derived from the
connection id; the ``connections`` row holds only those names.
"""

import base64
import hashlib
import json
import secrets as random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from agdash.connectors.base import Access
from agdash.connectors.http import pairing_client, read_only_client, server_origin
from agdash.db.models import Connection
from agdash.platform.secrets import SecretStore
from agdash.vocab import SyncState

KIND = "workchord"
API = "/api"
START = f"{API}/auth/native-connections/start"
EXCHANGE = f"{API}/auth/native-connections/exchange"
PAIRING_POSTS = (START, EXCHANGE)
MODES = ("managed", "trusted_local")
POLL_SECONDS = 2.0


class PairingError(Exception):
    """Pairing could not finish. The message says what to do; it never holds a secret."""


def session_ref(connection_id: int) -> str:
    return f"workchord.{connection_id}.session"


def run_key_ref(connection_id: int) -> str:
    return f"workchord.{connection_id}.run-key"


def webhook_ref(connection_id: int) -> str:
    """The secret WorkChord signs this connection's webhook deliveries with."""
    return f"workchord.{connection_id}.webhook"


# -- the read-only client -----------------------------------------------------------


class WorkChordClient:
    """GET requests to one WorkChord server, with the credentials agdash holds for it."""

    def __init__(
        self,
        origin: str,
        *,
        session_token: str | None = None,
        run_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        bearer = {"Authorization": f"Bearer {session_token}"} if session_token else {}
        self._main = read_only_client(origin, headers=bearer, transport=transport)
        self._runs = (
            read_only_client(origin, headers={"X-Agent-API-Key": run_key}, transport=transport)
            if run_key
            else None
        )

    def close(self) -> None:
        self._main.close()
        if self._runs is not None:
            self._runs.close()

    def __enter__(self) -> "WorkChordClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _get(self, path: str, *, runs: bool = False, **params: Any) -> Any:
        client = self._runs if runs else self._main
        if client is None:
            raise PermissionError("this needs an agent key with planning:read")
        response = client.get(
            f"{API}{path}", params={k: v for k, v in params.items() if v is not None}
        )
        response.raise_for_status()
        return response.json()

    @property
    def can_read_runs(self) -> bool:
        return self._runs is not None

    def me(self) -> dict[str, Any]:
        return self._get("/auth/me")

    def projects(self) -> Any:
        return self._get("/projects")

    def iterations(self) -> Any:
        return self._get("/iterations")

    def task_lookup(self, **filters: Any) -> Any:
        return self._get("/tasks/lookup", **filters)

    def iteration_tasks(self, iteration_id: int) -> Any:
        return self._get(f"/iterations/{iteration_id}/tasks")

    def project_tasks(self, project_id: int) -> Any:
        """Every task of a project the reader can see, backlog included, as task trees."""
        return self._get(f"/projects/{project_id}/tasks")

    def task(self, task_id: int) -> Any:
        return self._get(f"/tasks/{task_id}")

    def team_member_profiles(self) -> Any:
        return self._get("/team-member-profiles")

    def pipeline(self) -> Any:
        """People and local readers see it on the same rule as tasks."""
        return self._get("/agent/pipeline")

    def assignments(self, **filters: Any) -> Any:
        """With the agent key, its queue boundary; without it, current servers let a person
        or local reader see the assignments of the tasks they can see."""
        return self._get("/agent/assignments", runs=self.can_read_runs, **filters)

    def run_list(self, **filters: Any) -> Any:
        """Runs, newest first, on servers that let a person read them without an agent key."""
        return self._get("/agent/runs", **filters)

    # These need the agent key.

    def run(self, run_id: int) -> Any:
        return self._get(f"/agent/runs/{run_id}", runs=True)

    def run_usage(self, run_id: int) -> Any:
        """The usage the run's actor declared, or None when it declared none."""
        return self._get(f"/agent/runs/{run_id}/usage", runs=True)

    def actors(self) -> Any:
        return self._get("/agent/actors", runs=True)

    def model_bindings(self) -> Any:
        return self._get("/agent/model-bindings", runs=True)


# -- pairing ------------------------------------------------------------------------


@dataclass(frozen=True)
class PendingPairing:
    """A started native connection, waiting for the person to approve it."""

    origin: str
    request_id: str
    verifier: str
    verification_code: str
    verification_url: str
    expires_at: datetime


@dataclass(frozen=True)
class Session:
    token: str
    expires_at: datetime | None

    def dump(self) -> str:
        expires = self.expires_at.isoformat() if self.expires_at else None
        return json.dumps({"token": self.token, "expires_at": expires})

    @classmethod
    def load(cls, raw: str | None) -> "Session | None":
        if not raw:
            return None
        try:
            stored = json.loads(raw)
            expires = stored.get("expires_at")
            return cls(stored["token"], datetime.fromisoformat(expires) if expires else None)
        except (ValueError, KeyError, TypeError):
            return None

    def expired(self, now: datetime | None = None) -> bool:
        return self.expires_at is not None and self.expires_at <= (now or datetime.now(UTC))


def _verifier() -> tuple[str, str]:
    verifier = random.token_urlsafe(64)  # 86 characters of [A-Za-z0-9_-]
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def server_mode(origin: str, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """What the server says about itself at ``/api/auth/me``, asked with no credential."""
    with WorkChordClient(origin, transport=transport) as client:
        try:
            me = client.me()
        except httpx.HTTPError as error:
            raise PairingError(f"could not reach WorkChord at {origin}: {error}") from None
    if me.get("mode") not in MODES:
        raise PairingError(f"{origin} did not answer as a WorkChord server")
    return me


def start_pairing(origin: str, transport: httpx.BaseTransport | None = None) -> PendingPairing:
    verifier, challenge = _verifier()
    with pairing_client(origin, PAIRING_POSTS, transport=transport) as client:
        response = client.post(START, json={"code_challenge": challenge})
    if response.status_code != 201:
        raise PairingError(f"WorkChord did not start a connection (HTTP {response.status_code})")
    body = response.json()
    return PendingPairing(
        origin=origin,
        request_id=body["request_id"],
        verifier=verifier,
        verification_code=body["verification_code"],
        verification_url=origin + body["verification_path"],
        expires_at=_when(body["expires_at"]) or datetime.now(UTC),
    )


def finish_pairing(
    pending: PendingPairing,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Session:
    """Wait for the person to approve in the browser, then take the session."""
    with pairing_client(pending.origin, PAIRING_POSTS, transport=transport) as client:
        while now() < pending.expires_at:
            response = client.post(
                EXCHANGE, json={"request_id": pending.request_id, "code_verifier": pending.verifier}
            )
            if response.status_code == 200:
                body = response.json()
                if body.get("status") == "pending":
                    sleep(POLL_SECONDS)
                    continue
                if body.get("token_type") == "Bearer" and body.get("access_token"):
                    return Session(body["access_token"], _when(body.get("expires_at")))
            if response.status_code in (401, 403):
                raise PairingError(
                    "the approval was withdrawn; sign in in the browser and start again"
                )
            if response.status_code in (404, 410):
                raise PairingError("the connection ended before it was approved; start again")
            raise PairingError(f"WorkChord refused the exchange (HTTP {response.status_code})")
    raise PairingError("the connection was not approved in time; start again")


# -- stored connections -------------------------------------------------------------


def _row(db: DbSession, origin: str) -> Connection:
    row = db.scalars(
        select(Connection).where(Connection.kind == KIND, Connection.base_url == origin)
    ).one_or_none()
    if row is None:
        row = Connection(kind=KIND, base_url=origin)
        db.add(row)
        db.flush()
    return row


def save_connection(
    db: DbSession,
    secrets: SecretStore,
    origin: str,
    me: dict[str, Any],
    session: Session | None,
    *,
    name: str | None = None,
) -> Connection:
    """Record a paired server; its session, if any, goes to the secret store."""
    row = _row(db, origin)
    row.name = name or row.name
    row.contract_seen = {"auth_mode": me.get("mode"), "seen_at": datetime.now(UTC).isoformat()}
    if session is None:
        if row.credential_ref:
            secrets.delete(row.credential_ref)
        row.credential_ref = None
    else:
        secrets.set(session_ref(row.id), session.dump())
        row.credential_ref = session_ref(row.id)
    row.sync_state = SyncState.NEVER
    row.last_error = None
    db.flush()
    return row


def set_run_key(db: DbSession, secrets: SecretStore, row: Connection, key: str | None) -> None:
    if key:
        secrets.set(run_key_ref(row.id), key)
        row.run_credential_ref = run_key_ref(row.id)
    else:
        if row.run_credential_ref:
            secrets.delete(row.run_credential_ref)
        row.run_credential_ref = None
    db.flush()


def open_client(
    secrets: SecretStore, row: Connection, transport: httpx.BaseTransport | None = None
) -> WorkChordClient:
    """A read-only client with the credentials stored for ``row``."""
    session = Session.load(secrets.get(row.credential_ref)) if row.credential_ref else None
    run_key = secrets.get(row.run_credential_ref) if row.run_credential_ref else None
    return WorkChordClient(
        row.base_url,
        session_token=session.token if session else None,
        run_key=run_key,
        transport=transport,
    )


class WorkChordConnector:
    kind = KIND

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def access(self, db: DbSession, secrets: SecretStore, connection: Connection) -> Access:
        session = (
            Session.load(secrets.get(connection.credential_ref))
            if connection.credential_ref
            else None
        )
        mode = "session" if connection.credential_ref else "open"
        if connection.credential_ref and session is None:
            return Access(mode, False, problem="the stored session is missing; pair again")
        if session is not None and session.expired():
            return Access(
                mode,
                False,
                expires_at=session.expires_at,
                problem="the session expired; pair again",
            )
        with open_client(secrets, connection, self._transport) as client:
            try:
                me = client.me()
            except httpx.HTTPError as error:
                return Access(mode, False, problem=f"could not reach the server: {error}")
            run_traces = client.can_read_runs
        principal = (me.get("principal") or {}).get("display_name")
        local = me.get("mode") == "trusted_local" and not connection.credential_ref
        problem = None
        if connection.credential_ref and not me.get("authenticated"):
            problem = me.get("authentication_error") or "the server no longer accepts the session"
        return Access(
            mode=mode,
            authenticated=bool(me.get("authenticated")) or local,
            principal=principal,
            workspace_role=me.get("workspace_role"),
            projects=None if local else len(me.get("projects") or {}),
            run_traces=run_traces,
            expires_at=session.expires_at if session else None,
            problem=problem,
            notes=("open local workspace: no credential is used",) if local else (),
        )

    def forget(self, db: DbSession, secrets: SecretStore, connection: Connection) -> None:
        for ref in (
            connection.credential_ref,
            connection.run_credential_ref,
            webhook_ref(connection.id),
        ):
            if ref:
                secrets.delete(ref)
        db.delete(connection)
        db.flush()


def pair(
    db: DbSession,
    secrets: SecretStore,
    address: str,
    *,
    name: str | None = None,
    show: Callable[[PendingPairing], None] = lambda pending: None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Connection:
    """Connect agdash to a WorkChord server, pairing through the browser where it must."""
    origin = server_origin(address)
    me = server_mode(origin, transport)
    if me["mode"] == "trusted_local":
        return save_connection(db, secrets, origin, me, None, name=name)
    pending = start_pairing(origin, transport)
    show(pending)
    session = finish_pairing(pending, transport=transport, sleep=sleep)
    with WorkChordClient(origin, session_token=session.token, transport=transport) as client:
        confirmed = client.me()
    if not confirmed.get("authenticated"):
        raise PairingError("WorkChord did not accept the new session")
    return save_connection(db, secrets, origin, confirmed, session, name=name)
