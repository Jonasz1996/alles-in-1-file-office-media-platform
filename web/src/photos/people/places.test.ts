import { afterEach, describe, expect, it } from "vitest";
import { loadAreas, resetI18nForTests } from "../../i18n";
import type { Place } from "../map/types";
import { NO_FILTERS } from "./filters";
import { countryChoices, placeChoices, placeLabel, withCountry } from "./places";

// I1 (S7): Land en Plaats in de balk Filters op /fotos, uit GET /photos/places (de plaatsen van je
// eigen foto's, de meeste eerst).

const AMSTERDAM: Place = { id: 2759794, name: "Amsterdam", admin1: "Noord-Holland", country: "NL", n: 34 };
const UTRECHT: Place = { id: 2745912, name: "Utrecht", admin1: "Utrecht", country: "NL", n: 12 };
const GENT: Place = { id: 2797656, name: "Gent", admin1: "Vlaanderen", country: "BE", n: 3 };
const PARIJS: Place = { id: 2988507, name: "Parijs", admin1: "Île-de-France", country: "FR", n: 2 };
const LIST = [AMSTERDAM, UTRECHT, GENT, PARIJS];

afterEach(() => resetI18nForTests("nl"));

describe("de keuzes Land en Plaats op /fotos", () => {
  it("een plaats met zijn provincie, zonder dubbel", () => {
    expect(placeLabel(AMSTERDAM)).toBe("Amsterdam, Noord-Holland");
    expect(placeLabel(UTRECHT)).toBe("Utrecht");
    expect(placeLabel({ name: "Ergens", admin1: null })).toBe("Ergens");
  });

  it("de landen van je plaatsen, elk één keer, op naam in de taal van de app", () => {
    resetI18nForTests("nl");
    expect(countryChoices(LIST)).toEqual([
      { value: "BE", label: "België" },
      { value: "FR", label: "Frankrijk" },
      { value: "NL", label: "Nederland" },
    ]);
    resetI18nForTests("en");
    expect(countryChoices(LIST).map((c) => c.label)).toEqual(["Belgium", "France", "Netherlands"]);
    // Een plaats zonder land telt niet mee; een code in kleine letters wordt hoofdletters
    expect(countryChoices([{ ...GENT, country: null }, { ...UTRECHT, country: "nl" }])).toEqual([
      { value: "NL", label: "Netherlands" },
    ]);
    expect(countryChoices([])).toEqual([]);
  });

  it("de plaatsen op naam; met een land alleen die in dat land", () => {
    expect(placeChoices(LIST, null)).toEqual([
      { value: AMSTERDAM.id, label: "Amsterdam, Noord-Holland" },
      { value: GENT.id, label: "Gent, Vlaanderen" },
      { value: PARIJS.id, label: "Parijs, Île-de-France" },
      { value: UTRECHT.id, label: "Utrecht" },
    ]);
    expect(placeChoices(LIST, "NL").map((c) => c.value)).toEqual([AMSTERDAM.id, UTRECHT.id]);
    expect(placeChoices(LIST, "BE").map((c) => c.value)).toEqual([GENT.id]);
    expect(placeChoices(LIST, "DE")).toEqual([]);
  });

  it("twee plaatsen met dezelfde naam en provincie krijgen het land erachter", () => {
    resetI18nForTests("nl");
    const a: Place = { id: 1, name: "Bergen", admin1: null, country: "NL", n: 1 };
    const b: Place = { id: 2, name: "Bergen", admin1: null, country: "NO", n: 1 };
    expect(placeChoices([a, b], null).map((c) => c.label)).toEqual(["Bergen, Nederland", "Bergen, Noorwegen"]);
    // In één land is er geen dubbel meer
    expect(placeChoices([a, b], "NO").map((c) => c.label)).toEqual(["Bergen"]);
  });

  // Review I1, W5: twee plaatsen met dezelfde naam en provincie in hetzelfde land kregen twee keer
  // hetzelfde label (ook het land erachter hielp niet); de lijst Plaats had dan twee keuzes die je
  // niet uit elkaar houdt
  it("in hetzelfde land: het aantal foto's erachter, en is dat gelijk een volgnummer", async () => {
    resetI18nForTests("nl");
    await loadAreas(["photos_people"]);
    const a: Place = { id: 11, name: "Neustadt", admin1: "Bayern", country: "DE", n: 12 };
    const b: Place = { id: 12, name: "Neustadt", admin1: "Bayern", country: "DE", n: 1 };
    const c: Place = { id: 13, name: "Neustadt", admin1: "Bayern", country: "DE", n: 1 };
    expect(placeChoices([a, b], null)).toEqual([
      { value: 12, label: "Neustadt, Bayern (1 foto)" },
      { value: 11, label: "Neustadt, Bayern (12 foto's)" },
    ]);
    // Met het land gekozen hetzelfde, en geen ", Duitsland" dat niets uitmaakt
    expect(placeChoices([a, b], "DE").map((p) => p.label)).toEqual([
      "Neustadt, Bayern (1 foto)",
      "Neustadt, Bayern (12 foto's)",
    ]);
    // Ook het aantal gelijk: een volgnummer, in de volgorde van de API (de meeste foto's eerst)
    expect(placeChoices([b, c], "DE")).toEqual([
      { value: 12, label: "Neustadt, Bayern (1)" },
      { value: 13, label: "Neustadt, Bayern (2)" },
    ]);
    // Een land, dan het aantal: drie Bergens, twee in Nederland
    const nl1: Place = { id: 1, name: "Bergen", admin1: null, country: "NL", n: 4 };
    const nl2: Place = { id: 2, name: "Bergen", admin1: null, country: "NL", n: 1 };
    const no: Place = { id: 3, name: "Bergen", admin1: null, country: "NO", n: 2 };
    expect(placeChoices([nl1, no, nl2], null).map((p) => p.label)).toEqual([
      "Bergen, Nederland (1 foto)",
      "Bergen, Nederland (4 foto's)",
      "Bergen, Noorwegen",
    ]);
    const labels = placeChoices([a, b, c, nl1, no, nl2], null).map((p) => p.label);
    expect(new Set(labels).size).toBe(labels.length);
    resetI18nForTests("en");
    await loadAreas(["photos_people"]);
    expect(placeChoices([a, b], null).map((p) => p.label)).toEqual([
      "Neustadt, Bayern (1 photo)",
      "Neustadt, Bayern (12 photos)",
    ]);
  });

  it("een ander land: de plaats blijft alleen als hij in dat land ligt", () => {
    const f = { ...NO_FILTERS, place: AMSTERDAM.id };
    expect(withCountry(f, "NL", LIST)).toEqual({ ...f, country: "NL", place: AMSTERDAM.id });
    expect(withCountry(f, "BE", LIST)).toEqual({ ...f, country: "BE", place: null });
    // Weer alle landen: de plaats blijft
    expect(withCountry({ ...f, country: "NL" }, null, LIST)).toEqual({ ...f, country: null, place: AMSTERDAM.id });
    // Een plaats die niet (meer) in de lijst staat, ligt in geen enkel land
    expect(withCountry({ ...NO_FILTERS, place: 99 }, "NL", LIST).place).toBeNull();
    // Zonder plaats alleen het land; de andere filters blijven
    const g = { ...NO_FILTERS, person: ["p1"], camera: "canon" };
    expect(withCountry(g, "fr", LIST)).toEqual({ ...g, country: "FR", place: null });
  });
});
