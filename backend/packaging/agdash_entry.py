"""Entry point of the frozen agdash binary: the same command as ``agdash``."""

from agdash.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
