import { describe, expect, it } from "vitest";
import { type Slot, SlotAssigner } from "./slots";

const keyOf = (slots: Slot[], index: number) => slots.find((s) => s.index === index)?.key;

describe("vaste plekken", () => {
  it("elke rij één keer, sleutels uniek, in de volgorde van de plekken", () => {
    const slots = new SlotAssigner().assign(10, 47, null, null);
    expect(slots.map((s) => s.index).sort((a, b) => a - b)).toEqual(Array.from({ length: 37 }, (_, i) => 10 + i));
    expect(new Set(slots.map((s) => s.key)).size).toBe(37);
    expect(slots.map((s) => s.key).slice(0, 3)).toEqual(["s0", "s1", "s2"]);
  });

  it("wat blijft houdt zijn plek; wat erbij komt krijgt de plek van wat wegging", () => {
    const a = new SlotAssigner();
    const before = a.assign(10, 47, null, null);
    const after = a.assign(13, 50, null, null);
    for (let i = 13; i < 47; i++) expect(keyOf(after, i)).toBe(keyOf(before, i));
    const gone = [10, 11, 12].map((i) => keyOf(before, i));
    expect([47, 48, 49].map((i) => keyOf(after, i)).sort()).toEqual(gone.sort());
    // de volgorde (van de plekken) verandert niet
    expect(after.map((s) => s.key)).toEqual(before.map((s) => s.key));
  });

  it("dezelfde invoer twee keer: hetzelfde", () => {
    const a = new SlotAssigner();
    a.assign(0, 20, null, null);
    expect(a.assign(5, 25, null, null)).toEqual(a.assign(5, 25, null, null));
  });

  it("de rij met de focus houdt sleutel f, in en buiten beeld", () => {
    const a = new SlotAssigner();
    expect(keyOf(a.assign(0, 30, null, 5), 5)).toBe("f");
    const out = a.assign(100, 130, 5, 5);
    expect(keyOf(out, 5)).toBe("f");
    expect(out.filter((s) => s.key === "f")).toHaveLength(1);
    expect(out).toHaveLength(31);
  });
});
