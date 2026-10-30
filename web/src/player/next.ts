// Wat er na dit item komt (Keuze 32): de volgende aflevering (een kaart met een aftelling van
// 10 s bij het begin van de aftiteling, of 30 s voor het eind), en bij een film in delen (cd1,
// cd2) het volgende deel, zonder vragen. Plus de tijd in de bediening. Pure functies.

import type { Card, Item, ShowDetail } from "../media/types";

export const COUNTDOWN_S = 10;
/** Zonder hoofdstuk Aftiteling: de kaart zo lang voor het eind. */
export const CARD_BEFORE_END_S = 30;

/** Wanneer de kaart komt (s), of null (te kort). */
export function cardTime(durationS: number, creditsMs: number | null | undefined): number | null {
  if (!Number.isFinite(durationS) || durationS <= 0) return null;
  if (typeof creditsMs === "number" && creditsMs > 0 && creditsMs / 1000 < durationS - 1) return creditsMs / 1000;
  return durationS > CARD_BEFORE_END_S * 2 ? durationS - CARD_BEFORE_END_S : null;
}

/** De plaats van een seizoen, zoals de serie van M2 ze toont (items.show_detail): absolute
 * nummering (geen seizoen) eerst, dan 1, 2, …, en de specials (seizoen 0) achteraan. */
function seasonRank(season: number | null | undefined): number {
  if (season == null) return -1;
  return season === 0 ? Number.MAX_SAFE_INTEGER : season;
}

/** Alle afleveringen op volgorde: seizoen na seizoen (absolute nummering: één lijst). */
export function episodes(show: ShowDetail): Card[] {
  const seasons = [...show.seasons].sort((a, b) => seasonRank(a.season) - seasonRank(b.season));
  return seasons.flatMap((s) => [...s.episodes].sort((a, b) => (a.episode ?? 0) - (b.episode ?? 0)));
}

/** De buur in de lijst, maar nooit van een gewone aflevering naar een special (seizoen 0) of
 * terug: na de laatste aflevering van de serie komt geen special vanzelf. */
function neighbour(show: ShowDetail, nodeId: string, dir: 1 | -1): Card | null {
  const list = episodes(show).filter((c) => c.node_id);
  const i = list.findIndex((c) => c.node_id === nodeId);
  if (i < 0) return null;
  const other = list[i + dir] ?? null;
  if (!other) return null;
  return (other.season === 0) === (list[i].season === 0) ? other : null;
}

/** De aflevering na deze, of null. */
export function nextEpisode(show: ShowDetail, nodeId: string): Card | null {
  return neighbour(show, nodeId, 1);
}

/** De aflevering voor deze, of null. */
export function prevEpisode(show: ShowDetail, nodeId: string): Card | null {
  return neighbour(show, nodeId, -1);
}

/** Het volgende deel van een film in delen, of null. */
export function nextPart(item: Pick<Item, "parts">, nodeId: string): string | null {
  const parts = [...item.parts].sort((a, b) => a.part - b.part);
  const i = parts.findIndex((p) => p.node_id === nodeId);
  return i >= 0 ? (parts[i + 1]?.node_id ?? null) : null;
}

/** "12:34" of "1:45:00". */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

/** De kop "S1 · A3" bij een aflevering, of null. */
export function episodeTag(card: Pick<Card, "season" | "episode">): string | null {
  if (card.episode == null) return null;
  return card.season != null ? `S${card.season} · A${card.episode}` : `A${card.episode}`;
}
