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

Build the service as one self-contained binary for this machine, and check that it
starts, answers `/health` and stops cleanly:

```bash
.venv/bin/pip install -e "backend[freeze]"
(cd backend && ../.venv/bin/pyinstaller --noconfirm packaging/agdash.spec)
(cd backend && ../.venv/bin/python packaging/smoke.py dist/agdash)
```

CI builds and smoke-tests it for macOS arm64 and x86_64 and Linux amd64 and arm64,
each on a runner of that kind.

Backfill speed and memory, and how to measure them, are in
[docs/performance.md](docs/performance.md).

Contributor and agent rules are in [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE)
