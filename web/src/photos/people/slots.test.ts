import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import account from "../../Account.tsx?raw";
import { loadAreas, resetI18nForTests } from "../../i18n";
import shareDialog from "../../shares/ShareDialogPanel.tsx?raw";
import albumPage from "../AlbumPage.tsx?raw";
import photosPage from "../PhotosPage.tsx?raw";
import FilterBar from "./FilterBar";
import { NO_FILTERS } from "./filters";
import PhotoSettingsBlock from "./PhotoSettingsBlock";
import settingsBlock from "./PhotoSettingsBlock.tsx?raw";

// Waar Z6 zijn delen inhangt (fase 6): alles lazy, met het gebied photos_people, zodat de eerste
// lading en de route Bestanden niets van personen meenemen (scripts/budget.mjs telt dat na).

describe("de plekken van Z6", () => {
  it("Account laadt het blok Foto's lazy, niet voor een gast", () => {
    expect(account).toContain('lazyWithAreas(() => import("./photos/people/PhotoSettingsBlock"), ["photos_people"])');
    expect(account).toMatch(/user\.role !== "guest" && \(\s*<LazySlot>\s*<PhotoSettingsBlock \/>\s*<\/LazySlot>/);
  });

  it("het blok Foto's laadt de schakelaars van X2 en X3 lazy, met hun gebied", () => {
    expect(settingsBlock).toContain('lazyWithAreas(() => import("../faces/FacesSwitch"), ["photos_faces"])');
    expect(settingsBlock).toContain('lazyWithAreas(() => import("../../search/beeld/BeeldSwitch"), ["beeld"])');
    expect(settingsBlock).toMatch(/<FacesSwitch \/>/);
    expect(settingsBlock).toMatch(/<BeeldSwitch \/>/);
  });

  it("/fotos: de filters en herinneringen lazy, met de maanden geladen, en de knop Zoeken op beeld naar /zoeken?mode=image", () => {
    // Elk een eigen chunk met het gebied photos_people, gevraagd tegelijk met de maanden en getoond in
    // dezelfde commit als de tijdlijn (review golf 2, web F); alleen hun typen komen statisch mee
    expect(photosPage).toContain('const areas = loadAreas(["photos_people"]);');
    expect(photosPage).toContain('Promise.all([import("./people/FilterBar"), areas])');
    expect(photosPage).toContain('Promise.all([import("./Memories"), areas])');
    expect(photosPage).not.toMatch(/^import (?!type )[^;]*from "\.\/(people\/FilterBar|Memories)";/m);
    expect(photosPage).toContain("await topWait.current;");
    expect(photosPage).toContain('to="/zoeken?mode=image"');
    // De ingangen naar de kaart en de duplicaten (golf 3), elk een gewone link
    expect(photosPage).toContain('<Link to="/fotos/kaart" className="btn">');
    expect(photosPage).toContain('<Link to="/dubbel" className="btn">');
  });

  it("een album: de regel lazy, en de vraag pas bij delen (dynamisch geladen)", () => {
    expect(albumPage).toContain('lazyWithAreas<AlbumRuleProps>(() => import("./people/AlbumRule"), ["photos_people"])');
    expect(albumPage).toContain('await import("./people/shareQuestion")');
  });

  it("het deeldialoog van een album telt de items van de regel, lazy", () => {
    expect(shareDialog).toContain('() => import("../photos/people/RuleShareNote"),\n  ["photos_people"],');
    expect(shareDialog).toMatch(/node\.kind === "album" && \(\s*<LazySlot>\s*<RuleShareNote node=\{node\} \/>/);
  });
});

describe("de filters op /fotos", () => {
  beforeAll(async () => {
    resetI18nForTests("nl");
    await loadAreas(["photos_people"]);
  });
  afterEach(() => resetI18nForTests("nl"));

  it("zonder filters: geen teller en geen knop Filters wissen", async () => {
    await loadAreas(["photos_people"]);
    const html = renderToStaticMarkup(createElement(FilterBar, { filters: NO_FILTERS, onChange: () => {} }));
    expect(html).toContain("<legend>Filters</legend>");
    expect(html).toContain('role="combobox"');
    expect(html).not.toContain("Filters wissen");
  });

  it("met een soort en een periode: de teller en Filters wissen", async () => {
    await loadAreas(["photos_people"]);
    const filters = { ...NO_FILTERS, type: "video" as const, from: "2024-01-01" };
    const html = renderToStaticMarkup(createElement(FilterBar, { filters, onChange: () => {} }));
    expect(html).toContain("2 filters aan");
    expect(html).toContain("Filters wissen");
    expect(html).toMatch(/<option value="video" selected="">Alleen video&#x27;s<\/option>/);
  });

  it("het blok Foto's toont eerst alleen zijn kop (de instellingen komen van de API)", async () => {
    await loadAreas(["photos_people"]);
    const html = renderToStaticMarkup(createElement(PhotoSettingsBlock));
    expect(html).toMatch(/<h2 id="[^"]+-kop">Foto&#x27;s<\/h2>/);
    expect(html).not.toContain("checkbox");
  });
});
