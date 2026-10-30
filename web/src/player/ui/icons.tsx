// De iconen van de speler die ui/Icon.tsx niet heeft (volume, ondertitels, audiospoor, PiP,
// volledig scherm uit). Zelfde stijl: lijnen op een raster van 24 px in currentColor. Wat Icon
// wel heeft (spelen, pauze, terug, vooruit, info, instellingen) komt daarvandaan.

import { type JSX, memo } from "react";
import { Icon, type IconName } from "../../ui/Icon";

const SPEAKER = "M4 9h4l5-4v14l-5-4H4z";

const PATHS = {
  volume: [SPEAKER, "M16.5 8.5a5 5 0 0 1 0 7", "M19 6a8.5 8.5 0 0 1 0 12"],
  muted: [SPEAKER, "M17 9.5l5 5", "M22 9.5l-5 5"],
  cc: [
    "M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z",
    "M10.5 10H9a1.5 1.5 0 0 0-1.5 1.5v1A1.5 1.5 0 0 0 9 14h1.5",
    "M17 10h-1.5a1.5 1.5 0 0 0-1.5 1.5v1a1.5 1.5 0 0 0 1.5 1.5H17",
  ],
  speech: ["M5 4h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9l-5 4V6a2 2 0 0 1 1-2z", "M8 9h8", "M8 12.5h5"],
  pip: ["M3 5h18v14H3z", "M12 12h7v5h-7z"],
  unfull: ["M9 4v5H4", "M15 4v5h5", "M9 20v-5H4", "M15 20v-5h5"],
} as const;

export type PlayerIconName = keyof typeof PATHS | IconName;

/** Een icoon zonder label (de knop heeft het label). */
export const PIcon = memo(function PIcon({ name }: { name: PlayerIconName }): JSX.Element {
  const own = (PATHS as Record<string, readonly string[]>)[name];
  if (!own) return <Icon name={name as IconName} size={24} />;
  return (
    <svg
      className="icon"
      width={24}
      height={24}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      focusable="false"
    >
      {own.map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
});
