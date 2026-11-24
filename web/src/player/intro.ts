// Intro overslaan (fase 11, stap 7): de server herkent de intro van een aflevering (app/media/
// intro.py) en geeft hem mee als intro_ms [begin, eind]. De knop staat er zolang de tijd in de
// intro ligt, tot een seconde voor het eind.

/** Waarheen de knop springt (s), of null als hij er nu niet hoort. */
export function introSkip(time: number, intro: readonly number[] | null | undefined): number | null {
  if (!intro || intro.length !== 2) return null;
  const [start, end] = [intro[0] / 1000, intro[1] / 1000];
  if (!(end > start) || time < start || time >= end - 1) return null;
  return end;
}
