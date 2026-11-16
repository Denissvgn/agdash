"""The WorkChord connector's settings, for the app: servers, pairing, rules and pins.

The rest of the API only reads. These routes are the exception, and they change
agdash's own settings and nothing WorkChord holds:

* ``POST /workchord/pairings`` connects a server. A trusted-local one is
  connected at once; a managed one returns a code and a page where the person
  approves, read-only where the server offers it. The private verifier stays in
  this service; ``POST /workchord/pairings/{id}/finish`` asks once whether the
  person approved, and keeps the session in the secret store when they have.
  Pairing sends WorkChord only its two pairing requests.
* ``POST /workchord/connections/{id}/rules`` and ``.../pins`` add a folder,
  repository or launcher rule, or pin one session; ``DELETE
  /workchord/rules/{id}`` removes either. Sessions are mapped again at once.

They write through a connection the database lets change only the connector's
tables (see ``agdash.api.store``). Agent keys, webhook secrets and publish mode
stay on the command line, so no secret is typed into the app.
"""

import re
import secrets as random
from datetime import UTC, datetime
from threading import Lock
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from agdash.api.routes import Db
from agdash.api.store import Store
from agdash.connectors.mirror import object_states
from agdash.connectors.resolver import Resolver
from agdash.connectors.workchord import (
    KIND,
    PairingError,
    PendingPairing,
    begin_pair,
    complete_pair,
    exchange_once,
)
from agdash.db.models import Connection, MappingRule, Session, WcProject
from agdash.platform.secrets import SecretStore
from agdash.vocab import RuleMatchKind, RuleTargetKind

router = APIRouter(prefix="/workchord", tags=["workchord"])

MAX_PENDING = 8
_ABSOLUTE = re.compile(r"^(/|[A-Za-z]:[\\/]|\\\\)")


# -- shapes -------------------------------------------------------------------------


class SyncedObject(BaseModel):
    kind: str
    state: str
    detail: str | None
    rows: int | None
    checked_at: datetime
    synced_at: datetime | None


class ConnectionOut(BaseModel):
    id: int
    name: str | None
    server: str
    auth_mode: str | None = Field(description="managed or trusted_local, as the server said.")
    credential: Literal["read_only_session", "full_session", "none"]
    agent_key: bool
    publish: bool
    sync_state: str
    last_synced_at: datetime | None
    last_error: str | None
    objects: list[SyncedObject]


class PairingIn(BaseModel):
    address: str = Field(max_length=1000)
    name: str | None = Field(None, max_length=255)


class PairingOut(BaseModel):
    status: Literal["connected", "waiting"]
    connection_id: int | None = None
    pairing_id: str | None = None
    verification_code: str | None = None
    verification_url: str | None = None
    expires_at: datetime | None = None
    read_only: bool | None = Field(None, description="Whether a read-only session was asked for.")


class ProjectOut(BaseModel):
    id: int
    name: str


class RuleOut(BaseModel):
    id: int
    match: RuleMatchKind
    value: str | None = Field(description="The folder, repository or launcher; null for a pin.")
    session_id: int | None
    target: RuleTargetKind
    target_value: str
    target_name: str | None
    harness: str | None
    note: str | None


class RuleIn(BaseModel):
    match: Literal["folder", "repository", "launcher"]
    value: str = Field(min_length=1, max_length=1000)
    project_id: int | None = None
    runtime_ref: str | None = Field(None, max_length=1024)
    harness: str | None = Field(None, max_length=32)
    note: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def one_target(self) -> "RuleIn":
        if (self.project_id is None) == (self.runtime_ref is None):
            raise ValueError("give a project_id or a runtime_ref")
        if self.match == "folder" and not _ABSOLUTE.match(self.value):
            raise ValueError("give the folder as an absolute path")
        return self


class PinIn(BaseModel):
    session_id: int
    project_id: int | None = None
    actor_id: int | None = None

    @model_validator(mode="after")
    def one_target(self) -> "PinIn":
        if (self.project_id is None) == (self.actor_id is None):
            raise ValueError("give a project_id or an actor_id")
        return self


# -- plumbing -----------------------------------------------------------------------


def _writer(request: Request):
    store: Store = request.app.state.store
    with store.writing() as db:
        yield db


Writer = Annotated[DbSession, Depends(_writer)]


def _secrets(request: Request) -> SecretStore:
    found = getattr(request.app.state, "secrets", None)
    if found is None:
        from agdash.platform import current_host

        found = request.app.state.secrets = current_host().secret_store()
    return found


def _transport(request: Request) -> httpx.BaseTransport | None:
    return getattr(request.app.state, "workchord_transport", None)


class _Pending:
    """Pairings waiting for the person, held in this process only."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[PendingPairing, str | None]] = {}
        self._lock = Lock()

    def add(self, pending: PendingPairing, name: str | None) -> str:
        with self._lock:
            now = datetime.now(UTC)
            for key in [k for k, (p, _) in self._items.items() if p.expires_at <= now]:
                del self._items[key]
            while len(self._items) >= MAX_PENDING:
                self._items.pop(next(iter(self._items)))
            key = random.token_urlsafe(16)
            self._items[key] = (pending, name)
            return key

    def get(self, key: str) -> tuple[PendingPairing, str | None] | None:
        with self._lock:
            return self._items.get(key)

    def drop(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)


def _pending(request: Request) -> _Pending:
    found = getattr(request.app.state, "pairings", None)
    if found is None:
        found = request.app.state.pairings = _Pending()
    return found


def _connection(db: DbSession, connection_id: int) -> Connection:
    row = db.get(Connection, connection_id)
    if row is None or row.kind != KIND:
        raise HTTPException(status_code=404, detail="no such connection")
    return row


def _connection_out(db: DbSession, row: Connection) -> ConnectionOut:
    seen = row.contract_seen or {}
    credential = (
        "none"
        if not row.credential_ref
        else ("read_only_session" if seen.get("session_access") == "observe" else "full_session")
    )
    return ConnectionOut(
        id=row.id,
        name=row.name,
        server=row.base_url,
        auth_mode=seen.get("auth_mode"),
        credential=credential,
        agent_key=bool(row.run_credential_ref),
        publish=row.publish_enabled,
        sync_state=row.sync_state.value,
        last_synced_at=row.last_synced_at,
        last_error=row.last_error,
        objects=[
            SyncedObject(
                kind=o.kind.value,
                state=o.state.value,
                detail=o.detail,
                rows=o.rows,
                checked_at=o.checked_at,
                synced_at=o.synced_at,
            )
            for o in object_states(db, row.id)
        ],
    )


def _rule_out(db: DbSession, rule: MappingRule) -> RuleOut:
    name = None
    if rule.target_kind is RuleTargetKind.PROJECT and rule.target_value.isdigit():
        name = db.scalar(
            select(WcProject.name).where(
                WcProject.connection_id == rule.connection_id,
                WcProject.remote_id == int(rule.target_value),
            )
        )
    return RuleOut(
        id=rule.id,
        match=rule.match_kind,
        value=rule.match_value,
        session_id=rule.session_id,
        target=rule.target_kind,
        target_value=rule.target_value,
        target_name=name,
        harness=rule.harness,
        note=rule.note,
    )


def _remap(db: DbSession, row: Connection) -> None:
    Resolver().run(db, [row])


# -- servers ------------------------------------------------------------------------


@router.get("/connections", response_model=list[ConnectionOut], summary="Connected servers")
def connections(db: Db) -> list[ConnectionOut]:
    """Each server, how agdash is let in, and how its last sync went."""
    rows = db.scalars(select(Connection).where(Connection.kind == KIND).order_by(Connection.id))
    return [_connection_out(db, row) for row in rows]


@router.post("/pairings", response_model=PairingOut, summary="Connect a server")
def start(body: PairingIn, request: Request, db: Writer, response: Response) -> PairingOut:
    """Connect at once on a trusted-local server; otherwise start a pairing to approve."""
    try:
        connected, pending = begin_pair(
            db, _secrets(request), body.address, name=body.name, transport=_transport(request)
        )
    except (PairingError, ValueError) as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from None
    if connected is not None:
        db.commit()
        response.status_code = 201
        return PairingOut(status="connected", connection_id=connected.id)
    assert pending is not None
    key = _pending(request).add(pending, body.name)
    response.status_code = 202
    return PairingOut(
        status="waiting",
        pairing_id=key,
        verification_code=pending.verification_code,
        verification_url=pending.verification_url,
        expires_at=pending.expires_at,
        read_only=pending.read_only,
    )


@router.post("/pairings/{pairing_id}/finish", response_model=PairingOut, summary="Finish a pairing")
def finish(pairing_id: str, request: Request, db: Writer, response: Response) -> PairingOut:
    """Ask once whether the person approved. Waiting answers 202; ask again in a moment."""
    held = _pending(request)
    found = held.get(pairing_id)
    if found is None:
        raise HTTPException(status_code=404, detail="no such pairing; start again")
    pending, name = found
    if pending.expires_at <= datetime.now(UTC):
        held.drop(pairing_id)
        raise HTTPException(status_code=410, detail="the pairing expired; start again")
    transport = _transport(request)
    try:
        session = exchange_once(pending, transport=transport)
        if session is None:
            response.status_code = 202
            return PairingOut(status="waiting", pairing_id=pairing_id, read_only=pending.read_only)
        row = complete_pair(db, _secrets(request), pending, session, name=name, transport=transport)
    except PairingError as error:
        db.rollback()
        held.drop(pairing_id)
        raise HTTPException(status_code=409, detail=str(error)) from None
    db.commit()
    held.drop(pairing_id)
    response.status_code = 201
    return PairingOut(status="connected", connection_id=row.id, read_only=pending.read_only)


@router.get(
    "/connections/{connection_id}/projects",
    response_model=list[ProjectOut],
    summary="A server's projects",
)
def projects(db: Db, connection_id: int) -> list[ProjectOut]:
    """The projects agdash has copied from the server, to map sessions to."""
    _connection(db, connection_id)
    rows = db.execute(
        select(WcProject.remote_id, WcProject.name)
        .where(WcProject.connection_id == connection_id)
        .order_by(WcProject.name)
    )
    return [ProjectOut(id=remote, name=name) for remote, name in rows]


# -- rules and pins -----------------------------------------------------------------


@router.get(
    "/connections/{connection_id}/rules",
    response_model=list[RuleOut],
    summary="A server's rules and pins",
)
def rules(db: Db, connection_id: int) -> list[RuleOut]:
    _connection(db, connection_id)
    found = db.scalars(
        select(MappingRule)
        .where(MappingRule.connection_id == connection_id)
        .order_by(MappingRule.priority.desc(), MappingRule.id)
    )
    return [_rule_out(db, rule) for rule in found]


def _project_known(db: DbSession, connection_id: int, project_id: int) -> None:
    known = db.scalar(
        select(WcProject.id).where(
            WcProject.connection_id == connection_id, WcProject.remote_id == project_id
        )
    )
    if known is None:
        raise HTTPException(status_code=422, detail="no such project on this server")


@router.post(
    "/connections/{connection_id}/rules",
    response_model=RuleOut,
    status_code=201,
    summary="Add a rule",
)
def add_rule(db: Writer, connection_id: int, body: RuleIn) -> RuleOut:
    """Map sessions in a folder, of a repository or from a launcher; then map again."""
    row = _connection(db, connection_id)
    if body.project_id is not None:
        _project_known(db, connection_id, body.project_id)
    rule = MappingRule(
        connection_id=connection_id,
        harness=body.harness or None,
        match_kind=RuleMatchKind(body.match),
        match_value=body.value.strip(),
        target_kind=RuleTargetKind.PROJECT
        if body.project_id is not None
        else RuleTargetKind.RUNTIME_REF,
        target_value=str(body.project_id) if body.project_id is not None else body.runtime_ref,
        note=body.note or None,
    )
    db.add(rule)
    db.flush()
    _remap(db, row)
    db.commit()
    return _rule_out(db, rule)


@router.post(
    "/connections/{connection_id}/pins",
    response_model=RuleOut,
    status_code=201,
    summary="Pin a session",
)
def pin(db: Writer, connection_id: int, body: PinIn) -> RuleOut:
    """Pin one session to a project or an actor, replacing its earlier pin of that kind."""
    row = _connection(db, connection_id)
    if db.get(Session, body.session_id) is None:
        raise HTTPException(status_code=404, detail="no such session")
    if body.project_id is not None:
        _project_known(db, connection_id, body.project_id)
    kind = RuleTargetKind.PROJECT if body.project_id is not None else RuleTargetKind.ACTOR
    for old in db.scalars(
        select(MappingRule).where(
            MappingRule.connection_id == connection_id,
            MappingRule.match_kind == RuleMatchKind.SESSION,
            MappingRule.session_id == body.session_id,
            MappingRule.target_kind == kind,
        )
    ):
        db.delete(old)
    target = body.project_id if body.project_id is not None else body.actor_id
    rule = MappingRule(
        connection_id=connection_id,
        match_kind=RuleMatchKind.SESSION,
        session_id=body.session_id,
        target_kind=kind,
        target_value=str(target),
    )
    db.add(rule)
    db.flush()
    _remap(db, row)
    db.commit()
    return _rule_out(db, rule)


@router.delete("/rules/{rule_id}", status_code=204, summary="Remove a rule or a pin")
def remove_rule(db: Writer, rule_id: int) -> Response:
    rule = db.get(MappingRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="no such rule")
    row = _connection(db, rule.connection_id)
    db.delete(rule)
    db.flush()
    _remap(db, row)
    db.commit()
    return Response(status_code=204)


WRITE_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/workchord/pairings"),
        ("POST", "/workchord/pairings/{pairing_id}/finish"),
        ("POST", "/workchord/connections/{connection_id}/rules"),
        ("POST", "/workchord/connections/{connection_id}/pins"),
        ("DELETE", "/workchord/rules/{rule_id}"),
    }
)
"""Every route that changes anything; a test holds the API to exactly this list."""
