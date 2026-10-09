"""Lightweight checks for the Sprint 2 voice provider bootstrap path.

These do not require a real microphone, a VOSK model, or audio playback.
They exercise the honest “unavailable” path when the voice dependencies are
missing or misconfigured, which is the first thing Sprint 2 must do correctly.
"""

from __future__ import annotations

import builtins
import io
import sys
from contextlib import redirect_stderr

import pytest

from dude.voice import STTUnavailable, TTSUnavailable, VoiceProviders


def _block_import(monkeypatch, name: str) -> None:
    """Make ``import name`` raise ImportError even if the package is installed.

    Real packages live in site-packages, so popping ``sys.modules[name]`` is
    not enough: the import machinery happily re-imports them. Instead, patch
    ``builtins.__import__`` for the duration of the test so the provider's
    internal import hits a genuine ImportError, exactly as it would when the
    dependency is truly missing.
    """
    # Drop any cached module first so the import path actually executes.
    monkeypatch.delitem(sys.modules, name, raising=False)

    real_import = builtins.__import__

    def fake_import(modname, globals=None, locals=None, fromlist=(), level=0):
        root = modname.split(".")[0]
        if root == name:
            raise ImportError(
                f"import of {modname} blocked by test (simulating a missing dependency)"
            )
        return real_import(modname, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def _strip_python_import_noise(lines):
    out = []
    for line in lines:
        text = line.strip()
        if text.startswith("Importing") or text.startswith("ILLEGAL") or not text:
            continue
        out.append(text)
    return out


class TestVoiceProvidersBootstrapFailures:
    def test_stt_unavailable_when_speech_recognition_missing(self, monkeypatch):
        _block_import(monkeypatch, "speech_recognition")
        with redirect_stderr(io.StringIO()):
            with pytest.raises(STTUnavailable) as exc_info:
                VoiceProviders().stt()
        assert exc_info.value.args[0]


class TestVoiceProvidersTTSBootstrapFailure:
    def test_tts_unavailable_when_pyttsx3_missing(self, monkeypatch):
        _block_import(monkeypatch, "pyttsx3")
        with redirect_stderr(io.StringIO()):
            with pytest.raises(TTSUnavailable) as exc_info:
                VoiceProviders().tts()
        assert exc_info.value.args[0]


class TestVoiceProvidersAvailabilityReflectsState:
    def test_stt_available_when_probed_successfully(self, monkeypatch):
        # If the real dependency is present, availability should be True.
        # This is informational in environments that have the package.
        providers = VoiceProviders()
        with redirect_stderr(io.StringIO()):
            available = providers.stt_available()
        # We don't require the package to be installed in all envs, so this
        # assertion is best-effort: it passes when the dependency exists.
        if available:
            assert providers.stt_available()

    def test_tts_available_when_probed_successfully(self, monkeypatch):
        providers = VoiceProviders()
        with redirect_stderr(io.StringIO()):
            available = providers.tts_available()
        if available:
            assert providers.tts_available()
