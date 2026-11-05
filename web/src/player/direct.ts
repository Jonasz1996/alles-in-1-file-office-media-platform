// Direct afspelen (Keuze 32): video.src = /api/v1/stream/{sid}/file. De <video> doet de
// Range-verzoeken zelf. Gaat er iets mis, dan kijkt de engine met één klein verzoek (de eerste
// byte) wat de server zegt: 410, 403, 503 of geen verbinding (een fout met code voor de
// herstel-lus van session.ts), of 200 (dan kan de browser het bestand niet afspelen).
//
// Dezelfde engine speelt native HLS (native.ts: master.m3u8, Safari zonder MMS).

import { networkError } from "../api";
import type { Engine, EngineEvents } from "./engine";
import { responseError, StreamError } from "./session";

/** Een fout van de <video> zelf: wat zegt de server over het adres? */
export async function probeUrl(url: string, fetchFn: typeof fetch): Promise<StreamError> {
  let res: Response;
  try {
    res = await fetchFn(url, { headers: { Range: "bytes=0-0" }, credentials: "omit", cache: "no-store" });
  } catch (e) {
    throw networkError(e);
  }
  if (!res.ok) return responseError(res);
  void res.body?.cancel();
  return new StreamError(422, "De browser kan deze video niet afspelen", "media_decode");
}

export class SrcEngine implements Engine {
  readonly kind: "file" | "hls";
  private url: string;
  private last = 0;
  private wasPlaying = false;
  private destroyed = false;
  private failed = false;
  private readonly off: (() => void)[] = [];

  constructor(
    private readonly video: HTMLVideoElement,
    url: string,
    kind: "file" | "hls",
    private readonly events: EngineEvents,
    private readonly fetchFn: typeof fetch = (...a) => fetch(...a),
  ) {
    this.url = url;
    this.kind = kind;
  }

  async start(atS: number): Promise<void> {
    const on = (name: keyof HTMLMediaElementEventMap, fn: () => void) => {
      this.video.addEventListener(name, fn);
      this.off.push(() => this.video.removeEventListener(name, fn));
    };
    on("timeupdate", () => {
      if (!this.failed && this.video.readyState >= 1) this.last = this.video.currentTime;
    });
    on("error", () => void this.onError());
    this.load(atS);
  }

  private load(atS: number): void {
    this.failed = false;
    this.video.src = this.url;
    // Voor de metadata er is: de beginpositie (default playback start position)
    if (atS > 0) this.video.currentTime = atS;
    this.last = atS;
  }

  private async onError(): Promise<void> {
    if (this.destroyed || this.failed || !this.video.error) return;
    this.failed = true;
    this.wasPlaying = this.wasPlaying || !this.video.paused || this.video.autoplay;
    let e: unknown;
    try {
      e = await probeUrl(this.url, this.fetchFn);
    } catch (err) {
      e = err;
    }
    if (!this.destroyed) this.events.error(e);
  }

  /** Opnieuw laden op de plek waar hij was; gooit als het weer mislukt. */
  async retry(): Promise<void> {
    if (this.destroyed) throw new DOMException("afgebroken", "AbortError");
    const at = this.last;
    const playing = this.wasPlaying;
    await new Promise<void>((ok, fail) => {
      const done = () => {
        clean();
        ok();
      };
      const bad = () => {
        clean();
        probeUrl(this.url, this.fetchFn).then(fail, fail);
      };
      const clean = () => {
        this.video.removeEventListener("loadeddata", done);
        this.video.removeEventListener("error", bad);
      };
      this.video.addEventListener("loadeddata", done);
      this.video.addEventListener("error", bad);
      this.load(at);
    });
    // De fout is opgelost: de gewone luisteraar mag weer
    this.failed = false;
    if (playing) void this.video.play().catch(() => {});
  }

  async switchTo(url: string): Promise<void> {
    const at = this.video.readyState >= 1 ? this.video.currentTime : this.last;
    const playing = !this.video.paused || this.wasPlaying;
    this.url = url;
    this.load(at);
    if (playing) void this.video.play().catch(() => {});
  }

  /** Stoppen met laden tot de wissel er is (het oude adres gaat zo dicht). */
  hold(): void {
    this.wasPlaying = !this.video.paused;
    this.failed = true;
  }

  ahead(): number {
    const t = this.video.currentTime;
    const b = this.video.buffered;
    for (let i = 0; i < b.length; i++) if (t >= b.start(i) - 0.1 && t < b.end(i)) return b.end(i) - t;
    return 0;
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    for (const off of this.off.splice(0)) off();
    this.video.removeAttribute("src");
    try {
      this.video.load();
    } catch {
      // al weg
    }
  }
}
