import { describe, expect, it } from "vitest";
import { etaSeconds, RateMeter, roundEta } from "./eta";

describe("ETA", () => {
  it("glijdend gemiddelde over het venster, niet de laatste seconde", () => {
    const m = new RateMeter(4000, 1000);
    m.add(0, 0);
    expect(m.rate(500)).toBeNull(); // te kort gemeten
    m.add(1000, 1000);
    expect(m.rate(1000)).toBe(1000);
    m.add(2000, 2000);
    m.add(8000, 3000); // één snelle seconde
    expect(m.rate(3000)).toBeCloseTo(8000 / 3, 5);
    m.add(9000, 4000);
    m.add(10_000, 5000); // nu valt 0 ms uit het venster, 1000 ms blijft
    expect(m.rate(5000)).toBe((10_000 - 1000) / 4);
  });

  it("stilstand laat de snelheid zakken; minder bytes begint opnieuw", () => {
    const m = new RateMeter(10_000, 1000);
    m.add(0, 0);
    m.add(1000, 1000);
    expect(m.rate(1000)).toBe(1000);
    expect(m.rate(4000)).toBe(250);
    m.add(500, 5000); // een upload geannuleerd
    expect(m.rate(5000)).toBeNull();
  });

  it("resterende tijd en afronden", () => {
    expect(etaSeconds(1000, 100)).toBe(10);
    expect(etaSeconds(1000, null)).toBeNull();
    expect(etaSeconds(1000, 0)).toBeNull();
    expect(etaSeconds(0, null)).toBe(0);
    expect(roundEta(3)).toEqual({ unit: "s", value: 5 });
    expect(roundEta(42)).toEqual({ unit: "s", value: 45 });
    expect(roundEta(370)).toEqual({ unit: "min", value: 6 });
    expect(roundEta(2 * 3600 + 15 * 60)).toEqual({ unit: "h", value: 2, minutes: 15 });
  });
});
