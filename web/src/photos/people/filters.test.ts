import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api";
import type { PhotoItem, PhotoPage } from "../api";
import { cursorOf, MAX_RANGE } from "../range";
import {
  dayOrNull,
  filterCount,
  filteredApi,
  filterQuery,
  hasFilters,
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
    const f = { person: ["p1", "p2"], type: "video" as const, from: "2024-01-01", to: "2024-12-31" };
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
    expect(calls).toEqual([
      "/api/v1/photos/months?person=p1",
      "/api/v1/photos?month=2024-06&limit=500&person=p1",
      "/api/v1/photos/months",
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
