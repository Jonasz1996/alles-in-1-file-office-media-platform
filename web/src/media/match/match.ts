// Koppelen en verbeteren (fase 5, M8): de lijst Nakijken en de rekenregels van de vensters
// Koppelen (MatchDialog) en Bewerken (EditDialog). Pure functies, behalve reviewList (de route
// GET …/libraries/{id}/review, die er in M0 nog niet was: zie m8.md, M0-hotfix).

import { api } from "../../api";
import { query } from "../api";
import { LIBRARIES_PATH } from "../index";
import type { Card, Item, ItemPatch, MatchCandidate, Page } from "../types";

/** De site van TMDB (de bronvermelding, Keuze 40). */
export const TMDB_URL = "https://www.themoviedb.org/";
/** Zoveel per pagina in Nakijken. */
export const REVIEW_LIMIT = 30;

/** GET /media/libraries/{id}/review?cursor=&limit= → de lijst Nakijken (leeg als je niet mag
 * bewerken). */
export function reviewList(libraryId: string, cursor?: string | null): Promise<Page<Card>> {
  const q = query({ cursor: cursor ?? undefined, limit: REVIEW_LIMIT });
  return api.request<Page<Card>>("GET", `${LIBRARIES_PATH}/${encodeURIComponent(libraryId)}/review${q}`);
}

/** Mag je dit item koppelen (een film of aflevering, met schrijfrecht)? Een aflevering koppel je
 * via haar serie. */
export function canMatch(item: Pick<Item, "can_edit" | "kind">): boolean {
  return item.can_edit && item.kind === "movie";
}

/** Komen de gegevens van TMDB (de regel "Gegevens: TMDB")? */
export function fromTmdb(item: Pick<Item, "kind" | "match_state">): boolean {
  return (item.kind === "movie" || item.kind === "episode") && (item.match_state === "matched" || item.match_state === "manual");
}

/** Mag Ontkoppelen (er is iets van de provider)? */
export function canUnmatch(item: Pick<Item, "can_edit" | "kind" | "match_state">): boolean {
  return item.can_edit && fromTmdb(item);
}

/** "Titel (1999)" of "Titel". */
export function candidateName(c: Pick<MatchCandidate, "title" | "year">): string {
  return c.year ? `${c.title} (${c.year})` : c.title;
}

/** De oorspronkelijke titel als die anders is, anders null. */
export function originalTitle(c: Pick<MatchCandidate, "title" | "original_title">): string | null {
  const o = (c.original_title ?? "").trim();
  return o && o.toLocaleLowerCase() !== c.title.trim().toLocaleLowerCase() ? o : null;
}

/** Het jaar uit het zoekveld: leeg is geen jaar, anders 1800–2200 (zoals de API). */
export function parseYear(text: string): number | null | "bad" {
  const s = text.trim();
  if (!s) return null;
  if (!/^\d{4}$/.test(s)) return "bad";
  const n = Number(s);
  return n >= 1800 && n <= 2200 ? n : "bad";
}

// --- Bewerken ---

export type EditField = "title" | "sort_title" | "year" | "overview" | "season" | "episode";

const MOVIE_FIELDS: readonly EditField[] = ["title", "sort_title", "year", "overview"];
const EPISODE_FIELDS: readonly EditField[] = ["title", "sort_title", "season", "episode", "year", "overview"];

/** De velden van het venster voor dit soort item (seizoen en aflevering alleen bij een
 * aflevering, zoals de API). */
export function editFields(kind: Item["kind"]): readonly EditField[] {
  return kind === "episode" ? EPISODE_FIELDS : MOVIE_FIELDS;
}

export interface EditForm {
  values: Record<EditField, string>;
  locked: ReadonlySet<EditField>;
}

function text(v: string | number | null | undefined): string {
  return v === null || v === undefined ? "" : String(v);
}

/** Het formulier zoals het item nu is. */
export function formOf(item: Item): EditForm {
  const fields = new Set<string>(editFields(item.kind));
  return {
    values: {
      title: item.title,
      sort_title: text(item.sort_title),
      year: text(item.year),
      overview: text(item.overview),
      season: text(item.season),
      episode: text(item.episode),
    },
    locked: new Set((item.locked ?? []).filter((f): f is EditField => fields.has(f))),
  };
}

/** Een veld wijzigen: wat je wijzigt, zet de API vast; het vinkje gaat dus mee aan. */
export function setValue(form: EditForm, start: EditForm, field: EditField, value: string): EditForm {
  const locked = new Set(form.locked);
  if (value !== start.values[field]) locked.add(field);
  else if (!start.locked.has(field)) locked.delete(field);
  return { values: { ...form.values, [field]: value }, locked };
}

/** Het vinkje Vastzetten. Uit bij een gewijzigd veld: de wijziging gaat terug (de API zou hem
 * anders toch vastzetten). */
export function setLock(form: EditForm, start: EditForm, field: EditField, on: boolean): EditForm {
  const locked = new Set(form.locked);
  if (on) {
    locked.add(field);
    return { values: form.values, locked };
  }
  locked.delete(field);
  return { values: { ...form.values, [field]: start.values[field] }, locked };
}

export type EditProblem = "title" | "year" | "season" | "episode";

const RANGES: Record<"year" | "season" | "episode", [number, number]> = {
  year: [1800, 2200],
  season: [0, 9999],
  episode: [0, 99_999],
};

function whole(field: "year" | "season" | "episode", s: string): number | null | "bad" {
  const v = s.trim();
  if (!v) return null;
  if (!/^\d+$/.test(v)) return "bad";
  const n = Number(v);
  const [lo, hi] = RANGES[field];
  return n >= lo && n <= hi ? n : "bad";
}

/** Wat er naar PATCH gaat: de gewijzigde velden (en wat nieuw vastgezet is), en `unlock` voor wat
 * niet meer vast hoeft. Een lege patch: niets te doen. */
export function patchOf(
  kind: Item["kind"],
  start: EditForm,
  form: EditForm,
): { patch: ItemPatch; problem: null } | { patch: null; problem: EditProblem } {
  const patch: ItemPatch = {};
  const unlock: EditField[] = [];
  for (const f of editFields(kind)) {
    const changed = form.values[f] !== start.values[f];
    const lockNow = form.locked.has(f);
    const lockedBefore = start.locked.has(f);
    if (!changed && lockedBefore && !lockNow) {
      unlock.push(f);
      continue;
    }
    if (!changed && (lockedBefore || !lockNow)) continue;
    const raw = form.values[f];
    if (f === "title") {
      if (!raw.trim()) return { patch: null, problem: "title" };
      patch.title = raw.trim();
    } else if (f === "sort_title") {
      patch.sort_title = raw.trim();
    } else if (f === "overview") {
      patch.overview = raw.trim() ? raw : null;
    } else {
      const n = whole(f, raw);
      if (n === "bad") return { patch: null, problem: f };
      patch[f] = n;
    }
  }
  if (unlock.length) patch.unlock = unlock;
  return { patch, problem: null };
}

/** Is er iets te bewaren? */
export function isEmptyPatch(p: ItemPatch): boolean {
  return Object.keys(p).length === 0;
}
