//! Manage the Dude engine child process and its NDJSON/stdio protocol channel.
//!
//! Concurrency model: one dedicated reader thread owns engine stdout and
//! publishes parsed JSON lines to a shared queue; request senders block on a
//! shared condition variable until their correlation id appears. This keeps
//! the request path synchronous and simple, with bounded timeouts.
//!
//! Shutdown rules:
//! - on app exit, the engine gets a `shutdown` op, then a bounded wait, then
//!   kill if needed.
//! - if the host dies without warning, the engine sees stdin EOF and exits
//!   by itself — no orphans.

use super::client::{request_frame, EngineError, RequestIds};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

/// Default time to wait for the engine to become ready after spawn.
pub const DEFAULT_STARTUP_TIMEOUT: Duration = Duration::from_secs(10);
/// Default time to wait for any single request response.
pub const DEFAULT_REQUEST_TIMEOUT: Duration = Duration::from_secs(5);

struct Shared {
    /// Correlation id -> delivered response value.
    responses: Mutex<HashMap<String, Value>>,
    cv: Condvar,
    /// Set when the reader thread has seen stdout EOF.
    stdout_closed: AtomicBool,
    /// Structured error delivered in place of a live response.
    failure: Mutex<Option<EngineError>>,
}

impl Shared {
    fn new() -> Self {
        Shared {
            responses: Mutex::new(HashMap::new()),
            cv: Condvar::new(),
            stdout_closed: AtomicBool::new(false),
            failure: Mutex::new(None),
        }
    }

    fn deliver(&self, resp: Value) {
        let id = resp
            .get("id")
            .and_then(Value::as_str)
            .map(|s| s.to_string());
        if let Some(id) = id {
            self.responses.lock().unwrap().insert(id, resp);
            self.cv.notify_all();
        }
        // Frames without a usable id (e.g. hello) are dropped: the hello
        // frame is informational only and readiness is proven by health.
    }
}

pub struct EngineHandle {
    child: Mutex<Child>,
    stdin: Mutex<Option<Box<dyn Write + Send>>>,
    shared: Arc<Shared>,
    ids: RequestIds,
    reader: Mutex<Option<std::thread::JoinHandle<()>>>,
    engine_version: Mutex<Option<String>>,
    /// True when the engine exited on its own (with a clean exit code) in
    /// response to the shutdown op within the grace period.
    exited_on_request: AtomicBool,
    /// True once `stop()` has completed; Drop then skips its last-resort
    /// cleanup so a clean shutdown is never followed by a redundant
    /// shutdown write to a closed pipe (noisy false "not delivered" warning).
    stopped: AtomicBool,
}

impl Drop for EngineHandle {
    fn drop(&mut self) {
        // Last-resort cleanup if the owner forgot to call stop().
        if !self.stopped.load(Ordering::SeqCst) {
            self.send_shutdown(250);
            self.kill();
        }
    }
}

fn spawn_error(name: &str, detail: std::io::Error) -> EngineError {
    let hint = match detail.kind() {
        std::io::ErrorKind::NotFound => {
            "interpreter not found - check python path configuration"
        }
        _ => "check engine setup and configuration",
    };
    log::error!("spawn failed for {name}: {detail} ({hint})");
    EngineError::SpawnFailed(
        "could not start the Dude engine. Check that Python is installed and enabled in settings."
            .into(),
    )
}

impl EngineHandle {
    /// Spawn the engine and wait (bounded) until it answers `health`.
    ///
    /// The engine is launched as a module (`python -m dude.main`) with the
    /// working directory set to the `engine/` folder, which keeps package
    /// imports intact and avoids PYTHONPATH fragility.
    pub fn spawn_and_start(
        python_path: &str,
        engine_dir: &str,
        startup_timeout: Duration,
    ) -> Result<EngineHandle, EngineError> {
        let started = Instant::now();
        log::info!("spawning engine: {python_path} -m dude.main (cwd={engine_dir})");

        let mut child = Command::new(python_path)
            .args(["-X", "utf8", "-m", "dude.main"])
            .current_dir(engine_dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|e| spawn_error("python", e))?;

        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| EngineError::SpawnFailed("no stdin pipe".into()))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| EngineError::SpawnFailed("no stdout pipe".into()))?;

        let shared = Arc::new(Shared::new());
        let shared_for_reader = Arc::clone(&shared);

        let reader = std::thread::spawn(move || {
            let reader = BufReader::new(stdout);
            for line in reader.lines() {
                match line {
                    Ok(text) => {
                        let trimmed = text.trim();
                        if trimmed.is_empty() {
                            continue;
                        }
                        match serde_json::from_str::<Value>(trimmed) {
                            Ok(v) => shared_for_reader.deliver(v),
                            Err(err) => {
                                log::warn!("dropping malformed engine line: {err}")
                            }
                        }
                    }
                    Err(_) => break,
                }
            }
            shared_for_reader
                .stdout_closed
                .store(true, Ordering::SeqCst);
            shared_for_reader.cv.notify_all();
        });

        let handle = EngineHandle {
            child: Mutex::new(child),
            stdin: Mutex::new(Some(Box::new(stdin))),
            shared,
            ids: RequestIds::new(),
            reader: Mutex::new(Some(reader)),
            engine_version: Mutex::new(None),
            exited_on_request: AtomicBool::new(false),
            stopped: AtomicBool::new(false),
        };

        // Prove readiness with a bounded health request.
        let _hello = 0; // hello frame is informational; health proves readiness
        match handle.health_with_timeout(startup_timeout) {
            Ok(result) => {
                if let Some(v) = result.get("version").and_then(Value::as_str) {
                    *handle.engine_version.lock().unwrap() = Some(v.to_string());
                }
                log::info!(
                    "engine ready in {:.1?} (version {:?})",
                    started.elapsed(),
                    handle.engine_version.lock().unwrap()
                );
                Ok(handle)
            }
            Err(err) => {
                log::error!("engine failed to become ready: {err}");
                Err(err)
            }
        }
    }

    /// Perform a `health` request with an explicit timeout.
    pub fn health_with_timeout(
        &self,
        timeout: Duration,
    ) -> Result<Value, EngineError> {
        self.request("health", timeout)
    }

    /// Send a stop request, wait briefly, then force-kill if still alive.
    pub fn stop(&self, grace: Duration) {
        self.send_shutdown(grace.as_millis().max(500) as u64);
        self.kill();
        self.stopped.store(true, Ordering::SeqCst);
    }

    /// Send the shutdown op and wait up to `grace_ms` for the process to exit.
    fn send_shutdown(&self, grace_ms: u64) {
        if let Err(err) = self.send_request_line(&request_frame("stop", "shutdown")) {
            // Best effort: engine may already be gone; kill() will finish cleanup.
            log::warn!("shutdown request not delivered ({err}); forcing kill");
        }
        let deadline = Instant::now() + Duration::from_millis(grace_ms);
        loop {
            let mut child = self.child.lock().unwrap();
            match child.try_wait() {
                Ok(Some(status)) => {
                    log::info!("engine exited with {status}");
                    self.exited_on_request.store(status.success(), Ordering::SeqCst);
                    return;
                }
                Ok(None) => {}
                Err(e) => {
                    log::warn!("try_wait failed during shutdown: {e}");
                    return;
                }
            }
            if Instant::now() >= deadline {
                return; // kill() will finish the job
            }
            drop(child);
            std::thread::sleep(Duration::from_millis(50));
        }
    }

    /// Force-kill the engine and join the reader thread.
    fn kill(&self) {
        let mut child = self.child.lock().unwrap();
        let _ = child.kill();
        let _ = child.wait();
        drop(child);
        if let Some(handle) = self.reader.lock().unwrap().take() {
            let _ = handle.join();
        }
    }

    fn send_request_line(&self, frame: &Value) -> Result<(), EngineError> {
        let mut stdin_guard = self.stdin.lock().unwrap();
        if stdin_guard.is_none() {
            return Err(EngineError::NotRunning);
        }
        let text = serde_json::to_string(frame).map_err(|e| {
            log::error!("serialize request failed: {e}");
            EngineError::WriteFailed
        })?;
        if let Some(w) = stdin_guard.as_mut() {
            w.write_all(text.as_bytes())
                .and_then(|_| w.write_all(b"\n"))
                .and_then(|_| w.flush())
                .map_err(|e| {
                    log::error!("engine stdin write failed: {e}");
                    EngineError::WriteFailed
                })?;
        }
        Ok(())
    }

    /// Send a request and wait (bounded) for the correlated response.
    pub fn request(&self, op: &str, timeout: Duration) -> Result<Value, EngineError> {
        if self.shared.stdout_closed.load(Ordering::SeqCst) {
            return Err(EngineError::EngineExited);
        }
        let id = self.ids.next();
        self.send_request_line(&request_frame(&id, op))?;

        // Correlate by id, bounded by deadline; see docs/ARCHITECTURE.md.
        let deadline = Instant::now() + timeout;
        let mut responses = self.shared.responses.lock().unwrap();
        loop {
            if let Some(resp) = responses.remove(&id) {
                break self.interpret(resp);
            }
            if self.shared.stdout_closed.load(Ordering::SeqCst) {
                break Err(EngineError::EngineExited);
            }
            {
                let mut failure = self.shared.failure.lock().unwrap();
                if failure.is_some() {
                    break Err(failure.take().unwrap());
                }
            }
            let remaining = deadline.checked_duration_since(Instant::now());
            match remaining {
                None => break Err(EngineError::Timeout),
                Some(budget) => {
                    let (new_guard, wait_result) = self
                        .shared
                        .cv
                        .wait_timeout(responses, budget)
                        .unwrap();
                    responses = new_guard;
                    // Timeout re-checks the exit/failure/response conditions
                    // before giving up at the top of the next iteration.
                    if wait_result.timed_out() {
                        continue;
                    }
                }
            }
        }
    }

    /// Translate a raw response value into success or a structured error.
    fn interpret(&self, resp: Value) -> Result<Value, EngineError> {
        match resp.get("ok") {
            Some(Value::Bool(true)) => Ok(resp.get("result").cloned().unwrap_or(json!({}))),
            Some(Value::Bool(false)) => {
                let err = resp.get("error").cloned().unwrap_or(json!({}));
                let code = err
                    .get("code")
                    .and_then(Value::as_str)
                    .unwrap_or("internal")
                    .to_string();
                let message = err
                    .get("message")
                    .and_then(Value::as_str)
                    .unwrap_or("engine reported an error")
                    .to_string();
                Err(EngineError::Protocol { code, message })
            }
            _ => Err(EngineError::Protocol {
                code: "bad_response".into(),
                message: "engine sent a malformed response".into(),
            }),
        }
    }

    pub fn engine_version(&self) -> Option<String> {
        self.engine_version.lock().unwrap().clone()
    }

    /// Whether the engine exited on its own, with a clean exit code, within
    /// the grace period after a shutdown request (bounded shutdown behavior).
    pub fn exited_on_request(&self) -> bool {
        self.exited_on_request.load(Ordering::SeqCst)
    }
}
