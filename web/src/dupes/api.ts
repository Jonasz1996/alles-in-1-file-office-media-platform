// De aanroepen van de duplicaten (fase 6, bouwontwerp §3.4), frozen na Z0: de eindpunten van Z3.
// Tot Z3 zijn routes vult, geven ze 501. Naar de prullenbak gaat via de bestaande batchroute.

import { api } from "../api";
import { wireQuery } from "../search/wire";
import type { DupeKind, DupePage } from "./types";

const API = "/api/v1";

/** GET /duplicates?kind=exact|near&cursor= */
export function duplicates(kind: DupeKind, cursor: string | null = null): Promise<DupePage> {
  return api.request<DupePage>("GET", `${API}/duplicates${wireQuery({ kind, cursor })}`);
}

/** POST /duplicates/ignore {key} → 204: "niet gelijk"; 400 dupes_bad_key. */
export function ignoreDuplicates(key: string): Promise<void> {
  return api.request<void>("POST", `${API}/duplicates/ignore`, { key });
}
