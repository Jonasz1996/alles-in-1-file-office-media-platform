// Zoeken op beeld (fase 6, X3; Keuze 81 en 82): de stand "Wat er op de foto staat" op /zoeken, het
// raster, "Meer zoals deze" in het infopaneel, de schakelaar en het blok op /beheer. Het gebied is
// van X3; Z0b zette het leeg neer (Keuze 55). Geen versienummer in een tekst: of zoeken op beeld er
// is, zegt de server. Met de analyse uit zeggen notReady en later sinds 0.8.1 (I2) dat het uitstaat
// en wat de beheerder kan doen (in 0.8.0: "komt later").
export default {
  title: "Zoeken op beeld",
  // Het blok op /beheer zonder stand (de analyse staat uit): wat de beheerder kan doen
  notReady:
    "Zoeken op beeld staat uit op deze server: de analyse draait niet (ANALYSE=uit in /opt/platform/.env). Aanzetten: draai het installatiescript opnieuw met ANALYSE=aan; daarna zet ieder het zelf aan bij Account → Foto's. Zie docs/runbooks/analyse.md.",
  // De stand beeld op /zoeken als de server zoeken op beeld niet kent (I1, S8; de analyse staat
  // uit): zonder versienummer, voor iedereen
  later: "Zoeken op beeld staat uit op deze server (ANALYSE=uit in /opt/platform/.env). De beheerder kan het aanzetten met ANALYSE=aan.",
  grid: {
    label: "Foto's die passen bij '{q}'",
    labelLike: "Foto's die lijken op de gekozen foto",
    more: "Meer foto's laden",
    loadingMore: "Meer foto's laden…",
  },
  // De meldingen in role="status" (Keuze 81)
  notice: {
    indexing: "Je foto's worden nog bekeken: {pct}% klaar. Wat nog niet bekeken is, vind je nog niet.",
    none: "Geen foto's gevonden die hierbij passen.",
    few: "Misschien zijn er meer. Maak de vraag ruimer of haal een filter weg.",
    slow: "Zoeken op beeld duurde te lang. Dit zijn de treffers op naam en tekst.",
    down: "Zoeken op beeld werkt nu niet. Dit zijn de treffers op naam en tekst.",
    no_model: "Zoeken op beeld is nog niet klaar: het model ontbreekt. Dit zijn de treffers op naam en tekst.",
    offYou: "Zoeken op beeld staat uit. Je zet het aan bij Account → Foto's.",
    offAdmin: "Zoeken op beeld staat uit voor dit platform.",
    noVector: "Deze foto is nog niet bekeken. Probeer het straks opnieuw.",
  },
  // Het infopaneel van de viewer
  like: "Meer zoals deze",
  // De regel in Account → Foto's
  switch: {
    label: "Zoeken op beeld",
    hint: "Je foto's worden op de achtergrond bekeken, zodat je ze kunt vinden op wat erop staat ('strand', 'hond'). Dat gebeurt op deze server; er gaat niets naar buiten. Alleen jij kunt zo in je eigen foto's zoeken. Uit wist wat alleen voor jou bewaard is. Snapshots die de beheerder zelf van de server maakt, houden het tot ze verlopen. Wat je mag verwachten: één woord geeft meestal een paar goede foto's bovenaan. Sommige woorden vinden weinig; zoek dan op 'berg' in plaats van 'bergen', of op 'bootje' in plaats van 'boot'. Bij 'Meer zoals deze' gaat ongeveer de helft van de eerste foto's over hetzelfde onderwerp.",
    saved: "Opgeslagen.",
  },
  // Het blok op /beheer (Keuze 82): alleen aantallen, nooit een vector, een vraag of een naam
  admin: {
    state: "Zoeken op beeld",
    on: "aan",
    off: "uit",
    turnOff: "Zet uit voor iedereen",
    turnOn: "Zet aan voor iedereen",
    turnedOff: "Zoeken op beeld staat uit voor iedereen. De vectoren blijven tot je de beeldindex wist.",
    turnedOn: "Zoeken op beeld staat aan.",
    rebuild: "Opnieuw berekenen",
    rebuildStarted: "Opnieuw berekenen is begonnen.",
    wipe: "Beeldindex wissen",
    wipeTitle: "Beeldindex wissen?",
    wipeText:
      "Alle vectoren, de cache en het geheugen van de container gaan weg. Opnieuw berekenen kost ongeveer twee core-uur per 100.000 foto's.",
    wipeConfirm: "Wissen",
    wiped: "De beeldindex wordt gewist.",
    model: "Model",
    modelName: "SigLIP 2 B/32-256 (Google, Apache-2.0), int8.",
    modelOk: "In orde.",
    modelMissing: "Ontbreekt; install.sh haalt het bij de volgende run.",
    modelUnverified: "Niet nagerekend in CI.",
    analyse: "Container analyse",
    running: "draait",
    notRunning: "draait niet",
    seen: "Bekeken",
    seenValue: "{seen} van {photos} foto's",
    failed: "Mislukt",
    failedCode: "{code}: {count}",
    hour: "Het afgelopen uur",
    hourValue: {
      one: "{count} vraag, p95 {p95}; terug naar gewoon zoeken: {slow} keer te traag, {down} keer zonder container, {noModel} keer zonder model",
      other: "{count} vragen, p95 {p95}; terug naar gewoon zoeken: {slow} keer te traag, {down} keer zonder container, {noModel} keer zonder model",
    },
    noP95: "nog niet gemeten",
    space: "Ruimte",
    spaceValue: "{db} in de database, {cache} in de cache, {models} aan modellen",
    bench: "beeld-bench",
    benchValue: "{time}: p95 {p95}, {rate} foto's per minuut",
    quality: "beeld-kwaliteit",
    qualityOk: "{time}: in orde",
    qualityBad: "{time}: niet in orde",
    notDone: "nog niet gedaan",
    privacy:
      "De vectoren beschrijven wat er op een foto staat. Reservekopieën die het platform maakt, bevatten ze niet. Maak je zelf snapshots van /data (ZFS, restic), dan staan ze daar wel in, tot je die snapshot weggooit.",
  },
} as const;
