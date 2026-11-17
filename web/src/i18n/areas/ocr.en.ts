import type { Translation } from "../types";
import type nl from "./ocr.nl";

const en: Translation<typeof nl> = {
  row: {
    title: "Text in the image",
    loading: "Loading status…",
    queued: "Waiting for text recognition.",
    donePhoto: "Text found in this photo. Only you can find it with Search.",
    donePdf: "The scanned pages have been read; the text is searchable.",
    none: "No text found.",
    notYetPhoto: "Not read yet. Only you will be able to find the text with Search.",
    notYetPdf: "The scanned pages have not been read yet.",
    failed: "Text recognition failed.",
    missing: "Text recognition is not installed on this server.",
    badImage: "This image could not be read.",
    hasText: "This document already has enough text; text recognition is not needed.",
    run: "Recognise text",
    again: "Recognise again",
    asked: "Text recognition is queued.",
  },
  photo: {
    found: {
      one: "{n} word of your search marked in the photo",
      other: "{n} words of your search marked in the photo",
    },
    notFound: "Not found in the text of this photo",
  },
};

export default en;
