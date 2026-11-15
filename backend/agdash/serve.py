"""Run the agdash service: ``python -m agdash.serve`` is ``agdash serve``."""

from agdash.runtime import serve

if __name__ == "__main__":
    raise SystemExit(serve())
