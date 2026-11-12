// Gapless (gapless.ts): de rekensom van Keuze 38 en de run met een nep-MediaSource in node.
// De echte proef (de fase springt niet bij een overgang) is de e2e (e2e/test_media_m9.py); hier
// staat wat de run met de SourceBuffer doet: offset, venster, changeType, mode, de volgorde van
// laden, het vooruit laden en wat er bij een fout gebeurt.

import { describe, expect, it, vi } from "vitest";
import type { Gapless } from "../media/types";
import {
  GaplessRun,
  PREFETCH_S,
  placement,
  type RunDeps,
  type RunTrack,
  rangeAround,
  sameType,
  trackSeconds,
  validGapless,
} from "./gapless";

describe("de rekensom", () => {
  it("offset, begin en eind van het venster (Keuze 38)", () => {
    const flac: Gapless = { rate: 44100, delay: 0, samples: 88200 };
    expect(placement(0, flac)).toEqual({ offset: 0, start: 0, end: 2 });
    expect(placement(2, flac)).toEqual({ offset: 2, start: 2, end: 4 });
    const mp3: Gapless = { rate: 44100, delay: 576, samples: 88200 };
    const p = placement(4, mp3);
    expect(p.offset).toBeCloseTo(4 - 576 / 44100, 12);
    expect(p.start).toBe(4);
    expect(p.end).toBeCloseTo(6, 12);
  });

  it("zonder bruikbare gegevens: aansluiten, het eind open", () => {
    for (const g of [null, undefined, { rate: 0, delay: 0, samples: 1 }, { rate: 48000, delay: -1, samples: 1 }, { rate: 48000, delay: 0, samples: Number.NaN }]) {
      expect(validGapless(g as Gapless | null)).toBe(false);
      expect(placement(3, g as Gapless | null)).toEqual({ offset: 3, start: 3, end: Number.POSITIVE_INFINITY });
    }
  });

  it("de lengte van een nummer", () => {
    expect(trackSeconds({ rate: 48000, delay: 0, samples: 96000 }, 5000)).toBe(2);
    expect(trackSeconds(null, 5000)).toBe(5);
    expect(trackSeconds(null, null)).toBeNull();
    expect(trackSeconds(null, 0)).toBeNull();
  });

  it("een som van veel nummers blijft op het sample (geen opgetelde afrondingsfout)", () => {
    // 200 nummers van een oneven aantal samples: het eind van de laatste is de som
    let T = 0;
    let samples = 0;
    for (let i = 0; i < 200; i++) {
      const g = { rate: 44100, delay: 0, samples: 132_301 + i };
      T = placement(T, g).end;
      samples += g.samples;
    }
    expect(Math.abs(T * 44100 - samples)).toBeLessThan(1e-3);
  });

  it("hetzelfde type en het bereik rond t", () => {
    expect(sameType('audio/mp4; codecs="flac"', 'audio/mp4;codecs="FLAC"')).toBe(true);
    expect(sameType('audio/mp4; codecs="flac"', "audio/mpeg")).toBe(false);
    const r = { length: 2, start: (i: number) => [0, 10][i], end: (i: number) => [5, 20][i] };
    expect(rangeAround(r, 3)).toEqual([0, 5]);
    expect(rangeAround(r, 7)).toBeNull();
    expect(rangeAround(r, 19.99)).toEqual([10, 20]);
  });
});

// --- de run met nep-onderdelen ---

interface Append {
  bytes: number;
  type: string;
  mode: string;
  offset: number;
  start: number;
  end: number;
}

class FakeSourceBuffer extends EventTarget {
  mode: AppendMode = "segments";
  timestampOffset = 0;
  appendWindowStart = 0;
  appendWindowEnd = Number.POSITIVE_INFINITY;
  updating = false;
  appends: Append[] = [];
  removed: [number, number][] = [];
  changes: string[] = [];
  /** Hooguit zoveel bytes tegelijk (QuotaExceededError daarboven) */
  quota = Number.POSITIVE_INFINITY;
  held = 0;
  private end = 0;

  constructor(public type: string) {
    super();
  }

  get buffered(): Pick<TimeRanges, "length" | "start" | "end"> {
    const end = this.end;
    return end > 0 ? { length: 1, start: () => 0, end: () => end } : { length: 0, start: () => 0, end: () => 0 };
  }

  private later(change: () => void): void {
    if (this.updating) throw new DOMException("still processing", "InvalidStateError");
    this.updating = true;
    queueMicrotask(() => {
      change();
      this.updating = false;
      this.dispatchEvent(new Event("updateend"));
    });
  }

  appendBuffer(data: BufferSource): void {
    const bytes = (data as Uint8Array).byteLength;
    if (this.held + bytes > this.quota) throw new DOMException("vol", "QuotaExceededError");
    if (this.appendWindowStart >= this.appendWindowEnd) throw new TypeError("venster");
    this.held += bytes;
    this.appends.push({
      bytes,
      type: this.type,
      mode: this.mode,
      offset: this.timestampOffset,
      start: this.appendWindowStart,
      end: this.appendWindowEnd,
    });
    // Elke byte is 1 ms mediatijd (genoeg om het eind van de buffer te volgen)
    this.later(() => {
      this.end = Math.max(this.end, this.appendWindowStart + bytes / 1000);
      if (Number.isFinite(this.appendWindowEnd)) this.end = Math.min(this.end, this.appendWindowEnd);
    });
  }

  remove(a: number, b: number): void {
    this.removed.push([a, b]);
    this.later(() => {
      this.held = 0;
    });
  }

  changeType(type: string): void {
    this.changes.push(type);
    this.type = type;
  }
}

class FakeMediaSource extends EventTarget {
  static last: FakeMediaSource | null = null;
  /** Het quotum van de volgende SourceBuffer (bytes) */
  static quota = Number.POSITIVE_INFINITY;
  readyState = "open";
  duration = Number.NaN;
  buffer: FakeSourceBuffer | null = null;
  ended = 0;
  constructor() {
    super();
    FakeMediaSource.last = this;
    queueMicrotask(() => this.dispatchEvent(new Event("sourceopen")));
  }
  static isTypeSupported(): boolean {
    return true;
  }
  addSourceBuffer(type: string): FakeSourceBuffer {
    this.buffer = new FakeSourceBuffer(type);
    this.buffer.quota = FakeMediaSource.quota;
    return this.buffer;
  }
  endOfStream(): void {
    this.ended++;
    this.readyState = "ended";
  }
}

class FakeAudio extends EventTarget {
  currentTime = 0;
  src = "";
  removed = 0;
  get buffered(): Pick<TimeRanges, "length" | "start" | "end"> {
    return FakeMediaSource.last?.buffer?.buffered ?? { length: 0, start: () => 0, end: () => 0 };
  }
  removeAttribute(): void {
    this.src = "";
    this.removed++;
  }
  load(): void {}
  tick(t: number): void {
    this.currentTime = t;
    this.dispatchEvent(new Event("timeupdate"));
  }
}

async function settle(): Promise<void> {
  for (let i = 0; i < 30; i++) await new Promise((ok) => setTimeout(ok, 0));
}

const FLAC = 'audio/mp4; codecs="flac"';

interface Spec {
  key: string;
  mime?: string;
  sequence?: boolean;
  gapless?: Gapless | null;
  bytes?: number;
}

/** Zo vaak mag de run wachten (sleep) in één test. Een lus die blijft wachten (een volle buffer
 * zonder uitweg) faalt dan, en hangt niet: elke keer wachten geeft ook de timers van vitest een
 * beurt (review golf 4, C7). */
const SLEEPS_MAX = 200;

function setup(specs: Spec[], opts: { fail?: Record<string, number> } = {}) {
  const audio = new FakeAudio();
  let sleeps = 0;
  const released: string[] = [];
  const prepared: string[] = [];
  const tracks: string[] = [];
  const errors: unknown[] = [];
  const fails = { ...(opts.fail ?? {}) };
  const make = (s: Spec): RunTrack => ({
    key: s.key,
    url: `/api/v1/stream/sid-${s.key}/audio.mp4`,
    mime: s.mime ?? FLAC,
    sequence: s.sequence ?? false,
    gapless: s.gapless === undefined ? { rate: 1000, delay: 0, samples: 2000 } : s.gapless,
    release: () => released.push(s.key),
  });
  const byUrl = new Map(specs.map((s) => [`/api/v1/stream/sid-${s.key}/audio.mp4`, s]));
  const fetchFn = vi.fn(async (url: string) => {
    const s = byUrl.get(url)!;
    if (fails[s.key]) {
      fails[s.key]--;
      return new Response(JSON.stringify({ detail: { code: "media_busy", message: "druk" } }), { status: 503 });
    }
    return new Response(new Uint8Array(s.bytes ?? 600));
  });
  const deps: RunDeps = {
    ms: { ctor: FakeMediaSource as unknown as typeof MediaSource, managed: false },
    fetch: fetchFn as unknown as typeof fetch,
    createObjectURL: () => "blob:run",
    revokeObjectURL: vi.fn(),
    prepare: async (key) => {
      prepared.push(key);
      const s = specs.find((x) => x.key === key);
      return s ? make(s) : null;
    },
    after: (key) => {
      const i = specs.findIndex((x) => x.key === key);
      return i >= 0 && i + 1 < specs.length ? specs[i + 1].key : null;
    },
    onTrack: (key) => tracks.push(key),
    onError: (e) => errors.push(e),
    responseError: async (res) => Object.assign(new Error("fout"), { status: res.status }),
    sleep: async () => {
      if (++sleeps > SLEEPS_MAX) throw new Error("te vaak gewacht");
      await new Promise((ok) => setTimeout(ok, 0));
    },
  };
  const run = new GaplessRun(audio as unknown as HTMLMediaElement, make(specs[0]), 0, deps);
  return { run, audio, released, prepared, tracks, errors, deps, fetchFn, waits: () => sleeps };
}

describe("de run", () => {
  it("drie nummers, één SourceBuffer: offset en venster per nummer, changeType bij MP3", async () => {
    const mp3: Gapless = { rate: 1000, delay: 576, samples: 2000 };
    const { run, audio, released } = setup([
      { key: "a" },
      { key: "b" },
      { key: "c", mime: "audio/mpeg", sequence: true, gapless: mp3 },
    ]);
    run.start();
    expect(audio.src).toBe("blob:run");
    await settle();
    const ms = FakeMediaSource.last!;
    const sb = ms.buffer!;
    const firsts = sb.appends.filter((_, i, all) => i === 0 || all[i - 1].offset !== all[i].offset);
    expect(firsts.map((x) => [x.type, x.mode, x.start, x.end])).toEqual([
      [FLAC, "segments", 0, 2],
      [FLAC, "segments", 2, 4],
      ["audio/mpeg", "sequence", 4, 6],
    ]);
    expect(firsts[2].offset).toBeCloseTo(4 - 0.576, 12);
    expect(sb.changes).toEqual(["audio/mpeg"]);
    expect(released).toEqual(["a", "b", "c"]);
    expect(ms.ended).toBe(1);
    expect(run.entries.map((e) => [e.key, e.start, e.end])).toEqual([
      ["a", 0, 2],
      ["b", 2, 4],
      ["c", 4, 6],
    ]);

    // Het huidige nummer volgt de positie
    audio.tick(0.5);
    audio.tick(2.0);
    audio.tick(4.2);
    expect(run.currentKey).toBe("c");
  });

  it("het volgende nummer pas als er PREFETCH_S over is", async () => {
    const long: Gapless = { rate: 1000, delay: 0, samples: 60_000 };
    const { run, audio, prepared } = setup([
      { key: "a", gapless: long },
      { key: "b", gapless: long },
    ]);
    run.start();
    await settle();
    expect(prepared).toEqual([]);
    audio.tick(60 - PREFETCH_S - 5);
    await settle();
    expect(prepared).toEqual([]);
    audio.tick(60 - PREFETCH_S + 1);
    await settle();
    expect(prepared).toEqual(["b"]);
    // b staat erin (60..120); het eind van de bron pas als ook b bijna klaar is
    expect(FakeMediaSource.last!.ended).toBe(0);
    audio.tick(120 - PREFETCH_S + 1);
    await settle();
    expect(FakeMediaSource.last!.ended).toBe(1);
  });

  it("een nummer dat niet via MSE kan: endOfStream, de speler doet de rest na ended", async () => {
    const { run, deps } = setup([{ key: "a" }, { key: "b" }]);
    deps.prepare = async () => null;
    run.start();
    await settle();
    expect(FakeMediaSource.last!.ended).toBe(1);
    expect(run.entries.map((e) => e.key)).toEqual(["a"]);
  });

  it("zonder gapless-gegevens sluit het volgende aan op het eind van de buffer", async () => {
    const { run } = setup([
      { key: "a", gapless: null, bytes: 1500 },
      { key: "b", gapless: null, bytes: 500 },
    ]);
    run.start();
    await settle();
    expect(run.entries.map((e) => [e.key, e.start])).toEqual([
      ["a", 0],
      ["b", 1.5],
    ]);
  });

  it("een volle buffer: eerst wat achter de positie ligt eruit, dan opnieuw", async () => {
    FakeMediaSource.quota = 1500;
    try {
      const { run, audio, prepared, errors } = setup([
        { key: "a", bytes: 1000, gapless: { rate: 1000, delay: 0, samples: 40_000 } },
        { key: "b", bytes: 1000 },
      ]);
      run.start();
      await settle();
      const sb = FakeMediaSource.last!.buffer!;
      expect(sb.appends).toHaveLength(1);
      // b past er pas bij als wat achter de positie ligt (tot 30 − BEHIND_S) eruit is
      audio.tick(30);
      await settle();
      expect(prepared).toEqual(["b"]);
      expect(sb.removed.some(([a, b]) => a === 0 && b > 0 && b <= 20 + 1e-9)).toBe(true);
      expect(sb.appends.map((x) => x.start)).toEqual([0, 40]);
      expect(FakeMediaSource.last!.ended).toBe(1);
      expect(errors).toEqual([]);
    } finally {
      FakeMediaSource.quota = Number.POSITIVE_INFINITY;
    }
  });

  it("een volle buffer zonder iets achter de positie: wachten (begrensd), dan verder", async () => {
    FakeMediaSource.quota = 1500;
    try {
      // a is kort (0–15): b komt meteen, maar past er pas bij als er gespeeld is
      const { run, audio, errors, waits } = setup([
        { key: "a", bytes: 1000, gapless: { rate: 1000, delay: 0, samples: 15_000 } },
        { key: "b", bytes: 1000 },
      ]);
      run.start();
      await settle();
      const sb = FakeMediaSource.last!.buffer!;
      expect(sb.appends).toHaveLength(1);
      expect(waits()).toBeGreaterThan(0); // op 0 s ligt er niets achter de positie: wachten
      expect(sb.removed).toEqual([]);
      audio.tick(12); // 12 − BEHIND_S = 2: [0, 2) mag eruit (de nep heeft van a 1 s staan)
      await settle();
      expect(sb.removed).toEqual([[0, 1]]);
      expect(sb.appends.map((x) => x.start)).toEqual([0, 15]);
      expect(errors).toEqual([]);
    } finally {
      FakeMediaSource.quota = Number.POSITIVE_INFINITY;
    }
  });

  it("wat al gespeeld is gaat eruit: alleen [0, begin van het huidige nummer), en pas 2 s erin", async () => {
    const len = (s: number): Gapless => ({ rate: 1000, delay: 0, samples: s * 1000 });
    // a 0–60, b 60–70, c 70–80, d 80–90, e 90–100
    const { run, audio, prepared } = setup([
      { key: "a", gapless: len(60) },
      { key: "b", gapless: len(10) },
      { key: "c", gapless: len(10) },
      { key: "d", gapless: len(10) },
      { key: "e", gapless: len(10) },
    ]);
    run.start();
    await settle();
    const sb = FakeMediaSource.last!.buffer!;
    audio.tick(41); // b erbij
    await settle();
    expect(prepared).toEqual(["b"]);
    // 1 s in b: c en d erbij, maar b speelt pas net: a blijft nog staan
    audio.tick(61);
    await settle();
    expect(prepared).toEqual(["b", "c", "d"]);
    expect(run.currentKey).toBe("b");
    expect(sb.removed).toEqual([]);
    // In c (70–80), 1 s ver: e erbij; c speelt nog geen 2 s, dus nog niets weg
    audio.tick(71);
    await settle();
    expect(prepared).toEqual(["b", "c", "d", "e"]);
    expect(sb.removed).toEqual([]);
    // Een nieuwe run, verder in c: bij het laden gaat alleen wat vóór c ligt eruit
    const later = setup([
      { key: "a", gapless: len(60) },
      { key: "b", gapless: len(10) },
      { key: "c", gapless: len(30) },
      { key: "d", gapless: len(10) },
    ]);
    later.run.start();
    await settle();
    const sb2 = FakeMediaSource.last!.buffer!;
    later.audio.tick(41); // b
    await settle();
    later.audio.tick(61); // c (70–100)
    await settle();
    expect(later.prepared).toEqual(["b", "c"]);
    later.audio.tick(72.5); // 2,5 s in c: d erbij (100 − 72,5 > 20, dus nog niet)
    await settle();
    expect(later.prepared).toEqual(["b", "c"]);
    later.audio.tick(81); // d erbij, c speelt 11 s: [0, 70) eruit
    await settle();
    expect(later.prepared).toEqual(["b", "c", "d"]);
    expect(later.run.currentKey).toBe("c");
    expect(sb2.removed).toEqual([[0, 70]]);
  });

  it("een fout bij het laden: opnieuw met een nieuwe sessie, en dicht na drie keer", async () => {
    const ok = setup([{ key: "a" }], { fail: { a: 1 } });
    ok.run.start();
    await settle();
    expect(ok.prepared).toEqual(["a"]); // de tweede poging: een nieuwe sessie
    expect(ok.released).toEqual(["a", "a"]);
    expect(ok.errors).toEqual([]);
    expect(FakeMediaSource.last!.ended).toBe(1);

    const bad = setup([{ key: "a" }], { fail: { a: 5 } });
    bad.run.start();
    await settle();
    expect(bad.errors).toHaveLength(1);
    expect((bad.errors[0] as { status?: number }).status).toBe(503);
    expect(bad.released).toEqual(["a", "a", "a"]);
  });

  it("close breekt af, sluit de open sessie en geeft de object-URL vrij", async () => {
    const long: Gapless = { rate: 1000, delay: 0, samples: 60_000 };
    const { run, audio, prepared, deps } = setup([
      { key: "a", gapless: long },
      { key: "b", gapless: long },
    ]);
    run.start();
    await settle();
    run.close();
    expect(run.isClosed).toBe(true);
    expect(deps.revokeObjectURL).toHaveBeenCalledWith("blob:run");
    expect(audio.removed).toBe(1);
    audio.tick(59);
    await settle();
    expect(prepared).toEqual([]);
  });
});
