import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { loadAreas, resetI18nForTests } from "../i18n";
import {
  COPIES,
  CopiesNote,
  CopiesSelect,
  copiesChoices,
  copiesSaved,
  copiesToSave,
  copiesValue,
  parseCopies,
  setLibraryCopies,
} from "./LibraryCopies";

// Kopieën in het cluster per bibliotheek op /beheer/media (replicatie per bibliotheek, 0019): de
// keuzes "Standaard (N)", 1, 2 en 3, de waarde terug naar copies, de uitleg met en zonder
// PLATFORM_BLOB_SPLIT, en de route van /beheer. In het Nederlands en het Engels.

async function inLocale(locale: "nl" | "en") {
  resetI18nForTests(locale);
  await loadAreas(["admin", "common"]);
}

beforeEach(() => inLocale("nl"));
afterEach(() => {
  vi.restoreAllMocks();
  resetI18nForTests("nl");
});

const lib = { id: "lib-1", name: "Films", copies: 2 as const };

describe("de keuzes", () => {
  it("eerst de standaard van het cluster, dan 1, 2 en 3", () => {
    expect(COPIES).toEqual([1, 2, 3]);
    expect(copiesChoices(3)).toEqual([
      { value: "", label: "Standaard (3)" },
      { value: "1", label: "1" },
      { value: "2", label: "2" },
      { value: "3", label: "3" },
    ]);
    expect(copiesChoices(2)[0].label).toBe("Standaard (2)");
  });

  it("de waarde van de lijst terug naar copies; de standaard is null", () => {
    expect(parseCopies("")).toBeNull();
    expect(parseCopies("1")).toBe(1);
    expect(parseCopies("3")).toBe(3);
    expect(parseCopies("4")).toBeNull();
    expect(parseCopies("x")).toBeNull();
  });

  it("de lijst toont de keuze van de bibliotheek, met een label", () => {
    const html = renderToStaticMarkup(createElement(CopiesSelect, { lib, defaultCopies: 3, onSave: () => {} }));
    expect(html).toContain('aria-label="Kopieën in het cluster van Films"');
    expect(html).toContain('<option value="2" selected="">2</option>');
    expect(html).toContain('<option value="">Standaard (3)</option>');
    const none = renderToStaticMarkup(
      createElement(CopiesSelect, { lib: { ...lib, copies: null }, defaultCopies: 3, onSave: () => {} }),
    );
    expect(none).toContain('<option value="" selected="">Standaard (3)</option>');
  });

  it("opslaan met een knop: een keuze in de lijst alleen slaat niets op (review, pijltjestoetsen)", () => {
    const onSave = vi.fn();
    const html = renderToStaticMarkup(createElement(CopiesSelect, { lib, defaultCopies: 3, onSave }));
    // De knop staat er, met een label, en is uit zolang de keuze gelijk is aan wat bewaard is
    expect(html).toMatch(/<button[^>]*data-media="copies-save"[^>]*>/);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*aria-label="Kopieën in het cluster van Films opslaan"/);
    expect(html).toContain(">Opslaan</button>");
    expect(onSave).not.toHaveBeenCalled();
  });

  it("wat Opslaan bewaart: niets zolang de keuze niet afwijkt; anders de keuze (\"\" is de standaard)", () => {
    expect(copiesValue(null)).toBe("");
    expect(copiesValue(undefined)).toBe("");
    expect(copiesValue(2)).toBe("2");
    expect(copiesToSave(2, "2")).toBeUndefined();
    expect(copiesToSave(null, "")).toBeUndefined();
    expect(copiesToSave(undefined, "")).toBeUndefined();
    expect(copiesToSave(null, "2")).toBe(2);
    expect(copiesToSave(2, "")).toBeNull();
    expect(copiesToSave(3, "1")).toBe(1);
  });
});

describe("de uitleg", () => {
  it("met PLATFORM_BLOB_SPLIT alleen wat het doet", () => {
    const html = renderToStaticMarkup(createElement(CopiesNote, { split: true, defaultCopies: 3 }));
    expect(html).toContain('data-media="copies-help"');
    expect(html).toContain("buiten een bibliotheek geldt de standaard (3)");
    expect(html).not.toContain("copies-off");
  });

  it("zonder: dat het pas in het cluster werkt", () => {
    const html = renderToStaticMarkup(createElement(CopiesNote, { split: false, defaultCopies: 3 }));
    expect(html).toContain('data-media="copies-off"');
    expect(html).toContain("werkt pas in het cluster");
    // Wat al verhuisd is (SPLIT stond eerder aan), blijft waar het staat; niet "alles in de standaard"
    expect(html).toContain("wat al eerder verhuisd is, blijft waar het staat");
    expect(html).not.toContain("blijft alles in de standaard");
  });

  it("de melding na het opslaan", () => {
    expect(copiesSaved("Films", 2, 3, true)).toBe(
      "Films: 2 kopieën in het cluster. De inhoud verhuist bij de volgende ronde.",
    );
    expect(copiesSaved("Series", null, 3, true)).toBe(
      "Series: de standaard van het cluster (3 kopieën). De inhoud verhuist bij de volgende ronde.",
    );
  });

  it("zonder PLATFORM_BLOB_SPLIT geen belofte van een ronde (review)", () => {
    for (const copies of [2, null] as const) {
      const text = copiesSaved("Films", copies, 3, false);
      expect(text).toBe("Films: bewaard. Dit doet pas iets met PLATFORM_BLOB_SPLIT=true.");
      expect(text).not.toContain("ronde");
    }
  });

  it("ook in het Engels, zonder sleutel of {naam} in beeld", async () => {
    await inLocale("en");
    expect(copiesChoices(3)[0].label).toBe("Default (3)");
    const html = renderToStaticMarkup(createElement(CopiesNote, { split: false, defaultCopies: 2 }));
    expect(html).toContain("the default (2) applies");
    expect(html).toContain("only works in the cluster");
    expect(html).toContain("whatever was moved before stays where it is");
    expect(copiesSaved("Movies", 1, 3, false)).toBe("Movies: saved. This only takes effect with PLATFORM_BLOB_SPLIT=true.");
    const button = renderToStaticMarkup(createElement(CopiesSelect, { lib, defaultCopies: 3, onSave: () => {} }));
    expect(button).toContain('aria-label="Save copies in the cluster of Films"');
    expect(button).toContain(">Save</button>");
    for (const text of [
      html,
      button,
      copiesSaved("Movies", 1, 3, true),
      copiesSaved("Shows", null, 3, true),
      copiesSaved("Shows", null, 3, false),
    ]) {
      expect(text).not.toMatch(/admin\.media|\{/);
    }
  });
});

describe("de route", () => {
  it("PATCH /api/v1/admin/media/libraries/{id} met copies (null voor de standaard)", async () => {
    const request = vi.spyOn(api, "request").mockResolvedValue({});
    await setLibraryCopies("a/b", 1);
    await setLibraryCopies("lib-1", null);
    expect(request.mock.calls).toEqual([
      ["PATCH", "/api/v1/admin/media/libraries/a%2Fb", { copies: 1 }],
      ["PATCH", "/api/v1/admin/media/libraries/lib-1", { copies: null }],
    ]);
  });
});
