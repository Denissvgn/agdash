#!/bin/sh
# Piped into the running survey container by `survey.sh live`: what shows, right now,
# that a session is live. It only reads: the process table, each harness process's
# open files, Claude Code's session registry, Antigravity's summary database (from a
# copy in /tmp), and Pi's session files twice, a few seconds apart. Prompts, replies,
# tokens and account details are never printed.
set -u
cd "$HOME" || exit 1

echo "== Harness processes"
ps -eo pid,ppid,etime,stat,args --forest \
  | grep -E "claude|codex|agy|antigravity|pi( |$)|pi-coding-agent" | grep -v -E "grep|ps -eo" \
  | sed -E 's/((key|token|secret)[=: ]+)[^ ]+/\1REDACTED/Ig' | cut -c1-160

echo
echo "== Files each harness process holds open in the home folder"
for pid in $(pgrep -f "claude|codex|agy|antigravity|pi-coding-agent" ); do
  name=$(ps -o comm= -p "$pid" 2>/dev/null) || continue
  lsof -n -p "$pid" 2>/dev/null | awk -v home="$HOME" -v name="$name" -v pid="$pid" \
    '$NF ~ "^"home"/\\." { sub(home"/", "~/", $NF); print name, pid, $4, $NF }' \
    | sed -E 's#/[0-9a-f-]{20,}#/<id>#g; s#rollout-[^/ ]*#rollout-<id>.jsonl#; s#/[0-9TZ:._-]{10,}_<id>#/<ts>_<id>#' \
    | sort -u
done

echo
echo "== Claude Code: ~/.claude/sessions"
for file in .claude/sessions/*.json; do
  [ -e "$file" ] || { echo "(none)"; break; }
  jq -c '{file: (input_filename | split("/") | last | sub("^[0-9]+"; "<pid>")), keys: keys,
          status, pid_alive: (.pid != null)}' "$file"
done

echo
echo "== Antigravity: conversation_summaries.db, from a copy"
DB=.gemini/antigravity-cli/conversation_summaries.db
if [ -f "$DB" ]; then
  COPY=$(mktemp -d)
  for suffix in "" -wal -shm; do [ -f "$DB$suffix" ] && cp "$DB$suffix" "$COPY/db$suffix"; done
  sqlite3 -header "$COPY/db" "select status, not_fully_idle, killed, step_count,
    last_modified_time
    from conversation_summaries"
  rm -rf "$COPY"
  ls .gemini/antigravity-cli/presence 2>/dev/null | sed 's/^.*\./<id>./'
fi

echo
echo "== Pi and Codex: session files growing"
list() { find .pi/agent/sessions .codex/sessions -type f -name '*.jsonl' -printf '%s %p\n' 2>/dev/null | sort -k2; }
list > /tmp/live-1; sleep 5; list > /tmp/live-2
join -1 2 -2 2 /tmp/live-1 /tmp/live-2 | awk '{ printf "%s %s -> %s bytes\n", ($2 == $3 ? "still  " : "growing"), $1, $3 }' \
  | sed -E 's#/[^/]*_[0-9a-f-]{20,}\.jsonl#/<ts>_<id>.jsonl#; s#rollout-[^ ]*#rollout-<id>.jsonl#'
rm -f /tmp/live-1 /tmp/live-2
