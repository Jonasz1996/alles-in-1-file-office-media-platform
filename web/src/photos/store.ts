// De foto's van de tijdlijn (of een album) per sectie: een maand wordt pas geladen als hij in
// beeld komt, hooguit twee tegelijk, en wat intussen uit beeld scrolde komt achteraan. Geen
// React: de pagina volgt hem met useSyncExternalStore (version).

import type { PhotoItem, PhotoPage } from "./api";

export type FetchPage = (section: number, after: string | null) => Promise<PhotoPage>;

/** Een sectie die geladen wordt. section: waar het antwoord heen gaat (na setSections de nieuwe
 * plek van dezelfde maand), of null als het niet meer telt; gen: de generatie bij het begin. */
type Job = { section: number | null; gen: number; promise: Promise<PhotoItem[]> };

export class SectionStore {
  private data = new Map<number, PhotoItem[]>();
  private loading = new Map<number, Job>();
  private wanted: number[] = [];
  private failed = new Set<number>();
  /** Geladen, maar misschien verouderd (na een wijziging): blijft staan tot het nieuwe er is */
  private stale = new Set<number>();
  /** De oude items van een sectie die na setSections een ander aantal kreeg, tot hij opnieuw
   * geladen is: zo houdt de viewer de foto vast die open is (zie held) */
  private previous = new Map<number, PhotoItem[]>();
  private readonly listeners = new Set<() => void>();
  private ids = new Map<string, number>();
  /** Na setSections: oude antwoorden tellen niet meer */
  private generation = 0;
  private starts: readonly number[] = [0];
  version = 0;
  /** Een sectie had na het laden een ander aantal dan gedacht (iets kwam erbij of ging weg) */
  onMismatch: (section: number, got: number) => void = () => {};
  /** Laden mislukte (bijv. geen verbinding) */
  onError: (e: unknown) => void = () => {};

  constructor(
    private fetchPage: FetchPage,
    private counts: readonly number[] = [],
    private readonly concurrency = 2,
  ) {
    this.index();
  }

  getVersion = (): number => this.version;

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  private changed(): void {
    this.version++;
    for (const l of this.listeners) l();
  }

  private index(): void {
    const starts = [0];
    for (const c of this.counts) starts.push(starts[starts.length - 1] + c);
    this.starts = starts;
  }

  /** Nieuwe secties (de maanden opnieuw gelezen). keep: secties waarvan de inhoud nog klopt.
   * Een maand die nog laadt en hetzelfde aantal houdt, laadt door: wat dat geeft, telt als
   * verouderd (het begon vóór het opnieuw lezen). Zonder dat laadde een maand nooit zolang de
   * maanden sneller opnieuw gelezen werden dan hij duurde (veel preview.ready achter elkaar). */
  setSections(counts: readonly number[], fetchPage: FetchPage = this.fetchPage, keep: (s: number) => number = () => -1) {
    const old = this.data;
    const oldPrevious = this.previous;
    const oldLoading = this.loading;
    const oldCounts = this.counts;
    this.generation++;
    this.fetchPage = fetchPage;
    this.counts = counts;
    this.index();
    this.data = new Map();
    this.loading = new Map();
    this.failed = new Set();
    this.stale = new Set();
    this.previous = new Map();
    this.ids = new Map();
    for (let s = 0; s < counts.length; s++) {
      const from = keep(s);
      const items = from >= 0 ? old.get(from) : undefined;
      if (items && items.length === counts[s]) {
        this.put(s, items);
        this.stale.add(s);
      } else {
        const before = from >= 0 ? (items ?? oldPrevious.get(from)) : undefined;
        if (before) this.previous.set(s, before);
        const job = from >= 0 && !items ? oldLoading.get(from) : undefined;
        if (job && counts[s] === oldCounts[from]) {
          job.section = s;
          this.loading.set(s, job);
          oldLoading.delete(from);
        }
      }
    }
    for (const job of oldLoading.values()) job.section = null;
    this.changed();
    this.pump();
  }

  private put(s: number, items: PhotoItem[]): void {
    this.data.set(s, items);
    this.previous.delete(s);
    const start = this.starts[s] ?? 0;
    items.forEach((it, k) => this.ids.set(it.id, start + k));
  }

  section(s: number): PhotoItem[] | undefined {
    return this.data.get(s);
  }

  isLoaded(s: number): boolean {
    return this.data.has(s);
  }

  /** Het item op globale plek i, als zijn sectie geladen is. */
  item(i: number): PhotoItem | undefined {
    if (i < 0) return undefined;
    // Binair zoeken in de beginplekken
    let lo = 0;
    let hi = this.counts.length - 1;
    let s = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (this.starts[mid] <= i) {
        s = mid;
        lo = mid + 1;
      } else hi = mid - 1;
    }
    while (s > 0 && this.counts[s] === 0) s--;
    if (s < 0) return undefined;
    return this.data.get(s)?.[i - this.starts[s]];
  }

  /** De plek van een geladen item, of -1. */
  indexOf(id: string): number {
    return this.ids.get(id) ?? -1;
  }

  /** Een item dat niet meer geladen is omdat zijn sectie een ander aantal kreeg en nog opnieuw
   * geladen wordt (of dat mislukte): de sectie en de oude items. Null als hij geladen is, of
   * als zijn sectie opnieuw geladen is zonder hem (dan is hij echt weg). */
  held(id: string): { section: number; items: PhotoItem[] } | null {
    if (this.ids.has(id)) return null;
    for (const [section, items] of this.previous) if (items.some((it) => it.id === id)) return { section, items };
    return null;
  }

  /** Moet sectie s (opnieuw) van de server komen? */
  private needs(s: number): boolean {
    return (!this.data.has(s) || this.stale.has(s)) && !this.failed.has(s);
  }

  /** Deze secties zijn in beeld: die eerst laden, de rest van de wachtrij vervalt. */
  want(sections: readonly number[]): void {
    this.wanted = sections.filter((s) => this.needs(s));
    this.pump();
  }

  private pump(): void {
    while (this.loading.size < this.concurrency) {
      const s = this.wanted.find((x) => this.needs(x) && !this.loading.has(x));
      if (s === undefined) return;
      this.load(s, true).catch(() => {
        /* onError meldt het al */
      });
    }
  }

  /** Laadt sectie s (alle pagina's) en geeft de items. fresh: ook als hij verouderd is. */
  load(s: number, fresh = false): Promise<PhotoItem[]> {
    const have = this.data.get(s);
    if (have && !(fresh && this.stale.has(s))) return Promise.resolve(have);
    const busy = this.loading.get(s);
    if (busy) return busy.promise;
    const job: Job = { section: s, gen: this.generation, promise: Promise.resolve([]) };
    const run = async () => {
      const items: PhotoItem[] = [];
      let after: string | null = null;
      do {
        // De volgende pagina van dezelfde maand, ook als die intussen een andere plek kreeg
        const page: PhotoPage = await this.fetchPage(job.section ?? s, after);
        items.push(...page.items);
        after = page.next;
      } while (after !== null && job.section !== null);
      return items;
    };
    job.promise = run().then(
      (items) => {
        const at = job.section;
        if (at === null) return items;
        this.loading.delete(at);
        const current = job.gen === this.generation;
        if (current || items.length === this.counts[at]) {
          if (current) this.stale.delete(at);
          else this.stale.add(at); // tonen, en straks nog een keer van de server
          this.put(at, items);
          this.failed.delete(at);
          if (current && items.length !== this.counts[at]) this.onMismatch(at, items.length);
          this.changed();
        }
        this.pump();
        return items;
      },
      (e: unknown) => {
        const at = job.section;
        if (at !== null) {
          this.loading.delete(at);
          this.failed.add(at);
          this.onError(e);
          this.pump();
        }
        throw e;
      },
    );
    this.loading.set(s, job);
    return job.promise;
  }

  /** Mislukte secties weer proberen (bijv. weer online). */
  retry(): void {
    this.failed.clear();
    this.pump();
  }

  /** Alle geladen items, in de volgorde van de tijdlijn. */
  loadedItems(): PhotoItem[] {
    const out: PhotoItem[] = [];
    for (let s = 0; s < this.counts.length; s++) out.push(...(this.data.get(s) ?? []));
    return out;
  }
}
