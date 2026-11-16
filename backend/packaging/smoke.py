"""Smoke test of a frozen agdash binary: it starts, answers /health, and stops cleanly.

    python packaging/smoke.py dist/agdash

It runs with a fresh, empty home folder, so the service finds no harness data and
creates its store there; only /health is asked, which needs no token, so no
keychain is touched. Exit status 0 means every check passed.
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

START_SECONDS = 120  # a one-file build unpacks itself before it starts
STOP_SECONDS = 30


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def run(binary: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(binary), *args], env=env, capture_output=True, text=True, timeout=180
    )


def main() -> int:
    binary = Path(sys.argv[1]).resolve()
    home = Path(tempfile.mkdtemp(prefix="agdash-smoke-"))
    port = free_port()
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "AGDASH_PORT": str(port),
        "TMPDIR": os.environ.get("TMPDIR", tempfile.gettempdir()),
    }
    print(f"binary {binary} ({binary.stat().st_size / 1e6:.1f} MB), home {home}, port {port}")

    shown = run(binary, "--help", env=env)
    assert shown.returncode == 0 and "serve" in shown.stdout, shown.stderr[-2000:]
    print("ok   --help lists the commands")

    started = time.monotonic()
    service = subprocess.Popen(
        [str(binary), "serve"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        health = None
        while time.monotonic() - started < START_SECONDS:
            if service.poll() is not None:
                output = service.stdout.read()
                raise AssertionError(f"serve exited with {service.returncode}:\n{output}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as answer:
                    health = json.loads(answer.read())
                    break
            except (urllib.error.URLError, OSError, ValueError):
                time.sleep(0.5)
        assert health and health.get("status") == "ok", f"no health answer in {START_SECONDS}s"
        print(f"ok   /health answered in {time.monotonic() - started:.1f}s: {health}")
    finally:
        service.send_signal(signal.SIGTERM)
        try:
            code = service.wait(timeout=STOP_SECONDS)
        except subprocess.TimeoutExpired:
            service.kill()
            raise AssertionError("serve did not stop on SIGTERM") from None
        output = service.stdout.read()
    assert code == 0, f"serve stopped with status {code}:\n{output[-4000:]}"
    print("ok   serve stopped cleanly on SIGTERM")

    status = run(binary, "status", env=env)
    assert status.returncode == 0 and "Store:" in status.stdout, status.stdout + status.stderr
    assert "not created" not in status.stdout, status.stdout
    print("ok   status reads the store the service created")
    shutil.rmtree(home, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
