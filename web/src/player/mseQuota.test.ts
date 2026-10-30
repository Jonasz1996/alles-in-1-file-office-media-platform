// De lader bij een vol quotum (review golf 3, C6), met een nep-MediaSource en -SourceBuffer in
// node: na QuotaExceededError haalt stap 2 alles na t + 10 s weg. Daarna moet de lader verder bij
// het eind van de buffer, niet na het segment dat hij net toevoegde: anders bleef er een gat van
// t + 10 tot dat segment, en stond de video daar stil.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GridSegments } from "../media/types";
import { aheadOf, MseLoader, type Span, segmentTime } from "./mse";

// Segmenten van 2 s, 100 s lang
const GRID: GridSegments = { grid_ms: 2000, origin_ms: 0, count: 50, last_frame_ms: 99_980 };

/** Een SourceBuffer met een quotum in seconden. Een segment is [begin, eind] als twee doubles. */
class FakeSourceBuffer extends EventTarget {
  mode = "segments";
  timestampOffset = 0;
  updating = false;
  spans: [number, number][] = [];
  quotaHits = 0;

  constructor(private readonly quotaS: number) {
    super();
  }

  get buffered(): Pick<TimeRanges, "length" | "start" | "end"> {
    const list = this.spans;
    return { length: list.length, start: (i) => list[i][0], end: (i) => list[i][1] };
  }

  private total(): number {
    return this.spans.reduce((n, [a, b]) => n + (b - a), 0);
  }

  private later(change: () => void): void {
    this.updating = true;
    queueMicrotask(() => {
      change();
      this.updating = false;
      this.dispatchEvent(new Event("updateend"));
    });
  }

  appendBuffer(data: ArrayBuffer): void {
    if (data.byteLength !== 16) return this.later(() => undefined); // een init
    const [a, b] = new Float64Array(data);
    if (this.total() + (b - a) > this.quotaS) {
      this.quotaHits++;
      throw new DOMException("vol", "QuotaExceededError");
    }
    this.later(() => this.add(a, b));
  }

  remove(a: number, b: number): void {
    this.later(() => {
      const out: [number, number][] = [];
      for (const [x, y] of this.spans) {
        if (y <= a || x >= b) out.push([x, y]);
        else {
          if (x < a) out.push([x, a]);
          if (y > b) out.push([b, y]);
        }
      }
      this.spans = out;
    });
  }

  abort(): void {}

  private add(a: number, b: number): void {
    const all = [...this.spans, [a, b] as [number, number]].sort((p, q) => p[0] - q[0]);
    const out: [number, number][] = [];
    for (const s of all) {
      const last = out[out.length - 1];
      if (last && s[0] <= last[1] + 1e-6) last[1] = Math.max(last[1], s[1]);
      else out.push([s[0], s[1]]);
    }
    this.spans = out;
  }
}

let sb: FakeSourceBuffer;

class FakeMediaSource extends EventTarget {
  readyState = "open";
  duration = 0;
  static isTypeSupported(): boolean {
    return true;
  }
  constructor() {
    super();
    queueMicrotask(() => this.dispatchEvent(new Event("sourceopen")));
  }
  addSourceBuffer(): FakeSourceBuffer {
    return sb;
  }
  endOfStream(): void {}
}

class FakeVideo extends EventTarget {
  currentTime = 0;
  readyState = 4;
  seeking = false;
  src = "";
  removeAttribute(): void {}
  load(): void {}
}

async function settle(): Promise<void> {
  for (let i = 0; i < 15; i++) await new Promise((ok) => setTimeout(ok, 0));
}

const fetchFn = (async (url: string) => {
  const found = /\/(\d+)\.m4s/.exec(url);
  if (!found) return new Response(new Uint8Array([1]));
  const k = Number(found[1]);
  const a = segmentTime(GRID, k);
  return new Response(new Float64Array([a, a + 2]).buffer);
}) as unknown as typeof fetch;

describe("de lader na QuotaExceededError (C6)", () => {
  beforeEach(() => {
    vi.stubGlobal("MediaSource", FakeMediaSource);
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:nep");
    vi.spyOn(URL, "revokeObjectURL").mockReturnValue(undefined);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("vult vanaf het eind van de buffer en laat geen gat tot het segment van de fout", async () => {
    sb = new FakeSourceBuffer(24); // 12 segmenten; het 13e (24-26 s) geeft de fout
    const video = new FakeVideo();
    const errors: unknown[] = [];
    const loader = new MseLoader(
      video as unknown as HTMLVideoElement,
      { sid: "s", duration: 100, map: { gen: 1, mime: "video/mp4", offset: 0, init: "init.mp4", segments: GRID } },
      { error: (e) => errors.push(e) },
      fetchFn,
    );
    await loader.open(0);
    await settle();
    expect(sb.quotaHits).toBeGreaterThan(0);
    // De video speelt verder: bij elke stap moet er aansluitend data na de positie staan
    for (let t = 1; t <= 30; t++) {
      video.currentTime = t;
      video.dispatchEvent(new Event("timeupdate"));
      await settle();
      const list: Span[] = sb.spans;
      expect(aheadOf(list, t), `t = ${t}: ${JSON.stringify(list)}`).toBeGreaterThan(1);
    }
    expect(errors).toEqual([]);
    loader.destroy();
  });
});
