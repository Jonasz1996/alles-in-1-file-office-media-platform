import { describe, expect, it } from "vitest";
import {
  cellRect,
  cellsAcross,
  cellsBounds,
  coverRect,
  dotRadius,
  type Loaded,
  needsCells,
  placeDots,
  requestBBox,
} from "./cells";
import { latToY, lonToX } from "./projection";
import type { MapCell } from "./types";
import { contains, toScreen, visibleRect, WORLD } from "./view";

const SIZE = { w: 800, h: 600 };

/** De cel van een plek op zoom z, zoals de server hem rekent: (x16 >> (14 − z), y16 >> (14 − z)). */
function cellOf(lat: number, lon: number, z: number): { x: number; y: number } {
  const x16 = Math.floor(lonToX(lon) * 65536);
  const y16 = Math.floor(latToY(lat) * 65536);
  return { x: x16 >> (14 - z), y: y16 >> (14 - z) };
}

function cell(lat: number, lon: number, z: number, n: number, cover = `f${n}`): MapCell {
  return { ...cellOf(lat, lon, z), n, lat, lon, cover };
}

describe("de cellen van de server (Keuze 32)", () => {
  it("vier cellen per tegel: 2^(z+2) over de wereld, 64 pixels breed op hun eigen zoom", () => {
    expect(cellsAcross(0)).toBe(4);
    expect(cellsAcross(14)).toBe(65536);
    const r = cellRect(1, 2, 0);
    expect(r).toEqual({ x0: 0.25, y0: 0.5, x1: 0.5, y1: 0.75 });
    expect((r.x1 - r.x0) * 256).toBe(64);
    // de cel van een plek bevat die plek
    const utrecht = cellOf(52.0907, 5.1214, 9);
    const rect = cellRect(utrecht.x, utrecht.y, 9);
    expect(contains(rect, { x0: lonToX(5.1214), y0: latToY(52.0907), x1: lonToX(5.1214), y1: latToY(52.0907) })).toBe(true);
  });

  it("wat de kaart vraagt: het venster met een kwart extra, op hele cellen", () => {
    const v = { x: 0.52, y: 0.33, zoom: 8.4 };
    const z = 8;
    const r = coverRect(v, SIZE, z);
    const n = cellsAcross(z);
    for (const edge of [r.x0, r.y0, r.x1, r.y1]) expect(Math.abs(edge * n - Math.round(edge * n))).toBeLessThan(1e-9);
    const seen = visibleRect(v, SIZE);
    expect(contains(r, seen)).toBe(true);
    // een kwart van het venster aan elke kant, plus de afronding op hele cellen
    const pad = (seen.x1 - seen.x0) / 4;
    expect(r.x0).toBeLessThanOrEqual(seen.x0 - pad + 1e-12);
    expect(r.x0).toBeGreaterThan(seen.x0 - pad - 1 / n);
    // zonder rand: alleen wat in beeld is
    const tight = coverRect(v, SIZE, z, 0);
    expect(contains(tight, seen)).toBe(true);
    expect(tight.x1 - tight.x0).toBeLessThan(r.x1 - r.x0);
    // nooit voorbij de wereld
    expect(coverRect({ x: 0.5, y: 0.5, zoom: 2 }, SIZE, 2)).toEqual(WORLD);
  });

  it("de bbox ligt een halve tegel van zoom 16 binnen het stuk: de server neemt precies die cellen", () => {
    const r = cellRect(33700 >> 6, 21620 >> 6, 8);
    const [w, s, e, n] = requestBBox(r);
    expect(w).toBeLessThan(e);
    expect(s).toBeLessThan(n);
    // de randen vallen in de eerste en de laatste tegel van zoom 16 van het stuk
    const x16w = Math.floor(lonToX(w) * 65536);
    const x16e = Math.floor(lonToX(e) * 65536);
    const y16n = Math.floor(latToY(n) * 65536);
    const y16s = Math.floor(latToY(s) * 65536);
    expect(x16w).toBe(Math.round(r.x0 * 65536));
    expect(x16e).toBe(Math.round(r.x1 * 65536) - 1);
    expect(y16n).toBe(Math.round(r.y0 * 65536));
    expect(y16s).toBe(Math.round(r.y1 * 65536) - 1);
  });

  it("nieuwe cellen alleen als het venster buiten het geladen stuk komt, de zoom of de filters anders zijn", () => {
    const v = { x: 0.52, y: 0.33, zoom: 8.4 };
    const loaded: Loaded = { rect: coverRect(v, SIZE, 8), z: 8, filters: "" };
    expect(needsCells(null, v, SIZE, 8, "")).toBe(true);
    expect(needsCells(loaded, v, SIZE, 8, "")).toBe(false);
    // een beetje schuiven: nog binnen de rand
    expect(needsCells(loaded, { ...v, x: v.x + 0.0005 }, SIZE, 8, "")).toBe(false);
    // ver schuiven: wel
    expect(needsCells(loaded, { ...v, x: v.x + 0.01 }, SIZE, 8, "")).toBe(true);
    expect(needsCells(loaded, v, SIZE, 9, "")).toBe(true);
    expect(needsCells(loaded, v, SIZE, 8, "place=3")).toBe(true);
  });

  it("de stippen: alleen wat (half) in beeld is, in leesvolgorde, met een vaste sleutel", () => {
    const z = 6;
    const v = { x: lonToX(5), y: latToY(52), zoom: 6.5 };
    const cells = [
      cell(52.37, 4.9, z, 120, "amsterdam"),
      cell(51.92, 4.48, z, 30, "rotterdam"),
      cell(53.22, 6.57, z, 1, "groningen"),
      cell(40.71, -74.0, z, 9, "newyork"),
    ];
    const dots = placeDots(cells, z, v, SIZE);
    expect(dots.map((d) => d.cell.cover)).toEqual(["groningen", "amsterdam", "rotterdam"]);
    const ams = dots[1];
    expect(ams.key).toBe(`6/${cells[0].x}/${cells[0].y}`);
    expect(ams.r).toBe(dotRadius(120));
    expect(ams.n).toBe(120);
    expect(ams.rect).toEqual(cellRect(cells[0].x, cells[0].y, z));
    const [sx, sy] = [ams.sx, ams.sy];
    expect(sx).toBeGreaterThan(0);
    expect(sx).toBeLessThan(SIZE.w);
    expect(sy).toBeGreaterThan(0);
    expect(sy).toBeLessThan(SIZE.h);
    // een stip net buiten de rand telt mee zolang een stuk ervan in beeld is
    const edge = { ...cells[0], lon: 5 + 0.0001, lat: 52 };
    const atEdge = placeDots([edge], z, { ...v, x: v.x + (SIZE.w / 2 + 10) / (256 * 2 ** 6.5) }, SIZE);
    expect(atEdge).toHaveLength(1);
  });

  it("twee cellen die bijna op dezelfde plek vallen, zijn één stip met alle foto's en beide cellen", () => {
    // Amsterdam op de grens van twee cellen van zoom 14: 3 en 2 foto's, 110 m uit elkaar
    const z = 14;
    const a = cell(52.3762, 4.8936, z, 3, "ams-noord");
    const b = { ...cell(52.3752, 4.8936, z, 2, "ams-zuid") };
    expect(b.y).not.toBe(a.y);
    const v = { x: lonToX(4.8936), y: latToY(52.3757), zoom: 12 };
    const dots = placeDots([b, a], z, v, SIZE);
    expect(dots).toHaveLength(1);
    const [d] = dots;
    expect(d.cell.cover).toBe("ams-noord");
    expect(d.key).toBe(`14/${a.x}/${a.y}`);
    expect(d.n).toBe(5);
    expect(d.r).toBe(dotRadius(5));
    expect(d.rect).toEqual({
      x0: Math.min(cellRect(a.x, a.y, z).x0, cellRect(b.x, b.y, z).x0),
      y0: cellRect(a.x, a.y, z).y0,
      x1: Math.max(cellRect(a.x, a.y, z).x1, cellRect(b.x, b.y, z).x1),
      y1: cellRect(b.x, b.y, z).y1,
    });
    // het midden ligt tussen de twee, dichter bij de grootste (3 tegen 2)
    const [ax, ay] = toScreen(v, SIZE, lonToX(a.lon), latToY(a.lat));
    const [, by] = toScreen(v, SIZE, lonToX(b.lon), latToY(b.lat));
    expect(d.sx).toBeCloseTo(ax, 6);
    expect(d.sy).toBeCloseTo(ay + (by - ay) * 0.4, 6);
    // ver genoeg ingezoomd staan ze los
    const close = placeDots([b, a], z, { ...v, zoom: 17 }, SIZE);
    expect(close.map((x) => [x.cell.cover, x.n])).toEqual([
      ["ams-noord", 3],
      ["ams-zuid", 2],
    ]);
  });

  it("een losse foto is een stip van 18 px; een groep groeit met het aantal, tot 34", () => {
    expect(dotRadius(1)).toBe(18);
    expect(dotRadius(10)).toBe(20);
    expect(dotRadius(1000)).toBe(28);
    expect(dotRadius(10 ** 9)).toBe(34);
  });

  it("het stuk van alle cellen samen (voor Home), of null zonder cellen", () => {
    expect(cellsBounds([], 3)).toBeNull();
    expect(cellsBounds([{ x: 1, y: 2 }, { x: 3, y: 0 }], 0)).toEqual({ x0: 0.25, y0: 0, x1: 1, y1: 0.75 });
  });
});
