#!/usr/bin/env bash
# Install Playwright browsers and, on Linux, the system packages they need.
#
# On the hosted Ubuntu runners the package download sometimes hangs, which used to
# hold a job until its own time limit and fail it with "cancelled" and no test run.
# Each try is therefore bounded, apt gives up on a slow mirror and retries, and the
# whole install is tried three times before the job fails. A normal install takes
# under a minute, so a try that is still going after two and a half has stalled.
#
# Stopping a try stops npx, but not the apt-get it started as root, which would hold
# the package lock and fail the next try at once; it is stopped before the next one.
#
# The stalls land on large files from the runner's regional mirror while small ones
# arrive fast, and the same file can stall again. The second and third tries take the
# packages from the main Ubuntu archive instead. Files already fetched are kept.
#
#   install-browsers.sh webkit chromium
set -uo pipefail

limit=()
if [ "$(uname -s)" = Linux ]; then
  printf 'Acquire::Retries "5";\nAcquire::http::Timeout "30";\nAcquire::https::Timeout "30";\nDPkg::Lock::Timeout "120";\n' |
    sudo tee /etc/apt/apt.conf.d/99-ci-network >/dev/null
  limit=(timeout --kill-after=10 150)
fi

use_main_archive() {
  [ "$(uname -s)" = Linux ] || return 0
  for list in /etc/apt/sources.list.d/ubuntu.sources /etc/apt/sources.list; do
    [ -f "$list" ] && sudo sed -i 's#mirror+file:/etc/apt/apt-mirrors.txt#http://archive.ubuntu.com/ubuntu/#' "$list"
  done
  return 0
}

stop_apt() {
  [ "$(uname -s)" = Linux ] || return 0
  sudo pkill -KILL -x apt-get 2>/dev/null
  sudo pkill -KILL -f /usr/lib/apt/methods 2>/dev/null
  sudo dpkg --configure -a >/dev/null 2>&1
  return 0
}

for attempt in 1 2 3; do
  if "${limit[@]+"${limit[@]}"}" npx playwright install --with-deps "$@"; then
    exit 0
  fi
  echo "Browser install failed (attempt $attempt of 3)." >&2
  stop_apt
  use_main_archive
  [ "$attempt" -lt 3 ] && sleep 15
done
exit 1
