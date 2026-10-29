import { describe, expect, it } from "vitest";
import type { GridSegments, SegmentGrid } from "../media/types";
import {
  aheadOf,
  evictable,
  gapTarget,
  Inits,
  mediaEnd,
  nextSegment,
  segmentAt,
  segmentTime,
  type Span,
  spanAt,
  spans,
  Stalls,
} from "./mse";

// Het rooster van een transcode (Keuze 22): origin 21 ms (AAC), segmenten van 2 s
const GRID: GridSegments = { grid_ms: 2000, origin_ms: 21, count: 50, last_frame_ms: 99_980 };
// De lijst van een remux: segmenten op de keyframes
const LIST: SegmentGrid = [
  [0, 4000],
  [4000, 2500],
  [6500, 6000],
  [12500, 3000],
];

describe("het segment voor tijd t", () => {
  it("rooster: segment 0 loopt tot origin + 2 s, daarna per 2 s", () => {
    expect(segmentAt(GRID, 0)).toBe(0);
    expect(segmentAt(GRID, 2.0)).toBe(0);
    expect(segmentAt(GRID, 2.021)).toBe(1);
    expect(segmentAt(GRID, 10)).toBe(4);
    expect(segmentAt(GRID, 500)).toBe(49);
    expect(segmentTime(GRID, 0)).toBe(0);
    expect(segmentTime(GRID, 3)).toBeCloseTo(6.021);
  });

  it("lijst: het laatste segment dat op of voor t begint", () => {
    expect(segmentAt(LIST, 0)).toBe(0);
    expect(segmentAt(LIST, 4.0)).toBe(1);
    expect(segmentAt(LIST, 6.4)).toBe(1);
    expect(segmentAt(LIST, 6.5)).toBe(2);
    expect(segmentAt(LIST, 99)).toBe(3);
    expect(mediaEnd(LIST)).toBe(15500);
    expect(mediaEnd(GRID)).toBe(99_980);
  });

  it("na een sprong in de buffer: het segment na het gebufferde stuk", () => {
    const buf: Span[] = [[0, 10.02]];
    // t ligt erin: verder na 10,02 s (segment 5 begint op 10,021)
    expect(nextSegment(buf, 3, GRID)).toBe(5);
    // Een stuk dat net voor de grens eindigt (het geluid): toch het volgende segment
    expect(nextSegment([[0, 9.95]], 3, GRID)).toBe(5);
    // Een half segment (afgebroken): dat segment opnieuw
    expect(nextSegment([[0, 9.0]], 3, GRID)).toBe(4);
    // Buiten de buffer: het segment van t zelf
    expect(nextSegment(buf, 40, GRID)).toBe(19);
    // Tot het eind gebufferd: niets meer
    expect(nextSegment([[90, 99.98]], 95, GRID)).toBe(50);
    expect(nextSegment([[0, 15.5]], 14, LIST)).toBe(4);
  });
});

describe("wat er weg mag", () => {
  it("alles wat meer dan 60 s achter de positie ligt", () => {
    expect(evictable([[0, 100]], 70)).toEqual([[0, 10]]);
    expect(evictable([[0, 100]], 50)).toEqual([]);
    expect(evictable([[20, 30], [80, 100]], 90)).toEqual([[20, 30]]);
  });

  it("met aheadMax ook wat te ver vooruit ligt (na QuotaExceededError: 10 s)", () => {
    expect(evictable([[0, 60]], 20, 5, 10)).toEqual([
      [0, 15],
      [30, 60],
    ]);
    expect(evictable([[18, 25]], 20, 5, 10)).toEqual([]);
  });

  it("niets bij een lege buffer", () => {
    expect(evictable([], 100)).toEqual([]);
  });
});

describe("gen wisselen: X-Init is een sleutel, geen teller", () => {
  it("A, B, A: elke wissel een init, en A uit de cache", () => {
    const inits = new Inits();
    const fetched: string[] = [];
    const appended: string[] = [];
    // De echte routes geven bij A, B, A de generaties 1, 2, 1 (m4.md, afwijking 5)
    for (const key of ["1", "1", "2", "2", "1", "1"]) {
      if (inits.needs(key)) {
        if (!inits.has(key)) {
          fetched.push(key);
          inits.put(key, new ArrayBuffer(8));
        }
        appended.push(key);
        inits.current = key;
      }
    }
    expect(appended).toEqual(["1", "2", "1"]);
    expect(fetched).toEqual(["1", "2"]);
  });

  it("een lagere sleutel is niet oud", () => {
    const inits = new Inits();
    inits.current = "3";
    expect(inits.needs("1")).toBe(true);
    expect(inits.needs("3")).toBe(false);
  });

  it("een segment zonder kop houdt de init die er is", () => {
    const inits = new Inits();
    inits.current = "1";
    expect(inits.needs(null)).toBe(false);
    expect(inits.needs("")).toBe(false);
  });
});

describe("de beslissing over een gat (S4)", () => {
  it("het volgende stuk binnen 0,5 s: daarheen", () => {
    expect(gapTarget([[0, 10], [10.054, 20]], 10)).toBeCloseTo(10.054);
    expect(gapTarget([[0, 10], [10.5, 20]], 10)).toBeCloseTo(10.5);
    // Late video (late_video.mkv): het beeld begint pas op 0,5 s
    expect(gapTarget([[0.48, 6]], 0)).toBeCloseTo(0.48);
  });

  it("verder weg, of geen volgend stuk: wachten op data", () => {
    expect(gapTarget([[0, 10], [10.6, 20]], 10)).toBeNull();
    expect(gapTarget([[0, 10]], 10)).toBeNull();
    expect(gapTarget([], 3)).toBeNull();
  });

  it("midden in een stuk is geen gat", () => {
    expect(gapTarget([[0, 10], [10.1, 20]], 5)).toBeNull();
  });
});

describe("buffer", () => {
  it("TimeRanges als lijst, het stuk bij t en wat er vooruit is", () => {
    const r = { length: 2, start: (i: number) => [0, 30][i], end: (i: number) => [10, 40][i] };
    const list = spans(r);
    expect(list).toEqual([
      [0, 10],
      [30, 40],
    ]);
    expect(spanAt(list, 5)).toEqual([0, 10]);
    expect(spanAt(list, 29.95)).toEqual([30, 40]);
    expect(spanAt(list, 20)).toBeNull();
    expect(aheadOf(list, 32)).toBe(8);
    expect(aheadOf(list, 20)).toBe(0);
  });
});

describe("haperingen", () => {
  it("twee binnen een minuut: een trede lager; daarna begint het tellen opnieuw", () => {
    const s = new Stalls();
    expect(s.add(0)).toBe(false);
    expect(s.add(30_000)).toBe(true);
    expect(s.add(31_000)).toBe(false);
  });

  it("twee met meer dan een minuut ertussen: niets", () => {
    const s = new Stalls();
    expect(s.add(0)).toBe(false);
    expect(s.add(61_000)).toBe(false);
  });
});
