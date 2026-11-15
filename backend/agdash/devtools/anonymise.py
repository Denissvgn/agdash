"""Replace the content of harness records while keeping their structure.

Keys, record types, nesting, numbers and booleans stay. Every string that is
not a known structural value is replaced by something of the same shape:
identifiers consistently (so references between records still line up),
timestamps shifted by one fixed offset (so durations survive), paths and URLs
rebuilt from neutral words, blobs re-drawn from their own alphabet, and free
text rewritten from a fixed word list.

Every choice is derived from a keyed hash of the original value. The same salt
and input give the same output; without the salt the output cannot be mapped
back to the input.
"""

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from agdash.normalise import parse_timestamp
from agdash.redaction import redact_text

LEXICON = (
    "amber anchor apple arch atlas autumn badge banner basil beacon berry birch bison blossom "
    "bluff boulder breeze brook cabin canal candle canyon cedar chalk chapel cherry cinder "
    "citrus clover cobalt comet copper coral cotton crane crater creek crystal cypress dawn "
    "delta desert dove dune eagle ember falcon fern field fjord flint forest fossil garnet "
    "geyser ginger glacier granite grove harbor hazel heron hollow honey horizon iris island "
    "ivory jasmine juniper kestrel lagoon lantern laurel lemon lichen lily linen lotus maple "
    "marble meadow mesa mint mist moss nectar nutmeg oak oasis olive onyx orchard otter "
    "pebble pepper pine plume poppy prairie quartz quill raven reed ridge river robin saffron "
    "sage sapphire shore sierra slate sparrow spruce stone summit swan thistle thunder timber "
    "topaz tulip tundra valley velvet violet walnut willow winter wren yarrow zephyr"
).split()

# Values kept as they are when they sit under one of these keys and look like an enum.
ENUM_KEYS = frozenset(
    """type role subtype kind event event_type status state stop_reason stopReason rawStopReason
    finish_reason level entrypoint permissionMode mode sandbox approval_policy
    approvals_reviewer effort perTurnEffort reasoning_effort thinkingLevel thinkingDisplay
    provider api model modelId requestedModel advisorModel model_provider originator source
    thread_source cli_version version encoding mime_type media_type format phase decision
    reasonType reasonCode collaboration_mode_kind history_mode querySource promptSource
    renderedRole userType operation toolDenialKind rateLimitType overageStatus
    overageDisabledReason agent_name agentType subagent_type
    """.split()
)
TOOL_KEYS = frozenset({"toolName", "tool_name", "recipient", "tool"})
TOOL_TYPES = frozenset(
    {
        "tool_use",
        "server_tool_use",
        "mcp_tool_use",
        "function_call",
        "custom_tool_call",
        "toolCall",
        "tool_call",
    }
)
EPOCH_KEY = re.compile(r"(_at|_ms|[Tt]ime|timestamp|Timestamp|resetsAt)$")

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_ENUM_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+@-]{0,79}")
_MODEL_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+@/-]{0,119}")
_MIME_VALUE = re.compile(r"[a-z]+/[a-z0-9.+-]{1,60}")
MIME_KEYS = frozenset({"mime_type", "media_type", "mimeType", "mediaType"})
_SAFE_KEY = re.compile(r"[A-Za-z_@$][A-Za-z0-9_.:@$-]{0,63}")
_PREFIXED_ID = re.compile(r"([a-z]{2,10}[_-])([A-Za-z0-9]{8,})")
_BLOB = re.compile(r"[A-Za-z0-9+/=_-]{24,}")
_HEX = re.compile(r"[0-9a-f]{16,}")
_DIGITS = re.compile(r"\d{4,}")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
_URL = re.compile(r"[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_PATH = re.compile(r"(~|/|[A-Za-z]:\\)\S*")
_WORD = re.compile(r"\S+")

MAX_WORDS = 60
MAX_LINES = 12
MAX_BLOB = 256
EARLIEST = datetime(2000, 1, 1, tzinfo=UTC)


class Anonymiser:
    def __init__(self, salt: bytes) -> None:
        if len(salt) < 16:
            raise ValueError("the salt must be at least 16 bytes")
        self._salt = salt
        digest = self._digest("shift")
        # Between about one and two years back, plus a sub-day offset: dates no
        # longer say when the work happened, while every duration stays exact.
        self._shift = timedelta(
            days=365 + ((digest[0] << 8) | digest[1]) % 365, seconds=digest[2] * 337
        )

    # -- primitives ----------------------------------------------------------------

    def _digest(self, *parts: str) -> bytes:
        return hmac.new(self._salt, "\x00".join(parts).encode(), hashlib.sha256).digest()

    def _stream(self, *parts: str):
        counter = 0
        while True:
            yield from self._digest(*parts, str(counter))
            counter += 1

    def _draw(self, alphabet: str, length: int, *parts: str) -> str:
        stream = self._stream(*parts)
        return "".join(alphabet[next(stream) % len(alphabet)] for _ in range(length))

    def pick(self, modulus: int, *parts: str) -> int:
        """A number in ``range(modulus)`` chosen by the keyed hash of ``parts``."""
        return int.from_bytes(self._digest(*parts)[:8], "big") % modulus

    def bytes_like(self, original: bytes, limit: int = MAX_BLOB) -> bytes:
        stream = self._stream("bytes", original.hex())
        return bytes(next(stream) for _ in range(min(len(original), limit)))

    def word(self, *parts: str) -> str:
        return LEXICON[int.from_bytes(self._digest(*parts)[:4], "big") % len(LEXICON)]

    def uuid(self, original: str) -> str:
        raw = bytearray(self._digest("uuid", original.lower())[:16])
        raw[6] = (raw[6] & 0x0F) | 0x40
        raw[8] = (raw[8] & 0x3F) | 0x80
        h = raw.hex()
        replacement = f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"
        return replacement.upper() if original.isupper() else replacement

    def instant(self, moment: datetime) -> datetime:
        shifted = moment - self._shift
        return max(shifted, EARLIEST)

    def number(self, key: str, value: int | float) -> int | float:
        """Epoch-like numbers under time keys are shifted like timestamps; others stay."""
        if not EPOCH_KEY.search(key) or isinstance(value, bool):
            return value
        seconds = self._shift.total_seconds()
        if 1.0e9 <= value <= 4.1e9:
            return type(value)(value - seconds)
        if 1.0e12 <= value <= 4.1e12:
            return type(value)(value - seconds * 1000)
        return value

    def timestamp_text(self, text: str) -> str | None:
        moment = parse_timestamp(text)
        if moment is None or not any(c in text for c in "-:T"):
            return None
        shifted = self.instant(moment)
        if text.endswith("Z") and "." in text:
            fraction = len(text.rsplit(".", 1)[1]) - 1
            return shifted.strftime("%Y-%m-%dT%H:%M:%S.%f")[: 20 + fraction].rstrip(".") + "Z"
        if text.endswith("Z"):
            return shifted.strftime("%Y-%m-%dT%H:%M:%SZ")
        return shifted.isoformat()

    def path(self, original: str) -> str:
        """A neutral path with the same depth and file extension."""
        if original.startswith("file://"):
            return "file://" + self.path(original[7:])
        separator = "\\" if "\\" in original and "/" not in original else "/"
        parts = [p for p in original.split(separator) if p]
        new = []
        for index, part in enumerate(parts):
            stem, dot, extension = part.rpartition(".")
            name = self.word("path", part)
            if _UUID.fullmatch(stem or part):
                name = self.uuid(stem or part)
            if (
                index == len(parts) - 1
                and dot
                and stem
                and len(extension) <= 6
                and extension.isalnum()
            ):
                name = f"{name}.{extension}"
            new.append(name)
        return "/work/" + "/".join(new[1:]) if new else "/work"

    def text(self, original: str) -> str:
        """Neutral words in place of free text, with a similar word and line count."""
        words = _WORD.findall(original)
        if not words:
            return original
        lines = min(original.count("\n") + 1, MAX_LINES)
        count = min(len(words), MAX_WORDS)
        chosen = [self.word("text", original, str(i)) for i in range(count)]
        per_line = max(1, -(-count // lines))
        return "\n".join(" ".join(chosen[i : i + per_line]) for i in range(0, count, per_line))

    def blob(self, original: str) -> str:
        if _HEX.fullmatch(original):
            alphabet = "0123456789abcdef"
        elif re.fullmatch(r"[A-Za-z0-9_-]+", original):
            alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        else:
            alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        return self._draw(alphabet, min(len(original), MAX_BLOB), "blob", original)

    # -- strings by context --------------------------------------------------------

    def string(self, value: str, key: str = "", parent: dict | None = None) -> str:
        if not value:
            return value
        if _UUID.fullmatch(value):
            return self.uuid(value)
        shifted = self.timestamp_text(value) if len(value) <= 40 else None
        if shifted is not None:
            return shifted
        if self._keep(value, key, parent):
            return value
        stripped = value.strip()
        if stripped[:1] in "{[" and stripped[-1:] in "}]":
            try:
                parsed = json.loads(stripped)
            except ValueError:
                pass
            else:
                return json.dumps(self.value(parsed), ensure_ascii=False, separators=(",", ":"))
        if value.startswith("file://"):
            return self.path(value)
        if _EMAIL.fullmatch(value):
            return f"{self.word('email', value)}@example.test"
        if _URL.fullmatch(value):
            return f"https://example.test/{self.word('url', value)}/{self.word('url2', value)}"
        if _PATH.fullmatch(value) and ("/" in value or "\\" in value):
            return self.path(value)
        if _DIGITS.fullmatch(value):
            return self._draw("0123456789", len(value), "digits", value)
        prefixed = _PREFIXED_ID.fullmatch(value)
        if prefixed:
            return prefixed.group(1) + self.blob(prefixed.group(2))
        if _BLOB.fullmatch(value) and not value.isalpha():
            return self.blob(value)
        return self.text(value)

    def _keep(self, value: str, key: str, parent: dict | None) -> bool:
        if redact_text(value) != value:
            return False
        if key in ("model", "modelId", "requestedModel", "advisorModel") and _MODEL_VALUE.fullmatch(
            value
        ):
            return not value.startswith("/")
        if key in MIME_KEYS and _MIME_VALUE.fullmatch(value):
            return True
        if key in ENUM_KEYS and _ENUM_VALUE.fullmatch(value):
            return True
        is_tool_name = key in TOOL_KEYS or (
            key == "name" and parent is not None and parent.get("type") in TOOL_TYPES
        )
        return is_tool_name and _ENUM_VALUE.fullmatch(value) is not None

    # -- structures ----------------------------------------------------------------

    def key(self, key: str) -> str:
        if _SAFE_KEY.fullmatch(key) and redact_text(key) == key and not _UUID.fullmatch(key):
            return key
        return self.string(key)

    def value(self, value: Any, key: str = "", parent: dict | None = None) -> Any:
        if isinstance(value, dict):
            return {self.key(k): self.value(v, k, value) for k, v in value.items()}
        if isinstance(value, list):
            return [self.value(item, key, parent) for item in value]
        if isinstance(value, str):
            return self.string(value, key, parent)
        if isinstance(value, int | float):
            return self.number(key, value)
        return value
