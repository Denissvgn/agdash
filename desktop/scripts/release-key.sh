#!/bin/sh
# Make the updater key available to the packaging scripts in the release workflow:
#
#     desktop/scripts/release-key.sh "$KEY" "$PASSWORD"
#
# On a release (PUBLISH=true) KEY is the repository's TAURI_SIGNING_PRIVATE_KEY secret
# and must be set. Otherwise it is empty, and a throwaway key with no password is made,
# so a dry run signs its archives the same way without any secret. The key goes to a
# file only this job reads; its path and password go to $GITHUB_ENV for later steps.
set -eu

KEY="${1:-}"
PASSWORD="${2:-}"
FILE="$RUNNER_TEMP/updater.key"
umask 077

if [ -n "$KEY" ]; then
  printf '%s' "$KEY" > "$FILE"
  echo "Using the repository's updater key."
elif [ "${PUBLISH:-false}" = true ]; then
  echo "A release needs the TAURI_SIGNING_PRIVATE_KEY secret (see docs/releasing.md)." >&2
  exit 1
else
  PASSWORD=""
  "$(cd "$(dirname "$0")/../.." && pwd)/frontend/node_modules/.bin/tauri" \
    signer generate --ci --password "" --write-keys "$FILE" > /dev/null
  echo "A dry run: signing with a throwaway updater key."
fi

{
  echo "TAURI_SIGNING_PRIVATE_KEY_PATH=$FILE"
  echo "TAURI_SIGNING_PRIVATE_KEY_PASSWORD<<agdash-password-end"
  printf '%s\n' "$PASSWORD"
  echo "agdash-password-end"
} >> "$GITHUB_ENV"
