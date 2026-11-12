// Gezichten automatisch (fase 6, X2; Keuze 66 en 67): de voorstellen op /fotos/personen, de
// gezichten in het infopaneel, de schakelaar en het blok op /beheer. Het gebied is van X2; Z0b
// zette het leeg neer (Keuze 55), met alleen wat het lege blok op /beheer toont.
export default {
  title: "Gezichten",
  notReady: "Gezichten herkennen komt later in fase 6.",
  // De aantallen in het blok op /beheer zolang X2 het niet zelf vult (Z7, Keuze 51: alleen
  // aantallen, nooit per gebruiker of gezicht)
  admin: {
    faces: "Gezichten",
    named: "Waarvan benoemd",
    usersOn: "Gebruikers met gezichten aan",
    waiting: "Foto's in de rij",
    failed: "Mislukt",
  },
} as const;
