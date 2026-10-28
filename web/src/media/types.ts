// De contracten van media (fase 5, bouwontwerp §2 en §3.4), frozen na M0: wat de API geeft en
// neemt (app/media/types.py en de routers van de pakketten). Alleen typen, plus een paar vaste
// lijsten (de foutcodes, de redenen, de gebeurtenissen) voor de vertalingen en de tests. Wijzigen
// gaat via de hotfix-lane van M0, samen met de Python-kant.
//
// De foutcodes staan met hun Engelse tekst in i18n/errors/media.ts (van de API); hier alleen de
// namen en de extra velden (params) per code. De bestaande codes (not_found, no_room,
// rate_limited, guest, read_only) blijven waar ze staan.
//
// Tijden zijn milliseconden in mediatijd (de brontijd min de start_time S van de bron, Keuze 22),
// tenzij er iets anders staat. Datums zijn ISO 8601 in UTC, zoals de rest van de API.

// --- codes (§3.4) ---

/** Alle foutcodes van media, in de volgorde van §3.4. */
export const MEDIA_ERROR_CODES = [
  "media_off",
  "media_worker_down",
  "media_ffmpeg_missing",
  "media_probe_pending",
  "media_unavailable",
  "media_unsupported",
  "media_hdr",
  "media_busy",
  "media_session_gone",
  "media_access_gone",
  "media_timeout",
  "media_transcode_failed",
  "media_public_transcode",
  "media_not_owner",
  "media_library_nested",
  "media_library_not_folder",
  "media_library_limit",
  "media_import_path",
  "media_import_busy",
  "media_bench_busy",
  "media_provider_off",
  "media_provider_auth",
  "media_provider_unavailable",
  "media_provider_rate",
  "media_playlist_full",
] as const;

export type MediaErrorCode = (typeof MEDIA_ERROR_CODES)[number];

/** De HTTP-status bij elke code (§3.4). */
export const MEDIA_ERROR_STATUS: Readonly<Record<MediaErrorCode, number>> = {
  media_off: 503,
  media_worker_down: 503,
  media_ffmpeg_missing: 503,
  media_probe_pending: 409,
  media_unavailable: 409,
  media_unsupported: 415,
  media_hdr: 422,
  media_busy: 503,
  media_session_gone: 410,
  media_access_gone: 403,
  media_timeout: 503,
  media_transcode_failed: 502,
  media_public_transcode: 403,
  media_not_owner: 403,
  media_library_nested: 409,
  media_library_not_folder: 422,
  media_library_limit: 422,
  media_import_path: 422,
  media_import_busy: 409,
  media_bench_busy: 409,
  media_provider_off: 409,
  media_provider_auth: 422,
  media_provider_unavailable: 503,
  media_provider_rate: 503,
  media_playlist_full: 409,
};

/** De extra velden (params) per code; een code zonder regel heeft er geen. Retry-After staat
 * als kop in het antwoord, retry_after (seconden) ook in de params waar §3.4 het noemt. */
export interface MediaErrorParams {
  media_probe_pending: { reason?: "probe" | "keyframes" };
  media_unavailable: { reason?: string };
  media_unsupported: { reasons?: PlanReason[]; reason?: "too_large" };
  media_hdr: { can_raw: boolean; reason: "too_large" | "dolby_vision" | "no_tonemap" };
  media_busy: { reason: BusyReason; retry_after: number };
  media_session_gone: { reason?: "replaced" };
  media_access_gone: { reason: "revoked" | "stopped" | "blocked" };
  media_library_nested: { library_id: string };
  media_library_limit: { max: number };
  media_provider_rate: { retry_after: number };
  media_playlist_full: { max: number };
}

/** Waarom de toelating (of de worker) nee zegt (503 media_busy, Keuze 26 en 12). */
export type BusyReason = "cpu" | "disk" | "user" | "link" | "worker";

/** De meldingen van media op /beheer (NOTICES, Keuze 45). */
export const MEDIA_NOTICES = [
  "media_worker_down",
  "media_ffmpeg_missing",
  "media_cache_full",
  "media_sandbox_off",
  "media_provider_auth",
  "media_provider_failing",
] as const;

export type MediaNotice = (typeof MEDIA_NOTICES)[number];

// --- gebeurtenissen (SSE, §3.4) ---

/** De gebeurtenissen van media. affectsFolder (events.ts) negeert ze: node.changed komt al van
 * files.notify. */
export const MEDIA_EVENTS = ["playback.progress", "media.library", "media.item", "media.import"] as const;

export type MediaEventType = (typeof MEDIA_EVENTS)[number];

/** playback.progress, alleen naar de gebruiker zelf (Keuze 36). */
export interface PlaybackProgressEvent {
  node_id: string;
  position_ms: number;
  duration_ms: number;
  watched: boolean;
  /** Het tabblad of apparaat dat de stand gaf: een speler negeert zijn eigen origin */
  origin: string;
  updated_at: string;
}

/** media.library, naar wie de bibliotheek ziet. */
export interface LibraryEvent {
  library_id: string;
  scan_state: ScanState;
  item_count: number;
}

/** media.item, naar wie de bibliotheek ziet. */
export interface ItemEvent {
  node_id: string;
  library_id: string;
}

/** media.import, naar beheerders. job: het id van de taak media.import. */
export interface ImportEvent {
  job: string;
  state: ImportState;
  done: number;
  total: number;
  skipped: number;
  bytes: number;
}

export interface MediaEventData {
  "playback.progress": PlaybackProgressEvent;
  "media.library": LibraryEvent;
  "media.item": ItemEvent;
  "media.import": ImportEvent;
}

// --- M2: bibliotheken en items ---

/** Het soort bibliotheek (Keuze 4). videos: eigen video's, zonder online metadata. */
export type LibraryKind = "movies" | "shows" | "music" | "videos";

export const LIBRARY_KINDS: readonly LibraryKind[] = ["movies", "shows", "music", "videos"];

export type ScanState = "idle" | "queued" | "running" | "failed";

/** GET /api/v1/media/libraries (een regel) */
export interface Library {
  id: string;
  name: string;
  kind: LibraryKind;
  /** De map in de boom */
  root_id: string;
  /** Van jou (anders zie je hem via een deling van de map) */
  mine: boolean;
  item_count: number;
  scan_state: ScanState;
  scanned_at: string | null;
  /** Online opzoeken (Keuze 39) */
  online: boolean;
}

/** POST /api/v1/media/libraries → 201 Library | 403 media_not_owner | 409 media_library_nested
 * | 422 media_library_not_folder | 422 media_library_limit */
export interface LibraryIn {
  root_id: string;
  kind: LibraryKind;
  name?: string;
  online?: boolean;
}

/** PATCH /api/v1/media/libraries/{id} → 200 Library */
export interface LibraryPatch {
  name?: string;
  online?: boolean;
  /** "nl-NL" */
  language?: string;
}

/** Het soort item (media_item.kind). */
export type ItemKind = "movie" | "episode" | "track" | "video" | "extra";

/** Een kaart in een lijst: een item, of (in een lijst met type=show) een serie. */
export type CardKind = ItemKind | "show";

/** Een extra bij een film (media_item.extra). */
export type ExtraKind = "trailer" | "featurette" | "deleted" | "behind" | "sample" | "other";

/** local: alleen uit de naam, NFO of tags; matched: gekoppeld; review: nakijken (Keuze 42);
 * manual: met de hand gekoppeld of verbeterd. */
export type MatchState = "local" | "matched" | "review" | "manual";

/** Hoe ver je in een item bent (playback_state). */
export interface CardProgress {
  position_ms: number;
  duration_ms: number;
}

/** Card = {node_id, title, year, kind, poster, progress, watched} (§2 M2). */
export interface Card {
  /** De node van het bestand; bij een serie de map van de serie (null als die er niet meer is) */
  node_id: string | null;
  title: string;
  year: number | null;
  kind: CardKind;
  /** Een ondertekend adres (previews.info of art/), of null */
  poster: string | null;
  /** null: nog niet begonnen (of gezien) */
  progress: CardProgress | null;
  watched: boolean;
  /** Bij een serie en een aflevering: de serie (voor /media/serie/:id) */
  show_id?: string | null;
  /** Bij een aflevering */
  season?: number | null;
  episode?: number | null;
}

/** GET /api/v1/media/home */
export interface MediaHome {
  libraries: Library[];
  /** Verder kijken (Keuze 36; M7) */
  continue: Card[];
  /** Volgende aflevering (M7) */
  next_up: Card[];
  /** Nieuw toegevoegd */
  recent: Card[];
}

/** GET /api/v1/media/libraries/{id}/items: de query. */
export interface ItemsQuery {
  type?: "movie" | "show" | "video";
  sort?: "title" | "added" | "year";
  cursor?: string;
  /** Standaard 60 */
  limit?: number;
}

/** Een pagina van een lijst; next: de cursor voor de volgende, of null aan het eind. */
export interface Page<T> {
  items: T[];
  next: string | null;
}

/** Een spoor, kort (in Item.streams). */
export interface StreamBrief {
  index: number;
  type: "video" | "audio" | "subtitle";
  codec: string;
  lang: string | null;
  title: string | null;
}

/** Een ondertitel bij een item: ingebed (e<index>) of een los bestand (x<i>, Keuze 29). */
export interface SubtitleBrief {
  id: string;
  lang: string | null;
  label: string;
  forced: boolean;
  sdh: boolean;
  /** text: als <track>; image: alleen ingebrand (PGS, VobSub, DVB) */
  kind: "text" | "image";
}

/** Een hoofdstuk uit de probe; titel hooguit 200 tekens (Keuze 30). */
export interface Chapter {
  start_ms: number;
  end_ms: number;
  title: string;
}

/** De stappen van de analyse en hun staat (media_probe, Keuze 15). */
export type AnalysisStep = "probe" | "keyframes" | "subs" | "sprites" | "audio_info";

export type AnalysisState = "none" | "pending" | "ready" | "sparse" | "failed" | "skip";

/** Een deel van een film in delen (cd1, cd2; Keuze 8). */
export interface ItemPart {
  node_id: string;
  part: number;
}

/** GET /api/v1/media/items/{node_id}: Card plus de details (§2 M2). */
export interface Item extends Card {
  node_id: string;
  library_id: string;
  overview: string | null;
  backdrop: string | null;
  duration_ms: number | null;
  streams: StreamBrief[];
  subtitles: SubtitleBrief[];
  chapters: Chapter[];
  match_state: MatchState;
  /** Mag metadata verbeteren en koppelen (schrijfrecht, Keuze 43) */
  can_edit: boolean;
  parts: ItemPart[];
  analysis: Partial<Record<AnalysisStep, AnalysisState>>;
  extra?: ExtraKind | null;
  /** Bij een probe die mislukte: de reden (de badge "Kan niet afspelen", Keuze 46) */
  error?: string | null;
}

/** Een serie (media_show). */
export interface Show {
  id: string;
  library_id: string;
  title: string;
  year: number | null;
  overview: string | null;
  poster: string | null;
  backdrop: string | null;
  match_state: MatchState;
  can_edit: boolean;
}

/** GET /api/v1/media/shows/{show_id}. season null: absolute nummering (Keuze 8). */
export interface ShowDetail {
  show: Show;
  seasons: { season: number | null; episodes: Card[] }[];
}

// --- M3: profiel, afspelen en sessies ---

/** GET /api/v1/media/{node_id}/codecs: de vaste kandidaten plus de echte strings van dit item. */
export interface CodecsOut {
  candidates: string[];
  item: string[];
}

/** Wat de browser met één codec-string kan (Keuze 20). */
export interface CodecSupport {
  /** MediaSource.isTypeSupported (of ManagedMediaSource) */
  mse: boolean;
  /** video.canPlayType */
  file: boolean;
  /** navigator.mediaCapabilities.decodingInfo; weg als het niet gemeten is */
  smooth?: boolean;
  power?: boolean;
}

/** De basisprofielen (api/app/media/profiles/*.json). */
export type BaseProfile = "web" | "ios-safari" | "webos-5" | "android-exo" | "windows-webview2";

/** Het clientprofiel, versie 1 (Keuze 20). Hooguit 64 codec-strings en 8 KB; de server laat
 * onbekende velden weg. Geen geheim: mag in localStorage. */
export interface ClientProfile {
  v: 1;
  base: BaseProfile;
  client: { kind: "web" | "tv" | "android" | "ios" | "windows"; name: string; app: string };
  engines: { mse: boolean; mms: boolean; native_hls: boolean };
  /** De mime-types die <video src> of <audio src> zelf speelt */
  file: string[];
  codecs: Record<string, CodecSupport>;
  hdr: { pq: boolean; hlg: boolean; dv: boolean; screen: boolean };
  /** height: schermhoogte maal devicePixelRatio; bitrate_kbps 0 = geen grens */
  max: { height: number; bitrate_kbps: number };
  audio: { channels: number; track_switch: boolean };
}

/** De grens van de server (ClientProfile in pydantic). */
export const PROFILE_MAX_CODECS = 64;
export const PROFILE_MAX_BYTES = 8 * 1024;

/** Kwaliteit: auto, het origineel of een trede (Keuze 24). */
export type Quality = "auto" | "source" | 1080 | 720 | 480;

export const QUALITIES: readonly Quality[] = ["auto", "source", 1080, 720, 480];

/** POST /api/v1/media/{node_id}/play en POST /api/v1/public/media/{node_id}/play (body). */
export interface PlayIn {
  profile: ClientProfile;
  /** De index van het audiospoor (Stream.index) */
  audio?: number;
  /** "e<index>" of "x<i>" */
  subtitle?: string;
  quality?: Quality;
  start_ms?: number;
  /** HDR niet omzetten (na 422 media_hdr met can_raw) */
  hdr?: "raw";
  /** Deze sessie in dezelfde beurt sluiten (een ander spoor of een andere kwaliteit) */
  replace?: string;
}

/** Hoe een sessie afspeelt (Plan.mode). audio: een nummer via MSE (Keuze 38). */
export type PlayMode = "direct" | "remux" | "transcode" | "audio";

/** De vaste redenen van de beslissing (Keuze 19). */
export const PLAN_REASONS = [
  "container",
  "video_codec",
  "audio_codec",
  "audio_track",
  "subtitle_burn",
  "hdr",
  "dolby_vision",
  "interlaced",
  "quality",
  "bitrate",
  "not_smooth",
  "keyframes_missing",
  "gop_too_long",
  "too_large",
  "budget",
  "no_worker",
] as const;

export type PlanReason = (typeof PLAN_REASONS)[number];

/** Een audiospoor dat de speler kan kiezen. */
export interface AudioTrack {
  index: number;
  lang: string | null;
  title: string | null;
  codec: string;
  channels: number | null;
  default: boolean;
}

/** De staat van een ondertitel in de sessie: pending zolang hij gemaakt wordt (de VTT geeft dan
 * 202 met Retry-After: 5); burn: een beeldspoor, alleen ingebrand. */
export type SubtitleState = "ready" | "pending" | "failed" | "burn";

/** Een ondertitel in de sessie (tracks.subtitles). */
export interface SubtitleTrack {
  /** "e<index>" of "x<i>": subs/{id}.vtt */
  id: string;
  lang: string | null;
  label: string;
  forced: boolean;
  sdh: boolean;
  default: boolean;
  state: SubtitleState;
}

/** De sprites voor de zoekbalk (Keuze 30): trick.vtt met relatieve adressen naar trick/{i}.jpg. */
export interface TrickInfo {
  /** /api/v1/stream/{sid}/trick.vtt */
  url: string;
  interval_s: number;
  width: number;
  height: number;
}

/** Wat een nummer aan het begin en eind mist voor gapless (Keuze 18 en 38). */
export interface Gapless {
  rate: number;
  /** Voorloop in samples (bij MP3 als audio/mpeg: enc_delay, zonder +529; spike S8) */
  delay: number;
  /** Het aantal geldige samples */
  samples: number;
}

/** De adressen van een sessie, allemaal onder /api/v1/stream/{sid}/. Precies één van file
 * (direct), map (MSE, remux en transcode), master (native HLS) of audio (mode audio) is er
 * altijd; map en master horen bij elkaar. */
export interface PlayUrls {
  file?: string;
  map?: string;
  master?: string;
  audio?: string;
}

/** POST …/play → 200 */
export interface PlayOut {
  /** <node_name>.<24 willekeurige bytes>: een sleutel in het pad. Nooit in localStorage of een log */
  sid: string;
  mode: PlayMode;
  reasons: PlanReason[];
  urls: PlayUrls;
  /** 'video/mp4; codecs="avc1.64001f,mp4a.40.2"'; alleen de videocodec als de bron geen audio heeft */
  mime: string;
  duration_ms: number;
  start_ms: number;
  /** timestampOffset voor MSE (−1: tfdt = mediatijd + 1 s, Keuze 22) */
  offset: number;
  tracks: { audio: AudioTrack[]; subtitles: SubtitleTrack[] };
  chapters: Chapter[];
  /** Het begin van de aftiteling (Keuze 30) */
  credits_ms?: number | null;
  trick?: TrickInfo | null;
  /** "e<i>": een geforceerd beeldspoor dat niet vanzelf aangaat (Keuze 29) */
  forced_available?: string | null;
  quality: { current: Quality; options: Quality[] };
  /** Mode audio (Keuze 38) */
  gapless?: Gapless | null;
  /** De servertijd van de start; gaat mee met elke PUT van de voortgang (Keuze 36) */
  started_at: string;
  expires_at: string;
}

/** De segmenten in map.json: het rooster van 2 s bij transcode, of [begin_ms, duur_ms] per
 * segment uit de keyframe-index bij remux (Keuze 22 en 23). */
export type SegmentGrid = { grid_ms: number; count: number } | [number, number][];

/** GET /api/v1/stream/{sid}/map.json (Cache-Control: no-store), voor de MSE-speler. */
export interface MediaMap {
  /** De generatie van de init; elk segment heeft de kop X-Init met zijn generatie */
  gen: number;
  mime: string;
  offset: number;
  /** "init.mp4?g=1", relatief aan /api/v1/stream/{sid}/ */
  init: string;
  segments: SegmentGrid;
}

/** De kop op elk segment met de generatie van zijn init (Keuze 22). */
export const INIT_HEADER = "X-Init";

// --- M7: voortgang ---

export type ProgressEvent = "tick" | "pause" | "seek" | "stop" | "end";

/** PUT /api/v1/media/progress/{node_id} → 204 | 429 rate_limited | 404 not_found */
export interface ProgressIn {
  position_ms: number;
  duration_ms: number;
  event: ProgressEvent;
  /** Een willekeurig id per tabblad of apparaat */
  origin: string;
  /** Telt per origin op */
  seq: number;
  /** PlayOut.started_at */
  started_at: string;
  audio?: number | null;
  subtitle?: string | null;
}

/** POST /api/v1/media/progress/batch → 200 {updated} */
export type ProgressBatchIn =
  | { node_ids: string[]; watched: boolean }
  | { show_id: string; season?: number | null; watched: boolean };

export interface ProgressBatchOut {
  updated: number;
}

/** Hooguit zoveel node_ids in één batch. */
export const PROGRESS_BATCH_MAX = 1000;

// --- M8: metadata online ---

export type Provider = "tmdb" | "musicbrainz";

/** GET /api/v1/media/items/{node_id}/candidates?q=&year= (een regel) */
export interface MatchCandidate {
  provider: Provider;
  id: string;
  title: string;
  year: number | null;
  original_title: string | null;
  overview: string | null;
  poster: string | null;
}

/** POST /api/v1/media/items/{node_id}/match en /shows/{show_id}/match */
export interface MatchIn {
  provider: Provider;
  id: string;
}

/** PATCH /api/v1/media/items/{node_id} → Item. unlock: velden die weer vrij mogen (locked). */
export interface ItemPatch {
  title?: string;
  sort_title?: string;
  year?: number | null;
  overview?: string | null;
  season?: number | null;
  episode?: number | null;
  unlock?: string[];
}

// --- M9: muziek ---

/** Een album in een lijst. */
export interface AlbumCard {
  id: string;
  library_id: string;
  title: string;
  album_artist: string;
  year: number | null;
  compilation: boolean;
  cover: string | null;
  track_count: number;
  duration_ms: number;
}

/** Een nummer. */
export interface Track {
  node_id: string;
  title: string;
  artist: string | null;
  album_id: string | null;
  disc: number | null;
  track: number | null;
  duration_ms: number | null;
}

/** GET /api/v1/media/libraries/{id}/albums?sort=title|year|added&cursor= → Page<AlbumCard> */
export interface AlbumsQuery {
  sort?: "title" | "year" | "added";
  cursor?: string;
}

/** GET /api/v1/media/albums/{id} */
export interface AlbumDetail {
  album: AlbumCard & { mbid: string | null; match_state: MatchState };
  tracks: Track[];
}

/** GET /api/v1/media/libraries/{id}/artists (een regel) */
export interface Artist {
  /** Genormaliseerd (Keuze 37); in het adres /muziek/artiest/:key */
  key: string;
  name: string;
  album_count: number;
}

/** GET /api/v1/media/artists/{key}?library= */
export interface ArtistDetail {
  name: string;
  albums: AlbumCard[];
}

/** GET /api/v1/media/playlists (een regel) */
export interface Playlist {
  id: string;
  name: string;
  item_count: number;
  duration_ms: number;
}

export interface PlaylistItem {
  id: string;
  node_id: string;
  track: Track;
}

/** GET /api/v1/media/playlists/{id}. Een nummer dat je niet meer mag lezen, staat er niet in. */
export interface PlaylistDetail {
  playlist: Playlist;
  items: PlaylistItem[];
}

/** POST /api/v1/media/playlists/{id}/items → {items} | 409 media_playlist_full {max} */
export interface PlaylistItemsIn {
  node_ids: string[];
  position?: number;
}

/** Hooguit zoveel nummers in een afspeellijst (Keuze 37). */
export const PLAYLIST_MAX = 5000;

// --- M10: beheer ---

/** De plaatsen van de worker (Keuze 12). */
export type WorkClass = "transcode" | "remux" | "background" | "short" | "control";

export type WorkerState = "starting" | "ok" | "down" | "ffmpeg_missing";

/** MediaConfig in Setting "media" (Keuze 45); bench alleen lezen. */
export interface MediaConfig {
  enabled: boolean;
  /** 0,5–16 cores */
  cpu_budget: number;
  /** Hooguit het plafond uit env (PLATFORM_MEDIA_MAX_TRANSCODES) */
  max_transcodes: number;
  max_user_transcodes: number;
  public_transcode: boolean;
  /** HDR live alleen op 720, tenzij aan */
  hdr_1080: boolean;
  cache_gb: number;
  musicbrainz: boolean;
  readonly bench: { factor: number; f_dec: number | null; at: string | null } | null;
}

/** PUT /api/v1/admin/media/config (body): alles behalve bench. */
export type MediaConfigIn = Partial<Omit<MediaConfig, "bench">>;

/** Een lopende sessie op /beheer (sessions.list()). */
export interface SessionView {
  /** Het uuid van de sessie (niet de sid): DELETE /admin/media/sessions/{id} */
  id: string;
  user: { id: string; display_name: string } | null;
  /** Bij een publieke link */
  link: boolean;
  title: string;
  mode: PlayMode;
  /** "720 veryfast", of null bij direct en remux */
  rung: string | null;
  /** Hoe snel de producent loopt (× realtime) */
  speed: number | null;
  /** Voorsprong in seconden mediatijd */
  ahead_s: number | null;
  started_at: string;
}

/** De import van Keuze 2. */
export type ImportState = "queued" | "running" | "done" | "failed" | "stopped";

export interface ImportView {
  job: string;
  state: ImportState;
  source: string;
  target_id: string;
  owner_id: string;
  move: boolean;
  done: number;
  total: number;
  skipped: number;
  bytes: number;
  /** "overgeslagen" met de reden (in gebruik, geen rechten, een symlink …) */
  skipped_items: { path: string; reason: string }[];
}

/** GET /api/v1/admin/media (Keuze 45). Het tabblad Media op /beheer staat er zodra dit
 * antwoordt, ook als media uit staat (daar zet de beheerder het aan). */
export interface MediaAdmin {
  worker: {
    mode: "container" | "local";
    state: WorkerState;
    since: string;
    /** De uitkomst van de zelftest (Keuze 11); sandbox off bij de lokale worker */
    selftest: { ok: boolean; sandbox: "on" | "off"; problems: string[] } | null;
    places: Record<WorkClass, { used: number; max: number }>;
  };
  ffmpeg: {
    version: string | null;
    encoders: string[];
    filters: string[];
    missing: string[];
    hwaccel: string[];
    gpu: string | null;
  } | null;
  bench: { factor: number; f_dec: number | null; at: string | null } | null;
  budget: { used: number; total: number; ds_starting: boolean };
  sessions: SessionView[];
  /** Per dag de redenen van de beslissing (Keuze 19), de laatste 7 dagen */
  reasons: { day: string; counts: Partial<Record<PlanReason, number>> }[];
  jobs: { queued: number; running: number; failed: number };
  libraries: (Library & { owner: { id: string; display_name: string }; errors: number; low_confidence: number })[];
  cache: { parts: Record<"sessions" | "subs" | "trick" | "art" | "audio", number>; free_bytes: number };
  providers: {
    name: Provider | "caa";
    key_set: boolean;
    last_ok: string | null;
    last_error: string | null;
    breaker: "closed" | "open" | "half_open";
  }[];
  import: ImportView | null;
  config: MediaConfig;
  notices: { code: MediaNotice; params: Record<string, unknown>; text: string }[];
}

/** POST /api/v1/admin/media/cache/clear (body) */
export interface CacheClearIn {
  what: "sessions" | "audio" | "trick";
}

/** PUT /api/v1/admin/media/tmdb (body): de sleutel komt nooit terug (Keuze 40). */
export interface TmdbKeyIn {
  key: string;
}

/** GET /api/v1/admin/media/import/sources: de mappen onder /data/import. */
export interface ImportSource {
  /** Ten opzichte van /data/import */
  path: string;
  files: number;
  bytes: number;
  /** Bestanden die de app niet kan lezen of (bij verplaatsen) niet kan verplaatsen */
  unreadable: number;
  unmovable: number;
}

/** POST /api/v1/admin/media/import (body) → 202 {job} | 409 media_import_busy | 422 media_import_path */
export interface ImportIn {
  source: string;
  target_id: string;
  owner_id: string;
  move: boolean;
}

/** POST /api/v1/admin/media/loadtest (body, §4.6) → 202 | 409 media_bench_busy */
export interface LoadtestIn {
  streams: number;
  seconds: number;
  codec: "h264" | "vp9";
  sources?: string[];
}

/** GET /api/v1/admin/media/loadtest: de laatste (of lopende) belastingtest. */
export interface LoadtestView {
  state: "idle" | "making_sources" | "running" | "done" | "failed" | "stopped";
  phase: "a" | "b" | "c" | "d" | null;
  started_at: string | null;
  /** Het rapport (§4.6 punt 6 en 7), per fase */
  report: Record<string, unknown> | null;
}

/** Een antwoord met een taak (202). */
export interface Accepted {
  job: string;
}
