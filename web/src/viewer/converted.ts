// Het voorbeeld met opmaak (fase 4, O3): de PDF die de editor van een kantoordocument maakt. Hier
// wat OfficeView nodig heeft buiten de weergave: wat er te doen is (tonen, vragen, wachten), een
// voorbeeld vragen, en hoe vaak de viewer opnieuw kijkt zolang het gemaakt wordt.

import { api } from "../api";
import { convert } from "../office/api";
import type { ConvertOut } from "../office/types";
import type { DocumentInfo } from "./api";
import type { ViewerItem } from "./types";

/** Wat de viewer met een kantoordocument doet: de pagina's van de PDF, vragen of er een komt,
 * wachten tot hij er is, of alleen de eenvoudige weergave (B21). */
export type ConvertedView = "pages" | "ask" | "pending" | "simple";

/** ask alleen voor wie ingelogd is: een publieke link start nooit een conversie. */
export function convertedView(doc: Pick<DocumentInfo, "kind" | "pdf_state">, user: boolean): ConvertedView {
  if (doc.kind === "pdf") return "pages";
  if (doc.pdf_state === "pending") return "pending";
  if (doc.pdf_state === "ask" && user) return "ask";
  return "simple";
}

/** POST /office/files/{id}/convert, voor een eerdere versie met ?version=. */
export function requestPreview(item: Pick<ViewerItem, "id" | "version">): Promise<ConvertOut> {
  if (!item.version) return convert(item.id);
  const query = new URLSearchParams({ version: item.version.id });
  return api.request<ConvertOut>("POST", `/api/v1/office/files/${encodeURIComponent(item.id)}/convert?${query}`);
}

/** Zo lang (ms) wacht de viewer voor hij weer kijkt, na n keer kijken. */
export const POLL_DELAYS: readonly number[] = [2000, 3000, 5000, 8000];
export const POLL_MAX_DELAY = 10_000;
/** Na zoveel keer (ruim tien minuten) niet meer: dan is er iets mis, en Sluiten en weer openen
 * kijkt opnieuw. */
export const POLL_LIMIT = 64;

/** De wachttijd voor de volgende keer kijken, of null als de viewer ermee ophoudt. */
export function pollDelay(n: number): number | null {
  if (n >= POLL_LIMIT) return null;
  return POLL_DELAYS[n] ?? POLL_MAX_DELAY;
}
