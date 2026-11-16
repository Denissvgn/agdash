#!/bin/sh
# Inside a fresh Ubuntu container: install the service and desktop packages and check the
# app's libraries all resolve. The window and tray themselves need a desktop session.
#
#     docker run --rm -v "$PWD/out:/out:ro" -v "$PWD/backend/packaging/linux:/t:ro" \
#       ubuntu:24.04 sh /t/test-desktop-deb.sh /out/agdash_<v>_<arch>.deb /out/agdash-desktop_<v>_<arch>.deb
set -eu
export DEBIAN_FRONTEND=noninteractive
. /etc/os-release
echo "== $PRETTY_NAME, $(uname -m)"
apt-get update -qq
apt-get install -y -qq "$1" "$2" > /dev/null
test -x /usr/bin/agdash-app && test -x /usr/bin/agdash
echo "ok   both packages installed, agdash-app beside agdash"
if ldd /usr/bin/agdash-app | grep "not found"; then exit 1; fi
echo "ok   every library of agdash-app resolves"
grep -q "^Name=agdash$" /usr/share/applications/*.desktop
echo "ok   the menu entry is named agdash"
