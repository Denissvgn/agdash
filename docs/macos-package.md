# The macOS package

`desktop/scripts/package-macos.sh` builds `agdash.app` and a disk image,
`agdash-<version>-<arch>.dmg`, for the Mac it runs on (Apple silicon or Intel).
The app carries the service inside it, frozen into one binary:

```
agdash.app/Contents/MacOS/agdash-app   the window and the menu-bar item
agdash.app/Contents/MacOS/agdash       the service and the `agdash` command
```

The app finds the service binary beside itself. `agdash install`, run from that
binary, starts the service at login as a LaunchAgent that points at the copy
inside the app.

## Building

Requirements: Xcode's command line tools, Rust (the version in
`desktop/rust-toolchain.toml`), Node.js 22, and the project environment with the
freeze extra:

```bash
python3 -m venv .venv && .venv/bin/pip install -e "backend[dev,freeze]"
(cd frontend && npm ci)
desktop/scripts/package-macos.sh
```

The script builds the UI, freezes the service and runs its smoke test, builds the
app, signs it, and makes the disk image under
`desktop/target/package/release/bundle/`.

With no signing identity it signs ad hoc. That build is for checking the bundle on
the Mac that built it; Gatekeeper refuses it anywhere else.

Two things about the build:

- It uses its own build folder, `desktop/target/package`, apart from development
  builds.
- The compiler targets macOS 11.0, because with 14.0 Rust 1.95 cannot load the
  procedural macros it has just built. The app still requires macOS 14:
  `desktop/Info.plist` sets `LSMinimumSystemVersion`, which macOS checks before it
  opens the app. If a build fails with "can't find crate", delete
  `desktop/target/package` and build again.

### The icon

The app's icon files in `desktop/icons` are drawn from two SVG sources there:
`icon.svg`, and `icon-small.svg` for 32 pixels and under. After changing either,
draw them again on a Mac:

```bash
(cd frontend && npm run icons)
```

## Signing and notarising

Signing and notarising use your Apple Developer account. You set them up once, and
the script never sees a password.

1. Install a **Developer ID Application** certificate, with its private key, in
   your login keychain (Xcode: Settings → Accounts → Manage Certificates, or the
   Apple Developer website). `security find-identity -v -p codesigning` lists it.
2. Store notarisation credentials as a keychain profile. This asks for an
   app-specific password, which you create at appleid.apple.com:

   ```bash
   xcrun notarytool store-credentials agdash-notary --apple-id YOUR_APPLE_ID --team-id YOUR_TEAM_ID
   ```

3. Build with both set:

   ```bash
   AGDASH_CODESIGN_IDENTITY="Developer ID Application: Your Name (YOUR_TEAM_ID)" \
   AGDASH_NOTARY_PROFILE=agdash-notary \
   desktop/scripts/package-macos.sh
   ```

What the script signs, from the inside out, each with the hardened runtime and a
secure timestamp:

| What | Entitlements |
|---|---|
| The libraries packed inside the frozen service (PyInstaller signs them as it freezes) | — |
| `Contents/MacOS/agdash`, the service | `desktop/entitlements/service.plist` |
| `Contents/MacOS/agdash-app` | `desktop/entitlements/app.plist` (none) |
| `agdash.app` | `desktop/entitlements/app.plist` (none) |
| The disk image | — |

The service's one entitlement, `com.apple.security.cs.disable-library-validation`,
lets it load the Python libraries it unpacks into a temporary folder at start-up.
They are signed with the same identity, so it may prove unnecessary; drop it once a
signed build is shown to start without it.

The script then submits the disk image with `notarytool`, staples the ticket to the
disk image and the app, and checks both with `spctl`.

### Checking a build

```bash
codesign --verify --deep --strict --verbose=2 desktop/target/package/release/bundle/macos/agdash.app
codesign --display --entitlements - desktop/target/package/release/bundle/macos/agdash.app/Contents/MacOS/agdash
xcrun stapler validate desktop/target/package/release/bundle/dmg/agdash-*.dmg
spctl --assess --type open --context context:primary-signature --verbose=2 desktop/target/package/release/bundle/dmg/agdash-*.dmg
```

The acceptance check is a person's: on a Mac that has never run agdash, open the
disk image, drag the app to Applications and open it, with no Gatekeeper override.

## Full Disk Access

agdash should need no Full Disk Access, and macOS should show no privacy prompt.
macOS guards particular folders: Desktop, Documents and Downloads, iCloud Drive,
removable and network volumes, and other apps' data under `~/Library`, such as
Mail and Messages. The harnesses keep their records in hidden folders of the home
folder, which macOS does not guard:

| Harness | Where agdash reads |
|---|---|
| Claude Code | `~/.claude` (or `CLAUDE_CONFIG_DIR`) |
| Codex | `~/.codex` (or `CODEX_HOME`) |
| Antigravity | `~/.gemini/antigravity`, `~/.gemini/antigravity-cli` |
| Pi | `~/.pi/agent` |

agdash's own store is in `~/Library/Application Support/agdash`. A session's
working folder is read from its records as text; agdash does not open it. The
open-file and working-folder probes read process information, not the files.

The one case that prompts: when `CLAUDE_CONFIG_DIR` or `CODEX_HOME` points into a
guarded folder, such as `~/Documents/claude`. macOS then asks once whether agdash
may use that folder. Allowing it is enough; agdash needs no Full Disk Access.
Refusing it means that harness's sessions are not read.
