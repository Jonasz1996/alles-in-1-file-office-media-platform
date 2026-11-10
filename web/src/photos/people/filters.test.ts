import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api";
import type { PhotoItem, PhotoPage } from "../api";
import { cursorOf, MAX_RANGE } from "../range";
import {
  cameraOrNull,
  dayOrNull,
  filterCount,
  filteredApi,
  filterQuery,
  hasFilters,
  MAX_CAMERA,
  NO_FILTERS,
  pagedIds,
  sameFilters,
  togglePerson,
} from "./filters";

function item(id: string, day = 20240601): PhotoItem {
  return {
    id,
    name: `${id}.jpg`,
    mime: "image/jpeg",
    size: 1,
    modified_at: "2024-06-01T12:00:00Z",
    media: "image",
    taken_at: "2024-06-01T12:00:00Z",
    taken_day: day,
    width: null,
    height: null,
    duration: null,
    etag: '"x"',
    thumb: null,
    large: null,
  };
}

afterEach(() => vi.restoreAllMocks());

describe("de filters op /fotos (Keuze 43)", () => {
  it("zonder filter niets in de querystring", () => {
    expect(hasFilters(NO_FILTERS)).toBe(false);
    expect(filterCount(NO_FILTERS)).toBe(0);
    expect(filterQuery(NO_FILTERS)).toBe("");
  });

  it("personen (EN), soort en periode met dezelfde namen als /search", () => {
    const f = { ...NO_FILTERS, person: ["p1", "p2"], type: "video" as const, from: "2024-01-01", to: "2024-12-31" };
    expect(hasFilters(f)).toBe(true);
    expect(filterCount(f)).toBe(4);
    expect(filterQuery(f)).toBe("?person=p1&person=p2&type=video&from=2024-01-01&to=2024-12-31");
    expect(filterQuery(f, { month: "2024-06", after: null, limit: 500 })).toBe(
      "?month=2024-06&limit=500&person=p1&person=p2&type=video&from=2024-01-01&to=2024-12-31",
    );
  });

  it("een periode achterstevoren wordt omgedraaid; een halve datum telt niet", () => {
    expect(filterQuery({ ...NO_FILTERS, from: "2024-12-31", to: "2024-01-01" })).toBe("?from=2024-01-01&to=2024-12-31");
    expect(dayOrNull("2024-06-01")).toBe("2024-06-01");
    expect(dayOrNull("2024-06")).toBeNull();
    expect(dayOrNull("")).toBeNull();
  });

  it("een persoon erbij en weer eraf; sameFilters kijkt naar de inhoud", () => {
    const een = togglePerson(NO_FILTERS, "p1");
    expect(een.person).toEqual(["p1"]);
    expect(togglePerson(een, "p1").person).toEqual([]);
    expect(sameFilters(een, { ...NO_FILTERS, person: ["p1"] })).toBe(true);
    expect(sameFilters(een, NO_FILTERS)).toBe(false);
    expect(sameFilters(NO_FILTERS, { ...NO_FILTERS, type: "image" })).toBe(false);
  });

  // I1 (S7): plaats, land en camera, met de namen van GET /search (de filters van Z3)
  it("plaats, land en camera: elk één filter, met dezelfde namen als /search", () => {
    const f = { ...NO_FILTERS, place: 2759794, country: "NL", camera: "fujifilm x-t30" };
    expect(hasFilters(f)).toBe(true);
    expect(filterCount(f)).toBe(3);
    expect(filterQuery(f)).toBe("?place=2759794&country=NL&camera=fujifilm+x-t30");
    expect(filterQuery({ ...f, person: ["p1"], type: "image" }, { month: "2024-06" })).toBe(
      "?month=2024-06&person=p1&type=image&place=2759794&country=NL&camera=fujifilm+x-t30",
    );
    for (const one of [{ place: 1 }, { country: "BE" }, { camera: "canon" }]) {
      const g = { ...NO_FILTERS, ...one };
      expect(hasFilters(g)).toBe(true);
      expect(filterCount(g)).toBe(1);
    }
    // Plaats 0 is een id als elk ander (geen "niets")
    expect(filterQuery({ ...NO_FILTERS, place: 0 })).toBe("?place=0");
    expect(hasFilters({ ...NO_FILTERS, place: 0 })).toBe(true);
  });

  it("sameFilters ziet een andere plaats, een ander land en een andere camera", () => {
    const f = { ...NO_FILTERS, place: 1, country: "NL", camera: "canon" };
    expect(sameFilters(f, { ...f })).toBe(true);
    expect(sameFilters(f, { ...f, place: 2 })).toBe(false);
    expect(sameFilters(f, { ...f, place: null })).toBe(false);
    expect(sameFilters(f, { ...f, country: "BE" })).toBe(false);
    expect(sameFilters(f, { ...f, camera: "canon eos" })).toBe(false);
    expect(sameFilters(NO_FILTERS, { ...NO_FILTERS, camera: "canon" })).toBe(false);
  });

  it("het veld Camera: witruimte samen en eromheen weg, leeg is geen filter, hooguit 200 tekens", () => {
    expect(cameraOrNull("  Canon   EOS\t5D ")).toBe("Canon EOS 5D");
    expect(cameraOrNull("")).toBeNull();
    expect(cameraOrNull(" \n\t ")).toBeNull();
    expect(MAX_CAMERA).toBe(200);
    expect(cameraOrNull("x".repeat(250))).toBe("x".repeat(MAX_CAMERA));
    // Afgekapt op een spatie: geen spatie op het eind
    expect(cameraOrNull(`${"x".repeat(199)} y`)).toBe("x".repeat(199));
  });

  it("met filters vraagt de tijdlijn /photos en /photos/months met de filters", async () => {
    const calls: string[] = [];
    vi.spyOn(api, "request").mockImplementation(async (_m: string, path: string) => {
      calls.push(path);
      return (path.includes("months") ? { months: [] } : { items: [], next: null }) as never;
    });
    const f = { ...NO_FILTERS, person: ["p1"] };
    await filteredApi.months(f);
    await filteredApi.page(f, "2024-06", null);
    await filteredApi.months(NO_FILTERS);
    const g = { ...NO_FILTERS, country: "NL", camera: "canon" };
    await filteredApi.months(g);
    await filteredApi.page(g, "2024-06", null);
    expect(calls).toEqual([
      "/api/v1/photos/months?person=p1",
      "/api/v1/photos?month=2024-06&limit=500&person=p1",
      "/api/v1/photos/months",
      "/api/v1/photos/months?country=NL&camera=canon",
      "/api/v1/photos?month=2024-06&limit=500&country=NL&camera=canon",
    ]);
  });
});

describe("een reeks over een gefilterde tijdlijn (pagedIds)", () => {
  it("bladert vanaf a tot en met b", async () => {
    const all = ["a", "b", "c", "d", "e"].map((id) => item(id));
    const seen: string[] = [];
    const page = async (after: string): Promise<PhotoPage> => {
      seen.push(after);
      const start = all.findIndex((it) => cursorOf(it) === after) + 1;
      const items = all.slice(start, start + 2);
      const last = items[items.length - 1];
      return { items, next: start + 2 < all.length && last ? cursorOf(last) : null };
    };
    expect(await pagedIds(page, all[0], all[3])).toEqual({ ids: ["a", "b", "c", "d"], truncated: false });
    expect(seen).toEqual([cursorOf(all[0]), cursorOf(all[2])]);
    expect(await pagedIds(page, all[2], all[2])).toEqual({ ids: ["c"], truncated: false });
  });

  it("stopt bij MAX_RANGE", async () => {
    let n = 0;
    const page = async (): Promise<PhotoPage> => ({
      items: Array.from({ length: 500 }, () => item(`x${n++}`)),
      next: "verder",
    });
    const got = await pagedIds(page, item("start"), item("nooit"));
    expect(got.truncated).toBe(true);
    expect(got.ids).toHaveLength(MAX_RANGE);
  });
});
