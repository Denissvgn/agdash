//! The agdash desktop app: one window on the bundled UI and a tray item with the
//! number of live sessions. It attaches to the agdash service, or starts it, and
//! hands the UI the local token. Quitting the app leaves the service running. The tray
//! also offers updates, checked only when the person asks.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Mutex;
use std::time::{Duration, SystemTime};

use agdash_desktop::update::{self, Choice, Step};
use agdash_desktop::{http, live, service};
use tauri::menu::{IsMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::webview::DownloadEvent;
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent, Wry};
use tauri_plugin_updater::{Update, UpdaterExt};

const MAIN: &str = "main";
const TRAY: &str = "live";
const REFRESH: Duration = Duration::from_secs(5);
const VERSION: &str = env!("CARGO_PKG_VERSION");

/// The tray's update item and where its check is.
struct Updates {
    item: MenuItem<Wry>,
    step: Mutex<Step>,
    found: Mutex<Option<Update>>,
}

fn set_step(app: &AppHandle, step: Step) {
    let updates = app.state::<Updates>();
    let _ = updates.item.set_text(update::label(&step, VERSION));
    let _ = updates.item.set_enabled(update::enabled(&step));
    *updates.step.lock().unwrap() = step;
}

fn on_update(app: &AppHandle) {
    let step = app.state::<Updates>().step.lock().unwrap().clone();
    match update::choice(&step) {
        Choice::Check => check_for_update(app.clone()),
        Choice::Install => install_update(app.clone()),
        Choice::Nothing => {}
    }
}

fn check_for_update(app: AppHandle) {
    set_step(&app, Step::Checking);
    tauri::async_runtime::spawn(async move {
        let checked = match app.updater() {
            Ok(updater) => updater.check().await,
            Err(error) => Err(error),
        };
        let step = match checked {
            Ok(Some(found)) => {
                let version = found.version.clone();
                *app.state::<Updates>().found.lock().unwrap() = Some(found);
                Step::Found(version)
            }
            Ok(None) => Step::Current,
            Err(error) => {
                eprintln!("agdash: the update check failed: {error}");
                Step::Failed
            }
        };
        set_step(&app, step);
    });
}

fn install_update(app: AppHandle) {
    let Some(found) = app.state::<Updates>().found.lock().unwrap().take() else {
        return check_for_update(app);
    };
    set_step(&app, Step::Installing(found.version.clone()));
    tauri::async_runtime::spawn(async move {
        // The signature is checked against the app's public key before anything is
        // replaced. The new app, once started, brings the service up to its version.
        match found.download_and_install(|_, _| {}, || {}).await {
            Ok(()) => app.restart(),
            Err(error) => {
                eprintln!("agdash: the update did not install: {error}");
                set_step(&app, Step::Failed);
            }
        }
    });
}

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
        .plugin(tauri_plugin_updater::Builder::new().build())
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
            let macos = cfg!(target_os = "macos");
            let uid = current_uid();
            let manager = service::manager_command(macos, &home, uid);
            // A service that cannot be reached is shown by the UI's own notices.
            let _ = service::ensure_running(port, &bin, manager);
            // After an update, a service left on the earlier version is restarted onto
            // the binary this app carries, when the system's service manager runs it.
            if let Err(error) = service::catch_up(
                port,
                VERSION,
                service::manager_restart_command(macos, &home, uid),
            ) {
                eprintln!("agdash: {error}");
            }
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
            let check = MenuItem::with_id(
                app,
                "update",
                update::label(&Step::Idle, VERSION),
                true,
                None::<&str>,
            )?;
            let offered = update::offered(
                update::public_key(&serde_json::to_value(&app.config().plugins)?).as_deref(),
                update::replaceable(
                    macos,
                    &std::env::current_exe().unwrap_or_default(),
                    std::env::var_os("APPIMAGE").is_some(),
                ),
            );
            let separator = PredefinedMenuItem::separator(app)?;
            let mut items: Vec<&dyn IsMenuItem<Wry>> = vec![&open];
            if offered {
                items.push(&check);
            }
            items.extend([&separator as &dyn IsMenuItem<Wry>, &quit]);
            let menu = Menu::with_items(app, &items)?;
            app.manage(Updates {
                item: check,
                step: Mutex::new(Step::Idle),
                found: Mutex::new(None),
            });
            let mut tray = TrayIconBuilder::with_id(TRAY)
                .menu(&menu)
                .tooltip(live::tooltip(None))
                .title(live::title(None))
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => show(app),
                    "update" => on_update(app),
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
