import { describe, expect, it } from "vitest";
import { best, score } from "./spatial";

const box = (left: number, top: number, w = 100, h = 100) => ({ left, top, right: left + w, bottom: top + h });

describe("spatial", () => {
  it("kiest in een rij de buur ernaast", () => {
    const from = box(0, 0);
    const row = [box(120, 0), box(240, 0), box(0, 120)];
    expect(best(from, row, "right")).toBe(0);
    expect(best(from, row, "down")).toBe(2);
    expect(best(from, row, "left")).toBe(-1);
  });

  it("verkiest dezelfde kolom boven een dichtere schuine buur", () => {
    const from = box(300, 0);
    expect(best(from, [box(0, 130), box(300, 200)], "down")).toBe(1);
  });

  it("niets in de verkeerde richting", () => {
    expect(score(box(0, 0), box(0, 200), "up")).toBeNull();
  });
});
