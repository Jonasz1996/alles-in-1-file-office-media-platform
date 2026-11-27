// De paden van de iconen die de schil niet meteen nodig heeft (frozen na P0). Een eigen chunk:
// ui/Icon.tsx laadt het bij het eerste icoon dat hier staat, of even na het opstarten. Zo hoort
// deze tabel niet bij de eerste lading (bouwontwerp §4, budget).

import type { IconName } from "./Icon";

const FOLDER = "M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v9.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z";
const PAGE = "M6 3h8l4 4v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM14 3v4h4";
const FRAME = "M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z";

export const PATHS: Partial<Record<IconName, readonly string[]>> = {
  folder: [FOLDER],
  file: [PAGE],
  image: [FRAME, "M3 16l5-5 4 4 3-3 6 6", "M15.5 9.5a1.5 1.5 0 1 0 0-.01"],
  video: ["M4 6h11a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1z", "M16 10l5-3v10l-5-3"],
  audio: ["M9 18V5l11-2v13", "M9 18a3 3 0 1 1-3-3 3 3 0 0 1 3 3z", "M20 16a3 3 0 1 1-3-3 3 3 0 0 1 3 3z"],
  pdf: [PAGE, "M8 13h8", "M8 17h5"],
  document: [PAGE, "M8 11h8", "M8 14h8", "M8 17h6"],
  archive: ["M4 4h16v4H4z", "M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8", "M10 12h4"],
  album: ["M6 3h12a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z", "M5 15l4-4 3 3 2-2 5 5"],
  upload: ["M12 16V4", "M7 9l5-5 5 5", "M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"],
  "folder-upload": [FOLDER, "M12 17v-6", "M9.5 13.5L12 11l2.5 2.5"],
  download: ["M12 4v12", "M7 11l5 5 5-5", "M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"],
  share: [
    "M18 8a3 3 0 1 0-3-3 3 3 0 0 0 3 3z",
    "M6 15a3 3 0 1 0-3-3 3 3 0 0 0 3 3z",
    "M18 22a3 3 0 1 0-3-3 3 3 0 0 0 3 3z",
    "M8.6 13.5l6.8 4",
    "M15.4 6.5l-6.8 4",
  ],
  link: ["M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1", "M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"],
  trash: ["M4 7h16", "M9 7V4h6v3", "M6 7l1 13h10l1-13", "M10 11v6", "M14 11v6"],
  restore: ["M4 12a8 8 0 1 0 2.34-5.66", "M4 4v5h5"],
  more: ["M5 12h.01", "M12 12h.01", "M19 12h.01"],
  "chevron-left": ["M15 6l-6 6 6 6"],
  "chevron-up": ["M6 15l6-6 6 6"],
  "chevron-down": ["M6 9l6 6 6-6"],
  search: ["M11 18a7 7 0 1 0-7-7 7 7 0 0 0 7 7z", "M20 20l-4-4"],
  filter: ["M4 5h16l-6 8v6l-4-2v-4z"],
  grid: ["M4 4h7v7H4z", "M13 4h7v7h-7z", "M4 13h7v7H4z", "M13 13h7v7h-7z"],
  list: ["M8 6h12", "M8 12h12", "M8 18h12", "M4 6h.01", "M4 12h.01", "M4 18h.01"],
  "sort-asc": ["M7 18V6", "M4 9l3-3 3 3", "M13 7h7", "M13 12h5", "M13 17h3"],
  "sort-desc": ["M7 6v12", "M4 15l3 3 3-3", "M13 7h3", "M13 12h5", "M13 17h7"],
  "zoom-in": ["M11 18a7 7 0 1 0-7-7 7 7 0 0 0 7 7z", "M20 20l-4-4", "M11 8v6", "M8 11h6"],
  "zoom-out": ["M11 18a7 7 0 1 0-7-7 7 7 0 0 0 7 7z", "M20 20l-4-4", "M8 11h6"],
  rotate: ["M20 12a8 8 0 1 1-2.34-5.66", "M20 4v5h-5"],
  fullscreen: ["M4 9V4h5", "M20 9V4h-5", "M4 15v5h5", "M20 15v5h-5"],
  info: ["M12 21a9 9 0 1 0-9-9 9 9 0 0 0 9 9z", "M12 11v6", "M12 7.5h.01"],
  play: ["M7 4.5v15l12-7.5z"],
  pause: ["M8 5v14", "M16 5v14"],
  settings: [
    "M12 15a3 3 0 1 0-3-3 3 3 0 0 0 3 3z",
    "M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  ],
  copy: ["M9 9h11v11H9z", "M5 15H4V4h11v1"],
  move: [FOLDER, "M9 13.5h6", "M12.5 11l2.5 2.5-2.5 2.5"],
  rename: ["M4 20h4L19 9l-4-4L4 16z", "M13.5 6.5l4 4"],
  tag: ["M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9z", "M7.5 7.5h.01"],
  history: ["M4 12a8 8 0 1 0 2.34-5.66", "M4 4v5h5", "M12 8v4l3 2"],
  "new-folder": [FOLDER, "M12 10.5v6", "M9 13.5h6"],
  photo: [
    "M4 7h3l2-3h6l2 3h3a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1z",
    "M12 17a4 4 0 1 0-4-4 4 4 0 0 0 4 4z",
  ],
};
