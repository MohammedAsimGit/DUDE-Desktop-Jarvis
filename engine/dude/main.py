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
from typing import Any, Iterator, Mapping, Optional

from . import ENGINE_NAME, ENGINE_VERSION, PROTOCOL_VERSION
from .config import ConfigError, load_config
from .logging_setup import setup_logging
from .protocol import (
    ERR_UNKNOWN_OP,
    ProtocolError,
    Request,
    error_frame,
    hello_frame,
    parse_frame,
    success_frame,
    validate_request,
)
from .voice import VoiceProviders, VoiceSession
from .voice_protocol import (
    ERR_VOICE_INTERNAL,
    ERR_VOICE_NOT_ALLOWED,
    ERR_VOICE_UNAVAILABLE,
    OP_VOICE_CANCEL,
    OP_VOICE_INTERRUPT,
    OP_VOICE_START,
    OP_VOICE_STOP,
    OP_VOICE_STATUS,
    normalize_voice_result,
    voice_error_frame,
    voice_result_frame,
    voice_status_frame,
)
from .ai import AIOrchestrator, AINotConfigured, DeterministicAIProvider, ToolRegistry, ToolSchema
from .ai_protocol import (
    ERR_AI_BAD_REQUEST,
    ERR_AI_BUSY,
    ERR_AI_INTERNAL,
    ERR_AI_NOT_CONFIGURED,
    ERR_AI_STREAM_ALREADY_FINISHED,
    ERR_AI_STREAM_NOT_FOUND,
    OP_AI_CLEAR,
    OP_AI_CONVERSATION,
    OP_AI_RESET,
    OP_AI_STREAM_CANCEL,
    OP_AI_STREAM_NEXT,
    OP_AI_STREAM_START,
    OP_AI_STATUS,
    OP_AI_SUBMIT,
    OP_AI_TOOLS,
    ai_clear_result_frame,
    ai_conversation_frame,
    ai_error_frame,
    ai_stream_cancel_result_frame,
    ai_stream_done_frame,
    ai_stream_next_frame,
    ai_stream_start_result_frame,
    ai_status_frame,
    ai_submit_result_frame,
    ai_tools_frame,
    normalize_tool_names,
)

# Keep a small allowlisted tool registry so tool use stays narrow.
_DEFAULT_TOOL_REGISTRY = ToolRegistry()


def _register_default_tools(registry: ToolRegistry) -> None:
    """Register only safe, read-only, deterministic demonstration tools here.

    If a tool is not useful and safe for this first version, do not add it.
    """

    def current_time_handler(_args: Mapping[str, Any]) -> str:
        from datetime import datetime

        return datetime.now().strftime("%Y-%m-%d %H:%M")

    registry.register(
        ToolSchema(
            name="get_current_time",
            description="Return the current local date/time as a string. No side effects.",
            parameters={},
        ),
        current_time_handler,
    )


_register_default_tools(_DEFAULT_TOOL_REGISTRY)

logger = logging.getLogger(__name__)


class Engine:
    """Serves protocol v1 requests; Phase 0 + Sprint 2 + Sprint 3.

    - Phase 0: health + shutdown over versioned stdio IPC
    - Sprint 2: explicit voice start/stop/cancel/interrupt + voice_status
    - Sprint 3: in-memory chat, provider interface, streaming/cancel, and a
      small allowlisted tool registry

    Only one AI generation may be in flight at a time. Voice and chat share the
    same engine, but microphone audio is never sent to any AI provider.
    """

    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir
        self._stop_requested = False
        self.voice = VoiceSession(VoiceProviders())

        # AI is optional: if a real provider is configured later, it is swapped
        # in through the provider interface. Until then, use the deterministic
        # no-AI fallback so the engine can still accept submissions safely.
        self.ai = AIOrchestrator(
            provider=DeterministicAIProvider(),
            registry=_DEFAULT_TOOL_REGISTRY,
        )

        self._ai_generating = False
        self._ai_stream_id_counter = 0
        self._ai_streams: dict[str, dict[str, Any]] = {}

    # -- AI operations ---------------------------------------------------------

    def op_ai_status(self, args: Mapping[str, Any]) -> dict:
        return ai_status_frame(
            configured=self.ai.provider_configured,
            provider=self.ai.provider_name,
            can_stream=True,
            tool_count=len(self.ai.registry.list()),
        )

    def op_ai_conversation(self, args: Mapping[str, Any]) -> dict:
        return ai_conversation_frame(message_count=len(self.ai.conversation.messages))

    def op_ai_submit(self, args: Mapping[str, Any]) -> dict:
        if not isinstance(args.get("text"), str) or not args.get("text"):
            return ai_error_frame(
                OP_AI_SUBMIT,
                args.get("id"),
                ERR_AI_BAD_REQUEST,
                "message text is required",
            )
        if self._ai_generating:
            return ai_error_frame(
                OP_AI_SUBMIT,
                args.get("id"),
                ERR_AI_BUSY,
                "a generation is already in progress",
            )
        self._ai_generating = True
        try:
            result = self.ai.submit(str(args["text"]))
            return ai_submit_result_frame(
                OP_AI_SUBMIT,
                args.get("id"),
                result.text_or_fallback("I received your message."),
                bool(result.used_tools),
                normalize_tool_names([]),
            )
        except AINotConfigured:
            return ai_error_frame(
                OP_AI_SUBMIT,
                args.get("id"),
                ERR_AI_NOT_CONFIGURED,
                "no AI provider is configured",
            )
        except AIError:
            logger.exception("AI submit failed")
            return ai_error_frame(
                OP_AI_SUBMIT,
                args.get("id"),
                ERR_AI_INTERNAL,
                "AI request failed",
            )
        finally:
            self._ai_generating = False

    def op_ai_stream_start(self, args: Mapping[str, Any]) -> dict:
        if not isinstance(args.get("text"), str) or not args.get("text"):
            return ai_error_frame(
                OP_AI_STREAM_START,
                args.get("id"),
                ERR_AI_BAD_REQUEST,
                "message text is required",
            )
        if self._ai_generating:
            return ai_error_frame(
                OP_AI_STREAM_START,
                args.get("id"),
                ERR_AI_BUSY,
                "a generation is already in progress",
            )
        self._ai_generating = True
        stream_id = f"stream-{self._ai_stream_id_counter}"
        self._ai_stream_id_counter += 1
        try:
            # begin_stream registers the user turn and creates the chunk
            # iterator eagerly; provider setup failures surface here while we
            # still control the busy flag.
            chunk_iter = self.ai.begin_stream(str(args["text"]))
            self._ai_streams[stream_id] = {
                "chunks": chunk_iter,
                "consumed": False,
                "final": None,
                "done": False,
                "produced": False,
            }
            return ai_stream_start_result_frame(
                OP_AI_STREAM_START,
                args.get("id"),
                stream_id,
            )
        except AINotConfigured:
            self._ai_generating = False
            return ai_error_frame(
                OP_AI_STREAM_START,
                args.get("id"),
                ERR_AI_NOT_CONFIGURED,
                "no AI provider is configured",
            )
        except AIError:
            self._ai_generating = False
            logger.exception("AI stream start failed")
            return ai_error_frame(
                OP_AI_STREAM_START,
                args.get("id"),
                ERR_AI_INTERNAL,
                "AI streaming is not available",
            )

    def op_ai_stream_next(self, args: Mapping[str, Any]) -> dict:
        stream_id = args.get("stream_id")
        if not isinstance(stream_id, str) or not stream_id:
            return ai_error_frame(
                OP_AI_STREAM_NEXT,
                args.get("id"),
                ERR_AI_BAD_REQUEST,
                "stream_id is required",
            )
        stream = self._ai_streams.get(stream_id)
        if stream is None:
            return ai_error_frame(
                OP_AI_STREAM_NEXT,
                args.get("id"),
                ERR_AI_STREAM_NOT_FOUND,
                "stream not found",
            )
        if stream["done"]:
            return ai_error_frame(
                OP_AI_STREAM_NEXT,
                args.get("id"),
                ERR_AI_STREAM_ALREADY_FINISHED,
                "stream already finished",
            )

        # First pull: capture the provider's chunk iterator eagerly so later
        # next calls see deterministic behavior even after the busy window
        # was released. Providers yield plain text chunks; the engine owns the
        # chunk index.
        if not stream["consumed"]:
            try:
                text = next(stream["chunks"])
            except StopIteration:
                # Provider produced nothing at all: finish with no final text
                # and roll back the dangling user turn so retry stays clean.
                stream["done"] = True
                stream["final"] = ""
                self._ai_generating = False
                self.ai.rollback_stream()
                return ai_stream_done_frame(stream_id, "")
            except BaseException:
                stream["done"] = True
                stream["final"] = ""
                self._ai_generating = False
                self.ai.rollback_stream()
                logger.exception("AI stream chunk retrieval failed")
                return ai_stream_done_frame(stream_id, "")
            stream["remaining"] = [text]
            stream["consumed"] = True

        remaining = stream.get("remaining", [])
        if not remaining:
            # The iterator was already exhausted after the last chunk was
            # delivered, so this call is the terminal frame.
            stream["done"] = True
            final = stream["final"] or ""
            if final:
                self.ai.conversation.add_assistant(final)
            self._ai_generating = False
            return ai_stream_done_frame(stream_id, final)

        chunk_index = stream.get("next_index", 0)
        chunk_text = remaining.pop(0)
        stream["next_index"] = chunk_index + 1

        if stream.get("final") is None:
            stream["final"] = chunk_text
        else:
            stream["final"] += chunk_text

        # Pull one lookahead chunk so we know whether more text follows. If
        # the iterator is exhausted we keep the delivered chunk as a chunk
        # frame and report done on the next call; this keeps chunk frames and
        # the terminal frame cleanly separated.
        more: Optional[str] = None
        exhausted = False
        try:
            more = next(stream["chunks"])
        except StopIteration:
            exhausted = True
        except BaseException:
            exhausted = True
            logger.exception("AI stream chunk retrieval failed")

        if more is not None:
            stream["remaining"].append(more)

        if exhausted and not stream["remaining"]:
            stream["exhausted"] = True

        return ai_stream_next_frame(stream_id, chunk_index, chunk_text)

    def op_ai_stream_cancel(self, args: Mapping[str, Any]) -> dict:
        stream_id = args.get("stream_id")
        if not isinstance(stream_id, str) or not stream_id:
            return ai_error_frame(
                OP_AI_STREAM_CANCEL,
                args.get("id"),
                ERR_AI_BAD_REQUEST,
                "stream_id is required",
            )
        stream = self._ai_streams.get(stream_id)
        if stream is None:
            return ai_error_frame(
                OP_AI_STREAM_CANCEL,
                args.get("id"),
                ERR_AI_STREAM_NOT_FOUND,
                "stream not found",
            )
        if stream["done"]:
            return ai_error_frame(
                OP_AI_STREAM_CANCEL,
                args.get("id"),
                ERR_AI_STREAM_ALREADY_FINISHED,
                "stream already finished",
            )
        stream["done"] = True
        self._ai_generating = False

        # If nothing was produced yet, roll back the dangling user turn so a
        # retry after cancel starts clean.
        if not stream["produced"]:
            self.ai.rollback_stream()

        self.ai.cancel_current()
        return ai_stream_cancel_result_frame(stream_id)

    def op_ai_clear(self, args: Mapping[str, Any]) -> dict:
        self.ai.reset_conversation()
        return ai_clear_result_frame(args.get("id"))

    def op_ai_reset(self, args: Mapping[str, Any]) -> dict:
        self.ai.reset_conversation()
        return ai_clear_result_frame(args.get("id"))

    def op_ai_tools(self, args: Mapping[str, Any]) -> dict:
        return ai_tools_frame(self.ai.registry.list())

    # -- Phase 0 operations -------------------------------------------------

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

    # -- Sprint 2 voice operations -----------------------------------------

    def op_voice_status(self, args: Mapping[str, Any]) -> dict:
        return voice_status_frame(
            state=self.voice.state,
            transcript=self.voice.transcript or "",
            error=self.voice.error or "",
        )

    def op_voice_start(self, args: Mapping[str, Any]) -> dict:
        if not self.voice.can_start():
            if self.voice.state != "idle":
                return voice_error_frame(
                    OP_VOICE_START,
                    args.get("id"),
                    "voice_busy",
                    "a voice session is already in progress",
                )
            if not self.voice.providers.stt_available():
                return voice_error_frame(
                    OP_VOICE_START,
                    args.get("id"),
                    ERR_VOICE_UNAVAILABLE,
                    "speech recognition is not available",
                )
            return voice_error_frame(
                OP_VOICE_START,
                args.get("id"),
                ERR_VOICE_NOT_ALLOWED,
                "voice cannot be started in the current state",
            )
        self.voice.start()
        if self.voice.state == "error":
            return voice_error_frame(
                OP_VOICE_START,
                args.get("id"),
                ERR_VOICE_UNAVAILABLE,
                self.voice.error or "speech recognition is not available",
            )
        return voice_result_frame(
            OP_VOICE_START,
            args.get("id"),
            {"state": self.voice.state},
        )

    def op_voice_stop(self, args: Mapping[str, Any]) -> dict:
        if self.voice.state != "recording":
            return voice_error_frame(
                OP_VOICE_STOP,
                args.get("id"),
                ERR_VOICE_NOT_ALLOWED,
                f"cannot stop voice in state {self.voice.state}",
            )
        try:
            self.voice.stop()
        except Exception:  # noqa: BLE001 - keep voice failures bounded
            logger.exception("voice stop failed")
            return voice_error_frame(
                OP_VOICE_STOP,
                args.get("id"),
                ERR_VOICE_INTERNAL,
                "voice stop failed",
            )
        return voice_result_frame(
            OP_VOICE_STOP,
            args.get("id"),
            normalize_voice_result({
                "state": self.voice.state,
                "transcript": self.voice.transcript,
                "error": self.voice.error,
            }),
        )

    def op_voice_cancel(self, args: Mapping[str, Any]) -> dict:
        if not self.voice.active:
            return voice_error_frame(
                OP_VOICE_CANCEL,
                args.get("id"),
                ERR_VOICE_NOT_ALLOWED,
                "no active voice session to cancel",
            )
        try:
            self.voice.cancel()
        except Exception:  # noqa: BLE001 - keep voice failures bounded
            logger.exception("voice cancel failed")
            return voice_error_frame(
                OP_VOICE_CANCEL,
                args.get("id"),
                ERR_VOICE_INTERNAL,
                "voice cancel failed",
            )
        return voice_result_frame(
            OP_VOICE_CANCEL,
            args.get("id"),
            {"state": self.voice.state},
        )

    def op_voice_interrupt(self, args: Mapping[str, Any]) -> dict:
        if not self.voice.active:
            return voice_error_frame(
                OP_VOICE_INTERRUPT,
                args.get("id"),
                ERR_VOICE_NOT_ALLOWED,
                "no active voice session to interrupt",
            )
        try:
            self.voice.interrupt()
        except Exception:  # noqa: BLE001 - keep voice failures bounded
            logger.exception("voice interrupt failed")
            return voice_error_frame(
                OP_VOICE_INTERRUPT,
                args.get("id"),
                ERR_VOICE_INTERNAL,
                "voice interrupt failed",
            )
        return voice_result_frame(
            OP_VOICE_INTERRUPT,
            args.get("id"),
            {"state": self.voice.state},
        )

    def op_voice_reset(self, args: Mapping[str, Any]) -> dict:
        """Best-effort helper to return a finished session to idle.

        Intended for the host's periodic voice status polling path so the UI
        can surface "idle" after a terminal state. Safe to call repeatedly.
        """
        self.voice.reset()
        return voice_result_frame(
            "voice_reset",
            args.get("id"),
            {"state": self.voice.state},
        )

    # -- protocol loop ------------------------------------------------------

    def dispatch(self, req: Request) -> Optional[dict]:
        handlers = {
            "health": self.op_health,
            "shutdown": self.op_shutdown,
            OP_VOICE_STATUS: self.op_voice_status,
            OP_VOICE_START: self.op_voice_start,
            OP_VOICE_STOP: self.op_voice_stop,
            OP_VOICE_CANCEL: self.op_voice_cancel,
            OP_VOICE_INTERRUPT: self.op_voice_interrupt,
            "voice_reset": self.op_voice_reset,
            OP_AI_STATUS: self.op_ai_status,
            OP_AI_CONVERSATION: self.op_ai_conversation,
            OP_AI_SUBMIT: self.op_ai_submit,
            OP_AI_STREAM_START: self.op_ai_stream_start,
            OP_AI_STREAM_NEXT: self.op_ai_stream_next,
            OP_AI_STREAM_CANCEL: self.op_ai_stream_cancel,
            OP_AI_CLEAR: self.op_ai_clear,
            OP_AI_RESET: self.op_ai_reset,
            OP_AI_TOOLS: self.op_ai_tools,
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
        except Exception:  # noqa: BLE001 - last-resort internal guard
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
