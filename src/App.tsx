import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { EngineStatus } from "./types";

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

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      const s = await invoke<EngineStatus>("engine_status");
      setStatus(s);
    } catch (e) {
      setStatus({
        state: "disconnected",
        detail: "Could not query the engine host. See app logs.",
      });
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

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
