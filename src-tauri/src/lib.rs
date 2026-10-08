//! Dude desktop host — Rust/Tauri layer (Phase 0).
//!
//! Responsibilities of this layer:
//! - own the desktop application lifecycle (window, webview, commands)
//! - spawn/supervise the Python engine child process and expose its status to
//!   the UI through Tauri commands
//!
//! The UI never talks to the engine directly; all engine access goes through
//! commands defined here. See docs/ARCHITECTURE.md for the process model.

pub mod engine;
pub mod window;

use engine::{EngineState, Status};
use std::sync::atomic::{AtomicBool, Ordering};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::Manager;

/// Set once the user chooses Quit from the tray so the close-to-hide
/// interception lets the final shutdown close through instead of hiding
/// the window again.
static QUITTING: AtomicBool = AtomicBool::new(false);

/// UI-facing status command. Reads a cached snapshot; never blocks on IPC.
/// The snapshot is refreshed by a periodic health monitor, so the value can
/// lag by up to one monitor interval (5 s) after a state change.
#[tauri::command]
fn engine_status(state: tauri::State<EngineState>) -> Status {
    state.status()
}

/// On-demand health check: pings the live engine right now and updates the
/// cached status. Used by the UI's Refresh button.
#[tauri::command]
fn engine_health(state: tauri::State<EngineState>) -> Status {
    state.health_check()
}

pub fn run() {
    // Diagnostic logs go to stderr with timestamps, severity, and module path
    // (component). This keeps stderr clean for humans and leaves stdout free
    // for the engine IPC channel.
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info"))
        .format_timestamp_secs()
        .target(env_logger::Target::Stderr)
        .init();

    log::info!("Dude desktop host starting (Phase 0)");

    tauri::Builder::default()
        .manage(EngineState::default())
        // Opt-in Windows startup (off by default; see docs/DEVELOPMENT.md).
        // The plugin only writes the documented per-user Run key when the
        // UI explicitly enables it — nothing persists without user action.
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .setup(|app| {
            // Sprint 1: the window is created hidden (tauri.conf.json
            // `visible: false`); place it inside the primary work area —
            // near the bottom-right, clear of the taskbar — before showing
            // it, so it never appears at an invalid or off-screen position.
            match app.get_webview_window("main") {
                Some(win) => {
                    window::place_companion(&win);
                    if let Err(err) = win.show() {
                        log::error!("could not show companion window: {err}");
                    } else if let Err(err) = win.set_focus() {
                        log::warn!("could not focus companion window: {err}");
                    }
                }
                None => log::error!("main window not found; companion will not be shown"),
            }

            // System tray: exactly the three essential actions. The menu
            // opens on right-click; left-click reveals the companion.
            let show_item = MenuItem::with_id(app, "show", "Show Dude", true, None::<&str>)?;
            let hide_item = MenuItem::with_id(app, "hide", "Hide Dude", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "Quit Dude", true, None::<&str>)?;
            let tray_menu = Menu::with_items(app, &[&show_item, &hide_item, &quit_item])?;
            let tray_click_app = app.handle().clone();
            let tray = TrayIconBuilder::with_id("main-tray")
                .tooltip("Dude")
                .menu(&tray_menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "show" => window::show_companion(app),
                    "hide" => window::hide_companion(app),
                    "quit" => {
                        QUITTING.store(true, Ordering::SeqCst);
                        app.exit(0);
                    }
                    other => log::warn!("unhandled tray menu id: {other}"),
                })
                .on_tray_icon_event(move |_tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        window::show_companion(&tray_click_app);
                    }
                });
            let tray = match app.default_window_icon() {
                Some(icon) => tray.icon(icon.clone()),
                None => tray,
            };
            if let Err(err) = tray.build(app) {
                // The app must keep working without a tray (e.g. restricted
                // shell); surface the failure in the log instead of dying.
                log::error!("system tray unavailable: {err}");
            }

            // Start the engine on a background thread: window shows immediately
            // with a "starting" status, then flips to connected/disconnected.
            // `app_handle.clone()` gives the threads 'static borrows into
            // app-managed state.
            let start_handle = app.handle().clone();
            std::thread::spawn(move || {
                use tauri::Manager;
                start_handle.state::<EngineState>().start();
            });

            // Periodic health monitor: keeps the cached status honest.
            let monitor_handle = app.handle().clone();
            std::thread::spawn(move || loop {
                std::thread::sleep(std::time::Duration::from_secs(5));
                use tauri::Manager;
                monitor_handle.state::<EngineState>().health_check();
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            engine_status,
            engine_health,
            window::set_companion_expanded,
            window::set_companion_topmost
        ])
        .build(tauri::generate_context!())
        .expect("error while building Dude desktop host")
        .run(|app_handle, event| match event {
            // Frameless windows have no titlebar close button, but Alt+F4
            // still arrives here: hide to the tray instead of exiting so the
            // engine keeps serving. Quit (tray) is the real exit path.
            tauri::RunEvent::WindowEvent {
                event: tauri::WindowEvent::CloseRequested { api, .. },
                ..
            } if !QUITTING.load(Ordering::SeqCst) => {
                api.prevent_close();
                log::info!("close requested; hiding Dude (quit from the tray)");
                window::hide_companion(app_handle);
            }
            tauri::RunEvent::Exit => {
                log::info!("Dude desktop host exiting; stopping engine…");
                app_handle.state::<EngineState>().shutdown();
            }
            _ => {}
        });
}
