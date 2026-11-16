"""Auth method and account label, from allowlisted non-secret fields only.

Every settings file here is synthetic. The values of secret fields are long
marker strings, so a test can prove that none of them reaches the store.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from agdash.access import ReadOnlyAccess
from agdash.adapters import codex
from agdash.collector.auth import AuthDetector, Finding
from agdash.db.migrate import upgrade
from agdash.db.models import Event, Session
from agdash.ingest import Ingestor, discover_files
from agdash.ingest.ledger import SqlLedger
from agdash.platform.roots import all_harness_roots, harness_roots
from agdash.vocab import AuthBasis, AuthMethod, EventKind, Harness, SessionState

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
CODEX_FIXTURES = Path(__file__).parent / "fixtures" / "harness" / "codex"

# Values that must never be read into stored data.
SECRETS = {
    "claude_console_key": "sk-ant-api03-SYNTHETICCONSOLEKEY0000000000000000000000",
    "claude_env_key": "sk-ant-api03-SYNTHETICENVKEY11111111111111111111111111",
    "claude_access": "SYNTHETIC-CLAUDE-ACCESS-TOKEN-2222222222222222222222",
    "claude_refresh": "SYNTHETIC-CLAUDE-REFRESH-TOKEN-333333333333333333333",
    "codex_key": "sk-proj-SYNTHETICCODEXKEY444444444444444444444444444444",
    "codex_access": "SYNTHETIC-CODEX-ACCESS-TOKEN-555555555555555555555555",
    "codex_refresh": "SYNTHETIC-CODEX-REFRESH-TOKEN-66666666666666666666666",
    "codex_id": "SYNTHETIC-CODEX-ID-TOKEN-holding-an-address-7777777777",
    "google_refresh": "SYNTHETIC-GOOGLE-REFRESH-TOKEN-8888888888888888888888",
    "pi_access": "SYNTHETIC-PI-ACCESS-TOKEN-99999999999999999999999999999",
    "pi_refresh": "SYNTHETIC-PI-REFRESH-TOKEN-aaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "pi_key": "sk-SYNTHETICPIKEYbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "pi_endpoint_key": "SYNTHETIC-PI-ENDPOINT-KEY-ccccccccccccccccccccccccccc",
}


class Home:
    """A synthetic home directory with each harness's settings files."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.roots = harness_roots(path, {})
        for roots in self.roots.values():
            for root in roots:
                if not root.suffix:
                    root.mkdir(parents=True, exist_ok=True)

    def write(self, relative: str, document) -> None:
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(document if isinstance(document, str) else json.dumps(document))

    def detector(self) -> AuthDetector:
        access = ReadOnlyAccess(all_harness_roots(self.path, {}), snapshot_dir=self.path / "x")
        return AuthDetector(access, self.roots, clock=lambda: NOW)


@pytest.fixture
def home(tmp_path) -> Home:
    return Home(tmp_path / "home")


@pytest.fixture
def store(tmp_path):
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url)
    engine = create_engine(url)
    yield sessionmaker(engine, expire_on_commit=False), tmp_path / "agdash.db"
    engine.dispose()


def claude_account(**extra):
    return {
        "oauthAccount": {
            "billingType": "stripe_subscription",
            "organizationName": "Example Studio (synthetic)",
            "emailAddress": "dev@example.test",
            "accessToken": SECRETS["claude_access"],
            **extra,
        },
        "refreshToken": SECRETS["claude_refresh"],
    }


# -- Claude Code -------------------------------------------------------------------------


def test_claude_code_signed_in_reports_the_subscription_and_organisation(home):
    home.write(".claude.json", claude_account())
    assert home.detector().claude_code() == Finding(
        AuthMethod.SUBSCRIPTION, "Example Studio (synthetic)"
    )


def test_claude_code_account_label_falls_back_to_the_address(home):
    account = claude_account()
    del account["oauthAccount"]["organizationName"]
    home.write(".claude.json", account)
    assert home.detector().claude_code() == Finding(AuthMethod.SUBSCRIPTION, "dev@example.test")


@pytest.mark.parametrize(
    "settings",
    [
        {"env": {"ANTHROPIC_API_KEY": SECRETS["claude_env_key"]}},
        {"env": {"ANTHROPIC_AUTH_TOKEN": SECRETS["claude_env_key"]}},
        {"apiKeyHelper": "/usr/local/bin/print-a-key"},
    ],
)
def test_claude_code_api_key_outranks_the_account(home, settings):
    home.write(".claude.json", claude_account())
    home.write(".claude/settings.json", settings)
    assert home.detector().claude_code() == Finding(AuthMethod.API_KEY)


def test_a_stored_console_key_is_an_api_key(home):
    home.write(".claude.json", {"primaryApiKey": SECRETS["claude_console_key"]})
    assert home.detector().claude_code() == Finding(AuthMethod.API_KEY)


def test_claude_code_custom_endpoint(home):
    home.write(".claude.json", claude_account())
    home.write(
        ".claude/settings.json",
        {"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:4000", "ANTHROPIC_API_KEY": "x" * 40}},
    )
    assert home.detector().claude_code() == Finding(AuthMethod.CUSTOM_ENDPOINT)


@pytest.mark.parametrize(
    ("variable", "setting", "expected"),
    [
        ("CLAUDE_CODE_USE_BEDROCK", "1", AuthMethod.CLOUD_PROVIDER),
        ("CLAUDE_CODE_USE_VERTEX", "true", AuthMethod.CLOUD_PROVIDER),
        ("CLAUDE_CODE_USE_BEDROCK", "0", AuthMethod.SUBSCRIPTION),
    ],
)
def test_claude_code_cloud_providers(home, variable, setting, expected):
    home.write(".claude.json", claude_account())
    home.write(".claude/settings.json", {"env": {variable: setting}})
    assert home.detector().claude_code().method is expected


def test_nothing_to_go_on_is_unknown(home):
    assert home.detector().claude_code() == Finding(None)
    home.write(".claude.json", {"theme": "dark"})
    assert home.detector().claude_code() == Finding(None)


def test_a_malformed_settings_file_is_unknown_and_quiet_about_its_content(home, caplog):
    home.write(".claude.json", '{"primaryApiKey": "' + SECRETS["claude_console_key"] + '", ')
    with caplog.at_level(logging.WARNING):
        assert home.detector().claude_code() == Finding(None)
    assert SECRETS["claude_console_key"] not in caplog.text


# -- Codex ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (
            {"auth_mode": "chatgpt", "tokens": {"access_token": SECRETS["codex_access"]}},
            AuthMethod.SUBSCRIPTION,
        ),
        ({"auth_mode": "apikey", "OPENAI_API_KEY": SECRETS["codex_key"]}, AuthMethod.API_KEY),
        ({"OPENAI_API_KEY": SECRETS["codex_key"]}, AuthMethod.API_KEY),
        (
            {"OPENAI_API_KEY": None, "tokens": {"refresh_token": SECRETS["codex_refresh"]}},
            AuthMethod.SUBSCRIPTION,
        ),
        ({"last_refresh": "2026-11-01T00:00:00Z"}, None),
    ],
)
def test_codex_auth_mode(home, document, expected):
    home.write(".codex/auth.json", document)
    assert home.detector().codex() == Finding(expected)


def codex_store_with_provider(store, tmp_path, provider: str) -> Session:
    sessions, _ = store
    source = sorted(CODEX_FIXTURES.rglob("rollout-*.jsonl"))[0]
    lines = source.read_text().splitlines()
    header = json.loads(lines[0])
    assert header["type"] == "session_meta"
    header["payload"]["model_provider"] = provider
    root = tmp_path / "codex"
    target = root / "sessions" / "2026" / "11" / "17" / source.name
    target.parent.mkdir(parents=True)
    target.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n")
    access = ReadOnlyAccess([root], snapshot_dir=tmp_path / "snapshots")
    Ingestor(access, SqlLedger(sessions, codex.Writer()), harness=codex.HARNESS).scan(
        discover_files(access, [root], codex.PATTERNS)
    )
    with sessions() as db:
        return db.scalars(select(Session).where(Session.native_id == header["payload"]["id"])).one()


def test_a_codex_session_on_another_provider_is_a_custom_endpoint(store, tmp_path):
    session = codex_store_with_provider(store, tmp_path, "local-llama")
    assert session.auth_method == AuthMethod.CUSTOM_ENDPOINT.value
    assert session.auth_basis is AuthBasis.TRANSCRIPT


def test_a_codex_session_on_openai_leaves_the_method_to_the_settings(store, tmp_path):
    session = codex_store_with_provider(store, tmp_path, "openai")
    assert session.auth_method is None and session.auth_basis is None


# -- Antigravity ---------------------------------------------------------------------------


def test_antigravity_reports_the_active_google_account(home):
    home.write(".gemini/google_accounts.json", {"active": "dev@example.test", "old": []})
    assert home.detector().antigravity() == Finding(AuthMethod.SUBSCRIPTION, "dev@example.test")


def test_antigravity_without_an_active_account_is_unknown(home):
    home.write(".gemini/google_accounts.json", {"active": None, "old": ["a@example.test"]})
    assert home.detector().antigravity() == Finding(None)


# -- Pi ------------------------------------------------------------------------------------


def pi_files(home: Home) -> None:
    home.write(
        ".pi/agent/models.json",
        {
            "providers": {
                "workbench": {
                    "baseUrl": "http://127.0.0.1:8080/v1",
                    "apiKey": SECRETS["pi_endpoint_key"],
                    "models": [{"id": "local-model"}],
                }
            }
        },
    )
    home.write(
        ".pi/agent/auth.json",
        {
            "anthropic": {
                "type": "oauth",
                "access": SECRETS["pi_access"],
                "refresh": SECRETS["pi_refresh"],
                "expires": 1795000000000,
            },
            "openrouter": {"type": "api_key", "key": SECRETS["pi_key"]},
        },
    )


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("workbench", AuthMethod.CUSTOM_ENDPOINT),
        ("anthropic", AuthMethod.SUBSCRIPTION),
        ("openrouter", AuthMethod.API_KEY),
        ("groq", None),
        (None, None),
        ("token", None),  # named like a secret: nothing may be read about it
    ],
)
def test_pi_provider_lookup(home, provider, expected):
    pi_files(home)
    assert home.detector().pi(provider) == Finding(expected)


# -- applying to sessions -------------------------------------------------------------------


def add_session(db, harness, native_id, *, active_at, state=SessionState.UNKNOWN, provider=None):
    session = Session(
        harness=harness.value, native_id=native_id, last_activity_at=active_at, state=state
    )
    db.add(session)
    db.flush()
    if provider:
        db.add(
            Event(
                session_id=session.id,
                sequence=0,
                kind=EventKind.MODEL_CHANGE,
                payload={"model": "local-model", "extra": {"provider": provider}},
                occurred_at=active_at,
            )
        )
    return session


def everything(home: Home) -> None:
    home.write(".claude.json", {**claude_account(), "primaryApiKey": None})
    home.write(
        ".codex/auth.json",
        {
            "auth_mode": "chatgpt",
            "OPENAI_API_KEY": None,
            "tokens": {
                "access_token": SECRETS["codex_access"],
                "refresh_token": SECRETS["codex_refresh"],
                "id_token": SECRETS["codex_id"],
            },
        },
    )
    home.write(
        ".gemini/google_accounts.json",
        {"active": "dev@example.test", "refresh": SECRETS["google_refresh"]},
    )
    home.write(
        ".claude/settings.json", {"theme": "dark", "env": {"OTHER": SECRETS["claude_env_key"]}}
    )
    pi_files(home)


def test_settings_apply_only_to_sessions_active_now(home, store):
    everything(home)
    sessions, _ = store
    with sessions() as db:
        live = add_session(
            db,
            Harness.CLAUDE_CODE,
            "live",
            active_at=NOW - timedelta(hours=5),
            state=SessionState.WAITING,
        )
        recent = add_session(db, Harness.CODEX, "recent", active_at=NOW - timedelta(minutes=5))
        old = add_session(
            db,
            Harness.CLAUDE_CODE,
            "old",
            active_at=NOW - timedelta(days=3),
            state=SessionState.ENDED,
        )
        proven = add_session(db, Harness.CODEX, "proven", active_at=NOW)
        proven.auth_method, proven.auth_basis = "custom_endpoint", AuthBasis.TRANSCRIPT
        pi = add_session(db, Harness.PI, "pi", active_at=NOW, provider="workbench")
        google = add_session(db, Harness.ANTIGRAVITY, "agy", active_at=NOW)
        db.commit()

        assert home.detector().refresh(db) == 4
        db.commit()
        assert (live.auth_method, live.account_label, live.auth_basis) == (
            "subscription",
            "Example Studio (synthetic)",
            AuthBasis.CONFIG,
        )
        assert (recent.auth_method, recent.account_label) == ("subscription", None)
        assert old.auth_method is None  # its settings at the time are not known
        assert (proven.auth_method, proven.auth_basis) == ("custom_endpoint", AuthBasis.TRANSCRIPT)
        assert pi.auth_method == "custom_endpoint"
        assert (google.auth_method, google.account_label) == ("subscription", "dev@example.test")
        assert home.detector().refresh(db) == 0  # a method, once known, is kept


def test_no_token_key_or_refresh_value_reaches_stored_data(home, store, caplog):
    everything(home)
    sessions, path = store
    with sessions() as db:
        for harness in (Harness.CLAUDE_CODE, Harness.CODEX, Harness.ANTIGRAVITY):
            add_session(db, harness, harness.value, active_at=NOW)
        for provider in ("workbench", "anthropic", "openrouter"):
            add_session(db, Harness.PI, provider, active_at=NOW, provider=provider)
        db.commit()
        with caplog.at_level(logging.DEBUG):
            assert home.detector().refresh(db) == 6
        db.commit()
        stored = [(s.auth_method, s.account_label) for s in db.scalars(select(Session))]
    assert {method for method, _ in stored} == {"subscription", "api_key", "custom_endpoint"}
    raw = path.read_bytes()
    wal = path.with_name(path.name + "-wal")
    raw += wal.read_bytes() if wal.exists() else b""
    for name, secret in SECRETS.items():
        assert secret.encode() not in raw, name
        assert secret not in caplog.text, name
        assert all(secret not in str(row) for row in stored), name
