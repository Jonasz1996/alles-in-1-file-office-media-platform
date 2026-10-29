// De besturing van één speler (Keuze 32): het profiel meten, play, de engine kiezen, wisselen
// (ander spoor, andere kwaliteit: een nieuwe sessie met replace op de positie van nu), en bij een
// fout de herstel-lus van session.ts. Zonder React; ui/Player.tsx hangt de bediening eraan.
//
// Eén stap is "play en vastmaken": play (met replace van de sessie die er nu is) en de engine aan
// de nieuwe sessie hangen. Mislukt die stap (map.json 503, geen verbinding), dan doet de
// herstel-lus dezelfde stap opnieuw; de replace sluit de sessie van de vorige poging, zodat er
// nooit twee open blijven. Een fout tijdens het spelen (een segment, het bestand) probeert eerst
// hetzelfde adres opnieuw, en bij 410 de stap op de positie van nu.

import { getCodecs, play } from "../media/api";
import type { ClientProfile, PlayOut, Quality } from "../media/types";
import { buildProfile, type Size } from "./caps";
import { type AnyEngine, createEngine, engineKind } from "./engine";
import { Stalls } from "./mse";
import {
  type Choice,
  classify,
  closeQuietly,
  health,
  isAbort,
  type Notice,
  type Outcome,
  playBody,
  type RecoverDeps,
  Recovery,
  sleep,
  type StopReason,
  StreamError,
} from "./session";

export type SessionWhy = "start" | "renew" | "change";

export interface ControllerOptions {
  /** De maat van het item voor decodingInfo (anders 1080p) */
  size?: Size;
  /** De viewer: bij een fout aan het begin geen herstel-lus, maar meteen failed (de fallback) */
  quick?: boolean;
}

export interface ControllerEvents {
  /** Een nieuwe sessie speelt (de eerste, na 410, of na een wissel) */
  session(out: PlayOut, why: SessionWhy): void;
  notice(n: Notice): void;
  /** 403 media_access_gone: gestopt, met de reden */
  stopped(reason: StopReason): void;
  /** Het lukt niet. unreachable: de server bleef weg; loop: steeds weer 410; atStart: er speelde
   * nog niets (de viewer valt dan terug op de <video> van fase 3) */
  failed(e: unknown, o: { unreachable: boolean; loop: boolean; atStart: boolean }): void;
  /** Na twee haperingen een trede lager */
  lowered?(q: Quality): void;
}

/** Wat de controller van buiten nodig heeft (voor tests in te spuiten). */
export interface ControllerDeps {
  codecs(nodeId: string): Promise<Parameters<typeof buildProfile>[0]>;
  profile(codecs: Parameters<typeof buildProfile>[0], size?: Size): Promise<ClientProfile>;
  play(nodeId: string, body: ReturnType<typeof playBody>): Promise<PlayOut>;
  engine(video: HTMLVideoElement, out: PlayOut, ev: Parameters<typeof createEngine>[2]): AnyEngine;
  recovery: Omit<RecoverDeps, "attempt" | "renew" | "ahead" | "notice" | "cancelled">;
}

const DEFAULT_DEPS: ControllerDeps = {
  codecs: getCodecs,
  profile: (c, size) => buildProfile(c, undefined, size),
  play,
  engine: (video, out, ev) => createEngine(video, out, ev),
  recovery: { health: () => health(), sleep, now: Date.now },
};

const ABORT = (): DOMException => new DOMException("afgebroken", "AbortError");

/** De kwaliteit één trede onder wat er nu speelt, of null. */
export function lowerQuality(options: readonly Quality[], height: number, current: Quality): Quality | null {
  const rungs = options.filter((q): q is 1080 | 720 | 480 => typeof q === "number").sort((a, b) => b - a);
  const now = typeof current === "number" ? current : height || Infinity;
  return rungs.find((q) => q < now) ?? null;
}

export class Controller {
  out: PlayOut | null = null;
  choice: Choice = {};
  private profile: ClientProfile | null = null;
  private engine: AnyEngine | null = null;
  private readonly recovery = new Recovery();
  private readonly stalls = new Stalls();
  /** Telt elke nieuwe opdracht (open, wissel, sluiten): een oudere ronde stopt dan */
  private seq = 0;
  /** De ronde waarin een fout tijdens het spelen hersteld wordt, of -1 */
  private healing = -1;
  private switching = false;
  private closed = false;
  /** De pagina ging weg (pagehide) en de sessie is dicht; pageshow uit de bfcache opent weer */
  private hidden = false;
  private started = false;
  private last = 0;
  private readonly onTime = () => {
    if (this.video.readyState >= 1 && !this.video.seeking) this.last = this.video.currentTime;
  };

  constructor(
    private readonly video: HTMLVideoElement,
    private readonly nodeId: string,
    private readonly ev: ControllerEvents,
    private readonly opts: ControllerOptions = {},
    private readonly deps: ControllerDeps = DEFAULT_DEPS,
  ) {
    video.addEventListener("timeupdate", this.onTime);
  }

  /** De positie van nu (ook als de video net leeg is na een fout of een wissel). */
  position(): number {
    const v = this.video;
    if (v.readyState >= 1 && v.currentTime > 0) return v.currentTime;
    return this.last;
  }

  ahead(): number {
    return this.engine?.ahead() ?? 0;
  }

  get busy(): boolean {
    return this.switching;
  }

  private cancelled(my: number): boolean {
    return this.closed || my !== this.seq;
  }

  private deps4(my: number): Omit<RecoverDeps, "attempt" | "renew"> {
    return {
      ...this.deps.recovery,
      ahead: () => this.ahead(),
      notice: (n) => this.ev.notice(n),
      cancelled: () => this.cancelled(my),
    };
  }

  private async measured(): Promise<ClientProfile> {
    this.profile ??= await this.deps.profile(await this.deps.codecs(this.nodeId), this.opts.size);
    return this.profile;
  }

  /** De stap: play (replace: de sessie van nu) en de engine aan de nieuwe sessie hangen. */
  private async step(at: number, flush: boolean, why: SessionWhy, my: number): Promise<void> {
    if (this.cancelled(my)) throw ABORT();
    const profile = await this.measured();
    const out = await this.deps.play(this.nodeId, playBody(profile, this.choice, at, this.out?.sid));
    if (this.cancelled(my)) {
      closeQuietly(out.sid);
      throw ABORT();
    }
    const kind = engineKind(out);
    if (kind === null) {
      closeQuietly(out.sid);
      throw new StreamError(415, "Deze browser kan deze video niet afspelen", "media_decode");
    }
    // Vanaf hier is out de sessie van nu: een volgende poging sluit hem met replace
    this.out = out;
    if (this.engine && this.engine.kind === kind) {
      await this.engine.switchTo(out, flush);
    } else {
      this.engine?.destroy();
      const engine = this.deps.engine(this.video, out, {
        error: (e) => void this.onError(e, engine),
        stall: () => this.onStall(),
      });
      this.engine = engine;
      await engine.start(at);
    }
    if (this.cancelled(my)) throw ABORT();
    this.stalls.reset();
    this.started = true;
    this.ev.session(out, why);
  }

  /** De stap, en bij een fout de herstel-lus met dezelfde stap. */
  private async guarded(at: () => number, flush: boolean, why: SessionWhy, my: number): Promise<void> {
    const run = () => this.step(at(), flush, why, my);
    let outcome: Outcome;
    try {
      await run();
      return;
    } catch (first) {
      if (isAbort(first) || this.cancelled(my)) return;
      // In de viewer: lukt de eerste play niet (geen verbinding, de worker weg, 410), dan meteen
      // de <video> van fase 3. Alleen wachten (bezet, de probe loopt nog) en 403 gaan door de lus
      const kind = classify(first).kind;
      if (this.opts.quick && !this.started && kind !== "wait" && kind !== "stop") {
        this.finish({ ok: false, error: first });
        return;
      }
      outcome = await this.recovery.run(first, { ...this.deps4(my), attempt: run, renew: run });
    }
    this.finish(outcome);
  }

  /** De eerste sessie, vanaf startS. */
  async open(startS: number, choice: Choice = {}): Promise<void> {
    const my = ++this.seq;
    this.choice = { ...choice };
    this.last = startS;
    await this.guarded(() => startS, false, "start", my);
  }

  /** Een andere keuze: een nieuwe sessie met replace op de positie van nu. */
  async change(next: Partial<Choice>): Promise<void> {
    const my = ++this.seq;
    const at = this.position();
    this.choice = { ...this.choice, ...next };
    this.switching = true;
    // De oude sessie gaat zo dicht: zijn verzoeken (410) zijn geen fout om te herstellen
    this.engine?.hold();
    try {
      await this.guarded(() => at, true, "change", my);
    } finally {
      if (my === this.seq) this.switching = false;
    }
  }

  /** Een fout tijdens het spelen, van de engine die nu speelt. */
  private async onError(e: unknown, from: AnyEngine): Promise<void> {
    if (this.closed || from !== this.engine) return;
    const my = this.seq;
    if (this.healing === my) return;
    this.healing = my;
    try {
      const outcome = await this.recovery.run(e, {
        ...this.deps4(my),
        attempt: async () => {
          if (from !== this.engine) throw ABORT();
          await from.retry();
        },
        renew: () => this.step(this.position(), false, "renew", my),
      });
      this.finish(outcome);
    } finally {
      if (this.healing === my) this.healing = -1;
    }
  }

  private finish(outcome: Outcome): void {
    if (outcome.ok || "cancelled" in outcome || this.closed) return;
    if ("stop" in outcome) {
      this.video.pause();
      this.engine?.destroy();
      this.engine = null;
      this.out = null;
      this.ev.stopped(outcome.stop);
      return;
    }
    this.ev.failed(outcome.error, {
      unreachable: "unreachable" in outcome,
      loop: "loop" in outcome && outcome.loop === true,
      atStart: !this.started,
    });
  }

  private onStall(): void {
    const out = this.out;
    if (!out || out.mode !== "transcode" || this.switching || this.healing === this.seq) return;
    if (!this.stalls.add(Date.now())) return;
    const q = lowerQuality(out.quality.options, this.video.videoHeight, this.choice.quality ?? "auto");
    if (q === null) return;
    void this.change({ quality: q }).then(() => {
      if (this.out && this.choice.quality === q) this.ev.lowered?.(q);
    });
  }

  /** Opnieuw na een fout die de kijker zag ("Opnieuw proberen"), eventueel met een andere keuze
   * (HDR toch afspelen). */
  async again(next: Partial<Choice> = {}): Promise<void> {
    const at = this.position();
    this.engine?.destroy();
    this.engine = null;
    closeQuietly(this.out?.sid);
    this.out = null;
    await this.open(at, { ...this.choice, ...next });
  }

  /** De pagina gaat weg (pagehide: een ander adres, het tabblad dicht, de bfcache): de sessie
   * meteen dicht. Anders houdt een gesloten tabblad zijn plaats bij de toelating vast tot de
   * server de stille sessie opruimt, en krijgt wie terugkomt "te druk". */
  hide(): void {
    if (this.closed || this.hidden || !this.out) return;
    this.hidden = true;
    this.seq++;
    this.engine?.hold();
    closeQuietly(this.out.sid);
  }

  /** Terug uit de bfcache (pageshow met persisted): een nieuwe sessie op dezelfde plek. */
  async show(): Promise<void> {
    if (this.closed || !this.hidden) return;
    this.hidden = false;
    this.out = null;
    await this.again();
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.seq++;
    this.video.removeEventListener("timeupdate", this.onTime);
    this.engine?.destroy();
    this.engine = null;
    closeQuietly(this.out?.sid);
    this.out = null;
  }
}
