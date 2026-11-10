# Z0-e2e: het harnas, de fixtures, de wereld, de referentie en `e2e/slim.sh`

Dit is het deel Z0-e2e van het bouwontwerp van fase 6 ([bouwontwerp.md](bouwontwerp.md), §3.6,
de e2e-regels van §3.5, en §4.1–§4.3). Z0 bouwt geen functie: dit deel legt vast waarop Z1 tot Z7
en X1 testen. Zoeken in de app is nog dat van 0.7.0 (`LikeFallback`, op naam).

De kern (migratie 0010, de contracten, de stubs) staat in [z0-api.md](z0-api.md). Het web-deel
staat in [z0-web.md](z0-web.md), de spike S1–S11 en de job `zoek-perf` in [spike.md](spike.md).
Hoe de vier delen samenkwamen: [z0.md](z0.md).

De keuzes B49–B60 zijn nog een voorstel. Dit deel bouwt op de standaard: geen tesseract in het
image (de test met echte tesseract heeft marker `ocr` en draait niet in de gewone run), geen
automatische gezichten, niet zoeken met een foto, GPS alleen voor de eigenaar, en plaatsnamen pas
na de installatie (Z7).

## Wat er is

### De boom van 100.000 (`api/app/search/bench.py`)

De generator van §3.6 als module, met alleen de standaardbibliotheek. Zo kan
`tools/zoekbench/bench.py` (S9, draait in de container van 0.7.0 en mag de app niet importeren)
de logica overnemen; een test eist dat de module zonder `app.*` laadt.

- `Plan(n, seed, profiel, gebruikers, tekens)`. Profielen: `basis`, `groot` (200 documenten van
  1 MB tekst) en `kopieen` (één tekst van 1 MB met 10 kopieën, C11).
- `vul(con, plan, wachtwoord_hash=…, blob_standaard=…)` schrijft met gewone INSERTs in een
  database die al op head staat. De triggers van 0010 zetten `num`, `mpath`, `dead` en de
  FTS-rijen. De tekst gaat vóór de nodes in `blob_text`, dan schrijft de trigger hem in één keer
  mee.
- `maak(data_dir, plan)` doet de echte migratie en daarna `vul()`. `app_blob()` geeft de
  versies die de app nu zet, zodat de backfills bij het starten niets te doen hebben.
- Vanaf de opdrachtregel: `uv run python -m app.search.bench --data /tmp/boom --n 100000
  [--profiel groot] [--uitkomst boom.json]`. Aan het eind staat een samenvatting die begint met
  `=== kort ===`.
- De rollen van §4.4 staan in `ROLLEN`:
  - `eigenaar` is b00;
  - `ontvanger5` is b01, `ontvanger20` is b02;
  - `groepslid` is b03;
  - `gast` is een gast met 2 delingen;
  - `alles` heeft een deling op elke hoofdmap.

  Iedereen logt in met `bench.WACHTWOORD`.

Gemeten bij n = 100.000 en seed 6 (dezelfde boom bij elke run):

| | aantal |
|---|---|
| gebruikers | 32 |
| mappen | 5.900 |
| bestanden | 100.000 |
| documenten | 19.370, met 230.458.524 tekens |
| foto's | 55.414, waarvan 33.102 met GPS |
| kopieën bij een ander | 2.000 |
| prullenbak | 173 mappen en 994 bestanden |
| delingen | 92 |

Hoe lang het zaaien duurt:

- `python -m app.search.bench` (één proces, op 3 cores van deze VM): 120 s. Daarvan gaat 89 s
  naar de nodes met de FTS-triggers, 23 s naar de tekst en 0,8 s naar de migratie.
- Via het harnas (`start_boom(…, 100_000)`): 128 s, waarvan nodes 90 s en tekst 22 s. Daar
  komen twee keer starten van de server bij.

S7 eist < 3 minuten in CI. Dat meet de job `zoek-perf` van de spike, sinds de samenvoeging
met dit zaad (`test_spike_zaad.py`: lokaal 106,8 s op 3.53.1 en 112,4 s op 3.46.1, zie
[spike.md](spike.md)).

### De wereld (`api/tests/search/wereld.py`)

Eva, Bram, Cas, Gijs, Dirk, Ada, Fien, Hans en Iris met hun mappen, delingen, albums en
bestanden, precies zoals §4.2 ze beschrijft. Elke rij en elk woord staat in de docstring.

- Wat erin staat:
  - 30 nodes, waarvan 1 in de prullenbak;
  - 9 delingen, waarvan 2 met een publieke link;
  - het album `Utrecht`, met een regel;
  - 5 plekken in `photo_geo`.

  Elk bestand staat ook echt in de BlobStore, gemaakt met `fixtures.py`.
- De woorden zijn uniek: `zebrakwarts` in de tekst, `bonwoordy` als OCR-tekst van Eva's foto,
  `prullenwoordq` in de prullenbak, enzovoort. Zo weet een test precies wat hij hoort te vinden.
- `bouw(conn, blob_dir)` werkt op een synchrone verbinding (e2e, `seed.connect`).
  `bouw_async(settings)` is voor de API-tests.
- `intrekken(conn, w)` trekt de deling met Hans in en `verhuis(conn, w)` zet Verhuisd uit Werk.
  Een test roept ze aan op het moment dat hij kiest.
- `NAAMVRAGEN`: 13 vragen op naam, met voor elke persoon een ander antwoord.
- De datums komen van een vaste dag, 3 november 2026 (`DAG`), niet van nu. Fien's deling is een
  dag eerder verlopen.

De tests (`tests/search/test_wereld.py`, 8):

- de wereld staat er zoals beschreven, en is elke keer gelijk;
- wat de referentie per persoon ziet, is wat `access.shared_roots` van de app zegt;
- wie wat vindt (`zebrakwarts` per persoon, OCR van een foto alleen voor de eigenaar, de
  prullenbak, de inbox, `in`);
- intrekken en verhuizen;
- Fien na het verlopen;
- Iris wissen laat geen personen achter;
- en zoeken van nu (`/api/v1/search`) per persoon naast `Referentie.op_naam`.

### De referentie (`api/tests/search/referentie.py`)

Zoeken in gewoon Python over een momentopname van de database: de definitie van "wat moet
gevonden worden" (§4.3). De regels staan bovenaan in de module: de vraag (NFKC, `fold`, ß als
"ss", hooguit 200 tekens en 10 woorden, frases, geen operators), hele woorden, het voorvoegsel,
stukken van namen, wat je ziet (Keuze 4 en 25a), en OCR van een foto alleen voor de eigenaar.

- `Referentie.lees(conn)`, `zoek(user, q, in_=…, nu=…)` (met `woorden`, `namen` en `alles`),
  `zichtbaar()`, `wortels_van()` en `keten()`.
- `op_naam(user, q, in_=…)`: zoeken zoals in 0.7.0. Daarmee leggen de API-test en de e2e de app
  van nu ernaast.
- `vragen.tsv`: 200 vragen, gemaakt met `maak_vragen()` (niet met de hand). Er zitten 20 met
  operators bij, 12 met ß of "ss" in beide richtingen, en 16 frases.
- `BOOM` is de boom van 2.000 nodes (`bench.Plan(n=1_840, seed=6, tekens=2_000)`).

De tests (`tests/search/test_referentie.py`, 23):

- de vragenlijst, en hoe een vraag ontleed wordt;
- elke vraag van `vragen.tsv` op de boom van 2.000, naast `fts_node MATCH` en
  `instr(search_name)` van migratie 0010: 0 verschillen, en meer dan 1.000 treffers;
- ß in beide richtingen;
- een frase blijft in één kolom;
- en wat iedereen ziet volgt `access.shared_roots`.

### De plannen (`api/tests/search/plans.py`)

`assert_plan(conn, sql, args, require_first=…, require=…, forbid=("AUTOMATIC",),
forbid_index_terms=("parent_id=?",))` legt EXPLAIN QUERY PLAN vast. Hij werkt op sqlite3 en op een
synchrone SQLAlchemy-verbinding. Faalt hij, dan staat het hele plan in de fout.

`tests/search/test_plans.py` (26) zet de vormen van Keuze 4 en 4a op een boom van 1.500, met en
zonder ANALYZE:

| vorm | waar het plan begint, of wat erin moet |
|---|---|
| zoeken met 3, 64 en 65 wortels | eerst `fts_node VIRTUAL TABLE`; tot 64 een OR, daarboven `json_each` |
| filters langs de tijd | `ix_node_live_mod`, zonder TEMP B-TREE |
| filters vanaf de paden | `ix_node_mpath` |
| filter op soort | `ix_node_ftype_mod` |
| kaart | `ix_photo_geo_tile` |
| duplicaten en bijna-duplicaten | `ix_node_owner_blob` |

Dezelfde tests draaien in `python:3.12-slim` (SQLite 3.46.1, zie slim.sh).

### De gemaakte bestanden (`api/tests/search/fixtures.py`)

Er staat niets binairs in het repo. Alles wordt bij de test gemaakt, met vaste zaden, dus elke
keer met dezelfde bytes (een test eist dat). Kleine bestanden komen als bytes terug. Wat groot is,
maakt `make(naam)` één keer en bewaart het in `api/.pytest_cache/search/<hash>/`.
`ZOEK_FIXTURES_CACHE` zet een andere map.

- **PDF met tekst**: een eigen kleine PDF-schrijver (Helvetica, stdlib). 1–20 pagina's, twee
  kolommen, U+00AD, een emoji, "Straße" en "GROẞE" (met een ToUnicode-tabel), titel en auteur.
- **PDF als scan**: een JPEG-pagina zonder tekstlaag, met `ocrwoordx` alleen in het beeld.
- **docx, xlsx, pptx, odt, ods, odp** uit vaste XML-sjablonen. Ze hebben een woord per plek:
  hoofdtekst, voetnoot, kop- en voettekst, sprekersnotities, de laatste van 20.000 cellen, en
  titel en auteur. Daarnaast een zip-bom (`kantoor/zip-bom.docx`) en een docx met `<!ENTITY`. De
  reader van de app weigert die twee met `Unreadable`.
- **Scans voor OCR** (`ocr/scans`):
  - op 0°, 3°, 7°, −5° en 90°;
  - als PNG en als JPEG q80;
  - in het Nederlands en het Engels;
  - grijs papier met ruis, 150 dpi;
  - en een `info.json` die zegt welk lettertype het werd.
- **Foto's**:
  - EXIF met GPS (Pillow schrijft de GPS-IFD), en een foto zonder GPS;
  - een verkleinde kopie, en EXIF-draaiing 6;
  - HEIC met GPS (pillow-heif);
  - XMP met GPS, en XMP met een "billion laughs";
  - `IFDRational(0, 0)`, GPS zonder ref, en (0, 0);
  - video met `location` (mp4) en ISO 6709 (mov) via ffmpeg, alleen onder marker `media`.
- **Bijna-duplicaten** (S1):
  - `bijna/varianten`: elk origineel met vier varianten;
  - `bijna/brieven`: 150 brieven op één sjabloon;
  - `bijna/formulieren`: gescande formulieren met een andere invulling;
  - `bijna/schermen`: schermafdrukken van één app;
  - `bijna/egaal`: 150 bijna egale foto's (zwart, lucht, muur);
  - `bijna/anders`: andere foto's.
- **Punten voor plaatsen**: `GRENSPAREN` (Kerkrade/Herzogenrath, Zundert/Wuustwezel,
  Bergeijk/Lommel, Frisange/Hettange-Grande) en `ZEE` (vijf punten op de Noordzee).

De tests (`tests/search/test_fixtures.py`) zijn 30 gewone, 2 onder `media` (ffprobe leest de plek
van de video) en 1 onder `ocr` (echte tesseract).

### Het harnas van de e2e (`e2e/search_harness.py`)

- `start_wereld(map)` start een eigen server met de wereld in zijn database en de bestanden in
  zijn BlobStore. `ZoekApp.accounts` heeft een `Account` per persoon (`"eva"`, `"bram"`, …), met
  admin, user en guest. `ZoekApp.wereld` heeft de ids.
- `start_boom(map, n)` doet hetzelfde met de boom van `bench.py` (1.000 of 100.000), alleen in de
  database: geen bestanden op schijf. Er is een `Account` per rol van §4.4.
- Beide starten de app met `PLATFORM_SEARCH_INDEX=uit` (na de review van Z0, haalbaar F4): de
  referentie op naam is het zoeken van 0.7.0 (`LikeFallback`), ook nadat Z1 `FtsEngine` aanzet.
  Wie de index wil (Z1 en verder), geeft `zoekindex="aan"`.
- `app.referentie()` leest de referentie over de database van nu, ook na een upload van de test
  zelf.
- `app.db()` geeft een alleen-lezen sqlite3-verbinding naast de draaiende server, om in de index
  te kijken.
- `app.wijzig(fn)` stopt de server, draait `fn(conn, wereld)` in één transactie (bijvoorbeeld
  `wereld.intrekken`) en start de server weer.
- Inloggen gaat zoals in de rest van de e2e. 2FA is niet verplicht: de setting `security` staat
  in de database, zoals `make_admin` hem via de API zet.
- In `e2e/conftest.py` staan de fixtures `zoek_wereld` (één per sessie) en `zoek_browsers`
  (elke melding in de browser laat de test falen, met schermafdrukken bij een fout).

`e2e/test_zoek_z0.py` (marker `zoek`, 7 tests) test het harnas zelf in de echte app:

- iedereen uit de wereld logt in, met de goede rol;
- zoeken op naam per persoon en per vraag over HTTP is precies `Referentie.op_naam`, ook binnen
  een gedeelde map. `privé` vindt niemand behalve Eva;
- met het toetsenbord, als Bram:
  1. `/` in Bestanden, typen en Enter;
  2. naar `/zoeken`, waarbij het antwoord meteen naast de referentie gaat en de rijen op het
     scherm die van het antwoord zijn;
  3. het veld Zoekwoorden;
  4. Enter op de gedeelde map Werk opent die map;
- Dirk vindt niets, en het scherm zegt "Niets gevonden.";
- een upload met tekst is meteen op naam te vinden, voor Dirk alleen. De naam staat dan al in
  `fts_node` en `fts_name`: de triggers doen dat in dezelfde transactie, in het echte proces;
- de boom van 1.000: elke rol van §4.4 zoekt op naam. Tot 200 treffers moet het precies de
  referentie zijn, daarboven 200 ervan met "meer";
- de boom van 1.000 in de browser, zonder één melding.

### `e2e/slim.sh`

De snelle e2e van §4.1 (C16). Groen vóór een pakket "klaar" meldt.

1. De api-tests met marker `zoek` of `triggers` draaien in `python:3.12-slim` (SQLite 3.46.1,
   zoals V8). De venv van de host gaat alleen-lezen mee, met `--network none`, en de gemaakte
   bestanden komen in `api/.pytest_cache/slim`. `ocr`, `media` en `zoek_perf` doen niet mee.
2. De web-app wordt gebouwd als iets in `web/` nieuwer is dan `web/dist/index.html`.
3. De e2e met marker `zoek` draait tegen die build, op de host.

`--alleen-db` en `--alleen-e2e` doen één deel. `SLIM_CPUS=0-2` houdt alles op die cores. Aan het
eind staat een samenvatting die begint met `=== kort ===`.

Gemeten op 3 cores: **groen in 124 s**. Stap 1 duurde 70 s (173 tests op 3.46.1), stap 3 54 s (7
tests).

### Markers

- `zoek` geldt voor elke test in `api/tests/search/`. `tests/search/conftest.py` zet hem, en zet
  ook `triggers` op de bestanden over migratie 0010 en de triggers. In `e2e/` zetten de tests
  `zoek` zelf. Zoek-tests draaien gewoon mee in de jobs `api` en `e2e`.
- `ocr` is voor echte tesseract. Hij zit niet in de gewone run: de `addopts` van de api en de
  e2e-stap in CI (`and not ocr`) laten hem weg.
- `zoek_perf` (al van Z0-api) is voor de metingen van §4.4.

Ze staan in `api/pyproject.toml`, `e2e/pytest.ini` en de `pytest_configure` van de e2e. De job
`infra` draait nu ook shellcheck op `e2e/slim.sh`.

## Metingen die Z1 kan gebruiken

Zoeken op naam van 0.7.0 (`LikeFallback`) over de boom van 100.000 via HTTP: 5 vragen, elk 3
keer per rol, op 3 cores van deze VM. Dit is een eenmalige meting, geen poort.

| rol | mediaan | max |
|---|---|---|
| eigenaar | 156 ms | 210 ms |
| ontvanger5 | 173 ms | 245 ms |
| ontvanger20 | 165 ms | 197 ms |
| groepslid | 144 ms | 198 ms |
| gast | 80 ms | 110 ms |
| alles | **1.410 ms** | **1.693 ms** |

"alles" (een deling op elke hoofdmap) is het geval waar de LIKE over alle wortels het traagst is.
Daar moet Z1 onder de 200 ms van §4.4 komen.

## Gedraaid op deze tak

Op 3 cores van deze VM:

- api: `ruff check` en `ruff format --check` schoon. `pytest -q`: 4.106 groen, 2 overgeslagen,
  1 rood: de bekende `test_fuzz_10000_namen_met_vaste_seed` (0,561 s tegen een grens van
  0,5 s; zie punt 8 onder Open). Dat duurde 35 minuten.
- `tests/search` als `nobody` (`/tmp/ci-nobody/run.sh`, zoals CI): 173 groen.
- `-m media` en `-m ocr` op `test_fixtures.py`: 2 en 1 groen.
- web: typecheck, 940 tests, build, `check:i18n --strict` en `budget` groen.
- `e2e/slim.sh`: groen in 124 s.
- De e2e-set van CI (met `and not ocr`): 144 groen in 15 minuten, de 7 van `test_zoek_z0.py`
  erbij.
- `yamllint -s .` en shellcheck op `e2e/slim.sh`: schoon.

## Afwijkingen van het ontwerp

1. **`blob_text` met een gewone INSERT** in `bench.py` en `wereld.py`, niet via
   `analysis.write_text()`. Het is testdata, en Keuze 3 verbiedt alleen upserts. Wie
   `test_search_no_replace.py` schrijft (de regel "blob_text alleen via write_text()"), moet
   `app/search/bench.py` en `tests/` uitzonderen. `bench.py` staat onder `app/` omdat het
   ontwerp dat zegt.
2. **Twee gebruikers extra in de boom**: `gast` en `alles` (samen 32 in plaats van 30). Zonder
   hen bestaan de rollen "gast" en "alles" van §4.4 niet.
3. **De wereld is een beginstand.** Intrekken (Hans) en verhuizen (Verhuisd) zijn hulpjes die de
   test zelf aanroept. Zo kiest de test het moment, en kan hij "weg in de eerstvolgende vraag"
   aantonen.
4. **De afgeleide rijen staan er al** (`blob_text`, `blob_meta`, `photo_geo`, `photo_ocr`,
   `person`, `photo_person`, `dup_pair`), zoals de jobs van Z2, Z3 en Z6 ze zouden schrijven. Zo
   kan elk pakket testen zonder de jobs van een ander. `blob_hash` en `ocr_page` ontbreken: die
   maken de jobs zelf.
5. ~~**De plaatsen zijn niet nagekeken.** `PLAATS_ID = 2745912` (de stad Utrecht in GeoNames), de
   grensparen en de punten op de Noordzee zijn met de hand gekozen.
   `download.geonames.org` is hier dicht en Natural Earth is er niet. Z3 moet ze controleren tegen
   `places.sqlite` en de polygonen.~~ Nagekeken door Z3 met de echte bronnen (Natural Earth 10m
   v5.1.2 en `rg_cities1000.csv`, met hun sha256): `rg_cities1000.csv` heeft geen GeoNames-id, dus
   `PLAATS_ID` is nu het vaste id van `app.geo.load.place_id` voor Utrecht (150904566956734); de
   grensparen en de punten op de Noordzee kloppen zoals ze zijn (`api/tests/geo/test_places.py`,
   marker `geo`, in de job `api`).
6. ~~**OCR van een foto staat in dezelfde kolom als OCR van een PDF.** De view `search_src` van
   0010 zet `photo_ocr.text` en `blob_text.ocr` allebei in de kolom `ocr` van `fts_node`.~~ De
   referentie telt OCR-tekst van een foto alleen voor de eigenaar (Keuze 25a: Bram vindt
   `bonwoordy` niet op Eva's gedeelde foto). ~~FTS alleen kan dat verschil niet maken, dus de app
   moet het zelf doen: een eigen kolom, of een tweede controle op `photo_ocr`. Dat is voor Z1 en
   Z6.~~ [Na de review van Z0 (veilig F3): de foto-OCR staat in een eigen kolom `pocr`; Z1 sluit
   die uit voor de nodes van een ander met een kolomfilter. Zie [z0.md](z0.md), "De review".]
   `test_wie_wat_vindt` legt vast wat eruit moet komen.
7. **Tesseract en de hoeken.** Gemeten met tesseract 5.3.4 op de scans:
   - met `-l nld+eng` leest hij `ocrwoordx` op 0°, 3° en −5° (PNG en JPEG, NL en EN);
   - met `nld` alleen leest hij op 3° "oerwoordx";
   - op 7° leest hij het niet zonder rechtzetten;
   - 90° herkent hij met `--psm 0` ("Rotate: 90").

   De test onder `ocr` eist alleen 0°, 3°, −5° en de herkenning van 90°. 7° is het geval voor het
   rechtzetten van X1.
8. **`test_plans.py` gebruikt de SQL uit het ontwerp**, niet die van de app (die is er nog niet).
   Z1 en Z3 zetten hun echte SQL in `test_search_plans.py`, onder dezelfde `assert_plan`. Bij de
   samenvoeging kreeg `_bijna` de vorm met `EXISTS` van "Herzien na de spike (S4, S10)".
   Gedaan door Z3 (golf 3): de filters `place`, `country` en `camera` per node (`249b535`) en de
   vragen van `GET /duplicates` uit `app/dupes/groups.py` (`5880c50`), met en zonder ANALYZE.
9. **De e2e gebruikte `/` en Enter, niet Ctrl/Cmd+K**, omdat Ctrl/Cmd+K op de tak van Z0-web
   zat. Sinds de samenvoeging test `test_ctrl_k_en_het_zoekveld_bovenaan` het veld bovenaan ook.
   De e2e heet `test_zoek_z0.py` en test het harnas. `test_zoek_toegang.py` van §4.2 bouwt later
   op dezelfde wereld.
10. **`triggers` alleen in `tests/search/`.** De hook zit in `tests/search/conftest.py`. Een
    `test_search_churn.py` (Z1) moet daar ook staan, anders krijgt hij de marker niet en draait
    slim.sh hem niet op 3.46.1.
11. **De boom van het harnas heeft geen bestanden op schijf**, alleen de database. Preview staat
    op `none`, dus downloaden en previews van die nodes werken niet. Hij is voor zoeken en
    metingen; de wereld heeft wel echte bestanden.
12. **`test_de_sleutel_volgt_het_lettertype`** vergeleek in `python:3.12-slim` (zonder DejaVu)
    twee keer de terugval met elkaar en faalde daar. Hij zet nu beide lettertypen zelf, zodat hij
    overal hetzelfde test. Hij is niet losser geworden.
13. **Zonder DejaVu** tekenen de scans met het lettertype van Pillow, met een waarschuwing.
    `info.json` en de sleutel van de cache zeggen welk lettertype het werd, dus een OCR-test
    vergelijkt met wat erbij staat.

## Open

Voor andere pakketten:

1. ~~**Ctrl/Cmd+K in de e2e** komt na het samenvoegen met Z0-web.~~ Gedaan bij de samenvoeging
   (`test_ctrl_k_en_het_zoekveld_bovenaan`). Z4's `test_zoek_z4.py` test de combobox.
2. ~~**De CI-jobs `zoek-perf` en `ocr`** (§4.6) zijn van de spike.~~
   - Deze tak voegt alleen `and not ocr` toe aan de e2e-stap en shellcheck op `slim.sh`.
   - ~~De job `ocr` moet `-m ocr` draaien (api en e2e), met `tesseract-ocr`,
     `tesseract-ocr-nld` en `fonts-dejavu-core` uit apt in de job. Zonder DejaVu tekent
     `fixtures.py` met Pillow, en de scans zijn dan andere.~~

   Gedaan: de job `zoek-perf` kwam met de spike, de job `ocr` met X1. `ocr` installeert
   `tesseract-ocr` met nld, eng en osd en `fonts-dejavu-core` met apt en draait `-m ocr` in api en
   e2e (`.github/workflows/ci.yml`). Beide zijn groen in run 42827893297 (nummer 99) op `16ccb8d`.
3. ~~**Z3** controleert `PLAATS_ID`, `GRENSPAREN` en `ZEE` tegen `places.sqlite` en de polygonen
   (afwijking 5).~~ Gedaan (Z3, `api/tests/geo/test_places.py`).
4. ~~**Z1 en Z6** maken het verschil tussen OCR van een foto en van een PDF (afwijking 6).~~
   Gedaan in de review van Z0 (veilig F3): de OCR van een foto staat in een eigen kolom `pocr`
   van `fts_node` (`api/app/search/schema.py`).
5. ~~**Wie `test_search_no_replace.py` schrijft**, zondert `bench.py` en `tests/` uit
   (afwijking 1).~~ Gedaan door Z1: de test leest alleen `app/` en zondert `search/bench.py` uit.
6. ~~**Z1** zet `test_search_churn.py` in `tests/search/` (afwijking 10).~~ Gedaan door Z1
   (`api/tests/search/test_search_churn.py`). ~~Die test op 3.46.1 in CI draaien is I1 (A3).~~
   Sinds I1 (A3, `f211d7b`) draait `zoek-perf` hem op 3.46.1, in de stap "Churn en de triggers op
   3.46.1" (`.github/workflows/ci.yml:262-267`).

Vragen voor Jonas (de standaard waar dit deel op bouwt staat erbij):

7. ~~**Tesseract in het image** (Vraag 1, B-keuze). Nu niet. De test onder `ocr` draait alleen waar
   tesseract er is.~~ Beantwoord: ja (Vraag 1 en 15, B53). X1 zette tesseract in het image, en
   de job `ocr` draait de tests onder `ocr` met een echte tesseract. Zie [x1.md](x1.md).

Bekend en niet van Z0-e2e:

8. `tests/test_media_names.py::test_fuzz_10000_namen_met_vaste_seed` meet tijd en faalt alleen op
   deze VM. Hij is ongewijzigd en in CI groen.
