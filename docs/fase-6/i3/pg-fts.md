# P5 pg-fts: zoeken met een index op PostgreSQL (0.8.2)

Pakket P5 van 0.8.2 (plan §2, golf W3, punt 9). PR #6 bouwde de index; de review van PR #6 (F1
tot en met F17) staat in [w3.md](w3.md). Dit stuk zegt hoe het nu werkt, waar het afwijkt van
het plan en waarom, wat er lokaal gemeten is en wat CI bewijst. Punt 8 (de job `zoek-perf-pg`)
en de cache van de zoekvragen kwamen er in golf 4 bij.

Op SQLite verandert niets: daar blijft FTS5 van 0010, met de SQL van 0.8.1
(`tests/search/test_sql_sqlite.py`).

## Het ontwerp zoals het gebouwd is

### De tabellen (migratie 0014, alleen PostgreSQL)

- **`fts_node(rowid, tsv, otsv, capped)`**. `rowid` is `node.num`. Dezelfde namen als de
  FTS5-tabellen van SQLite: de vragen van `app/search/engine.py` houden zo hun vorm. GIN op `tsv`
  en op `otsv`.
- **`tsv`**: de naam (gewicht A), de titel uit de mediabibliotheek (B), de tekst (C), de OCR van
  de inhoud (C) en meta (D). Elk deel is een eigen "kolom": `cf_cat` zet er één lege plek tussen.
  Een frase loopt dus niet over twee delen, net als in FTS5 (F11).
- **`otsv`**: `tsv` met de OCR van een foto erbij, alleen als die er is. Die OCR is alleen van de
  eigenaar (Keuze 16 en 25a). Een vraag kijkt alleen voor de eigen nodes in `otsv`.
- **`capped`**: de tekst paste niet in een tsvector en is ingekort (zie hieronder).
- **`fts_name`**: een view `(rowid, search_name)` op `node`. De stukken van namen zoekt de app
  met `LIKE '%woord%'`, zonder index. Geen `pg_trgm`: het plan staat alleen ingebouwde dingen toe
  (vraag 1, geen extensies).

### Bijhouden: triggers in de transactie zelf

Zes triggers op `node`, `blob_text`, `photo_ocr` en `media_item` roepen `fts_refresh(nums)` aan.
Die schrijft de rij van elke node opnieuw uit de bron (`fts_rows`, `fts_doc`). Dat gebeurt in
dezelfde transactie als de wijziging: wie schrijft, ziet de index meteen kloppen, en een
rollback haalt ook de index terug. Zo werkt de FTS5-index van SQLite ook (de triggers van 0010).

Loopt er een herbouw (`search_state.cursor` gezet), dan schrijft een trigger alleen voor de nums
die de herbouw al deed; de rest neemt een volgend stuk mee. De cursor leest `fts_refresh` met
`FOR SHARE`: een stuk van de herbouw en een schrijver wachten op elkaar (F4). Tijdens een
herbouw zoekt de app met LIKE, zoals op SQLite.

### Woorden: `cf_fold` en `fold()`

De index en de vraag moeten dezelfde woorden zien. De vraag vouwt in Python met `fold()`
(`app/text.py`: NFKD, zonder tekens op een letter, `casefold()`). De index vouwt in de database
met `cf_fold`:

- NFKD met `normalize()`, elk teken met een combining class weg (de bevroren lijst `MARKS`);
- `lower()`, `ß` als `ss`, en wat `casefold()` anders doet dan `lower()` (de bevroren tabellen
  `FOLD_FROM` en `FOLD_TO`: `ς` als `σ`, Cherokee, …);
- alles wat geen letter of cijfer is, wordt een spatie. Daarna splitst
  `to_tsvector('simple', …)` op spaties, zonder stam en zonder stopwoorden.

De tabellen zijn bevroren tegen Python 3.13 (Unicode 15.1) en `lower()` van PostgreSQL 16 met
`C.UTF-8`. Daarmee geeft elke letter en elk cijfer hetzelfde als `fold()` (F2). Het verschil met
`fold()`: die laat leestekens staan, `cf_fold` maakt er spaties van. Voor zoeken telt dat niet:
de vraag splitst ook op leestekens (`app/search/query.py`).

### De vraag en de rang

- **Hele woorden**: `fts_node.tsv @@ to_tsquery('simple', :q)`, en voor de eigen nodes ook
  `otsv`. `Parsed.tsquery()` maakt `:q`: elk woord tussen aanhalingstekens, een frase met `<->`,
  de termen met `&`, het laatste woord als voorvoegsel (`:*`).
- **De rang**: `-ts_rank('{0.05, 0.1, 0.6, 1.0}', tsv, q, 1)`. De gewichten D, C, B en A staan
  als 0,5 : 1 : 6 : 10, zoals bm25 op SQLite (meta 0,5, tekst 1, titel 6, naam 10). Normalisatie
  1 deelt door 1 + log(lengte): een korte naam gaat voor, zoals bij bm25. Voor een node van een
  ander telt `otsv` niet mee, ook niet in de volgorde.
- **De rondes** zijn die van SQLite (Keuze 9): ronde A gerangschikt binnen
  `search_deadline_ms` (120 ms), dan de stukken van namen als A minder dan een pagina gaf; ronde B
  in de volgorde van `rowid`, zonder rang, met de rest van A + 40 ms; ronde C alleen de namen,
  hooguit 20 ms.
- **De grens**: een savepoint met `SET LOCAL statement_timeout`. Een vraag op de grens geeft
  `query_canceled` (57014). Dan gaat het savepoint terug, telt de grens als geraakt, en blijft
  de transactie eromheen bruikbaar. Stopt een nieuwere vraag van dezelfde gebruiker deze
  (Keuze 13), dan stuurt de app `pg_cancel_backend` via een andere verbinding uit de pool, elke
  50 ms tot het blok klaar is.
- **LIKE gaat ook door `limits.admit`**: met de index uit of tijdens een herbouw dezelfde
  snelheid per gebruiker en dezelfde 2 tegelijk (F5, plan punt 5). Ook op SQLite.

### De grens van 1 MB

Een tsvector mag niet groter zijn dan 1 MB. `fts_doc` bouwt de hele rij in één keer. Past die
niet, dan deel voor deel in vaste volgorde (naam, titel, tekst, OCR, meta, dan de OCR van de
foto in `otsv`), elk met `cf_add`. `cf_add` zoekt de langste reeks woorden vooraan die nog past
(binair zoeken op het aantal woorden, niet halveren). Wat erna komt, is niet vindbaar (plan
vraag 8). De rij krijgt `capped`. `/admin/search` telt die rijen (`index.capped`); `/beheer`
toont het getal als het niet 0 is. Op SQLite is het altijd 0 (F3, F10).

`cf_add` en `fts_doc` zijn `PARALLEL UNSAFE`: het blok met `EXCEPTION` is een subtransactie, en
die mag in geen enkel deel van een parallel plan (F14).

### Controle en herbouw

- **`check()`** (`app/search/rebuild.py`, ook de weekcontrole) vergelijkt `fts_node` met
  `fts_rows` (wat de bron nu zou geven). Alleen lezen: de test draait hem in een READ ONLY
  transactie (F13).
- **De herbouw** gaat in stukken, zoals op SQLite: per stuk `fts_refresh(…, force => true)` en
  de cursor verder; het laatste stuk zet de cursor op NULL.
- **db-copy** zet de cursor op 0 en maakt `fts_node` leeg; de app bouwt de index op bij het
  starten.

### Bekende grenzen

- **Een frase in een lange tekst.** Een tsvector kent hooguit plek 16.383: daarboven staan alle
  woorden op 16.383. En een woord houdt hooguit 256 plekken. Voorbij die grenzen vindt een
  frase soms niet wat FTS5 vindt, of juist wel. Losse woorden vindt de index tot de grens van
  1 MB. Zonder extensie is dat niet te verhelpen. Staat in het runbook van het cluster.
- **De ctype van de database moet UTF-8 zijn** (`C.UTF-8`, zoals ClusterForge hem maakt). Met
  `LC_CTYPE` `C` kennen `lower()` en `[[:alnum:]]` alleen ASCII, en mist de index elke andere
  letter. De app vouwt bij de start een vaste proef (`FOLD_PROBE`) en waarschuwt in het log als
  die niet klopt (F12).
- **De stukken van namen zijn een scan** over `node` (via de view `fts_name`), zonder index.

## Afwijkingen van het plan, en waarom

| Plan (P5) | Gebouwd | Waarom |
|---|---|---|
| `search_doc` met `search_dirty`; triggers zetten alleen een num klaar, een taak in de lane index bouwt de tsvector in Python (punt 1 en 2) | `fts_node` met triggers die de rij meteen schrijven, in de transactie; de tsvector in plpgsql (`cf_fold`, `fts_doc`) | Zo werkt de index zoals FTS5 op SQLite: na een commit klopt hij, er is geen achterstand en geen taak om te bewaken. De vragen in `engine.py` houden hun vorm (dezelfde tabelnamen). Het kost wel tijd bij het schrijven (zie de metingen). Besluit van PR #6; voor Jonas als afwijking genoteerd ([w3.md](w3.md), open punten). |
| "While search_dirty is not empty the LIKE engine answers" | LIKE zolang de cursor van een herbouw staat | Er is geen `search_dirty`. De cursor is dezelfde regel als op SQLite (Keuze 10 en 12). |
| De stukken van namen "as today" (met trigram) | `fts_name` als view, `LIKE` zonder index | Geen extensie (vraag 1). Een scan over `node`: bij 60.000 rijen lokaal ±35 ms ([w3.md](w3.md)). |
| `bm25` | `ts_rank` met gewichten in dezelfde verhouding | PostgreSQL heeft geen bm25 zonder extensie. De volgorde lijkt erop, maar is niet overal gelijk. En `ts_rank` leest de hele vector: bij grote documenten te traag (Lokaal gemeten). |
| De caps in Python (positions clamp, capped docs geteld) | In plpgsql: de langste reeks woorden vooraan, `capped` per rij | Volgt uit de triggers in de database. |
| Punt 8 en 9 in golf 3 | In golf 4 (dit stuk en de job `zoek-perf-pg`) | Zaten niet in PR #6. |

Er kwam één ding bij dat het plan niet noemde: de zoekvragen gaan weer door de cache van
SQLAlchemy (hieronder).

## De cache van de zoekvragen (golf 4)

PR #6 maakte voor elke dialect eigen stukken SQL: `FtsMatch`, `FtsRank`, `NotFromPocr`,
`AllWordsIn`, `NumNotIn` en `InRanges`. Ze hadden `inherit_cache = False`. Dan heeft geen
enkele zoekvraag een cache-sleutel, en compileert SQLAlchemy elke vraag bij elke uitvoering
opnieuw. Ook op SQLite: 0.8.1 gebruikte daar stukken die wel in de cache gingen.

Gemeten op een lege, gemigreerde database (4 cores, de drie statements van een zoekvraag:
ronde A, de stukken van namen en ronde B; 400 vragen per vorm, de mediaan per statement):

| | Compileren | `conn.execute`, voor | na |
|---|---|---|---|
| SQLite, eigen bereik | 0,18 – 0,23 ms | 0,99 – 1,13 ms | 0,44 – 0,58 ms |
| SQLite, gedeeld (3 bereiken) | 0,26 – 0,50 ms | 1,17 – 1,65 ms | 0,52 – 0,70 ms |
| PostgreSQL 16, eigen bereik | 0,20 – 0,29 ms | 0,98 – 1,11 ms | 0,38 – 0,55 ms |
| PostgreSQL 16, gedeeld | 0,23 – 0,33 ms | 1,06 – 1,16 ms | 0,43 – 0,68 ms |

Voor: elke uitvoering `NO_CACHE_KEY`. Na: 4 keer `CACHE_MISS` (de vormen) en 1196 keer
`CACHE_HIT`. De sleutel zelf kost 0,04 – 0,10 ms per statement. Een zoekvraag draait 1 tot 3 van
deze statements, in de event loop: ±0,5 ms per statement minder. Dat is meer dan ruis: golf 1
(H2) haalde om ±1 ms per pagina al de ORM-objecten weg.

Hoe het nu werkt: elk stuk maakt zijn parameters bij het maken, niet bij het compileren. Zijn
sleutel (`_traverse_internals`) is alleen de vorm: de tabel, of er een eigenaar is, waartegen hij
groepeert (de haakjes van F17), en de parameters zelf. Van een parameter telt alleen de naam en
het type, nooit de waarde. Twee vragen met dezelfde vorm delen zo één gecompileerde SQL. Een
andere vorm (een lang woord meer voor `fts_name`, een eigenaar of niet, gedeeld of niet, meer
bereiken) heeft een andere sleutel. SQLAlchemy koppelt de parameters bij een treffer op hun
naam; daarom heeft `NumNotIn` per dialect een eigen naam (`without` op SQLite,
`without_nums` op PostgreSQL), want de waarde verschilt.

`tests/search/test_sql_cache.py` houdt het vast (11 tests, op SQLite en PostgreSQL): dezelfde
vorm één sleutel met de eigen waarden, zeven paren vormen met elk een eigen sleutel, en tegen een
echte database met een eigen cache: een vraag met dezelfde vorm voegt niets toe en geeft haar
eigen treffers. Op de code van vóór golf 4: 10 van de 11 rood. De SQL op SQLite is gelijk
gebleven (`test_sql_sqlite.py` groen).

## De meting van §4.4 op PostgreSQL (job `zoek-perf-pg`)

Dezelfde test als de stap §4.4 van `zoek-perf`: `tests/search/test_perf_100k.py`. Met
`PLATFORM_TEST_DATABASE_URL` maakt hij de boom in een eigen, nieuwe database op die server, en
start hij de app met `PLATFORM_DATABASE_URL` naar die database. Verder alles gelijk: de boom van
100.000 (seed 6, profiel groot), de zes rollen, de 200 vragen van §4.3, de routes, en de harde
poorten (zoeken en volgende p95 ≤ 200 ms, filters ≤ 150, suggesties ≤ 50, snip ≤ 40, 4 tegelijk
≤ 300, `/health` ≤ 50, geen 5xx of 429).

Wat er voor nodig was, zonder kopie van de bench:

- `bench.maak(…, database_url=…)`: dezelfde migratie (tot en met 0014), dezelfde boom en dezelfde
  INSERTs. `_PgCon` is genoeg van een `sqlite3.Connection` voor `vul()`: per stuk van 5.000 rijen
  één `INSERT … SELECT … FROM unnest(tekst-arrays)`, elke kolom gecast naar zijn type. De
  triggers van 0013 en 0014 zetten num, pad, dead en de index. Daarna `VACUUM ANALYZE`, wat
  autovacuum na zo'n lading ook doet.
- `zonder_2fa` en de versie in het verslag ook voor PostgreSQL. Voor het meten controleert de
  test `cf_fold('Éλ') = 'eλ'`: anders meet hij een index die letters mist. En dat `fts_node`
  heel is en er geen herbouw loopt: anders zoekt de app met LIKE (`engine_for`).
- `test_bench.py::test_dezelfde_boom_op_postgresql` (marker `pg`, dus in `api-postgres`): een
  kleine boom op PostgreSQL heeft dezelfde tellingen als op SQLite, de boom klopt
  (`check_tree`), en de index is gelijk aan `fts_rows`.

De job: PostgreSQL 17 (`postgres:17-alpine`, zoals `api-postgres`), de test en de app onder
`taskset -c 0-2`, de getallen als artefact `zoek-perf-pg`, en aan het eind een blok
`=== kort ===` met de versies, de boom en de poorten. `test_zoek_ci.py` houdt de vorm van de job
vast.

### Lokaal gemeten

Lokaal: 4 cores, PostgreSQL 16.15 met `C.UTF-8`, in tmpfs met `fsync=off`. De test en de app op
de cores 0-2, zoals in CI. De echte maat met de stappen van de job zoals ze in `ci.yml` staan
(versies, §4.4, samenvatting). Daarvoor één keer dezelfde test op een kleine boom
(`ZOEK_PERF_N=3000`, ook met profiel groot, dus ook met de 200 documenten van 1 MB).

| | n = 3.000 | n = 100.000 |
|---|---|---|
| bestanden, tekens | 3.200, 206,6 miljoen | 100.200, 430,5 miljoen |
| de boom maken | 132 s (de nodes 114 s) | 302 s (de nodes 262 s) |
| de test als geheel | 587 s | 849 s |
| de poorten | gehaald | **niet**: 4 tegelijk p95 366,5 > 300 |

Bij 100.000, in ms. Elke p95 is de mediaan van de p95 van 3 rondes. B en C: hoe vaak ronde B en
ronde C nodig waren, op 600 vragen.

| Rol | zoeken p50 / p95 / p99 / max | B, C | volgende p95 | filters p95 | suggesties p95 | snip p95 |
|---|---|---|---|---|---|---|
| eigenaar | 160,5 / 183,4 / 192,9 / 202,0 | 429, 42 | 191,2 | 44,2 | 27,3 | 24,0 |
| ontvanger (5 delingen) | 46,4 / 185,2 / 196,8 / 202,9 | 25, 26 | 193,7 | 55,4 | 38,3 | 16,4 |
| ontvanger (20 delingen) | 52,4 / 194,6 / 198,8 / 217,8 | 101, 37 | 195,8 | 62,1 | 34,9 | 21,0 |
| groepslid | 44,9 / 192,7 / 196,0 / 209,3 | 40, 38 | 194,9 | 55,9 | 34,8 | 18,7 |
| gast | 21,2 / 27,3 / 34,8 / 67,9 | 0, 0 | 17,3 | 22,0 | 18,8 | 4,9 |
| alles (30 hoofdmappen) | 161,7 / 177,1 / 194,8 / 227,5 | 462, 12 | 195,9 | 46,6 | 37,7 | 23,8 |
| **poort** | **p95 ≤ 200** | rapport | **≤ 200** | **≤ 150** | **≤ 50** | **≤ 40** |

4 tegelijk: p95 366,5, p99 396,2, max 422,2 ms (poort ≤ 300: **niet gehaald**). `/health`
ernaast: p95 5,9 ms. De eerste vraag na een herstart: 204,2 ms (doel ≤ 300).

Ter vergelijking SQLite 3.46.1 in CI ([metingen.md](../metingen.md), M1): de eigenaar p95 73,4 ms
zonder één keer ronde B, 4 tegelijk p95 209,4 ms.

**Wat dat zegt.** Elke p95 van zoeken ligt onder 200, maar niet omdat de vraag snel is. Ronde A
haalt zijn 120 ms vaak niet: bij de eigenaar en bij "alles" in 7 van de 10 vragen (B 429 en 462
op 600). De rondes begrenzen dan de tijd: A 120 ms, B de rest + 40 ms, C hooguit 20 ms. Zo komt
de p95 op 177 tot 195 ms, vlak onder de poort, en de p50 van de eigenaar op 160 ms. Met 4
tegelijk delen de vier vragen in PostgreSQL de cores met de app; dan gaat de tijd over de 300.

**Waarom ronde A te traag is.** `ts_rank` leest de hele tsvector van elke treffer, en
`ORDER BY` de rang vraagt de rang van alle treffers. Een vraag als `hypotheek` raakt alle 200
grote documenten van profiel groot. Gemeten op de boom van 3.000 (zelfde machine, de mediaan van
5 keer; geen rij `capped`):

| Wat | Gemeten |
|---|---|
| `fts_node` | 3.440 rijen, `tsv` samen 68 MB, 201 rijen boven 100 kB (de grootste 354 kB) |
| zes vragen (`hypotheek`, `agenda`, `school`, `supplier`, `factuur`, `rapport jaar`) | 444 tot 678 treffers, elk met de 201 grote rijen (±69 MB) |
| de eerste 31 treffers, zonder rang | 0,5 – 0,8 ms |
| alle treffers op rang, de eerste 31 | 95 – 185 ms |
| idem, zonder de rijen boven 100 kB | 2,2 – 3,6 ms |

De tijd zit dus in de grote vectoren, niet in de index. SQLite heeft dat niet: `bm25` rekent met
wat in de index van FTS5 staat en leest het document niet.

## Wat CI bewijst

- **zoek-perf-pg**: §4.4 op PostgreSQL 17 bij 100.000 bestanden, met de poorten hard. Lokaal
  haalt hij de poort van 4 tegelijk niet (hierboven). Dan is de job rood tot dat is opgelost:
  de poort blijft zoals hij is.
- **api-postgres** (PostgreSQL 17, de hele suite): de zoektests met de index (`[fts]` en
  `needs_fts`), `test_zoekindex_pg.py`, `test_sql_cache.py` en de boom van de bench op
  PostgreSQL.
- **api-postgres-15**: migratie 0014 heen, terug en heen (`test_migration_0014.py`), als
  eigenaar zonder superuser.
- **api** (SQLite): `test_sql_sqlite.py` (de tekst van 0.8.1) en `test_sql_cache.py`.
- **zoek-perf** (SQLite 3.46.1): §4.4 zoals voorheen, nu met de cache.
- **api** en **api-postgres**: `test_search_no_raw.py` (geen vraag als tekst in een MATCH)
  en `test_zoek_ci.py` (de vorm van `zoek-perf-pg`).

## Open punten

- **zoek-perf-pg haalt §4.4 niet: ronde A is op PostgreSQL te traag** zodra een vraag de grote
  documenten raakt. Lokaal bij 100.000: 4 tegelijk p95 366,5 ms (poort 300); zoeken p95 177 tot
  195 ms, alleen onder 200 omdat de rondes de tijd begrenzen. De oorzaak staat hierboven
  (`ts_rank` leest de hele tsvector van elke treffer). Herstel vraagt een besluit: een kleine
  vector voor de rang naast `tsv` (een nieuwe migratie; 0015, 0016 en 0018 zijn van andere
  branches), of een andere volgorde in ronde A (dan is de rang niet meer die van SQLite). Niet in
  golf 4 gedaan. Voor Jonas.
- **Afwijking van het plan**: de index via triggers in de transactie, niet via `search_dirty` en
  een taak (zie boven). Het werkt en de tests zijn groen; de prijs is de tijd bij het schrijven
  (elke INSERT schrijft zijn rij in de index; de nodes van de boom van 100.000: 262 s). Nog
  voor Jonas.
- **De stukken van namen** blijven een scan over `node` zonder index (geen `pg_trgm`). In de
  meting zitten ze in ronde C (hooguit 20 ms) en in de aanvulling na ronde A.
- **De frase voorbij plek 16.383 of na de 256e keer van een woord**: een grens van tsvector,
  geen herstel zonder extensie (Bekende grenzen).
