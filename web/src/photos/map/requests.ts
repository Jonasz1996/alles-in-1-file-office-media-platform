// De verzoeken van de kaart die afgebroken moeten kunnen worden (schuiven en zoomen, Keuze 32).
// De aanroepen in ./api.ts (Z0, bevroren) nemen geen AbortSignal; deze paden zijn precies de hunne
// (requests.test.ts vergelijkt ze), en gaan met een signal via api.request.

import { api } from "../../api";
import type { Filters } from "../../search/types";
import { seg, wireQuery } from "../../search/wire";
import { bboxParam } from "./api";
import type { BBox, FileLocation, MapCells, MapItems } from "./types";

const API = "/api/v1";

/** Het pad van GET /photos/map, zoals mapCells() het vraagt. */
export function cellsPath(bbox: BBox, z: number, filters: Filters = {}): string {
  return `${API}/photos/map${wireQuery({ bbox: bboxParam(bbox), z, ...filters })}`;
}

/** Het pad van GET /photos/map/items, zoals mapItems() het vraagt. */
export function itemsPath(bbox: BBox, filters: Filters = {}, cursor: string | null = null): string {
  return `${API}/photos/map/items${wireQuery({ bbox: bboxParam(bbox), ...filters, cursor })}`;
}

/** Het pad van GET /files/{id}/location, zoals fileLocation() het vraagt. */
export function locationPath(fileId: string): string {
  return `${API}/files/${seg(fileId)}/location`;
}

export function getCells(path: string, signal?: AbortSignal): Promise<MapCells> {
  return api.request<MapCells>("GET", path, undefined, { signal });
}

export function getItems(path: string, signal?: AbortSignal): Promise<MapItems> {
  return api.request<MapItems>("GET", path, undefined, { signal });
}

export function getLocation(fileId: string, signal?: AbortSignal): Promise<FileLocation> {
  return api.request<FileLocation>("GET", locationPath(fileId), undefined, { signal });
}
