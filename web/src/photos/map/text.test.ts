import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterAll, beforeEach, describe, expect, it } from "vitest";
import { loadAreas, resetI18nForTests } from "../../i18n";
import { MapView } from "./MapView";
import { PlacesList } from "./PlacesList";
import {
  attributionLines,
  coordText,
  dotLabel,
  licenseText,
  locationLines,
  placeLine,
  placeWhere,
  regionName,
  sortPlaces,
} from "./text";
import type { FileLocation, GeoAttribution, Place } from "./types";

/** Vaste antwoorden in de vorm van §3.4 (GET /geo/attribution, /photos/places, /files/{id}/location). */
const ATTRIBUTION: GeoAttribution = {
  places: { source: "GeoNames cities500", license: "CC BY 4.0", date: "2026-10-01" },
  countries: { source: "Natural Earth 10m", license: "public domain", version: "5.1.1" },
  world: { source: "Natural Earth 110m", license: "public domain" },
};

const PLACES: Place[] = [
  { id: 3, name: "Zwolle", admin1: "Overijssel", country: "NL", n: 2 },
  { id: 1, name: "Utrecht", admin1: "Utrecht", country: "NL", n: 34 },
  { id: 7, name: "Antwerpen", admin1: "Vlaanderen", country: "BE", n: 2 },
  { id: 9, name: "Ergens", admin1: null, country: null, n: 1 },
];

const UTRECHT: FileLocation = {
  lat: 52.0907,
  lon: 5.1214,
  alt: 3.6,
  place: { name: "Utrecht", admin1: "Utrecht", cc: "NL" },
};

async function useLocale(l: "nl" | "en"): Promise<void> {
  resetI18nForTests(l);
  await loadAreas(["photos_map"]);
}

describe("de teksten van de kaart, in het Nederlands", () => {
  beforeEach(() => useLocale("nl"));

  it("noemt een stip met en zonder plaats, in enkelvoud en meervoud", () => {
    expect(dotLabel(1, "Utrecht")).toBe("Utrecht, 1 foto");
    expect(dotLabel(34, "Utrecht")).toBe("Utrecht, 34 foto's");
    expect(dotLabel(1200, null)).toBe("1.200 foto's");
  });

  it("zet de plaatsen met de meeste foto's eerst, bij gelijk aantal op naam", () => {
    expect(sortPlaces(PLACES).map((p) => p.name)).toEqual(["Utrecht", "Antwerpen", "Zwolle", "Ergens"]);
    expect(PLACES[0].name).toBe("Zwolle");
  });

  it("zet de provincie en het land onder een plaats", () => {
    expect(regionName("nl")).toBe("Nederland");
    expect(placeWhere(PLACES[0])).toBe("Overijssel, Nederland");
    expect(placeWhere(PLACES[2])).toBe("Vlaanderen, België");
    expect(placeWhere(PLACES[3])).toBe("");
    expect(placeLine({ name: "Utrecht", admin1: "Utrecht", cc: "NL" })).toBe("Utrecht, Nederland");
  });

  it("vermeldt de bronnen (Keuze 29), ook als ze nog niet geladen zijn", () => {
    expect(attributionLines(ATTRIBUTION)).toEqual([
      "Plaatsnamen: GeoNames cities500, CC BY 4.0.",
      "Landen: Natural Earth 10m (publiek domein).",
    ]);
    expect(attributionLines({ ...ATTRIBUTION, places: null, countries: null })).toEqual([
      "Landen: Natural Earth 110m (publiek domein).",
    ]);
    expect(attributionLines(null)).toEqual(["Landen: Natural Earth (publiek domein)."]);
    expect(licenseText("Public Domain")).toBe("publiek domein");
    expect(licenseText("CC BY 4.0")).toBe("CC BY 4.0");
  });

  it("schrijft de plek met windrichtingen en de hoogte in hele meters", () => {
    expect(coordText(52.0907, 5.1214)).toBe("52,0907° N, 5,1214° O");
    expect(coordText(-33.86882, -70.5)).toBe("33,8688° Z, 70,5000° W");
    expect(locationLines(UTRECHT)).toEqual(["Utrecht, Nederland", "52,0907° N, 5,1214° O", "4 m hoogte"]);
    expect(locationLines({ lat: 46.5, lon: 8.0, alt: 3970.4, place: null })).toEqual([
      "46,5000° N, 8,0000° O",
      "3.970 m hoogte",
    ]);
    expect(locationLines({ lat: 0, lon: 0, alt: null, place: null })).toEqual(["0,0000° N, 0,0000° O"]);
  });
});

describe("de teksten van de kaart, in het Engels", () => {
  beforeEach(() => useLocale("en"));
  afterAll(() => useLocale("nl"));

  it("dezelfde antwoorden in de andere taal", () => {
    expect(dotLabel(1, "Utrecht")).toBe("Utrecht, 1 photo");
    expect(dotLabel(1200, null)).toBe("1,200 photos");
    expect(placeWhere(PLACES[0])).toBe("Overijssel, Netherlands");
    expect(attributionLines(ATTRIBUTION)).toEqual([
      "Place names: GeoNames cities500, CC BY 4.0.",
      "Countries: Natural Earth 10m (public domain).",
    ]);
    expect(locationLines(UTRECHT)).toEqual(["Utrecht, Netherlands", "52.0907° N, 5.1214° E", "4 m altitude"]);
  });
});

describe("de lijst Plaatsen en de kaart voor een schermlezer", () => {
  beforeEach(() => useLocale("nl"));

  const noop = () => {};

  it("een knop per plaats, met aria-pressed op de gekozen plaats", () => {
    const html = renderToStaticMarkup(
      createElement(PlacesList, { places: sortPlaces(PLACES), error: null, chosen: 7, onChoose: noop }),
    );
    const pressed = [...html.matchAll(/aria-pressed="(true|false)"><span class="map-place-name">([^<]+)/g)].map(
      (m) => `${m[2]}:${m[1]}`,
    );
    expect(pressed).toEqual([
      "Alle plaatsen:false",
      "Utrecht:false",
      "Antwerpen:true",
      "Zwolle:false",
      "Ergens:false",
    ]);
    expect(html).toContain("34 foto&#x27;s");
    expect(html).toContain('aria-labelledby="plaatsen-kop"');
  });

  it("een melding bij een fout, en een eigen tekst als er nog geen plaatsen zijn", () => {
    const fout = renderToStaticMarkup(createElement(PlacesList, { places: null, error: "Weg", chosen: null, onChoose: noop }));
    expect(fout).toContain('<p role="alert">Weg</p>');
    const leeg = renderToStaticMarkup(createElement(PlacesList, { places: [], error: null, chosen: null, onChoose: noop }));
    expect(leeg).toContain("Nog geen plaatsen.");
  });

  it("de kaart is één plek in de tabvolgorde, met een naam en een uitleg", () => {
    const html = renderToStaticMarkup(
      createElement(MapView, {
        countries: null,
        view: null,
        dots: [],
        labelOf: () => "",
        nameOf: () => null,
        thumbOf: () => null,
        mark: null,
        describedBy: "kaart-hulp",
        onSize: noop,
        onView: noop,
        onHome: noop,
        onActivate: () => false,
      }),
    );
    expect(html).toBe(
      '<div class="map-view" tabindex="0" role="region" aria-roledescription="kaart" aria-label="Kaart met je foto&#x27;s" aria-describedby="kaart-hulp"></div>',
    );
  });
});
