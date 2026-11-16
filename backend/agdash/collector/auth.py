"""How each harness signs in, read from allowlisted, non-secret fields only.

Every read goes through :mod:`agdash.access.config`, which returns only the
fields named here. A field that holds a secret (a key, a token, a refresh
value) is only ever asked about by *presence*; its value is never decoded.

What each harness's settings can show:

* **Claude Code** — ``~/.claude.json``: the signed-in account's billing type,
  organisation and e-mail (``oauthAccount``) and whether a console API key is
  stored. ``~/.claude/settings.json``: whether its ``env`` block switches to
  Amazon Bedrock or Google Vertex AI, sets another base URL, or provides an API
  key, auth token or key helper. Claude Code's own order applies: cloud
  provider, then custom endpoint, then API key, then the signed-in account.
* **Codex** — ``auth.json``: ``auth_mode`` and whether an API key or sign-in
  tokens are stored. The account's e-mail lives only inside a token, so Codex
  has no account label. A session whose own header names another provider
  than OpenAI is a custom endpoint; the adapter records that from the
  transcript.
* **Antigravity** — ``google_accounts.json``: the active Google account, which
  is the account label. Antigravity signs in with Google only.
* **Pi** — the session's provider, from its transcript, looked up in
  ``models.json`` (a ``baseUrl`` means a custom endpoint) and ``auth.json``
  (the stored entry's ``type``: ``oauth`` or ``api_key``).

Settings say how the harness signs in *now*, so they are applied only to
sessions active while agdash sees them, with the basis ``config``. Older
sessions keep an unknown method unless their own records say otherwise. A key
set only in the agent's shell environment is invisible here.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session as DbSession

from agdash.access.config import ConfigFormatError, Field, presence, read_json_fields, value
from agdash.access.readonly import AccessError, ReadOnlyAccess
from agdash.db.models import Event, Session
from agdash.vocab import AuthBasis, AuthMethod, EventKind, Harness, SessionState

log = logging.getLogger("agdash.auth")

# Sessions active this recently, or in a live state, take what the settings say now.
RECENT = timedelta(minutes=30)
LIVE = (SessionState.ACTIVE, SessionState.WAITING, SessionState.IDLE)
TRUE = {"1", "true", "yes", "on"}
MAX_PROVIDER = 100


@dataclass(frozen=True)
class Finding:
    method: AuthMethod | None
    account_label: str | None = None


UNKNOWN = Finding(None)


def _on(found: object) -> bool:
    return isinstance(found, bool) and found or str(found).strip().lower() in TRUE


class AuthDetector:
    def __init__(
        self,
        access: ReadOnlyAccess,
        roots: dict[Harness, tuple[Path, ...]],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._access = access
        self._roots = roots
        self._clock = clock

    # -- reading --------------------------------------------------------------------

    def _fields(self, path: Path | None, fields: list[Field]) -> dict:
        """The asked-for fields, or nothing when the file is absent or unreadable."""
        if path is None:
            return {}
        try:
            if not self._access.exists(path):
                return {}
            return read_json_fields(self._access, path, fields)
        except ConfigFormatError:
            log.warning("a harness settings file is not well-formed; its auth method is unknown")
        except (AccessError, OSError, ValueError):
            log.warning("could not read a harness settings file; its auth method is unknown")
        return {}

    def _file(self, harness: Harness, name: str) -> Path | None:
        for root in self._roots.get(harness, ()):
            if root.name == name:
                return root
        return None

    def _in_folder(self, harness: Harness, *parts: str) -> Path | None:
        for root in self._roots.get(harness, ()):
            if not root.suffix:
                return root.joinpath(*parts)
        return None

    # -- one detector per harness --------------------------------------------------

    def claude_code(self) -> Finding:
        settings = self._fields(
            self._in_folder(Harness.CLAUDE_CODE, "settings.json"),
            [
                value("env", "CLAUDE_CODE_USE_BEDROCK"),
                value("env", "CLAUDE_CODE_USE_VERTEX"),
                presence("env", "ANTHROPIC_BASE_URL"),
                presence("env", "ANTHROPIC_API_KEY"),
                presence("env", "ANTHROPIC_AUTH_TOKEN"),
                presence("apiKeyHelper"),
            ],
        )
        account = self._fields(
            self._file(Harness.CLAUDE_CODE, ".claude.json"),
            [
                value("oauthAccount", "billingType"),
                value("oauthAccount", "organizationName"),
                value("oauthAccount", "emailAddress"),
                presence("primaryApiKey"),
            ],
        )
        env = ("env",)
        if _on(settings.get((*env, "CLAUDE_CODE_USE_BEDROCK"))) or _on(
            settings.get((*env, "CLAUDE_CODE_USE_VERTEX"))
        ):
            return Finding(AuthMethod.CLOUD_PROVIDER)
        if settings.get((*env, "ANTHROPIC_BASE_URL")):
            return Finding(AuthMethod.CUSTOM_ENDPOINT)
        if (
            settings.get((*env, "ANTHROPIC_API_KEY"))
            or settings.get((*env, "ANTHROPIC_AUTH_TOKEN"))
            or settings.get(("apiKeyHelper",))
            or account.get(("primaryApiKey",))
        ):
            return Finding(AuthMethod.API_KEY)
        organisation = account.get(("oauthAccount", "organizationName"))
        email = account.get(("oauthAccount", "emailAddress"))
        if account.get(("oauthAccount", "billingType")) is not None or email:
            label = organisation if isinstance(organisation, str) and organisation else email
            return Finding(AuthMethod.SUBSCRIPTION, label if isinstance(label, str) else None)
        return UNKNOWN

    def codex(self) -> Finding:
        found = self._fields(
            self._in_folder(Harness.CODEX, "auth.json"),
            [value("auth_mode"), presence("OPENAI_API_KEY"), presence("tokens")],
        )
        mode = str(found.get(("auth_mode",)) or "").replace("_", "").lower()
        if mode == "apikey":
            return Finding(AuthMethod.API_KEY)
        if mode.startswith("chatgpt"):
            return Finding(AuthMethod.SUBSCRIPTION)
        if found.get(("OPENAI_API_KEY",)):
            return Finding(AuthMethod.API_KEY)
        if found.get(("tokens",)):
            return Finding(AuthMethod.SUBSCRIPTION)
        return UNKNOWN

    def antigravity(self) -> Finding:
        found = self._fields(
            self._file(Harness.ANTIGRAVITY, "google_accounts.json"), [value("active")]
        )
        active = found.get(("active",))
        if isinstance(active, str) and active:
            return Finding(AuthMethod.SUBSCRIPTION, active)
        return UNKNOWN

    def pi(self, provider: str | None) -> Finding:
        if not provider or len(provider) > MAX_PROVIDER:
            return UNKNOWN
        try:
            endpoint = [presence("providers", provider, "baseUrl")]
            stored = [value(provider, "type")]
        except ValueError:  # a provider named like a secret: nothing may be read about it
            return UNKNOWN
        if self._fields(self._in_folder(Harness.PI, "models.json"), endpoint).get(
            ("providers", provider, "baseUrl")
        ):
            return Finding(AuthMethod.CUSTOM_ENDPOINT)
        kind = str(
            self._fields(self._in_folder(Harness.PI, "auth.json"), stored).get((provider, "type"))
            or ""
        )
        if kind == "oauth":
            return Finding(AuthMethod.SUBSCRIPTION)
        if kind in ("api_key", "apiKey", "key"):
            return Finding(AuthMethod.API_KEY)
        return UNKNOWN

    # -- applying -------------------------------------------------------------------

    def refresh(self, db: DbSession) -> int:
        """Give sessions active now, whose method is unknown, what the settings say."""
        now = self._clock()
        candidates = db.scalars(
            select(Session).where(
                Session.auth_method.is_(None),
                or_(Session.state.in_(LIVE), Session.last_activity_at >= now - RECENT),
            )
        ).all()
        if not candidates:
            return 0
        per_harness: dict[str, Finding] = {}
        detectors = {
            Harness.CLAUDE_CODE.value: self.claude_code,
            Harness.CODEX.value: self.codex,
            Harness.ANTIGRAVITY.value: self.antigravity,
        }
        changed = 0
        for session in candidates:
            if session.harness == Harness.PI.value:
                finding = self.pi(_pi_provider(db, session))
            elif session.harness in detectors:
                if session.harness not in per_harness:
                    per_harness[session.harness] = detectors[session.harness]()
                finding = per_harness[session.harness]
            else:
                continue
            if finding.method is None:
                continue
            session.auth_method = finding.method.value
            session.account_label = finding.account_label[:255] if finding.account_label else None
            session.auth_basis = AuthBasis.CONFIG
            changed += 1
        return changed


def _pi_provider(db: DbSession, session: Session) -> str | None:
    payload = db.scalar(
        select(Event.payload)
        .where(Event.session_id == session.id, Event.kind == EventKind.MODEL_CHANGE)
        .order_by(Event.sequence.desc())
        .limit(1)
    )
    provider = ((payload or {}).get("extra") or {}).get("provider")
    return provider if isinstance(provider, str) else None
