// Wat de kaart toont: het midden (x, y in de wereld van 0 tot 1) en de zoom, in een venster van
// w × h pixels. Schuiven, zoomen rond een punt, passend maken, en het stuk wereld dat in beeld is.
// Puur, zodat het in vitest te testen is. De kaart herhaalt zich niet naar links of rechts: op
// de kleinste zoom vult de wereld het venster, en bij ±180° houdt hij op (een venster gaat nooit
// over de datumgrens, dus west < oost in elke bbox).

import { TILE, worldSize, xToLon, yToLat } from "./projection";
import { type BBox, MAX_ZOOM } from "./types";

export interface View {
  /** Het midden, in de wereld van 0 tot 1 */
  x: number;
  y: number;
  /** De zoom, ook tussen twee hele getallen in */
  zoom: number;
}

export interface Size {
  w: number;
  h: number;
}

/** Een stuk wereld: x0 ≤ x1 en y0 ≤ y1, van 0 tot 1 (y0 is de noordkant). */
export interface Rect {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/** Zo ver kun je inzoomen. Boven MAX_ZOOM (14) blijven de cellen die van zoom 14: losse foto's. */
export const MAX_VIEW_ZOOM = 17;
/** Zo ver zoomt "alles in beeld" hooguit in (één foto). */
export const FIT_MAX_ZOOM = 15;
export const WORLD: Rect = { x0: 0, y0: 0, x1: 1, y1: 1 };

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

/** De kleinste zoom: de wereld vult het venster in beide richtingen. */
export function minZoom(size: Size): number {
  return Math.max(0, Math.log2(Math.max(size.w, size.h, 1) / TILE));
}

/** Binnen de grenzen: de zoom tussen minZoom en MAX_VIEW_ZOOM, en geen leeg stuk naast de wereld. */
export function clampView(v: View, size: Size): View {
  const zoom = clamp(Number.isFinite(v.zoom) ? v.zoom : 0, minZoom(size), MAX_VIEW_ZOOM);
  const W = worldSize(zoom);
  const hx = size.w / 2 / W;
  const hy = size.h / 2 / W;
  const x = hx >= 0.5 ? 0.5 : clamp(Number.isFinite(v.x) ? v.x : 0.5, hx, 1 - hx);
  const y = hy >= 0.5 ? 0.5 : clamp(Number.isFinite(v.y) ? v.y : 0.5, hy, 1 - hy);
  return { x, y, zoom };
}

/** Een punt in de wereld naar pixels in het venster. */
export function toScreen(v: View, size: Size, x: number, y: number): [number, number] {
  const W = worldSize(v.zoom);
  return [(x - v.x) * W + size.w / 2, (y - v.y) * W + size.h / 2];
}

/** Pixels in het venster naar een punt in de wereld. */
export function toWorld(v: View, size: Size, sx: number, sy: number): [number, number] {
  const W = worldSize(v.zoom);
  return [v.x + (sx - size.w / 2) / W, v.y + (sy - size.h / 2) / W];
}

/** De kaart dx, dy pixels meeschuiven (zoals slepen: de wereld gaat mee met de muis). */
export function panBy(v: View, size: Size, dx: number, dy: number): View {
  const W = worldSize(v.zoom);
  return clampView({ x: v.x - dx / W, y: v.y - dy / W, zoom: v.zoom }, size);
}

/** Naar een andere zoom, waarbij het punt onder (sx, sy) blijft staan. */
export function zoomAround(v: View, size: Size, zoom: number, sx: number, sy: number): View {
  const [wx, wy] = toWorld(v, size, sx, sy);
  const target = clamp(zoom, minZoom(size), MAX_VIEW_ZOOM);
  const W = worldSize(target);
  return clampView({ x: wx - (sx - size.w / 2) / W, y: wy - (sy - size.h / 2) / W, zoom: target }, size);
}

/** Het stuk wereld dat in beeld is. */
export function visibleRect(v: View, size: Size): Rect {
  const W = worldSize(v.zoom);
  return {
    x0: clamp(v.x - size.w / 2 / W, 0, 1),
    y0: clamp(v.y - size.h / 2 / W, 0, 1),
    x1: clamp(v.x + size.w / 2 / W, 0, 1),
    y1: clamp(v.y + size.h / 2 / W, 0, 1),
  };
}

/** Het venster dat r helemaal toont, met een rand van pad pixels, hooguit op maxZoom. */
export function fitRect(r: Rect, size: Size, maxZoom = FIT_MAX_ZOOM, pad = 32): View {
  const w = Math.max(r.x1 - r.x0, 1e-9);
  const h = Math.max(r.y1 - r.y0, 1e-9);
  const zw = Math.log2(Math.max(size.w - 2 * pad, 1) / (w * TILE));
  const zh = Math.log2(Math.max(size.h - 2 * pad, 1) / (h * TILE));
  return clampView({ x: (r.x0 + r.x1) / 2, y: (r.y0 + r.y1) / 2, zoom: Math.min(zw, zh, maxZoom) }, size);
}

/** De zoom van de cellen bij dit venster (Keuze 32): een cel is 64 tot 128 pixels breed. */
export function cellZoom(v: View): number {
  return clamp(Math.floor(v.zoom), 0, MAX_ZOOM);
}

/** Ligt b helemaal in a? */
export function contains(a: Rect, b: Rect): boolean {
  return b.x0 >= a.x0 && b.y0 >= a.y0 && b.x1 <= a.x1 && b.y1 <= a.y1;
}

/** Een stuk wereld als bbox (west, zuid, oost, noord) in graden. */
export function rectBBox(r: Rect): BBox {
  return [round6(xToLon(r.x0)), round6(yToLat(r.y1)), round6(xToLon(r.x1)), round6(yToLat(r.y0))];
}

/** Zes decimalen (±0,1 m): een vast adres voor hetzelfde stuk, en geen "1e-7" in de bbox. */
function round6(v: number): number {
  const r = Math.round(v * 1e6) / 1e6;
  return Object.is(r, -0) ? 0 : r;
}
