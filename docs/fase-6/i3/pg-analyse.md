# I3 pg-analyse: gezichten en beeld op PostgreSQL (P4)

Branch `wip/i3-pg-analyse`. Pakket P4 uit het plan voor 0.8.2: de analyse (gezichten en zoeken
op beeld) mag op PostgreSQL aan, maar alleen als de beheerder dat zegt en de triggers van 0013
er zijn. Daarbij: een sleutel die weg is wordt nooit stil vervangen, twee commando's voor de
sleutels en de kopie, wissen als crypto-shred, en het bestand met beeldvectoren herstelt zich als
het niet bij de database hoort.

Op SQLite verandert er niets voor de gebruiker van één node. Elke wijziging in de app kijkt naar
het dialect, of doet op SQLite precies hetzelfde als voor 0.8.2 (zie "SQLite blijft gelijk").

## De schakelaar

- Nieuwe instelling `PLATFORM_ANALYSE_POSTGRES`, standaard `false`. In
  `infra/single/compose.analyse.yaml`: `PLATFORM_ANALYSE_POSTGRES: ${ANALYSE_POSTGRES:-false}`.
  Een cluster met een oude template start dus nooit gezichten.
- `Settings.analyse_on` is nu `analyse_enabled` en `analyse_pg_block() is None`.
  `analyse_pg_block()` geeft:
  - op SQLite altijd `None` (zoals voor 0.8.2, zonder vraag aan de database);
  - op PostgreSQL zonder `PLATFORM_ANALYSE_POSTGRES`: `pg_not_enabled`;
  - op PostgreSQL met de instelling, zolang de triggers van 0013 niet allemaal gezien zijn:
    `no_pg_triggers`.
- De oude reden `no_sqlite` is weg. Overal waar hij stond, staat nu de reden van
  `analyse_pg_block()`: de monitor (`down` met de reden), analyse-status, `faces/state.py`,
  `beeld/jobs.py`, `beeld/cli.py`, de rij analyse (`jobs.active_lanes`), `/me/photo-settings`
  en `/people/faces`. Op `/beheer` twee nieuwe redenen in `photos_faces.nl.ts` en `.en.ts`
  ("ANALYSE_POSTGRES staat uit", "een trigger van 0013 ontbreekt"); de oude van `no_sqlite` is
  weg. Kort, want dat gebied zit in de chunk van gezichten, en die zat op 11.965 van de 12.000
  bytes (gzip). Met de twee redenen is het ±11.994. De regel over de sleutels (hieronder) staat
  daarom in het gebied `admin` (`admin.search.faceKeys`), dat in de chunk van de tab zit.

### De triggers (app/analyse/pgcheck.py)

- `pgcheck.TRIGGERS` is de lijst van migratie 0013 (tien triggers op `node`, `photo_geo` en
  `person`). Een test legt vast dat de twee lijsten gelijk zijn.
- `check()` draait in de lifespan direct na de migratie, voor alles wat de analyse aanmeldt. Hij
  kijkt in `pg_trigger` naar triggers die aan staan (`tgenabled <> 'D'`), in het schema van de
  app. Mist er een, dan schrijft hij een fout in het log met alleen de namen van de triggers, en
  blijft de analyse uit (`down: no_pg_triggers`). De uitkomst geldt per database-URL.
- De cli-commando's die `analyse_on` lezen (analyse-status, analyse-bench, analyse-kwaliteit en
  de beeld-commando's) doen dezelfde controle (`check_cli`).
- Zonder `PLATFORM_ANALYSE_ENABLED` of zonder `PLATFORM_ANALYSE_POSTGRES`, en op SQLite, vraagt
  `check()` niets aan de database. Een test telt de statements: nul.
- Een trigger die uitgezet wordt terwijl de app draait, ziet de app pas bij de volgende start
  (of het volgende cli-commando). Zie "Open punten".

## De sleutels van gezichten

Eén sleutel per eigenaar, een bestand `<hex>.key` van 32 bytes, 0600, in `PLATFORM_FACES_KEYS`
(`/keys`, buiten de data, Keuze 68). De vector staat versleuteld in `face.vec` (AES-GCM, de id
van het gezicht als AAD). De sleutel staat nooit in de database.

### Een sleutel die weg is

Op een cluster kan een node zonder de sleutelmap van de andere starten. Tot nu maakte
`ensure_key` dan stil een nieuwe sleutel. De oude vectoren gingen daarna nooit meer open, en
niemand merkte het.

Nu:

- `crypto.ensure_key(settings, db, owner)` weigert (`KeyLost`) als er geen sleutel is en er nog
  versleutelde gezichten van die eigenaar staan (`face_rows.has_sealed`). Zonder zulke rijen
  (de eerste keer, of na "uit" en het wissen) maakt hij wel een nieuwe.
- De scan slaat zo'n eigenaar over: zijn foto's blijven op `wait`, er komt geen nieuwe sleutel
  en geen nieuwe vector.
- `/beheer` (`faces.keys_missing`) en analyse-status ("gezichten: sleutel ontbreekt voor N
  eigenaar(s)") zeggen het. Alleen het aantal, nooit wie of een pad.
- `keys_missing` staat wel in het antwoord van `GET /admin/search`, maar niet in de OpenAPI
  (`SkipJsonSchema`). De OpenAPI moet byte voor byte die van 0.8.0 blijven
  (`tests/uit080/test_wandeling.py`: met de analyse uit is alles van fase 6 onzichtbaar). Met de
  analyse uit is `faces` daar `null`, dus verandert het antwoord dan ook niet.
- Komt de sleutel terug, dan is het aantal weer 0. De scan gaat verder bij de volgende ronde
  (`faces.plan`, elk uur) of bij een nieuwe foto, met dezelfde sleutel.

### Wissen op PostgreSQL: crypto-shred

"Uit" doet op beide databases hetzelfde: eerst de sleutel weg (unlink, dan fsync van de map),
dan haalt de wisronde de rijen weg (`face`, `face_scan`, `face_owner`). Op PostgreSQL bestaat
`PRAGMA secure_delete` niet; `wipe.py` doet dat alleen op SQLite.

Wat er op PostgreSQL nog even blijft staan:

- de oude versie van een rij in een dode tuple, tot VACUUM de ruimte hergebruikt;
- de WAL, tot het segment hergebruikt of gearchiveerd is;
- een replica, en elke `pg_dump` of basiskopie van vóór "uit".

Daarin staat de vector alleen versleuteld. De sleutel is weg, en hij stond nooit in de database
of in de WAL. Dus is de vector daar niet meer te lezen. Dat geldt alleen als de sleutelmap zelf
niet in een kopie zit: de sleutels staan buiten de data (Keuze 68), en een kopie van `/keys`
hoort niet bij de reservekopie van de database.

Wat niet versleuteld is (het vak op de foto, de score, de koppeling aan een persoon) kan zo ook
nog even in oude tuples en de WAL staan. Dat is geen biometrische vector.

`test_faces_wipe_bytes.py` kijkt naar de bytes van het SQLite-bestand. Die test blijft SQLite
(`sqlite_only`, met reden). Op beide databases geldt nu `test_faces_wipe_shred.py`: na "uit" is
de sleutel weg voordat de wisronde loopt, daarna staan er geen rijen van de eigenaar meer, en
geen enkele sleutel die er nog is opent een oude vector van hem. De vectoren van een ander
blijven open met diens eigen sleutel.

## De commando's

```
python -m app.cli sleutels-check [--opruimen]
python -m app.cli backup-wipe-tables
```

`sleutels-check` loopt de sleutels na. Een eigenaar heeft een sleutel nodig als hij gezichten
aan heeft, er geen wisronde loopt en er minstens één versleutelde vector van hem staat. Zo'n
sleutel moet een gewoon bestand zijn (geen symlink) van 32 bytes, rechten 0600, eigenaar 10001
(de gebruiker van de app in de container), in een sleutelmap die zelf geen symlink is. Exitcode
0 alleen als dat voor iedereen klopt, anders 1. Op het scherm alleen aantallen:

```
sleutelmap: in orde
eigenaren met een sleutel nodig: 1
  sleutel ontbreekt: 0
  geen gewoon bestand (symlink of iets anders): 0
  niet 32 bytes: 0
  rechten niet 0600: 0
  eigenaar niet 10001: 0
sleutels die niet nodig zijn: 0
```

`--opruimen` haalt de sleutels weg van eigenaren zonder rijen van gezichten die gezichten niet
aan hebben, of die niet meer bestaan. Dat is een sleutel die op een andere node na "uit" al weg
is. Per sleutel één transactie: eerst de schakelaar van die eigenaar vast (een `UPDATE` zonder
verandering op zijn rij in `photo_pref`: op PostgreSQL het rijslot, op SQLite het schrijfslot),
dan kijken, dan unlink en fsync van de map, dan de commit. Zo zet niemand
intussen gezichten aan. Een bestaande gebruiker zonder rij in `photo_pref` blijft staan: zijn
schakelaar kan niet vast.

`backup-wipe-tables` zegt welke tabellen een kopie van de database leeg moet maken
(`backup.WIPE`), één per regel. Op PostgreSQL maakt de beheerder de kopie zelf (`pg_dump`) en
leegt die tabellen erin, zoals `db-backup` dat op SQLite doet.

Geen van beide schrijft een sleutel, een byte daarvan, een pad of een eigenaar. De test draait
ze als echt proces en zoekt de id, de sleutel en de paden in stdout en stderr.

## Beeld: het bestand herstelt zich

`vecfile.is_current(directory, user_id, built_gen)` kijkt nu ook of de gen in de kop van het
bestand gelijk is aan `beeld_owner.built_gen`. Is dat niet zo, dan hoort het bestand niet bij de
database (een andere node maakte het, of de database of de map kwam uit een kopie) en zet
`search()` een nieuwe bouw klaar. `vec_job` en `_behind` doen hetzelfde. Tot het nieuwe bestand
er is, zoekt rank nog in het oude; de sha per treffer houdt oude inhoud tegen (§3.9).

Op PostgreSQL loopt het opzoeken van de treffers zonder deadline: `deadline()` stopt alleen een
vraag op SQLite. Het zijn hooguit top_k nodes op hun sleutel. De grens op PostgreSQL komt met P5.

## Golf 1: photo_rows.write en een verplaatsing (40P01)

photometa schrijft de plek (`photo_geo`) voor veel bestanden in één transactie: per inhoud een
statement (executemany). De trigger `geo_biu` zet bij elke nieuwe rij de node vast (`FOR SHARE`).
Een verplaatsing of prullenbak van een map erboven zet de submap vast op volgorde van `mpath`.
Zonder het slot van de boom kwamen ze elkaar in een andere volgorde tegen.

Nagedaan met twee verbindingen (`tests/test_pg_photo_rows.py`): T1 schrijft eerst voor het
bestand met het hoogste pad, T2 verplaatst `werk` of zet hem in de prullenbak, dan schrijft T1
voor het laagste pad. Zonder herstel: 40P01 in alle zes de varianten waar de app eerst was
(`write`, `write` met replace en `clear`, elk met verplaatsen en prullenbak). Waar de map eerst
was, wachtte de app gewoon.

Herstel zoals purge en retime: `write()` en `clear()` nemen eerst `db.lock_tree` (op SQLite
niets). Daarna 12 van 12 groen, en de boom en `photo_geo.dead` kloppen (`check_tree`,
`fase6_fill` geeft nul).

Niet nagedaan: `wipe_chunk` (locatie "uit") en de keten `geo.places`. Die schrijven
`photo_geo` met één statement per transactie en zetten geen node vast. Zie "Open punten".

## Tests

Nieuw:

- `tests/analyse/test_analyse_pg.py`: de lijst van triggers gelijk aan 0013; de schakelaar per
  database (SQLite met en zonder de instelling, PostgreSQL zonder de instelling, zonder de
  triggers, een andere database); `check` vraagt niets zonder de instellingen; op PostgreSQL
  (`pg`) één trigger uit (`DISABLE TRIGGER person_bd_face`) houdt de analyse uit, met één regel
  in het log met alleen de naam, en weer aan is weer aan.
- `tests/people/faces/test_faces_keys.py`: geen nieuwe sleutel bij versleutelde rijen, de scan
  wacht, `/beheer` en analyse-status zeggen het aantal, de sleutel terug en alles gaat verder;
  sleutels-check (in orde, rechten, symlink, weg, grootte, sleutelmap als symlink, eigenaar niet
  10001, zonder sleutelmap); `--opruimen` haalt alleen overbodige sleutels weg; de commando's
  als proces zeggen alleen aantallen; backup-wipe-tables.
- `tests/people/faces/test_faces_wipe_shred.py`: het wissen hierboven, op beide databases.
- `tests/beeld/test_vecfile.py`: een bestand met een andere gen dan `built_gen` wordt opnieuw
  gemaakt.
- `tests/test_pg_photo_rows.py`: de 40P01 hierboven (12 varianten, `pg`).

Aangepast:

- `tests/beeld/conftest.py` en `tests/people/faces/conftest.py` gebruiken de fixture
  `database_url` (via `tests/analyse/pgdb.py`); op PostgreSQL krijgen de Settings
  `PLATFORM_ANALYSE_POSTGRES` en kijkt `pgcheck` naar de echte triggers van de testdatabase.
  `tests/beeld/nep.py` hoefde niet te veranderen: hij krijgt de Settings van de fixture `beeld`,
  ook in `tests/search/test_search_logs.py`.
- `test_photo_settings.py` en `test_jobs_lanes.py`: de fixture `analyse_volgt_de_schakelaar`
  laat `analyse_on` op PostgreSQL alleen de schakelaar volgen, zoals op SQLite (zoals
  `test_photo_settings.py` al deed met monkeypatch).
- De plannen (`test_beeld_search.py`, `test_faces_plans.py`): op PostgreSQL met 5000 rijen en
  `ANALYZE`. Op een paar rijen kiest de planner terecht de index op de eigenaar; met echte
  aantallen kiest hij de index waar het om gaat. Op SQLite ongewijzigd.

`sqlite_only` (met reden in de marker), alle zeven over de bytes van het SQLite-bestand of de
kopie van een SQLite-bestand:

- `test_faces_wipe_bytes.py`: `test_niet_tonen_en_ongedaan_maken`,
  `test_niet_benoemde_wissen_en_een_persoon_wissen`,
  `test_definitief_wissen_met_het_checkpoint_van_het_uur`, `test_uit_met_een_open_lezer`;
- `test_faces_backup.py`: `test_de_kopie_van_db_backup`, `test_de_kopie_van_install_sh`;
- `test_faces_wipe_latency.py`: `test_een_upload_tijdens_het_wissen_met_een_open_lezer`.

Geen test is overgeslagen, uitgezet of zwakker gemaakt op een andere manier.

### Gevonden in de hele suite

De eerste hele run op SQLite (op f98456b) gaf twee rode tests die de gerichte runs niet zagen:

- `tests/uit080/test_wandeling.py::test_elke_route_en_de_stand_zoals_in_080`: het nieuwe veld
  `AdminFaces.keys_missing` veranderde `/api/openapi.json`, en die moet gelijk blijven aan die
  van 0.8.0. Hersteld met `SkipJsonSchema` (zie "De sleutels van gezichten"); de test is
  ongewijzigd en weer groen.
- `tests/test_media_names.py::test_fuzz_10000_namen_met_vaste_seed` (0,533 s, grens 0,5 s): een
  bekende tijdtest onder last (er liep een tweede suite op PostgreSQL naast). Alleen opnieuw
  gedraaid: groen in 1,3 s.

Daarnaast: `test_jobs_lanes.py::test_de_bestaande_soorten_veranderen_niet` was rood als hij direct
na `tests/analyse/test_analyse_monitor.py` draaide (ook op cbd0922). De lifespan met de analyse
aan meldt de soorten van beeld aan, en niets haalde ze weg. De helper `started()` in
`test_analyse_monitor.py` roept nu `bjobs.disable()` aan, zoals `tests/beeld/conftest.py`. Nu
groen in die volgorde, op SQLite en op PostgreSQL.

### Gemeten

{{GEMETEN}}

## SQLite blijft gelijk

- `analyse_pg_block()` geeft op SQLite `None` zonder iets te vragen; `pgcheck.check` vraagt op
  SQLite niets.
- `ensure_key` vraagt nu `has_sealed` (een `SELECT … LIMIT 1` op `face`) voordat hij een nieuwe
  sleutel maakt. Dat is een leesvraag, alleen als er nog geen sleutel is, en verandert niets aan
  wat er geschreven wordt.
- `lock_tree` is op SQLite een no-op (geen SQL).
- De deadline van beeld zoeken en de PRAGMA's van het wissen blijven op SQLite zoals ze waren.
- De gen in `is_current` is op SQLite ook nieuw. Op één node is de gen van het bestand altijd
  `built_gen` (de bouw schrijft eerst het bestand, dan `built_gen`); valt de app tussen die twee
  om, dan bouwt hij nu één keer opnieuw.
- De OpenAPI is byte voor byte die van 0.8.0 (`test_wandeling.py`). Met de analyse aan heeft het
  antwoord van `GET /admin/search` één veld meer in het blok `faces`: `keys_missing`.

## Afwijkingen

1. `face_rows.key_owners` (wie een sleutel nodig heeft) telt alleen eigenaren met gezichten aan,
   zonder lopende wisronde, en met minstens één versleutelde vector. Na "uit" is de sleutel al
   weg terwijl de rijen nog even staan; die eigenaar mist dus geen sleutel. Het plan zegt "elke
   eigenaar met versleutelde rijen".
2. `--opruimen` laat een bestaande gebruiker zonder rij in `photo_pref` staan (zijn schakelaar
   kan niet vast). Zijn sleutel telt als "niet nodig". "Zonder face_owner aan" uit het plan is
   hier: gezichten niet aan in `photo_pref` (`face_owner` heeft geen schakelaar).
3. Een eigenaar met een sleutel die weg is, gaat pas verder bij de volgende ronde van de scan
   (elk uur, of een nieuwe foto), niet direct als de sleutel terug is.
4. Beeld zoeken op PostgreSQL zonder deadline (P5 maakt de grens).
5. `_rank` kijkt niet naar de gen (search() zet de nieuwe bouw al klaar).
6. De plantests zijn per dialect: op PostgreSQL met echte aantallen en `ANALYZE`.
7. `keys_missing` staat niet in de OpenAPI (`SkipJsonSchema`), om die gelijk te houden aan 0.8.0.
   De web kent het veld wel (`searchApi.ts`), en `test_web_contracts.py` houdt beide gelijk.
8. De regel "sleutel ontbreekt" op `/beheer` staat in het i18n-gebied `admin`
   (`admin.search.faceKeys`), niet in `photos_faces`. Dat gebied zit in de chunk van gezichten,
   en die zit op zijn grens van 12 kB. De twee redenen van PostgreSQL zijn daarom kort.
9. `photo_rows.clear()` neemt het slot van de boom ook, niet alleen `write()`: in de proef gaf
   `clear` dezelfde 40P01.

## Open punten voor de ClusterForge-template

Sleutels:

- De sleutels moeten op elke node gelijk zijn, en ook samen weggaan. Na "uit" op node A is de
  sleutel op A weg, maar op B nog niet. Tot hij daar ook weg is, is een oude kopie met die
  sleutel nog te openen. De sync van de template staat nu op `delete: false` (alleen
  toevoegen). Nodig: weghalen erbij, met een controle vooraf (deze node heeft het VIP, de map is
  een echte map van 10001, en niet leeg als er versleutelde gezichten zijn), of
  `sleutels-check --opruimen` op elke node (bijvoorbeeld elk uur). Pas dan
  `ANALYSE_POSTGRES=true`.
- Een failover binnen een minuut na een nieuwe sleutel: op de nieuwe VIP-node mist die sleutel.
  Vroeger kwam er stil een nieuwe; nu wacht de scan van die eigenaar en zegt `/beheer`
  "sleutel ontbreekt" tot de sync hem brengt.
- `sleutels-check` hoort na elke update op elke node te draaien. Exitcode 1 is een reden om niet
  door te gaan. De status van de template kan per node het aantal sleutels laten zien.
- De sleutelmap hoort niet in de reservekopie van de database. Een kopie van `/keys` is een
  geheim, met een eigen bewaartermijn.
- Een `pg_dump` als reservekopie: leeg daarin de tabellen van `backup-wipe-tables`.
- Bij verbergen of terugzetten van een gezicht (niet "uit") gaat `vec` naar NULL, maar de oude
  versie van de rij met de versleutelde vector kan nog in een dode tuple, de WAL of een kopie
  staan, en de sleutel bestaat nog. Die is dus te openen tot "uit". Op SQLite haalt
  `secure_delete` en het checkpoint van het uur hem weg. Op PostgreSQL zou dat een sleutel per
  gezicht vragen, of een nieuwe sleutel en alles opnieuw versleutelen.

Grenzen:

- De template zet nu geen `compose.analyse-limits.yaml` (en die van media en office) aan. Zonder
  dat draait de analyse zonder grens op geheugen en CPU, op de VIP-node naast de app, SeaweedFS
  en misschien de primary van PostgreSQL. Met de limits: 3 GB en 1 core.
- Het werk van de analyse (de rij `analyse`) draait alleen op de VIP-node; de rij staat in
  PostgreSQL en gaat na een failover verder. Met 1 core duurt de eerste scan van gezichten
  ±2,2 tot 2,5 uur per 55.000 foto's, en beeld ±32 minuten per 55.000.
- Geheugen per node met de analyse aan: minstens 12 GB, liever 16 GB. Het minimum van 4 GB in de
  template past niet.

Overig:

- `pgcheck` kijkt alleen bij de start en bij de cli. Zet iemand een trigger uit terwijl de app
  draait, dan merkt de app dat pas bij de volgende start. De wekelijkse controle (`weekcheck`)
  zou het kunnen meenemen.
- `wipe_chunk` en `geo.places` naast een verplaatsing: niet nagedaan. Het zijn losse statements
  op `photo_geo` zonder slot op `node`; een deadlock is daar minder waarschijnlijk, maar niet
  uitgesloten.
- De deadline van beeld zoeken op PostgreSQL (P5).
