# Dude — AI (Sprint 3)

Sprint 3 makes Dude conversational. It adds a typed chat interface, short-term
in-memory conversation context, a provider interface for AI models, optional
streaming, and a small allowlisted tool registry. It does **not** give Dude
general access to files, shell, browser, screen, keyboard, mouse, or other
computer control. It also does **not** build the full frontend chat UI in this
pass unless that UI is already present and verified separately.

## What Sprint 3 provides

- A typed chat interface that accepts user messages and returns responses.
- Short-term in-memory context for the current session.
- A clear “not configured” / provider selection path.
- Optional streaming response display.
- Cancel and stop generation.
- Clear or reset the current conversation.
- A small tool registry with typed schemas and strict validation.
- One harmless, read-only demonstration tool where it is safe to include one:
  current local date/time.
- A small, provider-neutral AI surface that keeps prompts, transcripts, and
  responses behind the same local IPC as the rest of the engine.
- Integration points for Sprint 2 voice so a transcript can be sent to the
  configured AI path and, when enabled, spoken back through the existing TTS
  provider.

## What Sprint 3 does not provide

- Long-term memory, persisted chat history, user profiles, or a database schema.
- File access or modification, shell execution, app launching, or computer
  control.
- Screen capture, OCR, vision, or browser automation.
- Background listening, wake words, or automatic microphone use.
- Cloud accounts, sync, telemetry, analytics, or remote IPC.
- Arbitrary model-generated code execution.
- Unauthenticated local servers or network listeners.

> **Important:** the default provider in this repository is a deterministic
> non-AI fallback. It is not an AI model. When no real provider is configured,
> any plain-language replies explicitly say so.

## Provider interface

AI is implemented behind a small provider interface in the Python engine. The
engine does not pick a model, send prompts to a cloud service, or download model
weights by default.

Default behavior:

- Until a real provider is configured, the engine uses a deterministic
  non-AI fallback that answers simple prompts safely.
- The fallback is explicitly non-AI; it is not presented as an AI model, and
  its plain-language replies say so (for example, ``"I'm using the local
  deterministic fallback, not an AI model."``).
- The deterministic fallback is intentionally small. It is not a general
  chatbot and it is not a substitute for a configured model.
- The provider interface supports local-first providers where practical.
- Cloud providers are allowed only as explicit opt-in adapters. If a cloud
  provider is used, the user must configure and enable it, and conversation
  content would leave the device. Credentials must be kept out of Git and out
  of frontend code.

Provider status is exposed to the UI through `ai_status`, which reports:

- whether a provider is configured
- the provider name
- whether streaming is supported
- the number of allowlisted tools

### Provider options considered

For this sprint, the first usable AI path is a provider interface with a
clearly labeled deterministic fallback and an opt-in real-provider adapter.
Local model providers and cloud providers are both possible in principle, but
neither is forced into the default experience:

- **Local model providers** can keep prompts local, but they usually require
  explicit installation, more disk/RAM, and enough setup complexity that they
  are not a good default for a first conversational pass. They are supported as
  a future provider shape rather than forced into the default now.
- **Cloud providers** can be practical when a user already has one configured,
  but they must be explicit opt-in because they would send conversation content
  off device. They are not enabled by default and are not required for the local
  path.
- **The deterministic fallback** is not an AI model. It exists so the engine can
  still accept chat submissions safely when nothing else is configured, and so
  the UI can be honest about what is happening.

### First configured provider stance for this repository

The default first provider for this repository is the deterministic non-AI
fallback. It is intentionally not an AI model. The provider interface is
provider-neutral and can later be pointed at a local or opt-in cloud provider
without changing the IPC contract.

For this sprint, the provider interface is ready, the deterministic fallback is
in place, and the engine can accept chat and streaming submissions safely. The
first optional real adapter is the OpenAI-compatible provider; when the user
does not enable it, the deterministic fallback stays active.

## Conversation context

The engine keeps conversation context in memory for the current session only.

- Messages are stored in memory by default.
- Nothing is persisted to disk by default.
- There is no user profile and no long-term memory in this sprint.
- The user can clear the active conversation.
- The number and size of messages sent to a provider are bounded.
- If a conversation grows beyond the limit, older context is trimmed.

## Prompts, transcripts, and responses

- Full prompts, transcripts, and generated responses are not logged by default.
- Microphone audio is never sent to an AI provider.
- Only the text transcript that the user submits may be sent to a configured
  provider path, and only if that path is disclosed as local or opted-in cloud.
- Provider responses are treated as untrusted text. They are rendered as text;
  they are not interpreted as HTML or code.

### OpenAI-compatible provider (opt-in)

The `openai` adapter ships in the engine as `provider_openai.py`. It talks the
standard chat-completions wire format and can therefore be pointed at either
the official OpenAI API or any self-hosted local server exposing the same
schema.

Setup (both settings are required to activate it):

1. Set `DUDE_AI_PROVIDER=openai`.
2. Set `OPENAI_API_KEY` (never commit it; it lives in `.env`, which is
   git-ignored, or in the real environment).

Optional: `DUDE_AI_BASE_URL` (defaults to the official OpenAI endpoint) and
`DUDE_AI_MODEL` (defaults to `gpt-4o-mini`).

Privacy behavior:

- Activation is explicit. If either variable above is missing, the fallback
  remains active and nothing leaves the device.
- When active, the current conversation context plus the submitted message
  text is sent to the configured endpoint. It leaves the device.
- Microphone audio is never sent.
- The API key is read from the environment only. It is never logged, echoed,
  or included in request payloads.
- Provider errors are surfaced with short, non-secret messages; wire details
  and HTTP status go to the log only, without the key or the response body.

How to tell which provider is active:

- Ask the engine for `ai_status`. The reported provider name is `openai` when
  the adapter is active, and `deterministic` otherwise.
- In the UI, the chat panel shows this provider name. The name `deterministic`
  always means the local non-AI fallback, not an AI model.

## Streaming and cancellation

Sprint 3 models streaming through a small correlated stream handle on the local
IPC:

- `ai_stream_start` begins a streaming request and returns a stream handle.
- `ai_stream_next` returns the next chunk for that handle.
- `ai_stream_cancel` cancels an in-flight stream.
- When a stream finishes, the engine reports the final response.

This keeps the existing request/response protocol intact. Streaming uses
correlated chunk/status events on the same local IPC path. It does not create a
new network listener.

Cancellation stops generation where the selected provider supports it.
Where a provider does not support cancellation, the limitation is documented
instead of pretending cancellation works.

## Tool support

Sprint 3 includes a controlled tool registry foundation:

- Tools are allowlisted.
- Each tool has a typed argument schema and strict validation.
- Tools are run only through the engine’s registry.
- The engine never executes arbitrary model output or arbitrary commands.
- Only safe, read-only, deterministic demonstration tools are included where
  they are appropriate.

The first version includes one safe demonstration tool:

- `get_current_time` — returns the current local date/time as a string. No side
  effects.

If no safe tool is appropriate for a future version, the registry stays present
as an interface without adding fake functionality.

## Voice-to-chat integration

Sprint 2 voice is connected to the chat flow where it is reliable:

1. The user explicitly starts listening.
2. Dude transcribes the request and presents the transcript.
3. The transcript can be reviewed before sending, where the UI supports that.
4. The request goes through the configured AI provider.
5. The response appears in the conversation.
6. If voice output is enabled, Dude may speak the AI response through the
   existing TTS provider.
7. The user can interrupt playback or cancel generation.

Microphone capture is never automatic. If voice is not stable, voice actions
surface a clear explanation while typed chat remains usable.

## Configuration and secrets

Sprint 3 adds only configuration necessary for AI providers:

- Safe defaults with no provider or key configured.
- Clear local/cloud selection and a model identifier where applicable.
- A documented local setup path for any local model.
- A secure local credential path for cloud providers if included.

`.env.example` may contain variable names and placeholder comments only.
Real keys are not stored there. `.env` and provider credential files are ignored
by Git.

## Deployment notes

- The AI layer runs inside the existing Python engine process.
- It reuses the existing versioned stdio IPC.
- It preserves health checks, shutdown behavior, and the existing voice surface.
- It does not add new Tauri capabilities for computer control.
- It does not send prompts or transcripts anywhere unless a cloud provider is
  explicitly configured and enabled.

### What is configured now

Right now the engine has no real AI provider configured. The deterministic
fallback is active by default so chat submissions can be accepted safely. To use
a real model, a provider adapter and its configuration would need to be added
behind the existing provider interface, and any cloud provider would need to be
explicitly enabled by the user.

### Changelog for this implementation pass

- The default provider remains the deterministic non-AI fallback until a real
  provider is configured.
- Streaming is implemented as a correlated handle on the existing protocol.
- The deterministic fallback is labeled as non-AI in its replies and is kept
  small on purpose.
- Conversation context is in-memory only and can be cleared.
- The tool registry includes only the safe `get_current_time` demonstration
  tool in this version.
- The frontend chat UI is not part of this pass; the engine AI surface is
  complete but the UI changes are pending separate review.

## Known limitations

- A real model provider is not configured by default. Until one is configured,
  the engine uses the deterministic non-AI fallback.
- The deterministic fallback is intentionally limited. It is a clearly labeled
  non-AI response helper, not a general chatbot.
- Streaming quality and cancellation support depend on the selected provider.
- The current streaming handle path is usable for the deterministic fallback,
  but incremental chunk behavior for a real provider depends on that provider's
  implementation.
- Local model setup may require explicit installation and hardware resources;
  large models should not be downloaded automatically at launch.
- Voice integration depends on the Sprint 2 STT/TTS setup being available.
- This sprint does not complete the frontend chat UI in this pass; the engine
  AI surface is complete but the UI side still needs its own review and wiring.
- The frontend state was not fully re-verified in this session; the engine
  changes here do not depend on a specific UI shape, but any later chat UI must
  use the same AI ops documented above.
