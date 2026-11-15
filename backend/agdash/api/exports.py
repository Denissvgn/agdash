"""Session export as a download: the person's app saves it where they choose."""

from collections.abc import Iterator
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from agdash.api.store import Store
from agdash.db.models import Session
from agdash.export import FORMATS, MEDIA_TYPES, render

router = APIRouter()


@router.get(
    "/sessions/{session_id}/export",
    summary="Export a session",
    response_class=StreamingResponse,
    responses={200: {"content": {media: {} for media in set(MEDIA_TYPES.values())}}},
)
def export_session(
    request: Request,
    session_id: int,
    format: Literal["jsonl", "markdown", "raw"] = "markdown",  # noqa: A002 - the query name
) -> StreamingResponse:
    """The whole session as JSONL, Markdown or its archived raw lines, read page by page."""
    store: Store = request.app.state.store
    with store.reading() as db:
        if db.get(Session, session_id) is None:
            raise HTTPException(status_code=404, detail="no such session")

    def body() -> Iterator[bytes]:
        with store.reading() as db:
            session = db.get(Session, session_id)
            if session is None:
                return
            for chunk in render(db, session, format):
                yield chunk.encode("utf-8")

    name = f"agdash-session-{session_id}{FORMATS[format]}"
    return StreamingResponse(
        body(),
        media_type=MEDIA_TYPES[format],
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )
