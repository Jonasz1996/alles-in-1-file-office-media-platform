// Het kader om een persoon op een foto (Keuze 37): x, y, w en h als deel van de foto (0–1), zoals
// de API het bewaart. Tekenen kan met de muis (slepen) en met het toetsenbord: de pijlen
// verplaatsen, Shift met een pijl maakt groter of kleiner (→ en ↓ groter, ← en ↑ kleiner). Los
// van React, zodat het te testen is.

import type { Box } from "./types";

/** Zoveel per toets (2 % van de foto). */
export const STEP = 0.02;
/** Kleiner dan dit wordt een kader niet (ook niet met slepen). */
export const MIN_SIZE = 0.04;
/** Waar een nieuw kader begint: in het midden, wat hoger dan het midden (daar staan gezichten vaak). */
export const START_BOX: Box = [0.35, 0.25, 0.3, 0.4];

const round = (v: number): number => Math.round(v * 10_000) / 10_000;
const within = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v));

/** Het kader binnen de foto: niet kleiner dan MIN_SIZE, niet groter dan de foto, niet erbuiten. */
export function clampBox([x, y, w, h]: Box): Box {
  const cw = within(w, MIN_SIZE, 1);
  const ch = within(h, MIN_SIZE, 1);
  return [round(within(x, 0, 1 - cw)), round(within(y, 0, 1 - ch)), round(cw), round(ch)];
}

/** Verplaatsen (de maat blijft). */
export function moveBox([x, y, w, h]: Box, dx: number, dy: number): Box {
  return clampBox([x + dx, y + dy, w, h]);
}

/** Groter of kleiner vanuit de linkerbovenhoek; past het niet meer, dan schuift het kader terug. */
export function resizeBox([x, y, w, h]: Box, dw: number, dh: number): Box {
  return clampBox([x, y, w + dw, h + dh]);
}

/** Een kader tussen twee punten (0–1) die de muis aanwees, in welke richting er ook gesleept is. */
export function boxFromPoints(ax: number, ay: number, bx: number, by: number): Box {
  const x0 = within(Math.min(ax, bx), 0, 1);
  const y0 = within(Math.min(ay, by), 0, 1);
  const x1 = within(Math.max(ax, bx), 0, 1);
  const y1 = within(Math.max(ay, by), 0, 1);
  return clampBox([x0, y0, x1 - x0, y1 - y0]);
}

/** Wat een toets met het kader doet, of null als het geen toets van het kader is. */
export function boxKey(b: Box, key: string, shift: boolean): Box | null {
  const d = (
    {
      ArrowLeft: [-STEP, 0],
      ArrowRight: [STEP, 0],
      ArrowUp: [0, -STEP],
      ArrowDown: [0, STEP],
    } as Record<string, [number, number]>
  )[key];
  if (!d) return null;
  return shift ? resizeBox(b, d[0], d[1]) : moveBox(b, d[0], d[1]);
}

/** Het kader in hele procenten, voor wat een schermlezer hoort. */
export function boxPercent([x, y, w, h]: Box): { x: number; y: number; w: number; h: number } {
  const pct = (v: number) => Math.round(v * 100);
  return { x: pct(x), y: pct(y), w: pct(w), h: pct(h) };
}
