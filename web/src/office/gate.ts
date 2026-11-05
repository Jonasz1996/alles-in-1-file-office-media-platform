// Het poort-cookie office vernieuwen zolang de editor open is (Keuze 7 en 32): het geldt 2 uur,
// de pagina vraagt elke 30 minuten een nieuw (POST /office/gate). Een 404 (geen sessie meer, of
// geen recht) is geen reden om de pagina te storen: de editor merkt dat zelf (office.dropped of
// een fout van DS).

import { renewGate } from "./api";

export const GATE_RENEW_MS = 30 * 60_000;

export interface Interval {
  set(fn: () => void, ms: number): unknown;
  clear(handle: unknown): void;
}

const browserInterval: Interval = {
  set: (fn, ms) => setInterval(fn, ms),
  clear: (handle) => clearInterval(handle as ReturnType<typeof setInterval>),
};

/** Vernieuwt het cookie elke every ms; geeft de functie om te stoppen. */
export function keepGate(
  renew: () => Promise<void> = renewGate,
  every: number = GATE_RENEW_MS,
  timer: Interval = browserInterval,
): () => void {
  let stopped = false;
  const handle = timer.set(() => {
    if (stopped) return;
    renew().catch(() => {
      /* zie boven: de editor meldt het zelf */
    });
  }, every);
  return () => {
    stopped = true;
    timer.clear(handle);
  };
}
