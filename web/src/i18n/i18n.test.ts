import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, errorFromBody } from "../api";
import {
  type Area,
  applyAccountLocale,
  areasLoaded,
  collator,
  type ErrorCatalog,
  errorText,
  fmt,
  fold,
  initI18n,
  interpolate,
  loadAreas,
  locale,
  type MessageKey,
  resetI18nForTests,
  setErrorCatalogForTests,
  t,
} from "./index";
import { detectLocale } from "./locale";
import type { Messages } from "./types";

// Alle woordenboeken, direct geladen: om nl en en naast elkaar te leggen
const files = import.meta.glob<Messages>("./areas/*.ts", { eager: true, import: "default" });

const AREAS: Area[] = [
  "common",
  "shell",
  "auth",
  "pwa",
  "browser",
  "upload",
  "viewer",
  "public",
  "photos",
  "admin",
  "account",
  "sharing",
  "trash",
  "versions",
];

/** Elke tekst met zijn pad; een meervoud ({one, other}) is één blad met al zijn vormen. */
function leaves(d: Messages, prefix = ""): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const [k, v] of Object.entries(d)) {
    const key = prefix + k;
    if (typeof v === "string") out.set(key, [v]);
    else if (typeof (v as { other?: unknown }).other === "string") out.set(key, Object.values(v as Record<string, string>));
    else for (const [kk, vv] of leaves(v as Messages, `${key}.`)) out.set(kk, vv);
  }
  return out;
}

function placeholders(texts: string[]): string[] {
  return [...new Set(texts.flatMap((s) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1])))].sort();
}

beforeEach(() => {
  resetI18nForTests("nl");
});

afterEach(() => {
  vi.restoreAllMocks();
  resetI18nForTests("nl");
});

describe("woordenboeken", () => {
  it("elk gebied heeft een nl- en een en-bestand, en verder niets", () => {
    const names = Object.keys(files).sort();
    const want = AREAS.flatMap((a) => [`./areas/${a}.en.ts`, `./areas/${a}.nl.ts`]).sort();
    expect(names).toEqual(want);
  });

  for (const area of AREAS) {
    it(`${area}: dezelfde sleutels en dezelfde {plekken} in nl en en`, () => {
      const nl = leaves(files[`./areas/${area}.nl.ts`]);
      const en = leaves(files[`./areas/${area}.en.ts`]);
      expect([...en.keys()].sort()).toEqual([...nl.keys()].sort());
      for (const [key, texts] of nl) {
        expect(placeholders(en.get(key) ?? []), `${area}.${key}`).toEqual(placeholders(texts));
        for (const text of [...texts, ...(en.get(key) ?? [])]) expect(text.trim(), `${area}.${key}`).not.toBe("");
      }
    });
  }

  it("een meervoud heeft altijd one en other", () => {
    for (const [name, d] of Object.entries(files)) {
      const walk = (m: Messages, path: string) => {
        for (const [k, v] of Object.entries(m)) {
          if (typeof v === "string") continue;
          const forms = v as Record<string, unknown>;
          if (typeof forms.other === "string") expect(typeof forms.one, `${name} ${path}${k}`).toBe("string");
          else walk(v as Messages, `${path}${k}.`);
        }
      };
      walk(d, "");
    }
  });
});

describe("t", () => {
  it("common, shell en auth zijn er meteen (nl)", () => {
    expect(locale()).toBe("nl");
    expect(t("shell.brand")).toBe("Platform");
    expect(t("common.dialog.typeToConfirm", { text: "VERWIJDER" })).toBe("Typ VERWIJDER om te bevestigen.");
    // auth is van P1: alleen dat het geladen is, niet de tekst zelf
    expect(t("auth.title")).not.toBe("auth.title");
  });

  it("meervoud op vars.n, met getallen in de opmaak van de taal", async () => {
    expect(t("common.items", { n: 1 })).toBe("1 item");
    expect(t("common.items", { n: 0 })).toBe("0 items");
    expect(t("common.items", { n: 1234 })).toBe("1.234 items");
    // Een gebied van een pakket (P2): alleen dat het laadt en {n} invult, niet de tekst zelf
    await loadAreas(["browser"]);
    const selected = t("browser.selected", { n: 3 });
    expect(selected).not.toBe("browser.selected");
    expect(selected).toContain("3");
  });

  it("een gebied dat niet geladen is: de sleutel, en een melding die met i18n: begint", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(t("photos.title")).toBe("photos.title");
    expect(error).toHaveBeenCalledWith(expect.stringMatching(/^i18n: /));
  });

  it("een onbekende sleutel ook", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(t("shell.bestaatniet" as MessageKey)).toBe("shell.bestaatniet");
    expect(error).toHaveBeenCalledWith(expect.stringMatching(/^i18n: /));
  });

  it("in het Engels laadt ook de basis pas met loadAreas", async () => {
    resetI18nForTests("en");
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(t("common.ok")).toBe("common.ok");
    await loadAreas(["common", "shell"]);
    expect(t("shell.nav.files")).toBe("Files");
    expect(t("common.items", { n: 1 })).toBe("1 item");
    expect(t("common.items", { n: 2 })).toBe("2 items");
    expect(error).toHaveBeenCalledTimes(1);
  });
});

describe("loadAreas", () => {
  it("idempotent: twee keer (ook tegelijk) vragen laadt één keer", async () => {
    expect(areasLoaded(["photos"])).toBe(false);
    const a = loadAreas(["photos"]);
    const b = loadAreas(["photos", "photos"]);
    await Promise.all([a, b]);
    expect(areasLoaded(["photos"])).toBe(true);
    const title = t("photos.title");
    await loadAreas(["photos"]);
    expect(t("photos.title")).toBe(title);
    expect(title).not.toBe("photos.title"); // de tekst zelf is van P5
  });

  it("al geladen: meteen klaar", async () => {
    expect(areasLoaded(["common", "shell", "auth"])).toBe(true);
    await expect(loadAreas([])).resolves.toBeUndefined();
    await expect(loadAreas(["common"])).resolves.toBeUndefined();
  });
});

describe("interpolate", () => {
  it("vult in wat er is en laat de rest staan", () => {
    expect(interpolate("{a} en {b}", { a: "x" })).toBe("x en {b}");
    expect(interpolate("{n} bestanden", { n: 12345 })).toBe("12.345 bestanden");
    expect(interpolate("geen", undefined)).toBe("geen");
  });
});

describe("fmt", () => {
  it("bytes: 1024-basis, één decimaal, met de komma van de taal", () => {
    expect(fmt.bytes(0)).toBe("0 B");
    expect(fmt.bytes(512)).toBe("512 B");
    expect(fmt.bytes(1536)).toBe("1,5 KB");
    expect(fmt.bytes(1024 * 1024 * 1.25)).toBe("1,3 MB");
    expect(fmt.bytes(1023.96 * 1024)).toBe("1024,0 KB");
    expect(fmt.bytes(5 * 1024 ** 4)).toBe("5,0 TB");
    resetI18nForTests("en");
    expect(fmt.bytes(1536)).toBe("1.5 KB");
  });

  it("getallen, procenten, duur en lijsten", () => {
    expect(fmt.number(1234567)).toBe("1.234.567");
    expect(fmt.percent(0.25)).toMatch(/^25\s?%$/);
    expect(fmt.duration(83.4)).toBe("1:23");
    expect(fmt.duration(3723)).toBe("1:02:03");
    expect(fmt.list(["a", "b", "c"])).toBe("a, b en c");
    resetI18nForTests("en");
    expect(fmt.number(1234567)).toBe("1,234,567");
    expect(fmt.list(["a", "b", "c"])).toBe("a, b and c"); // en-GB: zonder Oxford-komma
  });

  it("wallClock toont de kloktijd zoals opgeslagen (UTC), zonder om te rekenen", () => {
    expect(fmt.wallClock("2026-07-04T23:30:00Z")).toMatch(/23:30/);
    expect(fmt.wallClock("2026-07-04T23:30:00Z")).toMatch(/2026/);
  });

  it("datums in de taal van de app", () => {
    const nl = fmt.date("2026-10-21T12:00:00Z", "long");
    resetI18nForTests("en");
    const en = fmt.date("2026-10-21T12:00:00Z", "long");
    expect(nl).toMatch(/oktober/);
    expect(en).toMatch(/October/);
  });

  it("relative ten opzichte van nu", () => {
    const now = Date.parse("2026-10-21T12:00:00Z");
    expect(fmt.relative(now - 3 * 60_000, now)).toBe("3 minuten geleden");
    resetI18nForTests("en");
    expect(fmt.relative(now + 2 * 86_400_000, now)).toBe("in 2 days");
    expect(fmt.relative(now - 86_400_000, now)).toBe("yesterday");
  });
});

describe("fold en collator", () => {
  it("zonder hoofdletters en accenten", () => {
    expect(fold("Één Café")).toBe("een cafe");
    expect(fold("Straße")).toBe("strasse");
  });

  it("foto 2 vóór foto 10", () => {
    expect(["foto 10", "Foto 2", "foto 1"].sort(collator.compare)).toEqual(["foto 1", "Foto 2", "foto 10"]);
  });
});

describe("errorText", () => {
  it("nl: de tekst van de server", () => {
    expect(errorText(new ApiError(403, "Je mag hier alleen kijken", "read_only"))).toBe("Je mag hier alleen kijken");
    expect(errorText(new TypeError("Failed to fetch"))).toBe(t("common.errors.network"));
    expect(errorText(new ApiError(0, "Geen verbinding", "network"))).toBe(t("common.errors.network"));
    // Een programmeerfout is geen verbindingsprobleem
    expect(errorText(new TypeError("x.map is not a function"))).toBe("x.map is not a function");
    // Een ItemResult van een bulkactie gaat ook door errorText
    expect(errorText({ code: "read_only", message: "Je mag hier alleen kijken" })).toBe("Je mag hier alleen kijken");
  });

  it("en: de catalogus op code, met params; anders toch de servertekst", () => {
    resetI18nForTests("en");
    setErrorCatalogForTests({
      read_only: { en: "You can only view this" },
      totp_locked: { en: "Try again in {seconds} seconds" },
    });
    expect(errorText(new ApiError(403, "Je mag hier alleen kijken", "read_only"))).toBe("You can only view this");
    expect(errorText(new ApiError(429, "Probeer het over 30 seconden", "totp_locked", { seconds: 30 }))).toBe(
      "Try again in 30 seconds",
    );
    expect(errorText(new ApiError(400, "Iets nieuws", "brand_new"))).toBe("Iets nieuws");
  });
});

describe("foutcatalogi (errors/*.ts)", () => {
  // De echte catalogi, direct geladen; de app laadt ze lazy via initI18n (alleen in het Engels)
  const catalogs = import.meta.glob<ErrorCatalog>("./errors/*.ts", { eager: true, import: "default" });

  it("elk gebied met eigen foutcodes heeft een catalogus, elke code één keer en met een Engelse tekst", () => {
    const names = Object.keys(catalogs).map((p) => p.replace(/^\.\/errors\/|\.ts$/g, ""));
    expect(names).toEqual(
      expect.arrayContaining(["common", "auth", "browser", "upload", "viewer", "photos", "admin", "sharing"]),
    );
    const seen = new Map<string, string>();
    for (const [path, catalog] of Object.entries(catalogs)) {
      for (const [code, entry] of Object.entries(catalog)) {
        expect(seen.get(code), `${code} staat ook in ${seen.get(code)}`).toBeUndefined();
        seen.set(code, path);
        expect(entry.en.trim(), code).not.toBe("");
      }
    }
    expect(seen.get("read_only")).toBe("./errors/common.ts");
    expect(seen.get("account_disabled")).toBe("./errors/common.ts");
  });

  it("en: initI18n laadt de catalogi en errorText vertaalt een fout van de API op code", async () => {
    resetI18nForTests("en");
    await initI18n();
    const body = (code: string, message: string, params?: Record<string, unknown>) => ({
      detail: { message, code, ...(params ? { params } : {}) },
    });
    expect(errorText(errorFromBody(403, body("read_only", "Je mag hier alleen kijken")))).toBe(
      "You can only view this",
    );
    expect(
      errorText(errorFromBody(422, body("too_many", "Te veel om in één keer te kopiëren", { max: 10000 }))),
    ).toBe("Too many items at once (more than 10,000)");
    const full = body("no_room_owner", "Er is niet genoeg ruimte meer bij Bea", { owner: "Bea" });
    expect(errorText(errorFromBody(413, full))).toBe("Bea has run out of storage space; ask them to make room");
    // Per item van een bulkactie (ItemResult met params) net zo
    expect(errorText({ id: "x", ok: false, code: "too_many", message: "Te veel", params: { max: 500 } })).toBe(
      "Too many items at once (more than 500)",
    );
    // Wat de web-app zelf maakt: geen verbinding, en een fout zonder tekst van de server
    expect(errorText(new ApiError(0, "Geen verbinding met de server. Probeer het opnieuw.", "network"))).toBe(
      "No connection to the server. Please try again.",
    );
    expect(errorText(errorFromBody(500, null))).toBe("Error 500");
    expect(errorText(new ApiError(413, "Onvoldoende quotum", "no_room"))).toBe("Not enough storage quota");
    expect(errorText(errorFromBody(400, body("brand_new", "Iets nieuws")))).toBe("Iets nieuws");
  });

  it("nl: de catalogi zijn niet nodig, de tekst van de server blijft", async () => {
    await initI18n();
    const body = { detail: { message: "Je mag hier alleen kijken", code: "read_only" } };
    expect(errorText(errorFromBody(403, body))).toBe("Je mag hier alleen kijken");
  });
});

describe("detectLocale", () => {
  it("opgeslagen keuze, dan de browser, dan nl", () => {
    expect(detectLocale("en", ["nl-NL"])).toBe("en");
    expect(detectLocale(null, ["en-US", "nl"])).toBe("en");
    expect(detectLocale(null, ["nl-BE"])).toBe("nl");
    expect(detectLocale("xx", ["fr-FR"])).toBe("en");
    expect(detectLocale(null, undefined)).toBe("nl");
  });
});

describe("applyAccountLocale", () => {
  function memory() {
    const data = new Map<string, string>();
    return {
      data,
      getItem: (k: string) => data.get(k) ?? null,
      setItem: (k: string, v: string) => void data.set(k, v),
      removeItem: (k: string) => void data.delete(k),
    };
  }

  afterEach(() => {
    vi.unstubAllGlobals();
    resetI18nForTests("nl");
  });

  it("herlaadt één keer naar de taal van het account, ook bij een volgende login in hetzelfde tabblad", () => {
    const local = memory();
    const reload = vi.fn();
    vi.stubGlobal("localStorage", local);
    vi.stubGlobal("sessionStorage", memory());
    vi.stubGlobal("window", { location: { reload } });
    const stored = () => local.data.get("platform-locale");

    // A (en) logt in op een Nederlandse browser: bewaren en herladen
    resetI18nForTests("nl");
    expect(applyAccountLocale("en")).toBe(true);
    expect(stored()).toBe("en");
    expect(reload).toHaveBeenCalledTimes(1);
    // Na het herladen klopt het
    resetI18nForTests("en");
    expect(applyAccountLocale("en")).toBe(false);

    // A kiest Nederlands (setLocale bewaart en herlaadt) en logt uit zonder te herladen
    local.data.set("platform-locale", "nl");
    resetI18nForTests("nl");
    expect(applyAccountLocale("nl")).toBe(false);

    // B (en) logt in in hetzelfde tabblad: weer naar het Engels
    expect(applyAccountLocale("en")).toBe(true);
    expect(stored()).toBe("en");
    expect(reload).toHaveBeenCalledTimes(2);

    // Komt de taal na dat herladen toch niet aan, dan niet eindeloos herladen (en niets bewaren)
    resetI18nForTests("nl");
    local.data.set("platform-locale", "nl");
    expect(applyAccountLocale("en")).toBe(false);
    expect(stored()).toBe("nl");
    expect(reload).toHaveBeenCalledTimes(2);
  });
});
