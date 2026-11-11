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

⟦C2: M7 na de laatste rondes⟧

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

⟦C2: de opdrachten per meting⟧
