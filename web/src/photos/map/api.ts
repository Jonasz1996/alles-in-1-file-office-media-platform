// De aanroepen van de plek van een foto, de kaart, de plaatsen, de waarschuwing bij delen en de
// bronvermelding (fase 6, bouwontwerp §3.4), frozen na Z0: precies de eindpunten van Z3, met de
// vorm van ./types. Losse functies via api.request; de routes zijn van Z3 (app/routers/geo.py en
// photos_map.py), en api/tests/search/test_web_contracts.py houdt paden en vragen gelijk.

import { api } from "../../api";
import type { Filters } from "../../search/types";
import { seg, wireQuery } from "../../search/wire";
import type { BBox, FileLocation, GeoAttribution, MapCells, MapItems, Places, ShareCheck } from "./types";

const API = "/api/v1";

/** "w,s,e,n" voor ?bbox= */
export function bboxParam(b: BBox): string {
  return b.join(",");
}

/** GET /files/{id}/location: alleen voor de eigenaar; anders 404 not_found. */
export function fileLocation(fileId: string): Promise<FileLocation> {
  return api.request<FileLocation>("GET", `${API}/files/${seg(fileId)}/location`);
}

/** GET /photos/map: de cellen in het venster op zoom z (0–14), ≤ 500. */
export function mapCells(bbox: BBox, z: number, filters: Filters = {}): Promise<MapCells> {
  return api.request<MapCells>("GET", `${API}/photos/map${wireQuery({ bbox: bboxParam(bbox), z, ...filters })}`);
}

/** GET /photos/map/items: de foto's in het venster, een pagina per keer. */
export function mapItems(bbox: BBox, filters: Filters = {}, cursor: string | null = null): Promise<MapItems> {
  return api.request<MapItems>("GET", `${API}/photos/map/items${wireQuery({ bbox: bboxParam(bbox), ...filters, cursor })}`);
}

/** GET /photos/places: de plaatsen met het aantal foto's (de lijst naast de kaart). */
export function places(filters: Filters = {}): Promise<Places> {
  return api.request<Places>("GET", `${API}/photos/places${wireQuery(filters)}`);
}

/** GET /photos/share-check?node=: voor de waarschuwing in het deeldialoog (een map, een album of
 * een los bestand van jezelf). */
export function shareCheck(nodeId: string): Promise<ShareCheck> {
  return api.request<ShareCheck>("GET", `${API}/photos/share-check${wireQuery({ node: nodeId })}`);
}

/** GET /geo/attribution */
export function geoAttribution(): Promise<GeoAttribution> {
  return api.request<GeoAttribution>("GET", `${API}/geo/attribution`);
}
