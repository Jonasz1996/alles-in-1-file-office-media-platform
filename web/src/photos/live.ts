// Live bijwerken van de tijdlijn en een album als een thumbnail klaar is (preview.ready): alleen
// de adressen van die foto's opnieuw vragen (POST /previews, hooguit 500 per keer) en in de
// geladen items zetten, in plaats van de maanden en alle zichtbare secties opnieuw te lezen.
// Veel preview.ready vlak na elkaar (een import) geven één verzoek per PATCH_MS. Een
// preview.ready met "dated" (de opnamedatum kwam net uit de foto, de volgorde kan veranderen)
// laat de pagina zelf opnieuw laden.

import { useEffect } from "react";
import { api, type Preview } from "../api";
import { liveEvents, type ServerEvent } from "../events";
import type { PhotoItem } from "./api";
import type { SectionStore } from "./store";

export const PATCH_MS = 500;
/** Hooguit zoveel ids per POST /previews (de server). */
const BATCH = 500;

export type FetchPreviews = (ids: string[]) => Promise<Record<string, Preview>>;

/** De velden van een item die uit een preview komen. */
export function fromPreview(p: Preview): Partial<PhotoItem> {
  const out: Partial<PhotoItem> = { thumb: p.thumb, large: p.large };
  const { width, height, duration } = p.meta ?? {};
  if (typeof width === "number") out.width = width;
  if (typeof height === "number") out.height = height;
  if (typeof duration === "number") out.duration = duration;
  return out;
}

/** Ids van een preview.ready (node_ids). */
function readyIds(event: ServerEvent): string[] {
  const raw = event.data.node_ids;
  return Array.isArray(raw) ? raw.map(String) : [];
}

/** Moet de pagina na deze gebeurtenis opnieuw laden (de volgorde kan veranderd zijn)? */
export function reorders(event: ServerEvent): boolean {
  return event.type === "preview.ready" && event.data.dated === true;
}

export class ThumbPatcher {
  private readonly ids = new Set<string>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;

  constructor(
    private readonly store: SectionStore,
    private readonly fetch: FetchPreviews = (ids) => api.previews(ids),
    private readonly delay = PATCH_MS,
  ) {}

  onEvent(event: ServerEvent): void {
    if (event.type !== "preview.ready" || reorders(event)) return;
    // Pas bij flush kijken wat geladen is: een maand die net opnieuw geladen wordt (een
    // nieuwe upload), heeft de foto dan meestal al
    for (const id of readyIds(event)) this.ids.add(id);
    if (this.ids.size === 0 || this.timer !== null || this.stopped) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, this.delay);
  }

  /** Vraagt de previews van wat er wacht en zet ze in de store. Mislukt dat, dan blijven de
   * plaatshouders staan (de volgende preview.ready of een herlaadbeurt brengt ze alsnog). */
  async flush(): Promise<void> {
    const ids = [...this.ids].filter((id) => this.store.indexOf(id) >= 0);
    this.ids.clear();
    for (let i = 0; i < ids.length && !this.stopped; i += BATCH) {
      let got: Record<string, Preview>;
      try {
        got = await this.fetch(ids.slice(i, i + BATCH));
      } catch {
        return;
      }
      if (this.stopped) return;
      const changes = new Map<string, Partial<PhotoItem>>();
      for (const [id, p] of Object.entries(got)) changes.set(id, fromPreview(p));
      this.store.patch(changes);
    }
  }

  stop(): void {
    this.stopped = true;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }
}

/** Zet thumbnails die klaar komen in de geladen items van deze store (zie ThumbPatcher). */
export function useThumbPatches(store: SectionStore): void {
  useEffect(() => {
    const patcher = new ThumbPatcher(store);
    const off = liveEvents.subscribe((e) => patcher.onEvent(e));
    return () => {
      off();
      patcher.stop();
    };
  }, [store]);
}
