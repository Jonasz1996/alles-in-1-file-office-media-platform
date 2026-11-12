import { describe, expect, it } from "vitest";
import { MAX_CUES, parseTrickVtt, resolveTile, trickAt, vttTime } from "./trick";

const ORIGIN = "https://cloud.example";
const BASE = "/api/v1/stream/node.abc/trick.vtt";

// Zoals make_vtt in app/media/trick.py (M5)
const VTT = `WEBVTT

00:00:00.000 --> 00:00:10.000
trick/0.jpg#xywh=0,0,160,90

00:00:10.000 --> 00:00:20.000
trick/0.jpg#xywh=160,0,160,90

00:01:40.000 --> 00:01:45.500
trick/1.jpg#xywh=0,90,160,90
`;

describe("trick.vtt lezen", () => {
  it("de cues met tijd, plaatje en vak", () => {
    const cues = parseTrickVtt(VTT, BASE, ORIGIN);
    expect(cues).toEqual([
      { start: 0, end: 10, url: "/api/v1/stream/node.abc/trick/0.jpg", x: 0, y: 0, w: 160, h: 90 },
      { start: 10, end: 20, url: "/api/v1/stream/node.abc/trick/0.jpg", x: 160, y: 0, w: 160, h: 90 },
      { start: 100, end: 105.5, url: "/api/v1/stream/node.abc/trick/1.jpg", x: 0, y: 90, w: 160, h: 90 },
    ]);
  });

  it("de cue bij een tijd (wat de speler bij hover toont)", () => {
    const cues = parseTrickVtt(VTT, BASE, ORIGIN);
    expect(trickAt(cues, 0)?.x).toBe(0);
    expect(trickAt(cues, 15)?.x).toBe(160);
    expect(trickAt(cues, 20)).toBeNull();
    expect(trickAt(cues, 101)?.url).toContain("trick/1.jpg");
    expect(trickAt([], 3)).toBeNull();
  });

  it("tijden van WebVTT", () => {
    expect(vttTime("00:01:02.003")).toBeCloseTo(62.003);
    expect(vttTime("01:02.500")).toBeCloseTo(62.5);
    expect(vttTime("1:00:00,000")).toBe(3600);
    expect(vttTime("abc")).toBeNull();
    expect(vttTime("00:61:00.000")).toBeNull();
  });

  it("alleen plaatjes op dezelfde origin", () => {
    expect(resolveTile("trick/2.jpg", BASE, ORIGIN)).toBe("/api/v1/stream/node.abc/trick/2.jpg");
    expect(resolveTile("https://elders.example/x.jpg", BASE, ORIGIN)).toBeNull();
    expect(resolveTile("//elders.example/x.jpg", BASE, ORIGIN)).toBeNull();
    expect(resolveTile("javascript:alert(1)", BASE, ORIGIN)).toBeNull();
  });

  it("kapotte regels slaat hij over", () => {
    const bad = `WEBVTT

00:00:10.000 --> 00:00:05.000
trick/0.jpg#xywh=0,0,160,90

00:00:00.000 --> 00:00:10.000
trick/0.jpg#xywh=0,0,0,90

00:00:00.000 --> 00:00:10.000
trick/0.jpg#xywh=a,b,c,d

00:00:00.000 --> 00:00:10.000
trick/0.jpg

00:00:20.000 --> 00:00:30.000
trick/3.jpg#xywh=0,0,160,90
`;
    const cues = parseTrickVtt(bad, BASE, ORIGIN);
    expect(cues.map((c) => c.start)).toEqual([20]);
  });

  it("begrensd: hooguit MAX_CUES", () => {
    const many = ["WEBVTT", ""];
    for (let i = 0; i < MAX_CUES + 50; i++) many.push(`00:00:00.000 --> 00:00:01.000`, `t/0.jpg#xywh=0,0,1,1`, "");
    expect(parseTrickVtt(many.join("\n"), BASE, ORIGIN).length).toBe(MAX_CUES);
  });
});

/** Een vaste PRNG (mulberry32): elke run dezelfde invoer (regel 10). */
function prng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

describe("fuzz (vaste seed)", () => {
  it("willekeurige en verminkte invoer: nooit een fout, altijd geldige cues", () => {
    const rnd = prng(20261029);
    const pieces = ["WEBVTT", "\n", "\r\n", "-->", " --> ", "00:00:0", "1.000", "#xywh=", ",", "160", "-1", "trick/", ".jpg", "\u0000", "é", "99:59:59.999", "//x/", ":"];
    for (let n = 0; n < 2000; n++) {
      let text: string;
      if (n % 2 === 0) {
        text = Array.from({ length: Math.floor(rnd() * 60) }, () => pieces[Math.floor(rnd() * pieces.length)]).join("");
      } else {
        const chars = [...VTT];
        for (let k = 0; k < 1 + Math.floor(rnd() * 8); k++) {
          const at = Math.floor(rnd() * chars.length);
          chars.splice(at, Math.floor(rnd() * 3), String.fromCharCode(Math.floor(rnd() * 128)));
        }
        text = chars.join("");
      }
      const cues = parseTrickVtt(text, BASE, ORIGIN);
      for (const c of cues) {
        expect(c.end).toBeGreaterThan(c.start);
        expect(c.w).toBeGreaterThan(0);
        expect(c.h).toBeGreaterThan(0);
        expect(c.url.startsWith("/")).toBe(true);
        expect(c.url.startsWith("//")).toBe(false);
      }
      for (let i = 1; i < cues.length; i++) expect(cues[i].start).toBeGreaterThanOrEqual(cues[i - 1].start);
      expect(() => trickAt(cues, rnd() * 200)).not.toThrow();
    }
  });
});
