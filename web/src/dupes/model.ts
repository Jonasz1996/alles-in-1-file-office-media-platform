// De logica van /dubbel (fase 6, Z5; Keuze 34–36), zonder React, zodat ze te testen is: de
// groepen van GET /duplicates bijhouden, wat de gebruiker aanvinkt (nooit iets vooraf), de map
// van een bestand, en waar de focus heen gaat als een groep verdwijnt.

import type { SearchHit } from "../api";
import type { DupeGroup, DupeItem, DupeKind } from "./types";

/** Het tabblad uit ?soort=: "lijkt" is near, al het andere (ook niets) exact. */
export function kindOf(param: string | null): DupeKind {
  return param === "lijkt" ? "near" : "exact";
}

/** En terug: exact staat niet in de adresbalk. */
export function paramOf(kind: DupeKind): string | null {
  return kind === "near" ? "lijkt" : null;
}

/** Hoe een groep heet: precies gelijk, of bij "near" het niveau van het zwakste paar. */
export type LevelKey = "exact" | "zeker" | "lijkt";

export function levelOf(group: Pick<DupeGroup, "kind" | "level">): LevelKey {
  if (group.kind === "exact") return "exact";
  // Alleen "zeker" als de server het zegt; iets anders (of niets) is het voorzichtige "lijkt"
  return group.level === "zeker" ? "zeker" : "lijkt";
}

/** De map van een bestand: van de hoofdmap (naam "") tot en met de map waar het in staat. */
export function folderPath(item: Pick<DupeItem, "node" | "path">): SearchHit["path"] {
  const path = item.path;
  return path.length && path[path.length - 1].id === item.node.id ? path.slice(0, -1) : path;
}

/** "Mijn bestanden / Vakantie / 2024" en de link naar die map. */
export function folderOf(item: Pick<DupeItem, "node" | "path">, myFiles: string): { label: string; href: string } {
  const path = folderPath(item);
  const label = path.length === 0 ? myFiles : path.map((p, i) => (i === 0 && p.name === "" ? myFiles : p.name)).join(" / ");
  const last = path[path.length - 1];
  const href = path.length <= 1 || !last ? "/" : `/map/${encodeURIComponent(last.id)}`;
  return { label, href };
}

/** Een volgende pagina erbij; een groep die er al staat (dezelfde key) komt niet dubbel. */
export function appendGroups(have: readonly DupeGroup[], more: readonly DupeGroup[]): DupeGroup[] {
  const seen = new Set(have.map((g) => g.key));
  const out = [...have];
  for (const g of more) {
    if (seen.has(g.key) || g.items.length < 2) continue;
    seen.add(g.key);
    out.push(g);
  }
  return out;
}

/** Na de prullenbak: die bestanden uit elke groep; een groep met minder dan twee gaat weg. */
export function dropItems(groups: readonly DupeGroup[], ids: ReadonlySet<string>): DupeGroup[] {
  const out: DupeGroup[] = [];
  for (const g of groups) {
    const items = g.items.filter((it) => !ids.has(it.node.id));
    if (items.length >= 2) out.push(items.length === g.items.length ? g : { ...g, items });
  }
  return out;
}

/** Na "Niet gelijk": de groep weg. */
export function dropGroup(groups: readonly DupeGroup[], key: string): DupeGroup[] {
  return groups.filter((g) => g.key !== key);
}

/** Wat aangevinkt is, per groep (key → ids). Begint altijd leeg: nooit iets vooraf. */
export type Ticks = ReadonlyMap<string, ReadonlySet<string>>;

export const NO_TICKS: Ticks = new Map();

const EMPTY: ReadonlySet<string> = new Set();

export function tickedIn(ticks: Ticks, key: string): ReadonlySet<string> {
  return ticks.get(key) ?? EMPTY;
}

export function tick(ticks: Ticks, key: string, id: string, on: boolean): Ticks {
  const set = new Set(tickedIn(ticks, key));
  if (on) set.add(id);
  else set.delete(id);
  const out = new Map(ticks);
  if (set.size === 0) out.delete(key);
  else out.set(key, set);
  return out;
}

/** Alleen wat nog bestaat: een id dat weg is (of een groep die weg is) telt niet meer. */
export function pruneTicks(ticks: Ticks, groups: readonly DupeGroup[]): Ticks {
  const out = new Map<string, ReadonlySet<string>>();
  for (const g of groups) {
    const had = ticks.get(g.key);
    if (!had) continue;
    const set = new Set(g.items.map((it) => it.node.id).filter((id) => had.has(id)));
    if (set.size) out.set(g.key, set);
  }
  return out;
}

/** De aangevinkte bestanden van een groep, in de volgorde van de groep. */
export function pickedIds(group: DupeGroup, ticks: Ticks): string[] {
  const set = tickedIn(ticks, group.key);
  return group.items.map((it) => it.node.id).filter((id) => set.has(id));
}

/** Is alles van de groep aangevinkt? Dan vraagt de pagina eerst of dat echt de bedoeling is. */
export function allPicked(group: DupeGroup, ticks: Ticks): boolean {
  return pickedIds(group, ticks).length === group.items.length;
}

/**
 * Welke groep de focus krijgt na een actie op groep key: dezelfde als hij er nog is, anders de
 * groep die erna kwam, anders die ervoor; null als er geen groep meer is.
 */
export function focusAfter(before: readonly DupeGroup[], after: readonly DupeGroup[], key: string): string | null {
  const left = new Set(after.map((g) => g.key));
  if (left.has(key)) return key;
  const at = before.findIndex((g) => g.key === key);
  for (let i = at + 1; i < before.length; i++) if (left.has(before[i].key)) return before[i].key;
  for (let i = at - 1; i >= 0; i--) if (left.has(before[i].key)) return before[i].key;
  return after[0]?.key ?? null;
}

/** De pijltjes binnen een groep: naar het vorige of volgende bestand, Home en End. */
export function moveInGroup(key: string, index: number, count: number): number | null {
  if (count <= 0) return null;
  if (key === "ArrowRight") return Math.min(index + 1, count - 1);
  if (key === "ArrowLeft") return Math.max(index - 1, 0);
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return null;
}
