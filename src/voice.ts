/**
 * Voice command wrappers for the Dude Tauri host (Sprint 2).
 *
 * Voice operations go through the Rust host, which forwards them to the
 * Python engine over the existing versioned stdio IPC. The UI never touches
 * audio devices or the engine directly.
 */
import { invoke } from "@tauri-apps/api/core";
import type { VoiceStatus } from "./types";

/** Current voice session status, or `null` if the host could not answer. */
export async function fetchVoiceStatus(): Promise<VoiceStatus | null> {
  try {
    return await invoke<VoiceStatus>("voice_status");
  } catch {
    return null;
  }
}

/** Start an explicit voice capture session. Throws a user-presentable message. */
export async function startVoice(): Promise<void> {
  await invoke("voice_start");
}

/** Finish the current recording and transcribe it. Throws a user-presentable message. */
export async function stopVoice(): Promise<void> {
  await invoke("voice_stop");
}

/** Cancel the current voice interaction without a transcript. */
export async function cancelVoice(): Promise<void> {
  await invoke("voice_cancel");
}

/** Interrupt any active speech playback immediately. */
export async function interruptVoice(): Promise<void> {
  await invoke("voice_interrupt");
}

/** Best-effort helper to return a finished voice session to idle. */
export async function resetVoice(): Promise<void> {
  await invoke("voice_reset");
}
