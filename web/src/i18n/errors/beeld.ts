// Foutcodes van zoeken op beeld (fase 6, X3; Keuze 80). Z0b zette de codes van het contract neer
// (Keuze 55); de Nederlandse tekst komt van de server, dit is de Engelse.
export default {
  beeld_off: { en: "Search by image is turned off" },
  beeld_no_vector: { en: "This photo has not been looked at yet; try again later" },
  beeld_not_installed: { en: "Search by image is not installed on this server" },
} satisfies Record<string, { en: string }>;
