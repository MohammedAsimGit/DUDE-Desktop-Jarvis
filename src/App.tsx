import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { EngineState, EngineStatus } from "./types";

const STARTING_POLL_MS = 1500;
const STARTING_POLL_MAX = 20; // ~30 s, mirrors the host's 10 s startup bound
const STATUS_POLL_MS = 5000; // aligned with the host's 5 s health monitor

/** Short status wording for the compact pill; full detail stays in `title`. */
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

/**
 * Dude's compact companion (Sprint 1).
 *
 * A small floating pill showing the assistant mark, the name Dude, and a
 * truthful engine-status line. All engine access goes through the Rust
 * host's commands — the UI never launches processes or talks to the engine
 * directly. The pill body is a native drag region; interactive controls
 * (added with the expanded panel) stay clickable and keyboard reachable.
 */
export default function App() {
  const [status, setStatus] = useState<EngineStatus>({
    state: "starting",
    detail: "Connecting to the Dude engine…",
  });

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

  // Follow the async engine startup with quick polls until it settles, and
  // keep a slow steady poll running for the whole session so the compact
  // status never goes stale.
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

  const tone = TONE[status.state];

  return (
    <main className="companion" aria-label="Dude desktop companion">
      <div className="pill" data-tauri-drag-region="deep">
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
          <span
            className={`status tone-${tone}`}
            role="status"
            aria-label={`Engine ${status.state}. ${status.detail}`}
            title={status.detail}
          >
            <span className="dot" aria-hidden="true" />
            {SHORT_LABEL[status.state]}
          </span>
        </span>
      </div>
    </main>
  );
}
