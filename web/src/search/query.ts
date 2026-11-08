// De adresbalk van /zoeken (fase 6, Z4; Keuze 43, 47 en 81): wat in de URL staat, en de vraag van
// §3.4 die de pagina daarvan maakt. Los van React, zodat het te testen is (query.test.ts).
//
// In de URL staan q, de filters van Keuze 43 en de stand beeld (mode=image, met like). Geen limit,
// offset of after: die zijn van "Meer laden", niet van de vraag. Wat niet klopt (een soort die er
// niet is, zoals archive van 0.7.0, een datum die geen datum is, een id dat geen id is), laat
// readQuery weg; zo stuurt de web alleen de vraag van §3.4 (limit ≤ 50, geen archive).
//
// ?bestand= (de viewer) hoort niet bij de vraag: readQuery slaat het over, en de viewer zet het
// zelf naast de vraag (viewer/param.ts).

import {
  DEFAULT_LIMIT,
  type Filters,
  MAX_OFFSET,
  SEARCH_TYPES,
  type SearchPage,
  type SearchParams,
  type SearchType,
} from "./types";
import { wireQuery } from "./wire";

export const SEARCH_PATH = "/zoeken";

/** Wat /zoeken uit de URL leest. Zonder mode: namen en tekst; "image": wat er op de foto staat
 * (X3, Keuze 78). like (Meer zoals deze) alleen met mode image. */
export interface PageQuery extends Filters {
  q?: string;
  mode?: "image";
  like?: string;
}

/** Een filter dat als chip op de pagina staat (Keuze 47): het veld en één waarde. */
export type FilterField = keyof Filters;
export interface Chip {
  field: FilterField;
  value: string;
}

const ID = /^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$/i;
const DAY = /^(\d{4})-(\d{2})-(\d{2})$/;
const MAX_CAMERA = 200;

function isId(v: string): boolean {
  return ID.test(v);
}

/** JJJJ-MM-DD van een dag die bestaat (geen 2026-02-30). */
export function isDay(v: string): boolean {
  const m = DAY.exec(v);
  if (!m) return false;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return d.getUTCFullYear() === Number(m[1]) && d.getUTCMonth() === Number(m[2]) - 1 && d.getUTCDate() === Number(m[3]);
}

function unique(values: string[]): string[] {
  return [...new Set(values)];
}

/** Leest de vraag uit ?…; wat niet klopt, laat hij weg. */
export function readQuery(search: string): PageQuery {
  const p = new URLSearchParams(search);
  const out: PageQuery = {};
  const q = p.get("q")?.trim();
  if (q) out.q = q;
  if (p.get("mode") === "image") {
    out.mode = "image";
    const like = p.get("like");
    if (like && isId(like)) out.like = like;
  }
  const types = unique(p.getAll("type")).filter((v): v is SearchType => (SEARCH_TYPES as readonly string[]).includes(v));
  if (types.length) out.type = types;
  const from = p.get("from");
  if (from && isDay(from)) out.from = from;
  const to = p.get("to");
  if (to && isDay(to)) out.to = to;
  const persons = unique(p.getAll("person")).filter(isId);
  if (persons.length) out.person = persons;
  const place = p.get("place");
  if (place && /^\d{1,15}$/.test(place)) out.place = Number(place);
  // Een plaats of een land, niet allebei (§3.4): de plaats is preciezer
  const country = p.get("country");
  if (out.place === undefined && country && /^[a-z]{2}$/i.test(country)) out.country = country.toUpperCase();
  const tags = unique(p.getAll("tag")).filter(isId);
  if (tags.length) out.tag = tags;
  const library = p.get("library");
  if (library && isId(library)) out.library = library;
  const camera = p.get("camera")?.trim();
  if (camera && camera.length <= MAX_CAMERA) out.camera = camera;
  const folder = p.get("in");
  if (folder && isId(folder)) out.in = folder;
  return out;
}

/** De velden in een vaste volgorde: zo geeft dezelfde vraag altijd dezelfde URL. */
function ordered(query: PageQuery): Record<string, unknown> {
  return {
    q: query.q,
    mode: query.mode,
    like: query.mode === "image" ? query.like : undefined,
    type: query.type,
    from: query.from,
    to: query.to,
    person: query.person,
    place: query.place,
    country: query.place === undefined ? query.country : undefined,
    tag: query.tag,
    library: query.library,
    camera: query.camera,
    in: query.in,
  };
}

/** /zoeken?… voor deze vraag; zonder iets /zoeken. readQuery(queryHref(x)) geeft x terug. */
export function queryHref(query: PageQuery): string {
  return SEARCH_PATH + wireQuery(ordered(query));
}

/** Is er een filter gezet (Keuze 43)? */
export function hasFilter(query: Filters): boolean {
  return Boolean(
    query.type?.length ||
      query.from ||
      query.to ||
      query.person?.length ||
      query.place !== undefined ||
      query.country ||
      query.tag?.length ||
      query.library ||
      query.camera ||
      query.in,
  );
}

/** Is er iets om te zoeken? Zonder q en zonder filter geeft de API 400 search_bad_filter; in de
 * stand beeld zijn q of like nodig. */
export function canSearch(query: PageQuery): boolean {
  if (query.mode === "image") return Boolean(query.q || query.like);
  return Boolean(query.q) || hasFilter(query);
}

/** De eerste pagina van GET /search voor de stand tekst (§3.4): limit 30, zonder mode. */
export function firstParams(query: PageQuery): SearchParams {
  const { mode: _mode, like: _like, ...rest } = query;
  return { ...(ordered(rest) as SearchParams), limit: DEFAULT_LIMIT };
}

/** De volgende pagina na `last` ("Meer laden"): verder na after (ronde B van Keuze 9), anders de
 * volgende offset. null als er niets meer is, of als de offset voorbij 1.000 zou gaan (dan zegt de
 * pagina: maak je vraag preciezer). */
export function nextParams(query: PageQuery, last: SearchPage): SearchParams | null {
  if (!last.more) return null;
  const first = firstParams(query);
  if (last.after !== null) return { ...first, after: last.after };
  const offset = last.offset + (first.limit ?? DEFAULT_LIMIT);
  return offset > MAX_OFFSET ? null : { ...first, offset };
}

/** De chips van de filters, in de volgorde van de URL: één per waarde (Keuze 47). */
export function chips(query: PageQuery): Chip[] {
  const out: Chip[] = [];
  const add = (field: FilterField, value: string | number | undefined) => {
    if (value !== undefined && value !== "") out.push({ field, value: String(value) });
  };
  for (const v of query.type ?? []) add("type", v);
  add("from", query.from);
  add("to", query.to);
  for (const v of query.person ?? []) add("person", v);
  add("place", query.place);
  add("country", query.place === undefined ? query.country : undefined);
  for (const v of query.tag ?? []) add("tag", v);
  add("library", query.library);
  add("camera", query.camera);
  add("in", query.in);
  return out;
}

/** De vraag zonder deze chip (de knop × op de chip). */
export function withoutChip(query: PageQuery, chip: Chip): PageQuery {
  const out: PageQuery = { ...query };
  switch (chip.field) {
    case "type":
      out.type = query.type?.filter((v) => v !== chip.value);
      if (!out.type?.length) delete out.type;
      break;
    case "person":
      out.person = query.person?.filter((v) => v !== chip.value);
      if (!out.person?.length) delete out.person;
      break;
    case "tag":
      out.tag = query.tag?.filter((v) => v !== chip.value);
      if (!out.tag?.length) delete out.tag;
      break;
    default:
      delete out[chip.field];
  }
  return out;
}

/** De vraag met een soort erbij (meer keer is OF); al gezet: dezelfde vraag. */
export function withType(query: PageQuery, type: SearchType): PageQuery {
  if (query.type?.includes(type)) return query;
  return { ...query, type: [...(query.type ?? []), type] };
}

/** De vraag met een tag erbij (meer keer is EN). */
export function withTag(query: PageQuery, tag: string): PageQuery {
  if (!isId(tag) || query.tag?.includes(tag)) return query;
  return { ...query, tag: [...(query.tag ?? []), tag] };
}

/** De vraag met een datum (van of tot); leeg of geen datum haalt hem weg. */
export function withDay(query: PageQuery, field: "from" | "to", day: string): PageQuery {
  const out = { ...query };
  if (isDay(day)) out[field] = day;
  else delete out[field];
  return out;
}

/** Dezelfde vraag in de andere stand (de keuze van Keuze 81): q en de filters blijven, like
 * alleen in de stand beeld. */
export function withMode(query: PageQuery, mode: "text" | "image"): PageQuery {
  const { mode: _mode, like, ...rest } = query;
  return mode === "image" ? { ...rest, mode: "image", ...(like ? { like } : {}) } : rest;
}
