// Meetpunten voor de prestatietests (e2e/perf): "de pagina is bruikbaar". Eén keer per soort;
// met ?perf in het adres ook in de console.

const done = new Set<string>();

/** Zet performance.mark("app:ready:<what>"), één keer per what. */
export function markReady(what: string): void {
  if (done.has(what) || typeof performance === "undefined") return;
  done.add(what);
  const name = `app:ready:${what}`;
  try {
    performance.mark(name);
  } catch {
    return; // geen User Timing
  }
  if (typeof location !== "undefined" && new URLSearchParams(location.search).has("perf")) {
    console.info(`${name} ${Math.round(performance.now())} ms`);
  }
}
