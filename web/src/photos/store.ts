// De foto's van de tijdlijn (of een album) per sectie: een maand wordt pas geladen als hij in
// beeld komt, hooguit twee tegelijk, en wat intussen uit beeld scrolde komt achteraan. Geen
// React: de pagina volgt hem met useSyncExternalStore (version).

import type { PhotoItem, PhotoPage } from "./api";

export type FetchPage = (section: number, after: string | null) => Promise<PhotoPage>;

export class SectionStore {
  private data = new Map<number, PhotoItem[]>();
  private loading = new Map<number, Promise<PhotoItem[]>>();
  private wanted: number[] = [];
  private failed = new Set<number>();
  /** Geladen, maar misschien verouderd (na een wijziging): blijft staan tot het nieuwe er is */
  private stale = new Set<number>();
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

  /** Nieuwe secties (de maanden opnieuw gelezen). keep: secties waarvan de inhoud nog klopt. */
  setSections(counts: readonly number[], fetchPage: FetchPage = this.fetchPage, keep: (s: number) => number = () => -1) {
    const old = this.data;
    this.generation++;
    this.fetchPage = fetchPage;
    this.counts = counts;
    this.index();
    this.data = new Map();
    this.loading = new Map();
    this.failed = new Set();
    this.stale = new Set();
    this.ids = new Map();
    for (let s = 0; s < counts.length; s++) {
      const from = keep(s);
      const items = from >= 0 ? old.get(from) : undefined;
      if (items && items.length === counts[s]) {
        this.put(s, items);
        this.stale.add(s);
      }
    }
    this.changed();
    this.pump();
  }

  private put(s: number, items: PhotoItem[]): void {
    this.data.set(s, items);
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
    if (busy) return busy;
    const gen = this.generation;
    const run = async () => {
      const items: PhotoItem[] = [];
      let after: string | null = null;
      do {
        const page: PhotoPage = await this.fetchPage(s, after);
        items.push(...page.items);
        after = page.next;
      } while (after !== null && gen === this.generation);
      return items;
    };
    const p = run().then(
      (items) => {
        if (gen !== this.generation) return items;
        this.loading.delete(s);
        this.stale.delete(s);
        this.put(s, items);
        this.failed.delete(s);
        if (items.length !== this.counts[s]) this.onMismatch(s, items.length);
        this.changed();
        this.pump();
        return items;
      },
      (e: unknown) => {
        if (gen === this.generation) {
          this.loading.delete(s);
          this.failed.add(s);
          this.onError(e);
          this.pump();
        }
        throw e;
      },
    );
    this.loading.set(s, p);
    return p;
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
