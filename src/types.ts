/**
 * Shared types for the Tauri command surface (Phase 0 + Sprint 2 + Sprint 3).
 *
 * These mirror the Rust command return types. Keep both sides in sync; the
 * contract is documented in docs/ARCHITECTURE.md.
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

/** Voice interaction states surfaced by the engine and the host. */
export type VoiceState =
  | "idle"
  | "recording"
  | "processing"
  | "speaking"
  | "done"
  | "cancelled"
  | "error";

export interface VoiceStatus {
  state: VoiceState;
  transcript: string;
  error: string;
}

/** Provider/tool status surfaced by the host's `ai_status` command. */
export interface AIStatus {
  /** True when the engine can truthfully answer chat. The deterministic
   * fallback reports "configured" because it is not a model but can respond. */
  configured: boolean;
  /** Provider name, e.g. "deterministic" (non-AI fallback) or "offline". */
  provider: string;
  canStream: boolean;
  toolCount: number;
}

/** Result of a synchronous `ai_submit`. */
export interface AISubmitResult {
  response: string;
  usedTools: boolean;
  toolNames: string[];
}

/** One streaming frame: either a chunk or the terminal done. */
export interface AIStreamFrame {
  frameType: "chunk" | "done";
  streamId: string;
  chunkIndex?: number;
  chunk?: string;
  /** Full accumulated text on the terminal frame. */
  finalText?: string;
}
