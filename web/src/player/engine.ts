// Hoe de <video> zijn beeld krijgt, per sessie (Keuze 32): direct (het bestand), MSE of MMS
// (map.json met fMP4-segmenten: remux en transcode), of native HLS (master.m3u8, Safari zonder
// MMS). Eén interface, zodat de speler bij een wissel (ander spoor, andere kwaliteit, of een
// nieuwe sessie na 410) in dezelfde engine kan blijven als de soort gelijk is.

import { networkError } from "../api";
import { streamUrl } from "../media";
import type { MediaMap, PlayOut } from "../media/types";
import { SrcEngine } from "./direct";
import { MseLoader, mseSupported } from "./mse";
import { nativeHls } from "./native";
import { responseError, StreamError } from "./session";

export interface EngineEvents {
  /** Een fout van een adres van de sessie; de speler start de herstel-lus */
  error(e: unknown): void;
  /** Een hapering (alleen MSE) */
  stall?(): void;
}

export type EngineKind = "file" | "mse" | "hls";

export interface Engine {
  readonly kind: EngineKind;
  start(atS: number): Promise<void>;
  /** Het mislukte opnieuw; gooit als het weer mislukt */
  retry(): Promise<void>;
  /** Stoppen met laden: zo meteen komt een andere sessie */
  hold(): void;
  /** Seconden buffer na de positie */
  ahead(): number;
  destroy(): void;
}

/** Een adres van de sessie: relatief aan /api/v1/stream/{sid}/, of al een pad. */
export function sessionUrl(sid: string, rel: string): string {
  return rel.startsWith("/") ? rel : streamUrl(sid, rel);
}

/** Welke engine deze sessie nodig heeft, of null als deze browser hem niet kan spelen. */
export function engineKind(out: PlayOut): EngineKind | null {
  if (out.urls.file || out.urls.audio) return "file";
  if (out.urls.map && mseSupported(out.mime)) return "mse";
  if (out.urls.master && nativeHls()) return "hls";
  return null;
}

/** map.json van de sessie (no-store); een fout als StreamError. */
export async function fetchMap(sid: string, rel: string, fetchFn: typeof fetch): Promise<MediaMap> {
  let res: Response;
  try {
    res = await fetchFn(sessionUrl(sid, rel), { credentials: "omit", cache: "no-store" });
  } catch (e) {
    throw networkError(e);
  }
  if (!res.ok) throw await responseError(res);
  return (await res.json()) as MediaMap;
}

class MseEngine implements Engine {
  readonly kind = "mse" as const;
  private loader: MseLoader | null = null;
  private startAt = 0;
  private destroyed = false;

  constructor(
    private readonly video: HTMLVideoElement,
    private out: PlayOut,
    private readonly events: EngineEvents,
    private readonly fetchFn: typeof fetch,
  ) {}

  async start(atS: number): Promise<void> {
    this.startAt = atS;
    const map = await fetchMap(this.out.sid, this.out.urls.map ?? "map.json", this.fetchFn);
    if (this.destroyed) return;
    this.loader = new MseLoader(
      this.video,
      { sid: this.out.sid, map, duration: this.out.duration_ms / 1000 },
      { error: (e) => this.events.error(e), stall: () => this.events.stall?.() },
      this.fetchFn,
    );
    await this.loader.open(atS);
  }

  async retry(): Promise<void> {
    if (!this.loader) return this.start(this.startAt);
    return this.loader.retry();
  }

  /** Naar een andere sessie, in dezelfde SourceBuffer. */
  async switchTo(out: PlayOut, flush: boolean): Promise<void> {
    const map = await fetchMap(out.sid, out.urls.map ?? "map.json", this.fetchFn);
    if (this.destroyed) return;
    this.out = out;
    if (!this.loader) {
      await this.start(this.video.currentTime || this.startAt);
      return;
    }
    await this.loader.switchTo({ sid: out.sid, map, duration: out.duration_ms / 1000 }, flush);
  }

  hold(): void {
    this.loader?.hold();
  }

  ahead(): number {
    return this.loader?.ahead() ?? 0;
  }

  destroy(): void {
    this.destroyed = true;
    this.loader?.destroy();
    this.loader = null;
  }
}

/** Een engine voor deze sessie. */
export function createEngine(
  video: HTMLVideoElement,
  out: PlayOut,
  events: EngineEvents,
  fetchFn: typeof fetch = (...a) => fetch(...a),
): Engine & { switchTo(out: PlayOut, flush: boolean): Promise<void> } {
  const kind = engineKind(out);
  if (kind === "mse") return new MseEngine(video, out, events, fetchFn);
  if (kind === "file" || kind === "hls") {
    const url = sessionUrl(out.sid, out.urls.file ?? out.urls.audio ?? out.urls.master ?? "");
    const engine = new SrcEngine(video, url, kind, events, fetchFn);
    return Object.assign(engine, {
      switchTo: (next: PlayOut) =>
        engine.switchTo(sessionUrl(next.sid, next.urls.file ?? next.urls.audio ?? next.urls.master ?? "")),
    });
  }
  throw new StreamError(415, "Deze browser kan deze video niet afspelen", "media_decode");
}

export type AnyEngine = ReturnType<typeof createEngine>;
