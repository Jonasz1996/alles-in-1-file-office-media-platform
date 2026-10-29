// De lader voor MSE en MMS (Keuze 32): map.json, één SourceBuffer met timestampOffset −1, 30 s
// vooruit en weg wat meer dan 60 s achter ligt, zoeken met abort(), een nieuwe init als X-Init
// verandert, en over gaten tot 0,5 s springen. Bovenaan de pure delen (mse.test.ts), daaronder
// de lader zelf.
//
// X-Init is de sleutel van een init, geen teller (m4.md, afwijking 5): bij de echte routes geeft
// A, B, A de generaties 1, 2, 1. Een lagere X-Init is dus niet oud; de inits blijven per sleutel
// bewaard en worden nooit op volgorde weggegooid.

import { networkError } from "../api";
import { streamUrl } from "../media";
import { segmentCount, segmentOf, segmentStart } from "../media/api";
import { INIT_HEADER, type MediaMap, type SegmentGrid } from "../media/types";
import { isAbort, responseError, StreamError } from "./session";

/** Zo ver vooruit buffert hij (s). */
export const AHEAD_S = 30;
/** Wat verder achter de positie ligt, gaat weg (s). */
export const BEHIND_S = 60;
/** Na QuotaExceededError nog maar zo ver vooruit (s). */
export const QUOTA_AHEAD_S = 10;
/** Over een gat tot zo groot springt hij (s). */
export const GAP_MAX_S = 0.5;
/** Zo lang na waiting kijkt hij of er een gat is (ms; spike S4). */
export const GAP_WAIT_MS = 250;
/** Twee haperingen in een minuut bij transcode: een trede lager. */
export const STALL_LIMIT = 2;
export const STALL_WINDOW_MS = 60_000;

// --- de pure delen ---

export type Span = readonly [number, number];

/** TimeRanges als lijst. */
export function spans(r: Pick<TimeRanges, "length" | "start" | "end">): Span[] {
  const out: Span[] = [];
  for (let i = 0; i < r.length; i++) out.push([r.start(i), r.end(i)]);
  return out;
}

/** Het stuk waar t in ligt (met wat speling aan het begin), of null. */
export function spanAt(list: readonly Span[], t: number, slack = 0.1): Span | null {
  return list.find(([a, b]) => t >= a - slack && t < b) ?? null;
}

/** Hoeveel seconden er na t gebufferd zijn (0 als t niet in een stuk ligt). */
export function aheadOf(list: readonly Span[], t: number): number {
  const s = spanAt(list, t);
  return s ? s[1] - t : 0;
}

/** Het begin van segment k in seconden. */
export function segmentTime(segments: SegmentGrid, k: number): number {
  return segmentStart(segments, k) / 1000;
}

/** Het segment voor tijd t (s). */
export function segmentAt(segments: SegmentGrid, t: number): number {
  return segmentOf(segments, Math.max(0, t * 1000));
}

/** Het eind van het laatste segment in ms (bij het rooster: het laatste videobeeld). */
export function mediaEnd(segments: SegmentGrid): number {
  if (Array.isArray(segments)) {
    const last = segments[segments.length - 1];
    return last ? last[0] + last[1] : 0;
  }
  return segments.last_frame_ms;
}

/**
 * Het eerste segment dat hij na een sprong naar t moet halen. Ligt t in een gebufferd stuk, dan
 * het segment na het eind daarvan (200 ms speling: een stuk eindigt vaak net voor de grens; een
 * half stuk wordt opnieuw gehaald, overlap mag in MSE). Loopt het stuk tot het eind, dan count.
 */
export function nextSegment(list: readonly Span[], t: number, segments: SegmentGrid): number {
  const count = segmentCount(segments);
  const s = spanAt(list, t);
  if (!s) return segmentAt(segments, t);
  const endMs = s[1] * 1000;
  if (endMs >= mediaEnd(segments) - 300) return count;
  return Math.min(count, segmentOf(segments, endMs + 200));
}

/** Wat er weg mag: alles tot BEHIND_S achter t, en (aheadMax) alles verder dan aheadMax na t. */
export function evictable(list: readonly Span[], t: number, behind = BEHIND_S, aheadMax = Infinity): Span[] {
  const out: Span[] = [];
  const cut = t - behind;
  if (list.length && list[0][0] < cut) out.push([list[0][0], cut]);
  const far = t + aheadMax;
  const last = list[list.length - 1];
  if (Number.isFinite(far) && last && last[1] > far) out.push([Math.max(far, list[0][0]), last[1]]);
  return out;
}

/**
 * De beslissing over een gat (spike S4): staat de speler stil (waiting) en begint het volgende
 * gebufferde stuk binnen GAP_MAX_S na t, dan daarheen. Anders null (er moet nog data komen).
 */
export function gapTarget(list: readonly Span[], t: number, max = GAP_MAX_S): number | null {
  // Midden in een stuk (niet aan het eind) is geen gat
  if (list.some(([a, b]) => t >= a && t < b - 0.05)) return null;
  const next = list.find(([a]) => a > t);
  return next && next[0] - t <= max ? next[0] : null;
}

/** De inits per sleutel (X-Init). current: de sleutel van de laatst toegevoegde. */
export class Inits {
  current: string | null = null;
  private readonly cache = new Map<string, ArrayBuffer>();

  /** Moet er voor dit segment eerst een andere init in? (een lege kop: nee) */
  needs(key: string | null): boolean {
    return key !== null && key !== "" && key !== this.current;
  }

  get(key: string): ArrayBuffer | undefined {
    return this.cache.get(key);
  }

  put(key: string, data: ArrayBuffer): void {
    this.cache.set(key, data);
  }

  has(key: string): boolean {
    return this.cache.has(key);
  }
}

/** Telt haperingen; true als er STALL_LIMIT binnen STALL_WINDOW_MS waren. */
export class Stalls {
  private times: number[] = [];

  add(now: number): boolean {
    this.times = this.times.filter((t) => now - t < STALL_WINDOW_MS);
    this.times.push(now);
    if (this.times.length < STALL_LIMIT) return false;
    this.times = [];
    return true;
  }

  reset(): void {
    this.times = [];
  }
}

// --- de lader ---

type MSCtor = typeof MediaSource;

/** De MediaSource van deze browser: ManagedMediaSource (Safari 17) of MediaSource. */
export function mediaSourceClass(): { ctor: MSCtor; managed: boolean } | null {
  const g = globalThis as unknown as { ManagedMediaSource?: MSCtor; MediaSource?: MSCtor };
  if (g.ManagedMediaSource) return { ctor: g.ManagedMediaSource, managed: true };
  if (g.MediaSource) return { ctor: g.MediaSource, managed: false };
  return null;
}

export function mseSupported(mime: string): boolean {
  const ms = mediaSourceClass();
  try {
    return !!ms && ms.ctor.isTypeSupported(mime);
  } catch {
    return false;
  }
}

export interface MseEvents {
  /** Een segment, init of map lukte niet; het laden staat stil tot retry() of een nieuwe sessie */
  error(e: unknown): void;
  /** Een hapering: waiting, niet door zoeken of de start */
  stall?(): void;
}

const ABORTED = (): DOMException => new DOMException("afgebroken", "AbortError");

/** Een sessie: sid en map. */
export interface MseSource {
  sid: string;
  map: MediaMap;
  duration: number;
}

export class MseLoader {
  private ms: MediaSource | null = null;
  private sb: SourceBuffer | null = null;
  private objectUrl: string | null = null;
  private src: MseSource;
  private inits = new Inits();
  private next = 0;
  private inflight = -1;
  private aheadLimit = AHEAD_S;
  private token = 0;
  private ctrl: AbortController | null = null;
  private pumping: Promise<void> | null = null;
  private again = false;
  private failed = false;
  private destroyed = false;
  private seekedAt = 0;
  private played = false;
  private gapTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly off: (() => void)[] = [];
  private readonly fetchFn: typeof fetch;

  constructor(
    private readonly video: HTMLVideoElement,
    src: MseSource,
    private readonly events: MseEvents,
    fetchFn: typeof fetch = (...a) => fetch(...a),
  ) {
    this.src = src;
    this.fetchFn = fetchFn;
  }

  /** De MediaSource aan de video hangen en vanaf startS laden. */
  async open(startS: number): Promise<void> {
    const found = mediaSourceClass();
    if (!found) throw new StreamError(415, "MediaSource ontbreekt", "media_unsupported");
    const ms = new found.ctor();
    this.ms = ms;
    if (found.managed) {
      // MMS speelt anders niet (Keuze 32); AirPlay valt zo weg
      (this.video as HTMLVideoElement & { disableRemotePlayback: boolean }).disableRemotePlayback = true;
    }
    const opened = new Promise<void>((ok) => ms.addEventListener("sourceopen", () => ok(), { once: true }));
    this.objectUrl = URL.createObjectURL(ms);
    this.video.src = this.objectUrl;
    await opened;
    if (this.destroyed) return;
    this.sb = ms.addSourceBuffer(this.src.map.mime);
    this.sb.mode = "segments";
    this.sb.timestampOffset = this.src.map.offset;
    if (this.src.duration > 0) ms.duration = this.src.duration;
    this.listen();
    this.next = segmentAt(this.src.map.segments, startS);
    if (startS > 0) this.video.currentTime = startS;
    this.seekedAt = performance.now();
    this.kick();
  }

  private listen(): void {
    const on = <K extends keyof HTMLVideoElementEventMap>(name: K, fn: () => void) => {
      this.video.addEventListener(name, fn);
      this.off.push(() => this.video.removeEventListener(name, fn));
    };
    on("timeupdate", () => this.kick());
    on("seeking", () => this.onSeeking());
    on("seeked", () => (this.seekedAt = performance.now()));
    on("waiting", () => this.onWaiting());
    on("playing", () => {
      this.played = true;
      this.clearGap();
    });
  }

  /** Seconden gebufferd na de positie. */
  ahead(): number {
    return aheadOf(this.buffered(), this.video.currentTime);
  }

  private buffered(): Span[] {
    try {
      return this.sb ? spans(this.sb.buffered) : [];
    } catch {
      return [];
    }
  }

  private onSeeking(): void {
    if (!this.sb) return;
    const t = this.video.currentTime;
    const want = nextSegment(this.buffered(), t, this.src.map.segments);
    this.clearGap();
    if (this.pumping && this.inflight === want) return;
    this.next = want;
    this.restart();
  }

  private onWaiting(): void {
    if (this.video.seeking || this.destroyed) {
      this.kick();
      return;
    }
    // Een hapering telt niet voor het eerste beeld, en niet vlak na een sprong of wissel
    if (this.played && performance.now() - this.seekedAt > 2000) this.events.stall?.();
    this.clearGap();
    this.gapTimer = setTimeout(() => {
      this.gapTimer = null;
      if (this.destroyed || this.video.readyState >= 3) return;
      const to = gapTarget(this.buffered(), this.video.currentTime);
      if (to !== null) this.video.currentTime = to;
    }, GAP_WAIT_MS);
    this.kick();
  }

  private clearGap(): void {
    if (this.gapTimer) clearTimeout(this.gapTimer);
    this.gapTimer = null;
  }

  /** Alles wat loopt afbreken en opnieuw beginnen bij this.next. */
  private restart(): void {
    this.token++;
    this.ctrl?.abort();
    if (this.sb?.updating && this.ms?.readyState === "open") {
      try {
        this.sb.abort();
      } catch {
        // al klaar
      }
    }
    this.kick(true);
  }

  private kick(force = false): void {
    if (this.destroyed || this.failed || !this.sb) return;
    if (this.pumping) {
      if (force) this.again = true;
      return;
    }
    this.pumping = this.pump(this.token).finally(() => {
      this.pumping = null;
      if (this.again) {
        this.again = false;
        this.kick();
      }
    });
  }

  private async pump(token: number): Promise<void> {
    const live = () => token === this.token && !this.destroyed && !this.failed;
    try {
      while (live()) {
        const t = this.video.currentTime;
        await this.evict(t, token);
        if (!live()) return;
        const count = segmentCount(this.src.map.segments);
        if (this.next >= count) {
          this.end();
          return;
        }
        // Genoeg vooruit (en het volgende segment begint pas na de buffer): wachten op timeupdate
        if (this.ahead() >= this.aheadLimit) return;
        await this.load(this.next, token);
        if (!live()) return;
        this.next++;
      }
    } catch (e) {
      if (isAbort(e) || !live()) return;
      this.failed = true;
      this.events.error(e);
    } finally {
      this.inflight = -1;
    }
  }

  /** Segment k halen (en zo nodig eerst zijn init) en toevoegen. */
  private async load(k: number, token: number): Promise<void> {
    this.inflight = k;
    const ctrl = new AbortController();
    this.ctrl = ctrl;
    const { sid } = this.src;
    const res = await this.get(streamUrl(sid, `${k}.m4s`), ctrl.signal);
    const key = res.headers.get(INIT_HEADER);
    const data = await res.arrayBuffer();
    if (token !== this.token) throw ABORTED();
    if (this.inits.needs(key) || this.inits.current === null) {
      const want = key || String(this.src.map.gen);
      let init = this.inits.get(want);
      if (!init) {
        const r = await this.get(streamUrl(sid, `init.mp4?g=${encodeURIComponent(want)}`), ctrl.signal);
        init = await r.arrayBuffer();
        if (token !== this.token) throw ABORTED();
        this.inits.put(want, init);
      }
      await this.append(init, token);
      this.inits.current = want;
    }
    await this.append(data, token);
  }

  private async get(url: string, signal: AbortSignal): Promise<Response> {
    let res: Response;
    try {
      res = await this.fetchFn(url, { credentials: "omit", signal });
    } catch (e) {
      throw networkError(e);
    }
    if (!res.ok) throw await responseError(res);
    return res;
  }

  /** Toevoegen; bij QuotaExceededError eerst achter weg, dan vooruit tot 10 s, en opnieuw. */
  private async append(data: ArrayBuffer, token: number): Promise<void> {
    for (let step = 0; ; step++) {
      try {
        await this.op(token, (sb) => sb.appendBuffer(data));
        return;
      } catch (e) {
        if ((e as { name?: string }).name !== "QuotaExceededError" || step >= 2) throw e;
        const t = this.video.currentTime;
        if (step === 0) {
          await this.removeAll(evictable(this.buffered(), t, 5), token);
        } else {
          this.aheadLimit = QUOTA_AHEAD_S;
          await this.removeAll(evictable(this.buffered(), t, 5, QUOTA_AHEAD_S), token);
        }
      }
    }
  }

  private async evict(t: number, token: number): Promise<void> {
    // Ver vooruit (van een eerdere sprong) mag ook weg, zodat het quotum niet volloopt
    await this.removeAll(evictable(this.buffered(), t, BEHIND_S, AHEAD_S * 4), token);
  }

  private async removeAll(list: readonly Span[], token: number): Promise<void> {
    for (const [a, b] of list) if (b > a) await this.op(token, (sb) => sb.remove(a, b));
  }

  /** Eén bewerking op de SourceBuffer, en wachten op updateend. */
  private op(token: number, run: (sb: SourceBuffer) => void): Promise<void> {
    const sb = this.sb;
    if (!sb || token !== this.token) return Promise.reject(ABORTED());
    return new Promise((ok, fail) => {
      const done = () => {
        clean();
        ok();
      };
      const aborted = () => {
        clean();
        fail(ABORTED());
      };
      const failed = () => {
        clean();
        fail(new StreamError(0, "De video is beschadigd", "media_decode"));
      };
      const clean = () => {
        sb.removeEventListener("updateend", done);
        sb.removeEventListener("abort", aborted);
        sb.removeEventListener("error", failed);
      };
      sb.addEventListener("updateend", done);
      sb.addEventListener("abort", aborted);
      sb.addEventListener("error", failed);
      try {
        run(sb);
      } catch (e) {
        clean();
        fail(e);
      }
    });
  }

  private end(): void {
    const ms = this.ms;
    if (!ms || ms.readyState !== "open" || this.sb?.updating) return;
    try {
      ms.endOfStream();
    } catch {
      // nog bezig
    }
  }

  /** Na een fout: het segment opnieuw (de herstel-lus van session.ts). Gooit als het weer mislukt. */
  async retry(): Promise<void> {
    if (this.destroyed || !this.sb) throw ABORTED();
    await this.pumping;
    this.failed = false;
    const token = ++this.token;
    try {
      const want = nextSegment(this.buffered(), this.video.currentTime, this.src.map.segments);
      this.next = want;
      if (want < segmentCount(this.src.map.segments)) {
        await this.load(want, token);
        this.next = want + 1;
      }
    } catch (e) {
      if (!isAbort(e)) this.failed = true;
      throw e;
    } finally {
      this.inflight = -1;
    }
    this.kick();
  }

  /** Stoppen met laden tot switchTo: de oude sessie gaat zo dicht, zijn verzoeken geven 410. */
  hold(): void {
    this.token++;
    this.ctrl?.abort();
    this.failed = true;
  }

  /**
   * Verder met een nieuwe sessie (na 410, of een andere kwaliteit of spoor). flush: wat er staat
   * weg (ander geluid of beeld); zonder flush loopt de buffer door (zelfde keuze). De inits van
   * de oude sessie tellen niet meer: de sleutels zijn per sessie.
   */
  async switchTo(src: MseSource, flush: boolean): Promise<void> {
    if (this.destroyed || !this.sb || !this.ms) throw ABORTED();
    this.token++;
    this.ctrl?.abort();
    if (this.sb.updating && this.ms.readyState === "open") {
      try {
        this.sb.abort();
      } catch {
        // al klaar
      }
    }
    await this.pumping;
    const token = this.token;
    const mimeChanged = src.map.mime !== this.src.map.mime;
    this.src = src;
    this.inits = new Inits();
    this.failed = false;
    this.aheadLimit = AHEAD_S;
    if (mimeChanged) {
      const sb = this.sb as SourceBuffer & { changeType?: (type: string) => void };
      if (!sb.changeType) throw new StreamError(415, "Ander formaat", "media_unsupported");
      sb.changeType(src.map.mime);
      sb.timestampOffset = src.map.offset;
    }
    const t = this.video.currentTime;
    if (flush || mimeChanged) {
      await this.removeAll(this.buffered(), token);
      this.next = segmentAt(src.map.segments, t);
    } else {
      this.next = nextSegment(this.buffered(), t, src.map.segments);
    }
    this.seekedAt = performance.now();
    this.kick();
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.token++;
    this.ctrl?.abort();
    this.clearGap();
    for (const off of this.off.splice(0)) off();
    this.video.removeAttribute("src");
    try {
      this.video.load();
    } catch {
      // geen video meer
    }
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = null;
    this.ms = null;
    this.sb = null;
  }
}
