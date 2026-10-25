import type { CSSProperties } from "react";
import type { MessageKey } from "./i18n";

/** Kleuren om uit te kiezen; de server accepteert elke "#rrggbb" of leeg (en bewaart wat hij
 * krijgt). De volgorde hoort bij de tokens --tag-1..7 (tokens.css): die hebben ook een donkere
 * variant. label: een sleutel voor t() (pas bij het tonen). */
export const TAG_COLORS: readonly { value: string; label: MessageKey }[] = [
  { value: "", label: "browser.tags.colors.none" },
  { value: "#1d5fd1", label: "browser.tags.colors.blue" },
  { value: "#1f7a3a", label: "browser.tags.colors.green" },
  { value: "#c27c0e", label: "browser.tags.colors.orange" },
  { value: "#b3261e", label: "browser.tags.colors.red" },
  { value: "#7b3fb5", label: "browser.tags.colors.purple" },
  { value: "#0f7f86", label: "browser.tags.colors.teal" },
  { value: "#6b6b6b", label: "browser.tags.colors.grey" },
];

function rgb(hex: string): [number, number, number] | null {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : null;
}

/** Het nummer (1..7) van de kleur uit het palet die het dichtst bij color ligt; 0 voor geen
 * (of een ongeldige) kleur. Een kleur van buiten het palet (een oudere app, de API) krijgt zo
 * ook een kleur die in het donkere thema werkt. */
export function tagColorIndex(color: string): number {
  const want = rgb(color);
  if (!want) return 0;
  let best = 0;
  let bestDistance = Infinity;
  TAG_COLORS.forEach((c, i) => {
    const have = rgb(c.value);
    if (!have) return;
    const d = (have[0] - want[0]) ** 2 + (have[1] - want[1]) ** 2 + (have[2] - want[2]) ** 2;
    if (d < bestDistance) {
      bestDistance = d;
      best = i;
    }
  });
  return best;
}

/** De klasse .tag-c1 … .tag-c7 (zet --tag, ook in het donkere thema), of "" zonder kleur. */
export function tagClass(color: string): string {
  const i = tagColorIndex(color);
  return i > 0 ? `tag-c${i}` : "";
}

/** Compat: een stijl met --tag voor een geldige kleur (anders undefined). De app zelf gebruikt
 * tagChip/tagClass: zonder style-attribuut. */
export function tagStyle(color: string): CSSProperties | undefined {
  return /^#[0-9a-f]{6}$/i.test(color) ? ({ "--tag": color } as CSSProperties) : undefined;
}

/** className voor een tag: "tag" plus de klasse van zijn kleur. */
export function tagChip(color: string): { className: string } {
  const cls = tagClass(color);
  return { className: cls ? `tag ${cls}` : "tag" };
}
