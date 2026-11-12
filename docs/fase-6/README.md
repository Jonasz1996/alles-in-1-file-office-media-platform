# Fase 6 — Zoeken en foto's

Status: **kern gebouwd, versie 0.8.0 (11 november 2026)**. De kern van fase 6 staat op de branch
`claude/stappenplan-uitwerken-lu4uv3`: zoeken in de inhoud van je bestanden, tekst in scans en
foto's, de kaart met plaatsen en landen, dubbele bestanden, personen, herinneringen en slimme
albums. Gezichten automatisch herkennen en zoeken op wat er op een foto staat zitten er nog niet in
([Wat komt in 0.8.1](#wat-komt-in-081)). Het ontwerp stelt grenzen aan de snelheid en het geheugen
(§4.4 en §4.5 van het [bouwontwerp](bouwontwerp.md)). Daarvan zijn er in de sandbox (de machine
waarop gebouwd is) en in CI (de automatische tests op GitHub) alle 27 gehaald die bij 0.8.0 horen,
en ook de grens voor de grootte van het image. In CI waren met de code van 0.8.0 alle 16 jobs groen
(run 105), en ook op de commit van 0.8.0 zelf (`3fca440`, run 106). Het laatste bewijs is je eigen
server: [Op de LXC](#op-de-lxc).

Zoeken gebruikt één index in de database die er al is (SQLite met FTS5, de zoekfunctie van
SQLite). Er komt geen nieuwe database of dienst bij. Zoeken op naam werkt meteen na de update; de
tekst van documenten, de plek en de hash van foto's (een vingerafdruk van het beeld, voor
duplicaten) en de plaatsnamen komen op de achtergrond. Dat volg je op `/beheer` → **Zoeken en
foto's**. Tekst lezen gebeurt in de afgeschermde mediaworker van fase 5 als media aan staat;
anders in de app zelf, buiten die afscherming (`/beheer` zegt welke). OCR (tekst herkennen in een
beeld) draait alleen in de mediaworker, dus alleen met media aan. Het image wordt alleen ±72 MB
groter, voor tesseract, het programma dat de tekst herkent.

Van de beslissingskaarten van fase 6 zijn alleen B53 (OCR) en B57 (personen, gezichten en beeld)
besloten; de rest is voorstel. Vraag 4 en Vraag 8–14 heb je nog niet beantwoord. 0.8.0 draait op
de standaarden; zo ziet alleen de eigenaar de plek van een foto (Vraag 4). Zie
[Vragen voor Jonas](#vragen-voor-jonas).

## Klaar als

Uit het stappenplan: een zoekopdracht over 100.000 bestanden antwoordt binnen 200 ms; OCR-tekst is
doorzoekbaar; gezichten zijn te benoemen en te zoeken. Het ontwerp vult dat aan met de rest van
het doel (§4.0, §4.4 en §4.5 van het [bouwontwerp](bouwontwerp.md)). De getallen komen uit
[metingen.md](metingen.md), met het nummer van de meting (M1–M9). "p95" betekent: 95 van de 100
vragen zijn sneller.

- **Zoeken over 100.000 bestanden binnen 200 ms** (p95), voor elke soort gebruiker: van iemand
  met alleen zijn eigen map tot iemand die 30 hoofdmappen ziet. In de meting was het slechtste
  geval 126,7 ms (M1). Met vier mensen
  tegelijk is de grens 300 ms; gemeten is 209,4 ms. De grens
  van 200 ms wordt in CI nooit ruimer gemaakt of overgeslagen (die regel staat in
  [e2e.md](e2e.md)).
- **OCR-tekst is doorzoekbaar.** Een gescande PDF zonder tekst en een foto van een bon zijn te
  vinden op een woord dat alleen in het beeld staat, en de viewer markeert dat woord (getest in
  de browser, met de echte tesseract). De grens is 8 pagina's per minuut op één core. Tijdens de
  bouw haalde CI 20,5 per minuut ([x1.md](x1.md), "Klaar als"); bij de integratie
  23,2 per minuut over de mix van brieven en volle pagina's (M5).
- **Personen zijn te benoemen en te zoeken**, in 0.8.0 met de hand: je zet een persoon op een
  foto, zo nodig met een kader, en vindt hem terug met zoeken en met het filter Persoon. Gezichten
  automatisch herkennen hoort bij 0.8.1.
- **De tekst uit documenten** (PDF, docx, xlsx, pptx, txt, md) is doorzoekbaar. Het lezen op de
  achtergrond moet minstens 1.000 documenten per minuut halen op één core; gemeten
  1.977 per minuut (M2).
- **Foto-gegevens, de kaart en plaatsnamen zonder internet.** GPS, camera en hash lezen moet
  minstens 5.000 foto's per minuut halen (gemeten 13.713, M3), land en plaats
  opzoeken minstens 20.000 per minuut (gemeten 44.495, M4). De kaart antwoordt
  binnen 150 ms (gemeten 51,9 ms, M1) en vraagt niets aan een andere
  server. Na de update heeft elke foto met een GPS-plek op land een land, en een plaats als er
  een binnen 30 km ligt: gehaald in M9, met 248 foto's bij echte plaatsen, over de wereld, op
  grenzen, op zee en op 180°.
- **Duplicaten en bijna-duplicaten.** `/dubbel` antwoordt bij 55.000 foto's binnen 300 ms;
  gemeten 192,7 ms (M1).
- **Herinneringen en slimme albums.** De herinneringen komen binnen 50 ms; gemeten
  27,0 ms (M1). De slimme albums zijn getest, ook het delen van
  nieuwe foto's uit een regel.
- **Filters** op soort, datum, persoon, plek, tag en bibliotheek: elk filter en elke combinatie van
  twee is getest tegen een eenvoudige referentie, met en zonder de index.
- **Alles met het toetsenbord**, zoals sinds fase 3 (de lijsten `toetsenbord-z*.md` en
  [toetsenbord-x1.md](toetsenbord-x1.md)).
- **Naast media en OnlyOffice.** Met films die spelen en mensen die typen in OnlyOffice, terwijl het
  achtergrondwerk vol staat, blijft zoeken binnen 200 ms, hapert geen film en gaat er geen woord
  verloren. De uitkomst per regel staat in [metingen.md](metingen.md) (M7); samengevat: in de vijfde
  meetronde haalde de stap met de poort elke regel, drie van de drie keer: zoeken hoogstens 54 ms,
  geen film haperde, opslaan in OnlyOffice even snel als zonder de drukte, en geen woord verloren.
  In de stap zonder poort, waar het achtergrondwerk niet wijkt voor media, haperde een film in twee
  van de drie runs één keer, vlak na verspringen. Er was nooit meer dan ±3,6 GiB van de 15,7 GiB
  geheugen in gebruik. De vier rondes ervoor haalden het niet, drie vooral door fouten in de
  meetopzet en één op haperen na verspringen (zie daar de Bevindingen).
- **Een upload wacht hooguit 1 s**, ook als de index tegelijk een tekst van 1 MB met 10 kopieën
  schrijft. De langste wachttijd was 337 ms (M6).

Per punt waar het bewezen is (in de sandbox, in CI of op de LXC): [integratie.md](integratie.md).

## Wat er is

- **Zoeken in de inhoud van bestanden.** Het zoekveld bovenaan (Ctrl+K) en de pagina `/zoeken`
  vinden een bestand op zijn naam en op de tekst erin: PDF, Word, Excel en PowerPoint (docx, xlsx,
  pptx), odt, ods en odp, txt, md, csv, html en rtf, en de titel, artiest en album van muziek en
  video. Bij elke
  treffer staat een stukje tekst rond het woord; in een PDF markeert de viewer het woord. Filters
  zet je als chips: soort, datum, tag, persoon, plaats, land, camera, map en mediabibliotheek. Je
  ziet alleen wat je mag zien. Er staat geen score en geen totaal bij, zodat een uitkomst niets
  zegt over bestanden van anderen ([z1.md](z1.md), [z2.md](z2.md), [z4.md](z4.md)).
- **Tekst in scans en foto's (OCR).** Een gescande PDF wordt vanzelf gelezen zolang OCR aan staat
  op `/beheer` (de standaard). Een foto alleen als de eigenaar dat wil: voor al zijn foto's met
  **Tekst in foto's herkennen** op `/account` bij Foto's (standaard uit), of per foto met de knop
  **Tekst herkennen** in het infopaneel. De tekst van een foto vindt alleen de eigenaar, ook als
  hij de foto deelt. OCR draait alleen met media aan ([x1.md](x1.md)).
- **De kaart, plaatsen en landen.** `/fotos/kaart` toont je foto's met een GPS-plek op een kaart
  met alleen landsgrenzen. Er gaat niets naar een kaartdienst. Elke foto met een GPS-plek op land
  krijgt een land, en ook een plaats als er binnen 30 km een ligt in hetzelfde land. Dat komt uit de
  plaatsnamen die `install.sh` ophaalt (GeoNames en Natural Earth), opgezocht op je eigen server.
  Op zee krijgt een foto geen land en geen plaats. Foto's die er al waren, krijgen die vanzelf na
  de bouw van de plaatsnamen. Met de lijst **Plaatsen** bij de kaart, en **Op de kaart** in het
  infopaneel ([z3.md](z3.md), [z5.md](z5.md)).
- **Dubbele bestanden** op `/dubbel`: bestanden die precies gelijk zijn, en foto's die erop
  lijken. Er staat nooit iets vooraf aangevinkt; wat je kiest, gaat naar de prullenbak
  ([z3.md](z3.md), [z5.md](z5.md)).
- **Personen, met de hand.** In het infopaneel van een foto (toets I) zet je een persoon op de
  foto, als je wilt met een kader (**Kader tekenen**). Op `/fotos/personen` staan je personen; per
  persoon kun je hernoemen, samenvoegen, verbergen of wissen. Zoeken en het filter **Persoon**
  vinden ze terug. Personen zijn van de eigenaar en gaan nooit mee via een publieke link
  ([z6.md](z6.md)).
- **Herinneringen.** Op `/fotos`: "Vandaag, N jaar geleden". Een dag verbergen kan (**Deze dag
  verbergen**), en een persoon uit je herinneringen halen ook. Uit te zetten op `/account` bij
  Foto's ([z6.md](z6.md)).
- **Slimme albums.** Een album met een **Regel** (een naam of tekst, en filters) vult zich vanzelf
  met de foto's die passen. Wat je er zelf in zet, blijft staan; wat je eruit haalt, komt niet
  terug. Of nieuwe foto's uit de regel ook gedeeld worden, kies je zelf ([z6.md](z6.md)).
- **Filters op `/fotos`:** persoon, soort (foto's of video's), vanaf en tot en met een datum,
  land, plaats en camera ([z6.md](z6.md)).
- **De waarschuwing bij delen met een plek.** Deel je een foto, map of album met foto's die een
  locatie in het bestand hebben, dan zegt het deeldialoog dat wie de foto downloadt, de plek
  meekrijgt ([z5.md](z5.md)).
- **`/beheer` → Zoeken en foto's** (Alt+9, alleen voor de beheerder): de zoekmachine, het
  achtergrondwerk pauzeren en hervatten, hoe ver de index is, **Index herbouwen**, **Mislukte
  opnieuw**, de plek en de duplicaten opnieuw berekenen, OCR aan en uit, de plaatsnamen en de
  Zoekbench (de snelheid van zoeken op jouw CPU). Wat je op de server doet, staat in het runbook
  [zoeken.md](../runbooks/zoeken.md) ([z7.md](z7.md)).
- **De wekelijkse controle.** Eens per week vergelijkt de app 200 willekeurige bestanden in de
  index met de database. Een verschil staat in oranje op `/beheer`, met wat je dan doet; daarna
  kijkt de app elk uur opnieuw. Herstellen blijft met de hand (het runbook, "De wekelijkse
  controle").

## Wat komt in 0.8.1

- **Gezichten automatisch herkennen.** Het platform stelt voor ("Is dit Eva?"); niets wordt
  vanzelf bevestigd. Per gebruiker staat het standaard uit. Het rekenen gebeurt in een eigen
  afgeschermde container, `analyse`, zonder netwerk en zonder je bestanden. Daarvoor komen eerst
  die container en de migraties 0011 en 0012 (een nieuwe stand van de tabellen).
- **Zoeken op wat er op een foto staat** ("strand", "hond"). Dit staat ook gepland voor 0.8.1,
  maar het kan naar een aparte fase 6b schuiven als het uitloopt (zie [Planning](#planning)). Per
  gebruiker standaard uit (Vraag 11).
- Met de gezichten komen ook de pagina `/over` met de bronvermeldingen, en de reparatie van het
  kader van een persoon bij een nieuwe versie van een foto (migratie 0011; zie
  [Bekende grenzen](#bekende-grenzen)).

De knop **Zoeken op beeld** op `/fotos` en de schakelaars **Gezichten herkennen** en **Zoeken op
beeld** op `/account` zijn er al, maar werken nog niet. Zoeken op beeld zegt dat het in een
volgende versie komt; bij Gezichten herkennen staat "Nog niet beschikbaar op deze server". Beide
schakelaars gaan nog niet aan.

## Wat er verandert sinds 0.7.x

- **Bijwerken maakt eerst een reservekopie van de database.** Mislukt die, dan stopt het script
  vóór de app of de database verandert. Aan het eind zegt het waar de kopie staat.
- **De database gaat naar migratie 0010.** Terug naar 0.7.1 kan, alleen met de stappen van het
  runbook ("Terug naar 0.7.1": commit `80d5bb8`, eerst `db-downgrade 0009`). Wat fase 6
  toevoegde, is daarna weg.
- **Het image wordt ±72 MB groter**, voor tesseract.
- **Het script haalt plaatsnamen op** (±16 MB). Lukt dat niet, dan gaat het gewoon door. Uitzetten
  kan met `PLAATSEN=uit`.
- **Na de update werkt zoeken op naam meteen.** De tekst, de plekken en de hashes vullen zich op
  de achtergrond. Voor 100.000 bestanden op één core is dat geschat: tekst ±10–15 minuten, de
  index ±30 seconden, GPS, camera en hash ±2,5–5 minuten, land en plaats ±1 minuut (het runbook,
  "De stand lezen").
- **Zoeken werkt anders dan in 0.7.x:**
  - de beste treffer staat eerst; zonder zoekwoord de nieuwste eerst;
  - woorden worden gesplitst: "foto-0001" zoekt "foto" en "0001", en vindt dus ook foto-00001;
  - 30 treffers per keer, met **Meer laden** (tot 1.000);
  - archieven vallen onder de soort **Overig**.
- **Een album kan een regel krijgen.** In 0.7.1 deelt een album elk item. Daarom vraagt de weg
  terug eerst wat er moet gebeuren met items uit een regel die niet gedeeld worden.
- **Nieuw op `/account` bij Foto's:** de plek in je foto's gebruiken (standaard aan, alleen voor jou),
  herinneringen (standaard aan) en tekst in foto's herkennen (standaard uit).
- **Een nieuwe tab op `/beheer`:** Zoeken en foto's (Alt+9).
- **Wie de API zelf aanroept:** een zoekvraag zoals in 0.7.0 (`type=archive`, of `limit` boven 50)
  geeft nu een foutmelding (422) in plaats van een ander antwoord.

## Bekende grenzen

| Wat | De grens | Waar het staat |
|---|---|---|
| Zoeken zonder index | Tijdens **Index herbouwen**, en op PostgreSQL, zoekt het platform alleen op naam, zoals 0.7.0 (met LIKE). Het werkt, maar langzamer; `/beheer` zegt het. PostgreSQL is niet getest. | het runbook, "De index herbouwen"; [z0-api.md](z0-api.md) |
| Een vraag die te lang duurt | Na ±120 ms geeft zoeken de eerste treffers, niet per se de beste, met een melding. Dat kan bij iemand met meer dan 64 gedeelde mappen, en bij alleen een datumfilter op foto's; dan kan die pagina ook leeg zijn, met de melding "Niets gevonden binnen de tijd". | [z1.md](z1.md), Open |
| De volgorde | Zoeken telt woorden over alle gebruikers. De volgorde van bijna gelijke treffers zegt daardoor een heel klein beetje over bestanden van anderen. Er is geen score en geen totaal. | risico R7 in het [bouwontwerp](bouwontwerp.md) |
| De kaart met een filter op soort, map, camera, persoon of tag | tot een halve seconde voor de hele wereld (gemeten in de sandbox 0,31–0,34 s en in CI 0,43 s, eigen grens 0,5 s); zonder zo'n filter binnen 0,15 s. Sneller kan alleen met een nieuwe migratie. | [z3.md](z3.md), Open; een vraag hieronder |
| Land en Plaats op `/fotos` | hooguit 500 plaatsen, de meeste foto's eerst: wie meer plaatsen heeft, mist de kleinste en soms een land. Camera is een tekstveld. | [z6.md](z6.md), Open |
| "Niet gelijk" op `/dubbel` | kan niet ongedaan: die groep komt niet terug. | [z5.md](z5.md), Open |
| Herinneringen per map verbergen | kan niet in de web-app; wel een dag, of een persoon. | [z6.md](z6.md), Open |
| Het kader van een persoon | blijft staan als een foto een nieuwe versie krijgt, ook als het beeld anders is. Dat wordt rechtgezet met migratie 0011 (0.8.1). | [z0b.md](z0b.md) |
| OCR | Een foto met precies dezelfde inhoud als een bestand dat er al was, krijgt zijn tekst pas bij de volgende start. "Tekst in foto's herkennen" uit wist de tekst die er is niet (een vraag hieronder). Van een PDF leest OCR hooguit 200 gescande pagina's. Met de knop **Tekst herkennen**: 20 keer per uur en 200 pagina's per dag per gebruiker. Een volle, liggende scan kost ±6 pagina's per minuut, ±160 minuten per 1.000 pagina's. | [x1.md](x1.md), Open |
| Uploaden terwijl de index schrijft | De upload wacht hooguit ±1 s op een schijf waar het wegschrijven (`fsync`) tot ±200 ms extra kost. Op een tragere schijf langer. | [z2.md](z2.md); `api/app/dbgate.py` |
| De wekelijkse controle | loopt alleen met de index (FTS5), niet met LIKE of op PostgreSQL. | het runbook, "De wekelijkse controle" |
| De bronvermelding | GeoNames en Natural Earth staan onder de kaart en in de [README](../../README.md) (Bronnen). De pagina `/over` is er nog niet. | [z3.md](z3.md), Open |
| Jouw CPU | De 200 ms is gemeten in de sandbox en in CI. De Zoekbench op `/beheer` → Zoeken en foto's toont de p95 op jouw CPU, voor "eigenaar" en "alles"; ligt een van beide boven 200 ms, dan haalt jouw CPU het niet. `/beheer` waarschuwt daar niet zelf voor. Meten doe je met `bench --los`. | het runbook, "Zoeken meten"; §4.4 van het [bouwontwerp](bouwontwerp.md), "Op de LXC" |

## Wat je koos

Op 3 november 2026 zei je "ja voeg alles toe", en daarna koos je op een kaartje "Ook gezichten".
Dat betekent:

- **OCR: ja.** Scans en foto's met tekst worden doorzoekbaar. Daarvoor komt tesseract in het
  image. Dat bleek ±72 MB, niet ±20 MB. Op 4 november 2026 koos je op een kaartje "Ja, erin"
  (Vraag 15).
- **Gezichten automatisch: ja**, in fase 6 (gepland voor 0.8.1). Per gebruiker staat het
  standaard uit.
- **Zoeken op wat er op een foto staat** ("strand", "hond"): ja, in fase 6 (gepland voor 0.8.1;
  het kan naar een fase 6b schuiven). Dat volgt uit "alles"; het kaartje noemde het niet apart.
  Daarom staat het per gebruiker standaard uit (Vraag 11).
- **De kaart: geen tegels van buiten.** Alleen landsgrenzen. Er gaat niets naar een kaartdienst.
- **Plaatsnamen en landsgrenzen downloaden bij de installatie** (de oude Vraag 5 en 6): de kaartjes
  zijn ingetrokken, omdat "alles" ze dekt. De standaard geldt.
- **Wie ziet de plek van een foto** (Vraag 4): die heb je **niet** beantwoord. Tot je dat doet,
  geldt de standaard: alleen de eigenaar. Dat is geen keuze van jou.

## Doel

Alles terugvinden, en meer halen uit je foto's. Uit het stappenplan:

- zoeken in de inhoud van pdf, docx, xlsx, pptx, txt en md;
- OCR voor scans en foto's met tekst;
- EXIF en GPS (datum, camera, plek), een kaart en plaatsnamen zonder internet;
- duplicaten en bijna-duplicaten;
- gezichten en zoeken op wat er op een foto staat, met lokale modellen;
- herinneringen ("vandaag, N jaar geleden") en slimme albums;
- filters op type, datum, persoon, plek, tag en bibliotheek.

Wat daarvan in 0.8.0 zit en wat er gemeten is: [Klaar als](#klaar-als).

## Hoe het werkt, in het kort

- Eén zoekindex in de SQLite die we al hebben (FTS5). Geen nieuwe database. Zoeken op naam werkt
  meteen na de update; de inhoud komt op de achtergrond.
- Je ziet alleen wat je mag zien. Geen score en geen totaal, zodat een uitkomst niets zegt over
  bestanden van anderen.
- Tekst lezen gebeurt in de afgeschermde mediaworker van fase 5 als media aan staat, anders in de
  app zelf (`/beheer` zegt welke). OCR draait alleen in de mediaworker, dus alleen met media aan.
- **Gezichten** (vanaf 0.8.1) **en zoeken op beeld** (gepland voor 0.8.1; het kan naar een fase 6b
  schuiven) rekenen in een eigen container, `analyse`. Die heeft geen netwerk en kan niet bij je
  bestanden: de app geeft hem alleen een klein voorbeeld van een foto. numpy en onnxruntime staan alleen daar; de app zelf gebruikt ze nooit.
- **Gezichten** (vanaf 0.8.1) geven voorstellen ("Is dit Eva?"). Niets wordt vanzelf bevestigd.
  De getallen die het platform van elk gezicht maakt (de vectoren), zijn versleuteld, met een
  sleutel per gebruiker. Zet iemand gezichten uit, dan wist het platform eerst die sleutel en dan
  de gezichten. Oude kopieën van die getallen zijn dan onleesbaar, in elke kopie waar de sleutel
  niet in zit (zie "Privacy en de wet").
- Een foto die iemand anders in jouw map zette, wordt niet gescand, tenzij die ander het zelf aan
  heeft. Een upload via een publieke link nooit (Vraag 9).
- Plek, personen en tekst uit een foto horen bij de eigenaar van het bestand. Nooit via een
  publieke link.
- Bijwerken met hetzelfde `install.sh`, met eerst een reservekopie van de database. Terug naar
  0.7.1 (commit `80d5bb8`, eerst `db-downgrade 0009`) met de stappen van het runbook
  [zoeken.md](../runbooks/zoeken.md), "Terug naar 0.7.1".

## Wat het kost

In 0.8.0 (gemeten, of geschat waar dat erbij staat):

| Wat | Hoeveel |
|---|---|
| het image | ±72 MB extra voor tesseract met Nederlands, Engels en de stand van de pagina (OCR): 69 MiB (±72 MB) geïnstalleerd, gemeten in de spike ([spike.md](spike.md), S5). CI laat het image hooguit 75 MB groeien. In CI groeide het image met 71,6 MB (M8). |
| de plaatsnamen | ±16 MB download bij het bijwerken (het runbook, "Plaatsnamen") |
| de database | ±370 MB per 100.000 bestanden (geschat; risico R8 in het [bouwontwerp](bouwontwerp.md)) |
| eenmalig rekenen, 100.000 bestanden | op één core, geschat: tekst ±10–15 minuten, de index ±30 seconden, GPS, camera en hash ±2,5–5 minuten, land en plaats ±1 minuut (het runbook, "De stand lezen") |
| OCR | 20,5 pagina's per minuut op één core voor de mix van de poort in CI (grens 8); een volle, liggende scan ±6 per minuut ([x1.md](x1.md)) |
| zoeken | binnen 200 ms (p95) |

In 0.8.1 komt erbij, met gezichten en zoeken op beeld (gemeten in de sandbox, of berekend uit die
metingen; [haalbaarheid.md](haalbaarheid.md)). Zoeken op beeld kan naar een fase 6b schuiven; dan
komt dat deel later:

| Wat | Hoeveel |
|---|---|
| het image | +118–141 MB voor numpy en onnxruntime, ook als je `analyse` uitzet |
| de modellen | ±39 MB voor gezichten en ±0,4 GB voor beeld, in `/opt/platform/modellen`; bij de installatie tijdelijk 1,55 GB extra |
| de database | ±0,3 GB per 100.000 foto's, voor gezichten en beeld samen; plus ±0,15 GB cache voor zoeken op beeld |
| eenmalig rekenen, 55.000 foto's | gezichten ±2,2–2,5 uur, beeld ±1,1–1,2 uur, op de achtergrond op één core |
| geheugen | de container `analyse` hooguit 3 GB; met media en OnlyOffice samen 8 van de 16 GB |
| zoeken op beeld | binnen 250 ms (Vraag 14) |

Je container heeft een rootdisk van 500 GB; dat past ruim. Een tragere CPU dan die van de sandbox rekent langer. `/beheer`
laat zien hoe ver het is.

## Planning

±43 werkdagen (≈ 9 weken), in twee delen (Vraag 13):

- **0.8.0 rond dag 27**: zoeken, tekst, OCR, foto-gegevens, de kaart, duplicaten, personen,
  herinneringen en slimme albums.
- **0.8.1 rond dag 43**: gezichten en zoeken op beeld.

Kies je één oplevering, dan is het ±41 werkdagen. Het stappenplan gaf 3 weken; dat was zonder
gezichten en beeld, en zonder review per golf. Loopt het uit, dan schuift eerst zoeken met woorden
op beeld, dan beeld helemaal (naar een fase 6b), dan de groepen "Wie is dit?" van gezichten. Geen
privacyregel schuift.

## Privacy en de wet

- **Gezichten zijn biometrische gegevens** (vanaf 0.8.1), ook van mensen op je foto's die zelf
  niets kozen. Voor gebruik in je eigen gezin geldt waarschijnlijk de uitzondering voor
  huishoudelijk gebruik in de AVG. Gebruik je het platform voor anderen (een vereniging, klanten),
  dan gaat het om bijzondere persoonsgegevens (artikel 9). Dan heb je uitdrukkelijke toestemming
  nodig van wie op de foto's staat. Dit is niet door een jurist nagekeken.
- **De sleutels van gezichten** (vanaf 0.8.1) staan in `/opt/platform/sleutels`, buiten de map met
  je data. In jouw container staat alles op één schijf, dus een snapshot of backup van de hele
  container bevat ook de sleutels. In zo'n kopie blijven oude gezichten leesbaar tot je die kopie
  weggooit. Het runbook zegt hoe je de sleutels eruit houdt, bijvoorbeeld met een eigen mount point
  zonder backup.
- **Licenties**: de modellen voor gezichten en beeld (YuNet: MIT; SFace en SigLIP 2: Apache-2.0)
  mogen privé en commercieel gebruikt worden. Waar ze mee getraind zijn, is niet nagekeken.

## Vragen voor Jonas

Elke vraag heeft een standaard. Tot je antwoordt, bouwen we daarop door; niemand hoeft te
wachten.

4. **Wie ziet de plek van een foto?** Standaard: **alleen de eigenaar**, en het deeldialoog
   waarschuwt dat een download de plek meeneemt. Anders: GPS uit downloads halen (±2 dagen), of
   "wie ik deel, ziet de plek ook".
8. **Mogen echte gezichten van NASA in CI gebruikt worden om de herkenning te meten?** Dat is
   biometrie van echte mensen, bij naam, op servers van GitHub in de VS. Standaard: **nee**; dan
   meet je de kwaliteit op je eigen server, en verlaat niets je huis.
9. **Worden foto's gescand die een ander in jouw map zette?** Standaard: **nee, tenzij die ander
   het zelf aan heeft**; een upload via een link nooit.
10. **Hoe komt het model voor zoeken op beeld op je server?** Standaard: **`install.sh` downloadt
    het origineel van Google (via huggingface.co) en zet het één keer om op je server**, zonder
    nieuw pakket.
11. **Staat zoeken op beeld standaard aan?** Standaard: **uit**, tot gemeten is of "Meer zoals
    deze" op een portret dezelfde persoon terugvindt. Wie het wil, zet het aan.
12. **Wat mag CI gebruiken om zoeken op beeld na te rekenen?** Standaard: **ja** voor een vaste set
    van 120 foto's uit het publiek domein (alleen in CI), **nee** voor de officiële bibliotheken
    als meetlat.
13. **Leveren we in twee delen op?** Standaard: **ja**: de kern rond dag 27, gezichten en beeld
    rond dag 43.
14. **Mag zoeken op beeld 250 ms duren?** Standaard: **ja**; gewoon zoeken houdt zijn 200 ms.

Vraag 1, 2, 3, 5, 6, 7 en 15 heb je beantwoord, of ze zijn ingetrokken (zie "Wat je koos"). De vragen
staan uitgebreid in het [bouwontwerp](bouwontwerp.md) onder "Vragen voor Jonas".

Nieuw bij 0.8.0, ook elk met een standaard:

- **Een tag voor 0.8.0 en 0.7.1?** Een tag is een vast label op een commit, zoals `v0.8.0`. Er is
  er nu geen voor 0.7.1 of 0.8.0; de weg terug gebruikt de volledige commit, zoals bij eerdere
  versies. Standaard: **geen tags**. Zeg je ja, dan komt `v0.8.0` op de commit van 0.8.0 en
  `v0.7.1` op `80d5bb8`.
- **Waar staat 0.8.0?** Standaard: **op de branch** `claude/stappenplan-uitwerken-lu4uv3`, en je
  werkt bij met `PLATFORM_BRANCH`, zoals in de [README](../../README.md). Anders gaat het naar
  `main`, en dan is het weer het gewone commando.
- **Moet "Tekst in foto's herkennen" uit ook de tekst wissen?** Nu blijft de tekst die er al is;
  er komen alleen geen nieuwe foto's meer bij. Standaard: **niet wissen** ([x1.md](x1.md), Open 1).
- **Duplicaten: eerst de groepen "Bijna zeker gelijk" vormen, en dan pas "Lijkt erop"?** Nu is een
  groep zo zeker als zijn zwakste paar. Lijkt één foto in een groep maar een beetje, dan staat de
  hele groep onder "Lijkt erop", ook als de andere onderling bijna zeker gelijk zijn. Standaard:
  **zo laten** ([z3.md](z3.md), Open).
- **Is een halve seconde goed genoeg voor de kaart met een filter op soort, map, camera, persoon of
  tag?** Gemeten in de sandbox 0,31–0,34 s en in CI 0,43 s (M1) voor de hele wereld; zonder zo'n
  filter binnen 0,15 s. Sneller kan alleen met een nieuwe migratie, na die van 0.8.1. Standaard:
  **ja**, met een eigen grens van 0,5 s ([z3.md](z3.md), Open).
- **Voorrang voor een video die na verspringen opnieuw moet starten?** Springt iemand voorbij wat
  al klaar is, dan start het omzetten opnieuw met minder dan één stukje voorraad, terwijl een
  andere video op volle kracht doorwerkt; dan kan het beeld kort haperen (fase 5, Bevinding 3).
  Standaard: **ja, na elke herstart** (verspringen, een nieuwe poging na een fout, verder na een
  pauze): die video krijgt voorrang tot 10 s beeld klaar is, hooguit 20 s, en andere kijkers
  houden zolang 10 s voorsprong in plaats van 60 s. Zo zit het in 0.8.0. Anders: alleen na
  verspringen, alleen de eerste twee stukjes, of niets veranderen
  ([beslissingen.md](../fase-0/beslissingen.md), B71).
- **Wat telt de meting van media als haperen, vlak na verspringen?** Landt de meetspeler na een
  sprong in de laatste fractie van een stukje van 2 s, en moet het volgende stukje van een herstart
  komen, dan telt hij 0,2 s later één keer haperen, omdat geen herstart dat stukje zo snel maakt,
  hoe rustig de server ook is. Zo landt ±1 op 4 sprongen; een browser telt het niet. Voorstel:
  **wachten op voorraad** (na een sprong speelt de meetspeler pas verder als er een heel stukje
  klaarstaat; dat wachten telt als springtijd, met een harde grens van 3 s). Anders: tellen zoals de
  browser, of zo laten. Tot je kiest, telt de meting zoals nu ([metingen.md](metingen.md),
  Bevinding 3).

## Op de LXC

Na het bijwerken naar 0.8.0. De commando's staan in het runbook
[zoeken.md](../runbooks/zoeken.md); de uitkomst gaat in [metingen.md](metingen.md), onder "Wat
alleen de LXC of CI kan laten zien".

- [ ] **Bijwerken:** met het commando uit de [README](../../README.md) bovenaan de repository,
  vanaf de branch (met `PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3`). Het script maakt
  eerst een reservekopie van de database en zegt aan het eind waar die staat. Noteer die regel.
- [ ] **`/beheer` → Zoeken en foto's** (Alt+9): **Zoekmachine** zegt `FTS5 (snel)`, **Tekst uit
  documenten lezen** zegt `in de zandbak van de mediaworker`, en **Achtergrondwerk** zegt `loopt`.
- [ ] **De index vult zich:** bij **Index** loopt **Tekst uit bestanden** op tot al je bestanden,
  en **Mislukt** blijft klein. Zijn er mislukte, kies dan **Mislukte opnieuw**. Hoe lang het duurt:
  het runbook, "De stand lezen".
- [ ] **De plaatsnamen zijn gebouwd:** het blok **Plaatsnamen** zegt `Geladen: …, gebouwd op …`.
  Daarna krijgen je foto's met een GPS-plek vanzelf een land, en waar mogelijk een plaats: kies
  op `/fotos` bij **Filters** een **Land**, en open `/fotos/kaart`. Staat er `Niet geladen`, zie het runbook,
  "Plaatsnamen".
- [ ] **Een zoekopdracht:** zoek op telefoon en laptop een woord dat alleen ín een document staat,
  niet in de naam, en open de treffer. Meet daarna de snelheid op jouw CPU met de **Zoekbench**
  (`bench --los`, het runbook "Zoeken meten"): de p95 hoort onder 200 ms.
- [ ] **De OCR-schakelaar:** het blok **OCR** zegt bij Geïnstalleerd `ja` en bij Aan `ja` (de
  standaard). Wil je tekst in je foto's laten herkennen, zet dan op `/account` bij Foto's **Tekst
  in foto's herkennen** aan, en zoek daarna een woord van een foto van een bon of een bord.

## Documenten

- [bouwontwerp.md](bouwontwerp.md): het ontwerp (revisie 3, leidend). Eerdere revisies:
  [bouwontwerp-r2.md](bouwontwerp-r2.md) en [bouwontwerp-r1.md](bouwontwerp-r1.md).
- [haalbaarheid.md](haalbaarheid.md): de metingen in de sandbox, met de commando's: H1–H8, V1–V10,
  de spike B1–B7 voor gezichten en beeld, en de proeven van de kritiek op revisie 3.
- [spike.md](spike.md): de spike van Z0, met een uitkomst per stap.
- Per pakket wat het bouwde, waar het afweek, de review en wat open bleef: [z0.md](z0.md) (met
  [z0-api.md](z0-api.md), [z0-web.md](z0-web.md) en [z0-e2e.md](z0-e2e.md)), [z0b.md](z0b.md)
  (met [z0b-api.md](z0b-api.md) en [z0b-web.md](z0b-web.md)), [z1.md](z1.md) tot en met
  [z7.md](z7.md), en [x1.md](x1.md); het toetsenbord in `toetsenbord-z0.md`, `-z4`, `-z5`,
  `-z6`, `-z7` en [toetsenbord-x1.md](toetsenbord-x1.md).
- [integratie.md](integratie.md): de integratie (I1): de open punten die ze afwerkte of liet
  liggen (met de reden), de wijzigingen aan contracten, de metingen, de standaarden, de
  installatietest met de weg terug naar 0.7.1, en wat alleen CI of de LXC kan bewijzen.
- [metingen.md](metingen.md): de metingen M1–M9 tegen de grenzen van §4.4 en §4.5, met de
  bevindingen en de commando's om opnieuw te meten.
- [e2e.md](e2e.md): de tests van zoeken (de markers `zoek`, `triggers`, `geo`, `zoek_perf` en
  `ocr`), `e2e/slim.sh`, de meetscripts, de jobs in CI, en de regel dat de grens van 200 ms in
  CI nooit ruimer wordt (C17).
- Het runbook [zoeken.md](../runbooks/zoeken.md): wat Jonas op de server doet (wat het script
  doet, de reservekopie, de plaatsnamen, de stand lezen, de wekelijkse controle, de index
  herbouwen, zoeken meten, en terug naar 0.7.1).
- [beslissingen](../fase-0/beslissingen.md): B49–B60 en B61–B70 voor fase 6. B53 en B57 zijn
  besloten; de rest is voorstel.

## Hoe het gebouwd is

Het ontwerp deelde fase 6 in pakketten: eerst het fundament, dan vier golven van twee pakketten
naast elkaar, dan de integratie. Na elke golf kwamen een review en een nacontrole. Elke reparatie
uit een review kreeg een test die zonder haar faalt; de nacontrole haalde elke reparatie weer weg
en keek of die test dan rood werd. De details staan per pakket in zijn eigen bestand.

- **Het fundament** (3 november 2026): Z0, met migratie 0010, de afspraken tussen de delen (de
  contracten) en de spike ([z0.md](z0.md), [spike.md](spike.md)). Migratie 0009 ging in 0.7.1 naar
  de indexen voor de foto's ([fotos-indexeren](../fotos-indexeren.md)); die van fase 6 is daarom
  0010. Daarna Z0b (4 november): de contracten van gezichten en zoeken op beeld, zodat die later
  passen ([z0b.md](z0b.md)).
- **Golf 1** (4 november): de zoekmachine (Z1, [z1.md](z1.md)) en tekst uit bestanden (Z2,
  [z2.md](z2.md)). Na de review halen de grenzen van §4.4 het, ook met 4 tegelijk.
- **Golf 2** (8 november): zoeken in de web-app (Z4, [z4.md](z4.md)), en personen, herinneringen,
  slimme albums en filters (Z6, [z6.md](z6.md)). De review vond vijftien punten, alle gedaan.
- **Golf 3** (9 november): de plek, de plaatsen en de duplicaten (Z3, [z3.md](z3.md)), en de
  kaart, `/dubbel` en de waarschuwing bij delen (Z5, [z5.md](z5.md)). De review vond veertien
  punten: twaalf met een reparatie en een test, twee in de documentatie.
- **Golf 4** (10 november): `/beheer` → Zoeken en foto's, de reservekopie en de plaatsnamen in
  `install.sh`, en de weg terug naar 0.7.1 (Z7, [z7.md](z7.md)); OCR met tesseract (X1,
  [x1.md](x1.md)). De review vond dertien punten (twaalf verschillende); ze klopten alle. Na golf
  4 was CI groen op `16ccb8d`: alle 16 jobs (run 42827893297, [x1.md](x1.md)).
- **De integratie** (I1, [integratie.md](integratie.md)): de open punten zonder eigenaar (zoals de
  wekelijkse controle, de plaatsen voor foto's van vóór de plaatsnamen, en Land, Plaats en Camera
  op `/fotos`), een rij voor het schrijven naar de database (zodat een upload niet achter het
  achtergrondwerk wacht), de metingen ([metingen.md](metingen.md)) en de tests ([e2e.md](e2e.md)).
- **A0** (12 november): het fundament van gezichten en zoeken op beeld, migratie 0011 en 0012 en
  de container `analyse` als contract ([a0.md](a0.md)). Zolang de analyse uit staat, doet het
  platform wat 0.8.0 doet, op één ding na: krijgt een foto nieuwe inhoud, dan gaat het kader van
  een persoon erop weg (Keuze 25a).
- **Nog niet:** X2 (gezichten automatisch) en X3 (zoeken op beeld). Die horen bij 0.8.1; zoeken op
  beeld kan naar een fase 6b schuiven. Zie [Wat komt in 0.8.1](#wat-komt-in-081).
