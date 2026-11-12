import { describe, expect, it } from "vitest";
import { firstStep, nextStep, overlaps } from "./rows";

describe("sprongen in het fotoraster", () => {
  const shown = { start: 0, end: 12 };

  it("overlaps: gewoon scrollen of een sprong", () => {
    expect(overlaps(shown, { start: 3, end: 15 })).toBe(true);
    expect(overlaps(shown, { start: 12, end: 20 })).toBe(false);
  });

  it("gewoon scrollen: meteen het hele bereik", () => {
    expect(firstStep(shown, { start: 3, end: 15 }, { start: 6, end: 12 })).toEqual({ start: 3, end: 15 });
  });

  it("een sprong: eerst alleen wat in beeld komt, de marge een frame later", () => {
    const wanted = { start: 497, end: 509 };
    const view = { start: 500, end: 506 };
    const first = firstStep(shown, wanted, view);
    expect(first).toEqual(view);
    expect(nextStep(first, wanted)).toEqual(wanted);
    expect(nextStep(wanted, wanted)).toBeNull();
  });

  it("is de sprong nog niet getekend (de transition loopt), dan nog niets", () => {
    expect(nextStep(shown, { start: 497, end: 509 })).toBeNull();
  });
});
