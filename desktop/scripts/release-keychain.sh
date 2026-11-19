#!/bin/sh
# Put the Developer ID and the notarisation key in a keychain of this job's own, in the
# release workflow, and tell package-macos.sh to use them. With no certificate the build
# stays ad hoc signed, as it is on a pull request; the draft release then says so.
#
# Reads, from the environment: CERTIFICATE (a base64 .p12 holding the Developer ID
# Application certificate and its key), CERTIFICATE_PASSWORD, API_KEY (the App Store
# Connect API key, the .p8 file's text), API_KEY_ID and API_ISSUER.
set -eu

if [ -z "${CERTIFICATE:-}" ]; then
  echo "No Developer ID: the app is signed ad hoc and not notarised."
  exit 0
fi
if [ -z "${API_KEY:-}" ] || [ -z "${API_KEY_ID:-}" ] || [ -z "${API_ISSUER:-}" ]; then
  echo "A Developer ID needs the App Store Connect API key to notarise (see docs/releasing.md)." >&2
  exit 1
fi

umask 077
KEYCHAIN="$RUNNER_TEMP/agdash-signing.keychain-db"
SECRET=$(openssl rand -hex 24) # unlocks this job's keychain, and nothing else
P12="$RUNNER_TEMP/agdash-signing.p12"
P8="$RUNNER_TEMP/agdash-notary.p8"
trap 'rm -f "$P12" "$P8"' EXIT

printf '%s' "$CERTIFICATE" | base64 --decode > "$P12"
printf '%s' "$API_KEY" > "$P8"
security create-keychain -p "$SECRET" "$KEYCHAIN"
security set-keychain-settings -lut 21600 "$KEYCHAIN"
security unlock-keychain -p "$SECRET" "$KEYCHAIN"
security import "$P12" -k "$KEYCHAIN" -P "${CERTIFICATE_PASSWORD:-}" -T /usr/bin/codesign -f pkcs12
security set-key-partition-list -S apple-tool:,apple: -s -k "$SECRET" "$KEYCHAIN" > /dev/null
# shellcheck disable=SC2046 # the keychains already searched, one word each
security list-keychains -d user -s "$KEYCHAIN" $(security list-keychains -d user | tr -d '"')

IDENTITY=$(security find-identity -v -p codesigning "$KEYCHAIN" \
  | sed -n 's/.*"\(Developer ID Application: .*\)"$/\1/p' | head -1)
if [ -z "$IDENTITY" ]; then
  echo "The certificate holds no Developer ID Application identity." >&2
  exit 1
fi
xcrun notarytool store-credentials agdash-notary \
  --key "$P8" --key-id "$API_KEY_ID" --issuer "$API_ISSUER" --keychain "$KEYCHAIN" > /dev/null

{
  echo "AGDASH_CODESIGN_IDENTITY=$IDENTITY"
  echo "AGDASH_NOTARY_PROFILE=agdash-notary"
  echo "AGDASH_NOTARY_KEYCHAIN=$KEYCHAIN"
} >> "$GITHUB_ENV"
echo "Signing as the repository's Developer ID; notarising with its API key."
