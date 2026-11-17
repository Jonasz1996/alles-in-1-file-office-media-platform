// Het zoekveld in de balk bovenaan (fase 6, Z0; Keuze 45): wat zonder React te testen is. Dit
// bestand zit in de initiële JS (hooguit 0,8 kB samen met AppBarSearch.tsx), dus het importeert
// alleen wat de schil al heeft (./types alleen voor MAX_QUERY): geen ./api, geen ./query. De
// suggesties (Z4, Keuze 46) zijn een eigen lazy chunk (Suggest.tsx), pas na de eerste toets.

import type { Shortcut } from "../ui/keys";

export { MAX_QUERY } from "./types";

/** Het id van het zoekveld bovenaan. Op Bestanden zet / de focus erin (Z4 besliste, z0.md Open
 * 12: één zoekveld; het veld boven de lijst van 0.7.1 is weg). */
export const SEARCH_INPUT_ID = "zoekveld";
/** Het id van de lijst met suggesties (aria-controls van het veld; Suggest.tsx, lazy). */
export const SUGGEST_LIST_ID = "zoek-suggesties";

/** Ctrl+K, op een Mac Cmd+K: de focus naar het zoekveld. Nergens anders in gebruik (zie
 * docs/fase-6/toetsenbord-z0.md). */
export const SEARCH_KEY = "mod+k";

/** Waar Enter heen gaat: /zoeken?q=…, of null zonder woorden (dan gebeurt er niets). */
export function searchTarget(text: string): string | null {
  const q = text.trim();
  return q ? `/zoeken?${new URLSearchParams({ q })}` : null;
}

/** Voor aria-keyshortcuts: zoals een schermlezer de toets noemt. */
export function ariaKeys(isMac: boolean): string {
  return isMac ? "Meta+K" : "Control+K";
}

/** De sneltoets: ook als je in een ander veld typt (Ctrl/Cmd+K typt geen teken). Onder een
 * dialoog, een menu of de viewer werkt hij niet: die houden de globale toetsen tegen. */
export function searchShortcut(focus: () => void, label: string): Shortcut {
  return { combo: SEARCH_KEY, label, allowInInput: true, run: focus };
}
