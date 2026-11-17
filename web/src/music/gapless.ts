// Gapless (fase 5, M9; Keuze 38): één MediaSource en één SourceBuffer voor een reeks nummers.
//
// Voor nummer i, met T_i = de som van samples_j/rate van de nummers ervoor:
//   timestampOffset    = T_i − delay_i/rate
//   appendWindowStart  = T_i
//   appendWindowEnd    = T_i + samples_i/rate
// audio.mp4 (remux_audio, -use_editlist 0) bevat alle samples, ook de voorloop: de browser
// knipt op de grenzen van het venster, op het sample. Wisselt de codec (FLAC na MP3), dan
// changeType(). MP3 komt rauw als audio/mpeg: zonder tijden in de stroom, dus mode "sequence"
// (de offset zet dan het begin van de groep). Zonder gapless-gegevens (audio_info nog niet
// klaar) komt het nummer achter het vorige, op het eind van wat de buffer heeft: aansluitend,
// maar niet op het sample.
//
// Het volgende nummer laden we als er PREFETCH_S over is; alleen het huidige en het volgende
// staan in de buffer (wat al gespeeld is, gaat eruit). Een ander nummer kiezen of ver zoeken is
// een nieuwe run (een nieuwe MediaSource): dat doet de speler (player.ts).
//
// De run kent geen API: de speler geeft per nummer een RunTrack (het adres onder
// /api/v1/stream/{sid}/, de mime en de gapless-gegevens) en sluit de sessie in release().

import type { Gapless } from "../media/types";

/** Zoveel seconden voor het eind van de buffer begint het volgende nummer. */
export const PREFETCH_S = 20;
/** Zoveel bytes per appendBuffer. */
export const CHUNK_BYTES = 256 * 1024;
/** Bij een volle buffer: alles tot zoveel seconden voor de positie mag eruit. */
export const BEHIND_S = 10;
/** Zo vaak (ms) kijken of de buffer weer ruimte heeft. */
export const QUOTA_WAIT_MS = 500;
/** Zo vaak opnieuw proberen als een nummer niet laadt (met een nieuwe sessie). */
export const TRACK_RETRIES = 3;

// --- de rekensom (puur) ---

export interface Placement {
  /** timestampOffset */
  offset: number;
  /** appendWindowStart */
  start: number;
  /** appendWindowEnd (Infinity: onbekend, zonder gapless-gegevens) */
  end: number;
}

/** Bruikbare gapless-gegevens (alles een eindig getal, rate en samples > 0, delay ≥ 0). */
export function validGapless(g: Gapless | null | undefined): g is Gapless {
  return (
    !!g &&
    Number.isFinite(g.rate) &&
    g.rate > 0 &&
    Number.isFinite(g.samples) &&
    g.samples > 0 &&
    Number.isFinite(g.delay) &&
    g.delay >= 0
  );
}

/** Waar een nummer in de tijdlijn komt, als het op T begint. */
export function placement(T: number, g: Gapless | null | undefined): Placement {
  if (!validGapless(g)) return { offset: T, start: T, end: Number.POSITIVE_INFINITY };
  return { offset: T - g.delay / g.rate, start: T, end: T + g.samples / g.rate };
}

/** De lengte van een nummer in seconden, uit gapless of de duur van de server. */
export function trackSeconds(g: Gapless | null | undefined, durationMs: number | null | undefined): number | null {
  if (validGapless(g)) return g.samples / g.rate;
  return typeof durationMs === "number" && durationMs > 0 ? durationMs / 1000 : null;
}

/** Hetzelfde type (zonder spaties en hoofdletters): dan is changeType niet nodig. */
export function sameType(a: string, b: string): boolean {
  const norm = (s: string) => s.replace(/\s+/g, "").toLowerCase();
  return norm(a) === norm(b);
}

/** Het begin van de buffer en het eind van het bereik rond t (of null). */
export function rangeAround(r: Pick<TimeRanges, "length" | "start" | "end">, t: number, slack = 0.05): [number, number] | null {
  for (let i = 0; i < r.length; i++) {
    if (r.start(i) - slack <= t && t < r.end(i) + slack) return [r.start(i), r.end(i)];
  }
  return null;
}

// --- de run ---

/** Eén nummer voor de run (de speler maakte de sessie). */
export interface RunTrack {
  /** Het nummer in de rij (QueueItem.uid) */
  key: string;
  /** /api/v1/stream/{sid}/audio.mp4 of …/file (MP3) */
  url: string;
  /** Voor addSourceBuffer en changeType */
  mime: string;
  /** audio/mpeg: mode "sequence" */
  sequence: boolean;
  gapless: Gapless | null;
  /** De sessie sluiten (na het laden, of als de run stopt) */
  release(): void;
}

export interface Entry {
  key: string;
  start: number;
  end: number;
}

type MSCtor = typeof MediaSource;

export interface RunDeps {
  ms: { ctor: MSCtor; managed: boolean };
  fetch: typeof fetch;
  createObjectURL(o: MediaSource): string;
  revokeObjectURL(url: string): void;
  /** Een sessie voor het nummer key; null: het staat niet meer in de rij, of kan niet via MSE */
  prepare(key: string): Promise<RunTrack | null>;
  /** Het nummer na key in de rij, of null */
  after(key: string): string | null;
  /** Het nummer dat speelt, is nu key */
  onTrack(key: string): void;
  /** Laden lukte niet (ook niet opnieuw); de run staat stil */
  onError(e: unknown, key: string): void;
  /** Een fout uit een antwoord dat niet ok is */
  responseError(res: Response): Promise<unknown>;
  sleep(ms: number): Promise<void>;
}

const ABORT = (): DOMException => new DOMException("afgebroken", "AbortError");

function isAbortError(e: unknown): boolean {
  return typeof e === "object" && e !== null && (e as { name?: unknown }).name === "AbortError";
}

function isQuota(e: unknown): boolean {
  return typeof e === "object" && e !== null && (e as { name?: unknown }).name === "QuotaExceededError";
}

/**
 * Een reeks nummers in één MediaSource. start() hangt hem aan het element en laadt het eerste
 * nummer (vanaf startAt seconden); daarna het volgende als er PREFETCH_S over is, tot prepare
 * null geeft: dan endOfStream, en "ended" van het element is voor de speler.
 */
export class GaplessRun {
  readonly entries: Entry[] = [];
  private source: MediaSource | null = null;
  private buffer: SourceBuffer | null = null;
  private url: string | null = null;
  private mime = "";
  private closed = false;
  private readonly abort = new AbortController();
  private readonly waiters = new Set<() => void>();
  private current: string | null = null;
  /** Alles voor deze tijd is al uit de buffer */
  private trimmed = 0;
  private open: RunTrack | null = null;

  constructor(
    private readonly audio: HTMLMediaElement,
    private readonly first: RunTrack,
    private readonly startAt: number,
    private readonly deps: RunDeps,
  ) {}

  /** Het nummer dat nu speelt (null: nog niets). */
  get currentKey(): string | null {
    return this.current;
  }

  get isClosed(): boolean {
    return this.closed;
  }

  entry(key: string): Entry | null {
    return this.entries.find((e) => e.key === key) ?? null;
  }

  /** Staat tijd t (in de tijdlijn van de run) in de buffer? */
  buffered(t: number): boolean {
    try {
      return rangeAround(this.audio.buffered, t) !== null;
    } catch {
      return false;
    }
  }

  start(): void {
    const source = new this.deps.ms.ctor();
    this.source = source;
    if (this.deps.ms.managed) (this.audio as HTMLMediaElement & { disableRemotePlayback: boolean }).disableRemotePlayback = true;
    this.url = this.deps.createObjectURL(source);
    source.addEventListener("sourceopen", () => void this.run(), { once: true });
    this.audio.addEventListener("timeupdate", this.onTime);
    this.audio.addEventListener("seeking", this.onTime);
    this.audio.src = this.url;
  }

  /** Stoppen: laden afbreken, sessies sluiten, de bron van het element weg (detach: false laat
   * het element staan voor de volgende run, die zijn eigen src zet). */
  close(detach = true): void {
    if (this.closed) return;
    this.closed = true;
    this.abort.abort();
    for (const w of [...this.waiters]) w();
    this.waiters.clear();
    this.open?.release();
    this.open = null;
    this.audio.removeEventListener("timeupdate", this.onTime);
    this.audio.removeEventListener("seeking", this.onTime);
    if (detach) {
      this.audio.removeAttribute("src");
      try {
        this.audio.load();
      } catch {
        // geen element meer
      }
    }
    if (this.url) this.deps.revokeObjectURL(this.url);
    this.url = null;
  }

  // --- het laden ---

  private async run(): Promise<void> {
    let track: RunTrack | null = this.first;
    let T = 0;
    let firstAppend = true;
    try {
      while (track && !this.closed) {
        const end = await this.load(track, T, firstAppend);
        firstAppend = false;
        T = end;
        await this.untilNear(T);
        if (this.closed) return;
        const key = this.deps.after(track.key);
        track = key === null ? null : await this.prepareWithRetry(key);
      }
      if (!this.closed && this.source?.readyState === "open") {
        await this.idle();
        this.source.endOfStream();
      }
    } catch (e) {
      if (this.closed || isAbortError(e)) return;
      this.deps.onError(e, track?.key ?? this.first.key);
    }
  }

  /** Een sessie voor key, met nieuwe pogingen bij een fout (1, 2, 4 s). */
  private async prepareWithRetry(key: string): Promise<RunTrack | null> {
    for (let attempt = 0; ; attempt++) {
      try {
        return await this.deps.prepare(key);
      } catch (e) {
        if (this.closed || isAbortError(e) || attempt >= TRACK_RETRIES - 1) throw e;
        await this.deps.sleep(1000 * 2 ** attempt);
        if (this.closed) throw ABORT();
      }
    }
  }

  /** Laadt één nummer vanaf T; geeft waar het volgende begint. Bij een fout opnieuw, met een
   * nieuwe sessie, na het weghalen van wat er van dit nummer stond. */
  private async load(track: RunTrack, T: number, firstAppend: boolean): Promise<number> {
    const place = placement(T, track.gapless);
    const entry: Entry = { key: track.key, start: place.start, end: place.end };
    this.entries.push(entry);
    let attempt = 0;
    let current: RunTrack = track;
    for (;;) {
      this.open = current;
      try {
        await this.configure(current, place);
        await this.stream(current, firstAppend);
        current.release();
        this.open = null;
        break;
      } catch (e) {
        current.release();
        this.open = null;
        if (this.closed || isAbortError(e) || ++attempt >= TRACK_RETRIES) throw e;
        await this.deps.sleep(1000 * 2 ** (attempt - 1));
        if (this.closed) throw ABORT();
        const fresh = await this.deps.prepare(track.key);
        if (!fresh) throw e;
        current = fresh;
        await this.remove(place.start, Number.POSITIVE_INFINITY);
      }
    }
    if (Number.isFinite(place.end)) return place.end;
    // Zonder gapless: het eind van wat er van dit nummer staat
    const end = this.bufferEnd();
    entry.end = Math.max(place.start, end);
    return entry.end;
  }

  private bufferEnd(): number {
    const b = this.buffer?.buffered;
    if (!b || !b.length) return 0;
    return b.end(b.length - 1);
  }

  /** De buffer klaarzetten voor dit nummer: type, mode, venster en offset. */
  private async configure(track: RunTrack, place: Placement): Promise<void> {
    const source = this.source;
    if (!source) throw ABORT();
    if (!this.buffer) {
      this.buffer = source.addSourceBuffer(track.mime);
      this.mime = track.mime;
    } else if (!sameType(this.mime, track.mime)) {
      await this.idle();
      const sb = this.buffer as SourceBuffer & { changeType?: (type: string) => void };
      if (typeof sb.changeType !== "function") throw new Error("changeType ontbreekt");
      sb.changeType(track.mime);
      this.mime = track.mime;
    }
    await this.idle();
    const sb = this.buffer;
    const mode: AppendMode = track.sequence ? "sequence" : "segments";
    if (sb.mode !== mode) sb.mode = mode;
    // Eerst het eind open, dan het begin, dan het eind: start < end blijft altijd waar
    sb.appendWindowEnd = Number.POSITIVE_INFINITY;
    sb.appendWindowStart = place.start;
    sb.appendWindowEnd = place.end;
    sb.timestampOffset = place.offset;
  }

  /** Haalt het nummer op en zet het in stukken in de buffer. */
  private async stream(track: RunTrack, firstAppend: boolean): Promise<void> {
    const res = await this.deps.fetch(track.url, {
      credentials: "omit",
      cache: "no-store",
      signal: this.abort.signal,
    });
    if (!res.ok) throw await this.deps.responseError(res);
    const body = res.body;
    if (!body) {
      await this.append(new Uint8Array(await res.arrayBuffer()));
      if (firstAppend) this.seekStart();
      return;
    }
    const reader = body.getReader();
    const parts: Uint8Array[] = [];
    let size = 0;
    let first = firstAppend;
    const flush = async () => {
      if (!size) return;
      const all = new Uint8Array(size);
      let at = 0;
      for (const p of parts) {
        all.set(p, at);
        at += p.byteLength;
      }
      parts.length = 0;
      size = 0;
      await this.append(all);
      if (first) {
        first = false;
        this.seekStart();
      }
    };
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (this.closed) throw ABORT();
        if (done) break;
        parts.push(value);
        size += value.byteLength;
        if (size >= CHUNK_BYTES) await flush();
      }
      await flush();
    } finally {
      reader.releaseLock();
    }
  }

  /** Na het eerste stuk: naar de start (een plek in het eerste nummer). */
  private seekStart(): void {
    if (this.startAt > 0) {
      try {
        this.audio.currentTime = this.startAt;
      } catch {
        // nog geen metadata: dan begint hij vooraan
      }
    }
  }

  /** Eén stuk in de buffer; bij een volle buffer eerst wat achter de positie ligt eruit, of
   * wachten tot er gespeeld is. */
  private async append(data: Uint8Array): Promise<void> {
    for (;;) {
      if (this.closed) throw ABORT();
      await this.idle();
      this.trimPlayed();
      await this.idle();
      try {
        this.buffer!.appendBuffer(data as BufferSource);
        await this.idle();
        return;
      } catch (e) {
        if (!isQuota(e)) throw e;
        const behind = this.audio.currentTime - BEHIND_S;
        const b = this.buffer!.buffered;
        if (b.length && behind > b.start(0) + 0.5) {
          await this.remove(0, behind);
          continue;
        }
        await this.deps.sleep(QUOTA_WAIT_MS);
      }
    }
  }

  /** Wat vóór het huidige nummer ligt, eruit (alleen het huidige en het volgende blijven). */
  private trimPlayed(): void {
    const sb = this.buffer;
    if (!sb || sb.updating) return;
    const now = this.entries.find((e) => e.key === this.current);
    if (!now || now.start <= this.trimmed + 0.001) return;
    if (this.audio.currentTime < now.start + 2) return;
    this.trimmed = now.start;
    try {
      sb.remove(0, now.start);
    } catch {
      // de bron is al dicht
    }
  }

  /** Haalt [from, to) uit de buffer (to Infinity: tot het eind). Alleen wat er staat: remove
   * voorbij de duur van de bron mag niet. */
  private async remove(from: number, to: number): Promise<void> {
    await this.idle();
    const sb = this.buffer;
    if (!sb) return;
    const end = Math.min(to, this.bufferEnd());
    if (!(end > from)) return;
    sb.remove(from, end);
    await this.idle();
  }

  /** Wacht tot de buffer klaar is met bijwerken. */
  private idle(): Promise<void> {
    const sb = this.buffer;
    if (!sb || !sb.updating) return this.closed ? Promise.reject(ABORT()) : Promise.resolve();
    return new Promise<void>((resolve, reject) => {
      const done = () => {
        sb.removeEventListener("updateend", done);
        sb.removeEventListener("error", fail);
        this.waiters.delete(stop);
        if (this.closed) reject(ABORT());
        else resolve();
      };
      const fail = () => {
        sb.removeEventListener("updateend", done);
        sb.removeEventListener("error", fail);
        this.waiters.delete(stop);
        reject(this.closed ? ABORT() : new Error("SourceBuffer: fout bij toevoegen"));
      };
      const stop = () => {
        sb.removeEventListener("updateend", done);
        sb.removeEventListener("error", fail);
        reject(ABORT());
      };
      this.waiters.add(stop);
      sb.addEventListener("updateend", done);
      sb.addEventListener("error", fail);
    });
  }

  /** Wacht tot er hooguit PREFETCH_S tot T over is (bij spelen; gepauzeerd wacht hij). */
  private async untilNear(T: number): Promise<void> {
    while (!this.closed && T - this.audio.currentTime > PREFETCH_S) {
      await new Promise<void>((resolve) => {
        const done = () => {
          this.audio.removeEventListener("timeupdate", done);
          this.audio.removeEventListener("seeked", done);
          this.waiters.delete(done);
          resolve();
        };
        this.waiters.add(done);
        this.audio.addEventListener("timeupdate", done);
        this.audio.addEventListener("seeked", done);
      });
    }
  }

  // --- het huidige nummer ---

  /** Het huidige nummer bijwerken naar de positie van nu (de speler, voor hij de positie leest). */
  sync(): void {
    this.onTime();
  }

  private readonly onTime = (): void => {
    const t = this.audio.currentTime;
    let found: Entry | null = null;
    for (const e of this.entries) {
      if (t + 0.0005 >= e.start) found = e;
      else break;
    }
    if (found && found.key !== this.current) {
      this.current = found.key;
      this.deps.onTrack(found.key);
    }
  };
}
