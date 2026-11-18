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

/// What the service's port says when asked for its health.
#[derive(Debug, PartialEq, Eq)]
pub enum Answer {
    /// The service runs and answers.
    Ready,
    /// The service runs but is still bringing its store up to date, which takes
    /// minutes on a large one. It answers every other request with "ask again".
    Preparing,
    /// Nothing answers, or not as agdash does.
    Nothing,
}

/// Ask the port for the service's health.
pub fn answer(port: u16) -> Answer {
    match http::get(port, "/health", None, Duration::from_secs(2)) {
        Ok((200, body)) if body.contains("\"ok\"") => Answer::Ready,
        Ok((200, body)) if body.contains("\"preparing\"") => Answer::Preparing,
        _ => Answer::Nothing,
    }
}

/// Whether the service answers its health check.
pub fn healthy(port: u16) -> bool {
    answer(port) == Answer::Ready
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

/// The agdash command to run. Inside an AppImage the bundled one lives in a mount that
/// goes away when the app exits, so a service started from it, or set to start at login,
/// would lose its binary. There the bundled command is copied to
/// `<data home>/agdash/bin/agdash`, once per version, and that copy is used.
pub fn settle_bin(bin: &Path, appimage: bool, data_home: &Path) -> PathBuf {
    if !appimage || !bin.is_file() {
        return bin.to_path_buf();
    }
    let target = data_home.join("agdash").join("bin").join("agdash");
    if same_file_contents(bin, &target) {
        return target;
    }
    match copy_executable(bin, &target) {
        Ok(()) => target,
        Err(_) => bin.to_path_buf(), // better a binary that goes away with the app than none
    }
}

fn same_file_contents(a: &Path, b: &Path) -> bool {
    match (std::fs::read(a), std::fs::read(b)) {
        (Ok(left), Ok(right)) => left == right,
        _ => false,
    }
}

fn copy_executable(from: &Path, to: &Path) -> std::io::Result<()> {
    let dir = to.parent().expect("a file in a folder");
    std::fs::create_dir_all(dir)?;
    let partial = dir.join(".agdash.partial");
    std::fs::copy(from, &partial)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&partial, std::fs::Permissions::from_mode(0o755))?;
    }
    std::fs::rename(&partial, to)
}

/// Where per-user data goes: `XDG_DATA_HOME`, else `~/.local/share`.
pub fn data_home(xdg: Option<String>, home: &Path) -> PathBuf {
    xdg.filter(|value| value.starts_with('/'))
        .map(PathBuf::from)
        .unwrap_or_else(|| home.join(".local").join("share"))
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
    /// A service runs and is preparing its store. Nothing more is started and nothing
    /// is waited for: the UI says so and loads by itself once the service is ready.
    Preparing,
}

/// Attach to a running service, or start it and wait until it answers.
pub fn ensure_running(
    port: u16,
    bin: &Path,
    manager: Option<Vec<String>>,
) -> Result<Running, String> {
    match answer(port) {
        Answer::Ready => return Ok(Running::Already),
        Answer::Preparing => return Ok(Running::Preparing),
        Answer::Nothing => {}
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
        match answer(port) {
            Answer::Ready => return Ok(how),
            Answer::Preparing => return Ok(Running::Preparing),
            Answer::Nothing => sleep(Duration::from_millis(250)),
        }
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

/// Whether the window may save what the page asks it to. Only a file the page itself
/// made from the service's answer, such as a session export: its address starts with
/// `blob:`. Anything fetched from elsewhere is refused.
pub fn page_made(address: &str) -> bool {
    address.starts_with("blob:")
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
    fn an_appimage_runs_a_copy_of_its_service_that_outlives_it() {
        let dir = scratch("appimage");
        let bundled = dir.join("mount/usr/bin/agdash");
        fs::create_dir_all(bundled.parent().unwrap()).unwrap();
        fs::write(&bundled, b"service v1").unwrap();
        let data = dir.join("data");
        // Outside an AppImage nothing is copied.
        assert_eq!(settle_bin(&bundled, false, &data), bundled);
        assert!(!data.exists());
        let settled = settle_bin(&bundled, true, &data);
        assert_eq!(settled, data.join("agdash/bin/agdash"));
        assert_eq!(fs::read(&settled).unwrap(), b"service v1");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(&settled).unwrap().permissions().mode() & 0o777,
                0o755
            );
        }
        // A newer AppImage replaces the copy; the same one leaves it alone.
        fs::write(&bundled, b"service v2").unwrap();
        assert_eq!(
            fs::read(settle_bin(&bundled, true, &data)).unwrap(),
            b"service v2"
        );
        assert_eq!(settle_bin(&bundled, true, &data), settled);
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn data_goes_where_xdg_says() {
        let home = Path::new("/home/someone");
        assert_eq!(
            data_home(None, home),
            PathBuf::from("/home/someone/.local/share")
        );
        assert_eq!(
            data_home(Some("/srv/data".into()), home),
            PathBuf::from("/srv/data")
        );
        assert_eq!(
            data_home(Some("relative".into()), home),
            PathBuf::from("/home/someone/.local/share")
        );
    }

    #[test]
    fn only_a_file_the_page_made_may_be_saved() {
        assert!(page_made("blob:tauri://localhost/0f6e4c1a-synthetic"));
        assert!(page_made("blob:http://localhost:5174/0f6e4c1a-synthetic"));
        assert!(!page_made("http://127.0.0.1:8765/api/sessions/1/export"));
        assert!(!page_made("https://example.invalid/file.md"));
        assert!(!page_made("data:text/plain,synthetic"));
        assert!(!page_made(""));
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

    #[test]
    fn a_service_preparing_its_store_is_neither_started_again_nor_waited_for() {
        use std::io::{Read, Write};
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            for _ in 0..2 {
                let (mut stream, _) = listener.accept().unwrap();
                let mut buffer = [0u8; 1024];
                let _ = stream.read(&mut buffer);
                stream
                    .write_all(
                        b"HTTP/1.1 200 OK\r\ncontent-length: 22\r\n\r\n{\"status\":\"preparing\"}",
                    )
                    .unwrap();
            }
        });
        assert_eq!(answer(port), Answer::Preparing);
        let missing = PathBuf::from("/nonexistent/agdash");
        let started = Instant::now();
        assert_eq!(ensure_running(port, &missing, None), Ok(Running::Preparing));
        assert!(started.elapsed() < Duration::from_secs(5));
        server.join().unwrap();
        // With nothing on the port there is no answer at all.
        assert_eq!(answer(port), Answer::Nothing);
    }
}
