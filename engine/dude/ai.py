"""Dude engine AI provider interface and conversation orchestration (Sprint 3).

This module keeps the AI boundary small and replaceable. The engine does not
pick a model, send prompts to a cloud service, or download model weights by
default. Instead:

- Provider selection and configuration come from the environment / local config
  path. If nothing is configured, the engine reports an honest
  "not configured" outcome.
- Local-first providers are supported where practical.
- Cloud providers are allowed only as explicit opt-in adapters; they must be
  configured and enabled by the user, and they must disclose that conversation
  content would leave the device.
- Conversation context is in-memory only for the current session. Nothing is
  persisted by default.

Audio and transcripts are handled like this:

- Microphone audio is never sent to an AI provider.
- Only the text transcript that the user submits can be sent to a configured
  provider path, and only if that path is disclosed as local or opted-in cloud.
- Prompts, transcripts, and generated responses are not logged by default.

Tool support (Sprint 3 foundation only):

- There is a small allowlisted tool registry with typed schemas and strict
  validation.
- One harmless, read-only, deterministic demonstration tool is included where
  it is safe and useful: current local date/time. The engine never executes
  arbitrary model output or arbitrary commands.

Architecture note:
- The AI layer does not own audio, window, or lifecycle concerns. Those stay
  in voice.py and the existing engine/IPC boundaries.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Iterator, Mapping, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider errors
# ---------------------------------------------------------------------------


class AIError(Exception):
    """The AI layer could not complete a request in a UI-presentable way."""


class AINotConfigured(AIError):
    """No usable provider is configured in this environment."""


class AIProviderError(AIError):
    """A configured provider failed."""


# ---------------------------------------------------------------------------
# Tool support (allowlisted, validated, no arbitrary execution)
# ---------------------------------------------------------------------------

_MAX_TOOL_ARGS_BYTES = 8 * 1024  # bound tool-call payloads
_MAX_TOOL_NAME_LEN = 64
_MAX_TOOL_DESCRIPTION_LEN = 512


class ToolSchema:
    """A declared tool's shape."""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: Mapping[str, Any],
    ) -> None:
        if not name or not isinstance(name, str):
            raise ValueError("tool name must be a non-empty string")
        if len(name) > _MAX_TOOL_NAME_LEN:
            raise ValueError(f"tool name too long (max {_MAX_TOOL_NAME_LEN})")
        if not isinstance(description, str):
            raise ValueError("tool description must be a string")
        if len(description) > _MAX_TOOL_DESCRIPTION_LEN:
            raise ValueError(
                f"tool description too long (max {_MAX_TOOL_DESCRIPTION_LEN})"
            )
        if not isinstance(parameters, Mapping):
            raise ValueError("tool parameters must be a mapping")
        self.name = name
        self.description = description
        self.parameters = dict(parameters)


class ToolHandle:
    """A registered, safe tool with a typed handler."""

    def __init__(self, schema: ToolSchema, handler: Any) -> None:
        self.schema = schema
        self._handler = handler

    def run(self, arguments: Mapping[str, Any]) -> Any:
        return self._handler(arguments)


class ToolRegistry:
    """Allowlist-based registry for safe, deterministic tools.

    Sprint 3 uses this to keep any "tool use" capability narrow and explicit.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolHandle] = {}

    def register(self, schema: ToolSchema, handler: Any) -> None:
        self._tools[schema.name] = ToolHandle(schema, handler)

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.schema.name,
                "description": tool.schema.description,
                "parameters": tool.schema.parameters,
            }
            for tool in self._tools.values()
        ]

    def has(self, name: str) -> bool:
        return name in self._tools

    def run(self, name: str, arguments: Mapping[str, Any]) -> Any:
        if not isinstance(name, str) or not name:
            raise AIProviderError("invalid tool name")
        if name not in self._tools:
            raise AIProviderError(f"unknown tool '{name}'")

        blob = f"{name}\n{json_dumps(arguments)}"
        if len(blob.encode("utf-8")) > _MAX_TOOL_ARGS_BYTES:
            raise AIProviderError("tool arguments too large")

        return self._tools[name].run(arguments)


def json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, separators=(",", ":"))


# ---------------------------------------------------------------------------
# In-memory conversation context
# ---------------------------------------------------------------------------

_MAX_MESSAGES = 64
_MAX_MESSAGE_CHARS = 4096


class ConversationMessage:
    """A single message in the current in-memory conversation."""

    def __init__(self, role: str, content: str) -> None:
        if not isinstance(role, str) or not role:
            raise ValueError("message role must be a non-empty string")
        if not isinstance(content, str):
            raise ValueError("message content must be a string")
        self.role = role
        self.content = content[:_MAX_MESSAGE_CHARS]

    def to_provider_format(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content}


class Conversation:
    """Short-term, in-memory conversation context for the active session.

    This is not a history store. It does not persist to disk, does not create
    user profiles, and does not build long-term memory.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self._messages: list[ConversationMessage] = []

    @property
    def messages(self) -> list[ConversationMessage]:
        return list(self._messages)

    def add_user(self, content: str) -> None:
        self._messages.append(ConversationMessage("user", content))
        self._trim()

    def add_assistant(self, content: str) -> None:
        self._messages.append(ConversationMessage("assistant", content))
        self._trim()

    def add_system(self, content: str) -> None:
        self._messages.append(ConversationMessage("system", content))
        self._trim()

    def drop_last_user(self) -> None:
        """Remove the trailing user message, if any.

        Used by the streaming path to roll back a dangling user turn when the
        provider fails before producing any response text.
        """
        if self._messages and self._messages[-1].role == "user":
            self._messages.pop()

    def clear(self) -> None:
        self._messages.clear()

    def as_messages(self) -> list[dict[str, Any]]:
        return [m.to_provider_format() for m in self._messages]

    def _trim(self) -> None:
        if len(self._messages) > _MAX_MESSAGES:
            self._messages = self._messages[-_MAX_MESSAGES:]


# ---------------------------------------------------------------------------
# AI provider interface
# ---------------------------------------------------------------------------


class BaseAIProvider:
    """Contract for an AI provider adapter.

    Implementations must:
    - be local-first or clearly disclosed as a configured cloud provider
    - produce structured message-driven output
    - never log full prompts or responses by default
    """

    def name(self) -> str:
        raise NotImplementedError

    def configured(self) -> bool:
        raise NotImplementedError

    def system_instruction(self) -> str:
        raise NotImplementedError

    def generate(self, messages: list[dict[str, Any]]) -> "AICompletion":
        raise NotImplementedError

    def stream_chunks(
        self, messages: list[dict[str, Any]]
    ) -> Iterator[str]:
        """Yield incremental text chunks.

        The default implementation provides the full response as a single
        chunk, which keeps the streaming handle path usable for providers that
        do not support real incremental streaming.
        """
        completion = self.generate(messages)
        if completion.text:
            yield completion.text


class AICompletion:
    """The result of a generate call."""

    def __init__(
        self,
        text: str,
        *,
        used_tools: bool = False,
        raw_provider_fields: Optional[dict[str, Any]] = None,
    ) -> None:
        self.text = text
        self.used_tools = used_tools
        self.raw_provider_fields = raw_provider_fields or {}

    def text_or_fallback(self, fallback: str) -> str:
        if self.text:
            return self.text
        return fallback


class StopToken:
    """Sentinel used to request cancellation of a long-running generate."""

    pass


# ---------------------------------------------------------------------------
# Default no-op provider
# ---------------------------------------------------------------------------


class NoOpAIProvider(BaseAIProvider):
    """Transparent "not configured" provider used when nothing else is set up."""

    def name(self) -> str:
        return "none"

    def configured(self) -> bool:
        return False

    def system_instruction(self) -> str:
        return "Dude has no AI provider configured."

    def generate(self, messages: list[dict[str, Any]]) -> AICompletion:
        return AICompletion("")


# ---------------------------------------------------------------------------
# Simple deterministic provider used before any real model is configured
# ---------------------------------------------------------------------------

_SYSTEM_ONLY_MESSAGE = (
    "You are Dude, a local desktop assistant. You do not have access to files, "
    "shell, browser, screen, keyboard, mouse, or any other computer control. "
    "Do not pretend to run commands or access the system. If you are unsure, "
    "say so plainly."
)


class DeterministicAIProvider(BaseAIProvider):
    """A clearly non-AI fallback used before a real provider is configured.

    It still respects the provider interface and the allowlisted tools, but its
    output is explicit non-AI behavior so the UI can present a truthful response
    type. Its plain-language replies say they come from a local deterministic
    fallback, not from an AI model.
    """

    def name(self) -> str:
        return "deterministic"

    def configured(self) -> bool:
        return True

    def system_instruction(self) -> str:
        return _SYSTEM_ONLY_MESSAGE

    def generate(self, messages: list[dict[str, Any]]) -> AICompletion:
        return AICompletion(self._respond(messages))

    def _respond(self, messages: list[dict[str, Any]]) -> str:
        user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_text = m.get("content", "")
                break

        if not user_text:
            return "I'm here. You can type a request or use voice when it is available."

        lowered = user_text.strip().lower()

        if any(start in lowered for start in ("hello", "hi", "hey")):
            return (
                "Hello. I'm Dude. I can answer questions when a model is "
                "configured, but I don't have a configured AI model yet."
            )

        if "time" in lowered:
            from datetime import datetime

            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            return (
                "It's "
                + now
                + " on this device. I'm using the local deterministic fallback, "
                "not an AI model. I can answer simple questions when I have a "
                "configured AI model."
            )

        if "date" in lowered:
            from datetime import datetime

            today = datetime.now().strftime("%Y-%m-%d")
            return (
                "Today is "
                + today
                + ". I'm using the local deterministic fallback, not an AI model. "
                "I can answer simple questions when I have a configured AI model."
            )

        if any(start in lowered for start in ("who are you", "what are you")):
            return (
                "I'm Dude, a local desktop assistant. I can chat when an AI "
                "model is configured, but I do not have access to your files, "
                "shell, browser, or other apps."
            )

        if "search" in lowered or "find" in lowered:
            return (
                "I don't have access to your files, browser, or the web, so I "
                "can't search or open things. I can answer simple questions "
                "when a model is configured."
            )

        return (
            "I heard you, but I don't have a configured AI model for that yet. "
            "You can keep typing, or configure a provider when one is available."
        )


# ---------------------------------------------------------------------------
# Engine AI orchestration
# ---------------------------------------------------------------------------


class AIOrchestrator:
    """Top-level AI flow for the engine.

    Responsibilities:
    - keep the current in-memory conversation
    - talk to a configured provider through the provider interface
    - support a streaming path that yields incremental chunks
    - keep prompts/responses out of logs by default
    """

    def __init__(
        self,
        provider: BaseAIProvider,
        registry: ToolRegistry,
        *,
        system_override: Optional[str] = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.conversation = Conversation(registry)
        self._system_override = system_override

    @property
    def provider_name(self) -> str:
        return self.provider.name()

    @property
    def provider_configured(self) -> bool:
        return self.provider.configured()

    def system_instruction(self) -> str:
        if self._system_override is not None:
            return self._system_override
        return self.provider.system_instruction()

    def prepare_messages(self) -> list[dict[str, Any]]:
        messages = []
        if self._system_override is not None or self.provider.system_instruction():
            messages.append({"role": "system", "content": self.system_instruction()})
        messages.extend(self.conversation.as_messages())
        return messages

    def submit(self, user_text: str) -> AICompletion:
        """Submit a user turn synchronously and record the assistant reply.

        This is the plain request/response path. The assistant reply is added
        to the in-memory conversation here. Streaming uses ``begin_stream`` so
        the final text is recorded exactly once by the engine's stream loop.
        """
        if not user_text or not isinstance(user_text, str):
            raise AIProviderError("empty or invalid user message")

        self.conversation.add_user(user_text)
        messages = self.prepare_messages()

        try:
            result = self.provider.generate(messages)
        except AIError:
            self.conversation.drop_last_user()
            raise
        except Exception as exc:  # noqa: BLE001 - last-resort guard, never log sensitive content
            logger.exception("AI provider error")
            self.conversation.drop_last_user()
            raise AIProviderError("provider request failed") from exc

        if result.text:
            self.conversation.add_assistant(result.text)
        else:
            self.conversation.drop_last_user()
        return result

    def begin_stream(self, user_text: str) -> Iterator[str]:
        """Start a streaming response for ``user_text``.

        The user turn is recorded immediately, and the returned iterator yields
        incremental text chunks. The caller (the engine's stream loop) records
        the final assistant text once the stream completes, so exactly one
        assistant message enters the conversation per streamed interaction.

        Provider setup failures are surfaced eagerly here; a failure *after*
        chunk delivery is handled by the engine's stream loop, which can roll
        back a dangling user turn when no text was produced at all.
        """
        if not user_text or not isinstance(user_text, str):
            raise AIProviderError("empty or invalid user message")

        self.conversation.add_user(user_text)
        messages = self.prepare_messages()

        try:
            chunk_iter = self.provider.stream_chunks(messages)
        except AINotConfigured:
            self.conversation.drop_last_user()
            raise
        except AIError:
            self.conversation.drop_last_user()
            raise
        except Exception as exc:  # noqa: BLE001 - never log sensitive content
            logger.exception("AI provider error")
            self.conversation.drop_last_user()
            raise AIProviderError("provider request failed") from exc
        return chunk_iter

    def rollback_stream(self) -> None:
        """Undo a dangling user turn from a stream that never produced text."""
        self.conversation.drop_last_user()

    def cancel_current(self) -> None:
        """Best-effort cancellation signal where the provider supports it."""
        # In this first version, cancellation is modeled explicitly at the
        # provider interface. If a real provider supports StopToken, this can
        # be wired here.
        logger.debug("AI generation cancellation requested")

    def reset_conversation(self) -> None:
        self.conversation.clear()

    def tool_available(self, name: str) -> bool:
        return self.registry.has(name)

    def run_tool(self, name: str, arguments: Mapping[str, Any]) -> Any:
        return self.registry.run(name, arguments)
