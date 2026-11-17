// Foutcodes van gezichten automatisch (fase 6, X2; de API van X2). Z0b zette de codes van het
// contract neer (Keuze 55); de Nederlandse tekst komt van de server, dit is de Engelse.
export default {
  faces_forbidden: { en: "The administrator has turned off face recognition" },
  faces_off: { en: "Face recognition is turned off for your photos" },
  faces_unavailable: { en: "Face recognition is not installed on this server" },
  faces_too_many: { en: "Choose at most 200 faces at a time" },
  faces_gone: { en: "These faces have changed in the meantime; load the page again" },
} satisfies Record<string, { en: string }>;
