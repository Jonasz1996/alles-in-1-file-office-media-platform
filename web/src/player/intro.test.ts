import { describe, expect, it } from "vitest";
import { introSkip } from "./intro";

describe("introSkip", () => {
  it("alleen in de intro, tot een seconde voor het eind", () => {
    const intro = [20_000, 50_000];
    expect(introSkip(10, intro)).toBeNull();
    expect(introSkip(20, intro)).toBe(50);
    expect(introSkip(48.9, intro)).toBe(50);
    expect(introSkip(49, intro)).toBeNull();
    expect(introSkip(30, null)).toBeNull();
    expect(introSkip(30, [50_000, 20_000])).toBeNull();
  });
});
