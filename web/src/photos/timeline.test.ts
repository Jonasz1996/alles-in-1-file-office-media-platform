import { describe, expect, it } from "vitest";
import {
  buildLayout,
  fractionToOffset,
  moveInLayout,
  offsetToFraction,
  rowAtOffset,
  rowInfo,
  rowOfItem,
  scrubStep,
  sectionAtOffset,
  sectionOffset,
  sectionOfItem,
  sectionsInRows,
  yearMarks,
} from "./timeline";

// Drie maanden: 5, 1 en 7 foto's, 3 kolommen, rijen van 100 px en koppen van 40 px
const l = buildLayout([5, 1, 7], 3, 100, 40);

describe("indeling", () => {
  it("rijen en hoogtes uit de aantallen", () => {
    // kop, 2 rijen | kop, 1 rij | kop, 3 rijen
    expect(l.rowCount).toBe(9);
    expect(l.total).toBe(13);
    expect(l.offsets).toEqual([0, 40, 140, 240, 280, 380, 420, 520, 620, 720]);
    expect(l.firstItem).toEqual([0, 5, 6, 13]);
    expect(l.firstRow).toEqual([0, 3, 5, 9]);
  });

  it("wat er in een rij staat", () => {
    expect(rowInfo(l, 0)).toEqual({ kind: "header", section: 0 });
    expect(rowInfo(l, 1)).toEqual({ kind: "photos", section: 0, first: 0, count: 3 });
    expect(rowInfo(l, 2)).toEqual({ kind: "photos", section: 0, first: 3, count: 2 });
    expect(rowInfo(l, 3)).toEqual({ kind: "header", section: 1 });
    expect(rowInfo(l, 4)).toEqual({ kind: "photos", section: 1, first: 5, count: 1 });
    expect(rowInfo(l, 8)).toEqual({ kind: "photos", section: 2, first: 12, count: 1 });
  });

  it("item ↔ rij en sectie", () => {
    expect([0, 2, 3, 4, 5, 6, 11, 12].map((i) => rowOfItem(l, i))).toEqual([1, 1, 2, 2, 4, 6, 7, 8]);
    expect([0, 4, 5, 6, 12].map((i) => sectionOfItem(l, i))).toEqual([0, 0, 1, 2, 2]);
  });

  it("zonder koppen (een album) en met lege secties", () => {
    const a = buildLayout([4], 2, 50, 40, false);
    expect(a.rowCount).toBe(2);
    expect(a.offsets).toEqual([0, 50, 100]);
    expect(rowInfo(a, 1)).toEqual({ kind: "photos", section: 0, first: 2, count: 2 });
    expect(buildLayout([0], 3, 50, 40, false).rowCount).toBe(0);
    const gaps = buildLayout([2, 0, 2], 2, 50, 40, false);
    expect(sectionOfItem(gaps, 2)).toBe(2);
    expect(rowInfo(gaps, 1)).toEqual({ kind: "photos", section: 2, first: 2, count: 2 });
  });

  it("de rij en sectie op een hoogte", () => {
    expect(rowAtOffset(l, 0)).toBe(0);
    expect(rowAtOffset(l, 39)).toBe(0);
    expect(rowAtOffset(l, 40)).toBe(1);
    expect(rowAtOffset(l, 10_000)).toBe(8);
    expect(sectionAtOffset(l, 300)).toBe(1);
    expect(sectionOffset(l, 2)).toBe(380);
  });

  it("welke secties in beeld zijn", () => {
    expect(sectionsInRows(l, 0, 2)).toEqual([0]);
    expect(sectionsInRows(l, 2, 6)).toEqual([0, 1, 2]);
    expect(sectionsInRows(l, 5, 100)).toEqual([2]);
    expect(sectionsInRows(l, 3, 3)).toEqual([]);
  });

  it("50.000 foto's over 137 maanden: snel en met de goede hoogte", () => {
    const counts = Array.from({ length: 137 }, (_, k) => (k === 136 ? 50_000 - 136 * 365 : 365));
    const t0 = performance.now();
    const big = buildLayout(counts, 6, 188, 48);
    expect(performance.now() - t0).toBeLessThan(50);
    expect(big.total).toBe(50_000);
    expect(big.rowCount).toBe(137 + counts.reduce((n, c) => n + Math.ceil(c / 6), 0));
    expect(rowInfo(big, big.rowCount - 1).kind).toBe("photos");
    expect(rowOfItem(big, 49_999)).toBe(big.rowCount - 1);
  });
});

describe("bewegen met het toetsenbord", () => {
  it("links en rechts door de volgorde, ook over een maand heen", () => {
    expect(moveInLayout(l, 4, "right")).toBe(5);
    expect(moveInLayout(l, 5, "left")).toBe(4);
    expect(moveInLayout(l, 0, "left")).toBe(0);
    expect(moveInLayout(l, 12, "right")).toBe(12);
  });

  it("op en neer naar dezelfde kolom, koppen overslaan, korte rij → laatste", () => {
    expect(moveInLayout(l, 1, "down")).toBe(4); // kolom 1, rij 2 (3, 4)
    expect(moveInLayout(l, 4, "down")).toBe(5); // de maand met één foto
    expect(moveInLayout(l, 2, "down")).toBe(4); // rij 2 heeft maar twee foto's
    expect(moveInLayout(l, 5, "down")).toBe(6);
    expect(moveInLayout(l, 7, "up")).toBe(5);
    expect(moveInLayout(l, 0, "up")).toBe(0);
    expect(moveInLayout(l, 12, "down")).toBe(12);
  });

  it("Home, End, PageUp en PageDown", () => {
    expect(moveInLayout(l, 7, "home")).toBe(0);
    expect(moveInLayout(l, 0, "end")).toBe(12);
    expect(moveInLayout(l, 0, "pagedown", 2)).toBe(5);
    expect(moveInLayout(l, 0, "pagedown", 50)).toBe(12);
    expect(moveInLayout(l, 12, "pageup", 50)).toBe(0);
    expect(moveInLayout(l, -1, "down")).toBe(0);
    expect(moveInLayout(l, -1, "end")).toBe(12);
    expect(moveInLayout(buildLayout([], 3, 1, 1), 0, "down")).toBe(-1);
  });
});

describe("scrubber", () => {
  it("plek op de balk ↔ scrollpositie", () => {
    // 720 px hoog, 220 px in beeld: 500 px te scrollen
    expect(offsetToFraction(l, 0, 220)).toBe(0);
    expect(offsetToFraction(l, 250, 220)).toBe(0.5);
    expect(offsetToFraction(l, 9999, 220)).toBe(1);
    expect(fractionToOffset(l, 0.5, 220)).toBe(250);
    expect(fractionToOffset(l, -1, 220)).toBe(0);
    expect(fractionToOffset(l, 2, 220)).toBe(500);
    // Past alles in beeld, dan is er niets te scrollen
    expect(offsetToFraction(l, 100, 2000)).toBe(0);
    for (const f of [0, 0.25, 0.8, 1]) expect(offsetToFraction(l, fractionToOffset(l, f, 220), 220)).toBeCloseTo(f, 2);
  });

  it("jaren op de balk", () => {
    const months = ["2025-02", "2025-01", "2024-12"];
    expect(yearMarks(l, months, 220)).toEqual([
      { year: "2025", section: 0, fraction: 0 },
      { year: "2024", section: 2, fraction: 380 / 500 },
    ]);
  });

  it("stappen met het toetsenbord: maand, jaar, begin en eind", () => {
    const months = ["2025-02", "2025-01", "2024-12", "2024-06", "2023-03"];
    expect(scrubStep(months, 0, "next")).toBe(1);
    expect(scrubStep(months, 0, "prev")).toBe(0);
    expect(scrubStep(months, 0, "nextYear")).toBe(2);
    expect(scrubStep(months, 3, "nextYear")).toBe(4);
    expect(scrubStep(months, 3, "prevYear")).toBe(0);
    expect(scrubStep(months, 4, "prevYear")).toBe(2);
    expect(scrubStep(months, 4, "nextYear")).toBe(4);
    expect(scrubStep(months, 2, "first")).toBe(0);
    expect(scrubStep(months, 2, "last")).toBe(4);
    expect(scrubStep([], 0, "next")).toBe(-1);
  });
});
