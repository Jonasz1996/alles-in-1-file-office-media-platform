# Metingen fase 6 (I1)

De metingen van §4.4 en §4.5 van het [bouwontwerp](bouwontwerp.md) bij de integratie I1 (0.8.0).
Alle getallen staan ook in [metingen.json](metingen.json). De scripts staan in [e2e.md](e2e.md); de
uitleg bij de uitkomst in [integratie.md](integratie.md). Wat niet gehaald werd, is niet bijgesteld
om te slagen; de oorzaak staat bij elke meting en onder [Bevindingen](#bevindingen).

## Kort

Elke poort van §4.4 en §4.5 die bij 0.8.0 hoort, is gehaald: 27 van de 27, en ook de grens voor de
grootte van het image (X1). De vier poorten van gezichten en beeld (X2 en X3) horen bij 0.8.1 en
zijn niet gemeten. M7 haalde het pas in de vijfde ronde (M7e); de rondes ervoor staan bij
[M7](#m7-zoeken-naast-media-en-onlyoffice-45-deel-b). Bij een poort over meer runs staat bij M1 tot
en met M5 de mediaan van de 3 rondes of runs, bij M6 en M7e de slechtste waarde. Bij M2 tot en met
M5 staat elke run bij de meting zelf. Bij M1 staan de rondes alleen bij de herinneringen, de kaart,
de kaart met een filter op soort, map of camera en `/duplicates`.

| Poort | Grens | Gemeten | Meting | Uitkomst |
|---|---|---|---|---|
| §4.4 zoeken p95, elk toegangsprofiel | ≤ 200 ms | 72,3–126,7 ms (het ergste: "alles") | M1 | gehaald |
| §4.4 volgende pagina's p95 | ≤ 200 ms | hoogstens 134,1 ms | M1 | gehaald |
| §4.4 alleen filters p95 | ≤ 150 ms | hoogstens 76,7 ms | M1 | gehaald |
| §4.4 suggesties p95 | ≤ 50 ms | hoogstens 30,8 ms | M1 | gehaald |
| §4.4 `snip` p95 | ≤ 40 ms | hoogstens 11,1 ms | M1 | gehaald |
| §4.4 4 clients tegelijk p95 | ≤ 300 ms | 209,4 ms | M1 | gehaald |
| §4.4 `GET /health` tijdens die 4 | ≤ 50 ms | 8,5 ms | M1 | gehaald |
| §4.4 `/photos/memories` p95 | ≤ 50 ms | 27,0 ms | M1 | gehaald |
| §4.4 `/photos/map` p95, 55.011 foto's | ≤ 150 ms | 51,9 ms | M1 | gehaald |
| §4.4 de kaart met een filter op datum, plaats of land | ≤ 150 ms | 110,4 ms | M1 | gehaald |
| §4.4 de kaart met een filter op soort, map of camera | ≤ 500 ms | 430,0 ms | M1 | gehaald |
| §4.4 `/duplicates` p95, 55.000 foto's | ≤ 300 ms | 192,7 ms | M1 | gehaald |
| §4.5 tekst met de worker | ≥ 1.000 per minuut per core | 1.977 | M2 | gehaald |
| §4.5 photometa | ≥ 5.000 per minuut per core | 13.713 | M3 | gehaald |
| §4.5 land en plaats | ≥ 20.000 per minuut per core | 44.495 | M4 | gehaald |
| §4.5 OCR | ≥ 8 pagina's per minuut | 23,2 over de mix | M5 | gehaald |
| §4.5 een upload naast de index | ≤ 1 s | 337 ms | M6 | gehaald |
| §4.5 media naast zoeken en OnlyOffice | elke start ≤ 5 s, 0 keer haperen | elke start ≤ 2,6 s, 0 keer haperen | M7e | gehaald |
| §4.5 zoeken p95 | ≤ 200 ms | 54,1 ms | M7e | gehaald |
| §4.5 `GET /files/root?limit=200` p95 | ≤ 200 ms | 87,8 ms | M7e | gehaald |
| §4.5 `GET /health` p95 | ≤ 50 ms | 6,1 ms | M7e | gehaald |
| §4.5 `MemAvailable` | ≥ 1,5 GiB | 12.434 MiB | M7e | gehaald |
| §4.5 geen container `OOMKilled` | DS met een grens van 2 GiB | nee | M7e | gehaald |
| §4.5 de app en de worker leven nog | dezelfde processen | ja | M7e | gehaald |
| §4.5 het achtergrondwerk hervat | ≤ 90 s na de laatste transcode | 22,8 s | M7e | gehaald |
| §4.5 OnlyOffice opslaan | ≤ 1,2 × de nulmeting | 0,99 × | M7e | gehaald |
| §4.5 OnlyOffice verloren woorden | 0 | 0 | M7e | gehaald |
| X1 tesseract in het image | ≤ 75 MB erbij | 71,6 MB | M8 | gehaald |

De rapporten zonder poort (de terugval van de tekst, de p99, het zoekscherm, `bench --los`, het
bijwerken van de index) staan bij M1, M2 en M8.

## Omstandigheden

- **Machine:** de sandbox, een Firecracker-VM met 4 vCPU (Intel Xeon @ 2,10 GHz), 15,7 GiB RAM,
  geen swap, geen GPU. SQLite 3.53.1 op de host en 3.46.1 in `python:3.12-slim` (Python 3.12.16);
  ffmpeg 6.1.1 (Ubuntu); tesseract 5.3.4.
- **De commits:** M2–M4 en run 1 van M5 op `d5624b5`; run 2 en 3 van M5 op `77292fe`, na een
  herstart van de sandbox (die commit voegt alleen een `print` toe aan een test, de app is
  dezelfde); M6 op `d5624b5`, met die `print` al in de werkboom; M9 op `d5624b5` (die commit is M9);
  M7 op `98ec2bc` (M7e, de laatste ronde; de rondes ervoor staan bij M7), M8 op `47d62eb` (dezelfde
  code als `98ec2bc`, op een commentaar na). M1 komt uit CI (run 102 op `0e19180`); de getallen uit
  CI in M8 komen uit run 105 op `47d62eb`.
- **De cores:** elke meting liep alleen. M2, M3 en M4 op één core (`taskset -c 1` en
  `--core 1`), M5 op core 0 (`taskset -c 0`, zoals de job `ocr`), M6 op 0-2 (`taskset -c 0-2` op
  de host, `--cpuset-cpus 0-2` in `python:3.12-slim`). M7: de worker op 0-2 en de app niet
  (regel 14 van fase 5; zie M7 voor de eerste rondes, die anders liepen).
- **Geheugen:** het minst vrije geheugen staat bij M7; bij de andere metingen in de sandbox is het
  niet gemeten. `MemAvailable` staat overal in MiB: `zoek_samen.py` rekent in MiB, al schrijft het
  "MB".

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

Elke poort van §4.4 gehaald; alle 16 jobs van die run waren groen. De hoogste p95 van zoeken over
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
| `python:3.12-slim` | 173 / 337 / 277 ms | 226–303 ms | 131–144 / 124–142 ms | gehaald |
| host, elke `fsync` 200 ms trager | 739 ms | 819 ms | 718 / 739 ms | gehaald |

De langste wachttijd van een upload was **337 ms** (in `python:3.12-slim`), en 739 ms met een
schijf die bij elke `fsync` 200 ms wacht (een shim met `LD_PRELOAD`, alleen in de sandbox). De
herbouw in stukken: het langste stuk 27–46 ms bij 7 stukken en 120–185 ms bij 27 stukken; de
langste upload ernaast 73–217 ms. Alle 20 tests slaagden in elke run.

## M7: zoeken naast media en OnlyOffice (§4.5, deel B)

`e2e/perf/zoek_samen.py --office --sessies 2 --bewerkers 3 --duur 300 --docs 5000`, met de echte DS
(`e2e/office_ds.sh`, grens 2 GiB) en 5.000 foto's en 500 scans ernaast, drie keer, alleen op de
machine. Elke run heeft drie stappen; in elke stap draait de belastingtest fase a en daarna fase b
(`--duur 300` is de duur per fase), ±9–11 minuten per stap en ±32–34 minuten per run: `pauze` (de
nulmeting, het achtergrondwerk staat stil), `achtergrond` (de poort: de pauze gaat eraf zodra de
belastingtest loopt) en `zonder_defer` (een rapport: 5.000 documenten erbij, en het achtergrondwerk
wijkt niet voor media). Er waren vijf rondes. De eerste drie faalden vooral door de testopzet; de
derde (M7c) haperde daarnaast al 2 / 2 / 2 keer in fase a, zoals M7d. De vierde (M7d) faalde alleen
op het haperen in fase a, waarna de app en de belastingtest iets veranderden (zie "Het herstel na
M7d"); de vijfde (M7e) haalde elke poort, drie van de drie keer. De grenzen zijn in geen enkele
ronde veranderd.

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
| `MemAvailable` (laagst) | 12.483 MiB | ≥ 1,5 GiB: gehaald |
| DS `OOMKilled`, dezelfde processen | nee, ja | gehaald |
| het achtergrondwerk hervat | 6,6 / 22,9 / 26,9 s | ≤ 90 s: gehaald |

Ook de nulmeting zonder achtergrondwerk haalde zoeken net niet (p95 208–209 ms) en `files_root`
niet (230–285 ms), en de API van de belastingtest stond in elke stap boven de 440 ms. Dat wees op
iets dat in alle drie de stappen meeliep.

**De diagnose.** Een eigen script (in de sandbox, niet in de repository) draaide alleen de app, de
zoekers van `zoek_samen` en een poller die doet wat `Watch.sample` van de belastingtest deed: elke
seconde `GET /api/v1/files/root` zonder limit en `GET /api/v1/media/home`. Zonder media en zonder
DS, 120 s per variant, drie keer:

| Variant | zoeken p95 | `files_root?limit=200` p95 | CPU van de app | de poller, `files/root` p50 / p95 |
|---|---|---|---|---|
| 5.000 documenten in de hoofdmap, geen poller | 22–25 ms | 38–42 ms | 0,06–0,07 core | — |
| idem, met de poller | 80–129 ms | 111–144 ms | 0,24 core | 217–225 / 284–329 ms |
| dezelfde documenten in een map `Documenten`, geen poller | 24–29 ms | 21–26 ms | 0,05–0,06 core | — |
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
| `MemAvailable` (laagst) | 12.488 MiB | ≥ 1,5 GiB: gehaald |
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
| `MemAvailable` (laagst) | 12.434 MiB | ≥ 1,5 GiB: gehaald |
| DS `OOMKilled`, dezelfde processen | nee, ja | gehaald |
| het achtergrondwerk hervat | 18,3 / 22,8 / 20,8 s | ≤ 90 s: gehaald |

M7e haalt elke poort, drie van de drie keer. In de nulmeting haperde geen speler. In
`zonder_defer` (een rapport, geen poort) haperde a1 één keer in run 1 en één keer in run 2, elk
0,2 s na de sprong, met 0,067 en 0,012 s voorraad, minder dan één tik van 0,1 s: hetzelfde
mechanisme als in het rooster hierboven (0,067 s is meer dan de 21 ms daar; de speler stond bij die
sprong dus niet precies op 60 s). a2
landde drie keer met hooguit 0,012 s voorraad, maar startte niet opnieuw: zijn volgende segment
was er op tijd, en hij haperde niet.

Te weinig productie zoals in M7d is niet meer gezien: geen speler haperde meer dan één keer, en a1
liep op 1,35–2,04 keer realtime. Waardoor dat komt, zegt M7e niet, want elke run had zowel de
herstartvoorrang als de nice van photometa. De proefmetingen zonder herstartvoorrang (E2) haperden
ook niet door te weinig productie, de nice weegt hier niet (zie hierboven), en ook de nulmeting was
sneller dan in M7d. Daar doet de nice van photometa niets (het achtergrondwerk staat stil), en b1
springt niet en krijgt dus geen herstartvoorrang. b1 liep er op 1,73–1,77 tegen 1,53–1,62 keer
realtime, bij een gemiddelde load van de machine van 5,5 tegen 6,6. a1 liep er ook sneller
(2,03–2,04 tegen 1,27–1,71 keer realtime), maar a1 startte ook in de nulmeting bij de sprong opnieuw
en kreeg dus herstartvoorrang; bij a1 kan die meetellen. Het kan dus ook verschil tussen runs zijn.

De poort blijft gevoelig voor het geval uit het rooster. a1 startte in M7e bij elke sprong
opnieuw, en ±1 op 4 sprongen landt in het laatste stukje van een segment. In een ronde van drie
runs is de kans op minstens één keer haperen in `achtergrond` daarom ±40–60 %, zonder dat de
server iets fout doet; dat M7e drie keer goed ging, is dus deels geluk. CI merkt het niet: daar
draait `zoek_samen --kort` alleen fase b (geen sprong), en de test van de belastingtest springt
nooit zo dicht bij het eind van een segment. Wat de meting hier telt, is een vraag aan Jonas
(kaartje van 11 november 2026); tot Jonas kiest, blijft het zoals het is.

## M8: rapporten

Geen poort, behalve de grootte van het image. Het zoekscherm en `bench --los` liepen alleen in de
sandbox op `47d62eb`, na `npm run build` in `web/`; de rest komt uit CI, run 105 (id 43088438680) op
`47d62eb`, waarin alle 16 jobs groen waren.

**Zoeken in de browser** (`e2e/perf/zoek_ui.py`, Chromium, latency 2 ms, 100 Mbit/s, CPU 1×; de
boom van 10.000 bestanden met de index aan, als de eigenaar; 5 keer koud en 20 keer warm). In de
sandbox onder `taskset -c 0-2`, in CI in de job `perf`. Mediaan / p95 / max in ms, met tussen haakjes
de duur van het verzoek zelf (mediaan / p95):

| | sandbox | CI |
|---|---|---|
| eerste suggestie na een toets, koud (met de chunk) | 162,9 / 173,7 / 173,7 (17,7 / 23,5) | 163,0 / 171,4 / 171,4 (13,9 / 22,2) |
| eerste suggestie na een toets, warm | 141,9 / 144,9 / 145,1 (17,2 / 20,1) | 136,1 / 138,5 / 141,0 (13,3 / 15,2) |
| `/zoeken?q=` laden tot de eerste treffer getekend is | 529,2 / 584,5 / 584,5 (56,2 / 61,9) | 489,9 / 511,6 / 511,6 (51,1 / 63,6) |
| Enter in Zoekwoorden tot de eerste treffer getekend is | 61,8 / 77,1 / 95,5 (33,3 / 50,1) | 58,5 / 65,9 / 90,3 (32,8 / 35,7) |

Ongeveer wat Z4 mat in zijn slotmeting ([z4.md](z4.md), `ef8d7a9`: mediaan 155,2, 137,0, 471,5 en
51,0 ms). Warm is de eerste suggestie vooral de wachttijd van 120 ms na de laatste toets.

**`bench --los`** (`app.cli bench --los --n 100000 --seed 6` onder `taskset -c 0-2`, met een lege
tijdelijke datamap; SQLite 3.53.1 en Python 3.12.13 op de host): de boom van 100.000 (108.452 nodes,
233 M tekens) in 22,8 s en de bench in 37,9 s. Elk profiel kreeg 171 vragen:

| Profiel | p50 | p95 | max | ronde C | p95 zonder de tijdsgrens |
|---|---|---|---|---|---|
| eigenaar (1 hoofdmap, 6.623 zichtbaar) | 5,7 ms | 164,4 ms | 172,6 ms | 12 keer | 347,8 ms |
| alles (30 hoofdmappen, 97.022 zichtbaar) | 9,5 ms | 161,7 ms | 172,7 ms | 12 keer | 436,0 ms |

Onder de 200 ms van de LXC. In CI (`zoek-perf`, SQLite 3.46.1) gaf dezelfde bench 161,0 en
160,6 ms. De p95 komt van de 12 vragen met een voorvoegsel (zoals `ver*`): die raken de tijdsgrens en krijgen
hun antwoord uit ronde C (zie [Bevindingen](#bevindingen)).

**Het bijwerken van de index** (§4.5, een rapport; de stap S7 van de spike in `zoek-perf`, SQLite
3.46.1): 100.000 bestanden en 5.900 mappen met 230 M tekens via de triggers in 37,4 s, waarvan de
tekst 7,7 s en de nodes 25,3 s; 600 MB. Het ontwerp schatte ±26 s voor 20.000 teksten. De herbouw
van 100.000 in 51 stukken: het langste stuk 141 ms, de 202 uploads ernaast mediaan 27 ms en max
333 ms.

**Het image** (job `images`, platform): tesseract maakt het image 71,6 MB groter, in de bestanden
van 18 pakketten (dpkg Installed-Size 69,0 MiB; de spike mat 69 MiB, S5). De poort is ≤ 75 MB
erbij: gehaald.

**De duur in CI:** `e2e-media-perf` 7 min 37 s, `zoek-perf` 12 min 16 s.

**Het blok kort van `e2e-media-perf`** (`zoek_samen --kort`: 1 sessie media, alleen fase b, 60 s per
fase, 300 documenten per stap en 300 foto's, zonder OnlyOffice; 211 s in totaal). De 20 scans voor
X1 liepen niet mee: de runner heeft geen tesseract met `nld`, `eng` en `osd`.

| Stap | zoeken p95 | `files/root?limit=200` p95 | `/health` p95 | `MemAvailable` min | media (fase b) | uitkomst |
|---|---|---|---|---|---|---|
| `pauze` | 15,9 ms | 19,7 ms | 2,1 ms | 14.419 MiB | start 0,53 s, 0 keer haperen | geslaagd |
| `achtergrond` | 16,9 ms | 25,5 ms | 2,5 ms | 14.560 MiB | start 0,32 s, 0 keer haperen; hervat 0,5 s | geslaagd |
| `zonder_defer` | 28,7 ms | 31,7 ms | 2,8 ms | 14.528 MiB | start 0,68 s, 0 keer haperen | geslaagd |

Van de poorten van stap `achtergrond` zijn er zeven gehaald en drie niet te meten (`OOMKilled`,
opslaan en verloren woorden: er is geen OnlyOffice); het blok eindigt met "uitkomst: rapport".
`zoek_backfill` kort in dezelfde job: de tekst met de worker 1.934,6 per minuut per core (poort
≥ 1.000: gehaald), de terugval 5.133,2 (rapport).

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

Een grens die niet gehaald werd, is een bevinding: de oorzaak gezocht, en gerepareerd met een test
als het een fout in onze code is. Getallen zijn nergens bijgesteld om te slagen.

1. **De testopzet liet M7 drie rondes falen** (gerepareerd in `0a2705d`, `ad71b8c`, `7992c1c` en
   `329e707`). Ronde 1 en 2 door het script zelf (zie [M7](#m7-zoeken-naast-media-en-onlyoffice-45-deel-b)).
   In ronde 3 vroeg de belastingtest van fase 5 elke seconde de hele hoofdmap op: met 5.000
   documenten kostte dat de app ±220–320 ms per seconde op zijn event loop, en die tijd ging af van
   zoeken, `files_root` en de spelers. De probe vraagt nu de eerste pagina (`?limit=200`), zoals de
   web-app. Wat blijft: een lijst zonder pagina's (de openbare map achter een link, en
   `GET /files/root` of `/files/{id}/children` zonder `limit`) kost bij een grote map nog evenveel;
   dat staat bij de open punten in [integratie.md](integratie.md).
2. **Een speler hapert na een sprong als de stroom opnieuw start** (fase 5 Bevinding 3; deels
   aangepakt). M7c haperde 2 / 2 / 2 en M7d 3 / 2 / 0 keer in fase a van `achtergrond`, M7e 0 keer.
   Tussen M7d en M7e kwamen de herstartvoorrang (B71, een voorstel) en photometa op nice 19. Of het
   verschil daardoor komt, is niet bewezen (zie M7e).
3. **Een sprong die in het laatste stukje van een segment landt, hapert als het volgende segment van
   een herstart komt** (de server doet niets fout; in de server is het alleen te verhelpen door bij
   zo'n herstart het eerste segment pas te geven als het volgende ook klaar is, en dan duurt elke
   sprong met een herstart 0,6–1 s langer). De speler heeft dan bijna geen voorraad, en
   2 s media binnen ±0,2 s haalt geen herstart. ±1 op 4 sprongen landt zo; in een ronde van drie
   runs is de kans op minstens één keer haperen in `achtergrond` daarom ±40–60 %. Een browser telt
   dit niet (`mse.ts`). Wat de poort hier telt, is een vraag aan Jonas.
4. **Photometa wijkt niet voor live transcoderen** (open). Nice 19 weegt alleen binnen één sessie of
   cpu-cgroup, en het renderkind, ffmpeg en de worker in zijn eigen container zitten elk apart
   (gemeten: 50 / 50 in twee sessies). Of photometa tijdens een herstart moet wachten (Defer), is
   open.
5. **Een GPS-waarde met 60,0 seconden geeft geen plek** (de testfoto's gerepareerd in `d5624b5`; de
   app is een open punt voor I2). M9 vond het: `_dms` in de testfoto's gaf (54, 47, 60.0), en
   `render_meta` leest seconden alleen onder 60. Bij een camera is het niet gezien.
6. **Een pagina op zijn kant kost OCR ±3× zo veel** (M5, een rapport). Elke pagina van 90° gaat door
   OSD; over de mix haalt OCR de poort ruim (23,2 tegen 8 pagina's per minuut). De trage brieven
   van 0° in run 2 zijn niet verklaard.
7. **Een trage schijf** (M6). Met 200 ms extra per `fsync` wachtte een upload hooguit 739 ms, onder
   de poort van 1 s. Hoe snel de ZFS-schijf van de LXC is, is niet gemeten.
8. **De p95 van `bench --los` is de tijdsgrens** (M8, een rapport). De 12 vragen met een
   voorvoegsel (zoals `ver*`) raken de grens van ronde A en B en krijgen alleen namen uit ronde C;
   zonder de grens was de p95 van de profielen 347,8 en 436,0 ms.
9. **Het krapst bij §4.4** (M1): de kaart met een filter op soort, map of camera (de join op
   `node`), 430,0 tegen 500 ms; daarna de kaart met een filter op datum, plaats of land, 110,4 tegen
   150 ms, en 4 clients tegelijk, 209,4 tegen 300 ms. Het profiel "alles" (30 hoofdmappen) had 9
   keer ronde B nodig.
10. **CI ziet het haperen in fase a niet.** `zoek_samen --kort` in `e2e-media-perf` draait alleen
    fase b, zonder sprong en zonder OnlyOffice; fase a draait in CI alleen in `test_fase_a_en_b`
    (job `api`), met een nep-worker. Het haperen van punt 2 en 3 ziet alleen een volle M7.

## Wat alleen de LXC of CI kan laten zien

- `bench --los` op de CPU van Jonas: de poort van 200 ms geldt op de LXC (runbook
  [zoeken.md](../runbooks/zoeken.md)).
- M7 met de echte verdeling van de LXC: de worker in zijn container met `cpus: 3`, de app en DS in
  de hunne, en de bewerkers op andere computers. In de sandbox draaien de bewerkers (Chromium) op
  dezelfde 4 vCPU als de server.
- De kwaliteit van gezichten en beeld op echte foto's (0.8.1).
- De jobs met het image: de grootte (`images`) en `--stappen` (`media-image`).

## Opnieuw meten

Elke meting alleen op de machine, vanuit `api/` (M6 in `python:3.12-slim` vanuit de wortel van de
repository). Waar een script zijn uitvoer afsluit met het blok `=== kort ===` (M2–M4, M7 en het
zoekscherm), is dat blok wat hier staat; `bench --los` sluit af met `=== zoekbench ===`, en M1, M5
en M9 met hun eigen slotregels. M6 heeft geen blok aan het eind: de getallen komen uit de regels
`=== kort ===`, `herbouw:` en `=== wissen ===` midden in de uitvoer van de drie tests.

```bash
# M1: de job zoek-perf in CI (§4.4); met de hand:
uv run pytest -q -s -m zoek_perf tests/search tests/photos/test_memories_perf.py \
  tests/photos/test_map_perf.py tests/photos/test_dupes_perf.py --durations=0 -rA
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
# M8: het zoekscherm (na npm run build in web/) en bench --los met een lege datamap
taskset -c 0-2 uv run --with playwright python ../e2e/perf/zoek_ui.py --json /tmp/zoek-ui.json
PLATFORM_DATA_DIR=<lege map> taskset -c 0-2 uv run python -m app.cli bench --los --n 100000 --seed 6
# de rest van M8 staat in de blokken kort van de jobs zoek-perf, perf, images en e2e-media-perf in CI
```
