// De aanroepen van de bestandsbrowser die api.ts (frozen) nog niet heeft: een map in pagina's,
// nodes opnieuw opvragen (live bijwerken) en het doel van een zip-download.

import { api, type FileNode, type Listing } from "../api";

/** Een pagina van een map: Listing plus de cursor voor de volgende en het totaal. */
export interface Page extends Listing {
  /** Voor ?after= van de volgende pagina; null: dit was de laatste */
  next: string | null;
  /** Hoeveel kinderen er in totaal zijn (met kind: alleen van dat soort) */
  total: number;
}

export interface PageOptions {
  limit?: number;
  after?: string | null;
  kind?: "dir" | "file";
  signal?: AbortSignal;
}

/** De eerste pagina is klein (snel iets op het scherm), de rest groter. */
export const FIRST_PAGE = 200;
export const NEXT_PAGE = 1000;
/** Hooguit zoveel ids per POST /files/lookup */
export const LOOKUP_MAX = 500;

export function pagePath(folderId: string | null, o: Omit<PageOptions, "signal"> = {}): string {
  const params = new URLSearchParams();
  if (o.limit !== undefined) params.set("limit", String(o.limit));
  if (o.after) params.set("after", o.after);
  if (o.kind) params.set("kind", o.kind);
  const qs = params.toString();
  const base = folderId ? `/api/v1/files/${folderId}/children` : "/api/v1/files/root";
  return qs ? `${base}?${qs}` : base;
}

export const browserApi = {
  /** Een pagina van een map (folderId null: de eigen hoofdmap). */
  page(folderId: string | null, o: PageOptions = {}): Promise<Page> {
    return api.request<Page>("GET", pagePath(folderId, o), undefined, { signal: o.signal });
  },
  /** Deze nodes zoals je ze nu ziet; wat weg is, in de prullenbak ligt of onzichtbaar werd,
   * ontbreekt. Hooguit LOOKUP_MAX ids. */
  async lookup(ids: string[]): Promise<FileNode[]> {
    return (await api.request<{ nodes: FileNode[] }>("POST", "/api/v1/files/lookup", { ids })).nodes;
  },
};
