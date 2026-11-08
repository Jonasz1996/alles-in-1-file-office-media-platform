import { describe, expect, it } from "vitest";
import { SUGGEST_LIST_ID } from "./appBar";
import { fallbackHref, fieldState, grouped, type ListState, move, onKey, optionId, optionsOf } from "./combo";
import type { Suggestions } from "./types";

const DIR = "1a1a1a1a-0000-4000-8000-000000000001";
const FILE = "1a1a1a1a-0000-4000-8000-000000000002";
const PERSON = "2b2b2b2b-0000-4000-8000-000000000003";
const TAG = "3c3c3c3c-0000-4000-8000-000000000004";
const ALBUM = "4d4d4d4d-0000-4000-8000-000000000005";

function answer(over: Partial<Suggestions> = {}): Suggestions {
  return { names: [], people: [], places: [], tags: [], albums: [], ...over };
}

const FULL = answer({
  names: [
    { id: DIR, name: "Utrecht 2024", kind: "dir" },
    { id: FILE, name: "utrecht.jpg", kind: "file" },
  ],
  people: [{ id: PERSON, name: "Ute" }],
  places: [{ id: 2745912, name: "Utrecht", admin1: "Utrecht", country: "NL" }],
  tags: [{ id: TAG, name: "uitje", color: "" }],
  albums: [{ id: ALBUM, name: "Uit in Utrecht" }],
});

describe("de opties van de suggesties (Keuze 46 en 81)", () => {
  it("namen, personen, plaatsen, tags, albums en als laatste zoeken op beeld; elk met een id", () => {
    const options = optionsOf(FULL, " utr ", true);
    expect(options.map((o) => [o.id, o.group, o.label, o.target])).toEqual([
      [`${SUGGEST_LIST_ID}-0`, "names", "Utrecht 2024", { href: `/map/${DIR}` }],
      [`${SUGGEST_LIST_ID}-1`, "names", "utrecht.jpg", { file: FILE, name: "utrecht.jpg" }],
      [`${SUGGEST_LIST_ID}-2`, "people", "Ute", { href: `/zoeken?person=${PERSON}` }],
      [`${SUGGEST_LIST_ID}-3`, "places", "Utrecht, Utrecht", { href: "/zoeken?place=2745912" }],
      [`${SUGGEST_LIST_ID}-4`, "tags", "uitje", { href: `/zoeken?tag=${TAG}` }],
      [`${SUGGEST_LIST_ID}-5`, "albums", "Uit in Utrecht", { href: `/album/${ALBUM}` }],
      [`${SUGGEST_LIST_ID}-6`, "image", "utr", { href: "/zoeken?q=utr&mode=image" }],
    ]);
    expect(optionId(6)).toBe(options[6].id);
  });

  it("een gast krijgt geen regel voor zoeken op beeld; zonder antwoord (429, offline) alleen die regel", () => {
    expect(optionsOf(FULL, "utr", false).map((o) => o.group)).toEqual(["names", "names", "people", "places", "tags", "albums"]);
    expect(optionsOf(null, "hond op het strand", true).map((o) => [o.group, o.target])).toEqual([
      ["image", { href: "/zoeken?q=hond+op+het+strand&mode=image" }],
    ]);
    expect(optionsOf(null, "x", false)).toEqual([]);
    expect(optionsOf(answer(), "   ", true)).toEqual([]);
  });

  it("een plaats zonder provincie heet alleen zo; een bestand dat weg is: zoeken op zijn naam", () => {
    const options = optionsOf(answer({ places: [{ id: 7, name: "Ergens", admin1: null, country: null }] }), "erg", false);
    expect(options.map((o) => o.label)).toEqual(["Ergens"]);
    expect(fallbackHref("verslag & co.pdf")).toBe("/zoeken?q=verslag+%26+co.pdf");
  });

  it("groepen voor het scherm, in volgorde, zonder lege; de laatste regel apart", () => {
    const options = optionsOf(answer({ names: FULL.names, tags: FULL.tags }), "u", true);
    expect(grouped(options).map(([g, items]) => [g, items.map(([i]) => i)])).toEqual([
      ["names", [0, 1]],
      ["tags", [2]],
      ["image", [3]],
    ]);
  });
});

describe("de toetsen van de combobox (Keuze 46)", () => {
  const open = (active: number): ListState => ({ open: true, active });

  it("↓ en ↑ door de opties; ↓ stopt bij de laatste, ↑ vanaf de eerste terug naar het veld", () => {
    let s = open(-1);
    const seen: number[] = [];
    for (const key of ["ArrowDown", "ArrowDown", "ArrowDown", "ArrowDown", "ArrowUp", "ArrowUp", "ArrowUp", "ArrowUp"]) {
      const r = onKey(s, 3, { key });
      expect(r?.do).toBe("show");
      if (r?.do === "show") s = r.state;
      seen.push(s.active);
    }
    expect(seen).toEqual([0, 1, 2, 2, 1, 0, -1, -1]);
    expect(move(1, "Home", 3)).toBeNull();
  });

  it("Enter kiest de actieve optie; zonder actieve optie is Enter van het formulier (/zoeken)", () => {
    expect(onKey(open(2), 3, { key: "Enter" })).toEqual({ do: "choose", index: 2 });
    expect(onKey(open(-1), 3, { key: "Enter" })).toBeNull();
    expect(onKey({ open: false, active: 1 }, 3, { key: "Enter" })).toBeNull();
    expect(onKey(open(0), 0, { key: "Enter" })).toBeNull();
  });

  it("Esc sluit een open lijst en houdt daar op; daarna is Esc weer van het veld", () => {
    expect(onKey(open(1), 3, { key: "Escape" })).toEqual({ do: "close", prevent: true });
    expect(onKey({ open: false, active: -1 }, 3, { key: "Escape" })).toBeNull();
    expect(onKey(open(-1), 0, { key: "Escape" })).toBeNull();
  });

  it("na Esc opent ↓ de lijst op de eerste, ↑ op de laatste; zonder opties doen de pijlen niets", () => {
    const shut: ListState = { open: false, active: -1 };
    expect(onKey(shut, 4, { key: "ArrowDown" })).toEqual({ do: "show", state: open(0) });
    expect(onKey(shut, 4, { key: "ArrowUp" })).toEqual({ do: "show", state: open(3) });
    expect(onKey(shut, 0, { key: "ArrowDown" })).toBeNull();
  });

  it("Tab sluit en gaat door; met Ctrl, Alt, Cmd of tijdens het samenstellen: niets", () => {
    expect(onKey(open(0), 3, { key: "Tab" })).toEqual({ do: "close", prevent: false });
    expect(onKey({ open: false, active: -1 }, 3, { key: "Tab" })).toBeNull();
    for (const mods of [{ ctrlKey: true }, { altKey: true }, { metaKey: true }, { isComposing: true }]) {
      expect(onKey(open(0), 3, { key: "ArrowDown", ...mods })).toBeNull();
      expect(onKey(open(0), 3, { key: "Enter", ...mods })).toBeNull();
    }
    expect(onKey(open(0), 3, { key: "a" })).toBeNull();
  });

  it("aria-expanded en aria-activedescendant van het veld volgen de lijst", () => {
    const options = optionsOf(FULL, "utr", true);
    expect(fieldState(open(-1), options)).toEqual({ open: true, active: null });
    expect(fieldState(open(3), options)).toEqual({ open: true, active: `${SUGGEST_LIST_ID}-3` });
    expect(fieldState({ open: false, active: 3 }, options)).toEqual({ open: false, active: null });
    expect(fieldState(open(0), [])).toEqual({ open: false, active: null });
  });
});
