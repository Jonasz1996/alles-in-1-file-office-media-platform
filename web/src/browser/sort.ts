// Volgordes van de lijst. De server levert (kind, naam zonder hoofdletters en accenten, id):
// mappen eerst ("dir" < "file"), dan op fold(naam) per codepunt (zoals SQLite vergelijkt), dan
// op id. Nieuwe nodes uit live gebeurtenissen komen op die plek in de lijst. Een eigen sortering
// (op naam zoals mensen dat verwachten, grootte of datum) kan pas als de hele map binnen is.

import type { FileNode } from "../api";
import { collator, fold } from "../i18n";

export type SortKey = "name" | "size" | "modified";
export type SortDir = "asc" | "desc";

export interface Sort {
  key: SortKey;
  dir: SortDir;
}

/** De volgorde van de server (en van de pagina's): naam oplopend. */
export const SERVER_SORT: Sort = { key: "name", dir: "asc" };

export function isServerSort(s: Sort): boolean {
  return s.key === SERVER_SORT.key && s.dir === SERVER_SORT.dir;
}

const folded = new WeakMap<FileNode, string>();

/** fold(naam), één keer per node berekend. */
export function searchName(node: FileNode): string {
  let name = folded.get(node);
  if (name === undefined) {
    name = fold(node.name);
    folded.set(node, name);
  }
  return name;
}

/** Vergelijkt per codepunt (niet per UTF-16-eenheid), zoals de database. */
export function compareCodepoints(a: string, b: string): number {
  if (a === b) return 0;
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    const x = a.codePointAt(i) ?? 0;
    const y = b.codePointAt(i) ?? 0;
    if (x !== y) return x < y ? -1 : 1;
    if (x > 0xffff) i++; // een surrogaatpaar telt als één codepunt
  }
  return a.length - b.length;
}

/** De volgorde van de server: kind, fold(naam), id. */
export function serverCompare(a: FileNode, b: FileNode): number {
  if (a.kind !== b.kind) return a.kind < b.kind ? -1 : 1;
  const byName = compareCodepoints(searchName(a), searchName(b));
  if (byName !== 0) return byName;
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

/**
 * Waar node in een lijst in servervolgorde hoort (binair zoeken). get(i) geeft de node op plek
 * i; ontbreekt die (nog niet geladen), dan telt die plek als "erna".
 */
export function insertionIndex(count: number, get: (i: number) => FileNode | undefined, node: FileNode): number {
  let lo = 0;
  let hi = count;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    const other = get(mid);
    if (other && serverCompare(other, node) < 0) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

function byKey(key: SortKey): (a: FileNode, b: FileNode) => number {
  switch (key) {
    case "size":
      return (a, b) => a.size - b.size;
    case "modified":
      return (a, b) => (a.modified_at < b.modified_at ? -1 : a.modified_at > b.modified_at ? 1 : 0);
    case "name":
      return (a, b) => collator.compare(a.name, b.name);
  }
}

/**
 * Een eigen sortering: mappen altijd eerst, dan op key (naam op de manier van mensen:
 * "foto 2" vóór "foto 10"). Bij gelijk blijft de volgorde van de server (stabiel). ids staan
 * in servervolgorde.
 */
export function sortIds(ids: readonly string[], nodes: ReadonlyMap<string, FileNode>, sort: Sort): string[] {
  const compare = byKey(sort.key);
  const sign = sort.dir === "asc" ? 1 : -1;
  const position = new Map<string, number>();
  ids.forEach((id, i) => position.set(id, i));
  return [...ids].sort((x, y) => {
    const a = nodes.get(x);
    const b = nodes.get(y);
    if (!a || !b) return (position.get(x) ?? 0) - (position.get(y) ?? 0);
    const dirs = (a.kind === "dir" ? 0 : 1) - (b.kind === "dir" ? 0 : 1);
    if (dirs !== 0) return dirs;
    return sign * compare(a, b) || (position.get(x) ?? 0) - (position.get(y) ?? 0);
  });
}

/** Wat een klik op de kop van een kolom doet: dezelfde kolom draait om, een andere begint
 * oplopend (datum en grootte: de nieuwste en grootste eerst). */
export function nextSort(current: Sort, key: SortKey): Sort {
  if (current.key === key) return { key, dir: current.dir === "asc" ? "desc" : "asc" };
  return { key, dir: key === "name" ? "asc" : "desc" };
}
