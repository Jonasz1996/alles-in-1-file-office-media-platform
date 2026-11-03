# Z0-api: de kern van zoeken en foto's, zoals hij gebouwd is

Dit is het deel Z0-api van het bouwontwerp van fase 6 ([bouwontwerp.md](bouwontwerp.md), §0–§3).
Z0 bouwt geen enkele functie: het legt vast waar Z1 tot Z7 en X1 in gaan bouwen. Na Z0 is de app
precies 0.7.1 plus een lege zoekindex: elk eindpunt van §3.4 bestaat met zijn pad, zijn body en
zijn antwoordmodel en geeft 501 `not_built`, en zoeken werkt nog net zoals in 0.7.0 (op naam, met
LIKE).

Het web-deel staat in [z0-web.md](z0-web.md). Het harnas, de fixtures, de wereld en de referentie
(§3.6) en de CI-jobs (§4.6) zijn van andere werkers; wat zij nog moeten doen staat onderaan bij
**Open**.

De keuzes B49–B60 staan nog open. Z0 bouwt op de standaard: geen tesseract in het image
(`ocr_enabled` uit, en de rij `ocr` draait dan niet), geen automatische gezichten, niet zoeken
met een foto als vraag, GPS alleen voor de eigenaar, en de bronnen van GeoNames en Natural Earth
haalt `install.sh` bij de installatie (Z7).

## Wat er is

### Het schema (§3.1)

- **`api/migrations/versions/0010_zoeken.py`** (`down_revision = "0009"`), heen en terug:
  - vier kolommen op `node`: `num` (een vast nummer uit `search_seq`, de rowid in de
    FTS-tabellen), `mpath` (het pad van nummers, `/1/7/42/`, zodat een submap één bereik op
    `ix_node_mpath` is), `dead` (1 als de node zelf of een map erboven in de prullenbak ligt) en
    `ftype` (de soort, een gegenereerde kolom zonder trigger);
  - twee kolommen op `album_item`: `source` (`'hand'` of `'rule'`) en `shared`;
  - zeventien nieuwe tabellen: `search_seq`, `search_state`, `blob_text`, `ocr_page`, `analysis`,
    `blob_meta`, `photo_geo`, `blob_hash`, `dup_pair`, `dup_ignore`, `person`, `photo_person`,
    `photo_ocr`, `photo_pref`, `memory_hide`, `album_rule`, `album_skip`;
  - de view `search_src` en de twee FTS5-tabellen `fts_node` (external content over die view) en
    `fts_name` (trigram op de naam), met hun shadowtabellen;
  - 25 triggers die het nummer, het pad, `dead`, de FTS-rijen en de foto-rijen bijhouden;
  - het vullen achteraf: elke bestaande node krijgt zijn nummer en pad, en de FTS-tabellen worden
    gevuld.
- **Zonder FTS5** (een SQLite zonder die module) maakt de migratie geen view en geen
  FTS-tabellen, en alleen de vijf triggers die nummer, pad, `dead` en de foto-rijen bijhouden
  (`TRIGGERS_NO_FTS`). Zoeken gaat dan via `LikeFallback`. De migratie kijkt zelf of FTS5 er is,
  en `config.attributes["search_fts5"]` laat een test het afdwingen.
- **`api/app/search/schema.py`** is dezelfde SQL als een leesbaar geheel: `FTYPE_SQL`,
  `NODE_COLUMNS`, `NODE_INDEXES`, `ALBUM_ITEM_COLUMNS`, `NEW_TABLES`, `VIEW_SQL`, `FTS_TABLES`,
  `TRIGGERS` en `TRIGGERS_NO_FTS`. De migratie heeft een eigen, bevroren kopie.
  `tests/search/test_schema_triggers.py` eist dat de drie (`sqlite_master`, de kopie in de
  migratie, en `TRIGGERS` hier) woord voor woord gelijk zijn, en
  `tests/search/test_ftype.py` dat `FTYPE_SQL` voor elk mime-type hetzelfde zegt als
  `mime.ftype()`. Wie hier iets verandert, moet dus ook een nieuwe migratie schrijven.
- **`api/app/models.py`**: de zeventien tabellen als model, en de nieuwe kolommen op `Node` en
  `AlbumItem`.
- **`api/app/mime.py`**: `FTYPES`, `SPREADSHEET_TYPES`, `PRESENTATION_TYPES`, `ARCHIVE_TYPES` en
  `ftype()`, de ene plek die zegt welke soort een bestand heeft.

### De vaste Python-vormen (§3.3)

Elk bestand zegt in zijn docstring wie de eigenaar is en wat Z0 al af heeft.

- `app/search/types.py` — `SearchParams`, `Hit`, `PathPart`, `SearchOut`, `Group`, `Suggestion`,
  `StepState`, `AnalysisOut`, en de rest van §3.3 en §3.4 voor zoeken.
- `app/search/engine.py` — het protocol `Engine` en `select_engine(settings, conn)`. Die geeft
  `LikeFallback` als de index uit staat (`PLATFORM_SEARCH_INDEX=uit`), als de database geen
  `fts_node` heeft (SQLite zonder FTS5, of PostgreSQL), of zolang er geen FTS-motor is (tot Z1).
- `app/search/like.py` — `LikeFallback`: het zoeken van 0.7.0 achter het protocol.
- `app/search/query.py` — `parse()`: de enige plek die een FTS5-uitdrukking maakt (Z1 bouwt hem).
- `app/search/deadline.py` — de vorm van de tijdsgrens met `raw.interrupt` (Keuze 9).
- `app/search/limits.py` — hoeveel vragen tegelijk (Keuze 13).
- `app/search/filters.py` — het register van filters en suggesties.
- `app/search/live.py` — **`own_live`**: de enige vorm voor "eigen, levende bestanden" (Keuze
  25a), plus `OWN_LIVE_SQL` voor rauwe SQL. Af, met tests.
- `app/search/hooks.py` — `blob_added`, `photo_copy`, `groups`, `suggest`, `backfill`. Af; dit
  bestand importeert geen enkel pakket, zodat de app zonder Z1–Z6 precies 0.7.1 is.
- `app/analysis.py` — de stappen per inhoud (`text`, `hash`, `ocr`) in de tabel `analysis`.
  `write_text()` is af: de enige schrijver van `blob_text`.
- `app/extract/result.py` — wat de worker per blob terugstuurt voordat de tekst zelf over de pipe
  gaat.
- `app/geo/types.py` — `Location`, `Place` en de API-vormen van de plek, de kaart, de plaatsen en
  de duplicaten.
- `app/people/types.py` — de API-vormen van personen, herinneringen en slimme albums.
- `app/prefs.py` — `PHOTO_PREFS` en de foto-instellingen per gebruiker (locatie `mij`/`uit`,
  `faces` uit).

### De instellingen (§3.2)

In `app/config.py`: `search_index`, `search_deadline_ms` (120), `search_inflight` (2),
`search_queue` (20), `search_max_text` (1 MiB), `search_timing`, `text_batch` (16), `places_db`,
`geo_src`, `index_defer_transcodes` (2), `ocr_enabled` (uit), `ocr_max_pages`, `ocr_per_hour`,
`ocr_pages_per_day`.

Z0 leest er zelf twee: `search_index` (in `select_engine`) en `ocr_enabled` (in
`jobs.active_lanes`). De rest staat klaar voor Z1, Z2, Z3 en X1; `search_inflight` en
`search_queue` staan in de docstring van `limits.py`, die Z1 bouwt.

### De API-contracten (§3.4)

Elk eindpunt bestaat met zijn pad, zijn methode, zijn body en zijn antwoordmodel, gemonteerd in
`main.py`, en geeft 501 `not_built` met het pakket erbij (`app/routers/stubs.py`). De inlog wordt
eerst gecontroleerd, en voor een beheerroute ook de rol — net zoals straks bij de echte route.
`tests/search/test_api_stubs.py` loopt ze allemaal langs: zonder inlog 401, als gewone gebruiker
op een beheerroute 403, en ingelogd 501 met de juiste code.

| Bestand | Routes | Pakket |
|---|---|---|
| `routers/search.py` | `GET /search`, `GET /search/suggest` (en de tags van 0.7.0) | Z1 |
| `routers/pages_words.py` | `GET /files/{id}/pages/{n}/words`, `GET /files/{id}/analysis` | Z2 |
| `routers/geo.py` | `GET /files/{id}/location`, `GET /geo/attribution` | Z3 |
| `routers/photos_map.py` | `GET /photos/map`, `/photos/map/items`, `/photos/places`, `/photos/share-check` | Z3 |
| `routers/duplicates.py` | `GET /duplicates`, `POST /duplicates/ignore` | Z3 |
| `routers/people.py` | de negen routes van personen en van personen op een node | Z6 |
| `routers/memories.py` | `GET /photos/memories` en het verbergen | Z6 |
| `routers/albums_rule.py` | `PUT/DELETE /albums/{id}/rule`, `/rule/confirm`, `/refresh` | Z6 |
| `routers/photo_prefs.py` | `GET/PATCH /me/photo-settings` | Z3 |
| `routers/ocr.py` | `POST /files/{id}/ocr` | X1 |
| `routers/admin_search.py` | `GET/PATCH /admin/search`, `/search/rebuild`, `/search/failed`, `/search/retry` | Z1, Z7 |

`GET /search` is geen stub: hij gaat langs `select_engine` en komt zo bij `LikeFallback` uit, dus
zoeken op naam blijft werken zoals in 0.7.0. De code `not_built` staat in
`web/src/i18n/errors/common.ts`, en de codes van zoeken en foto's in de catalogi die Z0-web
aanlegt.

### De plekken in de bestaande app (§3.5)

Elk met een test in `tests/search/test_hotspots.py`, behalve waar anders staat.

- **`app/jobs.py`**: de rijen `index` en `ocr`. `index` hoort bij `BASE_LANES` en draait dus ook
  zonder media (tekst lezen gaat buiten de zandbak als er geen mediaworker is); `ocr` draait
  alleen als media én OCR aan staan (`active_lanes`). Beide hebben één worker (`lane_workers`).
  Test: `tests/test_jobs_lanes.py`.
- **`app/main.py`**: de routers gemonteerd; onder `/search`, `/photos` en `/people` gaat de hele
  query string uit het log (`QUERY_PATHS`), want een filter op een plaats, een persoon of een
  woord is zelf een gegeven; en `lifespan` roept `search_hooks.backfill(settings)` vóór de rijen
  starten. Tests: `tests/search/test_log_hiding.py`, `tests/search/test_contracts.py`.
- **`app/cli.py`**:
  - `db-backup` is af: een consistente kopie terwijl de app draait
    (`sqlite3.Connection.backup`, dus ook met een schrijver in de WAL), in `data/backup/` met
    rechten 0700, de naam `platform-<head>-<tijd>.db` met de revisie uit `alembic_version`,
    eerst als `.part` en dan `os.replace`, en per revisie blijft de nieuwste staan.
  - `db-downgrade` waarschuwt nu ook over wat van zoeken weggaat, telt de items van slimme
    albums die nog niet gedeeld worden (`source='rule'` en `shared=0`) en vraagt of die er eerst
    uit moeten (standaard ja); met `--ja` haalt hij ze weg zonder te vragen. De vraag komt alleen
    als 0010 ook echt weggaat (`removed_revisions`).
  - vijf stubs die zeggen welk pakket ze bouwt: `search-bench`, `search-rebuild`,
    `search-check`, `search-triggers` (Z1) en `geo-load` (Z3).
  - Test: `tests/test_cli_db.py` (17 tests), met een kopie terwijl een `BEGIN IMMEDIATE` nog open
    staat.
- **`app/routers/files.py`**: `add_ref` meldt nieuwe inhoud ná de commit bij
  `search.hooks.blob_added(sha, owner)`. Het gaat via `session.info` en een `after_commit` op de
  sessie (hetzelfde patroon als `jobs._KICK`), dus een rollback meldt niets, en een fout in een
  haak komt in het log zonder de upload te stoppen.
- **`app/routers/files.py` en `app/routers/batch.py`**: `copy` roept
  `search.hooks.photo_copy(session, pairs)` met `(bron, kopie)` per gekopieerd bestand, nog vóór
  de commit en in dezelfde transactie (Z6 laat personen meegaan). Een map levert geen paar op.
- **`app/search/purge.py`**: `delete_nodes(session, ids)` is de ene plek waar nodes definitief
  weggaan; `routers/trash.py` (losse node, legen, `purge_expired`) en `userdelete.py` gaan er
  allebei door, met de kinderen vóór hun ouders, omdat de FK op CASCADE staat en een trigger de
  kinderen anders niet ziet.
- **`app/previews.py`**: `register_ready(fn)`; na `preview.ready` komt wie zich aanmeldde aan de
  beurt, met `(maker, sha, dated)`. Een fout komt in het log en de volgende gaat gewoon door.
- **`app/render.py`**: de ingangen `text` en `ocr_page` (`run_text`, `run_ocr_page`). Ze geven
  `state: "none"` tot Z2 en X1 ze vullen; `ocr_page` geeft `code: "ocr_missing"`.

### De tests

`api/tests/search/` (72 tests, plus de 17 van `tests/test_cli_db.py`):

- `test_migration_0010.py` — 0009 met data erin, upgrade, downgrade, upgrade; met en zonder FTS5;
  de kolommen, de indexen, de triggers, het vullen achteraf, en een paar keer hetzelfde doen.
- `test_schema_triggers.py` — de drie kopieën van de triggers zijn gelijk, en elke trigger staat
  op een tabel die bestaat.
- `test_ftype.py` — `FTYPE_SQL` en `mime.ftype()` zeggen hetzelfde, voor elk type.
- `test_own_live.py` — eigen, levend, geen map en geen album: wat de prullenbak doet (ook via een
  map erboven), wat een andere eigenaar doet, en dat `own_live` en `OWN_LIVE_SQL` hetzelfde
  antwoord geven.
- `test_contracts.py` — de haken (aanmelden, niets zonder aanmelding, een fout die het log in
  gaat), `select_engine` in elk geval, `write_text`, de backfill in `lifespan`, en dat de vormen
  van §3.3 er zijn met de velden van het ontwerp.
- `test_api_stubs.py` — alle routes van §3.4: 401, 403 en 501.
- `test_hotspots.py` — de plekken uit §3.5.
- `test_log_hiding.py` — de query string van `/search`, `/photos` en `/people` staat niet in het
  log.
- `migratie.py` — het hulpstuk dat 0009 met data vult en op en neer migreert.

## Afwijkingen van het ontwerp

1. **De migraties staan in `api/migrations/versions/`**, niet in `api/alembic/versions/` zoals
   §3.1 schrijft. De migratie heet `0010_zoeken.py` met `down_revision = "0009"`, zoals het
   bouwontwerp na de merge van delen en foto's zegt.
2. **`node_ai` en `node_au_move` gebruiken `coalesce(..., '/')`** voor het pad van een node
   zonder ouder: een hoofdmap heeft geen `parent_id`, en zonder `coalesce` werd `mpath` dan NULL
   en viel die node uit `ix_node_mpath`. Het ontwerp schrijft alleen het pad van de ouder.
3. **`album_rule.refreshed_at` is NOT NULL** met een standaard, niet nullable: een regel die nog
   nooit gedraaid heeft, heeft het moment van aanmaken, zodat sorteren op dat veld altijd werkt.
4. **`dup_pair`** heeft FK's naar `blob.sha256` met `ON DELETE CASCADE` (een opgeruimde blob
   neemt zijn paren mee) en een `CHECK (a < b)`, zodat hetzelfde paar niet twee keer kan staan.
   De tabel heeft geen rowid.
5. **De PostgreSQL-tak is niet getest.** 0010 maakt op PostgreSQL de tabellen en de kolommen,
   maar geen view, geen FTS, geen triggers en geen vulling: `num` en `mpath` blijven daar leeg, en
   `select_engine` geeft `LikeFallback`. Er is geen PostgreSQL in CI, dus dat staat alleen in de
   code.
6. **`Hit.path` blijft `list[PathPart]`** zoals in 0.7.0 (de mappen erboven), niet de string die
   §3.4 noemt. De web-app van nu leest dat veld, en Z0 wil die niet breken.
7. **`GET /search` houdt de parameters van 0.7.0.** Er is nog geen 400 `search_bad_filter`, de
   `limit` gaat tot 200, `type` en `tag` zijn enkelvoudig, en `archive` is een geldige soort.
   Z1 breidt ze uit; nu zou een strengere controle het zoeken van 0.7.1 kapot maken.
8. **`not_built` staat in `web/src/i18n/errors/common.ts`**, niet in een catalogus van zoeken:
   de code komt van elke stubroute, ook van een route die straks bij foto's of OCR hoort.
   En **`ocr_missing` staat in `admin.ts`**: §3.4 noemt het een code in `analysis.code` en geen
   HTTP-fout, maar `test_error_catalog.py` ziet elke `"code": "..."` in `app/` en wil er een
   Engelse tekst bij. Hij hoort bij `/beheer`, dus staat hij daar. De andere codes van
   `analysis.code` komen van Z2 en X1.
9. **Drie routes staan in een ander bestand dan §3.4 suggereert**: de analyse van een document in
   `routers/pages_words.py`, de regel van een slim album in `routers/albums_rule.py` en de
   OCR-aanvraag in `routers/ocr.py`. Zo blijft elk bestand bij één pakket.
10. **De velden van `AdminSearch`** (de beheerpagina van zoeken) zijn een voorstel: het ontwerp
    noemt ze niet allemaal. Z1 en Z7 mogen ze veranderen zolang de web-app meeverandert.
11. **`StepState` gebruikt `"queued"`** voor wat in de tabel `analysis` `wait` of `busy` is: wie
    het ziet hoeft dat verschil niet te weten.
12. **`write_text()` commit zelf.** Het is de enige schrijver van `blob_text` en moet de rij en
    de `analysis`-stap samen vastzetten; de aanroeper (een job) heeft geen eigen transactie nodig.
13. **De rij `index` draait altijd**, ook zonder media, met één worker. Zonder mediaworker leest
    de app tekst in haar eigen proces, en dat mag niet onbeperkt tegelijk.
14. **De naam van een reservekopie heeft milliseconden**
    (`platform-<head>-20261103T142501.337Z.db`): twee kopieën in dezelfde seconde moeten op naam
    te sorteren zijn. §3.10 noemt alleen `<tijd>`. Het patroon staat ook een naam zonder
    milliseconden toe, zodat "per revisie de nieuwste houden" straks ook over de kopie van
    `install.sh` werkt (Z7).
15. **De purge-helper staat in `app/search/purge.py`** en doet alles nog in één transactie, met
    IN-lijsten van 500 ids (de parametergrens van SQLite). Keuze 11 vraagt stukken van 2.000 met
    een commit ertussen; die zitten als `CHUNK` al in het bestand, maar Z1 bouwt ze, want zonder
    index is er nog geen reden om de transactie op te breken. Na de review (haalbaar F3) niet
    meer zo: de aanroepers maken de stukken al (`trash.purge_chunks`), zie `z0.md`, "De review".
16. **`register_ready(fn)` krijgt `(maker, sha, dated)`** — de sessionmaker, de inhoud en of de
    datum veranderd is — niet alleen de sha: een haak die iets in de database wil doen, heeft die
    maker nodig, en `announce` heeft er geen sessie.
17. **De ingangen van `render.py` heten `run_text` en `run_ocr_page`**, want `text` was al een
    functie in dat bestand (de tekst van een PDF-pagina voor de viewer). De modus in het verzoek
    heet wel `text` en `ocr_page`, zoals §3.5 zegt.
18. **`db-downgrade --ja` haalt de niet-gedeelde regel-items weg zonder te vragen.** De standaard
    op de vraag is ja, en `--ja` betekent "geen vragen", dus dat is hetzelfde antwoord.

## Open

Van Z0 zelf:

1. ~~**De spike S1–S11** (§3.7) en wat die aan het ontwerp verandert staan nog niet in
   `docs/fase-6/spike.md`. Die is van een andere werker.~~ Bij de samenvoeging van Z0 gekomen:
   [spike.md](spike.md), en de notities "Herzien na de spike" in het bouwontwerp.
2. ~~**Het harnas, de fixtures, de wereld en de referentie** (§3.6) en **`e2e/slim.sh`** zijn er
   nog niet, en dus ook geen e2e-test van fase 6.~~ Bij de samenvoeging gekomen:
   [z0-e2e.md](z0-e2e.md).
3. ~~**De CI-jobs van §4.6** bestaan nog niet.~~ Bij de samenvoeging: `zoek-perf` en de stappen
   S5, S9 en S11 in `media-image` zijn er (spike.md); de job `ocr` niet ([z0.md](z0.md), Open).
4. **`install.sh` maakt nog geen reservekopie** vóór een update; `db-backup` is er wel. Z7.
5. **De PostgreSQL-tak** van 0010 en van `select_engine` is niet getest (afwijking 5).

Vragen voor Jonas (de standaard waar Z0 op bouwt staat erbij):

6. **Tesseract in het image?** Nu niet, dus `ocr_enabled` staat uit en de rij `ocr` draait niet.
   Een nieuw hulpprogramma in het image is een vraag voor Jonas.
7. **GeoNames en Natural Earth** haalt `install.sh` bij de installatie (Z7); `places_db` wijst
   naar `/data/geo/places.sqlite`. Ontbreekt dat bestand, dan zijn er geen plaatsnamen. Beide
   bronnen zijn van buiten en kunnen hier in de sandbox niet gedownload worden
   (`download.geonames.org` is dicht), dus Z3 en Z7 moeten dat op een echte machine doen.

Bekend en niet van Z0:

8. `tests/test_media_names.py::test_fuzz_10000_namen_met_vaste_seed` meet tijd en faalt alleen op
   deze VM; hij staat ongewijzigd en is in CI groen.
