"""Smoke tests for the Dude engine protocol and lifecycle (Phase 0).

These exercise the engine in-process over a real pipe pair, covering: the
hello frame, health, shutdown, malformed frames, unsupported versions, unknown
ops, and clean exit on EOF.
"""

from __future__ import annotations

import io
import json
import sys

import pytest

from dude.main import Engine
from dude.protocol import (
    ERR_BAD_REQUEST,
    ERR_UNKNOWN_OP,
    ERR_UNSUPPORTED_VERSION,
    ProtocolError,
    parse_frame,
    validate_request,
)
from dude.config import ConfigError, load_config


# ── protocol validation ───────────────────────────────────────────


class TestValidateRequest:
    def test_valid_health_request(self):
        req = validate_request({"v": 1, "id": "r1", "op": "health"})
        assert req.id == "r1"
        assert req.op == "health"
        assert dict(req.args) == {}

    def test_valid_request_without_id(self):
        req = validate_request({"v": 1, "op": "shutdown"})
        assert req.id is None
        assert req.op == "shutdown"

    def test_missing_version_raises_unsupported(self):
        with pytest.raises(ProtocolError) as exc:
            validate_request({"id": "r1", "op": "health"})
        assert exc.value.code == ERR_UNSUPPORTED_VERSION

    def test_wrong_version_raises_unsupported(self):
        with pytest.raises(ProtocolError) as exc:
            validate_request({"v": 2, "id": "r1", "op": "health"})
        assert exc.value.code == ERR_UNSUPPORTED_VERSION

    def test_non_integer_version_raises_unsupported(self):
        with pytest.raises(ProtocolError) as exc:
            validate_request({"v": "1", "id": "r1", "op": "health"})
        assert exc.value.code == ERR_UNSUPPORTED_VERSION

    def test_bool_version_is_rejected(self):
        with pytest.raises(ProtocolError) as exc:
            validate_request({"v": True, "id": "r1", "op": "health"})
        assert exc.value.code == ERR_UNSUPPORTED_VERSION

    def test_missing_op_raises_bad_request(self):
        with pytest.raises(ProtocolError) as exc:
            validate_request({"v": 1, "id": "r1"})
        assert exc.value.code == ERR_BAD_REQUEST

    def test_non_string_id_raises_bad_request(self):
        with pytest.raises(ProtocolError) as exc:
            validate_request({"v": 1, "id": 42, "op": "health"})
        assert exc.value.code == ERR_BAD_REQUEST

    def test_args_must_be_object(self):
        with pytest.raises(ProtocolError) as exc:
            validate_request({"v": 1, "id": "r1", "op": "health", "args": [1, 2]})
        assert exc.value.code == ERR_BAD_REQUEST


class TestParseFrame:
    def test_valid_json_object(self):
        assert parse_frame(b'{"v": 1, "op": "health"}') == {"v": 1, "op": "health"}

    def test_invalid_json_raises_bad_request(self):
        with pytest.raises(ProtocolError) as exc:
            parse_frame(b"{not json")
        assert exc.value.code == ERR_BAD_REQUEST

    def test_non_object_raises_bad_request(self):
        with pytest.raises(ProtocolError) as exc:
            parse_frame(b"[1, 2, 3]")
        assert exc.value.code == ERR_BAD_REQUEST

    def test_invalid_utf8_raises_bad_request(self):
        with pytest.raises(ProtocolError) as exc:
            parse_frame(b"\xff\xfe\x00")
        assert exc.value.code == ERR_BAD_REQUEST

    def test_oversized_frame_raises_bad_request(self):
        with pytest.raises(ProtocolError) as exc:
            parse_frame(b'{"x": "' + b"a" * (2 * 1024 * 1024) + b'"}')
        assert exc.value.code == ERR_BAD_REQUEST


# ── engine lifecycle over a real pipe pair ────────────────────────


class ScriptedPipe(io.RawIOBase):
    """A minimal readable pipe over a fixed list of stdin lines."""

    def __init__(self, lines: list[bytes]):
        self._buffer = io.BytesIO(b"".join(line + b"\n" for line in lines))

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1) -> bytes:
        return self._buffer.read(n)

    def readinto(self, b) -> int:
        return self._buffer.readinto(b)


class FakeStdin:
    """Stands in for sys.stdin, exposing a .buffer like the real one."""

    def __init__(self, lines: list[bytes]):
        self.buffer = ScriptedPipe(lines)


def run_engine(lines: list[bytes]) -> tuple[list[dict], int]:
    """Run the Engine against scripted stdin; capture stdout frames."""
    out = io.StringIO()
    real_stdout = sys.stdout
    real_stdin = sys.stdin
    sys.stdout = out
    sys.stdin = FakeStdin(lines)
    try:
        code = Engine(data_dir=tmp_data_dir()).run()
    finally:
        sys.stdout = real_stdout
        sys.stdin = real_stdin
    frames = [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]
    return frames, code


def tmp_data_dir() -> str:
    import tempfile

    return tempfile.mkdtemp(prefix="dude-test-")


class TestEngineLoop:
    def test_hello_then_health_then_clean_eof(self):
        frames, code = run_engine([b'{"v":1,"id":"r1","op":"health"}'])
        assert code == 0

        hello, health = frames
        assert hello["type"] == "hello"
        assert hello["protocol"] == 1
        assert hello["engine"] == "dude-engine"

        assert health["id"] == "r1"
        assert health["ok"] is True
        assert health["result"]["status"] == "ready"
        assert health["result"]["protocol"] == 1

    def test_shutdown_op_exits_cleanly(self):
        frames, code = run_engine(
            [b'{"v":1,"id":"r2","op":"shutdown"}']
        )
        assert code == 0
        assert frames[-1]["id"] == "r2"
        assert frames[-1]["ok"] is True
        assert frames[-1]["result"]["status"] == "shutting_down"

    def test_malformed_json_keeps_serving(self):
        frames, code = run_engine(
            [b"{definitely not json", b'{"v":1,"id":"r3","op":"health"}']
        )
        assert code == 0
        hello, err, health = frames  # hello frame comes first on stdout
        assert hello["type"] == "hello"
        assert err["ok"] is False
        assert err["error"]["code"] == ERR_BAD_REQUEST
        assert err["id"] is None
        # engine kept serving afterwards
        assert health["ok"] is True
        assert health["id"] == "r3"

    def test_unsupported_version_reported(self):
        frames, code = run_engine([b'{"v":99,"id":"r4","op":"health"}'])
        assert code == 0
        assert frames[-1]["error"]["code"] == ERR_UNSUPPORTED_VERSION

    def test_unknown_op_reported(self):
        frames, code = run_engine([b'{"v":1,"id":"r5","op":"fly_to_the_moon"}'])
        assert code == 0
        assert frames[-1]["error"]["code"] == ERR_UNKNOWN_OP

    def test_blank_line_ignored(self):
        frames, code = run_engine([b"", b'{"v":1,"id":"r6","op":"health"}'])
        assert code == 0
        assert len(frames) == 2  # hello + health response

    def test_id_echoed_verbatim(self):
        frames, _ = run_engine([b'{"v":1,"id":"abc-123_XYZ","op":"health"}'])
        assert frames[-1]["id"] == "abc-123_XYZ"


# ── configuration validation ──────────────────────────────────────


class TestConfig:
    def test_invalid_log_level_rejected(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DUDE_LOG_LEVEL", raising=False)
        monkeypatch.setenv("DUDE_LOG_LEVEL", "LOUD")
        with pytest.raises(ConfigError) as exc:
            load_config()
        assert "DUDE_LOG_LEVEL" in str(exc.value)

    def test_valid_log_levels_accepted(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DUDE_LOG_LEVEL", raising=False)
        monkeypatch.setenv("DUDE_LOG_LEVEL", "debug")
        cfg = load_config()
        assert cfg.log_level == "DEBUG"

    def test_data_dir_created_and_used(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "custom data" / "dude"
        monkeypatch.setenv("DUDE_DATA_DIR", str(target))
        cfg = load_config()
        assert cfg.data_dir == target
        assert target.exists()

    def test_unicode_and_spaces_in_data_dir(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "späce ünïcode dir"
        monkeypatch.setenv("DUDE_DATA_DIR", str(target))
        cfg = load_config()
        assert cfg.data_dir.exists()
