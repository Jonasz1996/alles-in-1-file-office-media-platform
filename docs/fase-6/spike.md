# Spike Z0: de tijdsgrens, de triggers, de vormen van 4a en de zandbak

Datum: 3 november 2026. Volgens §3.7 van `docs/fase-6/bouwontwerp.md` (revisie 2). Tak
`wip/z0-spike`, op Z0-api (`7fcd92c`). Wat het ontwerp door de spike verandert, staat in het
bouwontwerp als "Herzien na de spike (Sx)".

Gemeten in de sandbox (4 cores, steeds `taskset -c 0-2`) op drie versies van SQLite:

| Waar | Python | SQLite |
|---|---|---|
| de host (`python3`) | 3.13 | 3.45.1 |
| de venv van `api` (`uv run`) | 3.12.13 | 3.53.1 |
| `python:3.12-slim` (Debian 13, de basis van het image) | 3.12.16 | 3.46.1 |

`python:3.12-slim` draaide zo, met de repo alleen-lezen en zonder netwerk:

```sh
docker run --rm --network none --cpuset-cpus 0-2 -v "$PWD:/repo:ro" -e HOME=/tmp \
  -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/repo/api/.venv/lib/python3.12/site-packages \
  -w /repo/api python:3.12-slim python -m pytest -q -p no:cacheprovider -s <tests>
```

S5 en S11 in de echte containers, en S1–S10 nog eens op 3.46.1, draaien in CI: de nieuwe job
`zoek-perf` (`python:3.12-slim`) en de job `media-image`. Beide eindigen met een blok
`=== kort ===` in het log. Op deze tak heeft CI nog niet gedraaid (er is niet gepusht); de
getallen "in CI" komen bij de eerste run. S5 is alleen in CI te meten: `apt` in docker geeft
hier 403.

**Kort.** Alle elf stappen hebben een uitkomst; geen stap vraagt een andere aanpak dan
het ontwerp. Wel veranderen er acht dingen:

1. **Ronde A stopt pas 2–22 ms na zijn grens** bij een voorvoegsel, omdat FTS5 de lijsten
   samenvoegt tussen twee keer kijken naar de interrupt. Met een vaste 40 ms voor ronde B kwam
   het antwoord tot 191 ms. Nu krijgt B de rest van 120 + 40 ms (minstens 1 ms): 30 keer "ver"*
   gaf 162,6–171,5 ms. Bij 100.000 nodes: p95 161–162 ms, max 178 ms (3.45.1 en 3.53.1); op
   3.46.1 met 164.000 nodes één keer 191 ms. "Hooguit 120 + 40 + ±5 ms" klopt dus niet: het is
   p95 ±162 ms, met een staart tot ±190 ms (S1, Keuze 9).
2. **Een stuk van de herbouw moet eerst schrijven, en pas daarna `:hi` en `search_seq.next`
   lezen.** Met pysqlite (en dus aiosqlite) opent een SELECT geen transactie. De naïeve volgorde
   liet in de test 3 nodes uit `fts_node` en 18 uit `fts_name` weg (S2, §3.1, Keuze 12).
3. **De titel uit de mediabibliotheek (`extra`) ging niet door `SS()`.** "grosse" vond "Die
   große Stille" niet. Nu wel, in `schema.py` en in 0010 (S8, §3.1).
4. **`parse()` houdt de tekens op een letter bij het woord** (Unicode-categorie M), anders valt
   "İstanbul" in "i" en "stanbul". En de tekst krijgt NFKC vóór FTS: `unicode61` breekt een
   ligatuur niet op (S8, Keuze 5 en 21).
5. **De vorm voor bijna-duplicaten uit Keuze 4a is een kruisproduct** (de twee `IN`-lijsten
   worden gelijkheden op de sleutel van `dup_pair`). Bij 55.000 foto's stopte hij niet binnen
   2 s. De vorm met `EXISTS` geeft dezelfde paren in p95 5–7 ms (S4, S10).
6. **De kaart haalt de poort van 150 ms niet** bij een fotograaf met 55.000 foto's met GPS:
   Nederland p95 217–244 ms, de wereld 328–351 ms. Zonder de join op `node` 22–34 ms. Dus komt
   de tabel `geo_cell` uit Keuze 4a (S10). Na de review van Z0 (haalbaar F1 en F2) niet meer:
   `photo_geo` houdt `dead` en `taken_at` van de node bij, en de kaart gaat zonder join (S10).
7. **De handdruk van Keuze 17 houdt `/proc/<pid>/fd` dicht, maar zonder Yama niet `mem` en
   `ptrace`.** Een tweede proces van dezelfde uid opent vóór "klaar" `/proc/<pid>/mem` of hangt
   er met ptrace aan, en leest na "klaar" de blobs uit het geheugen van het kind. Een python die
   niemand mag lezen (0711, zoals ffmpeg al in fase 5) maakt het kind vanaf de exec
   niet-dumpbaar: geen venster (S11, Keuze 17).
8. **De zoekbench heeft ±1,1 GB vrij nodig** bij 100.000: 1.056 MB met de WAL, 525 MB na een
   checkpoint (S9).

---

## S1: de tijdsgrens door aiosqlite en de pool

**Klaar als:** een vraag van 2 s stopt na 120 ± 30 ms; een vraag met een zichtbare treffer geeft
≥ 1 rij; de verbinding werkt daarna gewoon; een late `interrupt()` raakt de volgende vraag niet.
**Gehaald**, op 3.53.1 en 3.46.1.

`api/tests/search/test_spike_deadline.py` (Z1 houdt hem; `grens()` wordt daar
`app.search.deadline.deadline()`):

```sh
cd api && taskset -c 0-2 uv run pytest -q -s -m "zoek_perf or not zoek_perf" \
  tests/search/test_spike_deadline.py          # en hetzelfde in python:3.12-slim
```

6 passed op beide. De getallen:

| | 3.53.1 | 3.46.1 |
|---|---|---|
| een vraag van 2 s, grens 120 ms, 20 keer | 121,1–127,1 ms (mediaan 123,7) | 121,1–127,1 ms (mediaan 121,6) |
| de race: grens op de mediane duur, 200 keer | grens 5 ms, 47 gestopt | grens 6 ms, 63 gestopt |
| de vraag daarna gestopt | 0 van 200 | 0 van 200 |
| `"factuur"` op 20.000 documenten, 30 keer | ronde A, mediaan 18,6 ms | ronde A, mediaan 22,8 ms |
| `"de"` | ronde A, mediaan 34,9 ms | ronde A, mediaan 42,2 ms |
| `"ver"*`, B krijgt de rest | ronde C, 162,6–170,6 ms (p95 169,7) | ronde C, 162,6–171,5 ms (p95 169,6) |

Bij `"ver"*` geeft ronde C één rij: "Vergadering bestuur.pdf" van Eva, niet "Verslag van
Bram.pdf" (test_rondes_a_b_en_c).

**Wat de spike vond.**

- `interrupt()` moet in de callback van de timer zelf, op de `sqlite3`-verbinding in de thread
  van aiosqlite. `aiosqlite.Connection.interrupt()` is een coroutine en loopt pas als de vraag
  al klaar is: dan raakt hij de volgende.
- De adapter van SQLAlchemy leest elke uitkomst meteen helemaal. Na een `await` loopt er geen
  statement meer, en een late `interrupt()` doet niets.
- Ronde A stopt bij een voorvoegsel later dan zijn grens. Per ronde op 3.46.1, met de oude vaste
  40 ms voor B (10 keer): A 121,7–142,2 ms, B 41,5–47,1 ms, C 0,8–1,9 ms,
  samen 164,2–191,1 ms. In de eerste run van de job op 3.46.1 (lokaal in slim, de VM had
  andere last) kwam één antwoord op 346,8 ms en faalde de test (eis < 185 ms). Dat kwam in de
  40 runs daarna niet meer voor. Met "B krijgt de rest" is de som 160 ms plus de uitloop van B:
  30 keer 162,6–171,5 ms. De eis van de test (< 120 + 40 + 25 ms) is gelijk gebleven.
- Bij 100.000 nodes (S9) is de tijd tot het antwoord p95 161–162 ms en max 178 ms. Ronde C zelf
  kost p50 0,3–0,9 ms, max 1,5 ms; zij gaf nooit 0 rijen. Ronde B helpt bij een voorvoegsel
  bijna nooit (hooguit 1 van de 13 keer dat A stopte): de lijsten samenvoegen kost B evenveel
  als A.

## S2: de triggers onder elk pad van de app

**Klaar als:** na elke stap is de index gelijk aan een herbouw; een upload tijdens de herbouw
wacht ≤ 1 s. **Gehaald.**

`api/tests/search/test_spike_triggers.py` (12 tests; Z1 houdt ze): hernoemen, verplaatsen,
prullenbak in en uit, wissen, de batch van 500, een map kopiëren, nieuwe inhoud (vervangen, PUT,
een versie terug, tekst, OCR), een andere eigenaar, `userdelete`, `purge_expired`, de import van
fase 5, de cascade drie lagen diep, en de herbouw in stukken met uploads ertussen. Na elke stap:
`migratie.check_tree`, de integrity-check van FTS5, en term voor term gelijk aan een volledige
herbouw (`spike.herbouw_verschil`).

```sh
cd api && taskset -c 0-2 uv run pytest -q -s tests/search/test_spike_triggers.py
cd api && taskset -c 0-2 uv run pytest -q -s -m zoek_perf tests/search/test_spike_triggers.py
```

| | 3.53.1 | 3.46.1 |
|---|---|---|
| batch van 500, ms per actie (move, trash, restore, copy, purge) | 1523, 132, 219, 5831, 832 | 1988, 195, 234, 6520, 935 |
| herbouw 10.000 in stukken van 2.000: langste stuk | 92 ms | 100 ms |
| 18 uploads ertussen: mediaan, max | 112 ms, 169 ms | 111 ms, 172 ms |
| herbouw 100.000 (100 woorden): stukken, langste | 51, 201 ms | 51, 294 ms |
| 160 uploads ertussen: mediaan, max | 113 ms, 315 ms | 147 ms, 687 ms |

**Wat de spike vond.**

- De FK-cascade start `node_bd` ook voor kinderen en kleinkinderen: wissen met de cascade laat
  niets in de index staan (test_wissen_met_de_cascade_raakt_ook_de_kleinkinderen). Van onder
  naar boven wissen (`purge.delete_nodes`) is er voor de stukken van 2.000, niet voor de
  triggers. De docstring van `purge.py` zei het andersom; die is aangepast.
- Een stuk van de herbouw moet eerst schrijven en dan pas `:hi` en `search_seq.next` lezen. Het
  prototype begint met `UPDATE search_state SET cursor = cursor WHERE id = 1 RETURNING cursor`.
  Met pysqlite opent een SELECT geen transactie; BEGIN komt pas vóór de eerste schrijfactie. Leest
  het laatste stuk `next` daarvoor, dan mist de index een node die er net bij kwam:
  test_het_laatste_stuk_leest_de_teller_pas_onder_het_slot speelt de naïeve volgorde na en ziet
  3 nodes alleen na een herbouw in `fts_node` en 18 in `fts_name`.
- Het langste stuk bij 100.000 (201 ms op 3.53.1, 294 ms op 3.46.1) is langer dan de
  101–142 ms van V4. Een upload wacht dan tot 687 ms: onder de 1 s, maar met weinig ruimte.
  In de job `zoek-perf` komt het getal van de runner erbij.

## S3: 0010 heen en terug

**Klaar als:** precies 0009 na de downgrade; `compare_metadata`. **Gehaald**, door de tests van
Z0-api (niet overgedaan):

- `test_migration_0010.py::test_van_0009_met_data_heen_terug_en_weer_heen` (met en zonder FTS5):
  het schema na de downgrade is gelijk aan een verse 0009, de data ook;
- `::test_modellen_gelijk_aan_de_migratie`: `compare_metadata` tussen `models.py` en 0010;
- `::test_downgrade_haalt_alles_weg_ook_met_rijen_in_de_nieuwe_tabellen`;
- `::test_migratie_van_100000_nodes_binnen_10_seconden` (marker `zoek_perf`).

```sh
cd api && taskset -c 0-2 uv run pytest -q tests/search/test_migration_0010.py   # 7 passed
```

In `python:3.12-slim` (3.46.1) slaagde de migratie van 100.000 ook: 16,47 s voor de hele test,
met het vullen; de upgrade zelf binnen de 10 s. De job `zoek-perf` draait hem.

## S4: de plannen, met en zonder ANALYZE

**Klaar als:** groen in CI in het image. **Groen op 3.45.1, 3.53.1 en 3.46.1 (slim)**; in CI in
de job `zoek-perf`.

`api/tests/search/test_spike_plannen.py`, op een boom van 3.000 uit de zoekbench, met en zonder
`ANALYZE`. Per vorm: de eerste stap ligt vast, nergens `AUTOMATIC`, nooit een zoekstap op
`parent_id`. De vormen: zoeken (rondes A, B, C met 1, 30 en 65 wortels), alleen filters met
`INDEXED BY` (zonder `TEMP B-TREE`), vanaf de paden, de kaart, precies gelijk, bijna gelijk.

```sh
cd api && taskset -c 0-2 uv run pytest -q -s tests/search/test_spike_plannen.py
```

**Wat de spike vond.**

- De vorm voor bijna-duplicaten uit Keuze 4a (`p.a IN mine AND p.b IN mine`) wordt zonder
  ANALYZE `SEARCH p USING PRIMARY KEY (a=? AND b=?)` met twee `LIST SUBQUERY`: voor elke `a` uit
  `mine` elke `b` uit `mine`. De vorm met `EXISTS` loopt alle paren langs met per kant één
  zoekstap in `ix_node_owner_blob`. Ze geven dezelfde paren (test).
- Boven 64 wortels (`json_each`) is "vanaf de paden" geen bereik meer maar `SCAN` over heel
  `ix_node_mpath`. De code kiest dan altijd "langs de tijd".
- Zonder `INDEXED BY` kiest de planner zelf (alleen gemeld, geen eis). Op alle drie de versies
  gelijk: "langs de tijd" en "laatste jaar" via `ix_node_mpath`; "type" zonder ANALYZE via
  `ix_node_ftype_mod`, met ANALYZE via `ix_node_mpath`. Daarom kiest de code (Keuze 4a).
- Z0-e2e heeft `assert_plan` in `tests/search/plans.py`. Z1 en Z3 zetten hun echte SQL daaronder.

## S5: tesseract in Debian 13

**Klaar als:** een getal in dit bestand. **In CI** (stap "S5 tesseract in Debian 13 (alleen
meten)" van `media-image`; het blok `=== kort ===` toont de uitkomst).

In een wegwerpcontainer van het gebouwde image, als root en met netwerk: Debian-versie, of
`libavfilter` aan `libtesseract` hangt (`ldd`), of ffmpeg met `--enable-libtesseract` is gebouwd,
dan `apt-get install --no-install-recommends tesseract-ocr tesseract-ocr-nld tesseract-ocr-eng
tesseract-ocr-osd`, met de som van `Installed-Size` ervoor en erna, "Need to get" en "After
this operation", de nieuwe pakketten, `du` van de taaldata, en de versie en talen. Het image
verandert niet.

Hier gemeten: niets. `apt` in docker geeft in de sandbox 403, en het image is hier niet te
bouwen. Het script zelf draaide in `python:3.12-slim` tot `apt-get` (zonder netwerk). Wat al
vaststaat: het image installeert ffmpeg uit Debian 13 (`apt-get install ffmpeg`, Dockerfile).
Het getal staat in `tesseract.txt` (artefact `media-image`) en in het blok `kort`; het komt
hier bij de eerste run. Tot dan blijft de standaard van Vraag 1: tesseract niet in het image.

## S6: de landsgrenzen

**Klaar als:** ≤ 60 kB gzip. **Gehaald**: 33.686 bytes.

`tools/kaart/maak_wereld.py` (alleen de standaardbibliotheek). Leest de GeoJSON van Natural
Earth 110m admin-0, tag v5.1.2 (publiek domein, `raw.githubusercontent.com`), controleert de
sha256 (`6866c877…c977f`, 838.726 bytes), en schrijft per land de code, de naam in het
Nederlands en het Engels en de ringen. Een ring is één lijst gehele getallen: het eerste punt in
honderdsten van een graad, daarna verschillen; zonder het sluitpunt en zonder punten die na het
afronden gelijk zijn aan het vorige.

```sh
python3 -I tools/kaart/maak_wereld.py --download --uit "$S/kaart"
world-110m.f2c3625f1d27.json: 85456 bytes, gzip -9 33686 bytes (grens 60000)
```

Altijd dezelfde uitvoer bij dezelfde bron (vaste volgorde en scheidingstekens), dus ook de hash
in de naam. Exit 1 boven de grens, 2 bij een verkeerde bron. `test_spike_kaart.py` (4 tests)
gebruikt een kleine eigen bron; de job `zoek-perf` haalt de echte en meet. Het bestand zelf staat
niet in git.

## S7: het zaad van 100.000 in CI

**Klaar als:** < 3 minuten. **Gehaald** lokaal; in CI in de job `zoek-perf`.

`api/tests/search/test_spike_zaad.py` (marker `zoek_perf`): het zaad van §3.6 zelf,
`app/search/bench.py` van Z0-e2e (`maak()` met `Plan(n=100_000, seed=6)`). Dat draait de echte
migratie en zet de boom met gewone INSERTs in de database, zodat elke `node_ai` zijn rij in FTS
schrijft. Daarna is de index gelijk aan een herbouw.

Na de samenvoeging van Z0 (100.000 bestanden, 5.900 mappen, 230 miljoen tekens):

| | 3.53.1 | 3.46.1 (slim) |
|---|---|---|
| het zaad | 106,8 s | 112,4 s |
| migratie, plannen, blobs, tekst | 0,3 s, 2,8 s, 1,1 s, 21,1 s | 0,3 s, 3,0 s, 1,3 s, 22,0 s |
| nodes met de triggers | 78,4 s | 82,8 s |
| op schijf | 541 MB | 541 MB |
| de test, met de herbouw erna | 118,6 s | 125,9 s |

Lokaal, op 3 van de 4 cores (`taskset -c 0-2`, en `--cpuset-cpus 0-2` voor slim). In CI meet
de job `zoek-perf` hetzelfde op 3.46.1.

Vóór de samenvoeging stond `app/search/bench.py` nog niet op de tak van de spike; die mat met de
boom van de zoekbench (108.452 nodes, 233 miljoen tekens):

| | 3.53.1 | 3.46.1 (slim) |
|---|---|---|
| totaal | 81,9 s | 96,5 s |
| migratie, boom, tekst | 0,1 s, 3,5 s, 4,2 s | 0,2 s, 4,0 s, 5,1 s |
| nodes met de triggers | 70,9 s | 80,7 s |
| op schijf | 526 MB | 526 MB |

Een tweede run op 3.53.1: 79,9 s (nodes met de triggers 69,0 s). De test zelf, met de
controle op een herbouw erna: 85,7 s (3.53.1) en 102,6 s (3.46.1). Het zaad van Z0-e2e is
trager in "tekst" (21 s tegen 4 s): het maakt de tekst woord voor woord uit een woordenschat
(Zipf, `tekst()` in bench.py) en zet die per sha in `blob_text`, vóór de nodes.

## S8: de twee vormen per woord

**Klaar als:** ß, ẞ, ligaturen en İ vinden naam én tekst; "grosse" en "große" vinden elkaar, ook
"strasse" en "Straße". **Gehaald** op 3.45.1, 3.53.1 en 3.46.1.

`api/tests/search/test_spike_vouwen.py`: tien groepen schrijfwijzen (Straße, große, ﬁets, ﬂink,
ĳsje, ǆemal, İstanbul, één, café, œuvre), elk als naam en als tekst in de database; elke
schrijfwijze als vraag vindt ze allemaal, en niets van een andere groep. Ook via trigram op
`search_name`.

```sh
cd api && taskset -c 0-2 uv run pytest -q -s tests/search/test_spike_vouwen.py
```

**Wat de spike vond.**

- `extra` (de titel van `media_item`) ging niet door `SS()` in de view. Opgelost in `schema.py`
  en in de bevroren kopie in 0010 (`151a0a8`); test_een_titel_met_ss.
- Een woord houdt de tekens op een letter (Unicode M). Splitst `parse()` op "alles wat geen
  letter of cijfer is", dan valt "İstanbul" (I met punt: `i` + U+0307) in "i" en "stanbul";
  test_istanbul_blijft_een_woord.
- De tekst moet NFKC krijgen vóór FTS (Keuze 21): `unicode61` breekt "ﬁ" niet op;
  test_tekst_zonder_nfkc_vindt_de_ligatuur_niet.

## S9: `tools/zoekbench/bench.py`

**Klaar als:** draait in de container van 0.7.0 met `dc exec -T platform python -I - --los <
bench.py`, in < 5 minuten, en ruimt op. **Gehaald** op de host en met uv; in de container in CI
(`test_media_container.sh`, deel "zoeken").

Alleen de standaardbibliotheek. Maakt het schema van 0010 (een bevroren kopie;
`test_zoekbench.py` eist dat het letterlijk gelijk is aan head) en een boom van 100.000 in een
tijdelijke map, en meet zoeken voor "eigenaar" en "alles" met de rondes van Keuze 9 (B krijgt de
rest, zoals in S1), per soort vraag. Ruimt op, ook na SIGTERM.

```sh
cd api && taskset -c 0-2 python3 -I ../tools/zoekbench/bench.py --los --map "$S" --json …
cd api && taskset -c 0-2 uv run python -I ../tools/zoekbench/bench.py --los --map "$S" --json …
```

| 108.452 nodes, 233 M tekens | 3.45.1 | 3.53.1 |
|---|---|---|
| hele run (wandklok) | 69 s | 68 s |
| bouwen | 44,9 s | 44,5 s |
| eigenaar (6.623 zichtbaar): p50, p95, max | 9,7, 161,8, 178,0 ms | 12,3, 161,3, 167,0 ms |
| alles (97.022 zichtbaar): p50, p95, max | 15,0, 161,2, 175,1 ms | 17,7, 162,1, 175,4 ms |
| hoe vaak B, C (van 150 vragen) | B 0–1, C 12 | B 0–1, C 12 |
| ronde C zelf, max | 0,4–1,3 ms | 0,5–1,5 ms |
| A zonder grens, p95 | 692–722 ms | 563–599 ms |
| op schijf: met de WAL, na een checkpoint | 1.056 MB, 525 MB | 1.056 MB, 525 MB |
| opgeruimd | ja | ja |

Per soort vraag haalt alleen het voorvoegsel de grens (p95 167–173 ms); gewone woorden p95
31–71 ms, zeldzame 5–13 ms, twee woorden 16–62 ms, frases 8–12 ms, namen 28–73 ms. Zonder grens
duurt een kort voorvoegsel tot 776 ms.

In de container: `test_media_container.sh` draait `in_platform python -I - --los <
tools/zoekbench/bench.py`, eist ≤ 300 s, `opgeruimd: ja` en geen `zoekbench-*` meer in
`/data/cache` of `/tmp`. Het blok `=== zoekbench ===` staat in `kort` van `media-image`. Het
getal van de container komt hier bij de eerste run. In `python:3.12-slim` (3.46.1) met
`--vormen` (164.124 nodes): eigenaar p95 161,3 ms, alles p95 161,5 ms, max 191,3 ms; 117 s.

## S10: de vormen van Keuze 4a

**Klaar als:** per vorm een p95; haalt een vorm zijn poort niet, dan de tabel of de poort uit
Keuze 4a. **Gemeten**; de kaart haalt zijn poort niet.

`bench.py --vormen` zet er een fotograaf bij met 55.000 foto's met GPS (164.124 nodes in
totaal) en meet de SQL van Keuze 4a, 20 keer per vorm:

```sh
docker run --rm -i --network none --cpuset-cpus 0-2 -w /tmp python:3.12-slim \
  python -I - --los --vormen --map /tmp --json … < tools/zoekbench/bench.py      # 3.46.1
cd api && taskset -c 0-2 uv run python -I ../tools/zoekbench/bench.py --los --vormen …   # 3.53.1
```

| Vorm (p95 in ms) | 3.46.1 | 3.53.1 | Poort |
|---|---|---|---|
| filters eigenaar: geen, type, laatste jaar | 0,7, 0,6, 0,7 | 0,4, 1,4, 2,4 | 150 (SQL ≤ 100) |
| filters alles: geen, type, laatste jaar | 0,2, 0,1, 0,2 | 0,1, 0,1, 0,3 | 150 |
| vanaf de paden: eigenaar (6.623) | 9,6 | 7,0 | (≤ 20.000 nodes) |
| vanaf de paden: alles (97.022) | 171,2 | 163,4 | wordt niet gekozen |
| kaart eigenaar: wereld, Nederland | 8,8, 8,9 | 9,4, 6,0 | 150 |
| **kaart fotograaf (55.000): wereld** | **350,9** | **328,2** | **150: niet gehaald** |
| **kaart fotograaf: Nederland** | **217,5** | **244,1** | **150: niet gehaald** |
| kaart fotograaf: Nederland, zonder join | 22,3 | 33,9 | |
| precies gelijk: eigenaar, fotograaf | 12,8, 95,0 | 7,0, 114,0 | 300 |
| bijna gelijk met `EXISTS`: eigenaar, fotograaf | 1,5, 5,0 | 2,2, 6,7 | 300 |
| bijna gelijk vanaf de eigen sha's: fotograaf | 123,5 | 109,9 | |
| bijna gelijk, de vorm uit het ontwerp | > 2.000 (gestopt) | > 2.000 (gestopt) | 300: niet gehaald |

Paren: 30 bij de eigenaar, 522 bij de fotograaf. Hele run met de vormen: 117 s (3.46.1).

**Wat dat verandert.**

- **Alleen filters:** de SQL-p95 is ≤ 2,4 ms, dus poort 150 ms blijft. "Vanaf de paden" alleen
  tot 20.000 nodes en tot 64 wortels (S4); bij 97.000 kost hij 163–171 ms.
- **De kaart:** de join op `node` per foto kost het. Zonder join 22–34 ms. Volgens Keuze 4a komt
  er de tabel `geo_cell (owner_id, z, cx, cy, n, cover)`, bijgewerkt door de job `geo`. Die
  staat nog niet in 0010; zie "Open". **Herzien na de review van Z0 (haalbaar F1 en F2):** geen
  `geo_cell` (opgeslagen tellingen lopen na de prullenbak uit de pas, en kennen de filters
  niet). `photo_geo` houdt `dead` en `taken_at` van de node bij met triggers in 0010, en de kaart
  gaat zonder join met de dekkende `ix_photo_geo_tile`: p95 wereld 44,7 en Nederland 30,4 ms
  (3.53.1), 66,2 en 33,4 ms (3.46.1). De bench meet de oude vorm met join ernaast ("met join
  (oud)").
- **Bijna gelijk:** de vorm met `EXISTS` in plaats van `IN mine`. Daarmee haalt hij de poort
  ruim, en is `dup_group` niet nodig.
- Niet gemeten: "langs de tijd" met een filter dat in een groot bereik niets vindt. Die loopt de
  hele partiële index af (164.000 rijen hier). Z1 meet dat met de wereld van §3.6.

## S11: de handdruk van Keuze 17

**Klaar als:** altijd `EACCES` (of het pad bestaat niet); nooit de inhoud van een blob, via
`/proc/<pid>/fd/N` vóór en na "klaar". **Gehaald** lokaal als `nobody`; in de echte
worker-container in CI. Maar de handdruk lekt via een andere weg (zie onder).

`api/tests/search/handdruk.py` speelt de worker na: `run.spawn` met de rlimits, een kind dat
wacht op "ga", `PR_SET_DUMPABLE 0` zet, "klaar" meldt en dan pas de fd's van de blobs krijgt
via `SCM_RIGHTS`. Een tweede proces van dezelfde uid (de aanvaller) probeert vóór en na "klaar":
`/proc/<pid>/fd/N` openen en lezen, `/proc/<pid>/mem` (de fd van vóór "klaar" vastgehouden, na
"klaar" gelezen), en ptrace (`SEIZE`, `INTERRUPT`). Drie vormen:

- `ontwerp`: zoals Keuze 17;
- `vork`: het kind forkt na "klaar", en het kleinkind doet het werk (een nieuw geheugen);
- `0711`: het kind start uit een kopie van python met modus 0711 (met een `pyvenv.cfg` ernaast).

```sh
setpriv --reuid=65534 --regid=65534 --clear-groups env -i PATH=/usr/bin:/bin \
  /opt/ci-venv/bin/python -I - --srv "$PWD/api" < api/tests/search/handdruk.py
# in de container (test_media_container.sh): in_media python -I - --srv /srv --kopie-map /run/media
```

Lokaal (uid 65534, geen Yama, python 3.12.13):

| | ontwerp | vork | 0711 |
|---|---|---|---|
| `/proc/<pid>/fd/N` na "klaar" | `EACCES` | `EACCES` | `EACCES` |
| te openen vóór "klaar" | fd 0, 1 (`/dev/null`), 2 (pipe) | fd 0, 1, 2 | niets |
| `mem` vóór "klaar" | geopend | geopend | `EACCES` |
| `ptrace` vóór "klaar" | gelukt | gelukt | `EPERM` |
| `mem` en `ptrace` na "klaar" | `EACCES`, `EPERM` | `EACCES`, `EPERM` | `EACCES`, `EPERM` |
| **blob-inhoud gezien** | **ja, via de `mem`-fd van vóór "klaar"** | nee | nee |

Uitkomst: `S11 True` (geen blob via `/proc/<pid>/fd`, alles na "klaar" `EACCES` of weg); lekt
niet: `0711`, `vork`; geen venster: `0711`.

**Wat de spike vond.**

- `PR_SET_DUMPABLE 0` sluit `/proc/<pid>/fd` en een nieuwe `open` van `mem`. Maar een `mem`-fd
  die vóór "klaar" geopend is, blijft hetzelfde geheugen lezen. Na "klaar" leest het kind de
  blobs in dat geheugen; de aanvaller las 28 MB en zag de blob.
- ptrace vóór "klaar" lukt zonder Yama (`ptrace_scope` 0). Een vastgehouden tracer kan het kind
  daarna sturen; daar kijkt `dumpable` niet meer naar.
- Forken na "klaar" geeft een nieuw geheugen: de oude `mem`-fd leest dan niets meer van de
  blobs. Een ptrace van vóór "klaar" helpt dat niet.
- Een programma dat niemand mag lezen (0711) maakt het proces vanaf de exec niet-dumpbaar: er is
  geen venster. Het image doet dat al voor ffmpeg, ffprobe, nice, ionice en env (fase 5, Keuze
  11 en 47). Een 0711-kopie van python heeft een `pyvenv.cfg` met `home =` nodig, anders vindt
  hij `encodings` niet.
- Yama (`ptrace_scope` ≥ 1) houdt `mem` en ptrace van een broer tegen, maar niet het openen van
  `/proc/<pid>/fd` (dat is een leescontrole). Op de LXC van Jonas is Yama niet zeker.
- fd 2 (stderr, een pipe van de worker) is vóór "klaar" te openen. Er gaat geen blob doorheen,
  maar stderr kan beter `/dev/null` zijn, met fouten over de socket.
- tmpfs in docker is standaard `noexec`. In CI staat de 0711-kopie daarom in `/run/media`
  (`--kopie-map`); de test eist dat er daarna geen `/run/media/handdruk-*` over is.

In de container draait `test_media_container.sh` het als `media` (uid 10002), met de
`ptrace_scope` van de runner. `handdruk.json` (gemaskeerd) komt in het artefact, de uitkomst
per vorm in `kort`. Het getal komt hier bij de eerste run.

---

## In CI

`zoek-perf` (nieuw, `python:3.12-slim`, 3 cores): S4, S8, S9 (het schema) en S6 (de test),
dan de marker `zoek_perf` (S1 met de echte grenzen, S2 herbouw van 100.000, S3 migratie van
100.000, S7 met het zaad van `app/search/bench.py`), `bench.py --los --vormen` (S10, S9) en
`maak_wereld.py --download` (S6). Lokaal in slim kostte de marker `zoek_perf` ±3,5 minuten.

`media-image`: de stap S5, en `test_media_container.sh` met S11 (`handdruk.json`) en S9
(`zoekbench.log`). Het blok `=== kort ===` aan het eind toont S5, S9 en S11.

## Afwijkingen van §3.7

- **S7 mat in de spike met de boom van de zoekbench**, omdat het zaad van Z0-e2e nog niet op die
  tak stond. Sinds de samenvoeging van Z0 meet de test het zaad zelf (`app/search/bench.py`);
  de aparte stap in `zoek-perf` voor dat zaad is weg.
- **De plan-tests staan in `test_spike_plannen.py`**, met de SQL van de bench. `plans.py` en
  `assert_plan` (`test_plans.py`) zijn van Z0-e2e; beide sets blijven, met dezelfde vormen.
- **S8 veranderde 0010 en `schema.py`** (`extra` door `SS()`). 0010 is van Z0-api en nog niet
  uitgebracht; 0001–0009 bleven gelijk.
- **S11 meet meer dan `/proc/<pid>/fd`**: ook `mem` en ptrace, in drie vormen.
- **De bench heeft een eigen generator** (stdlib, voor `--los` in de container), los van
  `app/search/bench.py` van Z0-e2e. Dat blijft zo: `--los` draait één bestand zonder de app.

## Open

- ~~**De kaart: `geo_cell`.** S10 haalt de poort niet. De tabel moet in 0010 (Z0-api) of in de
  migratie van Z4; wie en wanneer is nog niet beslist.~~ Gedaan na de review van Z0 (haalbaar F1
  en F2), zonder `geo_cell`: `photo_geo.dead` en `taken_at` in 0010 (Z0), de kaart zonder join
  haalt de poort (zie S10).
- **Keuze 17: welke vorm.** De 0711-kopie van python is één regel in de Dockerfile (Z2); de vork
  na "klaar" is code in `app.render text`. Aanbevolen: 0711, plus stderr naar `/dev/null`.
- **CI-getallen op 3.46.1** voor S1, S2, S7, S9 en S10 in de job, en S5 en S11 in de
  container: bij de eerste run na de push.
- ~~**Z0-e2e `plans.py`**: als daar de vorm `IN mine` voor bijna-duplicaten staat, is die
  plan-test een kruisproduct; hij moet de vorm met `EXISTS` krijgen.~~ Gedaan bij de
  samenvoeging van Z0: `_bijna` in `api/tests/search/test_plans.py` heeft de vorm met `EXISTS`.
