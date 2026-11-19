# Releasing agdash

A release is a tag. Pushing `v<x.y.z>` runs `.github/workflows/release.yml`, which
builds every package on both systems and both architectures, signs the update
archives, writes the checksums and the updater's manifest, and opens a **draft**
GitHub release holding them. Nothing is public until you publish the draft, and the
app's updater sees a release only once it is published.

| File | Built on | What it is |
|---|---|---|
| `agdash-<v>-arm64.dmg`, `agdash-<v>-x86_64.dmg` | macOS 15 | The app, to install |
| `agdash-<v>-<arch>.app.tar.gz` and `.sig` | macOS 15 | The app, for the updater |
| `agdash_<v>_amd64.deb`, `agdash_<v>_arm64.deb` | Ubuntu 22.04 | The service |
| `agdash-desktop_<v>_amd64.deb`, `agdash-desktop_<v>_arm64.deb` | Ubuntu 22.04 | The window and tray |
| `agdash_<v>_amd64.AppImage`, `agdash_<v>_aarch64.AppImage` and `.sig` | Ubuntu 22.04 | All of it in one file; the updater uses it too |
| `latest.json` | | The updater's manifest: each platform's archive and signature |
| `SHA256SUMS` | | A checksum for every file above |

A pull request that changes the release workflow, the packaging scripts or the
app's bundle settings runs the same workflow as a dry run, and so does starting it
by hand from the Actions tab. A dry run uses no secret: it signs with a throwaway
updater key, signs the macOS app ad hoc, and creates no release. Its files,
`SHA256SUMS` and `latest.json` included, are kept as the run's `release-files`
artifact.

## Cutting a release

1. Set the new version in every place that carries it. The workflow refuses a tag
   that does not match all of them:

   | File | Field |
   |---|---|
   | `backend/agdash/__init__.py` | `__version__` |
   | `backend/pyproject.toml` | `version` |
   | `desktop/Cargo.toml` (and `desktop/Cargo.lock`, by `cargo check`) | `version` |
   | `desktop/tauri.conf.json` | `version` |
   | `frontend/package.json`, `frontend/package-lock.json` | `version` (`npm version <v> --no-git-tag-version` sets both) |

   ```bash
   .venv/bin/python backend/packaging/release.py check v0.2.0 --publish
   ```

2. Merge that change to `main`, then tag the merge commit and push the tag:

   ```bash
   git tag v0.2.0 && git push origin v0.2.0
   ```

3. When the workflow finishes, open the draft release, read it, and publish it.

The service and the app always carry the same version, since the app restarts a
service older than itself after an update (below).

## Setting up once

The repository needs these secrets (Settings → Secrets and variables → Actions).
Only a tag's run reads them; a pull request never does.

### The updater key (required)

The app checks every update against a public key it was built with, and refuses an
update that does not match. Make the key pair once, on your own machine:

```bash
frontend/node_modules/.bin/tauri signer generate --write-keys ~/.tauri/agdash-updater.key
```

- Put the **public** key, the text of `~/.tauri/agdash-updater.key.pub`, in
  `desktop/tauri.conf.json` at `plugins.updater.pubkey`, and merge it. A build
  without it offers no updates, and the workflow refuses to release.
- Add the **private** key, the text of `~/.tauri/agdash-updater.key`, as the
  secret `TAURI_SIGNING_PRIVATE_KEY`, and its password as
  `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`.

Keep the private key and its password safe. If either is lost, the apps already
installed can never update again, and their owners have to install a new download
by hand.

### The Developer ID (optional)

Without these the macOS app is signed ad hoc, Gatekeeper blocks it on first open,
and the draft release says so.

| Secret | What it holds |
|---|---|
| `APPLE_CERTIFICATE_P12` | The Developer ID Application certificate with its private key, exported from Keychain Access as a `.p12`, then base64 (`base64 -i cert.p12 \| pbcopy`) |
| `APPLE_CERTIFICATE_PASSWORD` | The password you gave the `.p12` on export |
| `APPLE_API_KEY_P8` | An App Store Connect API key's `.p8` file, as text (Users and Access → Integrations → App Store Connect API, with the Developer role) |
| `APPLE_API_KEY_ID` | That key's ID |
| `APPLE_API_ISSUER` | The issuer ID shown above the keys |

`desktop/scripts/release-keychain.sh` imports the certificate into a keychain that
exists only for that job, and stores the notarisation key there as a `notarytool`
profile. `package-macos.sh` then signs, notarises and staples as it does on your
own Mac ([macos-package.md](macos-package.md)).

## Updates in the app

The tray item has **Check for updates** in the macOS app and in the AppImage. The
app makes no request anywhere but to the agdash service on `127.0.0.1` until you
choose it. Then it fetches
`https://github.com/Denissvgn/agdash/releases/latest/download/latest.json` and,
when that names a newer version, offers **Install agdash \<v\> and restart**.
Installing downloads the archive for this system, checks its signature against the
app's public key, and checks that the signature was made for the version the
manifest names. Only then does it replace the app and start it again.

A `.deb` install has no update item: install the new packages instead.

After an update the app is new, but a service it did not stop still runs the earlier
version. When the app starts and finds a service older than itself that runs under
the system's service manager (`agdash install`), it restarts that service through the
manager (`launchctl kickstart -k`, or `systemctl --user restart`), so the service
runs the binary the new app carries. A service started any other way, such as
`agdash serve` in a terminal, is left running; restart it yourself.

## Checking a download

```bash
sha256sum --check --ignore-missing SHA256SUMS     # Ubuntu
shasum -a 256 --check --ignore-missing SHA256SUMS # macOS
```
