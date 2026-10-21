import type { CSSProperties } from "react";

/** Kleuren om uit te kiezen; de server accepteert elke "#rrggbb" of leeg. De volgorde hoort
 * bij de tokens --tag-1..7 (tokens.css): die hebben ook een donkere variant. */
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

/** De klasse .tag-c1 … .tag-c7 voor een kleur uit het palet (werkt ook in het donkere thema),
 * of "" voor geen of een andere kleur. */
export function tagClass(color: string): string {
  const i = TAG_COLORS.findIndex((c) => c.value !== "" && c.value.toLowerCase() === color.toLowerCase());
  return i > 0 ? `tag-c${i}` : "";
}

/** Inline stijl voor een tag: alleen een geldige kleur komt in de CSS terecht. Voor een kleur
 * uit het palet heeft tagClass() de voorkeur (tagChip doet dat). */
export function tagStyle(color: string): CSSProperties | undefined {
  return /^#[0-9a-f]{6}$/i.test(color) ? ({ "--tag": color } as CSSProperties) : undefined;
}

/** className en style voor een tag: een klasse uit het palet, anders de inline kleur. */
export function tagChip(color: string): { className: string; style: CSSProperties | undefined } {
  const cls = tagClass(color);
  return { className: cls ? `tag ${cls}` : "tag", style: cls ? undefined : tagStyle(color) };
}
