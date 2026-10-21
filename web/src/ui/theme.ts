// Het thema (licht, donker of het systeem volgen), per apparaat in localStorage. /boot.js zet
// het al vóór de eerste paint; dit wisselt het daarna zonder herladen.

import { useSyncExternalStore } from "react";

export type Theme = "light" | "dark" | "system";

export const THEME_KEY = "platform-theme";

/** Dezelfde waarden als --bg in tokens.css (voor <meta name=theme-color>). */
const THEME_COLOR = { light: "#ffffff", dark: "#121212" };

export function readTheme(): Theme {
  try {
    const v = globalThis.localStorage?.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

function systemDark(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-color-scheme: dark)").matches;
}

/** Zet data-theme en theme-color voor dit thema. */
export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  const dark = theme === "dark" || (theme === "system" && systemDark());
  let meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (!meta) {
    meta = document.createElement("meta");
    meta.name = "theme-color";
    document.head.appendChild(meta);
  }
  meta.content = dark ? THEME_COLOR.dark : THEME_COLOR.light;
}

const listeners = new Set<() => void>();
let current: Theme = readTheme();

export function setTheme(theme: Theme): void {
  try {
    if (theme === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* opslag uit: alleen voor nu */
  }
  current = theme;
  applyTheme(theme);
  for (const l of listeners) l();
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

if (typeof window !== "undefined") {
  // Het systeem wisselt (bijv. 's avonds donker): theme-color volgt
  window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", () => {
    if (current === "system") applyTheme("system");
  });
  // Een ander tabblad koos een ander thema
  window.addEventListener("storage", (e) => {
    if (e.key !== THEME_KEY) return;
    current = readTheme();
    applyTheme(current);
    for (const l of listeners) l();
  });
}

export function useTheme(): Theme {
  return useSyncExternalStore(
    subscribe,
    () => current,
    () => current,
  );
}
