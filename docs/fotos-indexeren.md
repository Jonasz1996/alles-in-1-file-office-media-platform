# Foto's indexeren: thumbnails, metadata en direct laden

Geïndexeerd betekent hier: elke foto en elke video heeft een thumbnail (256 px), een grote
versie (1024 px; 2048 px voor wat de browser zelf niet kan tonen, zoals HEIC, TIFF, PSD, JPEG
2000 en camera-RAW) en zijn metadata (opnamedatum, afmetingen, camera, duur). Locatie (GPS),
tekst in foto's en gezichten horen er niet bij; dat is fase 6.

De code: `api/app/previews.py` (de taken), `api/app/render.py` (het renderproces),
`api/app/jobs.py` (de rijen), `web/src/photos/live.ts` (thumbnails live bijwerken) en het
tabblad Taken op `/beheer` (`web/src/admin/Jobs.tsx`). De metingen van voor en na staan
hieronder bij "Gemeten".

## Hoe een foto zijn thumbnail krijgt

**Twee rondes.** Elke nieuwe inhoud (upload, import, office, publieke upload) krijgt een
taak `preview`. Voor een JPEG, een camera-RAW (de ingebedde JPEG) en een HEIC (de ingebedde
thumbnail, zoals een iPhone die erin zet) maakt die eerst alleen de thumbnail, met de
opnamedatum en de rest van de metadata. Dat kost een JPEG van 12 MP 50 ms in plaats van
425 ms. De grote versie komt daarna als taak `preview.large`, pas als er geen thumbnails meer
wachten. Opent iemand zo'n foto in de viewer, dan gaat zijn grote versie wel meteen (zie
hieronder). De viewer toont tot dan de thumbnail, vraagt het detail opnieuw zolang alleen de
thumbnail er is (en ingelogd ook bij `preview.ready`), en wisselt naar de grote versie zodra die
er is. Voor een RAW of HEIC, die de browser zelf niet kan tonen, bleef dat eerst 256 px.
Is er van zo'n foto (RAW, HEIC, TIFF, PSD) nog helemaal niets als je hem opent, dan staat er
"Het voorbeeld wordt gemaakt…" tot de preview er is (`ZoomView`). Eerst stond daar "Deze
afbeelding kan niet worden getoond.", en dat bleef staan, ook als de preview er daarna was (de
nacontrole: een PSD bleef zo 75 s leeg, tot je hem opnieuw opende).
Mislukt de tweede ronde ook na de laatste poging, dan blijft de thumbnail staan en krijgt de
foto `large_failed` in zijn metadata: `/beheer` telt hem dan apart, de viewer vraagt niet
verder, en **Alles nalopen** probeert het opnieuw.

**Wat je bekijkt, gaat voor.** De tijdlijn (`GET /photos`), een map (`POST /previews`) en de
viewer zetten de wachtende taken van wat ze tonen vooraan in de rij, de laatst bekeken eerst.
De tijdlijn en een map doen dat alleen voor de thumbnail (meer tonen ze niet); de viewer ook
voor de grote versie. Wat zo vooraan staat, gaat voor alles, ook voor de thumbnails van een
import (`jobs._fronted_filter`). Een nieuwe upload tijdens een grote import hoeft zo niet
achter de hele import te wachten.
Binnen één keer gaat het in de volgorde van het scherm: de tijdlijn haalt een hele pagina (tot
500) naar voren, en wat bovenaan staat, gaat als eerste. Dat was eerst niet zo: de hele pagina
kreeg dezelfde plek en dan ging de oudste taak voor. Bij een import staan de nieuwste foto's
bovenaan en zijn hun taken de jongste, dus de zichtbare thumbnails kwamen als laatste (de
nacontrole, zie "Gemeten").
Dat gebeurt pas na het antwoord, in een eigen sessie (`previews.to_front`): het schrijven
wacht soms op SQLite, en het antwoord hoeft daar niet op te wachten. Wie dezelfde foto's
binnen 10 seconden opnieuw laadt, schrijft niets (`jobs.FRONT_AGAIN`).

**Een eigen rij, die de rest niet verdringt.** Previews hebben hun eigen rij (`preview`) met
een worker minder dan het aantal kernen (instelbaar, zie hieronder). Het renderproces draait op
`nice 10`, zodat uploads, de API, office en afspelen voorgaan. Een voorgeladen forkserver maakt
per foto een eigen proces (`render.serve`). De grenzen van tijd en geheugen blijven per foto
gelden, maar Python en Pillow hoeven niet elke keer op te starten (272 ms in plaats van 493 ms
per JPEG). Start de forkserver niet, dan krijgt elke preview weer zijn eigen nieuwe proces,
zoals voorheen. Een nieuwe taak wekt alleen de workers van zijn eigen rij.

**De forkserver schermt niet minder af dan het eigen proces van vroeger** (nagelopen na de
review, ook als `nobody` zoals in CI). Hij wordt zelf gestart als een nieuw proces (`python -I`,
werkmap `/`, alleen `PATH`, `LANG` en `HOME`), niet afgesplitst van de app: in zijn geheugen
staan geen geheimen van de app. Een kind ervan heeft dezelfde omgeving, geen open bestanden of
sockets van de app (alleen `/dev/null` en zijn eigen uitvoerbestanden), dezelfde grenzen
(geheugen via `RLIMIT_AS`, `nice 10`), een eigen sessie, en draait in een kopie van de
forkserver: wat een kwaadaardige foto in één kind verandert, ziet het volgende niet. Wat de
controle na de review nog vond, en wat nu anders is:

- Een video krijgt de sleutel van de mediaworker mee. Via de forkserver bleef die in diens
  geheugen staan, en erfde elk volgend kind hem, ook een dat een foto decodeert. Een verzoek
  met de worker krijgt nu altijd een eigen proces.
- Afbreken (de tijdgrens, of de app stopt) vlak na het versturen, vóór de pid terug was, liet
  het kind doorlopen zonder tijdgrens; en de forkserver meldde de pid vóór het kind een eigen
  sessie had, zodat `killpg` het kon missen. Nu komt de pid pas na de `setsid`, en wacht
  afbreken op de pid om het kind toch te stoppen.
- Viel de forkserver weg tijdens een render, dan liep het kind door zonder dat iemand op de
  tijdgrens lette. Nu gaan zijn kinderen mee (`PR_SET_PDEATHSIG`); ook als de app wegvalt.
- Stopt de forkserver (gecrasht, doodgemaakt) of antwoordt hij niet meer, dan start de volgende
  preview een nieuwe (hooguit eens per 30 s, `previews.RESTART_SECONDS`; tot dan een eigen
  proces per foto). Een render die op dat moment liep, mislukt en komt als gewone nieuwe poging
  terug. Eerder bleef het tot een herstart van de app een eigen proces per foto, en liet een
  forkserver die niet antwoordde elke preview tot de tijdgrens hangen.

**Niets blijft stil zonder thumbnail:**

- Een JPEG tot 4× `preview_max_pixels` (dus 400 MP: panorama's en de 200 MP-stand van een
  telefoon) wordt verkleind gedecodeerd. Andere formaten houden de grens van 100 MP.
- Een afgekapte JPEG (een afgebroken sync) krijgt toch een thumbnail; wat ontbreekt is grijs.
- Camera-RAW (CR2, CR3, NEF, NRW, ARW, DNG, ORF, RW2, RAF, PEF) krijgt de ingebedde JPEG als
  preview en staat op de tijdlijn. Het zoeken naar die JPEG heeft een vaste grens aan het werk
  (`render.RAW_STEPS`): een gemaakt bestand met een JPEG-kop na elke paar bytes houdt een worker
  zo hooguit een fractie van een seconde bezig (was tot de tijdgrens van 300 s, drie pogingen
  lang). PSD en JPEG 2000 krijgen ook een preview.
- Een video die niet kon omdat de mediaworker geen ffmpeg had, komt vanzelf terug zodra de
  worker gezond is.
- Een video die wachtte omdat de mediaworker er (nog) niet was, bijvoorbeeld vlak na het
  starten van de app, gaat meteen zodra de worker er weer is (`previews.worker_back`, vanuit
  de gezondheidslus), niet pas na 5 minuten.
- Dezelfde inhoud nog eens uploaden probeert een mislukte preview opnieuw.
- Ontbreekt het bestand van een preview op schijf, dan wordt hij opnieuw gemaakt zodra iemand
  hem opvraagt (tot dan 404).
- Wat mislukte of geen preview kreeg, komt terug met **Alles nalopen** (hieronder).

## Direct laden

- Een previewadres blijft hetzelfde tot de volgende grens van zijn termijn en werkt daarna nog
  zo lang. De browser mag een thumbnail zo lang bewaren als het adres geldig is
  (`Cache-Control: max-age` is wat er nog over is; was altijd een dag). Binnen een termijn haalt
  een tweede bezoek de thumbnails dus uit de cache. De termijn is in de app standaard 6 uur,
  zoals sinds fase 3 (`PLATFORM_PREVIEW_URL_HOURS`, nooit korter); achter een publieke link
  altijd 6 uur (`previews.PUBLIC_PERIOD`), ook in de viewer van de link.
- De web-app vraagt adressen na 5 uur opnieuw (`STALE_MS` in `web/src/browser/previews.ts`).
  Geldt een adres langer, dan krijgt hij hetzelfde adres terug en blijft de afbeelding uit de
  cache komen.
- Komt er een thumbnail klaar (`preview.ready`), dan vraagt de tijdlijn (of het album) alleen
  de adressen van die foto's op (`POST /previews`, hooguit één keer per 500 ms) en zet de
  thumbnail in de plaatshouder. Vroeger las de pagina bij elke klare thumbnail de maanden en
  alle zichtbare secties opnieuw (9 tot 13 verzoeken per seconde per open tabblad tijdens een
  import). De maanden worden alleen nog opnieuw gelezen bij `node.*` (een bestand erbij, weg of
  verplaatst), na het opnieuw verbinden, of als de opnamedatum uit de foto hem naar een andere
  dag verschuift (`preview.ready` met `dated`). Dat gebeurt hooguit één keer per seconde.

**Te beslissen (Jonas):** een langere termijn in de app, bijvoorbeeld een week
(`PLATFORM_PREVIEW_URL_HOURS=168`). Dan haalt de browser de thumbnails hooguit eens per week
opnieuw in plaats van elke 6 uur. Maar een previewadres is ondertekend op de inhoud, niet op de
gebruiker: na een ingetrokken deling, een blokkade of een weggehaalde gast werkt een adres dat
al was uitgegeven nog twee termijnen (met een week: 14 dagen, nu 12 uur), ook voor wie het
doorgestuurd kreeg. Alleen voor die ene thumbnail of grote versie, en alleen met de inhoud van
toen. Achter een publieke link blijft het altijd 12 uur. De bouw stond eerst op 7 dagen; de
review vond dat te ruim zonder jouw besluit, dus de standaard is weer 6 uur.

## Op /beheer zien of alles geïndexeerd is

`/beheer` → **Taken** heeft bovenaan **Foto's en video's**:

- "x van y hebben een thumbnail, n in de wachtrij, m mislukt";
- zo nodig: hoeveel er nog een grote versie krijgen, bij hoeveel de grote versie mislukte
  (die hebben wel een thumbnail), hoeveel er geen preview kunnen krijgen (onleesbaar, of een
  soort zonder preview) en hoeveel video's wachten op ffmpeg in de mediaworker.

**Alles nalopen** zet alles zonder (complete) preview opnieuw in de rij, ook wat mislukte en
foto's of video's met "geen preview". Ook maakt het een preview opnieuw als zijn bestand op
schijf ontbreekt, en geeft het een foto met alleen een thumbnail zijn tweede ronde terug.
Daarna komen ook de metadata (photometa) en verouderde previews aan de beurt. Het loopt in
stukken van 500 als achtergrondtaak (`preview.recheck`); de app blijft bruikbaar. Twee keer
drukken start het niet twee keer, ook niet tegelijk (twee tabbladen of twee beheerders: een slot
in de app, `previews.queue_recheck`; eerst gaf zes keer tegelijk vijf ketens). Alleen de
commandoregel en de knop op precies hetzelfde moment kunnen er nog twee geven; de tweede stopt
dan na zijn eerste stuk van 500.

Hetzelfde vanaf de commandoregel, in de LXC:

```bash
dc exec -T platform python -m app.cli previews-recheck
```

## Instellingen

| omgevingsvariabele | standaard | wat |
|---|---|---|
| `PLATFORM_PREVIEW_WORKERS` | `0` | workers voor previews; 0 = een minder dan het aantal kernen (minstens 1) |
| `PLATFORM_PREVIEW_MEMORY_MB` | `1536` | geheugengrens per renderproces |
| `PLATFORM_PREVIEW_MAX_PIXELS` | `100000000` | grens voor één afbeelding; een JPEG mag 4 keer zoveel |
| `PLATFORM_PREVIEW_URL_HOURS` | `6` | zo lang blijft een previewadres in de app hetzelfde (minstens 6; zie "Te beslissen") |
| `PLATFORM_JOB_TIMEOUT` | `300` | zo lang mag één taak duren |

## Database: migratie 0009

Migratie 0009 voegt alleen twee indexen toe: `ix_job_queue` op de wachtrij (een claim kostte
73 ms met 50.000 previews in de rij, nu 6,6 ms) en `ix_blob_ref_count` (het opruimen van
ongebruikte inhoud). Ze loopt vanzelf bij het starten.

**Terug naar 0.7.0:** 0.7.0 start niet op revisie 0009. Zet het schema eerst terug, met het
image van nu en de app gestopt, en installeer daarna 0.7.0. De stappen staan in
[runbooks/media.md](runbooks/media.md) ("Terug naar 0.6.0"); gebruik daar `db-downgrade 0008`
in plaats van `0007`, en de commit van 0.7.0:

```bash
dc stop platform media
dc run --rm --no-deps platform python -m app.cli db-downgrade 0008
```

Er gaat niets verloren: de indexen worden alleen weggehaald.

## Gemeten

Op een machine met 4 kernen, de app op 3 (`taskset`), met 539 bestanden (500 JPEG's van
12 MP, HEIC, PNG, TIFF en 4 video's; 1,6 GB). Vóór is commit `1130e1b`.

| | vóór | na |
|---|---|---|
| import met /fotos open: alle thumbnails | 190,7 s | 103,5 s |
| idem: thumbnails na 30 / 60 / 90 s | 93 / 173 / 264 | 184 / 341 / 502 |
| idem: import zelf klaar | 59 s | 90 s (zonder tabblad zie hieronder) |
| achterstand van 540: alle thumbnails | 151,5 s (3 workers) | 64,3 s (2 workers) |
| nieuwe upload achter ~530 wachtende | 18,7 s | 0,16 s |
| /fotos open tijdens de achterstand: alles geschilderd | 148,5 s | 5,3 s |
| idem: `GET /photos` + maanden in 150 s | 1185 + 198 | 6 + 1 |
| `GET /photos` tijdens de achterstand (p50 / p95) | 99 / 275 ms | 84 / 141 ms |
| een taak claimen met 50.000 in de rij | 73 ms | 6,6 ms |
| laden van /fotos, map (alles geïndexeerd) | 1,0-2,0 s | gelijk |

Bij 50.000 foto's (afgeleid): alle thumbnails in ongeveer 1,7 uur, alles in 3,7 uur (was
4,9 uur, met de zichtbare foto's het laatst).

De review mat de import ook zonder open tabblad, en dan op dezelfde manier vóór en na: een
kleinere import van 159 foto's, 3 keer per kant, de app op 3 kernen. Hier stond eerst "na
zonder open tabblad 65 s" naast "vóór met open tabblad 59 s"; die vergelijking liet de
vertraging kleiner lijken dan hij is.

| zonder open tabblad, 159 foto's (3 keer) | vóór | na |
|---|---|---|
| import zelf klaar | 17,9 / 19,1 / 11,9 s | 23,0 / 20,4 / 21,5 s |
| alle thumbnails | 49,1 / 55,5 / 46,6 s | 24,6 / 22,0 / 23,1 s |
| alles, ook de grote versies | 49,1 / 55,5 / 46,6 s | 47,2 / 45,0 / 45,2 s |
| `GET children` tijdens de import (p50) | 40 / 39 / 27 ms | 57 / 56 / 60 ms |
| previews opvragen tijdens de import (p50) | 37 / 39 / 30 ms | 54 / 51 / 48 ms |
| CPU-tijd van de app | 115 / 112 / 105 s | 94 / 88 / 90 s |

De import zelf is dus ongeveer een derde trager (gemiddeld +33 %, de mediaan +21 %), en
verzoeken aan de API duren tijdens de import ongeveer anderhalf keer zo lang (de mediaan blijft
onder de 0,1 s). Daar staat tegenover dat alle thumbnails twee keer zo vroeg klaar zijn en dat
alles samen iets minder tijd en CPU kost.

De nacontrole na de review mat met 151 gemengde bestanden (100 JPEG's, PNG, TIFF, HEIC, PSD,
JPEG 2000, camera-RAW, GIF, WebP, kapotte en afgekapte, 2 video's), de app op 3 kernen: eerst
een gewone upload, daarna alle previews weg en opnieuw in de rij, de bovenkant van de tijdlijn
als laatste (zoals na een import). De bovenste 24 waren hier de zware soorten (TIFF van 18 MB,
HEIC, PNG, JPEG 2000).

| nacontrole, 151 bestanden | vóór de nacontrole | na |
|---|---|---|
| elk bestand een thumbnail of een reden op /beheer | 146 + 4 "zonder preview" (150 inhouden) | gelijk |
| /fotos open: de 24 zichtbare cellen geschilderd | 25,2 s (als laatste) | 10,2 s (119 wachten nog) |
| `GET /photos` tijdens de achterstand (p50 / p95 / max) | 38 / 74 / 246 ms | 38 / 64 / 172 ms |
| RAW in de viewer, 112 grote versies in de rij: 2048 te zien | | 3,1 s (107 wachten nog) |
| PSD in de viewer vóór zijn eerste preview | bleef "kan niet worden getoond" | na 9,9 s te zien |
| "Alles nalopen" zes keer tegelijk | 5 ketens | 1 |

De PSD kwam pas na 9,9 s aan de beurt doordat de tijdlijn hem 3 s eerder al naar voren had
gehaald: binnen `FRONT_AGAIN` (10 s) haalt ook de viewer hem niet opnieuw naar voren (zie
"Wat nog open staat").

## Wat nog open staat

- **HTTP/2.** De browser opent 6 verbindingen per host, en één daarvan houdt de live-stroom
  vast. Bij een eerste bezoek wachten thumbnails daardoor 84 tot 402 ms op een verbinding.
  uvicorn praat alleen HTTP/1.1; HTTP/2 vraagt een nieuwe afhankelijkheid of een proxy ervoor.
  Daarom is dit niet gebouwd.
- **Camera-RAW en iPhone-HEIC** zijn getest met nagemaakte bestanden, niet met echte foto's van
  een camera of iPhone.
- **Het renderproces draait als dezelfde gebruiker als de app**, met of zonder forkserver. Een
  decoder die echt wordt overgenomen, kan zo de omgeving van de app lezen
  (`/proc/<pid van de app>/environ`, dus `PLATFORM_JWT_SECRET` als die daar staat), en ook de
  database en de blobs. De kale omgeving van het renderproces voorkomt dus alleen dat geheimen
  per ongeluk meegaan, niet dat een aanvaller ze haalt. Dat was vóór dit werk precies zo;
  dichtzetten vraagt de app zelf niet-dumpbaar (`PR_SET_DUMPABLE 0`) of het renderen onder een
  eigen gebruiker. Niet gebouwd: dat raakt de hele app, niet alleen de foto's.
- **De import zelf is trager**, ook zonder dat iemand kijkt: op dezelfde manier gemeten
  ongeveer een derde (zie "Gemeten"; met /fotos open 59 naar 90 s). De thumbnails houden de
  import nu bij en delen met hem de event loop, de schrijver van SQLite en het slot van de
  opslag (`BlobStore.lock`). De thumbnails komen wel twee keer zo vroeg, en alles samen is
  niet later klaar. Minder vertraging vraagt een fijner slot of de previews afremmen zolang een
  import loopt; dat is niet gebouwd.
- **Een voorwaardelijk verzoek** (`If-None-Match`) op een thumbnail geeft nog 200 met de hele
  inhoud, geen 304. Met `immutable` en een geldig `max-age` vraagt de browser het niet.
- **Een open map laadt opnieuw** bij elke nieuwe verbinding van de live-stroom (`folderStore`,
  ongewijzigd). Dat haalt soms de thumbnails van de map twee keer op.
- **De viewer wacht soms tot 13 s op een thumbnail.** Wat de tijdlijn of een map in de laatste
  10 s naar voren haalde (`jobs.FRONT_AGAIN`), haalt ook de viewer niet opnieuw naar voren; hij
  krijgt zijn beurt in de volgorde van de pagina, of bij de volgende vraag van de viewer (elke
  3 s) zodra die 10 s om zijn. De grote versie heeft dat niet: die zet alleen de viewer klaar.
- **"Alles nalopen" kan een foto even terugzetten.** Komt een thumbnail klaar terwijl de keten
  zijn stuk van 500 bekijkt, dan kan hij die foto nog als "wacht" zien en opnieuw in de rij
  zetten (`previews._again`). Tot hij nog een keer gemaakt is, staat hij weer op "wacht" en is
  zijn thumbnail even weg. Alleen tijdens een achterstand, en alleen als iemand dan drukt.
- **Commandoregel en knop tegelijk.** `app.cli previews-recheck` is een eigen proces; drukt
  iemand op precies hetzelfde moment op de knop, dan kunnen er twee ketens klaarstaan. De
  tweede stopt na zijn eerste stuk.
- **Welke bestanden geen preview hebben**, ziet /beheer alleen als aantal ("4 zonder preview"),
  niet bij naam.
