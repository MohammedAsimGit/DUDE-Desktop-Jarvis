//! Dude desktop host — Rust/Tauri layer (Phase 0).
//!
//! Responsibilities of this layer:
//! - own the desktop application lifecycle (window, webview, commands)
//! - spawn/supervise the Python engine child process (added in the engine
//!   integration step) and expose its status to the UI through Tauri commands
//!
//! The UI never talks to the engine directly; all engine access goes through
//! commands defined here. See docs/ARCHITECTURE.md for the process model.

use serde::Serialize;

/// High-level engine connection state exposed to the UI.
/// Mirrors `EngineState` in `src/types.ts`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum EngineState {
    Starting,
    Connected,
    Disconnected,
}

/// Status payload returned by the `engine_status` command.
/// Mirrors `EngineStatus` in `src/types.ts`.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EngineStatus {
    pub state: EngineState,
    /// Safe, human-readable detail. Never contains secrets or raw stderr.
    pub detail: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub protocol_version: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub engine_version: Option<String>,
}

/// Phase 0 stub: reports the engine as disconnected until the engine
/// integration step wires the real child-process lifecycle in.
#[tauri::command]
fn engine_status() -> EngineStatus {
    EngineStatus {
        state: EngineState::Disconnected,
        detail: "Engine integration lands in the next step of Phase 0.".into(),
        protocol_version: None,
        engine_version: None,
    }
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
        .invoke_handler(tauri::generate_handler![engine_status])
        .run(tauri::generate_context!())
        .expect("error while running Dude desktop host");
}
