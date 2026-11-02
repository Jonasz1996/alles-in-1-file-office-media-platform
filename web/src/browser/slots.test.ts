import { describe, expect, it } from "vitest";
import { NO_SLOTS, planSlots, type Slot, type SlotMap, type SlotPlan } from "./slots";

const keyOf = (slots: Slot[], index: number) => slots.find((s) => s.index === index)?.key;
const rowAt = (map: SlotMap, slot: number) => [...map].find(([, s]) => s === slot)?.[0];

/** Zoals useSlots: elke render wordt getekend, en de volgende begint bij zijn plekken. */
function list() {
  let drawn: SlotMap = NO_SLOTS;
  return {
    draw(start: number, end: number, keep: number | null, budget?: number): SlotPlan {
      const plan = planSlots(drawn, start, end, keep, budget);
      drawn = plan.slotOf;
      return plan;
    },
    get drawn(): SlotMap {
      return drawn;
    },
  };
}

describe("vaste plekken", () => {
  it("elke rij één keer, sleutels uniek, in de volgorde van de plekken", () => {
    const { slots } = planSlots(NO_SLOTS, 10, 47, null);
    expect(slots.map((s) => s.index).sort((a, b) => a - b)).toEqual(Array.from({ length: 37 }, (_, i) => 10 + i));
    expect(new Set(slots.map((s) => s.key)).size).toBe(37);
    expect(slots.map((s) => s.key).slice(0, 3)).toEqual(["s0", "s1", "s2"]);
  });

  it("wat blijft houdt zijn plek; wat erbij komt krijgt de plek van wat wegging", () => {
    const l = list();
    const before = l.draw(10, 47, null).slots;
    const after = l.draw(13, 50, null).slots;
    for (let i = 13; i < 47; i++) expect(keyOf(after, i)).toBe(keyOf(before, i));
    const gone = [10, 11, 12].map((i) => keyOf(before, i));
    expect([47, 48, 49].map((i) => keyOf(after, i)).sort()).toEqual(gone.sort());
    // de volgorde (van de plekken) verandert niet
    expect(after.map((s) => s.key)).toEqual(before.map((s) => s.key));
  });

  it("dezelfde invoer twee keer: hetzelfde, ook met een budget (er is niets getekend)", () => {
    const l = list();
    l.draw(0, 20, null);
    expect(planSlots(l.drawn, 5, 25, null)).toEqual(planSlots(l.drawn, 5, 25, null));
    const once = planSlots(l.drawn, 40, 60, null, 8);
    expect(once.pending).toBe(true);
    expect(planSlots(l.drawn, 40, 60, null, 8)).toEqual(once);
  });

  it("de rij met de focus houdt zijn plek, ook buiten het bereik", () => {
    const l = list();
    const key = keyOf(l.draw(0, 30, null).slots, 5);
    const out = l.draw(100, 130, 5).slots;
    expect(keyOf(out, 5)).toBe(key);
    expect(out).toHaveLength(31);
    expect(new Set(out.map((s) => s.key)).size).toBe(31);
    // en terug in het bereik: nog steeds dezelfde
    expect(keyOf(l.draw(0, 30, 5).slots, 5)).toBe(key);
  });
});

describe("met een budget", () => {
  it("hooguit zoveel nieuwe rijen per getekende stap, die bij het midden eerst; dan de rest", () => {
    const l = list();
    l.draw(0, 20, null);
    // een sprong van een beeld: 20 nieuwe rijen, budget 8
    const first = l.draw(40, 60, null, 8);
    expect(first.pending).toBe(true);
    const shown = first.slots.map((s) => s.index).filter((i) => i >= 40);
    expect(shown).toHaveLength(8);
    // het dichtst bij het midden (49,5) eerst
    expect(shown.sort((x, y) => x - y)).toEqual([46, 47, 48, 49, 50, 51, 52, 53]);
    // de oude rijen blijven (buiten beeld) staan tot hun plek nodig is: geen nieuwe elementen
    expect(first.slots).toHaveLength(20);
    const second = l.draw(40, 60, null, 8);
    expect(second.pending).toBe(true);
    expect(second.slots.filter((s) => s.index >= 40)).toHaveLength(16);
    const third = l.draw(40, 60, null, 8);
    expect(third.pending).toBe(false);
    expect(third.slots.map((s) => s.index).sort((x, y) => x - y)).toEqual(
      Array.from({ length: 20 }, (_, i) => 40 + i),
    );
    // nog steeds dezelfde 20 plekken (sleutels), alleen hergebruikt
    expect(new Set(third.slots.map((s) => s.key))).toEqual(new Set(first.slots.map((s) => s.key)));
  });

  it("wat al staat telt niet mee; binnen het budget in één keer", () => {
    const l = list();
    const before = l.draw(10, 47, null, 10);
    expect(before.pending).toBe(true);
    l.draw(10, 47, null, 10);
    l.draw(10, 47, null, 10);
    expect(l.draw(10, 47, null, 10).pending).toBe(false);
    const after = l.draw(19, 56, null, 10);
    expect(after.pending).toBe(false);
    for (let i = 19; i < 47; i++) expect(keyOf(after.slots, i)).toBeDefined();
    expect(before.slots).toHaveLength(10);
  });

  it("de rij met de focus gaat voor", () => {
    const l = list();
    l.draw(0, 30, null);
    const out = l.draw(100, 130, 200, 1);
    expect(out.pending).toBe(true);
    expect(out.slots.map((s) => s.index)).toContain(200);
    expect(out.slots.filter((s) => s.index >= 100)).toHaveLength(1);
  });

  it("de rij met de focus gaat ook binnen het bereik voor (een toets wacht op hem)", () => {
    const l = list();
    l.draw(0, 30, null);
    // 129 ligt aan de rand van het bereik: zonder voorrang kwam hij pas in de laatste stap
    const out = l.draw(100, 130, 129, 1);
    expect(out.pending).toBe(true);
    expect(out.slots.filter((s) => s.index >= 100).map((s) => s.index)).toEqual([129]);
  });
});

describe("een render die React niet tekent", () => {
  it("laat niets achter: Home tijdens de stappen na End houdt het element van rij 0", () => {
    // Nagespeeld naar CI op 6f31a2c (test_integratie, test_03): de map staat eerst iets lager,
    // gaat naar boven met de focus op rij 0, één wielstap, dan End
    const l = list();
    l.draw(5, 32, null, Infinity);
    l.draw(0, 27, 0, Infinity);
    l.draw(26, 61, 0, 10);
    l.draw(26, 61, 9999, 10); // End: dringend, met het bereik van vóór End
    expect(l.draw(9973, 10000, 9999, 10).pending).toBe(true);
    expect(l.draw(9973, 10000, 9999, 10).pending).toBe(true);
    // Rij 0 staat nog in de DOM, als oude rij die op zijn beurt wacht
    const zero = l.drawn.get(0) as number;
    expect(zero).toBeDefined();

    // De volgende stap rekent React uit, maar tekent hem niet: Home komt ertussen. In die stap
    // ging de plek van rij 0 naar een rij van het eind
    const dropped = planSlots(l.drawn, 9973, 10000, 9999, 10);
    expect(dropped.slotOf.get(0)).toBeUndefined();
    const thief = rowAt(dropped.slotOf, zero);
    expect(thief).toBeGreaterThanOrEqual(9973);

    // Home zette de focus meteen op het element van rij 0 (focusRowNow). De render van Home
    // begint bij wat getekend is: rij 0 houdt dat element
    const home = l.draw(9973, 10000, 0, 10);
    expect(l.drawn.get(0)).toBe(zero);
    expect(keyOf(home.slots, 0)).toBe(`s${zero}`);
    expect(rowAt(l.drawn, zero)).toBe(0);

    // Zo ging het mis toen de weggegooide stap meetelde: het element met de focus werd die rij
    // van het eind, en rij 0 kreeg een nieuw element zonder focus
    const wrong = planSlots(dropped.slotOf, 9973, 10000, 0, 10);
    expect(wrong.slotOf.get(0)).not.toBe(zero);
    expect(rowAt(wrong.slotOf, zero)).toBe(thief);
  });
});
