import { afterEach, describe, expect, it, vi } from "vitest";
import { Api, ApiError, api } from "../api";
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
      ["GET", `${V}/files/f1/analysis`],
      ["POST", `${V}/files/f1/ocr`],
      ["GET", `${V}/search?q=rode+auto&mode=image&type=image`],
      ["GET", `${V}/search?mode=image&like=f%2F2`],
      ["GET", `${V}/photos/beeld`],
    ]);
  });

  it("pageWords gaat zonder api.request, met alleen het download-token (zoals de pagina's zelf)", async () => {
    const request = vi.spyOn(api, "request");
    const calls: [string, RequestInit | undefined][] = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([String(input), init]);
      return new Response(JSON.stringify({ page: 3, words: [{ t: "factuur", x: 0.1, y: 0.2, w: 0.1, h: 0.02 }], source: "pdf" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    try {
      const words = await search.pageWords("f/1", 3, "tok en");
      expect(words.words.map((w) => w.t)).toEqual(["factuur"]);
      expect(calls).toHaveLength(1);
      expect(calls[0][0]).toBe("/api/v1/files/f%2F1/pages/3/words?token=tok+en");
      expect(calls[0][1]?.credentials).toBe("same-origin");
      expect(new Headers(calls[0][1]?.headers).get("Authorization")).toBeNull();
      expect(request).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("een verlopen download-token bij de woorden logt je niet uit (via api.request wel)", async () => {
    let loggedOut = 0;
    const before = api.onLoggedOut;
    api.onLoggedOut = () => loggedOut++;
    const expired = () =>
      new Response(JSON.stringify({ detail: { code: "invalid_token", message: "Ongeldig of verlopen token" } }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      });
    vi.stubGlobal("fetch", async () => expired());
    try {
      await expect(search.pageWords("f1", 1, "oud")).rejects.toMatchObject({ status: 401, code: "invalid_token" });
      expect(loggedOut).toBe(0);
    } finally {
      vi.unstubAllGlobals();
      api.onLoggedOut = before;
    }
    // Waarom: api.request ververst na een 401 de sessie en vraagt nog eens met hetzelfde
    // download-token; die tweede 401 is voor api.request het einde van de sessie
    let out = 0;
    const other = new Api(async (input) => {
      const url = String(input);
      if (url.endsWith("/login") || url.endsWith("/refresh")) {
        const user = { id: "u", email: "j@x.nl", display_name: "J", role: "user", quota_bytes: 0, used_bytes: 0, root_id: "r" };
        return new Response(JSON.stringify({ access_token: "A1", refresh_token: "R", expires_in: 900, user }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return expired();
    });
    other.onLoggedOut = () => out++;
    await other.login("j@x.nl", "geheim");
    await expect(other.request("GET", "/api/v1/files/f1/pages/1/words?token=oud")).rejects.toBeInstanceOf(ApiError);
    expect(out).toBe(1);
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
