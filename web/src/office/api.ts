// De aanroepen van office (fase 4, bouwontwerp §2), frozen na O0. Losse functies via api.request
// uit ../api, zodat een chunk alleen meeneemt wat het gebruikt (de route Bestanden alleen de
// status). Plus de status van office in het geheugen: de haken (Bewerken, PDF, Nieuw, het
// tabblad Office) lezen die en zijn er alleen als de server zegt dat office er is. Zonder
// office-instellingen, bij een oudere server of bij een fout: niets, precies zoals fase 3.

import { api, type FileNode } from "../api";
import type {
  CheckpointOut,
  ConvertOut,
  NewDocumentIn,
  OfficeAdmin,
  OfficeAdminIn,
  OfficeFile,
  OfficeStatus,
  PdfLink,
  RetryOut,
  SessionIn,
  SessionOut,
} from "./types";

const BASE = "/api/v1/office";
const ADMIN = "/api/v1/admin/office";

function file(id: string, rest = ""): string {
  return `${BASE}/files/${encodeURIComponent(id)}${rest}`;
}

// --- O2: bewerken ---

/** GET /office/status: staat office aan, en kan er nu bewerkt worden? */
export function getStatus(): Promise<OfficeStatus> {
  return api.request<OfficeStatus>("GET", `${BASE}/status`);
}

/** GET /office/files/{id}: kan dit bestand nu de editor in, en hoe staat de PDF ervoor? */
export function getFile(id: string): Promise<OfficeFile> {
  return api.request<OfficeFile>("GET", file(id));
}

/** POST /office/files/{id}/session: de editor openen (zet ook het cookie office). */
export function openSession(id: string, body: SessionIn = {}): Promise<SessionOut> {
  return api.request<SessionOut>("POST", file(id, "/session"), body);
}

/** POST /office/files/{id}/save: nu tussentijds opslaan (202). */
export function saveNow(id: string): Promise<void> {
  return api.request<void>("POST", file(id, "/save"));
}

/** POST /office/gate: het cookie office vernieuwen (204). */
export function renewGate(): Promise<void> {
  return api.request<void>("POST", `${BASE}/gate`);
}

// --- O3: voorbeelden en PDF ---

/** POST /office/files/{id}/convert: een voorbeeld met opmaak vragen. */
export function convert(id: string): Promise<ConvertOut> {
  return api.request<ConvertOut>("POST", file(id, "/convert"));
}

/** POST /office/files/{id}/pdf: een downloadlink van de omgezette PDF. */
export function pdfLink(id: string): Promise<PdfLink> {
  return api.request<PdfLink>("POST", file(id, "/pdf"));
}

// --- O4: nieuw document ---

/** POST /office/new: een leeg docx, xlsx of pptx in een map. */
export function newDocument(body: NewDocumentIn): Promise<FileNode> {
  return api.request<FileNode>("POST", `${BASE}/new`, body);
}

// --- O5: beheer ---

export function getAdmin(): Promise<OfficeAdmin> {
  return api.request<OfficeAdmin>("GET", ADMIN);
}

export function putAdmin(body: OfficeAdminIn): Promise<OfficeAdmin> {
  return api.request<OfficeAdmin>("PUT", ADMIN, body);
}

export function checkpointAll(): Promise<CheckpointOut> {
  return api.request<CheckpointOut>("POST", `${ADMIN}/checkpoint`);
}

export function retryConversions(): Promise<RetryOut> {
  return api.request<RetryOut>("POST", `${ADMIN}/conversions/retry`);
}

export function restoreUnsaved(key: string): Promise<FileNode> {
  return api.request<FileNode>("POST", `${ADMIN}/unsaved/${encodeURIComponent(key)}/restore`);
}

export function deleteUnsaved(key: string): Promise<void> {
  return api.request<void>("DELETE", `${ADMIN}/unsaved/${encodeURIComponent(key)}`);
}

// --- de status in het geheugen ---

/** Zo lang (ms) geldt een antwoord van /office/status; daarna vragen we het opnieuw. */
export const STATUS_TTL = 60_000;
/** Zo lang (ms) na een fout (geen verbinding, een oudere server): dan opnieuw proberen. */
export const STATUS_RETRY = 15_000;

/**
 * De status van office, één keer per STATUS_TTL gevraagd. peek() leest zonder iets te doen (voor
 * useSyncExternalStore); get() vraagt hem ook opnieuw als hij oud is (voor de when() van een
 * actie). Een fout geeft null: de haken zijn er dan niet. Zonder login vraagt hij niets.
 */
export class StatusStore {
  private value: OfficeStatus | null = null;
  private at = Number.NEGATIVE_INFINITY;
  private ok = false;
  private inflight: Promise<OfficeStatus | null> | null = null;
  private readonly listeners = new Set<() => void>();

  constructor(
    private readonly fetchStatus: () => Promise<OfficeStatus>,
    private readonly canFetch: () => boolean = () => true,
    private readonly now: () => number = Date.now,
  ) {}

  /** De laatste status (of null), zonder te vragen. */
  peek = (): OfficeStatus | null => this.value;

  /** De laatste status; is die oud, dan op de achtergrond opnieuw vragen. */
  get(): OfficeStatus | null {
    if (this.stale()) void this.load();
    return this.value;
  }

  stale(): boolean {
    return this.now() - this.at >= (this.ok ? STATUS_TTL : STATUS_RETRY);
  }

  /** Vraagt de status (twee keer tegelijk vragen is één verzoek); geeft hem, of null. */
  load(): Promise<OfficeStatus | null> {
    if (this.inflight) return this.inflight;
    if (!this.canFetch()) return Promise.resolve(this.value);
    const p = this.fetchStatus().then(
      (s) => {
        this.settle(s, true);
        return this.value;
      },
      () => {
        this.settle(null, false);
        return null;
      },
    );
    this.inflight = p;
    void p.finally(() => {
      if (this.inflight === p) this.inflight = null;
    });
    return p;
  }

  /** Een status van buitenaf (tests, of een pakket dat net hoorde dat DS weg is). */
  set(s: OfficeStatus | null): void {
    this.settle(s, s !== null);
  }

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  private settle(s: OfficeStatus | null, ok: boolean): void {
    this.at = this.now();
    this.ok = ok;
    // Hetzelfde antwoord: hetzelfde object (en niemand hoeft opnieuw te tekenen)
    if (JSON.stringify(s) === JSON.stringify(this.value)) return;
    this.value = s;
    for (const l of [...this.listeners]) l();
  }
}

/** De status voor de hele app. */
export const officeStatus = new StatusStore(getStatus, () => api.loggedIn);
