import type { CSSProperties } from "react";

/** Kleuren om uit te kiezen; de server accepteert elke "#rrggbb" of leeg. */
export const TAG_COLORS = [
  { value: "", label: "Geen kleur" },
  { value: "#1d5fd1", label: "Blauw" },
  { value: "#1f7a3a", label: "Groen" },
  { value: "#c27c0e", label: "Oranje" },
  { value: "#b3261e", label: "Rood" },
  { value: "#7b3fb5", label: "Paars" },
  { value: "#0f7f86", label: "Turquoise" },
  { value: "#6b6b6b", label: "Grijs" },
];

/** Inline stijl voor een tag: alleen een geldige kleur komt in de CSS terecht. */
export function tagStyle(color: string): CSSProperties | undefined {
  return /^#[0-9a-f]{6}$/i.test(color) ? ({ "--tag": color } as CSSProperties) : undefined;
}
