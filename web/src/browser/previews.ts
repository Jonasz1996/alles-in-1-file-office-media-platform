// Previews (thumbnails) voor wat zichtbaar is, en wat er zo meteen in beeld komt. Eén cache
// per tabblad: id → preview, per etag (andere inhoud = andere preview). Alleen wat ontbreekt,
// verouderd is of nog in de maak was, wordt gevraagd: hooguit 500 ids per POST, niet vaker dan
// eens per 100 ms (de eerste meteen), het laatst gevraagde eerst (waar de gebruiker nu kijkt).
// preview.ready en connected (na een onderbreking) verversen wat nodig is.

import { useEffect, useMemo, useRef, useSyncExternalStore } from "react";
import { api, type FileNode, type Preview } from "../api";
import { eventIds, liveEvents, type ServerEvent } from "../events";

export const PREVIEW_BATCH = 500;
export const PREVIEW_THROTTLE_MS = 100;
const MAX_IN_FLIGHT = 2;
/** De adressen zijn ondertekend en verlopen (6 tot 12 uur); na 5 uur opnieuw vragen. */
const STALE_MS = 5 * 3600 * 1000;

interface Entry {
  etag: string;
  preview: Preview | null;
  at: number;
}

export type FetchPreviews = (ids: string[]) => Promise<Record<string, Preview>>;

export class PreviewCache {
  private readonly entries = new Map<string, Entry>();
  /** id → etag, in volgorde van vragen (de laatste het eerst) */
  private readonly queue = new Map<string, string>();
  private readonly asking = new Set<string>();
  private readonly listeners = new Set<() => void>();
  private inFlight = 0;
  private last = -Infinity;
  private timer: ReturnType<typeof setTimeout> | null = null;
  version = 0;

  constructor(
    private readonly fetch: FetchPreviews = (ids) => api.previews(ids),
    private readonly now: () => number = () => Date.now(),
  ) {}

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  getVersion = (): number => this.version;

  /** De preview van id (voor deze inhoud), als die er al is. */
  get(id: string, etag?: string): Preview | undefined {
    const e = this.entries.get(id);
    if (!e || (etag !== undefined && e.etag !== etag)) return undefined;
    return e.preview ?? undefined;
  }

  private needs(node: FileNode): boolean {
    const e = this.entries.get(node.id);
    if (!e || e.etag !== node.etag) return true;
    if (this.now() - e.at > STALE_MS) return true;
    return false;
  }

  /** Deze nodes moeten een preview hebben (de eerste het belangrijkst). */
  want(nodes: readonly (FileNode | undefined)[]): void {
    let added = false;
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      if (!n || n.kind !== "file" || this.asking.has(n.id) || !this.needs(n)) continue;
      // Opnieuw achteraan: wat als laatste gevraagd is, gaat eerst
      this.queue.delete(n.id);
      this.queue.set(n.id, n.etag);
      added = true;
    }
    if (added) this.schedule();
  }

  /** Opnieuw vragen, ook als ze er al zijn (preview.ready). */
  refresh(ids: readonly string[]): void {
    let added = false;
    for (const id of ids) {
      const e = this.entries.get(id);
      if (!e || this.asking.has(id)) continue;
      this.queue.delete(id);
      this.queue.set(id, e.etag);
      added = true;
    }
    if (added) this.schedule();
  }

  /** Wat nog in de maak was (na een onderbreking van de verbinding). */
  refreshPending(): void {
    this.refresh([...this.entries].filter(([, e]) => !e.preview || e.preview.state === "pending").map(([id]) => id));
  }

  private schedule(): void {
    if (this.timer !== null || this.queue.size === 0) return;
    const wait = Math.max(0, this.last + PREVIEW_THROTTLE_MS - this.now());
    if (wait === 0 && this.inFlight < MAX_IN_FLIGHT) {
      this.send();
      return;
    }
    this.timer = setTimeout(() => {
      this.timer = null;
      if (this.inFlight < MAX_IN_FLIGHT) this.send();
      else this.schedule();
    }, Math.max(wait, 16));
  }

  private send(): void {
    if (this.queue.size === 0) return;
    this.last = this.now();
    const batch = [...this.queue].slice(-PREVIEW_BATCH).reverse();
    for (const [id] of batch) {
      this.queue.delete(id);
      this.asking.add(id);
    }
    this.inFlight++;
    const ids = batch.map(([id]) => id);
    this.fetch(ids)
      .then(
        (got) => {
          const at = this.now();
          for (const [id, etag] of batch) this.entries.set(id, { etag, preview: got[id] ?? null, at });
          this.version++;
          for (const l of [...this.listeners]) l();
        },
        () => {
          /* geen previews: dan de iconen */
        },
      )
      .finally(() => {
        this.inFlight--;
        for (const id of ids) this.asking.delete(id);
        this.schedule();
      });
    if (this.queue.size) this.schedule();
  }

  onEvent(event: ServerEvent): void {
    if (event.type === "preview.ready") {
      const raw = event.data.node_ids;
      const ids = Array.isArray(raw) ? raw.map(String) : eventIds(event);
      this.refresh(ids);
    } else if (event.type === "connected") {
      this.refreshPending();
    }
  }
}

export const previewCache = new PreviewCache();

let listening = 0;
let stopListening: (() => void) | null = null;

function useCacheEvents(): void {
  useEffect(() => {
    if (listening++ === 0) stopListening = liveEvents.subscribe((e) => previewCache.onEvent(e));
    return () => {
      if (--listening === 0) {
        stopListening?.();
        stopListening = null;
      }
    };
  }, []);
}

/** Telt op als er nieuwe previews binnen zijn. */
export function usePreviewVersion(): number {
  useCacheEvents();
  return useSyncExternalStore(previewCache.subscribe, previewCache.getVersion, previewCache.getVersion);
}

/**
 * Previews voor deze nodes (wat zichtbaar is eerst, dan wat eraan komt). key: verandert als de
 * set verandert (bijv. het zichtbare bereik); de nodes zelf mogen elke render nieuw zijn.
 */
export function useVisiblePreviews(nodes: readonly (FileNode | undefined)[], key: string): number {
  const version = usePreviewVersion();
  // Op key: de nodes zelf mogen elke render een nieuwe lijst zijn
  useEffect(() => {
    previewCache.want(nodes);
  }, [key]);
  return version;
}

/** Previews worden per blok van zoveel plekken gevraagd (één POST per blok, niet per frame). */
export const PREVIEW_BLOCK = 200;

/**
 * Welk deel van een lijst previews moet hebben als [start, end) zichtbaar is: het blok waar
 * start in valt en minstens het volgende (scrollen gaat meestal door). Verandert alleen als je
 * een blokgrens passeert, zodat snel scrollen een handvol verzoeken geeft in plaats van één per
 * frame.
 */
export function previewWindow(start: number, end: number, count: number, block = PREVIEW_BLOCK): { from: number; to: number } {
  const from = Math.max(0, Math.floor(start / block) * block);
  const to = Math.min(count, Math.max(from + 2 * block, Math.ceil(end / block) * block));
  return { from, to };
}

/**
 * Previews voor een gevirtualiseerde lijst: get(i) geeft de node op plek i, [start, end) is
 * zichtbaar. Vraagt per blok (previewWindow), het zichtbare deel eerst. version: de lijst
 * veranderde (nieuwe pagina, live wijziging).
 */
export function useWindowPreviews(
  get: (i: number) => FileNode | undefined,
  start: number,
  end: number,
  count: number,
  version: number,
): number {
  const previews = usePreviewVersion();
  const { from, to } = previewWindow(start, end, count);
  const latest = { get, start, end };
  const ref = useRef(latest);
  ref.current = latest;
  useEffect(() => {
    const { get: at, start: s, end: e } = ref.current;
    const nodes: (FileNode | undefined)[] = [];
    const a = Math.max(from, Math.min(s, to));
    const b = Math.max(a, Math.min(e, to));
    for (let i = a; i < b; i++) nodes.push(at(i));
    for (let i = b; i < to; i++) nodes.push(at(i));
    for (let i = from; i < a; i++) nodes.push(at(i));
    previewCache.want(nodes);
  }, [from, to, version]);
  return previews;
}

/** Compat (SharedWithMe, Public): previews van een lijst, als object id → preview. */
export function usePreviewRecord(nodes: readonly FileNode[] | undefined): Record<string, Preview> {
  const list = nodes ?? [];
  const key = list.map((n) => `${n.id}:${n.etag}`).join(",");
  const version = useVisiblePreviews(list, key);
  // Op key en version: die staan voor de inhoud
  return useMemo(() => {
    const out: Record<string, Preview> = {};
    for (const n of list) {
      const p = previewCache.get(n.id, n.etag);
      if (p) out[n.id] = p;
    }
    return out;
  }, [key, version]);
}
