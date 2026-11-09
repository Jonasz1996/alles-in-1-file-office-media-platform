import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { FileNode } from "../api";
import { loadAreas, resetI18nForTests } from "../i18n";
import pageSource from "./DupesPage.tsx?raw";
import { Group, type GroupProps, groupHeadingId } from "./Group";
import {
  allPicked,
  appendGroups,
  dropGroup,
  dropItems,
  focusAfter,
  folderOf,
  kindOf,
  levelOf,
  moveInGroup,
  NO_TICKS,
  paramOf,
  pickedIds,
  pruneTicks,
  tick,
  tickedIn,
} from "./model";
import type { DupeGroup, DupeItem } from "./types";

function item(id: string, path: { id: string; name: string }[] = [{ id: "root", name: "" }], taken: string | null = null): DupeItem {
  const node: FileNode = {
    id,
    parent_id: path[path.length - 1]?.id ?? null,
    name: `${id}.jpg`,
    kind: "file",
    mime: "image/jpeg",
    size: 2048,
    etag: "e",
    created_at: "2026-11-03T10:00:00Z",
    modified_at: "2026-11-03T10:00:00Z",
    deleted_at: null,
    tags: [],
  };
  return { node, path, size: 2048, taken_at: taken };
}

function group(key: string, ids: string[], o: Partial<DupeGroup> = {}): DupeGroup {
  return { key, kind: "exact", level: null, dist: null, items: ids.map((id) => item(id)), ...o };
}

async function useLocale(l: "nl" | "en"): Promise<void> {
  resetI18nForTests(l);
  await loadAreas(["dupes"]);
}

afterAll(() => resetI18nForTests("nl"));

describe("de twee niveaus (Keuze 36)", () => {
  it("precies gelijk, bijna zeker gelijk, en anders altijd het voorzichtige 'lijkt erop'", () => {
    expect(levelOf({ kind: "exact", level: null })).toBe("exact");
    expect(levelOf({ kind: "near", level: "zeker" })).toBe("zeker");
    expect(levelOf({ kind: "near", level: "lijkt" })).toBe("lijkt");
    expect(levelOf({ kind: "near", level: null })).toBe("lijkt");
  });

  it("het tabblad in de adresbalk: ?soort=lijkt is near, de rest exact", () => {
    expect(kindOf(null)).toBe("exact");
    expect(kindOf("lijkt")).toBe("near");
    expect(kindOf("iets")).toBe("exact");
    expect(paramOf("near")).toBe("lijkt");
    expect(paramOf("exact")).toBeNull();
  });
});

describe("de map van een bestand", () => {
  it("in de hoofdmap: Mijn bestanden, naar /", () => {
    expect(folderOf(item("a"), "Mijn bestanden")).toEqual({ label: "Mijn bestanden", href: "/" });
    expect(folderOf(item("a", []), "Mijn bestanden")).toEqual({ label: "Mijn bestanden", href: "/" });
  });

  it("dieper: het hele pad, en de link naar de map waar hij in staat; het bestand zelf telt niet mee", () => {
    const path = [
      { id: "root", name: "" },
      { id: "d1", name: "Vakantie" },
      { id: "d2", name: "2024" },
    ];
    expect(folderOf(item("a", path), "Mijn bestanden")).toEqual({
      label: "Mijn bestanden / Vakantie / 2024",
      href: "/map/d2",
    });
    expect(folderOf(item("a", [...path, { id: "a", name: "a.jpg" }]), "Mijn bestanden").href).toBe("/map/d2");
  });
});

describe("de groepen bijhouden", () => {
  it("een volgende pagina: geen groep dubbel, geen groep van één", () => {
    const a = group("a", ["1", "2"]);
    const b = group("b", ["3", "4"]);
    expect(appendGroups([a], [a, b, group("c", ["5"])]).map((g) => g.key)).toEqual(["a", "b"]);
  });

  it("na de prullenbak: de bestanden weg, een groep met één bestand verdwijnt", () => {
    const groups = [group("a", ["1", "2", "3"]), group("b", ["4", "5"])];
    const after = dropItems(groups, new Set(["1", "4"]));
    expect(after.map((g) => [g.key, g.items.map((it) => it.node.id)])).toEqual([["a", ["2", "3"]]]);
    expect(dropItems(groups, new Set())[0]).toBe(groups[0]);
    expect(dropGroup(groups, "a").map((g) => g.key)).toEqual(["b"]);
  });

  it("de focus na een actie: dezelfde groep, anders de volgende, anders de vorige, anders de kop", () => {
    const before = [group("a", ["1", "2"]), group("b", ["3", "4"]), group("c", ["5", "6"])];
    expect(focusAfter(before, before, "b")).toBe("b");
    expect(focusAfter(before, dropGroup(before, "b"), "b")).toBe("c");
    expect(focusAfter(before, dropGroup(before, "c"), "c")).toBe("b");
    expect(focusAfter(before.slice(0, 1), [], "a")).toBeNull();
  });
});

describe("aanvinken: nooit iets vooraf", () => {
  it("begint leeg, en alleen tick() zet iets aan", () => {
    expect(NO_TICKS.size).toBe(0);
    const g = group("a", ["1", "2", "3"]);
    expect(pickedIds(g, NO_TICKS)).toEqual([]);
    let tk = tick(NO_TICKS, "a", "3", true);
    tk = tick(tk, "a", "1", true);
    expect(NO_TICKS.size).toBe(0); // niet veranderd
    expect(pickedIds(g, tk)).toEqual(["1", "3"]); // in de volgorde van de groep
    expect(allPicked(g, tk)).toBe(false);
    expect(allPicked(g, tick(tk, "a", "2", true))).toBe(true);
    tk = tick(tick(tk, "a", "1", false), "a", "3", false);
    expect(tk.size).toBe(0);
  });

  it("wat weg is, valt uit de vinkjes", () => {
    const tk = tick(tick(tick(NO_TICKS, "a", "1", true), "a", "2", true), "b", "4", true);
    const left = pruneTicks(tk, [group("a", ["2", "3"])]);
    expect([...tickedIn(left, "a")]).toEqual(["2"]);
    expect(tickedIn(left, "b").size).toBe(0);
  });

  it("de pagina begint met NO_TICKS voor beide tabbladen en zet nergens zelf een vinkje", () => {
    expect(pageSource).toContain("useState<Record<DupeKind, Ticks>>({ exact: NO_TICKS, near: NO_TICKS })");
    // tick() alleen vanuit onPick (de gebruiker); geen "alles kiezen" en geen tick bij het laden
    expect(pageSource.match(/tick\(/g)?.length).toBe(1);
    expect(pageSource).toContain("onPick={(id, on) => setTicks((tk) => ({ ...tk, [kind]: tick(tk[kind], g.key, id, on) }))}");
  });
});

describe("de toetsen binnen een groep", () => {
  it("links en rechts naar het vorige of volgende bestand, Home en End; de rest niet", () => {
    expect(moveInGroup("ArrowRight", 0, 3)).toBe(1);
    expect(moveInGroup("ArrowRight", 2, 3)).toBe(2);
    expect(moveInGroup("ArrowLeft", 0, 3)).toBe(0);
    expect(moveInGroup("ArrowLeft", 2, 3)).toBe(1);
    expect(moveInGroup("Home", 2, 3)).toBe(0);
    expect(moveInGroup("End", 0, 3)).toBe(2);
    expect(moveInGroup("Enter", 0, 3)).toBeNull();
    expect(moveInGroup(" ", 0, 3)).toBeNull();
    expect(moveInGroup("ArrowRight", 0, 0)).toBeNull();
  });
});

describe("een groep op het scherm", () => {
  beforeAll(() => {
    vi.stubGlobal("window", { location: { pathname: "/dubbel", search: "" } });
  });
  afterAll(() => {
    vi.unstubAllGlobals();
  });
  beforeEach(() => useLocale("nl"));

  function render(over: Partial<GroupProps> = {}): string {
    const props: GroupProps = {
      group: group("k1", ["a", "b"], { kind: "near", level: "zeker", dist: 3 }),
      picked: new Set(),
      previews: {},
      onPick: () => {},
      onTrash: () => {},
      onNotSame: () => {},
      onView: () => {},
      ...over,
    };
    return renderToStaticMarkup(createElement(Group, props));
  }

  it("niets aangevinkt: geen vinkje, en de knop van de prullenbak doet nog niets", () => {
    const html = render();
    expect(html.match(/type="checkbox"/g)?.length).toBe(2);
    expect(html).not.toContain("checked");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>.*Naar de prullenbak<\/button>/);
    expect(html).toContain("Vink eerst aan wat weg mag.");
    expect(html).toContain(">Niet gelijk</button>");
  });

  it("het niveau, de voorbeelden naast elkaar met datum, grootte en map, en namen voor de schermlezer", () => {
    const html = render();
    expect(html).toContain(`id="${groupHeadingId("k1")}"`);
    expect(html).toContain("Bijna zeker gelijk");
    expect(html).toContain("2 bestanden");
    expect(html).toContain('aria-label="a.jpg bekijken"');
    expect(html).toContain('aria-label="a.jpg kiezen voor de prullenbak"');
    expect(html).toContain("<dt>Gewijzigd</dt>");
    expect(html).toContain("<dd>2,0 KB</dd>");
    expect(html).toContain('href="/"');
    expect(html).toContain(">Mijn bestanden</a>");
    const lijkt = render({ group: group("k2", ["a", "b"], { kind: "near", level: "lijkt", dist: 9 }) });
    expect(lijkt).toContain("Lijkt erop");
    expect(render({ group: group("k3", ["a", "b"]) })).toContain("Precies gelijk");
  });

  it("met de opnamedatum: Gemaakt", () => {
    const g = group("k1", ["a", "b"]);
    g.items[0] = item("a", undefined, "2024-07-01T12:30:00Z");
    expect(render({ group: g })).toContain("<dt>Gemaakt</dt>");
  });

  it("alleen wat de gebruiker aanvinkte, staat aan; de knop zegt hoeveel", () => {
    const html = render({ picked: new Set(["b"]) });
    expect(html.match(/checked=""/g)?.length).toBe(1);
    expect(html).toMatch(/aria-label="b\.jpg kiezen voor de prullenbak" checked=""|checked="" aria-label="b\.jpg/);
    expect(html).toContain("1 bestand naar de prullenbak");
    expect(html).not.toContain("Vink eerst aan");
  });

  it("in het Engels", async () => {
    await useLocale("en");
    const html = render({ picked: new Set(["a", "b"]) });
    expect(html).toContain("Almost certainly the same");
    expect(html).toContain("Move 2 files to trash");
    expect(html).toContain("Not the same");
  });
});
