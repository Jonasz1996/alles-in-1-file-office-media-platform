// De voortgang van de speler naar de server (fase 5, M7, Keuze 36). playback.ts van M6 vindt
// deze module met import.meta.glob en roept attach één keer per item aan; de Reporter daar meldt
// tick (elke 15 s), pause, seek, end, stop, en pause met keepalive bij verbergen.
//
// Hier: origin (dit tabblad), seq (telt per origin op) en started_at gaan mee met elke
// PUT /api/v1/media/progress/{node}. Het token gaat in de Authorization-header (ook met keepalive:
// fetch kan dat, een beacon niet), nooit in een adres. Een PUT die faalt (429 bij een tick te
// vroeg, 404, het netwerk), laat de speler met rust: de volgende melding komt vanzelf.
//
// Andersom: een stand van een ander tabblad of apparaat voor hetzelfde item (progressSync)
// laat een gepauzeerde speler naar die positie springen. Het zoeken dat daaruit volgt, wordt
// niet teruggemeld (anders kaatst de stand heen en weer).

import { putProgress } from "../media/api";
import { onProgress, ORIGIN } from "../media/progressSync";
import type { PlaybackProgressEvent, ProgressIn } from "../media/types";
import type { PlaybackEvent, PlaybackHandle, ProgressSink } from "./playback";

/** Een sprong kleiner dan dit (s) is geen sprong: de speler staat er al. */
export const SAME_S = 1;
/** De vorm van subtitle die de server aanneemt (ProgressIn). */
const SUBTITLE = /^[ex][0-9]{1,3}$/;

let seq = 0;

/** Het volgende volgnummer van dit tabblad (over alle items heen: alleen oplopen telt). */
export function nextSeq(): number {
  seq += 1;
  return seq;
}

/** De body van de PUT voor een melding van de speler. */
export function toBody(e: PlaybackEvent, origin: string, n: number): ProgressIn {
  return {
    position_ms: Math.max(0, Math.round(e.position_ms)),
    duration_ms: Math.max(0, Math.round(e.duration_ms)),
    event: e.event,
    origin,
    seq: n,
    started_at: e.started_at,
    audio: Number.isInteger(e.audio) ? e.audio : null,
    subtitle: e.subtitle !== null && SUBTITLE.test(e.subtitle) ? e.subtitle : null,
  };
}

/** Moet de speler naar deze stand springen? Alleen bij hetzelfde item, gepauzeerd, niet gezien
 * (gezien zet de positie op 0: daar springen we niet heen), en als het echt ergens anders is. */
export function jumpTo(h: Pick<PlaybackHandle, "nodeId" | "paused" | "position">, e: PlaybackProgressEvent): number | null {
  if (e.node_id !== h.nodeId || e.watched || !h.paused()) return null;
  const to = e.position_ms / 1000;
  if (!Number.isFinite(to) || to < 0) return null;
  return Math.abs(h.position() - to) < SAME_S ? null : to;
}

type Send = (nodeId: string, body: ProgressIn, keepalive: boolean) => Promise<unknown>;

const put: Send = (nodeId, body, keepalive) => putProgress(nodeId, body, { keepalive });

/** De sink voor één item. send en listen: in te spuiten voor de tests. */
export function attach(
  h: PlaybackHandle,
  send: Send = put,
  listen: (fn: (e: PlaybackProgressEvent) => void) => () => void = onProgress,
): ProgressSink {
  let detached = false;
  // De positie (s) waar we zelf heen sprongen na een stand van elders: die seek melden we niet
  let echo: number | null = null;
  const off = listen((e) => {
    if (detached) return;
    const to = jumpTo(h, e);
    if (to === null) return;
    echo = to;
    h.seek(to);
  });
  return {
    report(e) {
      if (detached) return;
      if (e.event === "seek" && echo !== null) {
        const mine = Math.abs(e.position_ms / 1000 - echo) < SAME_S;
        echo = null;
        if (mine) return;
      }
      send(h.nodeId, toBody(e, ORIGIN, nextSeq()), e.keepalive).catch(() => {
        /* een volgende melding komt vanzelf */
      });
    },
    detach() {
      detached = true;
      off();
    },
  };
}
