import { describe, expect, it } from "vitest";
import { cellRect, requestBBox } from "./cells";
import { homeRect } from "./home";
import { latToY, lonToX } from "./projection";
import { cellsPath } from "./requests";
import { MAX_CELLS, type MapCell, type MapCells } from "./types";
import { cellZoom, contains, fitRect, WORLD } from "./view";

const SIZE = { w: 800, h: 600 };

/** Een nep-server met vaste foto's: telt per verzoek de cellen zoals Keuze 4a. */
function server(points: [number, number][]) {
  const asked: string[] = [];
  const get = async (path: string): Promise<MapCells> => {
    asked.push(path);
    const q = new URL(path, "http://x").searchParams;
    const z = Number(q.get("z"));
    const [w, s, e, n] = (q.get("bbox") ?? "").split(",").map(Number);
    const x16w = Math.floor(lonToX(w) * 65536);
    const x16e = Math.floor(lonToX(e) * 65536);
    const y16n = Math.floor(latToY(n) * 65536);
    const y16s = Math.floor(latToY(s) * 65536);
    const by = new Map<string, MapCell>();
    for (const [lat, lon] of points) {
      const x16 = Math.floor(lonToX(lon) * 65536);
      const y16 = Math.floor(latToY(lat) * 65536);
      if (x16 < x16w || x16 > x16e || y16 < y16n || y16 > y16s) continue;
      const x = x16 >> (14 - z);
      const y = y16 >> (14 - z);
      const key = `${x}/${y}`;
      const c = by.get(key);
      by.set(key, c ? { ...c, n: c.n + 1 } : { x, y, n: 1, lat, lon, cover: key });
    }
    return { cells: [...by.values()] };
  };
  return { get, asked };
}

describe("Home: al je foto's in beeld", () => {
  it("zonder foto's: null na één verzoek voor de hele wereld", async () => {
    const s = server([]);
    expect(await homeRect(s.get, {}, SIZE)).toBeNull();
    expect(s.asked).toEqual([cellsPath(requestBBox(WORLD), 0)]);
  });

  it("foto's in één stad: eerst zoom 0, dan dieper tot het venster niet meer verandert", async () => {
    const s = server([
      [52.0907, 5.1214],
      [52.0921, 5.1185],
      [52.0855, 5.1302],
    ]);
    const rect = await homeRect(s.get, { place: 7 }, SIZE);
    expect(rect).not.toBeNull();
    if (!rect) return;
    // het stuk bevat alle foto's en is klein (een paar cellen van zoom ≥ 12)
    for (const [lat, lon] of [
      [52.0907, 5.1214],
      [52.0855, 5.1302],
    ]) {
      expect(contains(rect, { x0: lonToX(lon), y0: latToY(lat), x1: lonToX(lon), y1: latToY(lat) })).toBe(true);
    }
    expect(cellZoom(fitRect(rect, SIZE))).toBeGreaterThanOrEqual(12);
    expect(s.asked.length).toBeGreaterThanOrEqual(3);
    expect(s.asked.length).toBeLessThanOrEqual(6);
    expect(s.asked[0]).toBe(cellsPath(requestBBox(WORLD), 0, { place: 7 }));
    // elk verzoek met het filter, en elke zoom dieper dan de vorige
    const zooms = s.asked.map((p) => Number(new URL(p, "http://x").searchParams.get("z")));
    for (let i = 1; i < zooms.length; i++) expect(zooms[i]).toBeGreaterThan(zooms[i - 1]);
    for (const p of s.asked) expect(p).toContain("place=7");
  });

  it("foto's over de hele wereld: blijft op het grove stuk", async () => {
    const s = server([
      [52.09, 5.12],
      [-33.87, 151.21],
      [40.71, -74.0],
    ]);
    const rect = await homeRect(s.get, {}, SIZE);
    expect(rect).not.toBeNull();
    expect(s.asked.length).toBeLessThanOrEqual(2);
  });

  it("te veel cellen: het vorige, grovere stuk blijft", async () => {
    let calls = 0;
    const first = cellRect(2, 1, 0);
    const get = async (): Promise<MapCells> => {
      calls++;
      if (calls === 1) return { cells: [{ x: 2, y: 1, n: 9999, lat: 52, lon: 5, cover: "a" }] };
      return {
        cells: Array.from({ length: MAX_CELLS }, (_, i) => ({ x: 4000 + i, y: 3000, n: 1, lat: 52, lon: 5, cover: `c${i}` })),
      };
    };
    expect(await homeRect(get, {}, SIZE)).toEqual(first);
    expect(calls).toBe(2);
  });
});
