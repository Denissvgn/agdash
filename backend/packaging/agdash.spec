# PyInstaller build of the agdash service as one self-contained executable.
#
#     cd backend && ../.venv/bin/pyinstaller --noconfirm packaging/agdash.spec
#
# The result is dist/agdash: the `agdash` command (serve, backfill, status, doctor,
# install, ...). It builds for the machine it runs on; macOS arm64 and x86_64 and
# Linux amd64 and arm64 are each built on a runner of that kind.
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

# The backend folder, so the analysis finds agdash as a plain package even when it is
# installed in editable mode.
BACKEND = os.path.dirname(SPECPATH)  # noqa: F821 (PyInstaller defines SPECPATH)

# Alembic runs env.py and the revision files from disk, so they ship as source files
# next to the event schema, where the package looks for them.
datas = collect_data_files(
    "agdash",
    include_py_files=True,
    includes=[
        "contracts/*.json",
        "migrations/*.py",
        "migrations/alembic.ini",
        "migrations/script.py.mako",
        "migrations/versions/*.py",
    ],
)
# keyring finds its backends through package metadata.
datas += copy_metadata("keyring")

hiddenimports = collect_submodules("agdash", filter=lambda name: ".tests" not in name)

a = Analysis(
    ["agdash_entry.py"],
    pathex=[BACKEND],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "ruff", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="agdash",
    console=True,
    strip=False,
    upx=False,
    # On macOS, with a Developer ID identity, PyInstaller signs every library it packs
    # inside the binary, so the ones it unpacks at run time pass library validation
    # under the hardened runtime. Without one the binary is signed ad hoc.
    codesign_identity=os.environ.get("AGDASH_CODESIGN_IDENTITY") or None,
    entitlements_file=os.environ.get("AGDASH_SERVICE_ENTITLEMENTS") or None,
)
