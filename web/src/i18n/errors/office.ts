// Foutcodes van office (fase 4): de editor, het poort-cookie, de interne routes voor de document
// server en de omzetting naar PDF. De Nederlandse tekst komt van de server; dit is de Engelse.
// Bestaande codes (not_found, read_only, trashed, not_a_file, album, no_room, folder_not_found,
// guest, rate_limited) staan in hun eigen catalogus. {naam} wordt ingevuld uit de params.
export default {
  office_off: { en: "Editing documents is not available on this server" },
  office_unavailable: {
    en: "The document editor cannot be reached right now; try again in a moment",
  },
  office_format: { en: "This type of file cannot be edited here" },
  office_too_large: { en: "This file is too large for the editor" },
  office_unsafe: { en: "This file did not pass the safety check, so it cannot be opened in the editor" },
  office_busy: { en: "This document is being saved; it opens again in a moment" },
  office_too_many: { en: "Too many people are editing right now (at most {max}); try again later" },
  office_gate: { en: "Your access to the editor has ended; open the document again" },
  office_token: { en: "Invalid or expired token from the document server" },
  office_convert_failed: { en: "The document could not be converted to PDF (code {code})" },
  pdf_pending: { en: "The PDF is still being made; try again in a moment" },
  office_owner_gone: { en: "The owner of this document no longer exists" },
  office_misconfigured: {
    en: "The document editor is not set up correctly; ask your administrator",
  },
} satisfies Record<string, { en: string }>;
