import { describe, expect, it } from "vitest";
import { latToY, lonToX } from "./projection";
import {
  cellZoom,
  clampView,
  contains,
  FIT_MAX_ZOOM,
  fitRect,
  MAX_VIEW_ZOOM,
  minZoom,
  panBy,
  rectBBox,
  toScreen,
  toWorld,
  visibleRect,
  WORLD,
  zoomAround,
} from "./view";

const SIZE = { w: 800, h: 600 };

describe("het venster van de kaart", () => {
  it("de kleinste zoom laat de wereld het venster vullen", () => {
    expect(minZoom({ w: 256, h: 100 })).toBe(0);
    expect(minZoom({ w: 200, h: 100 })).toBe(0);
    expect(minZoom({ w: 1024, h: 600 })).toBe(2);
    expect(clampView({ x: 0.5, y: 0.5, zoom: 0 }, { w: 1024, h: 600 }).zoom).toBe(2);
    expect(clampView({ x: 0.5, y: 0.5, zoom: 40 }, SIZE).zoom).toBe(MAX_VIEW_ZOOM);
  });

  it("geen leeg stuk naast de wereld: het midden blijft binnen de randen", () => {
    const v = clampView({ x: 0.001, y: 0.999, zoom: 3 }, SIZE);
    const r = visibleRect(v, SIZE);
    expect(r.x0).toBe(0);
    expect(r.y1).toBe(1);
    expect(v.x).toBeCloseTo(400 / 2048, 12);
    expect(v.y).toBeCloseTo(1 - 300 / 2048, 12);
    // kapotte getallen worden het midden
    expect(clampView({ x: Number.NaN, y: 0.5, zoom: Number.NaN }, SIZE)).toEqual(clampView({ x: 0.5, y: 0.5, zoom: 0 }, SIZE));
  });

  it("naar het scherm en terug", () => {
    const v = { x: 0.52, y: 0.33, zoom: 7.5 };
    const [sx, sy] = toScreen(v, SIZE, 0.521, 0.329);
    const [x, y] = toWorld(v, SIZE, sx, sy);
    expect(x).toBeCloseTo(0.521, 12);
    expect(y).toBeCloseTo(0.329, 12);
    expect(toScreen(v, SIZE, v.x, v.y)).toEqual([400, 300]);
  });

  it("slepen schuift de wereld mee met de muis", () => {
    const v = { x: 0.5, y: 0.5, zoom: 4 };
    const moved = panBy(v, SIZE, 100, -50);
    // het punt dat onder (400, 300) stond, staat nu onder (500, 250)
    expect(toScreen(moved, SIZE, 0.5, 0.5)).toEqual([500, 250]);
  });

  it("zoomen rond een punt laat dat punt staan", () => {
    const v = { x: 0.5, y: 0.4, zoom: 5 };
    const [wx, wy] = toWorld(v, SIZE, 650, 120);
    const z = zoomAround(v, SIZE, 7.25, 650, 120);
    expect(z.zoom).toBe(7.25);
    const [sx, sy] = toScreen(z, SIZE, wx, wy);
    expect(sx).toBeCloseTo(650, 9);
    expect(sy).toBeCloseTo(120, 9);
    expect(zoomAround(v, SIZE, 99, 0, 0).zoom).toBe(MAX_VIEW_ZOOM);
  });

  it("passend maken: het stuk staat helemaal in beeld, hooguit op zoom 15", () => {
    const nl = { x0: lonToX(3.3), y0: latToY(53.6), x1: lonToX(7.3), y1: latToY(50.7) };
    const v = fitRect(nl, SIZE);
    expect(contains(visibleRect(v, SIZE), nl)).toBe(true);
    // en niet verder weg dan nodig: een kwart stap dichterbij past het niet meer
    expect(contains(visibleRect({ ...v, zoom: v.zoom + 0.25 }, SIZE), nl)).toBe(false);
    expect(v.zoom).toBeCloseTo(7.3166, 3);
    const dot = { x0: 0.5, y0: 0.5, x1: 0.5, y1: 0.5 };
    expect(fitRect(dot, SIZE).zoom).toBe(FIT_MAX_ZOOM);
    expect(fitRect(WORLD, SIZE).zoom).toBe(minZoom(SIZE));
  });

  it("de zoom van de cellen: naar beneden afgerond, 0 tot 14", () => {
    expect(cellZoom({ x: 0, y: 0, zoom: 0 })).toBe(0);
    expect(cellZoom({ x: 0, y: 0, zoom: 6.99 })).toBe(6);
    expect(cellZoom({ x: 0, y: 0, zoom: 14 })).toBe(14);
    expect(cellZoom({ x: 0, y: 0, zoom: 16.5 })).toBe(14);
  });

  it("een stuk als bbox: west, zuid, oost, noord in zes decimalen", () => {
    expect(rectBBox(WORLD)).toEqual([-180, -85.051129, 180, 85.051129]);
    expect(rectBBox({ x0: 0.5, y0: 0.5, x1: 0.75, y1: 0.75 })).toEqual([0, -66.51326, 90, 0]);
  });
});
