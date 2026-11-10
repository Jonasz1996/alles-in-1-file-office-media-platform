// De aanroepen van de tab "Zoeken en foto's" op /beheer (fase 6, bouwontwerp §3.4 en Keuze 51):
// de eindpunten liggen vast na Z0; tot Z7 ze vult, geven ze 501 (de stub van Z0). Alleen voor
// beheerders.
//
// §3.4 noemt de velden van GET /admin/search niet één voor één. AdminSearch hieronder is het
// voorstel van Z0-web, uit Keuze 51; Z7 legt het vast samen met app/search/admin.py.
//
// Z0b (Keuze 55, 67, 80 en 82) zette de blokken van gezichten (X2) en zoeken op beeld (X3) erbij,
// elk null zolang zijn pakket er niet is, met hun schakelaars in de PATCH, "faces" en "beeld" bij
// herbouwen, en DELETE /admin/search/beeld (de beeldindex wissen). De vorm van de blokken is het
// voorstel van Z0b, uit de regels van Keuze 67 en 82; X2 en X3 leggen hem vast. Mislukt staat er
// alleen als aantal per code in (K2-21): nooit een foto, een gezicht of een gebruiker.

import { api } from "../api";
import type { AnalyseState, IndexProgress } from "../search/types";
import { wireQuery } from "../search/wire";

const API = "/api/v1";

/** Wat POST /admin/search/rebuild opnieuw opbouwt. faces: de mislukte foto's opnieuw en de versie
 * nalopen (X2); beeld: alles opnieuw berekenen (X3). */
export const REBUILD_WHAT = ["names", "text", "geo", "hash", "ocr", "faces", "beeld"] as const;
export type RebuildWhat = (typeof REBUILD_WHAT)[number];

/** Een herbouw die loopt, met zijn voortgang (0–1, ook via search.progress). */
export interface Rebuild extends IndexProgress {
  what: RebuildWhat;
  started_at: string;
}

/** Mislukt, als aantal per code (K2-21). */
export interface CodeCount {
  code: string;
  n: number;
}

/** Een model in de container analyse (models.lock, Keuze 58): de naam, de versie, en of de
 * sha256 van de geladen bytes klopt. */
export interface AnalyseModel {
  name: string;
  version: string;
  sha_ok: boolean;
}

/** Het blok "Gezichten" (X2, Keuze 67; voorstel). Nooit per gebruiker en nooit een gezicht. */
export interface AdminFaces {
  /** De schakelaar "Gezichtsherkenning toestaan" (standaard aan) */
  allowed: boolean;
  analyse: AnalyseState;
  /** Bij down de reden, bij models_missing de naam van het model; anders null */
  reason: string | null;
  models: AnalyseModel[];
  /** cpus en geheugen van de container; null: zonder grenzen (geen cgroup-controllers) */
  limits: { cpus: number; mem_mb: number } | null;
  /** Gebruikers met gezichten aan: alleen een aantal, geen namen */
  users_on: number;
  /** De rij analyse: wachtend, gedaan in het laatste uur, en waar hij op wacht */
  queue: { waiting: number; done_hour: number; waits_for: "transcodes" | "office" | "paused" | null };
  failed_by_code: CodeCount[];
  /** Gevonden gezichten, waarvan benoemd */
  faces: number;
  named: number;
  /** ±ruimte in de database (aantal × 0,8 kB) */
  db_bytes: number;
  /** De laatste uitkomst van app.cli analyse-bench, of null */
  bench: { at: string; photos_per_minute: number } | null;
  /** De geschatte tijd voor wat er wacht (seconden), of null */
  eta_s: number | null;
  /** "Wissen wacht op een lezer" */
  wipe_waiting: boolean;
  /** De laatste uitkomst van app.cli analyse-kwaliteit (Vraag 8); null: niet gemeten */
  quality: { at: string; ok: boolean } | null;
}

/** Het blok "Zoeken op beeld" (X3, Keuze 82; voorstel). */
export interface AdminBeeld {
  /** Aan of uit voor het hele platform */
  on: boolean;
  /** Het model: in orde, ontbreekt (install.sh haalt het bij de volgende run), of niet nagerekend
   * in CI (trust-on-first-use, Keuze 73) */
  model: "ok" | "missing" | "unverified";
  analyse: AnalyseState;
  /** Bekeken: zoveel van zoveel foto's */
  seen: number;
  photos: number;
  failed_by_code: CodeCount[];
  /** Het afgelopen uur: vragen, de p95 (null zonder vragen), en hoe vaak terug naar gewoon zoeken
   * (te traag, zonder container, zonder model) */
  hour: { queries: number; p95_ms: number | null; slow: number; down: number; no_model: number };
  /** Ruimte in bytes: in de database, in de cache en aan modellen */
  space: { db_bytes: number; cache_bytes: number; models_bytes: number };
  /** De uitkomst van app.cli beeld-bench (BZ6) en beeld-kwaliteit (BZ4), met datum, of null */
  bench: { at: string; p95_ms: number; photos_per_minute: number } | null;
  quality: { at: string; ok: boolean } | null;
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
  /** Het blok "Gezichten" (X2); null zolang X2 er niet is */
  faces: AdminFaces | null;
  /** Het blok "Zoeken op beeld" (X3); null zolang X3 er niet is */
  beeld: AdminBeeld | null;
  /** De laatste wekelijkse search-check (I1): wanneer, hoeveel nodes vergeleken en hoeveel
   * verschillen met de bron; null zolang er geen was */
  check: { at: string; sampled: number; bad: number } | null;
}

/** PATCH /admin/search {ocr?, paused?, faces_allowed?, beeld?}. faces_allowed false wist alle
 * gezichten van alle gebruikers (Keuze 64); beeld false zet zoeken op beeld uit voor iedereen,
 * de vectoren blijven tot wipeBeeld() (Keuze 82). */
export interface AdminSearchPatch {
  ocr?: boolean;
  paused?: boolean;
  faces_allowed?: boolean;
  beeld?: boolean;
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

/** DELETE /admin/search/beeld → 202: de beeldindex wissen (X3, Keuze 82): alle vectoren, de
 * bestanden in de cache en het geheugen van de container. */
export function wipeBeeld(): Promise<void> {
  return api.request<void>("DELETE", `${API}/admin/search/beeld`);
}
