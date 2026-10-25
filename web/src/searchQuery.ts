// Zoeken in de adresbalk (/zoeken?q=...&type=...&tag=...), zodat terug en vooruit werken
// en je een zoekopdracht kunt bewaren. Losse functies, zonder React, zodat ze te testen zijn.

import { type Category, type SearchHit, type SearchQuery, searchParams } from "./api";
import { type MessageKey, t } from "./i18n";
import { isShared } from "./sharing";

/** Compat: de Nederlandse namen. De app zelf gebruikt categoryLabel() (vertaald). */
export const CATEGORY_LABELS: Record<Category, string> = {
  folder: "Mappen",
  image: "Afbeeldingen",
  video: "Video",
  audio: "Audio",
  pdf: "PDF",
  document: "Documenten",
  archive: "Archieven",
  other: "Overig",
};

export const CATEGORIES = Object.keys(CATEGORY_LABELS) as Category[];

/** De naam van een soort in de taal van de app. */
export function categoryLabel(c: Category): string {
  return t(`browser.categories.${c}` as MessageKey);
}
export const MAX_LIMIT = 200;

/** Leest ?q=&type=&tag=&in=&limit= uit de adresbalk; onzin laat hij weg. */
export function readSearch(search: string): SearchQuery {
  const params = new URLSearchParams(search);
  const query: SearchQuery = {};
  const q = params.get("q")?.trim();
  if (q) query.q = q;
  const type = params.get("type");
  if (type && (CATEGORIES as string[]).includes(type)) query.type = type as Category;
  const tag = params.get("tag");
  if (tag) query.tag = tag;
  const folder = params.get("in");
  if (folder) query.in = folder;
  const limit = Number(params.get("limit"));
  if (Number.isInteger(limit) && limit > 0) query.limit = Math.min(limit, MAX_LIMIT);
  return query;
}

export function searchHref(query: SearchQuery): string {
  const params = searchParams(query);
  return params ? `/zoeken?${params}` : "/zoeken";
}

/** Is er iets om op te zoeken? Zonder woorden, soort of tag zou alles terugkomen. */
export function hasFilter(query: SearchQuery): boolean {
  return Boolean(query.q || query.type || query.tag);
}

/**
 * "Mijn bestanden / Foto's / 2026" voor het pad van een zoekresultaat. Bij iets dat met je
 * gedeeld is begint het pad van de server bij de gedeelde map: "Gedeeld met mij / Vakantie".
 */
export function pathLabel(
  path: SearchHit["path"],
  shared = false,
  labels: { mine: string; shared: string } = { mine: "Mijn bestanden", shared: "Gedeeld met mij" },
): string {
  if (shared) return [labels.shared, ...path.map((p) => p.name)].join(" / ");
  return path.map((p, i) => (i === 0 && p.name === "" ? labels.mine : p.name)).join(" / ");
}

/** pathLabel met de vertaalde namen (voor in de app). */
export function pathText(path: SearchHit["path"], shared = false): string {
  return pathLabel(path, shared, { mine: t("browser.myFiles"), shared: t("browser.sharedWithMe") });
}

/** Het pad van de map waar een resultaat in staat; zonder het resultaat zelf (een gedeelde
 * map die zelf gevonden wordt, staat bij sommige servers vooraan in zijn eigen pad). */
export function hitPath(hit: SearchHit): SearchHit["path"] {
  const path = hit.path;
  return path.length && path[path.length - 1].id === hit.node.id ? path.slice(0, -1) : path;
}

/** De map waar een resultaat in staat. Wat boven een gedeelde map staat, zie je niet: dan
 * naar Gedeeld met mij. */
export function parentHref(hit: SearchHit): string {
  const path = hitPath(hit);
  if (isShared(hit.node)) return path.length === 0 ? "/gedeeld" : `/map/${path[path.length - 1].id}`;
  return path.length <= 1 ? "/" : `/map/${path[path.length - 1].id}`;
}

/** Waar een klik op een resultaat heen gaat: een map zelf, of de map waar een bestand in staat. */
export function folderHref(hit: SearchHit): string {
  return hit.node.kind === "dir" ? `/map/${hit.node.id}` : parentHref(hit);
}
