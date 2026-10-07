//! End-to-end smoke test: spawn the real Python engine and speak protocol v1.
//!
//! Panics with a clear reason when `engine/` or the venv python is missing,
//! rather than failing obscurely.

use dude_lib::engine::manager::EngineHandle;
use std::path::PathBuf;
use std::time::Duration;

fn repo_dir() -> PathBuf {
    std::env::current_dir()
        .expect("cwd")
        .ancestors()
        .nth(1)
        .expect("repo root")
        .to_path_buf()
}

fn python() -> String {
    if let Ok(py) = std::env::var("DUDE_PYTHON") {
        return py;
    }
    let venv = repo_dir().join(".venv").join("Scripts").join("python.exe");
    if venv.exists() {
        return venv.to_string_lossy().into_owned();
    }
    panic!("venv python not found at {} - run `python -m venv .venv`", venv.display())
}

fn spawn_engine() -> EngineHandle {
    let dir = repo_dir().join("engine");
    assert!(dir.is_dir(), "engine/ missing");
    let py = python();
    EngineHandle::spawn_and_start(&py, dir.to_str().unwrap(), Duration::from_secs(10))
        .expect("engine failed to start")
}

#[test]
fn engine_ready_and_health_answers() {
    let handle = spawn_engine();
    let result = handle
        .health_with_timeout(Duration::from_secs(5))
        .expect("health request failed");
    assert_eq!(result["status"], "ready");
    assert_eq!(result["protocol"], 1);
    assert!(result["data_dir"].as_str().unwrap().contains("Dude"));
    assert_eq!(result["engine"], "dude-engine");
    handle.stop(Duration::from_secs(3));
}

#[test]
fn engine_shutdown_exits_cleanly() {
    let handle = spawn_engine();
    // stop() sends the shutdown op, waits within the grace period, then kills
    // if needed. The engine must exit on its own, cleanly, no kill needed.
    handle.stop(Duration::from_secs(3));
    assert!(
        handle.exited_on_request(),
        "engine did not exit cleanly within the grace period"
    );
}
