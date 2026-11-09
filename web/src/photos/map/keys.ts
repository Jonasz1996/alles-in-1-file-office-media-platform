// De toetsen van de kaart (Keuze 31): de pijlen schuiven, + en - zoomen, Home toont al je foto's.
// Alleen zonder Ctrl, Cmd en Alt: Ctrl met + of - is de zoom van de browser, en die blijft. Puur,
// zodat het in vitest te testen is; MapView past de actie toe.

import type { Size } from "./view";

export type MapAction =
  /** De wereld dx, dy pixels meeschuiven (pijl links: je kijkt verder naar het westen) */
  | { kind: "pan"; dx: number; dy: number }
  /** Zoveel zoomstappen erbij of eraf, rond het midden */
  | { kind: "zoom"; by: number }
  /** Al je foto's in beeld */
  | { kind: "home" };

type KeyLike = Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey" | "shiftKey">;

/** Een pijl schuift een kwart van het venster, met Shift een half venster. */
export function panStep(size: Size, shift: boolean): { x: number; y: number } {
  const f = shift ? 0.5 : 0.25;
  return { x: Math.round(size.w * f), y: Math.round(size.h * f) };
}

/** Wat een toets op de kaart doet, of null (dan is hij niet van de kaart). */
export function mapKey(e: KeyLike, size: Size): MapAction | null {
  if (e.ctrlKey || e.metaKey || e.altKey) return null;
  const step = panStep(size, e.shiftKey);
  switch (e.key) {
    case "ArrowLeft":
      return { kind: "pan", dx: step.x, dy: 0 };
    case "ArrowRight":
      return { kind: "pan", dx: -step.x, dy: 0 };
    case "ArrowUp":
      return { kind: "pan", dx: 0, dy: step.y };
    case "ArrowDown":
      return { kind: "pan", dx: 0, dy: -step.y };
    case "+":
    case "=":
      return { kind: "zoom", by: 1 };
    case "-":
    case "_":
      return { kind: "zoom", by: -1 };
    case "Home":
      return { kind: "home" };
    default:
      return null;
  }
}
