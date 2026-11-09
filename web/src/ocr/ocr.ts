// Tekst in beeld (fase 6, X1; Keuze 23 en 25a), zonder React, zodat vitest het kan toetsen: wat de
// rij in het infopaneel zegt bij de stand van OCR, en of de knop "Tekst herkennen" er staat.
//
// De stand komt uit GET /files/{id}/analysis (ocr en code). Wie mag vragen (POST /files/{id}/ocr):
// bij een foto alleen de eigenaar (de tekst hoort bij zijn node), bij een PDF wie mag schrijven.
// Wie niet mag vragen, krijgt geen rij: de API geeft een ander bij een foto toch altijd skip. Of
// de rij er komt (een foto of PDF, ingelogd, de huidige versie), zegt showOcrRow in
// viewer/infoRows.ts, naast de andere plekken.

import type { Permission } from "../api";
import type { MessageKey } from "../i18n";
import type { Analysis } from "../search/types";
import type { ViewKind } from "../viewer/media";

/** Mag deze gebruiker "Tekst herkennen" vragen? Een foto: alleen de eigenaar; een PDF: schrijven. */
export function mayAsk(kind: ViewKind, permission: Permission): boolean {
  return kind === "image" ? permission === "owner" : permission === "owner" || permission === "write";
}

/** Wat de rij toont: de zin bij de stand, welke knop (of geen), en of de knop wacht: hij staat in
 * de rij of wordt gelezen (de API gaf dan 409 ocr_running). Een wachtende knop blijft staan
 * (aria-disabled), zodat de focus na het vragen niet verdwijnt. */
export interface OcrView {
  text: MessageKey;
  button: "run" | "again" | null;
  waiting: boolean;
}

/** De zin en de knop bij de stand van ocr. De code telt alleen als hij van OCR is: analysis geeft
 * de eerste code van text, hash en ocr, en een code van text gaat de rij niet aan. */
export function ocrView(kind: ViewKind, state: Pick<Analysis, "ocr" | "code">): OcrView {
  const photo = kind === "image";
  switch (state.ocr) {
    case "queued":
      return { text: "ocr.row.queued", button: "run", waiting: true };
    case "done":
      return { text: photo ? "ocr.row.donePhoto" : "ocr.row.donePdf", button: "again", waiting: false };
    case "none":
      // Zonder tesseract kan het niet (de knop gaf 503 ocr_off)
      if (state.code === "ocr_missing") return { text: "ocr.row.missing", button: null, waiting: false };
      return { text: "ocr.row.none", button: "again", waiting: false };
    case "failed": {
      const text = state.code === "ocr_bad_image" ? "ocr.row.badImage" : "ocr.row.failed";
      return { text, button: "again", waiting: false };
    }
    default:
      // skip: nog niet gevraagd, of een PDF met al genoeg tekst (409 ocr_has_text)
      if (!photo && state.code === "ocr_has_text") return { text: "ocr.row.hasText", button: null, waiting: false };
      return { text: photo ? "ocr.row.notYetPhoto" : "ocr.row.notYetPdf", button: "run", waiting: false };
  }
}
