// Wat het raster van zoeken op beeld los van React doet (fase 6, X3; Keuze 78–81): de vraag aan
// GET /search in de stand beeld, de meldingen, het label van het raster, de rijen en de pijlen.
// Puur, zodat search/beeld/beeld.test.ts het zonder DOM toetst.

import type { MessageKey } from "../../i18n";
import { moveIndex, type Move } from "../../ui/keys";
import { hasFilter, type PageQuery } from "../query";
import { MAX_OFFSET, type SearchPage, type SearchParams } from "../types";
import type { Known } from "./status";

/** Zoveel foto's per pagina: deelbaar door 2, 3, 4, 6 en 8 kolommen (≤ 50, §3.4). */
export const PAGE = 48;

/**
 * De vraag aan GET /search voor de stand beeld. q of like, nooit allebei (de API geeft dan 400):
 * q gaat voor, zoals in het veld. Alleen foto's: type image zodra er q of een filter is, zodat
 * ook de terugval op gewoon zoeken (Keuze 9) alleen foto's geeft. "Meer zoals deze" zonder filter
 * stuurt geen type: dan geeft de terugval niets in plaats van al je foto's. Nooit after.
 */
export function imageParams(query: PageQuery, offset = 0): SearchParams {
  const { q, like, mode: _mode, type: _type, ...filters } = query;
  const out: SearchParams = { ...filters, mode: "image", limit: PAGE };
  if (q) out.q = q;
  else if (like) out.like = like;
  if (q || hasFilter(filters)) out.type = ["image"];
  if (offset > 0) out.offset = offset;
  return out;
}

/** De volgende pagina na `last`, of null (niets meer, of voorbij de grens van 1.000). */
export function nextImageParams(query: PageQuery, last: SearchPage): SearchParams | null {
  if (!last.more) return null;
  const offset = last.offset + PAGE;
  return offset > MAX_OFFSET ? null : imageParams(query, offset);
}

/** Het label van het raster: de vraag, of "lijken op" bij Meer zoals deze. */
export function gridLabel(query: PageQuery): { key: MessageKey; vars?: Record<string, string> } {
  return query.q ? { key: "beeld.grid.label", vars: { q: query.q } } : { key: "beeld.grid.labelLike" };
}

export interface Notice {
  key: MessageKey;
  vars?: Record<string, number>;
}

/** Wat het raster weet om meldingen bij te geven. */
export interface ResultState {
  /** De stand van GET /photos/beeld */
  known: Known | null;
  /** De laatste pagina, of null zolang er geen antwoord is */
  last: SearchPage | null;
  /** Zoveel foto's staan er */
  count: number;
  /** De vraag heeft een filter (Keuze 43) */
  filtered: boolean;
  /** De foutcode van het laatste antwoord: beeld_off of beeld_no_vector krijgen een eigen melding */
  code: string | null;
}

/** Naar beneden afgerond: 99,6% is nog niet klaar. */
export function pct(done: number): number {
  return Math.max(0, Math.min(99, Math.floor(done * 100)));
}

/**
 * De meldingen, in deze volgorde: uit (jij of de beheerder), deze foto nog niet bekeken, je foto's
 * worden nog bekeken, de terugval (en waarom), niets gevonden, misschien meer zonder filter.
 */
export function notices(s: ResultState): Notice[] {
  const out: Notice[] = [];
  const state = s.known?.state;
  if (state === "beheer_uit") return [{ key: "beeld.notice.offAdmin" }];
  if (state === "uit") return [{ key: "beeld.notice.offYou" }];
  if (s.code === "beeld_no_vector") out.push({ key: "beeld.notice.noVector" });
  const done = s.last?.indexing?.done ?? (state === "bezig" ? s.known?.done : null);
  if (done !== null && done !== undefined) out.push({ key: "beeld.notice.indexing", vars: { pct: pct(done) } });
  const last = s.last;
  if (!last) return out;
  if (last.fallback) out.push({ key: `beeld.notice.${last.fallback}` });
  if (s.count === 0) out.push({ key: "beeld.notice.none" });
  if (s.filtered && !last.more && s.count < PAGE) out.push({ key: "beeld.notice.few" });
  return out;
}

/** Kolommen in een rij van `width` px, cellen van minstens `min` px met `gap` ertussen. */
export function columnsFor(width: number, min: number, gap: number): number {
  return Math.max(1, Math.floor((width + gap) / (min + gap)));
}

/** De rijen van het raster (role="row"): per rij de plekken in de lijst. */
export function rows(count: number, columns: number): number[][] {
  const cols = Math.max(1, Math.floor(columns));
  const out: number[][] = [];
  for (let i = 0; i < count; i += cols) {
    const row: number[] = [];
    for (let j = i; j < Math.min(count, i + cols); j++) row.push(j);
    out.push(row);
  }
  return out;
}

/** De pijlen, Home en End in het raster (Keuze 81); PageUp en PageDown per drie rijen. */
export function moveInGrid(cur: number, m: Move, count: number, columns: number): number {
  return moveIndex(cur, m, count, columns, 3);
}

/** Of de vraag een filter heeft (voor de melding "misschien meer"). */
export function filtered(query: PageQuery): boolean {
  const { type: _type, ...rest } = query;
  return hasFilter(rest);
}
