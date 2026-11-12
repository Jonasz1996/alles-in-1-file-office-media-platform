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

  it("een maand met een ander aantal: de oude items blijven vast te houden tot hij opnieuw geladen is", async () => {
    // De viewer houdt zo de foto vast die open is (browse.tsx), ook als er in die maand een foto
    // bij komt of af gaat; pas als de maand opnieuw geladen is zonder hem, is hij echt weg.
    let server = all.slice(0, 8);
    let fail = false;
    const store = new SectionStore(async (s) => {
      if (fail) throw new Error("offline");
      return { items: server.slice(s * 4, s * 4 + 4), next: null };
    }, [4, 4]);
    await store.load(0);
    await store.load(1);
    expect(store.held("p1")).toBeNull(); // gewoon geladen

    // Er komt een foto bij in maand 0, en het opnieuw laden mislukt eerst
    server = [photo(99), ...all.slice(0, 8)];
    fail = true;
    store.setSections([5, 4], undefined, (s) => s);
    expect(store.indexOf("p1")).toBe(-1);
    expect(store.held("p1")).toEqual({ section: 0, items: all.slice(0, 4) });
    await expect(store.load(0)).rejects.toThrow("offline");
    expect(store.held("p1")?.section).toBe(0); // mislukt: nog steeds vast
    // Nog een wijziging voor maand 0 is geladen: de oude items gaan mee
    store.setSections([5, 4], undefined, (s) => s);
    expect(store.held("p1")?.section).toBe(0);

    fail = false;
    server = [photo(99), ...all.slice(0, 1), ...all.slice(2, 8)]; // p1 is intussen weg
    store.setSections([4, 4], undefined, (s) => s);
    await store.load(0);
    expect(store.indexOf("p0")).toBe(1);
    expect(store.held("p1")).toBeNull(); // opnieuw geladen zonder hem: echt weg
  });

  /** Een server die pas antwoordt als de test dat zegt; calls: [maand, after] per verzoek. */
  function slowServer(months: { current: string[] }) {
    const calls: [string, string | null][] = [];
    const waiting: ((page: PhotoPage) => void)[] = [];
    const fetch = (s: number, after: string | null) => {
      calls.push([months.current[s], after]);
      return new Promise<PhotoPage>((resolve) => waiting.push(resolve));
    };
    return { calls, waiting, fetch };
  }

  it("de maanden steeds opnieuw gelezen terwijl een maand laadt: hij komt toch in beeld", async () => {
    // Een reeks preview.ready-events leest de maanden om de halve seconde opnieuw. Duurde een
    // maand langer, dan gooide elk setSections zijn antwoord weg en bleef de tijdlijn op Laden…
    const months = { current: ["2024-07"] };
    const server = slowServer(months);
    const store = new SectionStore(server.fetch, [4]);
    store.want([0]);
    for (let k = 0; k < 3; k++) store.setSections([4], undefined, (s) => s);
    expect(server.calls).toEqual([["2024-07", null]]); // het verzoek loopt door, geen tweede ernaast
    server.waiting[0]({ items: all.slice(0, 4), next: null });
    await vi.waitFor(() => expect(store.isLoaded(0)).toBe(true));
    expect(store.item(3)?.id).toBe("p3");
    // Het begon vóór het opnieuw lezen, dus verouderd: nog een keer van de server, en tot dan
    // blijft het staan, ook als de maanden weer opnieuw gelezen worden
    await vi.waitFor(() => expect(server.calls).toHaveLength(2));
    store.setSections([4], undefined, (s) => s);
    expect(store.item(3)?.id).toBe("p3");
  });

  it("een maand die laadt en een andere plek krijgt: de volgende pagina is van dezelfde maand", async () => {
    const months = { current: ["2024-07"] };
    const server = slowServer(months);
    const store = new SectionStore(server.fetch, [4]);
    store.want([0]);
    // Een nieuwe maand vooraan; juli is nu sectie 1 en houdt zijn aantal
    months.current = ["2024-08", "2024-07"];
    store.setSections([1, 4], undefined, (s) => s - 1);
    server.waiting[0]({ items: all.slice(0, 2), next: "c" });
    await vi.waitFor(() => expect(server.calls).toContainEqual(["2024-07", "c"]));
    server.waiting[server.calls.findIndex((c) => c[1] === "c")]({ items: all.slice(2, 4), next: null });
    await vi.waitFor(() => expect(store.isLoaded(1)).toBe(true));
    expect(store.section(1)?.map((it) => it.id)).toEqual(["p0", "p1", "p2", "p3"]);
    expect(store.isLoaded(0)).toBe(false); // augustus laadt apart
  });

  it("een maand die laadt en een ander aantal krijgt: dat antwoord telt niet meer", async () => {
    const months = { current: ["2024-07"] };
    const server = slowServer(months);
    const store = new SectionStore(server.fetch, [4]);
    store.want([0]);
    store.setSections([5], undefined, (s) => s);
    expect(server.calls).toHaveLength(2); // meteen opnieuw, het oude antwoord klopt niet meer
    server.waiting[0]({ items: all.slice(0, 4), next: null });
    await new Promise((resolve) => setTimeout(resolve));
    expect(store.isLoaded(0)).toBe(false);
    server.waiting[1]({ items: [photo(99), ...all.slice(0, 4)], next: null });
    await vi.waitFor(() => expect(store.isLoaded(0)).toBe(true));
    expect(store.item(0)?.id).toBe("p99");
  });
});
