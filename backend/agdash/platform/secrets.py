"""agdash's own secrets: its local API token and its tracker credentials.

Two stores with one interface. The system keyring is used where there is one:
the Keychain on macOS, the Secret Service on a Linux desktop. A Linux host with
no desktop session gets a file only its owner can read.

Both stores are confined to agdash's own entries. The keyring store always
uses the service name ``agdash``, so it cannot read what other programs keep
there, including a harness's credentials.
"""

import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from agdash.platform.roots import is_inside

SERVICE = "agdash"
SECRET_FILE_NAME = "secrets.json"
_NAME = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}")
_UNUSABLE_BACKENDS = ("keyring.backends.fail", "keyring.backends.null", "keyrings.alt")


class SecretStoreError(Exception):
    """A secret could not be stored or read safely. Never carries a secret value."""


class SecretStore(Protocol):
    kind: str

    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


def _checked_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("a secret name is lower-case letters, digits and . _ : -")
    return name


def _checked_value(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("a secret value is a non-empty string")
    return value


class KeyringStore:
    """Entries under the ``agdash`` service of a keyring backend."""

    kind = "keyring"

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def get(self, name: str) -> str | None:
        return self._backend.get_password(SERVICE, _checked_name(name))

    def set(self, name: str, value: str) -> None:
        self._backend.set_password(SERVICE, _checked_name(name), _checked_value(value))

    def delete(self, name: str) -> None:
        try:
            self._backend.delete_password(SERVICE, _checked_name(name))
        except Exception as error:  # keyring signals "not there" with its own error type
            if type(error).__name__ != "PasswordDeleteError":
                raise


class FileStore:
    """A JSON file readable and writable by its owner only.

    The directory is created with mode 0700 and the file with 0600. A file that
    others can read is refused rather than trusted, and so is a link.
    """

    kind = "file"

    def __init__(self, path: Path, *, forbidden_roots: tuple[Path, ...] = ()) -> None:
        if not path.is_absolute():
            raise ValueError("the secret file path must be absolute")
        if is_inside(path, forbidden_roots):
            raise SecretStoreError(f"refusing to keep secrets inside a harness directory: {path}")
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> dict[str, str]:
        try:
            descriptor = os.open(self._path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise SecretStoreError(f"cannot open the secret file safely: {self._path}") from error
        with os.fdopen(descriptor, "rb") as source:
            found = os.fstat(source.fileno())
            if not stat.S_ISREG(found.st_mode) or found.st_mode & 0o077:
                raise SecretStoreError(f"the secret file is readable by others: {self._path}")
            if found.st_uid != os.getuid():
                raise SecretStoreError(f"the secret file belongs to another user: {self._path}")
            raw = source.read()
        try:
            entries = json.loads(raw)
        except ValueError:
            raise SecretStoreError(f"the secret file is not valid JSON: {self._path}") from None
        if not isinstance(entries, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in entries.items()
        ):
            raise SecretStoreError(f"the secret file has an unexpected shape: {self._path}")
        return entries

    def _save(self, entries: dict[str, str]) -> None:
        directory = self._path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or stat.S_IMODE(directory.stat().st_mode) & 0o077:
            raise SecretStoreError(f"the secret directory is not private: {directory}")
        staging = directory / f".{self._path.name}.{os.getpid()}.tmp"
        descriptor = os.open(
            staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
        )
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(json.dumps(entries, sort_keys=True).encode())
                target.flush()
                os.fsync(target.fileno())
            os.replace(staging, self._path)
        except BaseException:
            staging.unlink(missing_ok=True)
            raise

    def get(self, name: str) -> str | None:
        return self._load().get(_checked_name(name))

    def set(self, name: str, value: str) -> None:
        entries = self._load()
        entries[_checked_name(name)] = _checked_value(value)
        self._save(entries)

    def delete(self, name: str) -> None:
        entries = self._load()
        if entries.pop(_checked_name(name), None) is not None:
            self._save(entries)


def keyring_is_usable(backend: Any, *, needs_session_bus: bool, env: Mapping[str, str]) -> bool:
    """Whether a keyring backend really stores secrets securely on this host.

    The "fail" and "null" backends store nothing, and the ``keyrings.alt``
    backends store secrets in files whose protection we cannot vouch for; in
    those cases agdash's own file is the better choice. The Secret Service needs
    a session bus, which a host without a desktop session does not have.
    """
    if backend is None:
        return False
    if needs_session_bus and not env.get("DBUS_SESSION_BUS_ADDRESS"):
        return False
    module = type(backend).__module__
    if module.startswith(_UNUSABLE_BACKENDS):
        return False
    inner = getattr(backend, "backends", None)
    if inner is not None:  # a chain is usable when one of its links is
        return any(
            keyring_is_usable(link, needs_session_bus=needs_session_bus, env=env) for link in inner
        )
    return True


def system_keyring() -> Any:
    import keyring

    return keyring.get_keyring()


def choose_secret_store(
    *,
    data_dir: Path,
    env: Mapping[str, str],
    needs_session_bus: bool,
    forbidden_roots: tuple[Path, ...],
    keyring_factory: Callable[[], Any] = system_keyring,
) -> SecretStore:
    """The keyring when it is usable, otherwise the private file in ``data_dir``."""
    # Checked before the backend is created: creating one can try to reach a bus.
    if not needs_session_bus or env.get("DBUS_SESSION_BUS_ADDRESS"):
        backend = keyring_factory()
        if keyring_is_usable(backend, needs_session_bus=needs_session_bus, env=env):
            return KeyringStore(backend)
    return FileStore(data_dir / SECRET_FILE_NAME, forbidden_roots=forbidden_roots)
