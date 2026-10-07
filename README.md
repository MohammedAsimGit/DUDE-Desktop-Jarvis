# Dude

Dude is a native Windows desktop AI assistant: a React + TypeScript interface, a
Rust/Tauri desktop host, and a Python engine, talking over a versioned local IPC
boundary.

**Status: Phase 0 — architecture and project foundation.** The UI is intentionally
minimal (name, engine status). Voice, AI providers, memory, Windows control,
screen understanding, browser automation, and agents are *not* implemented yet.

## Stack

| Layer         | Technology                              |
| ------------- | --------------------------------------- |
| Desktop app   | Tauri 2.x (Windows, MSVC)               |
| UI            | React 18 + TypeScript (Vite)            |
| Native host   | Rust (via Tauri)                        |
| Engine        | Python 3.13 (venv + `requirements.txt`) |
| Local data    | SQLite (no schema yet — Phase 0 only boots the path) |
| IPC           | Newline-delimited JSON over stdio, protocol version 1 |

## Quick start

Prerequisites: Node 22+, npm, Python 3.13, Rust stable (MSVC), VS Build Tools,
WebView2. See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for details.

```bash
npm install
python -m venv .venv
.venv/Scripts/python -m pip install -r engine/requirements.txt
npm run tauri dev
```

The desktop window opens; the UI shows the engine connection status.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — process model, IPC protocol, layout
- [docs/SECURITY.md](docs/SECURITY.md) — security posture and Tauri capabilities
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — setup, scripts, workflow, troubleshooting
- [engine/README.md](engine/README.md) — engine internals and protocol examples

## Repo layout

```text
/
├── src/                  # React + TypeScript UI
├── src-tauri/            # Rust/Tauri host (spawn engine, health checks)
├── engine/               # Python Dude engine (stdio NDJSON protocol)
├── docs/                 # Architecture, security, development guides
└── .env.example          # Reserved configuration (no secrets, ever)
```
