#!/bin/sh
# Runs inside the reader container (see survey.sh read): the survey home is mounted
# read-only at $HOME, this folder at /survey, and the output folder at /out.
set -eu
OUT=/out
export XDG_DATA_HOME=/tmp/agdash-data XDG_CONFIG_HOME=/tmp/agdash-config
mkdir -p "$XDG_DATA_HOME" "$XDG_CONFIG_HOME"

# Every harness file: path, size, modification time, and a hash except for credential
# files, which are never opened.
snapshot() {
  python3 - "$HOME" <<'PY'
import hashlib, os, sys
home = sys.argv[1]
never = {".claude/.credentials.json", ".codex/auth.json", ".gemini/antigravity-cli/antigravity-oauth-token",
         ".gemini/oauth_creds.json", ".pi/agent/auth.json"}
for root in (".claude", ".claude.json", ".codex", ".gemini", ".config/antigravity", ".pi"):
    top = os.path.join(home, root)
    paths = [top] if os.path.isfile(top) else [os.path.join(d, f) for d, _, fs in os.walk(top) for f in fs]
    for path in sorted(paths):
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        rel = os.path.relpath(path, home)
        info = os.stat(path)
        digest = "-"
        if rel not in never:
            with open(path, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
        print(rel, info.st_size, info.st_mtime_ns, digest)
PY
}

snapshot > "$OUT/before.txt"
if touch "$HOME/.agdash-write-test" 2>/dev/null; then
  echo "The survey home is writable: refusing to read it." >&2
  exit 1
fi

echo "== Inventory"
python3 /survey/inventory.py "$HOME" > "$OUT/inventory.json"
echo "written to $OUT/inventory.json"

echo "== agdash backfill on the Linux layout"
AGDASH_DATABASE_URL="sqlite:////tmp/agdash-survey.db" agdash backfill | tee "$OUT/backfill.txt"
python3 - <<'PY' | tee "$OUT/sessions.txt"
import sqlite3
db = sqlite3.connect("/tmp/agdash-survey.db")
columns = [row[1] for row in db.execute("pragma table_info(sessions)")]
wanted = [c for c in ("harness", "surface", "state", "auth_method", "model", "thoughts_coverage") if c in columns]
print("sessions:", ", ".join(wanted))
for row in db.execute(f"select {', '.join(wanted)}, count(*) from sessions group by {', '.join(wanted)}"):
    print("  ", row)
print("events by harness, kind and thoughts coverage:")
for row in db.execute(
    "select s.harness, e.kind, e.thoughts_coverage, count(*) from events e join sessions s"
    " on s.id = e.session_id group by 1, 2, 3 order by 1, 2, 3"):
    print("  ", row)
print("source health (adapter, format version, unknown records, parse errors):")
for row in db.execute("select adapter, format_version, unknown_record_count, parse_error_count from source_health order by 1, 2"):
    print("  ", row)
PY

echo "== Fixtures"
python3 - <<'PY'
from pathlib import Path
from agdash.devtools.fixture_kit import generate, load_salt
from agdash.platform.roots import harness_roots
import os
report = generate(Path("/out/fixtures"), load_salt(Path("/out/fixture-kit.salt")),
                  harness_roots(Path(os.environ["HOME"]), os.environ))
for harness in sorted(report.files):
    print(f"{harness}: {report.files[harness]} files, {report.records[harness]} records")
print(f"unparseable lines skipped: {report.skipped_lines}")
PY

snapshot > "$OUT/after.txt"
echo "== Harness files before and after"
if cmp -s "$OUT/before.txt" "$OUT/after.txt"; then
  echo "unchanged: $(wc -l < "$OUT/before.txt") files, same size, time and hash"
else
  diff "$OUT/before.txt" "$OUT/after.txt" | cut -c1-120
  exit 1
fi
