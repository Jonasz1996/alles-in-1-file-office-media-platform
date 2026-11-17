# P1 pg-sql: de vragen van fase 6 op PostgreSQL (0.8.2)

Pakket P1 van 0.8.2 (plan §2, golf W1, naast P2 pg-0013). Doel: geen vraag van fase 6 faalt
op PostgreSQL, en elke vraag geeft daar wat hij op SQLite geeft. Op SQLite verandert niets: niet
de tekst van een vraag, niet het plan en niet wat er gebeurt.

Branch `wip/i3-pg-sql`, gemaakt vanaf `aa6fa8b`. Alles is per database gekozen: op SQLite loopt
precies de oude code, op PostgreSQL een eigen vorm.

## Wat er veranderde

### Het hulpje: `app/dbdialect.py` (nieuw)

- `name_of(db)`, `is_pg(db)`: de naam van de database achter een sessie of verbinding.
- `of(db)`, `module_for(naam)`, `insert_for(naam, tabel)`: de insert van die database
  (`sqlalchemy.dialects.postgresql` of `.sqlite`). Beide kennen `on_conflict_do_update`,
  `on_conflict_do_nothing` en `.excluded`. Elke upsert schrijft
  `dialect = dbdialect.of(db)` en dan `dialect.insert(Tabel)...` in één keten. Zo ziet de
  wachter van Keuze 3 (`tests/search/test_search_no_replace.py`) de tabel nog.
- `NoIndex(kolom, tight=False)`: op SQLite `+ kolom` (de hint "geen index voor deze term"), op
  PostgreSQL de kolom zelf. Daar bestaat geen unaire `+` op uuid of tekst. `tight=True` geeft
  `+kolom` zonder spatie, zoals de oude tekst van `photo_hash`.
- `FaceSeq()` en `face_seq_sql()`: de volgorde van invoegen van een gezicht. Op SQLite
  `face.rowid`, op PostgreSQL `face.seq`. Die kolom maakt migratie 0013 van P2, alleen op
  PostgreSQL en niet in het model.

### Per bestand

| Bestand | Wat (op PostgreSQL; op SQLite blijft het gelijk) |
|---|---|
| `origin.py` | `placed_stmt` gebruikt `dbdialect.module_for` (zelfde gedrag) |
| `albums_rule.py`, `memories.py`, `photometa.py`, `routers/duplicates.py`, `face_rows.py`, `photo_rows.py`, `photo_hash.py`, `people/faces/scan.py` | de upserts via `dbdialect.of(db).insert(...)` in plaats van de insert van SQLite (die compileert niet op PostgreSQL) |
| `face_rows.py` `no_index()` | via `NoIndex`; de aanroepers blijven gelijk (scan, links, read, verdicts, group) |
| `photo_hash.py` `_scan` | `+blob_hash.blob_sha` via `NoIndex(..., tight=True)` |
| `photo_rows.py` `_insert` | de parameter `g_place` is een `BigInteger`: een plaats-id is 48 bits, en asyncpg zet er anders `::INTEGER` bij |
| `routers/photos_map.py` | de omslag van een cel: `(array_agg(node_id ORDER BY taken_at DESC NULLS LAST, node_id))[1]` (de nieuwste, zonder datum achteraan); de gemiddelden met `float()` (PostgreSQL geeft een `Decimal`) |
| `previews.py` `photo_counts` | één gelabelde uitdrukking in SELECT en GROUP BY: PostgreSQL ziet dan één uitdrukking met één parameter |
| `media/items.py` | de eerste node van een serie: `min()` over de uuid als tekst met `COLLATE "C"`, terug als uuid (PostgreSQL kent geen `min(uuid)`; de hex-volgorde is dezelfde) |
| `people/faces/read.py` | `face.seq` voor `rowid`; een gewone `JOIN` voor `CROSS JOIN ... ON`; `IN (SELECT id FROM gone)` voor `IN gone`; geen `+`; `NULLS LAST` bij de score (zoals SQLite: NULL achteraan bij DESC); een naam voor elk deel van de UNION ALL; `sum(CAST(... AS INTEGER))` voor een som over waar/onwaar; de kolom `a` als tekst (UNION ALL wil één soort per kolom); een uuid als uuid-parameter |
| `beeld/search.py` | `unnest(CAST(:nums AS integer[])) WITH ORDINALITY` voor `json_each(:nums)`, en een gewone JOIN voor `_CrossJoin` |
| `search/access.py` `ranges_sql` | met `dialect="postgresql"`: `jsonb_array_elements(CAST(:ranges AS jsonb))` met `->> 0`/`->> 1`, en `FALSE` voor `0` als er geen bereik is |
| `search/snippets.py` | `left(kolom, WINDOW)` voor `substr(CAST(kolom AS BLOB), 1, 4 × WINDOW)` (een cast naar bytea faalt op een backslash) en `string_agg` voor `group_concat` |
| `media/progress.py` `_bad_value` | kent ook de fout van PostgreSQL: SQLSTATE klasse 22. Dat is 22003 (buiten bereik) van de server, en 22000 als asyncpg de parameter zelf al weigert |
| `people/faces/wipe.py` | `PRAGMA secure_delete = ON` alleen op SQLite (`_checkpoint()` deed op PostgreSQL al niets) |
| `cli.py` `db-backup` | op PostgreSQL: "Een reservekopie van PostgreSQL maakt dit commando niet: gebruik pg_dump (cluster: platform-ctl.sh db-backup).", exitcode 2 |

Het bereik onder een map in `routers/photos_map.py` (`share_check`, regel 339: `mpath` groter
dan het pad van de map en kleiner dan dat pad met `0` in plaats van de laatste `/`) klopt op
PostgreSQL alleen als de vergelijking op bytes gaat. Dat geeft P2 met `COLLATE "C"` op `node.mpath`. Een
parameter krijgt de collatie van de kolom, dus P1 hoeft hier niets te doen. De lokale database
van de tests is ook met `--locale=C` gemaakt.

## Het bewijs dat SQLite gelijk blijft

Drie metingen, elk met de code van vóór P1 (`aa6fa8b`, een eigen worktree) en die van erna.

### 1. De tekst van de vragen met een hint, byte voor byte

`tests/fixtures/pg_dialect_sqlite.json` is gemaakt met de oude code: 16 groepen, 22 vragen. Het
gaat om de vragen met `+kolom`, `rowid` of `CROSS JOIN` (verdicts, links, scan, group, read,
photo_hash, beeld), plus de andere vragen die P1 raakt: de kaart, de tellers, de snippets, de
plekken en de hashes. `test_de_tekst_op_sqlite_is_die_van_ervoor` bouwt ze met de nieuwe code en
vergelijkt de tekst voor SQLite byte voor byte. Uitslag: gelijk. De paar vragen met een hint die
hier niet in staan (het tweede kader in `links.py`, de volgorde van voorstellen en groepen in
`read.py`), dekt de opname van de hele suite hieronder.

### 2. Alle SQL van de hele suite

Een pytest-plugin buiten de repo (`sqlcapture.py` in de scratch van de bouwer) schreef elke
tekst die SQLAlchemy naar de database stuurde. Alleen de tekst, nooit de parameters. Dezelfde
run met de oude code en met de nieuwe code:

- Ervoor (`aa6fa8b`): 5223 verschillende teksten voor SQLite. Erna (`fb13bcd`): 5242. Gelijk:
  5220.
- 19 teksten zijn er alleen erna. Ze komen alle 19 uit de nieuwe `test_pg_dialect.py`: een
  opname van dat bestand alleen heeft ze ook.
- 3 teksten verschillen aan beide kanten, en geen ervan door P1:
  - 2 keer `EXPLAIN QUERY PLAN` van de wachtrij van de jobs, met de tijd van de run als tekst
    erin.
  - 1 keer de `DELETE FROM photo_person ... RETURNING` van `people.take_off` (niet aangeraakt):
    `RETURNING node_id, person_id` of `person_id, node_id`. Die volgorde kiest SQLAlchemy per
    proces. De oude code geeft met `PYTHONHASHSEED` 1 tot 4 de ene of de andere, soms beide in
    één run (`tests/people/test_people.py`, 20 geslaagd per keer).
- De vragen met een hint staan er erna precies zo in als ervoor: `+ face.owner_id` 18 keer,
  `+blob_hash.blob_sha` 4, `suggest_score DESC` 5, `CROSS JOIN` 265, `IN gone` 1, `group_concat`
  127. Daarmee ook het tweede kader in `links.py` en de volgorde in `read.py`.

De opname erna liep op `fb13bcd`. Daarna kwam alleen `234d829` (zie "Controles"): twee hulpjes
krijgen hun kolommen nu van de vraag. De tekst van die twee vragen op SQLite is gelijk aan die op
`aa6fa8b` (beide gecompileerd en vergeleken).

### 3. Snelheid op SQLite, ervoor en erna

Eén machine, elk alleen gedraaid (onder dezelfde lock), dezelfde bomen (seed 6, 7 en 8).
`tests/test_photos_speed.py` en de zoek_perf-tests van de vragen die P1 raakt:
kaart, kaart met filter, duplicaten, herinneringen, gezichten en §4.4.

Alle poorten gehaald, ervoor en erna (`test_photos_speed.py` 1 geslaagd, de zoek_perf-tests 7
geslaagd). In ms; voor de API-vragen de p95 (de mediaan van 3 rondes):

| Meting | Ervoor (`aa6fa8b`) | Erna (`fb13bcd`) | Poort |
|---|---|---|---|
| `/photos/months` | 47.7 | 49.7 | 100 |
| `/photos?month=` | 27.5 | 25.4 | 100 |
| `/photos?after=` | 35.1 | 30.2 | 100 |
| kaart | 40.1 | 37.3 | 150 |
| kaart, filter op `photo_geo` | 78.3 | 73.1 | 150 |
| kaart, filter op `node` | 323.7 | 306.6 | 500 |
| duplicaten | 157.4 | 156.1 | 300 |
| duplicaten negeren (rapport) | 264.0 | 228.0 | - |
| herinneringen | 28.5 | 28.8 | 50 |
| gezichten: voorstellen | 45.2 | 38.9 | 100 |
| gezichten: groepen | 44.2 | 41.9 | 100 |
| gezichten: foto | 11.8 | 11.0 | 100 |
| gezichten: stand | 37.0 | 36.1 | 50 |

§4.4 (zoeken over 100.000 bestanden, SQLite 3.53.1, seed 6, profiel groot), zoeken p95 en
filters p95 per rol:

Twee rondes per kant. Ronde 1: eerst ervoor, dan erna (`fb13bcd`). Ronde 2, zo'n twee uur
later: eerst erna (`234d829`), dan ervoor. Per cel ronde 1 / ronde 2, in ms:

| Rol | Zoeken p95 ervoor | Zoeken p95 erna | Filters p95 ervoor | Filters p95 erna |
|---|---|---|---|---|
| eigenaar | 67.4 / 83.5 | 69.1 / 71.3 | 50.3 / 61.5 | 49.2 / 54.8 |
| ontvanger5 | 73.1 / 92.9 | 76.7 / 86.1 | 62.5 / 70.5 | 62.7 / 66.5 |
| ontvanger20 | 85.2 / 98.8 | 94.3 / 103.3 | 74.0 / 83.9 | 72.6 / 83.9 |
| groepslid | 69.1 / 80.8 | 82.0 / 88.8 | 49.9 / 59.4 | 62.6 / 65.7 |
| gast | 63.6 / 79.6 | 66.3 / 80.1 | 23.5 / 30.1 | 27.0 / 26.8 |
| alles | 106.6 / 127.9 | 109.1 / 135.2 | 72.4 / 65.4 | 87.1 / 86.1 |
| 4 tegelijk (p95) | 201.2 / 233.1 | 207.6 / 262.2 | | |
| eerste vraag | 80.6 / 90.4 | 82.6 / 87.9 | | |

De poorten (zoeken 200, filters 150, 4 tegelijk 300, eerste vraag 300) zijn in alle vier de runs
gehaald. Dezelfde code zelf verschilt tussen twee rondes tot 21 ms (alles ervoor: 106.6 en
127.9), en het verschil gaat beide kanten op (eigenaar en ontvanger5 in ronde 2: erna sneller).
Bij "alles" en "groepslid" zijn de filters erna in beide rondes hoger (alles: 87 en 86 tegen 72
en 65). De weg van die vragen is niet veranderd: `engine.py`, `filters.py`,
`filters_photo.py` en `like.py` raakt P1 niet, `ranges_sql` geeft op SQLite dezelfde tekst (één
vergelijking van een string erbij), en de opname van de hele suite toont dezelfde SQL.

P1 verandert in de zoekmotor van SQLite niets: `FtsEngine` is niet aangeraakt, en de tekst van
`ranges_sql` en van de snippets is op SQLite gelijk (golden texts en de opname hierboven).

## De tests: `api/tests/test_pg_dialect.py`

Drie soorten:

1. **Tekst.**
   - `test_de_tekst_op_sqlite_is_die_van_ervoor`: de golden texts hierboven.
   - `test_op_postgresql_compileert_alles_zonder_sqlite`: compileert dezelfde vragen en de
     upserts voor PostgreSQL. Daar staat dan geen `rowid`, `json_each`, `group_concat`,
     `instr(`, unaire `+`, `PRAGMA`, `sqlite_master`, `CROSS JOIN`, `IN gone` of `BLOB` in. Wel
     staan er de nieuwe vormen in (`face.seq`, `NULLS LAST`, `string_agg`, `array_agg`, `unnest`,
     `ON CONFLICT`).
   - `test_de_zoeker_vindt_wat_alleen_sqlite_kent`: toont dat de zoeker die vormen echt vindt.
   - `test_de_vormen_van_dbdialect`: de tekst en de cache-sleutels van `NoIndex` en `FaceSeq`.
   - `test_serie_en_bereiken_op_sqlite_zoals_ervoor`: de oude strings van `ranges_sql`.
2. **Gedrag.** Deze tests draaien op de database van de test: SQLite, of PostgreSQL met
   `PLATFORM_TEST_DATABASE_URL`.
   - Elke upsert voegt in en werkt dan bij: plekken (`set_meta`, `photo_rows.write` met en
     zonder replace), hashes (`write` drie keer en `mark_none`), herinneringen verbergen,
     gezichten groeperen, `node_origin`, `rescan` van een gezicht, `album_skip`.
   - De kaart kiest de nieuwste foto. Een foto zonder datum is nooit de omslag. De sommen zijn
     floats.
   - De tellers van `photo_counts` en `GET /api/v1/admin/jobs`.
   - Een serie begint bij de kleinste node.
   - De bereiken van de toegang met 0, 1 en 70 wortels: OR en JSON.
   - Een venster met backslashes en tekens van meer bytes.
   - Een stand buiten bereik is een fout in de waarde. Een ontbrekende tabel is dat niet.
   - De gezichten lezen: tellingen, eerste gezichten per groep en per persoon, wat wegvalt.
   - `db-backup` op PostgreSQL geeft exitcode 2 en verwijst naar pg_dump.
3. **Alleen PostgreSQL** (`requires_pg`): `test_buiten_bereik_in_postgresql_zelf`. Een te groot
   getal in de database zelf geeft SQLSTATE 22003, en `_bad_value` kent dat.

Uitslag: SQLite 16 geslaagd, 1 overgeslagen (`requires_pg`); PostgreSQL 17 geslaagd.

## Uitslag op PostgreSQL

68 testbestanden: die van de code die P1 raakt, plus die met tests uit `pg-baseline-fail.args`
op die plekken. Op PostgreSQL 16.15 (UTF8, `--locale=C`), op `fb13bcd`:

```
PLATFORM_TEST_DATABASE_URL=postgresql+asyncpg://platform@127.0.0.1:55441/postgres \
  uv run pytest -q -p no:cacheprovider -o faulthandler_timeout=600 \
  --deselect 'tests/test_office_convert.py::test_on_dead_laat_een_pdf_die_er_is[tussen]' \
  <68 bestanden> --junitxml=...
157 failed, 682 passed, 16 deselected, 5 errors in 677.02s
```

Van de 200 tests in deze bestanden die op `aa6fa8b` faalden (`pg-baseline-fail.args`), slagen
er nu **38**. **162** falen nog. **Geen** test faalt die eerst slaagde.

Nu geslaagd (38): album-regels en delen van een album (11), duplicaten en pHash (4), kaart en
filters (6), `test_places_kapot::test_photometa_faalt_niet...`, een regel van herinneringen,
`test_fotos_compleet` (2), `test_jobs_admin` (2), voortgang (`test_media_progress_api` 2),
series (`test_media_scanner`), photometa (6) en `test_previews::test_crash_van_het_voorbeeldproces`
(2).

De 162 die nog falen, met de reden. Elke reden is bij een paar tests met de hand nagelopen. Geen
ervan ligt aan P1:

| Reden | Tests | Wacht op |
|---|---|---|
| `photo_geo.place_id` is een INTEGER (int4) en een plaats-id is 48 bits: `value out of int32 range` in de test-wereld, in de taak `geo.places` en in `photo_rows.write` | 37 | P2: BIGINT in 0013 en `BigInteger().with_variant(Integer(), "sqlite")` in het model. Daarna loopt een deel tegen `?` in `tests/search/wereld.py` aan (P3) |
| `dead`, `mpath`, `num` en `photo_geo.taken_at`/`dead` blijven leeg: alleen de triggers van SQLite schrijven ze. Dus telt een foto in de prullenbak mee, en is er geen datum voor de omslag of het datumfilter van de kaart | 41 | P2: de triggers van 0013 |
| `target.mpath` is NULL (share-check op een map): `ArgumentError` | 3 | P2: de triggers van 0013 (`mpath`) |
| `test_media_progress::test_apply_knipt_te_grote_getallen_af`: `SEQ_MAX` (2^62) en `MS_MAX` (10^10) passen niet in een INTEGER. `_bad_value` ziet de fout nu goed en laat alleen die ene stand vallen (de buurtest `test_een_stand_die_niet_te_bewaren_is_blokkeert_de_rest_niet` slaagt nu). Maar deze test wil de afgeknipte stand terugzien | 1 | P2: BIGINT voor `playback_state` in 0013 |
| tests/beeld zet de app op een lege SQLite (`no such table: user` / `analysis`): de fixture geeft geen `database_url` door | 54 | P4 (punt 8) |
| `test_photo_settings`: verwacht analyse aan op PostgreSQL | 2 | P4 (punt 8) |
| `test_cli_db`: tijden als ISO-tekst in rauwe SQL (8); `run_cli` zonder `PLATFORM_DATABASE_URL`, dus SQLite (4) | 12 | P3 (punt 4; de test van `db-backup` wordt `sqlite_only`, punt 3) |
| `test_map_filters::test_alleen_node_als_het_filter_hem_nodig_heeft`: de test zelf draait `EXPLAIN QUERY PLAN` | 7 | P3 |
| `test_photo_rows_lifecycle`: de test zoekt met `str(uuid)` in een dict | 1 | P3 |
| `assert 'like' == 'fts'`: PostgreSQL heeft (nog) geen FTS | 4 | P3 en P5 |

Samen: 82 wachten op P2, 56 op P4, 20 op P3, 4 op P3 en P5.

Na `234d829` (de kolommen van twee hulpjes, zie "Controles") opnieuw op PostgreSQL:
`test_map_clusters.py`, `test_map_filters.py`, `test_faces_read.py`, `test_faces_api.py` en
`test_own_live.py` gaven 15 failed, 51 passed: dezelfde 15 als hierboven (P2 en P3).
`test_pg_dialect.py` met `test_own_live.py`: 29 passed.

## Afwijkingen

1. **`requires_pg` in plaats van de marker `pg`.** P2 registreert de markers `pg` en
   `sqlite_only` in `pyproject.toml`. P1 mag dat bestand niet wijzigen. Daarom gebruikt
   `test_pg_dialect.py` een `skipif` op `PLATFORM_TEST_DATABASE_URL`. Na de samenvoeging kan
   dat `pytest.mark.pg` worden. De andere runtime-tests draaien op beide databases, zonder
   marker.
2. **De test maakt `face.seq` zelf.** Zolang 0013 er niet is, zet `_face_seq` in de test op
   PostgreSQL `ALTER TABLE face ADD COLUMN IF NOT EXISTS seq bigint GENERATED BY DEFAULT AS
   IDENTITY` neer, in de vorm van het contract met P2. Met 0013 doet die regel niets.
3. **`photo_geo.place_id` als bigint in de test.** De upsert-test schrijft een echte plaats-id
   van 48 bits. Zolang 0013 er niet is, maakt `_place_bigint` de kolom op PostgreSQL zelf
   `bigint`, zoals 0013 dat doet. Is hij al `bigint`, dan doet die regel niets. De compile-test
   eist `$n::BIGINT` voor `g_place` (review F3).
4. **`mpath` en `dead` met de hand in de test.** `test_de_gezichten_lezen` zet op PostgreSQL
   `mpath` en `dead` zelf, want zonder de triggers van 0013 blijven ze leeg.
5. **De lokale PostgreSQL is opnieuw gemaakt als UTF8.** Het eerste cluster op poort 55441 was
   `SQL_ASCII`. Daar telt `left()` bytes en faalt een emoji. Het cluster is opnieuw gemaakt met
   `-E UTF8 --locale=C`. De image van CI (`postgres:17-alpine`) maakt ook UTF8. Het cluster draait
   nog (PostgreSQL 16.15).
6. **De compile-tests draaien ook in een run op SQLite.** Ze hebben geen database nodig. Zo
   bewaakt ook de gewone suite de vorm voor PostgreSQL.

## Open

1. **Wissen van gezichten op PostgreSQL** heeft geen `secure_delete`. Oude rijen blijven in de
   bestanden tot (auto)vacuum ze opruimt. P4 (punt 5) maakt het wissen daar
   cryptografisch: eerst de sleutel weg, dan de rijen.
2. **`search/engine.py`** roept `ranges_sql` nog aan zonder database (regel 252 en 376), dus in
   de vorm van SQLite. Die motor is SQLite-only (FTS5). Op PostgreSQL zoekt LikeFallback, die
   `ranges_sql` niet gebruikt. P5 (pgfts) geeft `dialect=` mee.
3. **Het bereik onder een map** (`photos_map.py`, `read.py`, `access.py`) rekent op
   `COLLATE "C"` op `node.mpath` van P2. Zonder die collatie geeft een cluster met een andere
   locale verkeerde bereiken. De lokale database hier is `--locale=C` en toont dat dus niet; de
   test van P2 met een andere collatie dekt het. Ook `test_de_gezichten_lezen` rekent erop.
4. **Zoeken op beeld op PostgreSQL** compileert en draait (`unnest`), maar de analyse staat op
   PostgreSQL uit tot P4.

## Controles

Alles in `api/`, op `234d829` als er niets anders staat. `<pg>` is
`postgresql+asyncpg://platform@127.0.0.1:55441/postgres`; `<lock>` is
`flock <kladblok>/heavy.lock`.

| Wat | Commando | Uitslag |
|---|---|---|
| ruff | `uv run ruff check .` en `uv run ruff format --check .` | geen fouten; 800 bestanden al opgemaakt |
| P1 op SQLite | `uv run pytest -q -p no:cacheprovider tests/search/test_own_live.py tests/test_pg_dialect.py` | 28 passed, 1 skipped |
| P1 op PostgreSQL | `PLATFORM_TEST_DATABASE_URL=<pg> uv run pytest -q -p no:cacheprovider -o faulthandler_timeout=600 tests/test_pg_dialect.py tests/search/test_own_live.py` | 29 passed |
| 68 bestanden op PostgreSQL (`fb13bcd`) | zie "Uitslag op PostgreSQL" | 157 failed, 682 passed, 16 deselected, 5 errors; geen nieuwe rode |
| kaart en gezichten op PostgreSQL | `PLATFORM_TEST_DATABASE_URL=<pg> uv run pytest -q -p no:cacheprovider -o faulthandler_timeout=600 tests/people/faces/test_faces_read.py tests/people/faces/test_faces_api.py tests/geo/test_map_clusters.py tests/geo/test_map_filters.py tests/search/test_own_live.py` | 15 failed, 51 passed: dezelfde 15 als op `fb13bcd` |
| hele suite SQLite met opname (`fb13bcd`) | `<lock> env SQLCAPTURE_OUT=… PYTHONPATH=<kladblok> uv run pytest -q -p no:cacheprovider -p sqlcapture` | 3 failed, 6870 passed, 6 skipped, 241 deselected in 3024.87 s |
| hele suite SQLite (`234d829`) | `<lock> uv run pytest -q -p no:cacheprovider` | 6873 passed, 6 skipped, 241 deselected in 3349.83 s; geen rode |
| `test_backfill.py` alleen | `<lock> uv run pytest -q -s -p no:cacheprovider tests/test_backfill.py` | 4 passed (de upload hooguit 233 ms) |
| snelheid | `tests/test_photos_speed.py` en `-m zoek_perf` (kaart, duplicaten, herinneringen, gezichten, §4.4) | alle poorten gehaald, zie boven |

De drie rode van de run met opname. In de hele suite op `234d829` (zonder opname) slagen ze alle drie:

1. **`tests/search/test_own_live.py::test_elke_fotofunctie_gebruikt_own_live`: door P1.** De
   wachter van Keuze 25a kijkt per functie. De nieuwe hulpjes `_cover` (kaart) en `_best_first`
   (gezichten) noemden `PhotoGeo` en `Face` zonder `own_live`. Hersteld in `234d829`: ze krijgen
   hun kolommen nu van de vraag die `own_live_geo` of `own_live` toepast. De wachter is niet
   veranderd. De tekst op SQLite bleef gelijk. Nu groen.
2. **`tests/test_db.py::test_alleen_sqlite_krijgt_de_schrijfpoort`: door de opname zelf.** De
   plugin zet een tweede luisteraar op `before_cursor_execute` van elke engine, en de test telt
   er precies één. Zonder plugin: 2 passed. Met de plugin alleen deze test: weer `assert 2 == 1`.
   De run met opname op `aa6fa8b` had dezelfde rode, en verder geen.
3. **`tests/test_backfill.py::test_een_upload_wacht_nooit_langer_dan_1_s_tijdens_de_backfill`:
   1,32 s** (11 uploads, mediaan 52 ms; de tekst van 1 MB 260 ms). Dit is de test van Open 12 in
   [z2.md](../z2.md): een upload wacht op het schrijfslot van SQLite en de `fsync` van de
   commit, en dat loopt op als de schijf het druk heeft. P1 raakt die weg niet: niet de upload,
   niet de analyse, niet de schrijfpoort, en de opname toont daar dezelfde SQL. Alleen (onder de
   lock, op `234d829`): 4 passed, de langste upload 233 ms. In de hele suite op `234d829`:
   geslaagd (geen rode in die run).
