// Foutcodes van foto's en albums (P5), en van "Tekst herkennen" (fase 6, X1).
export default {
  album_read_only: { en: "An album can only be shared for viewing" },
  cover_not_in_album: { en: "The cover must be a photo from this album" },
  not_an_album: { en: "This link is not for an album" },
  // X1: POST /files/{id}/ocr
  ocr_off: { en: "Text recognition is turned off" },
  ocr_running: { en: "The text is already being recognised" },
  ocr_limit: { en: "You asked for text recognition too often; try again later" },
  ocr_has_text: { en: "This document already has enough text" },
  ocr_unsupported: { en: "Text recognition only works on a photo or a PDF" },
} satisfies Record<string, { en: string }>;
