// De tekstlaag van de PDF-viewer (fase 6, Z4; Keuze 18 en 47): welke woorden van een pagina bij
// de zoekvraag horen (?q= in de URL van de viewer), de woorden per pagina ophalen, en de eerste
// pagina met een treffer zoeken. Zonder React, zodat vitest het kan toetsen; TextLayer.tsx tekent.
//
// De woorden komen van GET /files/{id}/pages/{n}/words?token= (Z2), met het download-token van
// de viewer (dat zit in page_url). Die route geeft per gebruiker hooguit 20 per seconde: alle
// verzoeken gaan hier na elkaar, met minstens WORDS_GAP_MS ertussen. Een pagina zonder tekst
// (404) wordt onthouden; een andere fout niet, zodat een volgende keer (bijv. met een nieuw token)
// het opnieuw probeert.
//
// Welke woorden gemarkeerd zijn, volgt parse() van de API (app/search/query.py) en de snippets
// (app/search/snippets.py): elk woord van de vraag gevouwen (kleine letters, zonder accenten, ß
// als ss), als heel woord; het laatste als begin van een woord als de vraag op een letter of
// cijfer eindigt en dat woord minstens drie tekens heeft.

import { ApiError } from "../api";
import type { PageWord } from "../search/types";

/** Zoveel pagina's leest de viewer hooguit om de eerste treffer te vinden. */
export const SCAN_PAGES = 50;
/** Na zoveel pagina's achter elkaar zonder tekst (een scan zonder OCR) stopt het zoeken. */
export const SCAN_EMPTY = 5;
/** Minstens zoveel ms tussen het begin van twee verzoeken: de route geeft hooguit 20 per seconde. */
export const WORDS_GAP_MS = 60;
/** Na 429 zo lang wachten (ms), en hooguit zo vaak opnieuw. */
const RATE_WAIT_MS = 1000;
const RATE_TRIES = 2;

// Zoals parse(): hooguit 200 tekens en 10 woorden; een voorvoegsel vanaf 3 tekens
const MAX_CHARS = 200;
const MAX_WORDS = 10;
const PREFIX_MIN = 3;
const RAW_WORD = /[\p{L}\p{N}\p{M}]+/gu;
const NOT_ALNUM = /[^\p{L}\p{N}]+/u;
const ALNUM = /^[\p{L}\p{N}]$/u;

/** Zoals fold() van de API: zonder accenten en hoofdletters (é → e, ß → ss). */
export function fold(text: string): string {
  return text.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase().replace(/ß/g, "ss");
}

/** De woorden van een vraag, gevouwen, en of het laatste een voorvoegsel is. */
export interface Terms {
  words: string[];
  prefix: boolean;
}

/** De woorden om te markeren, of null als er niets te zoeken is. */
export function queryTerms(q: string | null | undefined): Terms | null {
  if (!q) return null;
  const text = q.normalize("NFKC").slice(0, MAX_CHARS);
  const words: string[] = [];
  for (const raw of text.match(RAW_WORD) ?? []) {
    for (const piece of fold(raw).split(NOT_ALNUM)) if (piece) words.push(piece);
  }
  if (words.length === 0) return null;
  words.length = Math.min(words.length, MAX_WORDS);
  // Zoals q.rstrip(_ZWSP) in parse(): een zero-width space aan het eind telt niet
  const last = [...text.replace(/\u200b+$/u, "")].pop() ?? "";
  return { words, prefix: ALNUM.test(last) && words[words.length - 1].length >= PREFIX_MIN };
}

/** Hoort een woord van de pagina bij de vraag? Een woord van de pagina is alles tot witruimte
 * ("factuur," of "e-mail"): het telt als een van zijn stukken past. */
export function matcher(terms: Terms): (text: string) => boolean {
  const whole = new Set(terms.words);
  const start = terms.prefix ? terms.words[terms.words.length - 1] : null;
  return (text) => {
    for (const piece of fold(text).split(NOT_ALNUM)) {
      if (piece && (whole.has(piece) || (start !== null && piece.startsWith(start)))) return true;
    }
    return false;
  };
}

/** De vraag voor de tekstlaag uit de querystring van de pagina: ?q=, behalve in de stand beeld
 * (mode=image, Keuze 81): dan beschrijft q een foto, geen woorden in een document. */
export function markQuery(search: URLSearchParams): string | null {
  if (search.get("mode") === "image") return null;
  return search.get("q");
}

/** Het download-token uit page_url ("/api/v1/files/…/pages/{n}.webp?w={w}&token=…"). */
export function tokenOf(pageUrl: string): string | null {
  const at = pageUrl.indexOf("?");
  if (at < 0) return null;
  return new URLSearchParams(pageUrl.slice(at + 1)).get("token") || null;
}

/** Hoe een woord in de laag staat: de plek als deel van de pagina, en of er een regel eindigt. */
export interface Placed {
  word: PageWord;
  marked: boolean;
  /** Het volgende woord begint op een nieuwe regel (of dit is het laatste) */
  lineEnd: boolean;
}

/** De woorden met hun markering, en waar een regel eindigt (voor kopiëren: een regeleinde, anders
 * een spatie). Een nieuwe regel: het midden van het volgende woord ligt niet in de hoogte van dit
 * woord, of het begint links ervan. */
export function place(words: readonly PageWord[], match: ((text: string) => boolean) | null): Placed[] {
  return words.map((word, i) => {
    const next = words[i + 1];
    const mid = next ? next.y + next.h / 2 : 0;
    const lineEnd = !next || mid < word.y || mid > word.y + word.h || next.x < word.x;
    return { word, marked: match !== null && match(word.t), lineEnd };
  });
}

/** Waar de eerste treffer staat: pagina n (vanaf 1) en y als deel van de pagina. */
export interface FirstHit {
  page: number;
  y: number;
}

/** Hoe het zoeken naar de eerste treffer afliep. */
export type ScanResult =
  | ({ kind: "found" } & FirstHit)
  /** Alle pagina's gelezen, zonder treffer */
  | { kind: "none" }
  /** SCAN_PAGES gelezen, er zijn er meer */
  | { kind: "limit"; pages: number }
  /** SCAN_EMPTY pagina's achter elkaar zonder tekst */
  | { kind: "no_text" };

/** Leest de pagina's na elkaar tot de eerste met een woord van de vraag. alive(): nog nodig (de
 * viewer is niet dicht en de vraag is niet veranderd); anders null. */
export async function firstHit(
  count: number,
  want: (n: number) => Promise<readonly PageWord[] | null>,
  match: (text: string) => boolean,
  alive: () => boolean,
  limit = SCAN_PAGES,
): Promise<ScanResult | null> {
  let empty = 0;
  const last = Math.min(count, limit);
  for (let n = 1; n <= last; n++) {
    if (!alive()) return null;
    const words = await want(n);
    if (!alive()) return null;
    if (!words || words.length === 0) {
      if (++empty >= SCAN_EMPTY) return { kind: "no_text" };
      continue;
    }
    empty = 0;
    const hit = words.find((w) => match(w.t));
    if (hit) return { kind: "found", page: n, y: hit.y };
  }
  return count > last ? { kind: "limit", pages: last } : { kind: "none" };
}

type Loader = (n: number) => Promise<PageWord[]>;

interface StoreOptions {
  gap?: number;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
}

const pause = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** De woorden per pagina van één bestand (of versie). Elk verzoek na het vorige, met WORDS_GAP_MS
 * ertussen. get(n): de woorden, null (geen tekst) of undefined (nog niet gevraagd of bezig). */
export class PageWordsStore {
  private readonly words = new Map<number, PageWord[] | null>();
  private readonly busy = new Map<number, Promise<PageWord[] | null>>();
  private readonly listeners = new Set<() => void>();
  private load: Loader | null = null;
  private chain: Promise<unknown> = Promise.resolve();
  private last = Number.NEGATIVE_INFINITY;
  private version = 0;
  private readonly gap: number;
  private readonly now: () => number;
  private readonly sleep: (ms: number) => Promise<void>;

  constructor(options: StoreOptions = {}) {
    this.gap = options.gap ?? WORDS_GAP_MS;
    this.now = options.now ?? (() => performance.now());
    this.sleep = options.sleep ?? pause;
  }

  /** Hoe de woorden te halen (met het token van nu); null: niet (geen token). */
  setLoader(load: Loader | null): void {
    this.load = load;
  }

  get(n: number): PageWord[] | null | undefined {
    return this.words.get(n);
  }

  /** De woorden van pagina n: uit wat er al is, of na de verzoeken die al wachten. */
  want(n: number): Promise<PageWord[] | null> {
    if (this.words.has(n)) return Promise.resolve(this.words.get(n) ?? null);
    const pending = this.busy.get(n);
    if (pending) return pending;
    const run = this.chain.then(() => this.fetch(n, 0));
    this.chain = run.catch(() => {});
    const done = run.finally(() => this.busy.delete(n));
    this.busy.set(n, done);
    return done;
  }

  /** Pagina's zonder tekst opnieuw vragen: search.ready zegt dat er tekst bij kwam (OCR, X1). */
  forgetEmpty(): void {
    for (const [n, w] of this.words) if (!w || w.length === 0) this.words.delete(n);
    this.changed();
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  snapshot = (): number => this.version;

  private changed(): void {
    this.version++;
    for (const listener of this.listeners) listener();
  }

  private async fetch(n: number, tries: number): Promise<PageWord[] | null> {
    const load = this.load;
    if (!load) return null;
    const wait = this.last + this.gap - this.now();
    if (wait > 0) await this.sleep(wait);
    this.last = this.now();
    try {
      const words = await load(n);
      this.words.set(n, words);
      this.changed();
      return words;
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        this.words.set(n, null); // geen tekst op deze pagina
        this.changed();
        return null;
      }
      if (e instanceof ApiError && e.status === 429 && tries < RATE_TRIES) {
        await this.sleep(RATE_WAIT_MS);
        return this.fetch(n, tries + 1);
      }
      return null; // niet onthouden: een volgende keer opnieuw
    }
  }
}
