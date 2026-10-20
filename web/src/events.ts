// Live gebeurtenissen van de server (Server-Sent Events), gelezen met fetch(): zo gaat het
// token mee in de Authorization-header en komt het nooit in een adres. Valt de verbinding weg,
// dan verbinden we opnieuw, steeds iets later. Een verlopen token ververst de API-client zelf;
// lukt dat niet, dan is de sessie voorbij en stopt de app dit (App.tsx).
//
// Na elke (nieuwe) verbinding komt er een "connected": wat er intussen gebeurde is gemist,
// dus wie luistert laadt dan opnieuw.

import { useEffect, useRef } from "react";
import { ApiError, api } from "./api";

export interface ServerEvent {
  type: string;
  data: Record<string, unknown>;
}

/** Leest de tekst van een SSE-stroom in stukken zoals ze binnenkomen. */
export class SseParser {
  private buffer = "";
  private type = "";
  private data: string[] = [];
  /** Hoe lang de server wil dat we wachten met opnieuw verbinden (ms). */
  retry: number | null = null;

  push(chunk: string): ServerEvent[] {
    this.buffer += chunk;
    const done: ServerEvent[] = [];
    for (;;) {
      const match = /\r\n|\n|\r/.exec(this.buffer);
      // Een losse \r aan het eind kan de helft van \r\n zijn: wacht op het volgende stuk
      if (!match || (match[0] === "\r" && match.index === this.buffer.length - 1)) break;
      const line = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      const event = this.line(line);
      if (event) done.push(event);
    }
    return done;
  }

  private line(line: string): ServerEvent | null {
    if (line === "") {
      const type = this.type || "message";
      const raw = this.data.join("\n");
      const had = this.data.length > 0;
      this.type = "";
      this.data = [];
      if (!had) return null;
      try {
        return { type, data: JSON.parse(raw) as Record<string, unknown> };
      } catch {
        return null; // geen JSON: niet van ons
      }
    }
    if (line.startsWith(":")) return null; // commentaar, zoals ": ping"
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") this.type = value;
    else if (field === "data") this.data.push(value);
    else if (field === "retry" && /^\d+$/.test(value)) this.retry = Number(value);
    return null;
  }
}

/** Het deel van Api dat de stroom nodig heeft (los te testen). */
export interface StreamApi {
  eventStream(signal: AbortSignal): Promise<Response>;
}

export interface LiveEventsOptions {
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
  /** Zo lang (ms) mag er niets binnenkomen; de server pingt elke 20 s. */
  stallTimeout?: number;
  random?: () => number;
}

const MAX_WAIT = 30_000;

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const done = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal.addEventListener("abort", done);
  });
}

export class LiveEvents {
  private controller: AbortController | null = null;
  private readonly listeners = new Set<(event: ServerEvent) => void>();
  private readonly sleep: (ms: number, signal: AbortSignal) => Promise<void>;
  private readonly stallTimeout: number;
  private readonly random: () => number;

  constructor(
    private readonly api: StreamApi,
    options: LiveEventsOptions = {},
  ) {
    this.sleep = options.sleep ?? pause;
    this.stallTimeout = options.stallTimeout ?? 45_000;
    this.random = options.random ?? Math.random;
  }

  get running(): boolean {
    return this.controller !== null;
  }

  /** Begint (als dat nog niet zo was) en blijft verbonden tot stop(). */
  start(): void {
    if (this.controller) return;
    const controller = new AbortController();
    this.controller = controller;
    void this.run(controller.signal);
  }

  stop(): void {
    this.controller?.abort();
    this.controller = null;
  }

  subscribe(listener: (event: ServerEvent) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private emit(event: ServerEvent): void {
    for (const listener of [...this.listeners]) {
      try {
        listener(event);
      } catch {
        /* één kapotte luisteraar houdt de rest niet tegen */
      }
    }
  }

  private async run(signal: AbortSignal): Promise<void> {
    let failures = 0;
    while (!signal.aborted) {
      let wait: number;
      try {
        const retry = await this.connect(signal);
        failures = 0;
        // De server sloot netjes (token verlopen, of hij stopt): na zijn retry opnieuw
        wait = retry ?? 1000;
      } catch (e) {
        if (signal.aborted) return;
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          // Niet (meer) ingelogd, of eerst 2FA: opnieuw proberen heeft geen zin
          if (this.controller?.signal === signal) this.controller = null;
          return;
        }
        failures++;
        const base = Math.min(MAX_WAIT, 1000 * 2 ** (failures - 1));
        wait = base / 2 + (base / 2) * this.random(); // niet alle tabbladen tegelijk
      }
      await this.sleep(wait, signal);
    }
  }

  /** Eén verbinding, tot de server sluit; geeft de retry van de server. */
  private async connect(signal: AbortSignal): Promise<number | null> {
    // Een eigen signaal per verbinding: valt de stroom stil zonder te sluiten, dan breken we
    // hem zelf af en verbinden we opnieuw
    const connection = new AbortController();
    const abort = () => connection.abort();
    signal.addEventListener("abort", abort);
    let stalled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const watch = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        stalled = true;
        connection.abort();
      }, this.stallTimeout);
    };
    try {
      watch();
      const res = await this.api.eventStream(connection.signal);
      if (!res.body) throw new ApiError(0, "Geen stroom");
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      const parser = new SseParser();
      this.emit({ type: "connected", data: {} });
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        watch();
        for (const event of parser.push(decoder.decode(value, { stream: true }))) this.emit(event);
      }
      return parser.retry;
    } catch (e) {
      if (stalled) throw new ApiError(0, "De verbinding viel stil");
      throw e;
    } finally {
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      connection.abort(); // ook de lopende fetch netjes afsluiten
    }
  }
}

export const liveEvents = new LiveEvents(api);

/** Roept listener aan bij elke gebeurtenis zolang het component bestaat. */
export function useServerEvents(listener: (event: ServerEvent) => void): void {
  const latest = useRef(listener);
  useEffect(() => {
    latest.current = listener;
  });
  useEffect(() => liveEvents.subscribe((event) => latest.current(event)), []);
}

/** Een deling met jou kwam erbij, veranderde (andere rechten) of is weg. */
export function isShareEvent(event: ServerEvent): boolean {
  return event.type.startsWith("share.");
}

/**
 * Of deze gebeurtenis de inhoud van map folderId verandert. shared: de map is met je gedeeld;
 * dan ook bij elke wijziging van een deling, want daarmee kunnen je rechten veranderen of kun
 * je er niet meer bij.
 */
export function affectsFolder(event: ServerEvent, folderId: string | null | undefined, shared = false): boolean {
  if (!folderId) return false;
  if (event.type === "connected") return true;
  if (event.type === "tags.changed") return true; // de tags in de lijst zijn hernoemd of weg
  if (shared && isShareEvent(event)) return true;
  if (!event.type.startsWith("node.")) return false;
  const { id, parent_id, old_parent_id } = event.data;
  return [id, parent_id, old_parent_id].includes(folderId);
}

/** Of de lijst "Gedeeld met mij" (met deze mappen en bestanden erin) opnieuw moet. */
export function affectsSharedWithMe(event: ServerEvent, nodeIds: ReadonlySet<string>): boolean {
  if (event.type === "connected" || event.type === "tags.changed" || isShareEvent(event)) return true;
  // Hernoemd, verplaatst of in de prullenbak door de eigenaar
  return event.type.startsWith("node.") && nodeIds.has(String(event.data.id));
}

/** Roept reload aan als match een gebeurtenis herkent; veel vlak na elkaar geven één keer. */
export function useReloadOn(match: (event: ServerEvent) => boolean, reload: () => void, delay = 250): void {
  const latest = useRef({ match, reload });
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    latest.current = { match, reload };
  });
  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current);
    },
    [],
  );
  useServerEvents((event) => {
    if (!latest.current.match(event) || timer.current !== null) return;
    timer.current = setTimeout(() => {
      timer.current = null;
      latest.current.reload();
    }, delay);
  });
}

/** Laadt de map opnieuw als er iets in verandert; veel wijzigingen vlak na elkaar (een map
 * vol uploads) geven één keer opnieuw laden. */
export function useFolderEvents(
  folderId: string | null | undefined,
  reload: () => void,
  delay = 250,
  shared = false,
): void {
  useReloadOn((event) => affectsFolder(event, folderId, shared), reload, delay);
}
