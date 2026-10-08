/**
 * Typed wrappers for the opt-in startup plugin (tauri-plugin-autostart v2).
 *
 * The plugin is registered in the Rust host and gated by the narrow
 * `autostart:default` capability. Raw `invoke` keeps package.json free of
 * extra runtime wrappers — the project already depends only on
 * `@tauri-apps/api/core`.
 *
 * Off by default: nothing registers at install or launch; the OS Run key
 * (the documented supported mechanism,
 * `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`) is only written
 * when the user explicitly turns the setting on, and removed when they
 * turn it off.
 */
import { invoke } from "@tauri-apps/api/core";

/** Current registration state, or `null` if the host could not answer. */
export async function fetchAutoStart(): Promise<boolean | null> {
  try {
    return await invoke<boolean>("plugin:autostart|is_enabled");
  } catch {
    return null;
  }
}

/** Opt in/out of launch-at-sign-in. Throws a user-presentable message. */
export async function setAutoStart(enabled: boolean): Promise<void> {
  await invoke(enabled ? "plugin:autostart|enable" : "plugin:autostart|disable");
}
