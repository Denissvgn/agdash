//! Updating the app from its tray menu, and only when the person asks.
//!
//! The app makes no request of its own to anywhere but agdash's service on loopback.
//! "Check for updates" asks the release page once; "Install" downloads the release,
//! checks its signature against the public key the app was built with, replaces the
//! app and starts it again. The `.app` and the AppImage update this way; a `.deb` is
//! updated by installing the new package.

use std::path::Path;

use serde_json::Value;

/// Where the update check is, as the menu shows it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Step {
    /// Nothing asked yet.
    Idle,
    Checking,
    /// The release page names no newer version.
    Current,
    /// This version is newer and can be installed.
    Found(String),
    Installing(String),
    /// The check or the install did not work; the item checks again.
    Failed,
}

/// The menu item's words for a step. `current` is this app's version.
pub fn label(step: &Step, current: &str) -> String {
    match step {
        Step::Idle => "Check for updates".into(),
        Step::Checking => "Checking for updates…".into(),
        Step::Current => format!("agdash {current} is the latest: check again"),
        Step::Found(version) => format!("Install agdash {version} and restart"),
        Step::Installing(version) => format!("Installing agdash {version}…"),
        Step::Failed => "Could not update: check again".into(),
    }
}

/// Whether the item can be chosen: not while it is already working.
pub fn enabled(step: &Step) -> bool {
    !matches!(step, Step::Checking | Step::Installing(_))
}

/// What choosing the item does at a step.
#[derive(Debug, PartialEq, Eq)]
pub enum Choice {
    Check,
    Install,
    Nothing,
}

pub fn choice(step: &Step) -> Choice {
    match step {
        Step::Found(_) => Choice::Install,
        Step::Checking | Step::Installing(_) => Choice::Nothing,
        _ => Choice::Check,
    }
}

/// The updater's public key from the app's plugin settings (`plugins.updater.pubkey`),
/// when one is set. A build without one offers no updates.
pub fn public_key(plugins: &Value) -> Option<String> {
    plugins
        .get("updater")?
        .get("pubkey")?
        .as_str()
        .map(str::trim)
        .filter(|key| !key.is_empty())
        .map(str::to_string)
}

/// Whether to offer updates: the build carries a public key, and the app is one the
/// updater can replace in place (the macOS `.app` or an AppImage).
pub fn offered(public_key: Option<&str>, replaceable: bool) -> bool {
    public_key.is_some() && replaceable
}

/// Whether the updater can replace this app in place: on macOS an app run from inside
/// its `.app` bundle, elsewhere an AppImage (`APPIMAGE` is set). A development build or
/// a `.deb` install is not.
pub fn replaceable(macos: bool, exe: &Path, appimage: bool) -> bool {
    if macos {
        exe.parent()
            .filter(|dir| dir.ends_with("Contents/MacOS"))
            .and_then(|dir| dir.parent()?.parent())
            .and_then(|bundle| bundle.extension())
            .is_some_and(|extension| extension == "app")
    } else {
        appimage
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn the_item_says_where_the_check_is() {
        assert_eq!(label(&Step::Idle, "0.1.0"), "Check for updates");
        assert_eq!(
            label(&Step::Current, "0.1.0"),
            "agdash 0.1.0 is the latest: check again"
        );
        assert_eq!(
            label(&Step::Found("0.2.0".into()), "0.1.0"),
            "Install agdash 0.2.0 and restart"
        );
        assert_eq!(
            label(&Step::Installing("0.2.0".into()), "0.1.0"),
            "Installing agdash 0.2.0…"
        );
    }

    #[test]
    fn a_found_version_installs_and_anything_else_checks() {
        assert_eq!(choice(&Step::Idle), Choice::Check);
        assert_eq!(choice(&Step::Current), Choice::Check);
        assert_eq!(choice(&Step::Failed), Choice::Check);
        assert_eq!(choice(&Step::Found("0.2.0".into())), Choice::Install);
        assert_eq!(choice(&Step::Checking), Choice::Nothing);
        assert_eq!(choice(&Step::Installing("0.2.0".into())), Choice::Nothing);
        assert!(!enabled(&Step::Checking));
        assert!(!enabled(&Step::Installing("0.2.0".into())));
        assert!(enabled(&Step::Found("0.2.0".into())));
    }

    #[test]
    fn updates_need_a_key_and_an_app_that_can_be_replaced() {
        let set = json!({"updater": {"pubkey": " key "}});
        let empty = json!({"updater": {"pubkey": ""}});
        assert_eq!(public_key(&set), Some("key".into()));
        assert_eq!(public_key(&empty), None);
        assert_eq!(public_key(&json!({})), None);
        assert!(offered(Some("key"), true));
        assert!(!offered(Some("key"), false));
        assert!(!offered(None, true));
    }

    #[test]
    fn only_a_bundled_app_or_an_appimage_is_replaced() {
        let bundled = Path::new("/Applications/agdash.app/Contents/MacOS/agdash-app");
        let dev = Path::new("/src/agdash/desktop/target/debug/agdash-app");
        assert!(replaceable(true, bundled, false));
        assert!(!replaceable(true, dev, false));
        assert!(replaceable(
            false,
            Path::new("/tmp/.mount_x/usr/bin/agdash-app"),
            true
        ));
        assert!(!replaceable(false, Path::new("/usr/bin/agdash-app"), false));
    }

    #[test]
    fn the_shipped_settings_ask_for_signed_versions_from_the_release_page() {
        let config: Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let updater = &config["plugins"]["updater"];
        assert_eq!(updater["requireSignedVersion"], json!(true));
        assert_eq!(
            updater["endpoints"],
            json!(["https://github.com/Denissvgn/agdash/releases/latest/download/latest.json"])
        );
    }
}
