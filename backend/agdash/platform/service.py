"""Install, remove and query agdash's own background service.

A LaunchAgent on macOS, a systemd user unit on Linux. Both run as the
logged-in user, start at login and restart after a crash. Every command these
managers run names agdash's own label or unit and nothing else.
"""

import os
import plistlib
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from agdash.platform.roots import is_inside

LAUNCHD_LABEL = "dev.agdash.agdashd"
SYSTEMD_UNIT = "agdashd.service"
DESCRIPTION = "agdash: read-only dashboard service for agentic coding sessions"
COMMAND_TIMEOUT_SECONDS = 30

Runner = Callable[..., subprocess.CompletedProcess[str]]


class ServiceError(Exception):
    """The service manager refused or failed an operation."""


class ServiceState(StrEnum):
    NOT_INSTALLED = "not_installed"
    INSTALLED = "installed"
    RUNNING = "running"


@dataclass(frozen=True)
class ServiceDefinition:
    """What the service runs. ``program`` is the full command line, first item absolute."""

    program: tuple[str, ...]
    log_dir: Path
    environment: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.program or not Path(self.program[0]).is_absolute():
            raise ValueError("the service program must start with an absolute path")
        if not self.log_dir.is_absolute():
            raise ValueError("the log directory must be absolute")
        for name in self.environment:
            if not name.replace("_", "").isalnum() or name[:1].isdigit():
                raise ValueError(f"not an environment variable name: {name!r}")


class ServiceManager(Protocol):
    unit_path: Path

    def render(self, definition: ServiceDefinition) -> str: ...

    def install(self, definition: ServiceDefinition) -> None: ...

    def uninstall(self) -> None: ...

    def state(self) -> ServiceState: ...


def _write_private(path: Path, text: str, forbidden_roots: tuple[Path, ...]) -> None:
    """Replace ``path`` atomically with ``text``, mode 0644."""
    if is_inside(path, forbidden_roots):
        raise ServiceError(f"refusing to write inside a harness directory: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(text)
        os.replace(staging, path)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise


class _Base:
    unit_path: Path

    def __init__(self, *, run: Runner, forbidden_roots: tuple[Path, ...]) -> None:
        self._run = run
        self._forbidden_roots = forbidden_roots

    def _call(self, command: Sequence[str], *, check: bool = True) -> int:
        try:
            result = self._run(
                list(command),
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ServiceError(f"could not run {command[0]}") from error
        if check and result.returncode != 0:
            # The command's output names only agdash's own service; it holds no secret.
            raise ServiceError(f"{' '.join(command)} failed: {result.stderr.strip()[:300]}")
        return result.returncode


class LaunchdManager(_Base):
    """A LaunchAgent in the user's ``~/Library/LaunchAgents``."""

    def __init__(
        self,
        *,
        home: Path,
        uid: int,
        run: Runner = subprocess.run,
        forbidden_roots: tuple[Path, ...] = (),
    ) -> None:
        super().__init__(run=run, forbidden_roots=forbidden_roots)
        self.unit_path = home / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        self._domain = f"gui/{uid}"

    def render(self, definition: ServiceDefinition) -> str:
        document: dict = {
            "Label": LAUNCHD_LABEL,
            "ProgramArguments": list(definition.program),
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ProcessType": "Background",
            "StandardOutPath": str(definition.log_dir / "agdashd.out.log"),
            "StandardErrorPath": str(definition.log_dir / "agdashd.err.log"),
        }
        if definition.environment:
            document["EnvironmentVariables"] = dict(definition.environment)
        return plistlib.dumps(document, sort_keys=True).decode()

    def install(self, definition: ServiceDefinition) -> None:
        if self.state() is not ServiceState.NOT_INSTALLED:
            self.uninstall()
        definition.log_dir.mkdir(parents=True, exist_ok=True)
        _write_private(self.unit_path, self.render(definition), self._forbidden_roots)
        self._call(["launchctl", "bootstrap", self._domain, str(self.unit_path)])

    def uninstall(self) -> None:
        # Not loaded is not an error when removing.
        self._call(["launchctl", "bootout", f"{self._domain}/{LAUNCHD_LABEL}"], check=False)
        self.unit_path.unlink(missing_ok=True)

    def state(self) -> ServiceState:
        if not self.unit_path.exists():
            return ServiceState.NOT_INSTALLED
        loaded = self._call(["launchctl", "print", f"{self._domain}/{LAUNCHD_LABEL}"], check=False)
        return ServiceState.RUNNING if loaded == 0 else ServiceState.INSTALLED


def systemd_quote(argument: str) -> str:
    """One ``ExecStart`` word, quoted so systemd reads it back unchanged."""
    escaped = (
        argument.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("%", "%%")
        .replace("$", "$$")
        .replace("\n", "\\n")
    )
    return f'"{escaped}"'


class SystemdUserManager(_Base):
    """A systemd user unit in ``$XDG_CONFIG_HOME/systemd/user``."""

    def __init__(
        self,
        *,
        config_dir: Path,
        run: Runner = subprocess.run,
        forbidden_roots: tuple[Path, ...] = (),
    ) -> None:
        super().__init__(run=run, forbidden_roots=forbidden_roots)
        self.unit_path = config_dir / "systemd" / "user" / SYSTEMD_UNIT

    def render(self, definition: ServiceDefinition) -> str:
        lines = [
            "[Unit]",
            f"Description={DESCRIPTION}",
            "",
            "[Service]",
            "Type=simple",
            "ExecStart=" + " ".join(systemd_quote(word) for word in definition.program),
            "Restart=on-failure",
            "RestartSec=5",
        ]
        lines += [
            "Environment=" + systemd_quote(f"{name}={value}")
            for name, value in sorted(definition.environment.items())
        ]
        lines += ["", "[Install]", "WantedBy=default.target", ""]
        return "\n".join(lines)

    def install(self, definition: ServiceDefinition) -> None:
        definition.log_dir.mkdir(parents=True, exist_ok=True)
        _write_private(self.unit_path, self.render(definition), self._forbidden_roots)
        self._call(["systemctl", "--user", "daemon-reload"])
        self._call(["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT])

    def uninstall(self) -> None:
        self._call(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT], check=False)
        self.unit_path.unlink(missing_ok=True)
        self._call(["systemctl", "--user", "daemon-reload"], check=False)

    def state(self) -> ServiceState:
        if not self.unit_path.exists():
            return ServiceState.NOT_INSTALLED
        active = self._call(
            ["systemctl", "--user", "is-active", "--quiet", SYSTEMD_UNIT], check=False
        )
        return ServiceState.RUNNING if active == 0 else ServiceState.INSTALLED
