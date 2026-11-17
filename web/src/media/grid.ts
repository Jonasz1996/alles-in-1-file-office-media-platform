// De kaarten van media (fase 5, M2): pure hulp voor het raster van een bibliotheek
// (Library.tsx), de rijen van de start (LibraryHome.tsx) en de afleveringen van een serie
// (ShowPage.tsx). Waar een kaart heen gaat, hoe hoog een rij van het raster is, wanneer de
// volgende pagina komt en waar "Verder op" begint. Zonder React, zodat vitest het kan testen.

import { itemPath, showPath, watchPath } from "./index";
import type { Card, CardProgress, Page, ShowDetail } from "./types";

/** Zoveel kaarten per pagina (limit=, zoals de server standaard geeft). */
export const PAGE_SIZE = 60;
/** Opnieuw laden (live) vraagt zoveel als er al stond, maar nooit meer dan dit (de server: 200). */
export const RELOAD_MAX = 200;

/** Een kaart is minstens zo breed (px), met deze ruimte ertussen. */
export const CARD_MIN = 150;
export const CARD_GAP = 16;
/** De titel en het jaar onder de poster (px). */
export const CARD_TEXT = 52;
/** Een poster is 2:3 (hoogte = 1,5 × breedte). */
export const POSTER_RATIO = 1.5;
/** Zoveel rijen voor het eind van wat geladen is, vragen we de volgende pagina al. */
export const LOAD_AHEAD_ROWS = 3;
/** Pas vanaf hier (ms) is er iets om verder te kijken; daaronder is het opnieuw beginnen. */
export const RESUME_MIN_MS = 1_000;

/** Een sleutel die in een lijst uniek is: een serie op zijn id, een item op zijn node. */
export function cardKey(c: Card): string {
  if (c.kind === "show") return `s:${c.show_id ?? c.node_id ?? c.title}`;
  return `n:${c.node_id ?? c.title}`;
}

/** Waar een kaart heen gaat: een serie naar /media/serie, de rest naar /media/item. null: het
 * bestand is er niet meer (dan is de kaart geen link). */
export function cardHref(c: Card): string | null {
  if (c.kind === "show") return c.show_id ? showPath(c.show_id) : null;
  return c.node_id ? itemPath(c.node_id) : null;
}

/** De hoogte van een rij van het raster bij deze breedte van een cel (poster, tekst en ruimte). */
export function gridRowHeight(cellWidth: number): number {
  return Math.round(Math.max(0, cellWidth) * POSTER_RATIO) + CARD_TEXT + CARD_GAP;
}

/** De volgende pagina achter de vorige; een kaart die er al staat (de lijst veranderde tussen
 * twee pagina's) komt er niet nog een keer bij. */
export function mergePage(old: readonly Card[], page: Page<Card>): Card[] {
  const seen = new Set(old.map(cardKey));
  const out = [...old];
  for (const c of page.items) {
    const key = cardKey(c);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(c);
  }
  return out;
}

/** Moet de volgende pagina komen? Als er een is, er niet al een onderweg is, en de rijen in
 * beeld (tot endRow) dicht bij het eind van wat geladen is komen. */
export function wantsMore(loaded: number, endRow: number, columns: number, next: string | null, busy: boolean): boolean {
  if (!next || busy) return false;
  return (endRow + LOAD_AHEAD_ROWS) * Math.max(1, columns) >= loaded;
}

/** Hoe ver je bent, van 0 tot 1 (voor het balkje op de kaart); 0 zonder stand of duur. */
export function progressFraction(p: CardProgress | null | undefined): number {
  if (!p || !(p.duration_ms > 0) || !(p.position_ms > 0)) return 0;
  return Math.min(1, p.position_ms / p.duration_ms);
}

/** Waar "Verder op" begint, in hele seconden; null als er niets verder te kijken is (niet
 * begonnen, al gezien, of nog vooraan). */
export function resumeAt(c: Pick<Card, "progress" | "watched">): number | null {
  const p = c.progress;
  if (!p || c.watched || !(p.position_ms >= RESUME_MIN_MS)) return null;
  if (p.duration_ms > 0 && p.position_ms >= p.duration_ms) return null;
  return Math.floor(p.position_ms / 1000);
}

/** Het adres van "Verder op 12:34" (of null). */
export function resumeHref(c: Pick<Card, "node_id" | "progress" | "watched">): string | null {
  const at = resumeAt(c);
  return at !== null && c.node_id ? watchPath(c.node_id, at) : null;
}

/** "S01E02"; zonder seizoen (absolute nummering) "E12"; zonder aflevering "". */
export function episodeCode(season: number | null | undefined, episode: number | null | undefined): string {
  const two = (n: number) => String(n).padStart(2, "0");
  if (episode === null || episode === undefined) return "";
  return season === null || season === undefined ? `E${two(episode)}` : `S${two(season)}E${two(episode)}`;
}

/** Wat "Afspelen" op de pagina van een serie start: de eerste aflevering die je half zag (dan
 * resume true), anders de eerste die je nog niet zag; alles gezien: de eerste. null zonder
 * afleveringen (of als geen ervan een bestand heeft). */
export function nextEpisode(seasons: ShowDetail["seasons"]): { card: Card; resume: boolean } | null {
  const all = seasons.flatMap((s) => s.episodes).filter((c) => c.node_id);
  const half = all.find((c) => resumeAt(c) !== null);
  if (half) return { card: half, resume: true };
  const fresh = all.find((c) => !c.watched);
  if (fresh) return { card: fresh, resume: false };
  return all.length ? { card: all[0], resume: false } : null;
}
