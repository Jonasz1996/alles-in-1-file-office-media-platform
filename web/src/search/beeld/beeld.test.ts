// Zoeken op beeld in de web (fase 6, X3b; Keuze 78–82): de vraag aan GET /search, de URL heen en
// terug, de pijlen in het raster, de meldingen, de teksten (alleen tekst, nooit HTML, geen
// versienummer), de foutcodes, de stand van GET /photos/beeld, de schakelaar, "Meer zoals deze" en
// het blok op /beheer. En met de analyse uit (501): precies wat 0.8.0 toonde.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import beeldBlockSource from "../../admin/BeeldBlock.tsx?raw";
import { BeeldBlock, hourText, modelKey } from "../../admin/BeeldBlock";
import type { AdminBeeld } from "../../admin/searchApi";
import { ApiError, api } from "../../api";
import { loadAreas, resetI18nForTests, t } from "../../i18n";
import beeldEn from "../../i18n/areas/beeld.en";
import beeldNl from "../../i18n/areas/beeld.nl";
import beeldErrors from "../../i18n/errors/beeld";
import likeRowSource from "../../viewer/LikeRow.tsx?raw";
import LikeRow, { LikeSection } from "../../viewer/LikeRow";
import apiPrefs from "../../../../api/app/prefs.py?raw";
import apiBeeldSearch from "../../../../api/app/beeld/search.py?raw";
import { IMAGE_SEARCH_READY, ImageHint, imageDescribedBy, setImageSearchReady } from "../ImageHint";
import imageHintSource from "../ImageHint.tsx?raw";
import { type PageQuery, queryHref, readQuery } from "../query";
import { BEELD_FALLBACKS, BEELD_STATES, MAX_OFFSET, type SearchPage } from "../types";
import { wireQuery } from "../wire";
import {
  columnsFor,
  filtered,
  gridLabel,
  imageParams,
  moveInGrid,
  nextImageParams,
  notices,
  PAGE,
  pct,
  type ResultState,
  rows,
} from "./beeld";
import beeldCss from "./beeld.css?raw";
import beeldSource from "./beeld.ts?raw";
import BeeldResults, { BeeldLater, BeeldNotices, stage } from "./BeeldResults";
import beeldResultsSource from "./BeeldResults.tsx?raw";
import BeeldSwitch, { BeeldCheck, showSwitch } from "./BeeldSwitch";
import beeldSwitchSource from "./BeeldSwitch.tsx?raw";
import { canLike, forgetBeeld, type Known, likeHref, loadBeeld, OFF, OFF_TTL_MS, TTL_MS } from "./status";
import statusSource from "./status.ts?raw";

const P1 = "4f1c0b2e-0000-4000-8000-000000000001";
const T1 = "7a7a7a7a-1111-4111-8111-111111111111";

function page(over: Partial<SearchPage> = {}): SearchPage {
  return {
    results: [],
    more: false,
    offset: 0,
    partial: false,
    ranked: true,
    names_only: false,
    after: null,
    groups: null,
    indexing: null,
    engine: "fts",
    mode: "image",
    fallback: null,
    ...over,
  };
}

function known(state: Known["state"], done: number | null = null): Known {
  return { state, done };
}

function result(over: Partial<ResultState> = {}): ResultState {
  return { known: known("ok"), last: page(), count: 5, filtered: false, code: null, ...over };
}

/** Alle teksten van een gebied, plat, met hun sleutel. */
function flat(dict: object, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(dict)) {
    if (typeof v === "string") out[prefix + k] = v;
    else Object.assign(out, flat(v as object, `${prefix}${k}.`));
  }
  return out;
}

beforeEach(async () => {
  resetI18nForTests("nl");
  await loadAreas(["beeld", "search"]);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  forgetBeeld();
  setImageSearchReady(false);
  resetI18nForTests("nl");
});

describe("de vraag aan GET /search in de stand beeld (Keuze 78 en 80)", () => {
  it("een vraag: mode image, alleen foto's, 48 per keer, nooit after", () => {
    const params = imageParams({ q: "strand", mode: "image" });
    expect(params).toEqual({ mode: "image", limit: PAGE, q: "strand", type: ["image"] });
    expect(PAGE).toBe(48);
    expect(wireQuery(params)).toBe("?mode=image&limit=48&q=strand&type=image");
    expect(params).not.toHaveProperty("after");
  });

  it("Meer zoals deze: like zonder type, zodat de terugval niet al je foto's geeft", () => {
    expect(imageParams({ mode: "image", like: P1 })).toEqual({ mode: "image", limit: PAGE, like: P1 });
  });

  it("q en like samen: q gaat voor (de API geeft anders 400)", () => {
    expect(imageParams({ mode: "image", q: "hond", like: P1 })).toEqual({
      mode: "image",
      limit: PAGE,
      q: "hond",
      type: ["image"],
    });
  });

  it("de filters gaan mee; een ander type wordt image", () => {
    const query: PageQuery = { mode: "image", like: P1, tag: [T1], from: "2024-01-01", type: ["pdf"] };
    expect(imageParams(query)).toEqual({
      tag: [T1],
      from: "2024-01-01",
      mode: "image",
      limit: PAGE,
      like: P1,
      type: ["image"],
    });
  });

  it("de volgende pagina met offset, tot de grens van 1.000", () => {
    const query: PageQuery = { mode: "image", q: "strand" };
    expect(nextImageParams(query, page({ more: false }))).toBeNull();
    expect(nextImageParams(query, page({ more: true, offset: 0 }))).toEqual({ ...imageParams(query), offset: 48 });
    expect(nextImageParams(query, page({ more: true, offset: 48 }))?.offset).toBe(96);
    expect(MAX_OFFSET).toBe(1000);
    expect(nextImageParams(query, page({ more: true, offset: 960 }))).toBeNull();
    expect(nextImageParams(query, page({ more: true, offset: 912 }))?.offset).toBe(960);
  });
});

describe("de URL heen en terug: mode en like (Keuze 81)", () => {
  it("readQuery(queryHref(x)) geeft x terug", () => {
    for (const query of [
      { mode: "image", q: "hond op het strand" },
      { mode: "image", like: P1 },
      { mode: "image", like: P1, tag: [T1] },
    ] satisfies PageQuery[]) {
      const href = queryHref(query);
      expect(readQuery(href.slice(href.indexOf("?")))).toEqual(query);
    }
  });

  it("likeHref is precies queryHref van mode image met like", () => {
    expect(likeHref(P1)).toBe(queryHref({ mode: "image", like: P1 }));
    expect(likeHref(P1)).toBe(`/zoeken?mode=image&like=${P1}`);
    expect(readQuery(likeHref(P1).slice("/zoeken".length))).toEqual({ mode: "image", like: P1 });
  });

  it("een like die geen id is, valt weg; zonder mode image ook", () => {
    expect(readQuery("?mode=image&like=../etc")).toEqual({ mode: "image" });
    expect(readQuery(`?like=${P1}`)).toEqual({});
  });
});

describe("de pijlen in het raster (Keuze 81)", () => {
  it("kolommen naar de breedte, minstens één", () => {
    expect(columnsFor(0, 160, 12)).toBe(1);
    expect(columnsFor(159, 160, 12)).toBe(1);
    expect(columnsFor(332, 160, 12)).toBe(2);
    // Vier cellen van 160 met drie keer 12 ertussen: 676
    expect(columnsFor(676, 160, 12)).toBe(4);
    expect(columnsFor(675, 160, 12)).toBe(3);
  });

  it("de rijen: de laatste is korter", () => {
    expect(rows(10, 4)).toEqual([
      [0, 1, 2, 3],
      [4, 5, 6, 7],
      [8, 9],
    ]);
    expect(rows(0, 4)).toEqual([]);
    expect(rows(3, 0)).toEqual([[0], [1], [2]]);
  });

  it("links, rechts, op, neer, Home en End", () => {
    // 10 foto's in 4 kolommen
    expect(moveInGrid(0, "right", 10, 4)).toBe(1);
    expect(moveInGrid(3, "right", 10, 4)).toBe(4);
    expect(moveInGrid(9, "right", 10, 4)).toBe(9);
    expect(moveInGrid(0, "left", 10, 4)).toBe(0);
    expect(moveInGrid(5, "left", 10, 4)).toBe(4);
    expect(moveInGrid(1, "up", 10, 4)).toBe(1);
    expect(moveInGrid(6, "up", 10, 4)).toBe(2);
    expect(moveInGrid(2, "down", 10, 4)).toBe(6);
    // De laatste rij is korter: neer gaat naar de laatste foto
    expect(moveInGrid(7, "down", 10, 4)).toBe(9);
    expect(moveInGrid(9, "down", 10, 4)).toBe(9);
    expect(moveInGrid(6, "home", 10, 4)).toBe(0);
    expect(moveInGrid(2, "end", 10, 4)).toBe(9);
  });

  it("PageUp en PageDown per drie rijen", () => {
    expect(moveInGrid(1, "pagedown", 48, 4)).toBe(13);
    expect(moveInGrid(13, "pageup", 48, 4)).toBe(1);
    expect(moveInGrid(45, "pagedown", 48, 4)).toBe(47);
  });

  it("het raster gebruikt de pijlen, Enter en Esc, en houdt de gewone sneltoetsen erbuiten", () => {
    expect(beeldResultsSource).toContain("moveTo(moveInGrid(focus, m, nodes.length, columns))");
    expect(beeldResultsSource).toMatch(/e\.key === "Enter"\) \{\s*e\.preventDefault\(\);\s*open\(focus\);/);
    expect(beeldResultsSource).toMatch(
      /e\.key === "Escape"\) \{\s*e\.preventDefault\(\);\s*document\.querySelector<HTMLInputElement>\("\.search-form input\[type=search\]"\)\?\.focus\(\);/,
    );
    expect(beeldResultsSource).toContain('role="grid" aria-label={label}');
    expect(beeldResultsSource).toContain('role="row" aria-rowindex={r + 1}');
    expect(beeldResultsSource).toContain('role="gridcell" data-index={index} tabIndex={tabbable ? 0 : -1}');
  });

  it("de focus gaat meteen naar de foto, niet een frame later (dan viel hij na een Esc terug)", () => {
    expect(beeldResultsSource).toMatch(
      /const moveTo = useCallback\(\(i: number\) => \{\s*setFocus\(i\);\s*grid\.current\?\.querySelector<HTMLElement>\(`\[data-index="\$\{i\}"\]`\)\?\.focus\(\);\s*\}, \[\]\);/,
    );
    expect(beeldResultsSource).not.toContain("requestAnimationFrame");
  });
});

describe("de meldingen in role=status (Keuze 81)", () => {
  it("niets bijzonders: geen melding", () => {
    expect(notices(result())).toEqual([]);
  });

  it("uit bij jou of bij de beheerder: alleen die melding", () => {
    expect(notices(result({ known: known("uit"), last: null }))).toEqual([{ key: "beeld.notice.offYou" }]);
    expect(notices(result({ known: known("beheer_uit"), last: null }))).toEqual([{ key: "beeld.notice.offAdmin" }]);
    // Ook met een oud antwoord of een code: alleen de reden waarom het uit staat
    expect(notices(result({ known: known("uit"), code: "beeld_no_vector", count: 0 }))).toEqual([
      { key: "beeld.notice.offYou" },
    ]);
  });

  it("je foto's worden nog bekeken: hoeveel procent, naar beneden afgerond", () => {
    expect(notices(result({ known: known("bezig", 0.437), last: null }))).toEqual([
      { key: "beeld.notice.indexing", vars: { pct: 43 } },
    ]);
    // Het antwoord van /search gaat voor de stand
    expect(notices(result({ known: known("bezig", 0.2), last: page({ indexing: { done: 0.5 } as never }) }))).toEqual([
      { key: "beeld.notice.indexing", vars: { pct: 50 } },
    ]);
    expect(pct(0.996)).toBe(99);
    expect(pct(1)).toBe(99);
    expect(pct(-1)).toBe(0);
    expect(pct(0)).toBe(0);
  });

  it("elke terugval met zijn eigen zin, en de treffers erbij", () => {
    for (const fallback of BEELD_FALLBACKS) {
      expect(notices(result({ last: page({ fallback }) }))).toEqual([{ key: `beeld.notice.${fallback}` }]);
    }
  });

  it("niets gevonden; met een filter misschien meer", () => {
    expect(notices(result({ count: 0 }))).toEqual([{ key: "beeld.notice.none" }]);
    expect(notices(result({ count: 0, filtered: true }))).toEqual([
      { key: "beeld.notice.none" },
      { key: "beeld.notice.few" },
    ]);
    expect(notices(result({ count: 7, filtered: true }))).toEqual([{ key: "beeld.notice.few" }]);
    // Er is nog meer, of de pagina is vol: geen "misschien meer"
    expect(notices(result({ count: 7, filtered: true, last: page({ more: true }) }))).toEqual([]);
    expect(notices(result({ count: PAGE, filtered: true }))).toEqual([]);
    expect(filtered({ q: "x", type: ["image"] })).toBe(false);
    expect(filtered({ q: "x", tag: [T1] })).toBe(true);
  });

  it("deze foto is nog niet bekeken (beeld_no_vector)", () => {
    expect(notices(result({ last: null, count: 0, code: "beeld_no_vector" }))).toEqual([{ key: "beeld.notice.noVector" }]);
  });

  it("de meldingen als tekst in één role=status, ook leeg", () => {
    expect(renderToStaticMarkup(createElement(BeeldNotices, { list: [] }))).toBe(
      '<div role="status" class="search-notices"></div>',
    );
    const list = notices(result({ known: known("bezig", 0.43), last: page({ fallback: "slow" }), count: 0 }));
    expect(renderToStaticMarkup(createElement(BeeldNotices, { list }))).toBe(
      '<div role="status" class="search-notices">' +
        '<p class="muted">Je foto&#x27;s worden nog bekeken: 43% klaar. Wat nog niet bekeken is, vind je nog niet.</p>' +
        '<p class="muted">Zoeken op beeld duurde te lang. Dit zijn de treffers op naam en tekst.</p>' +
        '<p class="muted">Geen foto&#x27;s gevonden die hierbij passen.</p></div>',
    );
  });

  it("het label van het raster: de vraag als tekst, of lijken op", () => {
    const label = gridLabel({ mode: "image", q: "<b>strand</b>" });
    expect(t(label.key, label.vars)).toBe("Foto's die passen bij '<b>strand</b>'");
    const like = gridLabel({ mode: "image", like: P1 });
    expect(t(like.key, like.vars)).toBe("Foto's die lijken op de gekozen foto");
  });
});

describe("de teksten van het gebied beeld", () => {
  const nl = flat(beeldNl);
  const en = flat(beeldEn);

  it("nl en en hebben dezelfde sleutels", () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(nl).sort());
  });

  it("alleen tekst: geen HTML, en nergens een versienummer", () => {
    for (const text of [...Object.values(nl), ...Object.values(en)]) {
      expect(text).not.toMatch(/[<>]/);
      expect(text).not.toMatch(/&[a-z#0-9]+;/i);
      expect(text).not.toMatch(/\b\d+\.\d+\.\d+\b/);
      expect(text).not.toMatch(/\bv\d/);
    }
  });

  it("de teksten van het ontwerp (Keuze 81)", () => {
    expect(nl["notice.indexing"]).toBe(
      "Je foto's worden nog bekeken: {pct}% klaar. Wat nog niet bekeken is, vind je nog niet.",
    );
    expect(nl["notice.none"]).toBe("Geen foto's gevonden die hierbij passen.");
    expect(nl["notice.few"]).toBe("Misschien zijn er meer. Maak de vraag ruimer of haal een filter weg.");
    expect(nl["notice.slow"]).toBe("Zoeken op beeld duurde te lang. Dit zijn de treffers op naam en tekst.");
    expect(nl["notice.down"]).toBe("Zoeken op beeld werkt nu niet. Dit zijn de treffers op naam en tekst.");
    expect(nl["notice.no_model"]).toBe(
      "Zoeken op beeld is nog niet klaar: het model ontbreekt. Dit zijn de treffers op naam en tekst.",
    );
    expect(nl["notice.offYou"]).toBe("Zoeken op beeld staat uit. Je zet het aan bij Account → Foto's.");
    expect(nl["notice.offAdmin"]).toBe("Zoeken op beeld staat uit voor dit platform.");
    expect(nl["notice.noVector"]).toBe("Deze foto is nog niet bekeken. Probeer het straks opnieuw.");
    expect(nl.like).toBe("Meer zoals deze");
    expect(`${nl["switch.label"]}. ${nl["switch.hint"]}`).toBe(
      "Zoeken op beeld. Je foto's worden op de achtergrond bekeken, zodat je ze kunt vinden op wat erop staat " +
        "('strand', 'hond'). Dat gebeurt op deze server; er gaat niets naar buiten. Alleen jij kunt zo in je eigen " +
        "foto's zoeken. Uit wist wat alleen voor jou bewaard is. Snapshots die de beheerder zelf van de server " +
        "maakt, houden het tot ze verlopen.",
    );
  });

  it("de code maakt nooit HTML van een tekst of een naam", () => {
    for (const source of [beeldResultsSource, beeldSwitchSource, beeldSource, statusSource, likeRowSource, beeldBlockSource]) {
      expect(source).not.toMatch(/dangerouslySetInnerHTML|innerHTML|insertAdjacentHTML|outerHTML/);
    }
    // Per foto de naam en de datum als tekst
    expect(beeldResultsSource).toContain('<span className="beeld-name">{node.name}</span>');
    expect(beeldResultsSource).toContain('<span className="beeld-date muted">{fmt.date(node.modified_at)}</span>');
    expect(beeldCss).not.toMatch(/content\s*:/);
  });
});

describe("de foutcodes (Keuze 80): wat de API echt geeft", () => {
  it("elke code in de catalogus komt uit app/beeld/search.py of app/prefs.py", () => {
    expect(Object.keys(beeldErrors).sort()).toEqual(["beeld_no_vector", "beeld_not_installed", "beeld_off"]);
    expect(apiBeeldSearch).toMatch(/errors\.fail\(409, "beeld_off"/);
    expect(apiBeeldSearch).toMatch(/errors\.fail\(\s*409, "beeld_no_vector"/);
    expect(apiPrefs).toMatch(/errors\.fail\(409, "beeld_off"/);
    expect(apiPrefs).toMatch(/"beeld_not_installed"/);
  });

  it("het raster kent beeld_off en beeld_no_vector; de schakelaar toont de reden van de server", () => {
    expect(beeldResultsSource).toContain('if (code === "beeld_off") forgetBeeld();');
    expect(beeldResultsSource).toContain('failed.code !== "beeld_off" && failed.code !== "beeld_no_vector"');
    expect(beeldSwitchSource).toContain("setError(errorText(e));");
  });
});

describe("de stand van GET /photos/beeld (status.ts)", () => {
  it("501 (de analyse staat uit) is de stand off, tien minuten bewaard", async () => {
    const request = vi.spyOn(api, "request").mockRejectedValue(new ApiError(501, "Nog niet gebouwd", "not_built"));
    expect(await loadBeeld("u1", 1000)).toEqual(OFF);
    expect(await loadBeeld("u1", 1000 + OFF_TTL_MS - 1)).toEqual(OFF);
    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith("GET", "/api/v1/photos/beeld");
    await loadBeeld("u1", 1000 + OFF_TTL_MS);
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("de stand van de server een halve minuut; een ander account vraagt opnieuw", async () => {
    const request = vi.spyOn(api, "request").mockResolvedValue({ state: "bezig", done: 0.4 });
    expect(await loadBeeld("u1", 0)).toEqual({ state: "bezig", done: 0.4 });
    await loadBeeld("u1", TTL_MS - 1);
    expect(request).toHaveBeenCalledTimes(1);
    await loadBeeld("u2", TTL_MS - 1);
    expect(request).toHaveBeenCalledTimes(2);
    await loadBeeld("u2", 2 * TTL_MS);
    expect(request).toHaveBeenCalledTimes(3);
  });

  it("een andere fout bewaart niets; forgetBeeld vergeet de stand", async () => {
    const request = vi.spyOn(api, "request").mockRejectedValueOnce(new ApiError(0, "Geen verbinding", "network"));
    await expect(loadBeeld("u1", 0)).rejects.toThrow("Geen verbinding");
    request.mockResolvedValue({ state: "ok", done: null });
    expect(await loadBeeld("u1", 1)).toEqual({ state: "ok", done: null });
    forgetBeeld();
    await loadBeeld("u1", 2);
    expect(request).toHaveBeenCalledTimes(3);
  });

  it("Meer zoals deze kan alleen bij ok of bezig", () => {
    expect(canLike(null)).toBe(false);
    expect(canLike(OFF)).toBe(false);
    expect(canLike(known("fout"))).toBe(false);
    expect(BEELD_STATES.filter((s) => canLike(known(s)))).toEqual(["ok", "bezig"]);
  });
});

describe("met de analyse uit: precies wat 0.8.0 toonde", () => {
  it("/zoeken in de stand beeld: niets tot de stand er is, dan de zin van 0.8.0", () => {
    vi.stubGlobal("window", { location: { pathname: "/zoeken", search: "?mode=image&q=strand" } });
    expect(renderToStaticMarkup(createElement(BeeldResults))).toBe("");
    expect(stage(OFF)).toBe("later");
    expect(renderToStaticMarkup(createElement(BeeldLater))).toBe(
      '<p class="muted">Zoeken op beeld komt in een volgende versie.</p>',
    );
    // Met de stand off vraagt het raster niets aan /search
    expect(beeldResultsSource).toContain("if (!known || off || blocked || !canSearch(query)) return;");
  });

  it("de uitleg bij het veld zegt dat het later komt, tot de server zoeken op beeld kent", () => {
    expect(IMAGE_SEARCH_READY).toBe(false);
    expect(imageDescribedBy()).toBe("zoek-beeld-later zoek-beeld-uitleg");
    expect(renderToStaticMarkup(createElement(ImageHint, {}))).toContain('<span id="zoek-beeld-later" hidden="">');
    // ImageHint vraagt dezelfde stand (dynamisch: de eerste lading blijft gelijk); alleen een
    // antwoord dat niet off is, zet hem om, een fout verandert niets
    expect(imageHintSource).toContain('import("./beeld/status")');
    expect(imageHintSource).not.toMatch(/^import .*beeld\//m);
    expect(imageHintSource).toMatch(/\(k\) => live && setImageSearchReady\(k\.state !== "off"\),\s*\(\) => \{\},/);
    expect(beeldResultsSource).not.toContain("ImageHint\"");
    setImageSearchReady(true);
    expect(imageDescribedBy()).toBe("zoek-beeld-uitleg");
    expect(renderToStaticMarkup(createElement(ImageHint, {}))).toBe(
      '<p id="zoek-beeld-uitleg" class="muted">Zoekt in je eigen foto&#x27;s. Druk op Enter.</p>',
    );
    setImageSearchReady(false);
    expect(imageDescribedBy()).toBe("zoek-beeld-later zoek-beeld-uitleg");
  });

  it("Account → Foto's: de schakelaar staat er niet, het vinkje van 0.8.0 blijft", () => {
    expect(renderToStaticMarkup(createElement(BeeldSwitch))).toBe("");
    expect(showSwitch(null)).toBe(false);
    expect(showSwitch(OFF)).toBe(false);
    expect(showSwitch(known("fout"))).toBe(false);
    expect(BEELD_STATES.filter((s) => showSwitch(known(s)))).toEqual([...BEELD_STATES]);
  });

  it("de viewer: geen Meer zoals deze", () => {
    const item = { id: P1, name: "strand.jpg", mime: "image/jpeg", size: 1, modified_at: "2024-06-01T12:00:00Z" };
    expect(renderToStaticMarkup(createElement(LikeRow, { item, detail: undefined, kind: "image" }))).toBe("");
    expect(likeRowSource).toContain("if (!can || own !== item.id) return null;");
  });

  it("/beheer: het blok zonder stand, zoals 0.8.0", () => {
    expect(renderToStaticMarkup(createElement(BeeldBlock, { beeld: null }))).toBe(
      '<section aria-labelledby="admin-beeld-title"><h3 id="admin-beeld-title">Zoeken op beeld</h3>' +
        '<p class="muted">Zoeken op beeld komt later in fase 6.</p></section>',
    );
  });
});

describe("de schakelaar in Account → Foto's (Keuze 81 en 82)", () => {
  it("het vinkje met de uitleg; standaard uit", () => {
    const html = renderToStaticMarkup(
      createElement(BeeldCheck, { known: known("ok"), on: false, error: null, onChange: () => {} }),
    );
    expect(html).toMatch(
      /^<div class="field"><label class="check"><input type="checkbox" aria-describedby="(\S+)-uitleg"\/>Zoeken op beeld<\/label><small id="\1-uitleg" class="muted">Je foto&#x27;s worden op de achtergrond bekeken, /,
    );
    expect(html).not.toContain("checked");
    expect(html).not.toContain("role=\"alert\"");
  });

  it("uit bij de beheerder: dat staat eronder; een weigering ook, als alert", () => {
    const html = renderToStaticMarkup(
      createElement(BeeldCheck, {
        known: known("beheer_uit"),
        on: true,
        error: "Zoeken op beeld staat uit voor dit platform",
        onChange: () => {},
      }),
    );
    const id = /id="(\S+)-uitleg"/.exec(html)?.[1];
    expect(id).toBeTruthy();
    expect(html).toContain(`aria-describedby="${id}-uitleg ${id}-beheer ${id}-fout" aria-invalid="true" checked=""`);
    expect(html).toContain(`<small id="${id}-beheer" class="muted">Zoeken op beeld staat uit voor dit platform.</small>`);
    expect(html).toContain(
      `<p id="${id}-fout" class="field-error" role="alert">Zoeken op beeld staat uit voor dit platform</p>`,
    );
  });

  it("aan of uit gaat naar PATCH /me/photo-settings, daarna vraagt elke plek de stand opnieuw", () => {
    expect(beeldSwitchSource).toContain('await updatePhotoSettings({ beeld: on ? "aan" : "uit" });');
    expect(beeldSwitchSource).toMatch(/setBeeld\(s\.beeld\);\s*setError\(null\);\s*forgetBeeld\(\);/);
  });
});

describe("Meer zoals deze in de viewer", () => {
  it("een kop en een link naar /zoeken met like", () => {
    vi.stubGlobal("window", { location: { pathname: "/bestanden", search: "" } });
    const html = renderToStaticMarkup(createElement(LikeSection, { id: P1 }));
    const id = /<h4 id="([^"]+)">/.exec(html)?.[1];
    expect(html).toBe(
      `<section class="like-row" aria-labelledby="${id}"><h4 id="${id}">Zoeken op beeld</h4>` +
        `<p><a href="/zoeken?mode=image&amp;like=${P1}">Meer zoals deze</a></p></section>`,
    );
  });

  it("alleen bij een eigen foto (permission owner, of geen permission)", () => {
    expect(likeRowSource).toContain('api.request<FileNode>("GET", `/api/v1/files/${encodeURIComponent(item.id)}`)');
    expect(likeRowSource).toContain('(node.permission ?? "owner") === "owner" ? item.id : null');
  });
});

describe("het blok op /beheer (Keuze 82)", () => {
  const BEELD: AdminBeeld = {
    on: false,
    model: "missing",
    analyse: "down",
    seen: 0,
    photos: 3,
    failed_by_code: [],
    hour: { queries: 1, p95_ms: null, slow: 0, down: 1, no_model: 0 },
    space: { db_bytes: 0, cache_bytes: 0, models_bytes: 0 },
    bench: { at: "2026-01-02T03:04:05Z", p95_ms: 140.4, photos_per_minute: 812.6 },
    quality: { at: "2026-01-02T03:04:05Z", ok: false } as AdminBeeld["quality"],
  };

  it("uit, model weg, container weg, één vraag zonder p95", () => {
    const html = renderToStaticMarkup(createElement(BeeldBlock, { beeld: BEELD }));
    expect(html).toContain('<p data-search="beeld-on">Zoeken op beeld: <span class="badge degraded">uit</span></p>');
    expect(html).toContain('data-search="beeld-switch">Zet aan voor iedereen</button>');
    expect(html).toContain(
      '<dd data-search="beeld-model">SigLIP 2 B/32-256 (Google, Apache-2.0), int8. Ontbreekt; install.sh haalt het bij de volgende run.</dd>',
    );
    expect(html).toContain('<dd data-search="beeld-analyse">draait niet</dd>');
    expect(html).toContain('<dd data-search="beeld-failed">0</dd>');
    expect(html).toContain(
      '<dd data-search="beeld-hour">1 vraag, p95 nog niet gemeten; terug naar gewoon zoeken: 0 keer te traag, 1 keer zonder container, 0 keer zonder model</dd>',
    );
    expect(html).toMatch(/<dd data-search="beeld-bench">[^<]+: p95 140 ms, 813 foto&#x27;s per minuut<\/dd>/);
    expect(html).toMatch(/<dd data-search="beeld-quality">[^<]+: niet in orde<\/dd>/);
  });

  it("het model: in orde, ontbreekt of niet nagerekend", () => {
    expect(modelKey("ok")).toBe("beeld.admin.modelOk");
    expect(modelKey("missing")).toBe("beeld.admin.modelMissing");
    expect(modelKey("unverified")).toBe("beeld.admin.modelUnverified");
    expect(hourText({ queries: 12, p95_ms: 139.6, slow: 0, down: 0, no_model: 0 })).toBe(
      "12 vragen, p95 140 ms; terug naar gewoon zoeken: 0 keer te traag, 0 keer zonder container, 0 keer zonder model",
    );
  });

  it("alleen aantallen: nooit een vector, een vraag of een naam", () => {
    expect(beeldBlockSource).not.toMatch(/\.(vec|embedding|score|q|query|name|user)\b/);
    expect(beeldBlockSource).toContain("danger: true");
  });
});
