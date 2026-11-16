"""Who may talk to the service: this host, through agdash's own app, with the token.

Three checks run on every request before any handler, the live stream included:

* **Host** must name the loopback interface on the service's port. A web page
  that points its own domain at 127.0.0.1 (DNS rebinding) sends its own name
  and is refused.
* **Origin**, when a browser sends one, must be agdash's own: the service's
  loopback address or the desktop app's web view. No CORS headers are ever
  sent, so other pages cannot read a response either.
* **The local token**, sent as ``Authorization: Bearer``, must match. It lives
  in agdash's secret store and is created on first use.

``/health`` answers without the token, so the app can tell whether the service
runs, but it carries no data and still needs a loopback Host. So do the files of
the UI, when the service serves it (``agdash.api.ui``): they hold no data, and
every ``/api`` request they make needs the token.
"""

import hmac
import ipaddress
import json
import secrets as random
import socket
from collections.abc import Callable, Iterable
from threading import Lock

from agdash.platform.secrets import SecretStore

TOKEN_NAME = "api.token"
LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})
# The desktop shell's web view, on macOS and on Linux.
APP_ORIGINS = ("tauri://localhost", "http://tauri.localhost", "https://tauri.localhost")
OPEN_PATHS = frozenset({"/health"})


class NotLoopback(ValueError):
    """The service was asked to listen somewhere other than this host."""


def ensure_loopback(host: str) -> None:
    """Refuse any address that is not, or does not resolve only to, the loopback interface."""
    try:
        if ipaddress.ip_address(host.strip("[]")).is_loopback:
            return
        raise NotLoopback(f"{host} is not a loopback address; agdash listens on this host only")
    except ValueError as error:
        if isinstance(error, NotLoopback):
            raise
    try:
        resolved = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        raise NotLoopback(f"{host} does not resolve; use 127.0.0.1") from None
    if not resolved or not all(ipaddress.ip_address(a.split("%")[0]).is_loopback for a in resolved):
        raise NotLoopback(f"{host} resolves beyond this host; agdash listens on loopback only")


def load_or_create_token(store: SecretStore) -> str:
    token = store.get(TOKEN_NAME)
    if not token:
        token = random.token_urlsafe(32)
        store.set(TOKEN_NAME, token)
    return token


class LazyToken:
    """The token, read from the secret store on first use rather than at import."""

    def __init__(self, source: Callable[[], str]) -> None:
        self._source = source
        self._value: str | None = None
        self._lock = Lock()

    def __call__(self) -> str:
        with self._lock:
            if self._value is None:
                self._value = self._source()
            return self._value


def allowed_hosts(port: int) -> frozenset[str]:
    names = {"localhost", "127.0.0.1", "[::1]"}
    return frozenset(names | {f"{name}:{port}" for name in names})


def allowed_origins(port: int, extra: Iterable[str] = ()) -> frozenset[str]:
    own = {f"http://{name}:{port}" for name in ("localhost", "127.0.0.1", "[::1]")}
    return frozenset(own | set(APP_ORIGINS) | set(extra))


class LocalAccess:
    """ASGI middleware applying the three checks. Refusals carry no detail beyond the reason."""

    def __init__(
        self,
        app,
        *,
        token: Callable[[], str],
        port: int,
        extra_origins: Iterable[str] = (),
        open_paths: Iterable[str] = (),
        open_prefixes: Iterable[str] = (),
    ) -> None:
        self.app = app
        self.token = token
        self.open_paths = OPEN_PATHS | frozenset(open_paths)
        self.open_prefixes = tuple(open_prefixes)
        self.hosts = allowed_hosts(port)
        self.origins = allowed_origins(port, extra_origins)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 1008})
        refusal = self._refusal(scope["path"], headers)
        if refusal is not None:
            status, reason = refusal
            return await self._refuse(send, status, reason)
        return await self.app(scope, receive, send)

    def _refusal(self, path: str, headers: dict[str, str]) -> tuple[int, str] | None:
        if headers.get("host", "").lower() not in self.hosts:
            return 421, "this service answers on its loopback address only"
        origin = headers.get("origin")
        if origin is not None and origin not in self.origins:
            return 403, "requests from other web pages are refused"
        if path in self.open_paths or path.startswith(self.open_prefixes):
            return None
        scheme, _, presented = headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            presented.encode(), self.token().encode()
        ):
            return 401, "the local token is missing or wrong"
        return None

    @staticmethod
    async def _refuse(send, status: int, reason: str) -> None:
        body = json.dumps({"detail": reason}).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]
        if status == 401:
            headers.append((b"www-authenticate", b"Bearer"))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})
