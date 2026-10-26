import { describe, expect, it } from "vitest";
import { nodeHref } from "./links";
import { isPlainLeftClick, match, ScrollMemory } from "./router";
import { allowed, isPublicPath, publicRoutes, resolve, routes } from "./routes";

describe("match", () => {
  it("vaste paden en parameters", () => {
    expect(match("/", "/")).toEqual({});
    expect(match("/zoeken", "/zoeken")).toEqual({});
    expect(match("/zoeken", "/zoeken/")).toEqual({}); // slash op het eind maakt niet uit
    expect(match("/map/:id", "/map/abc-123")).toEqual({ id: "abc-123" });
    expect(match("/map/:id", "/map")).toBeNull();
    expect(match("/map/:id", "/map/a/b")).toBeNull();
    expect(match("/gedeeld", "/gedeeld/door-mij")).toBeNull();
    expect(match("/", "/map/x")).toBeNull();
  });

  it("een optioneel laatste deel en *", () => {
    expect(match("/beheer/:tab?", "/beheer")).toEqual({});
    expect(match("/beheer/:tab?", "/beheer/groepen")).toEqual({ tab: "groepen" });
    expect(match("/beheer/:tab?", "/beheer/a/b")).toBeNull();
    expect(match("*", "/wat/dan/ook")).toEqual({});
    expect(match("/s/*", "/s/abc/def")).toEqual({});
  });

  it("decodeert parameters; kapotte codering past niet", () => {
    expect(match("/album/:id", "/album/a%20b")).toEqual({ id: "a b" });
    expect(match("/album/:id", "/album/%E0%A4%A")).toBeNull();
  });
});

describe("isPlainLeftClick", () => {
  const click = { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false };
  it("alleen een gewone linkerklik blijft in de app", () => {
    expect(isPlainLeftClick(click)).toBe(true);
    expect(isPlainLeftClick({ ...click, button: 1 })).toBe(false); // middelklik: nieuw tabblad
    expect(isPlainLeftClick({ ...click, ctrlKey: true })).toBe(false);
    expect(isPlainLeftClick({ ...click, metaKey: true })).toBe(false);
    expect(isPlainLeftClick({ ...click, shiftKey: true })).toBe(false);
    expect(isPlainLeftClick({ ...click, altKey: true })).toBe(false);
  });
});

describe("nodeHref", () => {
  it("map, album en bestand", () => {
    expect(nodeHref({ id: "d1", kind: "dir", parent_id: "p" })).toBe("/map/d1");
    expect(nodeHref({ id: "a1", kind: "album", parent_id: null })).toBe("/album/a1");
    expect(nodeHref({ id: "f1", kind: "file", parent_id: "p1" })).toBe("/map/p1?bestand=f1");
    // Los met je gedeeld, de map erboven is onbekend
    expect(nodeHref({ id: "f1", kind: "file", parent_id: null })).toBe("/gedeeld?bestand=f1");
  });
});

describe("de routetabel", () => {
  it("elk pad vindt een route; onbekend is NotFound", () => {
    expect(resolve("/").def.pattern).toBe("/");
    expect(resolve("/map/x")).toMatchObject({ def: { pattern: "/map/:id" }, params: { id: "x" } });
    expect(resolve("/gedeeld/door-mij").def.pattern).toBe("/gedeeld/door-mij");
    expect(resolve("/beheer/taken")).toMatchObject({ def: { pattern: "/beheer/:tab?" }, params: { tab: "taken" } });
    expect(resolve("/bestaat/niet").def.pattern).toBe("*");
    expect(resolve("/s/abc", publicRoutes)).toMatchObject({ params: { token: "abc" } });
  });

  it("de editor (fase 4) heeft een eigen adres met het gebied office, voor elke rol", () => {
    const r = resolve("/bewerken/abc%20d");
    expect(r).toMatchObject({ def: { pattern: "/bewerken/:id", areas: ["office"], title: "office.title" }, params: { id: "abc d" } });
    expect(r.def.section).toBeNull();
    for (const role of ["admin", "user", "guest"] as const) expect(allowed(r.def, role)).toBe(true);
    expect(resolve("/bewerken").def.pattern).toBe("*");
  });

  it("alleen beheerders in Beheer; gasten niet in de prullenbak of Foto's", () => {
    const def = (p: string) => resolve(p).def;
    expect(allowed(def("/beheer"), "admin")).toBe(true);
    expect(allowed(def("/beheer"), "user")).toBe(false);
    expect(allowed(def("/prullenbak"), "guest")).toBe(false);
    expect(allowed(def("/fotos"), "guest")).toBe(false);
    expect(allowed(def("/gedeeld"), "guest")).toBe(true);
    expect(allowed(def("/album/x"), "guest")).toBe(true); // een gedeeld album
  });

  it("de laatste route is *, en elke route heeft een titel", () => {
    expect(routes[routes.length - 1].pattern).toBe("*");
    for (const r of [...routes, ...publicRoutes]) expect(r.title).toMatch(/^[a-z]+\./);
  });

  it("publieke links staan buiten de login", () => {
    expect(isPublicPath("/s/abc")).toBe(true);
    expect(isPublicPath("/s")).toBe(true);
    expect(isPublicPath("/shared")).toBe(false);
    expect(isPublicPath("/")).toBe(false);
  });
});

describe("ScrollMemory", () => {
  function memory(stored: Record<string, number> = {}) {
    const box = { stored };
    const m = new ScrollMemory(
      () => box.stored,
      (all) => {
        box.stored = all;
      },
    );
    return { m, box };
  }

  it("terug naar een lange pagina: het inkorten tijdens het laden overschrijft de plek niet", () => {
    const { m } = memory();
    m.pushed("A");
    m.record("A", 5000); // gescrold op A
    m.leave("A", 5000); // navigate() naar B
    m.pushed("B");
    m.record("B", 0);
    m.popped(); // terug naar A; de pagina is nog kort
    m.record("A", 0); // de browser klemt scrollY: telt niet
    expect(m.get("A")).toBe(5000);
    expect(m.startRestore("A")).toBe(5000);
    m.record("A", 800); // nog niet lang genoeg: telt niet
    expect(m.get("A")).toBe(5000);
    expect(m.startRestore("A")).toBe(5000); // de pagina zelf, nu met data: nog eens
    m.finish("A", 5000); // de wachttijd is om (of de gebruiker scrolt)
    m.record("A", 5100); // nu telt scrollen weer
    expect(m.get("A")).toBe(5100);
    expect(m.startRestore("A")).toBeUndefined(); // al klaar: niet opnieuw
  });

  it("geeft op na de wachttijd of als de gebruiker zelf scrolt", () => {
    const { m } = memory({ A: 3000 });
    m.popped();
    expect(m.startRestore("A")).toBe(3000);
    expect(m.isPending("A")).toBe(true);
    m.finish("A", 1200);
    expect(m.isPending("A")).toBe(false);
    m.record("A", 1300);
    expect(m.get("A")).toBe(1300);
  });

  it("zonder bewaarde plek is de entry meteen actief; StrictMode geeft dezelfde plek", () => {
    const { m } = memory({ B: 400 });
    m.popped();
    expect(m.startRestore("A")).toBeUndefined();
    m.record("A", 10);
    expect(m.get("A")).toBe(10);
    m.popped();
    expect(m.startRestore("B")).toBe(400);
    expect(m.startRestore("B")).toBe(400);
  });

  it("terug binnen dezelfde pagina (viewer dicht): de entry telt meteen weer", () => {
    const { m, box } = memory();
    m.pushed("map");
    m.record("map", 3000);
    m.leave("map", 3000); // navigate() naar ?bestand=
    m.pushed("viewer");
    m.stayed("map", 3000); // back(): zelfde pad, geen terugzetten
    m.record("map", 3400); // verder scrollen telt
    expect(m.get("map")).toBe(3400);
    m.leave("map", 3500); // naar een andere map
    expect(box.stored.map).toBe(3500);
  });

  it("weggaan tijdens het terugzetten bewaart de oude plek", () => {
    const { m, box } = memory({ A: 2000 });
    m.popped();
    m.startRestore("A");
    m.leave("A", 0);
    expect(box.stored.A).toBe(2000);
  });

  it("opslaan houdt wat er voor het herladen stond, en hooguit 50", () => {
    const old: Record<string, number> = {};
    for (let i = 0; i < 60; i++) old[`o${i}`] = i;
    const { m, box } = memory(old);
    m.pushed("nieuw");
    m.leave("nieuw", 77);
    const keys = Object.keys(box.stored);
    expect(keys).toHaveLength(50);
    expect(box.stored.nieuw).toBe(77);
    expect(box.stored.o59).toBe(59); // van vóór het herladen, niet weggegooid
    expect(keys.at(-1)).toBe("nieuw");
  });
});
