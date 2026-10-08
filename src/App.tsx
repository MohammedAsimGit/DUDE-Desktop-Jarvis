import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { EngineState, EngineStatus } from "./types";
import { fetchAutoStart, setAutoStart } from "./autostart";
import {
  APPEARANCE_OPTIONS,
  applyAppearance,
  loadAppearance,
  saveAppearance,
  watchSystemTheme,
  type Appearance,
} from "./theme";

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

function PinIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M16 9V4h1c.55 0 1-.45 1-1s-.45-1-1-1H7c-.55 0-1 .45-1 1s.45 1 1 1h1v5c0 1.66-1.34 3-3 3v2h5.97v7l1 1 1-1v-7H19v-2c-1.66 0-3-1.34-3-3z" />
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
 * Dude's companion UI (Sprint 1).
 *
 * Compact: a small floating pill showing the assistant mark, the name Dude,
 * and a truthful engine-status line. Expanded: a modest panel with the
 * Phase 0 engine status card and desktop-presence controls (always-on-top).
 * All window operations go through Rust commands; all engine access goes
 * through the host's commands — the UI never launches processes or talks to
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
  const [busy, setBusy] = useState(false);
  const [opError, setOpError] = useState<string | null>(null);
  const [appearance, setAppearance] = useState<Appearance>(() =>
    loadAppearance(),
  );
  const [autoStart, setAutoStartState] = useState(false);
  const [autoStartBusy, setAutoStartBusy] = useState(false);

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

  return (
    <main
      className={[
        "companion",
        expanded ? "is-expanded" : "",
        topmost ? "is-topmost" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      aria-label="Dude desktop companion"
    >
      <div className="panel" id="companion-panel" data-tauri-drag-region="deep">
        <div className="panel-head">
          <span className="mark" aria-hidden="true">
            <svg viewBox="0 0 32 32" focusable="false" aria-hidden="true">
              <defs>
                <linearGradient id="dudeMark" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0%" stopColor="#3b82f6" />
                  <stop offset="100%" stopColor="#1d4ed8" />
                </linearGradient>
              </defs>
              <circle cx="16" cy="16" r="15" fill="url(#dudeMark)" />
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
              // A failed expand leaves us in the compact state, so the
              // error takes the status line's slot — it must stay visible.
              <span className="status tone-bad" role="alert" title={opError}>
                <span className="dot" aria-hidden="true" />
                <span className="status-text">{opError}</span>
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
        </div>

        {expanded && (
          <div className="panel-body">
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
              <span className="toggle-state">{topmost ? "On" : "Off"}</span>
            </button>

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
                  {autoStartBusy ? "…" : autoStart ? "On" : "Off"}
                </span>
              </button>
              <p className="hint">
                Off by default. On sign-in, Windows launches Dude
                automatically — turn this off to remove it.
              </p>
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
