// De timing van een ondertitel verschuiven (fase 11, stap 4). De <track> leest de VTT zelf;
// daarna verschuiven we de cues van zijn TextTrack. De oorspronkelijke tijden houden we per cue
// bij, zodat elke nieuwe verschuiving vanaf het origineel rekent (nooit opstapelt). De
// verschuiving per video en ondertitel bewaart de server (routers/media_suboffset.py).

/** Hooguit zoveel ms verschuiven (zoals de server). */
export const LIMIT_MS = 10 * 60 * 1000;
/** Een toets (G of H) verschuift zoveel ms; een keuze in het menu SUB_MENU_STEP_MS. */
export const SUB_STEP_MS = 100;
export const SUB_MENU_STEP_MS = 500;

interface Cue {
  startTime: number;
  endTime: number;
}

const originals = new WeakMap<object, { start: number; end: number }>();

/** Binnen de grens en op hele ms. */
export function clampOffset(ms: number): number {
  return Math.round(Math.max(-LIMIT_MS, Math.min(LIMIT_MS, ms)));
}

/** De cues van een track op origineel + offset (ms; > 0 is later tonen). Geeft het aantal. */
export function shiftCues(cues: ArrayLike<Cue> | null | undefined, offsetMs: number): number {
  if (!cues) return 0;
  const by = offsetMs / 1000;
  for (let i = 0; i < cues.length; i++) {
    const cue = cues[i];
    let orig = originals.get(cue);
    if (!orig) {
      orig = { start: cue.startTime, end: cue.endTime };
      originals.set(cue, orig);
    }
    const start = Math.max(0, orig.start + by);
    const end = Math.max(start, orig.end + by);
    if (cue.startTime !== start || cue.endTime !== end) {
      // eerst de kant die de andere niet voorbijloopt
      if (start > cue.endTime) {
        cue.endTime = end;
        cue.startTime = start;
      } else {
        cue.startTime = start;
        cue.endTime = end;
      }
    }
  }
  return cues.length;
}

/** "+1,5 s" of "−0,3 s" voor de melding. */
export function offsetText(ms: number, loc: string): string {
  const s = new Intl.NumberFormat(loc, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
    signDisplay: "exceptZero",
  }).format(ms / 1000);
  return `${s.replace("-", "−")} s`;
}

export interface SavedOffset {
  sub_id: string;
  label: string;
  offset_ms: number;
}

/** De bewaarde verschuiving voor deze ondertitel, alleen als het label nog klopt. */
export function savedOffset(saved: readonly SavedOffset[], id: string, label: string): number {
  const found = saved.find((s) => s.sub_id === id);
  return found && found.label === label ? found.offset_ms : 0;
}
