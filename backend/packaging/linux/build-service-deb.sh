#!/bin/sh
# Build the agdash service package from a frozen binary.
#
#     backend/packaging/linux/build-service-deb.sh <binary> <amd64|arm64> <version> <out dir>
#
# The package installs /usr/bin/agdash and nothing that runs on its own: no unit is
# enabled for anyone. Each user starts the service with `agdash install`, which
# writes their own systemd user unit. See /usr/share/doc/agdash/README.Debian.
set -eu

BINARY=$1
ARCH=$2
VERSION=$3
OUT=$4
case "$ARCH" in amd64|arm64) ;; *) echo "architecture must be amd64 or arm64" >&2; exit 2 ;; esac

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT

install -D -m 0755 "$BINARY" "$ROOT/usr/bin/agdash"
install -D -m 0644 "$HERE/README.Debian" "$ROOT/usr/share/doc/agdash/README.Debian"
# The license text, indented as the copyright format asks, blank lines as " .".
mkdir -p "$ROOT/usr/share/doc/agdash"
{ cat "$HERE/copyright"; sed -e 's/^/ /' -e 's/^ $/ ./' "$HERE/../../../LICENSE"; } \
  > "$ROOT/usr/share/doc/agdash/copyright"
chmod 0644 "$ROOT/usr/share/doc/agdash/copyright"
SIZE=$(du -sk "$ROOT/usr" | cut -f1)
mkdir -p "$ROOT/DEBIAN"
cat > "$ROOT/DEBIAN/control" <<CONTROL
Package: agdash
Version: $VERSION
Architecture: $ARCH
Maintainer: agdash <https://github.com/Denissvgn/agdash>
Installed-Size: $SIZE
Depends: libc6 (>= 2.35)
Section: devel
Priority: optional
Homepage: https://github.com/Denissvgn/agdash
Description: read-only dashboard of the agentic coding sessions on this host
 agdash observes Claude Code, Codex, Antigravity and Pi sessions on this machine
 and keeps a history of what each was asked, did and used. It never starts,
 stops or changes an agent, never writes to a harness's files, and never reads
 credentials. The service listens on 127.0.0.1 only.
 .
 This package is the service and the agdash command, with the UI built in for
 a browser. The window and tray are in the agdash-desktop package.
CONTROL
mkdir -p "$OUT"
dpkg-deb --root-owner-group --build "$ROOT" "$OUT/agdash_${VERSION}_${ARCH}.deb"
