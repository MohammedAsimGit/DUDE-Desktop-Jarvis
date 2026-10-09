"""Sprint 2 voice operation types for the Dude engine protocol.

Voice extends the existing v1 request/response contract with a small set of
voice operations and a shared voice-state envelope. The engine still speaks
newline-delimited JSON over stdio; voice adds:

- voice_status  -> { state, transcript, error }
- voice_start   -> start an explicit capture session
- voice_stop    -> finish recording and transcribe
- voice_cancel  -> abort without a transcript
- voice_interrupt -> stop any active speech playback

Constants here are synced with the Rust client types in
`src-tauri/src/engine/client.rs` (protocol version, op names, error codes).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

# Voices ops use the same protocol version as the rest of the engine.
VOICE_PROTOCOL_VERSION = 1

# Voice ops (must match the Rust-side op strings used by the host client).
OP_VOICE_STATUS = "voice_status"
OP_VOICE_START = "voice_start"
OP_VOICE_STOP = "voice_stop"
OP_VOICE_CANCEL = "voice_cancel"
OP_VOICE_INTERRUPT = "voice_interrupt"

# Shared voice state values reported by voice_status.
# These mirror the UI voice-state enum added in Sprint 2.
VOICE_STATE_IDLE = "idle"
VOICE_STATE_RECORDING = "recording"
VOICE_STATE_PROCESSING = "processing"
VOICE_STATE_SPEAKING = "speaking"
VOICE_STATE_DONE = "done"
VOICE_STATE_CANCELLED = "cancelled"
VOICE_STATE_ERROR = "error"

# Voice error codes shared across the protocol boundary.
ERR_VOICE_UNAVAILABLE = "voice_unavailable"
ERR_VOICE_NOT_ALLOWED = "voice_not_allowed"
ERR_VOICE_BUSY = "voice_busy"
ERR_VOICE_MIC_UNAVAILABLE = "voice_mic_unavailable"
ERR_VOICE_STT_FAILED = "voice_stt_failed"
ERR_VOICE_TTS_FAILED = "voice_tts_failed"
ERR_VOICE_INTERNAL = "voice_internal"


def voice_status_frame(state: str, transcript: Optional[str], error: Optional[str]) -> dict:
    return {
        "v": VOICE_PROTOCOL_VERSION,
        "type": "voice_status",
        "state": state,
        "transcript": transcript or "",
        "error": error or "",
    }


def voice_result_frame(op: str, id: Optional[str], result: Mapping[str, Any]) -> dict:
    from . import PROTOCOL_VERSION

    return {
        "v": PROTOCOL_VERSION,
        "id": id,
        "ok": True,
        "result": dict(result),
    }


def voice_error_frame(op: str, id: Optional[str], code: str, message: str) -> dict:
    from . import PROTOCOL_VERSION

    return {
        "v": PROTOCOL_VERSION,
        "id": id,
        "ok": False,
        "error": {"code": code, "message": message},
    }


def normalize_voice_state(raw: Any) -> str:
    if isinstance(raw, str) and raw in {
        VOICE_STATE_IDLE,
        VOICE_STATE_RECORDING,
        VOICE_STATE_PROCESSING,
        VOICE_STATE_SPEAKING,
        VOICE_STATE_DONE,
        VOICE_STATE_CANCELLED,
        VOICE_STATE_ERROR,
    }:
        return raw
    return VOICE_STATE_ERROR


def normalize_voice_result(result: Mapping[str, Any]) -> dict:
    return {
        "state": normalize_voice_state(result.get("state")),
        "transcript": result.get("transcript") or "",
        "error": result.get("error") or "",
    }
