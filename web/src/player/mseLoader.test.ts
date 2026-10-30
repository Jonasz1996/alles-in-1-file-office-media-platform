// De lader zelf, met een nep-MediaSource en -SourceBuffer in node (review golf 3):
// - C6: na QuotaExceededError haalt stap 2 alles na t + 10 s weg. Daarna moet de lader verder bij
//   het eind van de buffer, niet na het segment dat hij net toevoegde: anders bleef er een gat van
//   t + 10 tot dat segment, en stond de video daar stil;
// - K3: retry (de herstel-lus na een 503, bijvoorbeeld de worker gedood) en een pump die timeupdate
//   start, mogen nooit tegelijk aan de SourceBuffer: dat gaf "This SourceBuffer is still
//   processing an 'appendBuffer'" (InvalidStateError) en de speler stopte met een fout.
// - CI-run 41002597837: na de herstel-lus komt een segment van een nieuwe run waarvan het geluid
//   niet aansluit (H.264 met B-frames en AAC: 41 ms later dan het eind van het vorige). In
//   buffered is dat een gat, maar de video stond daar al stil: de springer moet ook dan nog kijken.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GridSegments } from "../media/types";
import { aheadOf, GAP_WAIT_MS, MseLoader, type Span, segmentTime } from "./mse";

// Segmenten van 2 s, 100 s lang
const GRID: GridSegments = { grid_ms: 2000, origin_ms: 0, count: 50, last_frame_ms: 99_980 };

/** Een SourceBuffer met een quotum in seconden. Een segment is [begin, eind] als twee doubles. */
class FakeSourceBuffer extends EventTarget {
  mode = "segments";
  timestampOffset = 0;
  updating = false;
  spans: [number, number][] = [];
  quotaHits = 0;
  appended: number[] = [];

  /** delayMs: zo lang duurt een bewerking (0: een microtask) */
  constructor(
    private readonly quotaS: number,
    private readonly delayMs = 0,
  ) {
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
    // Zoals een echte SourceBuffer: een tweede bewerking terwijl er een loopt, gooit
    if (this.updating) throw new DOMException("still processing", "InvalidStateError");
    this.updating = true;
    const done = () => {
      change();
      this.updating = false;
      this.dispatchEvent(new Event("updateend"));
    };
    if (this.delayMs > 0) setTimeout(done, this.delayMs);
    else queueMicrotask(done);
  }

  appendBuffer(data: ArrayBuffer): void {
    if (data.byteLength !== 16) return this.later(() => undefined); // een init
    if (this.updating) throw new DOMException("still processing", "InvalidStateError");
    const [a, b] = new Float64Array(data);
    this.appended.push(a);
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

/** Wachten tot cond (hooguit 3 s). */
async function until(cond: () => boolean): Promise<void> {
  const end = Date.now() + 3000;
  while (!cond() && Date.now() < end) await new Promise((ok) => setTimeout(ok, 5));
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

describe("retry na een fout en timeupdate tegelijk (K3)", () => {
  beforeEach(() => {
    vi.stubGlobal("MediaSource", FakeMediaSource);
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:nep");
    vi.spyOn(URL, "revokeObjectURL").mockReturnValue(undefined);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("retry houdt de lader vast: geen tweede append ernaast, en daarna laadt hij verder", async () => {
    sb = new FakeSourceBuffer(1000, 5);
    const video = new FakeVideo();
    const errors: unknown[] = [];
    // Segment 3: eerst 503 (de worker is weg), daarna pas als de nieuwe worker er is (release)
    let release: () => void = () => {};
    const back = new Promise<void>((ok) => (release = ok));
    let failedOnce = false;
    const gated = (async (url: string) => {
      if (/\/3\.m4s/.test(url)) {
        if (!failedOnce) {
          failedOnce = true;
          return new Response(JSON.stringify({ detail: { message: "weg", code: "media_worker_down" } }), {
            status: 503,
            headers: { "Content-Type": "application/json" },
          });
        }
        await back;
      }
      return fetchFn(url);
    }) as unknown as typeof fetch;
    const loader = new MseLoader(
      video as unknown as HTMLVideoElement,
      { sid: "s", duration: 100, map: { gen: 1, mime: "video/mp4", offset: 0, init: "init.mp4", segments: GRID } },
      { error: (e) => errors.push(e) },
      gated,
    );
    await loader.open(0);
    await until(() => errors.length > 0);
    expect(errors).toHaveLength(1); // de 503 van segment 3
    const retried = loader.retry();
    // Terwijl retry op segment 3 wacht: de video loopt (timeupdate start anders een tweede pump)
    for (let i = 0; i < 5; i++) {
      video.currentTime = 0.5 + i * 0.1;
      video.dispatchEvent(new Event("timeupdate"));
      await settle();
    }
    release();
    await retried;
    for (let t = 1; t <= 6; t++) {
      video.currentTime = t;
      video.dispatchEvent(new Event("timeupdate"));
      await until(() => aheadOf(sb.spans, t) > 4 || errors.length > 1);
    }
    expect(errors.slice(1).map(String)).toEqual([]); // eerst: InvalidStateError (still processing)
    expect(sb.appended.filter((a) => a === 6)).toHaveLength(1); // segment 3 (6-8 s) één keer
    expect(aheadOf(sb.spans, 6)).toBeGreaterThan(4);
    loader.destroy();
  });
});

describe("een gat dat pas komt als de video al stilstaat (CI-run 41002597837)", () => {
  beforeEach(() => {
    vi.stubGlobal("MediaSource", FakeMediaSource);
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:nep");
    vi.spyOn(URL, "revokeObjectURL").mockReturnValue(undefined);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("springt erover zodra het segment van de nieuwe run er is, zonder nieuwe waiting", async () => {
    sb = new FakeSourceBuffer(1000);
    const video = new FakeVideo();
    const errors: unknown[] = [];
    // Zoals in CI (H.264 met B-frames, AAC): in de oude run eindigt het geluid van segment 4
    // vóór de grens van 10 s, en buffered dus ook. Segment 5 geeft eerst 503 (de worker is
    // gedood); het komt daarna van een nieuwe run, met geluid dat 44 ms na dat eind begint
    let failedOnce = false;
    const seam = (async (url: string) => {
      const k = Number(/\/(\d+)\.m4s/.exec(url)?.[1] ?? -1);
      if (k === 5 && !failedOnce) {
        failedOnce = true;
        return new Response(JSON.stringify({ detail: { message: "weg", code: "media_worker_down" } }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (k === 4) return new Response(new Float64Array([8, 9.9705]).buffer);
      if (k === 5) return new Response(new Float64Array([10.0145, 12]).buffer);
      return fetchFn(url);
    }) as unknown as typeof fetch;
    const loader = new MseLoader(
      video as unknown as HTMLVideoElement,
      { sid: "s", duration: 100, map: { gen: 1, mime: "video/mp4", offset: 0, init: "init.mp4", segments: GRID } },
      { error: (e) => errors.push(e) },
      seam,
    );
    await loader.open(0);
    await until(() => errors.length > 0);
    expect(errors).toHaveLength(1); // de 503 van segment 5
    // De buffer is op: Chromium staat zo'n 50 ms vóór het eind stil (gemeten in Chromium 141)
    video.currentTime = 9.9202;
    video.readyState = 2;
    video.dispatchEvent(new Event("waiting"));
    await new Promise((ok) => setTimeout(ok, GAP_WAIT_MS + 50));
    expect(video.currentTime).toBe(9.9202); // nog niets om heen te springen
    // De herstel-lus van session.ts haalt het segment opnieuw; de video staat nog steeds stil
    await loader.retry();
    expect(sb.spans.some(([a]) => Math.abs(a - 10.0145) < 1e-9)).toBe(true);
    await until(() => video.currentTime !== 9.9202);
    expect(video.currentTime).toBeCloseTo(10.0145);
    expect(errors).toHaveLength(1);
    loader.destroy();
  });

  it("kijkt niet meer na destroy", async () => {
    sb = new FakeSourceBuffer(1000);
    const video = new FakeVideo();
    const loader = new MseLoader(
      video as unknown as HTMLVideoElement,
      { sid: "s", duration: 100, map: { gen: 1, mime: "video/mp4", offset: 0, init: "init.mp4", segments: GRID } },
      { error: () => undefined },
      fetchFn,
    );
    await loader.open(0);
    await settle();
    video.currentTime = 29.97;
    video.readyState = 2;
    video.dispatchEvent(new Event("waiting"));
    loader.destroy();
    sb.spans = [
      [0, 30],
      [30.1, 40],
    ];
    await new Promise((ok) => setTimeout(ok, 3 * GAP_WAIT_MS));
    expect(video.currentTime).toBe(29.97);
  });
});
