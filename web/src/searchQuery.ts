// Zoeken in de adresbalk (/zoeken?q=...&type=...&tag=...), zodat terug en vooruit werken
// en je een zoekopdracht kunt bewaren. Losse functies, zonder React, zodat ze te testen zijn.

import { type Category, type SearchHit, type SearchQuery, searchParams } from "./api";

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

const CATEGORIES = Object.keys(CATEGORY_LABELS) as Category[];
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

/** "Mijn bestanden / Foto's / 2026" voor het pad van een zoekresultaat. */
export function pathLabel(path: SearchHit["path"]): string {
  return path.map((p, i) => (i === 0 && p.name === "" ? "Mijn bestanden" : p.name)).join(" / ");
}

/** De map waar een resultaat in staat. */
export function parentHref(hit: SearchHit): string {
  return hit.path.length <= 1 ? "/" : `/map/${hit.path[hit.path.length - 1].id}`;
}

/** Waar een klik op een resultaat heen gaat: een map zelf, of de map waar een bestand in staat. */
export function folderHref(hit: SearchHit): string {
  return hit.node.kind === "dir" ? `/map/${hit.node.id}` : parentHref(hit);
}
