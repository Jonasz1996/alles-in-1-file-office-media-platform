// De kleine delen van de speler: subs.waitVtt (202 en Retry-After), de Reporter (de events van
// Keuze 36), de hulpjes van de tijdbalk, ?t= van /kijken en de maat uit de viewer.

import { describe, expect, it, vi } from "vitest";
import type { Chapter } from "../media/types";
import { type PlaybackEvent, Reporter, type ReporterState, TICK_MS, type VideoLike } from "./playback";
import { VTT_TRIES, waitVtt } from "./subs";
import { chapterAt, marks, timeAt } from "./ui/Seekbar";
import { sizeOf } from "./ViewerPlayer";
import { loadSize, startFrom } from "./WatchPage";

function vttDeps(responses: Array<Response | Error>) {
  const waits: number[] = [];
  let pending = 0;
  let cancel = false;
  const fetchFn = vi.fn(async () => {
    const r = responses.shift();
    if (!r) throw new Error("geen antwoord meer");
    if (r instanceof Error) throw r;
    return r;
  });
  return {
    deps: {
      fetchFn: fetchFn as unknown as typeof fetch,
      sleep: async (ms: number) => {
        waits.push(ms);
      },
      cancelled: () => cancel,
      pending: () => {
        pending += 1;
      },
    },
    waits,
    fetchFn,
    pending: () => pending,
    stop: () => (cancel = true),
  };
}

const preparing = (after?: string) =>
  new Response("", { status: 202, headers: after ? { "Retry-After": after } : {} });

describe("waitVtt", () => {
  it("202 met Retry-After: wachten, de melding, en dan klaar", async () => {
    const d = vttDeps([preparing("3"), preparing(), new Response("WEBVTT\n\n", { status: 200 })]);
    expect(await waitVtt("/s/subs/x0.vtt", d.deps)).toBe("ready");
    expect(d.waits).toEqual([3000, 5000]);
    expect(d.pending()).toBe(2);
  });

  it("Retry-After binnen 1..60 s", async () => {
    const d = vttDeps([preparing("0"), preparing("600"), new Response("", { status: 200 })]);
    expect(await waitVtt("/u", d.deps)).toBe("ready");
    expect(d.waits).toEqual([1000, 60_000]);
  });

  it("geen verbinding: na 2 s opnieuw", async () => {
    const d = vttDeps([new TypeError("netwerk"), new Response("", { status: 200 })]);
    expect(await waitVtt("/u", d.deps)).toBe("ready");
    expect(d.waits).toEqual([2000]);
  });

  it("een andere status is failed (geen lus)", async () => {
    const d = vttDeps([new Response("", { status: 404 })]);
    expect(await waitVtt("/u", d.deps)).toBe("failed");
    expect(d.fetchFn).toHaveBeenCalledTimes(1);
  });

  it("begrensd: na VTT_TRIES keer 202 failed", async () => {
    const d = vttDeps(Array.from({ length: VTT_TRIES + 5 }, () => preparing("1")));
    expect(await waitVtt("/u", d.deps)).toBe("failed");
    expect(d.fetchFn).toHaveBeenCalledTimes(VTT_TRIES);
  });

  it("weg (een andere sessie): cancelled, zonder nog te vragen", async () => {
    const d = vttDeps([preparing("1"), new Response("", { status: 200 })]);
    d.stop();
    expect(await waitVtt("/u", d.deps)).toBe("cancelled");
    expect(d.fetchFn).not.toHaveBeenCalled();
  });
});

/** Een nep-video en een nep-document voor de Reporter. */
function reporter(state: Partial<ReporterState> | null = {}) {
  const video = Object.assign(new EventTarget(), {
    currentTime: 0,
    duration: 120,
    paused: true,
    seeking: false,
  }) as unknown as VideoLike & { paused: boolean; seeking: boolean };
  const doc = Object.assign(new EventTarget(), { visibilityState: "visible" as DocumentVisibilityState });
  const events: PlaybackEvent[] = [];
  let tick: (() => void) | null = null;
  const timers = {
    set: ((fn: () => void, ms: number) => {
      expect(ms).toBe(TICK_MS);
      tick = fn;
      return 1;
    }) as unknown as typeof setInterval,
    clear: (() => {
      tick = null;
    }) as unknown as typeof clearInterval,
  };
  const full: ReporterState | null =
    state === null
      ? null
      : { position: 12.3456, started_at: "t0", duration_ms: 0, audio: 1, subtitle: null, ...state };
  const r = new Reporter(video, () => full, { report: (e) => events.push(e) }, doc, timers);
  const fire = (t: EventTarget, name: string) => t.dispatchEvent(new Event(name));
  return { r, video, doc, events, fire, tick: () => tick?.(), ticking: () => tick !== null };
}

describe("Reporter", () => {
  it("tick elke 15 s tijdens het spelen, met de positie van de controller", () => {
    const { video, events, fire, tick } = reporter();
    video.paused = false;
    fire(video, "playing");
    tick();
    expect(events).toEqual([
      {
        event: "tick",
        position_ms: 12_346,
        duration_ms: 120_000,
        started_at: "t0",
        audio: 1,
        subtitle: null,
        keepalive: false,
      },
    ]);
  });

  it("pause, seek en end; het einde geeft geen pause erbij", () => {
    const { video, events, fire, ticking } = reporter();
    video.paused = false;
    fire(video, "playing");
    video.paused = true;
    fire(video, "pause");
    expect(ticking()).toBe(false);
    fire(video, "seeked");
    fire(video, "ended");
    fire(video, "pause");
    expect(events.map((e) => e.event)).toEqual(["pause", "seek", "end"]);
  });

  it("pause tijdens het zoeken telt niet (het zoeken geeft seek)", () => {
    const { video, events, fire } = reporter();
    video.seeking = true;
    fire(video, "pause");
    expect(events).toEqual([]);
  });

  it("verborgen: pause met keepalive", () => {
    const { doc, events, fire } = reporter();
    doc.visibilityState = "hidden";
    fire(doc, "visibilitychange");
    expect(events).toMatchObject([{ event: "pause", keepalive: true }]);
  });

  it("stop: één keer, met keepalive; daarna niets meer (en niet na het einde)", () => {
    const a = reporter();
    a.r.stop();
    a.r.stop();
    a.fire(a.video, "seeked");
    expect(a.events).toMatchObject([{ event: "stop", keepalive: true }]);

    const b = reporter();
    b.fire(b.video, "ended");
    b.r.stop();
    expect(b.events.map((e) => e.event)).toEqual(["end"]);
  });

  it("zonder sessie (state null) meldt hij niets", () => {
    const { video, events, fire, r } = reporter(null);
    fire(video, "seeked");
    r.stop();
    expect(events).toEqual([]);
  });
});

const ch = (start_ms: number, end_ms: number, title: string): Chapter => ({ start_ms, end_ms, title }) as Chapter;

describe("de tijdbalk", () => {
  it("timeAt: binnen 0..duur, en 0 zonder maat of duur", () => {
    expect(timeAt(150, 100, 200, 60)).toBe(15);
    expect(timeAt(50, 100, 200, 60)).toBe(0);
    expect(timeAt(400, 100, 200, 60)).toBe(60);
    expect(timeAt(150, 100, 0, 60)).toBe(0);
    expect(timeAt(150, 100, 200, Number.NaN)).toBe(0);
  });

  it("chapterAt: het hoofdstuk van een tijd, het eind hoort bij het volgende", () => {
    const list = [ch(21, 5021, "Intro"), ch(5021, 24_021, "Deel 1"), ch(24_021, 30_000, "End Credits")];
    expect(chapterAt(list, 0)?.title).toBeUndefined();
    expect(chapterAt(list, 1)?.title).toBe("Intro");
    expect(chapterAt(list, 5.021)?.title).toBe("Deel 1");
    expect(chapterAt(list, 29.9)?.title).toBe("End Credits");
    expect(chapterAt(list, 30)).toBeNull();
  });

  it("marks: geen streepje voor een hoofdstuk dat (bijna) bij 0 begint", () => {
    const list = [ch(21, 5021, "Intro"), ch(5021, 24_021, "Deel 1"), ch(24_021, 30_000, "End Credits")];
    expect(marks(list).map((c) => c.title)).toEqual(["Deel 1", "End Credits"]);
    expect(marks([ch(0, 1000, "a"), ch(500, 1000, "b"), ch(501, 1000, "c")]).map((c) => c.title)).toEqual(["c"]);
  });
});

describe("/kijken ?t= en de maat uit de viewer", () => {
  it("startFrom: seconden ≥ 0, anders 0", () => {
    expect(startFrom("21")).toBe(21);
    expect(startFrom(" 12.5 ")).toBe(12.5);
    expect(startFrom(null)).toBe(0);
    for (const bad of ["", "-3", "1e9", "abc", "12,5", "1234567", "Infinity", "0x10"]) {
      expect(startFrom(bad)).toBe(0);
    }
  });

  it("sizeOf: de maat voor decodingInfo, met een bitrate per hoogte; zonder maat niets", () => {
    expect(sizeOf({ width: 1920, height: 1080 })).toEqual({
      width: 1920,
      height: 1080,
      bitrate: 8_000_000,
      framerate: 24,
    });
    expect(sizeOf({ width: 3840, height: 2160 })?.bitrate).toBe(25_000_000);
    expect(sizeOf({ width: 1280, height: 720 })?.bitrate).toBe(4_000_000);
    expect(sizeOf({})).toBeUndefined();
    expect(sizeOf({ width: 0, height: 720 })).toBeUndefined();
    expect(sizeOf({ width: -1, height: -1 })).toBeUndefined();
  });

  it("loadSize: /kijken haalt de maat uit de meta van de viewer, net als ViewerPlayer (K2)", async () => {
    const asked: string[] = [];
    const request = async <T,>(method: string, path: string): Promise<T> => {
      asked.push(`${method} ${path}`);
      return { meta: { width: 3840, height: 2160 } } as T;
    };
    expect(await loadSize("a/b", request)).toEqual(sizeOf({ width: 3840, height: 2160 }));
    expect(asked).toEqual(["GET /api/v1/files/a%2Fb/preview"]);
  });

  it("loadSize: geen maat, een fout of te traag geeft undefined (dan 1080p)", async () => {
    const answer = (v: unknown) => async <T,>(): Promise<T> => v as T;
    expect(await loadSize("n", answer({ meta: {} }))).toBeUndefined();
    expect(await loadSize("n", answer({}))).toBeUndefined();
    const failing = async <T,>(): Promise<T> => {
      throw new Error("404");
    };
    expect(await loadSize("n", failing)).toBeUndefined();
    const never = <T,>(): Promise<T> => new Promise<T>(() => {});
    expect(await loadSize("n", never, 5)).toBeUndefined();
  });
});
