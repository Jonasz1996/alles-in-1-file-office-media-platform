// "Downloaden als PDF" (Keuze 26): wanneer de actie er is, en het downloaden zelf. Voor de haak in
// browser/actions.ts (groep omzetten) en de knop in de viewer. Van O3.
//
// De actie is er als office ingesteld is (status.state niet "off") voor een bestand dat de editor
// naar PDF omzet (op de extensie, zoals app/office/convert.py op het soort inhoud), niet voor een
// map of een eerdere versie, en nooit via een publieke link (de viewer vraagt de haak dan niet). In
// de prullenbak: zichtbaar, maar het kan niet. Of de editor nu bereikbaar is of op /beheer uit
// staat, beslist de server: een PDF die er al is, werkt ook dan (uit stopt alleen nieuwe). Is de
// PDF er nog niet, dan zet de server hem eerst om (hooguit een minuut); duurt het even, dan zegt
// een melding "De PDF wordt gemaakt…".
//
// Niet te zien met een adres maar zonder werkende instellingen (enabled onwaar en state
// "misconfigured", o5.md afwijking 9): dan heeft de server geen motor en geeft elke klik 503
// office_off, ook voor een PDF die er al is. Met office aan en een DS die ons token weigert
// (enabled waar, state "misconfigured") blijft de actie: een PDF die er al is, werkt dan wel.
// Staat office dan ook nog uit op /beheer, dan is het antwoord hetzelfde als zonder motor, en is
// de actie ook weg (de status houdt die twee niet uit elkaar; o5.md afwijking 19).

import { loadAreas, t } from "../i18n";
import { toast } from "../ui/toast";
import { pdfLink } from "./api";
import type { HookState, OfficeStatus, OfficeTarget } from "./types";

/** Wat de editor naar PDF omzet (Engine.convert_formats). */
export const CONVERT_FORMATS: readonly string[] = [
  "docx",
  "xlsx",
  "pptx",
  "odt",
  "ods",
  "odp",
  "doc",
  "xls",
  "ppt",
  "rtf",
  "docm",
  "xlsm",
  "pptm",
];

/** Na zo lang (ms) zonder antwoord de melding dat de PDF gemaakt wordt. */
export const SLOW_MS = 1500;

const HIDDEN: HookState = { visible: false, reason: null };

/** De extensie van een naam, in kleine letters ("" zonder punt, of voor ".verborgen"). */
export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

/** Zet de editor dit bestand om naar PDF? */
export function convertible(name: string): boolean {
  return CONVERT_FORMATS.includes(extensionOf(name));
}

/** "Rapport 2024.docx" -> "Rapport 2024.pdf", zoals de server de bijlage noemt. */
export function pdfName(name: string): string {
  const ext = extensionOf(name);
  const stem = ext ? name.slice(0, -(ext.length + 1)) : name;
  return `${stem || "document"}.pdf`;
}

/** Is "Downloaden als PDF" er voor dit bestand, en kan het nu? */
export function canPdf(target: OfficeTarget, status: OfficeStatus | null): HookState {
  if (!status || status.state === "off") return HIDDEN;
  if (!status.enabled && status.state === "misconfigured") return HIDDEN;
  if (target.kind !== undefined && target.kind !== "file") return HIDDEN;
  if (target.version) return HIDDEN;
  if (!convertible(target.name)) return HIDDEN;
  if (target.deleted_at) return { visible: true, reason: "trashed" };
  return { visible: true, reason: null };
}

/** De download zelf: een link met download-naam aanklikken. */
function clickLink(url: string, name: string): void {
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.rel = "noopener";
  a.click();
}

/** Vraagt de link naar de omgezette PDF en downloadt hem. Gooit bij een fout (de haak meldt die).
 * Duurt het langer dan slowMs (de server zet hem eerst om), dan een melding tot het klaar is. */
export async function downloadPdf(
  target: Pick<OfficeTarget, "id" | "name">,
  options: { slowMs?: number; click?: (url: string, name: string) => void } = {},
): Promise<void> {
  const shown: { done: boolean; dismiss: (() => void) | null } = { done: false, dismiss: null };
  const timer = setTimeout(() => {
    loadAreas(["viewer"]).then(
      () => {
        if (!shown.done) shown.dismiss = toast(t("viewer.converted.making"), { timeoutMs: 0, id: `pdf-${target.id}` });
      },
      () => {},
    );
  }, options.slowMs ?? SLOW_MS);
  try {
    const { url } = await pdfLink(target.id);
    (options.click ?? clickLink)(url, pdfName(target.name));
  } finally {
    shown.done = true;
    clearTimeout(timer);
    shown.dismiss?.();
  }
}
