/**
 * Shared types for the Tauri command surface (Phase 0).
 *
 * These mirror the Rust `EngineStatus` struct returned by the
 * `engine_status` command. Keep both sides in sync; the contract is
 * documented in docs/ARCHITECTURE.md.
 */

/** High-level engine connection state shown in the UI. */
export type EngineState = "starting" | "connected" | "disconnected";

/** Status payload returned by the Rust host for the Python engine. */
export interface EngineStatus {
  state: EngineState;
  /** Safe, human-readable detail (never contains secrets or raw stderr). */
  detail: string;
  /** IPC protocol version reported by the engine, when known. */
  protocol_version?: number;
  /** Semantic version reported by the engine, when known. */
  engine_version?: string;
}
