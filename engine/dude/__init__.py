"""Dude engine package.

The Dude engine is a small Python process that the Rust/Tauri desktop host
starts as a child process. It speaks newline-delimited JSON over stdio
(protocol version 1) and exposes a minimal, versioned operation surface.

Phase 0 scope: readiness/health and graceful shutdown only.
"""

ENGINE_NAME = "dude-engine"
ENGINE_VERSION = "0.1.0"

# Wire protocol version. Bump on any breaking change to the message shapes;
# see docs/ARCHITECTURE.md for the protocol contract and change policy.
PROTOCOL_VERSION = 1
