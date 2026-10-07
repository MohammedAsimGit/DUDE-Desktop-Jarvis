# Dude — Development Guide (Windows)

Everything needed to run Dude locally on Windows. All commands are run from
the repository root unless noted.

## Prerequisites

| Tool            | Version used | Notes                                            |
| --------------- | ------------ | ------------------------------------------------ |
| Node.js + npm   | 22.18 / 10.9 | Any Node 20+ should work                         |
| Python          | 3.13         | 3.11+ required by the engine                     |
| Rust toolchain  | 1.95 stable  | `stable-x86_64-pc-windows-msvc`                  |
| VS Build Tools  | 2022         | "Desktop development with C++" workload (MSVC)   |
| WebView2        | 154          | Preinstalled on Windows 10/11                    |
| Git             | 2.49         | —                                                |

Verify:

```bash
node --version && python --version && cargo --version
```

## One-time setup

```bash
# 1. Frontend dependencies
npm install

# 2. Python virtual environment + engine dev dependencies
python -m venv .venv
.venv\Scripts\python -m pip install -r engine\requirements-dev.txt
```

## Run the desktop app (development)

```bash
npm run tauri dev
```

This starts the Vite dev server (port 1420), compiles the Rust host, and
opens the Dude window. The UI shows the engine status: it should flip from
"starting" to "connected" within a few seconds.

**Important:** the host spawns the engine with the plain `python` from `PATH`
by default. For the engine to use the project venv (and its dependencies),
set the override before launching:

```bash
# PowerShell
$env:DUDE_PYTHON = "$PWD\.venv\Scripts\python.exe"; npm run tauri dev

# Git Bash
DUDE_PYTHON="$PWD/.venv/Scripts/python.exe" npm run tauri dev
```

In Phase 0 the engine itself is stdlib-only, so plain `python` also works for
the demo; the override matters once runtime dependencies land.

## Environment variables

| Variable            | Used by  | Default                  | Meaning                          |
| ------------------- | -------- | ------------------------ | -------------------------------- |
| `DUDE_PYTHON`       | host     | `python` from PATH       | Interpreter used to run the engine |
| `DUDE_ENGINE_DIR`   | host     | `engine/` under repo root | Where `python -m dude.main` runs |
| `DUDE_LOG_LEVEL`    | engine   | `INFO`                   | `DEBUG/INFO/WARNING/ERROR`       |
| `DUDE_DATA_DIR`     | engine   | `%LOCALAPPDATA%\Dude`    | Per-user data directory          |

Copy `.env.example` to `.env` (git-ignored) to set engine variables; the
engine loads a `.env` from its working directory (`engine/`) if present.
Never commit `.env` or put secrets in it.

## Tests

```bash
# Engine unit tests (25 cases: protocol, lifecycle, config)
.venv\Scripts\python -m pytest engine/

# Rust integration tests (spawns the real engine, speaks protocol v1)
cd src-tauri && cargo test

# Frontend type-check + build
npm run build
```

## Engine smoke test without the app

```bash
# from engine/
$env:PYTHONPATH="."; ..\.venv\Scripts\python -m dude.main
```

Then paste frames on stdin:

```
{"v":1,"id":"1","op":"health"}
{"v":1,"id":"2","op":"shutdown"}
```

stdout carries protocol frames only; diagnostics go to stderr.

## Common issues

- **Engine shows "disconnected"** — run with `DUDE_PYTHON` pointed at the
  venv and check `RUST_LOG=debug` output; engine stderr appears in the
  `tauri dev` console.
- **Port 1420 in use** — another `tauri dev` is running; close it. Vite is
  pinned to 1420 (Tauri expects it).
- **Rust link errors** — install VS 2022 Build Tools with the
  "Desktop development with C++" workload.
- **Log file location** — `%LOCALAPPDATA%\Dude\engine.log` (engine) and the
  `tauri dev` terminal (host + engine stderr).

## Project layout

See [ARCHITECTURE.md](ARCHITECTURE.md) for the process model and protocol
contract, and [SECURITY.md](SECURITY.md) for the security posture.
