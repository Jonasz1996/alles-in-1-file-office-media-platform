import { afterEach, describe, expect, it, vi } from "vitest";
import worldRaw from "../../../public/kaart/world-110m.f2c3625f1d27.json?raw";
import {
  BadWorld,
  countryAt,
  countryByCode,
  countryName,
  countryPath,
  countryPaths,
  decodeWorld,
  loadWorld,
  ringDegrees,
  WORLD_FILE,
  WORLD_URL,
} from "./world";

/** Een kleine wereld in het formaat van maak_wereld.py: een vierkant land met een gat erin. */
const KLEIN = {
  v: 1,
  bron: "test",
  sha256: "0".repeat(64),
  schaal: 100,
  landen: [
    {
      a3: "AAA",
      a2: "AA",
      nl: "Vierkantland",
      en: "Squareland",
      // (0,0) (10,0) (10,10) (0,10) en een gat (4,4) (6,4) (6,6) (4,6), in honderdsten en als verschil
      v: [
        [
          [0, 0, 1000, 0, 0, 1000, -1000, 0],
          [400, 400, 200, 0, 0, 200, -200, 0],
        ],
      ],
    },
    { a3: "BBB", a2: null, nl: "Driehoek", en: "Triangle", v: [[[2000, 0, 500, 0, -250, 500]]] },
  ],
};

describe("de wereld uitpakken", () => {
  it("telt de verschillen op en deelt door de schaal", () => {
    expect(ringDegrees([123, -456, 10, 20, -133, 436], 100)).toEqual([1.23, -4.56, 1.33, -4.36, 0, 0]);
  });

  it("geeft per land de ringen in graden en het kader", () => {
    const [a, b] = decodeWorld(KLEIN);
    expect(a).toEqual({
      a3: "AAA",
      a2: "AA",
      nl: "Vierkantland",
      en: "Squareland",
      rings: [
        [0, 0, 10, 0, 10, 10, 0, 10],
        [4, 4, 6, 4, 6, 6, 4, 6],
      ],
      box: [0, 0, 10, 10],
    });
    expect(b.a2).toBeNull();
    expect(b.box).toEqual([20, 0, 25, 5]);
  });

  it("weigert wat niet de vorm van maak_wereld.py heeft", () => {
    expect(() => decodeWorld(null)).toThrow(BadWorld);
    expect(() => decodeWorld({ ...KLEIN, v: 2 })).toThrow(BadWorld);
    expect(() => decodeWorld({ ...KLEIN, schaal: 0 })).toThrow(BadWorld);
    expect(() => decodeWorld({ ...KLEIN, landen: "geen lijst" })).toThrow(BadWorld);
    expect(() => decodeWorld({ ...KLEIN, landen: [{ a3: 1, v: [] }] })).toThrow(BadWorld);
    // een ring met minder dan drie punten, een oneven lengte of een breuk
    for (const ring of [[0, 0, 1, 1], [0, 0, 1, 1, 2], [0, 0, 1, 1, 2, 2.5]]) {
      expect(() => decodeWorld({ ...KLEIN, landen: [{ ...KLEIN.landen[0], v: [[ring]] }] })).toThrow(BadWorld);
    }
  });

  it("vindt het land van een punt, met het gat als geen land", () => {
    const w = decodeWorld(KLEIN);
    expect(countryAt(w, 2, 2)?.a3).toBe("AAA");
    expect(countryAt(w, 5, 5)).toBeNull();
    expect(countryAt(w, 22, 1)?.a3).toBe("BBB");
    expect(countryAt(w, 15, 5)).toBeNull();
  });

  it("tekent elke ring als een eigen stuk, in pixels op zoom 0", () => {
    const d = countryPath({ rings: [[-180, 0, 0, 0, 0, 85.0511287798]] });
    expect(d).toBe("M0.000 128.000L128.000 128.000 128.000 0.000Z");
    const w = decodeWorld(KLEIN);
    const paths = countryPaths(w);
    expect(paths).toHaveLength(2);
    expect(paths[0].match(/M/g)).toHaveLength(2);
    expect(countryPaths(w)).toBe(paths);
  });

  it("noemt een land in de taal van de app en vindt het bij zijn code", () => {
    const w = decodeWorld(KLEIN);
    expect(countryName(w[0], "nl")).toBe("Vierkantland");
    expect(countryName(w[0], "en")).toBe("Squareland");
    expect(countryByCode(w, "aa")?.a3).toBe("AAA");
    expect(countryByCode(w, null)).toBeNull();
    expect(countryByCode(w, "ZZ")).toBeNull();
  });
});

describe("het bestand in public/kaart", () => {
  const world = decodeWorld(JSON.parse(worldRaw));

  it("heeft in zijn naam de eerste 12 tekens van de sha256 van de inhoud", async () => {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(worldRaw));
    const hex = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
    expect(WORLD_FILE).toBe(`world-110m.${hex.slice(0, 12)}.json`);
    expect(WORLD_URL).toBe(`/kaart/${WORLD_FILE}`);
  });

  it("heeft 177 landen met elk een eigen code en een Nederlandse en Engelse naam", () => {
    expect(world).toHaveLength(177);
    expect(new Set(world.map((c) => c.a3)).size).toBe(177);
    expect(world.every((c) => c.nl && c.en)).toBe(true);
    expect(world.filter((c) => c.a2 === null).map((c) => c.a3)).toEqual(["CYN", "SOL"]);
    const nl = countryByCode(world, "NL");
    expect(nl && [nl.a3, nl.nl, nl.en]).toEqual(["NLD", "Nederland", "Netherlands"]);
  });

  it("legt bekende plekken in het goede land, en de zee in geen land", () => {
    const at = (lat: number, lon: number) => countryAt(world, lon, lat)?.a3 ?? null;
    expect(at(52.3728, 4.8936)).toBe("NLD"); // Amsterdam
    expect(at(52.0907, 5.1214)).toBe("NLD"); // Utrecht
    expect(at(48.8566, 2.3522)).toBe("FRA"); // Parijs
    expect(at(52.52, 13.405)).toBe("DEU"); // Berlijn
    expect(at(-33.8688, 151.2093)).toBe("AUS"); // Sydney
    expect(at(40.7128, -74.006)).toBe("USA"); // New York
    expect(at(54.5, 3.5)).toBeNull(); // de Noordzee
    expect(at(0, -30)).toBeNull(); // de Atlantische Oceaan
  });
});

describe("de wereld ophalen", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("vraagt alleen de eigen host, één keer, en probeert na een fout opnieuw", async () => {
    const fail = vi.fn(async () => new Response("weg", { status: 503 }));
    vi.stubGlobal("fetch", fail);
    await expect(loadWorld()).rejects.toThrow(BadWorld);
    expect(fail).toHaveBeenCalledTimes(1);

    const ok = vi.fn(async () => new Response(JSON.stringify(KLEIN), { status: 200 }));
    vi.stubGlobal("fetch", ok);
    const [a, b] = await Promise.all([loadWorld(), loadWorld()]);
    expect(a).toBe(b);
    expect(a.map((c) => c.a3)).toEqual(["AAA", "BBB"]);
    expect(await loadWorld()).toBe(a);
    expect(ok).toHaveBeenCalledTimes(1);
    expect(ok).toHaveBeenCalledWith("/kaart/world-110m.f2c3625f1d27.json", { credentials: "same-origin" });
  });
});
