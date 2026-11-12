// Selecteren in de tijdlijn: met Shift een reeks van het anker tot hier, ook als er maanden
// tussen zitten die nog niet geladen zijn. Dan vraagt de server de ids van die reeks
// (/photos/ids, ≤ 10.000); is alles ertussen al geladen, dan rekenen we het hier uit.

/** Wat er van een foto nodig is om er een cursor van te maken. */
export interface Placed {
  id: string;
  taken_day: number;
  taken_at: string;
}

export interface RangeSource {
  /** Het item op plek i, als het geladen is */
  item(i: number): Placed | undefined;
  /** Laadt (de maand van) plek i en geeft het item */
  load(i: number): Promise<Placed | undefined>;
  /** De ids van a tot en met b, in de volgorde van de tijdlijn (de server) */
  fetchIds(a: Placed, b: Placed): Promise<{ ids: string[]; truncated: boolean }>;
}

export interface RangeResult {
  ids: string[];
  /** Langer dan het maximum: alleen het begin is geselecteerd */
  truncated: boolean;
}

/** Hooguit zoveel ids in één reeks (zoals de server). */
export const MAX_RANGE = 10_000;

/** base64url zonder "=", zoals de server hem leest (photos.read_cursor). */
function base64url(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** De cursor van een foto: base64url("<taken_day>|<taken_at>|<id>"). */
export function cursorOf(p: Placed): string {
  return base64url(`${p.taken_day}|${p.taken_at}|${p.id}`);
}

/** De ids van plek a tot en met plek b (in welke volgorde ook). */
export async function rangeIds(src: RangeSource, a: number, b: number): Promise<RangeResult> {
  const lo = Math.min(a, b);
  const hi = Math.max(a, b);
  const local: string[] = [];
  let complete = hi - lo < MAX_RANGE;
  for (let i = lo; complete && i <= hi; i++) {
    const it = src.item(i);
    if (!it) complete = false;
    else local.push(it.id);
  }
  if (complete) return { ids: local, truncated: false };
  const first = src.item(lo) ?? (await src.load(lo));
  const last = src.item(hi) ?? (await src.load(hi));
  if (!first || !last) return { ids: local, truncated: false };
  // Met de uiteinden kan de rest ook binnen zijn gekomen (een album is één sectie)
  if (hi - lo < MAX_RANGE) {
    const again: string[] = [];
    for (let i = lo; i <= hi; i++) {
      const it = src.item(i);
      if (!it) break;
      again.push(it.id);
    }
    if (again.length === hi - lo + 1) return { ids: again, truncated: false };
  }
  const got = await src.fetchIds(first, last);
  return { ids: got.ids, truncated: got.truncated };
}

/** Een selectie: welke ids, en waar een Shift-reeks begint (een plek in de tijdlijn). */
export interface PhotoSelection {
  ids: ReadonlySet<string>;
  anchor: number | null;
}

export const NO_SELECTION: PhotoSelection = { ids: new Set(), anchor: null };

/** Ctrl/Cmd-klik of Spatie: dit item erbij of eraf; het wordt het nieuwe anker. */
export function toggled(s: PhotoSelection, index: number, id: string): PhotoSelection {
  const ids = new Set(s.ids);
  if (ids.has(id)) ids.delete(id);
  else ids.add(id);
  return { ids, anchor: index };
}

/** Een reeks erbij (Shift); met keep blijft wat al geselecteerd was (Shift+Ctrl). */
export function withRange(s: PhotoSelection, ids: readonly string[], keep: boolean): PhotoSelection {
  const next = new Set(keep ? s.ids : []);
  for (const id of ids) next.add(id);
  return { ids: next, anchor: s.anchor };
}
