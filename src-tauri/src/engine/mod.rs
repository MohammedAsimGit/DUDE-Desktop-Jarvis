//! Dude engine integration: process management + protocol client (Phase 0 + Sprint 2 + Sprint 3).
//!
//! Adds the voice and AI surfaces on top of the existing protocol: the host
//! exposes voice commands, a pollable voice status, and AI chat commands
//! (status, submit, streaming-with-handle, clear) over the same v1 stdio IPC.
//! Shutdown cleanly cancels any active voice session and in-flight AI stream
//! before the engine is stopped.

pub mod client;
pub mod manager;

use client::{
    OP_AI_CLEAR, OP_AI_STREAM_CANCEL, OP_AI_STREAM_NEXT, OP_AI_STREAM_START, OP_AI_STATUS,
    OP_AI_SUBMIT, OP_VOICE_CANCEL, OP_VOICE_INTERRUPT, OP_VOICE_RESET, OP_VOICE_START,
    OP_VOICE_STOP, OP_VOICE_STATUS,
};
use manager::{EngineHandle, EngineSettings, DEFAULT_REQUEST_TIMEOUT, DEFAULT_STARTUP_TIMEOUT};
use serde::Serialize;
use serde_json::json;
use std::sync::{Arc, Mutex};
use std::time::Duration;

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

/// Voice session status returned by the host's `voice_status` command.
/// Mirrors `VoiceStatus` in `src/types.ts`.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct VoiceStatus {
    pub state: String,
    pub transcript: String,
    pub error: String,
}

impl VoiceStatus {
    pub fn from_engine(raw: serde_json::Value) -> Self {
        let state = raw
            .get("state")
            .and_then(serde_json::Value::as_str)
            .unwrap_or(client::VOICE_STATE_ERROR)
            .to_string();
        let transcript = raw
            .get("transcript")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .to_string();
        let error = raw
            .get("error")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .to_string();
        VoiceStatus {
            state,
            transcript,
            error,
        }
    }

    pub fn idle() -> Self {
        VoiceStatus {
            state: client::VOICE_STATE_IDLE.to_string(),
            transcript: String::new(),
            error: String::new(),
        }
    }
}

/// Valid voice state strings supported by the engine.
pub const VALID_VOICE_STATES: &[&str] = &[
    client::VOICE_STATE_IDLE,
    client::VOICE_STATE_RECORDING,
    client::VOICE_STATE_PROCESSING,
    client::VOICE_STATE_SPEAKING,
    client::VOICE_STATE_DONE,
    client::VOICE_STATE_CANCELLED,
    client::VOICE_STATE_ERROR,
];

fn is_valid_voice_state(state: &str) -> bool {
    VALID_VOICE_STATES.contains(&state)
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
            settings.python_path, settings.engine_dir
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
                inner.status =
                    Status::connected(client::PROTOCOL_VERSION as u32, engine_version);
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
            Ok(_) => {
                Status::connected(client::PROTOCOL_VERSION as u32, handle.engine_version())
            }
            Err(err) => {
                log::warn!("health check failed: {err}");
                Status::disconnected(err.safe_message())
            }
        };
        self.inner.lock().unwrap().status = status.clone();
        status
    }

    /// Graceful shutdown: cancel any active voice session first, then send the
    /// shutdown op, bounded wait, kill fallback.
    pub fn shutdown(&self) {
        let handle = self.inner.lock().unwrap().handle.take();
        if let Some(h) = handle {
            // Best-effort: if voice is active, try to interrupt it before the
            // engine stops. Failures here must not prevent shutdown.
            if let Err(err) = h.voice_request(OP_VOICE_INTERRUPT, DEFAULT_REQUEST_TIMEOUT) {
                log::warn!("voice interrupt during shutdown failed: {err}");
            }
            if let Err(err) = h.voice_request(OP_VOICE_CANCEL, DEFAULT_REQUEST_TIMEOUT) {
                log::warn!("voice cancel during shutdown failed: {err}");
            }
            h.stop(Duration::from_secs(3));
        }
        self.inner.lock().unwrap().status = Status::disconnected("Engine stopped.");
    }

    /// Current voice status snapshot for the UI.
    pub fn voice_status(&self) -> VoiceStatus {
        let handle = {
            let inner = self.inner.lock().unwrap();
            match &inner.handle {
                Some(h) => Arc::clone(h),
                None => return VoiceStatus::idle(),
            }
        };
        match handle.voice_request(OP_VOICE_STATUS, DEFAULT_REQUEST_TIMEOUT) {
            Ok(result) => {
                let mut st = VoiceStatus::from_engine(result);
                if !is_valid_voice_state(&st.state) {
                    log::warn!("engine returned unexpected voice state: {}", st.state);
                    st.state = client::VOICE_STATE_IDLE.to_string();
                }
                st
            }
            Err(err) => {
                log::warn!("voice status check failed: {err}");
                VoiceStatus {
                    state: client::VOICE_STATE_IDLE.to_string(),
                    transcript: String::new(),
                    error: "voice status unavailable".to_string(),
                }
            }
        }
    }

    /// Start an explicit voice capture session.
    pub fn voice_start(&self) -> Result<VoiceStatus, String> {
        let handle = self.inner.lock().unwrap().handle.as_ref().cloned();
        let handle = match handle {
            Some(h) => h,
            None => return Err("engine is not running".into()),
        };
        match handle.voice_request(OP_VOICE_START, DEFAULT_REQUEST_TIMEOUT) {
            Ok(result) => Ok(VoiceStatus::from_engine(result)),
            Err(err) => Err(err.safe_message()),
        }
    }

    /// Finish the current recording and transcribe it.
    pub fn voice_stop(&self) -> Result<VoiceStatus, String> {
        let handle = self.inner.lock().unwrap().handle.as_ref().cloned();
        let handle = match handle {
            Some(h) => h,
            None => return Err("engine is not running".into()),
        };
        match handle.voice_request(OP_VOICE_STOP, DEFAULT_REQUEST_TIMEOUT) {
            Ok(result) => Ok(VoiceStatus::from_engine(result)),
            Err(err) => Err(err.safe_message()),
        }
    }

    /// Cancel the current voice interaction without a transcript.
    pub fn voice_cancel(&self) -> Result<VoiceStatus, String> {
        let handle = self.inner.lock().unwrap().handle.as_ref().cloned();
        let handle = match handle {
            Some(h) => h,
            None => return Err("engine is not running".into()),
        };
        match handle.voice_request(OP_VOICE_CANCEL, DEFAULT_REQUEST_TIMEOUT) {
            Ok(result) => Ok(VoiceStatus::from_engine(result)),
            Err(err) => Err(err.safe_message()),
        }
    }

    /// Interrupt any active speech playback immediately.
    pub fn voice_interrupt(&self) -> Result<VoiceStatus, String> {
        let handle = self.inner.lock().unwrap().handle.as_ref().cloned();
        let handle = match handle {
            Some(h) => h,
            None => return Err("engine is not running".into()),
        };
        match handle.voice_request(OP_VOICE_INTERRUPT, DEFAULT_REQUEST_TIMEOUT) {
            Ok(result) => Ok(VoiceStatus::from_engine(result)),
            Err(err) => Err(err.safe_message()),
        }
    }

    /// Best-effort helper to return a finished session to idle.
    pub fn voice_reset(&self) -> Result<VoiceStatus, String> {
        let handle = self.inner.lock().unwrap().handle.as_ref().cloned();
        let handle = match handle {
            Some(h) => h,
            None => return Err("engine is not running".into()),
        };
        match handle.voice_request(OP_VOICE_RESET, DEFAULT_REQUEST_TIMEOUT) {
            Ok(result) => Ok(VoiceStatus::from_engine(result)),
            Err(err) => Err(err.safe_message()),
        }
    }

    // -- AI chat surface (Sprint 3) -----------------------------------------

    /// Provider/tool status snapshot for the UI. Returns `null`-safe defaults
    /// when the engine is not running so the UI can render its offline state
    /// without errors.
    pub fn ai_status(&self) -> AIStatus {
        let handle = {
            let inner = self.inner.lock().unwrap();
            match &inner.handle {
                Some(h) => Arc::clone(h),
                None => return AIStatus::offline(),
            }
        };
        match handle.voice_request(OP_AI_STATUS, DEFAULT_REQUEST_TIMEOUT) {
            Ok(result) => AIStatus::from_engine(result),
            Err(err) => {
                log::warn!("ai_status failed: {err}");
                AIStatus::offline()
            }
        }
    }

    /// Submit a message synchronously and get the full response.
    pub fn ai_submit(&self, text: &str) -> Result<AISubmitResult, String> {
        let handle = self.inner.lock().unwrap().handle.as_ref().cloned();
        let handle = match handle {
            Some(h) => h,
            None => return Err("engine is not running".into()),
        };
        let args = json!({ "text": text });
        match handle.ai_request_with_args(OP_AI_SUBMIT, args, DEFAULT_REQUEST_TIMEOUT) {
            Ok(result) => Ok(AISubmitResult::from_engine(result)),
            Err(err) => Err(err.safe_message()),
        }
    }

    /// Start a streaming request and return its handle.
    pub fn ai_stream_start(&self, text: &str) -> Result<String, String> {
        let handle = self.inner.lock().unwrap().handle.as_ref().cloned();
        let handle = match handle {
            Some(h) => h,
            None => return Err("engine is not running".into()),
        };
        let args = json!({ "text": text });
        match handle.ai_request_with_args(OP_AI_STREAM_START, args, DEFAULT_REQUEST_TIMEOUT) {
            Ok(result) => {
                let id = result
                    .get("stream_id")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("")
                    .to_string();
                if id.is_empty() {
                    Err("engine returned no stream id".into())
                } else {
                    Ok(id)
                }
            }
            Err(err) => Err(err.safe_message()),
        }
    }

    /// Pull the next chunk/status for an open stream handle.
    pub fn ai_stream_next(&self, stream_id: &str) -> Result<AIStreamFrame, String> {
        let handle = self.inner.lock().unwrap().handle.as_ref().cloned();
        let handle = match handle {
            Some(h) => h,
            None => return Err("engine is not running".into()),
        };
        let args = json!({ "stream_id": stream_id });
        match handle.ai_request_with_args(OP_AI_STREAM_NEXT, args, DEFAULT_REQUEST_TIMEOUT) {
            Ok(result) => Ok(AIStreamFrame::from_engine(result)),
            Err(err) => Err(err.safe_message()),
        }
    }

    /// Cancel an in-flight stream.
    pub fn ai_stream_cancel(&self, stream_id: &str) -> Result<(), String> {
        let handle = self.inner.lock().unwrap().handle.as_ref().cloned();
        let handle = match handle {
            Some(h) => h,
            None => return Err("engine is not running".into()),
        };
        let args = json!({ "stream_id": stream_id });
        match handle.ai_request_with_args(OP_AI_STREAM_CANCEL, args, DEFAULT_REQUEST_TIMEOUT) {
            Ok(_) => Ok(()),
            Err(err) => Err(err.safe_message()),
        }
    }

    /// Clear the in-memory conversation.
    pub fn ai_clear(&self) -> Result<(), String> {
        let handle = self.inner.lock().unwrap().handle.as_ref().cloned();
        let handle = match handle {
            Some(h) => h,
            None => return Err("engine is not running".into()),
        };
        match handle.voice_request(OP_AI_CLEAR, DEFAULT_REQUEST_TIMEOUT) {
            Ok(_) => Ok(()),
            Err(err) => Err(err.safe_message()),
        }
    }
}

/// Provider/tool status returned by the host's `ai_status` command.
/// Mirrors `AIStatus` in `src/types.ts`.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AIStatus {
    /// Whether a real provider is usable. The deterministic fallback reports
    /// itself as "configured" because it can answer truthfully without a model.
    pub configured: bool,
    pub provider: String,
    pub can_stream: bool,
    pub tool_count: u32,
}

impl AIStatus {
    pub fn from_engine(raw: serde_json::Value) -> Self {
        AIStatus {
            configured: raw
                .get("configured")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false),
            provider: raw
                .get("provider")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("none")
                .to_string(),
            can_stream: raw
                .get("can_stream")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false),
            tool_count: raw
                .get("tool_count")
                .and_then(serde_json::Value::as_u64)
                .unwrap_or(0) as u32,
        }
    }

    pub fn offline() -> Self {
        AIStatus {
            configured: false,
            provider: "offline".into(),
            can_stream: false,
            tool_count: 0,
        }
    }
}

/// Result of a synchronous `ai_submit`.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AISubmitResult {
    pub response: String,
    pub used_tools: bool,
    pub tool_names: Vec<String>,
}

impl AISubmitResult {
    pub fn from_engine(raw: serde_json::Value) -> Self {
        AISubmitResult {
            response: raw
                .get("response")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("")
                .to_string(),
            used_tools: raw
                .get("used_tools")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false),
            tool_names: raw
                .get("tool_names")
                .and_then(serde_json::Value::as_array)
                .map(|a| {
                    a.iter()
                        .filter_map(|v| v.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default(),
        }
    }
}

/// One frame of the streaming protocol: either a chunk or the terminal done.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AIStreamFrame {
    /// "chunk" or "done".
    pub frame_type: String,
    pub stream_id: String,
    pub chunk_index: Option<u32>,
    pub chunk: Option<String>,
    /// Full accumulated text on the terminal frame.
    pub final_text: Option<String>,
}

impl AIStreamFrame {
    pub fn from_engine(raw: serde_json::Value) -> Self {
        let frame_type = raw
            .get("type")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("chunk")
            .to_string();
        AIStreamFrame {
            frame_type,
            stream_id: raw
                .get("stream_id")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("")
                .to_string(),
            chunk_index: raw
                .get("chunk_index")
                .and_then(serde_json::Value::as_u64)
                .map(|v| v as u32),
            chunk: raw
                .get("chunk")
                .and_then(serde_json::Value::as_str)
                .map(|s| s.to_string()),
            final_text: raw
                .get("final")
                .and_then(serde_json::Value::as_str)
                .map(|s| s.to_string()),
        }
    }
}
