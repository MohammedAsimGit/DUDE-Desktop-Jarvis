/**
 * Dude appearance preference (Sprint 1).
 *
 * Storage: `localStorage` key `dude.appearance` — the simplest documented
 * mechanism available to this project (no new framework, no backend, no
 * Tauri store plugin). Valid values: "light" | "dark" | "system".
 *
 * Resolution: "system" follows the Windows/OS preference via
 * `prefers-color-scheme`; the concrete result is written to
 * `<html data-theme="light|dark">`, which the stylesheet keys off.
 */

export type Appearance = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export const APPEARANCE_STORAGE_KEY = "dude.appearance";
export const APPEARANCE_OPTIONS: readonly Appearance[] = [
  "light",
  "dark",
  "system",
];

const colorSchemeQuery = () =>
  window.matchMedia("(prefers-color-scheme: dark)");

/** Read the stored preference; anything invalid or missing means "system". */
export function loadAppearance(): Appearance {
  try {
    const raw = localStorage.getItem(APPEARANCE_STORAGE_KEY);
    if (raw && (APPEARANCE_OPTIONS as readonly string[]).includes(raw)) {
      return raw as Appearance;
    }
  } catch {
    // Storage unavailable (locked-down profile): fall back to system.
  }
  return "system";
}

/** Persist the preference. Failures are non-fatal by design. */
export function saveAppearance(appearance: Appearance): void {
  try {
    localStorage.setItem(APPEARANCE_STORAGE_KEY, appearance);
  } catch {
    // Non-fatal: the theme still applies for this session.
  }
}

/** Resolve a preference against the OS color scheme. */
export function resolveAppearance(
  appearance: Appearance,
  prefersDark: boolean = colorSchemeQuery().matches,
): ResolvedTheme {
  if (appearance === "system") return prefersDark ? "dark" : "light";
  return appearance;
}

/** Apply the resolved theme to the document root. */
export function applyAppearance(appearance: Appearance): void {
  document.documentElement.dataset.theme = resolveAppearance(appearance);
}

/**
 * Re-apply "system" whenever the OS color scheme changes. Returns an
 * unsubscribe function. Only relevant while the preference is "system".
 */
export function watchSystemTheme(
  appearance: () => Appearance,
): () => void {
  const query = colorSchemeQuery();
  const onChange = () => {
    if (appearance() === "system") applyAppearance("system");
  };
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}
