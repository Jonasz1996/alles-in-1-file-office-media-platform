import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { SEARCH_TAB_AREAS } from "../Beheer";
import { loadAreas, resetI18nForTests } from "../i18n";
import SearchTab from "./SearchTab";

// De blokken "Gezichten" (X2) en "Zoeken op beeld" (X3) in de tab "Zoeken en foto's" (Z0b, Keuze
// 55): alleen een kop en "komt later", zonder getallen of andere verzonnen stand.
const FACES_NL =
  '<section aria-labelledby="admin-faces-title"><h3 id="admin-faces-title">Gezichten</h3>' +
  '<p class="muted">Gezichten herkennen komt later in fase 6.</p></section>';
const BEELD_NL =
  '<section aria-labelledby="admin-beeld-title"><h3 id="admin-beeld-title">Zoeken op beeld</h3>' +
  '<p class="muted">Zoeken op beeld komt later in fase 6.</p></section>';
const FACES_EN =
  '<section aria-labelledby="admin-faces-title"><h3 id="admin-faces-title">Faces</h3>' +
  '<p class="muted">Face recognition comes later in phase 6.</p></section>';
const BEELD_EN =
  '<section aria-labelledby="admin-beeld-title"><h3 id="admin-beeld-title">Search by image</h3>' +
  '<p class="muted">Search by image comes later in phase 6.</p></section>';

beforeEach(() => {
  resetI18nForTests("nl");
});

afterEach(() => {
  resetI18nForTests("nl");
});

describe("de tab Zoeken en foto's met de blokken van X2 en X3 (Z0b)", () => {
  it("de tab neemt precies de gebieden van de twee blokken mee", () => {
    expect(SEARCH_TAB_AREAS).toEqual(["photos_faces", "beeld"]);
  });

  it("nl: onder het deel van Z7 eerst Gezichten, dan Zoeken op beeld, elk alleen met komt later", async () => {
    await loadAreas(["admin", ...SEARCH_TAB_AREAS]);
    const html = renderToStaticMarkup(createElement(SearchTab));
    expect(html.endsWith(FACES_NL + BEELD_NL)).toBe(true);
    expect(html.split("<section")).toHaveLength(3);
  });

  it("en: dezelfde blokken in het Engels", async () => {
    resetI18nForTests("en");
    await loadAreas(["common", "admin", ...SEARCH_TAB_AREAS]);
    const html = renderToStaticMarkup(createElement(SearchTab));
    expect(html.endsWith(FACES_EN + BEELD_EN)).toBe(true);
  });
});
