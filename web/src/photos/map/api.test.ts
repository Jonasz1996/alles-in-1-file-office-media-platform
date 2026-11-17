import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api";
import mapCatalog from "../../i18n/errors/photos_map";
import * as map from "./api";
import { PHOTOS_MAP_ERROR_CODES } from "./types";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("de aanroepen van plek, kaart, plaatsen en delen (§3.4, Z3)", () => {
  it("elke functie gebruikt de methode en het pad van het contract", async () => {
    const request = vi.spyOn(api, "request").mockResolvedValue({});
    await map.fileLocation("f/1");
    await map.mapCells([-10.5, 35, 30, 60], 4);
    await map.mapCells([4, 52, 5, 53], 14, { type: ["image"], person: ["p1"], from: "2024-06-01" });
    await map.mapItems([4, 52, 5, 53]);
    await map.mapItems([4, 52, 5, 53], { country: "NL" }, "c2");
    await map.places();
    await map.places({ person: ["p1", "p2"] });
    await map.shareCheck("map1");
    await map.geoAttribution();

    const V = "/api/v1";
    expect(request.mock.calls).toEqual([
      ["GET", `${V}/files/f%2F1/location`],
      ["GET", `${V}/photos/map?bbox=-10.5%2C35%2C30%2C60&z=4`],
      ["GET", `${V}/photos/map?bbox=4%2C52%2C5%2C53&z=14&type=image&person=p1&from=2024-06-01`],
      ["GET", `${V}/photos/map/items?bbox=4%2C52%2C5%2C53`],
      ["GET", `${V}/photos/map/items?bbox=4%2C52%2C5%2C53&country=NL&cursor=c2`],
      ["GET", `${V}/photos/places`],
      ["GET", `${V}/photos/places?person=p1&person=p2`],
      ["GET", `${V}/photos/share-check?node=map1`],
      ["GET", `${V}/geo/attribution`],
    ]);
  });

  it("bbox is w,s,e,n", () => {
    expect(map.bboxParam([-180, -85, 180, 85])).toBe("-180,-85,180,85");
  });

  it("de catalogus photos_map.ts heeft precies de foutcodes van kaart en duplicaten", () => {
    expect(Object.keys(mapCatalog).sort()).toEqual([...PHOTOS_MAP_ERROR_CODES].sort());
    for (const { en } of Object.values(mapCatalog)) expect(en).not.toMatch(/[{}]/);
  });
});
