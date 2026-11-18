#!/usr/bin/env bash
# Install Playwright browsers and, on Linux, the system packages they need.
#
# On the hosted Ubuntu runners the package download sometimes hangs, which used to
# hold a job until its own time limit and fail it with "cancelled" and no test run.
# Each try is therefore bounded, apt gives up on a slow mirror and retries, and the
# whole install is tried three times before the job fails.
#
#   install-browsers.sh webkit chromium
set -uo pipefail

limit=()
if [ "$(uname -s)" = Linux ]; then
  printf 'Acquire::Retries "5";\nAcquire::http::Timeout "30";\nAcquire::https::Timeout "30";\n' |
    sudo tee /etc/apt/apt.conf.d/99-ci-network >/dev/null
  limit=(timeout --kill-after=10 240)
fi

for attempt in 1 2 3; do
  if "${limit[@]+"${limit[@]}"}" npx playwright install --with-deps "$@"; then
    exit 0
  fi
  echo "Browser install failed (attempt $attempt of 3)." >&2
  [ "$attempt" -lt 3 ] && sleep 15
done
exit 1
