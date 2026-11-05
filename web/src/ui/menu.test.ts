import { describe, expect, it } from "vitest";
import { itemRole, nextIndex, placeMenu, placeSubmenu, typeahead, typedText } from "./menu";

describe("nextIndex", () => {
  it("rondom, ook vanaf niets", () => {
    expect(nextIndex(4, 0, 1)).toBe(1);
    expect(nextIndex(4, 3, 1)).toBe(0);
    expect(nextIndex(4, 0, -1)).toBe(3);
    expect(nextIndex(4, -1, 1)).toBe(0);
    expect(nextIndex(4, -1, -1)).toBe(3);
    expect(nextIndex(0, 0, 1)).toBe(-1);
  });
});

describe("typeahead", () => {
  const items = [{ label: "Downloaden" }, { label: "Delen…" }, { label: "Hernoemen" }, { label: "Étiketten" }, { label: "Dupliceren" }];

  it("de eerste letter springt naar het volgende item met die letter, rondom", () => {
    expect(typeahead(items, 0, "d")).toBe(1);
    expect(typeahead(items, 1, "d")).toBe(4);
    expect(typeahead(items, 4, "d")).toBe(0);
    expect(typeahead(items, -1, "h")).toBe(2);
  });

  it("steeds dezelfde letter gaat door de items met die letter", () => {
    expect(typeahead(items, 0, "dd")).toBe(1);
    expect(typeahead(items, 1, "ddd")).toBe(4);
  });

  it("een woord telt het huidige item mee", () => {
    expect(typeahead(items, 0, "do")).toBe(0);
    expect(typeahead(items, 0, "du")).toBe(4);
    expect(typeahead(items, 0, "de")).toBe(1);
  });

  it("zonder accenten en hoofdletters; niets gevonden geeft -1", () => {
    expect(typeahead(items, 0, "E")).toBe(3);
    expect(typeahead(items, 0, "x")).toBe(-1);
    expect(typeahead(items, 0, "")).toBe(-1);
    expect(typeahead([], 0, "a")).toBe(-1);
  });
});

describe("typedText", () => {
  it("binnen de tijd erachter, daarna opnieuw", () => {
    expect(typedText({ text: "", at: 0 }, "m", 1000, 700)).toBe("m");
    expect(typedText({ text: "m", at: 1000 }, "a", 1500, 700)).toBe("ma");
    expect(typedText({ text: "ma", at: 1000 }, "d", 1800, 700)).toBe("d");
  });

  it("een spatie telt alleen mee als er al getypt wordt (Map 2), anders is hij voor de knop", () => {
    expect(typedText({ text: "map", at: 1000 }, " ", 1200, 700)).toBe("map ");
    expect(typedText({ text: "map", at: 1000 }, " ", 1800, 700)).toBeNull();
    expect(typedText({ text: "", at: 0 }, " ", 1000, 700)).toBeNull();
    expect(typeahead([{ label: "Map 1" }, { label: "Map 2" }], 0, "map 2")).toBe(1);
  });
});

describe("placeMenu", () => {
  const viewport = { width: 1000, height: 800 };
  const size = { width: 200, height: 300 };

  it("onder een knop, links uitgelijnd", () => {
    expect(placeMenu({ x: 100, y: 50, width: 80, height: 30 }, size, viewport)).toEqual({ x: 100, y: 80 });
  });

  it("rechts uitgelijnd als het rechts niet past, erboven als het onder niet past", () => {
    expect(placeMenu({ x: 900, y: 50, width: 80, height: 30 }, size, viewport)).toEqual({ x: 780, y: 80 });
    expect(placeMenu({ x: 100, y: 700, width: 80, height: 30 }, size, viewport)).toEqual({ x: 100, y: 400 });
  });

  it("bij een punt (rechtsklik) daar, gespiegeld bij de rand", () => {
    expect(placeMenu({ x: 300, y: 200 }, size, viewport)).toEqual({ x: 300, y: 200 });
    expect(placeMenu({ x: 950, y: 750 }, size, viewport)).toEqual({ x: 750, y: 450 });
  });

  it("altijd binnen het venster, met een marge van 8 px", () => {
    expect(placeMenu({ x: 2, y: 2 }, size, viewport)).toEqual({ x: 8, y: 8 });
    const tall = { width: 200, height: 2000 };
    expect(placeMenu({ x: 100, y: 100 }, tall, viewport).y).toBe(8);
  });
});

describe("placeSubmenu", () => {
  const viewport = { width: 1000, height: 800 };
  const size = { width: 200, height: 150 };

  it("rechts naast het item, of links als het rechts niet past", () => {
    expect(placeSubmenu({ x: 100, y: 100, width: 180, height: 36 }, size, viewport)).toEqual({ x: 280, y: 100 });
    expect(placeSubmenu({ x: 700, y: 100, width: 180, height: 36 }, size, viewport)).toEqual({ x: 500, y: 100 });
  });

  it("schuift omhoog als het onder de rand valt", () => {
    expect(placeSubmenu({ x: 100, y: 750, width: 180, height: 36 }, size, viewport).y).toBe(642);
  });
});

describe("itemRole", () => {
  it("gewoon, vinkje en keuze uit een groep", () => {
    expect(itemRole({})).toBe("menuitem");
    expect(itemRole({ checked: false })).toBe("menuitemcheckbox");
    expect(itemRole({ checked: true, radio: true })).toBe("menuitemradio");
    expect(itemRole({ radio: true })).toBe("menuitem"); // radio zonder checked telt niet
  });
});
