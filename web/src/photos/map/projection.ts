// Webmercator (Keuze 31), zoals de tegels van een gewone kaart en de x16/y16 van photo_geo (Z3):
// de wereld is een vierkant van 0 tot 1, x naar het oosten en y naar het zuiden. Op zoom z is de
// wereld TILE · 2^z pixels breed. Puur, zodat het in vitest te testen is.

/** De breedte van een tegel in pixels (zoom 0 is één tegel). */
export const TILE = 256;
/** Verder dan dit naar het noorden of zuiden gaat Webmercator niet (y = 0 en y = 1). */
export const MAX_LAT = 85.0511287798066;

const RAD = Math.PI / 180;

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

/** Lengtegraad (−180 … 180) naar x (0 … 1). */
export function lonToX(lon: number): number {
  return (clamp(lon, -180, 180) + 180) / 360;
}

/** Breedtegraad naar y (0 bovenaan, 1 onderaan); voorbij ±MAX_LAT de rand. */
export function latToY(lat: number): number {
  const s = Math.sin(clamp(lat, -MAX_LAT, MAX_LAT) * RAD);
  return clamp(0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI), 0, 1);
}

/** x (0 … 1) naar lengtegraad. */
export function xToLon(x: number): number {
  return clamp(x, 0, 1) * 360 - 180;
}

/** y (0 … 1) naar breedtegraad. */
export function yToLat(y: number): number {
  return Math.atan(Math.sinh(Math.PI * (1 - 2 * clamp(y, 0, 1)))) / RAD;
}

/** (lengte, breedte) naar (x, y) in de wereld van 0 tot 1. */
export function project(lon: number, lat: number): [number, number] {
  return [lonToX(lon), latToY(lat)];
}

/** (x, y) naar (lengte, breedte). */
export function unproject(x: number, y: number): [number, number] {
  return [xToLon(x), yToLat(y)];
}

/** Hoe breed de wereld is op deze zoom, in pixels. */
export function worldSize(zoom: number): number {
  return TILE * 2 ** zoom;
}
