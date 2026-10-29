// Het clientprofiel (Keuze 20, docs/fase-5/clientprofiel.md): wat deze browser kan, gemeten per
// codec-string. GET …/codecs geeft de vaste kandidaten plus de strings van het item; de speler
// meet ze en stuurt het profiel mee met play.
//
// - mse: MediaSource.isTypeSupported (of ManagedMediaSource) met video/mp4, en voor VP9, AV1 en
//   Opus ook video/webm (audio met audio/mp4 en audio/webm);
// - file: canPlayType met dezelfde types, niet leeg;
// - smooth en power: decodingInfo (type media-source), het item op zijn maat (als die bekend
//   is, anders 1080p), de kandidaten op 1080p; lukt de vraag niet, dan blijven ze weg;
// - hooguit 64 strings en 8 KB: de strings van het item gaan voor, daarna de kandidaten.
//
// De kandidaten mogen per browserversie in localStorage (alleen gemak, in try/catch); de
// strings van het item meet hij elke keer opnieuw. Geen sid of ander geheim in de opslag.

import type { BaseProfile, ClientProfile, CodecsOut, CodecSupport } from "../media/types";
import { PROFILE_MAX_BYTES, PROFILE_MAX_CODECS } from "../media/types";
import { mediaSourceClass } from "./mse";

export interface Size {
  width: number;
  height: number;
  /** bit/s */
  bitrate: number;
  framerate: number;
}

export const SIZE_1080: Size = { width: 1920, height: 1080, bitrate: 8_000_000, framerate: 24 };

type DecodingInfo = { supported: boolean; smooth: boolean; powerEfficient: boolean };

/** Wat de meting van de browser nodig heeft; los in te vullen in caps.test.ts. */
export interface CapsEnv {
  /** MediaSource.isTypeSupported (of ManagedMediaSource); null zonder MSE */
  isTypeSupported: ((type: string) => boolean) | null;
  managed: boolean;
  canPlayType(type: string): string;
  decodingInfo?: (config: MediaDecodingConfiguration) => Promise<DecodingInfo>;
  matchMedia?: (query: string) => boolean;
  screenHeight: number;
  devicePixelRatio: number;
  userAgent: string;
  brands?: readonly { brand: string; version: string }[];
  audioTracks: boolean;
  storage?: Pick<Storage, "getItem" | "setItem"> | null;
}

const AUDIO = /^(mp4a|opus|flac|ac-3|ec-3|mp3|vorbis|alac)/;
const WEBM = /^(vp0[89]|av01|opus|vorbis)/;

export function isAudio(s: string): boolean {
  return AUDIO.test(s);
}

/** De types waarmee een string gemeten wordt (de eerste is die voor decodingInfo). */
export function typesFor(s: string): string[] {
  const kind = isAudio(s) ? "audio" : "video";
  const out = [`${kind}/mp4; codecs="${s}"`];
  if (WEBM.test(s)) out.push(`${kind}/webm; codecs="${s}"`);
  return out;
}

/** Containers die de <video> als bestand speelt. */
export const FILE_TYPES = [
  "video/mp4",
  "video/webm",
  "video/quicktime",
  "audio/mp4",
  "audio/mpeg",
  "audio/flac",
  "audio/ogg",
  "audio/webm",
] as const;

async function decode(env: CapsEnv, contentType: string, size: Size, hdr?: "pq" | "hlg"): Promise<DecodingInfo | null> {
  if (!env.decodingInfo) return null;
  try {
    const video: VideoConfiguration & { hdrMetadataType?: string } = {
      contentType,
      width: size.width,
      height: size.height,
      bitrate: size.bitrate,
      framerate: size.framerate,
    };
    if (hdr) {
      video.transferFunction = hdr;
      video.colorGamut = "rec2020";
      if (hdr === "pq") video.hdrMetadataType = "smpteSt2086";
    }
    return await env.decodingInfo({ type: "media-source", video });
  } catch {
    return null;
  }
}

/** Eén string meten. */
export async function measureOne(env: CapsEnv, s: string, size: Size): Promise<CodecSupport> {
  const types = typesFor(s);
  const mse = !!env.isTypeSupported && types.some((t) => safe(() => env.isTypeSupported?.(t) ?? false, false));
  const file = types.some((t) => safe(() => env.canPlayType(t) !== "", false));
  const out: CodecSupport = { mse, file };
  if (!isAudio(s) && (mse || file)) {
    const info = await decode(env, types[0], size);
    if (info?.supported) {
      out.smooth = info.smooth;
      out.power = info.powerEfficient;
    }
  }
  return out;
}

function safe<T>(fn: () => T, fallback: T): T {
  try {
    return fn();
  } catch {
    return fallback;
  }
}

/** "Chrome 141", "Firefox 130", "Safari 17": voor deny_by_name van het basisprofiel. */
export function clientName(env: Pick<CapsEnv, "userAgent" | "brands">): string {
  const real = env.brands?.filter((b) => !/not.?a.?brand/i.test(b.brand)) ?? [];
  const brand = real.find((b) => b.brand !== "Chromium") ?? real[0];
  if (brand) return `${brand.brand} ${brand.version}`.slice(0, 64);
  const ua = env.userAgent;
  for (const [re, name] of [
    [/Edg\/(\d+)/, "Edge"],
    [/OPR\/(\d+)/, "Opera"],
    [/Firefox\/(\d+)/, "Firefox"],
    [/Chrome\/(\d+)/, "Chrome"],
    [/Chromium\/(\d+)/, "Chromium"],
    [/Version\/(\d+)[\d.]* .*Safari\//, "Safari"],
  ] as const) {
    const m = re.exec(ua);
    if (m) return `${name} ${m[1]}`;
  }
  return "web";
}

/** Safari (macOS, iOS, iPadOS): basisprofiel ios-safari (MMS, native HLS). */
export function baseFor(env: Pick<CapsEnv, "userAgent">): BaseProfile {
  const ua = env.userAgent;
  return /Safari\//.test(ua) && !/(Chrome|Chromium|CriOS|Edg|OPR|Android|Firefox|FxiOS)\//.test(ua) ? "ios-safari" : "web";
}

/** De volgorde en de grens: de strings van het item eerst, zonder dubbele, hooguit 64. */
export function pickStrings(codecs: CodecsOut): string[] {
  return [...new Set([...codecs.item, ...codecs.candidates])].slice(0, PROFILE_MAX_CODECS);
}

function bytes(p: unknown): number {
  return new TextEncoder().encode(JSON.stringify(p)).length;
}

/** Binnen 8 KB: eerst smooth/power van de kandidaten weg, dan kandidaten van achteren af. */
export function fit(profile: ClientProfile, item: readonly string[]): ClientProfile {
  if (bytes(profile) <= PROFILE_MAX_BYTES) return profile;
  const own = new Set(item);
  const codecs: Record<string, CodecSupport> = {};
  for (const [s, c] of Object.entries(profile.codecs)) codecs[s] = own.has(s) ? c : { mse: c.mse, file: c.file };
  const out = { ...profile, codecs };
  const keys = Object.keys(codecs);
  while (bytes(out) > PROFILE_MAX_BYTES && keys.length) {
    const drop = [...keys].reverse().find((s) => !own.has(s)) ?? keys[keys.length - 1];
    keys.splice(keys.indexOf(drop), 1);
    delete codecs[drop];
  }
  return out;
}

const CACHE_KEY = "player.caps.v1";

function cached(env: CapsEnv): Record<string, CodecSupport> {
  try {
    const raw = env.storage?.getItem(CACHE_KEY);
    if (!raw) return {};
    const data = JSON.parse(raw) as { ua?: unknown; codecs?: unknown };
    if (data.ua !== env.userAgent || !data.codecs || typeof data.codecs !== "object") return {};
    return data.codecs as Record<string, CodecSupport>;
  } catch {
    return {};
  }
}

function store(env: CapsEnv, codecs: Record<string, CodecSupport>): void {
  try {
    env.storage?.setItem(CACHE_KEY, JSON.stringify({ ua: env.userAgent, codecs }));
  } catch {
    // vol of geblokkeerd: dan de volgende keer opnieuw meten
  }
}

/** Speelt deze browser de overdracht (pq of hlg) af? */
async function hdrSupport(env: CapsEnv, fn: "pq" | "hlg"): Promise<boolean> {
  for (const s of ["vp09.02.40.10", "hvc1.2.4.L150.90", "av01.0.08M.10"]) {
    const info = await decode(env, typesFor(s)[0], SIZE_1080, fn);
    if (info?.supported) return true;
  }
  return false;
}

/** Het hele profiel voor play. size: de maat van het item als die bekend is. */
export async function buildProfile(codecs: CodecsOut, env: CapsEnv = browserEnv(), size?: Size): Promise<ClientProfile> {
  const strings = pickStrings(codecs);
  const item = new Set(codecs.item);
  const memo = cached(env);
  const measured: Record<string, CodecSupport> = {};
  const fresh: Record<string, CodecSupport> = {};
  for (const s of strings) {
    if (!item.has(s) && memo[s]) {
      measured[s] = memo[s];
      continue;
    }
    measured[s] = await measureOne(env, s, item.has(s) && size ? size : SIZE_1080);
    if (!item.has(s)) fresh[s] = measured[s];
  }
  if (Object.keys(fresh).length) store(env, { ...memo, ...fresh });
  const file = FILE_TYPES.filter((t) => safe(() => env.canPlayType(t) !== "", false));
  const dv = !!env.isTypeSupported && safe(() => env.isTypeSupported?.('video/mp4; codecs="dvh1.05.06"') ?? false, false);
  const profile: ClientProfile = {
    v: 1,
    base: baseFor(env),
    client: { kind: "web", name: clientName(env), app: "web" },
    engines: {
      mse: !!env.isTypeSupported && !env.managed,
      mms: !!env.isTypeSupported && env.managed,
      native_hls: safe(() => env.canPlayType("application/vnd.apple.mpegurl") !== "", false),
    },
    file: [...file],
    codecs: measured,
    hdr: {
      pq: await hdrSupport(env, "pq"),
      hlg: await hdrSupport(env, "hlg"),
      dv,
      screen: safe(() => env.matchMedia?.("(dynamic-range: high)") ?? false, false),
    },
    max: {
      height: Math.max(144, Math.min(4320, Math.round((env.screenHeight || 1080) * (env.devicePixelRatio || 1)))),
      bitrate_kbps: 0,
    },
    audio: { channels: 2, track_switch: env.audioTracks },
  };
  return fit(profile, codecs.item);
}

/** De meting van deze browser. */
export function browserEnv(): CapsEnv {
  const video = document.createElement("video");
  const ms = mediaSourceClass();
  const nav = navigator as Navigator & { userAgentData?: { brands?: { brand: string; version: string }[] } };
  let storage: Storage | null = null;
  try {
    storage = window.localStorage;
  } catch {
    storage = null;
  }
  return {
    isTypeSupported: ms ? (t) => ms.ctor.isTypeSupported(t) : null,
    managed: ms?.managed ?? false,
    canPlayType: (t) => video.canPlayType(t),
    decodingInfo: navigator.mediaCapabilities
      ? (config) => navigator.mediaCapabilities.decodingInfo(config as MediaDecodingConfiguration)
      : undefined,
    matchMedia: (q) => window.matchMedia(q).matches,
    screenHeight: window.screen?.height ?? 1080,
    devicePixelRatio: window.devicePixelRatio || 1,
    userAgent: nav.userAgent,
    brands: nav.userAgentData?.brands,
    audioTracks: "audioTracks" in HTMLMediaElement.prototype,
    storage,
  };
}
