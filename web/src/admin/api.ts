// De API van /beheer (P6): gebruikers wijzigen en verwijderen, systeemstatus, opslag per node,
// achtergrondtaken en de bewaartermijn van de prullenbak. Via api.request uit ../api.

import { api, type HttpsStatus, type SystemStatus, type User } from "../api";

/** Wat een gebruiker opslaat; samen is het used_bytes. */
export interface UserUsage {
  files_bytes: number;
  trash_bytes: number;
  versions_bytes: number;
  file_count: number;
}

/** Een gebruiker zoals GET /users hem geeft (P6: met gebruik en of hij verwijderd wordt). */
export interface AdminUser extends User {
  disabled: boolean;
  created_at?: string | null;
  usage?: UserUsage | null;
  deleting?: boolean;
}

export interface NewUser {
  email: string;
  display_name: string;
  password: string;
  role: User["role"];
  quota_bytes: number | null;
}

export interface UserChange {
  display_name?: string;
  email?: string;
  role?: User["role"];
  /** 0 = onbeperkt */
  quota_bytes?: number;
  disabled?: boolean;
}

/** Een melding op /beheer: de web-app vertaalt op code; text is de Nederlandse tekst. */
export interface Notice {
  code: string;
  params: Record<string, string | number>;
  text: string;
}

export interface SystemInfo {
  version: string;
  mode: string;
  started_at: string | null;
  uptime_s: number | null;
  db_kind: "sqlite" | "postgresql";
  db_bytes: number;
  jobs: {
    queued: number;
    running: number;
    failed: number;
    oldest_queued_at: string | null;
    runner_alive: boolean;
  };
  https: { state: HttpsStatus["state"] | null; expires_at: string | null; days_left: number | null };
}

export interface AdminStatus extends SystemStatus {
  notices?: Notice[];
  system?: SystemInfo | null;
}

export interface HttpsDetails extends HttpsStatus {
  error_code?: string | null;
  error_params?: Record<string, string>;
  listener_error_code?: string | null;
  listener_error_params?: Record<string, string | number>;
}

export interface StorageParts {
  blobs: number;
  previews: number;
  uploads: number;
  database: number;
  other: number;
  /** <data_dir>/media (fase 5, integratie punt 4; additief): alleen met media aan */
  media?: number | null;
}

export interface NodeStorage {
  name: string;
  disk: { total_bytes: number; used_bytes: number; free_bytes: number } | null;
  parts: StorageParts;
  measured_at: string;
}

export interface StorageTotals {
  logical_bytes: number;
  stored_bytes: number;
  versions_bytes: number;
  trash_bytes: number;
  previews_bytes: number;
  uploads_bytes: number;
  uploads_reserved_bytes: number;
  db_bytes: number;
  replicas: number;
}

export interface StorageUser {
  id: string;
  display_name: string;
  quota_bytes: number;
  files_bytes: number;
  trash_bytes: number;
  versions_bytes: number;
  file_count: number;
}

export interface StorageOverview {
  nodes: NodeStorage[];
  totals: StorageTotals;
  users: StorageUser[];
}

export interface FailedJob {
  id: string;
  type: string;
  key: string | null;
  attempts: number;
  error: string | null;
  finished_at: string | null;
}

export interface RunningJob {
  id: string;
  type: string;
  key: string | null;
  attempts: number;
  started_at: string | null;
}

/** Foto's en video's naar de toestand van hun preview (/admin/jobs, previews.photo_counts). */
export interface PhotoIndex {
  total: number;
  ready: number;
  /** Alleen de thumbnail; de grote maat komt nog (de tweede ronde) */
  partial: number;
  /** Alleen de thumbnail; de tweede ronde mislukte ("Alles nalopen" probeert opnieuw) */
  large_failed: number;
  waiting: number;
  failed: number;
  /** Geen preview mogelijk (onleesbaar, of een soort zonder) */
  none: number;
  /** Een video die wacht tot de mediaworker ffmpeg heeft */
  later: number;
}

export interface JobsPage {
  counts: { queued: number; running: number; done: number; failed: number };
  /** Ontbreekt bij een server van vóór deze versie */
  photos?: PhotoIndex;
  by_type: { type: string; status: string; count: number }[];
  running: RunningJob[];
  oldest_queued_at: string | null;
  failed: FailedJob[];
  next_before: string | null;
}

export const adminApi = {
  status: () => api.request<AdminStatus>("GET", "/api/v1/admin/status"),
  users: () => api.request<AdminUser[]>("GET", "/api/v1/users"),
  /** quota_bytes null = de standaard van de server (api.addUser kent alleen een getal) */
  createUser: (u: NewUser) => api.request<AdminUser>("POST", "/api/v1/users", u),
  changeUser: (id: string, change: UserChange) => api.request<AdminUser>("PATCH", `/api/v1/users/${id}`, change),
  /** Verwijdert een geblokkeerde gebruiker op de achtergrond; geeft de taak. */
  deleteUser: (id: string) => api.request<{ job_id: string }>("DELETE", `/api/v1/users/${id}`),
  https: () => api.request<HttpsDetails>("GET", "/api/v1/admin/https"),
  storage: () => api.request<StorageOverview>("GET", "/api/v1/admin/storage"),
  jobs: (before?: string | null) =>
    api.request<JobsPage>("GET", `/api/v1/admin/jobs${before ? `?${new URLSearchParams({ before })}` : ""}`),
  deleteJob: (id: string) => api.request<void>("DELETE", `/api/v1/admin/jobs/${id}`),
  /** Alles nalopen: elke foto zonder (complete) preview opnieuw in de rij. started false: dat
   * liep al (of stond al klaar). */
  recheckPreviews: () => api.request<{ started: boolean }>("POST", "/api/v1/admin/jobs/recheck-previews"),
  trashSettings: () => api.request<{ retention_days: number }>("GET", "/api/v1/admin/trash"),
  setTrashSettings: (retention_days: number) =>
    api.request<{ retention_days: number }>("PUT", "/api/v1/admin/trash", { retention_days }),
};
