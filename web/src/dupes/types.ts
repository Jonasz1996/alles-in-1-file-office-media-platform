// De contracten van de duplicaten (fase 6, bouwontwerp §3.4 en Keuze 34–36), frozen na Z0: wat
// GET /duplicates en POST /duplicates/ignore van Z3 geven en nemen. Alleen eigen, levende
// bestanden. Opruimen gaat via de bestaande batchroute van de prullenbak. De vormen staan in
// app/geo/types.py; test_web_contracts.py vergelijkt.

import type { FileNode, SearchHit } from "../api";

/** exact: dezelfde inhoud (blob_id); near: foto's die erop lijken (pHash en de tweede controle). */
export type DupeKind = "exact" | "near";

/** Het niveau van een groep "near": dat van het zwakste paar (Keuze 35, 36). */
export type DupeLevel = "zeker" | "lijkt";

export interface DupeItem {
  node: FileNode;
  /** De map waar hij in staat, zoals bij een treffer van zoeken */
  path: SearchHit["path"];
  size: number;
  /** De opnamedatum als kloktijd, of null */
  taken_at: string | null;
}

/** Een groep: elk paar haalt de grens (een kliek). Bij exact zijn level en dist null. */
export interface DupeGroup {
  key: string;
  kind: DupeKind;
  level: DupeLevel | null;
  /** De grootste pHash-afstand in de groep */
  dist: number | null;
  items: DupeItem[];
}

/** GET /duplicates?kind=&cursor= */
export interface DupePage {
  groups: DupeGroup[];
  cursor: string | null;
}

/** POST /duplicates/ignore: "niet gelijk" voor de groep met deze sleutel (DupeIgnoreIn). */
export interface DupeIgnore {
  key: string;
}
