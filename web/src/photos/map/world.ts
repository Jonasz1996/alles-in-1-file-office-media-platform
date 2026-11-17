// De wereld van de kaart (Keuze 31): de landsgrenzen uit Natural Earth 110m (publiek domein),
// gemaakt door tools/kaart/maak_wereld.py en als statisch bestand in public/kaart/, nooit in de JS.
// De route haalt hem met fetch (de CSP heeft connect-src 'self'); hij komt niet in de precache van
// de service worker. CI maakt hem opnieuw uit de bron (sha256 vast) en vergelijkt hem met dit
// bestand (de job zoek-perf); world.test.ts toetst de naam tegen de inhoud.
//
// Het formaat (spike S6): per land a3, a2, nl, en en de ringen per vlak; een ring is één lijst
// gehele getallen in honderdsten van een graad: het eerste punt, daarna steeds het verschil met
// het vorige, zonder sluitpunt. Puur, zodat het in vitest te testen is.

import { latToY, lonToX, TILE } from "./projection";

/** De naam bevat de eerste 12 tekens van de sha256 van de inhoud (maak_wereld.naam). */
export const WORLD_FILE = "world-110m.f2c3625f1d27.json";
export const WORLD_URL = `/kaart/${WORLD_FILE}`;

/** Een land zoals in het bestand. */
interface RawCountry {
  a3: string;
  a2: string | null;
  nl: string;
  en: string;
  v: number[][][];
}

export interface Country {
  a3: string;
  /** ISO 3166-1 alpha-2, of null (zoals bij Kosovo of Noord-Cyprus) */
  a2: string | null;
  nl: string;
  en: string;
  /** De ringen in graden: [lon0, lat0, lon1, lat1, …], alle vlakken achter elkaar */
  rings: number[][];
  /** Het kader in graden: west, zuid, oost, noord */
  box: [number, number, number, number];
}

export class BadWorld extends Error {}

/** Eén ring terug naar graden. */
export function ringDegrees(r: readonly number[], scale: number): number[] {
  const out = new Array<number>(r.length);
  let x = 0;
  let y = 0;
  for (let i = 0; i + 1 < r.length; i += 2) {
    x += r[i];
    y += r[i + 1];
    out[i] = x / scale;
    out[i + 1] = y / scale;
  }
  return out;
}

function isInts(v: unknown): v is number[] {
  return Array.isArray(v) && v.length >= 6 && v.length % 2 === 0 && v.every((n) => Number.isInteger(n));
}

/** Het bestand uitpakken; BadWorld als het niet de vorm van maak_wereld.py heeft. */
export function decodeWorld(data: unknown): Country[] {
  const d = data as { v?: unknown; schaal?: unknown; landen?: unknown } | null;
  if (!d || d.v !== 1 || typeof d.schaal !== "number" || !(d.schaal > 0) || !Array.isArray(d.landen)) {
    throw new BadWorld("geen wereld van versie 1");
  }
  const scale = d.schaal;
  return (d.landen as RawCountry[]).map((land) => {
    if (typeof land?.a3 !== "string" || !Array.isArray(land.v)) throw new BadWorld("geen land");
    const rings: number[][] = [];
    let w = 180;
    let s = 90;
    let e = -180;
    let n = -90;
    for (const vlak of land.v) {
      if (!Array.isArray(vlak)) throw new BadWorld("geen vlak");
      for (const r of vlak) {
        if (!isInts(r)) throw new BadWorld("geen ring");
        const deg = ringDegrees(r, scale);
        for (let i = 0; i < deg.length; i += 2) {
          w = Math.min(w, deg[i]);
          e = Math.max(e, deg[i]);
          s = Math.min(s, deg[i + 1]);
          n = Math.max(n, deg[i + 1]);
        }
        rings.push(deg);
      }
    }
    return { a3: land.a3, a2: land.a2 ?? null, nl: String(land.nl), en: String(land.en), rings, box: [w, s, e, n] };
  });
}

/** De paden van alle landen (countryPath), één keer per wereld. */
const pathCache = new WeakMap<readonly Country[], string[]>();
export function countryPaths(countries: readonly Country[]): string[] {
  let out = pathCache.get(countries);
  if (!out) {
    out = countries.map(countryPath);
    pathCache.set(countries, out);
  }
  return out;
}

let loading: Promise<Country[]> | null = null;

/** De wereld van de app, één keer per pagina opgehaald: de kaart en de rij Plek delen hem. Met
 * fetch van de eigen host (connect-src 'self'); mislukt het, dan probeert de volgende keer opnieuw. */
export function loadWorld(): Promise<Country[]> {
  if (!loading) {
    const p = fetch(WORLD_URL, { credentials: "same-origin" })
      .then((r) => {
        if (!r.ok) throw new BadWorld(`HTTP ${r.status}`);
        return r.json() as Promise<unknown>;
      })
      .then(decodeWorld);
    loading = p;
    p.catch(() => {
      if (loading === p) loading = null;
    });
  }
  return loading;
}

/** Het pad van een land voor SVG, in pixels op zoom 0 (de wereld is 256 breed); de kaart schaalt
 * het met een transform. Elke ring is een eigen stuk (M … Z); gaten tekent evenodd. */
export function countryPath(c: Pick<Country, "rings">): string {
  let d = "";
  for (const r of c.rings) {
    for (let i = 0; i < r.length; i += 2) {
      const x = lonToX(r[i]) * TILE;
      const y = latToY(r[i + 1]) * TILE;
      d += `${i === 0 ? "M" : i === 2 ? "L" : " "}${x.toFixed(3)} ${y.toFixed(3)}`;
    }
    d += "Z";
  }
  return d;
}

/** Ligt (lon, lat) in de ring? (even-oneven, de ring in graden) */
function inRing(r: readonly number[], lon: number, lat: number): boolean {
  let inside = false;
  for (let i = 0, j = r.length - 2; i < r.length; j = i, i += 2) {
    const xi = r[i];
    const yi = r[i + 1];
    const xj = r[j];
    const yj = r[j + 1];
    if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/** Het land waar (lon, lat) in ligt, of null (op zee). Op 110m is een grens ±10 km grof: genoeg
 * voor de naam bij een groep foto's, niet voor de plaats van één foto (die komt van de server). */
export function countryAt(countries: readonly Country[], lon: number, lat: number): Country | null {
  for (const c of countries) {
    const [w, s, e, n] = c.box;
    if (lon < w || lon > e || lat < s || lat > n) continue;
    let inside = false;
    for (const r of c.rings) if (inRing(r, lon, lat)) inside = !inside;
    if (inside) return c;
  }
  return null;
}

/** De naam van een land in de taal van de app. */
export function countryName(c: Pick<Country, "nl" | "en">, locale: string): string {
  return locale === "nl" ? c.nl : c.en;
}

/** Een land bij zijn code (ISO alpha-2, zoals in GET /photos/places en de plek van een foto). */
export function countryByCode(countries: readonly Country[], a2: string | null): Country | null {
  if (!a2) return null;
  const code = a2.toUpperCase();
  return countries.find((c) => c.a2 === code) ?? null;
}
