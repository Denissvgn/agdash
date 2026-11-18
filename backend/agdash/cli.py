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
    agdash archive      what is archived, and take encoded data out of it
    agdash compact      give room freed inside the store back to the disk
    agdash usage        what the running service itself uses: CPU, memory and disk
    agdash token        the local API token, for the desktop app, through a pipe only
    agdash open         a link that opens the UI in a browser, for a host with no app

Nothing here starts, stops or changes an agent. No output holds a secret except
``agdash token``, which the desktop app reads through a pipe and which refuses to
print to a terminal, and ``agdash open``, whose link carries the token so a browser
can use the UI; it says so when it prints it.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import create_engine, func, select

from agdash.adapters.store import RECENT_FORMATS
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


def _serves_ui(timeout: float = 2.0) -> bool:
    settings = get_settings()
    try:
        with urllib.request.urlopen(  # noqa: S310 - our own loopback service
            f"http://{settings.host}:{settings.port}/", timeout=timeout
        ) as response:
            return response.status == 200 and "text/html" in response.headers.get(
                "content-type", ""
            )
    except (urllib.error.URLError, OSError, ValueError):
        return False


def open_ui(host: Host, out=print, err=None) -> int:
    """Print the link that opens the UI with the local token."""
    err = err or (lambda text: print(text, file=sys.stderr))
    settings = get_settings()
    if _health() is None:
        err(
            f"The service is not answering on http://{settings.host}:{settings.port}."
            " Start it with `agdash serve`, or `agdash install` to start it at login."
        )
        return 1
    if not _serves_ui():
        err(
            "The service answers but serves no UI: this build has none. Use the desktop app,"
            " or set AGDASH_UI_DIR to a built UI and restart the service."
        )
        return 1
    from agdash.api.security import load_or_create_token
    from agdash.api.ui import open_link

    link = open_link(settings.port, load_or_create_token(host.secret_store()))
    out(link)
    out("")
    out("This link holds the service's local token: anyone who opens it can read your sessions.")
    out("The token stays in the part after #, which the browser keeps to itself.")
    out("On a host with no display, open it on your own computer through an SSH tunnel:")
    out(f"    ssh -N -L {settings.port}:127.0.0.1:{settings.port} <this host>")
    return 0


UNITS = ("bytes", "kB", "MB", "GB", "TB")


def _size(amount: float | None, per_second: bool = False) -> str:
    """A byte count as people read it, in steps of a thousand as the UI counts them."""
    if amount is None:
        return "not reported"
    unit = 0
    while amount >= 1000 and unit < len(UNITS) - 1:
        amount, unit = amount / 1000, unit + 1
    shown = f"{amount:.0f}" if unit == 0 or amount >= 100 else f"{amount:.1f}"
    return f"{shown} {UNITS[unit]}" + ("/s" if per_second else "")


def _percent(value: float | None) -> str:
    return "not reported" if value is None else f"{value:.1f}% of one core"


def _service_usage(token: str, timeout: float = 5.0) -> dict | None:
    settings = get_settings()
    request = urllib.request.Request(  # noqa: S310 - our own loopback service
        f"http://{settings.host}:{settings.port}{settings.api_prefix}/service/usage?range=hour",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None


def usage(host: Host, out=print, err=None) -> int:
    """What the running service itself uses now, and over its last hour."""
    err = err or (lambda text: print(text, file=sys.stderr))
    settings = get_settings()
    if _health() is None:
        err(
            f"The service is not answering on http://{settings.host}:{settings.port}."
            " Start it with `agdash serve`, or `agdash install` to start it at login."
        )
        return 1
    from agdash.api.security import load_or_create_token

    report = _service_usage(load_or_create_token(host.secret_store()))
    if report is None:
        err("The service answers but gave no usage: it may be an earlier version.")
        return 1
    now = report.get("now")
    if now is None:
        out("Nothing measured yet: the service takes its first sample ten seconds after it starts.")
        return 0
    out(
        f"Measured: {now['at']}"
        + (f", sampling since {report['since']}" if report.get("since") else "")
    )
    out(f"CPU:      {_percent(now['cpu_percent'])}")
    out(f"Memory:   {_size(now['footprint_bytes'])} (resident {_size(now['resident_bytes'])})")
    out(
        f"Storage:  read {_size(now['read_bytes_per_second'], per_second=True)},"
        f" written {_size(now['written_bytes_per_second'], per_second=True)}"
    )
    out(f"Store:    {_size(now['store_bytes'])}, {_size(now['free_bytes'])} free on its volume")
    parts = report.get("parts") or []
    if parts:
        minutes = round(report["measured_seconds"] / 60)
        out(f"CPU by part over the last {minutes} minutes measured:")
        for part in parts:
            out(f"  {part['part']:<10} {_percent(part['cpu_percent'])}")
    return 0


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


def _day(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d")


def format_checks(url: str, now: datetime | None = None) -> list[Check]:
    """Per harness: the format version last seen, and whether its records are still understood."""
    now = now or datetime.now(UTC)
    try:
        engine = create_engine(sync_database_url(url))
        with engine.connect() as connection:
            rows = connection.execute(
                select(
                    SourceHealth.adapter,
                    SourceHealth.format_version,
                    SourceHealth.unknown_record_count,
                    SourceHealth.parse_error_count,
                    SourceHealth.first_seen_at,
                    SourceHealth.last_seen_at,
                ).order_by(SourceHealth.adapter, SourceHealth.last_seen_at)
            ).all()
        engine.dispose()
    except Exception:
        return []
    checks = []
    for adapter in sorted({row.adapter for row in rows}):
        mine = [row for row in rows if row.adapter == adapter]
        known = [row for row in mine if row.format_version != "unknown"]
        latest = max(known, key=lambda row: _aware(row.last_seen_at)) if known else None
        seen = (
            f"version {latest.format_version}, last seen {_day(_aware(latest.last_seen_at))}"
            if latest
            else "version not recorded"
        )
        troubled = [row for row in mine if row.unknown_record_count or row.parse_error_count]
        recent = [row for row in troubled if now - _aware(row.last_seen_at) <= RECENT_FORMATS]
        if recent:
            found = "; ".join(
                f"{row.unknown_record_count} unknown records and {row.parse_error_count}"
                f" unreadable lines in "
                + (
                    f"version {row.format_version}"
                    if row.format_version != "unknown"
                    else "records with no version"
                )
                + f" since {_day(_aware(row.first_seen_at))}"
                for row in recent
            )
            checks.append(
                Check(
                    f"{adapter} formats",
                    "warn",
                    f"{found}; {adapter} may have changed its format, so some of its records"
                    " are not shown",
                )
            )
            continue
        older = sum(row.unknown_record_count + row.parse_error_count for row in troubled)
        detail = f"{seen}; everything read recently was recognised"
        if older:
            detail += f" ({older} records from older versions were not)"
        checks.append(Check(f"{adapter} formats", "ok", detail))
    return checks


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


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
        checks.extend(format_checks(url))
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
        if name == "compact":
            from agdash.compact import main as compact

            return compact(argv, answering=lambda: _health() is not None)
        if name == "archive":
            from agdash.archive import main as archive

            return archive(argv)
        from agdash.retention import main as retention

        return retention(argv)

    return run


def main(argv: Sequence[str] | None = None, *, host: Host | None = None, manager=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("connect", "export", "retention", "archive", "compact"):
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
        ("token", "the local API token, for the desktop app (pipe only)"),
        ("open", "a link that opens the UI in a browser"),
        ("usage", "what the running service itself uses"),
    ):
        commands.add_parser(name, help=text)
    for name in ("connect", "export", "retention", "archive", "compact"):
        commands.add_parser(name, help=f"see `agdash {name} --help`", add_help=False)
    args = parser.parse_args(argv)
    host = host or current_host()

    if args.command == "serve":
        from agdash.runtime import serve

        return serve()
    if args.command == "token":
        if sys.stdout.isatty():
            print(
                "agdash token is for the desktop app, which reads it through a pipe;"
                " it does not print the token to a terminal.",
                file=sys.stderr,
            )
            return 2
        from agdash.api.security import load_or_create_token

        sys.stdout.write(load_or_create_token(host.secret_store()))
        sys.stdout.flush()
        return 0
    if args.command == "open":
        return open_ui(host)
    if args.command == "usage":
        return usage(host)
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
