// Foutcodes van de kaart, de plaatsen en de duplicaten (fase 6, bouwontwerp §3.4; Z3 en Z5). Z0
// zette de codes van het contract neer; de Nederlandse tekst komt van de server, dit is de Engelse.
export default {
  dupes_bad_key: { en: "This group of duplicates no longer exists" },
  geo_not_loaded: { en: "Place names are not loaded on this server" },
} satisfies Record<string, { en: string }>;
