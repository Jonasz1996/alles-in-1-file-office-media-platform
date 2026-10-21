import { describe, expect, it } from "vitest";
import { columnsFor, extraIndex, fixedRange, prefixOffsets, scrollTarget, variableRange } from "./virtual";

describe("fixedRange", () => {
  it("de zichtbare rijen plus overscan", () => {
    // rijen van 44 px, venster van 440 px: rij 0..9 zichtbaar
    expect(fixedRange(0, 440, 44, 10_000, 5)).toEqual({ start: 0, end: 15 });
    // halverwege rij 100
    expect(fixedRange(100 * 44 + 22, 440, 44, 10_000, 5)).toEqual({ start: 95, end: 116 });
  });

  it("blijft binnen 0..count", () => {
    expect(fixedRange(0, 440, 44, 3, 5)).toEqual({ start: 0, end: 3 });
    expect(fixedRange(1_000_000, 440, 44, 50, 2)).toEqual({ start: 50, end: 50 });
    expect(fixedRange(-100, 440, 44, 50, 0)).toEqual({ start: 0, end: 10 });
    expect(fixedRange(0, 440, 44, 0, 5)).toEqual({ start: 0, end: 0 });
  });
});

describe("variableRange", () => {
  const offsets = prefixOffsets(5, (i) => (i + 1) * 10); // hoogtes 10, 20, 30, 40, 50

  it("prefixOffsets: lengte n + 1, begint bij 0", () => {
    expect(offsets).toEqual([0, 10, 30, 60, 100, 150]);
  });

  it("de rijen die het venster raakt", () => {
    expect(variableRange(offsets, 0, 15, 0)).toEqual({ start: 0, end: 2 });
    expect(variableRange(offsets, 0, 30, 0)).toEqual({ start: 0, end: 2 }); // precies tot de rand
    expect(variableRange(offsets, 35, 30, 0)).toEqual({ start: 2, end: 4 });
    expect(variableRange(offsets, 35, 30, 1)).toEqual({ start: 1, end: 5 });
  });

  it("voorbij het einde, en leeg", () => {
    expect(variableRange(offsets, 500, 30, 1)).toEqual({ start: 4, end: 5 });
    expect(variableRange([0], 0, 100, 3)).toEqual({ start: 0, end: 0 });
  });

  it("komt overeen met fixedRange bij vaste hoogtes", () => {
    const even = prefixOffsets(1000, () => 44);
    for (const top of [0, 1, 43, 44, 1000, 20_000, 43_000]) {
      expect(variableRange(even, top, 500, 3)).toEqual(fixedRange(top, 500, 44, 1000, 3));
    }
  });
});

describe("extraIndex (keepIndex)", () => {
  it("een rij buiten beeld met de focus wordt extra gerenderd", () => {
    const range = { start: 100, end: 120 };
    expect(extraIndex(range, 5, 1000)).toBe(5);
    expect(extraIndex(range, 120, 1000)).toBe(120); // end is exclusief
    expect(extraIndex(range, 500, 1000)).toBe(500);
  });

  it("binnen beeld, geen focus of ongeldig: niets extra", () => {
    const range = { start: 100, end: 120 };
    expect(extraIndex(range, 100, 1000)).toBeNull();
    expect(extraIndex(range, 119, 1000)).toBeNull();
    expect(extraIndex(range, null, 1000)).toBeNull();
    expect(extraIndex(range, undefined, 1000)).toBeNull();
    expect(extraIndex(range, -1, 1000)).toBeNull();
    expect(extraIndex(range, 1000, 1000)).toBeNull(); // bestaat niet (meer)
  });
});

describe("scrollTarget", () => {
  it("auto: alleen scrollen als de rij (deels) buiten beeld is", () => {
    expect(scrollTarget(100, 144, 0, 500, "auto")).toBeNull();
    expect(scrollTarget(600, 644, 0, 500, "auto")).toBe(144);
    expect(scrollTarget(100, 144, 200, 500, "auto")).toBe(100);
  });

  it("start en center", () => {
    expect(scrollTarget(600, 644, 0, 500, "start")).toBe(600);
    expect(scrollTarget(600, 700, 0, 500, "center")).toBe(400);
  });
});

describe("columnsFor", () => {
  it("zoveel cellen van minstens minCell als er passen", () => {
    expect(columnsFor(1000, 160)).toEqual({ columns: 6, cellWidth: 1000 / 6 });
    expect(columnsFor(1000, 160, 8)).toEqual({ columns: 6, cellWidth: (1000 - 40) / 6 });
    expect(columnsFor(100, 160).columns).toBe(1);
    expect(columnsFor(0, 160)).toEqual({ columns: 1, cellWidth: 0 });
  });
});
