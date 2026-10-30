// De inhoud van één open map, ook met 10.000 kinderen. Eerst een kleine pagina (snel iets op
// het scherm), de rest in pagina's van 1.000 als de browser tijd heeft. Live wijzigingen komen
// binnen als node.*-gebeurtenissen: de ids worden 150 ms verzameld en dan in één keer opgevraagd
// (POST /files/lookup). Wat terugkomt met deze map als ouder, komt op zijn plek in de lijst; wat
// ontbreekt, in de prullenbak ligt of een andere ouder heeft, gaat eruit. Alles opnieuw laden
// alleen bij "connected" (er kan iets gemist zijn), tags.changed, een deling die verandert in
// een gedeelde map, of als de map zelf weg lijkt (404).
//
// De snapshot is onveranderlijk voor React (een nieuw object bij elke wijziging), maar de Map met
// nodes wordt in place bijgewerkt: gebruik version om iets te onthouden (useMemo).

import { ApiError, type FileNode } from "../api";
import { eventIds, folderEventIds, type ServerEvent } from "../events";
import { browserApi, FIRST_PAGE, LOOKUP_MAX, NEXT_PAGE, type Page, type PageOptions } from "./api";
import { takePreloaded } from "./preload";
import { insertionIndex, serverCompare } from "./sort";

export type FolderStatus = "loading" | "ready" | "gone" | "error";

export interface FolderSnapshot {
  /** De gevraagde map (null: de eigen hoofdmap) */
  folderId: string | null;
  folder: FileNode | null;
  /** Van de hoofdmap (of de gedeelde map) tot en met folder */
  path: readonly FileNode[];
  /** De geladen kinderen, in servervolgorde */
  ids: readonly string[];
  nodes: ReadonlyMap<string, FileNode>;
  /** Hoeveel kinderen er in totaal zijn (ook wat nog niet geladen is) */
  total: number;
  /** Alles is geladen */
  complete: boolean;
  status: FolderStatus;
  error: unknown;
  /** Telt op bij elke wijziging */
  version: number;
}

export interface FolderStoreDeps {
  page(folderId: string | null, o: PageOptions): Promise<Page>;
  lookup(ids: string[]): Promise<FileNode[]>;
  /** Iets doen als de browser tijd heeft (de volgende pagina); geeft afbreken terug */
  idle(cb: () => void): () => void;
  /** Na ms iets doen; geeft afbreken terug */
  later(cb: () => void, ms: number): () => void;
  preloaded(folderId: string | null): Promise<Page | null> | null;
}

/** Zo lang worden ids van gebeurtenissen verzameld voor één lookup. */
export const COLLECT_MS = 150;
const RETRY_MS = 2000;
const RETRIES = 3;

function browserIdle(cb: () => void): () => void {
  if (typeof requestIdleCallback === "function") {
    const handle = requestIdleCallback(() => cb(), { timeout: 300 });
    return () => cancelIdleCallback(handle);
  }
  const handle = setTimeout(cb, 0);
  return () => clearTimeout(handle);
}

export const defaultDeps: FolderStoreDeps = {
  page: (folderId, o) => browserApi.page(folderId, o),
  lookup: (ids) => browserApi.lookup(ids),
  idle: browserIdle,
  later: (cb, ms) => {
    const handle = setTimeout(cb, ms);
    return () => clearTimeout(handle);
  },
  preloaded: takePreloaded,
};

/** Wat de lijst moet weten over de map voor live gebeurtenissen. */
export interface EventContext {
  /** De map is met je gedeeld: een deling die verandert, kan je rechten veranderen */
  shared: boolean;
}

export class FolderStore {
  private snapshot: FolderSnapshot;
  private readonly listeners = new Set<() => void>();
  private readonly nodes = new Map<string, FileNode>();
  /** Na elke (her)start of opruimen: oude antwoorden tellen niet meer */
  private generation = 0;
  private cancelNext: (() => void) | null = null;
  private pending = new Set<string>();
  private cancelFlush: (() => void) | null = null;
  private reloading: Promise<void> | null = null;
  /** ids die tijdens het herladen binnenkwamen: na het wisselen nog eens opvragen */
  private touched = new Set<string>();
  private disposed = false;

  constructor(
    readonly folderId: string | null,
    private readonly deps: FolderStoreDeps = defaultDeps,
  ) {
    this.snapshot = {
      folderId,
      folder: null,
      path: [],
      ids: [],
      nodes: this.nodes,
      total: 0,
      complete: false,
      status: "loading",
      error: null,
      version: 0,
    };
  }

  getSnapshot = (): FolderSnapshot => this.snapshot;

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  private set(change: Partial<FolderSnapshot>): void {
    const next = { ...this.snapshot, ...change, nodes: this.nodes, version: this.snapshot.version + 1 };
    if (next.complete) next.total = next.ids.length;
    this.snapshot = next;
    for (const l of [...this.listeners]) l();
  }

  /** Het id van de map zelf (bij de hoofdmap pas na de eerste pagina bekend). */
  get id(): string | null {
    return this.snapshot.folder?.id ?? this.folderId;
  }

  /** De eerste pagina ophalen (of overnemen van preloadFolder); de rest volgt vanzelf. */
  start(): void {
    this.disposed = false;
    const gen = ++this.generation;
    const preloaded = this.deps.preloaded(this.folderId);
    const first = (preloaded ?? Promise.resolve(null)).then(
      (page) => page ?? this.deps.page(this.folderId, { limit: FIRST_PAGE }),
    );
    first.then(
      (page) => {
        if (gen !== this.generation) return;
        this.nodes.clear();
        for (const n of page.children) this.nodes.set(n.id, n);
        this.set({
          folder: page.folder,
          path: page.path,
          ids: page.children.map((n) => n.id),
          total: page.total,
          complete: page.next === null,
          status: "ready",
          error: null,
        });
        if (page.next) this.scheduleNext(gen, page.next, 0);
      },
      (e: unknown) => {
        if (gen !== this.generation) return;
        this.fail(e);
      },
    );
  }

  private fail(e: unknown): void {
    if (e instanceof ApiError && e.status === 404) {
      this.nodes.clear();
      this.set({ status: "gone", error: e, ids: [], total: 0, complete: true });
    } else if (this.snapshot.status === "loading") {
      this.set({ status: "error", error: e });
    } else {
      this.set({ error: e });
    }
  }

  private scheduleNext(gen: number, after: string, attempt: number): void {
    this.cancelNext?.();
    const run = () => {
      this.cancelNext = null;
      this.deps.page(this.folderId, { limit: NEXT_PAGE, after }).then(
        (page) => {
          if (gen !== this.generation) return;
          this.append(page);
          if (page.next) this.scheduleNext(gen, page.next, 0);
        },
        (e: unknown) => {
          if (gen !== this.generation) return;
          if (e instanceof ApiError && e.status === 404) return this.fail(e);
          // Even geen verbinding: straks opnieuw (daarna komt "connected", dan alles opnieuw)
          if (attempt < RETRIES) this.cancelNext = this.deps.later(() => this.scheduleNext(gen, after, attempt + 1), RETRY_MS);
          else this.set({ error: e });
        },
      );
    };
    this.cancelNext = attempt === 0 ? this.deps.idle(run) : (run(), null);
  }

  private append(page: Page): void {
    const added: string[] = [];
    for (const n of page.children) {
      if (!this.nodes.has(n.id)) added.push(n.id);
      this.nodes.set(n.id, n);
    }
    this.set({
      ids: added.length ? [...this.snapshot.ids, ...added] : this.snapshot.ids,
      total: Math.max(page.total, this.snapshot.ids.length + added.length),
      complete: page.next === null,
    });
  }

  /**
   * Alles opnieuw, in de achtergrond: de oude lijst blijft staan tot de nieuwe helemaal binnen
   * is (geen flikkering voor wie ver naar beneden staat). Een 404: de map is weg.
   */
  reload(): Promise<void> {
    if (this.reloading) return this.reloading;
    if (this.snapshot.status === "loading" || this.snapshot.status === "error") {
      this.start();
      return Promise.resolve();
    }
    const gen = ++this.generation;
    this.cancelNext?.();
    this.cancelNext = null;
    const run = async () => {
      try {
        let page = await this.deps.page(this.folderId, { limit: NEXT_PAGE });
        if (gen !== this.generation) return;
        const children = [...page.children];
        while (page.next) {
          page = await this.deps.page(this.folderId, { limit: NEXT_PAGE, after: page.next });
          if (gen !== this.generation) return;
          children.push(...page.children);
        }
        this.nodes.clear();
        const ids: string[] = [];
        for (const n of children) {
          if (this.nodes.has(n.id)) continue;
          this.nodes.set(n.id, n);
          ids.push(n.id);
        }
        this.set({ folder: page.folder, path: page.path, ids, complete: true, status: "ready", error: null });
      } catch (e) {
        if (gen === this.generation) this.fail(e);
      }
    };
    this.reloading = run().finally(() => {
      this.reloading = null;
      if (this.touched.size && !this.disposed) {
        for (const id of this.touched) this.pending.add(id);
        this.touched.clear();
        this.scheduleFlush();
      }
    });
    return this.reloading;
  }

  /** Een gebeurtenis van de server. */
  onEvent(event: ServerEvent, ctx: EventContext = { shared: false }): void {
    if (this.disposed) return;
    if (event.type === "connected" || event.type === "tags.changed") {
      void this.reload();
      return;
    }
    if (event.type.startsWith("share.")) {
      if (ctx.shared || this.snapshot.status === "gone") void this.reload();
      return;
    }
    if (!event.type.startsWith("node.")) return;
    if (this.snapshot.status === "gone") {
      // Komt de map terug (teruggezet, opnieuw gedeeld)? Dan staat hij er weer
      if (this.folderId && eventIds(event).includes(this.folderId)) void this.reload();
      return;
    }
    const folderId = this.id;
    // De map zelf of iets in het pad erboven: hernoemd, verplaatst of weg
    const header = new Set([folderId, ...this.snapshot.path.map((p) => p.id)]);
    const ids = eventIds(event);
    if (ids.some((id) => header.has(id))) this.queue([...header].filter((id): id is string => id !== null));
    this.queue(folderEventIds(event, folderId, (id) => this.nodes.has(id)));
  }

  private queue(ids: readonly string[]): void {
    if (!ids.length) return;
    for (const id of ids) {
      this.pending.add(id);
      if (this.reloading) this.touched.add(id);
    }
    this.scheduleFlush();
  }

  private scheduleFlush(): void {
    if (this.cancelFlush || this.disposed) return;
    this.cancelFlush = this.deps.later(() => {
      this.cancelFlush = null;
      void this.flush();
    }, COLLECT_MS);
  }

  /** Vraagt de verzamelde ids op en werkt de lijst bij. */
  async flush(): Promise<void> {
    const asked = [...this.pending];
    this.pending.clear();
    if (!asked.length) return;
    const gen = this.generation;
    const found: FileNode[] = [];
    try {
      for (let i = 0; i < asked.length; i += LOOKUP_MAX) {
        found.push(...(await this.deps.lookup(asked.slice(i, i + LOOKUP_MAX))));
      }
    } catch {
      return; // geen verbinding: bij "connected" komt alles opnieuw
    }
    if (gen !== this.generation || this.disposed) {
      // Intussen herladen: straks nog eens
      for (const id of asked) this.touched.add(id);
      return;
    }
    this.merge(asked, found);
  }

  /** Werkt de map en zijn pad bij als die in found zitten; geeft false als dat niet kan
   * (de map is weg of verplaatst: dan alles opnieuw). */
  private mergeHeader(asked: ReadonlySet<string>, byId: ReadonlyMap<string, FileNode>): boolean {
    const folder = this.snapshot.folder;
    if (!folder) return true;
    const path = this.snapshot.path;
    if (!path.some((p) => asked.has(p.id)) && !asked.has(folder.id)) return true;
    const fresh = path.map((p) => (asked.has(p.id) ? byId.get(p.id) : p));
    if (fresh.some((p) => p === undefined)) return false; // weg, of je kunt er niet meer bij
    const nodes = fresh as FileNode[];
    // Elke stap moet nog in de vorige liggen (de bovenste van een gedeelde map heeft geen ouder)
    for (let i = 1; i < nodes.length; i++) if (nodes[i].parent_id !== nodes[i - 1].id) return false;
    const newFolder = asked.has(folder.id) ? byId.get(folder.id) : folder;
    if (!newFolder) return false;
    this.set({ folder: newFolder, path: nodes.map((n) => (n.id === newFolder.id ? newFolder : n)) });
    return true;
  }

  /**
   * Verwerkt het antwoord van een lookup: elk gevraagd id dat terugkomt met deze map als ouder,
   * staat erin (op zijn plek); de rest niet. Ook voor de eigen acties (upsert/remove).
   */
  merge(asked: readonly string[], found: readonly FileNode[]): void {
    const byId = new Map(found.map((n) => [n.id, n]));
    const askedSet = new Set(asked);
    if (!this.mergeHeader(askedSet, byId)) {
      void this.reload();
      return;
    }
    const folderId = this.id;
    const s = this.snapshot;
    const out = new Set<string>();
    const place: FileNode[] = [];
    let delta = 0;
    for (const id of askedSet) {
      if (id === folderId) continue;
      const node = byId.get(id);
      const had = this.nodes.get(id);
      const belongs = node !== undefined && !node.deleted_at && node.parent_id === folderId;
      if (!belongs) {
        if (had) {
          out.add(id);
          this.nodes.delete(id);
          delta--;
        }
        continue;
      }
      if (had) {
        this.nodes.set(id, node);
        // Andere naam of soort: een andere plek in de lijst
        if (had.name !== node.name || had.kind !== node.kind) {
          out.add(id);
          place.push(node);
        }
      } else {
        place.push(node);
        delta++;
      }
    }
    let ids = out.size ? s.ids.filter((id) => !out.has(id)) : [...s.ids];
    place.sort(serverCompare);
    for (const node of place) {
      const at = insertionIndex(ids.length, (i) => this.nodes.get(ids[i]), node);
      if (at === ids.length && !s.complete) {
        // Hoort na wat er geladen is: dat komt met een volgende pagina
        this.nodes.delete(node.id);
        continue;
      }
      this.nodes.set(node.id, node);
      ids.splice(at, 0, node.id);
    }
    if (!out.size && !place.length && !found.length) return;
    if (ids.length === s.ids.length && ids.every((id, i) => id === s.ids[i])) ids = s.ids as string[];
    this.set({ ids, total: Math.max(ids.length, s.total + delta) });
  }

  /** Deze nodes opnieuw opvragen (tags, delingen of versies veranderd; teruggezet). */
  refresh(ids: readonly string[]): void {
    this.queue(ids);
  }

  /** Na een eigen actie: deze nodes zoals de API ze teruggaf (nieuw, hernoemd, verplaatst). */
  upsert(nodes: readonly FileNode[]): void {
    if (nodes.length) this.merge(
      nodes.map((n) => n.id),
      nodes,
    );
  }

  /** Na een eigen actie: deze ids staan er niet meer in (weggegooid, verplaatst). */
  remove(ids: readonly string[]): void {
    if (ids.length) this.merge(ids, []);
  }

  dispose(): void {
    this.disposed = true;
    this.generation++;
    this.cancelNext?.();
    this.cancelFlush?.();
    this.cancelNext = null;
    this.cancelFlush = null;
    this.pending.clear();
    this.touched.clear();
  }
}
