// De API van foto's en albums (routers/photos.py en routers/albums.py). Ingelogd, via api.request.

import { api, type FileNode } from "../api";
import { bcp47 } from "../i18n/locale";
import { cursorOf, type Placed } from "./range";

/** Een foto of video op de tijdlijn of in een album. taken_at is de kloktijd van de camera,
 * als UTC opgeslagen (toon hem met fmt.wallClock). thumb en large: ondertekende adressen. */
export interface PhotoItem extends Placed {
  id: string;
  name: string;
  mime: string | null;
  size: number;
  modified_at: string;
  media: "image" | "video";
  taken_at: string;
  taken_day: number;
  width: number | null;
  height: number | null;
  duration: number | null;
  etag: string;
  thumb: string | null;
  large: string | null;
  /** Alleen ingelogd: er staat een deling op */
  shared?: boolean;
}

export interface PhotoPage {
  items: PhotoItem[];
  next: string | null;
}

export interface MonthCount {
  /** "2026-10" */
  month: string;
  count: number;
}

export interface IdRange {
  ids: string[];
  truncated: boolean;
}

export interface AlbumOut {
  id: string;
  name: string;
  description: string;
  owner: { id: string; display_name: string };
  permission: "owner" | "read";
  count: number;
  cover: { thumb: string | null; large: string | null } | null;
  first_taken_at: string | null;
  last_taken_at: string | null;
  shared: boolean;
  created_at: string;
  modified_at: string;
}

/** Een pagina is hooguit zoveel items (de server). */
export const PAGE = 500;
/** Zoveel ids per verzoek om toe te voegen of weg te halen (de server). */
export const MAX_ADD = 1000;

function query(params: Record<string, string | number | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined) q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const photosApi = {
  months: () => api.request<{ months: MonthCount[] }>("GET", "/api/v1/photos/months"),
  page: (month: string | null, after: string | null, limit = PAGE) =>
    api.request<PhotoPage>("GET", `/api/v1/photos${query({ month, after, limit })}`),
  /** De ids van a tot en met b (in welke volgorde ook), ≤ 10.000. */
  ids: (a: Placed, b: Placed) =>
    api.request<IdRange>("GET", `/api/v1/photos/ids${query({ from: cursorOf(a), to: cursorOf(b) })}`),

  albums: () => api.request<{ albums: AlbumOut[] }>("GET", "/api/v1/albums"),
  album: (id: string) => api.request<AlbumOut>("GET", `/api/v1/albums/${id}`),
  createAlbum: (name: string, description = "") =>
    api.request<AlbumOut>("POST", "/api/v1/albums", { name, description }),
  changeAlbum: (id: string, change: { name?: string; description?: string; cover_id?: string | null }) =>
    api.request<AlbumOut>("PATCH", `/api/v1/albums/${id}`, change),
  deleteAlbum: (id: string) => api.request<void>("DELETE", `/api/v1/albums/${id}`),
  albumItems: (id: string, after: string | null, limit = PAGE) =>
    api.request<PhotoPage>("GET", `/api/v1/albums/${id}/items${query({ after, limit })}`),
  albumIds: (id: string) => api.request<IdRange>("GET", `/api/v1/albums/${id}/ids`),
  removeFromAlbum: (id: string, nodeIds: string[]) =>
    api.request<{ removed: number }>("POST", `/api/v1/albums/${id}/items/remove`, { node_ids: nodeIds }),
};

const monthFormats = new Map<string, Intl.DateTimeFormat>();

/** Een maand ("2024-07") als kop: "juli 2024" (in de taal van de app). */
export function monthLabel(month: string): string {
  const locale = bcp47();
  let f = monthFormats.get(locale);
  if (!f) {
    f = new Intl.DateTimeFormat(locale, { month: "long", year: "numeric", timeZone: "UTC" });
    monthFormats.set(locale, f);
  }
  const [y, m] = month.split("-").map(Number);
  return f.format(new Date(Date.UTC(y, m - 1, 1)));
}

const dayFormats = new Map<string, Intl.DateTimeFormat>();

/** Alleen de datum van een kloktijd (taken_at, als UTC opgeslagen): "1 jul 2024". */
export function wallDate(iso: string): string {
  const locale = bcp47();
  let f = dayFormats.get(locale);
  if (!f) {
    f = new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeZone: "UTC" });
    dayFormats.set(locale, f);
  }
  return f.format(new Date(iso));
}

const clockFormats = new Map<string, Intl.DateTimeFormat>();
const dayLabels = new Map<string, string>();
const timeLabels = new Map<string, string>();

/** Datum en tijd van een kloktijd, zoals fmt.wallClock maar met een cache per dag en per minuut
 * van de dag: bij scrollen door 50.000 foto's is Intl per cel te duur. */
export function wallLabel(iso: string): string {
  const locale = bcp47();
  const day = `${locale}|${iso.slice(0, 10)}`;
  const minute = `${locale}|${iso.slice(11, 16)}`;
  let date = dayLabels.get(day);
  if (date === undefined) {
    date = wallDate(iso);
    dayLabels.set(day, date);
  }
  let time = timeLabels.get(minute);
  if (time === undefined) {
    let f = clockFormats.get(locale);
    if (!f) {
      f = new Intl.DateTimeFormat(locale, { timeStyle: "short", timeZone: "UTC" });
      clockFormats.set(locale, f);
    }
    time = f.format(new Date(`1970-01-01T${iso.slice(11, 16)}:00Z`));
    timeLabels.set(minute, time);
  }
  return `${date} ${time}`;
}

/** De periode van een album: "1 jul 2024 – 3 aug 2024", of één datum. */
export function periodOf(a: Pick<AlbumOut, "first_taken_at" | "last_taken_at">): string {
  if (!a.first_taken_at || !a.last_taken_at) return "";
  const first = wallDate(a.first_taken_at);
  const last = wallDate(a.last_taken_at);
  return first === last ? first : `${first} – ${last}`;
}

/** Het album als FileNode, voor de deeldialoog (die deelt een node). */
export function albumNode(a: AlbumOut): FileNode {
  return {
    id: a.id,
    parent_id: null,
    name: a.name,
    kind: "album",
    mime: null,
    size: 0,
    etag: "",
    created_at: a.created_at,
    modified_at: a.modified_at,
    deleted_at: null,
    tags: [],
    permission: a.permission === "owner" ? "owner" : "read",
    owner: a.owner,
    shared: a.shared,
  };
}
