// De suggesties bij het kiezen van een persoon (Keuze 37 en 38): je eigen personen waarvan een
// woord van de naam begint met wat je typt (zonder accenten en hoofdletters, zoals de server
// zoekt), op naam. Met allowNew een laatste optie "Nieuwe persoon: …" als niemand precies zo heet.
// Los van React, zodat het te testen is.

import { collator, fold } from "../../i18n";
import type { Person } from "./types";

/** Hooguit zoveel suggesties tegelijk. */
export const MAX_SUGGESTIONS = 8;

export type ComboOption = { kind: "person"; person: Person } | { kind: "new"; name: string };

/** Begint een woord van naam met q? (q al gevouwen) */
export function nameMatches(name: string, q: string): boolean {
  if (!q) return true;
  const n = fold(name);
  return n.startsWith(q) || n.split(/[\s\-'’.]+/).some((w) => w.startsWith(q));
}

/** De opties voor wat er getypt is; exclude: ids die er al staan (bijv. al op de foto). */
export function suggest(
  people: readonly Person[],
  typed: string,
  o: { exclude?: ReadonlySet<string>; allowNew?: boolean } = {},
): ComboOption[] {
  const name = typed.trim().replace(/\s+/g, " ");
  const q = fold(name);
  const found = people
    .filter((p) => !o.exclude?.has(p.id) && nameMatches(p.name, q))
    .sort((a, b) => {
      // Wie zo begint eerst, dan op naam
      const sa = fold(a.name).startsWith(q) ? 0 : 1;
      const sb = fold(b.name).startsWith(q) ? 0 : 1;
      return sa - sb || collator.compare(a.name, b.name);
    })
    .slice(0, MAX_SUGGESTIONS)
    .map((person): ComboOption => ({ kind: "person", person }));
  const exact = people.some((p) => fold(p.name) === q);
  if (o.allowNew && name && !exact) found.push({ kind: "new", name });
  return found;
}
