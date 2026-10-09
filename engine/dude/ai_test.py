"""Focused checks for the Sprint 3 AI orchestration path.

These do not require a real model provider, microphone, or network access.
They exercise the deterministic fallback, the allowlisted tool registry,
in-memory conversation context, the streaming handle flow, and coexistence
with the existing voice surface.
"""

from __future__ import annotations

import pathlib
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


def test_voice_surface_intact() -> None:
    e = Engine(data_dir="tmp")
    status = e.op_voice_status({})
    assert status["state"] == "idle"

    # Starting voice is explicit and user-driven. In an environment without
    # the optional speech dependencies it must fail with a structured,
    # truthful error rather than pretending to record.
    resp = e.op_voice_start({"id": "v1"})
    if resp.get("ok"):
        assert resp["result"]["state"] == "recording"
    else:
        assert resp["error"]["code"] in {"voice_unavailable", "voice_not_allowed"}

    # voice_stop outside recording must be rejected, never crash.
    state_now = e.op_voice_status({})["state"]
    if state_now != "recording":
        stop = e.op_voice_stop({"id": "v2"})
        assert stop.get("ok") is False
        assert stop["error"]["code"] == "voice_not_allowed"


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
    assert e.op_ai_conversation({})["message_count"] == 0

    e.op_ai_submit({"id": "a", "text": "hello"})
    assert e.op_ai_conversation({})["message_count"] == 2

    e.op_ai_clear({"id": "c1"})
    assert e.op_ai_conversation({})["message_count"] == 0

    e.op_ai_reset({"id": "r1"})
    assert e.op_ai_conversation({})["message_count"] == 0


def test_submit_validates_input() -> None:
    e = Engine(data_dir="tmp")
    bad = e.op_ai_submit({"id": "x"})
    assert bad["error"]["code"] == "ai_bad_request"
    assert "message text is required" in bad["error"]["message"]


def test_submit_uses_deterministic_fallback() -> None:
    e = Engine(data_dir="tmp")
    resp = e.op_ai_submit({"id": "y", "text": "hello"})
    assert resp["ok"] is True
    assert "I don't have a configured AI model yet" in resp["result"]["response"]
    assert resp["result"]["used_tools"] is False


def test_streaming_handle_flow() -> None:
    e = Engine(data_dir="tmp")
    start = e.op_ai_stream_start({"id": "s1", "text": "time"})
    assert start["ok"] is True
    stream_id = start["result"]["stream_id"]

    chunk = e.op_ai_stream_next({"id": "n1", "stream_id": stream_id})
    assert chunk["type"] == "ai_stream_chunk"
    assert chunk["stream_id"] == stream_id
    assert chunk["chunk_index"] == 0
    assert chunk["chunk"]

    done = e.op_ai_stream_next({"id": "n2", "stream_id": stream_id})
    assert done["type"] == "ai_stream_done"
    assert done["stream_id"] == stream_id
    assert "It's" in done.get("final", "")
    assert "deterministic fallback" in done.get("final", "")

    cancel = e.op_ai_stream_cancel({"id": "c1", "stream_id": stream_id})
    assert cancel["ok"] is False
    assert cancel["error"]["code"] == "ai_stream_already_finished"


def test_stream_final_text_seen_by_context() -> None:
    e = Engine(data_dir="tmp")
    start = e.op_ai_stream_start({"id": "s1", "text": "date"})
    assert start["ok"] is True
    stream_id = start["result"]["stream_id"]

    chunk = e.op_ai_stream_next({"id": "n1", "stream_id": stream_id})
    assert chunk["type"] == "ai_stream_chunk"
    assert chunk["stream_id"] == stream_id
    assert chunk["chunk_index"] == 0
    assert chunk["chunk"]

    done = e.op_ai_stream_next({"id": "n2", "stream_id": stream_id})
    assert done["type"] == "ai_stream_done"
    assert done["stream_id"] == stream_id
    assert "Today is" in done.get("final", "")

    # The final streamed response is recorded in the in-memory conversation
    # exactly once: one user turn plus one assistant turn.
    conversation = e.op_ai_conversation({})
    assert conversation["message_count"] == 2, (
        conversation,
        [m.content for m in e.ai.conversation.messages],
    )
    assert any(
        m.role == "assistant" and "Today is" in m.content
        for m in e.ai.conversation.messages
    )


def test_cannot_submit_during_generation() -> None:
    e = Engine(data_dir="tmp")
    start = e.op_ai_stream_start({"id": "s1", "text": "hello"})
    assert start["ok"] is True
    stream_id = start["result"]["stream_id"]

    # While a stream is open, a new generation must be rejected as busy.
    busy = e.op_ai_submit({"id": "x", "text": "late"})
    assert busy["ok"] is False
    assert busy["error"]["code"] == "ai_busy"

    chunk = e.op_ai_stream_next({"id": "n1", "stream_id": stream_id})
    assert chunk["type"] == "ai_stream_chunk"

    done = e.op_ai_stream_next({"id": "n2", "stream_id": stream_id})
    assert done["type"] == "ai_stream_done"

    # Once the stream finishes, submission is allowed again.
    later = e.op_ai_submit({"id": "y", "text": "after"})
    assert later["ok"] is True, later
