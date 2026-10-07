"""Versioned wire protocol for the Dude engine (protocol v1).

Framing: one JSON object per line (newline-delimited JSON) on standard input /
standard output. Standard output is reserved exclusively for protocol frames;
all diagnostics go to standard error or a log file.

Message shapes (v1)
-------------------

Hello (engine -> host, unsolicited first line after startup)::

    {"v":1,"type":"hello","protocol":1,"engine":"dude-engine","version":"0.1.0"}

Request (host -> engine)::

    {"v":1,"id":"<correlation-id>","op":"health"}
    {"v":1,"id":"<correlation-id>","op":"shutdown"}

Success response (engine -> host)::

    {"v":1,"id":"<correlation-id>","ok":true,"result":{...}}

Error response (engine -> host)::

    {"v":1,"id":"<correlation-id>","ok":false,"error":{"code":"bad_request","message":"..."}}

Error codes: ``bad_request``, ``unsupported_version``, ``unknown_op``.

``id`` is an opaque correlation string chosen by the host and echoed verbatim.
On a malformed frame that cannot be parsed or lacks a usable ``id``, the engine
emits an error response with ``id: null`` and continues serving (it only exits
on ``shutdown`` or EOF).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional

# Error codes (stable, documented contract).
ERR_BAD_REQUEST = "bad_request"
ERR_UNSUPPORTED_VERSION = "unsupported_version"
ERR_UNKNOWN_OP = "unknown_op"

_MAX_FRAME_BYTES = 1 * 1024 * 1024  # refuse absurdly large lines


class ProtocolError(Exception):
    """A frame failed validation. ``code`` is a stable wire error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Request:
    """A validated host request."""

    id: Optional[str]
    op: str
    args: Mapping[str, Any]


def parse_frame(raw: bytes) -> dict:
    """Parse one raw line into a JSON object.

    Raises ProtocolError(ERR_BAD_REQUEST) on undecodable/oversized/non-object
    frames.
    """
    if len(raw) > _MAX_FRAME_BYTES:
        raise ProtocolError(ERR_BAD_REQUEST, "frame too large")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ProtocolError(ERR_BAD_REQUEST, "frame is not valid UTF-8")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        raise ProtocolError(ERR_BAD_REQUEST, "frame is not valid JSON")
    if not isinstance(obj, dict):
        raise ProtocolError(ERR_BAD_REQUEST, "frame must be a JSON object")
    return obj


def validate_request(obj: dict) -> Request:
    """Validate a parsed frame against the v1 request shape.

    Raises ProtocolError with ERR_UNSUPPORTED_VERSION for wrong protocol
    versions and ERR_BAD_REQUEST for shape problems.
    """
    version = obj.get("v")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ProtocolError(
            ERR_UNSUPPORTED_VERSION, "missing or non-integer protocol version 'v'"
        )
    if version != 1:
        raise ProtocolError(
            ERR_UNSUPPORTED_VERSION, f"unsupported protocol version {version}"
        )

    req_id = obj.get("id")
    if req_id is not None and not isinstance(req_id, str):
        raise ProtocolError(ERR_BAD_REQUEST, "'id' must be a string when present")

    op = obj.get("op")
    if not isinstance(op, str) or not op:
        raise ProtocolError(ERR_BAD_REQUEST, "missing or invalid 'op'")

    args = obj.get("args", {})
    if not isinstance(args, dict):
        raise ProtocolError(ERR_BAD_REQUEST, "'args' must be an object when present")

    return Request(id=req_id, op=op, args=args)


def hello_frame() -> dict:
    """The unsolicited readiness frame the engine writes as its first output."""
    from . import ENGINE_NAME, ENGINE_VERSION, PROTOCOL_VERSION

    return {
        "v": PROTOCOL_VERSION,
        "type": "hello",
        "protocol": PROTOCOL_VERSION,
        "engine": ENGINE_NAME,
        "version": ENGINE_VERSION,
    }


def success_frame(req_id: Optional[str], result: Mapping[str, Any]) -> dict:
    from . import PROTOCOL_VERSION

    return {"v": PROTOCOL_VERSION, "id": req_id, "ok": True, "result": dict(result)}


def error_frame(req_id: Optional[str], code: str, message: str) -> dict:
    from . import PROTOCOL_VERSION

    return {
        "v": PROTOCOL_VERSION,
        "id": req_id,
        "ok": False,
        "error": {"code": code, "message": message},
    }
