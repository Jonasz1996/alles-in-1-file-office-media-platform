// De contracten van de plek van een foto, de kaart, de plaatsen, de waarschuwing bij delen en de
// bronvermelding (fase 6, bouwontwerp §3.4), frozen na Z0: wat de routers van Z3 geven en nemen.
// Alleen eigen, levende bestanden (own_live); een plek ziet alleen de eigenaar, nooit via een
// publieke link (Keuze 27). De vormen staan in app/geo/types.py; test_web_contracts.py vergelijkt.

import type { PhotoItem } from "../api";

// --- grenzen (Keuze 32) ---

/** Zoom 0–14; daarboven losse foto's. */
export const MAX_ZOOM = 14;
/** Hooguit zoveel cellen per antwoord. */
export const MAX_CELLS = 500;

/** Een venster op de kaart: west, zuid, oost, noord, in graden. */
export type BBox = readonly [w: number, s: number, e: number, n: number];

/** De plaatsnaam bij een plek: uit GeoNames, het land uit Natural Earth (Keuze 29). */
export interface PlaceName {
  name: string;
  admin1: string | null;
  /** ISO 3166-1 alpha-2 */
  cc: string | null;
}

/** GET /files/{id}/location; 404 not_found als er geen is of je hem niet mag zien. */
export interface FileLocation {
  lat: number;
  lon: number;
  alt: number | null;
  place: PlaceName | null;
}

/** Een cel van de kaart op zoom z: (x16 >> (14 − z), y16 >> (14 − z)), met n foto's. */
export interface MapCell {
  x: number;
  y: number;
  n: number;
  /** Het midden van de foto's in de cel */
  lat: number;
  lon: number;
  /** De nieuwste foto in de cel (een node-id) */
  cover: string;
}

/** GET /photos/map?bbox=w,s,e,n&z=&<filters> */
export interface MapCells {
  cells: MapCell[];
}

/** GET /photos/map/items?bbox=&<filters>&cursor= */
export interface MapItems {
  items: PhotoItem[];
  cursor: string | null;
}

/** Een plaats met het aantal eigen, levende foto's (per verzoek geteld, Keuze 33). */
export interface Place {
  id: number;
  name: string;
  admin1: string | null;
  country: string | null;
  n: number;
}

/** GET /photos/places?<filters> */
export interface Places {
  places: Place[];
}

/** GET /photos/share-check?node=: hoeveel foto's in wat je deelt een plek in het bestand hebben
 * (blob_meta.gps), en van hoeveel dat onbekend is (een video met media uit). Keuze 30. */
export interface ShareCheck {
  with_location: number;
  unknown: number;
}

/** GET /geo/attribution: de bronnen achter plaatsen, landen en de kaart. null: niet geladen. */
export interface GeoAttribution {
  places: { source: string; license: string; date: string } | null;
  countries: { source: string; license: string; version: string } | null;
  world: { source: string; license: string };
}

// --- foutcodes (§3.4) ---

/** De foutcodes van kaart en duplicaten, met hun Engelse tekst in i18n/errors/photos_map.ts. */
export const PHOTOS_MAP_ERROR_CODES = ["dupes_bad_key", "geo_not_loaded"] as const;
export type PhotosMapErrorCode = (typeof PHOTOS_MAP_ERROR_CODES)[number];
