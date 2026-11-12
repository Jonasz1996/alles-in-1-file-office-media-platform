import { describe, expect, it } from "vitest";
import { boxFromPoints, boxKey, boxPercent, clampBox, MIN_SIZE, moveBox, resizeBox, START_BOX, STEP } from "./box";

describe("het kader (Keuze 37)", () => {
  it("blijft binnen de foto en wordt niet kleiner dan MIN_SIZE", () => {
    expect(clampBox([-0.2, 1.4, 0.5, 0.5])).toEqual([0, 0.5, 0.5, 0.5]);
    expect(clampBox([0.9, 0.9, 0.5, 0.5])).toEqual([0.5, 0.5, 0.5, 0.5]);
    expect(clampBox([0.1, 0.1, 0, 2])).toEqual([0.1, 0, MIN_SIZE, 1]);
  });

  it("de pijlen verplaatsen, Shift met een pijl maakt groter of kleiner", () => {
    const b = START_BOX;
    expect(boxKey(b, "ArrowRight", false)).toEqual(moveBox(b, STEP, 0));
    expect(boxKey(b, "ArrowLeft", false)).toEqual([0.33, 0.25, 0.3, 0.4]);
    expect(boxKey(b, "ArrowUp", false)).toEqual([0.35, 0.23, 0.3, 0.4]);
    expect(boxKey(b, "ArrowDown", false)).toEqual([0.35, 0.27, 0.3, 0.4]);
    expect(boxKey(b, "ArrowRight", true)).toEqual([0.35, 0.25, 0.32, 0.4]);
    expect(boxKey(b, "ArrowLeft", true)).toEqual([0.35, 0.25, 0.28, 0.4]);
    expect(boxKey(b, "ArrowDown", true)).toEqual([0.35, 0.25, 0.3, 0.42]);
    expect(boxKey(b, "ArrowUp", true)).toEqual([0.35, 0.25, 0.3, 0.38]);
    expect(boxKey(b, "Enter", false)).toBeNull();
    expect(boxKey(b, "a", true)).toBeNull();
  });

  it("tegen de rand: verplaatsen stopt, groter maken schuift het kader terug", () => {
    let b = START_BOX;
    for (let i = 0; i < 100; i++) b = moveBox(b, STEP, STEP);
    expect(b).toEqual([0.7, 0.6, 0.3, 0.4]);
    for (let i = 0; i < 100; i++) b = resizeBox(b, STEP, STEP);
    expect(b).toEqual([0, 0, 1, 1]);
    for (let i = 0; i < 100; i++) b = resizeBox(b, -STEP, -STEP);
    expect(b).toEqual([0, 0, MIN_SIZE, MIN_SIZE]);
  });

  it("slepen geeft hetzelfde kader in elke richting, binnen de foto", () => {
    expect(boxFromPoints(0.2, 0.3, 0.6, 0.7)).toEqual([0.2, 0.3, 0.4, 0.4]);
    expect(boxFromPoints(0.6, 0.7, 0.2, 0.3)).toEqual([0.2, 0.3, 0.4, 0.4]);
    expect(boxFromPoints(-1, -1, 2, 2)).toEqual([0, 0, 1, 1]);
    // Een klik zonder slepen: het kleinste kader
    expect(boxFromPoints(0.5, 0.5, 0.5, 0.5)).toEqual([0.5, 0.5, MIN_SIZE, MIN_SIZE]);
  });

  it("in procenten voor de schermlezer", () => {
    expect(boxPercent(START_BOX)).toEqual({ x: 35, y: 25, w: 30, h: 40 });
  });
});
