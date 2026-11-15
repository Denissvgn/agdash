"""Opaque paging cursors: where the next page starts, as a URL-safe token."""

import base64
import binascii
import json
from typing import Any

from fastapi import HTTPException


def encode(position: dict[str, Any]) -> str:
    raw = json.dumps(position, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode(cursor: str | None, *keys: str) -> dict[str, Any] | None:
    """The position in ``cursor``, which must hold ``keys``. A bad cursor is a 400."""
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        position = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="invalid cursor") from None
    if not isinstance(position, dict) or any(key not in position for key in keys):
        raise HTTPException(status_code=400, detail="invalid cursor")
    return position
