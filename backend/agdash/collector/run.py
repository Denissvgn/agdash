"""The collector: reads harness files and the process table, and keeps session states.

Two loops run side by side:

* **Ingest** reads new lines from every harness's files, through each adapter
  and the raw archive, when files change and on a slower full poll, and
  refreshes Antigravity's conversation summaries.
* **Probe** reads the process table every two seconds, links agent processes to
  the sessions they run (Claude Code through its registry, Codex and Pi through
  the files a process holds open), records them, and derives each session's
  state with its evidence.

A state is written only when it changes, so the live stream reports changes
rather than every pass, and each change of state is kept as a row of its own.
Everything here only reads harness data; writes go to agdash's own store.
"""

import logging
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from agdash.access import ReadOnlyAccess
from agdash.adapters import antigravity, claude_code, codex, pi
from agdash.archive import Archiving
from agdash.collector.auth import AuthDetector
from agdash.collector.processes import (
    ObservedProcess,
    ProcessTracker,
    Role,
    codex_rollouts,
    is_terminal_back_end,
    process_session_id,
    session_process_fields,
    stated_model,
    take_snapshot,
    terminal_clients,
)
from agdash.collector.state import (
    Basis,
    HarnessStatus,
    Observation,
    ProcessLiveness,
    StateRecord,
    advance,
    session_state_fields,
    turn_phase,
)
from agdash.db.models import Event, Session, SessionFile, SessionProcess, SessionStateChange
from agdash.ingest import (
    Ingestor,
    discover_files,
    discover_states,
    match_files,
    session_folders,
    signature_of,
    single_files,
)
from agdash.ingest.engine import ScanReport
from agdash.ingest.ledger import SqlLedger
from agdash.ingest.watch import run as watch
from agdash.platform import Host
from agdash.platform.probes import ProcessIdentity
from agdash.platform.roots import is_settings_file
from agdash.vocab import EventKind, Harness, SessionState, Surface

log = logging.getLogger("agdash.collector")

PROBE_SECONDS = 2.0
SUMMARY_SECONDS = 10.0
AUTH_SECONDS = 60.0
# Sessions looked at by the probe besides those with a live process: recently
# active ones, so they can move to idle or ended.
RECENT = timedelta(minutes=30)
CLAUDE_STATUS = {
    "busy": HarnessStatus.BUSY,
    "idle": HarnessStatus.IDLE,
    "waiting": HarnessStatus.WAITING,
}
TURN_ENDS = {"end_turn", "stop", "final_answer"}


@dataclass
class Source:
    """One harness's files: where they are, which match, and how they are stored."""

    harness: Harness
    roots: tuple[Path, ...]
    patterns: Sequence[str]
    writer: Callable[[], object]
    ingestor: Ingestor | None = None
    # Folders watched whole, for what the harness keeps beside its session files.
    beside: tuple[Path, ...] = ()


@dataclass(frozen=True)
class Reading:
    """How far the read of everything the harnesses had written at start-up has got.

    On a first start that is all their history; on a later one, mostly a check
    that each file is where it was left. Until it is finished the store misses
    sessions, so the UI says so rather than showing a short list as the whole.
    """

    done: int = 0
    total: int = 0
    finished: bool = False


@dataclass
class Totals:
    lines: int = 0
    files: int = 0
    refused: int = 0
    summaries: int = 0
    by_harness: dict[str, int] = field(default_factory=dict)


class Collector:
    def __init__(
        self,
        sessions: sessionmaker[DbSession],
        host: Host,
        *,
        snapshot_dir: Path,
        keep_raw: bool | Callable[[DbSession], bool] = True,
        keep_encoded: bool | Callable[[DbSession], bool] = False,
        probe=None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._host = host
        self._clock = clock
        # Folders only: a harness's single files (such as ~/.claude.json) hold settings and
        # account details, which the collector never reads.
        roots = {
            harness: tuple(root for root in found if not is_settings_file(root))
            for harness, found in host.harness_roots().items()
        }
        self._codex_roots = roots[Harness.CODEX]
        self._claude_roots = roots[Harness.CLAUDE_CODE]
        self._pi_roots = roots[Harness.PI]
        self._antigravity_bases = tuple(
            base for base in roots[Harness.ANTIGRAVITY] if base.name in antigravity.SURFACES
        )
        directories = tuple(root for harness_roots in roots.values() for root in harness_roots)
        self._access = ReadOnlyAccess(directories, snapshot_dir=snapshot_dir)
        sources = [
            Source(
                Harness.CLAUDE_CODE, self._claude_roots, claude_code.PATTERNS, claude_code.Writer
            ),
            Source(Harness.CODEX, self._codex_roots, codex.PATTERNS, codex.Writer),
            Source(Harness.PI, self._pi_roots, pi.PATTERNS, pi.Writer),
            Source(
                Harness.ANTIGRAVITY,
                self._antigravity_bases,
                antigravity.PATTERNS,
                antigravity.Writer,
                # Its summaries, read on every scan, sit beside the transcripts.
                beside=self._antigravity_bases,
            ),
        ]
        for source in sources:
            writer = Archiving(
                source.writer(),
                source.harness.value,
                enabled=keep_raw,
                keep_encoded=keep_encoded,
            )
            source.ingestor = Ingestor(
                self._access, SqlLedger(sessions, writer), harness=source.harness.value
            )
        self._sources = sources
        self._probe = probe or host.process_probe()
        self._tracker = ProcessTracker()
        self._records: dict[int, StateRecord] = {}
        self._ran_by: dict[ProcessIdentity, set[int]] = {}
        # Sessions with no file that a process stood for on the last pass and the store
        # did not hold: one Claude Code's registry named, or one known by its process
        # alone. One seen again on the next pass is stored; a single pass could catch
        # an id held only while the process starts up, a process about to name its
        # session, or a transcript the ingest pass is about to read.
        self._unread: dict[ProcessIdentity, str] = {}
        # The session standing in for each running process that names none of its own.
        self._stood_in: dict[ProcessIdentity, int] = {}
        self._harness_status: dict[str, tuple[HarnessStatus, datetime, bool]] = {}
        self._summaries_at = 0.0
        self._summaries_seen: dict[Path, tuple | None] = {}
        self._kinds_read = False
        self._lock = threading.Lock()
        # Its own access: unlike ingest, it may open the harnesses' single settings files,
        # and reads only the allowlisted fields of them.
        self._auth = AuthDetector(
            ReadOnlyAccess(host.all_harness_roots(), snapshot_dir=snapshot_dir),
            host.harness_roots(),
            clock=clock,
        )
        self._auth_at = 0.0
        self._reading: Reading | None = None
        self._reading_logged = time.monotonic()

    # -- ingest -----------------------------------------------------------------------

    def discover(self) -> list[Path]:
        found: list[Path] = []
        for source in self._sources:
            found += discover_files(self._access, source.roots, source.patterns)
        return found

    def discover_signed(self) -> dict[Path, object]:
        """Every session file with its signature, from the listing alone."""
        found: dict[Path, object] = {}
        for source in self._sources:
            found.update(discover_states(self._access, source.roots, source.patterns))
        return found

    def watched(self) -> list[Path]:
        """What to watch: where session files can be, and no more of a harness."""
        folders: set[Path] = set()
        for source in self._sources:
            folders.update(session_folders(self._access, source.roots, source.patterns))
            folders.update(single_files(self._access, source.roots, source.patterns))
            folders.update(base for base in source.beside if base.is_dir())
        return sorted(f for f in folders if not any(other in f.parents for other in folders))

    def matching(self, paths: Iterable[Path]) -> list[Path]:
        """The session files among ``paths``, found without listing any folder."""
        paths = list(paths)
        found: list[Path] = []
        for source in self._sources:
            found += match_files(self._access, source.roots, source.patterns, paths)
        return found

    def urgent_first(self, paths: list[Path]) -> list[Path]:
        """``paths`` in the order to read them first: what runs now, then newest first.

        The live board can only show a session once its file is read, so the
        sessions with a process now, and their subagents, go before all history.
        """
        try:
            snapshot = take_snapshot(self._probe.table(), self._probe, self._access.roots)
            registry = self._claude_registry()
            held = {f for process in snapshot.processes for f in process.harness_files}
            running = {
                registry[process.identity.pid].session_id
                for process in snapshot.processes
                if process.identity.pid in registry
            }
        except Exception:
            log.exception("could not tell which sessions run now; reading newest first")
            held, running = set(), set()

        def runs_now(path: Path) -> bool:
            return str(path) in held or path.stem in running or not running.isdisjoint(path.parts)

        def urgency(path: Path) -> tuple[bool, float]:
            try:
                found = self._access.stat(path)
            except OSError:
                return True, 0.0
            if runs_now(path):
                # Smallest first: one long transcript should not hold the others back.
                return False, float(found.st_size)
            return True, -found.st_mtime

        return sorted(paths, key=urgency)

    def reading(self) -> Reading | None:
        """How far the start-up read has got, or None when the loops do not run."""
        return self._reading

    def _read_so_far(self, done: int, total: int) -> None:
        finished = done >= total
        if self._reading is not None and self._reading.finished:
            return
        self._reading = Reading(done, total, finished)
        now = time.monotonic()
        if finished:
            log.info("start-up read done: %d files", total)
        elif now - self._reading_logged >= 30:
            self._reading_logged = now
            log.info("start-up read: %d of %d files", done, total)

    def scan(self, paths: Iterable[Path]) -> Totals:
        """Read new lines from ``paths``, each through its harness's adapter."""
        totals = Totals()
        paths = list(paths)
        for source in self._sources:
            mine = [p for p in paths if any(_inside(p, root) for root in source.roots)]
            if not mine:
                continue
            assert source.ingestor is not None
            report: ScanReport = source.ingestor.scan(mine)
            totals.lines += report.lines
            totals.files += len(report.files)
            totals.refused += len(report.refused)
            totals.by_harness[source.harness.value] = report.lines
        if time.monotonic() - self._summaries_at >= SUMMARY_SECONDS or not self._summaries_at:
            totals.summaries = self.refresh_summaries()
        if not self._kinds_read:
            self._kinds_read = True
            self.refresh_kinds()
        return totals

    def backfill(self) -> Totals:
        """Read everything there is now, once."""
        self._summaries_at = 0.0
        self._summaries_seen.clear()
        totals = self.scan(self.discover())
        self.refresh_auth()
        return totals

    def refresh_kinds(self) -> int:
        """The kind of Codex threads whose header was read before kinds were kept.

        Only a thread with a parent can be an agent's subagent or Codex's own review, and
        once its kind is stored it is not looked at again. What is left on later runs
        are threads whose file is gone or states no kind.
        """
        found = 0
        with self._sessions() as db:
            rows = db.execute(
                select(Session, SessionFile.path)
                .join(SessionFile, SessionFile.session_id == Session.id)
                .where(
                    Session.harness == Harness.CODEX.value,
                    Session.parent_session_id.is_not(None),
                    Session.kind.is_(None),
                )
            ).all()
            for session, path in rows:
                if session.kind is None:  # set already from another of its files
                    session.kind = codex.read_kind(self._access, path)
                    found += session.kind is not None
            db.commit()
        if found:
            log.info("noted the kind of %d Codex threads read earlier", found)
        return found

    def refresh_auth(self) -> int:
        """Auth method and account label for active sessions that have none yet."""
        self._auth_at = time.monotonic()
        with self._sessions() as db:
            changed = self._auth.refresh(db)
            db.commit()
        return changed

    def _summaries_state(self, base: Path) -> tuple | None:
        """The size and time of a base's summaries store and its write-ahead log, if readable."""
        found = []
        for name in (antigravity.SUMMARIES, f"{antigravity.SUMMARIES}-wal"):
            try:
                state = self._access.stat(base / name)
            except OSError:
                found.append(None)
            except Exception:  # outside the roots, or refused: read it the slow way
                return None
            else:
                found.append((state.st_size, state.st_mtime_ns))
        return tuple(found)

    def refresh_summaries(self) -> int:
        """Antigravity's own view of its conversations: titles, parents and status."""
        self._summaries_at = time.monotonic()
        count = 0
        with self._sessions() as db:
            for base in self._antigravity_bases:
                # Its summaries are read whole and stored one by one, a thousand of them on
                # a long-used machine, so only when their file has changed since last time.
                state = self._summaries_state(base)
                if state is not None and state == self._summaries_seen.get(base):
                    continue
                try:
                    summaries = antigravity.read_summaries(self._access, base)
                except Exception:  # a locked or changing store: try again next time
                    log.warning("could not read the Antigravity summaries in one base")
                    continue
                self._summaries_seen[base] = state
                antigravity.store_summaries(db, summaries, antigravity.SURFACES[base.name])
                for summary in summaries:
                    status = antigravity.harness_status(summary)
                    if status is not None and summary.last_modified is not None:
                        self._harness_status[summary.conversation_id] = (
                            status,
                            summary.last_modified,
                            summary.archived,
                        )
                count += len(summaries)
            db.commit()
        return count

    # -- probe ------------------------------------------------------------------------

    def _claude_registry(self) -> dict[int, claude_code.RegistryEntry]:
        entries: dict[int, claude_code.RegistryEntry] = {}
        for root in self._claude_roots:
            for entry in claude_code.read_registry(self._access, root):
                entries[entry.pid] = entry
        return entries

    def _sessions_of(
        self, process: ObservedProcess, registry: dict[int, claude_code.RegistryEntry]
    ) -> tuple[list[tuple[str, str]], HarnessStatus | None, datetime | None]:
        """(harness, native id) pairs a process runs, and the harness's own status if any."""
        if process.harness is Harness.CLAUDE_CODE:
            entry = registry.get(process.identity.pid)
            if entry is None:
                return [], None, None
            status = CLAUDE_STATUS.get(entry.status or "")
            return [(Harness.CLAUDE_CODE.value, entry.session_id)], status, entry.status_at
        if process.harness is Harness.CODEX:
            ids = codex_rollouts(process, self._codex_roots)
            return [(Harness.CODEX.value, native) for native in ids], None, None
        if process.harness is Harness.PI:
            held = [
                f
                for f in process.harness_files
                if f.endswith(".jsonl") and any(_inside(Path(f), root) for root in self._pi_roots)
            ]
            return [(Harness.PI.value, pi.session_id_of(f)) for f in held], None, None
        return [], None, None

    def probe(self) -> int:
        """One pass over the process table. Returns how many session states changed."""
        now = self._clock()
        snapshot = take_snapshot(self._probe.table(), self._probe, self._access.roots)
        changes = self._tracker.update(snapshot)
        registry = self._claude_registry()
        changed = 0
        with self._sessions() as db:
            running: dict[int, HarnessStatus | None] = {}
            status_at: dict[int, datetime | None] = {}
            held: dict[ProcessIdentity, set[int]] = {}
            unread: dict[ProcessIdentity, str] = {}

            named: set[ProcessIdentity] = set()

            def attach(process, pairs, status, at, make=None) -> None:
                """Tie ``process`` to the sessions it runs; ``make`` stores one with no file."""
                ids = held.setdefault(process.identity, set())
                for harness, native in pairs:
                    session = db.scalars(
                        select(Session).where(
                            Session.harness == harness, Session.native_id == native
                        )
                    ).one_or_none()
                    if session is None:
                        if make is None:
                            continue  # not read yet; the next ingest pass finds it
                        unread[process.identity] = native
                        if self._unread.get(process.identity) != native:
                            continue
                        session = make(native)
                    ids.add(session.id)
                    running[session.id] = status
                    status_at[session.id] = at
                    self._record_process(db, session.id, process, now)

            for process in snapshot.processes:
                if process.role is Role.HELPER or is_terminal_back_end(process):
                    continue
                pairs, status, at = self._sessions_of(process, registry)
                entry = registry.get(process.identity.pid)
                make = None
                if process.harness is Harness.CLAUDE_CODE and entry is not None:
                    make = partial(self._registered_session, db, process, entry)
                if pairs:
                    named.add(process.identity)
                attach(process, pairs, status, at, make)
            # Codex's terminal UI hands its session to a daemon, which holds the rollout
            # open and outlives the UI. What the daemon holds runs only while a terminal
            # UI does, and is the UI's: the session ends when the last UI exits. The
            # daemon cannot say which UI a rollout serves, so with several UIs running
            # each takes every rollout the daemon holds.
            clients = terminal_clients(snapshot)
            for back_end in filter(is_terminal_back_end, snapshot.processes):
                pairs, status, at = self._sessions_of(back_end, registry)
                for client in clients:
                    if pairs:
                        named.add(client.identity)
                    attach(client, pairs, status, at)
            # A session process that names no session: started with no record to keep,
            # or not yet given anything to do. It is shown from the process alone. Once
            # the process names a session, that one stands for the run and this one goes.
            for process in snapshot.processes:
                # Antigravity says itself which conversations run; its processes name none.
                if process.role is not Role.SESSION or process.harness is Harness.ANTIGRAVITY:
                    continue
                if process.identity in named:
                    stood_in = self._stood_in.pop(process.identity, None)
                    if stood_in is not None:
                        self._retire(db, stood_in)
                    continue
                own = [(process.harness.value, process_session_id(process))]
                attach(process, own, None, None, partial(self._process_session, db, process))
                for session_id in held[process.identity]:
                    self._stood_in[process.identity] = session_id
            self._stood_in = {
                identity: session_id
                for identity, session_id in self._stood_in.items()
                if identity in held
            }
            self._unread = unread
            for identity, ids in held.items():
                if ids:
                    self._ran_by[identity] = ids
            exited = set()
            for process in changes.ended:
                exited |= self._ran_by.pop(process.identity, set())
            exited -= set(running)

            for session in self._candidates(db, now, set(running) | exited):
                observation = self._observe(db, session, now, running, status_at, exited)
                previous = self._records.get(session.id) or _record_from(session)
                record = advance(previous, observation)
                self._records[session.id] = record
                if previous is None or (record.state, record.basis) != (
                    previous.state,
                    previous.basis,
                ):
                    for name, value in session_state_fields(record).items():
                        setattr(session, name, value)
                    if record.state is SessionState.ENDED and session.ended_at is None:
                        session.ended_at = now
                    if previous is None or record.state is not previous.state:
                        db.add(
                            SessionStateChange(
                                session_id=session.id,
                                state=record.state,
                                basis=record.basis,
                                previous=record.previous,
                                changed_at=record.since,
                            )
                        )
                    changed += 1
            db.commit()
        return changed

    def _registered_session(
        self,
        db: DbSession,
        process: ObservedProcess,
        entry: claude_code.RegistryEntry,
        native: str,
    ) -> Session:
        """A session known only from Claude Code's registry: it has no transcript.

        One started without persistence never writes a transcript, and one opened in a
        terminal writes none before its first prompt. What the registry and the process
        say is all there is to store. If a transcript does come, its records fill the
        same session in.
        """
        model = stated_model(process)
        session = Session(
            harness=Harness.CLAUDE_CODE.value,
            native_id=native,
            cwd=entry.cwd or process.cwd,
            surface=claude_code.SURFACES.get(entry.entrypoint or "") or process.surface,
            models=[model] if model else [],
            started_at=process.identity.started_at,
        )
        db.add(session)
        db.flush()
        return session

    def _process_session(self, db: DbSession, process: ObservedProcess, native: str) -> Session:
        """A session known only by the process that runs it: nothing on disk names it.

        A harness told to keep no record of a session writes none, and some say nothing
        of a session before its first prompt. The process still shows that an agent
        runs, where, since when, and on which model if its command line says.
        """
        model = stated_model(process)
        session = Session(
            harness=process.harness.value,
            native_id=native,
            cwd=process.cwd,
            surface=process.surface,
            models=[model] if model else [],
            started_at=process.identity.started_at,
        )
        db.add(session)
        db.flush()
        return session

    def _retire(self, db: DbSession, session_id: int) -> None:
        """Remove a session that stood in for a process which has since named its own."""
        session = db.get(Session, session_id)
        if session is not None:
            db.delete(session)
        self._records.pop(session_id, None)

    def _record_process(
        self, db: DbSession, session_id: int, process: ObservedProcess, now
    ) -> None:
        fields = session_process_fields(process)
        row = db.scalars(
            select(SessionProcess).where(
                SessionProcess.session_id == session_id,
                SessionProcess.pid == fields["pid"],
                SessionProcess.process_started_at == fields["process_started_at"],
            )
        ).one_or_none()
        if row is None:
            db.add(
                SessionProcess(session_id=session_id, first_seen_at=now, last_seen_at=now, **fields)
            )
        elif now - row.last_seen_at >= timedelta(seconds=30):
            row.last_seen_at = now  # coarse, so a long session does not write every pass

    def _candidates(self, db: DbSession, now: datetime, touched: set[int]) -> list[Session]:
        alive = (SessionState.ACTIVE, SessionState.WAITING, SessionState.IDLE)
        # Antigravity says a conversation's status itself, so one is looked at while that
        # is news: recently modified, or archived and not yet marked ended. Every other
        # conversation it ever kept can no longer change, and looking at each of them on
        # every pass cost a query apiece, every two seconds, for good.
        conversations = [
            conversation
            for conversation, (_status, modified, archived) in self._harness_status.items()
            if archived or modified >= now - RECENT
        ]
        # Asked as one question these make the store read every session it holds, every
        # pass. Each is answered from an index when asked on its own.
        wanted = [
            Session.state.in_(alive),
            # Every state is named so the index on state and last activity serves this.
            Session.state.in_(tuple(SessionState)) & (Session.last_activity_at >= now - RECENT),
        ]
        if touched:
            wanted.append(Session.id.in_(touched))
        if conversations:
            wanted.append(
                (Session.harness == Harness.ANTIGRAVITY.value)
                & Session.native_id.in_(conversations)
                & (Session.state != SessionState.ENDED)
            )
        found: dict[int, Session] = {}
        for condition in wanted:
            for session in db.scalars(select(Session).where(condition)):
                found[session.id] = session
        return [found[key] for key in sorted(found)]

    def _observe(self, db, session: Session, now, running, status_at, exited) -> Observation:
        process = None
        status = at = None
        archived = False
        if session.id in running:
            process = ProcessLiveness.RUNNING
            status, at = running[session.id], status_at.get(session.id)
        elif session.id in exited:
            process = ProcessLiveness.EXITED
        if (
            session.harness == Harness.ANTIGRAVITY.value
            and session.native_id in self._harness_status
        ):
            status, at, archived = self._harness_status[session.native_id]
        if status is not None and at is None:
            status = None
        last = db.execute(
            select(Event.kind, Event.payload)
            .where(Event.session_id == session.id, Event.kind != EventKind.USAGE)
            .order_by(Event.sequence.desc())
            .limit(1)
        ).first()
        turn = None
        if last is not None:
            extra = (last.payload or {}).get("extra") or {}
            ends = extra.get("stop_reason") in TURN_ENDS or extra.get("phase") in TURN_ENDS
            if last.kind == EventKind.STATUS and last.payload.get("status") == "task_complete":
                ends = True
            turn = turn_phase(EventKind.ASSISTANT_TEXT if ends else last.kind, ends_turn=ends)
        return Observation(
            now=now,
            process=process,
            harness_status=status,
            harness_status_at=at,
            last_activity_at=session.last_activity_at,
            turn=turn,
            archived=archived,
            one_shot=session.surface is Surface.EXEC,
        )

    # -- running ----------------------------------------------------------------------

    def run(self, stop: threading.Event, *, poll_seconds: float = 30.0) -> list[threading.Thread]:
        """Start both loops; they stop when ``stop`` is set."""
        self._reading = Reading()

        def ingest_loop() -> None:
            def scan(paths: list[Path]) -> bool:
                try:
                    totals = self.scan(paths)
                    starting = self._reading is not None and not self._reading.finished
                    if totals.lines and not starting:  # that read reports its own progress
                        log.info("read %d new lines from %d files", totals.lines, totals.files)
                    return True
                except Exception:
                    log.exception("an ingest pass failed; the next one retries")
                    return False

            def signature(path: Path) -> tuple[int, int, int]:
                return signature_of(self._access.stat(path))

            watch(
                directories=self.watched,
                discover=self.discover,
                scan=scan,
                stop=stop,
                poll_seconds=poll_seconds,
                match=self.matching,
                order=self.urgent_first,
                progress=self._read_so_far,
                signature=signature,
                discover_signed=self.discover_signed,
            )

        def probe_loop() -> None:
            while not stop.is_set():
                try:
                    changed = self.probe()
                    if changed:
                        log.info("%d session states changed", changed)
                    if time.monotonic() - self._auth_at >= AUTH_SECONDS:
                        self.refresh_auth()
                except Exception:
                    log.exception("a probe pass failed; the next one retries")
                stop.wait(PROBE_SECONDS)

        threads = [
            threading.Thread(target=ingest_loop, name="agdash-ingest", daemon=True),
            threading.Thread(target=probe_loop, name="agdash-probe", daemon=True),
        ]
        for thread in threads:
            thread.start()
        return threads


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except (ValueError, OSError):
            return False


def _record_from(session: Session) -> StateRecord | None:
    """The state record stored on a session, to carry on from after a restart."""
    evidence = session.state_evidence or {}
    if not evidence.get("basis") or not evidence.get("since"):
        return None
    try:
        return StateRecord(
            state=session.state,
            basis=Basis(evidence["basis"]),
            since=datetime.fromisoformat(evidence["since"]),
            observed_at=session.state_observed_at or datetime.fromisoformat(evidence["since"]),
            previous=SessionState(evidence["previous"]) if evidence.get("previous") else None,
            evidence=evidence.get("observed") or {},
        )
    except (ValueError, KeyError):
        return None
