#!/bin/sh
# Inside a fresh Ubuntu container: install the service package and check it runs with no
# display, serves the UI, and guards its data with the token.
#
#     docker run --rm -v "$PWD/out:/out:ro" -v "$PWD/backend/packaging/linux:/t:ro" \
#       ubuntu:24.04 sh /t/test-service-deb.sh /out/agdash_<version>_<arch>.deb
set -eu
DEB=$1
export DEBIAN_FRONTEND=noninteractive
. /etc/os-release
echo "== $PRETTY_NAME, $(uname -m)"
apt-get update -qq
apt-get install -y -qq "$DEB" curl ca-certificates > /dev/null
test -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" # no display
agdash --help > /dev/null

agdash serve > /tmp/serve.log 2>&1 &
SERVICE=$!
ok=""
for _ in $(seq 1 120); do
  if curl -fsS http://127.0.0.1:8765/health > /dev/null 2>&1; then ok=yes; break; fi
  sleep 1
done
[ -n "$ok" ] || { echo "the service did not answer"; cat /tmp/serve.log; exit 1; }
echo "ok   /health"

curl -fsS http://127.0.0.1:8765/ | grep -q 'id="root"'
echo "ok   the UI is served"
[ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/api/stats)" = 401 ]
echo "ok   data refused without the token"
TOKEN=$(agdash token | cat)
curl -fsS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/api/stats > /dev/null
echo "ok   data answered with the token"
agdash open | head -1 | grep -q '^http://127.0.0.1:8765/#agdash-token='
echo "ok   agdash open prints the link"
agdash status | grep -q "Answering: yes"
echo "ok   agdash status"

kill -TERM "$SERVICE"
wait "$SERVICE" || true
echo "ok   stopped"
