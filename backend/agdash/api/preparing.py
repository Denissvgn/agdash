"""What answers on the service's port while its store is brought up to date.

Bringing a large store up to date, or compacting it, takes minutes, and the
service proper listens only once that is done. Until then the app found nothing
on the port and said the service could not be reached. This small stand-in says
what is true instead: ``/health`` answers ``preparing``, and every other path
answers 503 with the same word, so the app and the UI can say that the service
is getting its store ready and ask again by themselves.

It holds no data and opens neither the store nor the secret store, so it asks
for no token. The Host and Origin checks of the service proper still apply.

It listens only when preparing takes longer than a moment, and stops before the
service proper takes the port.
"""

import json
import logging
import threading
from collections.abc import Iterable

import uvicorn

from agdash import __version__
from agdash.api.security import LocalAccess
from agdash.config import Settings

log = logging.getLogger("agdash")

STATE = "preparing"
DETAIL = "the service is preparing its store"
RETRY_SECONDS = 5
# A store that is up to date is ready well within this; nothing listens for it.
LISTEN_AFTER_SECONDS = 1.0
STOP_WAIT_SECONDS = 5.0


async def _answer(scope, receive, send) -> None:
    if scope["type"] != "http":
        return
    if scope["path"] == "/health":
        status, answer = 200, {"status": STATE, "version": __version__}
    else:
        status, answer = 503, {"detail": DETAIL, "status": STATE}
    body = json.dumps(answer).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"),
    ]
    if status == 503:
        headers.append((b"retry-after", str(RETRY_SECONDS).encode()))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def preparing_app(port: int, extra_origins: Iterable[str] = ()):
    """The stand-in, behind the service's own Host and Origin checks and no token."""
    return LocalAccess(
        _answer,
        token=lambda: "",
        port=port,
        extra_origins=extra_origins,
        open_prefixes=("/",),
    )


class WhilePreparing:
    """Listens on the service's port from a moment into the preparing until it ends.

    with WhilePreparing(settings):
        prepare(...)
    """

    def __init__(self, settings: Settings, *, after: float = LISTEN_AFTER_SECONDS) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._over = False
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._timer = threading.Timer(after, self._listen)
        self._timer.daemon = True

    def __enter__(self) -> "WhilePreparing":
        self._timer.start()
        return self

    def _listen(self) -> None:
        with self._lock:
            if self._over:
                return
            settings = self._settings
            config = uvicorn.Config(
                preparing_app(settings.port, settings.extra_origins),
                host=settings.host,
                port=settings.port,
                access_log=False,
                server_header=False,
                proxy_headers=False,
                lifespan="off",
                timeout_graceful_shutdown=1,
                log_level="warning",
            )
            self._server = uvicorn.Server(config)
            self._thread = threading.Thread(
                target=self._server.run, name="agdash-preparing", daemon=True
            )
            self._thread.start()
        log.info(
            "preparing the store; saying so on %s:%d until it is ready",
            settings.host,
            settings.port,
        )

    def __exit__(self, *_exc) -> None:
        with self._lock:
            self._over = True
            self._timer.cancel()
        if self._server is not None and self._thread is not None:
            self._server.should_exit = True
            self._thread.join(timeout=STOP_WAIT_SECONDS)
