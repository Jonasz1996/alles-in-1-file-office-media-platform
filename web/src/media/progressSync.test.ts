// De voortgang live (M7, Keuze 36): eigen origin genegeerd, de rijen bijgewerkt.
import { afterEach, describe, expect, it, vi } from "vitest";
import { liveEvents, type ServerEvent } from "../events";
import {
  applyContinue,
  applyNextUp,
  applyToCard,
  CONTINUE_MAX,
  makeOrigin,
  ORIGIN,
  onProgress,
  progressOf,
} from "./progressSync";
import type { Card, PlaybackProgressEvent } from "./types";

function card(over: Partial<Card> = {}): Card {
  return {
    node_id: "n1",
    library_id: "l1",
    title: "Film",
    year: 2001,
    kind: "movie",
    poster: null,
    progress: { position_ms: 90_000, duration_ms: 600_000 },
    watched: false,
    ...over,
  };
}

function stand(over: Partial<PlaybackProgressEvent> = {}): PlaybackProgressEvent {
  return {
    node_id: "n1",
    position_ms: 120_000,
    duration_ms: 600_000,
    watched: false,
    origin: "elders",
    updated_at: "2026-10-30T10:00:00Z",
    ...over,
  };
}

function event(data: Record<string, unknown>, type = "playback.progress"): ServerEvent {
  return { type, data };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("origin", () => {
  it("32 hextekens, per tabblad anders", () => {
    expect(ORIGIN).toMatch(/^[0-9a-f]{32}$/);
    expect(makeOrigin((b) => b.fill(0xab))).toBe("ab".repeat(16));
    expect(makeOrigin()).not.toBe(makeOrigin());
  });
});

describe("progressOf", () => {
  it("geeft de stand van een ander tabblad of apparaat", () => {
    const e = stand();
    expect(progressOf(event({ ...e }), "mij")).toEqual(e);
  });

  it("negeert de eigen origin", () => {
    expect(progressOf(event({ ...stand({ origin: "mij" }) }), "mij")).toBeNull();
    expect(progressOf(event({ ...stand({ origin: ORIGIN }) }))).toBeNull();
  });

  it("negeert andere events en kapotte data", () => {
    expect(progressOf(event({ ...stand() }, "media.item"), "mij")).toBeNull();
    expect(progressOf(event({ ...stand(), position_ms: "12" }), "mij")).toBeNull();
    expect(progressOf(event({ ...stand(), watched: 1 }), "mij")).toBeNull();
    expect(progressOf(event({ ...stand(), node_id: null }), "mij")).toBeNull();
    expect(progressOf(event({ ...stand(), updated_at: 5 }), "mij")?.updated_at).toBe("");
  });
});

describe("applyToCard", () => {
  it("zet het balkje en gezien; een andere kaart blijft dezelfde", () => {
    const c = card();
    expect(applyToCard(c, stand({ position_ms: 200_000 })).progress).toEqual({
      position_ms: 200_000,
      duration_ms: 600_000,
    });
    const seen = applyToCard(c, stand({ watched: true, position_ms: 0 }));
    expect(seen.watched).toBe(true);
    expect(seen.progress).toBeNull();
    expect(applyToCard(c, stand({ node_id: "n2" }))).toBe(c);
    const show = card({ kind: "show", show_id: "s1" });
    expect(applyToCard(show, stand())).toBe(show);
  });
});

describe("applyContinue", () => {
  const rij = [card({ node_id: "a" }), card({ node_id: "b" }), card({ node_id: "c" })];

  it("een stand van elders zet de kaart vooraan met het nieuwe balkje", () => {
    const out = applyContinue(rij, stand({ node_id: "c", position_ms: 300_000 }));
    expect(out.map((c) => c.node_id)).toEqual(["c", "a", "b"]);
    expect(out[0].progress?.position_ms).toBe(300_000);
  });

  it("gezien of niet meer voorbij 60 s: eruit", () => {
    expect(applyContinue(rij, stand({ node_id: "b", watched: true, position_ms: 0 })).map((c) => c.node_id)).toEqual([
      "a",
      "c",
    ]);
    expect(applyContinue(rij, stand({ node_id: "a", position_ms: 60_000 })).map((c) => c.node_id)).toEqual(["b", "c"]);
    expect(applyContinue(rij, stand({ node_id: "a", position_ms: 60_001 })).map((c) => c.node_id)).toEqual([
      "a",
      "b",
      "c",
    ]);
  });

  it("een onbekend item laat de rij staan (dat komt met het opnieuw laden)", () => {
    expect(applyContinue(rij, stand({ node_id: "x" }))).toBe(rij);
  });

  it("hooguit twintig", () => {
    const vol = Array.from({ length: CONTINUE_MAX + 3 }, (_, i) => card({ node_id: `n${i}` }));
    expect(applyContinue(vol, stand({ node_id: "n22" }))).toHaveLength(CONTINUE_MAX);
  });
});

describe("applyNextUp", () => {
  const rij = [card({ node_id: "e2", kind: "episode", progress: null }), card({ node_id: "e9", kind: "episode", progress: null })];

  it("gezien gaat eruit, begonnen krijgt een balkje", () => {
    expect(applyNextUp(rij, stand({ node_id: "e2", watched: true, position_ms: 0 })).map((c) => c.node_id)).toEqual([
      "e9",
    ]);
    const out = applyNextUp(rij, stand({ node_id: "e9", position_ms: 30_000 }));
    expect(out[1].progress?.position_ms).toBe(30_000);
    expect(out[0]).toBe(rij[0]);
  });

  it("een onbekend item laat de rij staan", () => {
    expect(applyNextUp(rij, stand({ node_id: "x" }))).toBe(rij);
  });
});

describe("onProgress", () => {
  it("één abonnement op de stroom voor alle luisteraars; eigen origin komt niet door", () => {
    let emit: ((e: ServerEvent) => void) | null = null;
    const off = vi.fn();
    const sub = vi.spyOn(liveEvents, "subscribe").mockImplementation((fn) => {
      emit = fn;
      return off;
    });
    const a = vi.fn();
    const b = vi.fn(() => {
      throw new Error("kapot");
    });
    const c = vi.fn();
    const offA = onProgress(a);
    const offB = onProgress(b);
    const offC = onProgress(c);
    expect(sub).toHaveBeenCalledTimes(1);
    emit!(event({ ...stand() }));
    emit!(event({ ...stand({ origin: ORIGIN }) }));
    emit!(event({}, "connected"));
    expect(a).toHaveBeenCalledTimes(1);
    expect(c).toHaveBeenCalledTimes(1); // b gooide, c kreeg het toch
    offA();
    offB();
    expect(off).not.toHaveBeenCalled();
    offC();
    expect(off).toHaveBeenCalledTimes(1);
  });
});
