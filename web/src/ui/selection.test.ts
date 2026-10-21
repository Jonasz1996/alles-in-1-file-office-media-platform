import { describe, expect, it, vi } from "vitest";
import { clickSelect, EMPTY_SELECTION, keySelect, SelectionStore, type SelectionState } from "./selection";

const order = ["a", "b", "c", "d", "e"];
const plain = { shift: false, toggle: false };
const shift = { shift: true, toggle: false };
const toggle = { shift: false, toggle: true };
const sel = (s: SelectionState) => [...s.selected].sort();

describe("clickSelect", () => {
  it("een gewone klik selecteert alleen dat item en zet het anker", () => {
    const s = clickSelect(clickSelect(EMPTY_SELECTION, order, "b", plain), order, "d", plain);
    expect(sel(s)).toEqual(["d"]);
    expect(s.anchor).toBe("d");
    expect(s.focus).toBe("d");
  });

  it("Ctrl/Cmd zet erbij of haalt eraf", () => {
    let s = clickSelect(EMPTY_SELECTION, order, "b", plain);
    s = clickSelect(s, order, "d", toggle);
    expect(sel(s)).toEqual(["b", "d"]);
    s = clickSelect(s, order, "b", toggle);
    expect(sel(s)).toEqual(["d"]);
    expect(s.anchor).toBe("b");
  });

  it("Shift selecteert de reeks vanaf het anker, in beide richtingen", () => {
    let s = clickSelect(EMPTY_SELECTION, order, "b", plain);
    s = clickSelect(s, order, "d", shift);
    expect(sel(s)).toEqual(["b", "c", "d"]);
    expect(s.anchor).toBe("b");
    // Het anker blijft: een tweede Shift-klik vervangt de reeks
    s = clickSelect(s, order, "a", shift);
    expect(sel(s)).toEqual(["a", "b"]);
  });

  it("Shift+Ctrl voegt de reeks toe aan wat er al was", () => {
    let s = clickSelect(EMPTY_SELECTION, order, "a", plain);
    s = clickSelect(s, order, "d", toggle); // anker d
    s = clickSelect(s, order, "e", { shift: true, toggle: true });
    expect(sel(s)).toEqual(["a", "d", "e"]);
  });

  it("Shift zonder (geldig) anker selecteert alleen het item", () => {
    const s = clickSelect({ selected: new Set(), anchor: "weg", focus: null }, order, "c", shift);
    expect(sel(s)).toEqual(["c"]);
    expect(s.anchor).toBe("c");
  });
});

describe("keySelect", () => {
  it("pijl zonder modifier: alleen het nieuwe item", () => {
    const s = keySelect(clickSelect(EMPTY_SELECTION, order, "a", plain), order, "b", plain);
    expect(sel(s)).toEqual(["b"]);
    expect(s.focus).toBe("b");
  });

  it("Ctrl+pijl: alleen de focus gaat mee", () => {
    const start = clickSelect(EMPTY_SELECTION, order, "a", plain);
    const s = keySelect(start, order, "c", toggle);
    expect(s.selected).toBe(start.selected);
    expect(s.focus).toBe("c");
    expect(s.anchor).toBe("a");
  });

  it("Shift+pijl: de reeks vanaf het anker groeit en krimpt", () => {
    let s = clickSelect(EMPTY_SELECTION, order, "b", plain);
    s = keySelect(s, order, "c", shift);
    s = keySelect(s, order, "d", shift);
    expect(sel(s)).toEqual(["b", "c", "d"]);
    s = keySelect(s, order, "c", shift);
    expect(sel(s)).toEqual(["b", "c"]);
  });

  it("Shift+pijl zonder anker begint bij de focus", () => {
    const s = keySelect({ selected: new Set(), anchor: null, focus: "b" }, order, "d", shift);
    expect(sel(s)).toEqual(["b", "c", "d"]);
    expect(s.anchor).toBe("b");
  });
});

describe("SelectionStore", () => {
  it("meldt elke wijziging, en niets als er niets verandert", () => {
    const store = new SelectionStore();
    const l = vi.fn();
    store.subscribe(l);
    store.clear(); // al leeg
    expect(l).not.toHaveBeenCalled();
    store.selectAll(order);
    expect(store.getSnapshot().selected.size).toBe(5);
    expect(store.has("c")).toBe(true);
    store.toggle("c");
    expect(store.has("c")).toBe(false);
    store.clear();
    expect(store.getSnapshot().selected.size).toBe(0);
    expect(l).toHaveBeenCalledTimes(3);
  });

  it("retain haalt weg wat er niet meer is", () => {
    const store = new SelectionStore();
    store.set(clickSelect(clickSelect(EMPTY_SELECTION, order, "a", plain), order, "c", shift));
    const before = store.getSnapshot();
    store.retain(new Set(order)); // alles is er nog: niets veranderd
    expect(store.getSnapshot()).toBe(before);
    store.retain(new Set(["b", "c"]));
    const s = store.getSnapshot();
    expect(sel(s)).toEqual(["b", "c"]);
    expect(s.anchor).toBeNull(); // a is weg
    expect(s.focus).toBe("c");
  });
});
