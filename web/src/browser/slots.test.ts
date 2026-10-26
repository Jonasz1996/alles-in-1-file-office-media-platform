import { describe, expect, it } from "vitest";
import { type Slot, SlotAssigner } from "./slots";

const keyOf = (slots: Slot[], index: number) => slots.find((s) => s.index === index)?.key;

describe("vaste plekken", () => {
  it("elke rij één keer, sleutels uniek, in de volgorde van de plekken", () => {
    const slots = new SlotAssigner().assign(10, 47, null);
    expect(slots.map((s) => s.index).sort((a, b) => a - b)).toEqual(Array.from({ length: 37 }, (_, i) => 10 + i));
    expect(new Set(slots.map((s) => s.key)).size).toBe(37);
    expect(slots.map((s) => s.key).slice(0, 3)).toEqual(["s0", "s1", "s2"]);
  });

  it("wat blijft houdt zijn plek; wat erbij komt krijgt de plek van wat wegging", () => {
    const a = new SlotAssigner();
    const before = a.assign(10, 47, null);
    const after = a.assign(13, 50, null);
    for (let i = 13; i < 47; i++) expect(keyOf(after, i)).toBe(keyOf(before, i));
    const gone = [10, 11, 12].map((i) => keyOf(before, i));
    expect([47, 48, 49].map((i) => keyOf(after, i)).sort()).toEqual(gone.sort());
    // de volgorde (van de plekken) verandert niet
    expect(after.map((s) => s.key)).toEqual(before.map((s) => s.key));
  });

  it("dezelfde invoer twee keer: hetzelfde", () => {
    const a = new SlotAssigner();
    a.assign(0, 20, null);
    expect(a.assign(5, 25, null)).toEqual(a.assign(5, 25, null));
  });

  it("de rij met de focus houdt zijn plek, ook buiten het bereik", () => {
    const a = new SlotAssigner();
    const key = keyOf(a.assign(0, 30, null), 5);
    const out = a.assign(100, 130, 5);
    expect(keyOf(out, 5)).toBe(key);
    expect(out).toHaveLength(31);
    expect(new Set(out.map((s) => s.key)).size).toBe(31);
    // en terug in het bereik: nog steeds dezelfde
    expect(keyOf(a.assign(0, 30, 5), 5)).toBe(key);
  });
});

describe("met een budget", () => {
  it("hooguit zoveel nieuwe rijen per keer, die bij het midden eerst; dan de rest", () => {
    const a = new SlotAssigner();
    a.assign(0, 20, null);
    // een sprong van een beeld: 20 nieuwe rijen, budget 8
    const first = a.assign(40, 60, null, 8);
    expect(a.pending).toBe(true);
    const shown = first.map((s) => s.index).filter((i) => i >= 40);
    expect(shown).toHaveLength(8);
    // het dichtst bij het midden (49,5) eerst
    expect(shown.sort((x, y) => x - y)).toEqual([46, 47, 48, 49, 50, 51, 52, 53]);
    // de oude rijen blijven (buiten beeld) staan tot hun plek nodig is: geen nieuwe elementen
    expect(first).toHaveLength(20);
    const second = a.assign(40, 60, null, 8);
    expect(a.pending).toBe(true);
    expect(second.filter((s) => s.index >= 40)).toHaveLength(16);
    const third = a.assign(40, 60, null, 8);
    expect(a.pending).toBe(false);
    expect(third.map((s) => s.index).sort((x, y) => x - y)).toEqual(Array.from({ length: 20 }, (_, i) => 40 + i));
    // nog steeds dezelfde 20 plekken (sleutels), alleen hergebruikt
    expect(new Set(third.map((s) => s.key))).toEqual(new Set(first.map((s) => s.key)));
  });

  it("wat al staat telt niet mee; binnen het budget in één keer", () => {
    const a = new SlotAssigner();
    const before = a.assign(10, 47, null, 10);
    expect(a.pending).toBe(true);
    a.assign(10, 47, null, 10);
    a.assign(10, 47, null, 10);
    a.assign(10, 47, null, 10);
    expect(a.pending).toBe(false);
    const after = a.assign(19, 56, null, 10);
    expect(a.pending).toBe(false);
    for (let i = 19; i < 47; i++) expect(keyOf(after, i)).toBeDefined();
    expect(before).toHaveLength(10);
  });

  it("de rij met de focus gaat voor", () => {
    const a = new SlotAssigner();
    a.assign(0, 30, null);
    const out = a.assign(100, 130, 200, 1);
    expect(a.pending).toBe(true);
    expect(out.map((s) => s.index)).toContain(200);
    expect(out.filter((s) => s.index >= 100)).toHaveLength(1);
  });
});
