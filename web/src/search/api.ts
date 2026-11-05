// De aanroepen van zoeken en tekst (fase 6, bouwontwerp §3.4), frozen na Z0: precies de
// eindpunten van Z1, Z2 en X1, met de vorm van ./types. Losse functies via api.request uit ../api,
// zodat een chunk alleen meeneemt wat het gebruikt. Tot een pakket zijn route vult, geeft de API
// daar 501 (de stub van Z0); GET /search werkt al (LikeFallback).
//
// De schil importeert dit bestand niet: het hoort bij de lazy chunks (het veld in de balk
// bovenaan, AppBarSearch.tsx, gaat met een gewone link naar /zoeken).
//
// Z0b (Keuze 80): zoeken op beeld (X3) gaat via dezelfde search() met mode "image" en q of like;
// beeldStatus() is GET /photos/beeld. Tot X3 er is, geeft de API daar 501 not_built.

import { api } from "../api";
import type { Analysis, BeeldStatus, PageWords, SearchPage, SearchParams, Suggestions } from "./types";
import { seg, wireQuery } from "./wire";

const API = "/api/v1";

/** GET /search: treffers op naam en inhoud, met de filters van Keuze 43; met mode "image" op wat
 * er op de foto staat (X3: 409 beeld_off als het uit staat). */
export function search(params: SearchParams): Promise<SearchPage> {
  return api.request<SearchPage>("GET", `${API}/search${wireQuery(params)}`);
}

/** GET /search/suggest?q= (1–200 tekens): namen, personen, plaatsen, tags en albums, elk ≤ 5. */
export function suggest(q: string): Promise<Suggestions> {
  return api.request<Suggestions>("GET", `${API}/search/suggest${wireQuery({ q })}`);
}

/** GET /files/{id}/pages/{n}/words?token=: de woorden van een pagina, met het download-token van
 * de viewer (met de versie). 404 not_found als er geen tekst is. */
export function pageWords(fileId: string, page: number, token: string): Promise<PageWords> {
  return api.request<PageWords>("GET", `${API}/files/${seg(fileId)}/pages/${page}/words${wireQuery({ token })}`);
}

/** GET /files/{id}/analysis: hoe ver de tekst, de hash en OCR van dit bestand zijn. */
export function analysis(fileId: string): Promise<Analysis> {
  return api.request<Analysis>("GET", `${API}/files/${seg(fileId)}/analysis`);
}

/** POST /files/{id}/ocr (X1) → 202. Fouten: 409 ocr_running, 503 ocr_off, 429 ocr_limit,
 * 409 ocr_has_text (in i18n/errors/photos.ts, van X1). */
export function requestOcr(fileId: string): Promise<void> {
  return api.request<void>("POST", `${API}/files/${seg(fileId)}/ocr`);
}

/** GET /photos/beeld (X3): de stand van zoeken op beeld voor jou; zet je foto's alvast klaar in de
 * container (hooguit eens per minuut, Keuze 78). */
export function beeldStatus(): Promise<BeeldStatus> {
  return api.request<BeeldStatus>("GET", `${API}/photos/beeld`);
}
