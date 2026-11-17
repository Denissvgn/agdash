# agdash

A read-only dashboard for agentic coding sessions on your machine. It shows
which agents are running now and keeps a history of what each session was
asked, did and used, across Claude Code, Codex, Antigravity and Pi.

agdash only observes. It never starts, stops or changes an agent, never writes
to a harness's files, and never reads credentials. Everything it shows comes from
the process table, the open-file table and the files the harnesses already
write. Where it has no evidence for a state or a mapping, it says "unknown"
rather than guessing.

## What it shows

- **Live**: every running session on one board, with its state (active,
  waiting, idle, ended or unknown) and the evidence for that state, and a graph
  of which live session started which, across harnesses.
- **History**: every session agdash has recorded, filterable by harness, period,
  model, folder and auth method, with search over what was asked, said or run. A
  session's page shows its task, model and effort, auth method, thoughts, tool
  calls and usage on a timeline. Thoughts appear at the coverage the harness
  left: full, summary, encrypted or absent.
- **Projects**: live sessions grouped by the project, agent and task of a
  [WorkChord](docs/workchord-access.md) server they work under, and the sessions
  that map to nothing.
- **Analytics**: tokens, cost and time by harness, model or folder; which tools
  agents call and how often a call fails; and when sessions are active.
- **Settings**: what agdash can see on this machine, the WorkChord connection,
  and what it keeps, for how long.

A session can be exported to a file you name. Nothing else leaves the machine.

## Run it

agdash runs on macOS and Ubuntu as a desktop app (a window and a tray item with
the number of live sessions) over a background service. The service listens on
`127.0.0.1` only, and refuses every API request without a local token. On an
Ubuntu host with no display, open the UI in a browser on your own computer
through an SSH tunnel.

- macOS: build the app and disk image as in
  [docs/macos-package.md](docs/macos-package.md).
- Ubuntu: install the `.deb` packages or the AppImage, on a desktop or a server,
  as in [docs/ubuntu-packages.md](docs/ubuntu-packages.md).

The `agdash` command manages the service:

| Command | What it does |
|---|---|
| `agdash serve` | Run the service here |
| `agdash install` / `uninstall` | Start the service at login (LaunchAgent or systemd user unit), or stop doing so; your data stays |
| `agdash status` | Whether the service is installed and running, and what it holds |
| `agdash doctor` | Check this host's setup and say what to fix |
| `agdash backfill` | Read everything the harnesses have written, once |
| `agdash open` | Print a link that opens the UI in a browser, for a host with no app |
| `agdash connect` | Connect a WorkChord server, and manage its mapping rules and pins |
| `agdash export` | Export a session to a file |
| `agdash retention` | See or apply how long history is kept |

None of these starts, stops or changes an agent. Settings come from `AGDASH_`
environment variables, such as `AGDASH_PORT`, `AGDASH_DATABASE_URL`,
`AGDASH_RETENTION_DAYS` and `AGDASH_KEEP_RAW`.

Session transcripts are private. The service redacts before it stores, and keeps
what it reads on this host.

## Develop

Requires Python 3.11 or later and Node.js 22 or later.

```bash
python3 -m venv .venv && .venv/bin/pip install -e "backend[dev]"
(cd frontend && npm ci)
```

Run the service and the UI in two terminals:

```bash
.venv/bin/uvicorn agdash.main:app --app-dir backend --host 127.0.0.1 --port 8765 --reload
```

```bash
(cd frontend && npm run dev)
```

Then open [127.0.0.1:5174](http://127.0.0.1:5174). The service answers at
[127.0.0.1:8765/health](http://127.0.0.1:8765/health).

Checks:

```bash
.venv/bin/ruff check backend && .venv/bin/ruff format --check backend
(cd backend && ../.venv/bin/pytest)
(cd frontend && npm run lint && npm run test:run && npm run build)
```

The same UI tests also run in WebKit, the engine of the desktop app's web view on
macOS and Linux (the few that read files with Node run under jsdom only):

```bash
(cd frontend && npx playwright install webkit && npm run test:webkit)
```

Build the service as one self-contained binary for this machine, and check that it
starts, answers `/health` and stops cleanly:

```bash
.venv/bin/pip install -e "backend[freeze]"
(cd backend && ../.venv/bin/pyinstaller --noconfirm packaging/agdash.spec)
(cd backend && ../.venv/bin/python packaging/smoke.py dist/agdash)
```

CI builds and smoke-tests it for macOS arm64 and x86_64 and Linux amd64 and arm64,
each on a runner of that kind.

The desktop app (`desktop/`, Tauri 2) is a window on the UI and a tray item with
the number of live sessions. It attaches to the service on `127.0.0.1:8765`, or
starts it (through the LaunchAgent or systemd unit when installed, otherwise
`agdash serve` in its own process group), and takes the local token from
`agdash token`. Quitting it leaves the service running. A debug build loads the
UI from the frontend's dev server, a page on its own origin, so start the service
letting that origin in:

```bash
(cd frontend && npm run dev)
AGDASH_EXTRA_ORIGINS='["http://localhost:5174"]' .venv/bin/agdash serve
(cd desktop && cargo run)
```

`AGDASH_BIN` names the `agdash` command to use; by default the app looks beside
itself and then on the `PATH`. Its checks:

```bash
(cd desktop && cargo fmt --check && cargo clippy --locked --all-targets -- -D warnings && cargo test --locked)
```

Building, signing and notarising the macOS app and disk image are in
[docs/macos-package.md](docs/macos-package.md).
The Ubuntu packages, and running agdash on a server with no display, are in
[docs/ubuntu-packages.md](docs/ubuntu-packages.md).

Backfill speed and memory, and how to measure them, are in
[docs/performance.md](docs/performance.md).
What agdash reads from WorkChord, and the WorkChord changes it relies on, are in
[docs/workchord-access.md](docs/workchord-access.md) and
[docs/workchord-changes.md](docs/workchord-changes.md).
How the figures are checked for keyboard use, screen readers, forced colours,
zoom and reflow is in [docs/figures-review.md](docs/figures-review.md).

Contributor and agent rules are in [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE)
