"""An optional receiver for WorkChord's outgoing webhooks, off by default.

Polling keeps agdash's copy of WorkChord current on its own. When a server can
reach this host, a webhook only makes the next sync of that connection happen
now instead of at the next poll. Nothing in a delivery is stored or trusted
beyond that: the sync reads the server as usual.

On, it is its own listener (``AGDASH_WEBHOOK_PORT``), on the same loopback
address as the service and apart from the API, which a delivery cannot reach.
Off, nothing listens.

Each connection has its own address, ``/workchord/<connection id>``, and its
own signing secret, which the person sets both in WorkChord's target and in
agdash (``agdash connect webhook-secret ID``). A delivery is accepted only when:

* it is a POST of at most 1 MiB;
* the connection exists and has a secret;
* ``X-WorkChord-Signature-256`` is ``sha256=`` and the HMAC-SHA256 of the raw
  body with that secret, compared in constant time;
* it carries an ``X-WorkChord-Event-Id``.

Anything else gets 401 and no detail. WorkChord signs no timestamp, so a
recorded delivery could be sent again: an event id accepted lately is answered
204 and changes nothing, which is also right for WorkChord's own retries.
Neither bodies nor signatures are logged.
"""

import hashlib
import hmac
import logging
import re
import threading
from collections import OrderedDict
from collections.abc import Callable

from fastapi import FastAPI, Request, Response
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from agdash.connectors.workchord import KIND, webhook_ref
from agdash.db.models import Connection
from agdash.platform.secrets import SecretStore

log = logging.getLogger("agdash.webhook")

MAX_BODY = 1024 * 1024
REMEMBERED_EVENTS = 4096
SIGNATURE = "X-WorkChord-Signature-256"
EVENT_ID = "X-WorkChord-Event-Id"
EVENT_TYPE = "X-WorkChord-Event"
TEST_EVENT = "webhook.test"
_EVENT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def signature(secret: str, body: bytes) -> str:
    """How WorkChord signs a delivery."""
    return "sha256=" + hmac.new(secret.strip().encode(), body, hashlib.sha256).hexdigest()


class _Seen:
    """Event ids accepted lately, oldest forgotten first."""

    def __init__(self, size: int = REMEMBERED_EVENTS) -> None:
        self._ids: OrderedDict[tuple[int, str], None] = OrderedDict()
        self._size = size
        self._lock = threading.Lock()

    def add(self, key: tuple[int, str]) -> bool:
        """True when ``key`` is new."""
        with self._lock:
            if key in self._ids:
                return False
            self._ids[key] = None
            while len(self._ids) > self._size:
                self._ids.popitem(last=False)
            return True


def create_receiver(
    sessions: sessionmaker[DbSession],
    secrets: SecretStore,
    wake: Callable[[int], None],
) -> FastAPI:
    """The receiver app. ``wake`` asks for a sync of one connection now."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    seen = _Seen()
    refused = Response(status_code=401)

    @app.post("/workchord/{connection_id}", status_code=204)
    async def deliver(connection_id: int, request: Request) -> Response:
        declared = request.headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY):
            return Response(status_code=413)
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BODY:
                return Response(status_code=413)
        with sessions() as db:
            connection = db.get(Connection, connection_id)
            known = connection is not None and connection.kind == KIND
        secret = secrets.get(webhook_ref(connection_id)) if known else None
        given = request.headers.get(SIGNATURE, "")
        if not secret or not hmac.compare_digest(given.encode(), signature(secret, body).encode()):
            return refused
        event_id = request.headers.get(EVENT_ID, "")
        if not _EVENT_ID.fullmatch(event_id):
            return refused
        if not seen.add((connection_id, event_id)):
            return Response(status_code=204)
        event = request.headers.get(EVENT_TYPE, "")
        log.info("WorkChord delivery %s for connection %d", event[:64] or "?", connection_id)
        if event != TEST_EVENT:
            wake(connection_id)
        return Response(status_code=204)

    return app
