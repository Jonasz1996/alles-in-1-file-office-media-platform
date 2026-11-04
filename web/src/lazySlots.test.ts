import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import FacesRow from "./photos/faces/FacesRow";
import FacesSwitch from "./photos/faces/FacesSwitch";
import PeopleFaces from "./photos/faces/PeopleFaces";
import PersonFaces from "./photos/faces/PersonFaces";
import peoplePage from "./photos/people/PeoplePage.tsx?raw";
import personPage from "./photos/people/PersonPage.tsx?raw";
import BeeldResults from "./search/beeld/BeeldResults";
import BeeldSwitch from "./search/beeld/BeeldSwitch";
import infoPanel from "./viewer/InfoPanel.tsx?raw";
import type { ViewerItem } from "./viewer/types";

// De lege plekken van X2 (gezichten) en X3 (zoeken op beeld) in bestanden van Z4 en Z6 (Z0b,
// review contract F3). Elk deel van X2 en X3 staat in een eigen bestand onder src/photos/faces/ of
// src/search/beeld/ (van X2 en X3), en de pagina van Z4 of Z6 laadt het lazy met het gebied. Zo
// hoeft X2 of X3 de bestanden van Z4 en Z6 niet open. Wie zo'n pagina herschrijft (Z6 vult
// PeoplePage en PersonPage), houdt de plek: deze test eist hem in de bron.

describe("de plekken van X2 in de bestanden van Z6 en in het infopaneel", () => {
  it("laden het deel van X2 lazy, met het gebied photos_faces, in een LazySlot", () => {
    expect(peoplePage).toContain('lazyWithAreas(() => import("../faces/PeopleFaces"), ["photos_faces"])');
    expect(peoplePage).toMatch(/<LazySlot>\s*<PeopleFaces \/>\s*<\/LazySlot>/);
    expect(personPage).toContain(
      'lazyWithAreas<PersonFacesProps>(() => import("../faces/PersonFaces"), ["photos_faces"])',
    );
    expect(personPage).toMatch(/<LazySlot>\s*<PersonFaces personId=\{params\.id\} \/>\s*<\/LazySlot>/);
    expect(infoPanel).toContain(
      'lazyWithAreas<InfoRowProps>(() => import("../photos/faces/FacesRow"), ["photos_faces"])',
    );
    expect(infoPanel).toMatch(/showFacesRow\(kind, item, signedIn\) && \(\s*<LazySlot key=\{`gezichten-\$\{item\.id\}`\}>/);
  });
});

describe("elke plek van X2 en X3 toont niets tot zijn pakket hem vult", () => {
  it("ook de plekken voor Z4 (/zoeken) en Z6 (Account → Foto's), die nog geen pagina hebben", () => {
    const item: ViewerItem = { id: "n1", name: "strand.jpg", mime: "image/jpeg", size: 1, modified_at: "2024-06-01T12:00:00Z" };
    const html = [
      createElement(PeopleFaces),
      createElement(PersonFaces, { personId: "p1" }),
      createElement(FacesRow, { item, detail: undefined, kind: "image" }),
      createElement(FacesSwitch),
      createElement(BeeldSwitch),
      createElement(BeeldResults),
    ].map((el) => renderToStaticMarkup(el));
    expect(html).toEqual(["", "", "", "", "", ""]);
  });
});
