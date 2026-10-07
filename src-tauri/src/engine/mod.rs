//! Dude engine integration: process management + protocol client (Phase 0).

pub mod client;
pub mod manager;

use manager::{EngineHandle, DEFAULT_REQUEST_TIMEOUT, DEFAULT_STARTUP_TIMEOUT};
use serde::Serialize;
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// Engine settings resolved from the environment (documented in DEVELOPMENT.md).
#[derive(Clone, Debug)]
pub struct EngineSettings {
    pub python_path: String,
    pub engine_dir: String,
}

impl EngineSettings {
    /// Resolve settings from environment with sane development defaults.
    ///
    /// In dev the Tauri process runs with CWD `src-tauri`, so relative engine
    /// dir paths resolve against the repo root (one ancestor up).
    /// `DUDE_PYTHON` overrides the interpreter and `DUDE_ENGINE_DIR` overrides
    /// the engine package directory (both used by smoke tests). Production
    /// packaging is a later phase; this seam is where a bundled engine would
    /// plug in.
    pub fn from_env() -> Self {
        let python_path = std::env::var("DUDE_PYTHON").unwrap_or_else(|_| "python".into());
        let engine_dir_var =
            std::env::var("DUDE_ENGINE_DIR").unwrap_or_else(|_| "engine".into());
        let dir = std::path::PathBuf::from(&engine_dir_var);
        let engine_dir = if dir.is_absolute() {
            dir
        } else {
            let repo_root = std::env::current_dir()
                .ok()
                .and_then(|cwd| cwd.ancestors().nth(1).map(|p| p.to_path_buf()))
                .unwrap_or_else(|| std::path::PathBuf::from("."));
            repo_root.join(dir)
        };
        EngineSettings {
            python_path,
            engine_dir: engine_dir.to_string_lossy().into_owned(),
        }
    }
}

/// Public status payload (Tauri command return type).
/// Mirrors `EngineStatus` in `src/types.ts`.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Status {
    pub state: String, // "starting" | "connected" | "disconnected"
    pub detail: String,
    pub protocol_version: Option<u32>,
    pub engine_version: Option<String>,
}

impl Status {
    pub fn starting(detail: impl Into<String>) -> Status {
        Status {
            state: "starting".into(),
            detail: detail.into(),
            protocol_version: None,
            engine_version: None,
        }
    }

    pub fn disconnected(detail: impl Into<String>) -> Status {
        Status {
            state: "disconnected".into(),
            detail: detail.into(),
            protocol_version: None,
            engine_version: None,
        }
    }

    pub fn connected(protocol_version: u32, engine_version: Option<String>) -> Status {
        Status {
            state: "connected".into(),
            detail: "Dude engine is connected and healthy.".into(),
            protocol_version: Some(protocol_version),
            engine_version,
        }
    }
}

/// App-managed engine state (shared via Tauri managed state).
pub struct EngineState {
    inner: Mutex<EngineInner>,
}

struct EngineInner {
    handle: Option<Arc<EngineHandle>>,
    status: Status,
}

impl Default for EngineState {
    fn default() -> Self {
        EngineState {
            inner: Mutex::new(EngineInner {
                handle: None,
                status: Status::starting("Starting the Dude engine…"),
            }),
        }
    }
}

impl EngineState {
    /// Spawn the engine and block (bounded) until it reports healthy.
    ///
    /// Called from a background thread during app setup so a slow or failing
    /// engine never blocks window creation.
    pub fn start(&self) {
        {
            let inner = self.inner.lock().unwrap();
            if inner.handle.is_some() {
                return; // already running
            }
        }
        let settings = EngineSettings::from_env();
        log::info!(
            "starting engine: python={:?} engine_dir={:?}",
            settings.python_path,
            settings.engine_dir
        );

        match EngineHandle::spawn_and_start(
            &settings.python_path,
            &settings.engine_dir,
            DEFAULT_STARTUP_TIMEOUT,
        ) {
            Ok(handle) => {
                let engine_version = handle.engine_version();
                let mut inner = self.inner.lock().unwrap();
                inner.handle = Some(Arc::new(handle));
                inner.status = Status::connected(client::PROTOCOL_VERSION as u32, engine_version);
                log::info!("engine connected (protocol v{})", client::PROTOCOL_VERSION);
            }
            Err(err) => {
                log::error!("engine startup failed: {err}");
                let mut inner = self.inner.lock().unwrap();
                inner.status = Status::disconnected(err.safe_message());
            }
        }
    }

    /// Current status snapshot for the UI.
    pub fn status(&self) -> Status {
        self.inner.lock().unwrap().status.clone()
    }

    /// Health check against a live engine, refreshing the cached status.
    pub fn health_check(&self) -> Status {
        let handle = {
            let inner = self.inner.lock().unwrap();
            match &inner.handle {
                Some(h) => Arc::clone(h),
                None => return self.inner.lock().unwrap().status.clone(),
            }
        };
        let status = match handle.health_with_timeout(DEFAULT_REQUEST_TIMEOUT) {
            Ok(_) => Status::connected(client::PROTOCOL_VERSION as u32, handle.engine_version()),
            Err(err) => {
                log::warn!("health check failed: {err}");
                Status::disconnected(err.safe_message())
            }
        };
        self.inner.lock().unwrap().status = status.clone();
        status
    }

    /// Graceful shutdown: send the shutdown op, bounded wait, kill fallback.
    pub fn shutdown(&self) {
        let handle = self.inner.lock().unwrap().handle.take();
        if let Some(h) = handle {
            h.stop(Duration::from_secs(3));
        }
        self.inner.lock().unwrap().status =
            Status::disconnected("Engine stopped.");
    }
}
