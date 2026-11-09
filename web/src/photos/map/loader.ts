// Laden terwijl je schuift en zoomt, zonder een verzoek per beeldje (Keuze 31 en 32): pas als het
// venster even stilstaat (debounce), en een nieuw verzoek breekt het vorige af (AbortController).
// Hetzelfde verzoek (dezelfde sleutel) nog eens vragen doet niets. Puur (alleen timers), zodat het
// in vitest te testen is.

/** Zo lang moet het venster stilstaan voor de kaart nieuwe cellen vraagt. */
export const SETTLE_MS = 150;

export interface LatestOnlyOptions<T> {
  /** Wachten na de laatste vraag, in ms */
  delay: number;
  run(key: string, signal: AbortSignal): Promise<T>;
  /** Het antwoord op de laatste vraag (nooit dat op een vraag die al vervangen is) */
  done(key: string, value: T): void;
  /** Een fout bij de laatste vraag (niet het afbreken zelf) */
  failed(key: string, error: unknown): void;
}

export function isAbort(e: unknown): boolean {
  return e instanceof DOMException ? e.name === "AbortError" : (e as { name?: string } | null)?.name === "AbortError";
}

/** Alleen de laatste vraag telt. */
export class LatestOnly<T> {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private controller: AbortController | null = null;
  /** De sleutel die wacht, loopt of klaar is */
  private current: string | null = null;

  constructor(private readonly o: LatestOnlyOptions<T>) {}

  /** Vraag key, na de wachttijd. Zelfde sleutel als de laatste: niets. delay 0: meteen. */
  request(key: string, delay = this.o.delay): void {
    if (key === this.current) return;
    this.stop();
    this.current = key;
    if (delay <= 0) this.start(key);
    else this.timer = setTimeout(() => this.start(key), delay);
  }

  /** Alles afbreken; de volgende vraag gaat altijd door, ook met dezelfde sleutel. */
  cancel(): void {
    this.stop();
    this.current = null;
  }

  /** Vergeet het laatste antwoord: dezelfde sleutel opnieuw vragen gaat dan wel door. */
  forget(): void {
    if (!this.timer && !this.controller) this.current = null;
  }

  private stop(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.controller?.abort();
    this.controller = null;
  }

  private start(key: string): void {
    this.timer = null;
    const controller = new AbortController();
    this.controller = controller;
    this.o.run(key, controller.signal).then(
      (value) => {
        if (controller.signal.aborted) return;
        this.controller = null;
        this.o.done(key, value);
      },
      (e: unknown) => {
        if (controller.signal.aborted || isAbort(e)) return;
        this.controller = null;
        // Een fout: dezelfde vraag later nog eens mag weer
        if (this.current === key) this.current = null;
        this.o.failed(key, e);
      },
    );
  }
}
