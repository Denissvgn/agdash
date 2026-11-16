"""Connect agdash to a WorkChord server from the command line.

    python -m agdash.connectors.cli pair https://work.example [--name Team]
    python -m agdash.connectors.cli list
    python -m agdash.connectors.cli status ID
    python -m agdash.connectors.cli sync ID          (refresh the local copy once)
    python -m agdash.connectors.cli run-key ID        (reads the key without echoing it)
    python -m agdash.connectors.cli forget ID

Secrets are never taken from the command line, where other processes can see
them, and never printed.
"""

import argparse
import getpass
import sys
from collections.abc import Sequence

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from agdash.config import get_settings
from agdash.connectors.mirror import object_states, sync_connection
from agdash.connectors.workchord import (
    KIND,
    PairingError,
    PendingPairing,
    WorkChordConnector,
    pair,
    set_run_key,
)
from agdash.db.migrate import sync_database_url, upgrade
from agdash.db.models import Connection
from agdash.platform import current_host
from agdash.platform.secrets import SecretStore


def _show(pending: PendingPairing) -> None:
    print("Open this page, sign in, check the code and approve the connection:")
    print(f"  {pending.verification_url}")
    print(f"  code: {pending.verification_code}")
    print(f"Waiting until {pending.expires_at:%H:%M:%S} UTC…", flush=True)


def _print_objects(db: DbSession, connection_id: int) -> None:
    for item in object_states(db, connection_id):
        rows = f", {item.rows} rows" if item.rows is not None else ""
        detail = f": {item.detail}" if item.detail else ""
        print(f"  {item.kind.value}: {item.state.value}{rows}{detail}")


def _connection(db: DbSession, connection_id: int) -> Connection:
    row = db.get(Connection, connection_id)
    if row is None or row.kind != KIND:
        raise SystemExit(f"no WorkChord connection {connection_id}")
    return row


def run(argv: Sequence[str], db: DbSession, secrets: SecretStore, transport=None) -> int:
    parser = argparse.ArgumentParser(prog="agdash-connect", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    pairing = commands.add_parser("pair", help="connect to a WorkChord server")
    pairing.add_argument("address")
    pairing.add_argument("--name")
    commands.add_parser("list", help="the connected servers")
    for command, text in (
        ("status", "whether a connection still works"),
        ("sync", "refresh the local copy of the server's objects once"),
        ("run-key", "store or clear an agent key with planning:read"),
        ("forget", "remove a connection and its stored credentials"),
    ):
        commands.add_parser(command, help=text).add_argument("id", type=int)
    args = parser.parse_args(argv)
    connector = WorkChordConnector(transport)

    if args.command == "pair":
        try:
            row = pair(db, secrets, args.address, name=args.name, show=_show, transport=transport)
        except (PairingError, ValueError) as error:
            db.rollback()
            print(f"Not connected: {error}", file=sys.stderr)
            return 1
        db.commit()
        how = "with your session" if row.credential_ref else "as an open local workspace"
        print(f"Connected to {row.base_url} {how} (connection {row.id}).")
        return 0
    if args.command == "list":
        for row in db.scalars(
            select(Connection).where(Connection.kind == KIND).order_by(Connection.id)
        ):
            how = "session" if row.credential_ref else "open"
            runs = ", agent key" if row.run_credential_ref else ""
            print(f"{row.id}\t{row.base_url}\t{how}{runs}\t{row.name or ''}")
        return 0
    row = _connection(db, args.id)
    if args.command == "status":
        access = connector.access(db, secrets, row)
        state = "works" if access.authenticated and not access.problem else "does not work"
        print(f"Connection {row.id} to {row.base_url} {state}.")
        for label, value in (
            ("signed in as", access.principal),
            ("workspace role", access.workspace_role),
            ("projects", access.projects),
            ("session expires", access.expires_at),
            ("agent key", "stored" if access.run_traces else "none"),
            ("problem", access.problem),
        ):
            if value is not None:
                print(f"  {label}: {value}")
        for note in access.notes:
            print(f"  {note}")
        _print_objects(db, row.id)
        return 0 if state == "works" else 1
    if args.command == "sync":
        report = sync_connection(db, secrets, row, transport=transport)
        db.commit()
        if not report.reachable:
            print(f"Not synced: {report.problem}", file=sys.stderr)
            return 1
        print(f"Synced connection {row.id} from {row.base_url}.")
        _print_objects(db, row.id)
        return 0
    if args.command == "run-key":
        key = (
            getpass.getpass("Agent key with planning:read (empty to clear): ")
            if sys.stdin.isatty()
            else sys.stdin.readline()
        ).strip()
        set_run_key(db, secrets, row, key or None)
        db.commit()
        print("Agent key stored." if key else "Agent key cleared.")
        return 0
    connector.forget(db, secrets, row)
    db.commit()
    print(f"Connection {args.id} forgotten.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    host = current_host()
    url = get_settings().database_url
    if not url:
        host.default_database_path().parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{host.default_database_path()}"
    upgrade(url)
    engine = create_engine(sync_database_url(url))
    try:
        with sessionmaker(engine)() as db:
            return run(sys.argv[1:] if argv is None else argv, db, host.secret_store())
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
