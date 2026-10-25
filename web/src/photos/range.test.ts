import { describe, expect, it, vi } from "vitest";
import type { PhotoItem, PhotoPage } from "./api";
import { cursorOf, NO_SELECTION, type Placed, rangeIds, toggled, withRange } from "./range";
import { SectionStore } from "./store";

function photo(k: number): PhotoItem {
  const day = 20240700 + 30 - Math.floor(k / 4);
  return {
    id: `p${k}`,
    name: `foto-${k}.jpg`,
    mime: "image/jpeg",
    size: 1,
    modified_at: "2024-07-01T00:00:00Z",
    media: "image",
    taken_at: `2024-07-${String(day % 100).padStart(2, "0")}T12:00:00Z`,
    taken_day: day,
    width: null,
    height: null,
    duration: null,
    etag: '"x"',
    thumb: null,
    large: null,
  };
}

/** Drie "maanden" van 4 foto's; de server kent alles. */
const all = Array.from({ length: 12 }, (_, k) => photo(k));
const pageOf = (s: number): PhotoPage => ({ items: all.slice(s * 4, s * 4 + 4), next: null });

describe("cursor", () => {
  it("base64url van dag|tijd|id, zonder =", () => {
    const c = cursorOf({ id: "abc", taken_day: 20240701, taken_at: "2024-07-01T12:00:00Z" });
    expect(c).not.toMatch(/[=+/]/);
    const raw = atob(c.replace(/-/g, "+").replace(/_/g, "/"));
    expect(raw).toBe("20240701|2024-07-01T12:00:00Z|abc");
  });
});

describe("een reeks selecteren", () => {
  it("alles ertussen geladen: zonder de server", async () => {
    const store = new SectionStore(async (s) => pageOf(s), [4, 4, 4]);
    await store.load(0);
    await store.load(1);
    const fetchIds = vi.fn();
    const r = await rangeIds({ item: (i) => store.item(i), load: async () => undefined, fetchIds }, 6, 1);
    expect(r).toEqual({ ids: ["p1", "p2", "p3", "p4", "p5", "p6"], truncated: false });
    expect(fetchIds).not.toHaveBeenCalled();
  });

  it("over een maand die nog niet geladen is: via /photos/ids met de cursors van de uiteinden", async () => {
    const store = new SectionStore(async (s) => pageOf(s), [4, 4, 4]);
    await store.load(0);
    await store.load(2);
    const fetchIds = vi.fn(async (a: Placed, b: Placed) => {
      const from = all.findIndex((p) => p.id === a.id);
      const to = all.findIndex((p) => p.id === b.id);
      return { ids: all.slice(from, to + 1).map((p) => p.id), truncated: false };
    });
    const r = await rangeIds(
      { item: (i) => store.item(i), load: async () => undefined, fetchIds },
      10,
      2, // van maand 0 tot maand 2; maand 1 is niet geladen
    );
    expect(r.ids).toEqual(all.slice(2, 11).map((p) => p.id));
    expect(fetchIds).toHaveBeenCalledWith(all[2], all[10]);
    expect(store.isLoaded(1)).toBe(false); // niet de hele maand ervoor geladen
  });

  it("een uiteinde dat nog niet geladen is (Shift+End): eerst die maand", async () => {
    const store = new SectionStore(async (s) => pageOf(s), [4, 4, 4]);
    await store.load(0);
    const load = vi.fn(async (i: number) => {
      await store.load(Math.floor(i / 4));
      return store.item(i);
    });
    const fetchIds = vi.fn(async () => ({ ids: all.slice(0, 12).map((p) => p.id), truncated: true }));
    const r = await rangeIds({ item: (i) => store.item(i), load, fetchIds }, 0, 11);
    expect(load).toHaveBeenCalledWith(11);
    expect(fetchIds).toHaveBeenCalledWith(all[0], all[11]);
    expect(r.truncated).toBe(true);
  });

  it("met de uiteinden geladen is alles binnen (een album): geen server", async () => {
    const store = new SectionStore(async () => ({ items: all, next: null }), [12]);
    const fetchIds = vi.fn();
    const r = await rangeIds(
      { item: (i) => store.item(i), load: async (i) => (await store.load(0))[i], fetchIds },
      3,
      8,
    );
    expect(r.ids).toEqual(all.slice(3, 9).map((p) => p.id));
    expect(fetchIds).not.toHaveBeenCalled();
  });

  it("Ctrl/Cmd en Shift op de selectie", () => {
    let s = toggled(NO_SELECTION, 3, "p3");
    expect([...s.ids]).toEqual(["p3"]);
    expect(s.anchor).toBe(3);
    s = withRange(s, ["p5", "p6"], true);
    expect([...s.ids].sort()).toEqual(["p3", "p5", "p6"]);
    expect(withRange(s, ["p1"], false).ids).toEqual(new Set(["p1"]));
    expect(toggled(s, 3, "p3").ids.has("p3")).toBe(false);
  });
});

describe("de store per maand", () => {
  it("laadt alleen wat in beeld komt, hooguit twee tegelijk, en alle pagina's", async () => {
    const calls: [number, string | null][] = [];
    const store = new SectionStore(
      async (s, after) => {
        calls.push([s, after]);
        if (s === 0 && after === null) return { items: all.slice(0, 2), next: "c" };
        return { items: s === 0 ? all.slice(2, 4) : pageOf(s).items, next: null };
      },
      [4, 4, 4],
    );
    store.want([0, 1, 2]);
    await vi.waitFor(() => expect(store.isLoaded(2)).toBe(true));
    expect(calls).toEqual([
      [0, null],
      [1, null],
      [0, "c"],
      [2, null],
    ]);
    expect(store.item(3)?.id).toBe("p3");
    expect(store.indexOf("p9")).toBe(9);
  });

  it("een ander aantal dan gedacht meldt hij; verouderde maanden blijven staan tot het nieuwe er is", async () => {
    let version = 0;
    const store = new SectionStore(async (s) => ({ items: pageOf(s).items.map((p) => ({ ...p, name: `v${version}` })), next: null }), [4, 3]);
    const mismatch = vi.fn();
    store.onMismatch = mismatch;
    await store.load(1);
    expect(mismatch).toHaveBeenCalledWith(1, 4);
    await store.load(0);
    version = 1;
    store.setSections([4, 4], undefined, (s) => s);
    expect(store.item(0)?.name).toBe("v0"); // nog het oude
    store.want([0]);
    await vi.waitFor(() => expect(store.item(0)?.name).toBe("v1"));
  });
});
