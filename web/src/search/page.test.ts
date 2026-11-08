import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it } from "vitest";
import { loadAreas, resetI18nForTests } from "../i18n";
import { AppBarSearch } from "./AppBarSearch";
import appBarSource from "./AppBarSearch.tsx?raw";
import { optionsOf } from "./combo";
import { chipLabel, FilterBar, typeLabel } from "./FilterBar";
import { appendHits, isPhoto } from "./hits";
import { Notices, notices, Snippet } from "./Notices";
import searchPageSource from "./SearchPage.tsx?raw";
import { SuggestList } from "./Suggest";
import type { Hit } from "./types";

const T1 = "7a7a7a7a-1111-4111-8111-111111111111";
const MAP = "0b0b0b0b-3333-4333-8333-333333333333";
const RIA = "5c5c5c5c-2222-4222-8222-222222222222";

beforeEach(async () => {
  resetI18nForTests("nl");
  await loadAreas(["shell", "browser", "search"]);
});

function hit(id: string, mime: string | null, kind: "file" | "dir" = "file"): Hit {
  return {
    node: { id, kind, name: id, mime } as Hit["node"],
    path: [{ id: "root", name: "" }],
    match: "name",
    snippet: null,
  };
}

describe("de snippet van een treffer (Keuze 47): alleen tekst, nooit HTML", () => {
  it("de woorden uit de vraag in <mark>, de rest als tekst", () => {
    const html = renderToStaticMarkup(
      createElement(Snippet, {
        parts: [
          { t: "het ", m: false },
          { t: "verslag", m: true },
          { t: " van maart", m: false },
        ],
      }),
    );
    expect(html).toBe('<span class="snippet">het <mark>verslag</mark> van maart</span>');
  });

  it("HTML in de tekst van een bestand blijft tekst, ook in een gemarkeerd stuk", () => {
    const html = renderToStaticMarkup(
      createElement(Snippet, {
        parts: [
          { t: '<img src=x onerror="alert(1)">', m: false },
          { t: "<b>vet</b>", m: true },
          { t: " & <script>x</script>", m: false },
        ],
      }),
    );
    expect(html).toBe(
      '<span class="snippet">&lt;img src=x onerror=&quot;alert(1)&quot;&gt;<mark>&lt;b&gt;vet&lt;/b&gt;</mark>' +
        " &amp; &lt;script&gt;x&lt;/script&gt;</span>",
    );
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<b>");
    expect(html).not.toContain("<script");
  });
});

describe("de meldingen bij de treffers (Keuze 9, 20 en 47)", () => {
  const base = { count: 5, partial: false, namesOnly: false, indexing: null };

  it("ronde A met treffers: geen melding; zonder treffers: Niets gevonden", () => {
    expect(notices(base)).toEqual([]);
    expect(notices({ ...base, count: 0 })).toEqual([{ key: "search.none" }]);
  });

  it("partial (ronde B of C): de tekst van Keuze 9, met of zonder treffers", () => {
    expect(notices({ ...base, partial: true })).toEqual([{ key: "search.partial" }]);
    expect(notices({ ...base, partial: true, count: 0 })).toEqual([{ key: "search.partialNone" }]);
  });

  it("names_only (ronde C): erbij dat alleen op naam gezocht is, niet als er niets is", () => {
    expect(notices({ ...base, partial: true, namesOnly: true })).toEqual([{ key: "search.partial" }, { key: "search.namesOnly" }]);
    expect(notices({ ...base, partial: true, namesOnly: true, count: 0 })).toEqual([{ key: "search.partialNone" }]);
  });

  it("de index is nog bezig: eerst, naar beneden afgerond (99,6% is nog niet klaar)", () => {
    expect(notices({ ...base, count: 0, indexing: { done: 0.996 } })).toEqual([
      { key: "search.indexing", vars: { pct: 99 } },
      { key: "search.none" },
    ]);
  });

  it("in één role=status, met de teksten van Keuze 9", () => {
    const html = renderToStaticMarkup(
      createElement(Notices, { list: notices({ count: 3, partial: true, namesOnly: true, indexing: { done: 0.43 } }) }),
    );
    expect(html).toBe(
      '<div role="status" class="search-notices">' +
        '<p class="muted">De index is nog bezig (43%)</p>' +
        '<p class="muted">Dit zijn de eerste treffers, niet per se de beste. Maak je vraag preciezer.</p>' +
        '<p class="muted">Alleen op naam gezocht: de tekst in de bestanden kwam niet aan de beurt.</p></div>',
    );
    // Ook zonder meldingen staat de status er (dan hoort een schermlezer de volgende)
    expect(renderToStaticMarkup(createElement(Notices, { list: [] }))).toBe('<div role="status" class="search-notices"></div>');
  });
});

describe("de chips en het paneel met filters (Keuze 43 en 47)", () => {
  const tags = [{ id: T1, name: "Vakantie", color: "", count: 4 }];

  it("wat op een chip staat", () => {
    expect(chipLabel({ field: "type", value: "pdf" }, tags)).toBe("PDF");
    expect(chipLabel({ field: "type", value: "other" }, tags)).toBe("Overig");
    expect(chipLabel({ field: "from", value: "2024-01-01" }, tags)).toBe("Vanaf 2024-01-01");
    expect(chipLabel({ field: "to", value: "2024-12-31" }, tags)).toBe("Tot en met 2024-12-31");
    expect(chipLabel({ field: "tag", value: T1 }, tags)).toBe("Vakantie");
    expect(chipLabel({ field: "tag", value: MAP }, tags)).toBe("Tag");
    expect(chipLabel({ field: "country", value: "NL" }, tags)).toBe("Land: NL");
    expect(chipLabel({ field: "camera", value: "fujifilm x100v" }, tags)).toBe("Camera: fujifilm x100v");
    expect(chipLabel({ field: "in", value: MAP }, tags)).toBe("In een map");
    expect(typeLabel("spreadsheet")).toBe("Rekenbladen");
  });

  it("elke chip met een knop × met een naam; het paneel met vinkjes per soort en per tag", () => {
    const html = renderToStaticMarkup(createElement(FilterBar, { query: { q: "x", type: ["pdf"], tag: [T1] }, tags }));
    expect(html).toContain('<ul class="search-chips" aria-label="Filters">');
    expect(html).toContain('aria-label="Filter weghalen: PDF"');
    expect(html).toContain('aria-label="Filter weghalen: Vakantie"');
    expect(html.match(/<li class="search-chip">/g)).toHaveLength(2);
    expect(html).toContain("<summary>Filters</summary>");
    // Negen soorten en één tag, de gezette aangevinkt
    expect(html.match(/type="checkbox"/g)).toHaveLength(10);
    expect(html.match(/type="checkbox" checked=""/g)).toHaveLength(2);
    expect(html).toContain("Vakantie (4)");
    expect(html.match(/type="date"/g)).toHaveLength(2);
  });

  it("een persoon (Z6, Keuze 38): op de chip zijn naam, een onbekende of verborgen persoon heet Persoon", () => {
    const people = [{ id: RIA, name: "Oma Ria", count: 3 }];
    expect(chipLabel({ field: "person", value: RIA }, tags, people)).toBe("Oma Ria");
    expect(chipLabel({ field: "person", value: MAP }, tags, people)).toBe("Persoon");
    expect(chipLabel({ field: "person", value: RIA }, tags)).toBe("Persoon");
  });

  it("met eigen personen een vinkje per persoon in het paneel; de gezette aangevinkt, met een chip", () => {
    const people = [
      { id: RIA, name: "Oma Ria", count: 3 },
      { id: MAP, name: "Opa Kees", count: 1 },
    ];
    const html = renderToStaticMarkup(createElement(FilterBar, { query: { person: [RIA] }, tags: [], people }));
    expect(html).toContain('aria-label="Filter weghalen: Oma Ria"');
    expect(html.match(/<li class="search-chip">/g)).toHaveLength(1);
    expect(html).toContain("<legend>Persoon</legend>");
    expect(html).toContain("Oma Ria (3)");
    expect(html).toContain("Opa Kees (1)");
    // Negen soorten en twee personen, één aangevinkt
    expect(html.match(/type="checkbox"/g)).toHaveLength(11);
    expect(html.match(/type="checkbox" checked=""/g)).toHaveLength(1);
  });

  it("een persoon in de groep bovenaan zet het filter person, zoals een plaats het filter place", () => {
    expect(searchPageSource).toContain("<Link to={queryHref(withPerson({}, p.id))}>");
    expect(searchPageSource).toContain("<Link to={queryHref({ place: Number(p.id) })}>");
    expect(searchPageSource).not.toContain("/fotos/personen/");
  });

  it("zonder filters geen lijst met chips, wel het paneel", () => {
    const html = renderToStaticMarkup(createElement(FilterBar, { query: { q: "x" }, tags: [] }));
    expect(html).not.toContain("search-chips");
    expect(html).toContain("<summary>Filters</summary>");
    expect(html.match(/type="checkbox"/g)).toHaveLength(9);
  });
});

describe("de treffers: Meer laden en de regel Geen foto met deze naam? (Keuze 81)", () => {
  it("een volgende pagina komt erachter, zonder een treffer twee keer", () => {
    const merged = appendHits([hit("a", "text/plain"), hit("b", "image/jpeg")], [hit("b", "image/jpeg"), hit("c", null, "dir")]);
    expect(merged.map((h) => h.node.id)).toEqual(["a", "b", "c"]);
  });

  it("een foto is een bestand met een mime image/…", () => {
    expect(isPhoto(hit("a", "image/jpeg"))).toBe(true);
    expect(isPhoto(hit("b", "application/pdf"))).toBe(false);
    expect(isPhoto(hit("c", null, "dir"))).toBe(false);
  });
});

describe("de stand beeld op /zoeken (Keuze 81): het raster van X3, lazy", () => {
  it("SearchPage laadt BeeldResults lazy met het gebied beeld, in een LazySlot", () => {
    expect(searchPageSource).toContain('lazyWithAreas(() => import("./beeld/BeeldResults"), ["beeld"])');
    expect(searchPageSource).toMatch(/<LazySlot>\s*<BeeldResults \/>\s*<\/LazySlot>/);
    // En nergens anders statisch
    expect(searchPageSource).not.toMatch(/import .*from "\.\/beeld\//);
  });

  it("zonder vraag in de stand beeld (de knop Zoeken op beeld) de focus in het veld, een frame na de kop", () => {
    expect(searchPageSource).toContain("if (!image || query.q) return;");
    expect(searchPageSource).toMatch(/requestAnimationFrame\(\(\) => \{[^}]*data-page-title[^}]*field\.current\?\.focus\(\)/s);
  });

  it("de keuze tussen de twee standen en de link onder de treffers", () => {
    expect(searchPageSource).toContain('role="radiogroup"');
    expect(searchPageSource).toContain('t("search.noPhoto")');
    expect(searchPageSource).toContain("queryHref(withMode(query, \"image\"))");
  });
});

describe("het veld bovenaan als combobox (Keuze 45 en 46)", () => {
  it("dicht: role=combobox, aria-expanded=false, zonder aria-controls of aria-activedescendant", () => {
    const html = renderToStaticMarkup(createElement(AppBarSearch));
    expect(html).toContain('id="zoekveld"');
    expect(html).toContain('role="combobox"');
    expect(html).toContain('aria-autocomplete="list"');
    expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain("aria-controls");
    expect(html).not.toContain("aria-activedescendant");
    expect(html).toContain('maxLength="200"');
    // De lijst laadt pas na de eerste toets
    expect(html).not.toContain("listbox");
  });

  it("de suggesties zijn een eigen chunk met het gebied search, niet in de eerste lading", () => {
    expect(appBarSource).toContain('Promise.all([import("./Suggest"), loadAreas(["search"])])');
    // Zonder lazy() en Suspense: React zou het tonen van de lijst tot 300 ms na de eerste toets
    // tegenhouden (de eerste suggestie in e2e/perf/zoek_ui.py)
    expect(appBarSource).not.toContain("<Suspense");
    expect(appBarSource).not.toMatch(/=\s*lazy[<(]/);
    // Alleen typen uit Suggest.tsx en combo.ts; de modules die echt meekomen, precies deze
    const values = [...appBarSource.matchAll(/^import (?!type )[^;]*?from "([^"]+)";/gm)].map((m) => m[1]);
    expect(values).toEqual(["react", "../i18n", "../router", "../ui/keys", "./appBar"]);
  });

  it("de lijst: een listbox met groepen, ids voor aria-activedescendant, de actieve optie gekozen", () => {
    const options = optionsOf(
      {
        names: [{ id: "n1", name: "<b>verslag</b>.pdf", kind: "file" }],
        people: [],
        places: [],
        tags: [{ id: T1, name: "Vakantie", color: "" }],
        albums: [],
      },
      "ver",
      true,
    );
    const html = renderToStaticMarkup(
      createElement(SuggestList, { options, active: 1, onActive: () => {}, onChoose: () => {} }),
    );
    expect(html).toBe(
      '<div id="zoek-suggesties" role="listbox" aria-label="Suggesties" class="suggest">' +
        '<div role="group" aria-label="Namen"><div class="suggest-head" aria-hidden="true">Namen</div>' +
        '<div id="zoek-suggesties-0" role="option" aria-selected="false" class="suggest-option">&lt;b&gt;verslag&lt;/b&gt;.pdf</div></div>' +
        '<div role="group" aria-label="Tags"><div class="suggest-head" aria-hidden="true">Tags</div>' +
        '<div id="zoek-suggesties-1" role="option" aria-selected="true" class="suggest-option">Vakantie</div></div>' +
        '<div id="zoek-suggesties-2" role="option" aria-selected="false" class="suggest-option suggest-image">' +
        "Zoek &#x27;ver&#x27; op wat er op je foto&#x27;s staat</div></div>",
    );
  });
});
