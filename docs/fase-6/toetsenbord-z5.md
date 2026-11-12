# Toetsenbord — Z5 (de kaart, dubbele bestanden en de waarschuwing bij delen)

Z5 bouwt de kaart (`/fotos/kaart`), de rij Plek in het infopaneel, `/dubbel` en de zin over de
locatie in het deeldialoog. Alles werkt met alleen het toetsenbord. Er komen geen nieuwe globale
sneltoetsen bij: de toetsen hieronder werken alleen als de focus op de kaart, in een groep op
`/dubbel` of in het deeldialoog staat. Met Ctrl, Cmd of Alt erbij doet de kaart niets: Ctrl met +
of - blijft de zoom van de browser.

De e2e-tests `e2e/test_zoek_kaart.py`, `e2e/test_zoek_dubbel.py` en `e2e/test_share_warning.py`
doen de stappen hieronder met alleen Tab, Shift+Tab, Enter, Spatie, Esc, de pijlen, Home, End, +
en -. Nergens een klik. Sinds de samenvoeging met Z3 (golf 3) zijn ze groen (zie [z5.md](z5.md)).
Sinds de review van golf 3 doet `test_zoek_kaart.py` ook de lijst Plaatsen (met een
`places.sqlite` uit de kleine bronnen van `api/tests/geo`), en kijkt hij na dat de focus nergens
naar de body valt (`test_de_focus_valt_niet_naar_de_body`).

Sinds de samenvoeging staan op `/fotos` de links "Kaart" en "Dubbele bestanden" (Tab na "Zoeken
op beeld", zie [toetsenbord-z6.md](toetsenbord-z6.md)); Enter opent `/fotos/kaart` of `/dubbel`.

## De kaart (`/fotos/kaart`)

De kaart is één element met `role="region"` en de naam "Kaart met je foto's". Onder de kaart
staat in gewone tekst welke toetsen werken (`aria-describedby`).

| Toets | Wat |
|---|---|
| Tab | na de kop: de kaart zelf, dan de stippen (groepjes foto's) van boven naar beneden en per rij van links naar rechts, dan de knoppen Inzoomen, Uitzoomen en "Al je foto's in beeld", dan de lijst Plaatsen |
| ← → ↑ ↓ (op de kaart) | schuift een kwart venster; met Shift een half venster. Pijl links: je kijkt verder naar het westen |
| + of = | één stap inzoomen, rond het midden |
| - of _ | één stap uitzoomen, rond het midden |
| Home | al je foto's in beeld (met een plaats gekozen: de foto's van die plaats) |
| ← → ↑ ↓, +, -, Home op een stip | hetzelfde als op de kaart; de stip is daarna weg (nieuwe stippen), dus de kaart krijgt de focus (review golf 3, web F1) |
| Enter op Inzoomen of Uitzoomen | één stap; op de grens doet de knop niets en houdt hij de focus (`aria-disabled`, niet `disabled`; review golf 3, web F4) |
| Enter of Spatie op een stip | meer dan één foto en nog niet op de kleinste cellen: de kaart zoomt in op die stip en de focus gaat terug naar de kaart (de stip is dan weg). Anders: de viewer opent met de foto's van die stip |
| Esc in de viewer | sluit de viewer; het adres verliest `?bestand=` |
| ← / → in de viewer | de vorige of volgende foto van de stip |

- Elke stip heeft een naam voor de schermlezer: de plaats (vanaf zoom 6) of het land, en het
  aantal: "Frankrijk, 1 foto", "Amsterdam, 2 foto's". Zonder plaatsnamen alleen het aantal.
- Een `status` onder de kaart zegt hoeveel plekken er in beeld zijn ("3 plekken met foto's in
  beeld"), pas als de kaart stilstaat.
- Terwijl er nieuwe cellen laden, staat de kaart op `aria-busy="true"`; een schermlezer wacht dan
  tot de stippen er staan.
- Het venster en de zoom komen nooit in de adresbalk: de URL blijft `/fotos/kaart`.
- Slepen, knijpen en het wieltje werken ook (het wieltje zoomt de kaart, de pagina scrolt niet);
  dubbelklikken zoomt in op die plek.

### De lijst Plaatsen

| Toets | Wat |
|---|---|
| Tab | langs "Alle plaatsen" en daarna elke plaats, met het aantal foto's |
| Enter of Spatie op een plaats | de kaart toont alleen de foto's van die plaats (`aria-pressed="true"` op die knop) en zet ze in beeld |
| Enter op "Alle plaatsen" (in de lijst, of naast "Alleen Utrecht" boven de kaart) | het filter weer uit. In de lijst houdt de knop de focus; boven de kaart verdwijnt de knop met het filter, en dan krijgt de kaart de focus (review golf 3, web F2) |

Is `places.sqlite` niet geladen, dan staat er "Nog geen plaatsen. …"; de kaart werkt dan gewoon,
met landen in plaats van plaatsen.

## De rij Plek in het infopaneel (alleen de eigenaar)

| Toets | Wat |
|---|---|
| Tab | in het infopaneel komt de rij Plek mee in de gewone volgorde: de plaats, de coördinaten en de hoogte staan er als tekst; het kaartje erbij is `aria-hidden` |
| Enter op "Op de kaart" | naar `/fotos/kaart`, ingezoomd op die plek met een ring eromheen. De plek gaat via het geheugen van de pagina mee, nooit via het adres. Was de viewer al op de kaart open (de foto's van een stip), dan sluit hij en krijgt de kaart de focus (review golf 3, web F3) |

Heeft de foto geen plek (404), dan is er geen rij.

## `/dubbel`

| Toets | Wat |
|---|---|
| Tab | na de kop en de uitleg: het tabblad dat aan staat ("Precies gelijk" of "Lijken erop"), dan het paneel, dan per groep: "Bekijken" van het eerste bestand, de map-link, het vinkje "Kiezen", en zo per bestand; dan "N bestanden naar de prullenbak" (alleen als er iets is aangevinkt) en "Niet gelijk" |
| ← / → op de tabbladen | het andere tabblad, meteen actief (WAI-ARIA tabs). Het adres krijgt `?soort=lijkt` voor "Lijken erop"; terug en herladen werken |
| ← / → binnen een groep | naar hetzelfde soort knop bij het vorige of volgende bestand: van "Bekijken" naar "Bekijken", van vinkje naar vinkje, van map naar map |
| Home / End binnen een groep | het eerste of laatste bestand van de groep |
| Enter op "Bekijken" | de viewer met de bestanden van die groep (← en → wisselen), Esc sluit |
| Spatie op "Kiezen" | vinkt dat bestand aan of uit. Er staat nooit iets vooraf aangevinkt |
| Enter op "N bestanden naar de prullenbak" | stuurt alleen wat aangevinkt is naar de prullenbak. Is alles van de groep aangevinkt, dan vraagt een dialoog eerst of dat echt de bedoeling is (Esc of "Annuleren": niets weg). Daarna een melding met "Ongedaan maken" |
| Enter op "Niet gelijk" | de groep komt niet meer terug (ook niet na herladen) en gaat weg |

- Zolang er niets is aangevinkt, is de knop van de prullenbak uitgeschakeld (Tab slaat hem over);
  "Vink eerst aan wat weg mag." staat ernaast en hoort bij de knop (`aria-describedby`).
- Na "Naar de prullenbak" of "Niet gelijk" krijgt de kop van dezelfde groep de focus (als die er
  nog is), anders die van de volgende groep, anders die van de vorige, anders de kop van de
  pagina. De kop zegt het niveau en hoeveel bestanden er nog zijn.
- Na "Meer laden": blijft de knop staan, dan houdt hij de focus; is alles geladen, dan krijgt de
  kop van de eerste nieuwe groep de focus.

## De zin over de locatie in het deeldialoog

Het deeldialoog opent zoals altijd (S op een rij of een foto, of "Delen"). De zin komt bovenaan
als het antwoord van de server er is: "12 foto's hierin hebben een locatie in het bestand. Wie ze
downloadt, krijgt die mee." of "Deze foto heeft een locatie in het bestand. …", en zo nodig "Van
3 foto's of video's weten we het nog niet (nog niet gelezen, of media staat uit)."

- Het dialoog wacht er niet op: Alt+P, Alt+L en Tab werken meteen.
- De zin heeft `role="note"`. De live-regio leest hem één keer voor; de focus blijft waar hij was
  (bijvoorbeeld in het zoekveld), zodat typen niet onderbroken wordt.
- Bij een publieke link (Alt+L, "Link maken"), een persoon, een groep en een selectie is het
  hetzelfde dialoog en dezelfde zin.
- Een document (geen foto of video) wordt niet nagegaan: geen zin.
