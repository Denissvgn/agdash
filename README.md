# agdash

A read-only dashboard for agentic coding sessions on your machine. It shows
which agents are running now and keeps a history of what each session was
asked, did and used, across Claude Code, Codex, Antigravity and Pi.

agdash only observes. It never starts, stops or changes an agent, never writes
to a harness's files, and never reads credentials.

**Status: early development.** The repository currently contains the project
skeleton; there is nothing to install yet.

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
How the figures are checked for keyboard use, screen readers, forced colours,
zoom and reflow is in [docs/figures-review.md](docs/figures-review.md).

Contributor and agent rules are in [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE)
