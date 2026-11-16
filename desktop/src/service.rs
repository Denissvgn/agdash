//! Attach to the agdash service, or start it, and hand the app its local token.
//!
//! The app never stops the service: a service it starts runs in its own process
//! group, or under the system's service manager, so quitting the app leaves it
//! running.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread::sleep;
use std::time::{Duration, Instant};

use crate::http;

pub const DEFAULT_PORT: u16 = 8765;
pub const LAUNCHD_LABEL: &str = "dev.agdash.agdashd";
pub const SYSTEMD_UNIT: &str = "agdashd.service";
const START_WAIT: Duration = Duration::from_secs(20);

/// The port the service answers on: `AGDASH_PORT`, else the default.
pub fn port_from(value: Option<String>) -> u16 {
    value
        .and_then(|text| text.trim().parse().ok())
        .unwrap_or(DEFAULT_PORT)
}

/// Whether the service answers its health check.
pub fn healthy(port: u16) -> bool {
    matches!(
        http::get(port, "/health", None, Duration::from_secs(2)),
        Ok((200, body)) if body.contains("\"ok\"")
    )
}

/// The agdash command: `AGDASH_BIN`, else the one bundled next to the app, else `agdash`.
pub fn find_bin(env_bin: Option<String>, app_exe: Option<PathBuf>) -> PathBuf {
    if let Some(path) = env_bin.filter(|value| !value.is_empty()) {
        return PathBuf::from(path);
    }
    if let Some(sibling) = app_exe.and_then(|exe| exe.parent().map(|dir| dir.join("agdash"))) {
        if sibling.is_file() {
            return sibling;
        }
    }
    PathBuf::from("agdash")
}

/// How to start the installed service, when it is installed: through the system's
/// service manager, so it runs as it does at login.
pub fn manager_command(macos: bool, home: &Path, uid: u32) -> Option<Vec<String>> {
    if macos {
        let plist = home
            .join("Library/LaunchAgents")
            .join(format!("{LAUNCHD_LABEL}.plist"));
        plist.is_file().then(|| {
            vec![
                "launchctl".into(),
                "kickstart".into(),
                format!("gui/{uid}/{LAUNCHD_LABEL}"),
            ]
        })
    } else {
        let unit = home.join(".config/systemd/user").join(SYSTEMD_UNIT);
        unit.is_file().then(|| {
            vec![
                "systemctl".into(),
                "--user".into(),
                "start".into(),
                SYSTEMD_UNIT.into(),
            ]
        })
    }
}

/// How the service came to be running.
#[derive(Debug, PartialEq, Eq)]
pub enum Running {
    Already,
    StartedByManager,
    StartedHere,
}

/// Attach to a running service, or start it and wait until it answers.
pub fn ensure_running(
    port: u16,
    bin: &Path,
    manager: Option<Vec<String>>,
) -> Result<Running, String> {
    if healthy(port) {
        return Ok(Running::Already);
    }
    let how = match manager {
        Some(command) => {
            let status = Command::new(&command[0])
                .args(&command[1..])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()
                .map_err(|error| format!("could not ask the service manager: {error}"))?;
            if !status.success() {
                return Err("the service manager could not start the service".into());
            }
            Running::StartedByManager
        }
        None => {
            spawn_detached(bin)?;
            Running::StartedHere
        }
    };
    let deadline = Instant::now() + START_WAIT;
    while Instant::now() < deadline {
        if healthy(port) {
            return Ok(how);
        }
        sleep(Duration::from_millis(250));
    }
    Err("the service did not answer in time".into())
}

fn spawn_detached(bin: &Path) -> Result<(), String> {
    let mut command = Command::new(bin);
    command
        .arg("serve")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0); // its own group: signals to the app do not reach it
    }
    command
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("could not start {}: {error}", bin.display()))
}

/// The local API token, which `agdash token` writes to a pipe from agdash's secret store.
pub fn token(bin: &Path) -> Result<String, String> {
    let output = Command::new(bin)
        .arg("token")
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .map_err(|error| format!("could not run {}: {error}", bin.display()))?;
    if !output.status.success() {
        return Err("agdash did not hand over the local token".into());
    }
    let token =
        String::from_utf8(output.stdout).map_err(|_| "the token is not text".to_string())?;
    let token = token.trim().to_string();
    if token.is_empty() {
        return Err("agdash handed over an empty token".into());
    }
    Ok(token)
}

/// The script the window runs before the UI loads, so the UI finds the service.
pub fn init_script(port: u16, token: Option<&str>) -> String {
    let config = serde_json::json!({
        "apiBase": format!("http://127.0.0.1:{port}"),
        "token": token,
    });
    format!("window.__AGDASH__ = Object.freeze({config});")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn scratch(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("agdash-desktop-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn the_port_comes_from_the_environment_or_the_default() {
        assert_eq!(port_from(None), 8765);
        assert_eq!(port_from(Some("9001".into())), 9001);
        assert_eq!(port_from(Some("nope".into())), 8765);
    }

    #[test]
    fn the_installed_service_is_started_through_its_manager() {
        let home = scratch("manager");
        assert_eq!(manager_command(true, &home, 501), None);
        assert_eq!(manager_command(false, &home, 501), None);
        fs::create_dir_all(home.join("Library/LaunchAgents")).unwrap();
        fs::write(
            home.join("Library/LaunchAgents/dev.agdash.agdashd.plist"),
            "",
        )
        .unwrap();
        assert_eq!(
            manager_command(true, &home, 501),
            Some(vec![
                "launchctl".into(),
                "kickstart".into(),
                "gui/501/dev.agdash.agdashd".into()
            ])
        );
        fs::create_dir_all(home.join(".config/systemd/user")).unwrap();
        fs::write(home.join(".config/systemd/user/agdashd.service"), "").unwrap();
        assert_eq!(
            manager_command(false, &home, 501),
            Some(vec![
                "systemctl".into(),
                "--user".into(),
                "start".into(),
                "agdashd.service".into()
            ])
        );
        fs::remove_dir_all(home).unwrap();
    }

    #[test]
    fn the_command_is_found_by_setting_then_beside_the_app() {
        let dir = scratch("bin");
        let app = dir.join("agdash-app");
        assert_eq!(
            find_bin(Some("/opt/agdash/agdash".into()), Some(app.clone())),
            PathBuf::from("/opt/agdash/agdash")
        );
        assert_eq!(find_bin(None, Some(app.clone())), PathBuf::from("agdash"));
        fs::write(dir.join("agdash"), "").unwrap();
        assert_eq!(find_bin(None, Some(app)), dir.join("agdash"));
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn the_token_reaches_the_ui_as_data_never_as_code() {
        let script = init_script(8765, Some("ab\"c</script>\\"));
        assert!(script.starts_with("window.__AGDASH__ = Object.freeze({"));
        let json = script
            .trim_start_matches("window.__AGDASH__ = Object.freeze(")
            .trim_end_matches(");");
        let parsed: serde_json::Value = serde_json::from_str(json).unwrap();
        assert_eq!(parsed["token"], "ab\"c</script>\\");
        assert_eq!(parsed["apiBase"], "http://127.0.0.1:8765");
        assert!(init_script(8765, None).contains("\"token\":null"));
    }

    #[cfg(unix)]
    #[test]
    fn the_token_is_taken_from_agdash_token() {
        use std::os::unix::fs::PermissionsExt;
        let dir = scratch("token");
        let fake = dir.join("agdash");
        fs::write(
            &fake,
            "#!/bin/sh\n[ \"$1\" = token ] && printf 'synthetic-token\\n' && exit 0\nexit 3\n",
        )
        .unwrap();
        fs::set_permissions(&fake, fs::Permissions::from_mode(0o755)).unwrap();
        assert_eq!(token(&fake), Ok("synthetic-token".to_string()));
        fs::write(&fake, "#!/bin/sh\nexit 2\n").unwrap();
        assert!(token(&fake).is_err());
        assert!(token(&dir.join("missing")).is_err());
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn a_service_that_answers_is_attached_to_without_starting_anything() {
        use std::io::{Read, Write};
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut buffer = [0u8; 1024];
            let _ = stream.read(&mut buffer);
            stream
                .write_all(b"HTTP/1.1 200 OK\r\ncontent-length: 15\r\n\r\n{\"status\":\"ok\"}")
                .unwrap();
        });
        let missing = PathBuf::from("/nonexistent/agdash");
        assert_eq!(ensure_running(port, &missing, None), Ok(Running::Already));
        server.join().unwrap();
    }
}
