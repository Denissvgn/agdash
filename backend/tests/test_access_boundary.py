"""A tripwire: file access in the service goes through the access layer.

This reads the source of every module and fails on the usual ways of opening,
changing or locking a file. It is a tripwire, not a proof: it cannot follow a
value to see what it is. A module with a real need is listed below with the
reason, so that reaching past the access layer is a decision someone reviews.
"""

import ast
from pathlib import Path

import agdash

PACKAGE = Path(agdash.__file__).resolve().parent

FORBIDDEN_IMPORTS = {"sqlite3", "fcntl", "shutil", "tempfile", "mmap"}
FORBIDDEN_CALLS = {"open", "os.open", "io.open", "codecs.open", "os.remove", "os.unlink"}
FORBIDDEN_METHODS = {
    "read_text",
    "read_bytes",
    "write_text",
    "write_bytes",
    "touch",
    "unlink",
    "rmdir",
    "mkdir",
    "makedirs",
    "rename",
    "rmtree",
    "chmod",
    "symlink_to",
    "hardlink_to",
    "flock",
    "lockf",
}

# Module, relative to the package -> what it may use, and why.
EXEMPT = {
    # The access layer itself. It writes only into its private snapshot directory.
    "access/readonly.py": {"sqlite3", "shutil", "tempfile", "open", "os.open", "mkdir", "rmtree"},
    # Reads the event contract that ships inside the package.
    "events.py": {"read_text"},
}


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return None


def _uses(tree: ast.AST) -> set[str]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names} & FORBIDDEN_IMPORTS
        elif isinstance(node, ast.ImportFrom) and node.module:
            found |= {node.module.split(".")[0]} & FORBIDDEN_IMPORTS
        elif isinstance(node, ast.Call):
            if _dotted(node.func) in FORBIDDEN_CALLS:
                found.add(_dotted(node.func))
            elif isinstance(node.func, ast.Attribute) and node.func.attr in FORBIDDEN_METHODS:
                found.add(node.func.attr)
    return found


def _modules() -> dict[str, Path]:
    return {
        path.relative_to(PACKAGE).as_posix(): path
        for path in sorted(PACKAGE.rglob("*.py"))
        if "versions" not in path.parts
    }


def test_only_listed_modules_touch_files_directly():
    unexpected = {}
    for name, path in _modules().items():
        extra = _uses(ast.parse(path.read_text(encoding="utf-8"))) - EXEMPT.get(name, set())
        if extra:
            unexpected[name] = sorted(extra)
    assert unexpected == {}


def test_exemptions_are_not_left_behind():
    modules = _modules()
    for name, allowed in EXEMPT.items():
        assert name in modules, f"exempt module no longer exists: {name}"
        used = _uses(ast.parse(modules[name].read_text(encoding="utf-8")))
        assert allowed <= used, f"{name} no longer needs: {sorted(allowed - used)}"


def test_the_access_layer_never_locks():
    for name in ("access/readonly.py", "access/guard.py"):
        used = _uses(ast.parse(_modules()[name].read_text(encoding="utf-8")))
        assert not used & {"fcntl", "flock", "lockf"}


def test_the_tripwire_sees_what_it_is_meant_to_see():
    source = """
import sqlite3
from fcntl import flock
import os.path
def f(p):
    open(p, "w")
    os.open(p, 0)
    p.write_text("x")
    p.parent.mkdir()
    p.read_bytes()
    "a-b".replace("-", "_")
    access.open(p)
"""
    assert _uses(ast.parse(source)) == {
        "sqlite3",
        "fcntl",
        "open",
        "os.open",
        "write_text",
        "mkdir",
        "read_bytes",
    }
