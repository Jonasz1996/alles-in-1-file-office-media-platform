# Toetsenbord — X2 (gezichten automatisch)

X2 vindt gezichten in je eigen foto's en stelt voor wie het is ([bouwontwerp.md](bouwontwerp.md),
"X2 — Gezichten automatisch"; Keuze 64–69). Wat er gebouwd is: [x2b.md](x2b.md). In de web komen
vijf plekken bij, alle vijf in de plekken die Z0b neerzette (lazy, met het gebied `photos_faces`):

1. de schakelaar "Gezichten herkennen in mijn foto's" onder Account, blok Foto's;
2. op `/fotos/personen`: de regel met de voortgang, de blokken "Is dit …?" en "Wie is dit?", en de
   knop "Niet benoemde gezichten wissen";
3. het raster van gezichten (bij een voorstel en bij een groep);
4. op de pagina van een persoon: "Misschien ook Anna";
5. in het infopaneel van de viewer: de rij Gezichten met "?" en "Wie is dit?";

en op `/beheer` → Zoeken en foto's het blok Gezichten (Keuze 67).

**Zonder de analyse** (de container staat uit of `PLATFORM_ANALYSE_ENABLED` niet aan) is er
niets van dit alles: geen schakelaar, geen regel, geen raster, geen rij, en op `/beheer` ~~het blok
van 0.8.0 ("Gezichten herkennen komt later in fase 6.", zonder knop of schakelaar)~~ het blok
zonder knop of schakelaar, met sinds 0.8.1 de zin "Gezichten herkennen draait niet op deze
server: de analyse staat uit, of is niet helemaal geïnstalleerd. Waarom staat in de eindtekst van
het installatiescript, bij Gezichten (de container analyse). Stond daar ANALYSE=uit, draai het
script dan opnieuw met ANALYSE=aan; daarna zet ieder het zelf aan bij Account → Foto's. Zie
docs/runbooks/analyse.md." [I2, `bea29ca` en na de review `d538cbb`; in 0.8.0 stond er "Gezichten
herkennen komt later in fase 6."]. Er komt dan ook geen nieuwe sneltoets bij. `e2e/test_zoek_gezichten.py::test_zonder_analyse_ziet_niemand_iets_nieuws`
controleert dat, en dat de web dan geen enkele route van gezichten vraagt.

`e2e/test_zoek_gezichten.py::test_gezichten_met_alleen_het_toetsenbord` doet met alleen Tab,
Enter, Spatie, Esc, de pijlen, J en N, Ctrl+Z, Ctrl+K en typen, tegen de echte app met de
nep-worker (nergens een klik): de schakelaar aan en uit (met beide dialogen); op
`/fotos/personen` "Is dit Anna?" met J, J, N, Ctrl+Z, N en Esc, en "Wie is dit?" met het veld
Naam en Bevestigen; in de viewer het kader van Z6 dat de rij Gezichten van "?" naar Anna zet;
zoeken op Anna; en op `/beheer` het vinkje "Gezichtsherkenning toestaan" (Esc in het dialoog,
verbieden, weer toestaan). De rest van deze lijst (V, Spatie om te deselecteren, Home en End,
"Meer laden", "Niet benoemde gezichten wissen", "Misschien ook Anna", "?" met Ja, Nee en Niet
tonen in de viewer, "Opnieuw zoeken", "Mislukte opnieuw") staat niet in een e2e: welke toets wat
doet, toetst `web/src/photos/faces/faces.test.ts`, de API erachter `api/tests/people/faces/`
([x2b.md](x2b.md), Open). *Mod* is Ctrl, op een Mac ⌘.

## De schakelaar (Account, blok Foto's)

| Toets | Wat |
|---|---|
| Tab | naar het vinkje "Gezichten herkennen in mijn foto's" |
| Spatie (uit → aan) | het dialoog met de toestemming (de tekst van Keuze 66); de focus staat eerst op **Annuleren** |
| Tab, Enter op "Aanzetten" | zet het aan (`PATCH /me/photo-settings`); een melding zegt "Gezichten herkennen staat aan." |
| Spatie (aan → uit) | het dialoog "Gezichten uitzetten?"; de focus eerst op Annuleren; "Uitzetten en wissen" wist |
| Esc in het dialoog | sluit het zonder iets te veranderen; de focus staat weer op het vinkje |

- Na het dialoog staat de focus weer op het vinkje. Een fout van de API staat in de
  `role="alert"` van het blok, zoals bij de andere vinkjes van Z6.
- Heeft de beheerder het verboden, dan staat onder het vinkje "De beheerder heeft
  gezichtsherkenning uitgezet" (via `aria-describedby`); aanzetten geeft dan de fout in de
  `role="alert"`, en het vinkje blijft uit.

## `/fotos/personen`

Bovenaan een regel in een `role="status"` ("Gezichten zoeken: 3 van 8 foto's", "Klaar: 7
gezichten in 8 foto's", "Wacht op de analyse"); een schermlezer leest elke nieuwe stand voor. De
regel vraagt elke 10 s opnieuw zolang er werk is en de pagina te zien is. Staat gezichten uit,
dan is er één regel met een `<details>` "Lees wat dat betekent" (Enter of Spatie klapt hem open)
en een link naar Account.

| Toets | Wat |
|---|---|
| Tab | langs de kaarten: per persoon een knop "Is dit Anna?" (met het aantal, via `aria-describedby`), per groep een knop "Wie is dit?" |
| Enter op een kaart | opent het raster op die plek; de focus staat meteen op het eerste gezicht |
| Tab tot "Niet benoemde gezichten wissen", Enter | een dialoog met het aantal; de focus eerst op Annuleren; "Wissen" zet het in de rij |

## Het raster

Een `role="grid"` met een roving tabindex: Tab komt op één gezicht, de pijlen gaan naar de andere.
Elke cel heeft een label ("Gezicht 2 van 3", na een keuze ": ja", ": nee" of ": niet getoond").
Boven het raster staat de hulp met de toetsen (`aria-describedby` van het raster).

| Toets | Wat |
|---|---|
| ← → ↑ ↓, Home, End | naar een ander gezicht |
| J (Engels: Y) | ja: dit is de voorgestelde persoon (alleen bij "Is dit …?") |
| N | nee: niet die persoon, en niet meer voorgesteld (alleen bij "Is dit …?") |
| V (Engels: H) | niet tonen: het gezicht komt niet meer terug, ook niet in een groep |
| Spatie | selecteren of niet (alleen bij "Wie is dit?"; eerst is alles geselecteerd) |
| Enter | de foto van dit gezicht in de viewer; Esc daar zet de focus terug op het gezicht |
| Ctrl+Z (⌘Z) | de laatste keuze ongedaan maken, zolang de melding met "Ongedaan maken" staat (8 s) |
| Esc | het raster sluiten; de focus staat weer op de knop van de kaart (is die kaart weg, want dit was het laatste voorstel: op de eerste kaart die er nog is, anders op "Niet benoemde gezichten wissen", anders op de regel met de voortgang) |

- Na J, N of V gaat de focus naar het volgende gezicht zonder keuze (of het vorige, als er na dit
  gezicht niets meer is). Een melding (`aria-live`) zegt wat er gebeurde: "1 gezicht bevestigd als
  Anna.", "Niet Anna.", "Wordt niet meer getoond.".
- Met alleen J, N en V gaat het nooit over een gezicht dat je niet zag: elk gezicht krijgt zijn
  eigen toets.
- Ook na de laatste keuze blijft het raster open, met de keuzes en Ctrl+Z, tot Esc; de pagina
  laadt intussen opnieuw, maar haalt het raster niet weg.
- **"Wie is dit?"**: onder het raster staat een formulier. Een `role="status"` zegt hoeveel er
  geselecteerd zijn ("3 van 3 geselecteerd"). Tab naar het veld "Naam" (de combobox van Z6: typ,
  ↓ en ↑ door de lijst, Enter kiest; een naam die er nog niet is, wordt een nieuwe persoon), Tab
  naar **Bevestigen**, Enter. Alleen de gezichten die geselecteerd zijn **en** die in beeld waren,
  gaan naar `confirm`; nooit "de hele groep" (Keuze 66). Esc in het veld sluit het raster.
- Zijn er bij het openen geen gezichten (meer), dan staat de focus op Sluiten.
- "Meer laden" (Enter) haalt de volgende pagina (60 bij voorstellen); de focus blijft op de knop.

## Op de pagina van een persoon: "Misschien ook Anna"

Een blok met zes uitsneden en een knop "Bekijken". Enter opent hetzelfde raster als hierboven;
Esc zet de focus terug op de knop. Was dat het laatste voorstel, dan blijft de kop staan met "Hier
staat niets meer.", en daar staat de focus. Zonder voorstellen is er geen blok.

## In de viewer: de rij Gezichten (infopaneel)

Alleen bij een eigen foto, met gezichten aan. Een ander (ook met een deling) krijgt `[]` en ziet
de rij niet.

| Toets | Wat |
|---|---|
| `i` in de viewer | het infopaneel open of dicht (zoals sinds fase 3) |
| Tab | door het paneel: na de rij Personen (Z6) de rij Gezichten; een benoemd gezicht is een link naar de persoon, een onbekend gezicht een knop "?" met het label "Wie is dit? (1/2)" |
| Enter op een naam | naar de pagina van die persoon |
| Enter op "?" | het formulier "Wie is dit?" (`aria-expanded`); met een voorstel staat er "Is dit Anna?" met **Ja** en **Nee**, en anders staat de focus in het veld Naam |
| Tab, Enter op Ja / Nee / Niet tonen | die keuze; een melding zegt het, de rij laadt opnieuw |
| typen in Naam, ↓, Enter, Tab tot Bevestigen, Enter | dit gezicht is die persoon (of een nieuwe) |
| Esc in het formulier | sluit het formulier (niet de viewer); de focus staat weer op "?" |
| Tab tot "Opnieuw zoeken", Enter | zet deze foto vooraan in de rij |

- Zolang het formulier open is, bladeren ← → Home End niet door de foto's.
- De kaders op de kleine foto boven de rij zijn `aria-hidden`: dezelfde namen staan in de lijst
  eronder.
- Een kader met de hand (de rij Personen van Z6) dat op een gevonden gezicht valt (IoU ≥ 0,5),
  maakt dat gezicht "ja"; de rij Gezichten toont dan de naam in plaats van "?". De e2e doet dat
  met Tab, Enter en de pijlen van het kader (Z6, [toetsenbord-z6.md](toetsenbord-z6.md)).
- Met het formulier van Personen open sluit Esc eerst dat formulier; pas een tweede Esc sluit de
  viewer (zoals in Z6).

## Zoeken op een persoon

Niets nieuws: Mod+K, typen ("anna"), ↓ tot de persoon in de groep "Personen" van de suggesties
(de bestandsnamen staan erboven), Enter (Z4 en Z6). Bevestigde gezichten staan in `photo_person`,
dus het filter Persoon vindt ze.

## `/beheer` → Zoeken en foto's → Gezichten

| Toets | Wat |
|---|---|
| Tab | naar het vinkje "Gezichtsherkenning toestaan" |
| Spatie (aan → uit) | het dialoog "Gezichtsherkenning verbieden?"; de focus eerst op Annuleren; "Verbieden en wissen" wist alles van iedereen |
| Spatie (uit → aan) | zet het meteen weer toe (geen dialoog); niemand staat daardoor aan |
| Tab tot "Mislukte opnieuw", Enter | de mislukte foto's terug in de rij; de focus blijft op de knop |

- De regels (Analyse, Modellen, Grenzen, Gebruikers met gezichten aan, Rij analyse, Mislukt,
  Totaal, Snelheid, Wissen, Kwaliteit) zijn tekst; de kwaliteit is "niet gemeten" tot
  `app.cli analyse-kwaliteit` draaide. Geen namen, geen gezichten, geen per-gebruiker.
- De tab ververst elke 5 s; de focus blijft staan.

## Bronnen en licenties (`/over`, sinds 0.8.1)

Voor iedereen die is ingelogd, ook een gast, met de analyse aan of uit (I2).

| Toets | Wat |
|---|---|
| Tab (op `/account`) | naar de link "Bronnen en licenties" in het deel "Over dit platform" |
| Enter | opent `/over`; de focus staat op de kop "Over dit platform" |
| Tab | langs de links naar de bronnen van de kaart en de modellen |

- Vaste tekst: de kaart en de plaatsen, de drie modellen (YuNet, SFace, SigLIP 2), de pakketten
  van de analyse en waar de volledige licentieteksten staan. De pagina vraagt de server niets.
- `e2e/test_zoek_gezichten.py` doet dit met alleen het toetsenbord, met de analyse aan
  (`44262c7`); `e2e/test_uit080.py` opent `/over` als gast met de analyse uit (`ca8af68`).
