// Zoeken op beeld (fase 6, X3; Keuze 81 en 82): de stand "Wat er op de foto staat" op /zoeken, het
// raster, "Meer zoals deze" in het infopaneel, de schakelaar en het blok op /beheer. Het gebied is
// van X3; Z0b zette het leeg neer (Keuze 55), met alleen wat het lege blok op /beheer toont.
export default {
  title: "Zoeken op beeld",
  notReady: "Zoeken op beeld komt later in fase 6.",
  // De aantallen in het blok op /beheer zolang X3 het niet zelf vult (Z7, Keuze 51: alleen
  // aantallen, nooit een vector of een naam)
  admin: {
    seen: "Bekeken",
    seenValue: "{seen} van {photos} foto's",
    queries: "Vragen het afgelopen uur",
    failed: "Mislukt",
  },
} as const;
