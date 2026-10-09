"""Sprint 3 AI operation types for the Dude engine protocol.

Chat extends the existing v1 request/response contract with a small set of
conversation and provider operations. The engine still uses newline-delimited
JSON over stdio and preserves the existing health/voice/shutdown surface.

AI ops:
- ai_status        -> { configured, provider, can_stream, tool_count }
- ai_conversation  -> { message_count }
- ai_submit        -> { response, used_tools, tool_names }
- ai_stream_start  -> starts a streaming request and returns a stream handle
- ai_stream_next   -> returns the next chunk for a stream handle
- ai_stream_cancel -> cancels an in-flight stream
- ai_clear         -> clears the in-memory conversation
- ai_reset         -> clears conversation and resets an engine-side AI session cleanly
- ai_tools         -> returns the allowlisted tool registry

Streaming is modeled as a small correlated handle so the host and UI can drive
chunked updates without inventing a new listener or network service. The engine
owns the stream lifetime; it cancels or discards it on shutdown and on explicit
cancel.

Operational envelope rule (important):

Op handlers return *plain payload dicts* and raise ProtocolError on failure.
The engine's ``handle_frame`` builds the single response envelope
(``{v, id, ok, result|error}``). Frame builders here therefore return payload
shapes, not complete envelopes — this keeps the wire un-nested.

Constants here are synced with the Rust client types in
`src-tauri/src/engine/client.rs` (protocol version, op names, error codes).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

# Chat ops use the same protocol version as the rest of the engine.
AI_PROTOCOL_VERSION = 1

# AI ops (must match the Rust-side op strings used by the host client).
OP_AI_STATUS = "ai_status"
OP_AI_CONVERSATION = "ai_conversation"
OP_AI_SUBMIT = "ai_submit"
OP_AI_STREAM_START = "ai_stream_start"
OP_AI_STREAM_NEXT = "ai_stream_next"
OP_AI_STREAM_CANCEL = "ai_stream_cancel"
OP_AI_CLEAR = "ai_clear"
OP_AI_RESET = "ai_reset"
OP_AI_TOOLS = "ai_tools"

# Shared AI state values reported by ai_status.
AI_STATE_IDLE = "idle"
AI_STATE_SUBMITTING = "submitting"
AI_STATE_STREAMING = "streaming"
AI_STATE_DONE = "done"
AI_STATE_CANCELLED = "cancelled"
AI_STATE_ERROR = "error"

# AI error codes shared across the protocol boundary.
ERR_AI_NOT_CONFIGURED = "ai_not_configured"
ERR_AI_NOT_ALLOWED = "ai_not_allowed"
ERR_AI_BUSY = "ai_busy"
ERR_AI_STREAM_NOT_FOUND = "ai_stream_not_found"
ERR_AI_STREAM_ALREADY_FINISHED = "ai_stream_already_finished"
ERR_AI_INTERNAL = "ai_internal"
ERR_AI_UNSUPPORTED = "ai_unsupported"
ERR_AI_BAD_REQUEST = "ai_bad_request"


def ai_status_frame(
    configured: bool,
    provider: str,
    can_stream: bool,
    tool_count: int,
) -> dict:
    """Payload for the ai_status response result."""
    return {
        "configured": configured,
        "provider": provider,
        "can_stream": can_stream,
        "tool_count": tool_count,
    }


def ai_conversation_frame(message_count: int) -> dict:
    """Payload for the ai_conversation response result."""
    return {"message_count": message_count}


def ai_submit_result_frame(
    response: str,
    used_tools: bool,
    tool_names: list[str],
) -> dict:
    """Payload for the ai_submit response result."""
    return {
        "response": response,
        "used_tools": used_tools,
        "tool_names": list(tool_names),
    }


def ai_stream_start_result_frame(stream_id: str) -> dict:
    """Payload for the ai_stream_start response result."""
    return {"stream_id": stream_id}


def ai_stream_next_frame(stream_id: str, chunk_index: int, chunk_text: str) -> dict:
    """Chunk payload carried inside the response result for ai_stream_next."""
    return {
        "type": "ai_stream_chunk",
        "stream_id": stream_id,
        "chunk_index": chunk_index,
        "chunk": chunk_text,
    }


def ai_stream_done_frame(stream_id: str, final_text: str) -> dict:
    """Terminal payload carried inside the response result for ai_stream_next."""
    return {
        "type": "ai_stream_done",
        "stream_id": stream_id,
        "final": final_text,
    }


def ai_stream_cancel_result_frame(stream_id: str) -> dict:
    """Payload for the ai_stream_cancel response result."""
    return {"stream_id": stream_id, "cancelled": True}


def ai_clear_result_frame() -> dict:
    """Payload for the ai_clear / ai_reset response result."""
    return {"cleared": True}


def ai_tools_frame(tools: list[dict[str, Any]]) -> dict:
    """Payload for the ai_tools response result."""
    return {"tools": tools}


def normalize_ai_state(raw: Any) -> str:
    if isinstance(raw, str) and raw in {
        AI_STATE_IDLE,
        AI_STATE_SUBMITTING,
        AI_STATE_STREAMING,
        AI_STATE_DONE,
        AI_STATE_CANCELLED,
        AI_STATE_ERROR,
    }:
        return raw
    return AI_STATE_ERROR


def normalize_tool_names(raw: Any) -> list[str]:
    if isinstance(raw, list) and all(isinstance(x, str) for x in raw):
        return list(raw)
    return []
