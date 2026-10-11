"""Focused checks for the Sprint 3 AI orchestration path.

These do not require a real model provider, microphone, or network access.
They exercise the deterministic fallback, the allowlisted tool registry,
in-memory conversation context, the streaming handle flow, and coexistence
with the existing voice surface.
"""

from __future__ import annotations

import pathlib
import json
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "engine"))

from dude.main import Engine  # noqa: E402


def test_health_still_works() -> None:
    e = Engine(data_dir="tmp")
    frame = e.op_health({"id": "h1"})
    assert frame["status"] == "ready"
    assert frame["protocol"] == 1
    assert frame["engine"] == "dude-engine"
    assert frame["version"] == "0.1.0"


def test_voice_surface_intact(monkeypatch) -> None:
    e = Engine(data_dir="tmp")
    status = e.op_voice_status({})
    assert status["state"] == "idle"

    # Starting voice is explicit and user-driven. Force the STT stack
    # unavailable so this test stays deterministic and never touches real
    # hardware or a real model: the engine must fail with a structured,
    # truthful error rather than pretending to record, and must surface
    # the provider's specific reason.
    monkeypatch.setattr(
        e.voice.providers, "stt_available", lambda: False
    )
    monkeypatch.setattr(
        e.voice.providers, "stt_error", lambda: "no VOSK model is installed"
    )
    resp = e.handle_frame(b'{"v":1,"id":"v1","op":"voice_start"}') or {}
    assert resp.get("ok") is False
    assert resp["error"]["code"] == "voice_unavailable"
    assert "VOSK model" in resp["error"]["message"]

    # voice_stop outside recording must be rejected, never crash.
    stop = e.handle_frame(b'{"v":1,"id":"v2","op":"voice_stop"}') or {}
    assert stop.get("ok") is False
    assert stop["error"]["code"] == "voice_not_allowed"


def test_voice_restart_after_terminal_state(monkeypatch) -> None:
    """A cancelled session must not block the next Start/Retry click."""

    class FakeSTT:
        def start_capture(self) -> None:
            pass

        def stop_capture(self) -> str:
            return ""

        def cancel(self) -> None:
            pass

    e = Engine(data_dir="tmp")
    # Deterministic in-memory provider: no hardware, no model.
    monkeypatch.setattr(e.voice.providers, "stt_available", lambda: True)
    monkeypatch.setattr(e.voice.providers, "stt", lambda: FakeSTT())

    # Simulate a finished session stuck in a terminal state.
    e.voice.state = "cancelled"
    resp = e.op_voice_start({})
    assert resp["state"] == "recording"

    # Same from a terminal error state: retry must be accepted, not busy.
    e.voice.state = "error"
    e.voice.error = "boom"
    resp2 = e.op_voice_start({})
    assert resp2["state"] == "recording"

    # An in-progress session is still protected from overlapping starts.
    busy = e.handle_frame(b'{"v":1,"id":"v9","op":"voice_start"}') or {}
    assert busy.get("ok") is False
    assert busy["error"]["code"] == "voice_busy"

    # Cancel releases the session cleanly and the mic-fake resources.
    cancel = e.op_voice_cancel({})
    assert cancel["state"] == "cancelled"


def test_ai_status_and_tools() -> None:
    e = Engine(data_dir="tmp")
    status = e.op_ai_status({})
    assert status["configured"] is True
    assert status["provider"] == "deterministic"
    assert status["can_stream"] is True
    assert status["tool_count"] >= 1

    tools = e.op_ai_tools({})
    assert isinstance(tools["tools"], list)
    names = {t["name"] for t in tools["tools"]}
    assert "get_current_time" in names


def test_conversation_boundaries() -> None:
    e = Engine(data_dir="tmp")
    conv = (e.handle_frame(b'{"v":1,"id":"c0","op":"ai_conversation"}') or {})
    assert conv["result"]["message_count"] == 0

    e.handle_frame(b'{"v":1,"id":"a","op":"ai_submit","args":{"text":"hello"}}')
    conv = (e.handle_frame(b'{"v":1,"id":"c1","op":"ai_conversation"}') or {})
    assert conv["result"]["message_count"] == 2

    e.handle_frame(b'{"v":1,"id":"cl","op":"ai_clear"}')
    conv = (e.handle_frame(b'{"v":1,"id":"c2","op":"ai_conversation"}') or {})
    assert conv["result"]["message_count"] == 0

    e.handle_frame(b'{"v":1,"id":"rs","op":"ai_reset"}')
    conv = (e.handle_frame(b'{"v":1,"id":"c3","op":"ai_conversation"}') or {})
    assert conv["result"]["message_count"] == 0


def test_submit_validates_input() -> None:
    e = Engine(data_dir="tmp")
    bad = e.handle_frame(b'{"v":1,"id":"x","op":"ai_submit","args":{}}') or {}
    assert bad["ok"] is False
    assert bad["error"]["code"] == "ai_bad_request"
    assert "message text is required" in bad["error"]["message"]


def test_submit_uses_deterministic_fallback() -> None:
    e = Engine(data_dir="tmp")
    resp = (e.handle_frame(b'{"v":1,"id":"y","op":"ai_submit","args":{"text":"hello"}}') or {})
    assert resp["ok"] is True
    assert "I don't have a configured AI model yet" in resp["result"]["response"]
    assert resp["result"]["used_tools"] is False


def test_streaming_handle_flow() -> None:
    e = Engine(data_dir="tmp")
    start = (e.handle_frame(b'{"v":1,"id":"s1","op":"ai_stream_start","args":{"text":"time"}}') or {})
    assert start["ok"] is True, start
    stream_id = start["result"]["stream_id"]

    chunk = (e.handle_frame(f'{{"v":1,"id":"n1","op":"ai_stream_next","args":{{"stream_id":"{stream_id}"}}}}'.encode()) or {})
    assert chunk["ok"] is True, chunk
    assert chunk["result"]["type"] == "ai_stream_chunk"
    assert chunk["result"]["stream_id"] == stream_id
    assert chunk["result"]["chunk_index"] == 0
    assert chunk["result"]["chunk"]

    done = (e.handle_frame(f'{{"v":1,"id":"n2","op":"ai_stream_next","args":{{"stream_id":"{stream_id}"}}}}'.encode()) or {})
    assert done["ok"] is True, done
    assert done["result"]["type"] == "ai_stream_done"
    assert done["result"]["stream_id"] == stream_id
    assert "It's" in done["result"].get("final", "")
    assert "deterministic fallback" in done["result"].get("final", "")

    # A third pull is rejected because the stream already finished.
    late = (e.handle_frame(f'{{"v":1,"id":"n3","op":"ai_stream_next","args":{{"stream_id":"{stream_id}"}}}}'.encode()) or {})
    assert late["ok"] is False
    assert late["error"]["code"] == "ai_stream_already_finished"


def test_stream_final_text_seen_by_context() -> None:
    e = Engine(data_dir="tmp")
    start = (e.handle_frame(b'{"v":1,"id":"s1","op":"ai_stream_start","args":{"text":"date"}}') or {})
    assert start["ok"] is True
    stream_id = start["result"]["stream_id"]

    chunk_frame = (e.handle_frame(f'{{"v":1,"id":"n1","op":"ai_stream_next","args":{{"stream_id":"{stream_id}"}}}}'.encode()) or {})
    assert chunk_frame["ok"] is True
    assert chunk_frame["result"]["type"] == "ai_stream_chunk"
    assert chunk_frame["result"]["stream_id"] == stream_id
    assert chunk_frame["result"]["chunk"]

    done = (e.handle_frame(f'{{"v":1,"id":"n2","op":"ai_stream_next","args":{{"stream_id":"{stream_id}"}}}}'.encode()) or {})
    assert done["ok"] is True
    assert done["result"]["type"] == "ai_stream_done"
    assert done["result"]["stream_id"] == stream_id
    assert "Today is" in done["result"].get("final", "")

    # The final streamed response is recorded in the in-memory conversation
    # exactly once: one user turn plus one assistant turn.
    conversation = (e.handle_frame(b'{"v":1,"id":"c","op":"ai_conversation"}') or {})
    assert conversation["result"]["message_count"] == 2, conversation
    assert any(
        m.role == "assistant" and "Today is" in m.content
        for m in e.ai.conversation.messages
    )


def test_cannot_submit_during_generation() -> None:
    e = Engine(data_dir="tmp")
    start = (e.handle_frame(b'{"v":1,"id":"s1","op":"ai_stream_start","args":{"text":"hello"}}') or {})
    assert start["ok"] is True
    stream_id = start["result"]["stream_id"]

    # While a stream is open, a new generation must be rejected as busy.
    busy = (e.handle_frame(b'{"v":1,"id":"x","op":"ai_submit","args":{"text":"late"}}') or {})
    assert busy["ok"] is False
    assert busy["error"]["code"] == "ai_busy"

    # Drain the stream, then submission is allowed again.
    while True:
        frame = (e.handle_frame(f'{{"v":1,"id":"n","op":"ai_stream_next","args":{{"stream_id":"{stream_id}"}}}}'.encode()) or {})
        if frame["result"].get("type") == "ai_stream_done":
            break

    later = (e.handle_frame(b'{"v":1,"id":"y","op":"ai_submit","args":{"text":"after"}}') or {})
    assert later["ok"] is True, later
