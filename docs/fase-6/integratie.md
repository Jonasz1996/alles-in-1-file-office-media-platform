# Fase 6: de integratie (I1)

## In het kort

De integratie I1 bracht de kern van fase 6 samen tot versie 0.8.0: Z0 tot en met Z7 en X1. Dat is
zoeken in de namen en de inhoud van bestanden, tekst in scans en foto's (OCR), foto-gegevens met
de kaart, land en plaats, duplicaten, personen met de hand, herinneringen, slimme albums en de tab
Zoeken en foto's op `/beheer`. Het ontwerp staat in [bouwontwerp.md](bouwontwerp.md), de tests in
[e2e.md](e2e.md) en de getallen in [metingen.md](metingen.md). I1 had vier delen:

- **Stap A, zes pakketten naast elkaar**, elk in een eigen worktree (`wip/i1-ci-infra`, `-api`,
  `-beheer`, `-web`, `-perf` en `-docs`), elk gebouwd, tegengelezen en hersteld, en daarna
  samengevoegd. De grootste punten: foto's die gelezen zijn vóór `places.sqlite` er was, krijgen
  na de bouw alsnog hun land en plaats (A4); `zoek_samen.py` meet nu met foto's en scans en kijkt
  of een container door zijn geheugengrens stopte (A1); `zoek_backfill.py` meet ook photometa en
  land en plaats (A2); de job `zoek-perf` draait churn en de triggers op SQLite 3.46.1 (A3); een
  wekelijkse controle van de index (S6); en Land, Plaats en Camera op `/fotos` (S7). Daarnaast de
  kleinere punten S1–S5 en S8–S13.
- **De integratie zelf:** vier fouten die pas na het samenvoegen bleken (C1–C4), een zin voor
  schermlezers in de stand beeld (web R4), een review van het geheel met zeven bevindingen
  (R1–R3, F1–F4), en een fout in een e2e die alleen in CI optrad (`2adccd2`).
- **D1, de schrijfpoort:** een upload wachtte tijdens de backfill van de index soms langer dan
  1 s. `app/dbgate.py` zet nu de schrijvers van de app in een rij, verzoeken eerst. Gereviewd met
  twaalf bevindingen, hersteld, en samengevoegd in `8a679bd`.
- **Stap B:** alle checks op de kop (B1, groen op één weigering van een lokale test na, zie
  [De checks](#de-checks)) en de metingen M1–M9 (B2). M9 vond een fout in de testfoto's
  (`d5624b5`).

Van de poorten van §4.4 en §4.5 zijn er ⟦C2: hoeveel van de hoeveel, uit de tabel Kort van
metingen.md⟧ gehaald ([Metingen](#metingen)). In CI is de commit van 0.8.0
⟦C2: de run in CI op de commit van 0.8.0 en het aantal groene jobs⟧. Elke wijziging aan een
contract van de API is additief, zoals het nieuwe veld `check` in `AdminSearch`
([Wijzigingen aan contracten](#wijzigingen-aan-contracten)).

Voor 0.8.1 blijft: gezichten automatisch herkennen (X2) en zoeken op wat er op een foto staat
(X3, dat ook naar een fase 6b kan schuiven), met de container `analyse`, de migraties 0011 en
0012 en de pagina `/over`. Wat I1 verder liet liggen, staat met de reden onder
[Niet gedaan, met de reden](#niet-gedaan-met-de-reden).

Van de beslissingskaarten van fase 6 zijn alleen B53 (OCR) en B57 (personen, gezichten en beeld)
besloten; B49–B70 blijven verder voorstel, en Vraag 4 en Vraag 8–14 zijn nog open. 0.8.0 draait
dus op de standaarden van het ontwerp. Een tag komt er alleen als Jonas ja zegt.

## Hoe het liep

De basis was `16ccb8d`: golf 4 met zijn nacontrole en een reparatie in `install.sh`, groen in CI
(run 42827893297, nummer 99: alle 16 jobs). Eerst kwam een plan. Daarin staan de punten die de
golven aan I1 overlieten (in de z*.md en x1.md heten ze "I1 (Ax)", "I1 (Sx)" en "I1 (D1)"),
verdeeld over zes pakketten die elk hun eigen bestanden hebben, zodat ze naast elkaar konden
lopen. D1 liep apart.

| Wanneer | Wat | Commits |
|---|---|---|
| 10 november, tot 18:29 | Stap A: zes pakketten, elk gebouwd, tegengelezen (de lenzen "breken" en "regels", bij docs "feiten") en hersteld | zie [Gedaan](#gedaan) en [De review van I1](#de-review-van-i1) |
| 18:29 | samengevoegd, in deze volgorde: ci-infra, api, beheer, web, perf, docs | `5f9ed65`, `2371356`, `17c3f98`, `a8ed7ef`, `c4b7ac1`, `dbbe470` |
| 18:40–18:52 | vier fouten uit het samenvoegen (C1–C4) | `995fa4f`, `ddbffda`, `a97eb70`, `4acfc52`, `1dc4dc1` |
| 19:02–19:20 | het zoekveld in de stand beeld (web R4), en de docs bij wat samengevoegd was | `6f7ed56`, `0a82ed3`, `9d27e28`; `0213b24`, `8414f8e`, `384f738`, `cac74dd`, `3280946` |
| 19:49–19:51 | de review van de integratie (op `3280946`) en zijn reparaties | `86f5b9c`, `45dcc03`, `083d7c4`, `cc436df` |
| 20:19 | de fout uit CI run 42890910950 | `2adccd2` |
| 17:16–21:06, apart op `7f32472` | D1: de schrijfpoort, de review en de reparaties | `684cb39`, `9ef30e5`, `2fc70e3`; `6944820`, `a0e8975`, `1cd0778`, `9acd6ff` |
| 23:11–23:13 | D1 samengevoegd, en e2e.md en z2.md bij | `8a679bd`, `0e19180` |
| 23:12 tot 11 november 01:30 | B1: alle checks | [De checks](#de-checks) |
| 11 november 00:11 | C1: de teksten voor 0.8.0, met plekken voor de metingen, op `wip/i1-docs` | `ebcbb4f` |
| 11 november 01:41 | M9: de update met de echte bronnen | `d5624b5` |
| 11 november 02:09 | M6: de wis-test `test_een_upload_wacht_niet_op_het_hele_wissen` laat met `-s` zien hoe lang de upload wachtte | `77292fe` |
| daarna | M1–M8, elk alleen | [metingen.md](metingen.md) |

CI liep na de integratie op `2adccd2` (run 101): alle 16 jobs groen. `zoek-perf` deed er 21
minuten over, met de nieuwe stap voor churn en de triggers op 3.46.1. Op `0e19180` (run 102, de
samenvoeging van D1): alle 13 jobs groen.

Wat er onderweg misging, met de oorzaak:

- **De nice-tests van de worker (C4).** Onder `nice -n 10`, zoals de checks van de integratie
  draaiden, faalden `test_nice_rlimits_en_kale_omgeving_van_frame` en
  `test_nice_ionice_en_geheugen_van_bench` met `assert 19 == 15`. De tests gingen uit van
  niceness 0, terwijl `os.nice` optelt en bij 19 stopt. De worker zelf was goed. Nu rekenen de
  tests vanaf de eigen niceness (`nice_plus`, `995fa4f`).
- **Fixtures die wegvielen door de volgorde op de opdrachtregel (C2, daarna R1).** Staat er een
  testbestand uit `tests/` tussen twee bestanden van dezelfde submap, dan maakt pytest 9.1.1 een
  tweede knoop voor die submap, en die ziet de fixtures uit zijn `conftest.py` niet. Zo faalden
  de twee async tests van `test_log_hiding.py` bij de setup (60 goed, 2 fouten). C2 zette de
  marker in dat bestand zelf (`ddbffda`); de review vond dat het dieper zat (een sync test met een
  async fixture, het opruimen `_databases_dicht`, de fixtures van `tests/geo`). `86f5b9c` geeft
  elke tweede knoop de fixtures van zijn `conftest.py`.
- **Mislukte opnieuw wekte de pompen te vroeg (C1).** `admin.retry` wekte de lanes vóór de commit,
  zodat een pomp die al wachtte de oude `run_after` las en pas na `jobs.POLL` (5 s) begon.
  Hetzelfde patroon had de review van beheer al bij Hervatten gevonden (F3, `a710466`). Nu
  `jobs.kick(lanes)` na de commit (`a97eb70`).
- **Plaatsen naast de draaiende app (C3).** `geo-load --force` als eigen proces naast de app: de
  app hield de oude `places.sqlite` open tot een herstart, zodat de keten van de nieuwe bouw in de
  oude opzocht (C3b, `4acfc52`). En een start met `PLAATSEN=uit` wiste Setting `geo.places`
  niet, zodat na "aan" geen nieuwe keten kwam (C3a, `1dc4dc1`).
- **Een e2e die alleen in CI faalde (`2adccd2`).** De fixture `leeg` in
  `e2e/test_zoek_metingen.py` (uit de review van het perf-pakket, `e144074`) draaide
  `asyncio.run` in de hoofddraad. Na een test met een browser draait daar de lus van Playwright
  nog, en dan weigert `asyncio.run`: in CI run 42890910950, job `e2e`, 5 fouten bij de setup.
  Lokaal nagespeeld met `test_pwa.py` ervoor: zonder de wijziging 5 fouten, met 39 goed. De
  migratie loopt nu in een eigen draad, zoals `run_async` in `media_harness.py`.
- **Een upload die langer dan 1 s wachtte (D1).** In de volle suites van de nacontrole van golf 4
  was `test_backfill.py::test_een_upload_wacht_nooit_langer_dan_1_s_tijdens_de_backfill` rood
  (1,02 en 1,22 s). SQLite in WAL-stand heeft één schrijfslot en geen rij. Wie het slot niet
  krijgt, slaapt in de busy handler (stappen tot 100 ms), terwijl de pomp van de index het slot
  na elke `write_text` meteen terugpakt; en de `fsync` van elke commit gebeurt terwijl het slot
  vastzit. Met een trage schijf werden dat seconden ([z2.md](z2.md), Open 12).
- **GPS met 60 seconden in de testfoto's (M9).** `_dms` in `api/tests/search/fixtures.py`
  rekende minuten en seconden los en gaf voor 54,8 en 6,1 graden (…, 60.0) seconden.
  `render_meta` leest alleen seconden onder 60, dus de testfoto's bij de Doggersbank en
  Herzogenrath hadden geen GPS. Gerepareerd in `d5624b5` (zie [Metingen](#metingen), M9).

## Open punten

De golven gaven hun open punten voor I1 een naam in de z*.md en x1.md: "I1 (Ax)", "I1 (Sx)" of
"I1 (D1)". Hieronder staan ze allemaal, met de punten die de integratie zelf vond (C1–C4, web R4,
M9). In de z*.md en x1.md is elk gedaan punt doorgestreept met de commit en de test (`cac74dd`,
`0213b24`, `8414f8e`, `384f738`; D1 in `2fc70e3`, `9acd6ff` en `0e19180`).

### Gedaan

| Punt | Wat er veranderde | Commit | Test |
|---|---|---|---|
| A1 | `e2e/perf/zoek_samen.py` zaait in stap 1 ook 5.000 foto's (640 × 480, 85 % met GPS, 5 % bijna gelijk) en 500 scans van X1, met `--kort` 300 en 20. Tien poorten, elk gehaald, niet gehaald of niet te meten; buiten `--kort` telt niet te meten als niet gehaald. De poort op OOMKilled kijkt eerst of DS een geheugengrens heeft (`HostConfig.Memory` > 0), want zonder grens zegt OOMKilled niets. Lokaal één run met `--kort`, op één core en zonder OnlyOffice: exit 0 in 242 s | `46eb037`; review `e144074`, `53f85d9`, `67443c3`, `f0a1341`, `112415b`, `2b17f78` | `e2e/test_zoek_metingen.py`, onder andere `test_alle_poorten_gemeten_en_gehaald`, `test_een_poort_die_niet_te_meten_is_telt_niet_als_gehaald`, `test_ds_oordeel` en `test_overleefd` |
| A2 | `e2e/perf/zoek_backfill.py --soort photometa` en `--soort geo` (met `--geo-bron`), met de poorten 5.000 foto's en 20.000 punten per minuut. In stap A: photometa met n = 300 4.988,4 per minuut (alleen een rapport); geo met n = 2.000 23.223,7 per minuut, gehaald, met een `places.sqlite` van 258 landen, 4.274 gebieden en 144.521 plaatsen, gebouwd in 4,8 s | `361d2e2` | `e2e/test_zoek_metingen.py`, onder andere `test_de_poorten_van_paragraaf_4_5_per_soort`, `test_oordeel_per_soort`, `test_geo_zonder_bronmap_stopt_met_uitleg` en `test_bouw_places_zonder_bronnen_noemt_haal_py`; de metingen M3 en M4 |
| A3 | De job `zoek-perf` draait de churn en de hele set triggers op SQLite 3.46.1 (marker `triggers`, 34 tests). Lokaal 201,58 s, waarvan de churn 152,79 s | `f211d7b`; review `bf62237` | `api/tests/search/test_zoek_ci.py::test_zoek_perf_draait_churn_en_de_triggers_op_3_46_1` (twee mutanten maken hem rood); in CI groen in run 101 |
| A4 | Een nieuwe job `geo.places`: na elke bouw van `places.sqlite` vult een keten (sleutel `geo.places:<bouw>`, stukken van 1.000) land en plaats in bij foto's waar `place_id` en `country` allebei leeg zijn; zee blijft leeg. De keten wacht op photometa van vóór de bouw. Elke bouw krijgt een eigen id (meta `bouw`; een bestand van vóór I1 heet `zonder-id:<datum>`), dus een nieuwe bouw begint vooraan, en een mislukt klaarzetten doet de volgende start. `geo-load` zet de keten ook klaar. Vóór de fix bleef een foto `(None, None, None)` | `7f9f6f9`; review `36b684f`, `5ae0651`; integratie `4acfc52`, `1dc4dc1` | `api/tests/geo/test_places_later.py`: `test_fotos_van_voor_places_sqlite_krijgen_na_de_bouw_hun_plaats`, `test_zee_uit_prullenbak_eigenaar_en_stukken`, `test_wacht_op_photometa_van_voor_de_bouw`, `test_geo_load_naast_de_draaiende_app`, `test_wat_tijdens_het_opzoeken_verandert_krijgt_geen_plaats`, `test_mislukt_klaarzetten_doet_de_volgende_start`, `test_een_nieuwe_bouw_begint_vooraan`; `tests/geo/test_geo_load.py::test_cli_geo_load`; met de echte bronnen M9 |
| S1 | Hervatten in `/admin/search` wekt de pompen van index en ocr, net als Opnieuw, en pas na de commit. De e2e `test_zoek_beheer.py` wacht daarom 15 s (`HERVAT_S`) in plaats van 30, en duurt 33,61 s in plaats van 53,96 s | `487e590`, `a710466` | `api/tests/search/test_admin_search.py::test_hervatten_wekt_de_pompen_van_index_en_ocr` |
| S2 | `test_admin_search.py` meldt de stap text zelf aan en hangt niet meer af van de volgorde van de tests | `ecd3149` | nagegaan door de tests in verschillende volgordes te draaien |
| S3 | De test van de guard wacht tot het kleinkind `sleep` heet | `f9edfb7` | `api/tests/test_media_worker_guard.py::test_verbinding_dicht_stopt_ffmpeg_en_kleinkinderen_binnen_3_s`; de race kwam lokaal niet terug (0 van 200) |
| S4 | `render_meta` zoekt een werkmap waar hij mag schrijven (als nobody faalde hij in een map van root) | `b4e0e5f` | `api/tests/test_render_meta.py::test_run_meta_met_een_werkmap_waar_niets_in_mag[alleen_lezen]` en `[weg]` (met de oude regel `PermissionError` en `FileNotFoundError`); als nobody groen in B1 |
| S5 | De logger van aiosqlite staat vast op INFO, zodat een wortel op DEBUG geen zoekwoorden in de log zet | `6eaaf95` | `api/tests/search/test_search_logs.py::test_niets_in_de_log_met_de_wortel_op_debug` (zonder: `('aiosqlite', ['geheimwoord7'])`) |
| S6 | `api/app/search/weekcheck.py`: eens per week vergelijkt de app 200 bestanden in de index met de database, in een eigen draad die alleen leest, in één momentopname. Na een verschil kijkt hij het volgende uur opnieuw; tijdens de pauze, een herbouw, zonder FTS5 of zonder nodes geeft hij geen uitkomst. De uitkomst (alleen aantallen) staat in `AdminSearch.check` en op `/beheer` in de regel "Controle (wekelijks)", oranje bij een verschil; met LIKE zegt de regel dat hij alleen met FTS5 loopt | `a9b8913`, `e84dd5d`; review `507d1ef`, `e1109f6`, `4e73235`, `afed2fb`, `7565237` | `api/tests/search/test_weekcheck.py` (onder andere `test_de_eerste_keer_meteen_en_daarna_pas_na_een_week`, `test_wat_de_app_intussen_schrijft_is_geen_verschil`, `test_na_een_verschil_het_volgende_uur_opnieuw`); `web/src/admin/searchTab.test.ts` |
| S7 | Land, Plaats en Camera in de balk Filters op `/fotos`. Twee plaatsen met dezelfde naam zijn uit elkaar te houden (eerst het land, dan het aantal foto's, dan een volgnummer) | `c4a20f2`, `9f08c06`; review `acdd152`, `fd9f396`, `1535021` | e2e `test_zoek_personen.py::test_plek_land_en_camera_op_fotos`; `web/src/photos/people/places.test.ts` en `slots.test.ts`. De eerste lading JS ging van 98.018 naar 98.062 B, en na de review was het 98.047 B van de 100.000 |
| S8 | De stand beeld op `/zoeken` zegt "Zoeken op beeld komt in een volgende versie.", zonder versienummer; sinds web R4 ook als beschrijving van het zoekveld, voor een schermlezer | `af07df7`; `6f7ed56`, `0a82ed3`, `9d27e28` | e2e `test_zoek_personen.py::test_de_knop_zoeken_op_beeld` (toetst ook de beschrijving); `web/src/lazySlots.test.ts`, `web/src/search/page.test.ts` |
| S9 | `install.sh`: uid 10001 kijkt de bronnen in `DATA_ROOT/geo` zelf na (`geo_ok_app` via `as_app_in`); root hasht geen bestand meer dat de app kan neerzetten. Wat root wel leest, leest hij zonder een symlink te volgen en hooguit één byte meer dan de bron | `5d15206`; review `ba76dfc`, `e928d6c` | `infra/single/tests/test_install_search.sh` (209 controles, waarvan 11 als root); mutanten rood, zie [De na-controle van de review](#de-na-controle-van-de-review) |
| S10 | Lege pakketten voor tesseract ook in `test_install_update.sh`, zodat die test zonder Debian-spiegel kan bouwen | `7c3a2f1` | alleen shellcheck en een losse container zonder netwerk; de test zelf liep niet (zie [Niet gedaan](#niet-gedaan-met-de-reden)) |
| S11 | De stap S5 van de spike is weg uit `media-image`; de grootte van tesseract bewaakt de job `images` | `86a4c47`; `2a05ad6` (spike.md) | `api/tests/search/test_zoek_ci.py::test_media_image_heeft_de_stap_s5_niet_meer` (rood tegen de oude ci.yml) |
| S12 | De docstring van `test_cli_db.py` noemt niet meer "de stub van geo-load" | `c23c07f` | alleen tekst |
| D1 | `api/app/dbgate.py`: een poort vóór SQLite, alleen op SQLite. Verzoeken eerst, dan het achtergrondwerk (wat een Runner draait, de herbouw); na 8 verzoeken op rij (`FG_STREAK`) komt een wachtende achtergrondschrijver aan de beurt. Wie de poort teruggeeft, geeft hem meteen door. Wie `BlobStore.lock` of het slot van een gebruiker heeft of erop wacht, telt als verzoek (`BoostLock`); een vrije poort blijft dan hooguit 0,2 s (`HOLD_BACK`) vrij voor hem. Het checkpoint van de WAL zit niet meer in een COMMIT (`wal_autocheckpoint=0`, een eigen checkpointer op een eigen verbinding). Opruimen na een upload pakt het blobslot alleen als er een wees is. `synchronous` blijft `FULL` | `684cb39`, `9ef30e5`, `2fc70e3`; review `6944820`, `a0e8975`, `1cd0778`, `9acd6ff`; samengevoegd `8a679bd` | `api/tests/test_db_gate.py` (onder andere `test_upload_gaat_voor_de_volgende_schrijfactie`, `test_een_upload_wacht_niet_achter_een_schrijver_die_steeds_terugkomt`, `test_pomp_haalt_een_wachtende_upload_niet_in`, `test_wat_een_runner_doet_is_achtergrond`, `test_blobslot_geeft_voorrang[blobs]` en `[gebruiker]`, `test_herbouw_schrijft_als_achtergrond`, `test_opruimen_zonder_wezen_pakt_het_blobslot_niet`, `test_sqlite_heeft_de_poort_en_geen_autocheckpoint`, `test_de_checkpointer_doet_het_buiten_de_engine`, `test_uploads_tegelijk_wachten_niet_achter_de_achtergrond`); `api/tests/test_db.py::test_alleen_sqlite_krijgt_de_schrijfpoort`; de tijd met M6 |
| C1 | Mislukte opnieuw wekt de pompen na de commit (`jobs.kick(lanes)`) | `a97eb70` | `api/tests/search/test_admin_search.py::test_mislukte_opnieuw_wekt_de_pompen_na_de_commit` |
| C2 | `test_log_hiding.py` zet zelf `pytest.mark.anyio`, met een wachter voor elke async test; daarna de haak van R1 | `ddbffda`, `86f5b9c` | `api/tests/test_anyio_markering.py`, `api/tests/test_conftest_volgorde.py` |
| C3a | Een start met `PLAATSEN=uit` wist Setting `geo.places` en een keten `geo.places` die nog klaarstaat; gaat het weer aan, dan loopt de keten één keer over alle foto's zonder plaats en land | `1dc4dc1` | `api/tests/geo/test_places_later.py::test_gelezen_met_plaatsen_uit_krijgt_zijn_plaats_als_ze_weer_aan_gaan`, `::test_een_keten_die_nog_klaarstaat_bij_plaatsen_uit_begint_daarna_vooraan` |
| C3b | `places.get` vergelijkt het open bestand met het pad (`dev`, `ino`, grootte en `mtime_ns`) en opent bij een verschil de nieuwe bouw | `4acfc52` | `api/tests/geo/test_places_later.py::test_geo_load_force_naast_de_app_zoekt_op_in_de_nieuwe_bouw` |
| C4 | De nice-tests van de worker rekenen vanaf de eigen niceness (`nice_plus`) | `995fa4f` | `test_media_worker_guard.py::test_nice_rlimits_en_kale_omgeving_van_frame`, `::test_nice_ionice_en_geheugen_van_bench` |
| M9 | `_dms` in de testfoto's rondt eerst de seconden in totaal af en deelt dan. Op 500.000 waarden verschillen alleen de 86 die 60 seconden gaven | `d5624b5` | `api/tests/search/test_fixtures.py::test_elke_plek_leest_render_meta_terug`; `api/tests/geo/test_places_later_echt.py::test_upgrade_met_de_echte_bronnen` |

### Nagekeken, al gedaan

Het pakket i1-docs (`304eb62`, `9c674e5`, `c45dd17`, `1c81358`, `7481c25`; na de review `3fbb85e`
en `78c55b4`) liep de Open punten en de cellen van "Klaar als" in z0, z0-api, z0-e2e, z0-web, z0b,
z0b-api, z1 tot en met z4, z6, z7 en x1 na tegen `16ccb8d`. Wat daar al gedaan was, is
doorgestreept met de reden en het bewijs: een commit, een bestand, of run 42827893297 (nummer
99, alle 16 jobs groen). De belangrijkste:

- **CI had nog niet gedraaid, of een cel "in CI na de push" stond open** (z0.md, z7.md, x1.md):
  groen in run 99, met `--stappen` in `infra` 79 goed en 0 fout, `e2e-media-perf` in 18 min 10 s,
  en de job `ocr` met 13 api-tests en 5 e2e goed en de poort van 130 pagina's in 380,2 s (20,5
  per minuut). De rode `infra` van run 98 (`7f32472`) kwam door de image-id onder Docker 29 en is
  gerepareerd in `16ccb8d`.
- **Wat Jonas koos over OCR** (z0.md Open 5, z0-api.md Open 6): B53 en B57 besloten in
  [beslissingen.md](../fase-0/beslissingen.md), tesseract met modus 0711 in het Dockerfile en
  `ocr_enabled` standaard aan.
- **De reservekopie en de bronnen voor plaatsen** (z0-api.md Open 4 en 7): Z7 (`2148508`),
  `tools/geo/haal.py` met sha256.
- **De wachters en kolommen van Z0** (z0-e2e.md, z0.md): `test_search_no_replace.py` (sluit
  `bench.py` uit), de kolom `pocr` in `schema.py` (review van Z0, veilig F3),
  `test_search_churn.py`.
- **De oude rondes van de deadline** (z0b.md Open 11): opgelost door Z1 (`f0bbb64`).
- **"4 tegelijk" niet gehaald en de GC van het hele proces** (z1.md): H2 (`1627aa7`,
  `serve.freeze_heap`), in run 99 een p95 van 206,6 ms bij 4 tegelijk.
- **De herinneringen op 3.46.1** (z6.md): p95 28,5 ms in run 99.
- **De chip van de plaats** (z3.md, `3c8dbf2`), en `/dubbel` en de waarschuwing bij delen (z4.md,
  door Z5 in golf 3).
- **De controle op `python-tekst`** (z2.md Open 9): `a8c097b`, groen in `media-image` van run 99.

Twee dingen zette het pakket recht in plaats van door te strepen: `zoek-perf` draaide op `16ccb8d`
geen churn en geen triggers (dat werd I1 (A3)), en CI draait de hele suite niet in
`python:3.12-slim` (de job `api` draait op de runner). En in z6.md en z3.md stond Vraag 4 bij de
personen; personen zijn Keuze 38, en Vraag 4 gaat over wie de plek van een foto ziet.

### Niet gedaan, met de reden

| Wat | Waarom niet | Waar het staat |
|---|---|---|
| Gezichten automatisch herkennen (X2), met de container `analyse`, migratie 0011 en de jobs `api-analyse` en `analyse-image` | hoort bij 0.8.1 (B68, Vraag 13 met standaard twee opleveringen) | README, "Wat komt in 0.8.1" |
| Zoeken op wat er op een foto staat (X3), met migratie 0012 en de job `beeld-model` | 0.8.1, of een fase 6b als het uitloopt | README, "Wat komt in 0.8.1" en "Planning" |
| De vormen van Z0b voor gezichten en zoeken op beeld (onder andere `AdminFaces`, `AdminBeeld`, `BeeldStatus` en `FacesConfirm`) | een voorstel tot A0, X2 en X3 ze vastleggen (0.8.1) | het plan van I1; [z0b.md](z0b.md), Open 1–3 |
| De pagina `/over` met de bronvermelding | komt met de gezichten (X2); tot dan staan GeoNames en Natural Earth onder de kaart en in de README | README, "Bekende grenzen" (De bronvermelding) |
| Het kader van een persoon bij een nieuwe versie van een foto | kan pas met migratie 0011, want 0010 ligt vast | README, "Bekende grenzen" |
| Zoeken op PostgreSQL: de tak van 0010, `select_engine` en het opruimen in stukken | niet getest; PostgreSQL zoekt alleen op naam (LIKE) | README, "Bekende grenzen" (Zoeken zonder index); z0-api.md Open 5 |
| Niemand luistert naar `search.progress` in de web | de tab op `/beheer` peilt elke 5 s, dus Jonas ziet hetzelfde; geen pakket had het, en het heeft geen eigenaar | z7.md (Afwijkingen), z0-web.md Open 6; niet in de README |
| De service worker bewaart `/kaart/` niet | klein, later | z0-web.md Open 5 |
| Herinneringen per map verbergen in de web (de API kan het, `kind = folder`), en een link naar `/dubbel` in het menu (nu alleen op `/fotos`) | geen pakket van I1 had het; klein, later | README, "Bekende grenzen" (herinneringen per map); [z6.md](z6.md) en [z5.md](z5.md), Open |
| De koude eerste weergave van ±8 s (één keer gezien, oorzaak onbekend) | geen meting in I1 draait `run_all.py` met een trace; naar I2 | z5.md, Open; niet in de README |
| Een e2e voor "Meer laden" in de lijst van mislukte op `/beheer` | daarvoor zijn meer dan 50 mislukte inhouden nodig; de bladzijden toetst `test_admin_search.py`; naar I2 | z7.md, Open |
| OCR voor een foto met dezelfde inhoud als een bestand dat er al was (`add_ref`) | vraagt een haak in Z0; zo'n foto krijgt zijn tekst bij de volgende start | README, "Bekende grenzen" (OCR); x1.md, Open |
| De rij van OCR staat alleen bij wie OCR mag vragen: wie een PDF alleen mag lezen, ziet niet of OCR klaar is (de tekst vindt hij wel met Zoeken) | klein; geen pakket van I1 had het | [x1.md](x1.md), Open 7 |
| Het camerafilter onder een libc-collatie op PostgreSQL (W2 van de review van web) | dat is de API van Z3, en PostgreSQL is niet getest; op SQLite werkt het | `api/app/search/filters_photo.py`; dit document |
| De teksten `beeld.notReady` en `photos_faces.notReady` op `/beheer` zeggen nog "komt later in fase 6" (W6 en R3 van de review van web) | horen bij de go/no-go van X2 en X3 | `web/src/i18n/areas/beeld.nl.ts`; dit document |
| De zichtbare uitleg in de stand beeld zegt nog "Druk op Enter" | buiten S8; de beschrijving van het veld begint nu met de zin dat het later komt | `web/src/search/ImageHint.tsx` (`IMAGE_SEARCH_READY`) |
| `uploads.cleanup_loop` en de lus van de mediascanner zijn niet als achtergrond gemerkt | hun schrijfacties staan bij de verzoeken in de rij, zoals vóór de poort | z2.md, Open 12 |
| De runner kijkt na een `Defer` pas na `jobs.POLL` (5 s) weer, tenzij iets hem wekt; bij een start met media aan kost dat tot 5 s voor de eerste tekst | het plan nam het alleen mee als D1 `jobs.py` toch zou raken; D1 zette daar alleen het achtergrondwerk van de Runner achter de poort. Hervatten wekt de pompen al zelf (S1) | [z2.md](z2.md), Open 11 |
| `synchronous=NORMAL` (geen `fsync` bij een commit) | dan kan een opgeslagen wijziging na een stroomstoring weg zijn; een open keuze, geen vraag in de README | README, "Bekende grenzen" (Uploaden terwijl de index schrijft); z1.md, Open na de nacontrole |
| Eén upload duurde 2.659 ms in `tests/test_pages.py::test_eerdere_versie`, in een volle suite tijdens de bouw van D1. De test heeft geen tijdgrens en slaagde; alleen gedraaid (twee keer) duurde de upload 36–40 ms | oorzaak niet gevonden; de poort was het niet, want in die test wachtte niemand op de poort | dit document |
| Kaart met een filter op soort, map, camera, persoon of tag sneller dan een halve seconde | kan alleen met een nieuwe migratie | README, "Bekende grenzen" en een vraag |
| Migraties na 0010: een index op (ftype, taken_day), de tabel `dup_group`, en een bewaking tegen een kapotte index als één UPDATE `owner_id` en `search_name` samen wijzigt | 0010 ligt vast; geen pad in de app doet zo'n UPDATE, en Z3 had `dup_group` niet nodig; ze komen met een migratie na A0 | het plan van I1; [z1.md](z1.md) en [z3.md](z3.md), Open |
| Land en Plaats op `/fotos` met meer dan 500 plaatsen | `GET /photos/places` geeft er hooguit 500, de meeste foto's eerst | README, "Bekende grenzen" |
| Een GPS-waarde met 60,0 seconden, zoals (54, 47, 60.0), geeft geen plek: `render_meta` leest minuten en seconden alleen onder 60 (`api/app/render_meta.py`) | zo ontworpen; M9 vond het in de testfoto's, niet bij een camera. Het blijft zo in 0.8.0, een open punt voor I2 | [Metingen](#metingen), M9 |
| S10 echt gedraaid: `test_install_update.sh --stappen` | mocht niet in de sandbox (en er was te weinig schijfruimte); in B1 weigerde de test omdat `python:3.12-slim` al bestaat; de test staat niet in CI | [De checks](#de-checks) |
| S13: de engines van aiosqlite sluiten in `test_geo_read.py` en `test_location_off.py` | niet na te spelen: drie keer 53 goed, zonder waarschuwing; niets veranderd | dit document |
| De woorden van de poorten verschillen: `zoek_backfill.py` en de poort van OCR (`api/tests/ocr/poort.py`) schrijven `NIET gehaald`, `zoek_samen.py` schrijft `niet gehaald` | `e2e/test_zoek_metingen.py` toetst beide vormen letterlijk, dus gelijktrekken maakt aan een van beide kanten een test rood; de blokken kort in `ci.yml` zoeken niet op deze woorden, en beide betekenen hetzelfde | [e2e.md](e2e.md), "De meetscripts in `e2e/perf`" (`3280946`) |
| Wijzigingen aan contracten voor later: het paginanummer bij een treffer, 204 in plaats van 404 voor `/pages/{n}/words`, kaders op gedraaide pagina's, een vriendelijke naam van de camera in de chip, "Niet gelijk" ongedaan maken, en een eigen zin bij een PDF zonder gescande pagina | de meeste vragen een wijziging aan een contract, en I1 voegde alleen toe; het plan schuift ze naar een latere ronde | het plan van I1; README, "Bekende grenzen" ("Niet gelijk"); [z4.md](z4.md), Open; [x1.md](x1.md), Open 6 |
| Teksten in het ontwerp (de handtekening van `admit()`, de wandklok van Keuze 8, `Hit.path`, de schatting van de minuten van `zoek-perf`) en kleine dingen in de web (`color-mix`, het samenvoegen van stippen, de scrollbalk van de viewer) | klein, later | het plan van I1 |
| De titels van een mediabestand als de probe pas na de stap text komt; `after_ready` voor een oud doc, xls of ppt als de miniaturen van OnlyOffice mislukken; de plek na een dedupe, een versie terug of een andere eigenaar (die komt pas bij de volgende ronde van de keten) | geen pakket van I1 had het; later, bij de robuustheid | het plan van I1; [z2.md](z2.md), Open 4 en 5; [z3.md](z3.md), Open |
| Een vlag in de viewer dat een foto van een ander is, zodat hij geen verzoeken doet die 404 of een lege lijst geven | klein, later | het plan van I1 |
| `files.free_name`, de grenzen van OCR per gebruiker in het geheugen, `jobs._runners` | horen bij het harden in fase 8 | het plan van I1 |
| Een lijst zonder pagina's kost de app zijn hele map op de event loop: `GET /public/listing` (de map achter een openbare link, `public.children` in `api/app/routers/public.py`) en `GET /files/{id}/children` of `/files/root` zonder `limit`. Met 5.000 bestanden in één map is dat 220–320 ms per vraag (M7) | de web-app vraagt een eigen map altijd in pagina's (`FIRST_PAGE` 200, daarna 1.000); de openbare pagina en de oude vorm zonder `limit` niet. Pagina's voor de openbare lijst vragen een wijziging aan een contract, en I1 voegde alleen toe | dit document; [Metingen](#metingen), M7 |

## Wijzigingen aan contracten

Alles additief, op één punt in een meetscript na: buiten `--kort` geeft `zoek_samen.py` nu ook
exitcode 1 als een poort niet te meten is. Er kwam geen route, gebeurtenis of migratie bij of af
(het schema blijft op `0010`), en `api/app/config.py` is niet veranderd. Met zoeken op PostgreSQL
verandert er niets: de schrijfpoort komt alleen op SQLite.

| Contract | Wat | Waarom | Test |
|---|---|---|---|
| `AdminSearch` (`api/app/search/types.py`) | nieuw veld `check: AdminCheck \| None = None`, met `AdminCheck(at: str, sampled: int, bad: int)`; alleen aantallen, nooit welke bestanden | de wekelijkse controle (S6) | `test_weekcheck.py`, `test_contracts`, `test_web_contracts` |
| `web/src/admin/searchApi.ts` | `check: { at: string; sampled: number; bad: number } \| null`, gelijk aan de pydantic | idem | `searchTab.test.ts` |
| De tabel `setting` | nieuwe sleutels `search_check` (de laatste uitkomst) en `geo.places` (de bouw waar de keten bij hoort) | S6 en A4 | `test_weekcheck.py`, `test_places_later.py` |
| De jobs | nieuw type `geo.places` (sleutel `geo.places:<bouw>`), en opruimwerk `search.check` (via `jobs.housekeeping`) | A4 en S6 | idem |
| `places.sqlite` | meta `bouw` met een eigen id per bouw; een bestand van vóór I1 heet `zonder-id:<datum>` | A4, review F2 | `test_een_nieuwe_bouw_begint_vooraan` |
| `app.cli geo-load` | zet na de bouw de keten `geo.places` klaar; lukt dat niet, dan een hint op stderr en de exitcode blijft 0 | A4 | `test_cli_geo_load` |
| `PhotoFilters` en `FilterBarProps` (web) | `place`, `country` en `camera` erbij, naar de parameters die de API al had; `places` optioneel in `FilterBarProps` | S7; geen wijziging aan de API | `places.test.ts`, `slots.test.ts`, de e2e van S7 |
| `ImageHint` (web, nieuw) en de vlag `IMAGE_SEARCH_READY` | de zin dat zoeken op beeld later komt, ook als beschrijving van het veld; de slot `BeeldResults` blijft gelijk | S8, web R4 | `page.test.ts`, `lazySlots.test.ts` |
| De meetscripts `e2e/perf/zoek_backfill.py` en `e2e/perf/zoek_samen.py` (geen API) | `zoek_backfill.py`: `--soort photometa` en `--soort geo`, `--geo-bron DIR` (nodig bij geo), de poorten `POORT_PHOTOMETA` 5.000 en `POORT_GEO` 20.000, en bij geo een blok `places` in de JSON. `zoek_samen.py`: `--fotos` en `--scans` (300 en 20 met `--kort`, anders 5.000 en 500), tien poorten waarvan de waarde ook `None` (niet te meten) kan zijn, de sleutel `overgeslagen` in de JSON, en buiten `--kort` geeft nu ook een poort die niet te meten is exitcode 1 (net als een poort die niet gehaald is) | A1, A2 | `e2e/test_zoek_metingen.py` |
| i18n | nieuwe sleutels, in NL en EN en alleen toegevoegd: `admin.search.index.check`, `checkNone`, `checkOk`, `checkBad` en `checkHelp` (S6) en `checkOff` (S6, review F5); `photos_people.filters.country`, `allCountries`, `place`, `allPlaces`, `camera` en `cameraHint` (S7) en `placePhotos` (S7, review W5); `beeld.later` (S8); `search.field.imageLater` (web R4) | S6, S7, S8, web R4 | `check:i18n --strict` |

Intern, zonder contract: de schrijfpoort (`dbgate.py`, met haken in `db.py`, `jobs.py`,
`storage.py`, `routers/files.py` en `search/rebuild.py`), de logger van aiosqlite op INFO (S5),
de werkmap van `render_meta` (S4), en `places.get` en `places_off` (C3).

## Metingen

De metingen van I1 zijn M1–M9. Elk liep alleen, op de kop van I1. M1 komt uit het artefact van
de job `zoek-perf` in CI; M2–M8 liepen in de sandbox ⟦C2: de commit, de cores en de machine van
de metingen, uit metingen.md "Omstandigheden"⟧. Alle getallen, de commando's en de bevindingen
staan in [metingen.md](metingen.md). Per poort staat hieronder de uitkomst zoals het script hem
zegt: gehaald, niet gehaald of niet te meten, nooit alleen de exitcode.

| Meting | Poort | Uitkomst |
|---|---|---|
| M1, zoeken (§4.4) | p95 ≤ 200 ms per toegangsprofiel, de mediaan over 3 rondes | 126,7 ms (het profiel "alles", het ergste; de andere 72,3–85,8), gehaald |
| M1, 4 tegelijk | p95 ≤ 300 ms | 209,4 ms (`/health` ernaast 8,5 ms), gehaald |
| M1, herinneringen | p95 ≤ 50 ms | 27,0 ms, gehaald |
| M1, de kaart | p95 ≤ 150 ms bij 55.000 foto's met een plek | 51,9 ms bij 55.011 foto's, gehaald; met een filter op datum, plaats of land 110,4 ms (≤ 150), op soort, map of camera 430,0 ms (≤ 500), beide gehaald |
| M1, duplicaten | p95 ≤ 300 ms bij 55.000 foto's | 192,7 ms, gehaald |
| M2, de tekst met de worker | ≥ 1.000 documenten per minuut op één core | 1.977 per minuut (mediaan van 3), gehaald |
| M2, de terugval zonder media | rapport | 4.817 per minuut (mediaan van 3) |
| M3, photometa | ≥ 5.000 foto's per minuut | 13.713 per minuut (mediaan van 3), gehaald |
| M4, land en plaats | ≥ 20.000 punten per minuut | 44.495 per minuut (mediaan van 3), gehaald |
| M5, OCR | ≥ 8 pagina's per minuut over de mix, op één core | 23,2 per minuut over de mix (mediaan van 3: 26,2, 19,6, 23,2), gehaald; brieven 38,7–47,7 per minuut bij −5°, 0°, 3° en 7°, 17,5 bij 90°; volle pagina's 17,9–18,0 bij 0° en 3°, 6,9 bij 90° (run 3) |
| M6, uploaden naast de index | een upload wacht ≤ 1 s, ook als de index een tekst van 1 MB met 10 kopieën schrijft | `test_backfill.py`, `test_search_rebuild.py` en `test_search_purge_batches.py`, elk 3 keer op de host en 3 keer in python:3.12-slim: host 241, 263 en 204 ms, `python:3.12-slim` 137, 337 en 277 ms, gehaald; 1 keer op de host met +200 ms per `fsync` (een shim met `LD_PRELOAD`): 739 ms, gehaald |
| M8, rapporten | geen poort, behalve de grootte van het image (tesseract hooguit 75 MB erbij) | ⟦C2: zoek_ui, bench --los, het bijwerken van de index in CI, de duur van e2e-media-perf en het blok kort, uit M8⟧; het image ⟦C2: met hoeveel MB, uit M8⟧ |

**M7, zoeken naast media en OnlyOffice** (`e2e/perf/zoek_samen.py`, §4.5 deel B), per regel van
stap 2 zoals het script hem schrijft:

| Poortregel | Uitkomst |
|---|---|
| media: elke start ≤ 5 s, 0 keer haperen | ⟦C2: gehaald, niet gehaald of niet te meten, met de waarde, uit M7⟧ |
| zoeken p95 ≤ 200 ms | ⟦C2: gehaald, niet gehaald of niet te meten, met de waarde, uit M7⟧ |
| GET /files/root p95 ≤ 200 ms | ⟦C2: gehaald, niet gehaald of niet te meten, met de waarde, uit M7⟧ |
| GET /health p95 ≤ 50 ms | ⟦C2: gehaald, niet gehaald of niet te meten, met de waarde, uit M7⟧ |
| MemAvailable ≥ 1,5 GiB | ⟦C2: gehaald, niet gehaald of niet te meten, met het laagste MemAvailable, uit M7⟧ |
| geen container OOMKilled (DS) | ⟦C2: gehaald, niet gehaald of niet te meten, met de geheugengrens van DS uit docker inspect, uit M7⟧ |
| de app en de worker leven nog, geen crash of OOM-kill | ⟦C2: gehaald, niet gehaald of niet te meten, uit M7⟧ |
| de index leest weer ≤ 90 s na de laatste transcode | ⟦C2: gehaald, niet gehaald of niet te meten, met de waarde, uit M7⟧ |
| OnlyOffice: opslaan ≤ 1,2 × de nulmeting | ⟦C2: gehaald, niet gehaald of niet te meten, met de waarde, uit M7⟧ |
| OnlyOffice: 0 verloren woorden | ⟦C2: gehaald, niet gehaald of niet te meten, uit M7⟧ |

De poort op OOMKilled zegt alleen iets als DS een geheugengrens heeft: `zoek_samen.py` leest
`HostConfig.Memory` met `docker inspect` en noemt het niet te meten als die 0 is; buiten `--kort`
stopt het dan. In deze meting gold ⟦C2: de geheugengrens van DS en van de andere containers die
echt golden, en of OOMKilled false bleef, uit M7⟧. Het hoogste geheugengebruik was
⟦C2: het hoogste geheugengebruik, uit M7⟧. De poorten van de beeldvraag en de container
`analyse` horen bij 0.8.1 en zijn niet gemeten.

**M9, plaatsen na de update (A4), met de echte bronnen.** De test
`api/tests/geo/test_places_later_echt.py::test_upgrade_met_de_echte_bronnen` (marker `geo`,
draait in de job `api` met de andere geo-tests) speelt de update na. Hij maakt 248 foto's met
GPS: 180 bij echte plaatsen (tot ±25 km ernaast), 50 willekeurig over de wereld, de 4 grensparen
en de 5 punten in de Noordzee van Z0, Utrecht, en 4 punten op 180°. photometa leest ze voordat er
een `places.sqlite` is. Daarna bouwt de app `places.sqlite` zoals bij een start (`load.start`), en
loopt de keten `geo.places` in 7 stukken van 37. Daarna heeft elke rij precies wat `lookup()` op
zijn eigen lat en lon zegt, in een `places.sqlite` die apart uit dezelfde bronnen gebouwd is.
Uitkomst: 193 op land, waarvan 179 met een plaats; 55 op zee of zonder land. Het lezen duurde
45,5 s, de bouw 3,1 s en de keten 0,26 s; de hele test 58,78 s. Gehaald: elke foto met GPS op
land heeft een land, en een plaats als er een binnen 30 km ligt.

De test vond een fout in de testfoto's, niet in de app: `_dms` in `api/tests/search/fixtures.py`
gaf voor 54,8 en 6,1 graden (54, 47, 60.0) en (6, 5, 60.0). `render_meta` weigert seconden vanaf
60, dus de foto's bij de Doggersbank en Herzogenrath hadden geen GPS. Nu rondt `_dms` eerst de
seconden in totaal af en deelt dan; op 500.000 waarden verschillen alleen de 86 die 60 gaven
(`d5624b5`, met `test_elke_plek_leest_render_meta_terug`). Dat de app 60,0 seconden weigert,
blijft zo in 0.8.0: een camera die het zo schrijft, geeft geen plek. Dat is een open punt voor
I2.

## Standaarden

I1 zette geen nieuwe standaard in `api/app/config.py`: dat bestand is sinds `16ccb8d` niet
veranderd. 0.8.0 draait op de standaarden van het ontwerp, omdat Jonas over B49–B70 (behalve B53
en B57) en Vraag 4 en 8–14 nog niet koos: de plaatsnamen aan (`PLAATSEN`, `places = "aan"`), OCR
aan op de server (`ocr_enabled = True`, B53), "Tekst in foto's herkennen" per gebruiker uit, de
plek van een foto alleen voor de eigenaar (Vraag 4), en gezichten en zoeken op beeld nog niet
beschikbaar.

Wat I1 wel vastlegde, zijn vaste waarden in de code, geen instellingen:

| Waarde | Waar | Waarom, en welke meting het draagt |
|---|---|---|
| `TIMEOUT` 5 s | `dbgate.py` | zo lang als `busy_timeout`; daarna dezelfde fout als zonder poort |
| `FG_STREAK` 8 verzoeken | `dbgate.py` | dan komt een wachtende achtergrondschrijver aan de beurt, dus niets wacht eeuwig. De backfill met de worker haalde met de poort 1.851–1.859 per minuut (zonder 1.875–1.909), de terugval 4.264–4.383 (zonder 4.309–4.342) ([z2.md](z2.md), Open 12) |
| `HOLD_BACK` 0,2 s | `dbgate.py` | een vrije poort wacht hooguit zo lang op een upload achter het blobslot; met vier uploaders 345–364 ms in plaats van 1.240 ms (review grens F1) |
| `CHECKPOINT_EVERY` 1 s, `CHECKPOINT_IDLE` 3 | `dbgate.py` | het checkpoint buiten de COMMIT, hooguit eens per seconde zolang er commits komen |
| `SLOW_HOLD` 2 s | `dbgate.py` | wie de poort langer heeft, komt in de log |
| `SAMPLE` 200, elke 7 dagen, na een verschil het volgende uur | `search/weekcheck.py` | de 200 uit het ontwerp ("een wekelijkse job draait hem met 200 nodes") |
| `PLACES_CHUNK` 1.000, `PLACES_WAIT` 5 s | `photo_rows.py` | per stuk ±1–3 s opzoeken in een draad en een paar ms schrijven; M9 deed 248 foto's in 0,26 s |

`synchronous` blijft `FULL`. Met `NORMAL` doet een commit geen `fsync` meer en wacht een upload
korter, maar dan kan een opgeslagen wijziging na een stroomstoring weg zijn. De grens van 1 s
houdt tot een `fsync` van ±200 ms extra (met +300 ms was Z2 rood met 1.124 ms); dat staat in de
README onder "Bekende grenzen".

Op grond van de metingen M1–M8: ⟦C2: welke standaard I1 op grond van M1–M8 zet of laat staan,
met de meting die het draagt; of dat er geen verandert⟧.

## Installatietest en terugrollen

De installatietest van zoeken is `infra/single/tests/test_install_search.sh`. Hij heeft twee
delen:

- **Het functiedeel**, zonder Docker te bouwen: de functies van `install.sh` (de reservekopie,
  de bronnen voor plaatsen, `PLAATSEN`, de symlinks, de eindtekst en de commando's van het
  runbook). Lokaal in B1: alle 209 controles geslaagd, ook het deel als root met de echte
  `setpriv` (11 controles, waaronder die van S9). In CI in de job `infra`, stap "Zoeken in
  install.sh controleren".
- **`--stappen`**, de update en terug, echt, met Docker:
  1. 0.7.1 installeren, met het script van 0.7.1 (`80d5bb8`);
  2. bijwerken naar deze versie, zonder netwerk naar de bronnen;
  3. een update waarbij de reservekopie faalt: het script stopt vóór elke wijziging;
  4. opnieuw, met netwerk: de bronnen, `geo-load` in de draaiende app, en een symlink die de app
     op `<bron>.part` zet (root volgt hem niet);
  5. de update erbovenop, met `PLAATSEN=uit` en dan weer aan;
  6. terug naar 0.7.1, met precies de stappen van §3.10;
  7. weer vooruit, met het script van de branch.

`--stappen` draait alleen in de job `infra`, stap "De update en terug naar 0.7.1
(test_install_search.sh --stappen, Z7)". Lokaal mag het niet, en er is te weinig schijfruimte.
In run 99 op `16ccb8d`: 79 goed, 0 fout. Op de commit van 0.8.0:
⟦C2: goed en fout van --stappen in de run op de commit van 0.8.0⟧.

Terug naar 0.7.1 gaat met de stappen van het runbook ([zoeken.md](../runbooks/zoeken.md),
"Terug naar 0.7.1"): kijken of commit `80d5bb8ce9cd937534f06a5bc5e4f54ec8509ad1` er is en het
script van die commit in een bestand zetten, `platform` en `media` stoppen, een verse kopie
(`app.cli db-backup`), `app.cli db-downgrade 0009`, en dan het script van `80d5bb8` met
`PLATFORM_SRC_REF`. `db-downgrade` vraagt eerst wat er moet gebeuren met items uit een regel van
een slim album die niet gedeeld worden, want in 0.7.1 deelt een album elk item. I1 veranderde
niets aan het schema of de migraties, dus de weg terug is dezelfde als die van Z7. Er zijn geen
tags behalve `fase4-basis` en `fase5-basis`; het runbook gebruikt de volledige commit.

Wat I1 aan de installatie veranderde: S9 in `install.sh` (10001 kijkt de bronnen zelf na) en S10
in `test_install_update.sh`. Die laatste test (van fase 4: OnlyOffice over een update) staat niet
in CI en weigerde lokaal (zie [De checks](#de-checks)). OnlyOffice over een update is in I1 dus
niet opnieuw getest; de stappen van `--stappen` in `infra` en `test_install_media.sh --stappen` in
`media-image` dekken de update van de app en van media.

## Wat alleen CI of de LXC kan bewijzen

**Alleen in CI:**

- **De jobs met het image.** `media-image`: `test_ocr_container.sh` (tesseract in het image,
  modus 0711, alleen in de worker), `test_media_container.sh` en `test_install_media.sh
  --stappen`. `images`: tesseract hooguit 75 MB erbij (X1); de grootte ⟦C2: met hoeveel MB, uit
  M8⟧.
- **De update en terug met Docker:** `test_install_search.sh --stappen` in `infra` (zie
  hierboven).
- **SQLite 3.46.1 met de boom van 100.000:** `zoek-perf`, met de harde poorten van §4.4 en sinds
  A3 ook churn en de triggers. M1 komt uit het artefact van deze job. De duur op de commit van
  0.8.0: ⟦C2: de duur van zoek-perf op de commit van 0.8.0⟧.
- **H.264 in de browser:** `e2e-media` en `e2e-media-perf` met `MEDIA_REQUIRE_H264=1`. De
  Chromium 141 van de sandbox kan `avc1.64001f` en `avc1.640028` niet spelen; hier slaan 4 e2e
  over.
- **De geo-tests met de bronnen van de runner:** de stap "pytest -m geo" in `api` haalt de
  bronnen met `tools/geo/haal.py`. Hier liepen ze met een kopie in de cache van de sandbox
  (`PLATFORM_GEO_TEST_SRC`).

**Alleen op de LXC** (de lijst [Op de LXC](README.md#op-de-lxc) in de README):

- **De 200 ms op de CPU van Jonas:** de Zoekbench op `/beheer` of `bench --los` (het runbook,
  "Zoeken meten").
- **De update met de echte data**, de reservekopie en de plaatsnamen (`Geladen: …, gebouwd op …`),
  en daarna land en plaats bij de eigen foto's.
- **De `fsync` van de schijf.** De grens van 1 s voor een upload houdt tot ±200 ms extra per
  `fsync`. Of de schijf van de LXC daaronder blijft, is niet gemeten, en de lijst Op de LXC heeft
  er geen punt voor.
- **De kwaliteit van gezichten en zoeken op beeld op echte foto's:** 0.8.1, en alleen na een ja
  op Vraag 8 en 12 in CI.

## Afronden en 0.8.0

C1 (`ebcbb4f` op `wip/i1-docs`) schreef de teksten voor 0.8.0, met plekken voor wat nog gemeten
werd:

- de README van fase 6 bovenaan voor Jonas: wat er is, wat er anders werkt sinds 0.7.x, de
  bekende grenzen, de vragen en de lijst Op de LXC;
- de README in de hoofdmap en die van `infra/single` ("Bijwerken naar 0.8.0 (zoeken en foto's)"),
  met 0.7.1 als geschiedenis;
- de runbooks: [zoeken.md](../runbooks/zoeken.md) (de schrijfpoort, wat OCR kost, Hervatten) en
  [media.md](../runbooks/media.md);
- het [stappenplan](../stappenplan.md): vijf punten van fase 6 afgevinkt (full-text met OCR, EXIF
  en GPS met de kaart, duplicaten, herinneringen en slimme albums, filters); gezichten en objecten
  niet; een alinea "Versie 0.8.0";
- de [beslissingen](../fase-0/beslissingen.md): bij B49–B70 een notitie dat het zo gebouwd is en
  de standaard is in 0.8.0, maar dat Jonas nog niet koos; alleen B53 en B57 zijn besloten;
- notities in het [bouwontwerp](bouwontwerp.md).

Stap C2 vult de plekken in, met deze versie op zes regels: `api/app/__init__.py`,
`api/pyproject.toml`, `api/uv.lock`, `web/package.json` en twee regels in
`web/package-lock.json` (nu nog 0.7.1). Daarna gaat het op de branch
`claude/stappenplan-uitwerken-lu4uv3`:

- de commit van 0.8.0: `⟦C2: commit⟧`, op ⟦C2: datum⟧;
- de run in CI op die commit: ⟦C2: de run in CI op de commit van 0.8.0 en het aantal groene
  jobs⟧.

Er komt geen tag zonder het ja van Jonas. De README vraagt het hem ("Een tag voor 0.8.0 en
0.7.1?", standaard geen tags), net als waar 0.8.0 staat (standaard op de branch).

## De review van I1

Elk stuk code dat I1 zelf toevoegde, is tegengelezen vóór het samenvoegen, en de integratie als
geheel daarna nog een keer. Per review de bevindingen en wat ermee gebeurde.

**ci-infra** (A3, S9, S10, S11; lenzen "breken" en "regels"):

| Bevinding | Wat | Gebeurd |
|---|---|---|
| S9-hang en R1 (minor) | `GEO_OK` las zonder grens: een symlink naar `/dev/zero` of een groot bestand vlak na `stat` liet `install.sh` hangen | `ba76dfc`: lezen met `nofollow` en `count_bytes`, hooguit de grootte plus één byte; drie nieuwe gevallen in het functiedeel |
| S9-test-vacuous (minor) | de controle "root kijkt niet in de map geo" kon niet falen | `e928d6c`: de echte `geo_ok` wordt gelogd; de controle eist nu wat er moet staan |
| A3-kort-space en R2 (nit) | de spatie na de dubbele punt in het blok `=== kort ===` was weg | `bf62237`, met een controle in `test_zoek_ci.py` |
| R3 (nit) | het commentaar noemde twee metingen met `zoek_perf` in de triggerbestanden, het zijn er vier | commentaar verbeterd, geen test |
| R4 (nit) | spike.md zei nog dat `media-image` de stap S5 heeft | `2a05ad6` |

**api** (A4, S3, S4, S5, S12, S13):

| Bevinding | Wat | Gebeurd |
|---|---|---|
| F1 | niets toetste dat een foto die tijdens het opzoeken verandert (locatie uit, verplaatst) geen plaats krijgt | `36b684f`, test zonder wijziging aan de app |
| F2 | mislukte het klaarzetten van de keten, dan kwam hij nooit meer | `5ae0651`: een id per bouw, elke start kijkt opnieuw |
| R1 | docs met de tekst van vóór I1 | doorgegeven aan docs; gedaan in `cac74dd` |
| R2, R3 | het bewijs van S3 noemde een run met `-m media` waar de test niet in zit; voor S4 stond "de run als nobody wordt groen" als feit | notities verbeterd; S4 is als nobody bevestigd in B1 |

**beheer** (S1, S2, S6):

| Bevinding | Wat | Gebeurd |
|---|---|---|
| F1 en B1 | de controle las over meer dan één momentopname, en zag wat de app intussen schreef als verschil | `507d1ef` |
| F2 en B2 | na een verschil kwam de volgende controle pas na een week | `e1109f6`: het volgende uur opnieuw |
| B3 | een bestand met een verschil in drie tabellen telde drie keer | `4e73235` |
| F3 | Hervatten wekte de pompen vóór de commit | `a710466` |
| F4 | de test raakte de tak voor een herbouw niet echt | `afed2fb` |
| F5 | met LIKE stond er een oude rode uitkomst | `7565237`: "loopt alleen met FTS5" |

**web** (S7, S8):

| Bevinding | Wat | Gebeurd |
|---|---|---|
| W1 | niets toetste dat Land een plaats buiten dat land uit het filter haalt | `acdd152` |
| W5 | twee plaatsen met dezelfde naam waren niet uit elkaar te houden | `fd9f396` |
| W3, W4, R1, R2 | de grens van 500 plaatsen, de pijltjes in Land, `BeeldResults` in het ontwerp | `1535021`: in toetsenbord-z6.md en het ontwerp |
| W2 | het camerafilter onder een libc-collatie op PostgreSQL | afgewezen voor I1: de API van Z3, zie Niet gedaan |
| W6 en R3 | de teksten `notReady` op `/beheer` | afgewezen voor I1: de go/no-go, zie Niet gedaan |
| R4 | het zoekveld in de stand beeld beschreef de nieuwe zin niet | gedaan bij de integratie: `6f7ed56`, `0a82ed3`, `9d27e28` |

**perf** (A1, A2): breken F1 en regels F2 (`e144074`: het zaaien en de stand van de foto's op het
echte schema), breken F2 (`53f85d9`: tests voor de worker, de herbouw van de hash en de stand per
stap), breken F3 en F5 (`67443c3`: tekst en OCR tellen overgeslagen rijen niet als gelezen; dode
code weg), regels F3 (`f0a1341`: een kleinere last dan §4.5 staat erbij), regels F4 (`112415b`:
de grens van DS zoals `office_ds.sh`), breken F4 en regels F1 (`2b17f78`: het runbook over
`zoek_samen`).

**docs:** F1 en F2 (`3fbb85e`: niet doorstrepen wat A3 nog moest doen; `search.progress` blijft
open), F3 en F4 (`78c55b4`: `e2e.md` over `E2E_PERF_OUT` en `test_zoek_z2.py`). F5 afgewezen: het
ging over het verslag van de bouwer, niet over de docs.

**D1** (de schrijfpoort; lenzen "hangen", "grens" en "regressie"; twaalf bevindingen, één
blokkerend):

| Bevinding | Wat | Gebeurd |
|---|---|---|
| HANG-1 | een taak die midden in een transactie stopt, gaf de poort door terwijl SQLite het slot nog had: 5 s en `database is locked` | `a0e8975`: de poort blijft tot de commit, de rollback of de pool; `test_een_taak_die_midden_in_een_transactie_stopt` |
| HANG-2 | een commit terwijl `asyncio.run` afbreekt, startte een checkpointer in een gesloten lus | `test_een_commit_bij_het_afbreken_laat_geen_checkpointer_achter` |
| grens F1 (blokkerend) | uploads wachten op `BlobStore.lock`, niet in de rij van de poort; met vier uploaders de langste 1.240 ms | `6944820`: wie op een `BoostLock` wacht, telt als verzoek; 345–364 ms, de echte pomp 344–557 ms (was 1.182–1.334); vier tests, waaronder `test_uploads_tegelijk_wachten_niet_achter_de_achtergrond` |
| grens F2 | het pad van een UPDATE in `blob_text` is langer dan de INSERT van 1 MB: 417–466 ms, met +200 ms per `fsync` een upload tot 902 ms | beschreven in z2.md; de triggers zijn van 0010 |
| grens F3 | tot welke `fsync` de grens houdt | beschreven: tot ±200 ms extra; bij +300 ms Z2 rood met 1.124 ms |
| grens F4 | Z1 rood naast een andere schrijver | uitgezocht: de grote `fsync` van een ander proces (een buur van 1,5 GB: 1.182 ms); `synchronous=NORMAL` is een open keuze (z1.md) |
| regressie F1 | de poort moest blijven tot de commit klaar is, en een commit moet de checkpointer wekken | `test_de_poort_blijft_tot_de_commit_klaar_is`, `test_een_commit_wekt_de_checkpointer` |
| regressie F2 | de reeks van `FG_STREAK` | `test_na_een_beurt_van_de_achtergrond_telt_de_reeks_opnieuw`, `test_verzoeken_zonder_wachtende_achtergrond_tellen_niet_mee` |
| regressie F3 | de poort terug na een fout of een rollback | `test_een_mislukte_schrijfactie_buiten_een_transactie_geeft_de_poort_terug`, `test_een_rollback_op_een_open_verbinding_geeft_de_poort_terug` |
| regressie F4 | een paar paden die de poort teruggeven, zijn elk alleen gedekt door een ander pad: de uitzondering in de commit, de afzonderlijke listeners van de pool, en de controle op de rij in `try_take` | niet gedaan: die paden vangen elkaar op, en alle listeners van de pool samen weghalen maakt al een test rood |
| regressie F5 | de checkpointer en de log | `test_de_checkpointer_maakt_geen_bestand_en_stopt_als_er_niets_meer_is`, `test_wie_de_poort_lang_heeft_komt_in_de_log`; R10b (de commit vóór het vroege einde van `collect_garbage` in `routers/files.py`) niet, want die sluit alleen een leestransactie die de sessie toch sluit |
| regressie F6 | `uploads.cleanup_loop` en de lus van de mediascanner zijn niet als achtergrond gemerkt; hun schrijfacties staan bij de verzoeken in de rij | niet gedaan: die bestanden horen niet bij D1, en ze schrijven zoals vóór de poort; zie [Niet gedaan](#niet-gedaan-met-de-reden) en z2.md, Open 12 |

De reparaties van de review staan in `6944820`, `a0e8975`, `1cd0778` en `9acd6ff`. Op `9acd6ff`
liep de volle suite twee keer: 5821 goed.

**De integratie als geheel** (op `3280946`; lenzen "breken" en "regels"):

| Bevinding | Wat | Gebeurd |
|---|---|---|
| R1 (minor) | C2 dekte één symptoom: dezelfde volgorde brak nog een sync test met een async fixture, het opruimen `_databases_dicht` en de fixtures van `tests/geo` | `86f5b9c`: een haak `pytest_make_collect_report` in `api/tests/conftest.py` die een tweede knoop de fixtures van zijn conftest geeft; wachter `test_conftest_volgorde.py` |
| R2 (nit) | mislukt het wissen bij een start met `PLAATSEN=uit` en is de volgende start "aan", dan komt er geen keten | `45dcc03`: alleen docs en een commentaar; het runbook noemt de regel `Plaatsnamen: Setting geo.places niet gewist` in de log en de remedie "Plek, camera en datum opnieuw lezen" |
| R3 (nit) | een voorgestelde regel voor de README zei dat een foto die tijdens het wisselen gelezen wordt geen plaats krijgt; de keten vult hem daarna | de regel anders gezegd (hij stond nog in geen bestand) |
| F1 (minor) | het runbook en z7.md zeiden "rood" bij een verschil; de regel is oranje | `083d7c4` |
| F2 (minor) | z0.md zei nog dat S5 in `media-image` draait | `cc436df` |
| F3 (minor) | het runbook suggereerde dat `search-check` de bestanden van de wekelijkse controle laat zien; hij neemt een eigen steekproef, en `--volledig` doet niet alles | `083d7c4` |
| F4 (nit) | in z0-e2e.md ontbrak "pas na de push van I1 in CI" | `cc436df` |

Na de reparaties: 117 goed op de geraakte tests.

## De checks

B1 liep op `8a679bd`, de kop met D1. `0e19180` kwam er twee minuten later bij en raakt alleen
e2e.md en z2.md, dus de code is voor alle checks dezelfde. De sandbox heeft 4 cores en 16,5 GB.

Het zware deel liep na elkaar onder `taskset -c 0-2`, van 23:12 tot 01:30, met niets anders
ernaast. Het lichte deel (web, shellcheck, yamllint, `git diff --check`) liep apart op core 3
met `nice -n 10`, van 23:12:46 tot 23:14:16, dus tijdens het begin van de eerste volle suite en
niet alleen.

| Check | Uitkomst |
|---|---|
| `ruff check` en `ruff format --check` (api en e2e) | alles goed, 730 bestanden al opgemaakt |
| `uv run pytest -q` (api), eerste keer | 5853 goed, 2 overgeslagen, 237 niet geselecteerd, 2 waarschuwingen, in 39:05 |
| `-m media` | 191 goed |
| `-m ocr` | 13 goed |
| `-m geo tests/geo`, met de echte bronnen (`PLATFORM_GEO_TEST_SRC`) | 13 goed |
| als nobody (`env -u SSL_CERT_FILE /tmp/ci-nobody/run.sh`) | 2129 goed, 1 overgeslagen, 78 niet geselecteerd, in 13:13 |
| als nobody met `-m "media or ocr"` | 58 goed |
| `test_install_env.sh` | alle controles geslaagd |
| `test_install_media.sh` (het functiedeel) | alle 127 controles geslaagd |
| `test_install_search.sh` (het functiedeel, ook als root) | alle 209 controles geslaagd |
| `test_install_update.sh` | exit 1, geweigerd: `STOP python:3.12-slim bestaat al; deze test vervangt hem niet` |
| `e2e/slim.sh` | groen in 960 s: de markers `zoek` en `triggers` in `python:3.12-slim` op SQLite 3.46.1 1258 goed (762 s), de e2e met marker `zoek` 71 goed (198 s) |
| e2e, de set van CI (`not perf and not media and not media_perf and not office and not office_real and not provider_real and not ocr`) | 208 goed, 135 niet geselecteerd, in 13:52 |
| e2e `-m office` | 30 goed |
| e2e `-m ocr` | 5 goed |
| e2e `-m media` | 65 goed, 4 overgeslagen |
| `uv run pytest -q` (api), tweede keer | 5853 goed, 2 overgeslagen, 237 niet geselecteerd, 2 waarschuwingen, in 38:33 |
| web: typecheck, vitest, build | goed; vitest 1265 tests in 120 bestanden |
| web: `check:i18n --strict` | 0 meldingen in 288 bestanden |
| web: budget | binnen het budget; de eerste lading JS 98,0 van 100 KB |
| shellcheck, `yamllint -s .`, `git diff --check 2adccd2 HEAD` | schoon |

De set als nobody is die van golf 4 ([z7.md](z7.md), "Gedraaid op de kop na de nacontrole":
`tests/search`, `tests/ocr`, `tests/extract`, `test_backup.py`, `test_analysis_versions.py`,
`test_install_geo.py`, `test_cli_db.py`, `test_jobs_lanes.py`, `test_media_off.py`,
`test_pages_words.py`, `test_fake_worker.py`, `test_media_worker_core.py`,
`test_media_worker_guard.py` en `test_media_no_ffmpeg_outside.py`), plus `tests/geo`, de tests
rond D1 (`test_db_gate.py`, `test_db.py`, `test_backfill.py` en `test_uploads.py`) en die van S4
(`test_render_meta.py`).

De twee e2e die [z4.md](z4.md) wisselvallig noemt,
`test_browser.py::test_tienduizend_openen_scrollen_en_selecteren` en `test_integratie.py`, zitten
in de set van CI en waren in B1 groen (208 goed, geen fout).

Wat niet groen was, met de oorzaak:

- **`test_install_update.sh` weigerde** (exit 1). De test bouwt een eigen `python:3.12-slim` en
  vervangt geen image dat er al is; in de sandbox staat dat image er (voor `slim.sh`). Er was ook
  geen Debian-spiegel bereikbaar. De test is van fase 4 en staat niet in CI. Wat hij toetst
  (OnlyOffice over een update), is in I1 dus niet opnieuw gedraaid.
- **4 e2e van media overgeslagen:** de Chromium 141.0.7390.37 van de sandbox kan
  `avc1.64001f` niet spelen (2 keer native, 1 keer via MSE) en `avc1.640028` niet via MSE. In CI
  draaien ze met `MEDIA_REQUIRE_H264=1`, en daar falen ze in plaats van over te slaan.
- **De 2 waarschuwingen** in de volle suite zijn `InsecureKeyLengthWarning` van jwt in
  `tests/test_office_gate.py` en `tests/test_office_stream.py`; geen fout.

Eerder in I1, vóór B1, waren er rode tests; elk is gerepareerd en staat met de oorzaak onder
[Hoe het liep](#hoe-het-liep): C4 (2 rood onder `nice -n 10`), C2 en R1 (fouten bij de setup door
de volgorde), C1 (1 rood), C3 (3 rood), de e2e in CI run 42890910950 (5 fouten), en de 1 s van D1
in de volle suites van golf 4.

## De na-controle van de review

I1 had geen aparte ronde voor de na-controle, zoals de golven en fase 5 die hadden. Wie een
bevinding repareerde, zette op dat moment de reparatie terug, zag de test rood, en zette haar
weer neer. Dat is hieronder per reparatie opgeschreven waar het gedaan is.

| Reparatie | Zonder de reparatie | Met |
|---|---|---|
| A4 F1 (`36b684f`) | `not_off` weg uit de UPDATE: de rij van Bram krijgt Amsterdam, rood; de gelijkheid van lat en lon weg: de verplaatste foto krijgt Amsterdam, rood | groen |
| A4 F2 (`5ae0651`) | mutaties maken `test_mislukt_klaarzetten_doet_de_volgende_start` en `test_een_nieuwe_bouw_begint_vooraan` rood | groen |
| A4 zelf (`7f9f6f9`) | de repro gaf `(None, None, None)` | groen |
| S9 (`ba76dfc`) | zonder `nofollow` code 0, zonder `count_bytes` 124, met `count=$2` 0; de versie van `7c3a2f1` is in alle drie rood | 209 controles |
| S9 (`e928d6c`) | de mutant MS9d van de review: 3 keer FOUT; `install.sh` van `16ccb8d`: 5 keer FOUT | 209 controles |
| S9 (`5d15206`) | `geo_ok` als root: 2 keer FOUT; zonder `nonblock` code 124 | groen |
| A3 (`bf62237`) | de spatie weer weg met sed: `test_zoek_perf_draait_churn_en_de_triggers_op_3_46_1` rood | groen |
| A3 (`f211d7b`) | twee mutanten van de stap in ci.yml: rood | groen |
| S11 (`86a4c47`) | tegen de oude ci.yml: `test_media_image_heeft_de_stap_s5_niet_meer` rood | groen |
| S4 (`b4e0e5f`) | de oude regel: `PermissionError` en `FileNotFoundError` | groen, ook als nobody in B1 |
| S5 (`6eaaf95`) | zonder de pin: `('aiosqlite', ['geheimwoord7'])` | groen |
| S6 F1 en B1 (`507d1ef`) | de oude kop: `AdminCheck(sampled=30, bad=2)` | `bad == 0` |
| S6 F2 en B2 (`e1109f6`) | de test faalde vóór de fix | groen |
| S6 B3 (`4e73235`) | `bad` was 3 voor één hernoemd bestand | 1 |
| S6 F4 (`afed2fb`) | `found.rebuilding or` weg: rood | groen |
| S6 F5 (`7565237`) | SearchTab terug: 2 vitest rood | groen |
| S1 en F3 (`487e590`, `a710466`) | de wekkers zagen alleen de oude `run_after`: rood | groen |
| W1 (`acdd152`) | de mutatie van de review (het land direct gezet): de e2e en de vitest rood | groen |
| W5 (`fd9f396`) | de oude `places.ts`: 1 rood, 5 goed | groen |
| perf (`e144074`, `53f85d9`, `67443c3`, `f0a1341`, `112415b`) | de mutanten van die review (daar M9–M20 genoemd, los van de metingen): elk maakt zijn nieuwe test rood | 38 tests van `test_zoek_metingen.py` goed |
| C1 (`a97eb70`) | 1 rood | 26 goed in `test_admin_search.py` |
| C2 (`ddbffda`) | in de volgorde van de fout: 60 goed en 2 fouten; `test_anyio_markering.py` rood op precies die 2 | 62 goed |
| C3a (`1dc4dc1`) | `load.py` terug: de twee tests rood (rij blijft leeg; `['done','queued'] != ['done']`) | groen |
| C3b (`4acfc52`) | `places.py` terug: rood (beide foto's leeg) | groen |
| C4 (`995fa4f`) | onder `nice -n 10`: 2 rood, `assert 19 == 15` | 2 goed, ook zonder `nice` |
| R1 (`86f5b9c`) | zonder de haak: `test_conftest_volgorde.py` 1 rood (32 tests met andere fixtures) | 2 goed; het verzamelen blijft 5787 van 6024 |
| web R4 (`6f7ed56`) | `IMAGE_SEARCH_READY` op true: de 2 nieuwe tests rood | groen |
| `2adccd2` | met `test_pwa.py` ervoor: 5 fouten | 39 goed |
| D1 (`684cb39` tot en met `1cd0778`) | alle bestanden van de app terug: 13 rood, 38 goed; elke regel van de poort apart terug: 13 keer rood | groen; de volle suite op `9acd6ff` twee keer 5821 goed |

Zonder een opgeschreven run met de reparatie terug: S2 (alleen bewezen door de tests in andere
volgordes te draaien), S3 (de race kwam lokaal niet terug, dus er was niets om rood te zien), S10
(alleen shellcheck en een container), S12 en S13, de regressietest van M9
(`test_elke_plek_leest_render_meta_terug`), R3 van ci-infra (een commentaar), en de reparaties die
alleen docs raken (R4 van ci-infra, `1535021` van web, `2b17f78` van perf, de docs-review, R2 en R3
en F1–F4 van de integratie).

## Klaar als: waar elk punt bewezen is

Per regel van §4.0 van het [bouwontwerp](bouwontwerp.md), met waar het laatste bewijs ligt:
**hier** (in de sandbox), **in CI** (de job en de stap) of **op de LXC** (de lijst
[Op de LXC](README.md#op-de-lxc)). "In CI" is groen in run 99 op `16ccb8d` en run 101 op
`2adccd2`; op de commit van 0.8.0 ⟦C2: de run in CI op de commit van 0.8.0 en het aantal groene
jobs⟧.

| Klaar als (stappenplan) | Waar | Bewijs |
|---|---|---|
| **Een zoekopdracht over 100.000 bestanden antwoordt binnen 200 ms** | **in CI**; de CPU van Jonas **op de LXC** | `api/tests/search/test_perf_100k.py` en `e2e/perf/zoek_100k.py`: p95 ≤ 200 ms per toegangsprofiel, op SQLite 3.46.1, in `zoek-perf`, stap "§4.4 100.000 bestanden binnen 200 ms (Z1; de poorten zijn hard)". M1: 126,7 ms, en met 4 tegelijk 209,4 ms (in run 99 206,6 ms, grens 300). LXC: `bench --los` |
| **OCR-tekst is doorzoekbaar** | **in CI** en **hier** | `e2e/test_zoek_ocr.py` (marker `ocr`, de echte tesseract) in de job `ocr`, stap "e2e -m ocr"; de poort in de stap "De poort van §4.5 (130 scans, taskset -c 0, ≥ 8 pagina's per minuut)" (run 99: 20,5 per minuut; M5 23,2 per minuut over de mix); `test_ocr_container.sh` in `media-image`. Hier in B1: `-m ocr` 13 goed, e2e `-m ocr` 5 goed |
| **Gezichten zijn te benoemen en te zoeken** | met de hand **hier** en **in CI**; automatisch **open voor 0.8.1** | `e2e/test_zoek_personen.py` (in de set van `e2e`) en `api/tests/people/test_people_privacy.py` (in `api`). Automatisch vinden (X2): `test_zoek_gezichten.py` en `test_zoek_gezichten_echt.py` en de jobs `api-analyse` en `analyse-image` bestaan nog niet |
| **Privacy van gezichten** | **open voor 0.8.1** | de tests uit §4.0 (`test_faces_*.py`, `test_db_pragmas.py`) horen bij X2 en bestaan nog niet. In 0.8.0 zijn er geen vectoren van gezichten: de schakelaar "Gezichten herkennen" op `/account` gaat niet aan |
| **Gezichten en objecten, semantisch zoeken (X3)** | **open voor 0.8.1**, of een fase 6b | `e2e/test_zoek_beeld.py` en `test_beeld_*.py` bestaan nog niet; de stand beeld zegt dat het in een volgende versie komt (S8) |
| **Full-text voor pdf, docx, xlsx, pptx, txt, md** | **hier** en **in CI** | `api/tests/extract/test_extract_*.py` (html, media, no_pdfium_in_app, normalize, office, pdf, plain, rtf, subprocess, terugval) in `api`; `e2e/test_zoek_z2.py` in `e2e` (alleen via de API), zijn test met de worker in `e2e-media`. M2: 1.977 per minuut met de worker |
| **EXIF en GPS, kaart, offline plaatsnamen** | **hier** en **in CI** | `api/tests/geo/*` in `api`, met de stap "pytest -m geo (grenzen, zee, de plaats tegen brute kracht, 180°)" op de echte bronnen, en sinds I1 `test_places_later.py` en `test_places_later_echt.py` (M9, gehaald); `e2e/test_zoek_kaart.py` (0 verzoeken naar andere hosts) in `e2e`; de kaart in `zoek-perf`, stap "§4.4 de kaart binnen 150 ms bij 55.000 foto's …" (M1: 51,9 ms). M3 13.713 per minuut, M4 44.495 per minuut. LXC: "De plaatsnamen zijn gebouwd" |
| **Duplicaten en bijna-duplicaten** | **hier** en **in CI** | `api/tests/dupes/test_dupes.py` en `test_phash.py` in `api`; `e2e/test_zoek_dubbel.py` in `e2e`; `zoek-perf`, stap "§4.4 de duplicaten binnen 300 ms" (M1: 192,7 ms) |
| **Herinneringen en slimme albums** | **hier** en **in CI** | `api/tests/photos/test_memories.py`, `api/tests/albums/test_album_rules.py` en `test_album_share_new.py` in `api`; `e2e/test_album_regel.py` in `e2e`; `zoek-perf`, stap "§4.4 herinneringen binnen 50 ms" (run 99 28,5 ms; M1: 27,0 ms) |
| **Filters: type, datum, persoon, locatie, tag, bibliotheek** | **hier** en **in CI** | `api/tests/search/test_search_filters.py` (elk filter en elke combinatie van twee, tegen de referentie) in `api`; voor `/fotos` sinds S7 ook `test_plek_land_en_camera_op_fotos` in `e2e` |
| **Alles met het toetsenbord (sinds fase 3)** | **hier** en **in CI** | de e2e per scherm met alleen het toetsenbord in `e2e`; de lijsten `toetsenbord-z0.md`, `-z4`, `-z5`, `-z6`, `-z7` en [toetsenbord-x1.md](toetsenbord-x1.md) |

Daarnaast twee punten uit §4.5 die de README onder "Klaar als" noemt:

| Punt | Waar | Bewijs |
|---|---|---|
| **Naast media en OnlyOffice** blijft zoeken binnen 200 ms, hapert geen film en gaat geen woord verloren | **hier**, de CPU van Jonas **op de LXC** | M7 (`e2e/perf/zoek_samen.py`): ⟦C2: elke poortregel van stap 2 gehaald of niet en het hoogste geheugengebruik, uit M7⟧. In CI alleen `--kort`, als rapport, in `e2e-media-perf` |
| **Een upload wacht hooguit 1 s**, ook als de index een tekst van 1 MB met 10 kopieën schrijft | **hier** en **in CI**; de schijf van Jonas is niet gemeten | `test_backfill.py::test_een_upload_wacht_nooit_langer_dan_1_s_tijdens_de_backfill`, `test_search_rebuild.py::test_een_upload_wacht_hooguit_1_s_en_de_index_klopt` en `test_search_purge_batches.py::test_een_upload_wacht_niet_op_het_hele_wissen` in `api`, en `test_db_gate.py` voor de volgorde zonder klok; M6: 337 ms. Geldt tot ±200 ms extra per `fsync` |

Open voor 0.8.1 blijven X2 (gezichten automatisch, met hun privacy) en X3 (zoeken op beeld), met
de container `analyse` en de jobs `api-analyse`, `analyse-image` en `beeld-model`. Schuift X3 naar
een fase 6b, dan blijft "semantisch zoeken" in het stappenplan open.
