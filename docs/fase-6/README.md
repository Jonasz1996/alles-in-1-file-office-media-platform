# Fase 6 — Zoeken en foto's

Status: **gebouwd, versie 0.8.2 (23 november 2026)**. Fase 6 staat op de
branch `claude/stappenplan-uitwerken-lu4uv3`. De kern kwam met 0.8.0 (11 november 2026): zoeken in
de inhoud van je bestanden, tekst in scans en foto's, de kaart met plaatsen en landen, dubbele
bestanden, personen, herinneringen en slimme albums. 0.8.1 maakt de fase af met gezichten
automatisch herkennen en zoeken op wat er op een foto staat, allebei per gebruiker aan te zetten,
en de pagina `/over` met de bronnen ([Wat 0.8.1 brengt](#wat-081-brengt)). 0.8.2 brengt alles
naar PostgreSQL, voor het cluster van ClusterForge ([Wat 0.8.2 brengt](#wat-082-brengt)).

Het ontwerp stelt grenzen aan de snelheid en het geheugen (§4.4 en §4.5 van het
[bouwontwerp](bouwontwerp.md)). Daarvan zijn er in de sandbox (de machine waarop gebouwd is) en in
CI (de automatische tests op GitHub) alle 27 gehaald die bij 0.8.0 horen, en ook de grens voor de
grootte van het image. In CI waren met de code van 0.8.0 alle 16 jobs groen (run 105), en ook op
de commit van 0.8.0 zelf (`3fca440`, run 106). Voor 0.8.1 waren op `e535332`, de code van golf 6,
alle 19 jobs groen (run 43892193604). Op de commit van 0.8.1 zelf: alle 19 jobs groen (run 43981579405 op `ae163ac`). Wat de integratie van 0.8.1 naliep en mat, staat in
[integratie-2.md](integratie-2.md). Het laatste bewijs is je eigen server:
[Op de LXC](#op-de-lxc).

Zoeken gebruikt één index in de database die er al is (SQLite met FTS5, de zoekfunctie van
SQLite). Er komt geen nieuwe database of dienst bij. Zoeken op naam werkt meteen na de update; de
tekst van documenten, de plek en de hash van foto's (een vingerafdruk van het beeld, voor
duplicaten) en de plaatsnamen komen op de achtergrond. Dat volg je op `/beheer` → **Zoeken en
foto's**. Tekst lezen gebeurt in de afgeschermde mediaworker van fase 5 als media aan staat;
anders in de app zelf, buiten die afscherming (`/beheer` zegt welke). OCR (tekst herkennen in een
beeld) draait alleen in de mediaworker, dus alleen met media aan. Het image wordt alleen ±72 MB
groter, voor tesseract, het programma dat de tekst herkent.

Gezichten en zoeken op beeld rekenen in een eigen afgeschermde container, `analyse`: zonder
netwerk en zonder je bestanden. Sinds 0.8.1 zet het installatiescript die standaard aan en haalt
het de modellen één keer op: ±39 MB voor gezichten, en ±1,55 GB voor zoeken op beeld, dat het op je
server omzet naar een model van ±400 MB. Met `ANALYSE=uit` blijft alles uit, met `BEELD=uit` alleen
zoeken op beeld. Is er weinig ruimte voor de gezichten, of lukt een download niet, dan zegt het
script `Let op: …` en gaat het door. Per gebruiker staat alles uit, tot die het zelf aanzet bij
Account → Foto's. Het image is ±118–141 MB groter, voor numpy en
onnxruntime, ook met de analyse uit.

Alle keuzes van fase 6 zijn besloten. B53 (OCR) en B57 (personen, gezichten en beeld) koos je op
3 november 2026, en op 16 november 2026 koos je zoeken op beeld toch uit te brengen (Keuze 85). Op
17 november 2026 zette je de rest op de standaard ("Standaard vastzetten"): de andere kaarten
B49–B71, Vraag 4 en Vraag 8–14, en de vragen bij 0.8.0 en 0.8.1. Hun status is
**besloten: standaard, Jonas 2026-11-17**. 0.8.1 draaide al op die standaarden, dus er verandert
niets: alleen de eigenaar ziet de plek van een foto (Vraag 4), en zoeken op beeld staat per
gebruiker uit (Vraag 11). Zie [Vragen voor Jonas](#vragen-voor-jonas).

Ook op 17 november 2026 kwam PR #2 op de branch (samengevoegd, `aa6fa8b`): `app.cli db-copy`
kopieert de SQLite van één node naar PostgreSQL voor het cluster, de tests kunnen op PostgreSQL
draaien met `PLATFORM_TEST_DATABASE_URL`, en CI heeft de job `api-postgres`. Voor wie alleen
SQLite gebruikt, verandert er niets.

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
- **Personen zijn te benoemen en te zoeken.** Met de hand sinds 0.8.0: je zet een persoon op een
  foto, zo nodig met een kader, en vindt hem terug met zoeken en met het filter Persoon. Sinds
  0.8.1 stelt het platform ook zelf voor wie er op een foto staat, voor wie gezichten aanzet;
  niets wordt vanzelf bevestigd. De scan haalde in de sandbox 1.043 foto's per minuut per core met
  één gezicht (grens 250, op `9228eb6`; [x2b.md](x2b.md)). Hoe goed de voorstellen zijn, is niet
  gemeten (zie [Bekende grenzen](#bekende-grenzen)).
- **Zoeken op beeld** (0.8.1): een zin als "hond op het strand", of **Meer zoals deze** vanuit een
  foto. In CI haalde het model de kwaliteitspoorten van Keuze 85 (op `dc5dfe5`: van de eerste vijf
  foto's bij een Nederlandse vraag klopten er gemiddeld 0,692, poort 0,6; "Meer zoals deze" 0,475,
  ondergrens 0,40; [x3b.md](x3b.md)). Eén foto verwerken voor de beeldindex kostte 33,8 ms op
  één core (grens 120 ms) en het model-deel van een vraag p95 22,2 ms (op `e535332`). Het geheel
  onder belasting, met alles aan: p95 191,8 ms in de sandbox (M13, §4.5 stap 2 met de analyse),
  grens 250 ms; met de gezichtenscan ernaast was het 290,4 ms, in een stap zonder poort
  ([metingen.md](metingen.md), en hieronder bij Bekende grenzen). Die metingen gebruiken modellen
  van de echte vorm met willekeurige gewichten: ze zeggen iets over de tijd, niet over de
  kwaliteit.
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
  meetopzet en één op haperen na verspringen (zie daar de Bevindingen). Met de analyse erbij
  (0.8.1, de container `analyse` die gezichten en beelden rekent; M13): alle 13 poorten gehaald.
  Zoeken p95 53,4 ms, geen film haperde, OnlyOffice sloeg op in 8,3 s (grens 8,9 s) zonder een
  woord te verliezen, zoeken op beeld p95 191,8 ms, en de analyse gebruikte samen 1,51 GB (grens
  2,5 GB). De testfoto's hadden geen gezichten: het herkennen en groeperen zelf zat er niet in.
- **Een upload wacht hooguit 1 s**, ook als de index tegelijk een tekst van 1 MB met 10 kopieën
  schrijft. De langste wachttijd was 337 ms (M6).

Per punt waar het bewezen is (in de sandbox, in CI of op de LXC): [integratie.md](integratie.md);
voor wat er met 0.8.1 bijkwam: [integratie-2.md](integratie-2.md).

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
- **Gezichten** (0.8.1). Wie het aanzet bij Account → Foto's (**Gezichten herkennen in mijn
  foto's**), krijgt op `/fotos/personen` voorstellen ("Is dit Eva?") en groepen gezichten zonder
  naam ("Wie is dit?"); in het infopaneel van een foto staan de gezichten met hun kader. Ja maakt
  er een persoon op de foto van, zoals met de hand. Zet je het uit, dan
  wist het platform de gezichten en de sleutel; personen blijven ([x2b.md](x2b.md),
  [Gezichten in detail](#gezichten-in-detail-vanaf-081)).
- **Zoeken op beeld** (0.8.1). Wie het aanzet bij Account → Foto's (**Zoeken op beeld**), zoekt op
  `/zoeken` (**Zoeken op** → **Wat er op de foto staat**) in zijn eigen foto's op wat erop staat,
  en vindt vanuit een foto **Meer zoals deze** ([x3b.md](x3b.md)).
- **`/over`** (0.8.1): de bronnen van de kaart en de modellen, met hun licentie, voor iedereen die
  is ingelogd, ook met de analyse uit. Via Account → **Bronnen en licenties**.
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
  Zoekbench (de snelheid van zoeken op jouw CPU). Sinds 0.8.1 ook de blokken **Gezichten**
  (**Gezichtsherkenning toestaan**) en **Zoeken op beeld** (**Zet uit voor iedereen**,
  **Beeldindex wissen**, **Opnieuw berekenen**), met hoe ver de scan is. Wat je op de server doet,
  staat in de runbooks [zoeken.md](../runbooks/zoeken.md) ([z7.md](z7.md)),
  [analyse.md](../runbooks/analyse.md) en [gezichten.md](../runbooks/gezichten.md).
- **De wekelijkse controle.** Eens per week vergelijkt de app 200 willekeurige bestanden in de
  index met de database. Een verschil staat in oranje op `/beheer`, met wat je dan doet; daarna
  kijkt de app elk uur opnieuw. Herstellen blijft met de hand (het runbook, "De wekelijkse
  controle").

## Wat 0.8.2 brengt

0.8.2 is de versie voor het cluster (PostgreSQL via Patroni). Op SQLite verandert er niets.

- **Zoeken met een index op PostgreSQL** (migratie 0014). Tot 0.8.1 zocht de app op PostgreSQL
  met LIKE: alleen op naam en traag. Nu heeft PostgreSQL een eigen index (`fts_node`, een
  tsvector per bestand met naam, titels, tekst, OCR en meta, met de gewichten van bm25 op SQLite)
  en zoekt de app ook in de inhoud. De OCR van een foto telt alleen voor de eigenaar (Keuze 16).
  Triggers houden de index bij; `search-check`, `search-rebuild` en `search-triggers` werken nu
  ook op PostgreSQL, net als de wekelijkse controle (die zet een afwijking meteen recht). De
  migratie vult de index meteen: reken op enkele minuten bij een grote bibliotheek.
- **Gezichten en zoeken op beeld op PostgreSQL**, achter `PLATFORM_ANALYSE_POSTGRES=true`. De
  template van ClusterForge zet dat aan zodra Analyse aan staat. De sleutels van gezichten staan
  per node; de app maakt nooit een nieuwe sleutel als er nog versleutelde gezichten zijn (een
  node waar de sleutel nog niet gesynchroniseerd is, wacht). `python -m app.cli sleutels-check`
  zegt of elke eigenaar met versleutelde gezichten een sleutel heeft die ze opent (exitcode 0),
  en `--opruimen` haalt de sleutels weg van wie gezichten uit heeft en er geen meer heeft. Het
  [runbook van het cluster](../runbooks/cluster.md#de-sleutels-van-gezichten) zegt hoe de
  sleutels over de nodes gaan.
- **Een nieuwere vraag stopt de vorige** nu ook op PostgreSQL (`pg_cancel_backend`), zoals
  `interrupt()` op SQLite.
- **Platform terug zonder gegevensverlies**: `MET_DATABASE=downgrade` laat het nieuwe image het
  schema terugzetten (`db-downgrade`), in plaats van de back-up van vóór de update terug te zetten
  ([runbook](../runbooks/cluster.md#terugzetten)).
- **Tests op PostgreSQL** in CI: een sjabloondatabase per testproces, zodat een test niet elke
  keer alle migraties draait, en de tests die echt alleen over SQLite gaan, zijn als zodanig
  gemarkeerd. Hoe lang de job duurt, zegt de eerste run in CI ([i3/w2.md](i3/w2.md)).

Terug naar 0.8.1 op PostgreSQL: eerst `python -m app.cli db-downgrade 0012 --ja` met 0.8.2 (of
Platform terug met `MET_DATABASE=downgrade`). Er gaan geen rijen verloren, maar twee dingen wel:
de zoekindex van 0014 (0.8.1 zoekt weer alleen op naam) en de plaatsen van foto's van 0013 (het
land en de plek op de kaart blijven). Met een nieuwe update naar 0.8.2 komen ze terug.

## Wat 0.8.1 brengt

- **Gezichten automatisch herkennen.** Het platform stelt voor ("Is dit Eva?"); niets wordt
  vanzelf bevestigd. Per gebruiker staat het standaard uit. Het rekenen gebeurt in een eigen
  afgeschermde container, `analyse`, zonder netwerk en zonder je bestanden. De database krijgt
  daarvoor migratie 0011 en 0012 (een nieuwe stand van de tabellen). Hoe het werkt:
  [Gezichten in detail](#gezichten-in-detail-vanaf-081).
- **Zoeken op wat er op een foto staat** ("strand", "hond"), en **Meer zoals deze** vanuit een
  foto. ~~Dit staat ook gepland voor 0.8.1, maar het kan naar een aparte fase 6b schuiven als het
  uitloopt (zie [Planning](#planning)).~~ [16 november 2026: het blijft in 0.8.1. Je koos "Toch
  uitbrengen" (Keuze 85 in het [bouwontwerp](bouwontwerp.md)), ook al vinden een paar woorden
  weinig ('bergen' en 'boot'; 'berg' en 'bootje' wel). De uitleg bij de schakelaar zegt wat je mag
  verwachten.] Per gebruiker standaard uit (Vraag 11, besloten: standaard, Jonas 2026-11-17).
- **De pagina `/over`** (Account → **Bronnen en licenties**): de bronnen van de kaart (GeoNames en
  Natural Earth) en van de modellen (YuNet, SFace en SigLIP 2), met hun licentie, en de pakketten
  van de analyse. Voor iedereen die is ingelogd, ook een gast, en ook met de analyse uit.
- **Het kader van een persoon** gaat weg als een foto een nieuwe versie met ander beeld krijgt; de
  persoon blijft (migratie 0011).

Wat je doet:

1. **Bijwerken** met het commando uit de [README](../../README.md) ("Bijwerken naar 0.8.1"). Het
   script zet de analyse aan, haalt de modellen één keer op (±39 MB voor gezichten, ±1,55 GB voor
   zoeken op beeld, op je server omgezet naar ±400 MB) en start de container. Met `ANALYSE=uit`
   blijft alles uit, met `BEELD=uit` alleen zoeken op beeld; het script onthoudt dat.
2. **Aanzetten, per gebruiker:** Account → Foto's → **Gezichten herkennen in mijn foto's** (eerst
   komt een uitleg) en **Zoeken op beeld**. Iedereen kiest dat zelf.
3. **Volgen en bijsturen** op `/beheer` → **Zoeken en foto's** (Alt+9): hoe ver de scan en de
   beeldindex zijn, en de knoppen voor iedereen (**Gezichtsherkenning toestaan**, **Zet uit voor
   iedereen**, **Beeldindex wissen**, **Opnieuw berekenen**).
4. **De lijst [Op de LXC](#op-de-lxc)**, met wat alleen op je eigen server te meten is.

Met `ANALYSE=uit` zie je niets van gezichten en zoeken op beeld: geen schakelaars, en op
`/beheer` en `/zoeken` alleen dat het op deze server uitstaat en hoe de beheerder het aanzet.
Stond de analyse eerst aan, dan wist uitzetten niets: alles is er weer zodra hij weer aan staat.
Wil je het echt kwijt, wis het dan eerst met de analyse aan (het runbook
[analyse.md](../runbooks/analyse.md), "Uitzetten en weer aan"). Terug naar 0.8.0 (commit
`59e0250`) kan, met de stappen van [infra/single/README.md](../../infra/single/README.md)
("Terug naar 0.8.0 van `59e0250`"); de tabellen van 0.8.1 zijn daarna weg.

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
| Zoeken zonder index | Tijdens **Index herbouwen**, en op PostgreSQL, zoekt het platform alleen op naam, zoals 0.7.0 (met LIKE). Het werkt, maar langzamer; `/beheer` zegt het. PostgreSQL is niet getest. [0.8.2: PostgreSQL heeft een eigen index (migratie 0014); alleen tijdens het herbouwen zoekt het daar op naam, zoals op SQLite.] | het runbook, "De index herbouwen"; [z0-api.md](z0-api.md) |
| Een vraag die te lang duurt | Na ±120 ms geeft zoeken de eerste treffers, niet per se de beste, met een melding. Dat kan bij iemand met meer dan 64 gedeelde mappen, en bij alleen een datumfilter op foto's; dan kan die pagina ook leeg zijn, met de melding "Niets gevonden binnen de tijd". | [z1.md](z1.md), Open |
| De volgorde | Zoeken telt woorden over alle gebruikers. De volgorde van bijna gelijke treffers zegt daardoor een heel klein beetje over bestanden van anderen. Er is geen score en geen totaal. | risico R7 in het [bouwontwerp](bouwontwerp.md) |
| De kaart met een filter op soort, map, camera, persoon of tag | tot een halve seconde voor de hele wereld (gemeten in de sandbox 0,31–0,34 s en in CI 0,43 s, eigen grens 0,5 s); zonder zo'n filter binnen 0,15 s. Sneller kan alleen met een nieuwe migratie. | [z3.md](z3.md), Open; Vragen voor Jonas: 0,5 s is goed (besloten: standaard, Jonas 2026-11-17) |
| Land en Plaats op `/fotos` | hooguit 500 plaatsen, de meeste foto's eerst: wie meer plaatsen heeft, mist de kleinste en soms een land. Camera is een tekstveld. | [z6.md](z6.md), Open |
| "Niet gelijk" op `/dubbel` | kan niet ongedaan: die groep komt niet terug. | [z5.md](z5.md), Open |
| Herinneringen per map verbergen | ~~kan niet in de web-app; wel een dag, of een persoon.~~ [0.8.2: kan, met "Een map verbergen…" boven de herinneringen.] | [z6.md](z6.md), Open; [i3/fase6-rest.md](i3/fase6-rest.md) |
| Het kader van een persoon | ~~blijft staan als een foto een nieuwe versie krijgt, ook als het beeld anders is. Migratie 0011 zet dat recht; die staat al op de branch (A0) en hoort bij 0.8.1.~~ [0.8.1: opgelost met migratie 0011; het kader gaat weg, de persoon blijft.] | [z0b.md](z0b.md), [a0.md](a0.md) |
| OCR | ~~Een foto met precies dezelfde inhoud als een bestand dat er al was, krijgt zijn tekst pas bij de volgende start.~~ [0.8.2: meteen.] "Tekst in foto's herkennen" uit wist de tekst die er is niet (Vragen voor Jonas: niet wissen, besloten: standaard, Jonas 2026-11-17). Van een PDF leest OCR hooguit 200 gescande pagina's. Met de knop **Tekst herkennen**: 20 keer per uur en 200 pagina's per dag per gebruiker. Een volle, liggende scan kost ±6 pagina's per minuut, ±160 minuten per 1.000 pagina's. | [x1.md](x1.md), Open |
| Uploaden terwijl de index schrijft | De upload wacht hooguit ±1 s op een schijf waar het wegschrijven (`fsync`) tot ±200 ms extra kost. Op een tragere schijf langer. | [z2.md](z2.md); `api/app/dbgate.py` |
| De wekelijkse controle | loopt alleen met de index (FTS5), niet met LIKE of op PostgreSQL. [0.8.2: ook op PostgreSQL, met de index van 0014.] | het runbook, "De wekelijkse controle" |
| De bronvermelding | GeoNames en Natural Earth staan onder de kaart en in de [README](../../README.md) (Bronnen). ~~De pagina `/over` is er nog niet.~~ [0.8.1: `/over` is er, via Account → Bronnen en licenties, ook met de analyse uit.] | [z3.md](z3.md), Open |
| Gezichten: de kwaliteit (0.8.1) | Niet gemeten: de tests hebben geen set met echte gezichten (Vraag 8 is nee, besloten: standaard, Jonas 2026-11-17). De drempels (0,45 voor een voorstel, 0,50 voor een groep) zijn voorlopig; `analyse-kwaliteit` meet ze op je LXC tegen je eigen ja en nee, en tot dan zegt `/beheer` "niet gemeten". Twee eisen van het ontwerp hebben geen gereedschap: "op ≥ 95% van de portretten precies het goede gezicht gevonden" en "de gekozen kleurvolgorde ≥ 0,05 hoger dan de andere". Die blijven bij besluit op **niet gemeten, geen gereedschap**. | het runbook [gezichten.md](../runbooks/gezichten.md), "Hoe goed is het"; [x2b.md](x2b.md), Open |
| Zoeken op beeld: zwakke woorden (0.8.1) | 'bergen' en 'boot' vinden weinig in de testset ('berg' en 'bootje' wel); "Meer zoals deze" haalde 0,475, waar het ontwerp 0,7 wilde. Zo uitgebracht met Keuze 85; de uitleg bij de schakelaar zegt het. | [x3b.md](x3b.md); Keuze 85 in het [bouwontwerp](bouwontwerp.md) |
| Gezichten en zoeken op beeld op jouw CPU (0.8.1) | Gemeten in de sandbox en in CI, niet op jouw CPU. `analyse-bench` (gezichten) en `beeld-bench` (zoeken op beeld) meten het daar; zie [Op de LXC](#op-de-lxc). | de runbooks [gezichten.md](../runbooks/gezichten.md) en [analyse.md](../runbooks/analyse.md) |
| De metingen van 0.8.1 | De tijd en het geheugen van gezichten en beeld zijn gemeten met modellen van de echte vorm, maar met willekeurige gewichten. Over de kwaliteit zeggen ze niets; die komt alleen uit BZ4 in CI. De testfoto's van de grote belastingtest (M13) hadden geen gezichten: het herkennen (SFace) en het groeperen zaten er niet in. | [metingen.md](metingen.md), M10–M13 |
| Zoeken op beeld na een herstart (0.8.1) | Na een herstart van de container laadt het opwarmen het tekstmodel één keer (±1,7 s in de sandbox). Dat betaalt het opwarmen (zodra iemand op `/zoeken` zoeken op beeld kiest of het infopaneel van een eigen foto opent, of na het bouwen van de beeldindex), niet de eerste zoekvraag. | [integratie-2.md](integratie-2.md), De review (U1) |
| Zoeken op beeld terwijl gezichten gescand worden (0.8.1) | De grens van 250 ms is gemeten terwijl de gezichtenscan wachtte (191,8 ms). Liep de scan van 5.000 foto's ernaast, dan was het 290,4 ms (9 van 103 vragen boven 250 ms); dat was een stap zonder poort. Zo blijft het: de standaard (besloten: standaard, Jonas 2026-11-17; Vragen voor Jonas). | [metingen.md](metingen.md), Bevinding 12 |
| `/over` (0.8.1) | Alleen voor wie is ingelogd, ook een gast; een bezoeker zonder account ziet de pagina niet. | [integratie-2.md](integratie-2.md), Bekende grenzen |
| Het budget van de schermen van gezichten (0.8.1) | 11.964 van 12.000 B: nog 36 B ruimte. Wie daar iets toevoegt, moet eerst iets anders kleiner maken of een hogere grens vragen. De grens blijft: de standaard (besloten: standaard, Jonas 2026-11-17; Vragen voor Jonas). | `web/scripts/budget.mjs` |
| Office: Sluiten schuift | Gaat een andere bewerker weg, dan wordt de statusregel korter en schuift **Sluiten** in de kopbalk ±80 px. Wie precies dan klikt, kan naast de knop loslaten. De test wacht er nu op; de app is niet veranderd. | [integratie-2.md](integratie-2.md), Bekende grenzen |
| De tijdstest van de namen | In de volle testsuite was één tijdstest een paar keer net te traag (0,51–0,56 s tegen 0,5 s). Waarom, is niet bewezen; de grens bleef, en bij rood zegt de test nu meer. In de volle suite van 0.8.1 was hij groen. | [integratie-2.md](integratie-2.md), Bekende grenzen |
| Reservekopieën en gezichten (0.8.1) | De reservekopieën van het platform bevatten geen gezichten: na het terugzetten begint de scan opnieuw. De sleutels staan in `/opt/platform/sleutels/gezichten`, niet op `/tank`. | het runbook [gezichten.md](../runbooks/gezichten.md) |
| Jouw CPU | De 200 ms is gemeten in de sandbox en in CI. De Zoekbench op `/beheer` → Zoeken en foto's toont de p95 op jouw CPU, voor "eigenaar" en "alles"; ligt een van beide boven 200 ms, dan haalt jouw CPU het niet. `/beheer` waarschuwt daar niet zelf voor. Meten doe je met `bench --los`. | het runbook, "Zoeken meten"; §4.4 van het [bouwontwerp](bouwontwerp.md), "Op de LXC" |

## Wat je koos

Op 3 november 2026 zei je "ja voeg alles toe", en daarna koos je op een kaartje "Ook gezichten".
Dat betekent:

- **OCR: ja.** Scans en foto's met tekst worden doorzoekbaar. Daarvoor komt tesseract in het
  image. Dat bleek ±72 MB, niet ±20 MB. Op 4 november 2026 koos je op een kaartje "Ja, erin"
  (Vraag 15).
- **Gezichten automatisch: ja**, in fase 6 (~~gepland voor~~ in 0.8.1). Per gebruiker staat het
  standaard uit.
- **Zoeken op wat er op een foto staat** ("strand", "hond"): ja, in fase 6 (~~gepland voor~~ in
  0.8.1; ~~het kan naar een fase 6b schuiven~~ [16 november 2026: het blijft in 0.8.1, Keuze 85]). Dat
  volgt uit "alles"; het kaartje noemde het niet apart.
  Daarom staat het per gebruiker standaard uit (Vraag 11).
- **De kaart: geen tegels van buiten.** Alleen landsgrenzen. Er gaat niets naar een kaartdienst.
- **Plaatsnamen en landsgrenzen downloaden bij de installatie** (de oude Vraag 5 en 6): de kaartjes
  zijn ingetrokken, omdat "alles" ze dekt. De standaard geldt.
- **Wie ziet de plek van een foto** (Vraag 4): ~~die heb je **niet** beantwoord. Tot je dat doet,
  geldt de standaard: alleen de eigenaar. Dat is geen keuze van jou.~~ [17 november 2026: je koos
  de standaard, alleen de eigenaar; besloten: standaard, Jonas 2026-11-17.]
- **De rest: de standaard** (17 november 2026, "Standaard vastzetten"). Vraag 4 en Vraag 8–14, de
  andere kaarten B49–B71, en de vragen bij 0.8.0 en 0.8.1 staan op hun standaard:
  besloten: standaard, Jonas 2026-11-17. Zie [Vragen voor Jonas](#vragen-voor-jonas).

## Doel

Alles terugvinden, en meer halen uit je foto's. Uit het stappenplan:

- zoeken in de inhoud van pdf, docx, xlsx, pptx, txt en md;
- OCR voor scans en foto's met tekst;
- EXIF en GPS (datum, camera, plek), een kaart en plaatsnamen zonder internet;
- duplicaten en bijna-duplicaten;
- gezichten en zoeken op wat er op een foto staat, met lokale modellen;
- herinneringen ("vandaag, N jaar geleden") en slimme albums;
- filters op type, datum, persoon, plek, tag en bibliotheek.

Wat daarvan in 0.8.0 en 0.8.1 zit en wat er gemeten is: [Klaar als](#klaar-als).

## Hoe het werkt, in het kort

- Eén zoekindex in de SQLite die we al hebben (FTS5). Geen nieuwe database. Zoeken op naam werkt
  meteen na de update; de inhoud komt op de achtergrond.
- Je ziet alleen wat je mag zien. Geen score en geen totaal, zodat een uitkomst niets zegt over
  bestanden van anderen.
- Tekst lezen gebeurt in de afgeschermde mediaworker van fase 5 als media aan staat, anders in de
  app zelf (`/beheer` zegt welke). OCR draait alleen in de mediaworker, dus alleen met media aan.
- **Gezichten en zoeken op beeld** (sinds 0.8.1; ~~zoeken op beeld kan naar een fase 6b
  schuiven~~ [16 november 2026: het blijft in 0.8.1, Keuze 85]) rekenen in een eigen container,
  `analyse`. Die heeft geen netwerk en kan niet bij je
  bestanden: de app geeft hem alleen een klein voorbeeld van een foto. numpy en onnxruntime staan alleen daar; de app zelf gebruikt ze nooit.
- **Gezichten** (sinds 0.8.1) geven voorstellen ("Is dit Eva?"). Niets wordt vanzelf bevestigd.
  De getallen die het platform van elk gezicht maakt (de vectoren), zijn versleuteld, met een
  sleutel per gebruiker. Zet iemand gezichten uit, dan wist het platform eerst die sleutel en dan
  de gezichten. Oude kopieën van die getallen zijn dan onleesbaar, in elke kopie waar de sleutel
  niet in zit (zie "Privacy en de wet").
- Een foto die iemand anders in jouw map zette, wordt niet gescand, tenzij die ander het zelf aan
  heeft. Een upload via een publieke link nooit (Vraag 9).
- Plek, personen en tekst uit een foto horen bij de eigenaar van het bestand. Nooit via een
  publieke link.
- Bijwerken met hetzelfde `install.sh`, met eerst een reservekopie van de database. Terug naar
  0.8.0 (commit `59e0250`, eerst `db-downgrade 0010`) of naar 0.7.1 (commit `80d5bb8`, eerst
  `db-downgrade 0009`) met de stappen van het runbook [zoeken.md](../runbooks/zoeken.md), "Terug
  naar 0.8.0 van `59e0250`" en "Terug naar 0.7.1".

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
metingen; [haalbaarheid.md](haalbaarheid.md)). ~~Zoeken op beeld kan naar een fase 6b schuiven; dan
komt dat deel later.~~ [16 november 2026: zoeken op beeld blijft in 0.8.1 (Keuze 85).] [I2: wat
daarna in de bouw en in CI gemeten is, staat erbij; de rest blijft een schatting.]

| Wat | Hoeveel |
|---|---|
| het image | +118–141 MB voor numpy en onnxruntime, ook als je `analyse` uitzet |
| de modellen | ±39 MB voor gezichten en ±0,4 GB voor beeld, in `/opt/platform/modellen`; bij de installatie tijdelijk 1,55 GB extra |
| de database | ±0,3 GB per 100.000 foto's, voor gezichten en beeld samen; plus ±0,15 GB cache voor zoeken op beeld |
| eenmalig rekenen, 55.000 foto's | gezichten ±2,2–2,5 uur, beeld ±1,1–1,2 uur, op de achtergrond op één core (geschat). [I2: gemeten in de sandbox 1.043 foto's per minuut per core met één gezicht, op `9228eb6` ([x2b.md](x2b.md)); in CI 33,8 ms per foto voor beeld op `e535332`. Op jouw CPU: [Op de LXC](#op-de-lxc).] |
| geheugen | de container `analyse` hooguit 3 GB; met media en OnlyOffice samen 8 van de 16 GB. [I2: met modellen van de echte vorm samen 1.835 MiB (BZ5, M11), grens 2,5 GB; onder belasting 1,51 GB (M13).] |
| zoeken op beeld | binnen 250 ms (Vraag 14). [I2: onder belasting p95 191,8 ms (M13); op één core zonder belasting 90–107 ms (M12).] |

Je container heeft een rootdisk van 500 GB; dat past ruim. Een tragere CPU dan die van de sandbox rekent langer. `/beheer`
laat zien hoe ver het is.

## Planning

±43 werkdagen (≈ 9 weken), in twee delen (Vraag 13; besloten: standaard, Jonas 2026-11-17):

- **0.8.0 rond dag 27**: zoeken, tekst, OCR, foto-gegevens, de kaart, duplicaten, personen,
  herinneringen en slimme albums.
- **0.8.1 rond dag 43**: gezichten en zoeken op beeld.

Met één oplevering was het ±41 werkdagen geweest. Het stappenplan gaf 3 weken; dat was zonder
gezichten en beeld, en zonder review per golf. Loopt het uit, dan schuift eerst zoeken met woorden
op beeld, dan beeld helemaal (naar een fase 6b), dan de groepen "Wie is dit?" van gezichten. Geen
privacyregel schuift. [16 november 2026: zoeken op beeld is gebouwd en blijft in 0.8.1 (Keuze
85).] [I2: 0.8.1 kwam in twee delen, zoals Vraag 13 met zijn standaard: 0.8.0 op 11 november en
0.8.1 op 16 november 2026. Niets schoof naar een fase 6b.]

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

## Gezichten in detail (vanaf 0.8.1)

Zo werkt het sinds 0.8.1. Het installatiescript zet de analyse standaard aan; per gebruiker
staat het uit tot die het zelf aanzet. Met `ANALYSE=uit` zie je er niets van. Hoe het gebouwd is:
[x2b.md](x2b.md); wat je op de server doet: het runbook [gezichten.md](../runbooks/gezichten.md).

- **Wat er bewaard wordt.** Alleen voor wie het aanzet, en alleen bij de eigen foto's: per
  gevonden gezicht het kader, een score en de vector (128 getallen, versleuteld), je keuzes (ja,
  nee, niet tonen), en per foto of hij al bekeken is. Uitsneden van gezichten worden niet
  opgeslagen; de app maakt ze bij elke vraag uit de preview. Een bevestigd gezicht wordt een
  persoon op de foto, net als wanneer je die met de hand benoemt. Ongeveer 0,8 kB per gezicht in
  de database. Een vector staat nooit in een antwoord van de API, een log of een foutmelding; de
  beheerder ziet alleen aantallen.
- **De toestemming.** Per gebruiker, standaard uit. Aanzetten doe je onder Account → Foto's, na
  een dialoog dat zegt wat er gebeurt: dat het biometrische data is, ook van mensen op je foto's
  die zelf niets kozen, dat de getallen versleuteld op de server staan, dat de beheerder er
  technisch bij kan, wat er met kopieën gebeurt, en dat uitzetten alles wist. De beheerder kan
  het voor iedereen verbieden (`/beheer`); dat wist alles, en weer toestaan zet niemand aan. Een
  foto die iemand anders in jouw map zette, wordt alleen bekeken als die ander het zelf ook aan
  heeft; een upload via een publieke link nooit.
- **De versleuteling.** Elke vector is versleuteld (AES-256-GCM) met een sleutel per gebruiker.
  De sleutels staan in `/opt/platform/sleutels/gezichten`, buiten de map met je data. Zet iemand
  het uit, dan wist het platform eerst die sleutel en dan de gezichten; personen die je zelf
  benoemde of bevestigde, blijven.
- **Reservekopieën en snapshots.** De reservekopieën die het platform zelf maakt (`db-backup`, en
  die van `install.sh` vóór een update), bevatten geen gezichten. Je eigen snapshots van `/tank`
  en de offsite-kopie bevatten de gezichten versleuteld, zonder de sleutels; na "uit" zijn ze
  daar niet meer te lezen. Een backup of snapshot van de **hele** container bevat de sleutels
  wel: het runbook zegt hoe je de sleutels op een eigen mount point zonder backup zet.
- **De AVG**: zie [Privacy en de wet](#privacy-en-de-wet).
- **Bronnen en licenties.** Twee modellen van OpenCV Zoo, van een vaste commit (`48b10fa`), met
  hun sha256 in `api/app/analyse/models.lock`: YuNet (`face_detection_yunet_2026may.onnx`, het
  vinden, MIT) en SFace (`face_recognition_sface_2021dec.onnx`, de vector, Apache-2.0). De
  licentieteksten staan naast de modellen in `/opt/platform/modellen`. Waar ze mee getraind zijn,
  is niet nagekeken.
- **De vijf pakketten van de extra `analyse`.** Alleen in de container `analyse` (in
  `/opt/analyse` van het image); de app zelf gebruikt ze nooit: numpy 2.5.4 (BSD-3-Clause, met
  delen onder 0BSD, MIT, Zlib en CC0-1.0), onnxruntime 1.31.1 (MIT), en wat onnxruntime meebrengt:
  protobuf 7.36.2 (BSD-3-Clause), flatbuffers 25.12.19 (Apache-2.0) en packaging 26.3
  (Apache-2.0 of BSD-2-Clause). Ze maken het image ±118–141 MB groter, ook als je de analyse
  niet aanzet.
- **De kwaliteit is niet gemeten.** Er zijn in de bouw geen echte gezichten gebruikt (Vraag 8,
  standaard nee). De grenzen voor een voorstel (0,45) en voor een groep (0,50) zijn voorlopig;
  `app.cli analyse-kwaliteit` meet ze op je eigen LXC tegen je eigen ja en nee, en tot dan zegt
  `/beheer` "niet gemeten". Niets wordt vanzelf bevestigd. Twee eisen van het ontwerp hebben
  geen gereedschap ("≥ 95% van de portretten" en "kleurvolgorde ≥ 0,05 hoger"): die blijven
  "niet gemeten, geen gereedschap". Dat is besloten: Vraag 8 is nee (besloten: standaard, Jonas 2026-11-17), dus
  er komt geen set echte gezichten in CI. De kwaliteit meet alleen jij, met `analyse-kwaliteit` op je
  LXC.

## Vragen voor Jonas

**Besloten op 17 november 2026.** Je koos "Standaard vastzetten": elke vraag hieronder staat op
haar standaard, met de status **besloten: standaard, Jonas 2026-11-17**. Er verandert niets aan de app: 0.8.1
draaide al zo. De andere antwoorden blijven staan als uitleg; wil je er later een omgooien, dan is
dat een nieuwe keuze.

4. **Wie ziet de plek van een foto?** Standaard: **alleen de eigenaar**, en het deeldialoog
   waarschuwt dat een download de plek meeneemt. Anders: GPS uit downloads halen (±2 dagen), of
   "wie ik deel, ziet de plek ook". (besloten: standaard, Jonas 2026-11-17)
8. **Mogen echte gezichten van NASA in CI gebruikt worden om de herkenning te meten?** Dat is
   biometrie van echte mensen, bij naam, op servers van GitHub in de VS. Standaard: **nee**; dan
   meet je de kwaliteit op je eigen server, en verlaat niets je huis. (besloten: standaard, Jonas 2026-11-17)
9. **Worden foto's gescand die een ander in jouw map zette?** Standaard: **nee, tenzij die ander
   het zelf aan heeft**; een upload via een link nooit. (besloten: standaard, Jonas 2026-11-17)
10. **Hoe komt het model voor zoeken op beeld op je server?** Standaard: **`install.sh` downloadt
    het origineel van Google (via huggingface.co) en zet het één keer om op je server**, zonder
    nieuw pakket. (besloten: standaard, Jonas 2026-11-17)
11. **Staat zoeken op beeld standaard aan?** Standaard: **uit**, tot gemeten is of "Meer zoals
    deze" op een portret dezelfde persoon terugvindt. Wie het wil, zet het aan.
    (besloten: standaard, Jonas 2026-11-17)
12. **Wat mag CI gebruiken om zoeken op beeld na te rekenen?** Standaard: **ja** voor een vaste set
    van 120 foto's uit het publiek domein (alleen in CI), **nee** voor de officiële bibliotheken
    als meetlat. (besloten: standaard, Jonas 2026-11-17)
13. **Leveren we in twee delen op?** Standaard: **ja**: de kern rond dag 27, gezichten en beeld
    rond dag 43. Zo gebeurd: 0.8.0 en 0.8.1. (besloten: standaard, Jonas 2026-11-17)
14. **Mag zoeken op beeld 250 ms duren?** Standaard: **ja**; gewoon zoeken houdt zijn 200 ms.
    (besloten: standaard, Jonas 2026-11-17)

Vraag 1, 2, 3, 5, 6, 7 en 15 heb je beantwoord, of ze zijn ingetrokken (zie "Wat je koos"). De vragen
staan uitgebreid in het [bouwontwerp](bouwontwerp.md) onder "Vragen voor Jonas".

Nieuw bij 0.8.0, ook elk met een standaard (besloten: standaard, Jonas 2026-11-17):

- **Een tag voor 0.8.0 en 0.7.1?** Een tag is een vast label op een commit, zoals `v0.8.0`. Er is
  er nu geen voor 0.7.1 of 0.8.0; de weg terug gebruikt de volledige commit, zoals bij eerdere
  versies. Standaard: **geen tags** (besloten: standaard, Jonas 2026-11-17). Met ja was `v0.8.0` op de commit van
  0.8.0 gekomen en `v0.7.1` op `80d5bb8`.
- **Waar staat 0.8.0?** Standaard: **op de branch** `claude/stappenplan-uitwerken-lu4uv3`, en je
  werkt bij met `PLATFORM_BRANCH`, zoals in de [README](../../README.md) (besloten: standaard, Jonas 2026-11-17).
  Anders was het naar `main` gegaan, met het gewone commando.
- **Moet "Tekst in foto's herkennen" uit ook de tekst wissen?** Nu blijft de tekst die er al is;
  er komen alleen geen nieuwe foto's meer bij. Standaard: **niet wissen** ([x1.md](x1.md), Open 1;
  besloten: standaard, Jonas 2026-11-17).
- **Duplicaten: eerst de groepen "Bijna zeker gelijk" vormen, en dan pas "Lijkt erop"?** Nu is een
  groep zo zeker als zijn zwakste paar. Lijkt één foto in een groep maar een beetje, dan staat de
  hele groep onder "Lijkt erop", ook als de andere onderling bijna zeker gelijk zijn. Standaard:
  **zo laten** ([z3.md](z3.md), Open; besloten: standaard, Jonas 2026-11-17).
- **Is een halve seconde goed genoeg voor de kaart met een filter op soort, map, camera, persoon of
  tag?** Gemeten in de sandbox 0,31–0,34 s en in CI 0,43 s (M1) voor de hele wereld; zonder zo'n
  filter binnen 0,15 s. Sneller kan alleen met een nieuwe migratie, na die van 0.8.1. Standaard:
  **ja**, met een eigen grens van 0,5 s ([z3.md](z3.md), Open; besloten: standaard, Jonas 2026-11-17).
- **Voorrang voor een video die na verspringen opnieuw moet starten?** Springt iemand voorbij wat
  al klaar is, dan start het omzetten opnieuw met minder dan één stukje voorraad, terwijl een
  andere video op volle kracht doorwerkt; dan kan het beeld kort haperen (fase 5, Bevinding 3).
  Standaard: **ja, na elke herstart** (verspringen, een nieuwe poging na een fout, verder na een
  pauze): die video krijgt voorrang tot 10 s beeld klaar is, hooguit 20 s, en andere kijkers
  houden zolang 10 s voorsprong in plaats van 60 s. Zo zit het in 0.8.0 (besloten: standaard, Jonas 2026-11-17).
  De andere antwoorden waren: alleen na verspringen, alleen de eerste twee stukjes, of niets
  veranderen ([beslissingen.md](../fase-0/beslissingen.md), B71).
- **Wat telt de meting van media als haperen, vlak na verspringen?** Landt de meetspeler na een
  sprong in de laatste fractie van een stukje van 2 s, en moet het volgende stukje van een herstart
  komen, dan telt hij 0,2 s later één keer haperen, omdat geen herstart dat stukje zo snel maakt,
  hoe rustig de server ook is. Zo landt ±1 op 4 sprongen; een browser telt het niet. Standaard:
  **tellen zoals nu** (besloten: standaard, Jonas 2026-11-17; [metingen.md](metingen.md), Bevinding 3). De andere
  antwoorden waren: wachten op voorraad (na een sprong speelt de meetspeler pas verder als er een
  heel stukje klaarstaat; dat wachten telt als springtijd, met een harde grens van 3 s), of tellen
  zoals de browser.

Nieuw bij 0.8.1, ook elk met een standaard (besloten: standaard, Jonas 2026-11-17):

- **Waar staat 0.8.1?** Standaard: **op de branch** `claude/stappenplan-uitwerken-lu4uv3`, net als
  0.8.0, en je werkt bij met `PLATFORM_BRANCH`, zoals in de [README](../../README.md). Anders was
  het naar `main` gegaan, met het gewone commando.
- **Een tag voor 0.8.1?** Standaard: **geen tag**, zoals bij 0.7.x en 0.8.0. De docs noemen de
  volledige commit van 0.8.1 als de weg terug voor de volgende versie: `ae163ac69470343de7bc8d876db1a48e7da9331e`. Met ja was `v0.8.1` op die commit gekomen.
- **Twee eisen voor gezichten zonder gereedschap.** "Op ≥ 95% van de portretten precies het goede
  gezicht gevonden" en "de gekozen kleurvolgorde ≥ 0,05 hoger dan de andere" kunnen we alleen
  meten met een set echte gezichten, en die komt er niet: Vraag 8 is nee. Standaard: beide blijven
  **niet gemeten, geen gereedschap**. Wat wel te meten is, meet jij met `analyse-kwaliteit` op je
  LXC (precisie, zuiverheid en vangst). Het andere antwoord was Vraag 8 op ja (een openbare set
  gezichten in CI, met jouw akkoord voor die set; ±1–2 dagen werk).
- **`BEELD=uit` samen met `ANALYSE=uit`.** Vraag je bij de installatie beide uit, dan onthoudt
  `.env` alleen `ANALYSE=uit`; zet je later `ANALYSE=aan`, dan komt zoeken op beeld mee, met de
  download van 1,55 GB. (a) Ook `BEELD=uit` onthouden; (b) zo laten en in de eindtekst zeggen dat
  `BEELD` alleen telt met de analyse aan. Standaard: **zo laten**; `install.sh` verandert niet.
- **Een vraag die te laat is, houdt de analyse 5 s bezig.** Daarna stopt de worker het kind hard.
  Standaard: **zo laten**. Het andere antwoord was een eigen pakket dat het kind eerder vrijmaakt, met tests.
- **De job `beeld-model` in CI** start niet als alleen de tests van de echte vorm (BZ5, BZ6)
  veranderen; die draaien we met de hand. Standaard: **zo laten**. Het andere antwoord was: die
  bestanden erbij.
- **De analyse met een lagere voorrang starten.** Start iemand de analyse zelf met `nice`, dan kan
  het kind voor zoeken op beeld niet starten. In de container zoals `install.sh` hem start, gebeurt
  dat niet. Standaard: **zo laten**. Het andere antwoord was: het kind past zich aan, met een test.
- **Nog 36 B ruimte in de schermen van gezichten.** Standaard: **de grens blijft**; wie er iets
  toevoegt, maakt eerst iets anders kleiner of vraagt het je. Het andere antwoord was: de grens nu
  ophogen.
- **Zoeken op beeld terwijl gezichten gescand worden** (290,4 ms, zie Bekende grenzen).
  Standaard: **zo laten**: de poort van 250 ms geldt voor de gewone belasting. De andere antwoorden
  waren: een aparte meting erbij, of de scan laten wijken voor een zoekvraag (een codewijziging).

Uitgebreider, met de opties: [integratie-2.md](integratie-2.md#vragen-voor-jonas).

## Op de LXC

Na het bijwerken naar 0.8.1 (of 0.8.0). De commando's staan in de runbooks
[zoeken.md](../runbooks/zoeken.md), [analyse.md](../runbooks/analyse.md) en
[gezichten.md](../runbooks/gezichten.md); de uitkomst gaat in [metingen.md](metingen.md), onder
"Wat alleen de LXC of CI kan laten zien". De eerste zes punten horen bij 0.8.0, de rest bij 0.8.1.

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
- [ ] **0.8.1: de eindtekst.** Die zegt `Gezichten (de container analyse): aan; …` en `Zoeken op
  beeld: het model staat klaar; …`. Staat er `Let op: …`, dan staat in het runbook
  [analyse.md](../runbooks/analyse.md) wat de melding betekent. Op je server zet het script het
  model voor zoeken op beeld voor het eerst echt om, in de afgeschermde container
  `analyse-omzetten`.
- [ ] **0.8.1: de container.** `dc exec -T platform python -m app.cli analyse-status` zegt `ok`,
  met de modellen en de opdrachten (het runbook [analyse.md](../runbooks/analyse.md), "De stand
  lezen").
- [ ] **0.8.1: gezichten.** Zet bij Account → Foto's **Gezichten herkennen in mijn foto's** aan, wacht
  tot `/beheer` → Zoeken en foto's zegt dat de scan klaar is, en kijk op `/fotos/personen` of de
  voorstellen kloppen. Meet de snelheid op jouw CPU met `analyse-bench`, en de kwaliteit met
  `analyse-kwaliteit` (het runbook [gezichten.md](../runbooks/gezichten.md)).
- [ ] **0.8.1: zoeken op beeld.** Zet **Zoeken op beeld** aan, wacht tot de beeldindex klaar is,
  en zoek op `/zoeken` een paar woorden en een **Meer zoals deze**. Meet daarna `beeld-bench`
  (BZ9: p95 ≤ 250 ms en ≥ 500 foto's per minuut) en, als je personen hebt bevestigd,
  `beeld-kwaliteit --personen` (BZ10); het runbook [analyse.md](../runbooks/analyse.md).
- [ ] **0.8.1: het geheugen.** Na een paar dagen met de scan aan: `memory.peak` en `OOMKilled` van
  `platform-analyse-1` (het runbook [analyse.md](../runbooks/analyse.md), "Het geheugen op de
  LXC"). `OOMKilled` hoort `false`, `memory.peak` onder 2,5 GB.
- [ ] **0.8.1: de sleutels buiten je backups**, als je dat wilt: een eigen mount point zonder
  backup voor `/opt/platform/sleutels/gezichten` (het runbook
  [gezichten.md](../runbooks/gezichten.md), "De map met sleutels niet in snapshots of backups").
- [ ] **0.8.1: `/over`.** Account → **Bronnen en licenties** toont de bronnen van de kaart en de
  modellen.

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
- [integratie-2.md](integratie-2.md): de integratie van 0.8.1 (I2): de pakketten, de controles, de
  metingen M10–M13, en wat alleen de LXC kan bewijzen.
- [a0.md](a0.md), [x2a.md](x2a.md), [x2b.md](x2b.md), [x3a.md](x3a.md) en [x3b.md](x3b.md): het
  fundament, gezichten en zoeken op beeld, per pakket; het toetsenbord in
  [toetsenbord-x2.md](toetsenbord-x2.md).
- [metingen.md](metingen.md): de metingen M1–M9 tegen de grenzen van §4.4 en §4.5, en M10–M13 voor
  gezichten en beeld, met de bevindingen en de commando's om opnieuw te meten.
- [e2e.md](e2e.md): de tests van zoeken (de markers `zoek`, `triggers`, `geo`, `zoek_perf` en
  `ocr`) en van de analyse (`analyse`, `analyse_echt`, `beeld_echt`), `e2e/slim.sh`, de
  meetscripts, de jobs in CI, en de regel dat de grens van 200 ms in CI nooit ruimer wordt (C17).
- Het runbook [zoeken.md](../runbooks/zoeken.md): wat Jonas op de server doet (wat het script
  doet, de reservekopie, de plaatsnamen, de stand lezen, de wekelijkse controle, de index
  herbouwen, zoeken meten, en terug naar 0.8.0 en 0.7.1). Voor 0.8.1 ook
  [analyse.md](../runbooks/analyse.md) (de container, zoeken op beeld) en
  [gezichten.md](../runbooks/gezichten.md) (de gegevens, de sleutels, wissen, de wet).
- [beslissingen](../fase-0/beslissingen.md): B49–B71 voor fase 6. B53 en B57 zijn besloten
  (3 november 2026); de rest is besloten: standaard, Jonas 2026-11-17.

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
- **Golf 5** (15 november): de container `analyse`, die in zijn eigen afscherming gezichten vindt
  (X2a, [x2a.md](x2a.md)), en de tokenizer en de omzetter van het beeldmodel SigLIP 2 (X3a,
  [x3a.md](x3a.md)). Voor jou is er nog niets te zien: zonder `ANALYSE=aan` doet `install.sh`
  precies wat 0.8.0 deed, en de app doet nog niets met de container.
- **Golf 6** (16 november): gezichten in de app (X2b, [x2b.md](x2b.md)) en zoeken op beeld in de
  app (X3b, [x3b.md](x3b.md)) zijn gebouwd, samengevoegd en nagekeken (review en nacontrole), en
  wat er intussen op GitHub stond, is erbij gehaald. Zoeken op beeld komt in 0.8.1: je koos "Toch
  uitbrengen" (Keuze 85), en de uitleg bij de schakelaar zegt nu wat je mag verwachten. In CI was
  de kwaliteitspoort van zoeken op beeld met die keuze goed (run 43870814179); de job zelf bleef
  rood omdat de vaste set foto's en de regels van het model in `models.lock` daar nog ontbraken.
  Die staan nu op de branch. ~~Golf 6 moet nog naar GitHub; daarna zegt CI of alles groen is. Nog
  niet: de integratie I2 en 0.8.1. Voor jou is er nog niets te zien: zonder `ANALYSE=aan` doet het
  platform wat 0.8.0 deed.~~ [I2: golf 6 ging naar GitHub als `dc5dfe5`; daar was de job
  `beeld-model` helemaal groen, met de stap "Uitslag" (run 43888472609). Drie tests die alleen in
  CI draaien, gingen nog uit van de stand vóór golf 6; hersteld in `e535332`, en daar waren alle 19
  jobs groen (run 43892193604).] Zie [Wat 0.8.1 brengt](#wat-081-brengt).
- **De integratie van 0.8.1** (I2, [integratie-2.md](integratie-2.md)): de analyse standaard aan
  in `install.sh` (uit blijft bewaard), `/over`, de zinnen met de analyse uit, de metingen met
  modellen van de echte vorm en §4.5 met de analyse (M10–M13), een stap in CI die SigLIP 2 voor
  het eerst echt omzet in `analyse-omzetten` (groen in run 43981579405: omgezet in 9,6 s, en de bestanden precies die van `models.lock`), en
  deze teksten. 0.8.1 is de commit `ae163ac` van 16 november 2026, met CI groen in run 43981579405 (alle 19 jobs).
