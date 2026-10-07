"""Dude engine entry point: newline-delimited JSON over stdio (protocol v1).

Lifecycle
---------
1. Load + validate configuration; set up logging (stderr + file, never stdout).
2. Emit the unsolicited ``hello`` frame as the first line on stdout.
3. Serve requests from stdin, one JSON object per line, until a ``shutdown``
   op arrives or stdin reaches EOF (which also covers the host dying).
4. Exit cleanly with code 0. Startup failures exit non-zero.

Bounded, structured handling:
- malformed frames        -> ``bad_request`` error response, keep serving
- wrong protocol version  -> ``unsupported_version`` error response
- unknown operation       -> ``unknown_op`` error response
- internal errors         -> ``internal`` error response (no traceback to stdout)

Standard output carries protocol frames only; diagnostics go to stderr/logs.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Mapping, Optional

from . import ENGINE_NAME, ENGINE_VERSION, PROTOCOL_VERSION
from .config import ConfigError, load_config
from .logging_setup import setup_logging
from .protocol import (
    ERR_BAD_REQUEST,
    ERR_UNKNOWN_OP,
    ERR_UNSUPPORTED_VERSION,
    ProtocolError,
    Request,
    error_frame,
    hello_frame,
    parse_frame,
    success_frame,
    validate_request,
)

logger = logging.getLogger(__name__)


class Engine:
    """Serves protocol v1 requests; Phase 0 surface is health + shutdown."""

    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir
        self._stop_requested = False

    # -- operations ---------------------------------------------------------

    def op_health(self, args: Mapping[str, Any]) -> dict:
        return {
            "status": "ready",
            "engine": ENGINE_NAME,
            "version": ENGINE_VERSION,
            "protocol": PROTOCOL_VERSION,
            "data_dir": self.data_dir,
        }

    def op_shutdown(self, args: Mapping[str, Any]) -> dict:
        self._stop_requested = True
        return {"status": "shutting_down"}

    # -- protocol loop ------------------------------------------------------

    def dispatch(self, req: Request) -> Optional[dict]:
        """Return the result payload for a validated request, or None to stop."""
        handlers = {
            "health": self.op_health,
            "shutdown": self.op_shutdown,
        }
        handler = handlers.get(req.op)
        if handler is None:
            raise ProtocolError(ERR_UNKNOWN_OP, f"unknown operation '{req.op}'")
        return handler(req.args)

    def handle_frame(self, raw: bytes) -> Optional[dict]:
        """Handle one raw stdin line. Returns the frame to write, if any."""
        try:
            obj = parse_frame(raw)
            req = validate_request(obj)
        except ProtocolError as exc:
            return error_frame(None, exc.code, exc.message)

        try:
            result = self.dispatch(req)
        except ProtocolError as exc:
            return error_frame(req.id, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001 - last-resort internal guard
            logger.exception("internal error handling op %s", req.op)
            return error_frame(req.id, "internal", "internal engine error")

        return success_frame(req.id, result)

    def run(self) -> int:
        """Serve requests until shutdown/EOF. Returns the process exit code."""
        logger.info(
            "engine starting (protocol v%d, data_dir=%s)", PROTOCOL_VERSION, self.data_dir
        )
        sys.stdout.write(_encode_frame(hello_frame()))
        sys.stdout.flush()
        logger.info("hello frame sent; serving requests on stdio")

        try:
            for raw in sys.stdin.buffer:
                line = raw.strip()
                if not line:
                    continue  # tolerate blank lines between frames
                frame = self.handle_frame(line)
                if frame is not None:
                    sys.stdout.write(_encode_frame(frame))
                    sys.stdout.flush()

                if self._stop_requested:
                    logger.info("shutdown requested; exiting cleanly")
                    return 0
        except KeyboardInterrupt:
            logger.info("interrupted; exiting cleanly")
            return 0
        finally:
            logger.info("engine stopped")

        logger.info("stdin closed; exiting cleanly")
        return 0


def _encode_frame(frame: Mapping[str, Any]) -> str:
    import json

    return json.dumps(frame, separators=(",", ":")) + "\n"


def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        # Config problems must be actionable and non-sensitive; no env dumps.
        print(f"dude-engine: configuration error: {exc}", file=sys.stderr)
        return 2

    setup_logging(config)
    logger.info("%s %s", ENGINE_NAME, ENGINE_VERSION)

    engine = Engine(data_dir=str(config.data_dir))
    return engine.run()


if __name__ == "__main__":
    sys.exit(main())
