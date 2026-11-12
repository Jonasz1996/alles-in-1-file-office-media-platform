// De API van de prullenbak (P6): het overzicht met plaats en grootte, terugzetten (het contract
// van P0 in crossApi), definitief verwijderen en legen.

import { api, type FileNode } from "../api";
import { type BatchResult, crossApi } from "../crossApi";

export interface PathPart {
  id: string;
  name: string;
}

export interface TrashItem {
  node: FileNode;
  /** De mappen waar het in stond (zonder de hoofdmap); [] = in de hoofdmap; null = nergens (een album) */
  location: PathPart[] | null;
  /** Een map: alles eronder samen */
  size: number;
  /** original: terug op de oude plek; root: die map is weg, dus in de hoofdmap */
  restore_to: "original" | "root";
}

export interface TrashOverview {
  items: TrashItem[];
  count: number;
  total_bytes: number;
}

export const trashApi = {
  overview: () => api.request<TrashOverview>("GET", "/api/v1/trash/overview"),
  restore: (ids: string[]): Promise<BatchResult> => crossApi.restore(ids),
  purge: (ids: string[]) => api.request<BatchResult>("POST", "/api/v1/trash/purge", { ids }),
  empty: () => api.request<{ purged: number }>("DELETE", "/api/v1/trash"),
};
