// Het naadje voor de voortgang (Keuze 36). player/progress.ts is van M7, niet van M6: deze module
// zoekt hem met import.meta.glob. Bestaat hij nog niet (golf 3), dan is de lijst leeg en meldt
// de speler niets. Zet M7 hem neer, dan laadt de speler hem vanzelf, zonder wijziging hier.
//
// Het voorstel aan M7 (staat ook in docs/fase-5/m6.md): progress.ts exporteert
//
//   export function attach(h: PlaybackHandle): ProgressSink
//
// De speler roept attach één keer per item aan, report() bij elk event, en detach() als hij
// weggaat (na de laatste report met "stop"). De speler meldt:
// - "tick" elke 15 s tijdens het spelen;
// - "pause", "seek" (na het zoeken, één keer per sprong), "end";
// - "stop" als de speler weggaat, en "pause" met keepalive bij visibilitychange naar verborgen
//   (Keuze 36: fetch met keepalive, want een beacon kan geen PUT met Bearer).
// origin, seq, de grens van 5 s op ticks en de PUT zelf zijn van M7. Met h.seek springt
// progressSync naar een nieuwe positie als het item hier gepauzeerd open staat.

import type { ProgressEvent } from "../media/types";

export const TICK_MS = 15_000;

export interface PlaybackEvent {
  event: ProgressEvent;
  position_ms: number;
  duration_ms: number;
  /** PlayOut.started_at van de sessie die nu speelt */
  started_at: string;
  audio: number | null;
  subtitle: string | null;
  /** Bij weggaan of verbergen: de PUT met fetch(…, {keepalive: true}) */
  keepalive: boolean;
}

/** Wat M7 van de speler mag vragen. */
export interface PlaybackHandle {
  readonly nodeId: string;
  /** Seconden */
  position(): number;
  paused(): boolean;
  /** Naar een positie (s) springen; alleen als hij gepauzeerd is (Keuze 36) */
  seek(s: number): void;
}

export interface ProgressSink {
  report(e: PlaybackEvent): void;
  detach(): void;
}

export interface ProgressModule {
  attach(h: PlaybackHandle): ProgressSink;
}

const found = import.meta.glob<ProgressModule>("./progress.ts");

/** progress.ts van M7, of null zolang hij er niet is. */
export async function loadProgress(): Promise<ProgressModule | null> {
  const load = found["./progress.ts"];
  if (!load) return null;
  try {
    const m = await load();
    return typeof m.attach === "function" ? m : null;
  } catch {
    return null;
  }
}

/** Wat de reporter van de video leest (een HTMLVideoElement; in tests een EventTarget). */
export type VideoLike = EventTarget & Pick<HTMLVideoElement, "currentTime" | "duration" | "paused" | "seeking">;

export interface ReporterState {
  /** De positie (s) van de controller: na een wissel of fout is currentTime even 0 */
  position: number;
  started_at: string;
  duration_ms: number;
  audio: number | null;
  subtitle: string | null;
}

/**
 * Zet de gebeurtenissen van de video om in de events van Keuze 36. Zonder sink (geen M7) doet
 * hij niets. timers: in te spuiten voor de tests.
 */
export class Reporter {
  private timer: ReturnType<typeof setInterval> | null = null;
  private ended = false;
  private stopped = false;
  private readonly off: (() => void)[] = [];

  constructor(
    private readonly video: VideoLike,
    private readonly state: () => ReporterState | null,
    private readonly sink: Pick<ProgressSink, "report">,
    doc: EventTarget & { visibilityState?: DocumentVisibilityState } = document,
    private readonly timers = { set: setInterval, clear: clearInterval },
  ) {
    const on = (t: EventTarget, name: string, fn: () => void) => {
      t.addEventListener(name, fn);
      this.off.push(() => t.removeEventListener(name, fn));
    };
    on(video, "playing", () => {
      this.ended = false;
      this.start();
    });
    on(video, "pause", () => {
      this.halt();
      // Het einde geeft ook pause: dan alleen "end"
      if (!this.ended && !video.seeking) this.emit("pause");
    });
    on(video, "seeked", () => this.emit("seek"));
    on(video, "ended", () => {
      this.ended = true;
      this.halt();
      this.emit("end");
    });
    on(doc, "visibilitychange", () => {
      if (doc.visibilityState === "hidden") this.emit("pause", true);
    });
  }

  private start(): void {
    if (this.timer !== null || this.stopped) return;
    this.timer = this.timers.set(() => {
      if (!this.video.paused) this.emit("tick");
    }, TICK_MS);
  }

  private halt(): void {
    if (this.timer !== null) this.timers.clear(this.timer);
    this.timer = null;
  }

  private emit(event: ProgressEvent, keepalive = false): void {
    if (this.stopped) return;
    const s = this.state();
    if (!s) return;
    const t = s.position;
    const dur = s.duration_ms || (Number.isFinite(this.video.duration) ? this.video.duration * 1000 : 0);
    this.sink.report({
      event,
      position_ms: Math.max(0, Math.round((Number.isFinite(t) ? t : 0) * 1000)),
      duration_ms: Math.round(dur),
      started_at: s.started_at,
      audio: s.audio,
      subtitle: s.subtitle,
      keepalive,
    });
  }

  /** De speler gaat weg: nog één "stop" (keepalive), daarna niets meer. */
  stop(): void {
    if (this.stopped) return;
    this.halt();
    if (!this.ended) this.emit("stop", true);
    this.stopped = true;
    for (const off of this.off.splice(0)) off();
  }
}
