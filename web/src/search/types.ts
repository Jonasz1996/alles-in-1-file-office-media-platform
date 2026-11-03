// De contracten van zoeken (fase 6, bouwontwerp §3.3 en §3.4), frozen na Z0: wat GET /search, de
// suggesties, de woorden per pagina en de stand van de analyse geven en nemen (app/search/types.py
// en de routers van Z1 en Z2). Alleen typen, plus een paar vaste lijsten (de foutcodes, de codes
// van de analyse, de gebeurtenissen) voor de vertalingen en de tests. Wijzigen gaat samen met de
// Python-kant.
//
// De filters (Filters) gelden ook voor /photos, de kaart en de regel van een slim album (Keuze 43):
// overal dezelfde namen als in de querystring van /search.
//
// Tot Z1 en Z2 hun routes vullen, geven de nieuwe routes 501 (de stubs van Z0); GET /search zelf
// werkt al (LikeFallback) en geeft de velden van 0.7.0, de rest pas met Z1.

import type { FileNode, SearchHit, TagRef } from "../api";

export type { TagRef } from "../api";

// --- grenzen (§3.4) ---

/** Zoveel tekens mag q hooguit hebben. */
export const MAX_QUERY = 200;
/** offset loopt van 0 tot en met 1.000 ("Meer laden"). */
export const MAX_OFFSET = 1000;
/** limit: 1–50, standaard 30. */
export const DEFAULT_LIMIT = 30;
export const MAX_LIMIT = 50;
/** Per groep van de suggesties hooguit zoveel. */
export const MAX_SUGGEST = 5;

// --- de filters (Keuze 43) ---

/** Het filter type, via node.ftype; meer keer is OF. Precies FileType van app/search/types.py
 * (mime.FTYPES): Keuze 43 noemt "other" niet als filter, maar de API neemt het wel aan. */
export const SEARCH_TYPES = [
  "folder",
  "image",
  "video",
  "audio",
  "pdf",
  "document",
  "spreadsheet",
  "presentation",
  "other",
] as const;
export type SearchType = (typeof SEARCH_TYPES)[number];

/** De filters van zoeken, /photos, de kaart en een slim album. Datums als JJJJ-MM-DD. De namen
 * zijn die van de querystring (§3.4); in Python heten ze types, date_from, date_to, persons en
 * tags, met deze namen als alias (SearchRule in app/search/types.py). */
export interface Filters {
  /** Meer keer: OF */
  type?: SearchType[];
  /** taken_day voor foto en video, anders modified_at */
  from?: string;
  to?: string;
  /** Eigen personen; meer keer: EN */
  person?: string[];
  /** Een plaats (id uit /photos/places) of een land (ISO 3166-1 alpha-2), niet allebei */
  place?: number;
  country?: string;
  /** Eigen tags; meer keer: EN */
  tag?: string[];
  /** Een mediabibliotheek */
  library?: string;
  /** "merk model", gevouwen (blob_meta.camera) */
  camera?: string;
  /** Alleen onder deze map */
  in?: string;
}

/** De vraag van GET /search. Zonder q en zonder filter: 400 search_bad_filter. */
export interface SearchParams extends Filters {
  q?: string;
  offset?: number;
  limit?: number;
  /** Ronde B: verder na deze rowid (de after van de vorige pagina, Keuze 9) */
  after?: number;
}

// --- het antwoord van GET /search (§3.3) ---

/** Waar de treffer zit: de naam, de titel uit de mediabibliotheek, de tekst, OCR of meta. */
export type MatchKind = "name" | "title" | "text" | "ocr" | "meta";

/** Een stuk van een snippet; m: gemarkeerd (een woord uit de vraag). Altijd tekst, nooit HTML. */
export interface SnippetPart {
  t: string;
  m: boolean;
}

/** Een treffer: {node, path} zoals in 0.7.0, met match en snippet erbij. */
export interface Hit extends SearchHit {
  match: MatchKind;
  snippet: SnippetPart[] | null;
}

export interface PersonRef {
  id: string;
  name: string;
}

export interface PlaceRef {
  id: number;
  name: string;
  /** De provincie of staat (Engels, uit GeoNames) */
  admin1: string | null;
  /** ISO 3166-1 alpha-2 */
  country: string | null;
}

/** Een groep bovenaan de treffers: een eigen persoon of plaats, met het aantal foto's. id is
 * tekst, ook voor een plaats (de vorm van GroupItem in app/search/types.py van Z0-api). */
export interface GroupItem {
  id: string;
  name: string;
  n: number;
}

/** Bovenaan de treffers: eigen personen en plaatsen waarvan de naam zo begint (Keuze 38). */
export interface Groups {
  people: GroupItem[];
  places: GroupItem[];
}

/** Tijdens de backfill: hoeveel van de tekst al in de index staat, 0–1. */
export interface IndexProgress {
  done: number;
}

export interface SearchPage {
  results: Hit[];
  more: boolean;
  offset: number;
  /** De tijdsgrens is geraakt: niet per se de beste treffers (Keuze 9) */
  partial: boolean;
  /** false na ronde A: zonder rangorde */
  ranked: boolean;
  /** Ronde C: alleen op naam */
  names_only: boolean;
  /** Voor de volgende pagina na ronde B */
  after: number | null;
  groups: Groups | null;
  indexing: IndexProgress | null;
  engine: "fts" | "like";
}

// --- de suggesties (Keuze 46) ---

/** Een naam in de suggesties: een map of bestand dat je kunt zien (zoals NameRef van Z0-api). */
export interface NameRef {
  id: string;
  name: string;
  kind: FileNode["kind"];
}

export interface AlbumRef {
  id: string;
  name: string;
}

/** GET /search/suggest?q=: elke groep hooguit MAX_SUGGEST. */
export interface Suggestions {
  names: NameRef[];
  people: PersonRef[];
  places: PlaceRef[];
  tags: TagRef[];
  albums: AlbumRef[];
}

// --- tekst: de woorden per pagina en de stand van de analyse (Z2, X1) ---

/** Een woord op een pagina; x, y, w en h als deel van de pagina (0–1). */
export interface PageWord {
  t: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

/** GET /files/{id}/pages/{n}/words?token= (de tekstlaag in de viewer) */
export interface PageWords {
  page: number;
  words: PageWord[];
  source: "pdf" | "ocr";
}

/** De stand van één stap (text, hash, ocr) voor de inhoud van een bestand. queued: nog in de rij. */
export type StepState = "queued" | "done" | "none" | "skip" | "failed";

/** Codes in analysis.code: alleen op /beheer, nooit als HTTP-fout (§3.4). */
export const ANALYSIS_CODES = [
  "too_slow",
  "ocr_missing",
  "ocr_bad_image",
  "ocr_has_text",
  "zip_limit",
  "xml_refused",
  "pages_limit",
] as const;
export type AnalysisCode = (typeof ANALYSIS_CODES)[number];

/** GET /files/{id}/analysis (leesrecht) */
export interface Analysis {
  text: StepState;
  hash: StepState;
  ocr: StepState;
  truncated: boolean;
  code: AnalysisCode | null;
}

// --- foutcodes (§3.4) ---

/** De foutcodes van zoeken, met hun Engelse tekst in i18n/errors/search.ts. */
export const SEARCH_ERROR_CODES = [
  "search_query_too_long",
  "search_bad_offset",
  "search_bad_filter",
  "search_busy",
  "search_rebuild_running",
] as const;
export type SearchErrorCode = (typeof SEARCH_ERROR_CODES)[number];

// --- gebeurtenissen (SSE, §3.4) ---

/** search.ready: naar wie de nodes kan zien; de viewer ververst dan de tekstlaag. */
export const SEARCH_READY = "search.ready";
export interface SearchReadyData {
  nodes: string[];
  step: "text" | "hash" | "ocr";
}

/** search.progress: alleen naar beheerders, elke 10 s tijdens een backfill of herbouw
 * (SearchProgressEvent in app/search/types.py). De hele stand haalt /beheer met GET /admin/search
 * (admin/searchApi.ts). */
export const SEARCH_PROGRESS = "search.progress";
export interface SearchProgressData {
  what: "backfill" | "names" | "text" | "geo" | "hash" | "ocr";
  /** 0–1 */
  done: number;
}
