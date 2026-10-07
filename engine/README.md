# Dude engine

The Dude engine is a small Python process started by the Rust/Tauri desktop
host as a child process. It speaks **newline-delimited JSON over stdio
(protocol version 1)** and exposes a minimal, versioned operation surface.

Phase 0 scope is deliberately tiny: readiness/health and graceful shutdown.
Voice, AI providers, memory, and all later capabilities are future work.

## Running manually

```bash
# from the repo root
.venv/Scripts/python -m dude.main          # Windows (venv activated or full path)
.venv/bin/python -m dude.main              # POSIX equivalent
```

The engine writes one JSON `hello` frame to stdout, then serves one JSON
request per stdin line and answers with one JSON response per line. Try:

```bash
{"v":1,"id":"1","op":"health"}
{"v":1,"id":"2","op":"shutdown"}
```

Pressing Ctrl+D / closing stdin also exits cleanly.

## Modules

| Module                    | Responsibility                                        |
| ------------------------- | ----------------------------------------------------- |
| `dude/__init__.py`        | Name, version, protocol version constants             |
| `dude/main.py`            | Entry point: hello frame, request loop, clean exits   |
| `dude/protocol.py`        | Frame parsing/validation, success/error frame builders|
| `dude/config.py`          | Env-based config loading + validation, data dir       |
| `dude/logging_setup.py`   | Logging to stderr + file; stdout is never touched     |

## Error codes (wire contract)

| Code                   | Meaning                                    |
| ---------------------- | ------------------------------------------ |
| `bad_request`          | Frame is not valid JSON / not an object / bad shape |
| `unsupported_version`  | Missing or non-integer `v`, or `v` != 1    |
| `unknown_op`           | Operation name is not in the Phase 0 set   |
| `internal`             | Unexpected engine error (details in logs)  |

See [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) for the full protocol
contract, and [docs/SECURITY.md](../docs/SECURITY.md) for the security posture.

## Tests

```bash
# from engine/
../.venv/Scripts/python -m pytest
```
