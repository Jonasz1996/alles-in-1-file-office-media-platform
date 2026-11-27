import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api";
import { fileLocation, mapCells, mapItems } from "./api";
import { cellsPath, getCells, getItems, getLocation, itemsPath, locationPath } from "./requests";
import type { BBox } from "./types";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("de verzoeken die afgebroken kunnen worden: dezelfde paden als ./api.ts (Z0, bevroren)", () => {
  it("cellen, foto's en de plek: precies het pad van mapCells, mapItems en fileLocation", async () => {
    const request = vi.spyOn(api, "request").mockResolvedValue({});
    const bbox: BBox = [4.25, 51.5, 5.75, 52.625];
    const filters = { place: 2759794, type: ["image" as const] };
    await mapCells(bbox, 9, filters);
    await mapItems(bbox, filters, "c1");
    await mapItems(bbox);
    await fileLocation("f/1");
    const frozen = request.mock.calls.map((c) => c[1]);
    expect([cellsPath(bbox, 9, filters), itemsPath(bbox, filters, "c1"), itemsPath(bbox), locationPath("f/1")]).toEqual(
      frozen,
    );
  });

  it("met een AbortSignal via api.request", async () => {
    const request = vi.spyOn(api, "request").mockResolvedValue({ cells: [] });
    const ctl = new AbortController();
    await getCells("/api/v1/photos/map?bbox=0%2C0%2C1%2C1&z=3", ctl.signal);
    await getItems("/api/v1/photos/map/items?bbox=0%2C0%2C1%2C1", ctl.signal);
    await getLocation("f1", ctl.signal);
    expect(request.mock.calls).toEqual([
      ["GET", "/api/v1/photos/map?bbox=0%2C0%2C1%2C1&z=3", undefined, { signal: ctl.signal }],
      ["GET", "/api/v1/photos/map/items?bbox=0%2C0%2C1%2C1", undefined, { signal: ctl.signal }],
      ["GET", "/api/v1/files/f1/location", undefined, { signal: ctl.signal }],
    ]);
  });

  it("alleen naar de app zelf: elk pad begint met /api/v1/", () => {
    for (const p of [cellsPath([0, 0, 1, 1], 0), itemsPath([0, 0, 1, 1]), locationPath("x")]) {
      expect(p.startsWith("/api/v1/")).toBe(true);
    }
  });
});
