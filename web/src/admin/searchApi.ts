// De aanroepen van de tab "Zoeken en foto's" op /beheer (fase 6, bouwontwerp §3.4 en Keuze 51):
// de eindpunten liggen vast na Z0; tot Z7 ze vult, geven ze 501 (de stub van Z0). Alleen voor
// beheerders.
//
// §3.4 noemt de velden van GET /admin/search niet één voor één. AdminSearch hieronder is het
// voorstel van Z0-web, uit Keuze 51; Z7 legt het vast samen met app/search/admin.py.

import { api } from "../api";
import type { IndexProgress } from "../search/types";
import { wireQuery } from "../search/wire";

const API = "/api/v1";

/** Wat POST /admin/search/rebuild opnieuw opbouwt. */
export const REBUILD_WHAT = ["names", "text", "geo", "hash", "ocr"] as const;
export type RebuildWhat = (typeof REBUILD_WHAT)[number];

/** Een herbouw die loopt, met zijn voortgang (0–1, ook via search.progress). */
export interface Rebuild extends IndexProgress {
  what: RebuildWhat;
  started_at: string;
}

/** GET /admin/search: de stand van Keuze 51 (voorstel). */
export interface AdminSearch {
  engine: "fts" | "like";
  /** Achtergrondwerk staat op pauze */
  paused: boolean;
  index: {
    names_done: boolean;
    text_done: number;
    text_total: number;
    failed: number;
  };
  rebuild: Rebuild | null;
  /** Waar tekst gelezen wordt: in de zandbak (de mediaworker) of erbuiten */
  extract_in_sandbox: boolean;
  /** Per uur: de tijdsgrens geraakt in ronde B en C, en search_busy */
  per_hour: { deadline_b: number; deadline_c: number; busy: number };
  ocr: { installed: boolean; on: boolean; pages_queued: number };
  places: { loaded: boolean; source: string | null; date: string | null };
  /** De uitkomst van app.cli bench --los, of null */
  bench: { at: string; p95_owner_ms: number; p95_all_ms: number } | null;
}

/** PATCH /admin/search {ocr?, paused?} */
export interface AdminSearchPatch {
  ocr?: boolean;
  paused?: boolean;
}

/** Een mislukte stap: de inhoud (sha), de stap, de code en wanneer. */
export interface FailedItem {
  sha: string;
  step: "text" | "hash" | "ocr" | "geo";
  code: string;
  at: string;
}

/** GET /admin/search/failed?cursor= */
export interface FailedPage {
  items: FailedItem[];
  cursor: string | null;
}

/** GET /admin/search */
export function adminSearch(): Promise<AdminSearch> {
  return api.request<AdminSearch>("GET", `${API}/admin/search`);
}

/** PATCH /admin/search */
export function patchAdminSearch(patch: AdminSearchPatch): Promise<AdminSearch> {
  return api.request<AdminSearch>("PATCH", `${API}/admin/search`, patch);
}

/** POST /admin/search/rebuild {what} → 202; 409 search_rebuild_running. */
export function rebuildSearch(what: RebuildWhat): Promise<void> {
  return api.request<void>("POST", `${API}/admin/search/rebuild`, { what });
}

/** GET /admin/search/failed?cursor= */
export function failedSearch(cursor: string | null = null): Promise<FailedPage> {
  return api.request<FailedPage>("GET", `${API}/admin/search/failed${wireQuery({ cursor })}`);
}

/** POST /admin/search/retry: de mislukte opnieuw. */
export function retrySearch(): Promise<void> {
  return api.request<void>("POST", `${API}/admin/search/retry`);
}
