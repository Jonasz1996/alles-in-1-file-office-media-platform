// De aanroepen van media (fase 5, bouwontwerp §2), frozen na M0: precies de eindpunten van de
// pakketten, met de vorm van ./types. Losse functies via api.request uit ../api, zodat een chunk
// alleen meeneemt wat het gebruikt. Tot een pakket zijn routes vult, geeft de API daar
// 503 media_off (de stub van M0).
//
// De schil importeert dit bestand nooit (zie ./index): het hoort bij de lazy chunks.

import { api } from "../api";
import type { PublicLink } from "../publicApi";
import { LIBRARIES_PATH, StatusStore, streamUrl } from "./index";
import type {
  Accepted,
  AlbumDetail,
  AlbumCard,
  AlbumsQuery,
  Artist,
  ArtistDetail,
  CacheClearIn,
  CodecsOut,
  ImportIn,
  ImportSource,
  Item,
  ItemPatch,
  ItemsQuery,
  Card,
  Library,
  LibraryIn,
  LibraryPatch,
  LoadtestIn,
  LoadtestView,
  MatchCandidate,
  MatchIn,
  MediaAdmin,
  MediaConfig,
  MediaConfigIn,
  MediaHome,
  MediaMap,
  Page,
  PlayIn,
  PlayOut,
  Playlist,
  PlaylistDetail,
  PlaylistItem,
  PlaylistItemsIn,
  ProgressBatchIn,
  ProgressBatchOut,
  ProgressIn,
  SegmentGrid,
  ShowDetail,
  TmdbKeyIn,
} from "./types";

const MEDIA = "/api/v1/media";
const ADMIN = "/api/v1/admin/media";

const seg = encodeURIComponent;

/** "?a=1&b=x" van de gezette velden, of "". */
export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

// --- M2: bibliotheken en items ---

/** GET /media/libraries: de bibliotheken die je ziet (ook de status van media, zie ./index). */
export function listLibraries(): Promise<Library[]> {
  return api.request<Library[]>("GET", LIBRARIES_PATH);
}

/** POST /media/libraries → 201 */
export function createLibrary(body: LibraryIn): Promise<Library> {
  return api.request<Library>("POST", LIBRARIES_PATH, body);
}

/** PATCH /media/libraries/{id} */
export function updateLibrary(id: string, body: LibraryPatch): Promise<Library> {
  return api.request<Library>("PATCH", `${LIBRARIES_PATH}/${seg(id)}`, body);
}

/** DELETE /media/libraries/{id} → 204 (de bestanden blijven) */
export function deleteLibrary(id: string): Promise<void> {
  return api.request<void>("DELETE", `${LIBRARIES_PATH}/${seg(id)}`);
}

/** POST /media/libraries/{id}/scan → 202 */
export function scanLibrary(id: string): Promise<void> {
  return api.request<void>("POST", `${LIBRARIES_PATH}/${seg(id)}/scan`);
}

/** GET /media/home */
export function getHome(): Promise<MediaHome> {
  return api.request<MediaHome>("GET", `${MEDIA}/home`);
}

/** GET /media/libraries/{id}/items?type=&sort=&cursor=&limit= */
export function listItems(libraryId: string, q: ItemsQuery = {}): Promise<Page<Card>> {
  return api.request<Page<Card>>("GET", `${LIBRARIES_PATH}/${seg(libraryId)}/items${query({ ...q })}`);
}

/** GET /media/items/{node_id} */
export function getItem(nodeId: string): Promise<Item> {
  return api.request<Item>("GET", `${MEDIA}/items/${seg(nodeId)}`);
}

/** GET /media/shows/{show_id} */
export function getShow(showId: string): Promise<ShowDetail> {
  return api.request<ShowDetail>("GET", `${MEDIA}/shows/${seg(showId)}`);
}

// --- M3: afspelen ---

/** GET /media/{node_id}/codecs */
export function getCodecs(nodeId: string): Promise<CodecsOut> {
  return api.request<CodecsOut>("GET", `${MEDIA}/${seg(nodeId)}/codecs`);
}

/** POST /media/{node_id}/play */
export function play(nodeId: string, body: PlayIn): Promise<PlayOut> {
  return api.request<PlayOut>("POST", `${MEDIA}/${seg(nodeId)}/play`, body);
}

/** POST /public/media/{node_id}/play, met de sleutel van de link (Keuze 34). */
export function publicPlay(link: Pick<PublicLink, "request">, nodeId: string, body: PlayIn): Promise<PlayOut> {
  return link.request<PlayOut>(`/api/v1/public/media/${seg(nodeId)}/play`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** DELETE /stream/{sid} → 204: de sessie sluiten. Zonder header (de sid is de sleutel). */
export async function closeSession(sid: string, fetchFn: typeof fetch = (...a) => fetch(...a)): Promise<void> {
  await fetchFn(`/api/v1/stream/${seg(sid)}`, { method: "DELETE", credentials: "omit", keepalive: true });
}

/** GET /stream/{sid}/map.json (no-store). Een fout gooit de Response (de speler leest de code). */
export async function getMap(sid: string, fetchFn: typeof fetch = (...a) => fetch(...a)): Promise<MediaMap> {
  const res = await fetchFn(streamUrl(sid, "map.json"), { credentials: "omit", cache: "no-store" });
  if (!res.ok) throw res;
  return (await res.json()) as MediaMap;
}

/** Hoeveel segmenten map.json beschrijft. */
export function segmentCount(segments: SegmentGrid): number {
  return Array.isArray(segments) ? segments.length : segments.count;
}

/** Het begin van segment k in ms mediatijd (zoals SegmentGrid.start_of in app/media/types.py):
 * bij het rooster 0 voor het eerste segment en origin_ms + k·grid_ms voor de rest; bij remux het
 * begin uit de lijst. */
export function segmentStart(segments: SegmentGrid, k: number): number {
  if (Array.isArray(segments)) {
    if (segments.length === 0) return 0;
    return segments[Math.max(0, Math.min(segments.length - 1, k))][0];
  }
  return k <= 0 ? 0 : segments.origin_ms + k * segments.grid_ms;
}

/** Het segment bij tijd t in ms mediatijd (zoals SegmentGrid.segment_of): nooit voor het eerste
 * en nooit voorbij het laatste (een fragment voorbij count hoort bij het laatste, spike punt 10). */
export function segmentOf(segments: SegmentGrid, t: number): number {
  if (Array.isArray(segments)) {
    // Het laatste segment dat op of voor t begint
    let lo = 0;
    let hi = segments.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (segments[mid][0] <= t) lo = mid;
      else hi = mid - 1;
    }
    return lo;
  }
  return Math.min(segments.count - 1, Math.max(0, Math.floor((t - segments.origin_ms) / segments.grid_ms)));
}

// --- M7: voortgang ---

/** PUT /media/progress/{node_id} → 204. keepalive voor visibilitychange naar verborgen. */
export function putProgress(nodeId: string, body: ProgressIn, o: { keepalive?: boolean } = {}): Promise<void> {
  return api.request<void>("PUT", `${MEDIA}/progress/${seg(nodeId)}`, body, o.keepalive ? { keepalive: true } : {});
}

/** POST /media/progress/batch → {updated} */
export function markWatched(body: ProgressBatchIn): Promise<ProgressBatchOut> {
  return api.request<ProgressBatchOut>("POST", `${MEDIA}/progress/batch`, body);
}

// --- M8: koppelen en verbeteren ---

/** GET /media/items/{node_id}/candidates?q=&year= */
export function candidates(nodeId: string, q?: string, year?: number | null): Promise<MatchCandidate[]> {
  return api.request<MatchCandidate[]>("GET", `${MEDIA}/items/${seg(nodeId)}/candidates${query({ q, year })}`);
}

/** POST /media/items/{node_id}/match → Item */
export function matchItem(nodeId: string, body: MatchIn): Promise<Item> {
  return api.request<Item>("POST", `${MEDIA}/items/${seg(nodeId)}/match`, body);
}

/** DELETE /media/items/{node_id}/match → Item (alleen lokaal) */
export function unmatchItem(nodeId: string): Promise<Item> {
  return api.request<Item>("DELETE", `${MEDIA}/items/${seg(nodeId)}/match`);
}

/** PATCH /media/items/{node_id} → Item */
export function updateItem(nodeId: string, body: ItemPatch): Promise<Item> {
  return api.request<Item>("PATCH", `${MEDIA}/items/${seg(nodeId)}`, body);
}

/** POST /media/shows/{show_id}/match */
export function matchShow(showId: string, body: MatchIn): Promise<ShowDetail> {
  return api.request<ShowDetail>("POST", `${MEDIA}/shows/${seg(showId)}/match`, body);
}

// --- M9: muziek ---

/** GET /media/libraries/{id}/albums?sort=&cursor= */
export function listAlbums(libraryId: string, q: AlbumsQuery = {}): Promise<Page<AlbumCard>> {
  return api.request<Page<AlbumCard>>("GET", `${LIBRARIES_PATH}/${seg(libraryId)}/albums${query({ ...q })}`);
}

/** GET /media/albums/{id} */
export function getAlbum(id: string): Promise<AlbumDetail> {
  return api.request<AlbumDetail>("GET", `${MEDIA}/albums/${seg(id)}`);
}

/** GET /media/libraries/{id}/artists */
export function listArtists(libraryId: string): Promise<Artist[]> {
  return api.request<Artist[]>("GET", `${LIBRARIES_PATH}/${seg(libraryId)}/artists`);
}

/** GET /media/artists/{key}?library= */
export function getArtist(key: string, libraryId?: string): Promise<ArtistDetail> {
  return api.request<ArtistDetail>("GET", `${MEDIA}/artists/${seg(key)}${query({ library: libraryId })}`);
}

/** GET /media/playlists */
export function listPlaylists(): Promise<Playlist[]> {
  return api.request<Playlist[]>("GET", `${MEDIA}/playlists`);
}

/** POST /media/playlists → 201 */
export function createPlaylist(name: string): Promise<Playlist> {
  return api.request<Playlist>("POST", `${MEDIA}/playlists`, { name });
}

/** GET /media/playlists/{id} */
export function getPlaylist(id: string): Promise<PlaylistDetail> {
  return api.request<PlaylistDetail>("GET", `${MEDIA}/playlists/${seg(id)}`);
}

/** PATCH /media/playlists/{id} {name} */
export function renamePlaylist(id: string, name: string): Promise<Playlist> {
  return api.request<Playlist>("PATCH", `${MEDIA}/playlists/${seg(id)}`, { name });
}

/** DELETE /media/playlists/{id} → 204 */
export function deletePlaylist(id: string): Promise<void> {
  return api.request<void>("DELETE", `${MEDIA}/playlists/${seg(id)}`);
}

/** POST /media/playlists/{id}/items → {items} | 409 media_playlist_full */
export function addToPlaylist(id: string, body: PlaylistItemsIn): Promise<{ items: PlaylistItem[] }> {
  return api.request<{ items: PlaylistItem[] }>("POST", `${MEDIA}/playlists/${seg(id)}/items`, body);
}

/** PUT /media/playlists/{id}/order {item_ids} → 204 */
export function reorderPlaylist(id: string, itemIds: string[]): Promise<void> {
  return api.request<void>("PUT", `${MEDIA}/playlists/${seg(id)}/order`, { item_ids: itemIds });
}

/** DELETE /media/playlists/{id}/items/{item_id} → 204 */
export function removeFromPlaylist(id: string, itemId: string): Promise<void> {
  return api.request<void>("DELETE", `${MEDIA}/playlists/${seg(id)}/items/${seg(itemId)}`);
}

// --- M10: beheer ---

/** GET /admin/media */
export function getAdmin(): Promise<MediaAdmin> {
  return api.request<MediaAdmin>("GET", ADMIN);
}

/** PUT /admin/media/config */
export function putConfig(body: MediaConfigIn): Promise<MediaConfig> {
  return api.request<MediaConfig>("PUT", `${ADMIN}/config`, body);
}

/** POST /admin/media/bench → 202 {job} | 409 media_bench_busy */
export function startBench(): Promise<Accepted> {
  return api.request<Accepted>("POST", `${ADMIN}/bench`);
}

/** DELETE /admin/media/sessions/{id}: Stop (de speler krijgt 403 media_access_gone, stopped). */
export function stopSession(id: string): Promise<void> {
  return api.request<void>("DELETE", `${ADMIN}/sessions/${seg(id)}`);
}

/** POST /admin/media/retry: de mislukte analyses opnieuw. */
export function retryFailed(): Promise<{ retried: number }> {
  return api.request<{ retried: number }>("POST", `${ADMIN}/retry`);
}

/** POST /admin/media/cache/clear {what} */
export function clearCache(body: CacheClearIn): Promise<void> {
  return api.request<void>("POST", `${ADMIN}/cache/clear`, body);
}

/** PUT /admin/media/tmdb {key}: de sleutel komt nooit terug. 422 media_provider_auth als hij niet werkt. */
export function setTmdbKey(body: TmdbKeyIn): Promise<void> {
  return api.request<void>("PUT", `${ADMIN}/tmdb`, body);
}

/** DELETE /admin/media/tmdb */
export function deleteTmdbKey(): Promise<void> {
  return api.request<void>("DELETE", `${ADMIN}/tmdb`);
}

/** POST /admin/media/tmdb/test */
export function testTmdb(): Promise<void> {
  return api.request<void>("POST", `${ADMIN}/tmdb/test`);
}

/** GET /admin/media/import/sources */
export function importSources(): Promise<ImportSource[]> {
  return api.request<ImportSource[]>("GET", `${ADMIN}/import/sources`);
}

/** POST /admin/media/import → 202 {job} */
export function startImport(body: ImportIn): Promise<Accepted> {
  return api.request<Accepted>("POST", `${ADMIN}/import`, body);
}

/** POST /admin/media/loadtest → 202 | 409 media_bench_busy */
export function startLoadtest(body: LoadtestIn): Promise<Accepted> {
  return api.request<Accepted>("POST", `${ADMIN}/loadtest`, body);
}

/** GET /admin/media/loadtest */
export function getLoadtest(): Promise<LoadtestView> {
  return api.request<LoadtestView>("GET", `${ADMIN}/loadtest`);
}

/**
 * Of het tabblad Media op /beheer er is: GET /admin/media antwoordt (M10 geeft het ook als
 * media uit staat; de stub van M0 geeft 503 media_off, dan is het er niet).
 */
export const adminMediaStatus = new StatusStore<MediaAdmin>(getAdmin, () => api.loggedIn);
