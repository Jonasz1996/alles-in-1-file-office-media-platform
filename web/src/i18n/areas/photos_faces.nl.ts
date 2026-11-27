// Gezichten automatisch (fase 6, X2; Keuze 66 en 67): de voorstellen op /fotos/personen, de
// gezichten in het infopaneel, de schakelaar, en van het blok op /beheer de kop en de zin zonder
// analyse. Het gebied is van X2; Z0b zette het leeg neer (Keuze 55). X2b vulde het met de teksten
// van Keuze 66 en 67. De regels van het blok op /beheer staan sinds 0.8.2 in admin.search.faces:
// elk scherm van gezichten laadt dit gebied mee (budget faces, 12 kB), en die regels toont alleen
// /beheer.
export default {
  title: "Gezichten",
  // Het blok op /beheer zonder gezichten (X2 draait niet: de analyse staat uit, of is niet helemaal
  // geïnstalleerd). Sinds 0.8.1 (I2): dat het niet draait en dat de eindtekst van het
  // installatiescript zegt waarom (review I2, U2; in 0.8.0 stond hier "komt later in fase 6")
  notReady:
    "Gezichten herkennen draait niet op deze server: de analyse staat uit, of is niet helemaal geïnstalleerd. Waarom staat in de eindtekst van het installatiescript, bij Gezichten (de container analyse). Stond daar ANALYSE=uit, draai het script dan opnieuw met ANALYSE=aan; daarna zet ieder het zelf aan bij Account → Foto's. Zie docs/runbooks/analyse.md.",
  switch: {
    label: "Gezichten herkennen in mijn foto's",
    consent:
      "Het platform zoekt gezichten in je eigen foto's en stelt voor wie het is. Daarvoor maakt het van elk gezicht een reeks getallen. Zo'n reeks is biometrische data, ook van mensen op je foto's die zelf niets kozen. De getallen staan versleuteld op deze server, alleen bij jouw foto's. Andere gebruikers zien ze nooit. Wie de server beheert, kan er technisch bij. Reservekopieën van het platform bevatten ze niet; snapshots die de beheerder zelf maakt wel, versleuteld. Zet je dit uit, dan wissen we de gezichten, de getallen en de sleutel. Daarna zijn ook die kopieën niet meer te lezen. Gebruik je het platform voor anderen dan je gezin, lees dan eerst de uitleg in de README.",
    on: "Aanzetten",
    offTitle: "Gezichten uitzetten?",
    offText:
      "We wissen alle gevonden gezichten en hun getallen. Personen die je zelf benoemde of bevestigde, blijven op je foto's staan.",
    off: "Uitzetten en wissen",
    turnedOn: "Gezichten herkennen staat aan.",
    turnedOff: "Gezichten herkennen staat uit; de gezichten worden gewist.",
  },
  status: {
    scanning: "Gezichten zoeken: {done} van {total} foto's",
    done: "Klaar: {faces} gezichten in {photos} foto's",
    waiting: "Wacht op de analyse",
    forbidden: "De beheerder heeft gezichtsherkenning uitgezet",
    wiping: "Gezichten worden gewist …",
    failed: { one: "{n} foto mislukt", other: "{n} foto's mislukt" },
    off: "Gezichten herkennen staat uit.",
    read: "Lees wat dat betekent",
    where: "Aanzetten doe je onder Account, bij Foto's.",
  },
  suggest: {
    title: "Is dit …?",
    question: "Is dit {name}?",
    count: { one: "{n} foto", other: "{n} foto's" },
    yes: "Ja",
    no: "Nee",
    hide: "Niet tonen",
    maybe: "Misschien ook {name}",
  },
  group: {
    title: "Wie is dit?",
    count: { one: "{n} gezicht", other: "{n} gezichten" },
    name: "Naam",
    selected: "{n} van {total} geselecteerd",
    confirm: "Bevestigen",
    pick: "Kies eerst een naam.",
  },
  grid: {
    open: "Bekijken",
    close: "Sluiten",
    more: "Meer laden",
    face: "Gezicht {i} van {total}",
    help: "Pijlen: naar een gezicht. Enter: de foto openen. Esc: sluiten. Ctrl+Z: ongedaan maken.",
    helpSuggest: "J: ja, N: nee, V: niet tonen.",
    helpGroup: "Spatie: selecteren of niet. V: niet tonen.",
    empty: "Hier staat niets meer.",
    chosenYes: "ja",
    chosenNo: "nee",
    chosenHidden: "niet getoond",
  },
  done: {
    yes: { one: "{n} gezicht bevestigd als {name}.", other: "{n} gezichten bevestigd als {name}." },
    no: "Niet {name}.",
    hidden: "Wordt niet meer getoond.",
    undo: "Ongedaan maken",
    undone: "Ongedaan gemaakt.",
  },
  wipe: {
    button: "Niet benoemde gezichten wissen",
    text: {
      one: "We wissen {n} gezicht dat je niet benoemde, met de getallen. Het komt niet terug, ook niet na een nieuwe zoekronde.",
      other:
        "We wissen {n} gezichten die je niet benoemde, met hun getallen. Ze komen niet terug, ook niet na een nieuwe zoekronde.",
    },
    confirm: "Wissen",
    queued: { one: "{n} gezicht wordt gewist.", other: "{n} gezichten worden gewist." },
  },
  row: {
    title: "Gezichten",
    none: "Geen gezichten gevonden op deze foto.",
    unknown: "?",
    who: "Wie is dit?",
    rescan: "Opnieuw zoeken",
    rescanned: "Deze foto staat vooraan in de rij.",
    cancel: "Annuleren",
  },
} as const;
