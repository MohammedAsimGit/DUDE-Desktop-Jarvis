import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { EngineStatus } from "./types";

const STARTING_POLL_MS = 1500;
const STARTING_POLL_MAX = 20; // ~30 s, mirrors the host's 10 s startup bound

/**
 * Minimal Phase 0 shell: shows Dude's name, engine connection status, and a
 * manual refresh. No futuristic interaction design yet — that is a later
 * sprint. All engine interaction goes through the Rust host's Tauri commands;
 * the UI never launches processes or talks to the engine directly.
 */
export default function App() {
  const [status, setStatus] = useState<EngineStatus>({
    state: "starting",
    detail: "Connecting to the Dude engine…",
  });
  const [busy, setBusy] = useState(false);

  // Live ping through the host; updates the cached status too.
  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      setStatus(await invoke<EngineStatus>("engine_health"));
    } catch {
      setStatus({
        state: "disconnected",
        detail: "Could not query the engine host. See app logs.",
      });
    } finally {
      setBusy(false);
    }
  }, []);

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

  // The engine starts asynchronously in the host; follow it from "starting"
  // until it connects or gives up, then stop polling (Refresh is manual live).
  useEffect(() => {
    let cancelled = false;
    let attempts = 0;
    const tick = window.setInterval(async () => {
      attempts += 1;
      if (cancelled || attempts > STARTING_POLL_MAX) {
        window.clearInterval(tick);
        return;
      }
      await fetchSnapshot();
      setStatus((current) => {
        if (current.state !== "starting") window.clearInterval(tick);
        return current;
      });
    }, STARTING_POLL_MS);
    void fetchSnapshot();
    return () => {
      cancelled = true;
      window.clearInterval(tick);
    };
  }, [fetchSnapshot]);

  const stateColor =
    status.state === "connected"
      ? "ok"
      : status.state === "starting"
        ? "busy"
        : "bad";

  return (
    <main className="shell">
      <h1>Dude</h1>
      <p className="tagline">Your desktop assistant — foundation build.</p>

      <section className={`status-card status-${stateColor}`}>
        <div className="status-head">
          <span className={`dot dot-${stateColor}`} aria-hidden="true" />
          <span className="state-label">
            Engine {status.state}
            {status.protocol_version
              ? ` · protocol v${status.protocol_version}`
              : ""}
          </span>
        </div>
        <p className="detail">{status.detail}</p>
        {status.engine_version && (
          <p className="meta">engine {status.engine_version}</p>
        )}
        <button onClick={() => void refresh()} disabled={busy}>
          {busy ? "Checking…" : "Refresh status"}
        </button>
      </section>
    </main>
  );
}
