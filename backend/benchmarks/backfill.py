"""Backfill benchmark: time and peak memory of reading Codex rollouts of growing size.

    ../.venv/bin/python benchmarks/backfill.py synthetic [--sizes 5,20,80]
    ../.venv/bin/python benchmarks/backfill.py measure HOME_DIR

``synthetic`` builds rollouts from the synthetic Codex fixture: its header, then
its records repeated with fresh ids and offsets until the file reaches each size
(in MB), once as a plain rollout and once as a compressed archive. Each size is
backfilled in a fresh process, through the same collector the service runs, so
the peak resident memory of one size is not carried into the next.

``measure`` backfills whatever Codex files lie under HOME_DIR/.codex and prints
the time and peak memory. Point it at a copy, or at a real home, to measure a
real archive: only counts, sizes, time and memory are printed, never content.
"""

import argparse
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from agdash.ingest.compressed import compress
from agdash.platform import OperatingSystem, current_host

FIXTURE = sorted(
    (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "harness" / "codex").rglob(
        "rollout-*.jsonl"
    )
)[0]


def peak_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # The kernel reports bytes on macOS and kilobytes on Linux.
    return peak if current_host().os is OperatingSystem.MACOS else peak * 1024


def build(home: Path, megabytes: int, archived: bool) -> Path:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    header, body = lines[0], lines[1:]
    native = str(uuid.UUID(int=megabytes * 2 + archived))
    meta = json.loads(header)
    meta["payload"]["id"] = native
    out = [json.dumps(meta)]
    size, target, round_ = len(out[0]), megabytes * 1_000_000, 0
    while size < target:
        round_ += 1
        for line in body:
            record = json.loads(line)
            record.setdefault("payload", {})
            if isinstance(record["payload"], dict) and "call_id" in record["payload"]:
                record["payload"]["call_id"] = f"{record['payload']['call_id']}-{round_}"
            text = json.dumps(record)
            out.append(text)
            size += len(text) + 1
            if size >= target:
                break
    data = ("\n".join(out) + "\n").encode("utf-8")
    name = f"rollout-2026-11-17T00-00-00-{native}.jsonl"
    if archived:
        path = home / ".codex" / "archived_sessions" / f"{name}.zst"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(compress(data))
    else:
        path = home / ".codex" / "sessions" / "2026" / "11" / "17" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return path


def measure(home: Path) -> dict:
    """Backfill HOME's Codex files into a fresh store under HOME; time and peak memory."""
    os.environ["HOME"] = str(home)
    os.environ["AGDASH_DATABASE_URL"] = f"sqlite:///{home / 'agdash.db'}"
    from agdash.adapters import codex as adapter
    from agdash.runtime import collector_for, prepare

    codex = home / ".codex"
    files = sorted({p for pattern in adapter.PATTERNS for p in codex.glob(pattern) if p.is_file()})
    host = current_host()
    store = prepare(host)
    collector, engine = collector_for(store, host)
    started = time.monotonic()
    try:
        totals = collector.backfill()
    finally:
        engine.dispose()
    return {
        "files": len(files),
        "bytes_on_disk": sum(p.stat().st_size for p in files),
        "lines": totals.lines,
        "seconds": round(time.monotonic() - started, 2),
        "peak_mb": round(peak_bytes() / 1e6, 1),
        "store_mb": round((home / "agdash.db").stat().st_size / 1e6, 1),
    }


def synthetic(sizes: list[int]) -> None:
    print(f"{'kind':10} {'MB':>5} {'lines':>8} {'seconds':>8} {'MB/s':>6} {'peak MB':>8}")
    for megabytes in sizes:
        for archived in (False, True):
            home = Path(tempfile.mkdtemp(prefix="agdash-bench-"))
            try:
                build(home, megabytes, archived)
                result = json.loads(
                    subprocess.run(
                        [sys.executable, __file__, "measure", str(home), "--json"],
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout
                )
            finally:
                shutil.rmtree(home, ignore_errors=True)
            rate = megabytes / result["seconds"] if result["seconds"] else 0
            kind = "archived" if archived else "plain"
            print(
                f"{kind:10} {megabytes:>5} {result['lines']:>8} {result['seconds']:>8}"
                f" {rate:>6.1f} {result['peak_mb']:>8}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    built = commands.add_parser("synthetic")
    built.add_argument("--sizes", default="5,20,80")
    real = commands.add_parser("measure")
    real.add_argument("home", type=Path)
    real.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.command == "synthetic":
        synthetic([int(size) for size in args.sizes.split(",")])
        return 0
    result = measure(args.home.resolve())
    print(json.dumps(result) if args.json else "\n".join(f"{k}: {v}" for k, v in result.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
