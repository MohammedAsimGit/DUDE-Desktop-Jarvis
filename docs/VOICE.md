# Dude — Voice (Sprint 2)

Sprint 2 adds **voice input and output only**. It does **not** add an AI model
and it does **not** make Dude understand requests. Until a later sprint, Dude’s
spoken acknowledgment is deterministic and explicitly non-AI.

## What Sprint 2 provides

- A **Start listening** control that opens the microphone only after you
  explicitly activate it.
- A clear **listening** state while the microphone is open.
- A **processing** state while captured audio is transcribed.
- A visible **transcript** for the current interaction only.
- A deterministic spoken acknowledgment through local text-to-speech.
- A **speaking** state while audio plays.
- Controls to **stop**, **cancel**, and **interrupt** voice activity at any
  time.
- Truthful error states for missing microphone, unavailable speech
  recognition, unavailable text-to-speech, cancellation, and shutdown.

## What Sprint 2 does not provide

- No wake word.
- No background or always-on listening.
- No speech understanding, chat, conversation history, memory, or tool use.
- No saving of recordings or transcripts by default.
- No automatic cloud speech service. A cloud provider is not enabled by default
  and is not required for the local path.

## How voice works in this project

```text
React + TypeScript UI
        │
        │ Tauri commands (voice_start, voice_stop, voice_cancel,
        │ voice_interrupt, voice_status)
        ▼
   Rust / Tauri host
        │
        │ existing versioned local IPC (newline-delimited JSON over stdio)
        ▼
   Python Dude engine
        ├── speech-to-text provider
        └── text-to-speech provider
```

- The UI never talks to the engine directly and never opens the microphone
  itself. Microphone access is requested only when the user starts listening.
- The engine owns the speech providers. The host owns the desktop lifecycle and
  clean shutdown.
- The existing health check, protocol versioning, correlation IDs, shutdown
  behavior, and no-orphan-process guarantees are preserved.
- There is no extra network listener.

## Selected providers and why

### Speech-to-text (default, local-first)

**Selected default:** a local offline recognizer using
[VOSK](https://alphacephei.com/vosk/) through
[SpeechRecognition](https://pypi.org/project/SpeechRecognition/) as a thin
convenience wrapper.

**Why:**

- It is offline and local-first, which matches the project’s privacy stance.
- It fits the existing Python engine and the existing stdio IPC.
- The provider layer is small and isolated, so it can be replaced later without
  rewriting the UI or the protocol.

**Tradeoffs and alternatives considered:**

- VOSK is a practical first usable local recognizer, but it requires installing
  the Python wrapper and a language model.
- Whisper-style local options can be stronger in accuracy, but they are heavier
  to set up for a first usable voice path, so they are deferred.
- Cloud speech backends exist inside some speech packages, but they send audio
  or transcripts off-device, so they are **not** enabled by default and are not
  the default path here.

### Text-to-speech (default, local)

**Selected default:** Windows SAPI5 through
[pyttsx3](https://pypi.org/project/pyttsx3/).

**Why:**

- It is local and offline on Windows.
- It uses the OS TTS engine already present on the system, so it does not
  require a separate model download for the default path.
- It is simple and fits the existing engine.

## Local processing and data handling

When the default local providers are installed and configured:

- Microphone audio is processed locally.
- The transcript for the current interaction is shown in the UI but is not
  saved by default.
- The spoken acknowledgment is generated locally.
- Raw audio is not written to logs.
- Transcripts are not persisted by default.

If a future cloud provider is added, it must be explicitly configured and
enabled by the user, and it must be clearly documented. That is not present in
Sprint 2.

## Required setup (Windows)

Voice features are optional. The engine works without them. Everything below
installs into the repository’s own `.venv` — nothing is installed
system-wide, and nothing is downloaded when Dude starts.

### 1. One-time setup script (recommended)

From the repository root in PowerShell:

```powershell
.\scripts\setup-voice.ps1
```

The script:

- creates `.venv` if it does not exist;
- installs the voice dependencies from `engine/requirements.txt` into
  `.venv` — `SpeechRecognition[audio,vosk]` (which pulls PyAudio for
  microphone capture and the VOSK runtime), `pyttsx3` (Windows SAPI5 TTS),
  and `tqdm` (used by the download helper);
- verifies the imports resolve in that interpreter;
- downloads the default VOSK model with the installed package’s own helper
  command (`sprc download vosk`) and verifies the recognizer can load it.

The model lands exactly where the installed SpeechRecognition looks for it:

```text
.venv\Lib\site-packages\speech_recognition\models\vosk
```

That path is inside the git-ignored `.venv`, so model files can never be
committed. Dude never guesses a project-local model directory — the setup
script asks the installed package where its model lives.

### 2. Model choice and language

The model determines which spoken language Dude can transcribe. The default
is the small **English** model `vosk-model-small-en-us-0.15`:

- download: ~40 MB;
- extracted on disk: ~50 MB;
- runtime memory: on the order of a few hundred MB once loaded.

To use another language, pick an official model from
<https://alphacephei.com/vosk/models> and pass its zip URL:

```powershell
.\scripts\setup-voice.ps1 -ModelUrl "https://alphacephei.com/vosk/models/vosk-model-small-fr-0.22.zip"
```

Bigger models are more accurate but slower and hungrier for memory; small
models are the right default for this companion. The model language must
match the language you speak to Dude.

### 3. Launch Dude with the project interpreter

The Tauri host spawns the engine with plain `python` from `PATH` unless told
otherwise. Voice dependencies live in `.venv`, so launch with:

```powershell
$env:DUDE_PYTHON = "$PWD\.venv\Scripts\python.exe"
npm run tauri dev
```

`DUDE_PYTHON` is read per terminal session only; nothing is written
machine-wide. If the engine runs under a different interpreter, voice will
report the missing dependencies honestly.

If the voice dependencies or model are missing, voice operations return a clear
“unavailable” outcome — with the specific cause — instead of failing silently
or falling back to a cloud service.

## How to use voice

1. Click **Start listening**.
2. When Dude shows the listening state, speak a short phrase.
3. Stop the recording when you are done; Dude transcribes the captured speech.
4. Dude speaks a deterministic acknowledgment such as “I heard you say …”.
5. Use **Stop**, **Cancel**, or **Interrupt** at any time if you want to end
   or abort the interaction.

The transcript is only for the current interaction. It is not stored by default.

## States

- **Idle:** no voice activity.
- **Recording:** the microphone is open after you explicitly started listening.
- **Processing:** audio has been captured and is being transcribed.
- **Speaking:** Dude is playing the spoken acknowledgment.
- **Done:** the interaction finished normally.
- **Cancelled:** the interaction was aborted without a transcript.
- **Error:** the operation failed; the error is shown so you can retry or stop.

## Interruption and cancellation

- **Stop** finishes the current recording and transcribes it.
- **Cancel** aborts the interaction without producing a transcript.
- **Interrupt** stops any active spoken output immediately.

Only one voice session is active at a time. Rapid repeated clicks are handled
safely: a new start cancels any prior in-flight work before beginning.

## Microphone permission and privacy

- The microphone is not used on launch.
- The microphone is opened only after you explicitly start listening.
- Dude shows a clear listening indicator while recording is active.
- There is no wake word and no background listening.
- Recordings are not saved by default.
- To review or change microphone permissions, use the Windows microphone/privacy
  settings for the app or the system microphone setting. The exact location
  depends on your Windows version and configuration.
- If the microphone is denied or unavailable, Dude shows that state honestly and
  does not pretend to be listening.

## Troubleshooting

Each message below is what Dude actually reports, and what to do about it.

- **“speech recognition is not installed (add the project’s voice
  dependencies)”** — `SpeechRecognition` is not in the interpreter running the
  engine. Run `\scripts\setup-voice.ps1`, and make sure
  `DUDE_PYTHON` points at `.venv\Scripts\python.exe`.
- **“microphone support (PyAudio) is not installed (run
  scripts\setup-voice.ps1)”** — the audio extra is missing. Re-run the setup
  script; it installs PyAudio into `.venv`.
- **“the VOSK runtime is not installed”** — the `vosk` package is missing from
  `.venv`. Re-run the setup script.
- **“no VOSK model is installed at …”** — dependencies are fine but no model
  has been downloaded (or it is at the wrong path). Run
  `\scripts\setup-voice.ps1`; it uses the installed package’s
  own helper so the path always matches.
- **“the VOSK model at … looks incomplete”** — a previous download was
  interrupted. Re-run the setup script; it replaces the model directory.
- **“no usable microphone was found for voice input”** — the audio stack works
  but no capture device could be opened. Check Windows sound settings and
  that a microphone is connected/enabled.
- **“Text-to-speech is not available”:** the local TTS dependency is missing or
  cannot use the system engine.
- **Microphone permission denied:** grant microphone access in Windows
  Settings → Privacy → Microphone (and allow desktop apps), then retry.
- **Empty transcript:** the recognizer did not detect usable speech within the
  bounded capture window. Try again in a quieter environment, closer to the
  microphone.
- **“a voice session is already in progress”:** recording/transcription is
  still running. Wait for it to finish, or press Cancel.
- **Interrupt not stopping speech immediately:** the local TTS backend may have
  limited interrupt support. Dude still attempts to stop playback and return to
  an idle/done state.

## Platform limitations

- The default voice path depends on Windows and the selected Python audio
  dependencies.
- A physical microphone and speaker are required to exercise the full flow.
- Microphone permission behavior depends on the Windows version and your system
  settings.
- The exact installed voices available to pyttsx3/SAPI5 depend on the Windows
  configuration.
- Cloud speech is not implemented as a default path. If a future sprint adds a
  disclosed cloud provider, it would be opt-in and documented separately.

## Security notes

- Voice does not add broad permissions. Microphone use is explicit and controlled
  by the user action that starts listening.
- The engine keeps the same local-only posture for the default providers.
- If a cloud provider is ever added, it must be explicitly configured and
  disclosed.
