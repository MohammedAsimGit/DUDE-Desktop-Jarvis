import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { AIStatus, EngineState, EngineStatus, VoiceState, VoiceStatus } from "./types";
import { fetchAutoStart, setAutoStart } from "./autostart";
import {
  APPEARANCE_OPTIONS,
  applyAppearance,
  loadAppearance,
  saveAppearance,
  watchSystemTheme,
  type Appearance,
} from "./theme";
import {
  cancelVoice,
  fetchVoiceStatus,
  interruptVoice,
  resetVoice,
  startVoice,
  stopVoice,
} from "./voice";
import {
  cancelStream,
  clearConversation,
  drainStream,
  fetchAIStatus,
  startStream,
} from "./chat";

const STARTING_POLL_MS = 1500;
const STARTING_POLL_MAX = 20; // ~30 s, mirrors the host's 10 s startup bound
const STATUS_POLL_MS = 5000; // aligned with the host's 5 s health monitor

/** Short status wording for the header; full detail stays in `title`. */
const SHORT_LABEL: Record<EngineState, string> = {
  starting: "Starting…",
  connected: "Connected",
  disconnected: "Offline",
};

const TONE: Record<EngineState, "ok" | "busy" | "bad"> = {
  starting: "busy",
  connected: "ok",
  disconnected: "bad",
};

function ChevronIcon({ up }: { up: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      {up ? (
        <path d="M7.41 15.41 12 10.83l4.59 4.58L18 14l-6-6-6 6z" />
      ) : (
        <path d="M7.41 8.59 12 13.17l4.59-4.58L18 10l-6 6-6-6z" />
      )}
    </svg>
  );
}

function MicIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <line x1="12" y1="19" x2="12" y2="23" stroke="currentColor" strokeWidth="2" />
      <line x1="8" y1="23" x2="16" y2="23" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

function StopIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <rect x="6" y="6" width="12" height="12" rx="2" />
    </svg>
  );
}

function XIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M18 6 6 18M6 6l12 12" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

function FullscreenIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M4 9V4h5v2H6v3H4zm11-5h5v5h-2V6h-3V4zM4 15h2v3h3v2H4v-5zm14 0h2v5h-5v-2h3v-3z" />
    </svg>
  );
}

function ExitFullscreenIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M9 4v5H4V7h3V4h2zm6 0h2v3h3v2h-5V4zM4 15h5v5H7v-3H4v-2zm11 0h5v2h-3v3h-2v-5z" />
    </svg>
  );
}

function PinIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M16 9V4h1c.55 0 1-.45 1-1s-.45 1-1 1H7c-.55 0-1 .45-1 1s.45 1 1 1h1v5c0 1.66-1.34 3-3 3v2h5.97v7l1 1 1-1v-7H19v-2c-1.66 0-3-1.34-3-3z" />
    </svg>
  );
}

function PowerIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M13 3h-2v10h2V3zm-4 15h2v2H9v-2zm8-8h-2v6h2v-6zM7 10H5c0 3.53 2.61 6.43 6 6.92V21h2v-4.08c3.39-.49 6-3.39 6-6.92h-2c0 2.76-2.24 5-5 5s-5-2.24-5-5z" />
    </svg>
  );
}

function safeError(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/**
 * The result of the most recent voice interaction, kept separately from the
 * engine's live session state.
 *
 * `kind` distinguishes a successful transcription from an honest empty
 * capture and from a failure, and either of the latter two keeps any earlier
 * recognized text visible instead of dropping it.
 */
interface VoiceOutcome {
  kind: "done" | "empty" | "error";
  transcript: string;
  error: string;
}

/**
 * Dude's companion UI (Sprint 1 + Sprint 2).
 *
 * Compact: a small floating pill showing the assistant mark, the name Dude,
 * and a truthful engine-status line. Expanded: a modest panel with the
 * Phase 0 engine status card, desktop-presence controls, and Sprint 2 voice
 * controls. All window and engine operations go through Rust commands; the
 * UI never launches processes, opens the microphone directly, or talks to
 * the engine directly. The panel body is a native drag region; buttons
 * stay clickable and keyboard reachable.
 */
export default function App() {
  const [status, setStatus] = useState<EngineStatus>({
    state: "starting",
    detail: "Connecting to the Dude engine…",
  });
  const [expanded, setExpanded] = useState(false);
  const [topmost, setTopmost] = useState(false);
  // Optional full-screen desktop layer. Off by default; entering it is always
  // an explicit user action and it is reversible from several places.
  const [fullscreen, setFullscreen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [opError, setOpError] = useState<string | null>(null);
  const [appearance, setAppearance] = useState<Appearance>(() =>
    loadAppearance(),
  );
  const [autoStart, setAutoStartState] = useState(false);
  const [autoStartBusy, setAutoStartBusy] = useState(false);

  // Voice state ----------------------------------
  const [voice, setVoice] = useState<VoiceStatus>({
    state: "idle",
    transcript: "",
    error: "",
  });
  const [voiceBusy, setVoiceBusy] = useState(false);
  // The result of the last interaction, kept independently of the engine's
  // session state so it survives the controls returning to "Start listening".
  const [lastVoice, setLastVoice] = useState<VoiceOutcome | null>(null);
  const fetchVoiceRef = useRef(fetchVoiceStatus);
  // Last raw status seen from the engine, so one interaction is recorded once
  // instead of being re-rendered on every poll.
  const observedVoiceRef = useRef<VoiceStatus | null>(null);

  // Read the host's cached status snapshot (never blocks on IPC; the host's
  // health monitor refreshes it every 5 s).
  const fetchSnapshot = useCallback(async () => {
    try {
      setStatus(await invoke<EngineStatus>("engine_status"));
    } catch {
      setStatus({
        state: "disconnected",
        detail: "Could not query the engine host. See app logs.",
      });
    }
  }, []);

  // Live ping through the host; updates the cached status too.
  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      setStatus(await invoke<EngineStatus>("engine_health"));
      setOpError(null);
    } catch {
      setStatus({
        state: "disconnected",
        detail: "Could not query the engine host. See app logs.",
      });
    } finally {
      setBusy(false);
    }
  }, []);

  // Follow the async engine startup with quick polls until it settles, and
  // keep a slow steady poll running for the whole session so the status
  // never goes stale.
  useEffect(() => {
    let cancelled = false;
    let attempts = 0;
    const startupTick = window.setInterval(async () => {
      attempts += 1;
      if (cancelled || attempts > STARTING_POLL_MAX) {
        window.clearInterval(startupTick);
        return;
      }
      await fetchSnapshot();
    }, STARTING_POLL_MS);
    const steadyTick = window.setInterval(() => {
      void fetchSnapshot();
    }, STATUS_POLL_MS);
    void fetchSnapshot();
    return () => {
      cancelled = true;
      window.clearInterval(startupTick);
      window.clearInterval(steadyTick);
    };
  }, [fetchSnapshot]);

  // Read the actual registration state once at startup (opt-in; off when
  // the host cannot answer, with the toggle surfacing any write failures).
  useEffect(() => {
    let cancelled = false;
    void fetchAutoStart().then((value) => {
      if (!cancelled && value !== null) setAutoStartState(value);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Apply + persist the appearance; while it is "system", keep following
  // OS color-scheme changes live.
  useEffect(() => {
    applyAppearance(appearance);
    saveAppearance(appearance);
    if (appearance !== "system") return;
    return watchSystemTheme(() => appearance);
  }, [appearance]);

  // Resize the native window (Rust command), then mirror the result. A
  // failed resize keeps the current state and shows the reason.
  const toggleExpanded = useCallback(async () => {
    const next = !expanded;
    try {
      await invoke("set_companion_expanded", { expanded: next });
      setExpanded(next);
      setOpError(null);
    } catch (err) {
      setOpError(
        `Could not ${next ? "expand" : "collapse"} Dude: ${safeError(err)}`,
      );
    }
  }, [expanded]);

  // Ask the host to toggle topmost; `enabled` is echoed back only after the
  // native call succeeded, so the indicator never lies.
  const toggleTopmost = useCallback(async () => {
    const next = !topmost;
    try {
      const applied = await invoke<boolean>("set_companion_topmost", {
        enabled: next,
      });
      setTopmost(applied);
      setOpError(null);
    } catch (err) {
      setOpError(`Always-on-top is unavailable: ${safeError(err)}`);
    }
  }, [topmost]);

  // Enter or leave the optional full-screen desktop layer. The host echoes
  // back the geometry it actually applied, so the indicator never lies and a
  // failed attempt leaves the current mode untouched.
  const toggleFullscreen = useCallback(async () => {
    const next = !fullscreen;
    try {
      const applied = await invoke<boolean>("set_desktop_fullscreen", {
        enabled: next,
      });
      setFullscreen(applied);
      setOpError(null);
    } catch (err) {
      setOpError(
        `Full-screen desktop mode is unavailable: ${safeError(err)}`,
      );
    }
  }, [fullscreen]);

  // Escape always leaves full-screen mode, so leaving never depends on
  // finding the exit control.
  useEffect(() => {
    if (!fullscreen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      void toggleFullscreen();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [fullscreen, toggleFullscreen]);

  // Opt-in Windows startup: enable/disable through the documented plugin
  // command; failures stay visible instead of silently reverting.
  const toggleAutoStart = useCallback(async () => {
    const next = !autoStart;
    setAutoStartBusy(true);
    try {
      await setAutoStart(next);
      setAutoStartState(next);
      setOpError(null);
    } catch (err) {
      setOpError(`Could not change startup registration: ${safeError(err)}`);
    } finally {
      setAutoStartBusy(false);
    }
  }, [autoStart]);

  const tone = TONE[status.state];

  const isVoiceActiveState = (state: VoiceState): boolean => {
    return state === "recording" || state === "processing" || state === "speaking";
  };

  const voiceLabel: Record<VoiceState, string> = {
    idle: "Voice idle",
    recording: "Listening…",
    processing: "Transcribing…",
    speaking: "Speaking…",
    done: "Done",
    cancelled: "Cancelled",
    error: "Voice error",
  };
  const voiceTone: Record<
    VoiceState,
    "ok" | "busy" | "bad" | "neutral"
  > = {
    idle: "neutral",
    recording: "busy",
    processing: "busy",
    speaking: "busy",
    done: "ok",
    cancelled: "neutral",
    error: "bad",
  };

  useEffect(() => {
    let cancelled = false;
    let interval: ReturnType<typeof setInterval> | null = null;
    const tick = async () => {
      if (cancelled) return;
      try {
        const next = await fetchVoiceRef.current();
        if (cancelled || !next) return;
        const before = observedVoiceRef.current;
        observedVoiceRef.current = next;
        setVoice(next);

        // Capture the outcome when the session reaches a terminal state. The
        // recorded result is what the UI shows; it is not derived from the
        // button state, so a successful transcription can never be mistaken
        // for "nothing happened".
        const enteredState = !before || before.state !== next.state;
        if (next.state === "done") {
          if (enteredState || before?.transcript !== next.transcript) {
            setLastVoice(
              next.transcript
                ? { kind: "done", transcript: next.transcript, error: "" }
                : { kind: "empty", transcript: "", error: "" },
            );
          }
        } else if (next.state === "error" && enteredState) {
          // An error must not silently erase a transcript already on screen.
          setLastVoice((prev) => ({
            kind: "error",
            transcript: prev?.kind === "done" ? prev.transcript : "",
            error: next.error || "Voice input failed.",
          }));
        }

        if (next.state === "idle") {
          void resetVoice();
        }
      } catch {
        if (!cancelled) {
          setVoice((v) => ({ ...v, error: "Voice status unavailable" }));
        }
      }
    };
    interval = setInterval(tick, 600);
    tick();
    return () => {
      cancelled = true;
      if (interval) clearInterval(interval);
    };
  }, []);

  const setVoiceError = useCallback((message: string | null) => {
    setOpError(message);
  }, []);

  const clearTranscript = useCallback(() => {
    setLastVoice(null);
  }, []);

  const doStart = useCallback(async () => {
    setVoiceBusy(true);
    setVoiceError(null);
    try {
      await startVoice();
      // The previous transcript is only cleared once a new interaction has
      // actually begun; a failed start leaves it on screen.
      setLastVoice(null);
    } catch (err) {
      setVoiceError(`Could not start listening: ${safeError(err)}`);
    } finally {
      setVoiceBusy(false);
    }
  }, []);

  const doStop = useCallback(async () => {
    setVoiceBusy(true);
    setVoiceError(null);
    try {
      await stopVoice();
    } catch (err) {
      setVoiceError(`Could not stop listening: ${safeError(err)}`);
    } finally {
      setVoiceBusy(false);
    }
  }, []);

  const doCancel = useCallback(async () => {
    setVoiceBusy(true);
    setVoiceError(null);
    try {
      await cancelVoice();
    } catch (err) {
      setVoiceError(`Could not cancel voice: ${safeError(err)}`);
    } finally {
      setVoiceBusy(false);
    }
  }, []);

  const doInterrupt = useCallback(async () => {
    setVoiceBusy(true);
    setVoiceError(null);
    try {
      await interruptVoice();
    } catch (err) {
      setVoiceError(`Could not interrupt speech: ${safeError(err)}`);
    } finally {
      setVoiceBusy(false);
    }
  }, []);

  // Chat state (Sprint 3) ------------------------------------
  interface ChatMessage {
    role: "user" | "assistant";
    text: string;
    /** Assistant messages still receiving chunks re-render as they fill. */
    streaming?: boolean;
  }
  const [chat, setChat] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [aiProvider, setAIProvider] = useState<AIStatus | null>(null);
  // Tracks the open engine stream so the Stop button can cancel it.
  const activeStreamRef = useRef<string | null>(null);
  const chatLogRef = useRef<HTMLDivElement | null>(null);

  // Poll provider status occasionally (cheap command, mirrors engine polls).
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      const next = await fetchAIStatus();
      if (!cancelled && next) setAIProvider(next);
    };
    void tick();
    const interval = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  // Keep the newest message visible while streaming.
  useEffect(() => {
    chatLogRef.current?.scrollTo({ top: chatLogRef.current.scrollHeight });
  }, [chat]);

  const doClearChat = useCallback(async () => {
    setChatError(null);
    try {
      await clearConversation();
      setChat([]);
    } catch (err) {
      setChatError(`Could not clear conversation: ${safeError(err)}`);
    }
  }, []);

  const doStopStream = useCallback(async () => {
    const id = activeStreamRef.current;
    if (!id) return;
    try {
      await cancelStream(id);
    } catch {
      // Engine may have finished meanwhile; harmless.
    } finally {
      activeStreamRef.current = null;
    }
  }, []);

  const doSend = useCallback(async () => {
    const text = chatInput.trim();
    if (!text || chatBusy) return;
    if (status.state !== "connected") {
      setChatError("The Dude engine is not connected yet. Try again when it is.");
      return;
    }
    setChatError(null);
    setChat((prev) => [...prev, { role: "user", text }]);
    setChatInput("");
    setChatBusy(true);
    const streamId = await startStream(text).catch((err) => {
      setChatError(`Could not start a response: ${safeError(err)}`);
      return null;
    });
    if (streamId === null) {
      setChatBusy(false);
      return;
    }
    activeStreamRef.current = streamId;
    const assistantIndex = chat.length + 1; // index of the assistant turn
    setChat((prev) => [...prev, { role: "assistant", text: "", streaming: true }]);
    try {
      const final = await drainStream(streamId, (index, chunk) => {
        void index;
        setChat((prev) => {
          const copy = [...prev];
          const msg = copy[assistantIndex];
          if (msg && msg.role === "assistant") {
            copy[assistantIndex] = { ...msg, text: msg.text + chunk };
          }
          return copy;
        });
      });
      setChat((prev) => {
        const copy = [...prev];
        const msg = copy[assistantIndex];
        if (msg && msg.role === "assistant") {
          copy[assistantIndex] = {
            role: "assistant",
            text: final || msg.text,
            streaming: false,
          };
        }
        return copy;
      });
    } catch (err) {
      setChatError(`Streaming failed: ${safeError(err)}`);
      setChat((prev) => {
        const copy = [...prev];
        const msg = copy[assistantIndex];
        if (msg && msg.role === "assistant" && !msg.text) {
          copy.splice(assistantIndex, 1); // drop the empty placeholder
        } else if (msg) {
          copy[assistantIndex] = { ...msg, streaming: false };
        }
        return copy;
      });
    } finally {
      activeStreamRef.current = null;
      setChatBusy(false);
    }
  }, [chat, chatBusy, chatInput, status.state]);

  const providerLabel = aiProvider
    ? aiProvider.provider === "deterministic"
      ? "Local fallback (not AI)"
      : aiProvider.provider === "offline"
        ? "Engine offline"
        : aiProvider.provider
    : null;

  return (
    <main
      className={[
        "companion",
        expanded ? "is-expanded" : "",
        fullscreen ? "is-fullscreen" : "",
        topmost ? "is-topmost" : "",
        voice.state === "recording" ? "is-recording" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      aria-label="Dude desktop companion"
    >
      <div
        className="panel"
        id="companion-panel"
        // The panel is the native drag region only in companion mode: while
        // the full-screen layer is up, dragging would move the layer off the
        // display it is meant to cover.
        data-tauri-drag-region={fullscreen ? undefined : "deep"}
      >
        <div className="panel-head">
          <span className="mark" aria-hidden="true">
            <svg
              viewBox="0 0 32 32"
              focusable="false"
              aria-hidden="true"
            >
              <defs>
                <linearGradient id="dudeMark" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0%" stopColor="#3b82f6" />
                  <stop offset="100%" stopColor="#1d4ed8" />
                </linearGradient>
              </defs>
              <circle cx="16" cy="16" r="15" fill="url(#dudeMark)" />
              {voice.state === "recording" && (
                <circle
                  cx="16"
                  cy="16"
                  r="18.4"
                  fill="none"
                  stroke="var(--accent)"
                  strokeWidth="1.6"
                  strokeDasharray="3 4"
                  aria-hidden="true"
                />
              )}
              <circle
                cx="16"
                cy="16"
                r="13.9"
                fill="none"
                stroke="rgba(255, 255, 255, 0.28)"
                strokeWidth="1.4"
              />
              <text
                x="16"
                y="21.4"
                textAnchor="middle"
                fontFamily="'Segoe UI Variable Text', 'Segoe UI', sans-serif"
                fontSize="16.5"
                fontWeight="700"
                fill="#ffffff"
              >
                D
              </text>
            </svg>
          </span>
          <span className="identity">
            <span className="name">Dude</span>
            {opError && !expanded ? (
              <span
                className="status tone-bad"
                role="alert"
                title={opError}
              >
                <span className="dot" aria-hidden="true" />
                <span className="status-text">{opError}</span>
              </span>
            ) : voice.state !== "idle" &&
              voice.state !== "done" &&
              voice.state !== "cancelled" ? (
              <span
                className={`status tone-${voiceTone[voice.state]}`}
                role="status"
                aria-label={`Voice ${voice.state}. ${
                  voice.error || voiceLabel[voice.state]
                }`}
                title={voice.error || voiceLabel[voice.state]}
              >
                <span className="dot" aria-hidden="true" />
                <span className="status-text">
                  {voiceLabel[voice.state]}
                </span>
              </span>
            ) : (
              <span
                className={`status tone-${tone}`}
                role="status"
                aria-label={`Engine ${status.state}. ${status.detail}`}
                title={status.detail}
              >
                <span className="dot" aria-hidden="true" />
                <span className="status-text">
                  {SHORT_LABEL[status.state]}
                </span>
              </span>
            )}
          </span>
          {fullscreen ? (
            <button
              type="button"
              className="icon-btn"
              onClick={() => void toggleFullscreen()}
              aria-label="Exit full-screen mode"
              title="Exit full-screen mode (Esc)"
            >
              <ExitFullscreenIcon />
            </button>
          ) : (
            <button
              type="button"
              className="icon-btn"
              onClick={() => void toggleExpanded()}
              aria-expanded={expanded}
              aria-controls="companion-panel"
              aria-label={expanded ? "Collapse Dude" : "Expand Dude"}
              title={expanded ? "Collapse" : "Expand"}
            >
              <ChevronIcon up={!expanded} />
            </button>
          )}
        </div>

        {(expanded || fullscreen) && (
          <div
            className="panel-body"
            id="companion-panel-body"
            role="region"
            aria-label="Dude panel content"
            tabIndex={0}
          >
            {fullscreen && (
              <button
                type="button"
                className="btn exit-fullscreen-btn"
                onClick={() => void toggleFullscreen()}
                title="Leave full-screen mode (Esc)"
              >
                <ExitFullscreenIcon />
                Exit full-screen mode
              </button>
            )}

            <section className="card" aria-label="Engine status">
              <p className="detail">
                {status.detail}
                {status.protocol_version
                  ? ` (protocol v${status.protocol_version})`
                  : ""}
              </p>
              {status.engine_version && (
                <p className="meta">engine {status.engine_version}</p>
              )}
              <button
                type="button"
                className="btn"
                onClick={() => void refresh()}
                disabled={busy}
              >
                {busy ? "Checking…" : "Refresh status"}
              </button>
            </section>

            <button
              type="button"
              className="toggle"
              aria-pressed={topmost}
              onClick={() => void toggleTopmost()}
              title="Keep Dude above other windows. Always starts off at launch."
            >
              <PinIcon />
              <span className="toggle-label">Always on top</span>
              <span className="toggle-state">
                {topmost ? "On" : "Off"}
              </span>
            </button>

            <button
              type="button"
              className="toggle"
              aria-pressed={fullscreen}
              onClick={() => void toggleFullscreen()}
              title="Cover this display with a transparent Dude layer that shows the real desktop behind it. Leave with Esc or the exit control."
            >
              <FullscreenIcon />
              <span className="toggle-label">Full-screen desktop mode</span>
              <span className="toggle-state">
                {fullscreen ? "On" : "Off"}
              </span>
            </button>
            <p className="hint">
              Shows the real desktop through a transparent layer — no
              wallpaper and no screenshot. Off by default, and it never pins
              Dude above other windows unless you turn on Always on top.
            </p>
            {fullscreen && (
              <p className="hint">
                Other windows stay visible behind the layer, but click them
                again after leaving full-screen mode. Press Esc or use Exit
                full-screen mode to leave.
              </p>
            )}

            <section className="section" aria-label="Appearance">
              <p className="section-label">Appearance</p>
              <div className="segmented">
                {APPEARANCE_OPTIONS.map((option) => (
                  <button
                    key={option}
                    type="button"
                    className="seg"
                    aria-pressed={appearance === option}
                    title={
                      option === "system"
                        ? "Follow the Windows light/dark setting"
                        : `Use the ${option} theme`
                    }
                    onClick={() => setAppearance(option)}
                  >
                    {option === "system"
                      ? "System"
                      : option === "light"
                        ? "Light"
                        : "Dark"}
                  </button>
                ))}
              </div>
            </section>

            <section className="section" aria-label="Startup">
              <button
                type="button"
                className="toggle"
                aria-pressed={autoStart}
                disabled={autoStartBusy}
                onClick={() => void toggleAutoStart()}
                title="Off by default. When on, Dude launches when you sign in to Windows."
              >
                <PowerIcon />
                <span className="toggle-label">Start with Windows</span>
                <span className="toggle-state">
                  {autoStartBusy
                    ? "…"
                    : autoStart
                      ? "On"
                      : "Off"}
                </span>
              </button>
              <p className="hint">
                Off by default. On sign-in, Windows launches Dude
                automatically — turn this off to remove it.
              </p>
            </section>              <section className="section" aria-label="Voice">
              <p className="section-label">Voice input & output</p>
              {voice.state === "idle" ||
              voice.state === "done" ||
              voice.state === "cancelled" ? (
                <button
                  type="button"
                  className={`btn voice-btn ${
                    voiceBusy ? "btn-busy" : ""
                  }`}
                  onClick={() => void doStart()}
                  disabled={
                    voiceBusy || status.state !== "connected"
                  }
                >
                  <MicIcon />
                  Start listening
                </button>
              ) : voice.state === "error" ? (
                <button
                  type="button"
                  className={`btn voice-btn ${
                    voiceBusy ? "btn-busy" : ""
                  }`}
                  onClick={() => void doStart()}
                  disabled={voiceBusy || status.state !== "connected"}
                >
                  <MicIcon />
                  Retry listening
                </button>
              ) : (
                <div className="voice-active">
                  <button
                    type="button"
                    className="voice-stop"
                    onClick={() => void doStop()}
                    disabled={voiceBusy || voice.state !== "recording"}
                    title="Finish recording and transcribe"
                  >
                    <StopIcon />
                    {voice.state === "recording"
                      ? "Stop recording"
                      : "Stop"}
                  </button>
                  <button
                    type="button"
                    className="voice-cancel"
                    onClick={() => void doCancel()}
                    disabled={voiceBusy || !isVoiceActiveState(voice.state)}
                    title="Cancel this interaction without a transcript"
                  >
                    <XIcon />
                    Cancel
                  </button>
                  {voice.state === "speaking" && (
                    <button
                      type="button"
                      className="voice-interrupt"
                      onClick={() => void doInterrupt()}
                      disabled={voiceBusy}
                      title="Stop speaking immediately"
                    >
                      Interrupt
                    </button>
                  )}
                </div>
              )}
            </section>

            {lastVoice?.kind === "error" && (
              <p className="op-error" role="alert">
                {lastVoice.error}
              </p>
            )}

            {lastVoice && lastVoice.transcript && (
              <div
                className="voice-transcript"
                aria-live="polite"
                aria-label="Transcript"
              >
                <div className="voice-transcript-head">
                  <span className="voice-transcript-label">You said:</span>
                  <button
                    type="button"
                    className="voice-transcript-clear"
                    onClick={clearTranscript}
                    title="Clear the transcript"
                  >
                    Clear
                  </button>
                </div>
                <p className="voice-transcript-text">
                  {lastVoice.transcript}
                </p>
              </div>
            )}

            {lastVoice?.kind === "empty" && (
              <p
                className="voice-transcript voice-transcript-empty"
                aria-live="polite"
              >
                <span className="voice-transcript-label">You said:</span>
                No speech was recognized — nothing was captured.
              </p>
            )}

            <section className="section chat-section" aria-label="Chat">
              <div className="chat-head">
                <p className="section-label">Chat</p>
                <div className="chat-head-right">
                  {providerLabel && (
                    <span className="chat-provider" title="The active response source. The deterministic fallback is not an AI model.">
                      {providerLabel}
                    </span>
                  )}
                  <button
                    type="button"
                    className="chat-clear"
                    onClick={() => void doClearChat()}
                    disabled={chatBusy || chat.length === 0}
                    title="Clear the in-memory conversation. Nothing is persisted."
                  >
                    Clear
                  </button>
                </div>
              </div>
              {chat.length > 0 && (
                <div
                  className="chat-log"
                  ref={chatLogRef}
                  aria-live="polite"
                  aria-label="Conversation"
                >
                  {chat.map((msg, i) => (
                    <div
                      key={i}
                      className={`chat-msg ${msg.role}`}
                    >
                      <span className="chat-role">
                        {msg.role === "user" ? "You" : "Dude"}
                      </span>
                      <span className="chat-text">
                        {msg.text || (msg.streaming ? "…" : "")}
                      </span>
                    </div>
                  ))}
                </div>
              )}
              {chatError && (
                <p className="op-error" role="alert">
                  {chatError}
                </p>
              )}
              <div className="chat-input-row">
                <input
                  type="text"
                  className="chat-input"
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                      e.preventDefault();
                      void doSend();
                    }
                  }}
                  placeholder={
                    chatBusy ? "Dude is responding…" : "Type a message…"
                  }
                  aria-label="Message text"
                  disabled={chatBusy || status.state !== "connected"}
                />
                {chatBusy ? (
                  <button
                    type="button"
                    className="chat-send chat-stop"
                    onClick={() => void doStopStream()}
                    title="Cancel the response in progress"
                  >
                    Stop
                  </button>
                ) : (
                  <button
                    type="button"
                    className="chat-send"
                    onClick={() => void doSend()}
                    disabled={!chatInput.trim() || status.state !== "connected"}
                    title="Send the message to the engine"
                  >
                    Send
                  </button>
                )}
              </div>
            </section>

            {opError && (
              <p className="op-error" role="alert">
                {opError}
              </p>
            )}
          </div>
        )}
      </div>
    </main>
  );
}
