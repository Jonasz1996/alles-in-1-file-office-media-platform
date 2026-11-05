// De muziekspeler (fase 5, M9; Keuze 37 en 38): één <audio> voor de hele app, de rij
// (queue.ts) en de weg per nummer. De pagina's en de muziekbalk delen deze module.
//
// - Het element hangt aan <body>, buiten React: navigeren (en de dubbele mount van StrictMode)
//   haalt het niet weg, dus de muziek speelt door. Eén element: een nieuw nummer zet zijn src.
// - Per nummer: GET …/codecs, het profiel (player/caps.ts, per set codecs onthouden) en play.
//   Mode audio (audio.mp4) en MP3 (direct, als MSE audio/mpeg kent) gaan via MSE in een
//   GaplessRun (gapless.ts): naadloos. De rest (geen MSE, of een codec die MSE niet kent):
//   <audio src> per nummer, met het volgende alvast gevraagd; dan is er een kort gat en zegt de
//   balk dat (state.gapless false).
// - Een ander nummer kiezen of ver zoeken: een nieuwe run. Het volgende nummer dat al in de
//   buffer staat (volgende, of vanzelf): alleen de positie.
// - De sid staat alleen in het geheugen en in de src van het element; een sessie gaat dicht
//   zodra het nummer geladen is (MSE) of als het volgende begint (<audio src>).
// - Media Session voor de mediatoetsen en het vergrendelscherm.
// - musicBar (media/index.ts) uit (uitloggen, of sluiten): stoppen en de rij wissen.

import { errorText } from "../i18n";
import { musicBar, streamUrl } from "../media";
import { getCodecs, play } from "../media/api";
import type { ClientProfile, PlayOut } from "../media/types";
import { buildProfile } from "../player/caps";
import { mediaSourceClass, mseSupported } from "../player/mse";
import { closeQuietly, isAbort, playBody, responseError } from "../player/session";
import { GaplessRun, PREFETCH_S, type RunTrack, trackSeconds } from "./gapless";
import {
  appendItems,
  moveItem,
  type QueueInput,
  type QueueItem,
  queue,
  removeAt,
  replaceQueue,
} from "./queue";

export type PlayerStatus = "idle" | "loading" | "playing" | "paused" | "error";

export interface PlayerState {
  status: PlayerStatus;
  /** Naadloos via MSE (true), of per nummer met <audio src> (false) */
  gapless: boolean;
  /** Seconden in het huidige nummer */
  position: number;
  /** De lengte van het huidige nummer (s), of null */
  duration: number | null;
  volume: number;
  muted: boolean;
  /** De fout als tekst (status error) */
  error: string | null;
}

/** Terug naar het begin van het nummer in plaats van het vorige, na zoveel seconden. */
export const RESTART_S = 3;
/** Zoveel seconden per stap (pijltjes op de tijdlijn, en de mediatoetsen). */
export const STEP_S = 10;
const VOLUME_KEY = "music.volume";

/** Hoe een nummer via MSE gaat, of null (dan <audio src>). */
export function runKind(
  out: Pick<PlayOut, "sid" | "mode" | "urls" | "mime">,
  supported: (mime: string) => boolean,
): { url: string; mime: string; sequence: boolean } | null {
  if (out.mode === "audio" && out.urls.audio) {
    return supported(out.mime) ? { url: streamUrl(out.sid, out.urls.audio), mime: out.mime, sequence: false } : null;
  }
  const kind = out.mime.split(";")[0].trim().toLowerCase();
  if (out.mode === "direct" && out.urls.file && kind === "audio/mpeg" && supported("audio/mpeg")) {
    return { url: streamUrl(out.sid, out.urls.file), mime: "audio/mpeg", sequence: true };
  }
  return null;
}

/** Het adres voor <audio src>. */
export function fileUrl(out: Pick<PlayOut, "sid" | "urls">): string | null {
  const rest = out.urls.file ?? out.urls.audio;
  return rest ? streamUrl(out.sid, rest) : null;
}

function readVolume(): number {
  try {
    const raw = window.localStorage.getItem(VOLUME_KEY);
    const v = raw === null ? Number.NaN : Number(raw);
    return Number.isFinite(v) && v >= 0 && v <= 1 ? v : 1;
  } catch {
    return 1;
  }
}

function saveVolume(v: number): void {
  try {
    window.localStorage.setItem(VOLUME_KEY, String(Math.round(v * 100) / 100));
  } catch {
    // geen opslag: dan de volgende keer weer vol
  }
}

function mediaSession(): MediaSession | null {
  return typeof navigator !== "undefined" && "mediaSession" in navigator ? navigator.mediaSession : null;
}

/** Een sessie die al gevraagd is voor het volgende nummer (<audio src>, of na een run). */
interface Prefetched {
  uid: string;
  out: PlayOut;
}

export class MusicPlayer {
  private el: HTMLAudioElement | null = null;
  private run: GaplessRun | null = null;
  /** De sessie van <audio src> (geen run) */
  private fileSid: string | null = null;
  private prefetched: Prefetched | null = null;
  private prefetching: string | null = null;
  private readonly profiles = new Map<string, Promise<ClientProfile>>();
  /** Elke start verhoogt dit: een antwoord van een oudere start telt niet meer */
  private gen = 0;
  /** Eén keer opnieuw bij een fout van <audio src> (een verlopen sessie) */
  private retried: string | null = null;
  private state: PlayerState = {
    status: "idle",
    gapless: true,
    position: 0,
    duration: null,
    volume: 1,
    muted: false,
    error: null,
  };
  private readonly listeners = new Set<() => void>();

  constructor() {
    // De balk weg terwijl er iets is (uitloggen: resetMedia): stoppen en de rij wissen. stop()
    // zelf zet eerst alles op leeg, dus dit roept zichzelf niet opnieuw aan.
    musicBar.subscribe(() => {
      if (!musicBar.visible() && (this.state.status !== "idle" || queue.snapshot().items.length)) this.stop();
    });
  }

  snapshot = (): PlayerState => this.state;

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  /** Het element (gemaakt bij het eerste gebruik, aan <body>). */
  get audio(): HTMLAudioElement {
    if (this.el) return this.el;
    const a = document.createElement("audio");
    a.className = "music-audio";
    a.preload = "auto";
    a.hidden = true;
    a.volume = readVolume();
    this.state = { ...this.state, volume: a.volume };
    a.addEventListener("timeupdate", this.onTime);
    a.addEventListener("play", this.onPlayState);
    a.addEventListener("playing", this.onPlayState);
    a.addEventListener("pause", this.onPlayState);
    a.addEventListener("ended", this.onEnded);
    a.addEventListener("error", this.onElementError);
    a.addEventListener("volumechange", this.onVolume);
    a.addEventListener("durationchange", this.onTime);
    document.body.append(a);
    this.el = a;
    this.bindMediaSession();
    return a;
  }

  /** Voor wie de rij is (de ingelogde gebruiker). Een rij uit sessionStorage komt terug als
   * gepauzeerd: de balk staat er, spelen is een klik. */
  bind(owner: string | null): void {
    queue.bind(owner);
    const q = queue.snapshot();
    if (q.items.length && this.state.status === "idle" && !this.run && !this.fileSid) {
      const item = q.items[q.index];
      this.update({ status: "paused", position: q.position, duration: trackSeconds(null, item?.duration_ms) });
      musicBar.show();
    }
  }

  // --- de rij ---

  /** Een nieuwe rij, en spelen vanaf start. */
  playItems(items: readonly QueueInput[], start = 0): void {
    if (!items.length) return;
    queue.set(replaceQueue(items, start));
    void this.playAt(Math.max(0, Math.min(items.length - 1, start)));
  }

  /** Achteraan in de rij; geeft hoeveel er bij kwamen. Speelt er niets, dan staat de balk klaar. */
  enqueue(items: readonly QueueInput[]): number {
    const before = queue.snapshot();
    const { state, added } = appendItems(before, items);
    if (!added) return 0;
    queue.set(state);
    if (!before.items.length) {
      this.update({ status: "paused", position: 0, duration: trackSeconds(null, state.items[0].duration_ms), error: null });
    }
    this.queueChanged();
    musicBar.show();
    return added;
  }

  /** Een nummer uit de rij. Het nummer dat speelt: dan het volgende. */
  removeFromQueue(i: number): void {
    const before = queue.snapshot();
    const current = before.items[before.index]?.uid;
    const next = removeAt(before, i);
    if (!next.items.length) {
      this.stop();
      return;
    }
    queue.set(next);
    if (before.items[i]?.uid === current && this.isLoaded()) {
      const wasPlaying = this.state.status === "playing" || this.state.status === "loading";
      void this.playAt(next.index, 0, wasPlaying);
      return;
    }
    this.queueChanged();
  }

  moveInQueue(from: number, to: number): void {
    queue.set(moveItem(queue.snapshot(), from, to));
    this.queueChanged();
  }

  // --- spelen ---

  /** Het nummer op plek index, vanaf startS (een nieuwe run of een nieuw <audio src>). */
  async playAt(index: number, startS = 0, autoplay = true): Promise<void> {
    const q = queue.snapshot();
    const item = q.items[index];
    if (!item) return;
    const g = ++this.gen;
    this.teardown();
    // Het vorige nummer stil, nu al: de sessie van het nieuwe kan even duren (review golf 4,
    // C5). Zijn tijd en zijn eind tellen niet meer (onTime en onEnded zonder bron).
    this.el?.pause();
    queue.set({ ...q, index, position: startS });
    this.retried = null;
    this.update({
      status: "loading",
      error: null,
      position: startS,
      duration: trackSeconds(null, item.duration_ms),
    });
    musicBar.show();
    this.setMetadata(item);
    try {
      const out = await this.session(item);
      if (g !== this.gen) {
        closeQuietly(out.sid);
        return;
      }
      this.begin(item, out, startS, autoplay);
    } catch (e) {
      if (g === this.gen) this.fail(e);
    }
  }

  /** Spelen of pauzeren. Zonder bron (na herladen, of een fout): het huidige nummer laden. */
  toggle(): void {
    const q = queue.snapshot();
    if (!q.items.length) return;
    if (!this.isLoaded() || this.state.status === "error") {
      void this.playAt(q.index, this.state.status === "error" ? this.state.position : q.position);
      return;
    }
    const a = this.audio;
    if (a.paused) void a.play().catch(() => this.onPlayState());
    else a.pause();
  }

  /** Spelen (de mediatoets "play"): zoals toggle, maar nooit pauzeren. */
  resume(): void {
    if (this.isLoaded() && this.state.status !== "error" && !this.audio.paused) return;
    this.toggle();
  }

  pause(): void {
    this.el?.pause();
  }

  next(): void {
    const q = queue.snapshot();
    const i = q.index + 1;
    if (i >= q.items.length) return;
    const entry = this.run?.entry(q.items[i].uid);
    if (this.run && entry && this.run.buffered(entry.start + 0.01)) {
      this.audio.currentTime = entry.start;
      return;
    }
    void this.playAt(i, 0, !this.audio.paused || this.state.status === "loading");
  }

  previous(): void {
    const q = queue.snapshot();
    if (this.state.position > RESTART_S || q.index <= 0) {
      this.seek(0);
      return;
    }
    void this.playAt(q.index - 1, 0, !this.audio.paused || this.state.status === "loading");
  }

  /** Naar s seconden in het huidige nummer. In de buffer: alleen de positie; anders een nieuwe run. */
  seek(s: number): void {
    const q = queue.snapshot();
    const item = q.items[q.index];
    if (!item) return;
    const len = this.state.duration ?? Number.POSITIVE_INFINITY;
    const target = Math.max(0, Math.min(s, Number.isFinite(len) ? Math.max(0, len - 0.05) : s));
    if (!this.isLoaded()) {
      queue.set({ ...q, position: target });
      this.update({ position: target });
      return;
    }
    if (this.run) {
      const entry = this.run.entry(item.uid);
      if (entry && this.run.buffered(entry.start + target)) {
        this.audio.currentTime = entry.start + target;
        return;
      }
      void this.playAt(q.index, target, !this.audio.paused);
      return;
    }
    this.audio.currentTime = target;
  }

  setVolume(v: number): void {
    const a = this.audio;
    a.volume = Math.max(0, Math.min(1, v));
    if (a.volume > 0 && a.muted) a.muted = false;
    saveVolume(a.volume);
  }

  toggleMute(): void {
    const a = this.audio;
    a.muted = !a.muted;
  }

  /** Stoppen en de rij wissen (sluiten, uitloggen). */
  stop(): void {
    this.gen++;
    this.teardown();
    const a = this.el;
    if (a) {
      a.pause();
      a.removeAttribute("src");
      try {
        a.load();
      } catch {
        // geen element
      }
    }
    queue.clear();
    const ms = mediaSession();
    if (ms) {
      ms.metadata = null;
      ms.playbackState = "none";
    }
    this.update({ status: "idle", position: 0, duration: null, error: null, gapless: true });
    musicBar.hide();
  }

  // --- intern ---

  private isLoaded(): boolean {
    return this.run !== null || this.fileSid !== null;
  }

  private update(patch: Partial<PlayerState>): void {
    const next = { ...this.state, ...patch };
    const same = (Object.keys(patch) as (keyof PlayerState)[]).every((k) => this.state[k] === next[k]);
    if (same) return;
    this.state = next;
    for (const l of [...this.listeners]) l();
  }

  /** Het profiel voor deze codecs (per set codecs één keer gemeten) en play. */
  private async session(item: QueueItem): Promise<PlayOut> {
    const codecs = await getCodecs(item.node_id);
    const key = [...codecs.item].sort().join(",");
    let profile = this.profiles.get(key);
    if (!profile) {
      profile = buildProfile(codecs);
      this.profiles.set(key, profile);
      profile.catch(() => this.profiles.delete(key));
    }
    return play(item.node_id, playBody(await profile, {}, 0));
  }

  private begin(item: QueueItem, out: PlayOut, startS: number, autoplay: boolean): void {
    const a = this.audio;
    const kind = runKind(out, mseSupported);
    const ms = mediaSourceClass();
    if (kind && ms) {
      const track = this.track(item.uid, out, kind);
      const g = this.gen;
      this.run = new GaplessRun(a, track, startS, {
        ms,
        fetch: (...args) => fetch(...args),
        createObjectURL: (o) => URL.createObjectURL(o),
        revokeObjectURL: (u) => URL.revokeObjectURL(u),
        prepare: (uid) => this.prepareRun(uid, g),
        after: (uid) => this.after(uid),
        onTrack: (uid) => this.onRunTrack(uid),
        onError: (e) => {
          if (g === this.gen) this.fail(e);
        },
        responseError,
        sleep: (ms2) => new Promise((ok) => setTimeout(ok, ms2)),
      });
      this.update({ gapless: true, duration: trackSeconds(out.gapless, out.duration_ms) });
      this.run.start();
    } else {
      const url = fileUrl(out);
      if (!url) {
        closeQuietly(out.sid);
        this.fail(new Error("geen adres"));
        return;
      }
      this.fileSid = out.sid;
      this.update({ gapless: false, duration: trackSeconds(null, out.duration_ms) });
      a.src = url;
      if (startS > 0) {
        const at = startS;
        a.addEventListener(
          "loadedmetadata",
          () => {
            a.currentTime = at;
          },
          { once: true },
        );
      }
    }
    if (autoplay) {
      void a.play().catch((e: unknown) => {
        if (!isAbort(e)) this.onPlayState();
      });
    } else {
      this.update({ status: "paused" });
    }
  }

  private track(uid: string, out: PlayOut, kind: { url: string; mime: string; sequence: boolean }): RunTrack {
    let open = true;
    return {
      key: uid,
      url: kind.url,
      mime: kind.mime,
      sequence: kind.sequence,
      gapless: out.gapless ?? null,
      release: () => {
        if (!open) return;
        open = false;
        closeQuietly(out.sid);
      },
    };
  }

  /** Het volgende nummer voor de run: via MSE een RunTrack; anders null (en de sessie wacht
   * voor <audio src> na "ended"). */
  private async prepareRun(uid: string, g: number): Promise<RunTrack | null> {
    const q = queue.snapshot();
    const item = q.items.find((x) => x.uid === uid);
    if (!item || g !== this.gen) return null;
    const out = await this.session(item);
    if (g !== this.gen) {
      closeQuietly(out.sid);
      return null;
    }
    const kind = runKind(out, mseSupported);
    if (!kind) {
      this.setPrefetched({ uid, out });
      return null;
    }
    return this.track(uid, out, kind);
  }

  private after(uid: string): string | null {
    const q = queue.snapshot();
    const i = q.items.findIndex((x) => x.uid === uid);
    return i >= 0 && i + 1 < q.items.length ? q.items[i + 1].uid : null;
  }

  private onRunTrack(uid: string): void {
    const q = queue.snapshot();
    const i = q.items.findIndex((x) => x.uid === uid);
    if (i < 0) return;
    const entry = this.run?.entry(uid);
    const len = entry && Number.isFinite(entry.end) ? entry.end - entry.start : trackSeconds(null, q.items[i].duration_ms);
    if (i !== q.index) queue.set({ ...q, index: i, position: 0 });
    this.update({ duration: len });
    this.setMetadata(q.items[i]);
  }

  /** De rij veranderde. Staat er al een volgend nummer in de buffer of klaar dat niet meer
   * het volgende is, dan opnieuw vanaf de positie van nu (een nieuwe run). */
  private queueChanged(): void {
    const q = queue.snapshot();
    const current = q.items[q.index];
    if (!current) return;
    const nextUid = q.items[q.index + 1]?.uid ?? null;
    if (this.prefetched && this.prefetched.uid !== nextUid) this.setPrefetched(null);
    const run = this.run;
    if (!run) return;
    const at = run.entries.findIndex((e) => e.key === current.uid);
    if (at < 0) return; // nog niets geladen: de run vraagt het volgende pas later
    const loadedNext = run.entries[at + 1]?.key ?? null;
    if (loadedNext !== null && loadedNext !== nextUid) {
      void this.playAt(q.index, this.state.position, !this.audio.paused);
    }
  }

  private setPrefetched(p: Prefetched | null): void {
    if (this.prefetched && this.prefetched.out.sid !== p?.out.sid) closeQuietly(this.prefetched.out.sid);
    this.prefetched = p;
  }

  /** Alles van het vorige nummer dicht: de run, de sessie van <audio src> en wat klaarstond. */
  private teardown(): void {
    this.run?.close(false);
    this.run = null;
    closeQuietly(this.fileSid);
    this.fileSid = null;
    this.setPrefetched(null);
    this.prefetching = null;
  }

  private fail(e: unknown): void {
    if (isAbort(e)) return;
    const text = errorText(e);
    this.el?.pause();
    this.update({ status: "error", error: text });
  }

  // --- de gebeurtenissen van het element ---

  private readonly onTime = (): void => {
    const a = this.el;
    // Zonder bron (een nieuw nummer laadt, of de rij is klaar): de tijd van het element is nog
    // die van het vorige nummer (C5)
    if (!a || !this.isLoaded()) return;
    const q = queue.snapshot();
    const item = q.items[q.index];
    if (!item) return;
    let position = a.currentTime;
    let duration = this.state.duration;
    if (this.run) {
      this.run.sync(); // eerst het huidige nummer van de run (kan net gewisseld zijn)
      const now = queue.snapshot();
      const entry = this.run.entry(now.items[now.index]?.uid ?? item.uid);
      if (!entry) return;
      position = Math.max(0, a.currentTime - entry.start);
    } else if (Number.isFinite(a.duration) && a.duration > 0) {
      duration = a.duration;
    }
    queue.setPosition(position);
    this.update({ position, duration });
    this.positionState(position, duration);
    if (!this.run && this.fileSid && duration !== null && duration - position <= PREFETCH_S) void this.prefetch();
  };

  /** <audio src>: het volgende nummer alvast vragen (de sessie, niet de bytes). */
  private async prefetch(): Promise<void> {
    const q = queue.snapshot();
    const next = q.items[q.index + 1];
    if (!next || this.prefetched?.uid === next.uid || this.prefetching === next.uid) return;
    const g = this.gen;
    this.prefetching = next.uid;
    try {
      const out = await this.session(next);
      if (g !== this.gen || queue.snapshot().items[queue.snapshot().index + 1]?.uid !== next.uid) {
        closeQuietly(out.sid);
        return;
      }
      this.setPrefetched({ uid: next.uid, out });
    } catch {
      // dan vraagt "ended" de sessie zelf
    } finally {
      if (this.prefetching === next.uid) this.prefetching = null;
    }
  }

  private readonly onPlayState = (): void => {
    const a = this.el;
    if (!a || this.state.status === "error") return;
    const status: PlayerStatus = a.paused ? (this.isLoaded() ? "paused" : this.state.status) : "playing";
    this.update({ status });
    const ms = mediaSession();
    if (ms) ms.playbackState = a.paused ? "paused" : "playing";
  };

  private readonly onEnded = (): void => {
    if (!this.isLoaded()) return; // het eind van het vorige nummer, terwijl het nieuwe laadt (C5)
    const q = queue.snapshot();
    const i = q.index + 1;
    if (i >= q.items.length) {
      // Het eind van de rij: de balk blijft, klaar om opnieuw te beginnen
      this.teardown();
      queue.set({ ...q, position: 0 });
      this.update({ status: "paused", position: 0 });
      return;
    }
    const ready = this.prefetched;
    if (ready && ready.uid === q.items[i].uid) {
      this.prefetched = null;
      const g = ++this.gen;
      this.teardown();
      queue.set({ ...q, index: i, position: 0 });
      this.update({ status: "loading", position: 0, duration: trackSeconds(null, q.items[i].duration_ms) });
      this.setMetadata(q.items[i]);
      if (g === this.gen) this.begin(q.items[i], ready.out, 0, true);
      return;
    }
    void this.playAt(i, 0, true);
  };

  private readonly onElementError = (): void => {
    // Bij een run: het element kan de bytes niet decoderen (de run zelf meldt laadfouten)
    if (this.run) {
      this.fail(this.el?.error ?? new Error("audio"));
      return;
    }
    // <audio src>: een verlopen sessie of een netwerkfout; één keer opnieuw op deze plek
    if (!this.fileSid) return;
    const q = queue.snapshot();
    const item = q.items[q.index];
    if (!item) return;
    if (this.retried !== item.uid) {
      const uid = item.uid;
      const at = this.state.position;
      void this.playAt(q.index, at, true).then(() => {
        this.retried = uid;
      });
      return;
    }
    this.fail(this.el?.error ?? new Error("audio"));
  };

  private readonly onVolume = (): void => {
    const a = this.el;
    if (a) this.update({ volume: a.volume, muted: a.muted });
  };

  // --- Media Session ---

  private setMetadata(item: QueueItem): void {
    const ms = mediaSession();
    if (!ms || typeof MediaMetadata === "undefined") return;
    try {
      ms.metadata = new MediaMetadata({
        title: item.title,
        artist: item.artist ?? "",
        album: item.album ?? "",
        artwork: item.cover ? [{ src: item.cover }] : [],
      });
    } catch {
      // een browser zonder artwork-ondersteuning
    }
  }

  private lastPositionState = 0;

  private positionState(position: number, duration: number | null): void {
    const ms = mediaSession();
    if (!ms?.setPositionState || duration === null || !Number.isFinite(duration) || duration <= 0) return;
    const now = Date.now();
    if (now - this.lastPositionState < 1000) return;
    this.lastPositionState = now;
    try {
      ms.setPositionState({ duration, position: Math.min(position, duration), playbackRate: this.el?.playbackRate ?? 1 });
    } catch {
      // ongeldige waarden: overslaan
    }
  }

  private bindMediaSession(): void {
    const ms = mediaSession();
    if (!ms) return;
    const set = (action: MediaSessionAction, handler: MediaSessionActionHandler) => {
      try {
        ms.setActionHandler(action, handler);
      } catch {
        // deze actie kent de browser niet
      }
    };
    set("play", () => this.resume());
    set("pause", () => this.pause());
    set("previoustrack", () => this.previous());
    set("nexttrack", () => this.next());
    set("seekbackward", (d) => this.seek(this.state.position - (d.seekOffset ?? STEP_S)));
    set("seekforward", (d) => this.seek(this.state.position + (d.seekOffset ?? STEP_S)));
    set("seekto", (d) => {
      if (typeof d.seekTime === "number") this.seek(d.seekTime);
    });
    set("stop", () => this.stop());
  }
}

export const player = new MusicPlayer();
