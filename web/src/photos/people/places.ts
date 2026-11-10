// De keuzes Land en Plaats in de balk Filters op /fotos (I1, S7; Keuze 43). Ze komen uit de
// plaatsen van je eigen foto's: GET /photos/places, dezelfde lijst als naast de kaart (leeg zonder
// places.sqlite of met locatie "uit"). Een land staat erbij als er een van je plaatsen in ligt; de
// API heeft geen eigen lijst van landen. Los van React, zodat het te testen is.

import { collator } from "../../i18n";
import { regionName } from "../map/text";
import type { Place } from "../map/types";
import type { PhotoFilters } from "./filters";

/** Een optie van een keuzelijst. */
export interface Choice<V> {
  value: V;
  label: string;
}

/** "Hengelo, Overijssel": een plaats met zijn provincie, zonder dubbel ("Utrecht", niet "Utrecht,
 * Utrecht"), zoals de chip van een plaats op /zoeken. */
export function placeLabel(p: Pick<Place, "name" | "admin1">): string {
  return p.admin1 && p.admin1 !== p.name ? `${p.name}, ${p.admin1}` : p.name;
}

function code(cc: string | null): string | null {
  return cc ? cc.toUpperCase() : null;
}

/** De landen van je plaatsen, elk één keer, op naam in de taal van de app. */
export function countryChoices(places: readonly Place[]): Choice<string>[] {
  const codes = new Set<string>();
  for (const p of places) {
    const c = code(p.country);
    if (c) codes.add(c);
  }
  return [...codes]
    .map((c) => ({ value: c, label: regionName(c) ?? c }))
    .sort((a, b) => collator.compare(a.label, b.label) || a.value.localeCompare(b.value));
}

/** De plaatsen op naam; met een land alleen die in dat land. Twee met dezelfde naam en provincie
 * (in een ander land) krijgen het land erachter. */
export function placeChoices(places: readonly Place[], country: string | null): Choice<number>[] {
  const shown = places.filter((p) => country === null || code(p.country) === country);
  const labels = new Map<string, number>();
  for (const p of shown) labels.set(placeLabel(p), (labels.get(placeLabel(p)) ?? 0) + 1);
  return shown
    .map((p) => {
      const label = placeLabel(p);
      const land = (labels.get(label) ?? 0) > 1 ? regionName(p.country) : null;
      return { value: p.id, label: land ? `${label}, ${land}` : label };
    })
    .sort((a, b) => collator.compare(a.label, b.label) || a.value - b.value);
}

/** Een ander land (of geen): de gekozen plaats blijft alleen als hij in dat land ligt. */
export function withCountry(f: PhotoFilters, country: string | null, places: readonly Place[]): PhotoFilters {
  const cc = code(country);
  const inside = f.place === null || cc === null || code(places.find((p) => p.id === f.place)?.country ?? null) === cc;
  return { ...f, country: cc, place: inside ? f.place : null };
}
