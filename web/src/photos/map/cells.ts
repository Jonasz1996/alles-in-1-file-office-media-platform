// De cellen van de server op het scherm (Keuze 32). Op zoom z (0–14) is een cel
// (x16 >> (14 − z), y16 >> (14 − z)): vier cellen per tegelbreedte, dus 2^(z+2) over de hele
// wereld, 64 pixels breed op zoom z. Welk stuk de kaart vraagt, en waar elke cel als stip komt.
// Puur, zodat het in vitest te testen is.

import { latToY, lonToX } from "./projection";
import type { BBox, MapCell } from "./types";
import { contains, type Rect, rectBBox, type Size, toScreen, type View, visibleRect } from "./view";

/** Een tegel van zoom 16 (x16, y16) is zo breed in de wereld van 0 tot 1. */
const X16 = 1 / 65536;
/** Om het venster heen vraagt de kaart een kwart venster extra, zodat een beetje schuiven geen
 * nieuw verzoek kost. */
const PAD = 0.25;

/** Hoeveel cellen er over de hele wereld gaan op zoom z. */
export function cellsAcross(z: number): number {
  return 2 ** (z + 2);
}

/** Het stuk wereld van cel (x, y) op zoom z. */
export function cellRect(x: number, y: number, z: number): Rect {
  const n = cellsAcross(z);
  return { x0: x / n, y0: y / n, x1: (x + 1) / n, y1: (y + 1) / n };
}

/** Het venster met een kwart extra aan elke kant (pad = 0: alleen wat in beeld is), naar buiten
 * afgerond op hele cellen van zoom z: dan telt de server elke cel helemaal. */
export function coverRect(v: View, size: Size, z: number, pad = PAD): Rect {
  const r = visibleRect(v, size);
  const dx = (r.x1 - r.x0) * pad;
  const dy = (r.y1 - r.y0) * pad;
  const n = cellsAcross(z);
  const snap = (value: number, up: boolean) => {
    const cells = up ? Math.ceil(value * n - 1e-9) : Math.floor(value * n + 1e-9);
    return Math.min(1, Math.max(0, cells / n));
  };
  return { x0: snap(r.x0 - dx, false), y0: snap(r.y0 - dy, false), x1: snap(r.x1 + dx, true), y1: snap(r.y1 + dy, true) };
}

/** De bbox voor de server bij een stuk dat op hele cellen ligt. Elke rand ligt een halve tegel
 * van zoom 16 naar binnen: of de server een rand als ≤ of als < leest, en of hij afrondt naar
 * beneden of naar boven, de tegels die hij neemt zijn precies die van de cellen in het stuk. */
export function requestBBox(r: Rect): BBox {
  const h = X16 / 2;
  return rectBBox({ x0: r.x0 + h, y0: r.y0 + h, x1: Math.max(r.x0 + h, r.x1 - h), y1: Math.max(r.y0 + h, r.y1 - h) });
}

/** Wat er geladen is: het stuk, de zoom van de cellen, en de filters (als sleutel). */
export interface Loaded {
  rect: Rect;
  z: number;
  filters: string;
}

/** Moet de kaart nieuwe cellen vragen? Niet zolang wat in beeld is binnen het geladen stuk ligt,
 * op dezelfde zoom van de cellen en met dezelfde filters. */
export function needsCells(loaded: Loaded | null, v: View, size: Size, z: number, filters: string): boolean {
  if (!loaded || loaded.z !== z || loaded.filters !== filters) return true;
  return !contains(loaded.rect, visibleRect(v, size));
}

/** Een cel als stip op het scherm; cellen die bijna op dezelfde plek vallen, samen als één. */
export interface Dot {
  key: string;
  /** De grootste cel van de stip: daar komen de omslag en het land vandaan */
  cell: MapCell;
  /** Hoeveel foto's de stip heeft (van alle cellen samen) */
  n: number;
  /** Het stuk wereld van de stip: de cel, of de cellen samen (inzoomen en de foto's openen) */
  rect: Rect;
  /** Het midden, in pixels in het venster */
  sx: number;
  sy: number;
  /** De straal in pixels */
  r: number;
}

/** De straal van een stip: een losse foto 18 px, een groep groeit met het aantal (hooguit 34). */
export function dotRadius(n: number): number {
  return n <= 1 ? 18 : Math.min(34, 16 + 4 * Math.log10(n));
}

/** Twee stippen die verder dan dit deel van hun stralen samen over elkaar vallen, worden één: een
 * groep foto's op de grens van twee cellen is dan één stip (met één naam), niet twee halve. */
const MERGE = 0.75;

function union(a: Rect, b: Rect): Rect {
  return { x0: Math.min(a.x0, b.x0), y0: Math.min(a.y0, b.y0), x1: Math.max(a.x1, b.x1), y1: Math.max(a.y1, b.y1) };
}

/** De cellen die in beeld zijn (ook half), als stippen in leesvolgorde: van boven naar beneden en
 * van links naar rechts (dan gaat Tab ook zo). De grootste cel eerst: een kleinere stip die er
 * grotendeels over valt, gaat erin op; het midden is dan dat van alle foto's samen. */
export function placeDots(cells: readonly MapCell[], z: number, v: View, size: Size): Dot[] {
  const order = [...cells].sort((a, b) => b.n - a.n || a.y - b.y || a.x - b.x);
  const groups: { cell: MapCell; n: number; rect: Rect; wx: number; wy: number; sx: number; sy: number; r: number }[] = [];
  for (const cell of order) {
    const wx = lonToX(cell.lon);
    const wy = latToY(cell.lat);
    const [sx, sy] = toScreen(v, size, wx, wy);
    const r = dotRadius(cell.n);
    const rect = cellRect(cell.x, cell.y, z);
    const host = groups.find((g) => Math.hypot(g.sx - sx, g.sy - sy) < (g.r + r) * MERGE);
    if (host) {
      const n = host.n + cell.n;
      host.wx = (host.wx * host.n + wx * cell.n) / n;
      host.wy = (host.wy * host.n + wy * cell.n) / n;
      host.n = n;
      host.rect = union(host.rect, rect);
      continue;
    }
    groups.push({ cell, n: cell.n, rect, wx, wy, sx, sy, r });
  }
  const out: Dot[] = [];
  for (const g of groups) {
    const [sx, sy] = toScreen(v, size, g.wx, g.wy);
    const r = dotRadius(g.n);
    if (sx < -r || sy < -r || sx > size.w + r || sy > size.h + r) continue;
    out.push({ key: `${z}/${g.cell.x}/${g.cell.y}`, cell: g.cell, n: g.n, rect: g.rect, sx, sy, r });
  }
  // Rijen van 48 px: stippen die bijna even hoog staan, van links naar rechts
  return out.sort((a, b) => Math.floor(a.sy / 48) - Math.floor(b.sy / 48) || a.sx - b.sx);
}

/** Het stuk wereld dat al deze cellen samen beslaan (null zonder cellen). */
export function cellsBounds(cells: readonly Pick<MapCell, "x" | "y">[], z: number): Rect | null {
  if (cells.length === 0) return null;
  let r: Rect | null = null;
  for (const c of cells) {
    const q = cellRect(c.x, c.y, z);
    r = r ? { x0: Math.min(r.x0, q.x0), y0: Math.min(r.y0, q.y0), x1: Math.max(r.x1, q.x1), y1: Math.max(r.y1, q.y1) } : q;
  }
  return r;
}
