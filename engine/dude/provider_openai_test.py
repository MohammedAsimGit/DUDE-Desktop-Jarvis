"""Focused checks for the opt-in OpenAI-compatible provider adapter.

No test here touches the network. They verify the selection rules, the
request shapes the adapter builds, and its error mapping — the parts that
matter for the local-first / opt-in contract.
"""

from __future__ import annotations

import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "engine"))

from dude.ai import AIProviderError
from dude.provider_openai import OpenAICompatProvider, resolve_provider_from_env


def test_default_selection_is_deterministic(monkeypatch) -> None:
    monkeypatch.delenv("DUDE_AI_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    name, provider = resolve_provider_from_env()
    assert name == "deterministic"
    assert provider is None  # engine falls back to DeterministicAIProvider


def test_unknown_provider_selection_is_deterministic(monkeypatch) -> None:
    monkeypatch.setenv("DUDE_AI_PROVIDER", "some_cloud")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake")
    name, provider = resolve_provider_from_env()
    assert name == "deterministic"
    assert provider is None


def test_openai_selection_requires_key(monkeypatch) -> None:
    monkeypatch.setenv("DUDE_AI_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    name, provider = resolve_provider_from_env()
    assert name == "deterministic"
    assert provider is None


def test_openai_selection_with_key_activates_adapter(monkeypatch) -> None:
    monkeypatch.setenv("DUDE_AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake")
    name, provider = resolve_provider_from_env()
    assert name == "openai"
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.name() == "openai"
    assert provider.configured() is True


def test_adapter_slash_protocol_construction_fails_closed() -> None:
    # An empty key must fail construction, not half-enable the adapter.
    try:
        OpenAICompatProvider("")
    except AIProviderError:
        pass  # expected
    else:  # pragma: no cover
        raise AssertionError("empty api key must be rejected")


def test_system_instruction_declares_boundaries() -> None:
    p = OpenAICompatProvider("sk-test-fake")
    instruction = p.system_instruction()
    assert "do not have access to" in instruction
    assert "computer control" in instruction


def test_request_bearer_header_carries_key_not_in_payload() -> None:
    key = "sk-test-fake"
    p = OpenAICompatProvider(key)
    headers = p._headers()
    assert headers["Authorization"] == f"Bearer {key}"
    payload = p._payload(
        [{"role": "user", "content": "hello"}], stream=False
    )
    blob = repr(payload)
    assert key not in blob  # the key never rides in the payload


def test_generate_parses_choices(monkeypatch) -> None:
    p = OpenAICompatProvider("sk-test-fake")

    def fake_post(self, payload):
        return {"choices": [{"message": {"content": "hello there"}}]}

    monkeypatch.setattr(OpenAICompatProvider, "_post", fake_post)
    result = p.generate([{"role": "user", "content": "hi"}])
    assert result.text == "hello there"


def test_generate_maps_missing_fields_to_provider_error(monkeypatch) -> None:
    p = OpenAICompatProvider("sk-test-fake")

    def fake_post(self, payload):
        return {"unexpected": "shape"}

    monkeypatch.setattr(OpenAICompatProvider, "_post", fake_post)
    try:
        p.generate([{"role": "user", "content": "hi"}])
    except AIProviderError:
        pass  # expected
    else:  # pragma: no cover
        raise AssertionError("malformed provider response must raise AIProviderError")
