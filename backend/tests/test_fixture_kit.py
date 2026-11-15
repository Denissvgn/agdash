"""The fixture kit: structure kept, content replaced, the same output every time.

The input here is a synthetic home directory in each harness's real layout,
planted with marker strings that stand for private content. No real session
data is involved.
"""

import json
import sqlite3
import stat
from datetime import timedelta
from pathlib import Path

import pytest

from agdash.access.guard import assert_untouched
from agdash.devtools.anonymise import LEXICON, Anonymiser
from agdash.devtools.fixture_kit import FixtureError, generate, load_salt, main
from agdash.normalise import cwd_slug, parse_timestamp
from agdash.platform.roots import harness_roots
from agdash.redaction import redact_text

SALT = b"fixture-kit-test-salt-0123456789"
PERSON = "Jane Doe"
HOME = "/Users/janedoe"
EMAIL = "jane.doe@corp.example"
PROJECT = f"{HOME}/secret-project"
SECRET = "sk-ant-api03-" + "Q" * 60 + "abcd"
ORG = "1f0e2d3c-4b5a-4968-8776-655443322110"
MARKERS = [
    "PROMPTMARKER",
    "janedoe",
    "Jane Doe",
    "corp.example",
    "secret-project",
    "sk-ant-api03",
    "id_rsa",
    "TITLEMARKER",
    "THOUGHTMARKER",
    "SUMMARYMARKER",
    ORG,
]

SESSION = "0b5f6c1e-7a2d-4c3b-9e1f-2d3c4b5a6f70"
CODEX_ID = "6e2a1c4b-5d3f-4a2e-9b8c-7d6e5f4a3b2c"
PI_ID = "9c8b7a6f-5e4d-4c3b-8a29-1f0e2d3c4b5a"
CONVERSATION = "aa11bb22-cc33-4d44-8e55-ff6677889900"


def jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def build_home(home: Path) -> Path:
    claude = home / ".claude"
    slug = cwd_slug("claude_code", PROJECT)
    first_uuid = "11111111-2222-4333-8444-555555555555"
    jsonl(
        claude / "projects" / slug / f"{SESSION}.jsonl",
        [
            {
                "type": "user",
                "uuid": first_uuid,
                "parentUuid": None,
                "sessionId": SESSION,
                "cwd": PROJECT,
                "timestamp": "2026-11-11T10:00:00.000Z",
                "gitBranch": "feature/secret-project",
                "message": {
                    "role": "user",
                    "content": "PROMPTMARKER please refactor the billing module",
                },
            },
            {
                "type": "assistant",
                "uuid": "22222222-3333-4444-8555-666666666666",
                "parentUuid": first_uuid,
                "timestamp": "2026-11-11T10:00:07.500Z",
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-5-5",
                    "stop_reason": "tool_use",
                    "content": [
                        {"type": "thinking", "thinking": "THOUGHTMARKER I should read the key"},
                        {
                            "type": "tool_use",
                            "id": "toolu_01AbCdEfGhIjKlMnOp",
                            "name": "Bash",
                            "input": {
                                "command": f"cat {HOME}/.ssh/id_rsa",
                                "description": "Read it",
                            },
                        },
                    ],
                    "usage": {"input_tokens": 12, "output_tokens": 40},
                },
            },
            {
                "type": "attachment",
                "attachment": {
                    "type": "credential_org",
                    "organizationUuid": ORG,
                    "identity": {"name": PERSON, "email": EMAIL},
                },
            },
            {
                "type": "custom-title",
                "customTitle": "TITLEMARKER refactor billing",
                "sessionId": SESSION,
            },
            {
                "type": "user",
                "uuid": "33333333-4444-4555-8666-777777777777",
                "parentUuid": None,
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": f"token is {SECRET}"}],
                },
            },
        ],
    )
    jsonl(
        claude / "projects" / slug / SESSION / "subagents" / "agent-abc123def4567890.jsonl",
        [
            {
                "type": "user",
                "isSidechain": True,
                "agentId": "abc123def4567890",
                "cwd": PROJECT,
                "message": {"role": "user", "content": "PROMPTMARKER survey the module"},
            }
        ],
    )
    jsonl(
        claude / "projects" / slug / "77777777-8888-4999-8aaa-bbbbbbbbbbbb.jsonl",
        [
            {
                "type": "user",
                "cwd": PROJECT,
                "message": {"role": "user", "content": "PROMPTMARKER hi"},
            }
        ],
    )
    (claude / "sessions").mkdir(parents=True)
    (claude / "sessions" / "4242.json").write_text(
        json.dumps(
            {
                "pid": 4242,
                "sessionId": SESSION,
                "cwd": PROJECT,
                "name": "TITLEMARKER session",
                "status": "busy",
                "entrypoint": "claude-desktop",
            }
        )
    )

    codex = home / ".codex"
    jsonl(
        codex / "sessions" / "2026" / "11" / "11" / f"rollout-2026-11-11T10-00-00-{CODEX_ID}.jsonl",
        [
            {
                "timestamp": "2026-11-11T10:00:00.000Z",
                "type": "session_meta",
                "payload": {
                    "id": CODEX_ID,
                    "cwd": PROJECT,
                    "originator": "codex_cli_rs",
                    "source": "cli",
                    "cli_version": "0.99.0",
                    "creator_account_id": "user-AbCdEf123456789",
                    "base_instructions": {"text": "PROMPTMARKER you are a helpful agent"},
                },
            },
            {
                "timestamp": "2026-11-11T10:00:01.000Z",
                "type": "response_item",
                "payload": {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "SUMMARYMARKER plan"}],
                    "encrypted_content": "gAAAAAB" + "x9" * 300,
                },
            },
            {
                "timestamp": "2026-11-11T10:00:02.000Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "call_AbCdEf123456",
                    "arguments": json.dumps({"cmd": f"ls {PROJECT}", "workdir": PROJECT}),
                },
            },
            {
                "timestamp": "2026-11-11T10:00:03.000Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"input_tokens": 100}},
                },
            },
        ],
    )
    jsonl(
        codex / "session_index.jsonl",
        [
            {
                "id": CODEX_ID,
                "thread_name": "TITLEMARKER thread",
                "updated_at": "2026-11-11T10:05:00Z",
            },
            {"id": "ffffffff-0000-4000-8000-000000000000", "thread_name": "other"},
        ],
    )

    pi = home / ".pi" / "agent" / "sessions" / cwd_slug("pi", PROJECT)
    records = [
        {
            "type": "session",
            "version": 3,
            "id": PI_ID,
            "cwd": PROJECT,
            "timestamp": "2026-11-11T10:00:00.000Z",
        }
    ]
    records += [
        {
            "type": "message",
            "id": f"m{i:04d}",
            "message": {"role": "user", "content": f"PROMPTMARKER {i}"},
        }
        for i in range(320)
    ]
    records += [
        {"type": "model_change", "provider": "local", "modelId": "unsloth/Qwen3.8-Flash-Next"},
        {"type": "compaction", "summary": "SUMMARYMARKER so far", "tokensBefore": 9000},
    ]
    jsonl(pi / f"2026-11-11T10-00-00-000Z_{PI_ID}.jsonl", records)

    for name, rows in (("antigravity", 2), ("antigravity-cli", 1)):
        base = home / ".gemini" / name
        (base / "brain" / CONVERSATION).mkdir(parents=True)
        (base / "brain" / CONVERSATION / "task.md").write_text(
            "# TITLEMARKER\n\nPROMPTMARKER do the thing\n"
        )
        (base / "brain" / CONVERSATION / "janedoe-notes.md").write_text("THOUGHTMARKER\n")
        db = sqlite3.connect(base / "conversation_summaries.db")
        db.execute(
            "CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, title TEXT, "
            "preview TEXT, step_count INTEGER, last_modified_time INTEGER, workspace_uris TEXT, "
            "status TEXT, parent_conversation_id TEXT, not_fully_idle INTEGER)"
        )
        db.execute(
            "CREATE VIRTUAL TABLE conversations_fts USING fts5("
            "conversation_id, title, user_body, agent_body)"
        )
        db.execute("CREATE TABLE search_schema_version (version INTEGER)")
        db.execute("INSERT INTO search_schema_version VALUES (2)")
        for i in range(rows + 2):
            cid = CONVERSATION if i == 0 else f"{i:08d}-0000-4000-8000-000000000000"
            db.execute(
                "INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    cid,
                    "TITLEMARKER conversation",
                    "PROMPTMARKER preview",
                    i + 1,
                    1762855200000 + i,
                    json.dumps([f"file://{PROJECT}"]),
                    "IDLE",
                    None,
                    0,
                ),
            )
            db.execute(
                "INSERT INTO conversations_fts VALUES (?,?,?,?)",
                (cid, "TITLEMARKER", "PROMPTMARKER body", "THOUGHTMARKER answer"),
            )
        db.commit()
        db.close()
    (home / ".gemini" / "google_accounts.json").write_text(json.dumps({"active": EMAIL}))
    return home


@pytest.fixture
def home(tmp_path) -> Path:
    return build_home(tmp_path / "home")


def run(home: Path, out: Path, salt: bytes = SALT):
    return generate(out, salt, harness_roots(home, {}))


def all_text(out: Path) -> str:
    """All output as text: files as they are, SQLite stores as their SQL dump."""
    chunks = []
    for path in sorted(out.rglob("*")):
        if not path.is_file():
            continue
        chunks.append(str(path.relative_to(out)))
        if path.suffix == ".db":
            # Through SQL, which shows every row; the raw pages are binary noise.
            with sqlite3.connect(path) as db:
                chunks.extend(db.iterdump())
        else:
            chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_no_private_text_survives(home, tmp_path):
    out = tmp_path / "fixtures"
    run(home, out)
    text = all_text(out)
    for marker in MARKERS:
        assert marker not in text, marker
    assert "@" not in text.replace("@example.test", "")
    assert not list(out.rglob("google_accounts.json"))


def test_the_same_input_gives_the_same_output(home, tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    run(home, first)
    run(home, second)
    files = sorted(p.relative_to(first) for p in first.rglob("*") if p.is_file())
    assert files == sorted(p.relative_to(second) for p in second.rglob("*") if p.is_file())
    assert len(files) >= 10
    for relative in files:
        assert (first / relative).read_bytes() == (second / relative).read_bytes(), relative


def test_a_different_salt_gives_different_output(home, tmp_path):
    run(home, tmp_path / "one")
    run(home, tmp_path / "two", salt=b"another-salt-for-the-fixture-kit")
    assert all_text(tmp_path / "one") != all_text(tmp_path / "two")


def test_every_harness_has_fixtures(home, tmp_path):
    out = tmp_path / "fixtures"
    report = run(home, out)
    assert {"claude_code", "codex", "pi", "antigravity"} <= set(report.files)
    assert list(out.glob("claude/projects/*/*.jsonl"))
    assert list(out.glob("claude/projects/*/*/subagents/*.jsonl"))
    assert list(out.glob("claude/sessions/*.json"))
    assert list(out.glob("codex/sessions/*/*/*/rollout-*.jsonl"))
    assert list(out.glob("codex/archived_sessions/rollout-*.jsonl"))
    assert (out / "codex" / "session_index.jsonl").exists()
    assert list(out.glob("pi/agent/sessions/*/*.jsonl"))
    assert (out / "antigravity" / "app" / "conversation_summaries.db").exists()
    assert (out / "antigravity" / "cli" / "conversation_summaries.db").exists()
    assert (out / "README.md").exists()


def test_structure_and_references_survive(home, tmp_path):
    out = tmp_path / "fixtures"
    run(home, out)
    anon = Anonymiser(SALT)

    (transcript,) = [
        p for p in out.glob("claude/projects/*/*.jsonl") if p.stem == anon.uuid(SESSION)
    ]
    rows = records(transcript)
    assert [r.get("type") for r in rows] == [
        "user",
        "assistant",
        "attachment",
        "custom-title",
        "user",
    ]
    assert rows[1]["parentUuid"] == rows[0]["uuid"], "references between records still line up"
    assert rows[0]["sessionId"] == transcript.stem
    assert transcript.parent.name == cwd_slug("claude_code", rows[0]["cwd"]), (
        "folder name matches the new cwd"
    )
    tool = rows[1]["message"]["content"][1]
    assert (tool["type"], tool["name"]) == ("tool_use", "Bash")
    assert rows[1]["message"]["model"] == "claude-opus-5-5"
    assert rows[1]["message"]["usage"] == {"input_tokens": 12, "output_tokens": 40}
    assert rows[1]["message"]["content"][0]["type"] == "thinking"
    assert rows[2]["attachment"]["type"] == "credential_org"
    assert set(rows[2]["attachment"]) == {"type", "organizationUuid", "identity"}

    (rollout,) = out.glob("codex/sessions/*/*/*/rollout-*.jsonl")
    codex = records(rollout)
    assert rollout.name.endswith(f"-{codex[0]['payload']['id']}.jsonl"), (
        "file name and session id agree"
    )
    assert codex[0]["payload"]["originator"] == "codex_cli_rs"
    call = json.loads(codex[2]["payload"]["arguments"])
    assert set(call) == {"cmd", "workdir"} and call["workdir"].startswith("/work/")
    assert len(codex[1]["payload"]["encrypted_content"]) == 256
    assert rollout.read_bytes() == next(out.glob("codex/archived_sessions/*.jsonl")).read_bytes()

    (pi,) = out.glob("pi/agent/sessions/*/*.jsonl")
    pi_rows = records(pi)
    assert pi.parent.name == cwd_slug("pi", pi_rows[0]["cwd"])
    assert pi.name.endswith(f"_{pi_rows[0]['id']}.jsonl")
    kinds = [r["type"] for r in pi_rows]
    assert kinds.count("message") == 299, "long files are cut to their first records"
    assert "model_change" in kinds and "compaction" in kinds, "rare records past the cut are kept"
    assert (
        next(r for r in pi_rows if r["type"] == "model_change")["modelId"]
        == "unsloth/Qwen3.8-Flash-Next"
    )


def test_timestamps_shift_together(home, tmp_path):
    out = tmp_path / "fixtures"
    run(home, out)
    anon = Anonymiser(SALT)
    (transcript,) = [
        p for p in out.glob("claude/projects/*/*.jsonl") if p.stem == anon.uuid(SESSION)
    ]
    first, second = (parse_timestamp(r["timestamp"]) for r in records(transcript)[:2])
    assert second - first == timedelta(seconds=7.5)
    assert first < parse_timestamp("2026-11-11T10:00:00Z") - timedelta(days=360)


def test_antigravity_stores_keep_their_schema_and_search(home, tmp_path):
    out = tmp_path / "fixtures"
    run(home, out)
    with sqlite3.connect(out / "antigravity" / "app" / "conversation_summaries.db") as db:
        tables = {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {"conversation_summaries", "conversations_fts", "search_schema_version"} <= tables
        summaries = db.execute(
            "SELECT conversation_id, workspace_uris, status FROM conversation_summaries"
        ).fetchall()
        assert 1 <= len(summaries) <= 3
        assert all(json.loads(uris)[0].startswith("file:///work/") for _, uris, _ in summaries)
        assert {status for _, _, status in summaries} == {"IDLE"}
        word = (
            db.execute("SELECT user_body FROM conversations_fts LIMIT 1").fetchone()[0].split()[0]
        )
        assert db.execute(
            "SELECT count(*) FROM conversations_fts WHERE conversations_fts MATCH ?", (word,)
        ).fetchone()[0]
    names = [p.name for p in out.glob("antigravity/app/brain/*/*")]
    assert "task.md" in names and not any("janedoe" in n for n in names)


def test_the_harness_tree_is_only_read(home, tmp_path):
    with assert_untouched(
        list(harness_roots(home, {}).values())[0]
        + (home / ".codex", home / ".pi", home / ".gemini")
    ):
        run(home, tmp_path / "fixtures")


def test_output_never_goes_inside_a_harness_directory(home, tmp_path):
    with pytest.raises(FixtureError, match="inside a harness directory"):
        run(home, home / ".codex" / "fixtures")
    assert not (home / ".codex" / "fixtures").exists()


def test_a_directory_the_kit_did_not_write_is_not_replaced(home, tmp_path):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("mine")
    with pytest.raises(FixtureError, match="did not write"):
        run(home, foreign)
    assert (foreign / "keep.txt").read_text() == "mine"
    run(home, tmp_path / "fixtures")
    run(home, tmp_path / "fixtures")  # a directory the kit wrote is replaced


def test_the_salt_is_created_once_and_kept_private(tmp_path):
    path = tmp_path / "config" / "fixture-kit.salt"
    first = load_salt(path)
    assert len(first) == 32 and load_salt(path) == first
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_the_command_line_reports_counts_only(home, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "agdash.devtools.fixture_kit.current_host",
        lambda: type(
            "Host", (), {"harness_roots": staticmethod(lambda: harness_roots(home, {}))}
        )(),
    )
    assert main(["--out", str(tmp_path / "fixtures"), "--salt-file", str(tmp_path / "salt")]) == 0
    printed = capsys.readouterr().out
    assert "files" in printed and not any(marker in printed for marker in MARKERS)


# -- the committed fixtures --------------------------------------------------------

COMMITTED = Path(__file__).parent / "fixtures" / "harness"
LEXICON_WORDS = set(LEXICON)


def _strings(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


@pytest.mark.skipif(not COMMITTED.exists(), reason="no fixtures have been generated")
def test_committed_fixtures_hold_no_private_text():
    text = all_text(COMMITTED)
    assert "/Users/" not in text and "/home/" not in text
    assert "@" not in text.replace("@example.test", "")
    assert redact_text(text) == text, "nothing shaped like a secret"
    prose = 0
    for path in COMMITTED.rglob("*.jsonl"):
        for row in records(path):
            for value in _strings(row):
                words = value.split()
                if len(words) >= 3 and all(w.isalpha() for w in words):
                    prose += 1
                    assert set(words) <= LEXICON_WORDS, (
                        f"{path.name}: free text not from the word list"
                    )
    assert prose > 0, "the scan saw free text to check"


def test_media_types_are_kept_but_paths_under_them_are_not():
    anon = Anonymiser(SALT)
    assert anon.value({"type": "image", "media_type": "image/png"}) == {
        "type": "image",
        "media_type": "image/png",
    }
    assert anon.value({"media_type": "/Users/janedoe/x.png"})["media_type"].startswith("/work/")


@pytest.mark.parametrize(
    "key",
    ["started_at", "startedAt", "nameSince", "last_modified_time", "timestamp", "completedAtMs"],
)
def test_epoch_numbers_under_time_keys_are_shifted(key):
    anon = Anonymiser(SALT)
    shifted = anon.value({key: 1794761852317})[key]
    assert shifted < 1794761852317 - 360 * 86400 * 1000
    assert anon.value({key: 1794761852})[key] < 1794761852 - 360 * 86400
    assert anon.value({"count": 1794761852317})["count"] == 1794761852317
