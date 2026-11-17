> Werkdocument: het bouwontwerp van fase 6 (zoeken en foto's), revisie 1.
> Nog niet besloten. De vragen aan Jonas staan onder "Vragen voor Jonas". Tot hij antwoordt,
> bouwen we op de standaard die daar staat.

# Fase 6 (zoeken en foto's): bouwontwerp, revisie 1

Basis: branch `claude/stappenplan-uitwerken-lu4uv3` @ `1130e1b` (0.7.0, migraties 0001–0008).
De app-code is gelijk aan `92a0d04`, waar CI op alle 14 jobs groen was; de commits daarna
veranderen alleen e2e, CI en documenten.

Revisie 1 voegt twee ontwerpen samen. Ontwerp A ("zelf bouwen, geen nieuwe afhankelijkheden,
eerlijk over wat er zonder Jonas niet lukt") is de basis voor de vorm: de pakketten, de vragen,
de uitrol en het terugrollen. Ontwerp B ("de hele fase, goed afgeschermd") is de basis voor de
zoekmachine zelf: het schema, de triggers, het toegangsfilter en de tests. Waar ze elkaar
tegenspraken, heb ik het nagemeten of in de code nagekeken (J1–J14 hieronder).

Bronnen (scratch, niet in het repo):

- `fase6/tech.md`: onderzoek naar FTS5, OCR, EXIF, GPS, plaatsnamen, kaarten, gezichten en
  beeldmodellen, met licenties en een bron per bewering;
- `fase6/feasibility.md`: de proef in de sandbox (H1–H8): zoeken over 100.000 bestanden, tekst,
  OCR, foto's, plaatsen, ML en wat de LXC en CI nodig hebben;
- `fase6/code-map.md`: waar fase 6 in de code inhaakt, met regelnummers;
- `fase6/judging.md`: het oordeel over A en B, per onderdeel en per lens;
- `fase6/design-a/` (D1–D6) en `fase6/design-b/` (de proef van B): de scripts en de uitkomsten.

Bij een bewering staat waar hij vandaan komt:

- **[gemeten]**: gedraaid in de sandbox;
- **[doc]**: officiële documentatie, een licentie of broncode;
- **[code]**: zo staat het in het repo;
- **[niet getest]**: niet bevestigd. Daar ontwerpen we voorzichtig. De spike (§3.7) of een test
  (§4) beslist.

Afkortingen:

- **app**: ons platform (API en web-app in één container);
- **worker**: de mediaworker van fase 5 (uid 10002, geen netwerk, bestanden via een fd);
- **FTS**: de full-text-index van SQLite (FTS5);
- **index**: `fts_node` en `fts_name` samen;
- **backfill**: het achteraf vullen van de index en de fototabellen voor bestanden die er al zijn;
- **wortel**: een map waar een gebruiker toegang toe heeft: zijn eigen hoofdmap, of een gedeelde
  map;
- **pad** (`mpath`): de nummers van alle ouders van een node, zoals `/1/7/42/`;
- **pHash**: een perceptuele hash van 64 bits van een foto;
- **X-pakket**: een pakket dat op een antwoord van Jonas wacht.

**Nagemeten en nagekeken bij deze revisie:**

- **J1. ß en het vouwen van de vraag** [gemeten, SQLite 3.45.1]. De tokenizer `unicode61
  remove_diacritics 2` maakt van "Straße" het token `straße`. Onze `fold()` (`app/text.py`) maakt
  er `strasse` van. Een vraag in de vorm van de tekst vindt het document; de gevouwen vraag niet.
  "Één", "café", "İstanbul", "ﬁets" en "ĳsje" werken in beide vormen. Daarom zoekt elk woord in
  twee vormen als die verschillen (Keuze 5).
- **J2. Brede woorden schrappen is fout.** In het zaad van de proef staat "factuur" in 58% van de
  documenten. Een regel "schrap woorden boven 25%" maakt van "factuur kpn" alleen "kpn". Dat
  verandert de vraag, en het hangt af van de bestanden van anderen. We schrappen niets.
- **J3. Het installatiecommando** gebruikt de branch, niet `main`: 0.7.0 staat nog niet op `main`
  (`infra/single/README.md`) [code]. Het script zelf valt zonder `PLATFORM_BRANCH` terug op
  `main` (`install.sh:21`), dus die variabele hoort in het commando (§3.10).
- **J4. B20 belooft een tekstlaag in de PDF-viewer in fase 6** (`docs/fase-0/beslissingen.md`).
  Die zit in dit ontwerp (Keuze 18).
- **J5. Terugrollen** gaat naar de laatste commit van 0.7.0 vóór Z0 (nu `1130e1b`, app-code gelijk
  aan `92a0d04`). Bij de integratie komt daar een tag `v0.7.0` op.
- **J6. Het voorbeeld van 256 px is al gedraaid volgens EXIF** (`render.py:357` en `:370`)
  [code]. De pHash daarop heeft geen eigen draaiing nodig.
- **J7. GPS uit downloads halen** hoort bij de bewerking "metadata en GPS-locatie weghalen" van
  fase 10 (stappenplan). In 0.7.0 gaat de originele foto al met GPS mee bij een download. Fase 6
  verandert daar niets aan, en waarschuwt (Keuze 30, Vraag 4).
- **J8. Geen nieuwe afhankelijkheid zonder Jonas.** B19 is besloten: niets nieuws in
  `api/pyproject.toml` of `web/package.json`. Tesseract, onnxruntime, numpy en de modellen zijn
  daarom vragen. Wat erop wacht, staat achteraan (§5).
- **J9. De toets `/`** opent op Bestanden al het naamfilter (fase 3). Die blijft. Het globale
  zoeken krijgt Ctrl/Cmd+K en een veld in de bovenbalk.
- **J10. Een slim album mag de foto's die je er met de hand in zette niet weggooien.** Daarom
  `album_item.source` en `album_skip` (Keuze 42).
- **J11. Toegang via het pad, niet via een lijst van mappen.** Een lijst van levende mappen per
  verzoek groeit met het aantal mappen; bij een eigenaar met alles zichtbaar (5.741 mappen) was
  dat p95 70 ms, max 113 ms. Het pad groeit met het aantal wortels: p95 45–54 ms tot 20 wortels,
  en 80 ms voor "alles", met snippets [gemeten, SQLite 3.46.1].
- **J12. De volgorde van de join ligt vast.** Met een gewone JOIN kan de planner na `ANALYZE`
  vanaf `node` beginnen en `MATCH` per rij doen. In de proef kostte dat 107 s. Dus `CROSS JOIN`
  en plan-tests met en zonder statistieken (Keuze 4).
- **J13. Grote wissen in stukken.** Een gebruiker met 10.911 nodes wissen kostte met de triggers
  3,06 s in één transactie [gemeten]. De schrijver mag niet zo lang vastzitten (`busy_timeout`
  5 s). Definitief wissen gaat daarom per 2.000 nodes met een commit (Keuze 11).
- **J14. Foutcodes** volgen de code: Engels, snake_case, met het gebied ervoor (`media_off`,
  `rate_limited`) [code].

**Uit de proef, en nog steeds de basis** (`feasibility.md`):

- H2: zoeken nu (`LIKE` in een recursieve query) kost 590–670 ms bij 101.000 nodes. FTS5 met een
  pad haalt het: zie J11. Een trigram-index op namen: 2,3 ms. Een prefix-index (`prefix='3'`)
  maakt de traagste soort (`ver*`) 59 → 28 ms en kost 62 MB.
- H2: SQLite in het image is 3.46.1 (Debian 13), op de ontwikkelmachine 3.53.1. `contentless`
  tabellen geven geen snippets; `contentless_unindexed` en `locale` (3.47) kunnen niet.
- H3: tekst uit een PDF-pagina met pypdfium2: 2,2 ms. Office-bestanden met zip en XML uit de
  standaardbibliotheek, met grenzen tegen zip-bommen en XML-aanvallen.
- H4: tesseract kost 2,1–3,3 s per A4-pagina op één core; 7° scheef breekt de herkenning; met
  threads kost het 2× CPU voor dezelfde tijd.
- H5: EXIF en GPS lezen: 0,24 ms per foto. pHash op het voorbeeld: 0,92 ms; zuiverheid
  0,998–1,0; zoeken met 4 × 16 bits: 0,6 ms.
- H6: GeoNames cities1000 in een R*Tree: 15 MB, 0,057 ms per foto.
- H7: YuNet (MIT) en SFace (Apache-2.0) via onnxruntime: 31 ms per foto en 29 ms per gezicht.
  InsightFace mag niet (gewichten alleen voor niet-commercieel onderzoek). CLIP is Engelstalig.
- H8: de backfill van 100.000 bestanden zonder OCR kost 2–3,5 core-uur.

---

## 0. Uitgangspunten

### 0.1 Wat er sinds het stappenplan veranderd is

Het stappenplan zegt voor fase 6: full-text met OCR, EXIF en GPS met een kaart en offline
plaatsnamen, duplicaten, "gezichten en objecten met lokale modellen (bijv. InsightFace + CLIP);
semantisch zoeken via pgvector", herinneringen en slimme albums, en filters. Klaar als: een
zoekopdracht over 100.000 bestanden antwoordt binnen 200 ms; OCR-tekst is doorzoekbaar;
gezichten zijn te benoemen en te zoeken.

| Wat | Gevolg voor fase 6 |
|---|---|
| B14 en B19: zelf bouwen, en geen nieuwe afhankelijkheid in `pyproject.toml` of `package.json`. | Tesseract, onnxruntime, numpy, modellen en datasets zijn elk een vraag aan Jonas, met een standaard waarmee de bouw doorgaat. |
| B15: standalone draait op SQLite. | pgvector bestaat hier niet. Vectoren komen pas met X2 en dan als BLOB in SQLite. pgvector pas met het cluster. |
| B17: zoeken in de eigen database, geen aparte zoekdienst. | FTS5 zit in SQLite. Dat past. |
| B20: de PDF-viewer krijgt in fase 6 een tekstlaag. | De tekst uit een PDF bewaren we per pagina met de plek van elk woord (Keuze 18). |
| B22: GPS lezen we in fase 3 niet, "dat hoort bij fase 6". | GPS wordt nu bewust weggegooid (`render_meta.py:21–22`, `media/worker/ops/meta.py:32`, `previews.SECRET_META:69`, `photodate.split_capture`) [code]. Het gaat open, maar alleen naar een eigen tabel, nooit naar `preview_meta`. |
| InsightFace: gewichten alleen voor niet-commercieel onderzoek [doc]. | Valt af. YuNet en SFace kunnen wel (X2). |
| CLIP is Engelstalig; SigLIP 2 is meertalig en Apache-2.0, maar groot (±200–400 MB) [doc]. | Objecten en semantisch zoeken komen niet in fase 6 (Vraag 3). |
| Zoeken nu: alleen op naam, 590–670 ms bij 101.000 nodes [gemeten]. | Haalt de 200 ms niet. Er komt een index. |
| SQLite in het image is 3.46.1 [gemeten]. | Elke meting en elke plan-test draait in het image. |
| Op de LXC draaien al OnlyOffice (≤ 2 GB) en de mediaworker (≤ 3 GB, `cpus: 3.0`) (B43, B47). | Indexeren wijkt voor transcodes. Eén proces, laagste voorrang. |
| CPU en GPU van Jonas zijn onbekend (B5). | Alles werkt op de CPU. Een tijdsgrens per vraag beschermt de 200 ms (Keuze 9). `bench --los` meet zijn LXC in week 1. |
| Jonas gaf het startsein voor fase 6 (3 november 2026) zonder de kaarten van fase 5 te beantwoorden. | De standaarden van fase 5 blijven. Fase 6 verandert er niets aan. |

### 0.2 De invalshoek

| | Principe | Wat het hier betekent |
|---|---|---|
| P1 | **Toegang eerst** | Een treffer verlaat de server pas na het toegangsfilter én een tweede controle per treffer. Een snippet maken we pas daarna. Zoeken kan alleen met een account; een publieke link zoekt nooit. |
| P2 | **Zelf bouwen met wat er is** | SQLite (FTS5, R*Tree), Pillow, pillow-heif, pypdfium2, mutagen, ffmpeg in de worker, de standaardbibliotheek. Eigen code voor de rest. |
| P3 | **Elk extern ding is een vraag** | Met een standaard waarmee de bouw doorgaat. Wat er zonder niet kan, staat in X-pakketten, achteraan. |
| P4 | **Eerlijk meten** | De 200 ms meten we via HTTP, in `python:3.12-slim` (SQLite 3.46.1), met het toegangsfilter, voor zes soorten gebruikers, als p95 én max. In CI en op de LXC van Jonas. |
| P5 | **De update breekt niets** | 0009 voegt kolommen, tabellen en triggers toe. Zoeken op naam werkt meteen; de inhoud vult zich. Een schakelaar zet het oude zoeken terug. Vóór de migratie een reservekopie. Terug naar 0.7.0 is getest. |
| P6 | **Privacy standaard dicht** | GPS en personen staan nooit in `preview_meta`, gaan nooit via een publieke link, en zijn alleen voor de eigenaar. Geen totalen of scores naar buiten. De zoekvraag komt in geen enkele log. |
| P7 | **Achtergrondwerk wijkt** | Eén rij `index`, `nice` 19 en de idle IO-klasse, korte transacties, en wachten als er ≥ 2 transcodes lopen of OnlyOffice start. |

### 0.3 Hoe een bestand vindbaar wordt, en hoe een vraag loopt

```
upload / kopie / hernoemen / verplaatsen / prullenbak / versie / import
        │
        ▼
  node (rij) ──triggers──► num, mpath, dead ──► fts_node (naam) + fts_name (trigram)
        │                                        direct, in dezelfde transactie
        │ blob_id (sha)
        ▼
  files.add_ref ──hook──► analysis(sha, 'text') ──► job index.text (rij "index")
                              render-subproces (python -I, RLIMIT_AS, nice 19, ionice idle)
                              pypdfium2 / zip+XML / tekst / mutagen
                              ▼
                         blob_text (per sha) ──triggers──► fts_node (inhoud) van elke node met die sha
                              │
                              └─► (X1) analysis(sha, 'ocr') ──► worker op `ocr` (tesseract)
                                        ──► blob_text.ocr + ocr_page (woorden met hun plek)

  tekstlaag in de viewer: GET /files/{id}/pages/{n}/text
        PDF met tekst: op aanvraag uit de PDF (render-subproces); scan: uit ocr_page

  photometa v2 (render_meta.VERSION 2) ──► blob_geo (per sha) ──► plaats uit places.sqlite
                                       └─► blob_meta (camera, lens)
  voorbeeld 256 px klaar ──hook──► analysis(sha, 'hash') ──► blob_hash ──► dup_pair

zoeken: GET /api/v1/search
  1. de wortels van de gebruiker (eigen hoofdmap + delingen), met hun paden    (≤ 1 ms)
  2. fts_node MATCH <vraag>  CROSS JOIN node  WHERE kind <> 'album' AND dead = 0
       AND (mpath in een van de wortels)  ORDER BY bm25, num  LIMIT offset + n + 1
  3. als er minder dan een pagina is: fts_name (trigram) voor stukken van namen
  4. tweede controle per treffer: access.resolve()                              (P1)
  5. pas dan: de snippets, in Python, uit de opgeslagen tekst
  6. de groepen "personen" en "plaatsen" van de gebruiker zelf
```

### 0.4 Wat "klaar als" in dit ontwerp betekent

| Klaar als (stappenplan) | Kern (Z0–Z7, zonder vragen) | Wat er zonder Jonas niet lukt |
|---|---|---|
| Een zoekopdracht over 100.000 bestanden antwoordt binnen 200 ms. | Ja. Een harde poort in CI via HTTP in `python:3.12-slim` (§4.4), en `bench --los` op de LXC. | Niets. Op de CPU van Jonas is het pas bewezen als `bench --los` daar slaagt. |
| OCR-tekst is doorzoekbaar. | Tekst uit PDF's met een tekstlaag, Office, tekstbestanden en (met OnlyOffice) oude .doc, .xls en .ppt is doorzoekbaar. Dat is geen OCR. | **OCR zelf.** Zonder tesseract (Vraag 1) is een scan alleen op naam te vinden. X1 is gebouwd en in CI getest met een echte tesseract; het image krijgt hem pas na "ja". |
| Gezichten zijn te benoemen en te zoeken. | Personen benoemen op een foto (met de hand, met of zonder kader), samenvoegen, verbergen, zoeken en filteren. | **Gezichten vinden.** Automatisch vinden vraagt numpy, onnxruntime en twee modellen (Vraag 2). Dat is X2. Zonder X2 is dit punt half gehaald. Zo staat het op `/beheer` en in de README. |

---

## 1. Keuzes

Elke keuze zegt wat, waarom, en waar het bewijs zit. "(A)" of "(B)" zegt uit welk ontwerp hij
komt; "(nieuw)" is van deze revisie.

### De zoekindex

1. **Eén index per node, in SQLite FTS5** (B). `fts_node` heeft vijf kolommen: `name` (de
   gevouwen naam, `node.search_name`), `extra` (de titel uit de mediabibliotheek), `body` (de
   tekst), `ocr` en `meta` (camera, en titel en auteur uit PDF en Office). Hij leest uit de view
   `search_src` (external content, `content_rowid='num'`), tokenizer `unicode61
   remove_diacritics 2`. Daarnaast `fts_name` met `trigram` op de naam, voor stukken van namen
   ("port" in "rapport").
   - Waarom niet tekst per blob en namen per node apart (A): één bm25 over alle kolommen is
     eenvoudiger en juister te rangschikken. Een kopie staat dan twee keer in de index. In het
     zaad is dat 2% van de bestanden: ruimte, geen tijd.
   - Ruimte per 100.000 bestanden (20.000 met tekst): index ±120 MB, opgeslagen tekst ±257 MB,
     naamindexen ±15 MB [gemeten, H8].
   - Albums staan niet in de index (`kind = 'album'`). Mappen wel: je vindt een map op naam.
2. **Een vast nummer, een pad en `dead` op `node`** (B). `num` komt uit `search_seq` en is de
   rowid in FTS. De rowid van `node` zelf kan na een `VACUUM` verschuiven; `num` niet. `mpath` is
   het pad van nummers (`/1/7/42/`). `dead` is 1 als de node of een ouder in de prullenbak ligt.
   Alle drie komen er met `ALTER TABLE … ADD COLUMN`, zonder de tabel te kopiëren. De migratie van
   104.696 nodes kost 2,8 s [gemeten, SQLite 3.46.1].
3. **De triggers zijn de enige weg naar de index** (B). Ze staan in §3.1 en letterlijk in
   `app/search/schema.py`. Regels, elk met een wachter-test (§4.1):
   - de triggers in `sqlite_master` zijn gelijk aan `schema.TRIGGERS`. Een latere migratie die
     `node` opnieuw maakt (`batch_alter_table` kopieert de tabel en laat triggers vallen), faalt
     dan meteen;
   - nooit `INSERT OR REPLACE` of `.merge()` op `node` of `blob_text`: een REPLACE start geen
     delete-trigger, en een external-content-index wil bij `delete` precies de oude waarden;
   - de FK-cascade start de delete-triggers wel [gemeten]. Definitief wissen hoeft dus niets
     extra's te doen.
   - De churn-test (1.000 willekeurige wijzigingen, daarna vergelijken met een index die opnieuw
     is opgebouwd) gaf 0 verschillen en `integrity-check` ok [gemeten]. Die test draait in CI op
     3.53.1 én 3.46.1 (§4.3).
4. **De vraag in SQL ligt vast** (B, met `CROSS JOIN` van deze revisie, J12):

   ```sql
   SELECT n.id, bm25(fts_node, 10.0, 6.0, 1.0, 0.8, 0.5) AS s
   FROM fts_node CROSS JOIN node AS n ON n.num = fts_node.rowid
   WHERE fts_node MATCH :expr
     AND n.kind <> 'album' AND n.dead = 0
     AND ((n.mpath >= :p1 AND n.mpath < :e1) OR (n.mpath >= :p2 AND n.mpath < :e2) …)
     AND <filters uit het register (Keuze 43)>
   ORDER BY s, n.num
   LIMIT :offset + :limit + 1
   ```

   `:e` is het pad met de laatste `/` vervangen door `0` (het eerste teken na `/`). `CROSS JOIN`
   dwingt SQLite om bij FTS te beginnen. Plan-tests (A, `assert_plan`) eisen dat, met en zonder
   `ANALYZE`, en verbieden `AUTOMATIC` en een scan op `parent_id`.
5. **De vraag ontleden** (B, met twee vormen van deze revisie, J1). `app/search/query.py`,
   `parse()`:
   - hooguit 200 tekens en 10 woorden, anders `400 search_query_too_long`;
   - NFKC, dan splitsen op alles wat geen letter of cijfer is. Elk woord tussen aanhalingstekens.
     Wat de gebruiker typt, is nooit een FTS-operator: `AND`, `NEAR`, `name:` en `*` zijn gewone
     tekens of woorden;
   - per woord twee vormen: de tekst-vorm (NFKC, kleine letters) en de naam-vorm (`fold()`).
     Verschillen ze (ß, sommige ligaturen), dan `("straße" OR "strasse")`;
   - het laatste woord is een voorvoegsel (`"rapp"*`) als het ≥ 3 tekens heeft en de vraag niet
     op een spatie eindigt. Kortere woorden alleen als heel woord;
   - een stuk tussen aanhalingstekens in de vraag is een frase;
   - er wordt niets geschrapt (J2). Een vraag die alleen uit woorden van 1–2 tekens bestaat,
     zoekt die als heel woord, plus in de namen via trigram met een `LIKE`-filter.
   - `parse()` is de enige plek die een `MATCH`-uitdrukking maakt. Een AST-test verbiedt een
     f-string of `%` naast `MATCH` (B).
6. **Rangorde** (B). bm25 met gewichten naam 10, titel 6, tekst 1, OCR 0,8, meta 0,5. Bij gelijke
   score op `num`, zodat de volgorde vast is. Stukken van namen uit `fts_name` komen ná de
   treffers op hele woorden, en alleen als de eerste ronde minder dan een pagina gaf.
7. **Pagina's zonder totaal en zonder score** (B). `offset` 0–1.000, `limit` 1–50 (standaard 30),
   en `more`. bm25 telt over de bestanden van iedereen; een totaal of een score zou iets zeggen
   over wat anderen hebben. De volgorde zelf houdt een klein restrisico (§5.1, R7).
8. **Snippets in Python, na de toegangscontrole** (B, met grenzen van deze revisie). FTS5
   `snippet()` is met een voorvoegsel traag (B: 658 ms). `app/search/snippets.py`:
   - alleen voor de treffers op de pagina, en pas na Keuze 15;
   - uit `blob_text.body`, anders `ocr`, anders `meta`; hooguit de eerste 200.000 tekens;
   - de tekst in woorden, elk woord gevouwen, vergeleken met de gevouwen vraag (dus ß ↔ ss klopt
     ook hier); het eerste stuk van ±160 tekens rond de eerste treffer;
   - als stukken: `[{"t": "…de ", "m": false}, {"t": "factuur", "m": true}, …]`. De web zet ze als
     tekst in de pagina, nooit als HTML;
   - hooguit 40 ms per verzoek voor alle snippets samen; daarna `snippet: null` (de naam blijft).
   - Gemeten: 2–18 ms per pagina [gemeten, B]. Met tekst van 1 MB is het [niet getest]; de
     benchmark heeft een profiel met grote documenten (§4.4).
9. **Een tijdsgrens per vraag** (A). `PLATFORM_SEARCH_DEADLINE_MS` = 150, via de
   progress-handler van SQLite (elke 1.000 stappen). Is de tijd op, dan stopt de vraag en komt
   wat er al is terug met `partial: true`. De web zegt dan "Niet alles kon op tijd. Maak je vraag
   preciezer." Spike S1 bewijst dat het door aiosqlite heen werkt; lukt dat niet, dan
   `interrupt()` vanuit een timer-thread.
10. **Een terugval: het zoeken van 0.7.0** (A). `LikeFallback` is de code van nu
    (`routers/search.py:132–195`). Hij draait als FTS5 ontbreekt, op PostgreSQL (het latere
    cluster), met `PLATFORM_SEARCH_INDEX=uit`, en tijdens een herbouw. De triggers en FTS bestaan
    alleen op SQLite. De keuze valt bij de start (`select_engine`), en staat op `/beheer`.
11. **Grote wissen in stukken** (J13). Definitief wissen uit de prullenbak, `purge_expired` en het
    wissen van een gebruiker gaan per 2.000 nodes, met een commit ertussen (`userdelete.py` doet
    nu 500 per keer en blijft zo). Verplaatsen blijft één transactie: een map van 5.276 nodes
    kost 56 ms, naar de prullenbak 31 ms [gemeten].
12. **Herstellen en controleren.** `app.cli search-rebuild` (beide FTS-tabellen opnieuw, minuten),
    `search-check` (vergelijkt 1.000 willekeurige nodes met de bron) en `search-triggers` (zet de
    triggers terug zoals in `schema.py`). Een wekelijkse job draait `search-check` met 200 nodes;
    een verschil komt op `/beheer`. `integrity-check` van FTS5 alleen met de hand: hij leest
    alles.
13. **Logs, grenzen en tijden.** `q` komt in `SECRET_PARAMS` (`main.py:89`) en in geen enkele
    logregel. Per gebruiker hooguit 10 zoekvragen en 20 suggesties per seconde, anders
    `429 rate_limited` (de code bestaat al). Een `Server-Timing`-kop met de tijd per stap alleen
    met `PLATFORM_SEARCH_TIMING=1` (tests en metingen).

### Toegang en privacy bij zoeken

14. **Wortels en paden** (B). Bij elk verzoek: de eigen hoofdmap (niet voor een gast) plus
    `access.shared_roots()` (`access.py:240–262`, direct, via groep, als gast), elk met zijn
    `mpath`. Een wortel binnen een andere valt weg. Het filter is een `OR` van bereiken. Boven
    64 wortels gaan de bereiken in een tijdelijke tabel [niet getest; zeldzaam]. Een verlopen of
    ingetrokken deling telt in hetzelfde verzoek al niet meer.
15. **Een tweede controle per treffer** (A en B). Elke treffer gaat nog door `access.resolve()`
    vóór de snippet. Wijkt die af van het filter, dan valt de treffer weg en komt er één logregel
    `search_access_mismatch` met het node-id (geen vraag, geen naam). In de tests mag die regel
    nooit voorkomen: een fixture laat de test dan falen.
16. **Wat nooit via zoeken gaat.**
    - Een publieke link heeft geen zoekroute. De filter op namen in de browser blijft zoals nu.
    - Plaatsnamen en personen staan niet in `fts_node`. Anders vindt Bram een foto van Eva op
      "Utrecht", terwijl alleen Eva de plek mag zien. Zoeken op plaats en persoon gaat via de
      groepen en filters, en die werken alleen op eigen bestanden.
    - Albums zijn geen treffers. De suggesties tonen albums die je mag zien op naam.

### Tekst uit bestanden

17. **In het render-subproces van nu** (A). Een job `index.text` in de rij `index` start
    `python -I -m app.render text` met `RLIMIT_AS` 1 GiB, `RLIMIT_CPU` 120 s, `nice` 19 en de
    idle IO-klasse. De tekst komt terug via een pipe op fd 3. Geen nieuwe container.
    - **PDF**: pypdfium2 (zit er al, B20), hooguit 2.000 pagina's.
    - **docx, xlsx, pptx, odt, ods, odp**: `zipfile` en `xml.etree` uit de standaardbibliotheek.
      Grenzen: ≤ 2.000 onderdelen, elk ≤ 50 MB uitgepakt, samen ≤ 200 MB, verhouding ≤ 100:1.
      Een XML met `DOCTYPE` of `ENTITY` wordt geweigerd. Kop- en voetteksten, voetnoten,
      sprekersnotities en cellen tellen mee.
    - **txt, md, csv**: UTF-8, UTF-16 met BOM, anders cp1252. **html**: tags weg met
      `html.parser`. **rtf**: een eigen stripper.
    - **doc, xls, ppt en rtf** die OnlyOffice al naar PDF zet (fase 4): de tekst uit die PDF, via
      de haak na de omzetting.
    - **audio en video**: titel, artiest en album (mutagen, of de probe van de worker) in `meta`.
    - Hooguit 1.048.576 tekens per blob; daarna `truncated`. Bestanden boven 512 MB: alleen de
      naam (`skip`).
    - Eerlijk (R6): PDFium draait in de app-container, net als bij de voorbeelden van B20. Het
      subproces met grenzen is dezelfde bescherming als nu.
18. **De tekstlaag van B20** (B). `GET /api/v1/files/{id}/pages/{n}/text` geeft de woorden met hun
    plek (0–1) op die pagina.
    - Een PDF met tekst: op aanvraag uit de PDF, in het render-subproces. Niets opgeslagen. HTTP
      `ETag` op sha en pagina, `Cache-Control: private`.
    - Een scan (X1): uit `ocr_page`.
    - De viewer legt de woorden doorzichtig over de pagina: selecteren en kopiëren werkt, en de
      woorden van de zoekvraag (`?q=` in de URL van de viewer) zijn gemarkeerd. Dit zit in de
      lazy chunk van de PDF-viewer.
19. **Eén tabel voor het werk per blob** (B). `analysis(blob_sha, step, version, state, tries,
    code)`. `step` is `text`, `hash` of `ocr`; `state` is `wait`, `busy`, `done`, `none` (niets te
    halen), `skip` of `failed`. Een hogere `version` in de code zet de rijen terug op `wait`.
    Hooguit 3 pogingen met wachttijd; daarna `failed` met een code, zichtbaar op `/beheer` met de
    sha, nooit met de naam van andermans bestand.
20. **De backfill.** Bij de start zet de app per 500 de blobs zonder rij in `analysis`, de nieuwste
    eerst. Elke job schrijft één blob in een korte transactie, zodat de schrijver vrij blijft
    (WAL, één schrijver). Zolang hij loopt, geeft `/search` `indexing: {"done": 0.43}`. Poort:
    ≥ 1.500 documenten per minuut per core (§4.5).
21. **De tekst opschonen.** NFKC, witruimte samen, zachte afbreekstreepjes (U+00AD) en tekens zonder
    breedte weg. Geen andere taalregels (geen stemming): "facturen" vindt "factuur" niet, wel
    "factu" als voorvoegsel. Stemming voor het Nederlands kan later als eigen tokenizer
    [niet getest].

### OCR (X1, wacht op Vraag 1)

22. **Tesseract in de mediaworker** (A, met de tests van B). Een opdracht `ocr` in de worker
    (soort `background`), niet in de app:
    - de app geeft de worker een fd: voor een PDF een pagina als PNG van 300 dpi (gemaakt in het
      render-subproces), voor een foto de blob zelf;
    - de worker draait eerst een eigen deskew (Pillow, projectie over ±10° in stappen van 0,5°)
      en de OSD van tesseract voor 90°, 180° en 270° (7° scheef breekt de herkenning, H4);
    - dan `tesseract stdin stdout -l nld+eng --psm 3 tsv` met `OMP_THREAD_LIMIT=1`: één thread is per
      CPU-seconde het zuinigst (H4). De TSV komt via een pipe terug (de boodschap zelf is hooguit
      64 KB);
    - woorden met zekerheid ≥ 60 gaan naar `blob_text.ocr` en met hun plek naar `ocr_page`.
23. **Welke bestanden.** PDF-pagina's met minder dan 20 tekens tekst. Foto's alleen als de
    eigenaar "Tekst in foto's herkennen" aanzet (`photo_pref.ocr_photos`, standaard uit) of op
    "Tekst herkennen" drukt in de viewer. Hooguit 200 pagina's per document. Eén tegelijk, in de
    rij `ocr`, die alleen draait als media aan staat en OCR aan staat.
24. **Zonder tesseract.** De code van X1 kan in het repo staan zonder dat het image tesseract
    heeft. `caps` van de worker zegt dan `ocr: false`; de rijen blijven `none` met code
    `ocr_missing`, en worden weer `wait` zodra `caps` OCR meldt. `/beheer` zegt "OCR: niet
    geïnstalleerd (Vraag 1)".

### EXIF, GPS en plaatsen

25. **GPS lezen, apart bewaren** (A en B). `render_meta.VERSION` wordt 2 en leest de GPS-IFD
    (JPEG, HEIC, TIFF, PNG `eXIf`, WebP), met XMP als tweede bron. Video's: de worker-opdracht
    `meta` geeft ook `location` en ISO 6709 (`com.apple.quicktime.location.ISO6709`, `©xyz`)
    door. Alles gaat naar `blob_geo` (per sha), met `x16`/`y16` (de tegel op zoom 16) voor de
    kaart. Nooit in `preview_meta`: `SECRET_META` blijft, en een test doorloopt elk antwoord van
    `/viewer` en `/public` op `lat`, `lon` en `geo`. De backfill volgt het patroon van
    `photometa.py`.
26. **Camera en lens** in `blob_meta`, gevouwen als "merk model", voor het filter `camera`. De
    camera mag ook in de kolom `meta` van de index: dat is geen plek.
27. **Alleen de eigenaar ziet de plek** (A). `GET /files/{id}/location` geeft anderen `404`
    (geen verschil met "geen plek"). De kaart, de plaatsenlijst en het filter `place` werken
    alleen op eigen bestanden. Een publieke link nooit, ook niet in een gedeeld album.
28. **Een schakelaar per gebruiker** (B). `photo_pref.location`: `mij` (standaard) of `uit`. Bij
    `uit` wist een job de rijen in `blob_geo` voor blobs waar geen andere eigenaar met `mij` bij
    hoort, en de kaart is leeg. Weer `mij`: de backfill leest ze opnieuw.
29. **Plaatsnamen offline** (A en B, Vraag 5). GeoNames cities1000 (CC BY 4.0) in
    `/srv/geo/places.sqlite`: een R*Tree, alleen-lezen, gebouwd in een eigen stap van de
    Dockerfile door `tools/geo/build_places.py` (stdlib). De dichtstbijzijnde plaats binnen
    30 km, met provincie en land; verder weg alleen het land. 0,057 ms per foto [gemeten].
    - Bron: de vaste kopie `rg_cities1000.csv` uit de sdist van `reverse_geocoder` 1.5.1 op PyPI
      (sha256 bekend, H6). Het is data uit 2015; een nieuwere kopie kan later (Vraag 5).
    - Niet fataal: lukt het downloaden bij de bouw niet, dan draait alles zonder namen en zegt
      `/beheer` "Plaatsnamen: niet geladen". `PLAATSEN=uit` in `.env` slaat de stap over.
    - Bronvermelding op de kaart en op `/over`: "Plaatsnamen: GeoNames, CC BY 4.0".
    - Provincienamen zijn Engels ("North Holland"). Nederlandse namen vragen de alternateNames
      (groter, niet gemeten): later.
30. **Delen met een plek: waarschuwen, niet herschrijven** (A, J7, Vraag 4). Het deeldialoog zegt:
    "12 foto's hierin hebben een locatie in het bestand. Wie ze downloadt, krijgt die mee." De
    bytes blijven gelijk, zoals in 0.7.0. GPS uit downloads halen is de bewerking van fase 10.

### De kaart

31. **Een eigen kaart, zonder tegels** (A). Landsgrenzen uit Natural Earth 110m (publiek domein)
    als JSON in de web (`web/src/photos/map/world-110m.json`), gemaakt door
    `tools/kaart/maak_wereld.py` uit de GeoJSON van tag v5.1.2 (sha256). CI maakt hem opnieuw en
    vergelijkt. Doel ≤ 60 kB gzip (spike S6). Webmercator, getekend als SVG; slepen, knijpen en
    het toetsenbord (pijlen, `+`, `-`). Geen tegels van buiten, de CSP blijft gelijk
    (`img-src 'self' data: blob:`). Lazy route `/fotos/kaart`.
32. **Clusters rekent de server** (B). Op zoom `z` (0–14) is een cel
    `(x16 >> (14 − z), y16 >> (14 − z))`: vier cellen per tegelbreedte. Boven zoom 14 losse
    foto's. `GROUP BY` over de eigen
    foto's in het venster, ≤ 500 cellen, met een omslag (de nieuwste). Poort p95 ≤ 150 ms bij
    55.000 foto's met GPS (§4.4). Plaatsnamen op de kaart pas vanaf zoom 6.
33. **Een lijst naast de kaart** (nieuw). "Plaatsen": de plaatsen met het aantal foto's
    (`place_count`), voor wie geen kaart gebruikt of met een schermlezer werkt. Een klik zet het
    filter `place`.

### Duplicaten

34. **Precies gelijk** (A en B). Dezelfde `blob_id` bij twee of meer eigen, levende bestanden. De
    opslag is al per sha, dus een kopie kost geen ruimte; de lijst helpt bij opruimen.
35. **Bijna gelijk, bijgehouden per nieuwe foto** (B). Na `preview.ready` maakt het
    render-subproces een pHash van 64 bits (DCT van 32 × 32 naar 8 × 8) uit het voorbeeld van
    256 px (J6), in `blob_hash` met vier stukken van 16 bits (`h0`–`h3`). Kandidaten delen
    minstens één stuk; met Hamming-afstand ≤ 10 komt het paar in `dup_pair`. 0,92 ms per hash,
    0,6 ms per zoektocht [gemeten].
36. **Groepen op het moment van vragen.** `GET /duplicates?kind=near` bouwt groepen uit de paren
    van eigen foto's. Afstand ≤ 6 is "bijna zeker gelijk", 7–10 "lijkt erop". "Niet gelijk" zet
    de groep in `dup_ignore`. **Nooit vanzelf wissen**: opruimen gaat via de prullenbak, met de
    bestaande batchroute.

### Personen en gezichten

37. **Personen met de hand in de kern** (A). `person` per eigenaar; `photo_person` koppelt een
    persoon aan een blob, met of zonder kader (`x`, `y`, `w`, `h`, 0–1) en `source` (`hand`, later
    `auto`). Per blob, dus een kopie van dezelfde foto heeft de persoon ook. In het infopaneel van
    de viewer: "Persoon toevoegen" met suggesties uit je eigen personen; een kader tekenen met de
    muis of met het toetsenbord (pijlen verplaatsen, Shift+pijlen maken groter of kleiner).
38. **Zoeken op persoon.** "anna" geeft bovenaan de groep personen (eigen personen waarvan de naam
    zo begint); een klik zet het filter `person`. `/fotos/personen` toont je personen met aantal
    en omslag. Per persoon: hernoemen, samenvoegen, verbergen, wissen. Alleen de eigenaar ziet
    personen: wie een gedeelde foto bekijkt, ziet er geen (`[]`), en de API geeft voor een
    persoon van een ander `404`.
39. **Gezichten automatisch: X2, na "ja" op Vraag 2** (A, met de worker van B). YuNet (MIT) en
    SFace (Apache-2.0) via onnxruntime, in een eigen container `analyse` uit hetzelfde image met
    een optionele extra (`numpy`, `onnxruntime`): uid 10003, `network_mode: none`, `read_only`,
    `cpus: 1.0`, `mem_limit: 2g`, `cpu_shares: 64`. Modellen met sha256 in `models.lock`. Aan per
    gebruiker (standaard uit), de beheerder kan het verbieden. Een gevonden gezicht is een voorstel
    ("Is dit Anna?"); het wordt pas een persoon na bevestigen. Tabellen `face` en `face_person` in
    migratie 0010. InsightFace mag niet (licentie).

### Beeld: semantisch zoeken en objecten

40. **Niet in fase 6** (A, Vraag 3). SigLIP 2 (Apache-2.0, meertalig) kost ±200–400 MB en 0,9–1,9
    core-uur per 55.000 foto's. CLIP is Engelstalig. pgvector vraagt PostgreSQL (cluster). De
    filters en de API laten ruimte voor een filter `label` en een stand `mode=image` later.

### Herinneringen, slimme albums en filters

41. **Herinneringen op verzoek** (A en B). `GET /photos/memories?day=JJJJ-MM-DD` (standaard vandaag
    in `PLATFORM_TIMEZONE`): eigen foto's van deze dag in eerdere jaren. Per jaar één bereik op
    `ix_node_owner_taken` (`taken_day` tussen `J·10000 + MMDD` en hetzelfde), dus geen scan. Regels
    om te verbergen in `memory_hide` (`day`, `folder`, `person`). Uit te zetten per gebruiker
    (`photo_pref.memories`). Bovenaan `/fotos`: "Vandaag, 3 jaar geleden".
42. **Slimme albums houden je eigen keuze** (B, J10). Een album kan een regel krijgen
    (`album_rule`, JSON met dezelfde filters als zoeken). De verversing zet treffers erbij met
    `album_item.source = 'rule'` en haalt alleen `rule`-items weg die niet meer passen. Haalt de
    eigenaar een `rule`-item weg, dan komt het in `album_skip` en komt het niet terug. Items met
    de hand blijven altijd. Alleen eigen bestanden, hooguit 10.000. Verversen na een wijziging van
    de regel, elke nacht, en 60 s na een reeks uploads.
43. **Eén register voor filters** (B). `app/search/filters.py`: elk filter meldt zich aan met een
    functie die SQL toevoegt. Zo werken dezelfde filters op `/search`, `/photos` en de regels van
    slimme albums, en hoeven Z3 en Z6 niet in hetzelfde bestand te schrijven.

    | Filter | Waarop | Meer keer |
    |---|---|---|
    | `type` | `image`, `video`, `audio`, `document`, `pdf`, `spreadsheet`, `presentation`, `folder` (uit `mime`) | OF |
    | `from`, `to` | `taken_day` voor foto en video, anders `modified_at` | — |
    | `person` | eigen personen | EN |
    | `place`, `country` | eigen foto's met een plek | — |
    | `tag` | eigen tags | EN |
    | `library` | een mediabibliotheek | — |
    | `camera` | `blob_meta.camera` | — |
    | `in` | onder deze map (via `mpath`) | — |

44. **Instellingen per gebruiker** in `photo_pref`: `location` (`mij`/`uit`), `memories`,
    `ocr_photos` (X1), en `faces` (X2, staat klaar). `GET` en `PATCH /me/photo-settings`.

### De web

45. **De ingang** (A). Een zoekveld in de bovenbalk, hooguit 0,8 kB in de initiële JS (nu 96,4
    van 100 kB). Ctrl/Cmd+K zet de focus erop. Op Bestanden blijft `/` het naamfilter (J9).
    Enter opent `/zoeken?q=…`.
46. **Suggesties** (B). Een lazy chunk na de eerste toets (wacht 120 ms), een ARIA-combobox
    (`listbox`, `aria-activedescendant`), met groepen: namen, personen, plaatsen, tags en albums,
    elk ≤ 5. Esc sluit. Dit is het eerste wat valt als de begroting knelt.
47. **De pagina `/zoeken`** (A en B). Per treffer: icoon, naam, pad en snippet (gemarkeerde woorden
    als `<mark>`, via tekst). Chips voor de filters. "Meer laden" (offset). Meldingen: "De index
    is nog bezig (43%)" en "Niet alles kon op tijd". Pijlen en Enter; Enter opent de viewer met
    `?q=` voor de markering in de tekstlaag.
48. **Nieuwe lazy routes**: `/fotos/kaart`, `/fotos/personen`, `/fotos/personen/:id`, `/dubbel`.
    Elk met een begroting in `budget.mjs`, teksten in NL en EN, foutcodes in de catalogus
    (`web/src/i18n/errors/search.ts` nieuw, `photos.ts` erbij), en een toetsenbordlijst
    `docs/fase-6/toetsenbord-z*.md`.

### Werk en CPU

49. **Twee rijen** (A). `index` (tekst, hash, GPS-backfill) draait altijd, ook zonder media:
    `active_lanes` (`jobs.py:570–574`) zet hem erbij. `ocr` (X1) draait alleen als media aan staat
    en OCR aan staat. Elke rij één job tegelijk.
50. **Wijken** (B). `jobs.Defer` (fase 5) voor `index` en `ocr` als er ≥ 2 transcodes lopen of
    OnlyOffice start. De naamindex wacht nooit: die zit in de triggers.

### Beheer, uitrol, update en terug

51. **Een tab "Zoeken en foto's" op `/beheer`.** De motor (`fts` of `like`), de stand van de
    index (namen klaar; tekst n van m; mislukt met sha en code), tijdsgrens geraakt per uur, OCR
    (geïnstalleerd, aan, pagina's in de rij), plaatsnamen (geladen, bron, datum), de uitkomst van
    `bench --los`. Knoppen: index herbouwen, mislukte opnieuw, achtergrondwerk pauzeren.
52. **Een reservekopie vóór de migratie** (A). `install.sh` bouwt eerst het nieuwe image, dan
    `app.cli db-backup` (`VACUUM INTO` naar `/data/backup/`, de laatste 2 blijven), en stopt als
    dat niet lukt: er is dan nog niets veranderd. Daarna `up -d`, de migratie, de health-check,
    media en office, zoals nu.
53. **Terug naar 0.7.0 is beschreven en getest** (A, §3.10). Met de branch-URL (J3).
54. **Meten op de LXC** (A). `app.cli bench --los` maakt een boom van 100.000 in
    `/data/cache/zoekbench` (±2 minuten, ±500 MB), meet zoeken voor "eigenaar" en "alles", en
    wist hem weer. De uitkomst komt op `/beheer`. Al in week 1 kan Jonas de losse versie uit Z0
    draaien in zijn container van 0.7.0, zonder iets te installeren (§3.7, S9).

### Vragen voor Jonas

1. **Mag tesseract (Apache-2.0, ±20 MB met Nederlands en Engels) in het image, voor OCR?**
   Standaard: **nee, tot je ja zegt.** We bouwen X1 wel, en testen het in CI met een echte
   tesseract op de runner. Zonder ja is een scan alleen op naam te vinden. Met ja komt er één
   regel in de Dockerfile en staat OCR op `/beheer` aan voor PDF-scans (foto's per gebruiker).
   OCR kost 2–3 s per pagina op één core, alleen als de mediaworker rustig is.
2. **Mogen gezichten automatisch gevonden worden?** Dat vraagt numpy en onnxruntime (±120 MB, in
   een eigen container zonder netwerk) en twee modellen (YuNet 0,2 MB, SFace 39 MB). Standaard:
   **nee**. Personen benoem je met de hand, en dat is doorzoekbaar. Met ja: X2 (±5–6 dagen), per
   gebruiker aan te zetten, ±1 uur rekenen per 55.000 foto's.
3. **Zoeken op wat er op een foto staat ("strand", "hond")?** Dat vraagt SigLIP 2 (±200–400 MB) en
   ±1–2 uur rekenen per 55.000 foto's. Standaard: **niet in fase 6.** pgvector pas met het
   cluster.
4. **Wie ziet de plek van een foto?** Standaard: **alleen de eigenaar.** Nooit via een publieke
   link. Het deeldialoog waarschuwt dat een download de locatie in het bestand meeneemt; de bytes
   blijven gelijk. Andere antwoorden:
   (b) GPS uit downloads halen voor iedereen behalve de eigenaar (de bewerking van fase 10 naar
   voren, ±2 dagen);
   (c) "wie ik deel, ziet de plek ook" als keuze per gebruiker.
5. **Mag de lijst van plaatsen van GeoNames (CC BY 4.0, 15 MB) in het image, met een vermelding
   van de bron?** Standaard: **ja**, uit een vaste kopie met sha256 (data uit 2015), niet fataal,
   uit te zetten met `PLAATSEN=uit`. Wil je een nieuwere lijst, dan maken we een vaste kopie van
   `cities1000.zip` via een CI-job die je met de hand start.
6. **Wat ligt er onder de kaart?** Standaard: **alleen landsgrenzen** (Natural Earth, publiek
   domein), geen tegels van buiten. Straten vragen kaarttegels. Die van OpenStreetMap zijn niet
   voor zwaar gebruik, en een eigen tegelserver is groot (tientallen GB). Dat is later.

---

## 2. Pakketten

### 2.0 Overzicht, eigendom en regels

| Pakket | Golf | Dagen | Wat | Wacht op Jonas |
|---|---|---|---|---|
| Z0 Fundament | 0 | 3 | migratie 0009, contracten, stubs, hotspots, harnas, spike, `bench --los` | nee |
| Z1 Zoekmachine | 1 | 4 | index, vraag, toegang, snippets, tijdsgrens, terugval, suggesties (API) | nee |
| Z2 Tekst | 1 | 4 | tekst uit bestanden, `analysis`, backfill, tekstlaag (API) | nee |
| Z3 Foto-gegevens | 1 | 4 | GPS, camera, plaatsen, pHash, duplicaten, kaart (API), privacy | nee |
| Z4 Zoeken in de web | 2 | 4 | ingang, suggesties, `/zoeken`, tekstlaag in de viewer | nee |
| Z5 Kaart en duplicaten (web) | 2 | 4 | `/fotos/kaart`, plaatsenlijst, `/dubbel`, waarschuwing bij delen | nee |
| Z6 Personen, herinneringen, albums, filters | 2 | 4 | API en web | nee |
| Z7 Beheer en uitrol | 3 | 2–3 | `/beheer`, `install.sh`, back-up, terugrollen, runbook, belastingtest | nee |
| X1 OCR | 3 | 2–3 | opdracht `ocr` in de worker, CI met echte tesseract | **ja, Vraag 1** (alleen de regel in de Dockerfile) |
| Integratie | 4 | 2 | samenvoegen, metingen, 0.8.0 | — |
| X2 Gezichten automatisch | na fase 6 | 5–6 | container `analyse`, YuNet en SFace, migratie 0010 | **ja, Vraag 2** |

Samen ±15 werkdagen zonder X1 (3 + 4 + 4 + 2 + 2), 16 met X1 als die een dag langer duurt dan
Z7. Dat past in de 3 weken van het stappenplan.

**Wat schuift, in deze volgorde, als een golf uitloopt:** plaatsnamen op de kaart (zoom ≥ 6),
dan de suggesties (de zoekpagina werkt zonder), dan slimme albums, dan de web van de bijna-
duplicaten (de API blijft), dan X1. Niets uit deze lijst raakt een "klaar als" van de kern.

**Niet in fase 6:** X2 (tot ja), objecten en semantisch zoeken (Vraag 3), GPS uit downloads
halen (fase 10, of Vraag 4b), de kaart in 50m en straten, Nederlandse provincienamen, stemming.

**Eigendom.** Een bestand heeft één eigenaar per golf. Z0 zet de stubs en de hotspots; daarna
bewerkt alleen de eigenaar het bestand.

| Bestand of map | Eigenaar |
|---|---|
| `api/alembic/versions/0009_zoeken.py`, `app/models.py` (nieuwe modellen), `app/search/schema.py` | Z0 |
| `app/search/{engine,types,query,access,snippets,deadline,like,suggest,bench}.py`, `routers/search.py` | Z1 |
| `app/search/filters.py` (register en de filters `type`, `from`/`to`, `tag`, `library`, `in`) | Z1 |
| `app/analysis.py`, `app/extract/*`, `app/render.py` (ingang `text`), `routers/pages_text.py` | Z2 |
| `app/geo/*`, `app/dupes/*`, `app/render_meta.py`, `app/photometa.py` (v2), `routers/{geo,photos_map,duplicates}.py`, `tools/geo/*`, `tools/kaart/*` | Z3 |
| filters `place`, `country`, `camera` in `app/search/filters_photo.py` | Z3 |
| `web/src/search/**`, de zoekknop in `web/src/App.tsx`, `web/src/viewer/TextLayer.tsx` | Z4 |
| `web/src/photos/map/**`, `web/src/dupes/**`, de waarschuwing in `web/src/sharing/ShareDialog.tsx` | Z5 |
| `app/people/*`, `app/memories.py`, `app/albums_rule.py`, `routers/{people,memories,photo_prefs}.py`, filter `person` | Z6 |
| `web/src/photos/people/**`, `web/src/photos/Memories.tsx`, de regel in het albumdialoog, `PeopleRow` in het infopaneel | Z6 |
| `app/search/admin.py`, `routers/admin_search.py`, `web/src/admin/SearchTab.tsx`, `infra/single/*`, `docs/runbooks/zoeken.md` | Z7 |
| `app/media/worker/ops/ocr.py`, `app/ocr/*`, `infra/single/test_ocr_container.sh` | X1 |

**Regels** (zoals fase 5):

- elk pakket in een eigen worktree; hooguit 3 tegelijk (de sandbox heeft 8 GB en hooguit 3 zware
  processen);
- samenvoegen in de volgorde van de tabel; na elke samenvoeging alle checks (§4.6);
- geen nieuwe afhankelijkheid in `api/pyproject.toml` of `web/package.json` (B19). X1 voegt alleen
  na ja één regel aan de Dockerfile toe. X2 voegt alleen na ja een optionele extra toe;
- elk pakket eindigt met `docs/fase-6/z<n>.md`: wat er gebouwd is, wat afwijkt van dit ontwerp,
  en wat open is;
- een pakket met schermen levert een toetsenbordlijst `docs/fase-6/toetsenbord-z<n>.md`;
- elke "klaar als" hieronder heeft een test met een naam. Zonder die test is het pakket niet af.

### Z1 — Zoekmachine

**Doel.** `GET /api/v1/search` en `GET /api/v1/search/suggest` over de index, binnen 200 ms over
100.000 bestanden, met het toegangsfilter.

**Bouwt:**

- `query.py`: `parse()` (Keuze 5) met de twee vormen per woord;
- `access.py`: de wortels en hun bereiken (Keuze 14), met een cache per verzoek;
- `engine.py`: `FtsEngine` (Keuzes 4, 6, 7) en `select_engine()`; `like.py`: `LikeFallback`
  (de code van nu, verplaatst);
- `snippets.py` (Keuze 8), `deadline.py` (Keuze 9);
- `filters.py`: het register en de filters zonder foto's;
- `suggest.py`: namen (`fts_name` en voorvoegsel), tags, albums; personen en plaatsen via de
  haken van Z3 en Z6 (tot dan leeg);
- de tweede controle (Keuze 15), de grenzen en de log (Keuze 13), wissen in stukken
  (Keuze 11, in `routers/trash.py` en `userdelete.py` via een gedeelde helper);
- `bench.py`: het zaad van 100.000 (§3.6), en `app.cli search-bench`, `search-rebuild`,
  `search-check`, `search-triggers`.

**Tests:** `test_search_query.py` (tabel met invoer en uitdrukking, met Één, café, Straße,
İstanbul, ﬁets; fuzz met vaste seed: `parse()` geeft nooit een fout van FTS5);
`test_search_access.py` (de wereld van §4.2); `test_search_reference.py` (§4.3);
`test_search_churn.py`; `test_search_plans.py`; `test_search_deadline.py`;
`test_search_fallback.py` (zonder FTS5, met `PLATFORM_SEARCH_INDEX=uit`); `test_search_logs.py`;
`test_search_snippets.py` (ß, grens van 200.000 tekens, budget van 40 ms met een nepklok);
`test_search_purge_batches.py` (10.911 nodes: geen transactie langer dan 1 s).

**Klaar als:** alle tests hierboven groen op 3.53.1 en in `python:3.12-slim` (3.46.1); de poort
van §4.4 (`zoek-perf`) groen; geen `search_access_mismatch` in de hele suite.

### Z2 — Tekst uit bestanden, backfill en tekstlaag

**Doel.** De inhoud van documenten in `blob_text`, en de tekstlaag van B20 als API.

**Bouwt:**

- `app/analysis.py`: de tabel `analysis`, `register_step()`, `queue()`, de rij `index`, `Defer`
  (Keuzes 19, 49, 50);
- `app/extract/`: `pdf.py`, `ooxml.py`, `odf.py`, `plain.py`, `html.py`, `rtf.py`, `media.py`,
  `limits.py` (zip en XML), `normalize.py` (Keuze 21); de ingang `text` in `app/render.py`;
- de haken: `files.add_ref` (nieuwe sha), de omzetting van OnlyOffice (oude formaten), media
  (titels);
- de backfill (Keuze 20) en `indexing` in het antwoord van `/search`;
- `GET /api/v1/files/{id}/pages/{n}/text` (Keuze 18): woorden met hun plek uit pypdfium2, of uit
  `ocr_page`;
- `GET /api/v1/files/{id}/analysis` (de stand per stap, voor de viewer en `/beheer`).

**Tests:** `test_extract_*.py` per formaat met de fixtures van §3.6; zip-bom, XML met `DOCTYPE`,
een PDF van 2.001 pagina's, tekst van 2 MB (`truncated`), UTF-16, cp1252;
`test_extract_subprocess.py` (RLIMIT, geen netwerk nodig, stopt bij een kapot bestand zonder de
app te raken); `test_analysis_versions.py`; `test_backfill.py` (WAL: een gewone upload wacht nooit
langer dan 1 s tijdens de backfill); `test_pages_text.py` (toegang: lezen nodig, publiek `404`);
`e2e/test_zoek_z2.py` (upload docx → zoeken op een woord uit de tekst → snippet).

**Klaar als:** de tests groen; `e2e/perf/zoek_backfill.py` haalt ≥ 1.500 documenten per minuut
per core (§4.5).

### Z3 — Foto-gegevens: GPS, plaatsen, camera, pHash, duplicaten en de kaart-API

**Doel.** Alles wat een foto over zichzelf zegt, apart bewaard en alleen voor de eigenaar.

**Bouwt:**

- `render_meta.py` VERSION 2, de GPS-lezers (EXIF, XMP), de video-tags in de worker
  (`media/worker/ops/meta.py`, `TAGS`), `photometa.py` v2 (Keuze 25);
- `blob_meta`, `blob_geo`, `place_count`; `photo_pref.location` en de job die wist (Keuze 28);
- `tools/geo/build_places.py`, de stap `geo` in de Dockerfile, `app/geo/places.py`
  (R*Tree, niet fataal) (Keuze 29);
- pHash in het render-subproces, `blob_hash`, `dup_pair`, `dup_ignore` (Keuzes 35–36);
- de filters `place`, `country`, `camera` in het register; de suggesties "plaatsen";
- de routes `GET /files/{id}/location`, `GET /photos/map`, `GET /photos/map/items`,
  `GET /photos/places`, `GET /geo/attribution`, `GET /duplicates`, `POST /duplicates/ignore`,
  `GET /photos/share-check` (aantal foto's met een plek onder een map, voor Keuze 30).

**Tests:** `test_geo_read.py` (Pillow schrijft de GPS-IFD, heen en terug; HEIC; video met
ISO 6709 via ffmpeg, marker `media`); `test_geo_never_public.py` (elke route onder `/public` en
de publieke albumpagina: geen `lat`, `lon`, `place`, `geo`); `test_geo_access.py` (de wereld);
`test_places.py` (tegen brute-force haversine op 1.000 punten; 180°; zee); `test_phash.py`
(varianten: half zo groot, JPEG q35, 3–8% bijgesneden, ±4° gedraaid: afstand ≤ 10; andere
foto's > 10 in ≥ 99,8%); `test_dupes.py`; `test_map_clusters.py`; `test_location_off.py`.

**Klaar als:** de tests groen; `/photos/map` p95 ≤ 150 ms en `/duplicates` p95 ≤ 300 ms in
`zoek-perf`.

### Z4 — Zoeken in de web

**Doel.** Het zoeken zoals een gebruiker het ziet, met het toetsenbord.

**Bouwt:** het veld in de bovenbalk en Ctrl/Cmd+K (Keuze 45); de suggesties (Keuze 46);
`/zoeken` met chips, snippets, "Meer laden" en de meldingen (Keuze 47); de tekstlaag in de
PDF-viewer met de markering van `?q=` (Keuze 18); `web/src/i18n/errors/search.ts`; de
begroting.

**Tests:** vitest voor `query.ts` (URL heen en terug), de combobox (pijlen, Esc, Enter,
`aria-activedescendant`), de snippets (alleen tekst, geen HTML); `e2e/test_zoek_z4.py` (alleen
toetsenbord: Ctrl+K, typen, pijl, Enter, filter, Meer, de viewer met gemarkeerde woorden);
`e2e/perf/zoek_ui.py` (rapport: eerste suggestie na een toets, tekenen van `/zoeken`).

**Klaar als:** de tests groen; initiële JS ≤ 100 kB en Bestanden ≤ 45 kB; de toetsenbordlijst.

### Z5 — De kaart en de duplicaten in de web

**Doel.** Foto's op een kaart zonder tegels van buiten, en opruimen van dubbele bestanden.

**Bouwt:** `tools/kaart/maak_wereld.py` en `world-110m.json` (Keuze 31); de kaart als SVG met
projectie, slepen, zoom en toetsen; clusters van de server; de lijst "Plaatsen" (Keuze 33);
`/dubbel` met de groepen, "niet gelijk", en "naar de prullenbak" via de batchroute; de
waarschuwing in het deeldialoog (Keuze 30); de bronvermelding.

**Tests:** vitest voor de projectie (heen en terug, 180°), de clusters op het scherm, de
toetsen; `e2e/test_zoek_kaart.py` (telt verzoeken naar andere hosts: 0; alleen toetsenbord);
`e2e/test_zoek_dubbel.py`; `e2e/test_share_warning.py`.

**Klaar als:** de tests groen; `/fotos/kaart` en `/dubbel` binnen hun begroting; de
toetsenbordlijst.

### Z6 — Personen, herinneringen, slimme albums en filters op foto's

**Doel.** Personen benoemen en zoeken; herinneringen; albums die zichzelf vullen.

**Bouwt:**

- API: `person`, `photo_person` (Keuze 37), de routes van §3.4, het filter `person`, de groep
  personen in `/search` en de suggesties (Keuze 38);
- `memories.py` en `memory_hide` (Keuze 41);
- `albums_rule.py`, `album_rule`, `album_item.source`, `album_skip`, de verversing (Keuze 42);
- `photo_pref` en `/me/photo-settings` (Keuze 44);
- web: "Persoon toevoegen" met kader in het infopaneel, `/fotos/personen`, de persoonpagina,
  "Vandaag, N jaar geleden" op `/fotos`, de regel in het albumdialoog, filters op `/fotos`.

**Tests:** `test_people.py` (alleen de eigenaar; samenvoegen; wissen; een kopie heeft de persoon
ook); `test_people_privacy.py` (de wereld: Bram ziet geen personen op Eva's foto);
`test_memories.py` (schrikkeldag, tijdzone, verbergen); `test_album_rules.py` (hand-items
blijven; `album_skip`; grens 10.000; alleen eigen bestanden); `test_search_filters.py` (elk
filter en elke combinatie van twee, tegen de referentie); `e2e/test_zoek_personen.py` (alleen
toetsenbord: persoon toevoegen met kader, zoeken op de naam, filter).

**Klaar als:** de tests groen; "een persoon benoemen en vinden" werkt in e2e; `/photos/memories`
p95 ≤ 50 ms in `zoek-perf`.

### Z7 — Beheer, uitrol, update en terug

**Doel.** Jonas kan updaten, zien wat er gebeurt, en terug.

**Bouwt:** de tab op `/beheer` (Keuze 51) en `app/search/admin.py`; `db-backup` in
`install.sh` (Keuze 52); de stap `geo` en `PLAATSEN` in `.env.example`; `app.cli bench --los`
(de versie van Z0, nu met de uitkomst op `/beheer`); `docs/runbooks/zoeken.md` (bijwerken, de
index herbouwen, terug naar 0.7.0, de meting); de belastingtest naast OnlyOffice en media
(§4.5); de README.

**Tests:** `infra/single/test_install_search.sh` (back-up gemaakt; een mislukte back-up stopt het
script vóór elke wijziging; `PLAATSEN=uit`; een download van de plaatsen die faalt, is niet
fataal; terug naar 0.7.0 en weer vooruit); `e2e/test_zoek_beheer.py`; vitest voor de tab.

**Klaar als:** de tests groen; de CI-job `infra` doet de weg terug echt; de toetsenbordlijst.

### X1 — OCR (wacht op Vraag 1)

**Doel.** Tekst uit scans en foto's van tekst, doorzoekbaar.

**Bouwt:** `Op.OCR` in de worker (`app/media/worker/ops/ocr.py`: deskew, OSD, tesseract, TSV);
`app/ocr/` in de app (pagina's als PNG uit het render-subproces, de rij `ocr`, `ocr_page`, de
knop "Tekst herkennen", `photo_pref.ocr_photos`) (Keuzes 22–24); de CI-job `ocr` (§4.6).

**Wat op Jonas wacht:** alleen de regel `apt-get install tesseract-ocr tesseract-ocr-nld
tesseract-ocr-eng tesseract-ocr-osd` in de Dockerfile. Zonder ja wordt X1 wel samengevoegd, maar
blijft OCR uit (Keuze 24). Zegt Jonas nee vóór golf 3 begint, dan valt X1 weg.

**Tests:** `test_ocr_unit.py` (deskew op 0°, 3°, 7°, −5°; TSV lezen; zekerheid; geen tesseract →
`ocr_missing`); `test_ocr_real.py` (marker `ocr`, echte tesseract op gemaakte scans van §3.6:
recall per woord ≥ 0,95 op 0° en 3°, ≥ 0,9 op 7° en 90°); `e2e/test_zoek_ocr.py` (marker `ocr`:
een PDF zonder tekstlaag en een foto van een bon → zoeken op een woord dat alleen in het beeld
staat → snippet → de viewer markeert het).

**Klaar als:** de tests groen in de job `ocr`; de poort ≥ 18 pagina's per minuut per core
(§4.5); met ja ook `media-image` groen met tesseract erin (+ ≤ 30 MB).

### X2 — Gezichten automatisch (na ja op Vraag 2; niet in de 3 weken)

**Doel.** Gezichten vinden en groeperen, als voorstel voor de personen van Z6.

**Bouwt:** de container `analyse` (Keuze 39) met `compose.analyse.yaml`, de sleutel en de
handdruk van de mediaworker (`wire.py` met een eigen label), YuNet en SFace met sha256 in
`models.lock`, `face` en `face_person` in migratie 0010, groeperen per eigenaar, de voorstellen
in de web, de schakelaar per gebruiker en voor de beheerder.

**Tests:** een vaste lijst van foto's uit het publiek domein (NASA) met sha256, opgenomen uitvoer
van de modellen voor de nep-worker, en met de echte modellen: zuiverheid ≥ 0,9 en recall ≥ 0,8
op groepen. `test_analyse_container.sh` (uid 10003, geen netwerk, `read_only`).

**Klaar als:** de tests groen; de belastingtest met X2 aan houdt de poorten van §4.5.

---

## 3. Fundament (Z0, eerst en alleen)

Z0 duurt ±3 dagen, in drie delen naast elkaar: **Z0-api** (migratie, modellen, contracten,
stubs, hotspots, spike), **Z0-web** (routes, lege pagina's, de zoekknop, catalogi, begrotingen)
en **Z0-e2e** (harnas, fixtures, de wereld, de referentie, CI-jobs). Z0 bouwt geen functie af;
het legt vast waar de pakketten op bouwen.

### 3.1 Migratie 0009 (`0009_zoeken.py`, down_revision `0008`)

Alles in ruwe SQL via `op.execute` (zoals 0005): FTS5, views en triggers kent Alembic niet. De
SQL van de triggers staat ook in `app/search/schema.py`; een test eist dat ze gelijk zijn. Op
PostgreSQL maakt de migratie alleen de gewone tabellen en kolommen (Keuze 10).

**Kolommen op bestaande tabellen** (`ALTER TABLE … ADD COLUMN`, geen kopie van de tabel):

```sql
ALTER TABLE node ADD COLUMN num INTEGER;               -- vast nummer, rowid in FTS
ALTER TABLE node ADD COLUMN mpath TEXT;                -- '/1/7/42/'
ALTER TABLE node ADD COLUMN dead INTEGER NOT NULL DEFAULT 0;
CREATE UNIQUE INDEX ix_node_num ON node (num);
CREATE INDEX ix_node_mpath ON node (mpath);
CREATE INDEX ix_node_trash ON node (mpath) WHERE deleted_at IS NOT NULL;
ALTER TABLE album_item ADD COLUMN source TEXT NOT NULL DEFAULT 'hand';   -- 'hand' of 'rule'
```

**Nieuwe tabellen.** Elke `blob_sha` is een FK naar `blob(sha256) ON DELETE CASCADE`; elke
`user_id`/`owner_id` naar `user(id) ON DELETE CASCADE`.

| Tabel | Kolommen | Sleutel en indexen | Pakket |
|---|---|---|---|
| `search_seq` | `id` (= 1), `next` | PK `id` | Z1 |
| `blob_text` | `blob_sha`, `body` TEXT '', `ocr` TEXT '', `meta` TEXT '', `truncated` INT 0, `updated_at` | PK `blob_sha` | Z2 |
| `ocr_page` | `blob_sha`, `page` INT, `words` BLOB (zlib, JSON `[[t, x, y, w, h], …]`) | PK (`blob_sha`, `page`) | X1 |
| `analysis` | `blob_sha`, `step` (`text`, `hash`, `ocr`), `version` INT, `state` (`wait`, `busy`, `done`, `none`, `skip`, `failed`), `tries` INT 0, `code` TEXT NULL, `updated_at` | PK (`blob_sha`, `step`); `ix_analysis_todo (step, state, version)` | Z2 |
| `blob_meta` | `blob_sha`, `camera` TEXT NULL (gevouwen "merk model"), `lens` TEXT NULL | PK; `ix_blob_meta_camera (camera)` | Z3 |
| `blob_geo` | `blob_sha`, `lat` REAL, `lon` REAL, `alt` REAL NULL, `x16` INT, `y16` INT, `place_id` INT NULL, `country` TEXT NULL, `admin1` TEXT NULL, `source` (`exif`, `xmp`, `video`) | PK; `ix_geo_tile (x16, y16)`, `ix_geo_place (place_id)`, `ix_geo_country (country)` | Z3 |
| `place_count` | `owner_id`, `place_id`, `country`, `n` | PK (`owner_id`, `place_id`) | Z3 |
| `blob_hash` | `blob_sha`, `phash` INT (64 bits, met teken), `h0`–`h3` INT (16 bits), `version` INT | PK; `ix_hash_0 (h0)` … `ix_hash_3 (h3)` | Z3 |
| `dup_pair` | `a`, `b` (sha, `a < b`), `dist` INT | PK (`a`, `b`), WITHOUT ROWID; `ix_dup_b (b)` | Z3 |
| `dup_ignore` | `user_id`, `key` TEXT (sha256 van de gesorteerde shas) | PK (`user_id`, `key`) | Z3 |
| `person` | `id` UUID, `owner_id`, `name` TEXT, `search_name` TEXT, `hidden` INT 0, `cover_sha` NULL (FK `blob` SET NULL), `created_at`, `updated_at` | PK; `ix_person_owner (owner_id, hidden, search_name)` | Z6 |
| `photo_person` | `person_id` (FK `person` CASCADE), `blob_sha`, `owner_id`, `x`, `y`, `w`, `h` REAL NULL, `source` (`hand`, `auto`) 'hand', `created_at` | PK (`person_id`, `blob_sha`); `ix_pp_owner_blob (owner_id, blob_sha)` | Z6 |
| `photo_pref` | `user_id`, `location` (`mij`, `uit`) 'mij', `memories` INT 1, `ocr_photos` INT 0, `faces` (`uit`, `aan`) 'uit', `updated_at` | PK `user_id` | Z6 |
| `memory_hide` | `user_id`, `kind` (`day`, `folder`, `person`), `ref` TEXT | PK (`user_id`, `kind`, `ref`) | Z6 |
| `album_rule` | `album_id` (FK `node` CASCADE), `rule` TEXT (JSON), `refreshed_at`, `count` INT, `code` TEXT NULL | PK `album_id` | Z6 |
| `album_skip` | `album_id`, `node_id` (beide FK `node` CASCADE) | PK (`album_id`, `node_id`) | Z6 |

`setting` (sleutel `search` voor de schakelaars van `/beheer`) en `job` bestaan al.

**De view en de FTS-tabellen:**

```sql
CREATE TABLE search_seq (id INTEGER PRIMARY KEY CHECK (id = 1), next INTEGER NOT NULL);

CREATE VIEW search_src AS
  SELECT n.num AS num, n.search_name AS name,
         coalesce((SELECT group_concat(m.title, ' ') FROM media_item m WHERE m.node_id = n.id), '') AS extra,
         coalesce(t.body, '') AS body, coalesce(t.ocr, '') AS ocr, coalesce(t.meta, '') AS meta
  FROM node n LEFT JOIN blob_text t ON t.blob_sha = n.blob_id;

CREATE VIRTUAL TABLE fts_node USING fts5(name, extra, body, ocr, meta,
  content='search_src', content_rowid='num', tokenize='unicode61 remove_diacritics 2');
CREATE VIRTUAL TABLE fts_name USING fts5(search_name,
  content='node', content_rowid='num', tokenize='trigram');
```

De view neemt ook albums mee: dan klopt de `delete` in FTS altijd met wat erin staat. De vraag
sluit ze uit (Keuze 4).

**De triggers** (zo gemeten in de proef van B, met de namen van de app). `END(p)` staat voor
`substr(p, 1, length(p) - 1) || '0'`; `DEL(w)` voor `INSERT INTO fts_node(fts_node, rowid,
name, extra, body, ocr, meta) SELECT 'delete', num, name, extra, body, ocr, meta FROM search_src
WHERE w;` en `INS(w)` voor `INSERT INTO fts_node(rowid, name, extra, body, ocr, meta) SELECT
num, name, extra, body, ocr, meta FROM search_src WHERE w;`. In `schema.py` staan ze uitgeschreven.

```sql
-- nieuw: nummer, pad, dead, beide FTS
CREATE TRIGGER node_ai AFTER INSERT ON node BEGIN
  UPDATE search_seq SET next = next + 1 WHERE id = 1;
  UPDATE node SET num = (SELECT next - 1 FROM search_seq WHERE id = 1) WHERE id = NEW.id;
  UPDATE node SET
    mpath = coalesce((SELECT p.mpath FROM node p WHERE p.id = NEW.parent_id), '/') || num || '/',
    dead  = coalesce((SELECT p.dead FROM node p WHERE p.id = NEW.parent_id), 0) OR NEW.deleted_at IS NOT NULL
  WHERE id = NEW.id;
  INS(num = (SELECT num FROM node WHERE id = NEW.id))
  INSERT INTO fts_name(rowid, search_name) SELECT num, search_name FROM node WHERE id = NEW.id;
END;

-- naam of inhoud: eerst de oude rij uit FTS (vóór de wijziging laat de view nog het oude zien)
CREATE TRIGGER node_bu_text BEFORE UPDATE OF search_name, blob_id ON node
WHEN OLD.num IS NOT NULL AND (OLD.search_name IS NOT NEW.search_name OR OLD.blob_id IS NOT NEW.blob_id) BEGIN
  DEL(num = OLD.num)
  INSERT INTO fts_name(fts_name, rowid, search_name) VALUES ('delete', OLD.num, OLD.search_name);
END;
CREATE TRIGGER node_au_text AFTER UPDATE OF search_name, blob_id ON node
WHEN NEW.num IS NOT NULL AND (OLD.search_name IS NOT NEW.search_name OR OLD.blob_id IS NOT NEW.blob_id) BEGIN
  INS(num = NEW.num)
  INSERT INTO fts_name(rowid, search_name) VALUES (NEW.num, NEW.search_name);
END;

-- wissen, ook via de FK-cascade [gemeten: de cascade start deze trigger]
CREATE TRIGGER node_bd BEFORE DELETE ON node WHEN OLD.num IS NOT NULL BEGIN
  DEL(num = OLD.num)
  INSERT INTO fts_name(fts_name, rowid, search_name) VALUES ('delete', OLD.num, OLD.search_name);
END;

-- verplaatsen: de paden van de hele submap, dan dead
CREATE TRIGGER node_au_move AFTER UPDATE OF parent_id ON node
WHEN OLD.parent_id IS NOT NEW.parent_id AND OLD.mpath IS NOT NULL BEGIN
  UPDATE node SET mpath = (SELECT p.mpath FROM node p WHERE p.id = NEW.parent_id) || NEW.num || '/'
                          || substr(mpath, length(OLD.mpath) + 1)
   WHERE mpath >= OLD.mpath AND mpath < END(OLD.mpath);
  DEAD_SUBTREE
END;

-- prullenbak in of uit
CREATE TRIGGER node_au_trash AFTER UPDATE OF deleted_at ON node
WHEN (OLD.deleted_at IS NULL) <> (NEW.deleted_at IS NULL) AND OLD.mpath IS NOT NULL BEGIN
  DEAD_SUBTREE
END;
```

`DEAD_SUBTREE` rekent `dead` opnieuw voor de submap. `CUR` is het pad van nu,
`(SELECT mpath FROM node WHERE id = NEW.id)`: `NEW.mpath` is in de trigger nog het oude.

```sql
UPDATE node SET dead =
    coalesce((SELECT p.dead FROM node p WHERE p.id = NEW.parent_id), 0)
    OR EXISTS (SELECT 1 FROM node t WHERE t.deleted_at IS NOT NULL
               AND t.mpath >= CUR AND t.mpath < END(CUR)              -- ix_node_trash
               AND node.mpath >= t.mpath AND node.mpath < END(t.mpath))
 WHERE mpath >= CUR AND mpath < END(CUR);
```

`blob_text` en `media_item` (een `media_item` waarvan `node_id` verandert, bestaat niet; een test
houdt dat vast):

```sql
CREATE TRIGGER bt_bi BEFORE INSERT ON blob_text BEGIN DEL(blob_id = NEW.blob_sha) END;
CREATE TRIGGER bt_ai AFTER  INSERT ON blob_text BEGIN INS(blob_id = NEW.blob_sha) END;
CREATE TRIGGER bt_bu BEFORE UPDATE ON blob_text BEGIN DEL(blob_id = OLD.blob_sha) END;
CREATE TRIGGER bt_au AFTER  UPDATE ON blob_text BEGIN INS(blob_id = NEW.blob_sha) END;
CREATE TRIGGER bt_bd BEFORE DELETE ON blob_text BEGIN DEL(blob_id = OLD.blob_sha) END;
CREATE TRIGGER bt_ad AFTER  DELETE ON blob_text BEGIN INS(blob_id = OLD.blob_sha) END;

CREATE TRIGGER mi_bi BEFORE INSERT ON media_item BEGIN DEL(num = (SELECT num FROM node WHERE id = NEW.node_id)) END;
CREATE TRIGGER mi_ai AFTER  INSERT ON media_item BEGIN INS(num = (SELECT num FROM node WHERE id = NEW.node_id)) END;
CREATE TRIGGER mi_bu BEFORE UPDATE OF title ON media_item BEGIN DEL(num = (SELECT num FROM node WHERE id = OLD.node_id)) END;
CREATE TRIGGER mi_au AFTER  UPDATE OF title ON media_item BEGIN INS(num = (SELECT num FROM node WHERE id = NEW.node_id)) END;
CREATE TRIGGER mi_bd BEFORE DELETE ON media_item BEGIN DEL(num = (SELECT num FROM node WHERE id = OLD.node_id)) END;
CREATE TRIGGER mi_ad AFTER  DELETE ON media_item BEGIN INS(num = (SELECT num FROM node WHERE id = OLD.node_id)) END;
```

**Vullen in de migratie** [gemeten op 104.696 nodes, SQLite 3.46.1: 2,8 s]:

```sql
UPDATE node SET num = rowid;                                      -- uniek; daarna los van rowid
INSERT INTO search_seq VALUES (1, coalesce((SELECT max(num) FROM node), 0) + 1);
WITH RECURSIVE t(id, mpath, dead) AS (
  SELECT id, '/' || num || '/', deleted_at IS NOT NULL FROM node WHERE parent_id IS NULL
  UNION ALL
  SELECT n.id, t.mpath || n.num || '/', t.dead OR n.deleted_at IS NOT NULL
  FROM node n JOIN t ON n.parent_id = t.id)
UPDATE node SET mpath = t.mpath, dead = t.dead FROM t WHERE node.id = t.id;
INSERT INTO fts_node(fts_node) VALUES ('rebuild');     -- alleen namen en titels: blob_text is leeg
INSERT INTO fts_name(fts_name) VALUES ('rebuild');
-- daarna pas de triggers
```

Zoeken op naam werkt dus meteen na de migratie. De inhoud komt met de backfill (Keuze 20).

**Downgrade naar 0008:** eerst alle triggers, dan de view, de twee FTS-tabellen (hun
schaduwtabellen gaan mee), de nieuwe tabellen, de drie indexen op `node`, dan `ALTER TABLE node
DROP COLUMN` voor `num`, `mpath`, `dead` en `ALTER TABLE album_item DROP COLUMN source` (SQLite ≥
3.35, beide builds). Geen kopie van `node`. `db-downgrade` waarschuwt vóór "ja": de index, de
plekken, de hashes, de personen en de regels van albums gaan weg; bestanden, delingen en albums
blijven.

**Tests** (`tests/test_migration_0009.py`, zoals die van 0008):

- leeg naar head; 0008 met data naar 0009 en terug, en weer heen: bestanden, delingen en albums
  blijven gelijk; `num` uniek; elk pad klopt met de ouders; `dead` klopt met de prullenbak;
  `integrity-check` van FTS slaagt;
- `compare_metadata` tussen de modellen en de migratie, met de schaduwtabellen van FTS
  (`fts_*_data`, `_idx`, `_docsize`, `_config`) en de view eruit gefilterd;
- zonder FTS5 (een SQLite zonder, via een vlag in de test): de migratie slaat FTS over, de app
  draait met `LikeFallback`;
- een migratie van 100.000 nodes duurt < 10 s (marker `zoek_perf`).

### 3.2 Instellingen

Nieuw in `.env` (`install.sh` zet de standaard één keer):

```
# Zoeken en foto's (fase 6)
PLAATSEN=aan                # aan | uit: de plaatsnamen van GeoNames in het image (Vraag 5)
```

In de container van de app (compose zet ze, of de standaard geldt):

| Variabele | Standaard | Wat |
|---|---|---|
| `PLATFORM_SEARCH_INDEX` | `aan` | `uit` zet `LikeFallback` terug (Keuze 10) |
| `PLATFORM_SEARCH_DEADLINE_MS` | `150` | tijdsgrens per vraag (Keuze 9) |
| `PLATFORM_SEARCH_MAX_TEXT` | `1048576` | tekens per blob |
| `PLATFORM_SEARCH_TIMING` | `false` | `Server-Timing` op `/search` (alleen tests en metingen) |
| `PLATFORM_PLACES_DB` | `/srv/geo/places.sqlite` | de plaatsen; ontbreekt hij, dan geen namen |
| `PLATFORM_INDEX_DEFER_TRANSCODES` | `2` | `index` en `ocr` wachten bij zoveel transcodes |
| `PLATFORM_OCR_MAX_PAGES` | `200` | per document (X1) |

Geen nieuwe geheimen. (X2 krijgt later `ANALYSE_WORKER_KEY`, zoals `MEDIA_WORKER_KEY`.)

### 3.3 Python-contracten (vast)

```python
# app/search/types.py
class SearchParams(BaseModel):
    q: str = Field("", max_length=200)
    types: list[FileType] = []; date_from: date | None = None; date_to: date | None = None
    persons: list[UUID] = []; place: int | None = None; country: str | None = None
    tags: list[UUID] = []; library: UUID | None = None; camera: str | None = None
    in_: UUID | None = None
    offset: int = Field(0, ge=0, le=1000); limit: int = Field(30, ge=1, le=50)

class SnippetPart(BaseModel): t: str; m: bool
class Hit(BaseModel):
    node: NodeOut; path: str
    match: Literal["name", "title", "text", "ocr", "meta"]
    snippet: list[SnippetPart] | None
class SearchPage(BaseModel):
    results: list[Hit]; more: bool; offset: int
    partial: bool                                   # tijdsgrens geraakt (Keuze 9)
    groups: Groups | None                           # people, places: alleen eigen
    indexing: IndexProgress | None                  # {"done": 0.43} tijdens de backfill
    engine: Literal["fts", "like"]

# app/search/engine.py
class SearchEngine(Protocol):
    name: Literal["fts", "like"]
    async def search(self, db: AsyncSession, user: User, p: SearchParams) -> SearchPage: ...
    async def suggest(self, db: AsyncSession, user: User, q: str) -> Suggestions: ...
def select_engine(settings: Settings, conn_info: ConnInfo) -> SearchEngine: ...

# app/search/query.py
def parse(q: str) -> Parsed | None: ...      # None: niets om te zoeken; nooit een FTS-fout
# app/search/deadline.py
@contextmanager
def deadline(conn: AsyncConnection, ms: int) -> Iterator[Deadline]: ...   # .hit na afloop
# app/search/filters.py
def register(name: str, apply: ApplyFilter, suggest: SuggestFn | None = None) -> None: ...
# app/search/hooks.py (zoals media.hooks: pakketten melden zich aan)
def register_groups(fn: GroupsFn) -> None: ...          # Z3 plaatsen, Z6 personen
def register_suggest(fn: SuggestFn) -> None: ...

# app/analysis.py
class Step(StrEnum): TEXT = "text"; HASH = "hash"; OCR = "ocr"
VERSIONS: dict[Step, int] = {Step.TEXT: 1, Step.HASH: 1, Step.OCR: 1}
def register_step(step: Step, lane: str, eligible: Eligible, run: Runner) -> None: ...
async def queue(db: AsyncSession, sha: str, steps: Iterable[Step], *, first: bool = False) -> None: ...

# app/extract/result.py
@dataclass(frozen=True)
class Extracted:
    state: Literal["done", "none", "skip", "failed"]
    chars: int; truncated: bool; scan_pages: list[int]; meta: str; code: str | None
    # de tekst zelf komt via fd 3

# app/geo/types.py
@dataclass(frozen=True)
class Location: lat: float; lon: float; alt: float | None; source: Literal["exif", "xmp", "video"]
@dataclass(frozen=True)
class Place: id: int; name: str; admin1: str; cc: str; km: float

# app/prefs.py
PHOTO_PREFS = {"location": "mij", "memories": True, "ocr_photos": False, "faces": "uit"}
```

Rijen in `jobs.py`:

```python
LANES = ("default", "media_scan", "media", "media_net", "import", "index", "ocr")
def active_lanes(s: Settings) -> tuple[str, ...]:
    return ((DEFAULT_LANE, "index")
            + (MEDIA_LANES if s.media_enabled else ())
            + (("ocr",) if s.media_enabled and s.ocr_enabled else ()))
```

### 3.4 API-contracten, gebeurtenissen en codes (vast)

Alles onder `/api/v1`, met inlog en 2FA zoals de rest. Geen enkele route hieronder werkt via een
publieke link.

**Zoeken**

| Methode en pad | Vraag | Antwoord |
|---|---|---|
| `GET /search` | `q` (≤ 200), `type` (meer keer), `from`, `to`, `person` (meer keer), `place` of `country`, `tag` (meer keer), `library`, `camera`, `in`, `offset` (0–1.000), `limit` (1–50, standaard 30) | `SearchPage` (§3.3). Zonder `q` en zonder filter: `400 search_bad_filter`. |
| `GET /search/suggest` | `q` (1–200) | `{"names": [NameRef], "people": [PersonRef], "places": [PlaceRef], "tags": [TagRef], "albums": [AlbumRef]}`, elk ≤ 5 |

`Hit` blijft `{node, path}` zoals in 0.7.0 en krijgt `match` en `snippet` erbij. De web van nu
leest alleen `results` en `more`, en werkt dus gewoon door.

**Tekst**

| Methode en pad | Wie | Antwoord |
|---|---|---|
| `GET /files/{id}/pages/{n}/text` | leesrecht | `{"page": n, "words": [{"t", "x", "y", "w", "h"}], "source": "pdf" \| "ocr"}`; `404 not_found` als er geen is |
| `GET /files/{id}/analysis` | leesrecht | `{"text": "done", "hash": "done", "ocr": "none", "truncated": false}` |
| `POST /files/{id}/ocr` (X1) | schrijfrecht | `202`; `409 ocr_running`; `503 ocr_off` |

**Foto's, plaats en kaart** (alleen eigen bestanden)

| Methode en pad | Antwoord |
|---|---|
| `GET /files/{id}/location` | `{"lat", "lon", "alt", "place": {"name", "admin1", "cc"} \| null}`; `404 not_found` als er geen is of je hem niet mag zien |
| `GET /photos/map?bbox=w,s,e,n&z=0..14&<filters>` | `{"cells": [{"x", "y", "n", "lat", "lon", "cover": uuid}]}` (≤ 500) |
| `GET /photos/map/items?bbox=&<filters>&cursor=` | `{"items": [PhotoItem], "cursor"}` |
| `GET /photos/places?<filters>` | `{"places": [{"id", "name", "admin1", "country", "n"}]}` |
| `GET /photos/share-check?node=` | `{"with_location": int}` (eigen map) |
| `GET /geo/attribution` | `{"places": {"source", "license", "date"} \| null, "world": {"source", "license"}}` |
| `GET /photos` (bestaat) | krijgt de filters van Keuze 43 |
| `GET /photos/memories?day=JJJJ-MM-DD` | `{"years": [{"years_ago", "day", "items": [PhotoItem]}]}` |
| `GET`, `POST /photos/memories/hide`, `DELETE /photos/memories/hide/{kind}/{ref}` | `{"kind": "day" \| "folder" \| "person", "ref": str}` |
| `GET`, `PATCH /me/photo-settings` | `{"location": "mij" \| "uit", "memories": bool, "ocr_photos": bool}` |

**Duplicaten** (alleen eigen bestanden)

| Methode en pad | Antwoord |
|---|---|
| `GET /duplicates?kind=exact\|near&cursor=` | `{"groups": [{"key", "kind", "dist": int \| null, "items": [{"node", "path"}]}], "cursor"}` |
| `POST /duplicates/ignore` | `{"key"}` → `204`; `400 dupes_bad_key` |

Opruimen gaat via de bestaande batchroute van de prullenbak.

**Personen** (alleen eigen; een id van een ander geeft `404 not_found`)

| Methode en pad | Antwoord |
|---|---|
| `GET /people?hidden=0\|1` | `{"people": [{"id", "name", "count", "cover": uuid \| null, "hidden"}]}` |
| `POST /people` | `{"name"}` → persoon; `409 people_name_taken` |
| `PATCH /people/{id}` | `{"name"?, "hidden"?, "cover"?}` |
| `POST /people/{id}/merge` | `{"into": uuid}` → persoon; `400 people_merge_self` |
| `DELETE /people/{id}` | `204` |
| `GET /people/{id}/photos?cursor=` | `PhotoItem`-lijst |
| `GET /files/{id}/people` | `{"people": [{"person": PersonRef, "box": [x, y, w, h] \| null, "source"}]}`; voor een ander `[]` |
| `POST /files/{id}/people` | `{"person_id"}` of `{"name"}`, `"box"?` → `201` (eigenaar) |
| `DELETE /files/{id}/people/{person_id}` | `204` |

**Slimme albums:** `PUT /albums/{id}/rule` met `{"rule": SearchParams zonder offset en limit}`,
`DELETE /albums/{id}/rule`, `POST /albums/{id}/refresh` → `202`. `GET /albums/{id}` geeft
`rule`, `refreshed_at`, `count`, alleen aan de eigenaar. Codes `album_rule_invalid`,
`album_rule_too_many`.

**Beheer:** `GET /admin/search` (de stand van Keuze 51), `PATCH /admin/search` (`{"ocr"?,
"paused"?}`), `POST /admin/search/rebuild` (`{"what": "names" | "text" | "geo" | "hash" | "ocr"}`
→ `202`; `409 search_rebuild_running`), `GET /admin/search/failed?cursor=` (sha, stap, code,
tijd), `POST /admin/search/retry`.

**Gebeurtenissen** (SSE, zoals `preview.ready`): `search.ready {"nodes": [uuid], "step"}` naar
wie de nodes kan zien (de viewer ververst de tekstlaag); `people.changed {}` naar de eigenaar;
`album.changed` (bestaat) na een verversing; `search.progress` alleen naar beheerders, elke 10 s
tijdens een backfill.

**Foutcodes** (elk in `web/src/i18n/errors/search.ts` of `photos.ts`, NL en EN;
`test_error_catalog.py`): `search_query_too_long`, `search_bad_offset`, `search_bad_filter`,
`search_rebuild_running`, `ocr_off`, `ocr_running`, `dupes_bad_key`, `people_name_taken`,
`people_merge_self`, `album_rule_invalid`, `album_rule_too_many`, `memories_bad_hide`. Bestaande
codes: `not_found`, `rate_limited`, `guest`, `read_only`.

### 3.5 Hotspots die Z0 zelf aanpast

| Bestand | Wat |
|---|---|
| `app/jobs.py` | `LANES` + `index`, `ocr`; `active_lanes` (§3.3); `test_jobs_lanes.py` bij |
| `app/main.py` | routers mounten (stubs geven `501` tot hun pakket); `q` in `SECRET_PARAMS`; lifespan: de backfills (stubs) |
| `app/config.py` | de instellingen van §3.2 |
| `app/cli.py` | `search-bench`, `search-rebuild`, `search-check`, `search-triggers` (stubs), en `db-backup` meteen af: `VACUUM INTO` naar `/data/backup/`, terwijl de app draait; migreert niets; de laatste 2 blijven |
| `app/routers/files.py` | in `add_ref()`: `search.hooks.blob_added(sha)` na de commit |
| `app/previews.py` | `register_ready(fn)`: na `preview.ready` (voor de pHash) |
| `app/render.py` | ingang `text` (stub die `none` geeft) |
| `app/media/wire.py` | `Op.OCR` (de worker geeft `not_built` tot X1) |
| `app/routers/search.py` | via `select_engine`; tot Z1 alleen `LikeFallback` |
| `web/src/App.tsx`, `routes.tsx`, `router.ts` | het zoekveld (leeg), Ctrl/Cmd+K, de nieuwe routes met lege pagina's |
| `web/src/viewer/InfoPanel.tsx` | sloten `LocationRow`, `PeopleRow` |
| `web/budget.mjs` | regels voor de nieuwe chunks |

### 3.6 Het harnas en de fixtures

- **De boom van 100.000** (`api/app/search/bench.py`, B's generator als module): 30 gebruikers,
  ±5.900 mappen tot diepte 5, 100.000 bestanden, 20.000 documenten met samen ±235 miljoen tekens
  (Zipf, NL en EN met diakrieten, kunstwoorden), 2.000 kopieën bij een ander, 3% van de mappen
  en 1% van de bestanden in de prullenbak, delingen naar personen, groepen en gasten, 55.000
  foto's waarvan 60% met GPS. Via de echte migratie en de echte triggers. Een profiel "groot"
  heeft 200 documenten van 1 MB tekst.
- **De wereld** (`api/tests/search/wereld.py`): Eva, Bram, Cas, Gijs, Dirk, Ada, Fien, Hans en
  Iris met hun mappen, delingen en bestanden (§4.2). Gedeeld door alle tests op toegang.
- **De referentie** (`api/tests/search/referentie.py`): zoeken in gewoon Python over dezelfde
  boom van 2.000 nodes: de definitie van "wat moet gevonden worden" (§4.3).
- **Gemaakte bestanden** (`api/tests/search/fixtures.py`, gemaakt bij de test en bewaard in
  `api/.pytest_cache/search`, zoals de media-fixtures; niets binairs in het repo):
  - PDF met tekst: een eigen kleine PDF-schrijver (Helvetica, stdlib), 1–20 pagina's, twee
    kolommen, U+00AD, een emoji;
  - PDF als scan: een JPEG-pagina (Pillow) zonder tekstlaag;
  - docx, xlsx, pptx, odt, ods, odp: zip met vaste XML-sjablonen (stdlib), met voetnoten, kop-
    en voetteksten, sprekersnotities, 20.000 cellen; en een zip-bom en een XML met `ENTITY`;
  - scans voor OCR (X1): Pillow tekent tekst met DejaVu (uit Debian in de job) op grijs papier
    met ruis, op 0°, 3°, 7°, −5° en 90°, als PNG en JPEG q80, NL en EN;
  - foto's met EXIF en GPS: Pillow schrijft de GPS-IFD; HEIC via pillow-heif; video met
    `location` via ffmpeg (marker `media`);
  - bijna-duplicaten: elk origineel met vier varianten, en andere foto's.
- **Plan-tests** (`api/tests/search/plans.py`): `assert_plan(sql, args, require_first="fts_node",
  forbid=["AUTOMATIC"], forbid_index_terms=["parent_id=?"])`.
- **e2e** (`e2e/search_harness.py`): start de app met een gezaaide database (de wereld, of 1.000,
  of 100.000) en geeft ingelogde gebruikers met elke rol.

### 3.7 De spike (Z0-api, dag 1–2)

| # | Wat | Waarom | Klaar als |
|---|---|---|---|
| S1 | De tijdsgrens via een progress-handler, door aiosqlite en de pool van SQLAlchemy heen | Keuze 9 is [niet getest] | een vraag van 2 s stopt na 150 ± 30 ms, en de verbinding werkt daarna gewoon |
| S2 | De triggers met de ORM: hernoemen, verplaatsen, prullenbak, de batch van 500, kopie, `userdelete`, `purge_expired`, de import van fase 5 | Keuze 3 | na elke stap is de index gelijk aan een herbouw |
| S3 | 0009 heen en terug met data; `compare_metadata` | §3.1 | precies 0008 na de downgrade |
| S4 | De plan-tests in `python:3.12-slim`, met en zonder `ANALYZE` | J12 | groen in CI in het image |
| S5 | Tesseract in Debian 13: grootte, en of ffmpeg `libtesseract` al meebrengt | Vraag 1 | een getal in `docs/fase-6/spike.md` (alleen gemeten in CI; het image verandert niet) |
| S6 | `world-110m.json` maken en meten | Keuze 31 | ≤ 60 kB gzip, of een andere vereenvoudiging |
| S7 | Het zaad van 100.000 in CI | §4.4 | < 3 minuten |
| S8 | De twee vormen per woord (J1) op 3.46.1 en 3.53.1 | Keuze 5 | ß, ligaturen en İ vinden naam én tekst |
| S9 | `tools/zoekbench/bench.py`: alleen stdlib, maakt het schema van 0009 en een boom van 100.000 in een tijdelijke map, en meet zoeken voor "eigenaar" en "alles" | Keuze 54, B5 | draait in de container van 0.7.0 met `dc exec -T platform python -I - --los < bench.py`, in < 5 minuten, en ruimt op |

Met S9 kan Jonas al in week 1 meten wat zijn CPU haalt, zonder iets te installeren. Z7 maakt er
`app.cli bench --los` van, met de uitkomst op `/beheer`.

### 3.8 Klaar als (Z0)

- 0009 heen en terug, ook zonder FTS5;
- alle routers gemount (stubs), de web-app bouwt met de lege pagina's en blijft binnen budget;
- de spike S1–S9 staat in `docs/fase-6/spike.md`, met wat het ontwerp daardoor verandert;
- de fixtures maken alles van §3.6; de wereld en de referentie hebben eigen tests;
- de CI-jobs van §4.6 bestaan en zijn groen op de stubs;
- api, api-media, web, e2e, office en media-image zijn groen zoals in 0.7.0.

### 3.9 Elke gebeurtenis, wat er gebeurt, en het bewijs

| Gebeurtenis | Index (namen en paden) | Inhoud (`blob_text`) | Bij zoeken | Test |
|---|---|---|---|---|
| upload | trigger, zelfde transactie | job `text` als de sha nieuw is | naam meteen; inhoud na de job (seconden) | `test_upload_name_then_text` |
| kopie | trigger | niets: de sha is er al | meteen, met inhoud | `test_copy_shares_text` |
| hernoemen | trigger (`search_name`) | niets | meteen | `test_rename` |
| verplaatsen, ook naar een map van een ander | trigger: paden van de submap | niets | meteen | `test_move` |
| inhoud vervangen, nieuwe versie | trigger (`blob_id`) | job voor de nieuwe sha | de oude tekst nooit meer | `test_replace_no_old_text` |
| versie terugzetten | trigger (`blob_id`) | de oude sha heeft zijn rij nog | meteen | `test_restore_version` |
| naar de prullenbak (bestand of map) | trigger: `dead` voor de submap | niets | weg | `test_trash_subtree` |
| terug uit de prullenbak | trigger: `dead` | niets | terug | `test_restore` |
| definitief wissen, gebruiker wissen | cascade → `node_bd`, per 2.000 | niets | weg | `test_purge_cascade` |
| blob opgeruimd | — | cascade: `blob_text`, `blob_geo`, `blob_hash`, `photo_person` | — | `test_gc_cascade` |
| delen met persoon, groep of gast | niets | niets | `shared_roots` neemt het meteen mee | `test_share_now_visible` |
| deling ingetrokken, verlopen, lid uit groep | niets | niets | meteen weg (en de tweede controle) | `test_unshare_now_gone` |
| album | niet als treffer | — | alleen in de suggesties | `test_album_not_searched` |
| publieke link | — | — | geen zoekroute; geen snippet, plek of persoon | `test_public_never_search` |
| tag erbij of eraf | — | — | bij de vraag, meteen | `test_tag_now` |
| titel in de mediabibliotheek | trigger op `media_item` | — | meteen | `test_media_title` |
| OnlyOffice maakt een PDF (oude formaten) | — | haak → job `text` | na de job | `test_officepdf_text` |
| OCR klaar (X1) | — | `ocr` gevuld (trigger werkt FTS bij) | na de job | `test_ocr_searchable` |
| persoon benoemd of weg | — | — | groep en filter meteen | `test_person_now` |

### 3.10 De update en terug, voor Jonas

**Bijwerken naar 0.8.0** (hetzelfde commando als altijd, met de branch, J3):

```sh
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/claude/stappenplan-uitwerken-lu4uv3/infra/single/install.sh | PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 bash
```

Zonder `PLATFORM_BRANCH` haalt het script de code van `main` (`install.sh:21`) [code]. Zo staat
het ook in `infra/single/README.md`. Staat 0.8.0 later op `main`, dan vervalt dat deel.

Wat het script doet, in deze volgorde:

1. De gewone stappen tot en met de instellingen (ongewijzigd); `PLAATSEN=aan` erbij als hij
   ontbreekt.
2. Het nieuwe image bouwen, nog zonder het te starten (`compose build platform`; nu doet het script
   `up -d --build` in één keer, `install.sh:552` [code]). De stap `geo` haalt de plaatsen met
   sha256; lukt dat niet, dan zegt het script "Plaatsnamen: niet geladen" en gaat door.
3. **Nieuw:** een reservekopie van de database, met het nieuwe image, terwijl de oude app nog
   draait: `compose run --rm --no-deps platform python -m app.cli db-backup` (`VACUUM INTO` naar
   `/data/backup/`, op de LXC `/tank/platform/backup/`; de laatste 2 blijven). Lukt dat niet, dan
   stopt het script hier: er is dan nog niets veranderd.
4. De app start (`up -d`), de migratie 0009 draait (seconden), de app is gezond (`wait_health`).
5. Media, dan office (zoals nu, B47).
6. Het script zegt: "Zoeken op naam werkt meteen. De inhoud wordt nu op de achtergrond gelezen;
   volg het op /beheer → Zoeken en foto's."

**Wat er daarna gebeurt** (op een CPU als in de sandbox; `bench --los` meet het op de LXC):

| Stap | Werk voor 100.000 bestanden (20.000 documenten, 55.000 foto's) | Tijd op één core |
|---|---|---|
| tekst uit documenten | 200.000 pagina's × 2,2 ms, plus Office | ±10 min [gemeten per stuk, H8] |
| index via de triggers | 235 miljoen tekens | ±30 s [gemeten] |
| GPS en camera (photometa v2) | 55.000 × 0,24 ms, plus bestanden openen | 1–5 min [niet getest: het openen] |
| plaatsnamen | 55.000 × 0,06 ms | 3 s [gemeten] |
| pHash uit het voorbeeld | 55.000 × ±1,5 ms | ±1,5 min [gemeten per stuk] |
| OCR (X1, alleen na ja) | per gescande pagina 2–3 s | 36–55 min per 1.000 pagina's |

**Terug naar 0.7.0** (als 0.8.0 niet goed is). `dc` is de functie uit `infra/single/README.md`
("Handige commando's"): `docker compose` met precies de bestanden die het script gebruikt.

```bash
# 1. kijken of 0.7.0 er is, en het script in een bestand, vóór je iets stopt
git -C /opt/platform/src fetch -q origin
git -C /opt/platform/src cat-file -e '1130e1bbe86d9e4242c0effb588d7671599f5d8a^{commit}' && echo "goed: 0.7.0 is er"
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/claude/stappenplan-uitwerken-lu4uv3/infra/single/install.sh -o /root/platform-install.sh && echo "goed: het script is er"
# 2. stoppen, zodat niets meer naar de nieuwe tabellen schrijft
dc stop platform media
# 3. het schema terug, met het image van nu (vraagt om "ja")
dc run --rm --no-deps platform python -m app.cli db-downgrade 0008
# 4. 0.7.0 installeren
PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 PLATFORM_SRC_REF=1130e1bbe86d9e4242c0effb588d7671599f5d8a bash < /root/platform-install.sh
```

- De downgrade moet met het image van 0.8.0 (dat kent 0009). Daarom eerst stoppen, dan
  `db-downgrade`, dan pas het oude script. Zeggen de twee regels van stap 1 niet allebei "goed",
  dan stop je daar (zoals in het runbook van media).
- De downgrade gooit de index, de plekken, de hashes, de personen en de regels van albums weg.
  Bestanden, delingen, albums en alles van fase 1–5 blijven.
- Lukt de downgrade niet, dan is er de reservekopie van stap 3: stoppen, het bestand terugzetten,
  het oude script.
- **Weer vooruit:** het script zonder `PLATFORM_SRC_REF`. De index vult zich opnieuw (zie de
  tabel). Personen die je met de hand benoemde, zijn weg: dat staat in het runbook en in de
  bevestiging van `db-downgrade`.
- `1130e1b` is de laatste commit vóór Z0; de app-code is gelijk aan `92a0d04` (CI groen op alle
  jobs). Komen er vóór Z0 nog commits, dan zet Z7 hier de laatste. Bij de integratie komt er een
  tag `v0.7.0` op. De CI-job `infra` doet deze weg terug echt (`test_install_search.sh`).

---

## 4. Tests en meten

### 4.0 Klaar als → bewijs

| Klaar als (stappenplan) | Bewijs | Job |
|---|---|---|
| Een zoekopdracht over 100.000 bestanden antwoordt binnen 200 ms | `api/tests/search/test_perf_100k.py` en `e2e/perf/zoek_100k.py` (§4.4): p95 ≤ 200 ms en max ≤ 300 ms per toegangsprofiel, via HTTP, op SQLite 3.46.1; op de LXC `bench --los` | `zoek-perf` (hard) |
| OCR-tekst is doorzoekbaar | `e2e/test_zoek_ocr.py` (marker `ocr`, echte tesseract): een PDF zonder tekstlaag en een foto van een bon → zoeken op een woord dat alleen in het beeld staat → snippet → de viewer markeert het | `ocr` (hard). In het image pas na ja op Vraag 1. |
| Gezichten zijn te benoemen en te zoeken | `e2e/test_zoek_personen.py`: een persoon met kader benoemen, zoeken op de naam, filteren; `test_people_privacy.py`. Automatisch vinden: X2, na ja op Vraag 2. | `e2e`, `api` (hard) |
| Full-text voor pdf, docx, xlsx, pptx, txt, md | `api/tests/extract/test_*.py` per formaat; `e2e/test_zoek_z2.py` | `api`, `e2e` |
| EXIF en GPS, kaart, offline plaatsnamen | `tests/geo/*`; `e2e/test_zoek_kaart.py` (telt verzoeken naar andere hosts: 0) | `api`, `e2e` |
| Duplicaten en bijna-duplicaten | `tests/dupes/*`, `e2e/test_zoek_dubbel.py` | `api`, `e2e` |
| Herinneringen en slimme albums | `tests/photos/test_memories.py`, `tests/albums/test_album_rules.py` | `api`, `e2e` |
| Filters: type, datum, persoon, locatie, tag, bibliotheek | `tests/search/test_search_filters.py`: elk filter en elke combinatie van twee, tegen de referentie | `api` |
| Alles met het toetsenbord (sinds fase 3) | e2e met alleen het toetsenbord per scherm; `docs/fase-6/toetsenbord-z*.md` | `e2e` |

Eerlijk: zonder ja op Vraag 1 haalt het image "OCR-tekst is doorzoekbaar" niet; de code is
bewezen in CI. Zonder ja op Vraag 2 is "gezichten" half: benoemen en zoeken wel, vinden niet.
Zo staat het in de README en op `/beheer`.

### 4.1 De lagen

| Laag | Waar | Draait met | Wat |
|---|---|---|---|
| Puur | `api/tests/{search,extract,geo,dupes,photos,albums,people}/` | `uv run pytest` | `parse`, snippets, pHash, plaatsen, de GPS-lezers, filters, regels, herinneringen |
| API | `api/tests/test_*.py` | httpx tegen de app (ASGI) | routes, toegang, events, foutcodes, wissen |
| Migratie | `tests/test_migration_0009.py` | 3.53.1 en in het image 3.46.1 | heen, terug, zonder FTS5 |
| Worker | `api/tests/media/test_ocr_*.py` | marker `ocr` | X1 met echte tesseract |
| e2e | `e2e/test_zoek_*.py` | Chromium (Playwright van buiten, B29), marker `zoek` | de schermen, alleen met het toetsenbord |
| Web | `web/src/**/*.test.ts(x)` | vitest | `query.ts`, combobox, projectie, clusters, snippets als tekst |
| Prestaties | marker `zoek_perf`, `e2e/perf/zoek_*.py` | §4.4–4.5 | tijden, backfill, CPU naast de rest |
| Installatie | `infra/single/test_install_search.sh` | bash | back-up, plaatsen, terugrollen |

**Wachters die altijd draaien** (in `api`):

- `test_search_triggers.py`: de triggers in `sqlite_master` zijn gelijk aan `schema.TRIGGERS`;
- `test_search_no_raw.py`: geen `MATCH` naast een f-string of `%`; alleen `parse()` maakt de
  uitdrukking (AST);
- `test_no_replace.py`: geen `INSERT OR REPLACE` of `.merge()` op `node` en `blob_text`;
- `test_no_batch_alter_node.py`: geen `batch_alter_table("node")` in een migratie na 0009;
- `test_search_logs.py`: zoek op `geheimwoord7`; daarna staat dat woord nergens in de logs, ook
  niet in de toegangslog, en niet bij `search_access_mismatch`;
- `test_geo_never_public.py`: alle routes onder `/api/v1/public` en de publieke albumpagina,
  met een foto met GPS: geen `lat`, `lon`, `place` of `geo` in enig antwoord (JSON doorlopen),
  en niet in `preview_meta`;
- `test_no_mismatch.py`: een fixture die elke test laat falen als de log
  `search_access_mismatch` bevat;
- `test_error_catalog.py` (bestaat): elke nieuwe foutcode in NL en EN.

### 4.2 De toegangsmatrix

Eén vaste wereld (`api/tests/search/wereld.py`), gedeeld door `test_search_access.py`,
`tests/geo/test_geo_access.py`, `tests/people/test_people_privacy.py` en de e2e
`test_zoek_toegang.py` (B).

- **Eva** (eigenaar), locatie `mij`. Haar map `Werk` is gedeeld met Bram, met groep `Team` en
  met gast Gijs. Een submap van `Werk` is ook apart gedeeld (dubbel pad). Een publieke link op
  `Werk/Foto's`.
- **Bram** (deling lezen), **Cas** (lid van `Team`), **Gijs** (gast), **Dirk** (niets gedeeld),
  **Ada** (beheerder, niets gedeeld), **Fien** (deling verlopen), **Hans** (deling ingetrokken),
  **Iris** (gewist).
- In `Werk`: een document met `zebrakwarts` in de tekst; een scan met `ocrwoordx` alleen in het
  beeld (X1); een foto met GPS (Utrecht) en persoon "Anna"; een kopie en een verkleinde kopie van
  die foto; een bestand in de prullenbak; een map die uit `Werk` naar Eva's eigen map is
  verplaatst.

**Wat ziet wie** (ja = zichtbaar; nee = afwezig, niet "geweigerd"):

| Wie | Treffer en snippet | Suggestie | Plaats, kaart, `/location` | Download | Personen | Duplicaten, herinneringen |
|---|---|---|---|---|---|---|
| Eva | ja | ja | ja | origineel | ja | ja |
| Bram, Cas, Gijs | ja, binnen `Werk` | namen binnen `Werk`; geen personen of plaatsen | nee (`404`); "Utrecht" vindt de foto niet | origineel (zoals 0.7.0; Vraag 4) | nee (`[]`, `404`) | nee (alleen eigen) |
| Dirk, Ada | nee | nee | nee | `404` | nee | nee |
| Fien, Hans | nee, in hetzelfde verzoek na verlopen of intrekken | nee | nee | `404` | nee | nee |
| Iris (gewist) | — | — | — | — | haar personen weg (cascade, 0 rijen) | — |
| Bram: bestand in de prullenbak | nee | nee | nee | `404` | nee | nee |
| Bram: de verplaatste map | nee, meteen na het verplaatsen | nee | nee | `404` | nee | nee |
| Publieke link | geen zoekroute (`404`) | alleen het filter op namen in de browser (zoals nu) | nooit | origineel (zoals nu) | nooit | nooit |

Per cel een test. Daarnaast:

- **Geen kanaal via de volgorde** (B). Twee werelden die alleen verschillen in een bestand dat
  Bram niet ziet: Bram krijgt dezelfde set treffers en dezelfde snippets, zonder score of totaal.
  bm25 telt wel over alles en kan de volgorde van bijna gelijke treffers een beetje verschuiven;
  de test eist alleen gelijke sets. Het restrisico staat bij R7.
- **Slim album van Eva met de regel `place=Utrecht`**, gedeeld met Bram: Bram ziet de foto's (dat
  is delen), maar geen plaats en geen regel.
- **Eva zet locatie op `uit`**: `/location` geeft `404`, de kaart is leeg, en na de job zijn er
  0 rijen in `blob_geo` voor haar blobs.

### 4.3 Wat zoeken betekent: de referentie, de vragenlijst en churn

- **De referentie is de definitie** (B). Elke vraag draait op de boom van 2.000 nodes door de app
  en door `referentie.py`. De sets moeten gelijk zijn. De referentie vouwt elk woord van de tekst
  en de vraag met `fold()`, en kent het voorvoegsel op het laatste woord.
- **De vragenlijst** (`api/tests/search/vragen.tsv`, 200 vragen): hele woorden, voorvoegsels,
  stukken van namen, korte woorden, diakrieten, ß, cijfers, frases, lege uitkomsten, vragen met
  operators (`AND`, `NEAR`, `*`, `"`, `:`) die gewone tekst moeten blijven.
- **De volgorde** wordt apart getest: een treffer op de naam staat boven een treffer in de tekst
  met hetzelfde woord; een treffer op een heel woord boven een stuk van een naam; bij gelijke
  score op `num`.
- **Churn** (B): 2.000 willekeurige wijzigingen met een vaste seed (upload, kopie, hernoemen,
  verplaatsen, prullenbak in en uit, versie, tekst erbij, definitief wissen, gebruiker wissen).
  Na elke 100: de index tegen een herbouw, en 42 vragen tegen de referentie. 0 verschillen, op
  3.53.1 (`api`) en 3.46.1 (`zoek-perf`).

### 4.4 De benchmark: 100.000 bestanden binnen 200 ms

**Opzet** (B; `api/tests/search/test_perf_100k.py`, marker `zoek_perf`; de HTTP-kant in
`e2e/perf/zoek_100k.py`):

1. Een gemigreerde database met de boom van §3.6 (`--n 100000 --seed 6`).
2. De app start zoals in productie (`python -m app.serve`, WAL, `busy_timeout` 5000), in een
   container van `python:3.12-slim`: dezelfde SQLite 3.46.1 als het image. De test controleert
   `sqlite_version()`.
3. Zes toegangsprofielen: eigenaar (1 wortel); ontvanger met 5 en met 20 delingen; groepslid;
   gast (2 delingen); "alles" (30 hoofdmappen, het ergste geval). Plus het profiel "groot" (200
   documenten van 1 MB) voor de snippets.
4. 20 vragen om op te warmen, dan de 200 van §4.3, 3 rondes, één voor één, via HTTP met een
   ingelogde sessie, vanuit een ander proces.
5. Gemeten bij de client. De stappen uit `Server-Timing` (`roots`, `fts`, `names`, `check`,
   `snip`) alleen met `PLATFORM_SEARCH_TIMING=1`.

**Poorten per profiel** (over 600 vragen):

| Wat | Poort | Job |
|---|---|---|
| `GET /search` | p95 ≤ 200 ms, max ≤ 300 ms | `zoek-perf` (hard) |
| volgende pagina's (`offset` 30–990) | p95 ≤ 200 ms | `zoek-perf` |
| alleen filters, zonder vraag | p95 ≤ 150 ms | `zoek-perf` |
| `GET /search/suggest` | p95 ≤ 50 ms | `zoek-perf` |
| `/photos/map` (55.000 met GPS) | p95 ≤ 150 ms | `zoek-perf` |
| `/duplicates` (55.000 foto's) | p95 ≤ 300 ms | `zoek-perf` |
| `/photos/memories` | p95 ≤ 50 ms | `zoek-perf` |
| 4 clients tegelijk | rapport | `zoek-perf` |
| eerste vraag na een herstart | rapport (doel ≤ 300 ms) | `zoek-perf` |

**Het vertrekpunt** [gemeten in de proef van B, SQL met snippets, zonder HTTP, `python:3.12-slim`]:

| Profiel | Zichtbare nodes | p50 | p95 | max |
|---|---|---|---|---|
| 1 hoofdmap | 6.369 | 30,3 | 45,3 | 76,8 |
| 5 delingen | 6.541 | 32,1 | 46,5 | 71,4 |
| 20 delingen | 6.946 | 36,1 | 54,0 | 76,1 |
| alles (30 hoofdmappen) | 105.931 | 37,6 | 79,8 | 136,0 |

HTTP, JSON, inloggen en de tweede controle komen erbij [niet gemeten; schatting +5–15 ms]. Is
de marge kleiner dan verwacht, dan eerst `prefix='3'` (`ver*` 59 → 28 ms, +62 MB), dan een
`limit` van 20.

**Waarom hard in CI.** Het ergste p95 is 80 ms; de poort is 200. Een runner die 2× trager is,
haalt het nog. Faalt de job twee keer in een week zonder wijziging in `app/search`, dan wordt hij
een rapport en verhuist de poort naar de integratie en de LXC. Dat staat als regel in
`docs/fase-6/e2e.md`.

**Op de LXC:** `bench --los` (Keuze 54) meet "eigenaar" en "alles" met dezelfde vragen. Haalt
de CPU van Jonas p95 ≤ 200 ms niet, dan zegt `/beheer` het, en gaat de tijdsgrens (Keuze 9) naar
120 ms met `partial`.

### 4.5 Meten: backfill en CPU naast OnlyOffice en media

**Snelheid van de backfill** (`e2e/perf/zoek_backfill.py`, elk 3 keer, één core, niets anders
actief):

| Stap | Invoer (gemaakt) | Poort per core | Verwachting |
|---|---|---|---|
| tekst | 2.000 documenten: 40% PDF (1–20 pagina's), 30% docx, 10% xlsx, 10% pptx, 10% txt, html, rtf | ≥ 1.500 per minuut | ±2.000 per minuut [H3, H8] |
| GPS, camera, pHash | 5.000 foto's | ≥ 5.000 per minuut | ±30.000 [H5] |
| index bijwerken | 20.000 teksten via de triggers | rapport | ±26 s [gemeten] |
| OCR (X1) | 100 gescande pagina's, A4, 300 dpi | ≥ 18 pagina's per minuut | 18–28 [H4] |

**CPU en geheugen naast OnlyOffice en media** (`e2e/perf/zoek_samen.py`, B). Drie stappen, elk
5 minuten:

1. **Zonder achtergrondwerk** (`paused`): de belastingtest van fase 5 (`app.cli media-loadtest
   --sessies 3 --duur 300 --extern`), 5 bewerkers in OnlyOffice die typen en opslaan, en 2
   zoekvragen per seconde. De nulmeting.
2. **Met achtergrondwerk, zoals standaard**: hetzelfde, terwijl `index` vol staat (5.000
   documenten, 5.000 foto's; met X1 ook 500 scans). `Defer` staat aan.
3. **Ergste geval**: `Defer` uit. Dan beschermen alleen `nice` en de idle IO-klasse.

**Poorten** (stap 2; stap 3 is een rapport):

| Wat | Poort |
|---|---|
| media | elke start ≤ 5 s, 0 keer haperen (de poorten van fase 5) |
| OnlyOffice | opslaan p95 ≤ 1,2 × de nulmeting; 0 verloren woorden |
| zoeken | p95 ≤ 200 ms |
| de app (`GET /files/root`) | p95 ≤ 200 ms |
| geheugen | geen container `OOMKilled`; `MemAvailable` ≥ 1,5 GiB |
| achtergrondwerk | loopt binnen 90 s na de laatste transcode weer |

**Waar:** in CI (`e2e-media-perf`) een korte versie als rapport (60 s, 1 sessie, zonder
OnlyOffice); in de integratie de hele test in de sandbox als poort (2 sessies media en 3
bewerkers, binnen 8 GB); op de LXC via het runbook `zoeken.md`.

### 4.6 CI

Geen nieuwe actions: alleen wat `ci.yml` al gebruikt.

| Job | Wanneer | Wat erbij komt | Soort |
|---|---|---|---|
| `api` (bestaat) | elke push | alle nieuwe tests, de wachters van §4.1, churn op 3.53.1 | hard |
| `zoek-perf` (nieuw) | elke push | in `python:3.12-slim`: migratie, plan-tests (S4), churn op 3.46.1, de boom van 100.000, §4.4 | hard |
| `ocr` (nieuw, X1) | elke push | `apt-get install tesseract-ocr tesseract-ocr-nld tesseract-ocr-eng tesseract-ocr-osd fonts-dejavu-core` op de runner; `pytest -m ocr`; `e2e -m ocr` | hard |
| `e2e` (bestaat) | elke push | marker `zoek` (Chromium) | hard |
| `media-image` (bestaat) | elke push | het image met de stap `geo` (sha256 van de plaatsen); met ja op Vraag 1 ook tesseract (+ ≤ 30 MB) | hard |
| `infra` (bestaat) | elke push | `test_install_search.sh` | hard |
| `web` (bestaat) | elke push | begrotingen, i18n streng, vitest; `world-110m.json` opnieuw maken en vergelijken | hard |
| `perf` (bestaat, rapport) | elke push | `e2e/perf/zoek_ui.py` | rapport |
| `e2e-media-perf` (bestaat, rapport) | elke push | `zoek_samen.py --kort` | rapport |

**Minuten.** ±10–15 minuten runner-tijd per push erbij; de jobs lopen naast elkaar.

### 4.7 Toetsenbord, toegankelijkheid en talen

- Elk nieuw scherm met alleen het toetsenbord in e2e, en een lijst
  `docs/fase-6/toetsenbord-z<n>.md` (zoals fase 5).
- De combobox volgt het ARIA-patroon: `role="combobox"`, `aria-expanded`, `aria-controls`,
  `aria-activedescendant`; de groepen zijn `role="group"` met een label.
- De kaart heeft een tekstalternatief: de lijst "Plaatsen" (Keuze 33). Een cluster is een knop met
  "Utrecht, 34 foto's".
- Een kader tekenen kan met het toetsenbord (Keuze 37).
- Alle tekst in NL en EN; i18n streng in CI; foutcodes in de catalogus.

---

## 5. Volgorde

Zoals fase 5: per golf bouwen in eigen worktrees (hooguit 3 tegelijk), dan samenvoegen in de
volgorde van de tabel, dan alle checks, dan een review en een fixer.

| Dagen | Wat | Samenvoegen |
|---|---|---|
| 1–3 | **Z0**: Z0-api, Z0-web en Z0-e2e naast elkaar; de spike S1–S9 op dag 1–2. Daarna Z0 samenvoegen. | Z0-api, Z0-web, Z0-e2e |
| 2 | Jonas krijgt de vragen als kaarten, en S9 om zijn CPU te meten. | — |
| 4–7 | **Golf 1**: Z1, Z2 en Z3 naast elkaar. | Z1, dan Z2, dan Z3 |
| 8–11 | **Golf 2**: Z4, Z5 en Z6 naast elkaar. | Z4, dan Z5, dan Z6 |
| 12–13(14) | **Golf 3**: Z7, en X1 als Jonas niet "nee" zei. | Z7, dan X1 |
| 14(15)–15(16) | **Integratie**: alle checks, de belastingtest (§4.5) als poort, metingen naar `docs/fase-6/metingen.md`, de README, 0.8.0, tag `v0.7.0`. | — |
| na ja op Vraag 2 | **X2**, ±5–6 dagen, als eigen golf met migratie 0010. | — |

**Wat er gebeurt als Jonas antwoordt:**

- **Vraag 1 ja** (op elk moment vóór de integratie): de regel in de Dockerfile komt in X1 of in
  de integratie. Na de integratie: een kleine 0.8.1. **Nee**: X1 valt weg; de knop "Tekst
  herkennen" en de rij `ocr` blijven uit.
- **Vraag 2 ja**: X2 na fase 6, of als Jonas het belangrijker vindt dan de slimme albums, in
  plaats van Z6's albumdeel (dat schuift dan).
- **Vraag 4 (b)**: ±2 dagen in golf 3 (een nieuw pakket naast Z7), of in fase 10.
- **Vraag 5 nee**: de stap `geo` gaat uit de Dockerfile; de kaart toont geen plaatsnamen.
- **Vraag 6 tegels**: niet in fase 6.

Niemand hoeft op een antwoord te wachten om verder te bouwen: elke vraag heeft een standaard.

### 5.1 Risico's

| # | Risico | Gevolg | Wat we doen |
|---|---|---|---|
| R1 | De CPU van Jonas is veel trager dan de sandbox | 200 ms niet gehaald op de LXC | S9 in week 1; de tijdsgrens met `partial`; `prefix='3'`; `limit` 20 |
| R2 | De planner kiest na `ANALYZE` of `PRAGMA optimize` een ander plan | seconden in plaats van ms (107 s in de proef) | `CROSS JOIN`; plan-tests met en zonder statistieken (S4) |
| R3 | Triggers maken grote schrijfacties traag (import, kopie van een grote map) | upload of import trager | S2 meet het; de import van fase 5 in de churn-test; batches blijven 500 |
| R4 | De index loopt uit de pas met de bron | verkeerde of ontbrekende treffers | wachters (§4.1), churn, de wekelijkse `search-check`, `search-rebuild` |
| R5 | Een latere migratie maakt `node` opnieuw | triggers weg, index bevriest | `test_search_triggers.py`, `test_no_batch_alter_node.py`, `search-triggers` |
| R6 | PDFium in de app-container leest bestanden van gebruikers | een fout in PDFium raakt de app | het subproces met grenzen (zoals B20 nu); later naar de worker als het image daar pypdfium2 krijgt |
| R7 | bm25 telt over alle gebruikers | de volgorde van bijna gelijke treffers zegt een heel klein beetje over andermans bestanden | geen score, geen totaal, geen schrappen; de test eist gelijke sets; het restrisico staat in de README |
| R8 | De database groeit (±370 MB per 100.000 bestanden) | schijf en back-up groter | `/beheer` toont de grootte; `db-backup` houdt er 2 |
| R9 | ß, ligaturen en andere vormen | gemiste treffers | twee vormen per woord (Keuze 5, S8); de referentie vouwt zelf |
| R10 | De initiële JS heeft nog 3,6 kB ruimte | het zoekveld past niet | het veld ≤ 0,8 kB; alles verder lazy; de suggesties eerst schrappen |
| R11 | Wissen van een grote gebruiker houdt de schrijver vast | andere schrijvers wachten | per 2.000 met commit (Keuze 11); `test_search_purge_batches.py` |
| R12 | De backfill vult de WAL en houdt de schrijver bezet | trage uploads tijdens de backfill | één blob per transactie; `test_backfill.py` (een upload wacht ≤ 1 s) |
| R13 | De plaatsen komen niet binnen bij de bouw | geen plaatsnamen | niet fataal; `/beheer` zegt het; `PLAATSEN=uit` |
| R14 | Tekst van 1 MB maakt snippets traag | `/search` boven 200 ms | 200.000 tekens en 40 ms per verzoek (Keuze 8); profiel "groot" in §4.4 |
| R15 | Jonas antwoordt niet | OCR en automatische gezichten niet in 0.8.0 | de kern is af zonder; X1 staat klaar; dat staat eerlijk in de README |
| R16 | De runner in CI is wisselend snel | `zoek-perf` faalt zonder reden | de regel van §4.4: twee keer in een week, dan rapport en poort in de integratie |

---

## 6. Verwerkte en afgewezen kritiek

Revisie 2 vult deze sectie.
