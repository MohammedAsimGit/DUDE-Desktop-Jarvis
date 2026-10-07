# Dude — Architecture

Phase 0. This document describes the architecture **as implemented**, including
the wire contract between the desktop host and the Python engine.

## Process model

```text
React + TypeScript UI
          │
          │ Tauri commands / events
          ▼
     Rust / Tauri host            (owns the app lifecycle)
          │
          │ versioned local IPC: newline-delimited JSON over stdio (v1)
          ▼
   Python Dude engine process    (child process, `python -m dude.main`)
```

- The **UI never talks to the engine directly** and never launches processes.
  It calls Tauri commands; the Rust host is the single owner of the engine.
- The **engine is a plain child process**, not a server. It exposes no
  network interface, opens no sockets, and loads no third-party packages
  (Phase 0 is stdlib-only).
- The host starts the engine on a background thread at app startup so the
  window appears immediately; a 5 s health monitor keeps the cached status
  honest.

## Components

| Path                          | Responsibility                                              |
| ----------------------------- | ----------------------------------------------------------- |
| `src/`                        | React 18 + TypeScript UI (status card, refresh)             |
| `src-tauri/src/engine/mod.rs` | App-level engine state, env settings, status payload         |
| `src-tauri/src/engine/manager.rs` | Child-process lifecycle, reader thread, request correlation |
| `src-tauri/src/engine/client.rs`  | Protocol types, error kinds, request-id generator           |
| `engine/dude/main.py`         | Engine entry point and protocol loop                        |
| `engine/dude/protocol.py`     | Frame parsing/validation, response builders                 |
| `engine/dude/config.py`       | Env-based configuration and validation                      |
| `engine/dude/logging_setup.py`| Logging sinks (stderr + file; stdout reserved)              |

## IPC choice: NDJSON over stdio (protocol v1)

**Chosen:** newline-delimited JSON over the engine's standard input/output,
with the engine launched as `python -X utf8 -m dude.main` and working
directory `engine/`.

**Why stdio and not a loopback socket:**

- No port allocation or collision handling; nothing to scan or misbind.
- The channel exists only for the lifetime of the child process, and the OS
  ties it to the parent: if the host dies, the engine's stdin hits EOF and it
  exits by itself — orphan processes are structurally difficult.
- No sockets listening on any interface (loopback included), which keeps the
  Phase 0 security surface minimal.
- Stdio handles spaces and Unicode paths naturally (no URL/argv encoding).

**Tradeoffs:** one request at a time per direction, all traffic passes through
pipes owned by the host, and stdio is unsuitable for streaming bulk data later
(a second mechanism or framing upgrade can be introduced behind the same
version boundary when needed).

**Channel rules:**

- The engine's **stdout carries protocol frames only**; diagnostics go to
  stderr (and mirror to a file under the per-user data directory).
- The host redirects engine stderr to its own stderr; `DUDE_PYTHON`-driven
  dev runs show engine logs inline.
- Frames are single-line JSON, UTF-8, capped at 1 MiB.

## Protocol v1 contract

### Frames

Hello (engine → host, unsolicited first line on stdout):

```json
{"v":1,"type":"hello","protocol":1,"engine":"dude-engine","version":"0.1.0"}
```

Request (host → engine):

```json
{"v":1,"id":"req-7","op":"health"}
{"v":1,"id":"req-8","op":"shutdown"}
```

Success response (engine → host):

```json
{"v":1,"id":"req-7","ok":true,"result":{"status":"ready","engine":"dude-engine","version":"0.1.0","protocol":1,"data_dir":"..."}}
```

Error response (engine → host):

```json
{"v":1,"id":"req-9","ok":false,"error":{"code":"unknown_op","message":"unknown operation 'nope'"}}
```

### Semantics

- **Versioning:** requests and hello frames carry `v` (integer). A request
  with a missing, non-integer, or non-1 `v` answers
  `unsupported_version`. Any breaking change bumps the version in
  `engine/dude/__init__.py` and `src-tauri/src/engine/client.rs` together.
- **Correlation:** every request carries a host-chosen `id` string, echoed
  verbatim in the response. Malformed frames that lack a usable id answer
  with `id: null` and the engine keeps serving.
- **Error codes (stable):** `bad_request`, `unsupported_version`,
  `unknown_op`, `internal`.

### Lifecycle and bounded behavior

| Situation                | Behavior                                                            |
| ------------------------ | ------------------------------------------------------------------- |
| Engine startup           | Host spawns the engine, sends `health` within 10 s startup timeout. |
| Startup timeout          | Engine marked disconnected with a UI-safe message; kill fallback.   |
| Malformed engine line    | Host logs and drops it; health correlation is unaffected.           |
| Engine exits             | Reader thread flags EOF; in-flight requests fail fast with `EngineExited`. |
| Malformed host request   | Engine replies `bad_request`/`unsupported_version` and keeps serving. |
| Shutdown                 | Host sends `shutdown`; engine replies and exits 0 within the grace period; host kills after 3 s as a fallback. |
| Host dies uncleanly      | Engine stdin hits EOF → engine exits by itself. No orphans.         |

### Current operations (Phase 0)

- `health` → `{ status: "ready", engine, version, protocol, data_dir }`
- `shutdown` → `{ status: "shutting_down" }` (engine exits cleanly after)

## Configuration

- Engine: environment variables (`DUDE_LOG_LEVEL`, `DUDE_DATA_DIR`), loaded
  via `engine/dude/config.py` with startup validation and actionable errors;
  see [DEVELOPMENT.md](DEVELOPMENT.md) for the full list.
- Host: `DUDE_PYTHON` (interpreter override) and `DUDE_ENGINE_DIR` (engine
  package dir override) are read by `EngineSettings::from_env()`; defaults
  target the dev layout. This function is the production packaging seam —
  a later phase can resolve a bundled interpreter + engine there.
- User data lives under `%LOCALAPPDATA%\Dude` (created on demand). Mutable
  data never goes into the installation directory. Paths with spaces and
  Unicode are exercised by tests.

## SQLite

Chosen as the future local database but **deliberately not wired in Phase 0**:
no schema, no memory features. The engine's validated `data_dir` is where a
SQLite file will live once a later phase adds persistence.

## Non-goals honored

Voice, AI providers, memory, Windows control, screen understanding, browser
automation, agents, plugin systems, scheduling, telemetry, cloud services, and
any non-local networking are **not implemented** in Phase 0.
