//! The agdash desktop app: one window on the bundled UI and a tray item with the
//! number of live sessions. It attaches to the agdash service, or starts it, and
//! hands the UI the local token. Quitting the app leaves the service running.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::time::{Duration, SystemTime};

use agdash_desktop::{http, live, service};
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::webview::DownloadEvent;
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent};

const MAIN: &str = "main";
const TRAY: &str = "live";
const REFRESH: Duration = Duration::from_secs(5);

fn show(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(MAIN) {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn live_count(port: u16, token: Option<&str>) -> Option<usize> {
    let pages: Vec<String> = live::queries(SystemTime::now())
        .iter()
        .map(
            |path| match http::get(port, path, token, Duration::from_secs(3)) {
                Ok((200, body)) => Some(body),
                _ => None,
            },
        )
        .collect::<Option<_>>()?;
    let pages: Vec<&str> = pages.iter().map(String::as_str).collect();
    live::count(&pages).ok()
}

fn watch(app: AppHandle, port: u16, token: Option<String>) {
    std::thread::spawn(move || loop {
        let count = live_count(port, token.as_deref());
        let handle = app.clone();
        let _ = app.run_on_main_thread(move || {
            if let Some(tray) = handle.tray_by_id(TRAY) {
                let _ = tray.set_title(Some(live::title(count)));
                let _ = tray.set_tooltip(Some(live::tooltip(count)));
            }
        });
        std::thread::sleep(REFRESH);
    });
}

fn main() {
    let port = service::port_from(std::env::var("AGDASH_PORT").ok());
    tauri::Builder::default()
        // A second launch brings the first one's window forward instead.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            show(app)
        }))
        .setup(move |app| {
            let home = app.path().home_dir().unwrap_or_default();
            let bin = service::settle_bin(
                &service::find_bin(
                    std::env::var("AGDASH_BIN").ok(),
                    std::env::current_exe().ok(),
                ),
                std::env::var_os("APPIMAGE").is_some(),
                &service::data_home(std::env::var("XDG_DATA_HOME").ok(), &home),
            );
            let manager = service::manager_command(cfg!(target_os = "macos"), &home, current_uid());
            // A service that cannot be reached is shown by the UI's own notices.
            let _ = service::ensure_running(port, &bin, manager);
            let token = service::token(&bin).ok();
            WebviewWindowBuilder::new(app, MAIN, WebviewUrl::App("index.html".into()))
                .title("agdash")
                .inner_size(1280.0, 860.0)
                .min_inner_size(375.0, 480.0)
                .initialization_script(service::init_script(port, token.as_deref()))
                // A session export: the page makes the file and the window saves it with
                // the person's downloads. Nothing else may be saved from here.
                .on_download(|_webview, event| match event {
                    DownloadEvent::Requested { url, .. } => service::page_made(url.as_str()),
                    _ => true,
                })
                .build()?;

            let open = MenuItem::with_id(app, "open", "Open agdash", true, None::<&str>)?;
            let quit = MenuItem::with_id(
                app,
                "quit",
                "Quit agdash (the service keeps running)",
                true,
                None::<&str>,
            )?;
            let menu =
                Menu::with_items(app, &[&open, &PredefinedMenuItem::separator(app)?, &quit])?;
            let mut tray = TrayIconBuilder::with_id(TRAY)
                .menu(&menu)
                .tooltip(live::tooltip(None))
                .title(live::title(None))
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => show(app),
                    "quit" => app.exit(0),
                    _ => {}
                });
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;
            watch(app.handle().clone(), port, token);
            Ok(())
        })
        // Closing the window keeps the app in the tray; Quit ends it.
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .run(tauri::generate_context!())
        .expect("the agdash app could not start");
}

#[cfg(unix)]
fn current_uid() -> u32 {
    std::process::Command::new("id")
        .arg("-u")
        .output()
        .ok()
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .and_then(|text| text.trim().parse().ok())
        .unwrap_or(0)
}

#[cfg(not(unix))]
fn current_uid() -> u32 {
    0
}
