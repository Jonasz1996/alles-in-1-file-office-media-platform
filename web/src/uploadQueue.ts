// De wachtrij met uploads staat buiten de pagina Bestanden: uploads lopen door als je naar een
// andere pagina gaat, en je ziet ze in de uploadlade (upload/UploadTray.tsx) met voortgang,
// Pauzeren, Hervatten en Annuleren. Per bestand en map loopt er hooguit één upload tegelijk.
//
// Opzet (P3):
// - de items staan in een Map (volgorde = volgorde van toevoegen), met lopende totalen, zodat
//   toevoegen, ontdubbelen en tellen O(n) blijft, ook bij 5.000 bestanden;
// - getSnapshot() geeft een stabiele array die bij elke wijziging vervangen wordt ([] als de
//   rij leeg is); subscribe() hoort elke wijziging, subscribeSummary() hooguit 4 keer per
//   seconde (voor de lade: aantallen, bytes, snelheid en resterende tijd);
// - een planner start er hooguit `concurrency` tegelijk (de wachtrij van de app: 3), en maar
//   één zolang er een bestand boven 256 MB bij is;
// - pauzeren stopt de upload zonder hem weg te gooien (abort met STOP); hervatten gaat verder
//   waar de server was.
// add() houdt zijn betekenis: hij is klaar als elk bestand van die batch klaar, mislukt of
// weggegooid is. addTree() doet een mapupload: eerst de mappen (folders/tree), dan de bezette
// namen (names/check), dan één vraag over dubbele bestanden, en pas dan de uploads.

import { useSyncExternalStore } from "react";
import { ApiError, api } from "./api";
import { t } from "./i18n";
import { CHUNK, type FolderError, tusUrl, type UploadRequests, uploadRequests } from "./upload/api";
import { type AskConflicts, type ConflictItem, conflicts } from "./upload/conflicts";
import { etaSeconds, RateMeter } from "./upload/eta";
import { interrupted } from "./upload/interrupted";
import { type ConflictChoice, findConflicts, nameKey, planConflicts } from "./upload/plan";
import type { Collected } from "./upload/walk";
import { discardUpload, STOP, storageKey, storageKeyFor, type TusApi, type UploadOptions, uploadFile } from "./upload";
import { uploadStatus } from "./uploadStatus";

export type UploadState = "waiting" | "running" | "paused" | "failed" | "discarding";
/** Wat de server doet als de naam bij het afronden bezet is. */
export type UploadConflict = "rename" | "replace" | "fail";

/** Waarom een upload mislukte; de lade maakt er tekst van (errorText of een eigen sleutel). */
export interface UploadFailure {
  code?: string;
  message: string;
  params?: Record<string, unknown>;
  /** De map kon niet gemaakt worden (folders/tree); het pad van die map */
  folder?: string;
  /** Weggooien mislukte (de upload staat er nog) */
  discard?: boolean;
}

export interface UploadItem {
  /** Alleen binnen deze pagina (React-key). Een teller: crypto.randomUUID bestaat alleen via
   * https of localhost, niet via http op het LAN-adres. */
  key: string;
  file: File;
  parent: string;
  progress: number;
  /** waiting: nog niet aan de beurt; running: loopt; paused: gepauzeerd (staat nog op de
   * server); failed: mislukt; discarding: wordt weggegooid. */
  state: UploadState;
  /** De tekst van de fout (zoals de server of de client hem gaf) */
  error?: string;
  failure?: UploadFailure;
  /** Upload-URL zodra de server de upload kent; zo werkt weggooien ook zonder localStorage. */
  url?: string;
  /** Het pad in een mapupload ("Vakantie/2024/a.jpg"), om te tonen */
  path?: string;
  conflict?: UploadConflict;
  /** false: opnieuw proberen heeft geen zin (de map kon niet gemaakt worden) */
  retry?: boolean;
}

/** De totalen voor de lade, sinds de rij voor het laatst leeg was. */
export interface UploadSummary {
  files: number;
  done: number;
  failed: number;
  bytes: number;
  sent: number;
  running: number;
  waiting: number;
  paused: number;
  /** Mappen maken en namen controleren voor een batch die nog niet in de rij staat */
  preparing: number;
  /** Bytes per seconde (glijdend gemiddelde), of null */
  rate: number | null;
  /** Resterende seconden, of null */
  eta: number | null;
}

export interface QueueOptions extends Omit<UploadOptions, "signal" | "url" | "onUrl" | "meta"> {
  /** Hoeveel uploads tegelijk (standaard 1, zoals vroeger; de wachtrij van de app: 3) */
  concurrency?: number;
  /** Vanaf deze grootte maar één upload tegelijk (standaard 256 MB) */
  bigFile?: number;
  /** Voor tests */
  now?: () => number;
  /** Hooguit zo vaak per seconde subscribeSummary (standaard 4) */
  summaryPerSecond?: number;
  /** Voor addTree: de verzoeken en de vraag over dubbele bestanden */
  requests?: UploadRequests;
  ask?: AskConflicts;
}

export interface AddOptions {
  /** Wat de server doet als de naam bezet is (standaard rename: nooit overschrijven) */
  conflict?: UploadConflict;
  /** Na elke gelukte upload: het id van het nieuwe (of vervangen) bestand */
  onCreate?: (nodeId: string, item: UploadItem) => void;
}

interface Entry {
  file: File;
  parent: string;
  conflict?: UploadConflict;
  path?: string;
  url?: string;
}

/** Grootte en datum, één keer gelezen (zie storageKeyFor). */
interface Meta {
  size: number;
  lastModified: number;
}

const EMPTY: readonly UploadItem[] = Object.freeze([]) as readonly UploadItem[];
const BIG = 256 * 1024 * 1024;
/** Zo lang mag het lezen van grootte en datum achter elkaar duren voor de wachtrij de
 * hoofdthread even teruggeeft (addTree). */
const SLICE_MS = 30;

const metaOf = (file: File): Meta => ({ size: file.size, lastModified: file.lastModified });

/** Even de hoofdthread teruggeven (een nieuwe taak, zodat de browser kan tekenen). */
const yieldTask = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

/** metaOf voor elk bestand, in stukjes van hooguit SLICE_MS (geen lange taak bij 5.000). */
async function readMeta(files: readonly File[]): Promise<Meta[]> {
  const out: Meta[] = [];
  let start = performance.now();
  for (const file of files) {
    out.push(metaOf(file));
    if (performance.now() - start > SLICE_MS) {
      await yieldTask();
      start = performance.now();
    }
  }
  return out;
}

function failureOf(e: unknown): UploadFailure {
  if (e instanceof ApiError) return { code: e.code, message: e.message, params: e.params };
  return { message: (e as Error)?.message ?? String(e) };
}

export class UploadQueue {
  private readonly items = new Map<string, UploadItem>();
  private readonly byStorage = new Map<string, string>();
  /** Per item: grootte en opslagsleutel, één keer gelezen (geen herhaalde vraag aan de browser). */
  private readonly meta = new Map<string, { size: number; sk: string }>();
  private snapshot: readonly UploadItem[] | null = EMPTY;
  /** Per lopende poging; abort(STOP) is pauzeren (ook als er intussen hervat is), abort() annuleren. */
  private readonly controllers = new Map<string, AbortController>();
  /** Pogingen die na pauzeren nog aflopen en intussen geannuleerd zijn: wat zo'n poging nog op
   * de server aanmaakt (tusCreate liep nog), gooit hij zelf weg (uploadFile, cancelled) */
  private readonly cancelledAttempts = new WeakSet<AbortController>();
  private readonly running = new Set<string>();
  private readonly waiters = new Map<string, (() => void)[]>();
  private readonly creates = new Map<string, (nodeId: string, item: UploadItem) => void>();
  private readonly listeners = new Set<() => void>();
  private readonly summaryListeners = new Set<() => void>();
  private readonly finishedListeners = new Set<(parent: string, ok: boolean, file: File) => void>();
  private queue: string[] = [];
  private head = 0;
  private seq = 0;
  private preparing = 0;
  private totals = { files: 0, done: 0, bytes: 0, sent: 0 };
  private counts: Record<UploadState, number> = { waiting: 0, running: 0, paused: 0, failed: 0, discarding: 0 };
  private readonly meter = new RateMeter();
  private summary: UploadSummary | null = null;
  private summaryTimer: ReturnType<typeof setTimeout> | null = null;
  private lastSummary = -Infinity;
  private readonly concurrency: number;
  private readonly bigFile: number;
  private readonly now: () => number;
  private readonly interval: number;
  private readonly upload: Omit<UploadOptions, "signal" | "url" | "onUrl" | "meta">;

  constructor(
    private readonly api: TusApi,
    private readonly options: QueueOptions = {},
  ) {
    const { concurrency, bigFile, now, summaryPerSecond, requests: _r, ask: _a, ...upload } = options;
    this.concurrency = Math.max(1, concurrency ?? 1);
    this.bigFile = bigFile ?? BIG;
    this.now = now ?? (() => Date.now());
    this.interval = 1000 / Math.max(0.1, summaryPerSecond ?? 4);
    this.upload = upload;
  }

  // --- kijken ---

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = (): readonly UploadItem[] => {
    this.snapshot ??= this.items.size ? Array.from(this.items.values()) : EMPTY;
    return this.snapshot;
  };

  /** Zoals subscribe, maar hooguit summaryPerSecond keer per seconde (de lade). */
  subscribeSummary = (listener: () => void) => {
    this.summaryListeners.add(listener);
    return () => {
      this.summaryListeners.delete(listener);
    };
  };

  getSummary = (): UploadSummary => {
    if (!this.summary) {
      const now = this.now();
      const rate = this.counts.running ? this.meter.rate(now) : null;
      const remaining = this.totals.bytes - this.totals.sent;
      this.summary = {
        ...this.totals,
        failed: this.counts.failed,
        running: this.counts.running,
        waiting: this.counts.waiting,
        paused: this.counts.paused,
        preparing: this.preparing,
        rate,
        eta: this.counts.running || this.counts.waiting ? etaSeconds(remaining, rate) : null,
      };
    }
    return this.summary;
  };

  /** Staat er iets in de rij of wordt er een batch voorbereid? */
  busy(): boolean {
    return this.items.size > 0 || this.preparing > 0;
  }

  /** Het item met deze upload-URL, als dat in de rij staat. */
  hasUrl(url: string): boolean {
    for (const item of this.items.values()) if (item.url === url) return true;
    return false;
  }

  /** listener krijgt na elke upload (gelukt of niet) de map waar hij in moest, of hij lukte
   * (niet bij mislukt of geannuleerd) en het bestand. Pauzeren telt niet als klaar. */
  onFinished(listener: (parent: string, ok: boolean, file: File) => void): () => void {
    this.finishedListeners.add(listener);
    return () => {
      this.finishedListeners.delete(listener);
    };
  }

  // --- intern: wijzigen en melden ---

  private changed(): void {
    this.snapshot = null;
    this.summary = null;
    for (const listener of this.listeners) listener();
    this.scheduleSummary();
  }

  private scheduleSummary(): void {
    if (this.summaryTimer !== null || this.summaryListeners.size === 0) return;
    const wait = Math.max(0, this.lastSummary + this.interval - this.now());
    this.summaryTimer = setTimeout(() => {
      this.summaryTimer = null;
      this.lastSummary = this.now();
      for (const listener of this.summaryListeners) listener();
    }, wait);
  }

  private set(key: string, change: Partial<UploadItem>): UploadItem | undefined {
    const old = this.items.get(key);
    if (!old) return undefined;
    const next = { ...old, ...change };
    if (change.state && change.state !== old.state) {
      this.counts[old.state]--;
      this.counts[next.state]++;
    }
    this.items.set(key, next);
    return next;
  }

  private change(key: string, change: Partial<UploadItem>): void {
    if (this.set(key, change)) this.changed();
  }

  private progress(key: string, fraction: number): void {
    const item = this.items.get(key);
    if (!item) return;
    const size = this.sizeOf(key, item);
    const before = item.progress * size;
    const after = fraction * size;
    this.totals.sent += after - before;
    this.meter.add(this.totals.sent, this.now());
    this.change(key, { progress: fraction });
  }

  /** Weg uit de rij. done: gelukt (telt mee in de totalen); anders tellen zijn bytes niet meer. */
  private drop(key: string, done: boolean): void {
    const item = this.items.get(key);
    if (!item) return;
    const size = this.sizeOf(key, item);
    const sk = this.meta.get(key)?.sk ?? storageKey(item.parent, item.file);
    this.items.delete(key);
    this.meta.delete(key);
    this.counts[item.state]--;
    if (this.byStorage.get(sk) === key) this.byStorage.delete(sk);
    this.creates.delete(key);
    if (done) {
      this.totals.done++;
      this.totals.sent += size * (1 - item.progress);
    } else {
      this.totals.files--;
      this.totals.bytes -= size;
      this.totals.sent -= size * item.progress;
    }
    if (this.items.size === 0) this.resetTotals();
    this.changed();
    this.settle(key);
  }

  private resetTotals(): void {
    this.totals = { files: 0, done: 0, bytes: 0, sent: 0 };
    this.meter.reset();
    this.queue = [];
    this.head = 0;
  }

  /** Wie op dit item wachtte (add), hoort dat het klaar is. */
  private settle(key: string): void {
    const waiting = this.waiters.get(key);
    if (!waiting) return;
    this.waiters.delete(key);
    for (const done of waiting) done();
  }

  private settled(key: string): boolean {
    const item = this.items.get(key);
    return !item || (item.state === "failed" && !this.running.has(key));
  }

  private waitFor(keys: Iterable<string>): Promise<void> {
    const open = [...new Set(keys)].filter((k) => !this.settled(k));
    if (open.length === 0) return Promise.resolve();
    return new Promise((resolve) => {
      let left = open.length;
      const one = () => {
        if (--left === 0) resolve();
      };
      for (const key of open) {
        const list = this.waiters.get(key);
        if (list) list.push(one);
        else this.waiters.set(key, [one]);
      }
    });
  }

  // --- de planner ---

  private sizeOf(key: string, item: UploadItem): number {
    return this.meta.get(key)?.size ?? item.file.size;
  }

  private canStart(item: UploadItem): boolean {
    if (this.running.size === 0) return true;
    if (this.running.size >= this.concurrency || this.sizeOf(item.key, item) > this.bigFile) return false;
    for (const key of this.running) {
      const other = this.items.get(key);
      if (other && this.sizeOf(key, other) > this.bigFile) return false;
    }
    return true;
  }

  private pump(): void {
    while (this.head < this.queue.length) {
      const key = this.queue[this.head];
      const item = this.items.get(key);
      // Verouderd (gepauzeerd, weg, of loopt nog af na pauzeren: die komt zelf terug)
      if (!item || item.state !== "waiting" || this.running.has(key)) {
        this.head++;
        continue;
      }
      if (!this.canStart(item)) break;
      this.head++;
      void this.attempt(key);
    }
    if (this.head > 1024 && this.head * 2 > this.queue.length) {
      this.queue = this.queue.slice(this.head);
      this.head = 0;
    }
  }

  private enqueueKey(key: string): void {
    this.queue.push(key);
  }

  /** Eén poging voor één item; klaar als die poging klaar is. */
  private async attempt(key: string): Promise<void> {
    const item = this.items.get(key);
    if (!item) return;
    const controller = new AbortController();
    this.controllers.set(key, controller);
    this.running.add(key);
    this.change(key, { state: "running", error: undefined, failure: undefined });
    let ok = false;
    let nodeId: string | null = null;
    let paused = false;
    try {
      nodeId = await uploadFile(this.api, item.parent, item.file, (p) => this.progress(key, p), {
        ...this.upload,
        signal: controller.signal,
        url: this.items.get(key)?.url ?? item.url,
        onUrl: (url) => this.change(key, { url }),
        meta: { conflict: item.conflict ?? "rename", lastmodified: String(item.file.lastModified) },
        cancelled: () => this.cancelledAttempts.has(controller),
      });
      ok = true;
    } catch (e) {
      // Gestopt (pauzeren) is nooit annuleren, ook niet als hij intussen al hervat is: tusCreate
      // en tusStatus lopen dan nog af, en hervatten zet hem hieronder weer in de rij.
      paused = controller.signal.reason === STOP && this.items.has(key);
      if (!paused) {
        if ((e instanceof ApiError && e.code === "aborted") || controller.signal.aborted) {
          this.running.delete(key);
          this.drop(key, false);
        } else {
          const failure = failureOf(e);
          this.running.delete(key);
          this.change(key, { state: "failed", error: failure.message, failure });
          this.settle(key);
        }
      }
    } finally {
      this.controllers.delete(key);
      this.running.delete(key);
    }
    const onCreate = this.creates.get(key);
    if (ok) {
      const done = this.items.get(key);
      this.drop(key, true);
      if (nodeId && done && onCreate) onCreate(nodeId, done);
    }
    if (!paused) for (const listener of this.finishedListeners) listener(item.parent, ok, item.file);
    // Intussen hervat (terwijl hij nog afliep): weer achteraan in de rij
    if (this.items.get(key)?.state === "waiting") this.enqueueKey(key);
    this.pump();
  }

  // --- toevoegen ---

  /**
   * Zet bestanden in de rij voor map `parent`. Staat hetzelfde bestand voor dezelfde map er al,
   * dan komt er geen tweede bij: loopt of wacht hij, dan blijft dat zo; is hij mislukt, dan gaat
   * hij op zijn beurt opnieuw (verder waar hij was); is hij gepauzeerd, dan gaat hij verder.
   * Klaar als elk bestand van deze batch klaar, mislukt of weggegooid is.
   */
  add(parent: string, files: Iterable<File>, opts: AddOptions = {}): Promise<void> {
    const entries: Entry[] = [];
    for (const file of files) entries.push({ file, parent, conflict: opts.conflict });
    return this.enqueue(entries, opts.onCreate);
  }

  /** metas[i]: grootte en datum van entries[i], als die al gelezen zijn (addTree). */
  private enqueue(
    entries: readonly Entry[],
    onCreate?: AddOptions["onCreate"],
    metas?: readonly Meta[],
  ): Promise<void> {
    const keys: string[] = [];
    for (let i = 0; i < entries.length; i++) {
      const e = entries[i];
      const m = metas?.[i] ?? metaOf(e.file);
      const sk = storageKeyFor(e.parent, e.file.name, m.size, m.lastModified);
      const same = this.byStorage.get(sk);
      const existing = same === undefined ? undefined : this.items.get(same);
      if (same !== undefined && existing) {
        if ((existing.state === "failed" && existing.retry !== false) || existing.state === "paused") {
          this.set(same, { state: "waiting", conflict: e.conflict ?? existing.conflict, url: e.url ?? existing.url });
          this.enqueueKey(same);
        }
        if (onCreate) this.creates.set(same, onCreate);
        keys.push(same);
        continue;
      }
      const key = `${e.file.name}-${++this.seq}`;
      const item: UploadItem = {
        key,
        file: e.file,
        parent: e.parent,
        progress: 0,
        state: "waiting",
        conflict: e.conflict,
        path: e.path,
        url: e.url,
      };
      this.items.set(key, item);
      this.meta.set(key, { size: m.size, sk });
      this.counts.waiting++;
      this.byStorage.set(sk, key);
      if (onCreate) this.creates.set(key, onCreate);
      this.totals.files++;
      this.totals.bytes += m.size;
      this.enqueueKey(key);
      keys.push(key);
    }
    if (entries.length) this.changed();
    const done = this.waitFor(keys);
    this.pump();
    return done;
  }

  /** Bestanden die niet kunnen (hun map kon niet gemaakt worden): meteen mislukt, zichtbaar in
   * de lade, zonder Opnieuw. */
  private addFailed(
    list: readonly { file: File; parent: string; path: string; failure: UploadFailure }[],
    metas?: readonly Meta[],
  ): void {
    for (let i = 0; i < list.length; i++) {
      const f = list[i];
      const size = metas?.[i]?.size ?? f.file.size;
      const key = `${f.file.name}-${++this.seq}`;
      this.items.set(key, {
        key,
        file: f.file,
        parent: f.parent,
        progress: 0,
        state: "failed",
        path: f.path,
        error: t(`upload.folderError.${f.failure.code as FolderError}`, { folder: f.failure.folder ?? "" }),
        failure: f.failure,
        retry: false,
      });
      this.meta.set(key, { size, sk: `failed:${key}` }); // nooit in byStorage
      this.counts.failed++;
      this.totals.files++;
      this.totals.bytes += size;
    }
    if (list.length) this.changed();
  }

  /** Een onderbroken upload (Onderbroken uploads) verder met het opnieuw gekozen bestand. */
  resumeUpload(entry: { id: string; parent_id: string }, file: File): Promise<void> {
    interrupted.remove(entry.id);
    return this.enqueue([{ file, parent: entry.parent_id, url: tusUrl(entry.id) }]);
  }

  /**
   * Een batch in map folderId, met mappen (een mapupload) of zonder (losse bestanden):
   * 1. de mappen via folders/tree (per 1.000 paden); een map die niet kan (een bestand heeft
   *    die naam) maakt zijn bestanden meteen mislukt;
   * 2. names/check, alleen in mappen die er al waren (in een nieuwe map staat nog niets);
   * 3. één vraag over de dubbele bestanden (ask); geen antwoord (null): niets uploaden;
   * 4. de uploads, zonder botsing met conflict "rename", met botsing de keuze.
   * Tot en met de keuze gaat er geen tus-verzoek uit. Klaar zoals add().
   */
  async addTree(folderId: string, collected: Pick<Collected, "files" | "dirs">, opts: AddOptions = {}): Promise<void> {
    const requests = this.options.requests;
    const ask = this.options.ask;
    if (!requests || !ask) {
      // Zonder verzoeken (bijv. een uploadlink): gewoon alles in deze map
      return this.add(
        folderId,
        collected.files.map((f) => f.file),
        opts,
      );
    }
    this.preparing++;
    this.changed();
    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      this.preparing--;
      this.changed();
    };
    try {
      // 1. mappen
      const ids = new Map<string, string>([["", folderId]]);
      const created = new Set<string>();
      const broken = new Map<string, FolderError>();
      const dirs = collected.dirs;
      for (let i = 0; i < dirs.length; i += CHUNK) {
        const part = dirs.slice(i, i + CHUNK);
        const out = await requests.foldersTree(folderId, part);
        for (const [path, id] of Object.entries(out.folders)) ids.set(path, id);
        for (const path of out.created) created.add(path);
        for (const [path, code] of Object.entries(out.errors)) broken.set(path, code);
      }
      const planned: { file: File; parent: string; path: string }[] = [];
      const failed: { file: File; parent: string; path: string; failure: UploadFailure }[] = [];
      for (const { file, path } of collected.files) {
        const slash = path.lastIndexOf("/");
        const dir = slash < 0 ? "" : path.slice(0, slash);
        const parent = ids.get(dir);
        if (parent) {
          planned.push({ file, parent, path });
          continue;
        }
        // De bovenste map die niet lukte, voor de melding
        let top = dir;
        for (let d = dir; d; d = d.slice(0, Math.max(0, d.lastIndexOf("/")))) if (broken.has(d)) top = d;
        const code = broken.get(top) ?? "invalid_name";
        failed.push({ file, parent: folderId, path, failure: { code, message: code, folder: top } });
      }

      // 2. bezette namen, alleen in mappen die er al waren
      const existed = new Set<string>([folderId]);
      for (const [path, id] of ids) if (path && !created.has(path)) existed.add(id);
      const byParent = new Map<string, string[]>();
      for (const f of planned) {
        if (!existed.has(f.parent)) continue;
        const list = byParent.get(f.parent);
        if (list) list.push(f.file.name);
        else byParent.set(f.parent, [f.file.name]);
      }
      const taken = new Map<string, Set<string>>();
      const kinds = new Map<string, string>();
      for (const [parent, names] of byParent) {
        const unique = [...new Set(names)];
        for (let i = 0; i < unique.length; i += CHUNK) {
          for (const hit of await requests.namesCheck(parent, unique.slice(i, i + CHUNK))) {
            const key = nameKey(hit.name);
            let set = taken.get(parent);
            if (!set) taken.set(parent, (set = new Set()));
            set.add(key);
            kinds.set(`${parent}\u001f${key}`, hit.kind);
          }
        }
      }

      // 3. één vraag
      const clashes = findConflicts(planned, taken);
      const choices = new Map<number, ConflictChoice>();
      if (clashes.length) {
        const items: ConflictItem[] = clashes.map((i) => ({
          name: planned[i].file.name,
          path: planned[i].path,
          kind: kinds.get(`${planned[i].parent}\u001f${nameKey(planned[i].file.name)}`) ?? "file",
        }));
        const answer = await ask(items);
        if (answer === null) {
          this.addFailed(failed);
          return;
        }
        clashes.forEach((index, j) => choices.set(index, answer[j] ?? "skip"));
      }
      const { create } = planConflicts(planned, taken, (_f, i) => choices.get(i) ?? "skip");

      // 4. de uploads (eerst in de rij, dan pas niet meer "voorbereiden": de lade blijft staan).
      // Grootte en datum eerst in stukjes lezen: dat is per bestand een synchrone vraag aan de
      // browser, en 5.000 achter elkaar zou een lange taak zijn.
      const failedMeta = await readMeta(failed.map((f) => f.file));
      const createMeta = await readMeta(create.map((c) => c.file));
      this.addFailed(failed, failedMeta);
      const done = this.enqueue(
        create.map((c) => ({ file: c.file, parent: c.parent, path: c.path, conflict: c.conflict })),
        opts.onCreate,
        createMeta,
      );
      release();
      await done;
    } finally {
      release(); // ook als het mislukte of niemand antwoordde
    }
  }

  // --- bedienen ---

  /** Start of hervat één upload meteen (buiten de planner om). Doet niets als hij al loopt,
   * weg is of niet opnieuw kan. */
  async run(key: string): Promise<void> {
    const item = this.items.get(key);
    if (!item || this.running.has(key) || item.retry === false) return;
    if (item.state !== "waiting" && item.state !== "failed" && item.state !== "paused") return;
    await this.attempt(key);
  }

  /** Opnieuw proberen, op zijn beurt (via de planner). */
  retry(key: string): void {
    const item = this.items.get(key);
    if (!item || item.state !== "failed" || item.retry === false) return;
    this.change(key, { state: "waiting" });
    this.enqueueKey(key);
    this.pump();
  }

  /** Pauzeren: de upload stopt maar blijft op de server staan; hervatten gaat verder. */
  pause(key: string): void {
    const item = this.items.get(key);
    if (!item) return;
    if (item.state === "running") {
      this.change(key, { state: "paused" });
      this.controllers.get(key)?.abort(STOP);
    } else if (item.state === "waiting") {
      this.change(key, { state: "paused" });
    }
  }

  resume(key: string): void {
    const item = this.items.get(key);
    if (!item || item.state !== "paused") return;
    this.change(key, { state: "waiting" });
    this.enqueueKey(key);
    this.pump();
  }

  pauseAll(): void {
    let any = false;
    for (const [key, item] of this.items) {
      if (item.state === "running") {
        this.set(key, { state: "paused" });
        this.controllers.get(key)?.abort(STOP);
        any = true;
      } else if (item.state === "waiting") {
        this.set(key, { state: "paused" });
        any = true;
      }
    }
    if (any) this.changed();
  }

  resumeAll(): void {
    let any = false;
    for (const [key, item] of this.items) {
      if (item.state !== "paused") continue;
      this.set(key, { state: "waiting" });
      this.enqueueKey(key);
      any = true;
    }
    if (any) {
      this.changed();
      this.pump();
    }
  }

  /** Annuleren: de server gooit de upload weg. Loopt hij niet, dan meteen weggooien. */
  cancel(key: string): void {
    const item = this.items.get(key);
    if (!item) return;
    const controller = this.controllers.get(key);
    if (controller && !controller.signal.aborted) {
      controller.abort();
      return;
    }
    // Loopt hij nog af na pauzeren (ook als hij al hervat is), dan weggooien zoals gepauzeerd,
    // en wat die poging nog aanmaakt ook
    if (controller) this.cancelledAttempts.add(controller);
    if (item.state === "waiting" && !item.url) {
      this.drop(key, false); // nog niet begonnen: er staat niets op de server
      return;
    }
    void this.discard(key);
  }

  /** Alles annuleren wat nog niet klaar is. */
  cancelAll(): void {
    for (const key of [...this.items.keys()]) this.cancel(key);
  }

  /** Een mislukte of gepauzeerde upload weggooien, zodat de server de gereserveerde ruimte
   * vrijgeeft. */
  async discard(key: string): Promise<void> {
    const item = this.items.get(key);
    if (!item || (item.state !== "failed" && item.state !== "paused" && item.state !== "waiting")) return;
    if (item.retry === false) {
      this.drop(key, false); // er was nooit een upload
      return;
    }
    // Loopt hij nog af na pauzeren, dan eerst dat (en wat die poging nog aanmaakt, gaat ook weg)
    const controller = this.controllers.get(key);
    if (controller) {
      controller.abort(STOP);
      this.cancelledAttempts.add(controller);
    }
    this.change(key, { state: "discarding" });
    try {
      await discardUpload(this.api, item.parent, item.file, item.url, this.options.storage);
      this.drop(key, false);
    } catch (e) {
      const failure = { ...failureOf(e), discard: true };
      this.change(key, {
        state: "failed",
        error: `Weggooien lukte niet: ${(e as Error).message}`,
        failure,
      });
      this.settle(key);
    }
  }

  /** Alle mislukte uploads weggooien (de lade: "Mislukte weggooien"). */
  discardFailed(): void {
    for (const [key, item] of this.items) if (item.state === "failed") void this.discard(key);
  }

  /**
   * Bij uitloggen: alle uploads stoppen en de lijst leegmaken. Ze blijven op de server staan;
   * hetzelfde bestand opnieuw kiezen gaat later verder waar hij was.
   */
  stopAll(): void {
    for (const controller of this.controllers.values()) controller.abort(STOP);
    const keys = [...this.items.keys(), ...this.waiters.keys()];
    this.items.clear();
    this.meta.clear();
    this.byStorage.clear();
    this.creates.clear();
    this.counts = { waiting: 0, running: 0, paused: 0, failed: 0, discarding: 0 };
    this.resetTotals();
    this.changed();
    for (const key of keys) this.settle(key);
  }
}

/** De verzoeken van uploaden buiten tus om (mappen, namen, onderbroken uploads). */
export const uploadApi = uploadRequests(api);

export const uploadQueue = new UploadQueue(api, {
  concurrency: 3,
  requests: uploadApi,
  ask: conflicts.ask,
});

// De schil (App.tsx) kijkt via uploadStatus mee: zo hoort deze module niet bij de eerste lading.
// "Bezig" is ook: een vraag over dubbele bestanden, of onderbroken uploads om te tonen.
uploadStatus.attach({
  subscribe: (listener) => {
    const offs = [uploadQueue.subscribe(listener), conflicts.subscribe(listener), interrupted.subscribe(listener)];
    return () => {
      for (const off of offs) off();
    };
  },
  busy: () => uploadQueue.busy() || conflicts.pending() || interrupted.getSnapshot().length > 0,
  stopAll: () => {
    uploadQueue.stopAll();
    conflicts.cancelAll();
    interrupted.clear();
  },
});

/** Kijkt (één keer per keer dat het nodig is) of er onderbroken uploads op de server liggen. */
export function checkInterrupted(): Promise<void> {
  return interrupted.check(uploadApi, (id) => uploadQueue.hasUrl(tusUrl(id)));
}

/** De uploads, voor een lijst op de pagina; ververst hooguit 4 keer per seconde. */
export function useUploads(): readonly UploadItem[] {
  return useSyncExternalStore(uploadQueue.subscribeSummary, uploadQueue.getSnapshot);
}

/** De totalen voor de lade (hooguit 4 keer per seconde). */
export function useUploadSummary(): UploadSummary {
  return useSyncExternalStore(uploadQueue.subscribeSummary, uploadQueue.getSummary);
}
