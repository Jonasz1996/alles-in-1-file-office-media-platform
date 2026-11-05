// De indeling van de fototijdlijn, zonder React of DOM: secties (maanden, of één album) met een
// kopregel en daaronder rijen van `columns` foto's. Alles volgt uit de aantallen per sectie, dus
// de volledige hoogte staat vast voordat er één foto geladen is. Items hebben een globaal
// volgnummer (0 = de nieuwste); rijen ook (0 = de kop van de eerste maand).

import type { Move } from "../ui/keys";

export interface Layout {
  columns: number;
  /** Aantal items per sectie */
  counts: readonly number[];
  /** Hebben secties een kopregel (de tijdlijn wel, een album niet) */
  headers: boolean;
  /** firstItem[s] = globaal nummer van het eerste item van sectie s; lengte secties + 1 */
  firstItem: readonly number[];
  /** firstRow[s] = de eerste rij van sectie s (de kop, als die er is); lengte secties + 1 */
  firstRow: readonly number[];
  rowCount: number;
  total: number;
  /** offsets[r] = bovenkant van rij r in px; lengte rowCount + 1 */
  offsets: readonly number[];
}

export type RowInfo =
  | { kind: "header"; section: number }
  | { kind: "photos"; section: number; first: number; count: number };

export function buildLayout(
  counts: readonly number[],
  columns: number,
  rowHeight: number,
  headerHeight: number,
  headers = true,
): Layout {
  const cols = Math.max(1, Math.floor(columns));
  const firstItem = [0];
  const firstRow = [0];
  const offsets = [0];
  let y = 0;
  for (const count of counts) {
    const n = Math.max(0, count);
    if (headers) {
      y += headerHeight;
      offsets.push(y);
    }
    const rows = Math.ceil(n / cols);
    for (let r = 0; r < rows; r++) {
      y += rowHeight;
      offsets.push(y);
    }
    firstItem.push(firstItem[firstItem.length - 1] + n);
    firstRow.push(offsets.length - 1);
  }
  return {
    columns: cols,
    counts,
    headers,
    firstItem,
    firstRow,
    rowCount: offsets.length - 1,
    total: firstItem[firstItem.length - 1],
    offsets,
  };
}

/** Laatste s met a[s] <= x (a oplopend, binair zoeken); -1 als x vóór a[0] ligt. */
function lastAtOrBelow(a: readonly number[], x: number, hi = a.length - 1): number {
  let lo = 0;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (a[mid] <= x) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return found;
}

/** De sectie van item i. Een lege sectie heeft geen items en wordt overgeslagen. */
export function sectionOfItem(l: Layout, i: number): number {
  const n = l.counts.length;
  if (n === 0) return -1;
  let s = lastAtOrBelow(l.firstItem, Math.max(0, Math.min(i, l.total - 1)), n - 1);
  while (s > 0 && l.counts[s] === 0) s--;
  return Math.max(0, s);
}

/** De sectie van rij r. */
export function sectionOfRow(l: Layout, r: number): number {
  const n = l.counts.length;
  if (n === 0) return -1;
  // Een lege sectie zonder kop heeft geen rijen: dan telt de laatste sectie die bij r begint
  return Math.max(0, lastAtOrBelow(l.firstRow, Math.max(0, Math.min(r, l.rowCount - 1)), n - 1));
}

export function rowInfo(l: Layout, r: number): RowInfo {
  const section = sectionOfRow(l, r);
  const top = l.firstRow[section];
  if (l.headers && r === top) return { kind: "header", section };
  const k = r - top - (l.headers ? 1 : 0);
  const first = l.firstItem[section] + k * l.columns;
  return { kind: "photos", section, first, count: Math.min(l.columns, l.firstItem[section + 1] - first) };
}

/** De rij waarin item i staat. */
export function rowOfItem(l: Layout, i: number): number {
  const s = sectionOfItem(l, i);
  if (s < 0) return -1;
  const k = Math.floor((i - l.firstItem[s]) / l.columns);
  return l.firstRow[s] + (l.headers ? 1 : 0) + k;
}

/** De rij op hoogte y (px vanaf de bovenkant van de tijdlijn). */
export function rowAtOffset(l: Layout, y: number): number {
  if (l.rowCount === 0) return -1;
  return Math.max(0, Math.min(l.rowCount - 1, lastAtOrBelow(l.offsets, y, l.rowCount - 1)));
}

/** De sectie die bovenaan staat als de tijdlijn tot y gescrold is. */
export function sectionAtOffset(l: Layout, y: number): number {
  const r = rowAtOffset(l, y);
  return r < 0 ? -1 : sectionOfRow(l, r);
}

/** Alle secties die (deels) in de rijen [start, end) vallen en items hebben. */
export function sectionsInRows(l: Layout, start: number, end: number): number[] {
  if (l.rowCount === 0 || end <= start) return [];
  const a = sectionOfRow(l, start);
  const b = sectionOfRow(l, Math.min(end, l.rowCount) - 1);
  const out: number[] = [];
  for (let s = a; s <= b; s++) if (l.counts[s] > 0) out.push(s);
  return out;
}

/** De rij met foto's boven of onder rij r (koppen overslaan); -1 als die er niet is. */
function photoRow(l: Layout, r: number, step: 1 | -1): number {
  for (let x = r + step; x >= 0 && x < l.rowCount; x += step) {
    if (rowInfo(l, x).kind === "photos") return x;
  }
  return -1;
}

/** Naar dezelfde kolom in een andere fotorij, of het laatste item als die rij korter is. */
function inRow(l: Layout, r: number, column: number): number {
  const info = rowInfo(l, r);
  if (info.kind !== "photos") return -1;
  return info.first + Math.min(column, info.count - 1);
}

/**
 * De volgende plek in de tijdlijn. Links/rechts gaan door de volgorde (ook over de grens van een
 * maand), op/neer naar dezelfde kolom in de fotorij erboven/eronder, PageUp/PageDown zoveel
 * fotorijen, Home/End naar de eerste/laatste foto.
 */
export function moveInLayout(l: Layout, cur: number, m: Move, pageRows = 3): number {
  if (l.total <= 0) return -1;
  const last = l.total - 1;
  if (cur < 0 || cur > last) return m === "end" ? last : 0;
  switch (m) {
    case "home":
      return 0;
    case "end":
      return last;
    case "left":
      return Math.max(0, cur - 1);
    case "right":
      return Math.min(last, cur + 1);
    default:
      break;
  }
  const step = m === "up" || m === "pageup" ? -1 : 1;
  const times = m === "pageup" || m === "pagedown" ? Math.max(1, pageRows) : 1;
  const s = sectionOfItem(l, cur);
  const column = (cur - l.firstItem[s]) % l.columns;
  let r = rowOfItem(l, cur);
  for (let k = 0; k < times; k++) {
    const next = photoRow(l, r, step);
    if (next < 0) {
      // Voorbij het begin of eind: PageUp/PageDown gaan dan naar de eerste of laatste foto
      if (k === 0 && times === 1) return cur;
      return step < 0 ? 0 : last;
    }
    r = next;
  }
  return inRow(l, r, column);
}

// --- de scrubber: een plek op de balk (0..1) ↔ een scrollpositie ---

/** Hoe ver er gescrold kan worden (de volle hoogte min wat in beeld past). */
export function scrollRange(l: Layout, viewport: number): number {
  return Math.max(0, (l.offsets[l.rowCount] ?? 0) - Math.max(0, viewport));
}

/** Scrollpositie y → plek op de balk (0 = boven, 1 = onder). */
export function offsetToFraction(l: Layout, y: number, viewport: number): number {
  const range = scrollRange(l, viewport);
  if (range <= 0) return 0;
  return Math.max(0, Math.min(1, y / range));
}

/** Plek op de balk → scrollpositie. */
export function fractionToOffset(l: Layout, f: number, viewport: number): number {
  return Math.round(Math.max(0, Math.min(1, f)) * scrollRange(l, viewport));
}

/** De bovenkant van sectie s (zijn kop). */
export function sectionOffset(l: Layout, s: number): number {
  const i = Math.max(0, Math.min(s, l.counts.length - 1));
  return l.offsets[l.firstRow[i]] ?? 0;
}

/** Waar op de balk elk jaar begint (voor de labels): de eerste maand van dat jaar in de lijst. */
export function yearMarks(
  l: Layout,
  months: readonly string[],
  viewport: number,
): { year: string; section: number; fraction: number }[] {
  const out: { year: string; section: number; fraction: number }[] = [];
  let prev = "";
  months.forEach((m, s) => {
    const year = m.slice(0, 4);
    if (year === prev) return;
    prev = year;
    out.push({ year, section: s, fraction: offsetToFraction(l, sectionOffset(l, s), viewport) });
  });
  return out;
}

/** De sectie bij een stap van de scrubber met het toetsenbord. */
export function scrubStep(
  months: readonly string[],
  cur: number,
  key: "prev" | "next" | "prevYear" | "nextYear" | "first" | "last",
): number {
  const n = months.length;
  if (n === 0) return -1;
  const c = Math.max(0, Math.min(cur, n - 1));
  switch (key) {
    case "first":
      return 0;
    case "last":
      return n - 1;
    case "prev":
      return Math.max(0, c - 1);
    case "next":
      return Math.min(n - 1, c + 1);
    case "prevYear":
    case "nextYear": {
      // De nieuwste maand staat bovenaan: een jaar terug in de tijd is omlaag in de lijst
      const step = key === "nextYear" ? 1 : -1;
      const year = months[c].slice(0, 4);
      let s = c;
      while (s + step >= 0 && s + step < n && months[s + step].slice(0, 4) === year) s += step;
      if (s + step < 0 || s + step >= n) return s;
      s += step;
      // Naar het begin (de nieuwste maand) van dat jaar
      const target = months[s].slice(0, 4);
      while (s - 1 >= 0 && months[s - 1].slice(0, 4) === target) s--;
      return s;
    }
  }
}
