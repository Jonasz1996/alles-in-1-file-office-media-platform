import { describe, expect, it } from "vitest";
import { PROFILE_MAX_BYTES, PROFILE_MAX_CODECS } from "../media/types";
import { baseFor, buildProfile, type CapsEnv, clientName, fit, pickStrings, SIZE_1080, typesFor } from "./caps";

const CANDIDATES = [
  "avc1.42E01E",
  "avc1.64001f",
  "avc1.640028",
  "avc1.640033",
  "hvc1.1.6.L120.90",
  "hvc1.2.4.L150.90",
  "vp09.00.40.08",
  "vp09.02.40.10",
  "av01.0.08M.08",
  "av01.0.08M.10",
  "mp4a.40.2",
  "mp4a.40.5",
  "ac-3",
  "ec-3",
  "opus",
  "flac",
  "mp3",
];

/** Een browser zoals Chromium 141 hier: VP9, AV1 en Opus, geen H.264 en geen HEVC. */
function chromium(o: Partial<CapsEnv> = {}): CapsEnv & { infos: MediaDecodingConfiguration[]; stored: Map<string, string> } {
  const ok = (t: string) => /vp09|av01|opus|flac|mp3|vorbis/.test(t) && !/vp09\.02/.test(t);
  const infos: MediaDecodingConfiguration[] = [];
  const stored = new Map<string, string>();
  return {
    isTypeSupported: ok,
    managed: false,
    canPlayType: (t) => (ok(t) || /^(video\/webm|audio\/(ogg|flac|mpeg|webm))$/.test(t) ? "maybe" : ""),
    decodingInfo: async (config) => {
      infos.push(config);
      const type = config.video?.contentType ?? "";
      return { supported: ok(type) && !config.video?.transferFunction, smooth: true, powerEfficient: false };
    },
    matchMedia: () => false,
    screenHeight: 720,
    devicePixelRatio: 1,
    userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/141.0.0.0 Safari/537.36",
    brands: [
      { brand: "Chromium", version: "141" },
      { brand: "Not=A?Brand", version: "24" },
    ],
    audioTracks: false,
    storage: { getItem: (k) => stored.get(k) ?? null, setItem: (k, v) => void stored.set(k, v) },
    infos,
    stored,
    ...o,
  };
}

describe("het profiel (Keuze 20)", () => {
  it("de vorm van versie 1, met wat de browser mat", async () => {
    const env = chromium();
    const p = await buildProfile({ candidates: CANDIDATES, item: ["hvc1.2.4.L120.90", "ac-3"] }, env);
    expect(Object.keys(p).sort()).toEqual(["audio", "base", "client", "codecs", "engines", "file", "hdr", "max", "v"]);
    expect(p.v).toBe(1);
    expect(p.base).toBe("web");
    expect(p.client).toEqual({ kind: "web", name: "Chromium 141", app: "web" });
    expect(p.engines).toEqual({ mse: true, mms: false, native_hls: false });
    expect(p.file).toEqual(["video/webm", "audio/mpeg", "audio/flac", "audio/ogg", "audio/webm"]);
    expect(p.codecs["vp09.00.40.08"]).toEqual({ mse: true, file: true, smooth: true, power: false });
    expect(p.codecs["avc1.640028"]).toEqual({ mse: false, file: false });
    expect(p.codecs.opus).toEqual({ mse: true, file: true });
    expect(p.codecs["hvc1.2.4.L120.90"]).toEqual({ mse: false, file: false });
    expect(p.hdr).toEqual({ pq: false, hlg: false, dv: false, screen: false });
    expect(p.max).toEqual({ height: 720, bitrate_kbps: 0 });
    expect(p.audio).toEqual({ channels: 2, track_switch: false });
    // De strings van het item eerst
    expect(Object.keys(p.codecs).slice(0, 2)).toEqual(["hvc1.2.4.L120.90", "ac-3"]);
  });

  it("smooth via decodingInfo als media-source, het item op zijn eigen maat", async () => {
    const env = chromium();
    const size = { width: 640, height: 360, bitrate: 900_000, framerate: 25 };
    await buildProfile({ candidates: ["vp09.00.40.08"], item: ["vp09.00.21.08"] }, env, size);
    const own = env.infos.find((c) => c.video?.contentType.includes("vp09.00.21.08"));
    const cand = env.infos.find((c) => c.video?.contentType.includes("vp09.00.40.08") && !c.video.transferFunction);
    expect(own?.type).toBe("media-source");
    expect(own?.video).toMatchObject({ width: 640, height: 360, bitrate: 900_000, framerate: 25 });
    expect(cand?.video).toMatchObject({ width: SIZE_1080.width, height: SIZE_1080.height });
    // HDR met transferFunction en rec2020
    const hdr = env.infos.find((c) => c.video?.transferFunction === "pq");
    expect(hdr?.video?.colorGamut).toBe("rec2020");
  });

  it("lukt decodingInfo niet, dan blijven smooth en power weg", async () => {
    const env = chromium({
      decodingInfo: async () => {
        throw new Error("niet hier");
      },
    });
    const p = await buildProfile({ candidates: ["vp09.00.40.08"], item: [] }, env);
    expect(p.codecs["vp09.00.40.08"]).toEqual({ mse: true, file: true });
  });

  it("de types: mp4, en webm voor VP9, AV1 en Opus; audio als audio/", () => {
    expect(typesFor("avc1.64001f")).toEqual(['video/mp4; codecs="avc1.64001f"']);
    expect(typesFor("vp09.00.40.08")).toEqual(['video/mp4; codecs="vp09.00.40.08"', 'video/webm; codecs="vp09.00.40.08"']);
    expect(typesFor("opus")).toEqual(['audio/mp4; codecs="opus"', 'audio/webm; codecs="opus"']);
    expect(typesFor("mp4a.40.2")).toEqual(['audio/mp4; codecs="mp4a.40.2"']);
  });

  it("hooguit 64 strings, de strings van het item gaan voor", () => {
    const item = Array.from({ length: 10 }, (_, i) => `avc1.6400${(i + 16).toString(16)}`);
    const many = Array.from({ length: 80 }, (_, i) => `vp09.00.${10 + i}.08`);
    const strings = pickStrings({ candidates: many, item });
    expect(strings.length).toBe(PROFILE_MAX_CODECS);
    expect(strings.slice(0, 10)).toEqual(item);
    expect(pickStrings({ candidates: ["opus", "opus"], item: ["opus"] })).toEqual(["opus"]);
  });

  it("een profiel met 64 strings blijft onder 8 KB", async () => {
    const many = Array.from({ length: 70 }, (_, i) => `vp09.00.${10 + i}.08`);
    const p = await buildProfile({ candidates: many, item: [] }, chromium());
    expect(Object.keys(p.codecs).length).toBe(PROFILE_MAX_CODECS);
    expect(new TextEncoder().encode(JSON.stringify(p)).length).toBeLessThanOrEqual(PROFILE_MAX_BYTES);
  });

  it("fit: boven 8 KB eerst smooth/power van de kandidaten weg, dan kandidaten van achteren", async () => {
    const p = await buildProfile({ candidates: ["vp09.00.40.08", "opus"], item: ["vp09.00.21.08"] }, chromium());
    const long = `vp09.00.40.08.${"9".repeat(9000)}`;
    const big = { ...p, codecs: { ...p.codecs, [long]: { mse: true, file: true, smooth: true } } };
    const out = fit(big, ["vp09.00.21.08"]);
    expect(new TextEncoder().encode(JSON.stringify(out)).length).toBeLessThanOrEqual(PROFILE_MAX_BYTES);
    expect(out.codecs["vp09.00.21.08"]).toEqual(p.codecs["vp09.00.21.08"]);
    expect(out.codecs[long]).toBeUndefined();
  });

  it("de kandidaten uit localStorage per browserversie; het item elke keer opnieuw", async () => {
    const env = chromium();
    await buildProfile({ candidates: CANDIDATES, item: ["vp09.00.21.08"] }, env);
    const raw = env.stored.get("player.caps.v1") ?? "";
    expect(raw).toContain("vp09.00.40.08");
    expect(raw).not.toContain("vp09.00.21.08");
    // Nooit een sid of iets anders dan codecs en de browser
    expect(Object.keys(JSON.parse(raw)).sort()).toEqual(["codecs", "ua"]);
    env.infos.length = 0;
    await buildProfile({ candidates: CANDIDATES, item: ["vp09.00.21.08"] }, env);
    const measured = env.infos.filter((c) => !c.video?.transferFunction).map((c) => c.video?.contentType);
    expect(measured).toEqual(['video/mp4; codecs="vp09.00.21.08"']);
    // Een andere browserversie meet alles opnieuw
    const other = chromium({ userAgent: "Chrome/142", storage: env.storage });
    await buildProfile({ candidates: ["vp09.00.40.08"], item: [] }, other);
    expect(other.infos.some((c) => c.video?.contentType.includes("vp09.00.40.08"))).toBe(true);
  });

  it("geen localStorage (privévenster): het werkt toch", async () => {
    const env = chromium({
      storage: {
        getItem: () => {
          throw new Error("geblokkeerd");
        },
        setItem: () => {
          throw new Error("geblokkeerd");
        },
      },
    });
    const p = await buildProfile({ candidates: ["opus"], item: [] }, env);
    expect(p.codecs.opus.mse).toBe(true);
  });

  it("zonder MSE: engines uit, mse overal false", async () => {
    const p = await buildProfile({ candidates: ["vp09.00.40.08"], item: [] }, chromium({ isTypeSupported: null }));
    expect(p.engines.mse).toBe(false);
    expect(p.codecs["vp09.00.40.08"].mse).toBe(false);
  });

  it("de hoogte is scherm maal devicePixelRatio, binnen 144..4320", async () => {
    expect((await buildProfile({ candidates: [], item: [] }, chromium({ screenHeight: 1080, devicePixelRatio: 2 }))).max.height).toBe(2160);
    expect((await buildProfile({ candidates: [], item: [] }, chromium({ screenHeight: 4000, devicePixelRatio: 3 }))).max.height).toBe(4320);
    expect((await buildProfile({ candidates: [], item: [] }, chromium({ screenHeight: 50 }))).max.height).toBe(144);
  });
});

describe("de naam en het basisprofiel", () => {
  it("de merknaam, anders uit de user agent", () => {
    expect(clientName({ userAgent: "", brands: [{ brand: "Google Chrome", version: "156" }, { brand: "Chromium", version: "156" }] })).toBe(
      "Google Chrome 156",
    );
    expect(clientName({ userAgent: "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0" })).toBe("Firefox 130");
    expect(
      clientName({ userAgent: "Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15" }),
    ).toBe("Safari 17");
  });

  it("Safari krijgt ios-safari, Chrome en Firefox web", () => {
    expect(baseFor({ userAgent: "Mozilla/5.0 (iPhone) AppleWebKit/605.1.15 Version/17.4 Mobile/15E148 Safari/604.1" })).toBe("ios-safari");
    expect(baseFor({ userAgent: "Mozilla/5.0 (X11) AppleWebKit/537.36 Chrome/141.0 Safari/537.36" })).toBe("web");
    expect(baseFor({ userAgent: "Mozilla/5.0 (X11; rv:130.0) Gecko/20100101 Firefox/130.0" })).toBe("web");
  });
});
