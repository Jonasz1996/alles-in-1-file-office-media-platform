import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import type { ClientProfile, PlayIn } from "../media/types";
import {
  classify,
  type Notice,
  playBody,
  type RecoverDeps,
  Recovery,
  RenewLimiter,
  retryAfter,
  retryDelay,
  StreamError,
} from "./session";

const PROFILE = { v: 1, base: "web" } as unknown as ClientProfile;

/** Een nepklok: sleep loopt de tijd meteen vooruit. */
function fake(o: Partial<RecoverDeps> & { aheadS?: number } = {}) {
  let now = 0;
  const attempts: number[] = [];
  const notices: Notice[] = [];
  const deps: RecoverDeps = {
    attempt: async () => {
      attempts.push(now);
    },
    renew: async () => {},
    ahead: () => o.aheadS ?? 30,
    health: async () => false,
    sleep: async (ms) => {
      now += ms;
    },
    now: () => now,
    notice: (n) => notices.push(n),
    ...o,
  };
  return { deps, attempts, notices, at: () => now, set: (t: number) => (now = t) };
}

const gone = () => new StreamError(410, "weg", "media_session_gone");
const net = () => new ApiError(0, "Geen verbinding", "network");
const bare503 = () => new StreamError(503, "Fout 503", "http_error", { status: 503 });

describe("classify", () => {
  it("elke code zijn gedrag (Keuze 32)", () => {
    expect(classify(gone())).toEqual({ kind: "renew" });
    expect(classify(new StreamError(410, "x", "media_session_gone", { reason: "replaced" }))).toEqual({ kind: "renew" });
    expect(classify(new StreamError(403, "x", "media_access_gone", { reason: "stopped" }))).toEqual({
      kind: "stop",
      reason: "stopped",
    });
    expect(classify(new StreamError(403, "x", "media_access_gone", { reason: "raar" }))).toEqual({
      kind: "stop",
      reason: "revoked",
    });
    expect(classify(new StreamError(503, "x", "media_busy", { reason: "worker", retry_after: 2 }, 2))).toEqual({
      kind: "wait",
      seconds: 2,
    });
    expect(classify(new StreamError(503, "x", "media_timeout", undefined, 5))).toEqual({ kind: "wait", seconds: 5 });
    // play gooit een ApiError zonder kop: dan retry_after uit de params, of 2 s
    expect(classify(new ApiError(503, "x", "media_busy", { reason: "cpu", retry_after: 7 }))).toEqual({
      kind: "wait",
      seconds: 7,
    });
    expect(classify(new ApiError(409, "x", "media_probe_pending", { reason: "probe" }))).toEqual({
      kind: "wait",
      seconds: 2,
    });
    expect(classify(net())).toEqual({ kind: "retry" });
    expect(classify(new TypeError("Failed to fetch"))).toEqual({ kind: "retry" });
    expect(classify(bare503())).toEqual({ kind: "retry" });
    expect(classify(new StreamError(502, "Fout 502", "http_error"))).toEqual({ kind: "retry" });
    expect(classify(new StreamError(503, "x", "media_worker_down"))).toEqual({ kind: "retry" });
    expect(classify(new StreamError(502, "x", "media_transcode_failed"))).toEqual({ kind: "fatal" });
    expect(classify(new StreamError(403, "x", "media_public_transcode"))).toEqual({ kind: "fatal" });
    expect(classify(new ApiError(415, "x", "media_unsupported"))).toEqual({ kind: "fatal" });
    expect(classify(new Error("programmeerfout"))).toEqual({ kind: "fatal" });
  });

  it("Retry-After alleen als getal", () => {
    expect(retryAfter("5")).toBe(5);
    expect(retryAfter(" 2 ")).toBe(2);
    expect(retryAfter("Wed, 21 Oct 2026 07:28:00 GMT")).toBeNull();
    expect(retryAfter(null)).toBeNull();
  });
});

describe("vernieuwen bij 410", () => {
  it("een nieuwe sessie op de positie van nu, met replace", async () => {
    let position = 754.321;
    const bodies: PlayIn[] = [];
    const { deps } = fake({
      renew: async () => {
        bodies.push(playBody(PROFILE, { audio: 2, quality: 720 }, position, "oude.sid"));
      },
    });
    const r = new Recovery();
    expect(await r.run(gone(), deps)).toEqual({ ok: true, renewed: true });
    position = 760;
    expect(bodies).toEqual([{ profile: PROFILE, audio: 2, quality: 720, start_ms: 754_321, replace: "oude.sid" }]);
  });

  it("geen lus: hooguit 3 keer per minuut, daarna weer", async () => {
    const r = new Recovery();
    const f = fake();
    let renews = 0;
    f.deps.renew = async () => {
      renews++;
    };
    for (let i = 0; i < 3; i++) {
      f.set(i * 10_000);
      expect((await r.run(gone(), f.deps)).ok).toBe(true);
    }
    f.set(30_000);
    expect(await r.run(gone(), f.deps)).toMatchObject({ ok: false, loop: true });
    expect(renews).toBe(3);
    f.set(61_000);
    expect((await r.run(gone(), f.deps)).ok).toBe(true);
    expect(renews).toBe(4);
  });

  it("de grens telt ook als de 410's uit verschillende fouten komen", () => {
    const l = new RenewLimiter();
    expect([l.allow(0), l.allow(1), l.allow(2), l.allow(3)]).toEqual([true, true, true, false]);
    expect(l.allow(60_001)).toBe(true);
  });

  it("na een herstart: eerst netwerkfouten, dan 410, dan een nieuwe sessie", async () => {
    let renewed = 0;
    const f = fake({
      renew: async () => {
        renewed++;
      },
    });
    f.deps.attempt = async () => {
      if (f.at() < 20_000) throw net();
      throw gone();
    };
    const out = await new Recovery().run(net(), f.deps);
    expect(out).toEqual({ ok: true, renewed: true });
    expect(renewed).toBe(1);
  });

  it("vernieuwen zelf mislukt eerst (de app is nog niet terug): opnieuw vernieuwen, niet het segment", async () => {
    const f = fake();
    let segments = 0;
    let renews = 0;
    f.deps.attempt = async () => {
      segments++;
    };
    f.deps.renew = async () => {
      renews++;
      if (f.at() < 5000) throw net();
    };
    expect(await new Recovery().run(gone(), f.deps)).toEqual({ ok: true, renewed: true });
    expect(segments).toBe(0);
    expect(renews).toBeGreaterThan(1);
  });
});

describe("stoppen bij 403", () => {
  it("zegt waarom, en probeert niets opnieuw", async () => {
    let tried = 0;
    const f = fake({
      attempt: async () => {
        tried++;
      },
      renew: async () => {
        tried++;
      },
    });
    const out = await new Recovery().run(new StreamError(403, "x", "media_access_gone", { reason: "revoked" }), f.deps);
    expect(out).toEqual({ ok: false, stop: "revoked" });
    expect(tried).toBe(0);
  });
});

describe("netwerkfouten en 503 zonder code (een herstart of update)", () => {
  for (const [name, error] of [
    ["netwerkfout", net],
    ["503 zonder code", bare503],
  ] as const) {
    it(`${name}: 30 s lang, opnieuw na 1, 2, 4, 8, 10, 10 s, en daarna loopt hij door`, async () => {
      const f = fake();
      const times: number[] = [];
      f.deps.attempt = async () => {
        times.push(f.at());
        if (f.at() < 30_000) throw error();
      };
      const out = await new Recovery().run(error(), f.deps);
      expect(out).toEqual({ ok: true, renewed: false });
      const gaps = times.map((t, i) => (t - (i ? times[i - 1] : 0)) / 1000);
      expect(gaps).toEqual([1, 2, 4, 8, 10, 10]);
      expect(f.notices[0]).toEqual({ kind: "reconnecting" });
      expect(f.notices[f.notices.length - 1]).toBeNull();
    });
  }

  it("is /api/v1/health er weer (na weg te zijn geweest), dan meteen opnieuw", async () => {
    const f = fake();
    f.deps.health = async () => f.at() >= 17_000;
    const times: number[] = [];
    f.deps.attempt = async () => {
      times.push(f.at());
      if (f.at() < 17_000) throw net();
    };
    expect((await new Recovery().run(net(), f.deps)).ok).toBe(true);
    // 1, 3, 7, 15: dan wacht hij 10 s, maar op 17 s is de app er weer
    expect(times).toEqual([1000, 3000, 7000, 15_000, 17_000]);
  });

  it("geeft het op na de buffer plus 120 s: de server is niet bereikbaar", async () => {
    const f = fake({ aheadS: 10 });
    f.deps.attempt = async () => {
      throw net();
    };
    const out = await new Recovery().run(net(), f.deps);
    expect(out).toMatchObject({ ok: false, unreachable: true });
    expect(f.at()).toBe(130_000);
  });
});

describe("bezet en te traag (503 met code)", () => {
  it("na Retry-After opnieuw, met een melding", async () => {
    const f = fake();
    const times: number[] = [];
    f.deps.attempt = async () => {
      times.push(f.at());
    };
    const busy = new StreamError(503, "Bezet", "media_busy", { reason: "worker", retry_after: 2 }, 3);
    expect(await new Recovery().run(busy, f.deps)).toEqual({ ok: true, renewed: false });
    expect(times).toEqual([3000]);
    expect(f.notices[0]).toEqual({ kind: "busy", error: busy });
  });

  it("begrensd: niet eindeloos wachten", async () => {
    const f = fake();
    f.deps.attempt = async () => {
      throw new StreamError(503, "Te traag", "media_timeout", undefined, 1);
    };
    const out = await new Recovery().run(new StreamError(503, "Te traag", "media_timeout", undefined, 1), f.deps);
    expect(out.ok).toBe(false);
    expect(f.at()).toBeLessThanOrEqual(31_000);
  });
});

describe("afbreken", () => {
  it("een andere keuze of de pagina weg: de lus stopt", async () => {
    let cancelled = false;
    const f = fake({ cancelled: () => cancelled });
    f.deps.attempt = async () => {
      cancelled = true;
      throw net();
    };
    expect(await new Recovery().run(net(), f.deps)).toEqual({ ok: false, cancelled: true });
  });
});

describe("playBody", () => {
  it("alleen wat gekozen is; auto en tekstondertitels gaan niet mee", () => {
    expect(playBody(PROFILE, {}, 0)).toEqual({ profile: PROFILE });
    expect(playBody(PROFILE, { quality: "auto" }, 12.5)).toEqual({ profile: PROFILE, start_ms: 12_500 });
    expect(playBody(PROFILE, { burn: "e3", quality: 480, hdr: "raw" }, 1, "s")).toEqual({
      profile: PROFILE,
      subtitle: "e3",
      quality: 480,
      hdr: "raw",
      start_ms: 1000,
      replace: "s",
    });
  });

  it("de wachttijden", () => {
    expect([0, 1, 2, 3, 4, 5, 9].map(retryDelay)).toEqual([1000, 2000, 4000, 8000, 10_000, 10_000, 10_000]);
  });
});
