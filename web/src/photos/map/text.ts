// De teksten van de kaart, de lijst Plaatsen, de bronvermelding en de rij Plek, uit de antwoorden
// van §3.4. Puur (alleen t() en Intl), zodat het in vitest te testen is met vaste antwoorden.

import { collator, fmt, locale, t } from "../../i18n";
import { bcp47 } from "../../i18n/locale";
import type { FileLocation, GeoAttribution, Place, PlaceName } from "./types";

/** "Utrecht, 34 foto's", of "34 foto's" zonder naam: de naam van een stip (§4.7). */
export function dotLabel(n: number, place: string | null): string {
  return place ? t("photos_map.map.photosAt", { place, n }) : t("photos_map.map.photos", { n });
}

let regions: { locale: string; names: Intl.DisplayNames | null } | null = null;

/** De naam van een land bij zijn code (ISO 3166-1 alpha-2), in de taal van de app; de code zelf als
 * de browser hem niet kent. */
export function regionName(cc: string | null): string | null {
  if (!cc) return null;
  const code = cc.toUpperCase();
  const l = bcp47();
  if (regions?.locale !== l) {
    let names: Intl.DisplayNames | null = null;
    try {
      names = new Intl.DisplayNames([l], { type: "region" });
    } catch {
      names = null;
    }
    regions = { locale: l, names };
  }
  try {
    return regions.names?.of(code) ?? code;
  } catch {
    return code;
  }
}

/** De plaatsen met de meeste foto's eerst; bij gelijk aantal op naam. */
export function sortPlaces(list: readonly Place[]): Place[] {
  return [...list].sort((a, b) => b.n - a.n || collator.compare(a.name, b.name) || a.id - b.id);
}

/** Wat onder de naam van een plaats staat: de provincie en het land. */
export function placeWhere(p: Pick<Place, "admin1" | "country">): string {
  return [p.admin1, regionName(p.country)].filter(Boolean).join(", ");
}

/** De naam van een plaats op een regel: "Utrecht, Utrecht, Nederland". */
export function placeLine(p: PlaceName): string {
  return [p.name, p.admin1, regionName(p.cc)].filter((v, i, all) => v && all.indexOf(v) === i).join(", ");
}

/** "publiek domein" en "public domain" in de taal van de app; een andere licentie zoals hij is. */
export function licenseText(license: string): string {
  return /^(public domain|publiek domein)$/i.test(license.trim()) ? t("photos_map.attribution.publicDomain") : license;
}

/** De bronvermelding (Keuze 29): de plaatsnamen als ze geladen zijn, en de landen (de grenzen op de
 * kaart komen ook van Natural Earth, dus zonder geladen landen staat de bron van de kaart er). */
export function attributionLines(a: GeoAttribution | null): string[] {
  const out: string[] = [];
  if (a?.places) out.push(t("photos_map.attribution.places", { source: a.places.source, license: a.places.license }));
  const countries = a?.countries ?? a?.world ?? { source: "Natural Earth", license: "public domain" };
  out.push(t("photos_map.attribution.countries", { source: countries.source, license: licenseText(countries.license) }));
  return out;
}

function degrees(v: number): string {
  return fmt.number(Math.abs(v), { minimumFractionDigits: 4, maximumFractionDigits: 4 });
}

/** "52,0907° N, 5,1214° O" */
export function coordText(lat: number, lon: number): string {
  const ns = t(lat < 0 ? "photos_map.location.south" : "photos_map.location.north", { v: degrees(lat) });
  const ew = t(lon < 0 ? "photos_map.location.west" : "photos_map.location.east", { v: degrees(lon) });
  return t("photos_map.location.coords", { lat: ns, lon: ew });
}

/** De regels van de rij Plek: de plaats (als die er is), de coördinaten en de hoogte. */
export function locationLines(loc: FileLocation): string[] {
  const out: string[] = [];
  if (loc.place) out.push(placeLine(loc.place));
  out.push(coordText(loc.lat, loc.lon));
  if (loc.alt !== null && Number.isFinite(loc.alt)) {
    out.push(t("photos_map.location.altitude", { m: fmt.number(Math.round(loc.alt)) }));
  }
  return out;
}

/** De taal van de app ("nl" of "en"), voor de naam van een land uit de wereld. */
export function appLocale(): string {
  return locale();
}
