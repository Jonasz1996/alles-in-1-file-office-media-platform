import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import installSh from "../../infra/single/install.sh?raw";
import { loadAreas, resetI18nForTests, t } from "./i18n";
import FacesRow from "./photos/faces/FacesRow";
import FacesSwitch from "./photos/faces/FacesSwitch";
import PeopleFaces from "./photos/faces/PeopleFaces";
import PersonFaces from "./photos/faces/PersonFaces";
import peoplePage from "./photos/people/PeoplePage.tsx?raw";
import personPage from "./photos/people/PersonPage.tsx?raw";
import BeeldResults, { BeeldLater, stage } from "./search/beeld/BeeldResults";
import beeldResults from "./search/beeld/BeeldResults.tsx?raw";
import BeeldSwitch from "./search/beeld/BeeldSwitch";
import { OFF } from "./search/beeld/status";
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
    ].map((el) => renderToStaticMarkup(el));
    expect(html).toEqual(["", "", "", "", ""]);
  });
});

// I1 (S8): in de stand beeld op /zoeken stond niets onder de knoppen. Nu één zin, zonder
// versienummer (sinds 0.8.1, I2: dat het uitstaat op deze server en dat de beheerder het kan
// aanzetten). X3b vult de plek met het raster; zolang de server zoeken op beeld niet kent (de
// analyse draait niet: GET /photos/beeld geeft 501, de stand off) staat er precies die zin, en
// zolang de stand er nog niet is niets (zonder DOM vraagt de plek hem niet).
describe("de stand beeld op /zoeken met de analyse uit", () => {
  beforeEach(() => vi.stubGlobal("window", { location: { pathname: "/zoeken", search: "?mode=image&q=strand" } }));
  afterEach(() => {
    vi.unstubAllGlobals();
    resetI18nForTests("nl");
  });

  it("nl: één zin dat zoeken op beeld uitstaat op deze server", async () => {
    resetI18nForTests("nl");
    await loadAreas(["beeld"]);
    expect(renderToStaticMarkup(createElement(BeeldResults))).toBe("");
    expect(stage(null)).toBe("wacht");
    expect(stage(OFF)).toBe("later");
    expect(renderToStaticMarkup(createElement(BeeldLater))).toBe(
      '<p class="muted">Zoeken op beeld staat uit op deze server. De beheerder kan het aanzetten.</p>',
    );
  });

  it("en: dezelfde zin in het Engels, en nergens een versienummer", async () => {
    resetI18nForTests("en");
    await loadAreas(["beeld"]);
    expect(renderToStaticMarkup(createElement(BeeldResults))).toBe("");
    const html = renderToStaticMarkup(createElement(BeeldLater));
    expect(html).toBe('<p class="muted">Search by image is off on this server. The administrator can turn it on.</p>');
    expect(html).not.toMatch(/\d/);
  });

  it("de plek toont de zin bij de stand off, en alleen het raster als de server beeld kent", () => {
    expect(beeldResults).toMatch(/if \(at === "wacht"\) return null;\s*if \(at === "later"\) return <BeeldLater \/>;/);
    for (const state of ["ok", "bezig", "uit", "beheer_uit", "niet_geinstalleerd", "weg", "fout"] as const) {
      expect(stage({ state, done: null })).toBe("raster");
    }
  });
});

// Review I2 (V2, U2): de zin voor iedereen noemt geen pad, bestand of variabele van de server. De
// zinnen op /beheer zeggen niet dat ANALYSE=uit de oorzaak is: de blokken zijn ook leeg als de
// analyse aan staat maar niet helemaal geïnstalleerd is (PostgreSQL, geweigerde mappen, geen map
// met sleutels). Ze wijzen naar de regel in de eindtekst van het installatiescript die zegt waarom.
describe("de zinnen als de server gezichten of zoeken op beeld niet kent (review I2, V2 en U2)", () => {
  afterEach(() => resetI18nForTests("nl"));

  for (const taal of ["nl", "en"] as const) {
    it(`${taal}: geen pad of variabele voor iedereen, de eindtekst voor de beheerder`, async () => {
      resetI18nForTests(taal);
      await loadAreas(["beeld", "search", "photos_faces"]);
      for (const zin of [t("beeld.later"), t("search.field.imageLater")]) {
        for (const woord of ["/opt/platform", ".env", "ANALYSE"]) expect(zin).not.toContain(woord);
      }
      for (const zin of [t("beeld.notReady"), t("photos_faces.notReady")]) {
        expect(zin).not.toContain("ANALYSE=uit in");
        expect(zin).not.toContain(".env");
        expect(zin).toContain("Gezichten (de container analyse)");
        expect(zin).toContain("ANALYSE=aan");
      }
    });
  }

  it("de regel waar de zinnen naar wijzen, staat zo in de eindtekst van install.sh", () => {
    expect(installSh).toContain('echo "Gezichten (de container analyse): uit (ANALYSE=uit)."');
    expect(installSh).toContain('echo "Gezichten (de container analyse): uit, want $ANALYSE_REASON."');
  });
});
