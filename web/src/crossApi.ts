// API-aanroepen die meerdere pakketten gebruiken, met een vast contract. trash, move, copy en
// archiveLink bouwt P2 (de endpoints), restore staat er al (P0, daarna P6), addToAlbum P5.
// Tot een endpoint er is, geeft de server 405 (de stub-router bestaat, maar zonder die methode)
// of 404 (geen route); de aanroep zelf blijft gelijk. Gebruik isNotBuilt(e) om dat te herkennen.
// Een mislukt item toon je met errorText(item) uit i18n: die vertaalt op code, met params.

import { ApiError, api, type FileNode } from "./api";

export interface ItemResult {
  id: string;
  ok: boolean;
  /** not_found, read_only, share_root, cross_owner, name_taken, into_itself, trashed, too_many,
   * album, not_own, not_media, already */
  code?: string;
  message?: string;
  /** Wat de tekst invult, bijv. {max} bij too_many of {owner} bij no_room_owner */
  params?: Record<string, string | number> | null;
  node?: FileNode;
}

/** Bestaat dit endpoint nog niet (een later pakket bouwt het)? 404, 405 of 501 zonder code. */
export function isNotBuilt(e: unknown): boolean {
  return e instanceof ApiError && !e.code && (e.status === 404 || e.status === 405 || e.status === 501);
}

export interface BatchResult {
  results: ItemResult[];
}

/** Hooguit zoveel ids per verzoek (zip-link, terugzetten, in de prullenbak). */
export const MAX_BATCH_IDS = 10_000;

export const crossApi = {
  /** Naar de prullenbak (P2: POST /api/v1/batch/trash). */
  trash(ids: string[]): Promise<BatchResult> {
    return api.request<BatchResult>("POST", "/api/v1/batch/trash", { ids });
  },
  /** Verplaatsen (P2). conflict "rename": een bezette naam krijgt "naam (1)". */
  move(ids: string[], parentId: string, conflict?: "fail" | "rename"): Promise<BatchResult> {
    return api.request<BatchResult>("POST", "/api/v1/batch/move", {
      ids,
      parent_id: parentId,
      ...(conflict ? { conflict } : {}),
    });
  },
  /** Kopiëren (P2); ItemResult.node is de kopie. */
  copy(ids: string[], parentId: string): Promise<BatchResult> {
    return api.request<BatchResult>("POST", "/api/v1/batch/copy", { ids, parent_id: parentId });
  },
  /** Een downloadlink voor een zip van deze mappen en bestanden (P2; ≤ 10.000 ids). */
  archiveLink(ids: string[], name: string): Promise<{ url: string; expires_in: number }> {
    return api.request<{ url: string; expires_in: number }>("POST", "/api/v1/archive/link", { ids, name });
  },
  /** Terugzetten uit de prullenbak (P0: POST /api/v1/trash/restore; ≤ 10.000 ids). Eén
   * resultaat per verschillend id, in de volgorde waarin het voor het eerst voorkomt. */
  restore(ids: string[]): Promise<BatchResult> {
    return api.request<BatchResult>("POST", "/api/v1/trash/restore", { ids });
  },
  /** Eigen foto's en video's aan een album toevoegen (P5; ≤ 1000 ids). */
  addToAlbum(albumId: string, nodeIds: string[]): Promise<{ added: number; skipped: ItemResult[] }> {
    return api.request<{ added: number; skipped: ItemResult[] }>("POST", `/api/v1/albums/${albumId}/items`, {
      node_ids: nodeIds,
    });
  },
};
