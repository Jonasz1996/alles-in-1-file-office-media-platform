import { describe, expect, it } from "vitest";
import { searchTarget } from "./appBar";
import {
  canSearch,
  chips,
  firstParams,
  hasFilter,
  isDay,
  nextParams,
  type PageQuery,
  queryHref,
  readQuery,
  withDay,
  withMode,
  withoutChip,
  withTag,
  withType,
} from "./query";
import { DEFAULT_LIMIT, MAX_LIMIT, MAX_OFFSET, type SearchPage } from "./types";
import { wireQuery } from "./wire";

const P1 = "4f1c0b2e-0000-4000-8000-000000000001";
const P2 = "4f1c0b2e-0000-4000-8000-000000000002";
const T1 = "7a7a7a7a-1111-4111-8111-111111111111";
const T2 = "7a7a7a7a-2222-4222-8222-222222222222";
const MAP = "0b0b0b0b-3333-4333-8333-333333333333";
const LIB = "0c0c0c0c-4444-4444-8444-444444444444";

function page(over: Partial<SearchPage> = {}): SearchPage {
  return {
    results: [],
    more: false,
    offset: 0,
    partial: false,
    ranked: true,
    names_only: false,
    after: null,
    groups: null,
    indexing: null,
    engine: "fts",
    mode: "text",
    fallback: null,
    ...over,
  };
}

describe("de URL van /zoeken heen en terug (Keuze 43 en 47)", () => {
  it("alle filters, meer keer waar dat mag, in een vaste volgorde", () => {
    const query: PageQuery = {
      q: "café menu",
      type: ["pdf", "document"],
      from: "2024-01-01",
      to: "2024-12-31",
      person: [P1, P2],
      place: 2759794,
      tag: [T1, T2],
      library: LIB,
      camera: "fujifilm x100v",
      in: MAP,
    };
    const href = queryHref(query);
    expect(href).toBe(
      `/zoeken?q=caf%C3%A9+menu&type=pdf&type=document&from=2024-01-01&to=2024-12-31&person=${P1}&person=${P2}` +
        `&place=2759794&tag=${T1}&tag=${T2}&library=${LIB}&camera=fujifilm+x100v&in=${MAP}`,
    );
    expect(readQuery(href.slice("/zoeken".length))).toEqual(query);
    // Een andere volgorde in de URL geeft dezelfde vraag, en dan weer de vaste URL
    const shuffled = `?in=${MAP}&type=pdf&q=caf%C3%A9+menu&tag=${T1}&camera=fujifilm+x100v&person=${P1}&to=2024-12-31&type=document&from=2024-01-01&library=${LIB}&person=${P2}&place=2759794&tag=${T2}`;
    expect(queryHref(readQuery(shuffled))).toBe(href);
  });

  it("de stand beeld (Keuze 81): mode=image, met like; zonder mode is het tekst", () => {
    const beeld: PageQuery = { q: "hond op het strand", mode: "image" };
    expect(queryHref(beeld)).toBe("/zoeken?q=hond+op+het+strand&mode=image");
    expect(readQuery("?q=hond+op+het+strand&mode=image")).toEqual(beeld);
    expect(readQuery(`?mode=image&like=${P1}`)).toEqual({ mode: "image", like: P1 });
    expect(queryHref({ mode: "image", like: P1 })).toBe(`/zoeken?mode=image&like=${P1}`);
    // like alleen in de stand beeld; mode text of iets anders is de standaard
    expect(readQuery(`?q=x&like=${P1}`)).toEqual({ q: "x" });
    expect(readQuery("?q=x&mode=text")).toEqual({ q: "x" });
    expect(readQuery("?q=x&mode=IMAGE")).toEqual({ q: "x" });
    expect(queryHref({ q: "x", like: P1 })).toBe("/zoeken?q=x");
  });

  it("het veld bovenaan (Enter) geeft dezelfde URL als de pagina", () => {
    for (const q of ["utrecht", "café & co", "  a b  ", "100%"]) {
      expect(searchTarget(q)).toBe(queryHref({ q: q.trim() }));
      expect(readQuery((searchTarget(q) ?? "").slice("/zoeken".length))).toEqual({ q: q.trim() });
    }
    expect(queryHref({})).toBe("/zoeken");
  });

  it("de vraag van 0.7.0 bestaat niet meer: geen archive, geen limit, geen offset", () => {
    expect(readQuery("?q=vakantie&type=archive&limit=200&offset=40&after=9")).toEqual({ q: "vakantie" });
    expect(readQuery("?type=archive&type=image")).toEqual({ type: ["image"] });
    expect(queryHref(readQuery("?q=x&limit=50"))).toBe("/zoeken?q=x");
  });

  it("laat onzin weg: lege tekst, onbekende soorten, geen datum, geen id, een plaats en een land samen", () => {
    expect(readQuery("?q=%20%20&type=foto&limit=-3")).toEqual({});
    expect(readQuery("?type=image&type=image&type=")).toEqual({ type: ["image"] });
    expect(readQuery("?from=2026-02-30&to=26-1-1")).toEqual({});
    expect(readQuery("?from=2024-02-29&to=2024-03-01")).toEqual({ from: "2024-02-29", to: "2024-03-01" });
    expect(readQuery(`?person=niet-een-id&person=${P1}&person=${P1}&tag=x&in=..%2F&library=1`)).toEqual({ person: [P1] });
    expect(readQuery("?place=-1&country=nld")).toEqual({});
    expect(readQuery("?place=12&country=nl")).toEqual({ place: 12 });
    expect(readQuery("?country=nl")).toEqual({ country: "NL" });
    expect(readQuery(`?camera=${"x".repeat(201)}`)).toEqual({});
    // ?bestand= is van de viewer, niet van de vraag
    expect(readQuery(`?q=no&bestand=${P1}`)).toEqual({ q: "no" });
  });

  it("een tag-chip (Tags.tsx) en de suggesties schrijven /zoeken?tag=…; de pagina leest hem terug", () => {
    const href = `/zoeken?${new URLSearchParams({ tag: T1 })}`;
    expect(href).toBe(queryHref({ tag: [T1] }));
    expect(readQuery(href.slice("/zoeken".length))).toEqual({ tag: [T1] });
  });

  it("isDay: alleen JJJJ-MM-DD van een dag die bestaat", () => {
    expect(["2024-02-29", "1999-12-31", "2026-11-05"].every(isDay)).toBe(true);
    expect(["2023-02-29", "2024-13-01", "2024-00-10", "2024-1-1", "2024-01-01T00:00", ""].some(isDay)).toBe(false);
  });
});

describe("de vraag van §3.4 (limit ≤ 50, Meer laden)", () => {
  it("de eerste pagina: limit 30, zonder mode, like, offset of after", () => {
    const query = readQuery(`?q=factuur&type=pdf&tag=${T1}&mode=image&like=${P1}`);
    expect(firstParams({ q: "factuur", type: ["pdf"], tag: [T1] })).toEqual({
      q: "factuur",
      type: ["pdf"],
      tag: [T1],
      limit: DEFAULT_LIMIT,
    });
    expect(firstParams(query).mode).toBeUndefined();
    expect(firstParams(query).like).toBeUndefined();
    expect(wireQuery(firstParams({ q: "100%", tag: [T1] }))).toBe(`?q=100%25&tag=${T1}&limit=30`);
    expect(DEFAULT_LIMIT).toBeLessThanOrEqual(MAX_LIMIT);
  });

  it("Meer laden: de volgende offset, of verder na after (ronde B); niets als er niets meer is", () => {
    const query: PageQuery = { q: "verslag" };
    expect(nextParams(query, page({ more: false }))).toBeNull();
    expect(nextParams(query, page({ more: true, offset: 0 }))).toEqual({ q: "verslag", limit: 30, offset: 30 });
    expect(nextParams(query, page({ more: true, offset: 60 }))).toEqual({ q: "verslag", limit: 30, offset: 90 });
    // Ronde B: verder na de laatste rowid, zonder offset
    expect(nextParams(query, page({ more: true, offset: 0, after: 4711, partial: true, ranked: false }))).toEqual({
      q: "verslag",
      limit: 30,
      after: 4711,
    });
  });

  it("niet voorbij offset 1.000 (400 search_bad_offset): dan geen volgende pagina", () => {
    const query: PageQuery = { q: "de" };
    expect(nextParams(query, page({ more: true, offset: 960 }))).toEqual({ q: "de", limit: 30, offset: 990 });
    expect(nextParams(query, page({ more: true, offset: 990 }))).toBeNull();
    expect(MAX_OFFSET).toBe(1000);
  });

  it("zonder q en zonder filter is er niets om te zoeken; in de stand beeld q of like", () => {
    expect(canSearch({})).toBe(false);
    expect(canSearch({ q: "x" })).toBe(true);
    expect(canSearch({ tag: [T1] })).toBe(true);
    expect(canSearch({ from: "2024-01-01" })).toBe(true);
    expect(canSearch({ place: 0 })).toBe(true);
    expect(canSearch({ mode: "image", type: ["image"] })).toBe(false);
    expect(canSearch({ mode: "image", q: "hond" })).toBe(true);
    expect(canSearch({ mode: "image", like: P1 })).toBe(true);
    expect(hasFilter(readQuery("?limit=10"))).toBe(false);
    expect(hasFilter(readQuery(`?tag=${T1}`))).toBe(true);
  });
});

describe("de chips van de filters (Keuze 47)", () => {
  it("één chip per waarde, in de volgorde van de URL; × haalt alleen die waarde weg", () => {
    const query = readQuery(`?q=x&type=pdf&type=image&from=2024-01-01&tag=${T1}&tag=${T2}&country=nl&in=${MAP}`);
    expect(chips(query)).toEqual([
      { field: "type", value: "pdf" },
      { field: "type", value: "image" },
      { field: "from", value: "2024-01-01" },
      { field: "country", value: "NL" },
      { field: "tag", value: T1 },
      { field: "tag", value: T2 },
      { field: "in", value: MAP },
    ]);
    expect(queryHref(withoutChip(query, { field: "type", value: "pdf" }))).toBe(
      `/zoeken?q=x&type=image&from=2024-01-01&country=NL&tag=${T1}&tag=${T2}&in=${MAP}`,
    );
    const zonderTags = withoutChip(withoutChip(query, { field: "tag", value: T1 }), { field: "tag", value: T2 });
    expect(zonderTags.tag).toBeUndefined();
    expect(withoutChip(query, { field: "in", value: MAP }).in).toBeUndefined();
    expect(chips({ q: "alleen woorden" })).toEqual([]);
  });

  it("een soort, een tag of een datum erbij", () => {
    expect(withType({ q: "x" }, "pdf")).toEqual({ q: "x", type: ["pdf"] });
    const both = withType(withType({ q: "x" }, "pdf"), "image");
    expect(both.type).toEqual(["pdf", "image"]);
    expect(withType(both, "pdf")).toBe(both);
    expect(withTag({}, T1)).toEqual({ tag: [T1] });
    expect(withTag({ tag: [T1] }, "geen-id")).toEqual({ tag: [T1] });
    expect(withDay({ q: "x" }, "from", "2024-05-01")).toEqual({ q: "x", from: "2024-05-01" });
    expect(withDay({ q: "x", to: "2024-05-01" }, "to", "")).toEqual({ q: "x" });
  });

  it("de andere stand houdt q en de filters; like alleen in de stand beeld", () => {
    const tekst: PageQuery = { q: "strand", tag: [T1] };
    expect(queryHref(withMode(tekst, "image"))).toBe(`/zoeken?q=strand&mode=image&tag=${T1}`);
    expect(withMode(withMode(tekst, "image"), "text")).toEqual(tekst);
    expect(withMode({ mode: "image", like: P1, q: "x" }, "text")).toEqual({ q: "x" });
  });
});
