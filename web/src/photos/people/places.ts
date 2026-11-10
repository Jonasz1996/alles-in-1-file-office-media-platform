// De keuzes Land en Plaats in de balk Filters op /fotos (I1, S7; Keuze 43). Ze komen uit de
// plaatsen van je eigen foto's: GET /photos/places, dezelfde lijst als naast de kaart (leeg zonder
// places.sqlite of met locatie "uit"). Een land staat erbij als er een van je plaatsen in ligt; de
// API heeft geen eigen lijst van landen. Los van React, zodat het te testen is.

import { collator, t } from "../../i18n";
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

interface Row {
  place: Place;
  label: string;
}

/** Rijen met hetzelfde label krijgen `more` erachter, maar alleen in een groep waar dat ze uit
 * elkaar houdt: "Bergen, Nederland" en "Bergen, Noorwegen", niet twee keer ", Nederland". */
function apart(rows: readonly Row[], more: (p: Place) => string): Row[] {
  const groups = new Map<string, Row[]>();
  for (const r of rows) {
    const group = groups.get(r.label);
    if (group) group.push(r);
    else groups.set(r.label, [r]);
  }
  const labels = new Map<Row, string>();
  for (const group of groups.values()) {
    if (group.length < 2) continue;
    const extra = group.map((r) => more(r.place));
    if (new Set(extra).size < 2) continue;
    group.forEach((r, i) => labels.set(r, r.label + extra[i]));
  }
  return rows.map((r) => ({ place: r.place, label: labels.get(r) ?? r.label }));
}

/** Wat dan nog gelijk is (zelfde naam, provincie, land en aantal foto's), krijgt een volgnummer:
 * twee keuzes die je niet uit elkaar houdt, zijn er nooit. */
function numbered(rows: readonly Row[]): Row[] {
  const total = new Map<string, number>();
  for (const r of rows) total.set(r.label, (total.get(r.label) ?? 0) + 1);
  const seen = new Map<string, number>();
  return rows.map((r) => {
    if ((total.get(r.label) ?? 0) < 2) return r;
    const i = (seen.get(r.label) ?? 0) + 1;
    seen.set(r.label, i);
    return { place: r.place, label: `${r.label} (${i})` };
  });
}

/** De plaatsen op naam; met een land alleen die in dat land. Twee met dezelfde naam en provincie
 * krijgen het land erachter als dat verschilt, anders hun aantal foto's ("Neustadt, Bayern (12
 * foto's)"), en is dat ook gelijk een volgnummer (review I1, W5). */
export function placeChoices(places: readonly Place[], country: string | null): Choice<number>[] {
  const shown = places.filter((p) => country === null || code(p.country) === country);
  let rows: Row[] = shown.map((place) => ({ place, label: placeLabel(place) }));
  rows = apart(rows, (p) => {
    const land = regionName(p.country);
    return land ? `, ${land}` : "";
  });
  rows = apart(rows, (p) => ` (${t("photos_people.filters.placePhotos", { n: p.n })})`);
  return numbered(rows)
    .map((r) => ({ value: r.place.id, label: r.label }))
    .sort((a, b) => collator.compare(a.label, b.label) || a.value - b.value);
}

/** Een ander land (of geen): de gekozen plaats blijft alleen als hij in dat land ligt. */
export function withCountry(f: PhotoFilters, country: string | null, places: readonly Place[]): PhotoFilters {
  const cc = code(country);
  const inside = f.place === null || cc === null || code(places.find((p) => p.id === f.place)?.country ?? null) === cc;
  return { ...f, country: cc, place: inside ? f.place : null };
}
