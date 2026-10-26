// "Downloaden als PDF" (Keuze 26): wanneer de actie er is, en het downloaden zelf. Voor de haak in
// browser/actions.ts (groep omzetten) en de knop in de viewer.
//
// Van O3. O0 zet dit als stub met de vaste vorm: canPdf() geeft een HookState (in de stub nog
// nooit zichtbaar, want POST …/pdf bestaat pas met O3), downloadPdf() vraagt de link en start de
// download. O3 maakt het af.

import { pdfLink } from "./api";
import type { HookState, OfficeStatus, OfficeTarget } from "./types";

/** Is "Downloaden als PDF" er voor dit bestand, en kan het nu? */
export function canPdf(_target: OfficeTarget, _status: OfficeStatus | null): HookState {
  return { visible: false, reason: null };
}

/** Vraagt de link naar de omgezette PDF en downloadt hem. Gooit bij een fout (de haak meldt die). */
export async function downloadPdf(target: Pick<OfficeTarget, "id" | "name">): Promise<void> {
  const { url } = await pdfLink(target.id);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${target.name.replace(/\.[^.]+$/, "")}.pdf`;
  a.rel = "noopener";
  a.click();
}
