# Dude — Security (Phase 0)

Phase 0 builds the security *foundation*; later phases extend it. This document
records the posture as implemented.

## Process and privilege model

- **Three processes, least privilege by design:**
  - UI (webview): untrusted by construction; sandboxed web content with a
    strict CSP and a minimal capability set.
  - Rust/Tauri host: the only component allowed to spawn the engine; runs as
    the signed-in user with no elevation.
  - Python engine: pure computation over stdio; no network, no shell, no
    filesystem access beyond its log file and data directory.

## IPC security

- The engine opens **no sockets and no network interfaces** — loopback
  included. The only channel is the stdio pipe pair created by the host when
  spawning the engine, which no other local process can address.
- The protocol is versioned and validated on both sides; malformed frames get
  structured rejections (`bad_request`, `unsupported_version`), never
  execution.
- Frames are capped at 1 MiB to bound memory abuse.
- The UI cannot reach the engine except through reviewed Tauri commands
  (`engine_status`, `engine_health`), which return only safe, human-readable
  status.

## Tauri capabilities (reviewed, minimal)

`src-tauri/capabilities/default.json` grants **only** `core:default` for the
main window: no shell, no filesystem, no HTTP, no process plugins, no
clipboard/dialog/notification permissions. The CSP in `tauri.conf.json` is
strict:

```
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'
```

`unsafe-inline` for styles is the one pragmatic exception (Vite injects styles
in dev); scripts and connections are locked to the app bundle.

## Secrets and data

- Phase 0 has **no secrets**: no API keys, tokens, or credentials exist, and
  `.gitignore` blocks `.env` (except `.env.example`) plus `*.log`, `*.db`,
  `.venv/`, and build outputs.
- User data goes to `%LOCALAPPDATA%\Dude` (per-user, never the install dir).
  In Phase 0 this holds only `engine.log`.
- No telemetry, analytics, update beacons, or cloud calls of any kind.

## Logging discipline

- Rust: `log` + `env_logger` to stderr with timestamps, severity, and module.
- Python: stderr + file mirror, same fields.
- UI-safe error strings are separate from log detail: engine stderr and
  exception internals never reach the UI, only structured, human-safe messages.
- Nothing logs secrets, full environment contents, or personal data (there
  are none in Phase 0 to log).

## Trust boundaries summary

| Boundary             | Protection                                           |
| -------------------- | ---------------------------------------------------- |
| Web → host           | Tauri command allowlist + strict CSP + capabilities   |
| Host → engine        | Single stdio pipe pair, versioned validated protocol  |
| Engine → system      | No network, no shell, data dir + log file only        |
| Repo → users         | No secrets committed; `.env` ignored; lockfiles in    |
