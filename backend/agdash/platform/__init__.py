"""Everything that differs between macOS and Linux.

This package is the only code that may ask which operating system it runs on;
a lint rule refuses such a question anywhere else. Adapters, the API and the
collector are handed a :class:`Host` and use what it gives them.
"""

import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from agdash.platform import roots
from agdash.platform.probes import ProcessProbe
from agdash.platform.secrets import SecretStore, choose_secret_store
from agdash.platform.service import (
    LaunchdManager,
    ServiceDefinition,
    ServiceManager,
    SystemdUserManager,
)
from agdash.platform.usage import LinuxUsageReader, MacUsageReader, UsageReader
from agdash.vocab import Harness


class OperatingSystem(StrEnum):
    MACOS = "macos"
    LINUX = "linux"


class UnsupportedOperatingSystem(RuntimeError):
    pass


def detect_os(platform_name: str | None = None) -> OperatingSystem:
    name = sys.platform if platform_name is None else platform_name
    if name == "darwin":
        return OperatingSystem.MACOS
    if name.startswith("linux"):
        return OperatingSystem.LINUX
    raise UnsupportedOperatingSystem(f"agdash supports macOS and Linux, not {name}")


@dataclass(frozen=True)
class Host:
    """This machine as agdash sees it. Build one with :func:`current_host`."""

    os: OperatingSystem
    home: Path
    env: Mapping[str, str]

    def data_dir(self) -> Path:
        if self.os is OperatingSystem.MACOS:
            return roots.macos_data_dir(self.home, self.env)
        return roots.linux_data_dir(self.home, self.env)

    def default_database_path(self) -> Path:
        return self.data_dir() / "agdash.db"

    def log_dir(self) -> Path:
        if self.os is OperatingSystem.MACOS:
            return self.home / "Library" / "Logs" / roots.APP_NAME
        return self.data_dir() / "logs"

    def harness_roots(self) -> dict[Harness, tuple[Path, ...]]:
        return roots.harness_roots(self.home, self.env)

    def all_harness_roots(self) -> tuple[Path, ...]:
        return roots.all_harness_roots(self.home, self.env)

    def secret_store(self, **options) -> SecretStore:
        return choose_secret_store(
            data_dir=self.data_dir(),
            env=self.env,
            needs_session_bus=self.os is OperatingSystem.LINUX,
            forbidden_roots=self.all_harness_roots(),
            **options,
        )

    def process_probe(self, **options) -> ProcessProbe:
        return ProcessProbe(lsof_fallback=self.os is OperatingSystem.MACOS, **options)

    def usage_reader(self, **options) -> UsageReader:
        """Reads what this process itself uses; it is asked about no other."""
        if self.os is OperatingSystem.MACOS:
            return MacUsageReader(**options)
        return LinuxUsageReader(**options)

    def service_manager(self, *, uid: int | None = None, **options) -> ServiceManager:
        forbidden = self.all_harness_roots()
        if self.os is OperatingSystem.MACOS:
            return LaunchdManager(
                home=self.home,
                uid=os.getuid() if uid is None else uid,
                forbidden_roots=forbidden,
                **options,
            )
        return SystemdUserManager(
            config_dir=roots.linux_config_dir(self.home, self.env),
            forbidden_roots=forbidden,
            **options,
        )

    def service_definition(self, program: Sequence[str]) -> ServiceDefinition:
        return ServiceDefinition(program=tuple(program), log_dir=self.log_dir())


def current_host() -> Host:
    return Host(detect_os(), Path.home(), dict(os.environ))


__all__ = ["Host", "OperatingSystem", "UnsupportedOperatingSystem", "current_host", "detect_os"]
