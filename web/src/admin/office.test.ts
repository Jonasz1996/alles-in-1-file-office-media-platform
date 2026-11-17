import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { loadAreas, resetI18nForTests } from "../i18n";
import type { Notice } from "./api";
import {
  canRestore,
  jobTypeLabel,
  licenseText,
  noticeText,
  OFFICE_LIMITS,
  officeBadge,
  PINNED_VERSION,
  parseOfficeNumber,
  tokensText,
  UNLIMITED,
  unsavedReason,
} from "./format";

async function inLocale(locale: "nl" | "en") {
  resetI18nForTests(locale);
  await loadAreas(["admin"]);
}

beforeAll(() => inLocale("nl"));
afterEach(() => inLocale("nl"));

const note = (code: string, params: Notice["params"] = {}): Notice => ({ code, params, text: `server: ${code}` });

// De meldingen van app/office/monitor.py, met de params die de server meegeeft
const OFFICE_NOTICES: Notice[] = [
  note("office_down"),
  note("office_misconfigured"),
  note("office_version", { version: "9.4.0.1", pinned: PINNED_VERSION }),
  note("office_limit", { used: 16, max: 20 }),
  note("office_unsaved", { count: 2 }),
  note("office_convert_failed", { count: 1 }),
];

describe("de meldingen van office", () => {
  it("zijn alle zes vertaald, in het Nederlands en het Engels", async () => {
    for (const locale of ["nl", "en"] as const) {
      await inLocale(locale);
      for (const n of OFFICE_NOTICES) {
        const text = noticeText(n);
        expect(text, `${locale} ${n.code}`).not.toBe(n.text);
        expect(text, `${locale} ${n.code}`).not.toMatch(/[{}]|admin\./);
      }
    }
  });

  it("vullen de params in, met enkelvoud en meervoud voor een aantal", async () => {
    expect(noticeText(OFFICE_NOTICES[2])).toBe(
      `De document server heeft versie 9.4.0.1, niet de vastgepinde ${PINNED_VERSION}.`,
    );
    expect(noticeText(OFFICE_NOTICES[3])).toBe("Office gebruikt 16 van de 20 verbindingen van de licentie.");
    expect(noticeText(note("office_unsaved", { count: 1 }))).toMatch(/^1 document uit de editor is niet/);
    expect(noticeText(note("office_unsaved", { count: 3 }))).toMatch(/^3 documenten uit de editor zijn niet/);
    expect(noticeText(note("office_convert_failed", { count: 2 }))).toMatch(/^2 documenten konden niet/);
    await inLocale("en");
    expect(noticeText(note("office_unsaved", { count: 1 }))).toMatch(/^1 document from the editor was not saved/);
    expect(noticeText(OFFICE_NOTICES[3])).toBe("Office uses 16 of the 20 connections of its license.");
  });

  it("een onbekende code houdt de tekst van de server", () => {
    expect(noticeText(note("office_iets_nieuws"))).toBe("server: office_iets_nieuws");
  });
});

describe("de taken van office", () => {
  it("office.pdf en office.pdf.backfill hebben een naam", async () => {
    expect(jobTypeLabel("office.pdf")).toBe("PDF maken (office)");
    expect(jobTypeLabel("office.pdf.backfill")).toBe("PDF's inhalen (office)");
    await inLocale("en");
    expect(jobTypeLabel("office.pdf.backfill")).toBe("Catch up on PDFs (office)");
    expect(jobTypeLabel("office.iets")).toBe("office.iets");
  });
});

describe("het tabblad Office", () => {
  it("de licentie: 2147483647 is geen limiet", async () => {
    expect(licenseText(UNLIMITED)).toBe("geen limiet");
    expect(licenseText(UNLIMITED + 1)).toBe("geen limiet");
    expect(licenseText(20)).toBe("20 verbindingen");
    expect(licenseText(1)).toBe("1 verbinding");
    expect(licenseText(null)).toBe("nog onbekend");
    await inLocale("en");
    expect(licenseText(UNLIMITED)).toBe("no limit");
  });

  it("tokens geëist of niet", () => {
    expect(tokensText(true)).toBe("geëist");
    expect(tokensText(false)).toMatch(/^niet geëist/);
    expect(tokensText(null)).toBe("nog onbekend");
  });

  it("de kleur van de toestand", () => {
    expect(officeBadge("ok")).toBe("ok");
    expect(officeBadge("starting")).toBe("degraded");
    expect(officeBadge("off")).toBe("degraded");
    expect(officeBadge("down")).toBe("down");
    expect(officeBadge("misconfigured")).toBe("down");
  });

  it("de reden bij niet-opgeslagen werk, en wanneer Terugzetten kan", () => {
    expect(unsavedReason("no_room")).toBe("quotum vol");
    expect(unsavedReason("unknown")).toBe("onbekend bij de app");
    expect(unsavedReason(null)).toBe("het herstel probeert het nog");
    expect(unsavedReason("iets_anders")).toBe("iets_anders");
    expect(canRestore({ owner: "Anna", reason: "no_room" })).toBe(true);
    expect(canRestore({ owner: "Anna", reason: null })).toBe(true);
    expect(canRestore({ owner: null, reason: "no_room" })).toBe(false);
    expect(canRestore({ owner: null, reason: "unknown" })).toBe(false);
    expect(canRestore({ owner: "Anna", reason: "owner_gone" })).toBe(false);
    // Het bestand is definitief weg (de eigenaar leegde de prullenbak): de server weigert (409)
    expect(unsavedReason("gone")).toBe("het bestand bestaat niet meer");
    expect(canRestore({ owner: "Anna", reason: "gone" })).toBe(false);
  });

  it("de instellingen: hele getallen binnen de grenzen van de server", () => {
    expect(OFFICE_LIMITS).toEqual({
      max_editors: { min: 1, max: 500 },
      checkpoint_minutes: { min: 1, max: 60 },
      version_minutes: { min: 5, max: 1440 },
    });
    expect(parseOfficeNumber("max_editors", "20")).toBe(20);
    expect(parseOfficeNumber("max_editors", " 1 ")).toBe(1);
    expect(parseOfficeNumber("max_editors", "0")).toBeUndefined();
    expect(parseOfficeNumber("max_editors", "501")).toBeUndefined();
    expect(parseOfficeNumber("max_editors", "2.5")).toBeUndefined();
    expect(parseOfficeNumber("max_editors", "")).toBeUndefined();
    expect(parseOfficeNumber("checkpoint_minutes", "60")).toBe(60);
    expect(parseOfficeNumber("checkpoint_minutes", "61")).toBeUndefined();
    expect(parseOfficeNumber("version_minutes", "4")).toBeUndefined();
    expect(parseOfficeNumber("version_minutes", "1440")).toBe(1440);
  });
});
