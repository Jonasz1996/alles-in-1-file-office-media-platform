// Wanneer de viewer een nieuw token (of een nieuwe sleutel) haalt voor de pagina's van een PDF:
// een minuut voor het verloopt, en bij een kort token halverwege. Nooit vaker dan elke 5 s.

/** Na zoveel milliseconden opnieuw /document vragen, bij een token dat expiresIn s werkt. */
export function renewDelay(expiresIn: number): number {
  const s = Math.max(0, expiresIn);
  return Math.max(5, s > 120 ? s - 60 : s / 2) * 1000;
}

export interface Timers {
  set(fn: () => void, ms: number): unknown;
  clear(handle: unknown): void;
}

const realTimers: Timers = {
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
};

/**
 * Haalt steeds op tijd een nieuw token: renew() geeft de nieuwe looptijd (s). Lukt het niet,
 * dan over een minuut nog eens. Geeft de functie om te stoppen.
 */
export function keepFresh(expiresIn: number, renew: () => Promise<number>, timers: Timers = realTimers): () => void {
  let stopped = false;
  let handle: unknown = null;
  const plan = (seconds: number) => {
    if (stopped) return;
    handle = timers.set(() => {
      renew().then(plan, () => plan(120));
    }, renewDelay(seconds));
  };
  plan(expiresIn);
  return () => {
    stopped = true;
    if (handle !== null) timers.clear(handle);
  };
}
