# A0-db: de database van gezichten en beeld

Dit is het deel van de database van A0 ([bouwontwerp.md](bouwontwerp.md), §2 "A0", §3.1a, §3.5
en "Het wissen"; Keuze 61–64, 69 en 76). Het andere deel, de container `analyse` als contract,
is A0-analyse en staat in [a0-analyse.md](a0-analyse.md); het geheel na de samenvoeging staat in
[a0.md](a0.md). A0-db bouwt geen functie: het legt de tabellen, de
triggers, het wissen en de haken vast, zodat X2a en X3a kunnen beginnen zonder een bestand van
A0 te wijzigen.

Wat vaststaat: 0.8.0 is uit. Zolang de analyse uit staat (de standaard), doet het platform wat
0.8.0 doet: dezelfde routes, dezelfde antwoorden, geen nieuwe fout en geen tragere weg (zie
Metingen). Eén ding is wel anders, met opzet en volgens het ontwerp: krijgt een foto nieuwe
inhoud, dan verdwijnt het kader van een persoon op die foto (de persoon blijft); zie Open 1.
De versie blijft 0.8.0. Terug kan altijd: `app.cli db-downgrade 0010` naar 0.8.0, `0009` naar
0.7.1.

## Wat er is

### Migratie 0011: gezichten (`0011_gezichten.py`)

Vijf nieuwe tabellen, zeven indexen en drie triggers. Geen bestaande tabel verandert en niets
herschrijft `node`.

| Tabel | Sleutel | Wat |
|---|---|---|
| `face` | `id` (uuid) | een gezicht: `node_id`, `owner_id`, het kader `x y w h` (0–1), `score`, `px`, `vec` (NULL is gewist), `version`, `cluster`, `suggest_id` (→ `person`, SET NULL), `suggest_score`, `suggest_why`, `hidden`, `created_at` |
| `face_person` | (`face_id`, `person_id`) | een oordeel: `verdict` `ja` of `nee`, `owner_id`, `created_at`; hooguit één `ja` per gezicht (`ux_fp_ja`) |
| `face_scan` | `node_id` | de scan van een foto: `owner_id`, `version`, `state`, `faces`, `tries`, `code`, `prio`, `queued_at`, `updated_at` |
| `face_owner` | `owner_id` | per eigenaar: `run`, `grouped_at`, `suggested_at`, `verdict_at`, `served_at`, `wiping` |
| `node_origin` | `node_id` | wie een bestand neerzette: `created_by` (→ `user`, SET NULL), `via_link` |

Elke verwijzing naar `node`, `user` en `person` heeft `ON DELETE CASCADE`, behalve de twee met
SET NULL hierboven. De indexen: `ix_face_node`, `ix_face_open` (`owner_id, suggest_id,
suggest_score DESC`, alleen voorstellen die getoond worden), `ix_face_cluster`, `ix_face_new`
(`owner_id, created_at DESC`, alleen getoond), `ux_fp_ja`, `ix_fp_person` en
`ix_face_scan_todo` (`state, owner_id, prio DESC`).

De triggers:

- `node_au_owner_face`: een andere eigenaar, dan weg met de gezichten en de scan van die node
  (Keuze 25a).
- `node_au_blob_face`: nieuwe inhoud, dan weg met de gezichten en de scan, en het kader van
  `photo_person` leeg (Keuze 25a; dat hoorde in 0010, maar 0010 was gepusht: z0b.md Afwijking 1).
- `person_bd_face`: vóór een persoon weggaat, worden zijn bevestigde gezichten "niet tonen":
  `hidden` 1, en `vec`, `suggest_*` en `cluster` NULL (Keuze 64). `face_person` gaat daarna via
  de cascade.

De downgrade zet `secure_delete` aan, maakt de vijf tabellen leeg (kinderen eerst), haalt de
triggers en de tabellen weg. Daarna maken `app.migrate` en `app.cli` de WAL leeg (zie onder).
Personen en `photo_person` blijven.

### Migratie 0012: zoeken op beeld (`0012_beeld.py`)

- `blob_embed`: een gewone rowid-tabel (`id`), `blob_sha` (→ `blob.sha256`, CASCADE, uniek via
  `ux_blob_embed_sha`), `model`, `version`, `vec` (NOT NULL), `updated_at`.
- `beeld_owner`: per gebruiker (`user_id`, CASCADE) `gen`, `built_gen`, `built_rows` (alle drie
  NOT NULL DEFAULT 0) en `built_at` (NULL: nog nooit gemaakt).
- `photo_pref.beeld`: TEXT NULL. NULL is de standaard uit `PHOTO_PREFS` (Vraag 11), zodat een
  ander antwoord op Vraag 11 alleen de code verandert. Een nieuwe rij van `PATCH
  /me/photo-settings` laat hem NULL, tenzij `beeld` zelf verandert.
- Drie triggers die `beeld_owner.gen` ophogen (BUMP van Keuze 76): `node_ai_beeld` (een nieuw
  beeldbestand), `node_au_beeld` (een andere eigenaar of inhoud, als het voor of na een beeld
  is) en `node_ad_beeld` (definitief wissen). BUMP is een gewone `INSERT … WHERE NOT EXISTS` en
  een `UPDATE`, geen upsert, en voegt alleen een rij toe als de gebruiker nog bestaat (BZ8).

De upgrade maakt geen rijen in `beeld_owner`: `gen` begint bij de eerste verandering. De
downgrade haalt de triggers, de twee tabellen en de kolom weg (gewone `DROP COLUMN`, geen kopie
van `photo_pref`).

### `app/search/schema.py` en de modellen

`TRIGGERS_0011`, `INDEXES_0011`, `TABLES_0011`, `TRIGGERS_0012`, `INDEXES_0012`, `TABLES_0012` en
`bump()` staan naast die van 0010; `TRIGGERS` is ze allemaal. Elke migratie heeft een bevroren
kopie; `test_schema_triggers.py` eist dat ze gelijk zijn, en dat `sqlite_master` na head precies
`TRIGGERS` en de indexen heeft. De modellen `Face`, `FacePerson`, `FaceScan`, `FaceOwner`,
`NodeOrigin`, `BlobEmbed` en `BeeldOwner` staan in `app/models.py`, met dezelfde indexen
(`test_modellen_gelijk_aan_0011_en_0012`: `compare_metadata` geeft niets).

`app/search/rebuild.py` (`search-triggers`): wat zonder triggers bleef hangen, gaat bij het
terugzetten weg, nu ook `face` en `face_scan` van een node met een andere eigenaar; en zijn er
triggers teruggezet, dan gaat `gen` van elke eigenaar één omhoog.

### `secure_delete` en `temp_store` op elke verbinding (`app/db.py`)

`db.privacy(conn)` zet `PRAGMA secure_delete=1` en `PRAGMA temp_store=2` (Keuze 64, K1-03). De
connect-haak van de pool roept hem aan, en ook elke eigen verbinding van de app: het checkpoint
van `dbgate`, de controle van de week (`search/weekcheck.py`), `db.wal_truncate`, en de engine
die alembic zelf maakt (`migrations/env.py`; met de verbinding van de app heeft die hem al).

Zonder `privacy` blijven, met reden:

- `app/backup.py`: alleen de standaardbibliotheek (install.sh draait hem in het oude image); hij
  zet zelf `secure_delete = ON` op de kopie, en leest de bron alleen.
- `app/search/bench.py`, de geo-bronnen en de `:memory:`-database van `rebuild`: geen
  `platform.db`, en niets van gezichten.

### De WAL leegmaken (`app/db.py`, `app/migrate.py`, `app/jobs.py`, `app/cli.py`)

- `db.wal_truncate(path) -> Checkpoint(busy, log, done)`: één keer, op een eigen verbinding met
  `busy_timeout` 0: eerst `wal_checkpoint(PASSIVE)` (neemt het schrijfslot niet), dan
  `TRUNCATE`. Zit iemand in de weg: `busy` 1, meteen terug. Synchroon; in een thread draaien.
- `db.wal_truncate_retry(path, budget)`: opnieuw na 50 ms, dan steeds het dubbele tot 5 s,
  hooguit `budget` seconden. Het wachten gebeurt met `asyncio.sleep`, nooit in SQLite.
- `migrate.checkpoint(settings) -> bool`: na de transactie van alle migraties (eerder kan niet),
  met een budget van 10 s; lukt het niet, dan één regel met alleen de getallen.
  `migrate.upgrade` en `cli.db_downgrade` roepen hem aan.
- `jobs` `db.checkpoint`: eens per uur bij het opruimwerk, één poging; bezet, dan één regel op
  INFO met alleen de getallen, en het volgende uur opnieuw.

### `node_origin` (`app/origin.py`)

- `origin.placed(session, node_id, actor_id)`: in `files.place_file` als er nieuwe inhoud is
  (een nieuw bestand, of vervangen door andere inhoud; dezelfde inhoud nog eens: niets).
  `actor_id` None is een uploadlink: `created_by` NULL, `via_link` 1 (zo noemt `place_file` dat
  al). Ook `restore_version`: wie terugzet.
- `origin.copied(session, pairs, actor_id)`: in `files.copy` en `batch.copy_many`, voor de
  bestanden (een map krijgt geen rij). De kopie is van wie kopieert, tenzij het origineel van een
  ander kwam (`via_link` niet 0, `created_by` NULL, of niet de eigenaar): dan gaat die herkomst
  mee. Zo wordt een upload van een ander nooit de jouwe door hem te kopiëren.
- Geen rij (alles van vóór 0011): telt als door de eigenaar geüpload (Vraag 9).

Alles in de transactie van de aanroeper; een upsert per bestand (`on_conflict_do_update`),
kopieën in stukken van 500.

### De haken van personen (`app/people/hooks.py`)

| Haak | Waar | Wanneer |
|---|---|---|
| `person_removed(session, owner_id, node_id, person_id)` | `DELETE /files/{id}/people/{person}` | na `P.take_off` |
| `persons_merged(session, owner_id, source_id, into_id)` | `POST /people/{id}/merge` | vóór `P.merge`: de bron en zijn `face_person` zijn er nog |
| `person_deleted(session, owner_id, person_id)` | `DELETE /people/{id}` | na de flush: `person_bd_face` heeft gelopen, de persoon is weg |

X2 meldt zich aan met `register_person_removed`, `register_persons_merged` en
`register_person_deleted` (twee keer aanmelden telt één keer). Alle drie lopen in de transactie
van de actie, vóór de commit, onder het slot van de eigenaar. Een fout gaat door: dan gebeurt de
actie niet (zoals `search.hooks.photo_copy`). Zonder aanmelding doen ze niets.

### `db-downgrade` en `db-backup` (`app/cli.py`, `app/backup.py`)

`db-downgrade` kent 0011 en 0012: hij zegt wat er weggaat (`lost_lines`: eerst beeld, dan
gezichten, dan zoeken) en maakt na de downgrade de WAL leeg. `db-backup` en install.sh gebruiken
`app/backup.py` zoals het was: die wiste al `face_person`, `face`, `face_scan`, `face_owner` en
`blob_embed` in de kopie, voor elke tabel die bestaat.

## Vormen die A0-db vastlegde

Wat het ontwerp open liet, en hoe het nu is. X2a en X3a bouwen hierop.

1. **De triggers, indexen en tabellen als SQL in `schema.py`**, net als 0010: de indexen zijn
   gedeeltelijk en hebben `DESC`, en dat ziet `compare_metadata` niet. `test_schema_triggers.py`
   vergelijkt ze met `sqlite_master`.
2. **BUMP alleen voor een bestaande gebruiker** (BZ8). Een gebruiker wissen wist zijn nodes via
   de cascade; zonder deze voorwaarde kwam zijn rij in `beeld_owner` dan terug.
3. **`node_au_beeld` kijkt naar `OLD.ftype` en `NEW.ftype`**, en hoogt de oude en de nieuwe
   eigenaar op. Is dat dezelfde, dan gaat `gen` met 2 omhoog; X3 kijkt alleen of `gen >
   built_gen`.
4. **Wat `gen` niet verandert:** een andere naam of een andere mime zonder nieuwe inhoud, de
   prullenbak in en uit (de node blijft), en een kopie van een map (dat is een INSERT: wel).
   Zie Open 2.
5. **`beeld_owner`**: `built_gen`, `built_rows` NOT NULL DEFAULT 0, `built_at` NULL is nooit
   gemaakt. De upgrade maakt geen rijen.
6. **`photo_pref.beeld` NULL is de standaard** (Vraag 11), zie boven.
7. **Het checkpoint na een migratie komt na de hele transactie**: alembic draait alle migraties
   in één transactie, en binnen een transactie kan een checkpoint de WAL niet leegmaken. Elk
   uur één poging; na een migratie opnieuw met backoff, tot 10 s.
8. **`node_origin` bij een kopie**: de herkomst van een ander gaat mee (zie boven). Een versie
   terugzetten is van wie terugzet: wie de versie destijds maakte, staat nergens (Open 3).
9. **De reservekopie houdt `node_origin` en `beeld_owner`.** Geen van beide is biometrisch, en
   zonder `node_origin` zou een teruggezette kopie elk bestand als van de eigenaar zelf tellen,
   dus onder zijn toestemming scannen.
10. **`person_deleted` na een expliciete flush** in `routers/people.py`: zo heeft de trigger
    gelopen als de haak komt. Zonder haak verandert er niets (dezelfde transactie, dezelfde
    commit).
11. **De tests van de triggers en de migraties staan in `tests/search/`** (`test_triggers_*`,
    `test_migration_001*`, `test_backup_gezichten.py`): zo krijgen ze de markers `zoek` en
    `triggers`, en lopen ze ook in `e2e/slim.sh` op SQLite 3.46.1 (python:3.12-slim).
12. **`test_migration_0010.py` gaat naar `upgrade('0010')`** (K2-16), niet naar head; de test van
    een lege database heet nu `test_lege_database_naar_0010`.
13. **`app/people/faces_types.py` blijft zoals Z0b hem neerzette** (z0b-api.md, punt 4): A0-db
    legt hem zo vast en veranderde er niets aan. De kolommen passen erop: `face.suggest_why` is
    `sim` of `tag` (`FaceSuggest.why`), het kader `x y w h` is dat van `Box`, en een vector of
    score heeft geen veld. De modellen heten zoals `tests/search/test_own_live.py` ze al kende
    (`Face`, `FacePerson`, `FaceScan`, `BlobEmbed`); die lijst bleef gelijk.

## Afwijkingen van het ontwerp

1. **Het kader van `photo_person` gaat bij nieuwe inhoud in 0011, niet in 0010** (z0b.md
   Afwijking 1). Dat is zichtbaar anders dan 0.8.0; zie Open 1.
2. **Bestanden die niet van A0 zijn, kleine wijzigingen:** `app/dbgate.py` en
   `app/search/weekcheck.py` (één regel `privacy`), `migrations/env.py` (de connect-haak als
   alembic zelf een engine maakt), `app/routers/batch.py` (`copy_many` vult `node_origin`; het
   ontwerp noemt alleen `files.py` en de uploads), `app/prefs.py` en
   `app/routers/photo_prefs.py` (de kolom `beeld`), `app/search/rebuild.py` (zie boven),
   `tests/search/conftest.py` (de marker `triggers` voor de nieuwe bestanden),
   `tests/test_cli_db.py` (head 0012, de exacte lijsten),
   `infra/single/tests/test_install_search.sh` (stap 3 zette een tabel `face` neer om de kopie te
   laten falen; die bestaat nu: het is een trigger op `face_owner` geworden) en
   `tools/zoekbench/bench.py` (de bevroren kopie van het schema van head: `test_zoekbench.py`
   eist elke trigger van head letterlijk, dus ook de zes van 0011 en 0012, met de tabellen die
   ze raken; die blijven in de bench leeg, en de boom bouwt nu met de BUMP van `node_ai_beeld`,
   zoals de app schrijft).
3. **Dit stuk heet `a0-db.md`**, naast `a0.md` van A0-analyse, zodat de twee takken niet in
   hetzelfde bestand schrijven. Bij de samenvoeging werd dat `a0-analyse.md`, en is
   [a0.md](a0.md) het verslag van het geheel.

## Metingen

Op deze machine (SQLite 3.53.1, `taskset -c 0-2`).

| Wat | Voor (59e0250) | Na |
|---|---|---|
| 0010 → 0012 op 100.000 nodes (`test_0010_naar_0012_op_100000_nodes`) | – | 36–42 ms (drie runs), 79 pagina's in de WAL; de database heeft 30.814 pagina's |
| 500 beeldbestanden erbij, elk in een eigen transactie: mediaan / p95 | 0,149 / 0,525 ms | 0,155 / 0,525 ms |
| 200 keer nieuwe inhoud: mediaan / p95 | 0,065 / 0,204 ms | 0,079 / 0,218 ms |
| M6 kort (`test_backfill.py`): 11 uploads naast het indexeren, langste / mediaan | 165–201 / 40–44 ms (vier runs) | 155–269 / 40–47 ms (zes runs) |
| M6 kort: de tekst van 1 MB bij 11 nodes | 215–233 ms | 206–290 ms |
| M6: de andere uploads (251 nodes, de herbouw in 7 en 27 stukken, het wissen los en legen), de langste over alle runs | 164 ms | 193 ms |
| §4.4 (`test_perf_100k.py`), p95 zoeken: eigenaar / o5 / o20 / groep / gast / alles | 55,3 / 64,1 / 77,9 / 66,1 / 60,8 / 98,3 ms | 58,3 / 65,5 / 76,5 / 66,3 / 60,9 / 94,3 ms |
| §4.4: 4 tegelijk p95 / `/health` p95 / eerste vraag na een herstart | 179,6 / 6,7 / 66,9 ms | 179,5 / 6,7 / 67,2 ms; poorten gehaald |

De twee rijen van 500 en 200 zijn zonder en met de triggers van 0011 en 0012, op dezelfde
database, drie rondes door elkaar; het verschil valt binnen de ruis.

M6 is drie keer om en om gedraaid (voor, na, voor, na, …) met dezelfde venv, alleen op de
machine; daarnaast één losse run voor en één na, en twee keer na om en om met een kopie van na
met `secure_delete=0`. De mediaan blijft gelijk. De langste van de 11 uploads was na twee keer
268 en 269 ms (voor hooguit 201 ms), maar met `secure_delete=0` was hij 196 en 223 ms, en na
zelf ook eens 155 ms: het is niet `secure_delete` alleen, en met 11 uploads is het langste getal
een enkel geval. Wat er per upload bij komt: één rij in `node_origin` en de
triggers van 0011 en 0012 (zie de rijen van 500 en 200). De grens van de test (1 s) wordt in elke
run ruim gehaald. Of de langste wachttijd echt hoger ligt, is zo niet te zeggen; zie Open 6.

Het checkpoint naast een schrijver: `test_een_schrijver_wacht_niet_op_het_checkpoint` (150
schrijfacties, met en zonder lezer, geen enkele wacht 100 ms of langer op het schrijfslot). Onder
zware schijflast (dd ernaast) hield één TRUNCATE het schrijfslot eens ±267 ms vast, voor de
`ftruncate` en de fsync; dat hoort bij TRUNCATE, en gebeurt hooguit eens per uur.

## Open

1. **Het kader bij nieuwe inhoud** (`node_au_blob_face`). Het ontwerp (Keuze 25a) wil dat het
   kader van een persoon weggaat als de foto nieuwe inhoud krijgt: het hoort bij de oude
   inhoud. In 0.8.0 bleef het staan. Wie 0.8.0 precies wil houden zolang de analyse uit staat,
   haalt vóór de push de regel `UPDATE photo_person SET x = NULL …` uit `triggers_0011()` en uit
   0011 (en de controle daarvan uit `test_nieuwe_inhoud`); na de push kan dat alleen nog met een
   nieuwe migratie. Bij de samenvoeging zo gelaten, zoals het ontwerp het vraagt; zie Open 1 van
   [a0.md](a0.md).
2. **`gen` bij een andere mime zonder nieuwe inhoud.** `node_au_beeld` luistert alleen naar
   `owner_id` en `blob_id`. Wordt een bestand een beeld (of geen beeld meer) zonder nieuwe
   inhoud, dan telt dat niet. Dat komt in de app niet voor; X3 kan het bij het maken van het
   bestand per eigenaar toch nakijken.
3. **Wie een versie maakte**, staat nergens: terugzetten telt als van wie terugzet.
4. **Na `search-triggers` zonder triggers**: wat in die tijd nieuwe inhoud kreeg, houdt zijn
   gezichten (rebuild ruimt alleen een andere eigenaar op). `face_scan.version` zegt niet welke
   inhoud gescand is; X2 kan het daar vastleggen.
5. **`--stappen` van `test_install_search.sh`** (de update van 0.7.1 naar deze versie en terug)
   draait alleen in CI. Stap 3 is hier met de echte `backup.py` op een database op 0012
   nagespeeld: exit 1 met "Reservekopie mislukt: installatietest", geen kopie; daarna weer een
   kopie.
6. **De langste upload in M6 kort** was na twee keer 268 en 269 ms (voor: hooguit 201 ms), met
   dezelfde mediaan; zie Metingen. Met 11 uploads per run is dat niet te scheiden van de ruis.
   Wie het zeker wil weten, draait M6 vaker om en om op een rustige machine.
7. **PostgreSQL** is alleen met de hand nagelopen (heen, terug naar 0010 en 0009, weer heen,
   PostgreSQL 16); er is geen vaste test die 0011 en 0012 op PostgreSQL draait.
8. **Het checkpoint bij het starten** loopt elke keer, ook als er niets te migreren was. Meestal is
   de WAL dan leeg en duurt het milliseconden. Staat er nog iets in de WAL en houdt een ander proces
   een lezer open (bijvoorbeeld een `sqlite3`-shell van de beheerder), dan probeert het tot 10 s,
   schrijft één regel met de getallen en start de app gewoon. Alleen na een echte migratie zou
   genoeg zijn; dat vraagt dat `migrate.upgrade` weet of alembic iets deed.

## Klaar als

| Uit het ontwerp | Test | Uitkomst |
|---|---|---|
| `test_migration_0011.py`: heen en terug met data | `test_van_0010_met_data_heen_terug_en_weer_heen` (met en zonder FTS5), `test_terug_met_een_lezer_open_lukt_het_checkpoint_later` | groen |
| `test_migration_0012.py`: heen en terug met data, `compare_metadata` | `test_van_0011_met_data_heen_terug_en_weer_heen`, `test_terug_naar_0010_en_0009_in_een_keer`, `test_modellen_gelijk_aan_0011_en_0012` | groen |
| `test_migration_0010.py` naar `upgrade('0010')` (K2-16) | `test_lege_database_naar_0010` en de rest van het bestand | groen |
| `test_schema_triggers.py` met 0010, 0011 en 0012 (K2-17) | `test_de_kopie_in_0011_en_0012_is_gelijk_aan_schema`, `test_triggers_in_de_database_zijn_schema_triggers`, `test_indexen_van_0011_en_0012_in_de_database` | groen |
| `test_db_pragmas.py`: elke verbinding `secure_delete` 1, `temp_store` 2 | `test_elke_verbinding_uit_de_pool`, `test_elke_checkout_tijdens_echte_verzoeken`, `test_de_verbinding_van_een_migratie_en_van_db_downgrade`, `test_alembic_zelf`, `test_de_checkpoints_en_de_controle_van_de_week`, `test_ook_na_een_herstart_van_de_pool` | groen |
| `test_node_origin.py`: upload, kopie, `place_file`, een uploadlink | zes tests, van `test_uploads_in_een_keer_en_via_tus` tot `test_van_voor_0011_geen_rij` | groen |
| het checkpoint (K2-08) | `test_db_checkpoint.py`, acht tests | groen |
| de triggers in hun levensloop | `test_triggers_gezichten.py` (6 × 2), `test_triggers_beeld.py` (6 × 2) | groen |
| de haken (Keuze 63) | `tests/people/test_people_hooks.py`, vijf tests | groen |
| de reservekopie met echte rijen van 0011 en 0012 | `test_backup_gezichten.py` (`db-backup` en zoals install.sh) | groen |
| `own_live` op de vragen van X2 | `test_de_plannen_gebruiken_de_indexen_van_0011` | groen |
| X2a en X3a beginnen zonder een bestand van A0 te wijzigen | de tabellen, modellen, triggers, haken en `node_origin` staan; zie "Vormen" | ja |

## Gedraaid

Op deze machine, elk zwaar ding alleen en onder `taskset -c 0-2`.

- `uv run ruff check .` en `uv run ruff format --check .`: schoon.
- De hele api-suite (`uv run pytest -q`). De eerste keer, vóór 430cf19: 2 failed, 5937 passed,
  2 skipped, 239 deselected (2394 s). `test_search_no_replace::test_niets_in_app`: de upsert in
  `origin.py` stond in twee stappen, en de wachter van Keuze 3 zag de tabel niet (7729431, nu in
  één keten). `test_zoekbench::test_het_schema_van_de_bench_is_dat_van_head`: de bevroren kopie
  van head in `tools/zoekbench/bench.py` miste 0011 en 0012 (430cf19). Op de laatste kop
  (7729431): 5939 passed, 2 skipped, 239 deselected (2199 s); ook
  `test_media_names.py::test_fuzz_10000_namen_met_vaste_seed` is groen.
- Als nobody (`/tmp/ci-nobody/run.sh`, zoals CI), de twaalf testbestanden van A0-db met
  `test_search_no_replace.py` en `test_zoekbench.py`: 125 passed, 2 deselected (115 s).
- `SLIM_CPUS=0-2 e2e/slim.sh`, alleen, op 7729431: groen in 931 s (zoeken en triggers in
  python:3.12-slim met SQLite 3.46.1: 1300 passed in 731 s; e2e met marker zoek: groen in 192 s).
- De e2e-set van CI na de web-build: 238 passed, 135 deselected (819 s).
- §4.4 (`test_perf_100k.py`, marker `zoek_perf`): de poorten gehaald (373 s); de getallen staan
  bij Metingen. M6 ook daar.
- Infra: `shellcheck` en `bash -n` op de scripts van de infra-job, `docker compose config -q`
  (alleen, met office, met de grenzen), `test_install_env.sh` (alle controles),
  `test_install_media.sh` (127) en `test_install_search.sh` (209): groen. `--stappen` niet (Open 5).
- PostgreSQL 16 met de hand: van 0010 naar head, terug naar 0010, weer naar head, terug naar
  0009 en weer naar head (Open 7).
