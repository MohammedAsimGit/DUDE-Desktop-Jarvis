"""Dude engine speech providers — local-first voice I/O (Sprint 2).

Providers are kept behind small interfaces so the transport and UI stay
independent of the selected recognizer/synthesizer. The default path is
local where practical:

- Speech-to-text default: VOSK (offline), via ``speech_recognition`` as a
  thin local convenience wrapper over a VOSK model.
- Text-to-speech default: Windows ``sapi5`` via ``pyttsx3``.

Both are optional dependencies. If either is missing or misconfigured, the
engine reports an honest "unavailable" outcome rather than falling back to
any cloud service and without downloading models at startup.

Alternatives considered and rejected for this sprint:

- Whisper-local implementations: stronger accuracy, but noticeably larger
  runtime and setup complexity for a first usable voice path. Deferred.
- SpeechRecognition cloud backends (e.g. Google Web Speech): requires a
  network transfer and is not local-first, so not enabled by default. If a
  future sprint wants a cloud option it must be explicitly configured and
  documented — never enabled by default.
- System browser/OS辅助 speech services: outside the project's local-first
  stance unless explicitly chosen and documented later.

Model and resource notes (from the provider docs as referenced in
docs/ARCHITECTURE.md and docs/DEVELOPMENT.md):

- A small VOSK model is typically around 50 MB on disk and needs on the order
  of a few hundred MB of runtime memory. The exact number depends on the
  selected model file; Dude does not ship or auto-download a model.
- pyttsx3 / sapi5 uses the OS TTS engine already present on Windows, so it
  does not require a separate model download.

Audio handling rules enforced by this module:

- Microphone is opened only after an explicit user start action.
- Captured audio is ephemeral: held in a short-lived in-memory buffer and
  discarded after recognition.
- Transcripts are not logged or persisted by default.
- Raw audio is never written to logs.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Optional voice dependency. Imported through a lazy hook rather than at
# module import time so the engine stays runnable without the voice stack,
# while methods that need ``sr`` can resolve it cleanly and report honest
# "unavailable" errors instead of unrelated NameErrors.
_sr: Optional[Any] = None
_sr_checked = False


def _get_sr() -> Any:
    """Return the ``speech_recognition`` module or raise STTUnavailable."""
    global _sr, _sr_checked
    if _sr is not None:
        return _sr
    if _sr_checked:
        raise STTUnavailable(
            "speech recognition is not installed (add the project's voice "
            "dependencies)."
        )
    _sr_checked = True
    try:
        import speech_recognition as sr  # bound here, shared below
    except ImportError as exc:
        raise STTUnavailable(
            "speech recognition is not installed (add the project's voice "
            "dependencies)."
        ) from exc
    _sr = sr
    return _sr

# ---------------------------------------------------------------------------
# Provider errors
# ---------------------------------------------------------------------------


class VoiceError(Exception):
    """A voice operation failed in a way the UI can present safely."""


class STTUnavailable(VoiceError):
    """Speech-to-text is not usable in this environment."""


class TTSUnavailable(VoiceError):
    """Text-to-speech is not usable in this environment."""


class MicrophoneUnavailable(VoiceError):
    """No usable microphone could be opened for an explicit capture request."""


# ---------------------------------------------------------------------------
# Shared provider state
# ---------------------------------------------------------------------------


class VoiceProviders:
    """Lazy, best-effort provider initialization.

    The engine does **not** import heavy audio stacks at startup.Providers are
    probed only when voice is first used, and failures are treated as
    "unavailable" rather than fatal.
    """

    def __init__(self) -> None:
        self._stt: Optional["_VoskSTT"] = None
        self._tts: Optional["_Pyttsx3TTS"] = None
        self._stt_error: Optional[str] = None
        self._tts_error: Optional[str] = None

    # -- speech-to-text -------------------------------------------------------

    def stt(self) -> "_VoskSTT":
        """Return a usable STT provider, probing once."""
        if self._stt is not None:
            return self._stt
        if self._stt_error:
            raise STTUnavailable(self._stt_error)
        try:
            self._stt = _VoskSTT()
            return self._stt
        except VoiceError as exc:
            self._stt_error = str(exc)
            raise

    def stt_available(self) -> bool:
        """Whether speech-to-text can be used right now."""
        try:
            self.stt()
            return True
        except VoiceError:
            return False

    # -- text-to-speech ------------------------------------------------------

    def tts(self) -> "_Pyttsx3TTS":
        """Return a usable TTS provider, probing once."""
        if self._tts is not None:
            return self._tts
        if self._tts_error:
            raise TTSUnavailable(self._tts_error)
        try:
            self._tts = _Pyttsx3TTS()
            return self._tts
        except VoiceError as exc:
            self._tts_error = str(exc)
            raise

    def tts_available(self) -> bool:
        """Whether text-to-speech can be used right now."""
        try:
            self.tts()
            return True
        except VoiceError:
            return False


# ---------------------------------------------------------------------------
# Speech-to-text provider interface
# ---------------------------------------------------------------------------


class BaseSTT:
    """Contract for a local speech-to-text provider.

    Implementations must not log or persist raw audio or transcripts by
    default, and must be cancelable.
    """

    def start_capture(self) -> None:
        """Prepare for recording after an explicit user action."""

    def stop_capture(self) -> str:
        """End a capture session and return the recognized text.

        May return an empty string when no speech was recognized.
        """

    def cancel(self) -> None:
        """Abort an in-progress capture without producing a transcript."""


class _VoskSTT(BaseSTT):
    """Local offline recognizer using VOSK through SpeechRecognition.

    This is the Sprint 2 default because it is offline, fits the existing
    Python engine, and keeps audio on the device. The model itself is not
    shipped by Dude and must be installed/configured separately.
    """

    # Recognition hygiene: keep intermediate context minimal and bounded.
    _MAX_CHARS = 2000  # truncate absurd transcripts before they leave the engine

    def __init__(self) -> None:
        try:
            import speech_recognition as sr  # noqa: F401 - availability probe
        except ImportError as exc:
            raise STTUnavailable(
                "speech recognition is not installed (add the project's voice "
                "dependencies)."
            ) from exc

        self._recognizer = sr.Recognizer()
        self._source: Optional[sr.Microphone] = None
        self._audio: Optional[sr.AudioData] = None

    def start_capture(self) -> None:
        sr = _get_sr()
        try:
            self._source = sr.Microphone()
        except Exception as exc:
            raise MicrophoneUnavailable(
                "no microphone is available for voice input."
            ) from exc
        try:
            with self._source:
                self._recognizer.adjust_for_ambient_noise(self._source)
        except Exception as exc:
            logger.warning("microphone ambient adjustment failed: %s", exc)

    def stop_capture(self) -> str:
        sr = _get_sr()
        if self._source is None:
            raise MicrophoneUnavailable("no microphone session is active.")

        try:
            with self._source:
                self._audio = self._recognizer.listen(self._source)
        except Exception as exc:
            logger.warning("microphone capture failed: %s", exc)
            raise MicrophoneUnavailable("microphone capture failed.") from exc

        if self._audio is None:
            return ""

        return self._recognize(self._audio)

    def cancel(self) -> None:
        self._audio = None

    # -- private -------------------------------------------------------------

    def _recognize(self, audio: Any) -> str:
        sr = _get_sr()

        # Use VOSK offline if the recognizer has a VOSK model configured.
        # Otherwise fall back to a clear "not configured" outcome rather than
        # silently attempting a cloud backend.
        try:
            text = self._recognizer.recognize_vosk(audio)
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as exc:
            raise STTUnavailable(
                "speech recognition is not configured for offline use."
            ) from exc
        except Exception as exc:
            logger.warning("VOSK recognition error: %s", exc)
            raise STTUnavailable("speech recognition failed.") from exc

        if not isinstance(text, str):
            return ""
        return text[: self._MAX_CHARS].strip()


# ---------------------------------------------------------------------------
# Text-to-speech provider interface
# ---------------------------------------------------------------------------


class BaseTTS:
    """Contract for a local text-to-speech provider."""

    def speak(self, text: str) -> None:
        """Speak the given text. Blocks until finished or interrupted."""

    def interrupt(self) -> None:
        """Stop any in-progress speech immediately."""


class _Pyttsx3TTS(BaseTTS):
    """Local Windows TTS using pyttsx3 / sapi5."""

    def __init__(self) -> None:
        try:
            import pyttsx3  # noqa: F401
        except ImportError as exc:
            raise TTSUnavailable(
                "text-to-speech is not installed (add the project's voice "
                "dependencies)."
            ) from exc

        try:
            self._engine = pyttsx3.init()
        except Exception as exc:
            raise TTSUnavailable(
                "text-to-speech could not initialize on this system."
            ) from exc
        self._running = False

    def speak(self, text: str) -> None:
        if not text:
            return
        self._running = True
        try:
            self._engine.say(text)
            self._engine.runAndWait()
        finally:
            self._running = False

    def interrupt(self) -> None:
        try:
            self._engine.stop()
        except Exception as exc:
            logger.warning("TTS interrupt failed: %s", exc)
        finally:
            self._running = False


# ---------------------------------------------------------------------------
# Engine-side voice state machine
# ---------------------------------------------------------------------------

_VOICE_IDLE = "idle"
_VOICE_RECORDING = "recording"
_VOICE_PROCESSING = "processing"
_VOICE_SPEAKING = "speaking"
_VOICE_DONE = "done"
_VOICE_CANCELLED = "cancelled"
_VOICE_ERROR = "error"


class VoiceSession:
    """A single, non-overlapping voice interaction cycle.

    Only one session is active at a time. Rapid repeated clicks are safe
    because each start cancels any prior in-flight work before beginning.
    """

    def __init__(self, providers: VoiceProviders, interrupt_timeout_s: float = 2.0) -> None:
        self.providers = providers
        self.interrupt_timeout_s = interrupt_timeout_s
        self.state = _VOICE_IDLE
        self.transcript: Optional[str] = None
        self.error: Optional[str] = None
        self._stt: Optional[BaseSTT] = None
        self._tts: Optional[BaseTTS] = None
        self._used_stt = False
        self._used_tts = False

    # -- convenience queries -------------------------------------------------

    @property
    def active(self) -> bool:
        return self.state in (_VOICE_RECORDING, _VOICE_PROCESSING, _VOICE_SPEAKING)

    # -- lifecycle -----------------------------------------------------------

    def can_start(self) -> bool:
        """Whether a new recording can be started right now."""
        return self.state == _VOICE_IDLE and self.providers.stt_available()

    def start(self) -> None:
        """Begin an explicit voice capture session.

        This is the only entry point for recording. It is not called on launch
        and not called automatically.
        """
        if self.state != _VOICE_IDLE:
            self._cancel_in_flight()
        if not self.providers.stt_available():
            self.state = _VOICE_ERROR
            self.error = "speech recognition is not available"
            return

        self.state = _VOICE_RECORDING
        self.transcript = None
        self.error = None
        try:
            self._stt = self.providers.stt()
            self._stt.start_capture()
            self._used_stt = True
        except VoiceError as exc:
            self.state = _VOICE_ERROR
            self.error = str(exc)

    def stop(self) -> None:
        """Finish recording and transcribe what was captured."""
        if self.state != _VOICE_RECORDING:
            return
        self.state = _VOICE_PROCESSING
        try:
            text = self._stt.stop_capture()
        except VoiceError as exc:
            self.state = _VOICE_ERROR
            self.error = str(exc)
            return

        self.transcript = text
        if not text:
            self.state = _VOICE_DONE
            self.transcript = ""
            return

        self.state = _VOICE_SPEAKING
        self._speak_acknowledgment(text)

    def cancel(self) -> None:
        """Abort the current interaction without producing a transcript."""
        if self.state == _VOICE_IDLE:
            return
        self._cancel_in_flight()
        self.state = _VOICE_CANCELLED
        self.transcript = None
        self.error = None

    def interrupt(self) -> None:
        """Stop any active audio playback immediately."""
        if self._tts is not None:
            try:
                self._tts.interrupt()
            except Exception as exc:
                logger.warning("voice interrupt failed: %s", exc)
        self.state = _VOICE_DONE
        self._used_tts = False

    # -- internal ------------------------------------------------------------

    def _speak_acknowledgment(self, transcript: str) -> None:
        if not self.providers.tts_available():
            self.state = _VOICE_DONE
            self.error = "text-to-speech is not available"
            return

        try:
            self._tts = self.providers.tts()
            self._used_tts = True
        except VoiceError as exc:
            self.state = _VOICE_DONE
            self.error = str(exc)
            return

        phrase = f"I heard you say {transcript}."
        try:
            self._tts.speak(phrase)
        except Exception as exc:
            logger.warning("TTS speak failed: %s", exc)
            self.state = _VOICE_DONE
            self.error = "speech playback failed"
            return

        self.state = _VOICE_DONE

    def _reset_after_done(self) -> None:
        """Best-effort cleanup once a session reaches a terminal state.

        This keeps the engine tidy between interactions without creating
        a new long-running background task.
        """
        if self.state in (_VOICE_DONE, _VOICE_CANCELLED, _VOICE_ERROR):
            self._stt = None
            self._tts = None

    # -- public helpers ---------------------------------------------------------

    def reset(self) -> None:
        """Explicitly reset the session to idle.

        This is a managed-path helper for the host's periodic voice status
        polling: it lets the UI surface return to idle after a terminal
        state once the interaction is clearly finished.
        """
        if self.state in (_VOICE_DONE, _VOICE_CANCELLED, _VOICE_ERROR):
            self.state = _VOICE_IDLE
            self.transcript = None
            self.error = None
            self._stt = None
            self._tts = None

    def _cancel_in_flight(self) -> None:
        if self._stt is not None:
            try:
                self._stt.cancel()
            except Exception as exc:
                logger.warning("voice cancel failed: %s", exc)
        self.interrupt()
        self._stt = None
        self._tts = None
