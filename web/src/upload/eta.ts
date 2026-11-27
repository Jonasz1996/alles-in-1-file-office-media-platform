// Snelheid en resterende tijd van de uploads: een glijdend gemiddelde over de laatste seconden,
// zodat één trage of snelle seconde de schatting niet laat springen. Puur (los te testen).

interface Sample {
  t: number;
  bytes: number;
}

/** Houdt de verzonden bytes (oplopend totaal) bij en geeft de snelheid over het venster. */
export class RateMeter {
  private samples: Sample[] = [];

  constructor(
    /** Over hoeveel ms het gemiddelde loopt. */
    private readonly windowMs = 8000,
    /** Pas een snelheid na zoveel ms aan metingen (anders is één stuk al "1 GB/s"). */
    private readonly minSpanMs = 1000,
  ) {}

  /** Een meting: het totaal aan verzonden bytes op moment now (ms). */
  add(bytes: number, now: number): void {
    const last = this.samples.at(-1);
    // Minder dan eerst (een upload geannuleerd of opnieuw begonnen): opnieuw beginnen
    if (last && bytes < last.bytes) this.samples = [];
    if (last && now === last.t) last.bytes = bytes;
    else this.samples.push({ t: now, bytes });
    // Oud uit het venster, maar houd er één van vóór het venster: dan loopt het venster vol
    const from = now - this.windowMs;
    let drop = 0;
    while (drop + 1 < this.samples.length && this.samples[drop + 1].t <= from) drop++;
    if (drop) this.samples.splice(0, drop);
  }

  /** Bytes per seconde, of null als er nog te weinig gemeten is. now: tot wanneer (stilstand
   * telt mee: na een tijd zonder voortgang zakt de snelheid). */
  rate(now: number): number | null {
    const first = this.samples[0];
    const last = this.samples.at(-1);
    if (!first || !last) return null;
    const span = Math.max(now, last.t) - first.t;
    if (span < this.minSpanMs) return null;
    return ((last.bytes - first.bytes) * 1000) / span;
  }

  reset(): void {
    this.samples = [];
  }
}

/** Resterende tijd in seconden, of null als die niet te schatten is. */
export function etaSeconds(remaining: number, rate: number | null): number | null {
  if (remaining <= 0) return 0;
  if (!rate || rate <= 0 || !Number.isFinite(rate)) return null;
  return remaining / rate;
}

/** Afgerond voor mensen: seconden tot een minuut, daarna minuten, daarna uren (met minuten). */
export function roundEta(seconds: number): { unit: "s" | "min" | "h"; value: number; minutes?: number } {
  if (seconds < 60) return { unit: "s", value: Math.max(1, Math.ceil(seconds / 5) * 5) };
  if (seconds < 3600) return { unit: "min", value: Math.round(seconds / 60) };
  const hours = Math.floor(seconds / 3600);
  return { unit: "h", value: hours, minutes: Math.round((seconds - hours * 3600) / 60) };
}
