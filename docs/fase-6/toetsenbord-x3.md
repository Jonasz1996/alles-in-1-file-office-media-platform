# Toetsenbord — X3 (zoeken op beeld)

X3 maakt je eigen foto's vindbaar op wat erop staat ([bouwontwerp.md](bouwontwerp.md), "X3 —
Zoeken op wat er op een foto staat"; Keuzes 78, 79, 81 en 82). In de web komen vier dingen bij:
het raster in de stand "Wat er op de foto staat" op `/zoeken`, de rij "Meer zoals deze" in het
infopaneel van de viewer, het vinkje "Zoeken op beeld" onder Account → Foto's, en het blok "Zoeken
op beeld" in de tab "Zoeken en foto's" op `/beheer`. Er komt geen nieuwe sneltoets bij: de pijlen,
Home, End, PageUp, PageDown, Tab, Enter, Spatie, `i` en Esc werken zoals elders in de app. *Mod*
is Ctrl, op een Mac ⌘.

Dit alles alleen met de analyse aan. Met de analyse uit (geen container, `ANALYSE` uit of geen
regel) ziet de web eruit en werkt hij zoals in 0.8.0: de stand beeld toont één zin ("Zoeken op
beeld komt in een volgende versie."), het vinkje en het blok zijn zoals in 0.8.0, en de rij "Meer
zoals deze" is er niet.

`e2e/test_zoek_beeld.py` doet de stappen hieronder met alleen Tab, Shift+Tab, de pijlen, typen,
Enter, Spatie, `i` en Esc, met de nep-worker met vaste uitvoer. Nergens een klik. Wat er gebouwd
is: [x3b.md](x3b.md).

## De stand kiezen (/zoeken)

| Toets | Wat |
|---|---|
| Tab naar "Zoeken op", → | de stand "Wat er op de foto staat" (`/zoeken?mode=image`); ← terug naar "Namen en tekst" (zoals Z4) |
| Mod+K, typen, ↓ naar de laatste suggestie, Enter | `/zoeken?q=…&mode=image` (de suggestie "Zoek '…' op wat er op je foto's staat", Z4) |
| Enter op "Zoeken op beeld" op `/fotos` | `/zoeken?mode=image` met de focus in het veld (Z4) |

- In de stand beeld beschrijft het veld zich als "Zoekt in je eigen foto's. Druk op Enter.". Er
  is geen zoeken terwijl je typt (Keuze 71): elke vraag kost tijd van de container.
- De stand van zoeken op beeld komt eerst van `GET /photos/beeld`. Zolang die er niet is, staat er
  niets onder het veld. Kent de server zoeken op beeld niet (de analyse staat uit), dan staat er
  "Zoeken op beeld komt in een volgende versie.", ook in de beschrijving van het veld, zoals in
  0.8.0.

## Zoeken en het raster

| Toets | Wat |
|---|---|
| typen in "Zoekwoorden", Enter | zoekt (`GET /search?mode=image&q=…`); de URL wordt `/zoeken?q=…&mode=image`; de focus blijft in het veld |
| Tab | naar het raster: één tabstop, de foto met de focus (eerst de eerste) |
| ← → | de foto ernaast |
| ↑ ↓ | de foto erboven of eronder; op de korte laatste rij brengt ↓ je naar de laatste foto |
| Home, End | de eerste of de laatste geladen foto |
| PageUp, PageDown | drie rijen omhoog of omlaag |
| Enter | opent de viewer met die foto (`?bestand=`) |
| Esc | terug naar het veld "Zoekwoorden" |
| Tab naar "Meer foto's laden", Enter | de volgende 48 foto's erbij; de focus op de eerste nieuwe foto (zoals "Meer laden" in Z4); na de laatste pagina is de knop weg |

- Het raster is een `role="grid"` met een `aria-label`: "Foto's die passen bij 'strand'", of bij
  "Meer zoals deze" "Foto's die lijken op de gekozen foto". Elke rij is een `role="row"`, elke
  foto een `role="gridcell"` met de naam en de datum als tekst. Het aantal kolommen volgt de
  breedte (cellen van minstens 160 px); de pijlen volgen dat aantal.
- Alleen je eigen, levende foto's (Keuze 79). Een video, een document of een foto die met je
  gedeeld is, staat er niet in.

## De viewer vanuit het raster

| Toets | Wat |
|---|---|
| ← → in de viewer | de vorige of volgende foto uit het raster (zoals elke viewer) |
| Esc | sluit de viewer; de focus staat op de foto die je het laatst zag |
| `i` | het infopaneel open of dicht |

Daarna staat Esc weer op het raster: nog een keer Esc brengt je naar het veld.

## De meldingen

Alle meldingen staan in één `role="status"` boven het raster; een schermlezer leest elke nieuwe
voor. De tekst komt uit de stand en uit het antwoord, nooit uit de vraag.

| Wanneer | Melding | Raster |
|---|---|---|
| je foto's worden nog bekeken | "Je foto's worden nog bekeken: 40% klaar. Wat nog niet bekeken is, vind je nog niet." | wat al gevonden is |
| te traag (de klok van 800 ms, Keuze 78) | "Zoeken op beeld duurde te lang. Dit zijn de treffers op naam en tekst." | de treffers van gewoon zoeken met dezelfde vraag |
| de container is weg | "Zoeken op beeld werkt nu niet. Dit zijn de treffers op naam en tekst." | idem |
| het model ontbreekt | "Zoeken op beeld is nog niet klaar: het model ontbreekt. Dit zijn de treffers op naam en tekst." | idem |
| niets gevonden | "Geen foto's gevonden die hierbij passen." | geen |
| weinig gevonden met een filter | "Misschien zijn er meer. Maak de vraag ruimer of haal een filter weg." | wat er is |
| jij hebt het uit staan | "Zoeken op beeld staat uit. Je zet het aan bij Account → Foto's." | geen; er gaat geen vraag naar `/search` |
| de beheerder heeft het uit gezet | "Zoeken op beeld staat uit voor dit platform." | geen; er gaat geen vraag naar `/search` |
| "Meer zoals deze" op een foto zonder vector | "Deze foto is nog niet bekeken. Probeer het straks opnieuw." | geen |

Een andere fout (te veel vragen, een filter dat niet kan) staat eronder in een `role="alert"`.

## "Meer zoals deze" (infopaneel van de viewer)

De rij staat in het infopaneel bij een foto die van jou is, als zoeken op beeld voor jou aan staat
(ook als je foto's nog bekeken worden). Bij een foto van een ander, met zoeken op beeld uit, of met
de analyse uit is er geen rij, zoals in 0.8.0.

| Toets | Wat |
|---|---|
| `i` in de viewer | het infopaneel open |
| Tab naar "Meer zoals deze" | de link in het blok "Zoeken op beeld" |
| Enter | `/zoeken?mode=image&like=<id>`; de viewer gaat dicht en de focus staat op de eerste foto van het raster |

De foto zelf staat meestal bovenaan; daarna de foto's die erop lijken.

## "Zoeken op beeld" (Account → Foto's)

Een gewoon vinkje in het blok Foto's onder Account. Standaard uit (Vraag 11). Tab erheen, Spatie
zet het aan of uit; een melding zegt "Opgeslagen.", de focus blijft op het vinkje. De uitleg eronder
(de beschrijving van het vinkje) zegt wat er gebeurt: je foto's worden op deze server bekeken, er
gaat niets naar buiten, alleen jij kunt zo zoeken, en uit wist wat alleen voor jou bewaard is.
Een fout van de API staat in de `role="alert"` van het blok, zoals bij de andere vinkjes (Z6).

Met de analyse uit is het vinkje zoals in 0.8.0.

## Het blok "Zoeken op beeld" (/beheer, tab "Zoeken en foto's")

| Toets | Wat |
|---|---|
| Tab naar "Zet uit voor iedereen", Enter of Spatie | zoeken op beeld uit voor iedereen; de knop heet dan "Zet aan voor iedereen" en houdt de focus; een melding zegt het |
| Tab naar "Opnieuw berekenen", Enter | de vectoren opnieuw (job op de achtergrond) |
| Tab naar "Beeldindex wissen", Enter | een bevestiging met de focus op "Annuleren" (de veilige knop); Tab naar "Wissen" en Enter wist; Esc of Enter op "Annuleren" breekt af; daarna staat de focus weer op de knop |

Daaronder staan alleen getallen en woorden van de app: de stand, het model, of de container draait,
hoeveel foto's bekeken zijn, mislukt per code, het afgelopen uur, de ruimte, de laatste uitkomst
van `beeld-bench` en `beeld-kwaliteit`, en de vaste zin over snapshots. Nooit een vector, een vraag
of een bestandsnaam (Keuze 51 en 82). De tab ververst elke 5 seconden; de focus blijft waar hij
was.

Met de analyse uit staat er, zoals in 0.8.0, alleen "Zoeken op beeld komt later in fase 6.", zonder
knop.

## Afgevinkt met de e2e

- [ ] `/zoeken`, Tab naar "Namen en tekst", →: `/zoeken?mode=image`, "Wat er op de foto staat"
      gekozen en met de focus; de beschrijving van het veld is "Zoekt in je eigen foto's. Druk op
      Enter."; de zin "komt in een volgende versie" staat er niet.
- [ ] Shift+Tab naar het veld, "strand", Enter: de twee oranje foto's bovenaan, dan de rest; de URL
      is `/zoeken?q=strand&mode=image`; het raster heet "Foto's die passen bij 'strand'"; elke foto
      heeft zijn naam en datum; geen melding; de focus in het veld.
- [ ] Tab naar het raster, →, Enter: de viewer met de tweede foto; Esc: dicht, de focus op die foto;
      Esc: de focus in het veld.
- [ ] Vanuit het raster een oranje foto openen, `i`, Tab naar "Meer zoals deze", Enter:
      `/zoeken?mode=image&like=…` zonder `q`, de twee oranje foto's bovenaan, het raster heet
      "Foto's die lijken op de gekozen foto", de viewer is dicht en de focus staat op de eerste foto.
- [ ] 50 foto's: het raster toont er 48; Tab naar "Meer foto's laden", Enter: `offset=48`, 50
      foto's, de knop weg en de focus op de 49e foto.
- [ ] Een vraag die te lang duurt: de melding "Zoeken op beeld duurde te lang. Dit zijn de treffers
      op naam en tekst." en de foto die op naam gevonden is, in hetzelfde raster.
- [ ] Account → Foto's: "Zoeken op beeld" staat standaard uit, met de uitleg als beschrijving;
      Spatie: aan, "Opgeslagen."; Spatie: uit, de focus op het vinkje; na elke keer vraagt de pagina
      de stand opnieuw (na uit: "uit"). Daarna in de stand beeld:
      "Zoeken op beeld staat uit. Je zet het aan bij Account → Foto's.", geen raster en geen vraag
      aan `/search`.
- [ ] `/beheer` → "Zoeken en foto's": het blok met de stand, het model, de container, "6 van 6
      foto's"; Enter op "Zet uit voor iedereen": uit, de focus op "Zet aan voor iedereen"; Eva ziet
      "Zoeken op beeld staat uit voor dit platform." zonder vraag aan `/search`; weer aan: Eva vindt
      haar foto's weer.
- [ ] Geen verzoek naar een andere host, in geen van de tests.
- [ ] Met de analyse uit: de stand beeld toont alleen "Zoeken op beeld komt in een volgende
      versie." (ook in de beschrijving van het veld), zonder raster, melding of vraag aan
      `/search`; Account → Foto's zonder de uitleg van beeld; de viewer zonder "Meer zoals deze";
      `/beheer` met "Zoeken op beeld komt later in fase 6." en geen knop.
