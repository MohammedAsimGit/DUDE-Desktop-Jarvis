"""Opt-in OpenAI-compatible chat provider adapter (Sprint 3).

This adapter is **explicitly opt-in**: the engine only uses it when the user
configures and enables it. With nothing configured the engine keeps using the
deterministic non-AI fallback, which sends nothing anywhere.

Activation (both required):

1. ``DUDE_AI_PROVIDER=openai`` in the engine environment (or the local,
   git-ignored ``.env`` file).
2. ``OPENAI_API_KEY`` set in the same environment.

Data disclosure (the important part):

- When this adapter is active, **conversation content leaves the device**: the
  in-session messages (system instruction + bounded conversation context plus
  the submitted text) are sent to the OpenAI-compatible chat-completions
  endpoint at the configured base URL. This is a network transfer to a third
  party and does not happen unless both settings above are present.
- Microphone audio is never sent. Only the text that the user (or the explicit
  voice flow) submits can reach the provider, and only through the same
  in-memory conversation context described in docs/AI.md.
- The API key is read from the environment only. It is never written to logs,
  never echoed to the UI, and never committed. ``.env`` is git-ignored.

Implementation notes:

- Uses ``urllib.request`` from the standard library — no new third-party
  dependency is required to send chat text when the user opts in.
- Bounded: per-request timeout, bounded response size, and bounded message
  history (determined by the orchestrator's conversation limits).
- Errors surface as ``AIProviderError`` with short, non-secret messages;
  wire/host details go to the log only if they are non-sensitive.
- Streaming uses the Chat Completions SSE protocol (``stream=True``), parsed
  incrementally into text chunks for the engine's streaming handle.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Iterator, Optional

from .ai import AICompletion, AIProviderError, BaseAIProvider

logger = logging.getLogger(__name__)

# Bounded request behavior.
_DEFAULT_TIMEOUT_S = 30.0
_MAX_RESPONSE_BYTES = 512 * 1024  # refuse absurd responses
_DEFAULT_MODEL = "gpt-4o-mini"

_OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAICompatProvider(BaseAIProvider):
    """OpenAI-compatible chat provider behind the BaseAIProvider interface.

    Compatible with the official OpenAI API and with any service that speaks
    the same chat-completions wire protocol (e.g. a self-hosted local server
    exposing the same schema), which is what makes this adapter useful for a
    local-first setup too: point ``DUDE_AI_BASE_URL`` at a local endpoint and
    the same adapter works without any cloud call.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        if not api_key or not isinstance(api_key, str):
            raise AIProviderError("provider is enabled but no API key is configured")
        self._api_key = api_key
        self._base_url = (base_url or _OPENAI_DEFAULT_BASE_URL).rstrip("/")
        self._model = model or _DEFAULT_MODEL
        self._timeout_s = timeout_s

    # -- metadata ------------------------------------------------------------

    def name(self) -> str:
        return "openai"

    def configured(self) -> bool:
        return True

    def system_instruction(self) -> str:
        return (
            "You are Dude, a local desktop assistant. You do not have access to "
            "files, shell, browser, screen, keyboard, mouse, or any other "
            "computer control. Do not pretend to run commands or access the "
            "system. If you are unsure, say so plainly. Answer in plain text "
            "only; do not produce HTML or code unless explicitly asked."
        )

    # -- request building ----------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _endpoint(self) -> str:
        return f"{self._base_url}/chat/completions"

    def _payload(self, messages: list[dict[str, Any]], *, stream: bool) -> dict[str, Any]:
        return {
            "model": self._model,
            "messages": messages,
            "stream": stream,
        }

    # -- HTTP plumbing ---------------------------------------------------------

    def _post(self, payload: dict[str, Any]) -> Any:
        """POST the request and return the parsed JSON body (bounded)."""
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint(),
            data=body,
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                raw = resp.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            # Never include the response body or the key in the raised message.
            detail = f"HTTP {exc.code}"
            raise AIProviderError(
                f"provider request failed ({detail}); check configuration and key"
            ) from None
        except urllib.error.URLError as exc:
            raise AIProviderError(
                "could not reach the provider endpoint; check network and base URL"
            ) from exc
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise AIProviderError("provider response was too large")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AIProviderError("provider returned a malformed response") from exc

    # -- provider interface ----------------------------------------------------

    def generate(self, messages: list[dict[str, Any]]) -> AICompletion:
        payload = self._payload(messages, stream=False)
        data = self._post(payload)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("provider response was missing expected fields") from exc
        if not isinstance(text, str):
            raise AIProviderError("provider response had an unexpected shape")
        return AICompletion(text)

    def stream_chunks(self, messages: list[dict[str, Any]]) -> Iterator[str]:
        """Yield incremental chunks from a Chat Completions SSE stream."""
        payload = self._payload(messages, stream=True)
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint(),
            data=body,
            headers={**self._headers(), "Accept": "text/event-stream"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[len("data: "):]
                    if data_str == "[DONE]":
                        return
                    try:
                        chunk_obj = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    try:
                        delta = chunk_obj["choices"][0]["delta"]
                    except (KeyError, IndexError, TypeError):
                        continue
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        yield text
        except urllib.error.HTTPError as exc:
            raise AIProviderError(
                f"provider request failed (HTTP {exc.code}); check configuration and key"
            ) from None
        except urllib.error.URLError as exc:
            raise AIProviderError(
                "could not reach the provider endpoint; check network and base URL"
            ) from exc


# ---------------------------------------------------------------------------
# Provider selection from the environment
# ---------------------------------------------------------------------------


def resolve_provider_from_env() -> tuple[str, Optional[BaseAIProvider]]:
    """Resolve the active provider from the environment.

    Returns ``(provider_name, provider_or_None)``. The deterministic fallback
    reports name ``"deterministic"``; a cloud adapter reports its own name and
    is returned only when the user explicitly configured and enabled it.

    Selection rules:

    - ``DUDE_AI_PROVIDER`` unset or not a supported value -> the deterministic
      non-AI fallback (name ``"deterministic"``). Nothing is sent anywhere.
    - ``DUDE_AI_PROVIDER=openai`` **with** ``OPENAI_API_KEY`` set -> the
      OpenAI-compatible adapter. Conversation content leaves the device.
    - ``DUDE_AI_PROVIDER=openai`` **without** a key -> the deterministic
      fallback stays active. The misconfiguration is logged once (without the
      key value or any reason detail beyond "key missing") and surfaced to the
      UI through the provider name, which stays truthful.
    """
    raw = os.environ.get("DUDE_AI_PROVIDER", "").strip().lower()
    if raw != "openai":
        return "deterministic", None

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        # Honest failure: log once without any secret material, keep the safe
        # local fallback active.
        logger.warning(
            "DUDE_AI_PROVIDER=openai is set but OPENAI_API_KEY is missing; "
            "using the deterministic non-AI fallback"
        )
        return "deterministic", None

    base_url = os.environ.get("DUDE_AI_BASE_URL", "").strip() or None
    model = os.environ.get("DUDE_AI_MODEL", "").strip() or None
    try:
        provider: BaseAIProvider = OpenAICompatProvider(
            api_key,
            base_url=base_url,
            model=model,
        )
    except AIProviderError:
        logger.warning(
            "OpenAI-compatible provider could not initialize; "
            "using the deterministic non-AI fallback"
        )
        return "deterministic", None
    return "openai", provider
