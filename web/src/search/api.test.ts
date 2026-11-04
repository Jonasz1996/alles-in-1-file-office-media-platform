import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import beeldCatalog from "../i18n/errors/beeld";
import searchCatalog from "../i18n/errors/search";
import * as search from "./api";
import { BEELD_ERROR_CODES, SEARCH_ERROR_CODES, type SearchParams } from "./types";
import { wireQuery } from "./wire";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("wireQuery: de filters in de querystring (Keuze 43)", () => {
  it("een lijst wordt dezelfde sleutel meer keer, in volgorde", () => {
    expect(wireQuery({ type: ["image", "video"], person: ["p1", "p2"] })).toBe("?type=image&type=video&person=p1&person=p2");
  });

  it("laat null, undefined, lege tekst en lege lijsten weg; zonder iets: geen ?", () => {
    expect(wireQuery({ q: "", from: null, to: undefined, tag: [], camera: "   " })).toBe("");
    expect(wireQuery({})).toBe("");
  });

  it("tekst zonder spaties eromheen; getallen, 0 en false blijven", () => {
    expect(wireQuery({ q: "  vakantie 2024 ", offset: 0, place: 2759794, hidden: false })).toBe(
      "?q=vakantie+2024&offset=0&place=2759794&hidden=false",
    );
  });

  it("codeert wat in een querystring iets betekent", () => {
    expect(wireQuery({ q: "a&b=c#d?e/f", in: "map 1" })).toBe("?q=a%26b%3Dc%23d%3Fe%2Ff&in=map+1");
  });
});

describe("de aanroepen van zoeken en tekst (§3.4, Z1, Z2, X1)", () => {
  it("elke functie gebruikt de methode en het pad van het contract", async () => {
    const request = vi.spyOn(api, "request").mockResolvedValue({});
    const params: SearchParams = {
      q: "factuur",
      type: ["pdf", "document"],
      from: "2024-01-01",
      to: "2024-12-31",
      person: ["p1"],
      country: "NL",
      tag: ["t1", "t2"],
      library: "l1",
      camera: "fujifilm x100v",
      in: "map1",
      offset: 30,
      limit: 30,
      after: 1234,
    };
    await search.search(params);
    await search.search({ q: "anna" });
    await search.suggest("an");
    await search.pageWords("f/1", 3, "tok en");
    await search.analysis("f1");
    await search.requestOcr("f1");
    // Zoeken op beeld (X3, vastgelegd in Z0b): met tekst of vanaf een foto, en de stand
    await search.search({ q: "rode auto", mode: "image", type: ["image"] });
    await search.search({ mode: "image", like: "f/2" });
    await search.beeldStatus();

    const V = "/api/v1";
    expect(request.mock.calls).toEqual([
      [
        "GET",
        `${V}/search?q=factuur&type=pdf&type=document&from=2024-01-01&to=2024-12-31&person=p1&country=NL&tag=t1&tag=t2` +
          "&library=l1&camera=fujifilm+x100v&in=map1&offset=30&limit=30&after=1234",
      ],
      ["GET", `${V}/search?q=anna`],
      ["GET", `${V}/search/suggest?q=an`],
      ["GET", `${V}/files/f%2F1/pages/3/words?token=tok+en`],
      ["GET", `${V}/files/f1/analysis`],
      ["POST", `${V}/files/f1/ocr`],
      ["GET", `${V}/search?q=rode+auto&mode=image&type=image`],
      ["GET", `${V}/search?mode=image&like=f%2F2`],
      ["GET", `${V}/photos/beeld`],
    ]);
  });

  it("de catalogus search.ts heeft precies de foutcodes van het contract", () => {
    expect(Object.keys(searchCatalog).sort()).toEqual([...SEARCH_ERROR_CODES].sort());
    for (const { en } of Object.values(searchCatalog)) {
      expect(en.trim()).not.toBe("");
      expect(en).not.toMatch(/[{}]/); // de API geeft geen params mee: een {x} bleef letterlijk staan
    }
  });

  it("de catalogus beeld.ts heeft precies de foutcodes van zoeken op beeld (X3)", () => {
    expect([...BEELD_ERROR_CODES].sort()).toEqual(["beeld_no_vector", "beeld_not_installed", "beeld_off"]);
    expect(Object.keys(beeldCatalog).sort()).toEqual([...BEELD_ERROR_CODES].sort());
    for (const { en } of Object.values(beeldCatalog)) {
      expect(en.trim()).not.toBe("");
      expect(en).not.toMatch(/[{}]/);
    }
  });
});
