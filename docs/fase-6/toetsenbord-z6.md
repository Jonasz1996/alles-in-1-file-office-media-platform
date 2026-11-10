# Toetsenbord — Z6 (personen, herinneringen, slimme albums en filters)

Z6 vult de plekken die Z0 open liet: de rij Personen in het infopaneel, `/fotos/personen` en de
pagina van één persoon, en het blok Foto's onder Account. Daarnaast komen er de filters,
"Vandaag, N jaar geleden" en de knop "Zoeken op beeld" op `/fotos`, en de regel van een album.
Alles werkt met alleen het toetsenbord. Er komen geen nieuwe globale sneltoetsen bij. Op de
pagina van een persoon werken F2 en Shift+Delete zoals op de pagina van een album.

De e2e-tests `e2e/test_zoek_personen.py` en `e2e/test_album_regel.py` doen de stappen hieronder
met alleen Tab, Shift+Tab, Enter, Spatie, Esc, de pijlen en typen. Nergens een klik.

## /fotos

| Toets | Wat |
|---|---|
| Tab | na de kop: "Zoeken op beeld", "Kaart", "Dubbele bestanden" (sinds de samenvoeging van golf 3), "Albums", dan het blok Filters, de herinneringen (als die er zijn) en de tijdlijn |
| Enter op "Zoeken op beeld" | naar `/zoeken?mode=image` (Keuze 81); de focus staat in het veld "Zoekwoorden" (de pagina van Z4, sinds de merge van golf 2) |
| typen in "Persoon" | de lijst met je personen die zo heten (een woord van de naam begint met wat je typt, zonder accenten of hoofdletters) |
| ↓ / ↑ in "Persoon" | door de lijst; is hij dicht, dan gaat hij open |
| Enter in "Persoon" | kiest de persoon. De tijdlijn toont alleen zijn foto's. Kies je meer personen, dan moeten ze er allemaal op staan |
| Esc in "Persoon" | sluit de lijst; het veld houdt de focus |
| Enter op een gekozen persoon ("Oma Ria uit het filter halen") | haalt hem uit het filter; de focus gaat terug naar het veld "Persoon" |
| ↑ / ↓ in "Soort" | foto's en video's, alleen foto's of alleen video's |
| "Vanaf" en "Tot en met" | gewone datumvelden; de datum telt bij het verlaten van het veld (Tab) of Enter, zoals op `/zoeken`: typen geeft één vraag, niet één per cijfer. Een omgekeerde periode wordt omgedraaid |
| ↑ / ↓ in "Land" (I1) | alle landen of één land. Een land maakt de lijst "Plaats" korter; een gekozen plaats die niet in dat land ligt, gaat uit het filter |
| ↑ / ↓ in "Plaats" (I1) | alle plaatsen of één plaats ("Amsterdam, Noord-Holland"). Land en Plaats samen: de foto moet aan allebei voldoen |
| typen in "Camera", dan Enter of Tab (I1) | het merk en model, of de eerste woorden ervan: "canon" vindt "Canon EOS 5D", "canon eos" ook "Canon EOS R5", maar "can" niets. Hoofdletters maken niet uit. Het telt bij Enter of het verlaten van het veld, zoals een datum: typen geeft één vraag, niet één per letter |
| Enter op "Filters wissen" | alle filters uit; de focus gaat naar het veld "Persoon". De knop staat er alleen als er een filter aan is |

- "Land" en "Plaats" komen uit de plaatsen van je eigen foto's (dezelfde lijst als naast de kaart).
  Hebben je foto's geen plaats (geen plaatsenbestand, of de plek in je foto's staat uit onder
  Account → Foto's), dan staan ze er niet; "Camera" staat er altijd. De volgorde met Tab: "Persoon",
  "Soort", "Vanaf", "Tot en met", "Land", "Plaats", "Camera", "Filters wissen".
- Hoeveel filters er aan staan, staat in een `status` ("1 filter aan", "2 filters aan"). Een
  schermlezer leest dat voor.
- Past er niets, dan staat er "Geen foto's die bij deze filters passen.", met nog een knop
  "Filters wissen". Ook die zet de focus in het veld "Persoon"; de balk Filters blijft staan tot de
  hele tijdlijn er weer is.
- Een filter blijft in de pagina. Het komt nooit in de adresbalk: de URL blijft `/fotos`.
- De tijdlijn met een filter werkt zoals zonder: de pijlen, Enter voor de viewer, selecteren, A,
  D, Delete. Het selecteren van een reeks (Shift) volgt het filter.

### Herinneringen ("Vandaag, N jaar geleden")

Alleen zonder filters, en alleen als je herinneringen aan hebt (Account → Foto's).

| Toets | Wat |
|---|---|
| Tab | door de foto's van elk jaar; elke foto is een knop met de naam en de datum |
| Enter op een foto | opent hem in de viewer; ← en → gaan door de foto's van dat jaar; Esc sluit hem en zet de focus terug op de knop |
| Enter op "Deze dag verbergen" | deze dag komt niet meer terug; de melding heeft "Ongedaan maken". De focus gaat naar de kop van de pagina, want het blok verdwijnt |

Het blok is een `region` met de naam "Herinneringen"; elk jaar heeft een kop van niveau 3
("Vandaag, 1 jaar geleden").

## De viewer: de rij Personen in het infopaneel (I)

Alleen voor de eigenaar van de foto of video. Voor een ander toont de rij niets.

| Toets | Wat |
|---|---|
| Tab | de personen op de foto (een link naar hun pagina, met "met kader" als er een kader is), per persoon de knop "… van deze foto halen", en "Persoon toevoegen" |
| Enter op "… van deze foto halen" | haalt de persoon van de foto; de focus gaat naar "Persoon toevoegen" |
| Enter op "Persoon toevoegen" | opent het formulier; de focus staat in "Naam" |
| typen in "Naam", ↓ / ↑, Enter | kiest een van je personen. Heet niemand precies zo, dan is er de keuze "Nieuwe persoon: …". Na de keuze voegt een tweede Enter de persoon toe |
| Spatie op "Kader tekenen" | zet het kader aan of uit (een vinkje). Aan: het kader staat in het midden, wat boven het midden |
| Tab naar "Kader op de foto" | het kader zelf (`role=application`) |
| ← → ↑ ↓ in het kader | verplaatst het kader met 2 % van de foto. De viewer bladert dan niet |
| Shift met een pijl | maakt het kader groter (→ en ↓) of kleiner (← en ↑), niet kleiner dan 4 % |
| Enter op "Toevoegen" | voegt de persoon toe; de melding "Oma Ria staat nu op deze foto." en de focus terug op "Persoon toevoegen" |
| Esc in het formulier | sluit eerst een open lijst, daarna het formulier; de focus gaat terug naar "Persoon toevoegen". De viewer blijft open |
| ← → Home End in het formulier | bladeren niet naar een ander bestand, ook niet vanaf "Kader tekenen" of een knop: het formulier blijft. Shift+← → wisselt wel, zoals overal in de viewer |
| ← → op een link of knop van de rij, zonder formulier | naar het vorige of volgende bestand, zoals elders in de viewer. Wat de focus had, is bij het nieuwe bestand weg; de focus staat dan op het beeld, waar de pijlen verder werken |

- Waar het kader staat, staat in tekst onder het kader (`aria-live`): "Kader 41 % van links,
  25 % van boven, 30 % breed en 44 % hoog.". Een schermlezer leest elke stap voor.
- Met de muis kan het ook: slepen over de foto tekent een kader.
- Een video heeft geen kader: daar is alleen de naam.

## /fotos/personen

| Toets | Wat |
|---|---|
| Tab | het zoekveld "Zoek een persoon", dan de kaarten van je personen (elke kaart is een link) |
| typen in het zoekveld | de kaarten die passen (zoals in "Persoon" op `/fotos`) |
| Enter in het zoekveld | opent de eerste persoon die past |
| Spatie op "Verborgen personen tonen" | toont ook je verborgen personen, in een eigen lijst |

Wat je typt, blijft in de pagina; het komt niet in de adresbalk. De lege plek van X2
(`PeopleFaces`) staat boven de lijst en toont nog niets.

## De pagina van één persoon (`/fotos/personen/:id`)

| Toets | Wat |
|---|---|
| Tab | de knoppen Hernoemen, Samenvoegen, Verbergen (of Niet meer verbergen), Niet in herinneringen (of Weer in herinneringen), Wissen, dan de link "Alle personen" en het raster |
| F2 | hernoemen (een dialoog met het veld "Naam", Enter slaat op, Esc annuleert) |
| Enter op Samenvoegen | een dialoog met "Samenvoegen met" (een combobox: typen, ↓ / ↑, Enter); Enter op Samenvoegen voegt samen en opent de andere persoon |
| Enter op Verbergen | de persoon staat niet meer in de lijst en niet in de suggesties; de knop wordt "Niet meer verbergen" |
| Enter op Niet in herinneringen | foto's met deze persoon komen niet meer in je herinneringen |
| Shift+Delete, of Enter op Wissen | wissen, na een vraag (de focus staat op Annuleren; Esc is annuleren). De foto's zelf blijven |
| het raster | zoals een album: de pijlen, Enter opent de viewer, Spatie selecteert, Mod+A selecteert alles; "Downloaden" staat in de balk van de selectie |

## Een album: de regel

Alleen voor de eigenaar.

| Toets | Wat |
|---|---|
| Tab naar "Regel", Enter | het dialoog "Regel voor …"; de focus staat in "Naam of tekst" |
| typen, Enter | slaat op (Enter in een veld verstuurt het formulier). De melding "De regel is opgeslagen. Het album wordt bijgewerkt." |
| Tab | "Persoon" (zoals op `/fotos`), "Soort", "Vanaf", "Tot en met" |
| Spatie op "Nieuwe foto's die passen, ook delen" | het antwoord op de vraag van Keuze 42 als vinkje. Alleen als het album al een regel heeft en gedeeld is |
| Enter op "Nu bijwerken" | het album wordt opnieuw bij de regel gebracht (op de achtergrond); het dialoog blijft open |
| Enter op "Regel weghalen" | de regel weg; de foto's blijven in het album |
| Esc, of Annuleren | sluit zonder iets te bewaren |

- Zonder naam of tekst en zonder filter bewaart het niets: "Vul een naam of tekst in, of kies
  een filter." (`role=alert`) en de focus gaat terug naar "Naam of tekst".
- Een foto die de regel erin zette en die nog niet gedeeld wordt, heeft het merkje
  "alleen voor jou". Het staat ook achter de naam van de cel (de naam, de datum en dan
  ", alleen voor jou"), zodat een schermlezer het voorleest. Anderen zien die foto niet, en het merkje ook niet.
- Achter de naam van het album staat "met een regel".

### De vraag over delen (Keuze 42)

De vraag "Nieuwe foto's ook delen?" komt:

- na Opslaan van een regel op een album dat al gedeeld is of een link heeft;
- na een nieuwe deling of een nieuwe link op een album met een regel waarvan nieuwe foto's nog
  niet gedeeld worden. Hij staat dan bovenop het deeldialoog.

| Toets | Wat |
|---|---|
| (bij het openen) | de focus staat op "Nee, alleen voor mij" |
| Enter | nee: wat de regel erin zet, ziet alleen jij |
| Tab naar "Ja, delen", Enter | ja: alle foto's van de regel worden gedeeld, ook wat er later bij komt |
| Esc | nee |

Het deeldialoog (S) van een album met een regel zegt hoeveel foto's uit de regel komen en
hoeveel daarvan gedeeld worden ("2 foto's komen uit de regel; 0 daarvan worden gedeeld.", een
`status`).

## Account → Foto's

Een blok met een kop van niveau 2. Niet voor een gast.

| Toets | Wat |
|---|---|
| Tab | "De plek in je foto's gebruiken (alleen voor jou)", "Herinneringen tonen op Foto's", "Gezichten herkennen", "Zoeken op beeld" |
| Spatie | zet het vinkje aan of uit; de melding "Opgeslagen." |

- Gezichten (X2) en zoeken op beeld (X3) zijn er nog niet. Aanzetten weigert de API (409; 403 als
  de beheerder het verbiedt). Het vinkje blijft dan uit en de reden staat eronder
  (`role=alert`, en het vinkje wijst er met `aria-describedby` naar). De rest van het blok werkt
  gewoon door.
- Als X2 of X3 hun eigen schakelaar leveren (`FacesSwitch`, `BeeldSwitch`), verdwijnt het vinkje
  van Z6 en staat hun schakelaar op die plek.
