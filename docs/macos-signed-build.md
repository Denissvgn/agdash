# Runbook: a signed and notarised macOS build

This takes agdash from a development build to a `.dmg` that opens on any Mac with no
Gatekeeper override, and shows that it does. It runs on your own Mac with your own
Apple account. [macos-package.md](macos-package.md) explains what the build does;
this page is the order to do it in and what to check at each step. To sign in CI
instead, see [releasing.md](releasing.md).

Allow an hour the first time. Most of it is Apple's setup and the notarisation wait.

## What you need

- A paid **Apple Developer Program** membership. Only the account holder can create
  a Developer ID certificate.
- Xcode, or its command line tools (`xcode-select --install`).
- This repository set up for packaging:

  ```bash
  python3 -m venv .venv && .venv/bin/pip install -e "backend[dev,freeze]"
  (cd frontend && npm ci)
  ```

- A clean Mac for the last check: another Mac, a macOS virtual machine, or a new
  user account on this Mac. It must never have run agdash.

## 1. The Developer ID certificate (once)

In Xcode: **Settings → Accounts**, choose your team, **Manage Certificates → + →
Developer ID Application**. Xcode creates the key in your login keychain and
installs the certificate.

Check:

```bash
security find-identity -v -p codesigning
```

Expect a line `"Developer ID Application: Your Name (TEAMID1234)"`. Copy the
quoted text: it is the identity used below. An "Apple Development" or "Apple
Distribution" identity is not enough; it must say Developer ID Application.

## 2. The notarisation profile (once)

At [account.apple.com](https://account.apple.com), under **Sign-In and Security →
App-Specific Passwords**, create one named `agdash-notary`. Then store it in your
keychain. The command asks for that password; nothing else ever sees it:

```bash
xcrun notarytool store-credentials agdash-notary --apple-id you@example.com --team-id TEAMID1234
```

Check:

```bash
xcrun notarytool history --keychain-profile agdash-notary
```

Expect a table, which is empty the first time, and no error.

## 3. Build, sign and notarise

Start from `main`, with a clean packaging folder:

```bash
rm -rf desktop/target/package
AGDASH_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID1234)" \
AGDASH_NOTARY_PROFILE=agdash-notary \
desktop/scripts/package-macos.sh 2>&1 | tee package-macos.log
```

macOS may ask once whether `codesign` may use the key in your keychain. Choose
**Always Allow**.

What to expect, in order:

1. `== Signing, inside out, as Developer ID Application: …`, ending with
   `agdash.app: valid on disk` and `satisfies its Designated Requirement`.
2. `== Notarising`, then a submission id and `status: Accepted`. This usually
   takes 2 to 15 minutes.
3. `The staple and validate action worked!` for the disk image and the app.
4. Two `spctl` lines that each say `accepted` and `source=Notarized Developer ID`.

If notarisation ends with `status: Invalid`, read Apple's reasons:

```bash
xcrun notarytool log <submission id> --keychain-profile agdash-notary
```

The usual causes are a nested binary that was not signed, or one that lacks the
hardened runtime or a secure timestamp. Send the log back rather than working
around it.

## 4. Check the build on this Mac

```bash
B=desktop/target/package/release/bundle
codesign --verify --deep --strict --verbose=2 "$B/macos/agdash.app"
codesign -dvv "$B/macos/agdash.app" 2>&1 | grep -E "^Authority=Developer ID|^TeamIdentifier|flags="
codesign -dvv "$B/macos/agdash.app/Contents/MacOS/agdash" 2>&1 | grep -E "^Authority=Developer ID|flags="
xcrun stapler validate "$B"/dmg/agdash-*.dmg
spctl -a -t open --context context:primary-signature -vv "$B"/dmg/agdash-*.dmg
spctl -a -t exec -vv "$B/macos/agdash.app"
```

| Check | Expected |
|---|---|
| `codesign --verify` | `valid on disk`, `satisfies its Designated Requirement` |
| The app and the service binary | `Authority=Developer ID Application: …`, and `flags=0x10000(runtime)` |
| `stapler validate` | `The validate action worked!` |
| Both `spctl` | `accepted`, `source=Notarized Developer ID` |

## 5. Install on a clean Mac (the acceptance check)

Gatekeeper judges only files that carry the quarantine mark a download gives them.
A copy over a network share or a USB disk can skip it and prove nothing. So move
the disk image the way a person would get it: upload it somewhere private (a draft
GitHub release, a file share) and download it with Safari on the clean Mac, or send
it there by AirDrop.

On the clean Mac:

1. Check the mark is there. The command prints a value; "No such xattr" means the
   test does not count:

   ```bash
   xattr -p com.apple.quarantine ~/Downloads/agdash-*.dmg
   ```

2. Open the disk image, drag agdash to Applications, and open it from Applications.
3. Expect only the usual first-open question, "agdash is an app downloaded from the
   Internet. Are you sure you want to open it?", which says Apple checked it. Choose
   **Open**.
4. It fails if macOS says it "cannot be opened", "cannot verify", or that Apple
   could not check it for malicious software. It also fails if you have to go to
   **System Settings → Privacy & Security → Open Anyway**.
5. The window opens and the menu-bar item appears. With no harnesses on that Mac,
   Live and History are empty; that is expected.

Write down the macOS version and Mac model, how the disk image arrived, and the
quarantine value.

## 6. Full Disk Access, on the Mac you work on

A clean Mac has no harness records, so check for privacy prompts on your own Mac.
First quit any development build. Then install the signed app to `/Applications`
from the disk image, and point the service at it:

```bash
/Applications/agdash.app/Contents/MacOS/agdash install
/Applications/agdash.app/Contents/MacOS/agdash doctor
```

Open agdash and use Live, History and a session's page for a few minutes.

- Expected: no privacy prompt, and agdash not needed in **System Settings → Privacy
  & Security → Full Disk Access**.
- A prompt about a folder is expected only when `CLAUDE_CONFIG_DIR` or `CODEX_HOME`
  points into Documents, Desktop or Downloads ([macos-package.md](macos-package.md)).
  Note any prompt's exact words, and which folder it names.

## 7. What to send back

- `package-macos.log`, or just its signing, notarisation and `spctl` lines.
- The output of step 4.
- From step 5: the macOS version, how the disk image arrived, the quarantine
  value, and which dialog appeared.
- From step 6: whether any prompt appeared, with its words.

That is the evidence that the build installs on a clean Mac and needs no Full Disk
Access.

## Afterwards

- The service carries one entitlement, `disable-library-validation`
  ([macos-package.md](macos-package.md)). Once a signed build runs, you can try
  without it: delete the entitlement from `desktop/entitlements/service.plist`,
  build again as in step 3, and see whether the service still starts
  (`agdash status`). If it does, keep it removed.
- To sign releases in CI, export the identity from Keychain Access (**My
  Certificates**, right-click the Developer ID identity, **Export**, as `.p12` with
  a password). Then add the secrets listed in [releasing.md](releasing.md). CI
  notarises with an App Store Connect API key rather than the app-specific password.
