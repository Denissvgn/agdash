"""Run the agdash service on this host's loopback interface.

    python -m agdash.serve

The address and port come from ``AGDASH_HOST`` and ``AGDASH_PORT``. Any
address that is not loopback is refused before anything listens. Access logs
are off: request lines can carry search text from private transcripts.
"""

import sys

import uvicorn

from agdash.api.security import NotLoopback, ensure_loopback
from agdash.config import get_settings
from agdash.main import create_app


def main() -> int:
    settings = get_settings()
    try:
        ensure_loopback(settings.host)
    except NotLoopback as error:
        print(f"agdash: refusing to start: {error}", file=sys.stderr)
        return 2
    uvicorn.run(
        create_app(port=settings.port),
        host=settings.host,
        port=settings.port,
        access_log=False,
        server_header=False,
        proxy_headers=False,
        timeout_graceful_shutdown=2,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
