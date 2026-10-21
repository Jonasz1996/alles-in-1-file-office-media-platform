// Eigen iconen: eenvoudige lijntekeningen op een raster van 24 px, in de tekstkleur
// (currentColor), zodat ze in beide thema's werken. Geen iconenbibliotheek.

import { type JSX, useEffect, useSyncExternalStore } from "react";

/** Alle namen (voor tests en een overzicht). */
export const ICON_NAMES = [
  "folder",
  "file",
  "image",
  "video",
  "audio",
  "pdf",
  "document",
  "archive",
  "album",
  "upload",
  "folder-upload",
  "download",
  "share",
  "link",
  "trash",
  "restore",
  "more",
  "close",
  "check",
  "chevron-left",
  "chevron-right",
  "chevron-up",
  "chevron-down",
  "search",
  "filter",
  "grid",
  "list",
  "sort-asc",
  "sort-desc",
  "zoom-in",
  "zoom-out",
  "rotate",
  "fullscreen",
  "info",
  "play",
  "pause",
  "settings",
  "user",
  "logout",
  "sun",
  "moon",
  "language",
  "keyboard",
  "warning",
  "error",
  "copy",
  "move",
  "rename",
  "tag",
  "history",
  "new-folder",
  "photo",
] as const;

export type IconName = (typeof ICON_NAMES)[number];

/** De iconen van de schil (balk bovenaan, accountmenu, dialogen, meldingen, foutpagina's): die
 * staan er meteen. De rest staat in iconPaths.ts, een eigen chunk. */
export const SHELL_PATHS: Partial<Record<IconName, readonly string[]>> = {
  close: ["M6 6l12 12", "M18 6L6 18"],
  check: ["M5 12.5l4.5 4.5L19 7"],
  "chevron-right": ["M9 6l6 6-6 6"],
  user: ["M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4z", "M4 21a8 8 0 0 1 16 0"],
  logout: ["M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4", "M10 17l-5-5 5-5", "M5 12h11"],
  sun: [
    "M12 16a4 4 0 1 0-4-4 4 4 0 0 0 4 4z",
    "M12 2v2",
    "M12 20v2",
    "M4.9 4.9l1.4 1.4",
    "M17.7 17.7l1.4 1.4",
    "M2 12h2",
    "M20 12h2",
    "M4.9 19.1l1.4-1.4",
    "M17.7 6.3l1.4-1.4",
  ],
  moon: ["M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"],
  language: ["M4 5h9", "M8.5 3v2", "M6 5c1 4 4 7 7 8", "M11 5c-1 4-4 7-7 8", "M13 21l4-9 4 9", "M14.5 18h5"],
  keyboard: [
    "M3 7h18a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1z",
    "M6 10h.01",
    "M10 10h.01",
    "M14 10h.01",
    "M18 10h.01",
    "M7 14h10",
  ],
  warning: ["M12 3l10 18H2z", "M12 10v4", "M12 17.5h.01"],
  error: ["M12 21a9 9 0 1 0-9-9 9 9 0 0 0 9 9z", "M15 9l-6 6", "M9 9l6 6"],
};

type Paths = Partial<Record<IconName, readonly string[]>>;
let more: Paths | null = null;
let loading: Promise<void> | null = null;
const waiting = new Set<() => void>();

/** Laadt de andere iconen (één keer; lukt het niet, dan bij het volgende icoon opnieuw). */
export function loadIcons(): Promise<void> {
  loading ??= import("./iconPaths").then(
    (m) => {
      more = m.PATHS;
      for (const l of waiting) l();
    },
    () => {
      loading = null;
    },
  );
  return loading;
}

function subscribe(cb: () => void): () => void {
  waiting.add(cb);
  return () => {
    waiting.delete(cb);
  };
}

if (typeof window !== "undefined") {
  // Even na het opstarten alvast: dan staan ze er als een pagina ze nodig heeft
  window.setTimeout(() => void loadIcons(), 1500);
}

/** Een icoon. Zonder label is het versiering (aria-hidden); met label leest een schermlezer het.
 * Een icoon buiten de schil is één keer even leeg (zelfde maat) tot zijn chunk er is. */
export function Icon({ name, size = 20, label }: { name: IconName; size?: 16 | 20 | 24 | 32; label?: string }): JSX.Element {
  const loaded = useSyncExternalStore(subscribe, () => more, () => more);
  const paths = SHELL_PATHS[name] ?? loaded?.[name];
  useEffect(() => {
    if (!paths) void loadIcons();
  }, [paths]);
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      {(paths ?? []).map((d, i) => (
        <path key={i} d={d} />
      ))}
    </svg>
  );
}
