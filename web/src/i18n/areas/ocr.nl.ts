// Tekst in beeld (fase 6, X1; Keuze 23 en 25a): de rij in het infopaneel met de knop "Tekst
// herkennen", en de markering van de zoekvraag in de tekst van een foto in de viewer. Het gebied
// is van X1. De foutteksten van POST /files/{id}/ocr staan in i18n/errors/photos.ts.
export default {
  row: {
    title: "Tekst in beeld",
    loading: "Stand laden…",
    queued: "Wacht op tekstherkenning.",
    donePhoto: "Tekst gevonden in deze foto. Alleen jij vindt hem met Zoeken.",
    donePdf: "De gescande pagina's zijn gelezen; de tekst is doorzoekbaar.",
    none: "Geen tekst gevonden.",
    notYetPhoto: "Nog niet gelezen. Alleen jij vindt de tekst straks met Zoeken.",
    notYetPdf: "De gescande pagina's zijn nog niet gelezen.",
    failed: "Tekst herkennen is mislukt.",
    missing: "Tekst herkennen is niet geïnstalleerd op deze server.",
    badImage: "Deze afbeelding kon niet gelezen worden.",
    hasText: "Dit document heeft al genoeg tekst; tekst herkennen is niet nodig.",
    run: "Tekst herkennen",
    again: "Opnieuw herkennen",
    asked: "Tekst herkennen staat in de wachtrij.",
  },
  photo: {
    found: {
      one: "{n} woord van je zoekvraag gemarkeerd in de foto",
      other: "{n} woorden van je zoekvraag gemarkeerd in de foto",
    },
    notFound: "Niet gevonden in de tekst van deze foto",
  },
} as const;
