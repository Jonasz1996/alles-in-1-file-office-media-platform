import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { SEARCH_TAB_AREAS } from "../Beheer";
import { fmt, loadAreas, resetI18nForTests } from "../i18n";
import { BeeldBlock } from "./BeeldBlock";
import { FacesBlock } from "./FacesBlock";
import SearchTab, {
  checkText,
  indexRebuilding,
  photosRebuilding,
  POLL_MS,
  SearchView,
  textFraction,
} from "./SearchTab";
import type { AdminBeeld, AdminFaces, AdminSearch } from "./searchApi";

// De tab "Zoeken en foto's" op /beheer (Z7, Keuze 51), met onderaan de blokken "Gezichten" (X2)
// en "Zoeken op beeld" (X3) van Z0b (Keuze 55). Zonder X2 en X3 (faces en beeld null) houden die
// blokken precies hun kop en "komt later", zonder getallen of andere verzonnen stand.
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

// Laden: alleen de kop en "laden" (de eerste GET loopt nog)
const LOADING_NL =
  '<section aria-labelledby="admin-search-title"><h2 id="admin-search-title">Zoeken en foto&#x27;s</h2>' +
  '<p class="muted">Het overzicht van zoeken laden…</p></section>';

// Keuze 24: zonder OCR in de worker (caps ocr false, of media uit) zegt /beheer dit
const NIET_GEINSTALLEERD =
  "OCR: niet geïnstalleerd. De mediaworker meldt geen OCR: media staat uit, of het image is ouder. Scans wachten tot OCR er is.";

// De delen van Z7, in deze volgorde; "admin-search-rebuild" alleen met een herbouw
const PARTS = [
  "admin-search-engine",
  "admin-search-pause",
  "admin-search-index",
  "admin-search-failed",
  "admin-search-photos",
  "admin-search-hour",
  "admin-search-ocr",
  "admin-search-places",
  "admin-search-bench",
];

function state(over: Partial<AdminSearch> = {}): AdminSearch {
  return {
    engine: "fts",
    paused: false,
    index: { names_done: true, text_done: 3, text_total: 4, failed: 2 },
    rebuild: null,
    extract_in_sandbox: true,
    per_hour: { deadline_b: 5, deadline_c: 6, busy: 7 },
    ocr: { installed: false, on: false, pages_queued: 0 },
    places: { loaded: true, source: "GeoNames cities1000 (CC BY 4.0) en Natural Earth 5.1.1", date: "2026-11-01" },
    bench: { at: "2026-11-09T10:00:00+00:00", p95_owner_ms: 12.5, p95_all_ms: 48 },
    faces: null,
    beeld: null,
    check: null,
    ...over,
  };
}

const FACES: AdminFaces = {
  allowed: true,
  analyse: "ok",
  reason: null,
  models: [{ name: "detector", version: "1", sha_ok: true }],
  limits: null,
  users_on: 3,
  queue: { waiting: 40, done_hour: 12, waits_for: null },
  failed_by_code: [
    { code: "decode", n: 2 },
    { code: "timeout", n: 1 },
  ],
  faces: 1234,
  named: 56,
  db_bytes: 1000,
  bench: null,
  eta_s: null,
  wipe_waiting: false,
  quality: null,
};

const BEELD: AdminBeeld = {
  on: true,
  model: "ok",
  analyse: "ok",
  seen: 900,
  photos: 1000,
  failed_by_code: [{ code: "decode", n: 4 }],
  hour: { queries: 8, p95_ms: 300, slow: 0, down: 0, no_model: 0 },
  space: { db_bytes: 1, cache_bytes: 2, models_bytes: 3 },
  bench: null,
  quality: null,
};

function view(data: AdminSearch | null, error: unknown = null): string {
  return renderToStaticMarkup(createElement(SearchView, { data, error, reload: async () => {} }));
}

/** De ids van de secties, in volgorde. */
function sections(html: string): string[] {
  return [...html.matchAll(/<section aria-labelledby="([^"]+)"/g)].map((m) => m[1]);
}

/** De tekst van het element met data-search="name" (zonder de tags erin). */
function field(html: string, name: string): string {
  const m = new RegExp(`data-search="${name}"[^>]*>(.*?)</(dd|p|span)>`).exec(html);
  return m ? m[1].replace(/<[^>]+>/g, "") : "";
}

/** De tekst van de knop met data-search=name, en of die knop aria-pressed heeft. */
function button(html: string, name: string): [string, boolean] {
  const m = new RegExp(`<button([^>]*data-search="${name}"[^>]*)>(.*?)</button>`).exec(html);
  expect(m, name).not.toBeNull();
  return [m![2].replace(/<[^>]+>/g, ""), m![1].includes("aria-pressed")];
}

beforeEach(async () => {
  resetI18nForTests("nl");
  await loadAreas(["common", "admin", ...SEARCH_TAB_AREAS]);
});

afterEach(() => {
  resetI18nForTests("nl");
});

describe("de tab Zoeken en foto's met de blokken van X2 en X3 (Z0b)", () => {
  it("de tab neemt precies de gebieden van de twee blokken mee", () => {
    expect(SEARCH_TAB_AREAS).toEqual(["photos_faces", "beeld"]);
  });

  it("nl: onder het deel van Z7 eerst Gezichten, dan Zoeken op beeld, elk alleen met komt later", () => {
    const html = view(state());
    expect(html.endsWith(FACES_NL + BEELD_NL)).toBe(true);
    expect(sections(html)).toEqual(["admin-search-title", ...PARTS, "admin-faces-title", "admin-beeld-title"]);
  });

  it("en: dezelfde blokken in het Engels", async () => {
    resetI18nForTests("en");
    await loadAreas(["common", "admin", ...SEARCH_TAB_AREAS]);
    const html = view(state());
    expect(html.endsWith(FACES_EN + BEELD_EN)).toBe(true);
  });
});

describe("de tab Zoeken en foto's (Z7)", () => {
  it("de default export begint met laden en ververst elke 5 seconden", () => {
    expect(renderToStaticMarkup(createElement(SearchTab))).toBe(LOADING_NL);
    expect(view(null)).toBe(LOADING_NL);
    expect(POLL_MS).toBe(5000);
  });

  it("een fout bij verversen staat als alert boven de stand die er al was", () => {
    const html = view(state(), { code: "x", message: "Kapot" });
    expect(html).toContain('<p role="alert">Vernieuwen mislukt: Kapot</p>');
    expect(sections(html)).toEqual(["admin-search-title", ...PARTS, "admin-faces-title", "admin-beeld-title"]);
    expect(sections(view(null, { message: "Kapot" }))).toEqual(["admin-search-title"]);
  });

  it("toont alles wat GET /admin/search geeft", () => {
    const s = state();
    const html = view(s);
    expect(field(html, "engine")).toBe("FTS5 (snel)");
    expect(field(html, "sandbox")).toBe("in de zandbak van de mediaworker");
    expect(field(html, "paused")).toBe("loopt");
    expect(field(html, "names")).toBe("klaar");
    expect(field(html, "text")).toBe("3 van 4 bestanden ");
    expect(html).toContain('<progress max="1" value="0.75" aria-label="Tekst uit bestanden gelezen"></progress>');
    expect(field(html, "failed")).toBe("2 inhouden");
    expect(field(html, "check")).toBe("nog niet gedaan");
    expect(field(html, "hour-b")).toBe("5");
    expect(field(html, "hour-c")).toBe("6");
    expect(field(html, "hour-busy")).toBe("7");
    expect(html).toContain(NIET_GEINSTALLEERD);
    expect(field(html, "places")).toBe(
      `Geladen: GeoNames cities1000 (CC BY 4.0) en Natural Earth 5.1.1, gebouwd op ${fmt.date("2026-11-01")}.`,
    );
    expect(field(html, "bench")).toBe(
      `p95 eigenaar 12,5 ms, alles 48 ms (gemeten ${fmt.dateTime("2026-11-09T10:00:00+00:00")}).`,
    );
    // De knoppen: index herbouwen, mislukte opnieuw (er zijn er 2), de lijst, pauzeren, de foto's
    for (const knop of ["rebuild-index", "retry", "failed-toggle", "pause-button", "rebuild-hash", "rebuild-geo"]) {
      expect(html).toContain(`data-search="${knop}"`);
    }
    // Zonder OCR geen knoppen; zonder herbouw geen voortgang
    expect(html).not.toContain('data-search="ocr-button"');
    expect(html).not.toContain('data-search="rebuild-ocr"');
    expect(field(html, "ocr-queued")).toBe("0 inhouden (PDF&#x27;s en foto&#x27;s)");
    expect(html).not.toContain('data-search="rebuild"');
  });

  it("zonder bestanden met tekst geen voortgangsbalk; zonder mislukte geen knop en geen lijst", () => {
    const html = view(state({ index: { names_done: true, text_done: 0, text_total: 0, failed: 0 } }));
    expect(field(html, "text")).toBe("0 van 0 bestanden");
    expect(html).not.toContain("<progress");
    expect(field(html, "failed")).toBe("0 inhouden");
    expect(html).not.toContain('data-search="retry"');
    expect(html).not.toContain('data-search="failed-toggle"');
    expect(html).toContain("Niets mislukt.");
  });

  it("de wekelijkse search-check (I1): geen verschil, of een verschil in rood met wat Jonas doet", () => {
    const at = "2026-11-08T03:00:00+00:00";
    const help = "Herbouw de index (de knop hierboven).";
    const goed = view(state({ check: { at, sampled: 200, bad: 0 } }));
    expect(field(goed, "check")).toBe(`${fmt.dateTime(at)}: 200 bestanden vergeleken, geen verschil`);
    expect(goed).not.toContain("badge degraded");
    expect(goed).not.toContain(help);
    const fout = view(state({ check: { at, sampled: 200, bad: 3 } }));
    expect(fout).toContain('<dd data-search="check"><span class="badge degraded">');
    expect(field(fout, "check")).toBe(`${fmt.dateTime(at)}: 3 verschillen met de bron (200 vergeleken)`);
    expect(fout).toContain(help);
    // Na een verschil kijkt de app elk uur opnieuw (weekcheck.AGAIN): na de herbouw ziet Jonas binnen
    // een uur of het verschil terugkomt
    expect(fout).toContain("Na een verschil controleert de app elk uur opnieuw.");
    expect(fout).toContain("python -m app.cli search-triggers");
    // Enkelvoud, en duizendtallen
    expect(checkText({ at, sampled: 1, bad: 0 })).toBe(`${fmt.dateTime(at)}: 1 bestand vergeleken, geen verschil`);
    expect(checkText({ at, sampled: 1200, bad: 1 })).toBe(
      `${fmt.dateTime(at)}: 1 verschil met de bron (1.200 vergeleken)`,
    );
    expect(checkText(null)).toBe("nog niet gedaan");
  });

  it("met LIKE: de uitleg erbij", () => {
    const html = view(state({ engine: "like", extract_in_sandbox: false }));
    expect(field(html, "engine")).toBe("LIKE (langzamer)");
    expect(field(html, "sandbox")).toBe("buiten de zandbak (zonder mediaworker)");
    expect(html).toContain("Zoeken gaat nu met LIKE");
  });

  it("gepauzeerd: de knop zegt wat hij doet (hervatten), zonder aria-pressed, en de uitleg staat erbij", () => {
    // Een tekst die wisselt met de stand en aria-pressed samen zeggen het dubbel (een
    // schermlezer: "hervatten, ingedrukt"); de knop noemt de actie (review golf 4, contract F1)
    const html = view(state({ paused: true }));
    expect(field(html, "paused")).toBe("gepauzeerd");
    expect(button(html, "pause-button")).toEqual(["Achtergrondwerk hervatten", false]);
    expect(html).toContain("De index en OCR staan stil tot je hervat.");
    expect(button(view(state()), "pause-button")).toEqual(["Achtergrondwerk pauzeren", false]);
  });

  it("een herbouw van de index: voortgang, en de knop index herbouwen is weg", () => {
    const rebuild = { what: "text" as const, started_at: "2026-11-09T09:00:00+00:00", done: 0.42 };
    const html = view(state({ rebuild, index: { names_done: false, text_done: 3, text_total: 4, failed: 0 } }));
    expect(sections(html)).toEqual([
      "admin-search-title",
      "admin-search-engine",
      "admin-search-pause",
      "admin-search-index",
      "admin-search-rebuild",
      ...PARTS.slice(3),
      "admin-faces-title",
      "admin-beeld-title",
    ]);
    expect(field(html, "rebuild")).toBe(`De index: 42%, sinds ${fmt.dateTime(rebuild.started_at)} `);
    expect(html).toContain('<progress max="1" value="0.42" aria-label="Voortgang van het herbouwen"></progress>');
    expect(field(html, "names")).toBe("worden opnieuw opgebouwd");
    expect(html).not.toContain('data-search="rebuild-index"');
    expect(html).toContain('data-search="rebuild-hash"');
  });

  it("een herbouw van de duplicaten: voortgang, en de knoppen van de foto's zijn weg", () => {
    const rebuild = { what: "hash" as const, started_at: "2026-11-09T09:00:00+00:00", done: 0.1 };
    const html = view(state({ rebuild }));
    expect(field(html, "rebuild")).toBe(`Duplicaten: 10%, sinds ${fmt.dateTime(rebuild.started_at)} `);
    expect(html).not.toContain('data-search="rebuild-hash"');
    expect(html).not.toContain('data-search="rebuild-geo"');
    expect(html).toContain('data-search="rebuild-index"');
  });

  it("OCR zoals X1 het meldt: geïnstalleerd, aan, wat er in de rij staat, met de twee knoppen", () => {
    const uit = view(state({ ocr: { installed: true, on: false, pages_queued: 1 } }));
    expect(button(uit, "ocr-button")).toEqual(["OCR aanzetten", false]);
    expect(uit).toMatch(/data-search="rebuild-ocr"[^>]*>Alles opnieuw herkennen</);
    expect(uit).not.toContain(NIET_GEINSTALLEERD);
    expect(field(uit, "ocr-queued")).toBe("1 inhoud (een PDF of foto)");
    const aan = view(state({ ocr: { installed: true, on: true, pages_queued: 1234 } }));
    expect(button(aan, "ocr-button")).toEqual(["OCR uitzetten", false]);
    expect(aan).toMatch(/data-search="rebuild-ocr"[^>]*>Alles opnieuw herkennen</);
    expect(aan).toContain(
      '<dl class="admin-totals" data-search="ocr"><div><dt>Geïnstalleerd</dt><dd>ja</dd></div>' +
        '<div><dt>Aan</dt><dd>ja</dd></div><div><dt>In de rij</dt><dd data-search="ocr-queued">' +
        "1.234 inhouden (PDF&#x27;s en foto&#x27;s)</dd></div></dl>",
    );
  });

  it("OCR niet geïnstalleerd (ocr_missing, Keuze 24): de zin, en geen knoppen", () => {
    const html = view(state({ ocr: { installed: false, on: false, pages_queued: 7 } }));
    expect(html).toContain(NIET_GEINSTALLEERD);
    expect(html).not.toContain('data-search="ocr-button"');
    expect(html).not.toContain('data-search="rebuild-ocr"');
    expect(field(html, "ocr-queued")).toBe("7 inhouden (PDF&#x27;s en foto&#x27;s)");
  });

  it("zonder plaatsnamen en zonder meting: wat Jonas kan doen", () => {
    const html = view(state({ places: { loaded: false, source: null, date: null }, bench: null }));
    expect(field(html, "places")).toContain("Niet geladen. install.sh haalt ze op (PLAATSEN in .env)");
    expect(field(html, "bench")).toBe("Nog niet gemeten op deze machine.");
    expect(html).toContain("python -m app.cli bench --los");
  });

  it("de hulpjes: herbouwen van de index, van de foto's, en het deel tekst", () => {
    expect(indexRebuilding(state())).toBe(false);
    expect(indexRebuilding(state({ index: { names_done: false, text_done: 0, text_total: 0, failed: 0 } }))).toBe(true);
    for (const what of ["names", "text"] as const) {
      expect(indexRebuilding(state({ rebuild: { what, started_at: "x", done: 0 } }))).toBe(true);
      expect(photosRebuilding(state({ rebuild: { what, started_at: "x", done: 0 } }))).toBe(false);
    }
    for (const what of ["hash", "geo"] as const) {
      expect(photosRebuilding(state({ rebuild: { what, started_at: "x", done: 0 } }))).toBe(true);
      expect(indexRebuilding(state({ rebuild: { what, started_at: "x", done: 0 } }))).toBe(false);
    }
    expect(textFraction({ names_done: true, text_done: 1, text_total: 4, failed: 0 })).toBe(0.25);
    expect(textFraction({ names_done: true, text_done: 0, text_total: 0, failed: 0 })).toBeNull();
    expect(textFraction({ names_done: true, text_done: 9, text_total: 4, failed: 0 })).toBe(1);
  });

  it("en: de stand in het Engels, zonder sleutel of {naam} in beeld", async () => {
    resetI18nForTests("en");
    await loadAreas(["common", "admin", ...SEARCH_TAB_AREAS]);
    const html = view(state({ paused: true, rebuild: { what: "geo", started_at: "2026-11-09T09:00:00+00:00", done: 0.5 } }));
    expect(field(html, "engine")).toBe("FTS5 (fast)");
    expect(field(html, "text")).toBe("3 of 4 files ");
    expect(field(html, "paused")).toBe("paused");
    expect(field(html, "rebuild")).toMatch(/^Place, camera and date: 50%, since /);
    expect(html).not.toMatch(/admin\.search|\{[a-z]+\}/);
    expect(view(state())).not.toMatch(/admin\.search|\{[a-z]+\}/);
    const ocr = view(state({ ocr: { installed: true, on: true, pages_queued: 2 } }));
    expect(field(ocr, "ocr-queued")).toBe("2 contents (PDFs and photos)");
    expect(ocr).toMatch(/data-search="rebuild-ocr"[^>]*>Recognise everything again</);
    expect(ocr).not.toMatch(/admin\.search|\{[a-z]+\}/);
    expect(view(state())).toContain("OCR: not installed.");
    const at = "2026-11-08T03:00:00+00:00";
    expect(field(view(state()), "check")).toBe("not done yet");
    expect(checkText({ at, sampled: 200, bad: 0 })).toBe(`${fmt.dateTime(at)}: 200 files compared, no differences`);
    const fout = view(state({ check: { at, sampled: 200, bad: 1 } }));
    expect(field(fout, "check")).toBe(`${fmt.dateTime(at)}: 1 difference from the source (200 compared)`);
    expect(fout).toContain("Rebuild the index (the button above).");
    expect(fout).toContain("After a difference the app checks again every hour.");
    expect(fout).not.toMatch(/admin\.search|\{[a-z]+\}/);
  });
});

describe("de blokken Gezichten en Zoeken op beeld met een stand (alleen aantallen, Keuze 51)", () => {
  it("Gezichten: aantallen, nooit een naam, een gebruiker of een model", () => {
    const html = renderToStaticMarkup(createElement(FacesBlock, { faces: FACES }));
    expect(html).toBe(
      '<section aria-labelledby="admin-faces-title"><h3 id="admin-faces-title">Gezichten</h3>' +
        '<dl class="admin-totals" data-search="faces">' +
        "<div><dt>Gezichten</dt><dd>1.234</dd></div>" +
        "<div><dt>Waarvan benoemd</dt><dd>56</dd></div>" +
        "<div><dt>Gebruikers met gezichten aan</dt><dd>3</dd></div>" +
        "<div><dt>Foto&#x27;s in de rij</dt><dd>40</dd></div>" +
        "<div><dt>Mislukt</dt><dd>3</dd></div></dl></section>",
    );
  });

  it("Zoeken op beeld: bekeken, vragen het afgelopen uur en mislukt", () => {
    const html = renderToStaticMarkup(createElement(BeeldBlock, { beeld: BEELD }));
    expect(html).toBe(
      '<section aria-labelledby="admin-beeld-title"><h3 id="admin-beeld-title">Zoeken op beeld</h3>' +
        '<dl class="admin-totals" data-search="beeld">' +
        "<div><dt>Bekeken</dt><dd>900 van 1.000 foto&#x27;s</dd></div>" +
        "<div><dt>Vragen het afgelopen uur</dt><dd>8</dd></div>" +
        "<div><dt>Mislukt</dt><dd>4</dd></div></dl></section>",
    );
  });

  it("in de tab: de stand van X2 en X3 gaat naar hun blok", () => {
    const html = view(state({ faces: FACES, beeld: BEELD }));
    expect(html).toContain('data-search="faces"');
    expect(html).toContain('data-search="beeld"');
    expect(html).not.toContain("komt later");
  });
});
