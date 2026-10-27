// De contracten van office (fase 4, bouwontwerp §2 en §3.4), frozen na O0: precies wat de API
// geeft en neemt (api/app/routers/office_contracts.py). Typen, plus twee vaste lijsten (de
// redenen en de namen van de gebeurtenissen). Wijzigen gaat via de hotfix-lane van O0, samen met
// de Python-kant.
//
// De foutcodes staan in i18n/errors/office.ts (office_off, office_unavailable, office_format,
// office_too_large, office_unsafe, office_busy, office_too_many {max}, office_gate,
// office_token, office_convert_failed {code}, pdf_pending, office_owner_gone,
// office_misconfigured); de bestaande codes (not_found, read_only, trashed, not_a_file, album,
// no_room, folder_not_found, guest, rate_limited) blijven waar ze staan.

import type { FileNode, Permission } from "../api";

/** Hoe het met de editor (DS) gaat; "off": niet ingesteld (precies fase 3). */
export type OfficeHealth = "off" | "starting" | "ok" | "down" | "misconfigured";

/** De staat van de omgezette PDF van een blob (Keuze 25); null: nog niets. */
export type PdfState = "pending" | "ready" | "failed" | "none" | "unsafe" | "ask";

/** Wat de editor bewerkt en wat "Nieuw" maakt (Keuze 23 en 24). */
export type OfficeKind = "docx" | "xlsx" | "pptx";

/** Waarom bewerken (nu) niet kan, per bestand (GET /office/files/{id}, en de haken). */
export type OfficeReason =
  | "read_only"
  | "format"
  | "too_large"
  | "unsafe"
  | "trashed"
  | "album"
  | "office_off"
  | "office_down"
  | "too_many";

/** Alle redenen, in vaste volgorde (voor de vertalingen en de tests). */
export const OFFICE_REASONS: readonly OfficeReason[] = [
  "read_only",
  "format",
  "too_large",
  "unsafe",
  "trashed",
  "album",
  "office_off",
  "office_down",
  "too_many",
];

// --- O2: bewerken ---

/** GET /api/v1/office/status */
export interface OfficeStatus {
  /** Ingesteld en op /beheer aangezet */
  enabled: boolean;
  /** enabled, en DS is gezond: er kan nu bewerkt worden */
  editing: boolean;
  state: OfficeHealth;
  /** ["docx", "xlsx", "pptx"] */
  edit_formats: string[];
  /** ["docx", "xlsx", "pptx"] */
  new_kinds: OfficeKind[];
  /** 104857600: groter opent de editor niet */
  max_bytes: number;
}

/** GET /api/v1/office/files/{id} */
export interface OfficeFile {
  can_edit: boolean;
  reason: OfficeReason | null;
  /** Er loopt een sessie op dit bestand */
  open: { editors: number } | null;
  pdf_state: PdfState | null;
}

/** POST /api/v1/office/files/{id}/session (body) */
export interface SessionIn {
  type?: "desktop" | "mobile";
  theme?: "light" | "dark";
}

/** POST /api/v1/office/files/{id}/session → 200, plus het cookie office (Path=/office/).
 * Fouten: 404 not_found; 403 read_only | guest; 422 not_a_file | album | trashed |
 * office_format | office_too_large | office_unsafe; 409 office_busy; 429 office_too_many {max}
 * | rate_limited; 503 office_off | office_unavailable | office_misconfigured. */
export interface SessionOut {
  /** "/office/web-apps/apps/api/documents/api.js" */
  api_url: string;
  /** De editorconfig, getekend (config.token), zonder events */
  config: Record<string, unknown>;
  key: string;
  /** Het bestand veranderde buiten de editor terwijl de sessie liep */
  stale: boolean;
  /** 7200: zo lang geldt het cookie office */
  gate_seconds: number;
}

// POST /api/v1/office/files/{id}/save → 202 {} (404 als er geen open sessie is)
// POST /api/v1/office/gate → 204 + Set-Cookie (alleen als lid van een open sessie; anders 404)

// --- O3: voorbeelden en PDF ---

/** POST /api/v1/office/files/{id}/convert → 202. Fouten: 404; 422 office_format |
 * office_unsafe; 413 office_too_large; 429 rate_limited; 503 office_unavailable. */
export interface ConvertOut {
  pdf_state: "pending" | "ready";
}

/** POST /api/v1/office/files/{id}/pdf. Fouten: 404; 422 office_format | office_unsafe; 409
 * pdf_pending; 429 rate_limited; 503 office_unavailable; 502 office_convert_failed {code}. */
export interface PdfLink {
  /** "/api/v1/office/pdf/{id}?token=…" (application/pdf, als bijlage) */
  url: string;
  /** 600 */
  expires_in: number;
}

// --- O4: nieuw document ---

/** POST /api/v1/office/new → 201 NodeOut. Fouten: 403 read_only; 404 folder_not_found; 413
 * no_room; 422 album | invalid_input. Werkt ook als DS uit staat. */
export interface NewDocumentIn {
  parent_id: string;
  kind: OfficeKind;
  /** Zonder extensie krijgt hij die erbij; bestaat hij al: "Nieuw document (2).docx" */
  name?: string;
}

// --- O5: beheer ---

export interface OfficePeak {
  connections: number;
  at: string;
}

export interface OfficeOpenDocument {
  name: string;
  owner: string;
  editors: string[];
  since: string;
  /** pending, open of saving; pending en saving houden de conversies tegen */
  state?: "pending" | "open" | "saving" | null;
}

export interface OfficeUnsaved {
  key: string;
  name: string;
  owner: string | null;
  at: string | null;
  reason: string | null;
}

export interface OfficeConversions {
  queued: number;
  failed: number;
  unsafe: number;
  ask: number;
}

/** GET en PUT /api/v1/admin/office */
export interface OfficeAdmin {
  configured: boolean;
  enabled: boolean;
  state: OfficeHealth;
  down_since: string | null;
  /** command version */
  version: string | null;
  /** command license; ≥ 2147483647 = geen limiet */
  license_connections: number | null;
  /** De controle zonder token (Keuze 28) */
  tokens_enforced: boolean | null;
  max_editors: number;
  /** De andere twee waarden van OfficeConfig, voor het formulier */
  checkpoint_minutes: number;
  version_minutes: number;
  connections: number;
  people: number;
  documents: number;
  /** Sinds de start van de app */
  peak: OfficePeak | null;
  open: OfficeOpenDocument[];
  unsaved: OfficeUnsaved[];
  conversions: OfficeConversions;
}

/** PUT /api/v1/admin/office (body) */
export interface OfficeAdminIn {
  enabled: boolean;
  /** 1–500 */
  max_editors: number;
  /** 1–60 */
  checkpoint_minutes: number;
  /** 5–1440 */
  version_minutes: number;
}

/** POST /api/v1/admin/office/checkpoint */
export interface CheckpointOut {
  sent: number;
  saved: number;
}

/** POST /api/v1/admin/office/conversions/retry */
export interface RetryOut {
  queued: number;
}

// POST /api/v1/admin/office/unsaved/{key}/restore → NodeOut (404 not_found; 409
//   office_owner_gone; 413 no_room)
// DELETE /api/v1/admin/office/unsaved/{key} → 204

// --- gebeurtenissen (SSE, na de commit; de ontvangers bepaalt de server bij het versturen) ---

export const OFFICE_EVENTS = {
  unsaved: "office.unsaved",
  recovered: "office.recovered",
  dropped: "office.dropped",
  presence: "office.presence",
} as const;

export type OfficeEventType = (typeof OFFICE_EVENTS)[keyof typeof OFFICE_EVENTS];

/** office.unsaved: naar de eigenaar en de deelnemers die nu nog schrijfrecht hebben. */
export interface UnsavedEvent {
  node_id: string | null;
  name: string;
  reason: "no_room" | "too_large" | "owner_gone" | "error_status";
  /** false: een tussenstand; de sessie loopt door */
  final: boolean;
}

/** office.recovered: naar wie de herstelkopie nu kan zien; nooit naar wie eruit gezet is. */
export interface RecoveredEvent {
  node_id: string | null;
  copy_id: string;
  name: string;
  reason: "status3" | "no_writer" | "forgotten";
}

/** office.dropped: alleen naar wie eruit gezet is, of (stale) wiens editor een oude key had.
 * Zonder naam. */
export interface DroppedEvent {
  node_id: string;
  key: string;
  reason: "access" | "stale";
}

/** office.presence: na elke status 1, naar de deelnemers die nu schrijfrecht hebben. */
export interface PresenceEvent {
  node_id: string;
  count: number;
}

export type OfficeEvent =
  | { type: "office.unsaved"; data: UnsavedEvent }
  | { type: "office.recovered"; data: RecoveredEvent }
  | { type: "office.dropped"; data: DroppedEvent }
  | { type: "office.presence"; data: PresenceEvent };

// --- de haken van O0 (Bewerken, Downloaden als PDF) ---

/**
 * Of een haak er is voor een bestand: verborgen (visible false), of zichtbaar; dan zegt reason
 * waarom hij nu niet kan (null: hij kan). office/actions.ts (O2) en office/pdf.ts (O3) bepalen
 * het; de haken in browser/actions.ts en viewer/Viewer.tsx vertalen de reden in hun eigen
 * woordenboek (blok office).
 */
export interface HookState {
  visible: boolean;
  reason: OfficeReason | null;
}

/** Wat een haak van een bestand weet: een FileNode, of een item van de viewer (dan zonder
 * kind en permission; zonder permission is het van jou, zoals overal). */
export interface OfficeTarget {
  id: string;
  name: string;
  mime: string | null;
  size: number;
  kind?: FileNode["kind"];
  permission?: Permission;
  deleted_at?: string | null;
  /** Een eerdere versie (de viewer bij Versies…): nooit te bewerken */
  version?: { id: string } | null;
}
