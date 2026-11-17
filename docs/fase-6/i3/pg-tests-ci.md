# P3 pg-tests-ci: de tests van de API op PostgreSQL (0.8.2)

Pakket P3 van 0.8.2 (plan §2, golf 2, naast P4 pg-analyse). Doel: elke test van de API die niet
van P4 is, slaagt op PostgreSQL, en de job api-postgres in CI blokkeert. De app zelf veranderde
niet: alles hier zit in `api/tests/` en `.github/workflows/ci.yml`. Op SQLite blijft elke test
toetsen wat hij toetste.

Branch `wip/i3-pg-tests-ci`, gemaakt vanaf `cbd0922` (golf 1 samengevoegd, zie [w1.md](w1.md)).

## Kort

- De hele suite op PostgreSQL 16: 6681 geslaagd, 62 rood, 137 overgeslagen, in 42 min 39 s.
  Vóór dit pakket: 271 rood, in 57 tot 58 min.
- Van de 62 rode tests zijn er 60 van P4 (zoeken op beeld en de analyse). De andere twee, en
  de test van search-bench die nu weer op PostgreSQL draait, tonen een fout in de app (zie
  Open). Geen enkele test is nieuw rood.
- Wat op PostgreSQL niet draait, staat hieronder met naam en reden: 46 tests met `sqlite_only`,
  88 met `needs_fts` en 33 varianten `[fts]` die op PostgreSQL niet gemaakt worden. Die laatste
  twee komen met golf 3 terug, via één schakelaar.
- De deadlock van de office-test is weg; niemand heeft de `--deselect` meer nodig.
- CI: api-postgres blokkeert (45 min), en de nieuwe job api-postgres-15 draait als eigenaar
  zonder superuser.

## Wat er veranderde

| Commit | Wat |
|---|---|
| `85e4650` | **Eén sjabloondatabase per run.** De fixture `settings` krijgt op PostgreSQL een kopie (`CREATE DATABASE … TEMPLATE`) van een database die één keer per sessie gemigreerd is, in plaats van per test een nieuwe database te migreren. Het sjabloon komt in een eigen draad met een eigen event loop, raakt dus de engines van de test niet, en gaat aan het eind weg met `WITH (FORCE)`. `PLATFORM_TEST_PG_TEMPLATE=0` zet het uit. De fixture `database_url` (P2) geeft nog steeds een lege database (de migratietests migreren zelf); `database_url_head` geeft er een op de nieuwste revisie. De markers `pg` en `sqlite_only` (P2) werken zoals eerst. |
| `0221371` | **De deadlock van `test_on_dead_laat_een_pdf_die_er_is[tussen]`.** De test schreef de "klaar" van de conversie in dezelfde taak terwijl de sessie van `on_dead` de rij vasthield (`SELECT … FOR NO KEY UPDATE`). Op PostgreSQL wachtte de test op zichzelf; dat was de time-out van api-postgres. Nu loopt de conversie in een eigen taak met een eigen verbinding. Op PostgreSQL ziet de test met `pg_blocking_pids` dat de conversie op het slot van `on_dead` wacht en pas daarna klaar is. De bewering blijft: de PDF is `ready` met de huidige `PDF_VERSION`. Nagegaan: zonder de bewaking in de UPDATE faalt de run op SQLite, zonder `FOR NO KEY UPDATE` die op PostgreSQL. In de hele run op PostgreSQL slaagden `[al]` en `[tussen]`. |
| `736cdff`, `5621b58` | **`sqlite_only` op wat echt alleen SQLite is** (zie hieronder). **CLI-tests met de database van de test:** `database_env(settings)` geeft een kindproces (`python -m app.cli`) de `PLATFORM_DATABASE_URL` van de test; op SQLite verandert er niets. In `test_cli_db.py`: `has_table` via de inspector in plaats van `sqlite_master`, tijden als `datetime` met het type van de kolom in plaats van ISO-tekst, booleans voor `album_item.shared`, een eigen bind voor `search_name` (varchar tegen text). Ook `test_2fa.py` en `test_places_later.py` geven hun kindproces de database mee. |
| `9ba6589` | **De zoekwereld (`tests/search/wereld.py`) op PostgreSQL.** `bench.ANALYSE_SQL` is SQL van sqlite3 (`?`, tijden als tekst). Op PostgreSQL gaan dezelfde rijen via de tabel `analysis` met echte tijden. Op SQLite blijft het statement gelijk. |
| `8d24072` | **Eén plek voor de zoekmotoren: `tests/search/motoren.py`** (zie hieronder). |
| `6759b3e` | **Zoektests lezen en schrijven op beide databases gelijk:** benoemde binds met een type (uuid, timestamptz) in plaats van `?` en hex-tekst, Core-inserts via de tabel van het model, `inspect().has_table` in plaats van `sqlite_master`, de `OperationalError` van de driver zelf, en in `test_admin_search.py` een lezing met asyncpg in een eigen draad waar de test het bestand met sqlite3 las. Waar één test de namen én de FTS-index toetste, is het FTS-deel een eigen test met `needs_fts` (`test_wereld.py`, `test_api_stubs.py`, `test_search_filters.py`). Zo draait de rest op PostgreSQL en houdt SQLite elke bewering. |
| `c3025db` | **`test_search_churn.py`:** de OCR-rijen met een getypte bind voor bytea en timestamptz (eerst een bit-literal `x'00'` en ISO-tekst). Zonder FTS-index (PostgreSQL tot golf 3) controleert de test na elke ronde de boom, en vergelijkt hij LikeFallback met `Referentie.op_naam` voor elke gebruiker, in plaats van de index en de referentie over te slaan. |
| `090b0c8` | **Wat PostgreSQL zelf moet geven:** de indexen van 0011 en 0012 tegen `pg_indexes` (na de vorm die PostgreSQL er zelf van maakt: `public.`, `USING btree`, `::text`, haakjes). Het plan van de kaartfilters via `EXPLAIN (FORMAT JSON)` met `enable_seqscan = off` (`plans.pg_stappen`): `node` staat alleen in het plan als het filter hem nodig heeft, op zijn sleutel gekoppeld, en geen tabel wordt helemaal gelezen. In `test_photometa.py` het slot van de boom (`node_lock_tree()`, P2) als statement in de burst. In `test_admin_status.py` `db_kind` `postgresql`. Op SQLite blijft elke verwachting gelijk. |
| `eeca47e` | **Zoek-CLI:** de bewaking van search-bench (nooit in een map met een database) leest de database van de app via de engine en draait nu ook op PostgreSQL. De drie tests van search-check en search-rebuild hebben `needs_fts`. |
| `634288e` | **CI** (zie hieronder) en `test_dbcopy.py` met de marker `pg`. `test_copy_without_superuser` geeft zijn rol aan de beheerder (`GRANT r TO CURRENT_USER`), zodat ook een beheerder zonder superuser de database aan die rol kan geven en hem daarna weghaalt. |
| `3ba5294` | **`test_bench_maakt_de_boom` draait weer op PostgreSQL** (in `eeca47e` stond hij op `sqlite_only`). search-bench moet zijn eigen `platform.db` bouwen, welke database de app ook heeft. Op PostgreSQL is hij rood door een fout in de app (Open 2). Een test overslaan omdat hij een fout laat zien, mag niet. |

## sqlite_only: wat alleen over SQLite gaat

`@pytest.mark.sqlite_only("reden")` (geregistreerd in `api/pyproject.toml`, `tests/conftest.py`)
slaat een test over als `PLATFORM_TEST_DATABASE_URL` gezet is, met de reden in de uitvoer
("alleen SQLite: …"). 46 tests, alle met een eigen reden; de lijst komt uit de junit van de hele
run op PostgreSQL:

| Bestand | Tests | Reden |
|---|---|---|
| `tests/test_db_gate.py` | 18 (`POORT`): `test_wie_te_lang_wacht_krijgt_database_is_locked`, `test_twee_databases_hebben_elk_een_eigen_poort`, `test_een_tweede_verbinding_van_dezelfde_taak_gaat_zoals_vroeger`, `test_de_poort_gaat_terug_op_elk_pad`, `test_de_poort_blijft_tot_de_commit_klaar_is`, `test_een_commit_wekt_de_checkpointer`, `test_een_mislukte_schrijfactie_buiten_een_transactie_geeft_de_poort_terug`, `test_een_rollback_op_een_open_verbinding_geeft_de_poort_terug`, `test_een_taak_die_midden_in_een_transactie_stopt`, `test_wie_de_poort_lang_heeft_komt_in_de_log`, `test_sqlite_heeft_de_poort_en_geen_autocheckpoint`, `test_de_checkpointer_doet_het_buiten_de_engine`, `test_een_upload_wacht_niet_achter_een_schrijver_die_steeds_terugkomt`, `test_uploads_tegelijk_wachten_niet_achter_de_achtergrond`, `test_wat_een_runner_doet_is_achtergrond`, `test_blobslot_geeft_voorrang[blobs]`, `test_blobslot_geeft_voorrang[gebruiker]`, `test_herbouw_schrijft_als_achtergrond` | de schrijfpoort van SQLite (`app/dbgate.py`); `app/db.py` zet hem alleen op een engine van SQLite |
| `tests/test_db_checkpoint.py` | 6 (`WAL`): `test_het_checkpoint_van_het_uur_maakt_de_wal_leeg`, `test_met_een_lezer_geeft_het_op_en_logt_alleen_getallen`, `test_na_een_migratie_opnieuw_tot_het_budget_op_is`, `test_retry_stopt_zodra_het_lukt`, `test_een_schrijver_wacht_niet_op_het_checkpoint[zonder-lezer]`, `test_een_schrijver_wacht_niet_op_het_checkpoint[met-lezer]` | het WAL-checkpoint van SQLite |
| `tests/test_db_pragmas.py` | 5 (`PRAGMAS`): `test_elke_verbinding_uit_de_pool`, `test_elke_checkout_tijdens_echte_verzoeken`, `test_de_verbinding_van_een_migratie_en_van_db_downgrade`, `test_de_checkpoints_en_de_controle_van_de_week`, `test_ook_na_een_herstart_van_de_pool` | `secure_delete` en `temp_store` zijn PRAGMA's van SQLite |
| `tests/search/test_spike_triggers.py` | 11 (de module): `test_hernoemen_verplaatsen_prullenbak_en_wissen`, `test_wissen_met_de_cascade_raakt_ook_de_kleinkinderen`, `test_een_batch_van_500`, `test_een_map_kopieren`, `test_nieuwe_inhoud`, `test_een_andere_eigenaar`, `test_een_gebruiker_wissen`, `test_purge_expired`, `test_de_import_van_fase_5`, `test_herbouw_in_stukken_met_uploads_ertussen`, `test_het_laatste_stuk_leest_de_teller_pas_onder_het_slot` | de spike van de triggers van 0010 en FTS5 in SQLite |
| `tests/test_cli_db.py` | 3 (`BACKUP`): `test_db_backup_maakt_een_kopie_terwijl_de_app_draait`, `test_db_backup_houdt_per_head_de_nieuwste`, `test_db_backup_zonder_database` | db-backup kopieert het SQLite-bestand; op PostgreSQL maakt pg_dump de kopie. Dat het commando daar weigert met exitcode 2, toetst `test_pg_dialect.py::test_db_backup_op_postgresql_verwijst_naar_pg_dump`. |
| `tests/search/test_search_purge_batches.py` | 3: `test_wissen_zonder_de_triggers_van_0010[los]`, `test_wissen_zonder_de_triggers_van_0010[bulk]`, `test_wissen_zonder_de_triggers_van_0010[legen]` | doet PostgreSQL na op SQLite door de triggers van 0010 weg te halen; op PostgreSQL zelf lopen de echte triggers van 0013 in de andere tests van dit bestand |

Van de tests in deze bestanden die niet over SQLite alleen gaan, draait de rest op beide
databases (bijvoorbeeld de tests van de poort zelf, zonder engine, in `test_db_gate.py`).

Vergeleken met het plan en [w1.md](w1.md) (47): de poort (18), de pragma's (5), het checkpoint
(6) en de spike (11) zijn gelijk. De 6 tests van FTS5 in `test_search_rebuild.py` hebben hier
`needs_fts` in plaats van `sqlite_only`: met golf 3 komen ze terug op PostgreSQL. db-backup telt
3 in plaats van 1: alle drie gaan over het SQLite-bestand. Bij golf 1 was
`test_db_backup_zonder_database` groen op PostgreSQL, maar alleen omdat het kindproces de
database van de test niet kreeg en dus SQLite gebruikte. De 3 van het wissen kwamen erbij.

## FTS: wat op PostgreSQL wacht op golf 3

Op SQLite heeft de app twee zoekmotoren: `FtsEngine` (de FTS5-index van 0010) en `LikeFallback`.
PostgreSQL heeft tot golf 3 alleen `LikeFallback`: 0010 maakt daar geen index (Keuze 76); de
eigen index komt met 0014 (P5). `tests/search/motoren.py` zegt dat op één plek:

- `motoren.params("fts", "like")`: de motoren voor een fixture of `parametrize`. Op PostgreSQL
  zonder FTS bestaat de variant `[fts]` niet; hij wordt niet gemaakt, dus ook niet overgeslagen.
- `@motoren.needs_fts`: een test die de FTS-index zelf nodig heeft (de index lezen, herbouwen,
  een vraag in de index afbreken, de grenzen en de tweede controle van `FtsEngine`). Op
  PostgreSQL overgeslagen met de reden "de FTS-index; die heeft PostgreSQL pas met golf 3 (P5,
  migratie 0014)".
- `motoren.DEFAULT`: de motor die de app zonder keuze neemt (`fts` op SQLite, `like` op
  PostgreSQL), voor tests die alleen zeggen welke motor antwoordt.
- `motoren.check_index(conn)`: de controle van de FTS-tabellen (`migratie.check_fts`) waar er een
  index is. Op PostgreSQL loopt de rest van de test (boom, boekhouding) wel.

**Voor golf 3:** zet `PG_FTS = True` in `motoren.py` (en zo nodig de naam van de motor van
PostgreSQL in `params`). Dan komen alle tests hieronder terug op PostgreSQL. `test_search_churn.py`
toetst op PostgreSQL nu LikeFallback tegen `Referentie.op_naam`; met `PG_FTS` gaat hij vanzelf
terug naar de index en `Referentie.zoek`.

### Varianten `[fts]` die op PostgreSQL niet gemaakt worden (33)

- `tests/albums/test_album_rules.py` (1): `test_de_regel_past_op_eigen_levende_fotos_in_beide_motoren[fts]`
- `tests/geo/test_photo_filters.py` (2): `test_een_plek_en_een_camera_zijn_alleen_van_de_eigenaar[fts]`, `test_locatie_uit_en_weer_mij[fts]`
- `tests/people/test_people.py` (3): `test_de_groep_personen_bij_zoeken[fts]`, `test_een_haak_voor_de_groepen_die_faalt[fts]`, `test_het_filter_person_in_beide_motoren[fts]`
- `tests/people/test_people_privacy.py` (1): `test_alleen_eva_ziet_anna[fts]`
- `tests/search/test_search_filters.py` (3): `test_een_ander_ziet_alleen_het_zijne[fts]`, `test_elk_filter_en_elk_paar_tegen_de_referentie[fts]`, `test_wat_je_niet_ziet_is_404_en_een_onbekende_plek_niets[fts]`
- `tests/search/test_search_overgang.py` (1): `test_de_vraag_van_070_is_een_422[fts]`
- `tests/search/test_search_vraag.py` (11): `test_de_eigen_hoofdmap_is_nooit_een_treffer[fts]`, `test_de_filters_van_z3_geven_niets_zonder_eigen_plek[fts-camera-Canon EOS 5D-Z3]`, `test_de_filters_van_z3_geven_niets_zonder_eigen_plek[fts-country-NL-Z3]`, `test_de_filters_van_z3_geven_niets_zonder_eigen_plek[fts-place-0-Z3]`, `test_de_filters_van_z3_geven_niets_zonder_eigen_plek[fts-place-2759794-Z3]`, `test_een_bibliotheek_die_je_niet_ziet_is_404[fts]`, `test_een_te_lange_vraag_is_400[fts]`, `test_from_en_to_in_beide_motoren[fts]`, `test_het_filter_person_van_z6_geeft_niets_voor_een_onbekende[fts]`, `test_in_en_library_samen_zoeken_onder_allebei[fts]`, `test_in_zoekt_onder_de_map_zonder_de_map_zelf[fts]`
- `tests/test_search.py` (11): `test_hooguit_zoveel_tags[fts]`, `test_in_map_limiet_en_andermans_bestanden[fts]`, `test_lijst_en_zoeken_zonder_query_per_bestand[fts]`, `test_niets_uit_de_prullenbak[fts]`, `test_snel_genoeg_met_twintigduizend_bestanden[fts]`, `test_tags_beheren[fts]`, `test_tags_op_bestanden[fts]`, `test_tegelijk_dezelfde_tag_aanmaken[fts]`, `test_tegelijk_tags_zetten[fts]`, `test_zoeken_op_naam[fts]`, `test_zoeken_op_soort[fts]`

Nagegaan: precies deze 33 ontbreken in de verzameling op PostgreSQL tegen die op SQLite (de
andere verschillen zijn de willekeurige namen van de fuzz-tests van office). Bij golf 1 waren 9
ervan groen op PostgreSQL. Ze toetsten daar niet de index: `select_engine` neemt LikeFallback als
de database geen FTS heeft, dus deed `[fts]` op PostgreSQL hetzelfde als `[like]`.

### Tests met `needs_fts` (88, uit 84 functies)

Met de parameters erbij, zoals de junit van de run op PostgreSQL ze overslaat:

- `tests/geo/test_photo_filters.py` (1): `test_de_plaatsen_in_de_suggesties_en_de_groep`
- `tests/ocr/test_ocr_access.py` (3): `test_brams_ocr_van_zijn_kopie_komt_niet_bij_eva`, `test_de_ocr_van_een_pdf_volgt_de_leestoegang`, `test_een_kopie_van_bram_krijgt_eva_s_tekst_niet`
- `tests/ocr/test_ocr_lifecycle.py` (6): `test_gc_cascade`, `test_ocr_searchable`, `test_owner_change_wipes`, `test_photo_ocr_per_node`, `test_replace_no_old_text`, `test_trash_and_restore_keep_ocr`
- `tests/ocr/test_ocr_step.py` (4): `test_alleen_de_gescande_pagina_s_van_een_pdf`, `test_een_foto_alleen_op_verzoek`, `test_een_foto_van_een_ander_krijgt_niets_van_de_pomp`, `test_een_kopie_door_dezelfde_eigenaar_houdt_de_tekst`
- `tests/people/test_people.py` (1): `test_de_suggesties_voor_personen`
- `tests/search/test_admin_search.py` (6): `test_de_index_herbouwen_loopt_op_de_achtergrond`, `test_een_herbouw_van_de_index_met_voortgang`, `test_hash_loopt_ook_als_de_index_tegelijk_herbouwd_wordt`, `test_motor_namen_en_herbouw_uit_een_lezing`, `test_na_de_202_ziet_een_get_de_herbouw_al`, `test_ronde_b_en_c_aan_de_grens_en_search_busy_tellen`
- `tests/search/test_api_stubs.py` (1): `test_zoeken_met_de_index_aan_via_ftsengine`
- `tests/search/test_contracts.py` (1): `test_parse_deadline_en_admit_zijn_gebouwd`
- `tests/search/test_schema_triggers.py` (3): `test_ocr_van_een_foto_staat_alleen_in_pocr`, `test_triggers_in_de_database_zijn_schema_triggers`, `test_view_en_fts_tabellen_in_de_database`
- `tests/search/test_search_access.py` (11): `test_de_cellen_van_de_matrix`, `test_de_controle_en_het_pad_in_een_query`, `test_de_ocr_van_een_foto_is_alleen_van_de_eigenaar`, `test_een_deling_ingetrokken_in_hetzelfde_verzoek`, `test_een_eigen_map_in_de_prullenbak_in_hetzelfde_verzoek`, `test_fien_ziet_niets_na_het_verlopen`, `test_intrekken_en_verhuizen_tellen_meteen`, `test_veel_wortels[1000]`, `test_veel_wortels[64]`, `test_veel_wortels[65]`, `test_wat_ziet_wie_volgt_de_referentie`
- `tests/search/test_search_cli.py` (3): `test_check_dan_triggers_dan_weer_goed`, `test_check_tijdens_een_herbouw`, `test_rebuild_in_stukken_met_voortgang`
- `tests/search/test_search_deadline.py` (3): `test_ronde_b_geeft_de_zichtbare_treffer`, `test_ronde_c_geeft_de_zichtbare_naam[kort]`, `test_ronde_c_geeft_de_zichtbare_naam[trigram]`
- `tests/search/test_search_fallback.py` (2): `test_met_de_index_uit`, `test_tijdens_een_herbouw`
- `tests/search/test_search_filters.py` (1): `test_een_woord_in_de_tekst_met_een_filter`
- `tests/search/test_search_limits.py` (6): `test_25_tegelijk_geeft_search_busy`, `test_de_snelheid_via_de_api`, `test_de_tweede_vraag_stopt_de_eerste_via_de_api`, `test_een_tweede_vraag_van_dezelfde_gebruiker_stopt_de_eerste`, `test_overgenomen_via_de_api[/api/v1/search/suggest]`, `test_overgenomen_via_de_api[/api/v1/search]`
- `tests/search/test_search_logs.py` (1): `test_niets_in_de_log_van_de_app`
- `tests/search/test_search_rebuild.py` (8): `test_de_controle_tijdens_een_herbouw`, `test_de_controle_vindt_verschillen`, `test_de_triggers_terug_en_wat_ze_bijhouden`, `test_een_herbouw_die_stopt_gaat_verder`, `test_een_node_met_meer_tekst_dan_het_budget_is_een_stuk`, `test_een_stuk_vol_lange_teksten_houdt_de_schrijver_kort`, `test_een_upload_wacht_hooguit_1_s_en_de_index_klopt`, `test_twee_tegelijk`
- `tests/search/test_search_reference.py` (3): `test_bij_gelijke_score_op_num_ook_over_de_paginas`, `test_de_naam_staat_boven_de_tekst`, `test_een_heel_woord_staat_boven_een_stuk_van_een_naam`
- `tests/search/test_search_snippets.py` (6): `test_de_ocr_van_een_foto_alleen_voor_de_eigenaar`, `test_de_snippet_in_het_antwoord`, `test_een_treffer_na_teken_20000`, `test_in_een_thread_met_een_limiter_van_1`, `test_server_timing_alleen_met_search_timing`, `test_ss_in_de_tekst_via_de_api`
- `tests/search/test_search_suggest.py` (8): `test_albums_eigen_en_gedeeld`, `test_de_namen_krijgen_wat_er_van_het_verzoek_over_is`, `test_de_tweede_controle`, `test_kort_geen_namen_wel_tags`, `test_namen_die_je_mag_zien_het_voorvoegsel_eerst`, `test_personen_en_plaatsen_via_de_haken`, `test_tags_alleen_de_eigen`, `test_te_lang_en_leeg`
- `tests/search/test_weekcheck.py` (6): `test_de_controle_leest_alleen_in_een_eigen_draad`, `test_een_verschil_komt_op_beheer_met_alleen_aantallen`, `test_na_een_verschil_het_volgende_uur_opnieuw`, `test_niet_tijdens_de_pauze`, `test_niet_tijdens_een_herbouw_zonder_fts5_of_zonder_nodes`, `test_wat_de_app_intussen_schrijft_is_geen_verschil`
- `tests/search/test_wereld.py` (1): `test_de_fts_index_van_de_wereld_is_heel`
- `tests/test_backfill.py` (3): `test_een_upload_wacht_nooit_langer_dan_1_s_tijdens_de_backfill`, `test_het_deel_per_node_ook_bij_alleen_ocr`, `test_veel_kopieen_houden_de_schrijfactie_klein`

## CI

**api-postgres** (PostgreSQL 17) blokkeert nu: geen `continue-on-error` meer, `timeout-minutes:
45`, de health-check `pg_isready -U platform -d postgres`, pytest met
`-o faulthandler_timeout=600` (een test die vastloopt, laat na 10 minuten zijn stacks zien; geen
plugin voor een tijdslimiet) en `--junitxml`; bij rood komt het junit-bestand als artifact
(`actions/upload-artifact@v4`, dat ci.yml al gebruikte). De aparte stap voor `test_dbcopy.py` is
weg: die tests hebben nu de marker `pg` en lopen mee in de gewone run.

**api-postgres-15** (nieuw) draait op `postgres:15-alpine` met de rol `eigenaar`, zoals op
ClusterForge: eigenaar van zijn databases, geen superuser (`NOSUPERUSER CREATEDB`, en
`CREATEROLE` omdat `test_copy_without_superuser` zelf een rol maakt en weghaalt). De superuser
`platform` van de service maakt die rol in een eerste stap. Daarna: `-m pg` (de tests voor alleen
PostgreSQL, met `test_dbcopy.py`), en `test_pg_dialect.py` met de migratietests
(`tests/test_migration_0*.py`, `tests/search/test_migration_0*.py`). Tijdslimiet 20 minuten.

Lokaal nagedaan op PostgreSQL 16 (geen 15 in de sandbox) met dezelfde rol:

| Stap | Uitslag |
|---|---|
| `-m pg` | eerst 1 rood: `test_copy_without_superuser` (`must be able to SET ROLE` bij `CREATE DATABASE … OWNER`). Na de `GRANT` in de test: 27 passed |
| `-m "not pg"` met `test_pg_dialect.py` en de migratietests | 56 passed |
| `tests/test_dbcopy.py` als superuser `platform` | 4 passed |

`yamllint -s .github/workflows/ci.yml` is groen. Een echte run in CI is er nog niet (er is niet
gepusht).

## Metingen: hoe lang de hele suite duurt

Alles lokaal: 4 CPU's, PostgreSQL 16 in `/dev/shm` (`fsync=off`), `-o faulthandler_timeout=600
-p no:cacheprovider`. Naast deze runs werkte P4 in dezelfde sandbox.

| Run | Kop | Sjabloon | Uitslag | Duur |
|---|---|---|---|---|
| golf 1, run 1 ([w1.md](w1.md)) | `2032ce2` | nee: elke test migreert | 256 failed, 16 errors, 6635 passed (met de `--deselect` van de deadlock) | 58 min |
| golf 1, run 2 ([w1.md](w1.md)) | `fe22717` | nee | 255 failed, 16 errors, 6636 passed (idem) | 57 min |
| P3 | `634288e` | ja | 62 failed, 6681 passed, 137 skipped, 241 deselected, zonder `--deselect` | 42 min 39 s (2559 s) |

Waar de 15 minuten vandaan komen:

| Meting | Uitkomst |
|---|---|
| Een nieuwe database maken en migreren tot 0013 | 0,49 s (mediaan van 5; tijdens een hele run, dus onder last) |
| Een kopie van het sjabloon (`CREATE DATABASE … TEMPLATE`) | 0,03 s (mediaan van 5, idem) |
| Tests die een gemigreerde database krijgen (`pytest --setup-plan`: `database_url_head`) | 2025 |
| Verwacht verschil: 2025 × 0,46 s | 930 s, 15,5 min |

**A/B op een deelset** (`tests/geo` en `tests/test_office_callback.py`, om en om met en zonder
sjabloon): loopt nog; de uitslag komt hier.

**In CI.** De job api-postgres haalde nog nooit het eind (de deadlock; op `cbd0922` na 60 min
afgebroken), dus een tijd van vóór dit pakket is er in CI niet. De job met de hele suite op
SQLite duurt in CI 35 tot 38 min, lokaal 54 tot 55 min. Met die verhouding wordt api-postgres in
CI ongeveer 29 min: net onder het doel van 30 min uit het plan, en ruim onder de grens van 45
min. Dat is een schatting; de eerste run in CI na de samenvoeging geeft het echte getal.

**SQLite.** De hele suite op SQLite op deze branch: loopt nog; de uitslag komt hier.

## Nog rood op PostgreSQL

In de hele run op `634288e` waren 62 tests rood. Geen ervan is nieuw: alle 62 stonden al in de
lijst van golf 1. Daarbij komt `test_bench_maakt_de_boom` (`3ba5294`), die daar
`sqlite_only` had. Bij golf 1 was hij groen op PostgreSQL, maar alleen omdat het kindproces de
database van de test niet kreeg (net als `test_db_backup_zonder_database`).

| Tests | Van wie | Eerste oorzaak |
|---|---|---|
| 55 in `tests/beeld/` (`test_beeld_access.py` 3, `test_beeld_cli.py` 5, `test_beeld_events.py` 17, `test_beeld_gen.py` 2, `test_beeld_never_out.py` 1, `test_beeld_off.py` 2, `test_beeld_search.py` 9, `test_embed_step.py` 8, `test_vecfile.py` 8) | P4 | de fixture `beeld` (via `tests/analyse`) maakt eigen Settings op SQLite: `no such table: user` |
| `tests/search/test_search_logs.py::test_niets_in_de_log_met_zoeken_op_beeld` | P4 | dezelfde fixture `beeld` uit `tests/beeld/conftest.py` |
| `tests/photos/test_photo_settings.py`: `test_met_x2_aangemeld`, `test_op_postgresql_niet_beschikbaar` | P4 | de analyse staat uit op PostgreSQL |
| `tests/test_jobs_lanes.py`: `test_opruimwerk_een_keer_per_periode_met_alle_runners`, `test_de_rij_analyse_alleen_met_de_container` | P4 | de rij `analyse` ontbreekt (de analyse staat uit op PostgreSQL) |
| `tests/geo/test_places_later.py`: `test_zee_uit_prullenbak_eigenaar_en_stukken`, `test_wat_tijdens_het_opzoeken_verandert_krijgt_geen_plaats` | de app (Open 1) | de log `geo_places_done rows=N` telt verkeerd op PostgreSQL |
| `tests/search/test_search_cli.py::test_bench_maakt_de_boom` | de app (Open 2) | search-bench migreert met `PLATFORM_DATABASE_URL` de database van de app |

Van de 271 die bij golf 1 rood waren: 55 slagen nu, 45 hebben `sqlite_only`, 85 `needs_fts`, 24
zijn varianten `[fts]` die op PostgreSQL niet meer gemaakt worden, en 62 zijn nog rood (de tabel
hierboven, zonder de bench).

Tot P4 erbij is en de twee fouten in de app hersteld zijn, is api-postgres in CI dus rood.

## Open

1. **`run_places` telt verkeerd op PostgreSQL** (`app/photo_rows.py`, `run_places`). De log
   `geo_places_done rows=N` telt `result.rowcount` van `db.execute(_FILL, found)` met een lijst:
   een `executemany`. asyncpg geeft daar geen aantal; SQLAlchemy zet het op -1
   (`supports_sane_multi_rowcount = False`), en `max(-1, 0)` maakt er 0 van. Een proef met
   dezelfde UPDATE van 3 rijen: aiosqlite geeft rowcount 3, asyncpg -1; met een lijst van 1 rij
   geven beide 1. Daarom zegt de log `rows=1` in plaats van `rows=3` en `rows=0` in plaats van
   `rows=1`. De rijen zelf kloppen (de tests controleren ze en dat deel slaagt). Herstel hoort in
   de app: tel per rij (`execute` per rij in het stuk), of lees het aantal terug met
   `RETURNING`. Op SQLite blijft het getal dan gelijk.
2. **search-bench met `PLATFORM_DATABASE_URL`** (`app/search/bench.py`, `maak`). `maak` maakt
   `Settings(data_dir=…)`; die leest ook `PLATFORM_DATABASE_URL`. Staat die op PostgreSQL, dan
   migreert de bench de database van de app in plaats van zijn eigen `platform.db`, en schrijft
   sqlite3 daarna in een leeg bestand (`sqlite3.OperationalError: no such table: user`, exitcode
   1). Op een node van het cluster is dat een migratie van de echte, gedeelde database, met de
   revisie van het image waarin de bench draait. Herstel in de app: `Settings(data_dir=…,
   database_url="")` in `maak`, zodat de bench altijd zijn eigen bestand neemt; op SQLite zonder
   die variabele verandert er niets. P5 (zoek-perf op PostgreSQL) kiest daarna of de bench ook
   PostgreSQL moet kunnen.
3. **Zoeken op PostgreSQL tot golf 3** (voor P5): LikeFallback draait buiten `limits.admit`
   (Keuze 13: zo zocht 0.7.0), dus op het cluster gelden de grenzen van zoeken (2 tegelijk, 1 per
   gebruiker, de snelheid) nog niet. Ook kent LikeFallback geen tweede controle van de toegang
   (`search_access_mismatch`), geeft `/search/suggest` met meer dan 10 woorden 200 in plaats van
   400 (`search_query_too_long`), en sorteert hij anders bij gelijke treffers. De tests daarvan
   staan hierboven onder `needs_fts`.
4. **De rol op ClusterForge.** Een beheerder zonder superuser die een database aan een andere rol
   wil geven, moet lid zijn van die rol (PostgreSQL 16: `SET ROLE` mogen; 15: lid zijn). `db-copy`
   zelf doet dat niet; alleen de test maakt zo een doel. Voor de updatestappen van ClusterForge:
   maak het doel van `db-copy` als de eigenaar zelf, of geef de rol eerst aan de beheerder.
5. **De duur in CI** is een schatting (29 min, zie Metingen). Na de eerste run in CI: het echte
   getal hier zetten, en `timeout-minutes: 45` laten staan zolang er ruim marge is.

## Afwijkingen van het plan

- **FTS-internals met `needs_fts`, niet met `sqlite_only`.** Het plan noemt "FTS5-internals"
  bij `sqlite_only`. Hier krijgt alleen de spike van de triggers (die over SQLite-triggers gaat)
  `sqlite_only`. Tests van de index zelf (herbouw, controle, weekcontrole, snippets) hebben
  `needs_fts`: golf 3 zet ze met één regel terug op PostgreSQL.
- **search-bench niet `sqlite_only`.** De test van de bench draait op PostgreSQL en is daar rood
  door een fout in de app (Open 2), in plaats van overgeslagen.
- **Tests gesplitst.** Drie tests toetsten de namen en de FTS-index samen
  (`test_wereld.py`, `test_api_stubs.py`, `test_search_filters.py`). Het FTS-deel staat nu in een
  eigen test; op SQLite draaien beide, met alle beweringen.
- **`test_search_churn.py` op PostgreSQL** vergelijkt LikeFallback met `Referentie.op_naam` in
  plaats van de index met `Referentie.zoek`; de boom controleert hij op beide.
- **Het plan van de kaartfilters op PostgreSQL** toetst: `node` alleen als het filter hem nodig
  heeft, gekoppeld op zijn sleutel, en geen volledige scan (met `enable_seqscan = off`). De vorm
  van SQLite ("`photo_geo` eerst", "`node` via de sleutelindex") heeft geen vaste tegenhanger in
  een plan van PostgreSQL; de planner kiest de volgorde zelf.
- **`CREATEROLE` voor de rol van api-postgres-15.** Het plan zegt `NOSUPERUSER CREATEDB`;
  `test_copy_without_superuser` maakt zelf een rol, dus de rol krijgt ook `CREATEROLE`.
- **Geen aparte stap meer voor `test_dbcopy.py`** in api-postgres: de marker `pg` neemt hem mee.
