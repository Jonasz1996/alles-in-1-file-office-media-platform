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
  progressOf,
  runs,
  SearchView,
  textFraction,
  withProgress,
} from "./SearchTab";
import searchTabSource from "./SearchTab.tsx?raw";
import type { AdminBeeld, AdminFaces, AdminSearch } from "./searchApi";

// De tab "Zoeken en foto's" op /beheer (Z7, Keuze 51), met onderaan de blokken "Gezichten" (X2)
// en "Zoeken op beeld" (X3) van Z0b (Keuze 55). Zonder X2 en X3 (faces en beeld null) houden die
// blokken precies hun kop en één zin, zonder getallen of andere verzonnen stand: sinds 0.8.1 (I2)
// dat het niet draait en dat de eindtekst van het installatiescript zegt waarom (review I2, U2), in
// plaats van "komt later".
const FACES_NL =
  '<section aria-labelledby="admin-faces-title"><h3 id="admin-faces-title">Gezichten</h3>' +
  '<p class="muted">Gezichten herkennen draait niet op deze server: de analyse staat uit, of is niet helemaal geïnstalleerd. Waarom staat in de eindtekst van het installatiescript, bij Gezichten (de container analyse). Stond daar ANALYSE=uit, draai het script dan opnieuw met ANALYSE=aan; daarna zet ieder het zelf aan bij Account → Foto&#x27;s. Zie docs/runbooks/analyse.md.</p></section>';
const BEELD_NL =
  '<section aria-labelledby="admin-beeld-title"><h3 id="admin-beeld-title">Zoeken op beeld</h3>' +
  '<p class="muted">Zoeken op beeld draait niet op deze server: de analyse staat uit, of is niet helemaal geïnstalleerd. Waarom staat in de eindtekst van het installatiescript, bij Gezichten (de container analyse). Stond daar ANALYSE=uit, draai het script dan opnieuw met ANALYSE=aan; daarna zet ieder het zelf aan bij Account → Foto&#x27;s. Zie docs/runbooks/analyse.md.</p></section>';
const FACES_EN =
  '<section aria-labelledby="admin-faces-title"><h3 id="admin-faces-title">Faces</h3>' +
  '<p class="muted">Face recognition is not running on this server: the analysis is off, or not fully installed. The end text of the install script says why, at Gezichten (de container analyse). If it says ANALYSE=uit, run the script again with ANALYSE=aan; then everyone turns it on for themselves under Account → Photos. See docs/runbooks/analyse.md.</p></section>';
const BEELD_EN =
  '<section aria-labelledby="admin-beeld-title"><h3 id="admin-beeld-title">Search by image</h3>' +
  '<p class="muted">Search by image is not running on this server: the analysis is off, or not fully installed. The end text of the install script says why, at Gezichten (de container analyse). If it says ANALYSE=uit, run the script again with ANALYSE=aan; then everyone turns it on for themselves under Account → Photos. See docs/runbooks/analyse.md.</p></section>';

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
    index: { names_done: true, text_done: 3, text_total: 4, failed: 2, capped: 0 },
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
  keys_missing: 0,
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

  it("nl: onder het deel van Z7 eerst Gezichten, dan Zoeken op beeld, elk alleen met de zin dat het uitstaat", () => {
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
    const html = view(state({ index: { names_done: true, text_done: 0, text_total: 0, failed: 0, capped: 0 } }));
    expect(field(html, "text")).toBe("0 van 0 bestanden");
    expect(html).not.toContain("<progress");
    expect(field(html, "failed")).toBe("0 inhouden");
    expect(html).not.toContain('data-search="retry"');
    expect(html).not.toContain('data-search="failed-toggle"');
    expect(html).toContain("Niets mislukt.");
    expect(html).not.toContain('data-search="capped"');
  });

  it("ingekorte documenten (de index op PostgreSQL, 1 MB per document) alleen als er zijn", () => {
    const html = view(state({ index: { names_done: true, text_done: 4, text_total: 4, failed: 0, capped: 2 } }));
    expect(field(html, "capped")).toBe("2 bestanden: te groot voor de index, het eind is niet vindbaar");
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
    // De wekelijkse search-check loopt dan niet: geen "nog niet gedaan" en geen oude uitkomst
    // (review I1, F5)
    expect(field(html, "check")).toBe("loopt alleen met FTS5");
    const at = "2026-11-08T03:00:00+00:00";
    const oud = view(state({ engine: "like", check: { at, sampled: 200, bad: 3 } }));
    expect(field(oud, "check")).toBe("loopt alleen met FTS5");
    expect(oud).not.toContain('<dd data-search="check"><span class="badge degraded">');
    expect(oud).not.toContain("Herbouw de index (de knop hierboven).");
    // Tijdens een herbouw zoekt de app ook met LIKE: dan blijft de laatste uitkomst staan
    const index = { names_done: false, text_done: 3, text_total: 4, failed: 0, capped: 0 };
    const bezig = view(state({ engine: "like", index, check: { at, sampled: 200, bad: 3 } }));
    expect(field(bezig, "check")).toBe(`${fmt.dateTime(at)}: 3 verschillen met de bron (200 vergeleken)`);
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
    const html = view(state({ rebuild, index: { names_done: false, text_done: 3, text_total: 4, failed: 0, capped: 0 } }));
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
    expect(indexRebuilding(state({ index: { names_done: false, text_done: 0, text_total: 0, failed: 0, capped: 0 } }))).toBe(true);
    for (const what of ["names", "text"] as const) {
      expect(indexRebuilding(state({ rebuild: { what, started_at: "x", done: 0 } }))).toBe(true);
      expect(photosRebuilding(state({ rebuild: { what, started_at: "x", done: 0 } }))).toBe(false);
    }
    for (const what of ["hash", "geo"] as const) {
      expect(photosRebuilding(state({ rebuild: { what, started_at: "x", done: 0 } }))).toBe(true);
      expect(indexRebuilding(state({ rebuild: { what, started_at: "x", done: 0 } }))).toBe(false);
    }
    expect(textFraction({ names_done: true, text_done: 1, text_total: 4, failed: 0, capped: 0 })).toBe(0.25);
    expect(textFraction({ names_done: true, text_done: 0, text_total: 0, failed: 0, capped: 0 })).toBeNull();
    expect(textFraction({ names_done: true, text_done: 9, text_total: 4, failed: 0, capped: 0 })).toBe(1);
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
    expect(field(view(state({ engine: "like" })), "check")).toBe("runs only with FTS5");
    expect(fout).not.toMatch(/admin\.search|\{[a-z]+\}/);
  });
});

describe("de blokken Gezichten (Keuze 67) en Zoeken op beeld (alleen aantallen, Keuze 51) met een stand", () => {
  it("Gezichten (X2b, Keuze 67): de analyse, de modellen, de rij en aantallen; nooit een naam of een gebruiker", () => {
    const html = renderToStaticMarkup(createElement(FacesBlock, { faces: FACES }));
    expect(html).toBe(
      '<section aria-labelledby="admin-faces-title"><h3 id="admin-faces-title">Gezichten</h3>' +
        '<dl class="admin-totals" data-search="faces">' +
        '<div><dt>Analyse</dt><dd data-search="faces-analyse">werkt</dd></div>' +
        '<div><dt>Modellen</dt><dd data-search="faces-models">detector, versie 1: sha256 klopt</dd></div>' +
        '<div><dt>Grenzen</dt><dd data-search="faces-limits">zonder grenzen</dd></div>' +
        '<div><dt>Gebruikers met gezichten aan</dt><dd data-search="faces-users">3</dd></div>' +
        '<div><dt>Rij analyse</dt><dd data-search="faces-queue">40 wachtend, 12 in het laatste uur</dd></div>' +
        '<div><dt>Mislukt</dt><dd data-search="faces-failed">decode 2, timeout 1</dd></div>' +
        '<div><dt>Totaal</dt><dd data-search="faces-total">1.234 gezichten, waarvan 56 benoemd; ±1000 B in de database</dd></div>' +
        '<div><dt>Snelheid</dt><dd data-search="faces-speed">niet gemeten (app.cli analyse-bench)</dd></div>' +
        '<div><dt>Kwaliteit</dt><dd data-search="faces-quality">niet gemeten</dd></div></dl>' +
        '<p><label class="check"><input type="checkbox" data-search="faces-allowed" checked=""/>' +
        "Gezichtsherkenning toestaan</label></p>" +
        '<p class="muted">Reservekopieën die het platform maakt, bevatten geen gezichten. Maak je zelf snapshots ' +
        "van /data (ZFS, restic), dan staan ze daar versleuteld in. Na &#x27;uit&#x27; zijn ze daar niet meer te " +
        "lezen, zolang de map met sleutels niet in die snapshots zit.</p>" +
        '<p><button type="button" class="btn" data-search="faces-retry">Mislukte opnieuw</button></p></section>',
    );
  });

  // X3b (Keuze 82): het hele blok, met de drie knoppen, het model, de container, mislukt per code,
  // het afgelopen uur, de ruimte, bench en kwaliteit, en de vaste zin over snapshots
  it("Zoeken op beeld: de stand, de knoppen, aantallen per code en de vaste zin", () => {
    const html = renderToStaticMarkup(createElement(BeeldBlock, { beeld: BEELD }));
    expect(html).toBe(
      '<section aria-labelledby="admin-beeld-title"><div class="admin-head"><h3 id="admin-beeld-title">Zoeken op beeld</h3></div>' +
        '<p data-search="beeld-on">Zoeken op beeld: <span class="badge ok">aan</span></p>' +
        '<p><button type="button" class="btn" data-search="beeld-switch">Zet uit voor iedereen</button> ' +
        '<button type="button" class="btn" data-search="beeld-rebuild">Opnieuw berekenen</button> ' +
        '<button type="button" class="btn danger" data-search="beeld-wipe">Beeldindex wissen</button></p>' +
        '<dl class="admin-totals" data-search="beeld">' +
        '<div><dt>Model</dt><dd data-search="beeld-model">SigLIP 2 B/32-256 (Google, Apache-2.0), int8. In orde.</dd></div>' +
        '<div><dt>Container analyse</dt><dd data-search="beeld-analyse">draait</dd></div>' +
        '<div><dt>Bekeken</dt><dd data-search="beeld-seen">900 van 1.000 foto&#x27;s</dd></div>' +
        '<div><dt>Mislukt</dt><dd data-search="beeld-failed">decode: 4</dd></div>' +
        '<div><dt>Het afgelopen uur</dt><dd data-search="beeld-hour">8 vragen, p95 300 ms; terug naar gewoon zoeken: ' +
        "0 keer te traag, 0 keer zonder container, 0 keer zonder model</dd></div>" +
        '<div><dt>Ruimte</dt><dd data-search="beeld-space">1 B in de database, 2 B in de cache, 3 B aan modellen</dd></div>' +
        '<div><dt>beeld-bench</dt><dd data-search="beeld-bench">nog niet gedaan</dd></div>' +
        '<div><dt>beeld-kwaliteit</dt><dd data-search="beeld-quality">nog niet gedaan</dd></div></dl>' +
        '<p class="muted">De vectoren beschrijven wat er op een foto staat. Reservekopieën die het platform maakt, ' +
        "bevatten ze niet. Maak je zelf snapshots van /data (ZFS, restic), dan staan ze daar wel in, tot je die " +
        "snapshot weggooit.</p></section>",
    );
  });

  it("in de tab: de stand van X2 en X3 gaat naar hun blok", () => {
    const html = view(state({ faces: FACES, beeld: BEELD }));
    expect(html).toContain('data-search="faces"');
    expect(html).toContain('data-search="beeld"');
    expect(html).not.toContain("komt later");
  });
});

describe("search.progress op de tab (0.8.2, z0-web.md Open 6)", () => {
  const loopt = state({ rebuild: { what: "geo", started_at: "2026-11-09T09:00:00+00:00", done: 0.25 } });

  it("alleen een search.progress met what en een eindig getal telt", () => {
    expect(progressOf({ type: "search.progress", data: { what: "geo", done: 0.5 } })).toEqual({ what: "geo", done: 0.5 });
    expect(progressOf({ type: "search.ready", data: { what: "geo", done: 0.5 } })).toBeNull();
    expect(progressOf({ type: "search.progress", data: { what: "geo" } })).toBeNull();
    expect(progressOf({ type: "search.progress", data: { what: "geo", done: Number.NaN } })).toBeNull();
    expect(progressOf({ type: "search.progress", data: { what: 3, done: 0.5 } })).toBeNull();
  });

  it("de voortgang van de herbouw die de tab toont, meteen; de rest wacht op de volgende GET", () => {
    const nu = withProgress(loopt, { what: "geo", done: 0.6 });
    expect(nu.rebuild).toEqual({ what: "geo", started_at: "2026-11-09T09:00:00+00:00", done: 0.6 });
    expect(loopt.rebuild?.done).toBe(0.25); // de stand van de GET blijft zoals hij was
    expect(view(nu)).toContain('value="0.6"');
    // Een ander soort, de backfill, of geen herbouw: niets veranderd (hetzelfde object)
    expect(withProgress(loopt, { what: "hash", done: 0.9 })).toBe(loopt);
    expect(withProgress(loopt, { what: "backfill", done: 0.9 })).toBe(loopt);
    const geen = state();
    expect(withProgress(geen, { what: "geo", done: 0.9 })).toBe(geen);
    // Hetzelfde getal: hetzelfde object (geen nieuwe render); buiten 0–1 begrensd
    expect(withProgress(loopt, { what: "geo", done: 0.25 })).toBe(loopt);
    expect(withProgress(loopt, { what: "geo", done: 1.5 }).rebuild?.done).toBe(1);
    expect(withProgress(loopt, { what: "geo", done: -1 }).rebuild?.done).toBe(0);
  });

  it("de tab luistert naar de stroom en blijft elke 5 s vragen", () => {
    expect(searchTabSource).toContain("useServerEvents((event) => {");
    expect(searchTabSource).toContain("const progress = progressOf(event);");
    expect(searchTabSource).toContain("usePolling(adminSearch, POLL_MS)");
  });
});

describe("acties die elkaar overlappen in één blok (0.8.2)", () => {
  it("het blok blijft bezig tot de laatste actie klaar is, ook als de eerste eerder eindigt", () => {
    const gezet: (string | null)[] = [];
    const open = runs((key) => gezet.push(key));
    // De lijst tonen, en "Meer laden" terwijl de lijst nog ververst
    open.start("list");
    open.start("more");
    open.end(); // de lijst is klaar: het blok is nog bezig met "Meer laden"
    expect(gezet).toEqual(["list", "more"]);
    open.end(); // "Meer laden" klaar: nu pas niet meer bezig (useFocusAfter geeft de focus terug)
    expect(gezet).toEqual(["list", "more", null]);
    // Daarna gewoon weer: één actie, bezig en klaar
    open.start("retry");
    open.end();
    expect(gezet).toEqual(["list", "more", null, "retry", null]);
  });

  it("idle zegt of er nog een actie loopt (het effect van een vorige render neemt de knop niet mee)", () => {
    const open = runs(() => undefined);
    expect(open.idle()).toBe(true);
    open.start("list");
    open.end(); // de lijst klaar; het effect van die render loopt pas na de volgende toets
    open.start("more"); // "Meer laden" ingedrukt
    expect(open.idle()).toBe(false); // dat effect laat de ingedrukte knop staan
    open.end();
    expect(open.idle()).toBe(true);
    expect(searchTabSource).toContain("useFocusAfter(busy !== null, heading, open.idle)");
    expect(searchTabSource).toContain("if (busy || !idle()) return;");
  });

  it("useAction zet bezig alleen via runs, niet bij het einde van elke actie", () => {
    expect(searchTabSource).toContain("const [open] = useState(() => runs(setBusy));");
    expect(searchTabSource).toContain("open.end();");
    expect(searchTabSource.split("setBusy(null)").length - 1).toBe(1); // alleen in runs
  });
});
