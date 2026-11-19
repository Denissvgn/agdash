#!/bin/sh
# Build the Ubuntu packages for this machine's architecture into a folder:
#
#     desktop/scripts/package-ubuntu.sh amd64|arm64 OUT
#
# OUT receives agdash_<version>_<arch>.deb (the service), agdash-desktop_<version>_<arch>.deb
# (the window and tray) and agdash_<version>_<amd64|aarch64>.AppImage (both in one file).
# Build on the oldest supported release, 22.04, so the packages run there and later.
# Needs the project environment with the freeze extra, the
# frontend's packages, Rust, and the web view and tray build libraries
# (docs/ubuntu-packages.md).
#
# To sign the AppImage for the app's updater, set TAURI_SIGNING_PRIVATE_KEY (or
# TAURI_SIGNING_PRIVATE_KEY_PATH) and TAURI_SIGNING_PRIVATE_KEY_PASSWORD; the Tauri CLI
# reads them and writes <AppImage>.sig. See docs/releasing.md.
set -eu

ARCH="${1:?amd64 or arm64}"
OUT="${2:?a folder for the packages}"
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
TAURI="$ROOT/frontend/node_modules/.bin/tauri"
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
step() { printf '\n== %s\n' "$*"; }

step "The UI"
(cd "$ROOT/frontend" && npm run build)

step "The service, frozen, with the UI inside"
(cd "$ROOT/backend" && \
  AGDASH_UI_DIST="$ROOT/frontend/dist" ../.venv/bin/pyinstaller --noconfirm packaging/agdash.spec)
(cd "$ROOT/backend" && ../.venv/bin/python packaging/smoke.py dist/agdash)
VERSION=$("$ROOT/.venv/bin/python" -c "import agdash; print(agdash.__version__)")

step "The packages"
"$ROOT/backend/packaging/linux/build-service-deb.sh" "$ROOT/backend/dist/agdash" "$ARCH" "$VERSION" "$OUT"
mkdir -p "$ROOT/desktop/binaries"
cp "$ROOT/backend/dist/agdash" "$ROOT/desktop/binaries/agdash-$(rustc -vV | sed -n 's/^host: //p')"
BUNDLE="$ROOT/desktop/target/release/bundle"
rm -rf "$BUNDLE/deb" "$BUNDLE/appimage"
(cd "$ROOT/desktop" && "$TAURI" build --config bundle.linux-deb.json --bundles deb)
(cd "$ROOT/desktop" && "$TAURI" build --config bundle.linux-appimage.json --bundles appimage)
cp "$BUNDLE"/deb/*.deb "$BUNDLE"/appimage/*.AppImage "$OUT/"

step "The AppImage's update signature"
if [ -n "${TAURI_SIGNING_PRIVATE_KEY:-}${TAURI_SIGNING_PRIVATE_KEY_PATH:-}" ]; then
  for image in "$OUT"/*.AppImage; do
    "$TAURI" signer sign --app-version "$VERSION" "$image" > /dev/null
    test -s "$image.sig"
  done
else
  echo "No updater key: the AppImage is not signed, and the app's updater would refuse it."
fi

step "Done"
ls -la "$OUT"
