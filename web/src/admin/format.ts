// Losse functies voor /beheer, zonder React (te testen): meldingen op code vertalen, de
// verdeling van een schijf in stukken, het quotumveld en labels voor taken.

import { fmt, type MessageKey, t } from "../i18n";
import type { HttpsDetails, Notice, StorageParts } from "./api";

const NOTICES = new Set([
  "db_unreachable",
  "storage_not_writable",
  "disk_full",
  "db_no_primary",
  "db_no_sync",
  "etcd_no_quorum",
  "storage_no_leader",
  "node_down",
  "jobs_stopped",
  "jobs_waiting",
  "https_error",
  "https_listener",
  "https_expiring",
]);

const HTTPS_ERRORS = new Set([
  "acme_dns",
  "acme_connection",
  "acme_unauthorized",
  "acme_rate_limited",
  "acme_rejected",
  "acme_error",
  "acme_unreachable",
  "unexpected",
  "listener_bind",
  "listener_stopped",
]);

/** De tekst bij een fout van het certificaat of de https-poort (code met params); zonder
 * bekende code de tekst van de server. */
export function httpsErrorText(code: string | null | undefined, params: Record<string, unknown>, text: string): string {
  if (!code || !HTTPS_ERRORS.has(code)) return text;
  const vars: Record<string, string | number> = { detail: "", reason: "", port: "" };
  for (const [k, v] of Object.entries(params)) if (typeof v === "string" || typeof v === "number") vars[k] = v;
  return t(`admin.https.errors.${code}` as MessageKey, vars);
}

/** Een melding van /admin/status in de taal van de app; een onbekende code houdt de tekst van
 * de server. */
export function noticeText(n: Notice): string {
  if (!NOTICES.has(n.code)) return n.text;
  if (n.code === "https_error" || n.code === "https_listener") {
    const reason = httpsErrorText(String(n.params.error ?? ""), n.params, n.text);
    return t(`admin.notice.${n.code}` as MessageKey, { reason });
  }
  return t(`admin.notice.${n.code}` as MessageKey, n.params);
}

/** De fout van het certificaat en van de https-poort, vertaald. */
export function httpsErrors(s: HttpsDetails): { error: string | null; listener: string | null } {
  return {
    error: s.error ? httpsErrorText(s.error_code, s.error_params ?? {}, s.error) : null,
    listener: s.listener_error
      ? httpsErrorText(s.listener_error_code, s.listener_error_params ?? {}, s.listener_error)
      : null,
  };
}

export type PartKey = keyof StorageParts | "free";

export interface Segment {
  key: PartKey;
  bytes: number;
  /** Breedte in procenten (0–100), op één decimaal; samen nooit meer dan 100 */
  percent: number;
}

const ORDER: PartKey[] = ["blobs", "previews", "uploads", "database", "other", "free"];

/**
 * De stukken van een balk voor een schijf van total bytes. Lege stukken vallen weg; een stuk
 * dat er wel is maar heel klein is, krijgt minstens 0,5 % (anders zie je het niet), ten koste
 * van het grootste stuk. Samen nooit meer dan 100 %.
 */
export function segments(parts: StorageParts, total: number, free = 0): Segment[] {
  if (!(total > 0)) return [];
  const values: Record<PartKey, number> = { ...parts, free };
  const out: Segment[] = [];
  for (const key of ORDER) {
    const bytes = Math.max(0, values[key] ?? 0);
    if (bytes === 0) continue;
    out.push({ key, bytes, percent: Math.max(0.5, Math.round((bytes / total) * 1000) / 10) });
  }
  let sum = out.reduce((s, x) => s + x.percent, 0);
  while (sum > 100 + 1e-9 && out.length) {
    const biggest = out.reduce((a, b) => (b.percent > a.percent ? b : a));
    const cut = Math.min(sum - 100, biggest.percent - 0.5);
    if (cut <= 0) break;
    biggest.percent = Math.round((biggest.percent - cut) * 10) / 10;
    sum = out.reduce((s, x) => s + x.percent, 0);
  }
  return out;
}

const GB = 1024 ** 3;

/** Het quotumveld (in GB) als bytes. "" → null (bij een nieuwe gebruiker: de standaard van de
 * server); "0" → 0 (onbeperkt). Een komma mag ook ("1,5"). undefined als het geen getal is. */
export function parseQuota(text: string): number | null | undefined {
  const s = text.trim().replace(",", ".");
  if (s === "") return null;
  if (!/^\d+(\.\d+)?$/.test(s)) return undefined;
  return Math.round(Number(s) * GB);
}

/** Bytes als getal voor het quotumveld (in GB, hooguit twee decimalen): 0 → "0". */
export function quotaInput(bytes: number): string {
  if (!bytes) return "0";
  return String(Math.round((bytes / GB) * 100) / 100);
}

/** De soort taak voor in de tabel: bekende soorten vertaald, de rest zoals hij heet. */
export function jobTypeLabel(type: string): string {
  const key = type.replace(/\./g, "_");
  if (key === "preview" || key === "user_delete" || key === "photometa") return t(`admin.jobs.types.${key}` as MessageKey);
  return type;
}

/** Waar de taak over gaat, kort: bij een preview de eerste tekens van de inhoud (sha). */
export function jobSubject(key: string | null): string {
  if (!key) return "";
  const i = key.lastIndexOf(":");
  const rest = i >= 0 ? key.slice(i + 1) : key;
  return rest.length > 12 ? `${rest.slice(0, 12)}…` : rest;
}

/** "3 minuten geleden" vanaf een ISO-tijd. */
export function ago(iso: string, now = Date.now()): string {
  return fmt.relative(iso, now);
}

/** Gebruikers filteren op naam of e-mailadres (zonder hoofdletters of accenten). */
export function matchesUser(u: { display_name: string; email: string }, q: string, fold: (s: string) => string): boolean {
  const needle = fold(q.trim());
  if (!needle) return true;
  return fold(u.display_name).includes(needle) || fold(u.email).includes(needle);
}
