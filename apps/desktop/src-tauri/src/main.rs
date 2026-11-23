//! De desktop-app van het platform voor Windows en Linux (fase 11, stap 11).
//!
//! - **Het venster** toont de web-app van het platform zelf (bestanden, foto's, media met de
//!   speler, upscaling in de speler, ondertitels): wat de web-app krijgt, krijgt de app meteen.
//!   Eerst een eigen pagina (ui/index.html) om het adres te kiezen, in te loggen voor het
//!   synchroniseren en de map te kiezen.
//! - **Synchroniseren** (sync.rs) loopt op de achtergrond: elke `interval_min` minuten, en een
//!   paar seconden na een wijziging in de map. Het venster sluiten laat de app in het systeemvak
//!   doorlopen; Afsluiten in het menu daar stopt ze.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod api;
mod config;
mod sync;

use notify::{RecursiveMode, Watcher};
use serde::Serialize;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, State, Url, WebviewWindow};
use tokio::sync::{Mutex, Notify};

struct App {
    api: api::Api,
    wake: Notify,
    running: Mutex<bool>,
    last: Mutex<Option<sync::Counts>>,
    now: Mutex<String>,
    watcher: std::sync::Mutex<Option<notify::RecommendedWatcher>>,
}

type Shared = Arc<App>;

#[derive(Serialize)]
struct Status {
    server: Option<String>,
    user: Option<String>,
    logged_in: bool,
    folder: Option<String>,
    interval_min: u64,
    last_run: Option<u64>,
    last_error: Option<String>,
    running: bool,
    current: String,
    counts: Option<sync::Counts>,
}

fn err(e: impl std::fmt::Display) -> String {
    e.to_string()
}

#[tauri::command]
async fn status(app: State<'_, Shared>) -> Result<Status, String> {
    let cfg = config::load();
    Ok(Status {
        logged_in: cfg.refresh_token.is_some(),
        server: cfg.server,
        user: cfg.user,
        folder: cfg.folder.map(|p| p.display().to_string()),
        interval_min: cfg.interval_min,
        last_run: cfg.last_run,
        last_error: cfg.last_error,
        running: *app.running.lock().await,
        current: app.now.lock().await.clone(),
        counts: app.last.lock().await.clone(),
    })
}

#[tauri::command]
async fn set_server(app: State<'_, Shared>, url: String) -> Result<String, String> {
    let mut base = url.trim().trim_end_matches('/').to_owned();
    if !base.starts_with("http://") && !base.starts_with("https://") {
        base = format!("https://{base}");
    }
    app.api.info(&base).await.map_err(err)?;
    let mut cfg = config::load();
    if cfg.server.as_deref() != Some(base.as_str()) {
        cfg.refresh_token = None;
        cfg.user = None;
        cfg.remote_folder = None;
        sync::forget_state();
    }
    cfg.server = Some(base.clone());
    config::save(&cfg).map_err(err)?;
    Ok(base)
}

#[tauri::command]
async fn login(app: State<'_, Shared>, email: String, password: String, code: Option<String>) -> Result<String, String> {
    let code = code.filter(|c| !c.trim().is_empty());
    match app.api.login(email.trim(), &password, code.as_deref()).await {
        Ok(name) => {
            app.wake.notify_one();
            Ok(name)
        }
        Err(e) => {
            if let Some(a) = e.downcast_ref::<api::ApiError>() {
                if a.code.as_deref() == Some("totp_required") {
                    return Err("totp_required".into());
                }
            }
            Err(err(e))
        }
    }
}

#[tauri::command]
async fn logout(app: State<'_, Shared>) -> Result<(), String> {
    app.api.logout().await.map_err(err)
}

#[tauri::command]
async fn choose_folder(handle: AppHandle, app: State<'_, Shared>) -> Result<Option<String>, String> {
    use tauri_plugin_dialog::DialogExt;
    let picked = handle.dialog().file().blocking_pick_folder();
    let Some(picked) = picked else { return Ok(None) };
    let path = picked.into_path().map_err(err)?;
    let mut cfg = config::load();
    cfg.folder = Some(path.clone());
    cfg.remote_folder = None;
    config::save(&cfg).map_err(err)?;
    sync::forget_state();
    watch(&app);
    app.wake.notify_one();
    Ok(Some(path.display().to_string()))
}

#[tauri::command]
async fn clear_folder(app: State<'_, Shared>) -> Result<(), String> {
    let mut cfg = config::load();
    cfg.folder = None;
    cfg.remote_folder = None;
    config::save(&cfg).map_err(err)?;
    sync::forget_state();
    watch(&app);
    Ok(())
}

#[tauri::command]
async fn set_interval(minutes: u64) -> Result<(), String> {
    let mut cfg = config::load();
    cfg.interval_min = minutes.clamp(1, 1440);
    config::save(&cfg).map_err(err)
}

#[tauri::command]
async fn sync_now(app: State<'_, Shared>) -> Result<(), String> {
    app.wake.notify_one();
    Ok(())
}

#[tauri::command]
async fn open_platform(window: WebviewWindow) -> Result<(), String> {
    open_remote(&window)
}

fn open_remote(window: &WebviewWindow) -> Result<(), String> {
    let server = config::load().server.ok_or("Geen platform gekozen")?;
    let url = Url::parse(&server).map_err(err)?;
    window.navigate(url).map_err(err)
}

/// De eigen pagina van de app (instellingen).
fn open_settings(window: &WebviewWindow) {
    let local = if cfg!(windows) { "http://tauri.localhost/index.html" } else { "tauri://localhost/index.html" };
    if let Ok(url) = Url::parse(local) {
        let _ = window.navigate(url);
    }
}

fn show(handle: &AppHandle) -> Option<WebviewWindow> {
    let w = handle.get_webview_window("main")?;
    let _ = w.show();
    let _ = w.unminimize();
    let _ = w.set_focus();
    Some(w)
}

/// De map bewaken: een wijziging maakt de ronde wakker (na een korte pauze in de lus).
fn watch(app: &Shared) {
    let mut slot = app.watcher.lock().unwrap();
    *slot = None;
    let Some(folder) = config::load().folder else { return };
    let me = app.clone();
    let made = notify::recommended_watcher(move |res: notify::Result<notify::Event>| {
        if let Ok(ev) = res {
            let ours = ev.paths.iter().all(|p| {
                p.file_name().and_then(|n| n.to_str()).is_some_and(|n| n.ends_with(".platform-tmp"))
            });
            if !ours && !ev.kind.is_access() {
                me.wake.notify_one();
            }
        }
    });
    if let Ok(mut w) = made {
        if w.watch(&folder, RecursiveMode::Recursive).is_ok() {
            *slot = Some(w);
        }
    }
}

async fn sync_loop(handle: AppHandle, app: Shared) {
    loop {
        let interval = config::load().interval_min.max(1);
        tokio::select! {
            _ = tokio::time::sleep(Duration::from_secs(interval * 60)) => {}
            _ = app.wake.notified() => {
                // Even wachten: een programma dat opslaat, schrijft vaak in stukken
                tokio::time::sleep(Duration::from_secs(4)).await;
            }
        }
        *app.running.lock().await = true;
        let _ = handle.emit("sync", "start");
        let progress_app = app.clone();
        let progress = move |p: &str| {
            if let Ok(mut now) = progress_app.now.try_lock() {
                *now = p.to_owned();
            }
        };
        let result = sync::run(&app.api, &progress).await;
        let mut cfg = config::load();
        match result {
            Ok(Some(c)) => {
                cfg.last_run = Some(SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0));
                cfg.last_error = None;
                *app.last.lock().await = Some(c);
            }
            Ok(None) => {}
            Err(e) => cfg.last_error = Some(e.to_string()),
        }
        let _ = config::save(&cfg);
        *app.running.lock().await = false;
        app.now.lock().await.clear();
        let _ = handle.emit("sync", "done");
    }
}

fn main() {
    let shared: Shared = Arc::new(App {
        api: api::Api::new(),
        wake: Notify::new(),
        running: Mutex::new(false),
        last: Mutex::new(None),
        now: Mutex::new(String::new()),
        watcher: std::sync::Mutex::new(None),
    });
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|handle, _, _| {
            show(handle);
        }))
        .plugin(tauri_plugin_dialog::init())
        .manage(shared.clone())
        .invoke_handler(tauri::generate_handler![
            status,
            set_server,
            login,
            logout,
            choose_folder,
            clear_folder,
            set_interval,
            sync_now,
            open_platform
        ])
        .setup(move |app| {
            let open = MenuItem::with_id(app, "open", "Platform openen", true, None::<&str>)?;
            let settings = MenuItem::with_id(app, "settings", "Instellingen", true, None::<&str>)?;
            let now = MenuItem::with_id(app, "sync", "Nu synchroniseren", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Afsluiten", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &settings, &now, &quit])?;
            let tray_app = shared.clone();
            TrayIconBuilder::with_id("tray")
                .icon(app.default_window_icon().cloned().expect("icoon"))
                .tooltip("Platform")
                .menu(&menu)
                .on_menu_event(move |handle, event| match event.id().as_ref() {
                    "open" => {
                        if let Some(w) = show(handle) {
                            let _ = open_remote(&w);
                        }
                    }
                    "settings" => {
                        if let Some(w) = show(handle) {
                            open_settings(&w);
                        }
                    }
                    "sync" => tray_app.wake.notify_one(),
                    "quit" => handle.exit(0),
                    _ => {}
                })
                .build(app)?;
            watch(&shared);
            let handle = app.handle().clone();
            let loop_app = shared.clone();
            tauri::async_runtime::spawn(async move { sync_loop(handle, loop_app).await });
            // Ingelogd en een adres: meteen naar het platform
            let cfg = config::load();
            if cfg.server.is_some() && cfg.refresh_token.is_some() {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = open_remote(&w);
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // Sluiten: naar het systeemvak; synchroniseren loopt door
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("de app kon niet starten");
}
