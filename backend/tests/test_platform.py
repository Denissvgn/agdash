"""The platform layer, tested for both operating systems on whichever one runs the tests.

No test here touches the real keyring, the real service manager or a harness
directory: each is replaced by a fake or a temporary directory.
"""

import ast
import os
import plistlib
import stat
import subprocess
import tomllib
from datetime import timedelta
from pathlib import Path

import psutil
import pytest

import agdash
from agdash.access import ReadOnlyAccess
from agdash.platform import (
    Host,
    OperatingSystem,
    UnsupportedOperatingSystem,
    current_host,
    detect_os,
)
from agdash.platform.probes import (
    LSOF,
    ProcessGone,
    ProcessIdentity,
    ProcessProbe,
    parse_lsof_regular_files,
)
from agdash.platform.secrets import (
    SERVICE,
    FileStore,
    KeyringStore,
    SecretStoreError,
    keyring_is_usable,
)
from agdash.platform.service import (
    LAUNCHD_LABEL,
    SYSTEMD_UNIT,
    LaunchdManager,
    ServiceDefinition,
    ServiceError,
    ServiceState,
    SystemdUserManager,
    systemd_quote,
)
from agdash.vocab import Harness

PACKAGE = Path(agdash.__file__).resolve().parent
HOME = Path("/home/someone")

# -- which operating system ---------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("darwin", OperatingSystem.MACOS),
        ("linux", OperatingSystem.LINUX),
        ("linux2", OperatingSystem.LINUX),
    ],
)
def test_supported_systems_are_recognised(name, expected):
    assert detect_os(name) is expected


def test_each_system_has_the_name_people_write():
    assert OperatingSystem.MACOS.label == "macOS"
    assert OperatingSystem.LINUX.label == "Linux"


@pytest.mark.parametrize("name", ["win32", "cygwin", "freebsd14", ""])
def test_other_systems_are_refused(name):
    with pytest.raises(UnsupportedOperatingSystem):
        detect_os(name)


def test_the_current_host_is_a_supported_system():
    host = current_host()
    assert host.os in set(OperatingSystem)
    assert host.home == Path.home()


# -- data and harness locations -----------------------------------------------------


def _host(system: OperatingSystem, home: Path = HOME, **env: str) -> Host:
    return Host(system, home, env)


@pytest.mark.parametrize(
    ("system", "env", "expected"),
    [
        (OperatingSystem.MACOS, {}, HOME / "Library/Application Support/agdash"),
        (OperatingSystem.LINUX, {}, HOME / ".local/share/agdash"),
        (OperatingSystem.LINUX, {"XDG_DATA_HOME": "/data/xdg"}, Path("/data/xdg/agdash")),
        (OperatingSystem.LINUX, {"XDG_DATA_HOME": "relative/xdg"}, HOME / ".local/share/agdash"),
        (
            OperatingSystem.MACOS,
            {"XDG_DATA_HOME": "/data/xdg"},
            HOME / "Library/Application Support/agdash",
        ),
        (OperatingSystem.MACOS, {"AGDASH_DATA_DIR": "/srv/agdash"}, Path("/srv/agdash")),
        (OperatingSystem.LINUX, {"AGDASH_DATA_DIR": "/srv/agdash"}, Path("/srv/agdash")),
        (OperatingSystem.LINUX, {"AGDASH_DATA_DIR": "srv"}, HOME / ".local/share/agdash"),
    ],
)
def test_data_directory(system, env, expected):
    host = _host(system, **env)
    assert host.data_dir() == expected
    assert host.default_database_path() == expected / "agdash.db"


def test_log_directory():
    assert _host(OperatingSystem.MACOS).log_dir() == HOME / "Library/Logs/agdash"
    assert _host(OperatingSystem.LINUX).log_dir() == HOME / ".local/share/agdash/logs"


@pytest.mark.parametrize("system", list(OperatingSystem))
def test_default_harness_roots(system):
    roots = _host(system).harness_roots()
    assert roots == {
        Harness.CLAUDE_CODE: (HOME / ".claude", HOME / ".claude.json"),
        Harness.CODEX: (HOME / ".codex",),
        Harness.ANTIGRAVITY: (
            HOME / ".gemini/antigravity",
            HOME / ".gemini/antigravity-cli",
            HOME / ".gemini/google_accounts.json",
        ),
        Harness.PI: (HOME / ".pi/agent",),
    }
    assert set(roots) == set(Harness)


def test_harness_roots_follow_absolute_overrides_only():
    moved = _host(OperatingSystem.LINUX, CLAUDE_CONFIG_DIR="/cfg/claude", CODEX_HOME="/cfg/codex")
    assert moved.harness_roots()[Harness.CLAUDE_CODE] == (
        Path("/cfg/claude"),
        Path("/cfg/claude/.claude.json"),
    )
    assert moved.harness_roots()[Harness.CODEX] == (Path("/cfg/codex"),)
    relative = _host(OperatingSystem.LINUX, CLAUDE_CONFIG_DIR="cfg", CODEX_HOME="")
    assert relative.harness_roots()[Harness.CLAUDE_CODE][0] == HOME / ".claude"
    assert relative.harness_roots()[Harness.CODEX] == (HOME / ".codex",)


def test_the_roots_are_accepted_by_the_access_layer(tmp_path):
    host = _host(OperatingSystem.LINUX, home=tmp_path)
    roots = host.all_harness_roots()
    assert all(root.is_absolute() for root in roots)
    access = ReadOnlyAccess(roots, snapshot_dir=tmp_path / "snapshots-elsewhere")
    assert access.exists(tmp_path / ".codex" / "sessions") is False
    assert not (tmp_path / ".codex").exists(), "discovery must not create a harness directory"


def test_agdash_data_never_lands_in_a_harness_root():
    for system in OperatingSystem:
        host = _host(system)
        for root in host.all_harness_roots():
            assert root not in host.data_dir().parents
            assert root != host.data_dir()


# -- secrets ------------------------------------------------------------------------


class FakeKeyring:
    """Stands in for a keyring backend and records every call."""

    def __init__(self, entries=None):
        self.entries = dict(entries or {})
        self.calls = []

    def get_password(self, service, name):
        self.calls.append(("get", service, name))
        return self.entries.get((service, name))

    def set_password(self, service, name, value):
        self.calls.append(("set", service, name))
        self.entries[(service, name)] = value

    def delete_password(self, service, name):
        self.calls.append(("delete", service, name))
        if (service, name) not in self.entries:
            raise type("PasswordDeleteError", (Exception,), {})("not found")
        del self.entries[(service, name)]


def test_the_keyring_store_only_touches_agdash_entries():
    backend = FakeKeyring({("Claude Code-credentials", "someone"): "harness secret"})
    store = KeyringStore(backend)
    store.set("local-token", "value-1")
    assert store.get("local-token") == "value-1"
    assert store.get("someone") is None
    store.delete("local-token")
    store.delete("local-token")
    assert {service for _, service, _ in backend.calls} == {SERVICE}
    assert backend.entries == {("Claude Code-credentials", "someone"): "harness secret"}


@pytest.mark.parametrize("name", ["", "Local-Token", "a/b", "../x", "x" * 200, "space here", None])
def test_secret_names_are_restricted(name):
    store = KeyringStore(FakeKeyring())
    with pytest.raises(ValueError):
        store.get(name)
    with pytest.raises(ValueError):
        FileStore(Path("/nonexistent/secrets.json")).set(name, "v")


def test_empty_secret_values_are_refused(tmp_path):
    with pytest.raises(ValueError):
        KeyringStore(FakeKeyring()).set("local-token", "")
    with pytest.raises(ValueError):
        FileStore(tmp_path / "s" / "secrets.json").set("local-token", "")


def test_the_file_store_keeps_secrets_private(tmp_path):
    path = tmp_path / "data" / "secrets.json"
    store = FileStore(path)
    assert store.get("local-token") is None
    store.set("local-token", "value-1")
    store.set("workchord:1", "value-2")
    assert store.get("local-token") == "value-1"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert sorted(os.listdir(path.parent)) == ["secrets.json"], "no staging file is left behind"
    store.delete("local-token")
    assert store.get("local-token") is None
    assert store.get("workchord:1") == "value-2"


def test_a_secret_file_others_can_read_is_refused(tmp_path):
    path = tmp_path / "data" / "secrets.json"
    FileStore(path).set("local-token", "value-1")
    os.chmod(path, 0o644)
    with pytest.raises(SecretStoreError, match="readable by others") as raised:
        FileStore(path).get("local-token")
    assert "value-1" not in str(raised.value)


def test_a_secret_file_that_is_a_link_is_refused(tmp_path):
    real = tmp_path / "elsewhere.json"
    real.write_text('{"local-token": "value-1"}')
    os.chmod(real, 0o600)
    path = tmp_path / "data" / "secrets.json"
    path.parent.mkdir(mode=0o700)
    path.symlink_to(real)
    with pytest.raises(SecretStoreError):
        FileStore(path).get("local-token")


def test_a_damaged_secret_file_is_reported_without_its_content(tmp_path):
    path = tmp_path / "data" / "secrets.json"
    path.parent.mkdir(mode=0o700)
    path.write_text('{"local-token": "value-1"')
    os.chmod(path, 0o600)
    with pytest.raises(SecretStoreError) as raised:
        FileStore(path).get("local-token")
    assert "value-1" not in str(raised.value)
    assert raised.value.__cause__ is None


def test_a_secret_directory_others_can_enter_is_refused(tmp_path):
    path = tmp_path / "data" / "secrets.json"
    path.parent.mkdir(mode=0o755)
    os.chmod(path.parent, 0o755)
    with pytest.raises(SecretStoreError, match="not private"):
        FileStore(path).set("local-token", "value-1")


def test_secrets_are_never_kept_inside_a_harness_root(tmp_path):
    harness = tmp_path / ".codex"
    with pytest.raises(SecretStoreError, match="harness directory"):
        FileStore(harness / "agdash" / "secrets.json", forbidden_roots=(harness,))
    assert not harness.exists()


def _backend(module: str, name: str = "Keyring", **attributes):
    return type(name, (), {"__module__": module, **attributes})()


@pytest.mark.parametrize(
    ("backend", "needs_bus", "env", "usable"),
    [
        (_backend("keyring.backends.macOS"), False, {}, True),
        (
            _backend("keyring.backends.SecretService"),
            True,
            {"DBUS_SESSION_BUS_ADDRESS": "unix:x"},
            True,
        ),
        (_backend("keyring.backends.SecretService"), True, {}, False),
        (_backend("keyring.backends.fail"), False, {}, False),
        (_backend("keyring.backends.null"), False, {}, False),
        (_backend("keyrings.alt.file", "PlaintextKeyring"), False, {}, False),
        (_backend("keyring.backends.chainer", "ChainerBackend", backends=[]), False, {}, False),
        (
            _backend(
                "keyring.backends.chainer",
                "ChainerBackend",
                backends=[_backend("keyring.backends.fail"), _backend("keyring.backends.macOS")],
            ),
            False,
            {},
            True,
        ),
        (None, False, {}, False),
    ],
)
def test_which_keyrings_count_as_usable(backend, needs_bus, env, usable):
    assert keyring_is_usable(backend, needs_session_bus=needs_bus, env=env) is usable


def test_macos_uses_the_keychain(tmp_path):
    backend = _backend("keyring.backends.macOS")
    store = _host(OperatingSystem.MACOS, home=tmp_path).secret_store(
        keyring_factory=lambda: backend
    )
    assert store.kind == "keyring"


def test_a_linux_desktop_uses_the_secret_service(tmp_path):
    backend = _backend("keyring.backends.SecretService")
    host = _host(
        OperatingSystem.LINUX, home=tmp_path, DBUS_SESSION_BUS_ADDRESS="unix:path=/run/bus"
    )
    assert host.secret_store(keyring_factory=lambda: backend).kind == "keyring"


def test_a_headless_linux_host_uses_the_private_file_without_asking_the_keyring(tmp_path):
    def must_not_be_called():
        raise AssertionError("a host without a session bus must not reach for the keyring")

    store = _host(OperatingSystem.LINUX, home=tmp_path).secret_store(
        keyring_factory=must_not_be_called
    )
    assert store.kind == "file"
    assert store.path == tmp_path / ".local/share/agdash/secrets.json"


def test_a_failing_keyring_falls_back_to_the_file(tmp_path):
    host = _host(OperatingSystem.MACOS, home=tmp_path)
    store = host.secret_store(keyring_factory=lambda: _backend("keyring.backends.fail"))
    assert store.kind == "file"
    assert store.path == tmp_path / "Library/Application Support/agdash/secrets.json"


# -- process probes -----------------------------------------------------------------


def test_the_probe_reads_this_process(tmp_path):
    probe = current_host().process_probe()
    me = probe.identity(os.getpid())
    assert me is not None and me.pid == os.getpid()
    held = tmp_path / "held-open.txt"
    held.write_text("x")
    with open(held) as _handle:
        files = probe.open_files(me)
    assert os.path.realpath(probe.cwd(me)) == os.path.realpath(os.getcwd())
    assert files is not None
    assert os.path.realpath(held) in {os.path.realpath(path) for path in files}
    assert files == sorted(set(files))


def test_a_child_process_is_read_and_left_running(tmp_path):
    """The probe reads another process without touching it."""
    child = subprocess.Popen(
        [os.sys.executable, "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
        cwd=tmp_path,
    )
    try:
        probe = current_host().process_probe()
        identity = probe.identity(child.pid)
        assert identity is not None
        assert os.path.realpath(probe.cwd(identity)) == os.path.realpath(tmp_path)
        assert child.poll() is None
    finally:
        child.stdin.close()
        child.wait(timeout=10)


def test_a_pid_with_a_different_start_time_is_another_process():
    probe = current_host().process_probe()
    me = probe.identity(os.getpid())
    impostor = ProcessIdentity(me.pid, me.started_at - timedelta(minutes=5))
    with pytest.raises(ProcessGone):
        probe.cwd(impostor)
    with pytest.raises(ProcessGone):
        probe.open_files(impostor)


def test_a_finished_process_has_no_identity():
    child = subprocess.Popen([os.sys.executable, "-c", "pass"])
    child.wait(timeout=10)
    probe = current_host().process_probe()
    assert probe.identity(child.pid) is None


class FakePsutil:
    """A psutil whose one process refuses to describe itself."""

    NoSuchProcess = psutil.NoSuchProcess
    ZombieProcess = psutil.ZombieProcess
    AccessDenied = psutil.AccessDenied

    def __init__(self, started=1767323045.5):
        self.started = started

    def Process(self, pid):  # noqa: N802 - mirrors psutil's name
        if pid == 404:
            raise psutil.NoSuchProcess(pid)
        outer = self

        class Refusing:
            def create_time(self):
                return outer.started

            def cwd(self):
                raise psutil.AccessDenied(pid)

            def open_files(self):
                raise psutil.AccessDenied(pid)

        return Refusing()


LSOF_OUTPUT = """p4242
fcwd
tDIR
n/work/example
ftxt
tREG
n/usr/bin/agent
f0
tCHR
n/dev/ttys001
f5
tREG
n/home/someone/.codex/sessions/2026/01/02/rollout-a.jsonl
f6
tREG
n/home/someone/.codex/sessions/2026/01/02/rollout-a.jsonl
f7
tIPv4
nlocalhost:52000->localhost:8480
f8
tREG
nrelative-name-is-ignored
"""


def _runner(output: str, returncode: int = 0):
    calls = []

    def run(command, **options):
        calls.append((command, options))
        return subprocess.CompletedProcess(command, returncode, stdout=output, stderr="")

    return run, calls


def test_lsof_output_is_parsed_for_regular_files_only():
    assert parse_lsof_regular_files(LSOF_OUTPUT.splitlines()) == [
        "/usr/bin/agent",
        "/home/someone/.codex/sessions/2026/01/02/rollout-a.jsonl",
        "/home/someone/.codex/sessions/2026/01/02/rollout-a.jsonl",
    ]


def test_on_macos_a_refused_read_falls_back_to_lsof():
    run, calls = _runner(LSOF_OUTPUT)
    fake = FakePsutil()
    probe = ProcessProbe(lsof_fallback=True, psutil_module=fake, run=run)
    identity = probe.identity(4242)
    assert probe.open_files(identity) == [
        "/home/someone/.codex/sessions/2026/01/02/rollout-a.jsonl",
        "/usr/bin/agent",
    ]
    commands = [command for command, _ in calls]
    assert all(command[0] == LSOF for command in commands)
    assert all("-p" in command and "4242" in command for command in commands)
    assert all(options["timeout"] and options["check"] is False for _, options in calls)

    run, _ = _runner("p4242\nfcwd\ntDIR\nn/work/example\n")
    probe = ProcessProbe(lsof_fallback=True, psutil_module=fake, run=run)
    assert probe.cwd(identity) == "/work/example"


def test_on_linux_a_refused_read_is_reported_as_hidden():
    run, calls = _runner(LSOF_OUTPUT)
    probe = ProcessProbe(lsof_fallback=False, psutil_module=FakePsutil(), run=run)
    identity = probe.identity(4242)
    assert probe.open_files(identity) is None
    assert probe.cwd(identity) is None
    assert calls == []


@pytest.mark.parametrize(
    "failure",
    [
        lambda command, **options: (_ for _ in ()).throw(FileNotFoundError(command[0])),
        lambda command, **options: (_ for _ in ()).throw(subprocess.TimeoutExpired(command, 10)),
        lambda command, **options: subprocess.CompletedProcess(command, 2, stdout="", stderr="bad"),
    ],
)
def test_a_failing_lsof_means_hidden_not_empty(failure):
    probe = ProcessProbe(lsof_fallback=True, psutil_module=FakePsutil(), run=failure)
    identity = probe.identity(4242)
    assert probe.open_files(identity) is None
    assert probe.cwd(identity) is None


def test_a_missing_process_has_no_identity():
    probe = ProcessProbe(lsof_fallback=True, psutil_module=FakePsutil())
    assert probe.identity(404) is None
    with pytest.raises(ProcessGone):
        probe.cwd(
            ProcessIdentity(404, current_host().process_probe().identity(os.getpid()).started_at)
        )


# -- the service manager ------------------------------------------------------------


def _definition(tmp_path, **environment) -> ServiceDefinition:
    return ServiceDefinition(
        program=("/opt/agdash/bin/agdashd", "serve", "--port", "8765"),
        log_dir=tmp_path / "logs",
        environment=environment,
    )


@pytest.mark.parametrize(
    ("program", "log_dir", "environment"),
    [
        ((), "/logs", {}),
        (("agdashd",), "/logs", {}),
        (("/bin/agdashd",), "logs", {}),
        (("/bin/agdashd",), "/logs", {"BAD NAME": "x"}),
        (("/bin/agdashd",), "/logs", {"1ST": "x"}),
    ],
)
def test_a_malformed_definition_is_refused(program, log_dir, environment):
    with pytest.raises(ValueError):
        ServiceDefinition(program=program, log_dir=Path(log_dir), environment=environment)


def test_the_launch_agent_definition(tmp_path):
    manager = LaunchdManager(home=tmp_path, uid=501, run=_runner("")[0])
    document = plistlib.loads(manager.render(_definition(tmp_path, AGDASH_PORT="8765")).encode())
    assert document["Label"] == LAUNCHD_LABEL
    assert document["ProgramArguments"] == ["/opt/agdash/bin/agdashd", "serve", "--port", "8765"]
    assert document["RunAtLoad"] is True
    assert document["KeepAlive"] == {"SuccessfulExit": False}
    assert document["EnvironmentVariables"] == {"AGDASH_PORT": "8765"}
    assert document["StandardErrorPath"].startswith(str(tmp_path / "logs"))
    assert manager.unit_path == tmp_path / "Library/LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def test_installing_and_removing_the_launch_agent(tmp_path):
    run, calls = _runner("", returncode=0)
    manager = LaunchdManager(home=tmp_path, uid=501, run=run)
    assert manager.state() is ServiceState.NOT_INSTALLED
    manager.install(_definition(tmp_path))
    assert manager.unit_path.exists()
    assert stat.S_IMODE(manager.unit_path.stat().st_mode) == 0o644
    assert manager.state() is ServiceState.RUNNING
    manager.uninstall()
    assert not manager.unit_path.exists()
    commands = [command for command, _ in calls]
    assert ["launchctl", "bootstrap", "gui/501", str(manager.unit_path)] in commands
    assert ["launchctl", "bootout", f"gui/501/{LAUNCHD_LABEL}"] in commands
    assert all(LAUNCHD_LABEL in " ".join(command) for command in commands)


def test_a_launch_agent_that_is_installed_but_not_loaded(tmp_path):
    run, _ = _runner("", returncode=113)
    manager = LaunchdManager(home=tmp_path, uid=501, run=run)
    manager.unit_path.parent.mkdir(parents=True)
    manager.unit_path.write_text("<plist/>")
    assert manager.state() is ServiceState.INSTALLED


def test_a_failed_bootstrap_is_reported(tmp_path):
    def run(command, **options):
        returncode = 1 if command[:2] == ["launchctl", "bootstrap"] else 113
        return subprocess.CompletedProcess(
            command, returncode, stdout="", stderr="Bootstrap failed: 5"
        )

    manager = LaunchdManager(home=tmp_path, uid=501, run=run)
    with pytest.raises(ServiceError, match="Bootstrap failed"):
        manager.install(_definition(tmp_path))


def test_the_systemd_unit(tmp_path):
    manager = SystemdUserManager(config_dir=tmp_path / ".config", run=_runner("")[0])
    text = manager.render(_definition(tmp_path, AGDASH_PORT="8765"))
    assert manager.unit_path == tmp_path / ".config/systemd/user" / SYSTEMD_UNIT
    assert 'ExecStart="/opt/agdash/bin/agdashd" "serve" "--port" "8765"' in text
    assert 'Environment="AGDASH_PORT=8765"' in text
    assert "Restart=on-failure" in text
    assert "WantedBy=default.target" in text


@pytest.mark.parametrize(
    ("word", "quoted"),
    [
        ("plain", '"plain"'),
        ("with space", '"with space"'),
        ('a "quote"', '"a \\"quote\\""'),
        ("back\\slash", '"back\\\\slash"'),
        ("100%", '"100%%"'),
        ("$HOME", '"$$HOME"'),
        ("two\nlines", '"two\\nlines"'),
    ],
)
def test_systemd_words_are_quoted(word, quoted):
    assert systemd_quote(word) == quoted


def test_installing_and_removing_the_systemd_unit(tmp_path):
    run, calls = _runner("", returncode=0)
    manager = SystemdUserManager(config_dir=tmp_path / ".config", run=run)
    manager.install(_definition(tmp_path))
    assert manager.state() is ServiceState.RUNNING
    manager.uninstall()
    assert not manager.unit_path.exists()
    commands = [command for command, _ in calls]
    assert commands[:2] == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT],
    ]
    assert all(command[:2] == ["systemctl", "--user"] for command in commands)
    assert all(SYSTEMD_UNIT in command or command[2] == "daemon-reload" for command in commands), (
        "every command is about agdash's own unit"
    )


def test_service_files_are_never_written_inside_a_harness_root(tmp_path):
    harness = tmp_path / ".config"
    manager = SystemdUserManager(config_dir=harness, run=_runner("")[0], forbidden_roots=(harness,))
    with pytest.raises(ServiceError, match="harness directory"):
        manager.install(_definition(tmp_path))
    assert not manager.unit_path.exists()


def test_the_host_gives_each_system_its_own_manager(tmp_path):
    run = _runner("")[0]
    assert isinstance(
        _host(OperatingSystem.MACOS, home=tmp_path).service_manager(run=run, uid=501),
        LaunchdManager,
    )
    linux = _host(OperatingSystem.LINUX, home=tmp_path, XDG_CONFIG_HOME=str(tmp_path / "cfg"))
    manager = linux.service_manager(run=run)
    assert isinstance(manager, SystemdUserManager)
    assert manager.unit_path == tmp_path / "cfg/systemd/user" / SYSTEMD_UNIT
    definition = linux.service_definition(["/opt/agdash/bin/agdashd"])
    assert definition.log_dir == linux.log_dir()


# -- no operating-system branches outside the platform layer -------------------------

BANNED = {
    "sys.platform",
    "os.name",
    "os.uname",
    "platform.system",
    "platform.platform",
    "platform.uname",
    "platform.mac_ver",
    "platform.release",
    "platform.freedesktop_os_release",
}
SYSTEM_NAMES = {"darwin", "linux", "linux2", "win32", "cygwin", "macos", "windows"}


def test_the_lint_rule_bans_every_way_of_asking():
    config = tomllib.loads((PACKAGE.parent / "pyproject.toml").read_text(encoding="utf-8"))
    lint = config["tool"]["ruff"]["lint"]
    assert "TID251" in lint["select"]
    assert set(lint["flake8-tidy-imports"]["banned-api"]) == BANNED
    assert lint["per-file-ignores"] == {"agdash/platform/**": ["TID251"]}


def _is_system_name(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and str(node.value).lower() in SYSTEM_NAMES


def _compares_with_a_system_name(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            left = node.left
            for operator, right in zip(node.ops, node.comparators, strict=True):
                if isinstance(operator, (ast.Eq, ast.NotEq, ast.Is, ast.IsNot)) and (
                    _is_system_name(left) or _is_system_name(right)
                ):
                    return True
                if (
                    isinstance(operator, (ast.In, ast.NotIn))
                    and isinstance(right, (ast.Tuple, ast.List, ast.Set))
                    and any(_is_system_name(item) for item in right.elts)
                ):
                    return True
                left = right
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("startswith", "endswith")
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and str(node.args[0].value).lower() in SYSTEM_NAMES
        ):
            return True
    return False


def test_no_module_outside_the_platform_layer_compares_with_a_system_name():
    """Catches what the lint rule cannot: a system name that arrived some other way."""
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE).as_posix()
        if relative.startswith("platform/"):
            continue
        if _compares_with_a_system_name(ast.parse(path.read_text(encoding="utf-8"))):
            offenders.append(relative)
    assert offenders == []


def test_the_scan_sees_a_disguised_branch():
    for source in [
        'if name == "darwin":\n    pass\n',
        'if "linux" != name:\n    pass\n',
        'ok = name.startswith("linux")\n',
        'ok = name in ("darwin", "linux")\n',
    ]:
        assert _compares_with_a_system_name(ast.parse(source)), source
    for source in ['found = "linux" in docs\n', 'label = "macos"\n', 'ok = name == "other"\n']:
        assert not _compares_with_a_system_name(ast.parse(source)), source
