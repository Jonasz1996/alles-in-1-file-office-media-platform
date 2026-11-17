// De voortgang van de speler naar de server (M7, Keuze 36): origin, seq, de body, en springen
// naar een stand van elders zonder die sprong terug te melden.
import { afterEach, describe, expect, it, vi } from "vitest";
import { ORIGIN } from "../media/progressSync";
import type { PlaybackProgressEvent, ProgressIn } from "../media/types";
import { type PlaybackEvent, type PlaybackHandle, Reporter, TICK_MS, type VideoLike } from "./playback";
import { attach, jumpTo, nextSeq, toBody } from "./progress";

afterEach(() => {
  vi.unstubAllGlobals();
});

function melding(over: Partial<PlaybackEvent> = {}): PlaybackEvent {
  return {
    event: "tick",
    position_ms: 30_000.4,
    duration_ms: 600_000,
    started_at: "2026-10-30T10:00:00Z",
    audio: 1,
    subtitle: "e3",
    keepalive: false,
    ...over,
  };
}

function stand(over: Partial<PlaybackProgressEvent> = {}): PlaybackProgressEvent {
  return {
    node_id: "n1",
    position_ms: 30_000,
    duration_ms: 600_000,
    watched: false,
    origin: "elders",
    updated_at: "2026-10-30T10:00:00Z",
    ...over,
  };
}

function speler(paused = true, at = 0): PlaybackHandle & { at: number; seeks: number[] } {
  const h = {
    nodeId: "n1",
    at,
    seeks: [] as number[],
    position: () => h.at,
    paused: () => paused,
    seek(s: number) {
      h.seeks.push(s);
      h.at = s;
    },
  };
  return h;
}

/** Een nep-stroom: geeft de luisteraar terug zodat de test standen kan sturen. */
function stroom() {
  const fns = new Set<(e: PlaybackProgressEvent) => void>();
  return {
    listen: (fn: (e: PlaybackProgressEvent) => void) => {
      fns.add(fn);
      return () => fns.delete(fn);
    },
    send: (e: PlaybackProgressEvent) => {
      for (const fn of fns) fn(e);
    },
    get size() {
      return fns.size;
    },
  };
}

describe("toBody", () => {
  it("rondt af, neemt origin en seq mee, en alleen een geldige ondertitel", () => {
    const b = toBody(melding(), "o", 7);
    expect(b).toEqual({
      position_ms: 30_000,
      duration_ms: 600_000,
      event: "tick",
      origin: "o",
      seq: 7,
      started_at: "2026-10-30T10:00:00Z",
      audio: 1,
      subtitle: "e3",
    });
    expect(toBody(melding({ subtitle: "burn:x", audio: null, position_ms: -5 }), "o", 1)).toMatchObject({
      position_ms: 0,
      subtitle: null,
      audio: null,
    });
    expect(toBody(melding({ subtitle: "x12" }), "o", 1).subtitle).toBe("x12");
  });
});

describe("nextSeq", () => {
  it("telt alleen op", () => {
    const a = nextSeq();
    expect(nextSeq()).toBe(a + 1);
  });
});

describe("jumpTo", () => {
  it("alleen hetzelfde item, gepauzeerd, niet gezien, en echt ergens anders", () => {
    expect(jumpTo(speler(true, 0), stand())).toBe(30);
    expect(jumpTo(speler(false, 0), stand())).toBeNull();
    expect(jumpTo(speler(true, 0), stand({ node_id: "n2" }))).toBeNull();
    expect(jumpTo(speler(true, 0), stand({ watched: true, position_ms: 0 }))).toBeNull();
    expect(jumpTo(speler(true, 29.5), stand())).toBeNull();
    expect(jumpTo(speler(true, 0), stand({ position_ms: Number.NaN }))).toBeNull();
  });
});

describe("attach", () => {
  it("elke melding gaat als PUT met de origin van dit tabblad en een oplopende seq", async () => {
    const sent: { node: string; body: ProgressIn; keepalive: boolean }[] = [];
    const s = stroom();
    const sink = attach(
      speler(),
      async (node, body, keepalive) => {
        sent.push({ node, body, keepalive });
      },
      s.listen,
    );
    sink.report(melding());
    sink.report(melding({ event: "pause", keepalive: true }));
    expect(sent.map((x) => [x.node, x.body.event, x.body.origin, x.keepalive])).toEqual([
      ["n1", "tick", ORIGIN, false],
      ["n1", "pause", ORIGIN, true],
    ]);
    expect(sent[1].body.seq).toBe(sent[0].body.seq + 1);
  });

  it("een PUT die faalt (429, netwerk) laat de speler met rust", async () => {
    const s = stroom();
    const send = vi.fn(() => Promise.reject(new Error("429")));
    const sink = attach(speler(), send, s.listen);
    expect(() => sink.report(melding())).not.toThrow();
    await Promise.resolve();
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("een stand van elders laat een gepauzeerde speler springen, zonder die sprong te melden", () => {
    const sent: ProgressIn[] = [];
    const s = stroom();
    const h = speler(true, 0);
    const sink = attach(
      h,
      async (_n, body) => {
        sent.push(body);
      },
      s.listen,
    );
    s.send(stand({ position_ms: 90_000 }));
    expect(h.seeks).toEqual([90]);
    // De speler meldt het zoeken: dat is onze eigen sprong, die gaat niet terug
    sink.report(melding({ event: "seek", position_ms: 90_000 }));
    expect(sent).toHaveLength(0);
    // Een volgende sprong van de gebruiker zelf gaat wel
    sink.report(melding({ event: "seek", position_ms: 120_000 }));
    expect(sent.map((b) => b.position_ms)).toEqual([120_000]);
  });

  it("een spelende speler springt niet", () => {
    const s = stroom();
    const h = speler(false, 0);
    attach(h, async () => undefined, s.listen);
    s.send(stand({ position_ms: 90_000 }));
    expect(h.seeks).toEqual([]);
  });

  it("detach meldt af en stuurt niets meer", () => {
    const s = stroom();
    const send = vi.fn(async () => undefined);
    const h = speler(true, 0);
    const sink = attach(h, send, s.listen);
    expect(s.size).toBe(1);
    sink.detach();
    expect(s.size).toBe(0);
    sink.report(melding({ event: "stop" }));
    s.send(stand({ position_ms: 90_000 }));
    expect(send).not.toHaveBeenCalled();
    expect(h.seeks).toEqual([]);
  });
});

describe("de Reporter van M6 met de echte timers", () => {
  // In de browser geeft setInterval als methode van een object (timers.set(…)) "Illegal
  // invocation"; Node niet. Zo brak de eerste tik in de e2e van M7 (m7.md). Hier doen de
  // timers wat de browser doet.
  it("start en stopt de tik zonder Illegal invocation", () => {
    const strict = (name: string, out: unknown) =>
      vi.fn(function (this: unknown) {
        if (this !== undefined && this !== globalThis) throw new TypeError(`Illegal invocation (${name})`);
        return out;
      });
    const set = strict("setInterval", 7);
    const clear = strict("clearInterval", undefined);
    vi.stubGlobal("setInterval", set);
    vi.stubGlobal("clearInterval", clear);
    const video = Object.assign(new EventTarget(), {
      currentTime: 0,
      duration: 60,
      paused: false,
      seeking: false,
    }) as unknown as VideoLike;
    const doc = Object.assign(new EventTarget(), { visibilityState: "visible" as DocumentVisibilityState });
    const events: PlaybackEvent[] = [];
    const state = () => ({ position: 1, started_at: "t0", duration_ms: 60_000, audio: null, subtitle: null });
    const r = new Reporter(video, state, { report: (e) => events.push(e) }, doc);
    expect(() => video.dispatchEvent(new Event("playing"))).not.toThrow();
    expect(set).toHaveBeenCalledWith(expect.any(Function), TICK_MS);
    expect(() => r.stop()).not.toThrow();
    expect(clear).toHaveBeenCalledWith(7);
    expect(events.map((e) => e.event)).toEqual(["stop"]);
  });
});

describe("de Reporter van M6 bij weggaan", () => {
  // Bij weggaan ruimt de speler eerst de controller op (zijn effect staat eerder) en pas dan
  // de Reporter: state() is dan null. Zonder de laatste stand kwam er geen "stop" (m7.md).
  it("meldt stop met de laatste stand als de controller al dicht is", () => {
    const video = Object.assign(new EventTarget(), {
      currentTime: 0,
      duration: 60,
      paused: false,
      seeking: false,
    }) as unknown as VideoLike;
    const doc = Object.assign(new EventTarget(), { visibilityState: "visible" as DocumentVisibilityState });
    let open = true;
    let position = 3;
    const state = () =>
      open ? { position, started_at: "t0", duration_ms: 60_000, audio: 2, subtitle: null } : null;
    const events: PlaybackEvent[] = [];
    const timers = { set: () => 1 as unknown as ReturnType<typeof setInterval>, clear: () => undefined };
    const r = new Reporter(video, state, { report: (e) => events.push(e) }, doc, timers);
    video.dispatchEvent(new Event("playing"));
    position = 12.5;
    video.dispatchEvent(new Event("timeupdate"));
    open = false; // de controller is dicht
    r.stop();
    expect(events).toEqual([
      {
        event: "stop",
        position_ms: 12_500,
        duration_ms: 60_000,
        started_at: "t0",
        audio: 2,
        subtitle: null,
        keepalive: true,
      },
    ]);
  });
});
