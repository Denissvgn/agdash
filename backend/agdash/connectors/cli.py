"""Connect agdash to a WorkChord server from the command line.

    python -m agdash.connectors.cli pair https://work.example [--name Team]
    python -m agdash.connectors.cli list
    python -m agdash.connectors.cli status ID
    python -m agdash.connectors.cli sync ID          (refresh the local copy once)
    python -m agdash.connectors.cli run-key ID        (reads the key without echoing it)
    python -m agdash.connectors.cli forget ID
    python -m agdash.connectors.cli rules ID          (rules and pins)
    python -m agdash.connectors.cli rule ID --folder PATH --project N
    python -m agdash.connectors.cli rule ID --launcher 'codex *' --runtime-ref REF
    python -m agdash.connectors.cli pin ID SESSION --project N | --actor N
    python -m agdash.connectors.cli unrule ID RULE
    python -m agdash.connectors.cli publish ID on|off (session links on WorkChord tasks)

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
from agdash.connectors.links import Linker
from agdash.connectors.mirror import object_states, sync_connection
from agdash.connectors.publish import Publisher, PublishError
from agdash.connectors.publish import disable as disable_publish
from agdash.connectors.publish import enable as enable_publish
from agdash.connectors.resolver import Resolver
from agdash.connectors.workchord import (
    KIND,
    PairingError,
    PendingPairing,
    WorkChordConnector,
    pair,
    set_run_key,
    webhook_ref,
)
from agdash.db.migrate import sync_database_url, upgrade
from agdash.db.models import Connection, MappingRule, Session
from agdash.platform import current_host
from agdash.platform.secrets import SecretStore
from agdash.vocab import RuleMatchKind, RuleTargetKind


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


def _rules(args, db: DbSession, row: Connection) -> int:
    if args.command == "rules":
        for rule in db.scalars(
            select(MappingRule)
            .where(MappingRule.connection_id == row.id)
            .order_by(MappingRule.priority.desc(), MappingRule.id)
        ):
            what = (
                f"session {rule.session_id}"
                if rule.match_kind is RuleMatchKind.SESSION
                else f"{rule.match_kind.value} {rule.match_value}"
            )
            only = f" ({rule.harness} only)" if rule.harness else ""
            off = "" if rule.enabled else " [off]"
            print(f"{rule.id}\t{what}{only} -> {rule.target_kind.value} {rule.target_value}{off}")
        return 0
    if args.command == "unrule":
        found = db.get(MappingRule, args.rule)
        if found is None or found.connection_id != row.id:
            print(f"No rule {args.rule} on connection {row.id}.", file=sys.stderr)
            return 1
        db.delete(found)
        db.commit()
        print(f"Rule {args.rule} removed.")
        return 0
    if args.command == "pin":
        if db.get(Session, args.session) is None:
            print(f"No session {args.session}.", file=sys.stderr)
            return 1
        kind = RuleTargetKind.PROJECT if args.project is not None else RuleTargetKind.ACTOR
        for old in db.scalars(
            select(MappingRule).where(
                MappingRule.connection_id == row.id,
                MappingRule.match_kind == RuleMatchKind.SESSION,
                MappingRule.session_id == args.session,
                MappingRule.target_kind == kind,
            )
        ):
            db.delete(old)
        target = args.project if args.project is not None else args.actor
        db.add(
            MappingRule(
                connection_id=row.id,
                match_kind=RuleMatchKind.SESSION,
                session_id=args.session,
                target_kind=kind,
                target_value=str(target),
            )
        )
        db.commit()
        print(f"Session {args.session} pinned to {kind.value} {target}.")
        return 0
    if args.folder is not None and not (
        args.folder.startswith(("/", "\\")) or args.folder[1:3] in (":\\", ":/")
    ):
        print("Give the folder as an absolute path.", file=sys.stderr)
        return 1
    kind, value = next(
        (kind, value)
        for kind, value in (
            (RuleMatchKind.FOLDER, args.folder),
            (RuleMatchKind.REPOSITORY, args.repository),
            (RuleMatchKind.LAUNCHER, args.launcher),
        )
        if value is not None
    )
    target_kind = RuleTargetKind.PROJECT if args.project is not None else RuleTargetKind.RUNTIME_REF
    target = str(args.project) if args.project is not None else args.runtime_ref
    made = MappingRule(
        connection_id=row.id,
        harness=args.harness,
        match_kind=kind,
        match_value=value,
        target_kind=target_kind,
        target_value=target,
        priority=args.priority,
        note=args.note,
    )
    db.add(made)
    db.commit()
    print(f"Rule {made.id}: {kind.value} {value} -> {target_kind.value} {target}.")
    return 0


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
        ("webhook-secret", "store or clear the secret WorkChord signs webhooks with"),
        ("forget", "remove a connection and its stored credentials"),
        ("rules", "the local mapping rules and pins of a connection"),
    ):
        commands.add_parser(command, help=text).add_argument("id", type=int)
    rule = commands.add_parser("rule", help="map sessions by folder, repository or launcher")
    rule.add_argument("id", type=int)
    match = rule.add_mutually_exclusive_group(required=True)
    match.add_argument("--folder", help="an absolute folder; sessions in it or below match")
    match.add_argument("--repository", help="a repository's remote address")
    match.add_argument(
        "--launcher", help="a pattern for the agent's command line, such as 'codex *'"
    )
    target = rule.add_mutually_exclusive_group(required=True)
    target.add_argument("--project", type=int, help="the WorkChord project id")
    target.add_argument("--runtime-ref", help="a team member's runtime reference")
    rule.add_argument("--harness", help="only sessions of this harness")
    rule.add_argument("--priority", type=int, default=0)
    rule.add_argument("--note")
    pin = commands.add_parser("pin", help="pin one session to a project or an actor")
    pin.add_argument("id", type=int)
    pin.add_argument("session", type=int)
    pinned = pin.add_mutually_exclusive_group(required=True)
    pinned.add_argument("--project", type=int)
    pinned.add_argument("--actor", type=int)
    publish = commands.add_parser(
        "publish", help="add a link to each observed session on its WorkChord task (off by default)"
    )
    publish.add_argument("id", type=int)
    publish.add_argument("switch", choices=("on", "off"))
    unrule = commands.add_parser("unrule", help="remove a rule or a pin")
    unrule.add_argument("id", type=int)
    unrule.add_argument("rule", type=int)
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
        access = (row.contract_seen or {}).get("session_access")
        how = (
            "as an open local workspace"
            if not row.credential_ref
            else (
                "with a read-only session"
                if access == "observe"
                else "with your full session (this server has no read-only sessions)"
            )
        )
        print(f"Connected to {row.base_url} {how} (connection {row.id}).")
        return 0
    if args.command == "list":
        for row in db.scalars(
            select(Connection).where(Connection.kind == KIND).order_by(Connection.id)
        ):
            access = (row.contract_seen or {}).get("session_access")
            how = (
                ("read-only session" if access == "observe" else "full session")
                if row.credential_ref
                else "open"
            )
            runs = ", agent key" if row.run_credential_ref else ""
            publishing = ", publish on" if row.publish_enabled else ""
            print(f"{row.id}\t{row.base_url}\t{how}{runs}{publishing}\t{row.name or ''}")
        return 0
    row = _connection(db, args.id)
    if args.command == "status":
        access = connector.access(db, secrets, row)
        state = "works" if access.authenticated and not access.problem else "does not work"
        print(f"Connection {row.id} to {row.base_url} {state}.")
        for label, value in (
            ("signed in as", access.principal),
            (
                "session",
                {"observe": "read-only", "full": "full rights"}.get(access.session_access or ""),
            ),
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
        linked = Linker().run(db, [row])
        Resolver().run(db, [row])
        db.commit()
        published = Publisher(transport).run(db, secrets, [row])
        db.commit()
        if published.sent or published.refused:
            print(f"Session links: {published.sent} added, {published.refused} refused.")
        for why in published.skipped.values():
            print(f"Publish mode: {why}.", file=sys.stderr)
        if linked.added:
            added = ", ".join(f"{n} {basis}" for basis, n in sorted(linked.added.items()))
            print(f"New session links: {added}.")
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
    if args.command == "webhook-secret":
        secret = (
            getpass.getpass("Webhook signing secret, as set in WorkChord (empty to clear): ")
            if sys.stdin.isatty()
            else sys.stdin.readline()
        ).strip()
        if secret:
            secrets.set(webhook_ref(row.id), secret)
            print(
                f"Webhook secret stored. WorkChord's target: /workchord/{row.id} on the receiver."
            )
        else:
            secrets.delete(webhook_ref(row.id))
            print("Webhook secret cleared; deliveries for this connection are refused.")
        return 0
    if args.command == "publish":
        if args.switch == "off":
            disable_publish(db, secrets, row)
            db.commit()
            print(f"Publish mode is off for connection {row.id}.")
            return 0
        try:
            enable_publish(db, secrets, row, show=_show, transport=transport)
        except (PublishError, PairingError) as error:
            db.rollback()
            print(f"Publish mode stays off: {error}", file=sys.stderr)
            return 1
        db.commit()
        print(
            f"Publish mode is on for connection {row.id}: each session with an exact task "
            "link gets one link on that task."
        )
        return 0
    if args.command in ("rules", "rule", "pin", "unrule"):
        return _rules(args, db, row)
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
