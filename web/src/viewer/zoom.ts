// De rekensom achter inzoomen en verschuiven (ZoomView), zonder DOM: los te testen.
//
// De afbeelding staat met zijn eigen grootte (natural, in pixels van het origineel) in het
// midden van het venster (viewport), geschaald met scale en verschoven met x, y (pixels op het
// scherm). rotation is 0, 90, 180 of 270 graden.

export interface Size {
  w: number;
  h: number;
}

export interface ZoomState {
  scale: number;
  x: number;
  y: number;
  rotation: number;
}

export const MAX_SCALE = 8;
/** Stap van + en − en van één tik van het wieltje (×) */
export const STEP = 1.25;
/** Pijltjes verschuiven 10 % van het venster */
export const PAN_FRACTION = 0.1;

/** De grootte op het scherm bij scale 1, gedraaid. */
export function rotated(natural: Size, rotation: number): Size {
  return rotation % 180 === 0 ? natural : { w: natural.h, h: natural.w };
}

/** Passend in het venster, nooit groter dan 100 % (een kleine afbeelding blijft scherp). */
export function fitScale(natural: Size, viewport: Size, rotation = 0): number {
  const r = rotated(natural, rotation);
  if (r.w <= 0 || r.h <= 0 || viewport.w <= 0 || viewport.h <= 0) return 1;
  return Math.min(viewport.w / r.w, viewport.h / r.h, 1);
}

export function fitted(natural: Size, viewport: Size, rotation = 0): ZoomState {
  return { scale: fitScale(natural, viewport, rotation), x: 0, y: 0, rotation };
}

/** Ingezoomd: groter dan passend (dan pannen de pijltjes en navigeren ze niet). */
export function isZoomed(state: ZoomState, natural: Size, viewport: Size): boolean {
  return state.scale > fitScale(natural, viewport, state.rotation) * 1.001;
}

/** Zo ver mag de afbeelding verschuiven: tot de rand, en niet als hij in het venster past. */
export function clampPan(state: ZoomState, natural: Size, viewport: Size): ZoomState {
  const r = rotated(natural, state.rotation);
  const maxX = Math.max(0, (r.w * state.scale - viewport.w) / 2);
  const maxY = Math.max(0, (r.h * state.scale - viewport.h) / 2);
  const clamp = (v: number, m: number) => Math.min(m, Math.max(-m, v));
  return { ...state, x: clamp(state.x, maxX), y: clamp(state.y, maxY) };
}

/**
 * Zoomen met factor rond een punt (ten opzichte van het midden van het venster): wat onder de
 * muis of tussen de vingers staat, blijft daar staan. Tussen passend en MAX_SCALE.
 */
export function zoomAt(
  state: ZoomState,
  factor: number,
  point: { x: number; y: number },
  natural: Size,
  viewport: Size,
): ZoomState {
  const fit = fitScale(natural, viewport, state.rotation);
  const max = Math.max(MAX_SCALE, fit);
  const scale = Math.min(max, Math.max(fit, state.scale * factor));
  const k = scale / state.scale;
  const next = { ...state, scale, x: point.x - (point.x - state.x) * k, y: point.y - (point.y - state.y) * k };
  return clampPan(next, natural, viewport);
}

/** Naar een vaste schaal (1 = 100 %), rond het midden. */
export function zoomTo(state: ZoomState, scale: number, natural: Size, viewport: Size): ZoomState {
  return zoomAt(state, scale / state.scale, { x: 0, y: 0 }, natural, viewport);
}

export function panBy(state: ZoomState, dx: number, dy: number, natural: Size, viewport: Size): ZoomState {
  return clampPan({ ...state, x: state.x + dx, y: state.y + dy }, natural, viewport);
}

export type Arrow = "left" | "right" | "up" | "down";

/**
 * Een pijltje als de afbeelding is ingezoomd: 10 % van het venster de andere kant op schuiven
 * (→ laat zien wat rechts staat). Niet ingezoomd: null (dan navigeert het pijltje).
 */
export function arrowPan(state: ZoomState, arrow: Arrow, natural: Size, viewport: Size): ZoomState | null {
  if (!isZoomed(state, natural, viewport)) return null;
  const dx = viewport.w * PAN_FRACTION;
  const dy = viewport.h * PAN_FRACTION;
  const move = { left: [dx, 0], right: [-dx, 0], up: [0, dy], down: [0, -dy] }[arrow];
  return panBy(state, move[0], move[1], natural, viewport);
}

/** Een kwartslag verder; daarna weer passend. */
export function rotate(state: ZoomState, natural: Size, viewport: Size): ZoomState {
  return fitted(natural, viewport, (state.rotation + 90) % 360);
}

/** Zoveel pixels van het origineel zijn nu nodig in de breedte (voor het wisselen naar groter). */
export function neededWidth(state: ZoomState, natural: Size, dpr = 1): number {
  return Math.round(natural.w * state.scale * dpr);
}

/** Afstand tussen twee vingers (knijpen). */
export function distance(a: { x: number; y: number }, b: { x: number; y: number }): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}
