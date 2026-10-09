/**
 * Chat command wrappers for the Dude Tauri host (Sprint 3).
 *
 * Chat goes through the Rust host, which forwards ops to the Python engine
 * over the existing versioned stdio IPC. The UI never talks to the engine
 * directly. Streaming is a pull loop: `ai_stream_start` returns a handle,
 * `ai_stream_next` yields chunk/done frames, `ai_stream_cancel` aborts.
 *
 * Data handling: typed text only. Nothing here sends microphone audio
 * anywhere; only the text the user (or the explicit voice flow) submits can
 * reach the configured provider path, and the default provider is the local
 * deterministic fallback which sends nothing off device.
 */
import { invoke } from "@tauri-apps/api/core";
import type { AIStatus, AISubmitResult, AIStreamFrame } from "./types";

/** Provider/tool status snapshot, or offline defaults when unavailable. */
export async function fetchAIStatus(): Promise<AIStatus | null> {
  try {
    return await invoke<AIStatus>("ai_status");
  } catch {
    return null;
  }
}

/** Submit a message and get the full response. Throws a presentable message. */
export async function submitMessage(text: string): Promise<AISubmitResult> {
  return await invoke<AISubmitResult>("ai_submit", { text });
}

/** Start a streaming response; resolves with the stream handle. */
export async function startStream(text: string): Promise<string> {
  return await invoke<string>("ai_stream_start", { text });
}

/** Pull the next chunk or terminal frame for an open stream. */
export async function nextStreamFrame(streamId: string): Promise<AIStreamFrame> {
  return await invoke<AIStreamFrame>("ai_stream_next", { streamId });
}

/** Cancel an in-flight stream. Resolves even if the stream already finished. */
export async function cancelStream(streamId: string): Promise<void> {
  await invoke("ai_stream_cancel", { streamId });
}

/** Clear the in-memory conversation in the engine. */
export async function clearConversation(): Promise<void> {
  await invoke("ai_clear");
}

/** Pull a stream to completion, invoking onChunk for every chunk frame. */
export async function drainStream(
  streamId: string,
  onChunk: (index: number, text: string) => void,
): Promise<string> {
  let guard = 0;
  for (;;) {
    guard += 1;
    if (guard > 10_000) {
      // Bounded loop: a stream that never terminates is an engine bug.
      throw new Error("stream did not finish; cancelled after 10000 frames");
    }
    const frame = await nextStreamFrame(streamId);
    if (frame.frameType === "done") {
      return frame.finalText ?? "";
    }
    if (typeof frame.chunkIndex === "number" && typeof frame.chunk === "string") {
      onChunk(frame.chunkIndex, frame.chunk);
    }
  }
}
