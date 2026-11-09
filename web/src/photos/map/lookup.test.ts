import { describe, expect, it } from "vitest";
import { Lookup, labelCovers, MAX_LABELS, PLACE_ZOOM, placeOf } from "./lookup";

describe("ophalen per omslag (thumbnails en plaatsnamen)", () => {
  it("haalt alleen wat nog niet bekend is, in stukken; wat ontbreekt is null", async () => {
    const asked: string[][] = [];
    let changes = 0;
    const l = new Lookup<string>(
      async (ids) => {
        asked.push(ids);
        return new Map(ids.filter((id) => id !== "weg").map((id) => [id, `t-${id}`]));
      },
      () => changes++,
      2,
      1,
    );
    expect(l.get("a")).toBeUndefined();
    await l.want(["a", "b", "a", "weg"]);
    expect(asked).toEqual([["a", "b"], ["weg"]]);
    expect(l.get("a")).toBe("t-a");
    expect(l.get("weg")).toBeNull();
    expect(changes).toBe(2);
    await l.want(["a", "b", "c"]);
    expect(asked).toEqual([["a", "b"], ["weg"], ["c"]]);
  });

  it("een fout of afbreken onthoudt niets: de volgende keer vraagt hij het opnieuw", async () => {
    let fail = true;
    const asked: string[] = [];
    const l = new Lookup<string>(
      async (ids) => {
        asked.push(...ids);
        if (fail) throw new Error("geen verbinding");
        return new Map(ids.map((id) => [id, id]));
      },
      () => {},
    );
    await l.want(["a"]);
    expect(l.get("a")).toBeUndefined();
    fail = false;
    await l.want(["a"]);
    expect(l.get("a")).toBe("a");
    expect(asked).toEqual(["a", "a"]);
  });

  it("na afbreken begint er geen nieuw stuk meer", async () => {
    const asked: string[] = [];
    const ctl = new AbortController();
    const l = new Lookup<string>(
      async (ids) => {
        asked.push(...ids);
        ctl.abort();
        return new Map(ids.map((id) => [id, id]));
      },
      () => {},
      1,
      1,
    );
    await l.want(["a", "b", "c"], ctl.signal);
    expect(asked).toEqual(["a"]);
    expect(l.get("b")).toBeUndefined();
  });

  it("plaatsnamen pas vanaf zoom 6, voor de grootste groepen, hooguit 16", () => {
    const dots = Array.from({ length: 20 }, (_, i) => ({ n: i + 1, cell: { cover: `c${i + 1}` } }));
    expect(labelCovers(dots, PLACE_ZOOM - 0.01)).toEqual([]);
    const covers = labelCovers(dots, PLACE_ZOOM);
    expect(covers).toHaveLength(MAX_LABELS);
    expect(covers.slice(0, 3)).toEqual(["c20", "c19", "c18"]);
    expect(covers).not.toContain("c4");
  });

  it("de plaats van een plek, of null", () => {
    expect(placeOf(null)).toBeNull();
    expect(placeOf({ lat: 1, lon: 2, alt: null, place: null })).toBeNull();
    const p = { name: "Utrecht", admin1: "Utrecht", cc: "NL" };
    expect(placeOf({ lat: 1, lon: 2, alt: null, place: p })).toBe(p);
  });
});
