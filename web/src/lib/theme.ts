/**
 * Client-side appearance preferences (brand theme + light/dark mode).
 *
 * The choice is persisted in localStorage and applied by toggling
 * `theme-*` / `dark` classes on <html>; the matching token overrides live
 * in index.css. This is intentionally browser-local (not saved to the
 * backend) since it is purely a display preference.
 */

export const THEMES = ["blue", "emerald", "violet", "rose", "gray"] as const
export type ThemeName = (typeof THEMES)[number]

export type ThemeMode = "light" | "dark"

const THEME_KEY = "document-gen:theme"
const MODE_KEY = "document-gen:mode"

/** Read the stored brand theme, falling back to the default (blue). */
export function getStoredTheme(): ThemeName {
  const value = localStorage.getItem(THEME_KEY)
  return (THEMES as readonly string[]).includes(value ?? "")
    ? (value as ThemeName)
    : "blue"
}

/** Read the stored mode; defaults to the OS preference when unset. */
export function getStoredMode(): ThemeMode {
  const value = localStorage.getItem(MODE_KEY)
  if (value === "light" || value === "dark") return value
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light"
}

/** Apply a brand theme + mode to the document root. */
export function applyTheme(theme: ThemeName, mode: ThemeMode): void {
  const root = document.documentElement
  for (const name of THEMES) root.classList.remove(`theme-${name}`)
  if (theme !== "blue") root.classList.add(`theme-${theme}`)
  root.classList.toggle("dark", mode === "dark")
}

/** Persist and apply a brand theme (mode is kept as-is). */
export function setTheme(theme: ThemeName): void {
  localStorage.setItem(THEME_KEY, theme)
  applyTheme(theme, getStoredMode())
}

/** Persist and apply a light/dark mode (theme is kept as-is). */
export function setMode(mode: ThemeMode): void {
  localStorage.setItem(MODE_KEY, mode)
  applyTheme(getStoredTheme(), mode)
}
