# Metingen fase 6 (I1)

De metingen van §4.4 en §4.5 van het [bouwontwerp](bouwontwerp.md) bij de integratie I1 (0.8.0).
Alle getallen staan ook in [metingen.json](metingen.json). De scripts staan in [e2e.md](e2e.md); de
uitleg bij de uitkomst in [integratie.md](integratie.md). Wat niet gehaald werd, is niet bijgesteld
om te slagen; de oorzaak staat bij elke meting en onder [Bevindingen](#bevindingen).

## Kort

⟦C2: de tabel met elke poort van §4.4 en §4.5, de waarde en gehaald of niet, na M7 en M8⟧

## Omstandigheden

- **Machine:** de sandbox, een Firecracker-VM met 4 vCPU (Intel Xeon @ 2,10 GHz), 15,7 GiB RAM,
  geen swap, geen GPU. SQLite 3.53.1 op de host en 3.46.1 in `python:3.12-slim` (Python 3.12.16);
  ffmpeg 6.1.1 (Ubuntu); tesseract 5.3.4.
- **De commits:** M2–M6 op `d5624b5`, M9 op `d5624b5` (die commit is M9), M7 op ⟦C2: de commit
  van de laatste M7⟧, M8 op ⟦C2: commit van M8⟧. M1 komt uit CI (run 102 op `0e19180`), net als
  de getallen van het image en `e2e-media-perf` in M8.
- **De cores:** elke meting liep alleen. M2, M3 en M4 op één core (`taskset -c 1` en
  `--core 1`), M5 op core 0 (`taskset -c 0`, zoals de job `ocr`), M6 op 0-2 (`taskset -c 0-2` op
  de host, `--cpuset-cpus 0-2` in `python:3.12-slim`). M7: de worker op 0-2 en de app niet
  (regel 14 van fase 5; zie M7 voor de eerste rondes, die anders liepen).
- **Geheugen:** het minst vrije geheugen staat bij M7; bij de andere metingen bleef `MemAvailable`
  boven de 12 GiB.

## M1: zoeken en foto's (§4.4)

Uit de job `zoek-perf` van CI, run 102 (id 42939492812) op `0e19180`: de boom van 100.000
bestanden (`--n 100000 --seed 6`, profiel "groot"), de app in `python:3.12-slim` met SQLite
3.46.1, via HTTP vanuit een ander proces. Elke p95 is de mediaan van de p95 van 3 rondes, in ms.

| Profiel | zoeken p95 / p99 / max | ronde B, C | volgende pagina's p95 | filters p95 | suggesties p95 | snip p95 |
|---|---|---|---|---|---|---|
| eigenaar | 73,4 / 107,3 / 120,8 | 0, 0 | 73,1 | 49,3 | 14,9 | 10,3 |
| ontvanger (5 delingen) | 80,8 / 118,0 / 129,3 | 0, 0 | 88,8 | 65,0 | 24,9 | 10,6 |
| ontvanger (20 delingen) | 85,8 / 129,4 / 136,3 | 0, 0 | 94,9 | 76,7 | 30,3 | 11,1 |
| groepslid | 81,9 / 115,8 / 129,4 | 0, 0 | 81,2 | 60,4 | 23,9 | 11,1 |
| gast | 72,3 / 118,0 / 153,7 | 0, 0 | 53,5 | 25,1 | 24,6 | 6,3 |
| alles (30 hoofdmappen) | 126,7 / 165,1 / 184,7 | 9, 0 | 134,1 | 58,7 | 30,8 | 8,7 |
| **poort** | **p95 ≤ 200** (p99 ≤ 300 rapport) | rapport | **≤ 200** | **≤ 150** | **≤ 50** | **≤ 40** |

| Wat | Poort | Gemeten | Uitkomst |
|---|---|---|---|
| 4 clients tegelijk | p95 ≤ 300 ms | p95 209,4, p99 244,5, max 277,8 ms | gehaald |
| `GET /health` tijdens die 4 | p95 ≤ 50 ms | 8,5 ms | gehaald |
| eerste vraag na een herstart | rapport (doel ≤ 300 ms) | 85,5 ms | — |
| `/photos/memories` | p95 ≤ 50 ms | 27,0 ms (34,1, 27,0, 16,5); p99 34,9 | gehaald |
| `/photos/map`, 55.011 foto's met een plek | p95 ≤ 150 ms | 51,9 ms (75,0, 51,6, 51,9); p99 78,7; `map/items` 44,4 | gehaald |
| de kaart met een filter op datum, plaats of land (`photo_geo`) | p95 ≤ 150 ms | 110,4 ms | gehaald |
| de kaart met een filter op soort, map of camera (de join op `node`) | p95 ≤ 500 ms | 430,0 ms (437,5, 430,0, 275,1) | gehaald |
| `/duplicates`, 55.000 foto's | p95 ≤ 300 ms | 192,7 ms (193,2, 192,7, 190,8); p99 204,1 | gehaald |

Elke poort van §4.4 gehaald; alle 13 jobs van die run waren groen. De hoogste p95 van zoeken over
de profielen is 126,7 ms ("alles", het ergste geval, met 9 keer ronde B). Rapporten uit dezelfde
job: de herbouw van 100.000 in 51 stukken, het langste 184 ms, met 202 uploads ernaast (mediaan 55 ms,
max 255 ms); de boom van 100.000 in 69,7 s.

## M2: de backfill van de tekst (§4.5)

`e2e/perf/zoek_backfill.py --n 2000 --keer 3 --soort beide --core 1`: 2.000 gemaakte documenten
(40% PDF, 30% docx, 10% xlsx, 10% pptx, 10% txt, html en rtf), de hele keten met het starten van
de kinderen en het schrijven.

| Weg | Per minuut per core (3 runs) | Mediaan | Poort | Uitkomst |
|---|---|---|---|---|
| met de worker | 1.972,5 / 1.980,9 / 1.977,1 | **1.977** | ≥ 1.000 | gehaald |
| de terugval zonder media | 4.709,1 / 4.816,8 / 4.832,3 | **4.817** | rapport | — |

De terugval is sneller dan de worker, zoals §4.5 verwachtte ("iets sneller", batches van 50).

## M3: photometa (§4.5)

`zoek_backfill.py --soort photometa --n 5000 --keer 3 --core 1`: 5.000 JPEG's met EXIF (4.250 met
een plek), GPS, camera en pHash in één keer (photometa v2).

| Runs | Mediaan | Poort | Uitkomst |
|---|---|---|---|
| 13.405,7 / 13.801,3 / 13.713,3 foto's per minuut per core | **13.713** | ≥ 5.000 | gehaald |

Elke run: 5.000 gehasht, 4.250 met GPS, 0 mislukt.

## M4: land en plaats (§4.5)

`zoek_backfill.py --soort geo --n 55000 --keer 3 --core 1` met de echte bronnen
(`places.sqlite` met 144.521 plaatsen en 258 landen, gebouwd in 3,3 s, buiten de meting).

| Runs | Mediaan | Poort | Uitkomst |
|---|---|---|---|
| 44.397,5 / 44.494,8 / 45.922,9 punten per minuut per core | **44.495** | ≥ 20.000 | gehaald |

Van de 55.000 punten kregen er 42.169 een land en 39.025 een plaats; de rest ligt op zee of verder
dan 30 km van een plaats.

## M5: OCR (§4.5)

De poort van X1 (`python -m tests.ocr.poort`, `taskset -c 0`), 3 keer: 100 brieven (0°, 3°, 7°,
−5° en 90°, elk 20) en 30 volle pagina's van 44 regels (0°, 3° en 90°, elk 10), A4 op 300 dpi.

| Run | Brieven per minuut | Volle pagina's per minuut | Over de mix | Poort | Uitkomst |
|---|---|---|---|---|---|
| 1 | 38,0 | 12,9 | 26,2 | ≥ 8 | gehaald |
| 2 | 24,8 | 11,6 | 19,6 | ≥ 8 | gehaald |
| 3 | 32,8 | 11,7 | 23,2 | ≥ 8 | gehaald |

Mediaan over de mix: **23,2 pagina's per minuut**. Per hoek (run 3): brieven 0° 47,7, 3° 41,7,
7° 40,5, −5° 38,7 en 90° 17,5 per minuut; volle pagina's 0° 17,9, 3° 18,0 en 90° 6,9. Een
pagina op zijn kant kost OSD (elke pagina van 90°, nooit een andere): de herkenning duurt dan
±3× zo lang. De recall was op elke pagina 1,0. In run 2 waren de brieven van 0° trager (14,1 per
minuut); de andere hoeken van die run niet, en de poort haalt hij ruim.

## M6: schrijven met kopieën (§4.5)

`test_backfill.py`, `test_search_rebuild.py` en `test_search_purge_batches.py` met `-s`, elk 3
keer op de host (SQLite 3.53.1) en in `python:3.12-slim` (3.46.1), na D1. De poort: een upload
naast de index wacht ≤ 1 s, ook als de index een tekst van 1 MB met 10 kopieën schrijft.

| Waar | Langste upload (3 runs) | Schrijven van 1 MB bij 11 nodes | Wissen (los / legen) | Uitkomst |
|---|---|---|---|---|
| host | 241 / 263 / 204 ms | 245–284 ms | 105–121 / 102–122 ms | gehaald |
| `python:3.12-slim` | 137 / 337 / 277 ms | 226–303 ms | 131–144 / 124–142 ms | gehaald |
| host, elke `fsync` 200 ms trager | 731 ms | 819 ms | 718 / 739 ms | gehaald |

De langste wachttijd van een upload was **337 ms** (in `python:3.12-slim`), en 739 ms met een
schijf die bij elke `fsync` 200 ms wacht (een shim met `LD_PRELOAD`, alleen in de sandbox). De
herbouw in stukken: het langste stuk 27–46 ms bij 7 stukken en 120–185 ms bij 27 stukken; de
langste upload ernaast 73–217 ms. Alle 20 tests slaagden in elke run.

## M7: zoeken naast media en OnlyOffice (§4.5, deel B)

`e2e/perf/zoek_samen.py --office --sessies 2 --bewerkers 3 --duur 300 --docs 5000`, met de echte
DS (`e2e/office_ds.sh`, grens 2 GiB) en 5.000 foto's en 500 scans ernaast, drie keer, alleen op de
machine. Elke run heeft drie stappen van 5 minuten: `pauze` (de nulmeting, het achtergrondwerk
staat stil), `achtergrond` (de poort: de pauze gaat eraf zodra de belastingtest loopt) en
`zonder_defer` (een rapport: 5.000 documenten erbij, en het achtergrondwerk wijkt niet voor media).
Er waren vier rondes. De eerste drie faalden, en elke keer lag de oorzaak bij de testopzet en niet
bij de app; de grenzen zijn in geen enkele ronde veranderd.

**Ronde 1 (M7, `77292fe`).** Niet gehaald, maar niet te lezen: de bewerkers begonnen in geen
enkele stap (ze wachtten op een stille machine), de gezaaide documenten hadden geen PDF zodat
OnlyOffice in elke stap ±2.700 bestanden omzette, en `GET /files/root` in het script vroeg de hele
hoofdmap op (stap 2: p95 567–662 ms). Hersteld in `0a2705d` (de bewerkers typen zodra de
belastingtest loopt, de documenten liggen klaar zoals op een 0.7.x met OnlyOffice, het script vraagt
`?limit=200` zoals de web-app, een token per verzoek, en alleen een volledige ronde telt).

**Ronde 2 (M7b, `0a2705d`).** OnlyOffice haalde zijn poorten (opslaan 8,9–9,0 s tegen 8,6–9,2 s in
de nulmeting, 0 verloren woorden), maar zoeken (p95 303–378 ms), `files_root` (407–445 ms) en
media (6–10 keer haperen in fase a) niet. Het hele script liep onder `taskset -c 0-2`: de app deelde
de cores van ffmpeg en de bewerkers, en de vierde core bleef bijna leeg. Zo staat het niet op de
LXC (`compose.media-limits.yaml` begrenst alleen de worker, `cpus: 3`) en zo mat fase 5 het ook niet
(regel 14). Hersteld in `ad71b8c`: het script zet de worker en zijn kinderen op 0-2, de app niet.

**Ronde 3 (M7c, `ad71b8c`).**

| Stap 2 (`achtergrond`), drie runs | Waarde | Poort |
|---|---|---|
| zoeken p95 | 250 / 265 / 273 ms | ≤ 200 ms: niet gehaald |
| `GET /files/root?limit=200` p95 | 292 / 314 / 334 ms | ≤ 200 ms: niet gehaald |
| `GET /health` p95 | 10,6 / 9,0 / 11,4 ms | ≤ 50 ms: gehaald |
| media | 2 / 2 / 2 keer haperen in fase a; de API van de belastingtest 499–522 ms | niet gehaald |
| OnlyOffice opslaan | 9,1 / 9,6 / 10,4 s tegen 9,5 / 9,4 / 8,9 s in de nulmeting (hoogstens 1,17×) | ≤ 1,2×: gehaald |
| verloren woorden | 0 | gehaald |
| `MemAvailable` (laagst) | 12.483 MB | ≥ 1,5 GiB: gehaald |
| DS `OOMKilled`, dezelfde processen | nee, ja | gehaald |
| het achtergrondwerk hervat | 6,6 / 22,9 / 26,9 s | ≤ 90 s: gehaald |

Ook de nulmeting zonder achtergrondwerk haalde zoeken net niet (p95 208–209 ms) en `files_root`
niet (230–285 ms), en de API van de belastingtest stond in elke stap boven de 440 ms. Dat wees op
iets dat in alle drie de stappen meeliep.

**De diagnose.** Een eigen script (in de sandbox, niet in de repository) draaide alleen de app, de
zoekers van `zoek_samen` en een poller die doet wat `Watch.sample` van de belastingtest deed: elke
seconde `GET /api/v1/files/root` zonder limit en `GET /api/v1/media/home`. Zonder media en zonder
DS, 90 s per variant, drie keer:

| Variant | zoeken p95 | `files_root?limit=200` p95 | CPU van de app | de poller, `files/root` p50 / p95 |
|---|---|---|---|---|
| 5.000 documenten in de hoofdmap, geen poller | 22–25 ms | 38–42 ms | 0,06–0,07 core | — |
| idem, met de poller | 80–129 ms | 111–144 ms | 0,24 core | 217–225 / 284–329 ms |
| dezelfde documenten in een map `Documenten`, geen poller | 24–29 ms | 20–26 ms | 0,05–0,06 core | — |
| idem, met de poller | 24–29 ms | 20–26 ms | 0,07–0,08 core | 13–15 / 18–27 ms |

De belastingtest zelf vroeg dus elke seconde de lijst van 5.000 bestanden op, en die kostte de app
±220–320 ms op zijn event loop, ook zonder media; die tijd ging af van de spelers en van elke vraag
ernaast. Met de documenten in een submap verdwijnt het effect helemaal. De belastingtest is van
fase 5, met een hoofdmap van ±10 bestanden; daar was het niet te zien.

**Het herstel (`7992c1c`, `329e707`).** De probe van de belastingtest vraagt nu de eerste pagina,
`GET /api/v1/files/root?limit=200`, zoals de web-app een map opent (`FIRST_PAGE` in
`web/src/browser/api.ts`); een test toetst dat de paden de echte routes zijn en 200 geven. De grens
van 200 ms is gebleven (fase 5, §4.6 punt 6 heeft een noot). `zoek_samen` bewaart nu per stap ook de
spelers van de belastingtest (start, seek, haperen), zodat haperen naast het werk ernaast te leggen
is. Het herstel verklaart zoeken, `files_root` en de API van de belastingtest; het haperen in fase a
niet: dat zag fase 5 ook al met een bijna lege hoofdmap (fase 5, [metingen.md](../fase-5/metingen.md),
Bevinding 3: na een sprong start een nieuwe ffmpeg, en de speler heeft dan minder dan één segment
voorraad).

**Ronde 4 (M7d, `329e707`).**

| Stap 2 (`achtergrond`), drie runs | Waarde | Poort |
|---|---|---|
| zoeken p95 | 82,8 / 81,2 / 86,8 ms | ≤ 200 ms: gehaald |
| `GET /files/root?limit=200` p95 | 121,7 / 118,7 / 135,3 ms | ≤ 200 ms: gehaald |
| `GET /health` p95 | 8,1 / 7,7 / 6,8 ms | ≤ 50 ms: gehaald |
| media | elke start ≤ 2,6 s; 3 / 2 / 0 keer haperen in fase a, 0 in fase b; de API van de belastingtest 86–91 ms | niet gehaald (twee van de drie runs) |
| OnlyOffice opslaan | 8,9 / 8,8 / 9,3 s tegen 8,5 / 8,6 / 8,8 s in de nulmeting (hoogstens 1,06×) | ≤ 1,2×: gehaald |
| verloren woorden | 0 | gehaald |
| `MemAvailable` (laagst) | 12.488 MB | ≥ 1,5 GiB: gehaald |
| DS `OOMKilled`, dezelfde processen | nee, ja | gehaald |
| het achtergrondwerk hervat | 20,8 / 16,2 / 20,3 s | ≤ 90 s: gehaald |

Na het herstel van ronde 3 zitten zoeken, `files_root` en de API van de belastingtest ruim onder hun
grens, ook in de nulmeting (58–70 ms en 85–103 ms) en in het rapport `zonder_defer` (109–121 ms en
132–171 ms). Alleen media faalt nog, en alleen op haperen in fase a: 0 / 0 / 1 keer in de nulmeting,
3 / 2 / 0 in `achtergrond` en 4 / 7 / 9 in `zonder_defer`. Fase b haperde in geen van de negen stappen.

**Waarom fase a hapert.** Drie onderzoekers en een rechter lazen de rapporten, de CPU-tellingen en de
code (zekerheid: middel; drie runs per stap, altijd in dezelfde volgorde):

1. De hoofdoorzaak is fase 5 Bevinding 3. In fase a springt elke speler na 60 s 90 s vooruit
   (`api/app/media/loadtest.py`). Valt de sprong buiten wat al klaar is, dan start de stroom opnieuw
   en heeft de speler minder dan één segment van 2 s voorraad. De startvoorrang van Keuze 26 geldt
   alleen voor de eerste start van een stroom, dus de andere stroom blijft op volle kracht werken
   tot hij 60 s voorsprong heeft. Het onderscheid in M7d: a2 (720p) had bij zijn eigen sprong een
   treffer in de cache (0,01 s) in precies de drie runs zonder haperen, en startte opnieuw (1,4–2,5 s)
   in alle zes runs die haperden.
2. Het verergert door drukte op cores 0-2 in de eerste minuut van fase a: de editors openen, en in
   `achtergrond` loopt de herbouw van de foto-hashes. Photometa hield zich daarbij niet aan P7: zijn
   renderprocessen draaiden op nice 0, boven live transcoderen op nice 5.
3. In `zonder_defer` is er aanhoudende drukte van de index en OCR. Dat is hetzelfde mechanisme onder
   meer last, en voor een stap zonder Defer verwacht.

**Het herstel na M7d.**

- **Herstartvoorrang** (B71, voorstel; `4303800`, tests in `b4a93c9` en `609bee8`). Na elke herstart
  van een stroom (een sprong buiten de cache, een nieuwe poging van Keuze 46, verder na idle) lezen
  de andere producenten hooguit `PRIORITY_MS` (10 s) vooruit, tot de nieuwe run 10 s media maakte,
  hooguit `PRIORITY_S` (20 s). Er is geen nieuwe constante. De eerste start van een stroom houdt de
  startvoorrang van Keuze 26.
- **Photometa op nice 19** (P7; `66e5565`, `30c17d1`). Het renderproces van photometa draaide op
  nice 0, boven live transcoderen op nice 5. Nu draait het op 19. Maar nice weegt alleen binnen één
  cpu-cgroup, en met autogroup (zo staat de sandbox) alleen binnen één sessie. Het renderkind en
  ffmpeg hebben elk een eigen sessie, en in de standaard installatie draait de worker in een eigen
  container. Gemeten op één core, nice 5 tegen nice 19: in één sessie 95,7 / 4,3 %, in twee sessies
  50 / 50. Hier en in de standaard installatie wijkt photometa dus niet voor live transcoderen. Of
  photometa tijdens een herstart wacht (Defer), is nog open.
- **Wanneer de speler hapert** (`6db0fcf`, `9ac71be`, `98ec2bc`). Het rapport van elke speler noemt
  nu elk moment van haperen (`stall_at`, `stall_after_seek`) en de voorraad direct na de sprong
  (`seek_buffer_s`). Dat is alleen om te lezen: `judge()` en de poorten bleven byte voor byte gelijk.

**De proefmetingen.** Alleen de belastingtest van media (2 sessies, 300 s, zonder zoeken en zonder
OnlyOffice), afwisselend zonder herstartvoorrang (`2998b39`) en met (`609bee8`). In E0 liep er
niets naast, in E2 vanaf de eerste live transcode 50 s een volle core op nice 0 op cores 0-2 (zoals
photometa vóór de nice).

| Proef | zonder herstartvoorrang | met herstartvoorrang |
|---|---|---|
| E0, niets ernaast, 4 + 4 runs | 0 keer haperen; elke sprong een treffer in de cache | 0 keer haperen; elke sprong een treffer in de cache |
| E2, een volle core ernaast, 3 + 3 runs | 1 keer haperen in fase a (a2) | 4 keer haperen in fase a (a1 3, a2 1) |

Fase b haperde in geen van de 14 runs. In E0 is elke sprong een treffer: de sprong van 90 s is
precies `AHEAD_MS` (30 s) plus `LEAD_MS` (60 s), dus zonder drukte staat het doel al klaar.

**Het haperen in E2 komt door waar de sprong in het rooster valt.** Het rooster begint op 21 ms
(de priming van AAC): segment 0 loopt van 0 tot 2,021 s, segment k begint op 2k + 0,021 s. De
speler springt na 60 s spelen 90 s vooruit en kijkt elke 0,1 s. Valt die kijk in de eerste 21 ms na
60 s, dan ligt het doel in segment 74 (148,021–150,021 s) met hooguit 21 ms voorraad; anders ligt
het in segment 75 met bijna 2 s. Komt het volgende segment dan van een herstart, dan moet het er
binnen ±0,2 s zijn: de speler telt het haperen pas bij zijn tweede tik. Dat is 2 s media in 0,2 s,
±10 keer realtime, en dat haalt geen herstart op deze machine (zonder drukte 3,0–3,4 keer).

- In E2 landden 5 sprongen in segment 74 met een herstart erachter, en die haperden alle 5 (0,2–0,6
  s). De 7 sprongen in segment 75 haperden geen enkele keer.
- In E0 landden 2 sprongen in segment 74 met het volgende segment al in de cache: geen haperen.
- 7 van de 28 sprongen in fase a landden in segment 74, zoals de kans van ±21 % per sprong voorspelt.
- Het verschil tussen met en zonder herstartvoorrang (4 tegen 1) is precies het aantal sprongen in
  segment 74 (4 tegen 1). Over de voorrang zegt het dus niets; die kan hier ook niet helpen.

Een browser telt dit niet: `web/src/player/mse.ts` telt een wachten binnen 2 s na `seeked` niet als
haperen. De virtuele speler is strenger, zoals fase 5 bedoelt ("zonder haperen is hier strenger dan
in een browser", [bouwontwerp](../fase-5/bouwontwerp.md) §4.7). Wat de poort telt, blijft zo tot
Jonas kiest.

Voor M7d betekent dit: er is één sprong per speler per stap, dus hooguit 7 van de 26 keer haperen
kunnen zo komen. De rest (minstens 19 keer, minstens 9,5 van de 16,3 s) is te weinig productie na de
herstart onder drukte: de runs van a1 die haperden liepen op 1,02–1,23 keer realtime, die zonder
haperen op 1,27–1,71. M7d draaide vóór de herstartvoorrang en vóór de nice van photometa.

**Ronde 5 (M7e, `98ec2bc`).** Dezelfde code als de merge `47d62eb`; alleen een commentaar verschilt.

| Stap 2 (`achtergrond`), drie runs | Waarde | Poort |
|---|---|---|
| zoeken p95 | 46,6 / 54,1 / 48,9 ms | ≤ 200 ms: gehaald |
| `GET /files/root?limit=200` p95 | 80,9 / 76,2 / 87,8 ms | ≤ 200 ms: gehaald |
| `GET /health` p95 | 5,1 / 5,2 / 6,1 ms | ≤ 50 ms: gehaald |
| media | elke start ≤ 2,6 s; 0 keer haperen in fase a en b; de API van de belastingtest 54–56 ms | gehaald |
| OnlyOffice opslaan | 7,8 / 7,8 / 7,7 s tegen 8,2 / 7,9 / 7,8 s in de nulmeting (hoogstens 0,99×) | ≤ 1,2×: gehaald |
| verloren woorden | 0 | gehaald |
| `MemAvailable` (laagst) | 12.434 MB | ≥ 1,5 GiB: gehaald |
| DS `OOMKilled`, dezelfde processen | nee, ja | gehaald |
| het achtergrondwerk hervat | 18,3 / 22,8 / 20,8 s | ≤ 90 s: gehaald |

M7e haalt elke poort, drie van de drie keer. In de nulmeting haperde geen speler. In
`zonder_defer` (een rapport, geen poort) haperde a1 één keer in run 1 en één keer in run 2, elk
0,2 s na de sprong, met 0,067 en 0,012 s voorraad: precies het geval uit het rooster hierboven. a2
landde drie keer met hooguit 0,012 s voorraad, maar startte niet opnieuw: zijn volgende segment
was er op tijd, en hij haperde niet.

Te weinig productie zoals in M7d is niet meer gezien: geen speler haperde meer dan één keer, en a1
liep op 1,35–2,04 keer realtime. Waardoor dat komt, zegt M7e niet, want elke run had zowel de
herstartvoorrang als de nice van photometa. De proefmetingen zonder herstartvoorrang (E2) haperden
ook niet door te weinig productie, de nice weegt hier niet (zie hierboven), en ook de nulmeting,
waar geen van beide iets doet, was sneller dan in M7d (a1 op 2,03–2,04 tegen 1,27–1,71 keer
realtime, bij een gemiddelde load van de machine van 5,5 tegen 6,6). Het kan dus ook verschil
tussen runs zijn.

De poort blijft gevoelig voor het geval uit het rooster. a1 startte in M7e bij elke sprong
opnieuw, en ±1 op 4 sprongen landt in het laatste stukje van een segment. In een ronde van drie
runs is de kans op minstens één keer haperen in `achtergrond` daarom ±40–60 %, zonder dat de
server iets fout doet; dat M7e drie keer goed ging, is dus deels geluk. CI merkt het niet: daar
draait `zoek_samen --kort` alleen fase b (geen sprong), en de test van de belastingtest springt
nooit zo dicht bij het eind van een segment. Wat de meting hier telt, is een vraag aan Jonas
(kaartje van 11 november 2026); tot Jonas kiest, blijft het zoals het is.

## M8: rapporten

⟦C2: zoek_ui, bench --los, het bijwerken van de index in CI, de grootte van het image, de duur van
e2e-media-perf en het blok kort uit CI⟧

## M9: plaatsen na de update (I1, A4)

`api/tests/geo/test_places_later_echt.py` (marker `geo`, in de job `api`) met de echte bronnen:
248 foto's met GPS (bij echte plaatsen tot ±25 km, verspreid over de wereld, de grensparen en de
Noordzee van Z0, en 180°), gelezen zonder `places.sqlite`, zoals na de update; dan de bouw zoals
bij de start en de keten `geo.places` in stukken van 37. Daarna heeft elke rij precies wat
`lookup()` op zijn eigen lengte en breedte zegt in een `places.sqlite` die apart uit dezelfde
bronnen gebouwd is. 193 foto's liggen op land, 179 kregen een plaats, 55 liggen op zee of hebben
geen land. Het lezen duurde 45,5 s, de bouw 3,1 s en de keten 0,26 s in 7 stukken.

## Gezichten en beeld (0.8.1)

De rijen van X2 en X3 in §4.5 (gezichten en beeld in de container) horen bij 0.8.1 en worden in I1
niet gemeten.

## Bevindingen

⟦C2: de bevindingen uit M1–M9, na M7 en M8⟧

## Wat alleen de LXC of CI kan laten zien

- `bench --los` op de CPU van Jonas: de poort van 200 ms geldt op de LXC (runbook
  [zoeken.md](../runbooks/zoeken.md)).
- M7 met de echte verdeling van de LXC: de worker in zijn container met `cpus: 3`, de app en DS in
  de hunne, en de bewerkers op andere computers. In de sandbox draaien de bewerkers (Chromium) op
  dezelfde 4 vCPU als de server.
- De kwaliteit van gezichten en beeld op echte foto's (0.8.1).
- De jobs met het image: de grootte (`images`) en `--stappen` (`media-image`).

## Opnieuw meten

Elke meting alleen op de machine, vanuit `api/` (M6 in `python:3.12-slim` vanuit de wortel van
de repository). Het blok `=== kort ===` aan het eind van elke uitvoer is wat hier staat.

```bash
# M1: de job zoek-perf in CI (§4.4); met de hand:
uv run pytest -q -s -m zoek_perf tests/search --durations=0 -rA
# M2, M3 en M4: de backfill op één core
taskset -c 1 uv run python ../e2e/perf/zoek_backfill.py --n 2000 --keer 3 --soort beide --core 1
taskset -c 1 uv run python ../e2e/perf/zoek_backfill.py --soort photometa --n 5000 --keer 3 --core 1
taskset -c 1 uv run python ../e2e/perf/zoek_backfill.py --soort geo --n 55000 --keer 3 --core 1 \
  --geo-bron <map met de bronnen van places.sqlite>
# M5: de poort van X1 op core 0, zoals de job ocr
taskset -c 0 uv run python -m tests.ocr.poort --json /tmp/ocr-poort.json
# M6: schrijven met kopieën, op de host (en in python:3.12-slim met e2e/slim.sh)
taskset -c 0-2 uv run pytest -q -s tests/test_backfill.py tests/search/test_search_rebuild.py \
  tests/search/test_search_purge_batches.py
# M7: niet onder taskset (het script zet de worker zelf op 0-2); de DS zoals in het runbook
E2E_REAL_DS=http://127.0.0.1:18150 E2E_REAL_DS_SECRET_FILE=<map>/jwt.secret \
  uv run --with playwright python ../e2e/perf/zoek_samen.py --office --sessies 2 --bewerkers 3 \
  --duur 300 --docs 5000 --json /tmp/zoek-samen.json
# M9: de update met de echte bronnen (marker geo)
uv run pytest -q -m geo tests/geo/test_places_later_echt.py
```

⟦C2: de opdrachten van M8⟧
