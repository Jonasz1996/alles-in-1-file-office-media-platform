import { describe, expect, it } from "vitest";
import nl from "../i18n/areas/ocr.nl";
import photosPeopleNl from "../i18n/areas/photos_people.nl";
import photoErrors from "../i18n/errors/photos";
import settingsBlock from "../photos/people/PhotoSettingsBlock.tsx?raw";
import type { Analysis, StepState } from "../search/types";
import infoPanel from "../viewer/InfoPanel.tsx?raw";
import { showOcrRow } from "../viewer/infoRows";
import type { ViewKind } from "../viewer/media";
import zoomView from "../viewer/ZoomView.tsx?raw";
import { mayAsk, ocrView } from "./ocr";

const KINDS: ViewKind[] = ["image", "pdf", "video", "audio", "text", "office", "archive", "none"];

function state(ocr: StepState, code: Analysis["code"] = null): Pick<Analysis, "ocr" | "code"> {
  return { ocr, code };
}

describe("de rij Tekst in beeld: waar hij staat (X1)", () => {
  it("alleen bij een foto of een PDF, ingelogd", () => {
    expect(KINDS.filter((k) => showOcrRow(k, {}, true))).toEqual(["image", "pdf"]);
  });

  it("nooit via een publieke link, en niet bij een eerdere versie", () => {
    expect(KINDS.filter((k) => showOcrRow(k, {}, false))).toEqual([]);
    expect(showOcrRow("pdf", { version: { id: "v1", label: "Versie 2" } }, true)).toBe(false);
  });
});

describe("wie Tekst herkennen mag vragen (Keuze 23, 25a)", () => {
  it("een foto: alleen de eigenaar, ook niet met schrijfrecht", () => {
    expect(mayAsk("image", "owner")).toBe(true);
    expect(mayAsk("image", "write")).toBe(false);
    expect(mayAsk("image", "read")).toBe(false);
  });

  it("een PDF: wie mag schrijven", () => {
    expect(mayAsk("pdf", "owner")).toBe(true);
    expect(mayAsk("pdf", "write")).toBe(true);
    expect(mayAsk("pdf", "read")).toBe(false);
  });
});

describe("de zin en de knop bij elke stand", () => {
  it("een foto", () => {
    expect(ocrView("image", state("skip"))).toEqual({ text: "ocr.row.notYetPhoto", button: "run", waiting: false });
    expect(ocrView("image", state("queued"))).toEqual({ text: "ocr.row.queued", button: "run", waiting: true });
    expect(ocrView("image", state("done"))).toEqual({ text: "ocr.row.donePhoto", button: "again", waiting: false });
    expect(ocrView("image", state("none"))).toEqual({ text: "ocr.row.none", button: "again", waiting: false });
    expect(ocrView("image", state("failed"))).toEqual({ text: "ocr.row.failed", button: "again", waiting: false });
    expect(ocrView("image", state("failed", "ocr_bad_image"))).toEqual({
      text: "ocr.row.badImage",
      button: "again",
      waiting: false,
    });
  });

  it("een PDF, ook met al genoeg tekst (ocr_has_text): dan geen knop", () => {
    expect(ocrView("pdf", state("skip"))).toEqual({ text: "ocr.row.notYetPdf", button: "run", waiting: false });
    expect(ocrView("pdf", state("queued"))).toEqual({ text: "ocr.row.queued", button: "run", waiting: true });
    expect(ocrView("pdf", state("done"))).toEqual({ text: "ocr.row.donePdf", button: "again", waiting: false });
    expect(ocrView("pdf", state("skip", "ocr_has_text"))).toEqual({ text: "ocr.row.hasText", button: null, waiting: false });
  });

  it("zonder tesseract (ocr_missing) geen knop: de API gaf 503 ocr_off", () => {
    for (const kind of ["image", "pdf"] as const) {
      expect(ocrView(kind, state("none", "ocr_missing"))).toEqual({ text: "ocr.row.missing", button: null, waiting: false });
    }
  });

  it("een code van de stap text gaat de rij niet aan", () => {
    expect(ocrView("pdf", state("done", "pages_limit"))).toEqual({ text: "ocr.row.donePdf", button: "again", waiting: false });
    expect(ocrView("pdf", state("skip", "too_slow"))).toEqual({ text: "ocr.row.notYetPdf", button: "run", waiting: false });
  });

  it("elke zin staat in het gebied ocr", () => {
    const keys = new Set(Object.keys(nl.row).map((k) => `ocr.row.${k}`));
    for (const kind of ["image", "pdf"] as const) {
      for (const s of ["queued", "done", "none", "skip", "failed"] as const) {
        for (const code of [null, "ocr_missing", "ocr_bad_image", "ocr_has_text"] as const) {
          expect(keys.has(ocrView(kind, state(s, code)).text)).toBe(true);
        }
      }
    }
  });
});

describe("de fouten van POST /files/{id}/ocr staan in de catalogus van foto's (§3.4)", () => {
  it("met een Engelse tekst", () => {
    for (const code of ["ocr_off", "ocr_running", "ocr_limit", "ocr_has_text", "ocr_unsupported"]) {
      expect(photoErrors[code as keyof typeof photoErrors]?.en, code).toBeTruthy();
    }
  });
});

describe("de plekken van X1 in de bestanden van anderen", () => {
  it("het infopaneel laadt de rij lazy, met het gebied ocr", () => {
    expect(infoPanel).toContain('lazyWithAreas<InfoRowProps>(() => import("../ocr/OcrRow"), ["ocr"])');
    expect(infoPanel).toMatch(/showOcrRow\(kind, item, signedIn\) && \(\s*<LazySlot key=\{`tekst-\$\{item\.id\}`\}>/);
  });

  it("ZoomView laadt de woorden van een foto lazy, alleen ingelogd, bij de huidige versie en met ?q=", () => {
    expect(zoomView).toContain('lazyWithAreas<PhotoWordsProps>(() => import("../ocr/PhotoWords"), ["ocr"])');
    expect(zoomView).toContain('source.kind === "user" && !item.version && markQuery(query) !== null');
    expect(zoomView).toMatch(/<PhotoWords item=\{item\} width=\{natural\.w\} height=\{natural\.h\} style=\{placed\} \/>/);
  });

  it("het blok Foto's onder Account heeft het vinkje Tekst in foto's herkennen (ocr_photos)", () => {
    expect(settingsBlock).toContain("checked={s.ocr_photos}");
    expect(settingsBlock).toContain("change({ ocr_photos: on })");
    expect(photosPeopleNl.settings.ocr).toBe("Tekst in foto's herkennen");
  });
});
