//! Protocol v1 types shared by the Rust engine client.
//!
//! These mirror the contract documented in docs/ARCHITECTURE.md and implemented
//! by the Python engine (`engine/dude/protocol.py`). Keep both sides in sync.

use serde_json::{json, Value};
use std::fmt;
use std::sync::atomic::{AtomicU64, Ordering};

/// Bump if the Rust side ever speaks a different protocol than the engine.
pub const PROTOCOL_VERSION: u64 = 1;

/// Errors that can occur while talking to the engine.
///
/// `safe_message()` output is safe to show in the UI: it never includes engine
/// stderr contents or environment data. Diagnostic detail goes to logs.
#[derive(Debug)]
pub enum EngineError {
    /// The Python interpreter could not be started.
    SpawnFailed(String),
    /// The engine process is not running.
    NotRunning,
    /// The engine process exited before responding.
    EngineExited,
    /// Writing a request to the engine failed.
    WriteFailed,
    /// The engine did not respond within the allotted time.
    Timeout,
    /// The engine answered with a structured protocol error.
    Protocol { code: String, message: String },
}

impl EngineError {
    /// A short, human-safe summary for UI status text.
    pub fn safe_message(&self) -> String {
        match self {
            EngineError::SpawnFailed(_) => {
                "could not start the Dude engine. Check that Python is installed."
                    .to_string()
            }
            EngineError::NotRunning => "engine is not running".into(),
            EngineError::EngineExited => "engine process exited unexpectedly".into(),
            EngineError::WriteFailed => "could not send request to the engine".into(),
            EngineError::Timeout => "engine did not respond in time".into(),
            EngineError::Protocol { code, message } => {
                format!("engine error {code}: {message}")
            }
        }
    }
}

impl fmt::Display for EngineError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            EngineError::SpawnFailed(d) => write!(f, "spawn failed: {d}"),
            EngineError::NotRunning => write!(f, "engine is not running"),
            EngineError::EngineExited => write!(f, "engine exited before responding"),
            EngineError::WriteFailed => write!(f, "write failed"),
            EngineError::Timeout => write!(f, "engine did not respond in time"),
            EngineError::Protocol { code, message } => {
                write!(f, "protocol error {code}: {message}")
            }
        }
    }
}

impl std::error::Error for EngineError {}

/// Monotonic request-id source for correlating responses with requests.
#[derive(Default)]
pub struct RequestIds(AtomicU64);

impl RequestIds {
    pub fn new() -> Self {
        RequestIds(AtomicU64::new(1))
    }

    pub fn next(&self) -> String {
        let n = self.0.fetch_add(1, Ordering::Relaxed);
        format!("req-{n}")
    }
}

/// Build a v1 request frame with the given id and operation.
pub fn request_frame(id: &str, op: &str) -> Value {
    json!({ "v": PROTOCOL_VERSION, "id": id, "op": op })
}
