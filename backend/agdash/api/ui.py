"""The UI, served by the service itself, for a host with no desktop app.

On a server with no display the person opens the UI in a browser on their own
computer, through an SSH tunnel to the service's loopback port. The service then
serves the built UI: ``index.html`` at ``/``, the files beside it, and
``/assets/``. Those files hold no data, so they are served without the local
token; every ``/api`` request still needs it. ``agdash open`` prints a link
that hands the browser the token in the URL fragment, which a browser never
sends to a server.

Where the UI comes from: ``AGDASH_UI_DIR`` when set, otherwise the copy frozen
into the ``agdash`` binary (``agdash/ui``), otherwise none, and only the API is
served.
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

PACKAGED = Path(__file__).resolve().parent.parent / "ui"
ASSETS = "/assets/"
# The UI talks only to this service; nothing else may run, load or frame it.
HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def ui_directory(configured: str | None) -> Path | None:
    """The built UI to serve, or None when there is none."""
    for candidate in ([Path(configured)] if configured else []) + [PACKAGED]:
        if (candidate / "index.html").is_file():
            return candidate.resolve()
    return None


def mount_ui(app: FastAPI, directory: Path) -> tuple[frozenset[str], tuple[str, ...]]:
    """Serve the UI; return the paths and prefixes that need no token."""
    top = {
        f"/{item.name}"
        for item in directory.iterdir()
        if item.is_file() and not item.name.startswith(".")
    }

    def respond(path: Path, *, page: bool = False) -> FileResponse:
        headers = dict(HEADERS)
        headers["Cache-Control"] = "no-store" if page else "public, max-age=31536000, immutable"
        return FileResponse(path, headers=headers)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return respond(directory / "index.html", page=True)

    @app.get("/{name}", include_in_schema=False)
    def top_file(name: str) -> FileResponse:
        if f"/{name}" not in top:
            raise HTTPException(status_code=404)
        return respond(directory / name, page=name == "index.html")

    assets = (directory / "assets").resolve()

    @app.get("/assets/{rest:path}", include_in_schema=False)
    def asset(rest: str) -> FileResponse:
        found = (assets / rest).resolve()
        if not found.is_relative_to(assets) or not found.is_file():
            raise HTTPException(status_code=404)
        return respond(found)

    return frozenset({"/", *top}), (ASSETS,)


def open_link(port: int, token: str) -> str:
    """The link that opens the UI with the local token, which stays in the fragment."""
    return f"http://127.0.0.1:{port}/#agdash-token={token}"
