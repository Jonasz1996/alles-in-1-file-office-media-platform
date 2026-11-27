// De muziekspeler (player.ts) met een nep-<audio> en nep-API in node: wat er gebeurt terwijl een
// nieuw nummer laadt (review golf 4, C5) en "volgende" op een nummer dat al in de buffer staat
// (alleen de positie, geen nieuwe sessie; C7).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PlayOut } from "../media/types";

const api = vi.hoisted(() => ({ getCodecs: vi.fn(), play: vi.fn() }));
const mse = vi.hoisted(() => ({ on: false }));
const closed = vi.hoisted(() => [] as string[]);
const runs = vi.hoisted(() => [] as { key: string; entries: { key: string; start: number; end: number }[]; closedWith: boolean[] }[]);

vi.mock("../media/api", async (orig) => ({ ...(await orig<typeof import("../media/api")>()), ...api }));
vi.mock("../player/caps", async (orig) => ({
  ...(await orig<typeof import("../player/caps")>()),
  buildProfile: async () => ({}),
}));
vi.mock("../player/mse", async (orig) => ({
  ...(await orig<typeof import("../player/mse")>()),
  mseSupported: () => mse.on,
  mediaSourceClass: () => (mse.on ? { ctor: class {}, managed: false } : null),
}));
vi.mock("../player/session", async (orig) => ({
  ...(await orig<typeof import("../player/session")>()),
  closeQuietly: (sid: string | null | undefined) => {
    if (sid) closed.push(sid);
  },
}));
vi.mock("./gapless", async (orig) => {
  const real = await orig<typeof import("./gapless")>();
  /** Een run zonder MSE: de speler vraagt alleen entry, buffered, sync, start en close. */
  class FakeRun {
    entries: { key: string; start: number; end: number }[] = [];
    closedWith: boolean[] = [];
    key: string;
    constructor(
      private readonly audio: { src: string },
      track: { key: string },
    ) {
      this.key = track.key;
      runs.push(this);
    }
    start(): void {
      this.audio.src = "blob:run";
    }
    close(detach = true): void {
      this.closedWith.push(detach);
    }
    entry(key: string) {
      return this.entries.find((e) => e.key === key) ?? null;
    }
    buffered(t: number): boolean {
      return this.entries.some((e) => t >= e.start && t < e.end);
    }
    sync(): void {}
  }
  return { ...real, GaplessRun: FakeRun };
});

import { queue } from "./queue";
import { MusicPlayer } from "./player";

class FakeAudio extends EventTarget {
  currentTime = 0;
  duration = Number.NaN;
  private source = "";
  paused = true;
  volume = 1;
  muted = false;
  playbackRate = 1;
  error: MediaError | null = null;
  preload = "";
  hidden = false;
  className = "";
  play(): Promise<void> {
    this.paused = false;
    this.dispatchEvent(new Event("play"));
    this.dispatchEvent(new Event("playing"));
    return Promise.resolve();
  }
  pause(): void {
    if (this.paused) return;
    this.paused = true;
    this.dispatchEvent(new Event("pause"));
  }
  /** Een nieuwe bron begint op 0 (zoals een echt element) */
  get src(): string {
    return this.source;
  }
  set src(v: string) {
    this.source = v;
    this.currentTime = 0;
  }
  removeAttribute(): void {
    this.src = "";
  }
  load(): void {}
  tick(t: number): void {
    this.currentTime = t;
    this.dispatchEvent(new Event("timeupdate"));
  }
}

let audio: FakeAudio;

/** Een antwoord van play dat de test zelf laat komen (of mislukken). */
function later<T>() {
  let ok!: (v: T) => void;
  let no!: (e: unknown) => void;
  const promise = new Promise<T>((a, b) => {
    ok = a;
    no = b;
  });
  return { promise, ok, no };
}

function out(sid: string, mode: "direct" | "audio" = "direct"): PlayOut {
  return {
    sid,
    mode,
    reasons: [],
    urls: mode === "audio" ? { audio: "audio.mp4" } : { file: "file" },
    mime: mode === "audio" ? 'audio/mp4; codecs="flac"' : "audio/flac",
    duration_ms: 200_000,
    start_ms: 0,
    offset: 0,
    tracks: { audio: [], subtitles: [] },
    chapters: [],
    quality: { current: "auto", options: [] },
    gapless: null,
    started_at: "2026-10-30T00:00:00Z",
    expires_at: "2026-10-30T06:00:00Z",
  } as unknown as PlayOut;
}

const items = ["Een", "Twee", "Drie", "Vier"].map((title, i) => ({
  node_id: `n${i}`,
  title,
  artist: "Kim",
  album: "Plaat",
  album_id: "a1",
  duration_ms: 200_000,
}));

async function settle(): Promise<void> {
  for (let i = 0; i < 20; i++) await new Promise((ok) => setTimeout(ok, 0));
}

beforeEach(() => {
  audio = new FakeAudio();
  vi.stubGlobal("document", { createElement: () => audio, body: { append: () => {} } });
  api.getCodecs.mockReset().mockResolvedValue({ item: ["flac"] });
  // Zonder eigen antwoord: elke sessie een nieuwe sid (ook het vooruit vragen van het volgende)
  let n = 0;
  api.play.mockReset().mockImplementation(async () => out(`p${++n}`));
  closed.length = 0;
  runs.length = 0;
  mse.on = false;
  queue.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  queue.clear();
});

describe("terwijl een nieuw nummer laadt (C5)", () => {
  it("het oude nummer stopt, en zijn tijd en zijn eind tellen niet voor het nieuwe", async () => {
    const p = new MusicPlayer();
    api.play.mockResolvedValueOnce(out("s1"));
    p.playItems(items, 1);
    await settle();
    expect(audio.src).toContain("s1");
    expect(audio.paused).toBe(false);
    audio.tick(190); // ook: Drie wordt alvast gevraagd (minder dan PREFETCH_S over)
    expect(p.snapshot().position).toBe(190);
    await settle();
    expect(api.play).toHaveBeenCalledTimes(2);

    // Op Drie klikken: de sessie komt pas later
    const pending = later<PlayOut>();
    api.play.mockReturnValueOnce(pending.promise);
    void p.playAt(2);
    await settle();
    expect(p.snapshot().status).toBe("loading");
    expect(audio.paused).toBe(true); // Twee speelt niet door
    audio.tick(199.6);
    expect(p.snapshot().position).toBe(0);
    expect(queue.snapshot().position).toBe(0);
    // Het eind van Twee slaat Drie niet over
    audio.dispatchEvent(new Event("ended"));
    await settle();
    expect(queue.snapshot().index).toBe(2);
    expect(p.snapshot().status).toBe("loading");

    pending.ok(out("s3"));
    await settle();
    expect(audio.src).toContain("s3");
    expect(queue.snapshot().index).toBe(2);
  });

  it("een mislukte sessie: opnieuw vanaf het begin van het nieuwe nummer, niet vanaf de tijd van het oude", async () => {
    const p = new MusicPlayer();
    api.play.mockResolvedValueOnce(out("s1"));
    p.playItems(items, 0);
    await settle();
    audio.tick(150);
    const pending = later<PlayOut>();
    api.play.mockReturnValueOnce(pending.promise);
    void p.playAt(1);
    await settle();
    audio.tick(150.4);
    pending.no(Object.assign(new Error("druk"), { status: 503 }));
    await settle();
    expect(p.snapshot().status).toBe("error");
    expect(p.snapshot().position).toBe(0);

    // Opnieuw (de knop): Twee vanaf 0, niet vanaf 2:30 van Een
    api.play.mockResolvedValueOnce(out("s2"));
    p.toggle();
    await settle();
    expect(queue.snapshot().index).toBe(1);
    expect(audio.src).toContain("s2");
    audio.dispatchEvent(new Event("loadedmetadata"));
    expect(audio.currentTime).toBe(0);
    expect(p.snapshot().position).toBe(0);
  });

  it("vorige tijdens het laden gaat naar het vorige nummer, niet terug naar het begin", async () => {
    const p = new MusicPlayer();
    api.play.mockResolvedValueOnce(out("s1"));
    p.playItems(items, 1);
    await settle();
    audio.tick(120);
    const pending = later<PlayOut>();
    api.play.mockReturnValueOnce(pending.promise);
    void p.playAt(2);
    await settle();
    audio.tick(121);
    api.play.mockResolvedValueOnce(out("s2b"));
    p.previous();
    await settle();
    expect(queue.snapshot().index).toBe(1);
    pending.ok(out("s3"));
    await settle();
    expect(closed).toContain("s3"); // een antwoord van een oudere start gaat dicht
    expect(audio.src).toContain("s2b");
  });
});

describe("volgende op een nummer in de buffer (C7)", () => {
  it("alleen de positie: geen nieuwe sessie en geen nieuwe run", async () => {
    mse.on = true;
    const p = new MusicPlayer();
    api.play.mockResolvedValueOnce(out("s1", "audio"));
    p.playItems(items.slice(0, 2), 0);
    await settle();
    expect(runs).toHaveLength(1);
    const [a, b] = queue.snapshot().items;
    runs[0].entries = [
      { key: a.uid, start: 0, end: 200 },
      { key: b.uid, start: 200, end: 400 },
    ];
    audio.tick(50);
    p.next();
    await settle();
    expect(audio.currentTime).toBe(200);
    expect(api.play).toHaveBeenCalledTimes(1);
    expect(runs).toHaveLength(1);
    expect(runs[0].closedWith).toEqual([]);

    // Niet in de buffer: een nieuwe run met een nieuwe sessie
    runs[0].entries = [{ key: a.uid, start: 0, end: 200 }];
    api.play.mockResolvedValueOnce(out("s2", "audio"));
    audio.tick(10);
    p.next();
    await settle();
    expect(api.play).toHaveBeenCalledTimes(2);
    expect(runs).toHaveLength(2);
    expect(runs[0].closedWith).toEqual([false]);
  });
});
