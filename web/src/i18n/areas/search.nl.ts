// Zoeken (fase 6, Z4): de pagina /zoeken, de suggesties onder het veld bovenaan en de meldingen.
// Het gebied is van Z4. Het veld in de balk bovenaan zelf staat in shell (dat is er altijd); de
// suggesties laden dit gebied met hun chunk.
export default {
  title: "Zoeken",
  field: {
    label: "Zoekwoorden",
    placeholder: "Een naam of een woord uit de tekst",
    placeholderImage: "Beschrijf de foto, bijvoorbeeld: hond op het strand",
    hintImage: "Zoekt in je eigen foto's. Druk op Enter.",
    // Ook de beschrijving van het veld zolang het raster van X3 er niet is (I1, review R4; de
    // schakelaar staat in search/ImageHint.tsx). Dezelfde zin als beeld.later, zonder versienummer
    imageLater: "Zoeken op beeld komt in een volgende versie.",
    submit: "Zoeken",
  },
  mode: {
    label: "Zoeken op",
    text: "Namen en tekst",
    image: "Wat er op de foto staat",
  },
  filters: {
    label: "Filters",
    addType: "Soort",
    addTag: "Tag",
    addPerson: "Persoon",
    tagCount: "{name} ({n})",
    personCount: "{name} ({n})",
    from: "Vanaf",
    to: "Tot en met",
    remove: "Filter weghalen: {name}",
    fromChip: "Vanaf {day}",
    toChip: "Tot en met {day}",
    tag: "Tag",
    person: "Persoon",
    place: "Plaats",
    country: "Land: {name}",
    library: "Mediabibliotheek",
    camera: "Camera: {name}",
    folder: "In een map",
  },
  types: {
    folder: "Mappen",
    image: "Afbeeldingen",
    video: "Video",
    audio: "Audio",
    pdf: "PDF",
    document: "Documenten",
    spreadsheet: "Rekenbladen",
    presentation: "Presentaties",
    rest: "Overig",
  },
  results: "Zoekresultaten",
  hint: "Typ een naam of een woord uit de tekst, of kies een filter.",
  none: "Niets gevonden.",
  // Keuze 9: ronde B of C gaf wat er binnen de tijd was
  partial: "Dit zijn de eerste treffers, niet per se de beste. Maak je vraag preciezer.",
  partialNone: "Niets gevonden binnen de tijd. Maak je vraag preciezer.",
  namesOnly: "Alleen op naam gezocht: de tekst in de bestanden kwam niet aan de beurt.",
  indexing: "De index is nog bezig ({pct}%)",
  more: "Meer laden",
  loadingMore: "Meer treffers laden…",
  tooMany: "Er zijn nog meer treffers. Maak je vraag preciezer.",
  // Keuze 81: in de stand tekst, zonder foto's in de treffers
  noPhoto: "Geen foto met deze naam?",
  noPhotoAction: "Zoek op wat er op je foto's staat.",
  groups: {
    people: "Personen",
    places: "Plaatsen",
    count: "{name} ({n})",
  },
  suggest: {
    label: "Suggesties",
    names: "Namen",
    people: "Personen",
    places: "Plaatsen",
    tags: "Tags",
    albums: "Albums",
    image: "Zoek '{q}' op wat er op je foto's staat",
  },
  // De tekstlaag in de PDF-viewer (Keuze 18): waar de eerste treffer van ?q= staat
  viewer: {
    found: "Gevonden op pagina {n}",
    notFound: "Niet gevonden in de tekst van de pagina's",
    notInFirst: "Niet gevonden in de eerste {n} pagina's",
    noText: "Deze pagina's hebben geen tekst om in te zoeken",
  },
} as const;
