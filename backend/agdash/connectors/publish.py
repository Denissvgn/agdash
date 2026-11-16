"""Publish mode: the one thing agdash writes to WorkChord, off unless turned on per server.

When on, agdash adds one external link on each WorkChord task a session worked
on, pointing at that session in agdash, so the task shows where its work was
observed. It never posts run events, usage or anything else: in WorkChord those
are the actor's own declarations.

* **Which tasks.** Only exact links: a work call in the transcript or a run that
  declared the session. Suggestions and server-only links never publish.
* **What is sent.** ``provider: "agdash"``, a loopback ``url`` to the session,
  a ``title`` naming the harness and the session's ``external_key``. No title,
  prompt, path or other transcript content.
* **Once.** A row per session and task in ``published_links`` keeps a link from
  being sent twice, and WorkChord returns the existing link when the same
  ``external_key`` comes again, so a lost answer cannot duplicate it.
* **Refusals.** A server refusal (4xx) is recorded and not retried; a server
  that cannot be reached is tried on the next pass.
* **How.** Through a client whose transport sends only POST, only to
  ``/api/tasks/<id>/external-links``. On a managed server it carries a full
  session paired for this alone and stored apart from the read session; a
  trusted-local server takes it without a credential.

A server must list ``agdash-external-links-v1`` among its features; an older one
would refuse the provider, and agdash never falls back to another.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from agdash.api.security import NotLoopback, ensure_loopback
from agdash.config import get_settings
from agdash.connectors.http import publish_client
from agdash.connectors.workchord import (
    MODES,
    PairingError,
    PendingPairing,
    Session,
    finish_pairing,
    publish_ref,
    server_mode,
    start_pairing,
)
from agdash.db.models import Connection, PublishedLink, RunLink
from agdash.db.models import Session as AgentSession
from agdash.platform.secrets import SecretStore
from agdash.vocab import Harness, RunLinkBasis

log = logging.getLogger("agdash.publish")

FEATURE = "agdash-external-links-v1"
WRITE_PATH = re.compile(r"/api/tasks/[1-9][0-9]{0,17}/external-links")
EXACT = (RunLinkBasis.TOOL_CALL, RunLinkBasis.TRACE_ID)
PUBLISHED, REFUSED = "published", "refused"
LABEL = "agdash publish mode"
PER_PASS = 50
HARNESS_NAMES = {
    Harness.CLAUDE_CODE.value: "Claude Code",
    Harness.CODEX.value: "Codex",
    Harness.ANTIGRAVITY.value: "Antigravity",
    Harness.PI.value: "Pi",
}


class PublishError(Exception):
    """Publish mode cannot be turned on. The message says why; it never holds a secret."""


def features(row: Connection) -> set[str]:
    return set((row.contract_seen or {}).get("features") or [])


def link_url(session_id: int, template: str | None = None) -> str:
    """The session's address in agdash. WorkChord takes only loopback for this provider."""
    url = (template or get_settings().publish_link).replace("{session_id}", str(session_id))
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
        raise PublishError("the publish link must be an http address on this computer")
    try:
        ensure_loopback(parts.hostname)
    except NotLoopback:
        raise PublishError("the publish link must point at this computer (loopback)") from None
    if len(url) > 1000:
        raise PublishError("the publish link is longer than WorkChord accepts")
    return url


# -- turning it on and off ----------------------------------------------------------


def enable(
    db: DbSession,
    secrets: SecretStore,
    row: Connection,
    *,
    show: Callable[[PendingPairing], None] = lambda pending: None,
    transport: httpx.BaseTransport | None = None,
    sleep=None,
) -> None:
    """Turn publish mode on for one server, pairing a full session for it where needed."""
    link_url(1)  # refuse a non-loopback link before asking anyone to approve anything
    me = server_mode(row.base_url, transport)
    if FEATURE not in (me.get("features") or []):
        raise PublishError(
            "this WorkChord server does not accept agdash links; update it to use publish mode"
        )
    seen = dict(row.contract_seen or {})
    seen["features"] = sorted(f for f in me.get("features") or [] if isinstance(f, str))
    row.contract_seen = seen
    if me.get("mode") == "managed":
        pending = start_pairing(row.base_url, transport, access="full", label=LABEL)
        show(pending)
        options = {"transport": transport} | ({"sleep": sleep} if sleep else {})
        session = finish_pairing(pending, **options)
        secrets.set(publish_ref(row.id), session.dump())
    elif me.get("mode") not in MODES:
        raise PublishError("the server did not answer as a WorkChord server")
    row.publish_enabled = True
    db.flush()


def disable(db: DbSession, secrets: SecretStore, row: Connection) -> None:
    secrets.delete(publish_ref(row.id))
    row.publish_enabled = False
    db.flush()


# -- publishing ---------------------------------------------------------------------


@dataclass
class Published:
    sent: int = 0
    refused: int = 0
    skipped: dict[int, str] = field(default_factory=dict)  # connection id -> why


class Publisher:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def run(self, db: DbSession, secrets: SecretStore, connections: list[Connection]) -> Published:
        published = Published()
        for row in connections:
            if not row.publish_enabled:
                continue
            why = self._connection(db, secrets, row, published)
            if why:
                published.skipped[row.id] = why
        db.flush()
        return published

    def _credentials(self, secrets: SecretStore, row: Connection) -> dict[str, str] | None:
        if (row.contract_seen or {}).get("auth_mode") == "trusted_local":
            return {}
        session = Session.load(secrets.get(publish_ref(row.id)))
        if session is None or session.expired():
            return None
        return {"Authorization": f"Bearer {session.token}"}

    def _connection(self, db, secrets, row: Connection, published: Published) -> str | None:
        if FEATURE not in features(row):
            return "the server does not accept agdash links"
        headers = self._credentials(secrets, row)
        if headers is None:
            return "the publish session is missing or expired; turn publish mode on again"
        done = select(PublishedLink.session_id, PublishedLink.task_remote_id).where(
            PublishedLink.connection_id == row.id
        )
        sent = {tuple(pair) for pair in db.execute(done)}
        pending = [
            (session_id, task_id)
            for session_id, task_id in db.execute(
                select(RunLink.session_id, RunLink.task_remote_id)
                .where(
                    RunLink.connection_id == row.id,
                    RunLink.basis.in_(EXACT),
                    RunLink.task_remote_id.is_not(None),
                )
                .distinct()
                .order_by(RunLink.session_id, RunLink.task_remote_id)
            )
            if (session_id, task_id) not in sent
        ]
        if not pending:
            return None
        with publish_client(
            row.base_url, WRITE_PATH, headers=headers, transport=self._transport
        ) as client:
            for session_id, task_id in pending[:PER_PASS]:
                session = db.get(AgentSession, session_id)
                if session is None:
                    continue
                body = {
                    "provider": "agdash",
                    "external_key": f"{session.harness}:{session.native_id}"[:255],
                    "url": link_url(session.id),
                    "title": f"Observed {HARNESS_NAMES.get(session.harness, session.harness)} "
                    "session",
                }
                try:
                    response = client.post(f"/api/tasks/{task_id}/external-links", json=body)
                except httpx.HTTPError:
                    return "the server could not be reached; trying again next pass"
                if response.status_code >= 500:
                    return f"the server answered HTTP {response.status_code}; trying again"
                ok = response.status_code in (200, 201)
                remote = response.json().get("id") if ok and _is_json(response) else None
                db.add(
                    PublishedLink(
                        connection_id=row.id,
                        session_id=session_id,
                        task_remote_id=task_id,
                        state=PUBLISHED if ok else REFUSED,
                        remote_link_id=remote if isinstance(remote, int) else None,
                        detail=None if ok else _refusal(response),
                        attempted_at=datetime.now(UTC),
                    )
                )
                db.flush()
                if ok:
                    published.sent += 1
                else:
                    published.refused += 1
                    log.warning(
                        "WorkChord refused a session link on connection %d: HTTP %d",
                        row.id,
                        response.status_code,
                    )
        return None


def _is_json(response: httpx.Response) -> bool:
    return response.headers.get("content-type", "").startswith("application/json")


def _refusal(response: httpx.Response) -> str:
    """The refusal's code, never the server's message, which may quote the request."""
    code = None
    if _is_json(response):
        detail = response.json().get("detail")
        if isinstance(detail, dict) and isinstance(detail.get("code"), str):
            code = detail["code"][:64]
    return f"HTTP {response.status_code}" + (f" {code}" if code else "")


__all__ = ["PairingError", "PublishError", "Publisher", "disable", "enable", "link_url"]
