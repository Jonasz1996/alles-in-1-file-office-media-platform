import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ClientProfile, PlayIn, PlayOut, Quality } from "../media/types";
import { Controller, type ControllerDeps, type ControllerEvents, lowerQuality } from "./controller";
import type { AnyEngine } from "./engine";

const PROFILE = { v: 1, base: "web" } as unknown as ClientProfile;

function out(sid: string, o: Partial<PlayOut> = {}): PlayOut {
  return {
    sid,
    mode: "direct",
    reasons: [],
    urls: { file: `/api/v1/stream/${sid}/file` },
    mime: 'video/webm; codecs="vp9,opus"',
    duration_ms: 60_000,
    start_ms: 0,
    offset: 0,
    tracks: { audio: [], subtitles: [] },
    chapters: [],
    quality: { current: "auto", options: ["auto", "source", 720, 480] },
    started_at: "2026-10-29T12:00:00Z",
    expires_at: "2026-10-29T13:00:00Z",
    ...o,
  } as PlayOut;
}

/** Een nep-<video>: alleen wat de controller leest. */
function fakeVideo(o: { currentTime?: number; videoHeight?: number } = {}) {
  return {
    currentTime: o.currentTime ?? 0,
    readyState: 4,
    seeking: false,
    videoHeight: o.videoHeight ?? 720,
    pause: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  } as unknown as HTMLVideoElement & { currentTime: number };
}

interface FakeEngine {
  kind: "file";
  start: ReturnType<typeof vi.fn>;
  switchTo: ReturnType<typeof vi.fn>;
  retry: ReturnType<typeof vi.fn>;
  hold: ReturnType<typeof vi.fn>;
  ahead: () => number;
  destroy: ReturnType<typeof vi.fn>;
  stall?: () => void;
}

function setup(o: { mode?: PlayOut["mode"]; video?: ReturnType<typeof fakeVideo> } = {}) {
  const bodies: PlayIn[] = [];
  const engines: FakeEngine[] = [];
  const sessions: Array<[string, string]> = [];
  const lowered: Quality[] = [];
  let n = 0;
  const deps: ControllerDeps = {
    codecs: async () => ({ candidates: [], item: [] }) as never,
    profile: async () => PROFILE,
    play: async (_node, body) => {
      bodies.push(body);
      n += 1;
      return out(`film.${n}`, { mode: o.mode ?? "direct" });
    },
    engine: (_video, _out, ev) => {
      const e: FakeEngine = {
        kind: "file",
        start: vi.fn(async () => {}),
        switchTo: vi.fn(async () => {}),
        retry: vi.fn(async () => {}),
        hold: vi.fn(),
        ahead: () => 10,
        destroy: vi.fn(),
        stall: () => ev.stall?.(),
      };
      engines.push(e);
      return e as unknown as AnyEngine;
    },
    recovery: { health: async () => true, sleep: async () => {}, now: () => 0 },
  };
  const ev: ControllerEvents = {
    session: (s, why) => sessions.push([s.sid, why]),
    notice: () => {},
    stopped: () => {},
    failed: () => {},
    lowered: (q) => lowered.push(q),
  };
  const video = o.video ?? fakeVideo();
  const c = new Controller(video, "node-1", ev, {}, deps);
  return { c, bodies, engines, sessions, lowered, video };
}

let fetchSpy: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchSpy = vi.fn(async () => new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetchSpy);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

/** De sids die met DELETE gesloten werden. */
const deleted = () =>
  fetchSpy.mock.calls
    .filter(([, init]) => (init as RequestInit | undefined)?.method === "DELETE")
    .map(([url, init]) => {
      expect((init as RequestInit).keepalive).toBe(true);
      return String(url).replace("/api/v1/stream/", "");
    });

describe("lowerQuality", () => {
  it("één trede onder wat er speelt", () => {
    expect(lowerQuality(["auto", "source", 720, 480], 720, "auto")).toBe(480);
    expect(lowerQuality(["auto", 1080, 720, 480], 1080, "auto")).toBe(720);
    expect(lowerQuality(["auto", 1080, 720, 480], 0, 720)).toBe(480);
  });
  it("niets onder de laagste, en niets zonder treden", () => {
    expect(lowerQuality(["auto", "source", 720, 480], 480, "auto")).toBeNull();
    expect(lowerQuality(["auto", 480], 0, 480)).toBeNull();
    expect(lowerQuality(["auto", "source"], 720, "auto")).toBeNull();
  });
  it("zonder hoogte (nog geen beeld) de hoogste trede", () => {
    expect(lowerQuality(["auto", 720, 480], 0, "auto")).toBe(720);
  });
});

describe("Controller", () => {
  it("open: play met het profiel en de start, dan de engine op die plek", async () => {
    const { c, bodies, engines, sessions } = setup();
    await c.open(12.5);
    expect(bodies).toEqual([{ profile: PROFILE, start_ms: 12_500 }]);
    expect(engines[0].start).toHaveBeenCalledWith(12.5);
    expect(sessions).toEqual([["film.1", "start"]]);
    expect(c.out?.sid).toBe("film.1");
  });

  it("een wissel: replace van de sessie van nu, op de positie van nu, met flush", async () => {
    const video = fakeVideo({ currentTime: 30 });
    const { c, bodies, engines, sessions } = setup({ video });
    await c.open(0);
    await c.change({ audio: 2 });
    expect(bodies[1]).toEqual({ profile: PROFILE, audio: 2, start_ms: 30_000, replace: "film.1" });
    expect(engines).toHaveLength(1);
    expect(engines[0].hold).toHaveBeenCalled();
    expect(engines[0].switchTo).toHaveBeenCalledWith(expect.objectContaining({ sid: "film.2" }), true);
    expect(sessions.at(-1)).toEqual(["film.2", "change"]);
  });

  it("twee haperingen bij een transcode: een trede lager, en lowered", async () => {
    const { c, bodies, engines, lowered } = setup({ mode: "transcode" });
    await c.open(0);
    engines[0].stall?.();
    expect(bodies).toHaveLength(1);
    engines[0].stall?.();
    await vi.waitFor(() => expect(lowered).toEqual([480]));
    expect(bodies[1]).toMatchObject({ quality: 480, replace: "film.1" });
    expect(c.choice.quality).toBe(480);
  });

  it("haperingen bij direct of remux veranderen niets (geen lagere trede te maken)", async () => {
    const { c, bodies, engines } = setup({ mode: "remux" });
    await c.open(0);
    engines[0].stall?.();
    engines[0].stall?.();
    engines[0].stall?.();
    await Promise.resolve();
    expect(bodies).toHaveLength(1);
  });

  it("hide (pagehide): de sessie meteen dicht, één keer", async () => {
    const { c, engines } = setup();
    await c.open(0);
    c.hide();
    c.hide();
    expect(engines[0].hold).toHaveBeenCalledTimes(1);
    expect(deleted()).toEqual(["film.1"]);
  });

  it("show na hide (terug uit de bfcache): een nieuwe sessie op dezelfde plek, zonder replace", async () => {
    const video = fakeVideo({ currentTime: 42 });
    const { c, bodies, sessions } = setup({ video });
    await c.open(0);
    c.hide();
    await c.show();
    expect(bodies[1]).toEqual({ profile: PROFILE, start_ms: 42_000 });
    expect(sessions.at(-1)).toEqual(["film.2", "start"]);
    // de oude sessie ging bij hide dicht, niet nog eens
    expect(deleted()).toEqual(["film.1"]);
  });

  it("show zonder hide doet niets; hide voor de eerste sessie ook niet", async () => {
    const { c, bodies } = setup();
    c.hide();
    await c.open(0);
    await c.show();
    expect(bodies).toHaveLength(1);
    expect(deleted()).toEqual([]);
  });

  it("close: de sessie dicht, en daarna doen hide en show niets", async () => {
    const { c, bodies, engines } = setup();
    await c.open(0);
    c.close();
    c.hide();
    await c.show();
    expect(engines[0].destroy).toHaveBeenCalled();
    expect(bodies).toHaveLength(1);
    expect(deleted()).toEqual(["film.1"]);
  });
});
