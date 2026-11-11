# Agent instructions — agdash

agdash is a read-only dashboard for agentic coding sessions on this host
(Claude Code, Codex, Antigravity, Pi). It ships as a macOS app and an Ubuntu
app. These rules bind every change in this repository.

## Boundaries: agdash observes and nothing else

Code in this repository must never:

1. **Control an agent.** Do not start, stop, signal, resume or message an
   agent or its process.
2. **Touch harness data.** Do not write, lock or create files inside a harness
   directory. Open harness files read-only; read SQLite stores from a private
   snapshot copy.
3. **Change harness configuration.** Do not install hooks, plugins, MCP
   servers or settings into a harness.
4. **Connect to harness control channels.** Do not open a harness control
   socket or port (messaging sockets, IPC sockets, language-server ports).
5. **Read credentials.** Do not read a token, key or refresh credential. Take
   the auth *method* from allowlisted, non-secret fields only, and never load
   other fields of a credential file.
6. **Write to WorkChord.** The WorkChord connector only reads. The single
   exception is the opt-in publish mode, which adds one link on a task.

Everything agdash learns comes from the process table, the open-file table,
and files the harnesses already write. If a feature seems to need more than
that, stop and raise it instead of implementing it.

The same rules apply to what you do while working here: do not modify
`~/.claude`, `~/.codex`, `~/.gemini`, `~/.pi` or any harness application data
to build, test or debug agdash. Tests use synthetic fixtures.

## Privacy

- The service binds to loopback only.
- Session transcripts are private. Never copy real transcript content, paths,
  account names or secrets into fixtures, tests, logs, commits or issues.
- Redact before storing. Never log request bodies or process arguments
  unredacted.

## Python

- Always use the project virtual environment at `.venv/`.
- Run tools as `.venv/bin/python`, `.venv/bin/pytest`, `.venv/bin/ruff`.
- Never use bare `python`, `pip` or other Python tools.

## Layout and checks

| Path | What | Checks |
|---|---|---|
| `backend/` | Service and API (FastAPI) | `.venv/bin/ruff check backend`, `.venv/bin/ruff format --check backend`, `cd backend && ../.venv/bin/pytest` |
| `frontend/` | UI (React, Vite, Tailwind) | `npm run lint`, `npm run test:run`, `npm run build` in `frontend/` |

Set up with:

```bash
python3 -m venv .venv && .venv/bin/pip install -e "backend[dev]"
(cd frontend && npm ci)
```

All checks must pass on macOS and Ubuntu before a change is done. Keep
operating-system differences behind the platform layer; adapters and API code
contain no operating-system branches.

## Repository hygiene

- Public documentation (README, published docs) must not mention internal
  development phases, plans or task ids.
- Code and tests (comments, identifiers, fixtures) must not carry task ids or
  planning references.
- Keep edits surgical and state uncertainty.
