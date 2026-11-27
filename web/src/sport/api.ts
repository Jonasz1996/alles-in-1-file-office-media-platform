// De aanroepen van Sport (app/routers/sport.py): de Strava-vervanger in het platform.

import { api } from "../api";

const API = "/api/v1/sport";

export type SportKind = "run" | "ride" | "walk" | "hike" | "swim" | "ski" | "row" | "other";
export const SPORTS: readonly SportKind[] = ["run", "ride", "walk", "hike", "swim", "ski", "row", "other"];

export interface ImportOutcome {
  name: string;
  status: "new" | "duplicate" | "error";
  id: string | null;
  reason: string | null;
}

export interface ActivityItem {
  id: string;
  name: string;
  sport: SportKind;
  start_at: string | null;
  day: string | null;
  distance: number;
  moving: number;
  elapsed: number;
  gain: number;
  avg_hr: number | null;
  /** "x,y x,y ..." op 0–1000 */
  preview: string;
}

export interface ActivityPage {
  items: ActivityItem[];
  cursor: string | null;
}

export interface Series {
  lat: number[];
  lon: number[];
  d: number[];
  t: (number | null)[] | null;
  ele: (number | null)[] | null;
  ele_raw: (number | null)[] | null;
  hr: (number | null)[] | null;
  cad: (number | null)[] | null;
  power: (number | null)[] | null;
  speed: (number | null)[] | null;
}

export interface Split {
  km: number;
  distance: number;
  seconds: number;
  elev: number;
  hr: number | null;
}

export interface Activity {
  id: string;
  name: string;
  sport: SportKind;
  start_at: string | null;
  day: string | null;
  notes: string;
  distance: number;
  elapsed: number;
  moving: number;
  gain: number;
  loss: number;
  ele_min: number | null;
  ele_max: number | null;
  max_speed: number;
  avg_hr: number | null;
  max_hr: number | null;
  avg_cad: number | null;
  avg_power: number | null;
  points: number;
  fixed: number;
  ele_filter: boolean;
  /** zuid, west, noord, oost */
  bbox: [number, number, number, number];
  efforts: Record<string, number>;
  source_name: string;
  source_fmt: string;
  series: Series;
  splits: Split[];
}

export interface ActivityPatch {
  name?: string;
  sport?: SportKind;
  notes?: string;
  ele_filter?: boolean;
}

export interface Totals {
  key: string;
  count: number;
  distance: number;
  moving: number;
  gain: number;
}

export interface Stats {
  year: number;
  years: number[];
  months: Totals[];
  weeks: Totals[];
  per_year: Totals[];
  per_sport: Totals[];
  all_time: Totals;
}

export interface RecordRow {
  distance: number;
  best: { id: string; name: string; day: string | null; seconds: number }[];
}

export interface Records {
  sport: SportKind;
  rows: RecordRow[];
}

export interface SportConfig {
  tiles: boolean;
  attribution: string | null;
  can_edit: boolean;
  tiles_url: string | null;
}

function query(params: Record<string, string | number | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** Eén bestand (GPX, TCX, .gz of een zip) als ruwe body. */
export async function importFile(file: File): Promise<ImportOutcome[]> {
  const res = await api.rawRequest(`${API}/import`, {
    method: "POST",
    body: file,
    headers: { "Content-Type": "application/octet-stream", "X-File-Name": encodeURIComponent(file.name) },
  });
  return ((await res.json()) as { results: ImportOutcome[] }).results;
}

export async function importFiles(ids: readonly string[]): Promise<ImportOutcome[]> {
  const r = await api.request<{ results: ImportOutcome[] }>("POST", `${API}/import-files`, { ids });
  return r.results;
}

export function activities(p: {
  sport?: SportKind | null;
  year?: number | null;
  q?: string | null;
  cursor?: string | null;
}): Promise<ActivityPage> {
  return api.request<ActivityPage>("GET", `${API}/activities${query({ ...p, limit: 50 })}`);
}

export function activity(id: string): Promise<Activity> {
  return api.request<Activity>("GET", `${API}/activities/${encodeURIComponent(id)}`);
}

export function patchActivity(id: string, body: ActivityPatch): Promise<Activity> {
  return api.request<Activity>("PATCH", `${API}/activities/${encodeURIComponent(id)}`, body);
}

export function deleteActivity(id: string): Promise<void> {
  return api.request<void>("DELETE", `${API}/activities/${encodeURIComponent(id)}`);
}

/** Het originele bestand ophalen en als download aanbieden. */
export async function downloadSource(a: Pick<Activity, "id" | "source_name">): Promise<void> {
  const res = await api.rawRequest(`${API}/activities/${encodeURIComponent(a.id)}/file`);
  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = a.source_name.replace(/\.gz$/i, "") || "activiteit.gpx";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export function stats(year?: number | null, sport?: SportKind | null): Promise<Stats> {
  return api.request<Stats>("GET", `${API}/stats${query({ year, sport })}`);
}

export function records(sport: SportKind): Promise<Records> {
  return api.request<Records>("GET", `${API}/records${query({ sport })}`);
}

export function sportConfig(): Promise<SportConfig> {
  return api.request<SportConfig>("GET", `${API}/config`);
}

export function saveSportConfig(tiles_url: string | null, attribution: string | null): Promise<SportConfig> {
  return api.request<SportConfig>("PUT", `${API}/config`, { tiles_url, attribution });
}

/** Een kaarttegel via de server (met het token), als object-URL; null als hij er niet is. */
export async function tileUrl(z: number, x: number, y: number, signal?: AbortSignal): Promise<string | null> {
  try {
    const res = await api.rawRequest(`${API}/tiles/${z}/${x}/${y}`, { signal });
    return URL.createObjectURL(await res.blob());
  } catch {
    return null;
  }
}
