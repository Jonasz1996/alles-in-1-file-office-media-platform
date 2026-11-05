// De regels van scripts/budget.mjs voor fase 6 (Z0 en Z0b): de grenzen, de pagina's, de lazy
// plekken en de delen van gezichten (X2) en zoeken op beeld (X3). Exacte lijsten: een regel erbij
// of eraf laat deze test falen. Of de build binnen de grenzen blijft, meet `npm run budget` zelf.

import { readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { LIMITS, ZOEK_GROUPS, ZOEK_INITIAL, ZOEK_MODULE, ZOEK_PAGES, ZOEK_SLOTS } from "./budget.mjs";

const KB = 1000;
const SRC = fileURLToPath(new URL("../src", import.meta.url));

/** De bestanden in een map van src, als pad vanaf de web-map ("src/viewer/LikeRow.tsx"). */
function files(dir) {
  return readdirSync(`${SRC}/${dir}`)
    .map((name) => `src/${dir}/${name}`)
    .sort();
}

describe("de regels van het budget (fase 6)", () => {
  it("de grenzen: precies deze, met faces, beeld en likeRow van Z0b", () => {
    expect(LIMITS).toEqual({
      initialJs: 100 * KB,
      filesRoute: 45 * KB,
      lazyChunk: 50 * KB,
      heavyChunk: 60 * KB,
      initialCss: 12 * KB,
      sw: 8 * KB,
      mediaRoute: 45 * KB,
      player: 35 * KB,
      music: 40 * KB,
      search: 45 * KB,
      mapRoute: 30 * KB,
      mapData: 60 * KB,
      dupes: 45 * KB,
      people: 45 * KB,
      faces: 12 * KB,
      beeld: 15 * KB,
      likeRow: 3 * KB,
    });
  });

  it("de pagina's van fase 6 met hun woordenboeken en hun grens", () => {
    expect(ZOEK_PAGES).toEqual({
      "src/search/SearchPage.tsx": [["browser", "sharing", "search"], "search"],
      "src/photos/map/MapPage.tsx": [["photos_map"], "mapRoute"],
      "src/dupes/DupesPage.tsx": [["dupes"], "dupes"],
      "src/photos/people/PeoplePage.tsx": [["photos_people"], "people"],
      "src/photos/people/PersonPage.tsx": [["photos_people"], "people"],
    });
  });

  it("de delen van X2 en X3: gezichten, het raster van beeld en Meer zoals deze, met hun gebied", () => {
    expect(ZOEK_GROUPS.map(({ limit, area, pkg }) => [limit, area, pkg])).toEqual([
      ["faces", "photos_faces", "X2"],
      ["beeld", "beeld", "X3"],
      ["likeRow", "beeld", "X3"],
    ]);
    for (const { limit } of ZOEK_GROUPS) expect(LIMITS[limit]).toBeGreaterThan(0);
    const [faces, beeld, like] = ZOEK_GROUPS.map((g) => g.modules);
    expect(faces.test("src/photos/faces/FacesPage.tsx")).toBe(true);
    expect(faces.test("src/photos/people/PeoplePage.tsx")).toBe(false);
    expect(beeld.test("src/search/beeld/Grid.tsx")).toBe(true);
    expect(beeld.test("src/search/types.ts")).toBe(false);
    expect(like.test("src/viewer/LikeRow.tsx")).toBe(true);
    expect(like.test("src/viewer/PeopleRow.tsx")).toBe(false);
  });

  it("de lazy plekken zijn precies Plek, Personen, Meer zoals deze en de waarschuwing bij delen", () => {
    const slots = [...files("viewer"), ...files("shares")].filter((f) => ZOEK_SLOTS.test(f));
    expect(slots).toEqual([
      "src/viewer/LikeRow.tsx",
      "src/viewer/LocationRow.tsx",
      "src/viewer/PeopleRow.tsx",
      "src/shares/LocationWarning.tsx",
    ]);
  });

  it("de modules van fase 6, ook gezichten en het raster van beeld; in de eerste lading alleen het zoekveld", () => {
    for (const m of ["src/photos/faces/api.ts", "src/search/beeld/Grid.tsx", "src/photos/people/api.ts", "src/dupes/api.ts"]) {
      expect(ZOEK_MODULE.test(m), m).toBe(true);
    }
    // De blokken op /beheer zitten in de chunk van de tab, niet bij de modules van fase 6
    for (const m of ["src/photos/PhotosPage.tsx", "src/admin/FacesBlock.tsx", "src/admin/BeeldBlock.tsx", "src/viewer/LikeRow.tsx"]) {
      expect(ZOEK_MODULE.test(m), m).toBe(false);
    }
    expect([...ZOEK_INITIAL].sort()).toEqual(["src/search/AppBarSearch.tsx", "src/search/appBar.ts", "src/search/types.ts"]);
  });
});
