"""HTTP for connectors: GET only, except for the exact requests pairing needs.

A connector's data client goes through ``ReadOnlyTransport``, which refuses
every method but GET and HEAD before anything leaves the machine. Pairing has
its own client whose transport allows POST to a fixed list of paths and
nothing else. Publish mode, off by default, has a third client that can send
only POST to one path pattern and nothing at all besides. Redirects are never
followed, so a credential is only ever sent to the server it was issued by.
"""

import re
from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

import httpx

READ_METHODS = frozenset({"GET", "HEAD"})
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class WriteRefused(RuntimeError):
    """A connector tried to send a request that could change the tracker."""


class ReadOnlyTransport(httpx.BaseTransport):
    """Passes GET and HEAD to ``inner``; refuses everything else."""

    def __init__(self, inner: httpx.BaseTransport) -> None:
        self._inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method not in READ_METHODS:
            raise WriteRefused(f"{request.method} is not allowed: this connector only reads")
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


class PairingTransport(httpx.BaseTransport):
    """GET anywhere, POST only to the listed paths."""

    def __init__(self, inner: httpx.BaseTransport, post_paths: Iterable[str]) -> None:
        self._inner = inner
        self._post_paths = frozenset(post_paths)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method not in READ_METHODS and not (
            request.method == "POST" and request.url.path in self._post_paths
        ):
            raise WriteRefused(f"{request.method} {request.url.path} is not part of pairing")
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


def server_origin(address: str) -> str:
    """``scheme://host[:port]`` of a tracker. Plain HTTP is accepted on loopback only."""
    parts = urlsplit(address.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("give the server's address, such as https://work.example")
    if parts.username or parts.password:
        raise ValueError("the address must not carry a user name or password")
    if parts.scheme == "http" and parts.hostname not in LOOPBACK_HOSTS:
        raise ValueError("use https for a server that is not on this machine")
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, "", "", ""))


class PublishTransport(httpx.BaseTransport):
    """POST to paths that fully match ``path``; refuses everything else, reads included."""

    def __init__(self, inner: httpx.BaseTransport, path: re.Pattern[str]) -> None:
        self._inner = inner
        self._path = path

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST" or not self._path.fullmatch(request.url.path):
            raise WriteRefused(f"{request.method} {request.url.path} is not the publish write")
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


def read_only_client(
    base_url: str,
    *,
    headers: dict[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        headers=headers or {},
        transport=ReadOnlyTransport(transport or httpx.HTTPTransport()),
        follow_redirects=False,
        timeout=TIMEOUT,
    )


def publish_client(
    base_url: str,
    path: re.Pattern[str],
    *,
    headers: dict[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        headers=headers or {},
        transport=PublishTransport(transport or httpx.HTTPTransport(), path),
        follow_redirects=False,
        timeout=TIMEOUT,
    )


def pairing_client(
    base_url: str, post_paths: Iterable[str], *, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        transport=PairingTransport(transport or httpx.HTTPTransport(), post_paths),
        follow_redirects=False,
        timeout=TIMEOUT,
    )
