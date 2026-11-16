#!/bin/sh
# Build agdash.app and agdash-<version>-<arch>.dmg for this Mac, with the service inside.
#
#     desktop/scripts/package-macos.sh
#
# With no identity the bundle is signed ad hoc: good for checking the build, not for
# handing out. To sign and notarise, set:
#
#     AGDASH_CODESIGN_IDENTITY  "Developer ID Application: <name> (<team id>)", installed
#                               in your login keychain
#     AGDASH_NOTARY_PROFILE     a notarytool keychain profile you created with
#                               `xcrun notarytool store-credentials`
#
# The script never asks for, reads or prints a password: codesign uses the identity's
# key in your keychain, and notarytool the profile stored there. See
# docs/macos-package.md.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
DESKTOP="$ROOT/desktop"
IDENTITY="${AGDASH_CODESIGN_IDENTITY:-}"
PROFILE="${AGDASH_NOTARY_PROFILE:-}"
TRIPLE=$(rustc -vV | sed -n 's/^host: //p')
ARCH=$(uname -m)
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' "$DESKTOP/Cargo.toml" | head -1)
# A build folder of its own, apart from ordinary development builds.
export CARGO_TARGET_DIR="$DESKTOP/target/package"
# The compiler targets macOS 11.0 (bundle.macos.json): with 14.0, Rust 1.95 cannot load
# the procedural macros it has just built. desktop/Info.plist sets the app's real
# minimum, macOS 14.
OUT="$CARGO_TARGET_DIR/release/bundle"
APP="$OUT/macos/agdash.app"
DMG="$OUT/dmg/agdash-$VERSION-$ARCH.dmg"

if [ -n "$PROFILE" ] && [ -z "$IDENTITY" ]; then
  echo "AGDASH_NOTARY_PROFILE needs AGDASH_CODESIGN_IDENTITY: only a signed build can be notarised" >&2
  exit 2
fi
SIGN_AS="${IDENTITY:--}" # "-" is an ad hoc signature
step() { printf '\n== %s\n' "$*"; }

step "The UI"
(cd "$ROOT/frontend" && npm run build)

step "The service, frozen"
(cd "$ROOT/backend" && \
  AGDASH_CODESIGN_IDENTITY="$IDENTITY" \
  AGDASH_SERVICE_ENTITLEMENTS="$([ -n "$IDENTITY" ] && echo "$DESKTOP/entitlements/service.plist")" \
  ../.venv/bin/pyinstaller --noconfirm --log-level WARN packaging/agdash.spec)
(cd "$ROOT/backend" && ../.venv/bin/python packaging/smoke.py dist/agdash)
mkdir -p "$DESKTOP/binaries"
cp "$ROOT/backend/dist/agdash" "$DESKTOP/binaries/agdash-$TRIPLE"

step "The app"
rm -rf "$APP"
(cd "$DESKTOP" && "$ROOT/frontend/node_modules/.bin/tauri" build --config bundle.macos.json --bundles app)
test -x "$APP/Contents/MacOS/agdash-app"
test -x "$APP/Contents/MacOS/agdash"

step "Signing, inside out, as ${IDENTITY:-ad hoc}"
sign() { # entitlements, path
  codesign --force --sign "$SIGN_AS" --options runtime --timestamp$([ -z "$IDENTITY" ] && echo "=none") \
    --entitlements "$1" "$2"
}
sign "$DESKTOP/entitlements/service.plist" "$APP/Contents/MacOS/agdash"
sign "$DESKTOP/entitlements/app.plist" "$APP/Contents/MacOS/agdash-app"
sign "$DESKTOP/entitlements/app.plist" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

step "The disk image"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
mkdir -p "$(dirname "$DMG")"
rm -f "$DMG"
hdiutil create -volname "agdash" -srcfolder "$STAGE" -fs HFS+ -format UDZO -quiet "$DMG"
codesign --force --sign "$SIGN_AS" --timestamp$([ -z "$IDENTITY" ] && echo "=none") "$DMG"

if [ -n "$PROFILE" ]; then
  step "Notarising"
  xcrun notarytool submit "$DMG" --keychain-profile "$PROFILE" --wait
  xcrun stapler staple "$DMG"
  xcrun stapler staple "$APP"
  xcrun stapler validate "$DMG"
  spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG"
  spctl --assess --type execute --verbose=2 "$APP"
else
  step "Not notarised"
  echo "No AGDASH_NOTARY_PROFILE: this build is for checking only; Gatekeeper will refuse it on another Mac."
fi

step "Done"
echo "$APP"
echo "$DMG"
