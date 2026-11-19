# What the harnesses expose on Ubuntu

The four CLI harnesses, surveyed on Ubuntu 24.04.5 (arm64) on 2026-11-19, in the
container in [`devtools/ubuntu-survey`](../devtools/ubuntu-survey/README.md).
Versions: Claude Code 2.1.326, Codex 0.169.0 (its daemon updated itself to
0.170.0), Pi 1.5.2 and the Antigravity CLI (`agy`) 1.5.12. Each harness ran the
same short session in the same folder, signed in as on the Mac.

Every cell is **verified** (seen in this survey) or **not available** (the harness
does not write it on Linux). The Antigravity desktop app was not surveyed: a
container has no desktop.

| | Live signal | History | Thoughts | Auth method source |
|---|---|---|---|---|
| **Claude Code** | Verified: `~/.claude/sessions/<pid>.json` with pid, `procStart`, session id, cwd, entry point, name and `busy`/`idle` status, removed when the session ends. As on macOS | Verified: `~/.claude/projects/<cwd-slug>/<session>.jsonl`, the slug being the cwd with `/` as `-` | Verified: `thinking` blocks carry a signature and no text. agdash records them as `encrypted`, as on macOS | Verified: `oauthAccount` fields in `~/.claude.json` (billing type). The sign-in token is in the file `~/.claude/.credentials.json`, where macOS uses the Keychain |
| **Codex** | Verified: the process table, and the rollout file held open. But the TUI hands the session to an **app-server daemon**. The daemon holds the rollout open, and keeps running after the TUI quits | Verified: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`, `session_index.jsonl` and the SQLite stores (`state_5`, `goals_1`, `logs_2`, `thread_history_1`, `memories_1`, `queue_1`), as on macOS | Not available in these sessions: no reasoning record was written | Verified: `auth_mode` in `~/.codex/auth.json`, a file as on macOS |
| **Antigravity CLI** | Verified: the process table, and `not_fully_idle` and `last_modified_time` in `conversation_summaries.db`. The `agy` process holds that database and `conversations/<id>.db` open. `presence/<id>.lock` stays after the session ends, as on macOS | Verified: `~/.gemini/antigravity-cli/`: `conversation_summaries.db`, `conversations/<id>.db`, `brain/<id>/.system_generated/logs/transcript_full.jsonl`, `history.jsonl` | Verified: `thinking` text on planner steps. agdash records it as `full` | Not available as on macOS: there is no `google_accounts.json`. The sign-in is the file `~/.gemini/antigravity-cli/antigravity-oauth-token`, beside `~/.config/antigravity/credential-user-scope` |
| **Pi** | Verified: the process table and session file growth; Pi holds no file open, as on macOS | Verified: `~/.pi/agent/sessions/--<cwd-slug>--/<ts>_<id>.jsonl`, session format 3 | Verified: `thinking` blocks with text. agdash records them as `full` | Verified: a `baseUrl` for the provider in `~/.pi/agent/models.json` means a custom endpoint, as on macOS |

## What agdash does with it today

agdash's current adapters read every harness's history on Ubuntu unchanged. The
survey's read-only run gave sessions, prompts, replies, tool calls, usage and
thinking for all four, and no parse error. `backend/tests/test_linux_fixtures.py`
holds that to the Linux fixtures.

What the survey found missing, and how agdash handles it now:

| Where | What the survey saw | Now |
|---|---|---|
| Process probe, Claude Code | The npm install runs `@anthropic-ai/claude-code/bin/claude.exe`; the probe expected a binary named `claude` | A binary named `claude.exe`, started as `claude`, is Claude Code |
| Process probe, Pi | Pi 1.5.2 sets its process title, so the command line is just `pi` under `node` | A `node` or `bun` process started as `pi` is Pi |
| Process probe, Codex | The app-server daemon (`codex app-server … --managed-daemon`) and its updater (`codex app-server daemon …`) were classed as a desktop host | The daemon is a terminal back end; its updater is a helper |
| Codex live signal | The daemon keeps a finished session's rollout open | What the daemon holds runs only while a Codex terminal UI does, and belongs to that UI: the session ends when the last UI exits. With several UIs open, each takes every rollout the daemon holds, since the daemon does not say which UI a rollout serves |
| Antigravity auth | No `google_accounts.json` | The CLI's token file existing means a Google sign-in. agdash asks only whether it exists and never opens it. There is no account label |
| Claude Code records | `mode` and `permission-mode` were unknown | Read as metadata |
| `agdash doctor` | A harness with no files here was a warning | It is "not installed on this host", not a fault; only a host with no harness at all is warned about |

Still as it was: Pi sessions have no surface. That is the same on macOS, because
Pi's records do not say how it was started.

Also seen:

- On Linux, all three sign-ins are plain files with mode `0600`. agdash reads none of
  them; the sign-in method comes from allowlisted fields elsewhere, or not at all.
- Codex updates itself into `~/.codex/packages/`. A pinned `codex` still hands its
  sessions to the newer daemon, so the rollout carries the daemon's version (0.170.0).

## How it was checked

- `survey.sh live` read the process table, each harness process's open files,
  Claude Code's registry and Antigravity's summary database, with all four sessions
  open.
- `survey.sh read` ran agdash from this checkout in a container that mounts the
  survey home **read-only** with no network. It made the inventory, ran the backfill
  and the fixture kit, and compared every harness file's size, time and hash before
  and after. They matched: 2,432 files.
- Some sessions ran first on the container's earlier versions (Claude Code 2.1.325,
  Codex 0.167.2, Pi 1.0.2's first-run files were removed). The fixture kit chose
  from every session in the home, so the Claude Code fixture is a 2.1.325 session.
- The fixtures in `backend/tests/fixtures/harness_linux/` are the kit's output, byte
  for byte. No string of twelve characters or more from them appears in the
  survey's records, other than record types, model names and other vocabulary. The
  committed-fixtures privacy scan covers them too.
