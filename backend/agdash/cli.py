"""The ``agdash`` command.

    agdash serve        run the service here: collect and answer the app
    agdash backfill     read everything the harnesses have written, once
    agdash status       whether the service is installed and running, and what it holds
    agdash doctor       check this host's setup and say what to fix
    agdash install      start the service at login (LaunchAgent or systemd user unit)
    agdash uninstall    stop starting it at login; your data stays where it is
    agdash connect ...  connect a WorkChord server (see agdash connect --help)
    agdash export ...   export a session to a file you name
    agdash retention    see or apply the retention setting

Nothing here starts, stops or changes an agent, and no output holds a secret.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import create_engine, func, select

from agdash.api.security import NotLoopback, ensure_loopback
from agdash.config import database_url, get_settings
from agdash.db.migrate import sync_database_url
from agdash.db.models import Event, Session, SourceHealth
from agdash.platform import Host, current_host
from agdash.platform.service import ServiceError, ServiceManager, ServiceState

# Settings the installed service should run with, when they are set here.
PASSED_SETTINGS = (
    "AGDASH_HOST",
    "AGDASH_PORT",
    "AGDASH_DATABASE_URL",
    "AGDASH_RETENTION_DAYS",
    "AGDASH_KEEP_RAW",
    "AGDASH_EXTRA_ORIGINS",
)


def service_program() -> tuple[str, ...]:
    """How the service manager starts agdash: the frozen binary itself, or this Python."""
    if getattr(sys, "frozen", False):
        return (sys.executable, "serve")
    return (sys.executable, "-m", "agdash.cli", "serve")


def _manager(host: Host) -> ServiceManager:
    return host.service_manager()


# -- status and doctor ----------------------------------------------------------------


def _health(timeout: float = 2.0) -> dict | None:
    settings = get_settings()
    try:
        with urllib.request.urlopen(  # noqa: S310 - our own loopback service
            f"http://{settings.host}:{settings.port}/health", timeout=timeout
        ) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _counts(url: str) -> dict[str, int] | None:
    try:
        engine = create_engine(sync_database_url(url))
        with engine.connect() as connection:
            sessions = connection.execute(select(func.count()).select_from(Session)).scalar_one()
            events = connection.execute(select(func.count()).select_from(Event)).scalar_one()
            health = connection.execute(
                select(
                    func.coalesce(func.sum(SourceHealth.unknown_record_count), 0),
                    func.coalesce(func.sum(SourceHealth.parse_error_count), 0),
                )
            ).one()
        engine.dispose()
    except Exception:
        return None
    return {"sessions": sessions, "events": events, "unknown": health[0], "errors": health[1]}


def status(host: Host, manager: ServiceManager, out=print) -> int:
    url = database_url()
    try:
        state = manager.state()
    except ServiceError as error:
        out(f"Service: could not ask the service manager ({error})")
        state = None
    labels = {
        ServiceState.NOT_INSTALLED: "not installed (run `agdash install` to start it at login)",
        ServiceState.INSTALLED: "installed, not running",
        ServiceState.RUNNING: "installed and running",
    }
    if state is not None:
        out(f"Service: {labels[state]}")
        out(f"  definition: {manager.unit_path}")
    health = _health()
    settings = get_settings()
    out(
        f"Answering: {'yes' if health else 'no'} on http://{settings.host}:{settings.port}"
        + (f" (version {health.get('version')})" if health else "")
    )
    counts = _counts(url)
    out(f"Data: {host.data_dir()}")
    if counts is None:
        out("Store: not created yet (run `agdash backfill` or start the service)")
    else:
        out(f"Store: {counts['sessions']} sessions, {counts['events']} events")
    return 0


@dataclass
class Check:
    name: str
    level: str  # "ok", "warn" or "fail"
    detail: str


def doctor_checks(host: Host, manager: ServiceManager) -> list[Check]:
    from agdash.runtime import private, sqlite_file

    checks: list[Check] = []
    settings = get_settings()
    try:
        ensure_loopback(settings.host)
        checks.append(
            Check("Listening address", "ok", f"{settings.host}:{settings.port}, loopback only")
        )
    except NotLoopback as error:
        checks.append(Check("Listening address", "fail", str(error)))

    for harness, roots in host.harness_roots().items():
        found = [str(root) for root in roots if root.exists()]
        if found:
            checks.append(Check(f"{harness.value} files", "ok", ", ".join(found)))
        else:
            checks.append(Check(f"{harness.value} files", "warn", "not found on this host"))

    url = database_url()
    path = sqlite_file(url)
    counts = _counts(url)
    if counts is None:
        checks.append(Check("Store", "warn", "not created yet; `agdash backfill` creates it"))
    else:
        checks.append(
            Check("Store", "ok", f"{counts['sessions']} sessions, {counts['events']} events")
        )
        if counts["unknown"] or counts["errors"]:
            checks.append(
                Check(
                    "Harness formats",
                    "warn",
                    f"{counts['unknown']} unknown records and {counts['errors']} unreadable lines;"
                    " a harness may have changed its format",
                )
            )
    data_dir = host.data_dir()
    if data_dir.exists():
        checks.append(
            Check(
                "Data folder",
                "ok" if private(data_dir) else "fail",
                f"{data_dir} {'is private' if private(data_dir) else 'can be read by others'}",
            )
        )
    if path is not None and path.exists():
        checks.append(
            Check(
                "Store file",
                "ok" if private(path) else "fail",
                "readable by you only" if private(path) else f"{path} can be read by others",
            )
        )
    try:
        state = manager.state()
        level = "ok" if state is ServiceState.RUNNING else "warn"
        detail = {
            ServiceState.RUNNING: "starts at login and runs now",
            ServiceState.INSTALLED: "installed but not running",
            ServiceState.NOT_INSTALLED: "not installed; `agdash install` starts it at login",
        }[state]
        checks.append(Check("Service", level, detail))
    except ServiceError as error:
        checks.append(Check("Service", "warn", f"could not ask the service manager: {error}"))
    checks.append(
        Check(
            "Answering",
            "ok" if _health() else "warn",
            "the service answers on loopback"
            if _health()
            else "nothing answers on the service port",
        )
    )
    try:
        kind = host.secret_store().kind
        checks.append(Check("Secret store", "ok", f"{kind} (agdash's own entries only)"))
    except Exception as error:  # the message never holds a secret
        checks.append(Check("Secret store", "fail", type(error).__name__))
    return checks


def doctor(host: Host, manager: ServiceManager, out=print) -> int:
    marks = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}
    checks = doctor_checks(host, manager)
    for check in checks:
        out(f"[{marks[check.level]}] {check.name}: {check.detail}")
    return 1 if any(check.level == "fail" for check in checks) else 0


# -- install and uninstall ------------------------------------------------------------


def install(host: Host, manager: ServiceManager, out=print) -> int:
    environment = {name: os.environ[name] for name in PASSED_SETTINGS if os.environ.get(name)}
    definition = host.service_definition(service_program())
    definition = type(definition)(definition.program, definition.log_dir, environment)
    try:
        manager.install(definition)
    except ServiceError as error:
        out(f"Not installed: {error}")
        return 1
    out(f"Installed: the service starts at login and now. Definition: {manager.unit_path}")
    out(f"Logs: {definition.log_dir}")
    return 0


def uninstall(host: Host, manager: ServiceManager, out=print) -> int:
    try:
        manager.uninstall()
    except ServiceError as error:
        out(f"Not removed: {error}")
        return 1
    out(f"Removed the service definition ({manager.unit_path}); it no longer starts at login.")
    out(f"Your data stays in {host.data_dir()}.")
    return 0


# -- the command ----------------------------------------------------------------------


def _pass_through(name: str) -> Callable[[list[str]], int]:
    def run(argv: list[str]) -> int:
        if name == "connect":
            from agdash.connectors.cli import main as connect

            return connect(argv)
        if name == "export":
            from agdash.export import main as export

            return export(argv)
        from agdash.retention import main as retention

        return retention(argv)

    return run


def main(argv: Sequence[str] | None = None, *, host: Host | None = None, manager=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("connect", "export", "retention"):
        return _pass_through(argv[0])(argv[1:])
    parser = argparse.ArgumentParser(prog="agdash", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("serve", "run the service here"),
        ("backfill", "read everything the harnesses have written, once"),
        ("status", "whether the service runs, and what the store holds"),
        ("doctor", "check this host's setup"),
        ("install", "start the service at login"),
        ("uninstall", "stop starting the service at login; data stays"),
    ):
        commands.add_parser(name, help=text)
    for name in ("connect", "export", "retention"):
        commands.add_parser(name, help=f"see `agdash {name} --help`", add_help=False)
    args = parser.parse_args(argv)
    host = host or current_host()

    if args.command == "serve":
        from agdash.runtime import serve

        return serve()
    if args.command == "backfill":
        from agdash.runtime import collector_for, prepare

        store = prepare(host)
        collector, engine = collector_for(store, host)
        try:
            totals = collector.backfill()
        finally:
            engine.dispose()
        print(f"Read {totals.lines} new lines from {totals.files} files.")
        for harness, lines in sorted(totals.by_harness.items()):
            print(f"  {harness}: {lines} lines")
        if totals.summaries:
            print(f"  antigravity summaries: {totals.summaries} conversations")
        return 0
    manager = manager or _manager(host)
    handlers = {"status": status, "doctor": doctor, "install": install, "uninstall": uninstall}
    return handlers[args.command](host, manager)


if __name__ == "__main__":
    raise SystemExit(main())
