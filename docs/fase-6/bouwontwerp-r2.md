> Werkdocument: het bouwontwerp van fase 6 (zoeken en foto's), revisie 2. Revisie 2 is leidend.
> Revisie 1 staat in [bouwontwerp-r1.md](bouwontwerp-r1.md).
> Nog niet besloten. De vragen aan Jonas staan onder "Vragen voor Jonas". Tot hij antwoordt,
> bouwen we op de standaard die daar staat.

# Fase 6 (zoeken en foto's): bouwontwerp, revisie 2

Basis: branch `claude/stappenplan-uitwerken-lu4uv3` @ `1130e1b` (0.7.0, migraties 0001–0008).
De app-code is gelijk aan `92a0d04`, waar CI op alle 14 jobs groen was; de commits daarna
veranderen alleen e2e, CI en documenten.

Revisie 2 verwerkt de kritiek op revisie 1: 18 punten over veiligheid en privacy (S1–S18) en 17
over haalbaarheid en bouw (C1–C17). Ik heb elk punt nagelopen in de code van `1130e1b`, in de
notities en, waar het kon, met een meting in de sandbox (V1–V10 hieronder; de commando's staan in
[haalbaarheid.md](haalbaarheid.md)). Geen punt bleek onjuist. Wat ik niet overnam, zijn delen van
voorgestelde fixes. §6 zegt per punt wat er veranderde, en waarom een deel niet.

De grootste veranderingen:

- **De SQL van de index klopt nu** (Keuze 3, §3.1). De view `search_src` had geen kolom
  `blob_id`, dus de eerste schrijfactie naar `blob_text` faalde. Upserts en `INSERT OR IGNORE`
  maken een external-content-index kapot; die zijn nu verboden, met een wachter (C1, C2, V1).
- **De tijdsgrens geeft altijd iets terug** (Keuze 9). Een gerangschikte vraag die stopt, geeft
  0 rijen. Nu stopt `interrupt()` vanuit een timer de vraag, en dan volgen een snelle ronde zonder
  rangorde en, als die ook te traag is, de namen (S4, C4, V3).
- **Foto-gegevens horen bij een bestand van een eigenaar, niet bij een blob** (Keuze 25a). Plek,
  personen en tekst uit foto's staan per node, alleen voor de eigenaar en alleen met zijn
  toestemming. Elke fotofunctie gebruikt dezelfde regel "eigen en levend" (S8, S9, S17).
- **Een slim album deelt nieuwe foto's niet vanzelf** (Keuze 42). Wie het album deelt, kiest dat
  apart (S2).
- **De waarschuwing bij delen telt goed**: ook voor een album, een los bestand, een eigenaar met
  locatie "uit" en video's (Keuze 30, S3).
- **Tekst lezen en OCR draaien in de mediaworker** als media aan staat, in batches, met een
  genormaliseerd grijs beeld voor tesseract (Keuze 17, 22; S6, S7, C3, C10).
- **Bijna-duplicaten met een tweede controle** (Keuze 35, 36). Vlakke beelden krijgen geen hash,
  "bijna zeker gelijk" vraagt een tweede, onafhankelijke controle, en een groep is een kliek
  (S1, V5).
- **Het land komt uit polygonen**, niet uit de dichtstbijzijnde plaats (Keuze 29, S10, V6).
- **Grenzen voor de hele app**, een herbouw in stukken en stateless toegangsfilters (Keuze 12–14;
  S12, S13, S18, V4).
- **De planning is eerlijk**: ±26 werkdagen in golven van twee, met tijd voor review. Dat past
  niet in de 3 weken van het stappenplan; daarom Vraag 7 (C8).
- **De weg terug werkt met elk image** (Keuze 52, 53; C7, C13).

Revisie 1 voegde twee ontwerpen samen. Ontwerp A ("zelf bouwen, geen nieuwe afhankelijkheden,
eerlijk over wat er zonder Jonas niet lukt") is de basis voor de vorm: de pakketten, de vragen,
de uitrol en het terugrollen. Ontwerp B ("de hele fase, goed afgeschermd") is de basis voor de
zoekmachine zelf: het schema, de triggers, het toegangsfilter en de tests. Waar ze elkaar
tegenspraken, is het nagemeten of in de code nagekeken (J1–J14 hieronder).

Bronnen (scratch, niet in het repo):

- `fase6/tech.md`: onderzoek naar FTS5, OCR, EXIF, GPS, plaatsnamen, kaarten, gezichten en
  beeldmodellen, met licenties en een bron per bewering;
- `fase6/feasibility.md`: de proef in de sandbox (H1–H8). De getallen staan ook in
  [haalbaarheid.md](haalbaarheid.md);
- `fase6/code-map.md`: waar fase 6 in de code inhaakt, met regelnummers;
- `fase6/judging.md`: het oordeel over A en B;
- `fase6/critic/`, `fase6/critic-sec/` en `fase6/verify/`: de scripts van de critici en van
  deze revisie (V1–V10).

Bij een bewering staat waar hij vandaan komt:

- **[gemeten]**: gedraaid in de sandbox;
- **[doc]**: officiële documentatie, een licentie of broncode;
- **[code]**: zo staat het in het repo;
- **[niet getest]**: niet bevestigd. Daar ontwerpen we voorzichtig. De spike (§3.7) of een test
  (§4) beslist.

Afkortingen:

- **app**: ons platform (API en web-app in één container);
- **worker**: de mediaworker van fase 5 (uid 10002, geen netwerk, geen `/data`, bestanden via een
  fd);
- **FTS**: de full-text-index van SQLite (FTS5);
- **index**: `fts_node` en `fts_name` samen;
- **backfill**: het achteraf vullen van de index en de fototabellen voor bestanden die er al zijn;
- **wortel**: een map waar een gebruiker toegang toe heeft: zijn eigen hoofdmap, of een gedeelde
  map;
- **pad** (`mpath`): de nummers van alle ouders van een node, zoals `/1/7/42/`;
- **eigen en levend** (`own_live`): een bestand van mij dat niet in de prullenbak ligt, ook niet
  via een map erboven (Keuze 25a);
- **pHash**: een perceptuele hash van 64 bits van een foto;
- **X-pakket**: een pakket dat op een antwoord van Jonas wacht.

**Nagemeten en nagekeken bij revisie 2** (de commando's in [haalbaarheid.md](haalbaarheid.md)):

- **V1. De SQL van §3.1 van revisie 1** [gemeten, SQLite 3.53.1 en 3.46.1]. Letterlijk gedraaid:
  `insert blob_text FAILED: no such column: blob_id`. Met `n.blob_id AS blob_id` in de view en
  3.000 willekeurige wijzigingen met gewone `INSERT`, `UPDATE` en `DELETE`: `integrity-check` ok
  op beide versies, paden en `dead` kloppen. Dezelfde reeks met `INSERT OR IGNORE` of met
  `INSERT … ON CONFLICT DO UPDATE` op `blob_text`: `database disk image is malformed` op beide
  versies (C1, C2).
- **V2. Een renderproces starten kost tijd** [gemeten]. `python -I` met `import app.render`:
  112–115 ms CPU per proces; een kaal `python -I`: 24–33 ms (C3).
- **V3. De tijdsgrens** [gemeten, 200.000 documenten, 3.53.1 en 3.46.1]. Een progress-handler
  heeft bij `"ver"*` gaten van 144–201 ms tussen twee aanroepen, bij elke 1.000 én elke 100
  stappen: het werk zit in FTS5 zelf. `interrupt()` vanuit een timer van 20 ms stopt dezelfde
  vraag na 21–26 ms. Een gerangschikte vraag die stopt, geeft 0 rijen. Dezelfde vraag zonder
  `ORDER BY` op `"de"` geeft 31 rijen in 0,2 ms. Een `interrupt()` als er geen vraag loopt, doet
  niets met de volgende (S4, C4).
- **V4. Herbouwen in stukken** [gemeten, 200.000 rijen, 78 MB]. `delete-all`: 17 ms op 3.53.1,
  250 ms op 3.46.1. Daarna opnieuw vullen per 2.000: het langste stuk 101 ms (3.53.1) en 142 ms
  (3.46.1), samen 4,6 en 6,0 s; `integrity-check` ok (S13).
- **V5. Bijna-duplicaten** [gemeten, beelden gemaakt met Pillow]. 150 brieven op één sjabloon:
  1.076 verschillende paren op pHash-afstand ≤ 6. 150 bijna egale foto's: alle paren op afstand 0.
  De spreiding van 32 × 32 grijs: egaal 0,0; foto's ≥ 6,5; brieven ≥ 23. De gemiddelde afwijking
  van 64 × 64 grijs (MAD64): echte varianten (half zo groot, JPEG q35) ≤ 2,18; verschillende
  brieven op hetzelfde sjabloon ≥ 3,94. Bijsnijden en draaien geven pHash p95 18–22 (S1).
- **V6. Het land bij een grens** [gemeten]. Met de dichtstbijzijnde plaats uit cities1000 krijgt
  4,1% van 4.000 punten op land rond NL, BE, DE, LU en FR het verkeerde land; 92% van 1.000 punten
  op de Noordzee krijgt toch een land. Punt-in-polygoon op Natural Earth 10m admin-0 (v5.1.2):
  0,81 ms per punt in puur Python, 3,4 MB als float32 met zlib. Natural Earth 10m heeft de
  enclaves van Baarle niet (S10).
- **V7. De OCR-keten per pagina** [gemeten, tesseract 5.3.4, één core]. Een A4-pagina op 300 dpi
  grijs renderen: 170–210 ms. Als PGM: 6–20 ms; als PNG: 154–174 ms (bij de critici op een
  drukke pagina 1,34 s). OCR `nld+eng`: 1,9–2,1 s (H4: 2,1–3,3 s). OSD: 2,0–2,1 s. tesseract
  leest PGM van stdin (C10).
- **V8. Testen op SQLite 3.46.1 in de sandbox** [gemeten]. `docker run --network none` met
  `python:3.12-slim` en de venv van de host alleen-lezen: Python 3.12.16, SQLite 3.46.1,
  pytest 9.1.1, FTS5 met `trigram`, en `json_each` (C16, S18).
- **V9. In de code nagekeken** [code]. Een album deelt elk item, ook via een publieke link als
  origineel (`access.py:150–172`, `public.py:184–231` en `:316–334`). Een album heeft geen kinderen
  in de boom (B23). Een kopie krijgt de eigenaar van de doelmap (`files.py:1394`). De tijdlijn en
  albums eisen al "levend" (`photos.py:153–162`, `albums.py:122–133`). De worker draait hetzelfde
  image met dezelfde `/opt/venv`, dus ook pypdfium2 (`compose.media.yaml:27`, `Dockerfile`). Elke
  ffmpeg-opdracht heeft `-max_pixels`; `nfo` draait in de worker zelf, zonder kind. Pagina's van
  de viewer gaan door een limiter van 2 processen en een cache (`viewer.py:11–15`, `:219–222`).
  Eén proces voor http en https (`serve.py`). `SECRET_PARAMS` kent `q`, `bbox` en `place` niet
  (`main.py:89–91`). `busy_timeout` is 5 s (`db.py:32`). Pillow 13 leest XMP alleen met
  defusedxml, en dat zit er niet in; `office/reader.py` heeft een geharde `sax()`. 0.7.0 kent
  `app.cli db-backup` niet. `compose.yaml` geeft geen build-args door. `guard.BINARIES` is
  `("ffmpeg", "ffprobe")`. De paden `web/src/ShareDialog.tsx` en `web/scripts/budget.mjs` (C9).
- **V10. De SQL van §3.1 van revisie 2** [gemeten, SQLite 3.45.1, 3.46.1 en 3.53.1]. De view
  met `blob_id`, ß naar "ss", `photo_ocr` per node, de triggers met de cursor van de herbouw, en
  het wissen van foto-rijen bij een andere eigenaar of nieuwe inhoud. 3.000 willekeurige wijzigingen
  met alleen gewone schrijfacties, dan een herbouw in stukken met 20 wijzigingen na elk stuk, dan
  nog 1.000: `integrity-check` van beide FTS-tabellen ok na elke fase, 0 foto-rijen met een andere
  eigenaar dan de node, geen fout. De virtuele kolom `node.ftype` met een partiële index: toevoegen
  met `ALTER TABLE`, het plan gebruikt de index, en weghalen werkt (C1, C2, C6, S13).

**Nagemeten en nagekeken bij revisie 1:**

- **J1. ß en het vouwen van de vraag** [gemeten, SQLite 3.45.1]. De tokenizer `unicode61
  remove_diacritics 2` maakt van "Straße" het token `straße`. Onze `fold()` (`app/text.py`) maakt
  er `strasse` van. "Één", "café", "İstanbul", "ﬁets" en "ĳsje" werken in beide vormen. Revisie 2
  maakt van ß en ẞ "ss" in alles wat in FTS gaat, en in de vraag (Keuze 5, 21). Dan zijn de twee
  vormen gelijk.
- **J2. Brede woorden schrappen is fout.** In het zaad van de proef staat "factuur" in 58% van de
  documenten. Een regel "schrap woorden boven 25%" maakt van "factuur kpn" alleen "kpn". We
  schrappen niets.
- **J3. Het installatiecommando** gebruikt de branch, niet `main`: 0.7.0 staat nog niet op `main`
  (`infra/single/README.md`) [code]. Het script valt zonder `PLATFORM_BRANCH` terug op `main`
  (`install.sh:21`), dus die variabele hoort in het commando (§3.10).
- **J4. B20 belooft een tekstlaag in de PDF-viewer in fase 6.** Die zit in dit ontwerp (Keuze 18).
- **J5. Terugrollen** gaat naar de laatste commit van 0.7.0 vóór Z0 (nu `1130e1b`, app-code gelijk
  aan `92a0d04`). Bij de integratie komt daar een tag `v0.7.0` op.
- **J6. Het voorbeeld van 256 px is al gedraaid volgens EXIF** (`render.py:357` en `:370`)
  [code]. De pHash daarop heeft geen eigen draaiing nodig.
- **J7. GPS uit downloads halen** hoort bij de bewerking "metadata en GPS-locatie weghalen" van
  fase 10. In 0.7.0 gaat de originele foto al met GPS mee bij een download. Fase 6 verandert daar
  niets aan, en waarschuwt (Keuze 30, Vraag 4).
- **J8. Geen nieuwe afhankelijkheid zonder Jonas.** B19 is besloten. Tesseract, onnxruntime,
  numpy en de modellen zijn daarom vragen.
- **J9. De toets `/`** opent op Bestanden al het naamfilter (fase 3). Die blijft. Het globale
  zoeken krijgt Ctrl/Cmd+K en een veld in de bovenbalk.
- **J10. Een slim album mag de foto's die je er met de hand in zette niet weggooien.** Daarom
  `album_item.source` en `album_skip` (Keuze 42).
- **J11. Toegang via het pad, niet via een lijst van mappen.** Een lijst van levende mappen per
  verzoek groeit met het aantal mappen (p95 70 ms, max 113 ms bij 5.741 mappen). Het pad groeit
  met het aantal wortels: p95 45–54 ms tot 20 wortels, en 80 ms voor "alles" [gemeten, 3.46.1].
- **J12. De volgorde van de join ligt vast.** Met een gewone JOIN kan de planner na `ANALYZE`
  vanaf `node` beginnen; in de proef kostte dat 107 s. Dus `CROSS JOIN` en plan-tests.
- **J13. Grote wissen in stukken.** Een gebruiker met 10.911 nodes wissen kostte met de triggers
  3,06 s in één transactie [gemeten]. Definitief wissen gaat per 2.000 nodes met een commit.
- **J14. Foutcodes** volgen de code: Engels, snake_case, met het gebied ervoor [code].

**Uit de proef, en nog steeds de basis** (H1–H8, ook in [haalbaarheid.md](haalbaarheid.md)):

- H2: zoeken nu (`LIKE` in een recursieve query) kost 590–670 ms bij 101.000 nodes. FTS5 met een
  pad haalt het: zie J11. Een trigram-index op namen: 2,3 ms. Een prefix-index (`prefix='3'`)
  maakt `ver*` 59 → 28 ms en kost 62 MB.
- H2: SQLite in het image is 3.46.1 (Debian 13), op de ontwikkelmachine 3.53.1.
- H3: tekst uit een PDF-pagina met pypdfium2: 2,2 ms. Office-bestanden: 1–70 ms.
- H4: tesseract kost 2,1–3,3 s per A4-pagina op één core; 7° scheef breekt de herkenning.
- H5: EXIF en GPS lezen: 0,24 ms per foto. pHash: 0,92 ms; zoeken met 4 × 16 bits: 0,6 ms. De
  beelden waren synthetisch: V5 laat zien wat dat mist.
- H6: GeoNames cities1000 in een R*Tree: 15 MB, 0,057 ms per foto.
- H7: YuNet (MIT) en SFace (Apache-2.0) via onnxruntime: 31 ms per foto en 29 ms per gezicht.
  InsightFace mag niet (gewichten alleen voor niet-commercieel onderzoek). CLIP is Engelstalig.
- H8: de backfill van 100.000 bestanden zonder OCR kost 2–3,5 core-uur. V2 voegt daar de start
  van de processen aan toe (§3.10).

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
| B18 en B23: een album en een publieke link geven elk item als origineel, met GPS (V9). | Een slim album deelt nieuwe foto's niet vanzelf (Keuze 42). De waarschuwing bij delen telt ook albums (Keuze 30). |
| B20: de PDF-viewer krijgt in fase 6 een tekstlaag. | De woorden met hun plek komen uit dezelfde render als het beeld van de pagina (Keuze 18). |
| B22: GPS lezen we in fase 3 niet, "dat hoort bij fase 6". | GPS wordt nu bewust weggegooid (`render_meta.py:21–22`, `media/worker/ops/meta.py:32`, `previews.SECRET_META:69`, `photodate.split_capture`) [code]. Het gaat open, maar alleen naar een eigen tabel per bestand en eigenaar, nooit naar `preview_meta`. |
| B40: onvertrouwde bestanden lezen hoort in de worker, zonder netwerk en zonder `/data`. | Tekst lezen en OCR draaien in de worker als media aan staat (Keuze 17, 22). Alleen zonder media blijft het in het renderproces, zoals de voorbeelden van 0.7.0. |
| InsightFace: gewichten alleen voor niet-commercieel onderzoek [doc]. | Valt af. YuNet en SFace kunnen wel (X2). |
| CLIP is Engelstalig; SigLIP 2 is meertalig en Apache-2.0, maar groot (±200–400 MB) [doc]. | Objecten en semantisch zoeken komen niet in fase 6 (Vraag 3). |
| Zoeken nu: alleen op naam, 590–670 ms bij 101.000 nodes [gemeten]. | Haalt de 200 ms niet. Er komt een index. |
| SQLite in het image is 3.46.1 [gemeten]. | Elke meting en elke plan-test draait in `python:3.12-slim`, ook in de sandbox (V8). |
| Op de LXC draaien al OnlyOffice (≤ 2 GB) en de mediaworker (≤ 3 GB, `cpus: 3.0`) (B43, B47). | Indexeren wijkt voor transcodes. Laagste voorrang, eerlijk per eigenaar. |
| De app is één Python-proces met één event loop (`serve.py`). | Zoeken krijgt een grens voor de hele app, niet alleen per gebruiker (Keuze 13). |
| CPU en GPU van Jonas zijn onbekend (B5). | Alles werkt op de CPU. Een tijdsgrens per vraag beschermt de 200 ms (Keuze 9). `bench --los` meet zijn LXC in week 1. |
| Jonas gaf het startsein voor fase 6 (3 november 2026) zonder de kaarten van fase 5 te beantwoorden. | De standaarden van fase 5 blijven. Fase 6 verandert er niets aan. |
| Het stappenplan geeft fase 6 drie weken. | Met review per golf is het ±26 werkdagen. Dat is Vraag 7. |

### 0.2 De invalshoek

| | Principe | Wat het hier betekent |
|---|---|---|
| P1 | **Toegang eerst** | Een treffer verlaat de server pas na het toegangsfilter én een tweede controle per treffer. Een snippet maken we pas daarna. Zoeken kan alleen met een account; een publieke link zoekt nooit. |
| P2 | **Zelf bouwen met wat er is** | SQLite (FTS5, R*Tree), Pillow, pillow-heif, pypdfium2, mutagen, de eigen kantoorlezer (B21), ffmpeg in de worker, de standaardbibliotheek. Eigen code voor de rest. |
| P3 | **Elk extern ding is een vraag** | Met een standaard waarmee de bouw doorgaat. Wat er zonder niet kan, staat in X-pakketten. |
| P4 | **Eerlijk meten** | De 200 ms meten we via HTTP, in `python:3.12-slim` (SQLite 3.46.1), met het toegangsfilter, voor zes soorten gebruikers. De poort is de p95; de rest is een rapport. In CI en op de LXC van Jonas. |
| P5 | **De update breekt niets** | 0010 voegt kolommen, tabellen en triggers toe. Zoeken op naam werkt meteen; de inhoud vult zich. Een schakelaar zet het oude zoeken terug. Vóór de migratie een reservekopie die met elk image werkt. Terug naar 0.7.0 is getest. |
| P6 | **Privacy standaard dicht** | Plek, personen en tekst uit foto's horen bij een bestand van één eigenaar, met zijn toestemming (Keuze 25a). Nooit in `preview_meta`, nooit via een publieke link, nooit in een log. Een gedeeld album deelt alleen wat de eigenaar koos. Geen totalen of scores naar buiten. |
| P7 | **Achtergrondwerk wijkt, en is eerlijk** | Twee rijen, `nice` 19 en de idle IO-klasse, korte transacties, batches, om de beurt per eigenaar, en wachten als er ≥ 2 transcodes lopen of OnlyOffice start. |
| P8 | **Onvertrouwde bestanden in de zandbak** | Wat de app vanzelf leest (tekst, OCR), leest de worker. Een decoder krijgt een begrensd, genormaliseerd beeld, nooit de ruwe blob als het anders kan. |

### 0.3 Hoe een bestand vindbaar wordt, en hoe een vraag loopt

```
upload / kopie / hernoemen / verplaatsen / prullenbak / versie / import
        │
        ▼
  node (rij) ──triggers──► num, mpath, dead ──► fts_node (naam) + fts_name (trigram)
        │                                        direct, in dezelfde transactie
        │ blob_id (sha)
        ▼
  files.add_ref ──hook──► analysis(sha, 'text', eigenaar) ──► job index.text (rij "index")
        │                   batch van ≤ 16 blobs, om de beurt per eigenaar
        │                   media aan:  worker, opdracht `text` (kind: python -I -m app.render text)
        │                   media uit:  renderproces van de app (zoals 0.7.0), melding op /beheer
        │                   pypdfium2 / kantoorlezer (B21) / tekst / mutagen
        │                       ▼
        │                  blob_text (per sha) ──triggers──► fts_node (inhoud) van elke node met die sha
        │                       │
        │                       └─► (X1) PDF-scan: worker, opdracht `ocr` ──► blob_text.ocr + ocr_page
        │
        └─► photometa v2 (batch van 200, renderproces of worker)
                ├─► blob_meta (camera, lens, gps ja/nee: geen coördinaten)
                ├─► photo_geo (per node, alleen eigenaar met locatie "mij") ──► land en plaats
                └─► blob_hash (pHash, of "vlak") ──► kandidaten ──► tweede controle ──► dup_pair

  foto's van tekst (X1, eigenaar zette het aan) ──► worker `ocr` ──► photo_ocr (per node)
  tekstlaag in de viewer: dezelfde render als het beeld van de pagina, met cache en limiter

zoeken: GET /api/v1/search   (hooguit 2 vragen tegelijk in de app, 1 per gebruiker)
  1. de wortels van de gebruiker (eigen hoofdmap + delingen), met hun paden    (≤ 1 ms)
  2. ronde A: fts_node MATCH  CROSS JOIN node  WHERE kind <> 'album' AND dead = 0
       AND (pad in een wortel)  ORDER BY bm25, num  LIMIT offset + n + 1      (grens 120 ms)
     te laat → ronde B: dezelfde vraag zonder ORDER BY                          (grens 40 ms)
     te laat → ronde C: alleen namen (fts_name, trigram)                        (±3 ms)
  3. als er minder dan een pagina is: fts_name (trigram) voor stukken van namen
  4. tweede controle per treffer: access.resolve()                              (P1)
  5. pas dan: de snippets, in een thread, uit de eerste 20.000 tekens
  6. de groepen "personen" en "plaatsen" van de gebruiker zelf (eigen en levend)
```

### 0.4 Wat "klaar als" in dit ontwerp betekent

| Klaar als (stappenplan) | Kern (Z0–Z7, zonder vragen) | Wat er zonder Jonas niet lukt |
|---|---|---|
| Een zoekopdracht over 100.000 bestanden antwoordt binnen 200 ms. | Ja. Een harde poort op de p95 in CI via HTTP in `python:3.12-slim` (§4.4), en `bench --los` op de LXC. | Niets. Op de CPU van Jonas is het pas bewezen als `bench --los` daar slaagt. |
| OCR-tekst is doorzoekbaar. | Tekst uit PDF's met een tekstlaag, Office, tekstbestanden en (met OnlyOffice) oude .doc, .xls en .ppt is doorzoekbaar. Dat is geen OCR. | **OCR zelf.** Zonder tesseract (Vraag 1) is een scan alleen op naam te vinden. X1 is gebouwd en in CI getest met een echte tesseract; het image krijgt hem pas na "ja". |
| Gezichten zijn te benoemen en te zoeken. | Personen benoemen op een foto (met de hand, met of zonder kader), samenvoegen, verbergen, zoeken en filteren. | **Gezichten vinden.** Automatisch vinden vraagt numpy, onnxruntime en twee modellen (Vraag 2). Dat is X2. Zonder X2 is dit punt half gehaald. Zo staat het op `/beheer` en in de README. |

---

## 1. Keuzes

Elke keuze zegt wat, waarom, en waar het bewijs zit. "(A)" of "(B)" zegt uit welk ontwerp hij
komt; "(r2)" is van deze revisie, met de kritiek erbij.

### De zoekindex

1. **Eén index per node, in SQLite FTS5** (B). `fts_node` heeft vijf kolommen: `name` (de
   gevouwen naam, `node.search_name`), `extra` (de titel uit de mediabibliotheek), `body` (de
   tekst), `ocr` en `meta` (camera, en titel en auteur uit PDF en Office). Hij leest uit de view
   `search_src` (external content, `content_rowid='num'`), tokenizer `unicode61
   remove_diacritics 2`. Daarnaast `fts_name` met `trigram` op de naam, voor stukken van namen
   ("port" in "rapport").
   - Waarom niet tekst per blob en namen per node apart (A): één bm25 over alle kolommen is
     eenvoudiger en juister te rangschikken.
   - Een kopie staat twee keer in de index. In het zaad is dat 2% van de bestanden. Dat kost
     ruimte, en bij het schrijven tijd per kopie: de triggers schrijven de tekst voor elke node
     met die sha. Gemeten door de critici (C11): 1 MB tekst met 10 kopieën kost 648 ms per
     schrijfactie. Keuze 20 en 23 houden dat begrensd.
   - Ruimte per 100.000 bestanden (20.000 met tekst): index ±120 MB, opgeslagen tekst ±257 MB,
     naamindexen ±15 MB [gemeten, H8].
   - Albums staan niet in de index als treffer (`kind = 'album'`). Mappen wel: je vindt een map op
     naam.
2. **Een vast nummer, een pad en `dead` op `node`** (B). `num` komt uit `search_seq` en is de
   rowid in FTS. De rowid van `node` zelf kan na een `VACUUM` verschuiven; `num` niet. `mpath` is
   het pad van nummers (`/1/7/42/`). `dead` is 1 als de node of een ouder in de prullenbak ligt.
   Alle drie komen er met `ALTER TABLE … ADD COLUMN`, zonder de tabel te kopiëren. De migratie van
   104.696 nodes kost 2,8 s [gemeten, SQLite 3.46.1].
3. **De triggers zijn de enige weg naar de index** (B, herzien in r2: C1, C2). Ze staan in §3.1
   en letterlijk in `app/search/schema.py`. Regels, elk met een wachter-test (§4.1):
   - de triggers in `sqlite_master` zijn gelijk aan `schema.TRIGGERS`. Een latere migratie die
     `node` opnieuw maakt (`batch_alter_table` kopieert de tabel en laat triggers vallen), faalt
     dan meteen;
   - op `node`, `blob_text`, `media_item` en `photo_ocr` alleen gewone `INSERT`, `UPDATE` en
     `DELETE`. Verboden: `INSERT OR REPLACE`, `INSERT OR IGNORE`, `ON CONFLICT` (upsert),
     `.merge()`, `on_conflict_do_update`, `on_conflict_do_nothing` en `prefix_with("OR …")`. Een
     REPLACE start geen delete-trigger. Een upsert start eerst de trigger vóór het invoegen en dan
     die vóór het bijwerken, en verwijdert dezelfde rij dus twee keer. Met `OR IGNORE` loopt de
     trigger vóór het invoegen, maar de rij komt er niet. Alle drie maken de index kapot op 3.53.1
     en 3.46.1 (V1);
   - `blob_text` schrijft alleen `app/analysis.py` via één vaste helper `write_text()`: eerst
     `SELECT`, dan `INSERT` of `UPDATE`, in één korte transactie, met `body`, `ocr` en `meta` in
     één `UPDATE`. Ook een nieuwe poging na een crash gaat zo;
   - de view geeft `blob_id` mee, want de triggers op `blob_text` zoeken erop (C1);
   - de FK-cascade start de delete-triggers wel [gemeten]. Definitief wissen hoeft dus niets
     extra's te doen;
   - de churn-test (§4.3) gaf met precies deze vorm en gewone schrijfacties `integrity-check` ok en
     kloppende paden, op 3.53.1 en 3.46.1 (V1). Hij draait in CI op beide versies, ook met
     herhaalde schrijfacties (de nieuwe pogingen van `analysis`).
4. **De vraag in SQL ligt vast** (B, met `CROSS JOIN`, J12; de bereiken zonder staat in r2, S18):

   ```sql
   SELECT n.id, bm25(fts_node, 10.0, 6.0, 1.0, 0.8, 0.5) AS s
   FROM fts_node CROSS JOIN node AS n ON n.num = fts_node.rowid
   WHERE fts_node MATCH :expr
     AND n.kind <> 'album' AND n.dead = 0
     AND <de bereiken van de wortels>
     AND <filters uit het register (Keuze 43)>
   ORDER BY s, n.num
   LIMIT :offset + :limit + 1
   ```

   De bereiken, zonder tijdelijke tabel en zonder iets op de verbinding (Keuze 14):

   ```sql
   -- tot en met 64 wortels: een OR van bereiken (zo gemeten, J11)
   ((n.mpath >= :p1 AND n.mpath < :e1) OR (n.mpath >= :p2 AND n.mpath < :e2) …)
   -- meer dan 64: dezelfde bereiken als één JSON-parameter
   EXISTS (SELECT 1 FROM json_each(:ranges) AS r
           WHERE n.mpath >= r.value ->> '$[0]' AND n.mpath < r.value ->> '$[1]')
   ```

   `:e` is het pad met de laatste `/` vervangen door `0` (het eerste teken na `/`). `CROSS JOIN`
   dwingt SQLite om bij FTS te beginnen. Plan-tests (`assert_plan`) eisen dat, met en zonder
   `ANALYZE`, en verbieden `AUTOMATIC` en een scan op `parent_id`. `json_each` en `->>` zitten in
   3.46.1 (V8).
4a. **Ook de andere vragen liggen vast** (r2, C6). Zonder deze vormen had §4.4 drie poorten
   zonder bewijs. Spike S10 meet ze op 3.46.1 onder "eigenaar" en "alles". Haalt een vorm zijn
   poort in S10 niet, dan wordt die poort 200 ms, of komt er de tabel die erbij staat.
   - **Alleen filters, zonder vraag.** Twee plannen; de code kiest, niet de planner. Is het
     bereik klein (de wortels samen ≤ 20.000 nodes, geteld via `ix_node_mpath`), dan vanaf de
     paden en sorteren. Anders langs de tijd, met de partiële index
     `ix_node_live_mod (modified_at DESC, num DESC) WHERE dead = 0`; die stopt na genoeg treffers.
     Het filter `type` gebruikt de virtuele kolom `node.ftype` (uit `mime`, zonder trigger) met
     `ix_node_ftype_mod (ftype, modified_at DESC, num DESC) WHERE dead = 0`.

     ```sql
     SELECT n.id FROM node AS n INDEXED BY ix_node_live_mod
     WHERE n.dead = 0 AND n.kind <> 'album' AND <bereiken> AND <filters>
     ORDER BY n.modified_at DESC, n.num DESC
     LIMIT :offset + :limit + 1
     ```

     Gemeten door de critici met een eenvoudiger vorm zonder index: "alles" p95 142,6 ms op
     3.46.1 (C6). Poort 150 ms als S10 een SQL-p95 ≤ 100 ms meet, anders 200 ms.
   - **De kaart** (Keuze 32), met `ix_photo_geo_tile (owner_id, x16, y16)`:

     ```sql
     SELECT g.x16 >> :s AS cx, g.y16 >> :s AS cy, count(*) AS n,
            max(n.taken_at) AS t, n.id AS cover        -- de nieuwste foto van de cel
     FROM photo_geo AS g CROSS JOIN node AS n ON n.id = g.node_id
     WHERE g.owner_id = :me AND g.x16 BETWEEN :x0 AND :x1 AND g.y16 BETWEEN :y0 AND :y1
       AND n.dead = 0 AND n.kind = 'file' AND <filters>
     GROUP BY cx, cy
     LIMIT 501
     ```

     Poort p95 ≤ 150 ms bij 55.000 foto's met GPS van één eigenaar. Haalt S10 dat niet, dan een
     tabel `geo_cell (owner_id, z, cx, cy, n, cover)` die de job `geo` bijwerkt.
   - **Duplicaten** (Keuze 34–36), met `ix_node_owner_blob (owner_id, blob_id) WHERE dead = 0`:

     ```sql
     -- precies gelijk
     SELECT n.blob_id, count(*) FROM node AS n
     WHERE n.owner_id = :me AND n.dead = 0 AND n.kind = 'file'
     GROUP BY n.blob_id HAVING count(*) > 1
     -- bijna gelijk: de paren tussen eigen, levende foto's; de klieken maakt Python
     WITH mine(sha) AS (SELECT DISTINCT blob_id FROM node
                        WHERE owner_id = :me AND dead = 0 AND kind = 'file' AND ftype = 'image')
     SELECT p.a, p.b, p.dist, p.level FROM dup_pair AS p
     WHERE p.a IN mine AND p.b IN mine
     ```

     Poort p95 ≤ 300 ms bij 55.000 foto's. Haalt S10 dat niet, dan een tabel `dup_group` per
     eigenaar, bijgewerkt door de job `hash`, en bij het lezen nog door `own_live` gefilterd.
5. **De vraag ontleden** (B, met ß in r2: S5). `app/search/query.py`, `parse()`:
   - hooguit 200 tekens en 10 woorden, anders `400 search_query_too_long`;
   - NFKC, kleine letters, ß en ẞ naar "ss", dan splitsen op alles wat geen letter of cijfer is.
     Elk woord tussen aanhalingstekens. Wat de gebruiker typt, is nooit een FTS-operator: `AND`,
     `NEAR`, `name:` en `*` zijn gewone tekens of woorden;
   - de tekst in FTS krijgt dezelfde regel voor ß (Keuze 21). Daarmee zijn de tekst-vorm en de
     naam-vorm (`fold()`) gelijk voor alle vormen die we kennen (J1). Verschillen ze toch (een vorm
     die we niet kennen), dan zoekt het woord in beide vormen: `("a" OR "b")`;
   - het laatste woord is een voorvoegsel (`"rapp"*`) als het ≥ 3 tekens heeft en de vraag niet
     op een spatie eindigt. Kortere woorden alleen als heel woord;
   - een stuk tussen aanhalingstekens in de vraag is een frase;
   - er wordt niets geschrapt (J2). Een vraag die alleen uit woorden van 1–2 tekens bestaat,
     zoekt die als heel woord, plus in de namen via trigram met een `LIKE`-filter;
   - `parse()` is de enige plek die een `MATCH`-uitdrukking maakt. Een AST-test verbiedt een
     f-string of `%` naast `MATCH`.
6. **Rangorde** (B). bm25 met gewichten naam 10, titel 6, tekst 1, OCR 0,8, meta 0,5. Bij gelijke
   score op `num`, zodat de volgorde vast is. Stukken van namen uit `fts_name` komen ná de
   treffers op hele woorden, en alleen als de eerste ronde minder dan een pagina gaf. Na de
   tijdsgrens is er geen rangorde (`ranked: false`, Keuze 9).
7. **Pagina's zonder totaal en zonder score** (B). `offset` 0–1.000, `limit` 1–50 (standaard 30),
   en `more`. bm25 telt over de bestanden van iedereen; een totaal of een score zou iets zeggen
   over wat anderen hebben. De volgorde zelf houdt een klein restrisico (§5.1, R7).
8. **Snippets na de toegangscontrole, begrensd** (B, herzien in r2: C5, S12).
   `app/search/snippets.py`:
   - alleen voor de treffers op de pagina, en pas na Keuze 15;
   - SQL leest alleen `substr(body, 1, 20000)`, en anders `substr(ocr, 1, 20000)` of `meta`. Met
     1 MB tekst kostte het lezen van de hele tekst al 88–105 ms voor 30 treffers (C5);
   - één keer vouwen (`fold()` per teken, met een tabel van plekken terug naar de tekst), dan één
     reguliere expressie met de gevouwen woorden van de vraag. Het eerste stuk van ±160 tekens rond
     de eerste treffer. Gemeten door de critici: 33 ms voor 30 documenten met een venster van
     20.000 tekens (C5);
   - staat het woord niet in de eerste 20.000 tekens, dan `snippet: null` en `match: "text"`. De
     web toont dan "gevonden in de tekst";
   - als stukken: `[{"t": "…de ", "m": false}, {"t": "factuur", "m": true}, …]`. De web zet ze als
     tekst in de pagina, nooit als HTML;
   - de klok per document; hooguit 40 ms per verzoek, en voor de hele app hooguit 100 ms CPU per
     seconde (Keuze 13). Daarna `snippet: null` (de naam blijft);
   - de snippets draaien in een thread (`anyio.to_thread`) met een eigen limiter van 1, zodat de
     event loop tussendoor kan;
   - `snip` uit `Server-Timing` heeft in `zoek-perf` een harde poort: p95 ≤ 40 ms, ook in het
     profiel "groot" (200 documenten van 1 MB).
9. **Een tijdsgrens per vraag, en nooit een lege pagina als er treffers zijn** (A, herzien in r2:
   S4, C4). `app/search/deadline.py`.
   - De grens werkt met `interrupt()` op de SQLite-verbinding, gestart door een timer in de event
     loop. Een progress-handler werkt niet goed genoeg: bij een voorvoegsel zit er 144–201 ms
     tussen twee aanroepen, omdat FTS5 dat werk zelf doet. `interrupt()` stopt dezelfde vraag na
     21–26 ms bij een grens van 20 ms (V3).
   - De timer gaat uit vóór de verbinding terug naar de pool gaat. Een `interrupt()` die toch te
     laat komt, doet niets met de volgende vraag (V3); een test houdt dat vast.
   - **Ronde A**: de gerangschikte vraag (Keuze 4), grens `PLATFORM_SEARCH_DEADLINE_MS` = 120.
     Een gerangschikte vraag die stopt, geeft 0 rijen: de sortering moet af zijn voordat er één
     rij komt (V3).
   - **Ronde B**, als A stopt: dezelfde uitdrukking, toegang en filters, zonder bm25 en zonder
     `ORDER BY`, `LIMIT :limit + 1`, grens 40 ms. Die stroomt in de volgorde van `rowid`: "de" gaf
     31 rijen in 0,2 ms (V3). Een volgende pagina gaat verder vanaf de laatste `rowid`.
   - **Ronde C**, als ook B stopt: een kort voorvoegsel (`ver*`) voegt in FTS5 eerst alle lijsten
     samen en geeft ook zonder `ORDER BY` 0 rijen (V3). Dan alleen de namen via `fts_name`
     (trigram, ±3 ms, H2), met het laatste woord als stuk van de naam.
   - Bij B en C: `partial: true` en `ranked: false`; bij C ook `names_only: true`. Samen hooguit
     120 + 40 + ±5 ms; met HTTP onder de 200 ms.
   - De web zegt het eerlijk. Met treffers: "Dit zijn de eerste treffers, niet per se de beste.
     Maak je vraag preciezer." Zonder treffers: "Niets gevonden binnen de tijd. Maak je vraag
     preciezer."
   - Spike S1 bewijst het door aiosqlite en de pool heen, en eist: een vraag die de grens raakt,
     geeft ≥ 1 rij als er een zichtbare treffer is in de namen of in de eerste rijen van B.
10. **Een terugval: het zoeken van 0.7.0** (A). `LikeFallback` is de code van nu
    (`routers/search.py:132–195`). Hij draait als FTS5 ontbreekt, op PostgreSQL (het latere
    cluster), met `PLATFORM_SEARCH_INDEX=uit`, en tijdens een herbouw (Keuze 12). De keuze valt bij
    de start (`select_engine`) en per verzoek tijdens een herbouw, en staat op `/beheer`.
11. **Grote wissen in stukken** (J13). Definitief wissen uit de prullenbak, `purge_expired` en het
    wissen van een gebruiker gaan per 2.000 nodes, met een commit ertussen. Verplaatsen blijft één
    transactie: een map van 5.276 nodes kost 56 ms, naar de prullenbak 31 ms [gemeten].
12. **Herstellen en controleren, zonder de schrijver vast te houden** (herzien in r2: S13).
    - `search-rebuild` is nooit één statement: een `rebuild` van FTS5 houdt de schrijver minuten
      vast, en `busy_timeout` is 5 s (V9). In plaats daarvan: `delete-all` (17–250 ms, V4), dan
      opnieuw vullen per 2.000 `num` met een commit na elk stuk (≤ 142 ms per stuk op 3.46.1, V4).
    - De voortgang staat in `search_state.cursor`. De triggers schrijven tijdens een herbouw
      alleen naar FTS voor `num <= cursor`; wat daarboven verandert, neemt het volgende stuk mee.
      Het laatste stuk zet `cursor` op `NULL`, in dezelfde transactie. Zo is de index na afloop
      gelijk aan een volledige herbouw.
    - Tijdens een herbouw zoekt de app met `LikeFallback` (Keuze 10).
    - `search-triggers` zet de triggers terug zoals in `schema.py` én draait daarna altijd deze
      herbouw: na R5 klopt de index niet meer met de bron.
    - `search-check` vergelijkt 1.000 willekeurige nodes met de bron. Een wekelijkse job draait
      hem met 200 nodes; een verschil komt op `/beheer`. `integrity-check` van FTS5 alleen met de
      hand: hij leest alles.
    - Test: een upload tijdens een herbouw wacht hooguit 1 s, en de index is daarna gelijk aan een
      volledige herbouw.
13. **Logs, grenzen en tijden** (herzien in r2: S12, S16).
    - **Logs.** Voor elk pad onder `/api/v1/search`, `/api/v1/photos` en `/api/v1/people` gaat de
      hele query string uit de toegangslog (`?***`). Zo staan `q`, `bbox`, `z`, `place`,
      `country`, `person` en `day` nergens. `q` gaat daarnaast in `SECRET_PARAMS`. Een kaart op
      zoom 14 rond een cluster wijst vaak het huis aan.
    - **Voor de hele app.** Hooguit 2 FTS-vragen tegelijk (`PLATFORM_SEARCH_INFLIGHT`, een
      `CapacityLimiter`), en een rij van hooguit 20 wachtenden; daarboven `429 search_busy` met
      `Retry-After: 1`. De app is één proces met één event loop (`serve.py`): drie gebruikers die
      elk 10 vragen per seconde met snippets doen, legden anders alles stil (S12).
    - **Per gebruiker.** Hooguit 1 vraag tegelijk; een nieuwe vraag van dezelfde gebruiker stopt
      de oude met `interrupt()`. Dat past bij typen. Suggesties hebben dezelfde regel, apart.
      Hooguit 10 zoekvragen en 20 suggesties per seconde, anders `429 rate_limited`.
    - **Snippets**: zie Keuze 8; het budget van 100 ms CPU per seconde geldt voor de hele app.
    - **Tijden.** Een `Server-Timing`-kop met de tijd per stap (`roots`, `fts`, `round`, `names`,
      `check`, `snip`) alleen met `PLATFORM_SEARCH_TIMING=1` (tests en metingen).
    - **Poorten** (§4.4): 4 clients tegelijk p95 ≤ 300 ms, en `GET /health` p95 ≤ 50 ms tijdens
      die run (de event loop blijft vrij).

### Toegang en privacy bij zoeken

14. **Wortels en paden, zonder staat** (B, herzien in r2: S18). Bij elk verzoek: de eigen
    hoofdmap (niet voor een gast) plus `access.shared_roots()` (`access.py:240–262`, direct, via
    groep, als gast), elk met zijn `mpath`. Een wortel binnen een andere valt weg. Het filter is
    de vorm van Keuze 4: tot en met 64 wortels een `OR`, daarboven `json_each` met één parameter.
    Nooit een tijdelijke tabel: verbindingen gaan via de pool van verzoek naar verzoek, en een
    tabel die blijft staan, geeft de volgende gebruiker andermans wortels. Een verlopen of
    ingetrokken deling telt in hetzelfde verzoek al niet meer. Tests met 64, 65 en 1.000 wortels,
    met een plan-test voor beide vormen, en een deling die in hetzelfde verzoek wordt ingetrokken.
15. **Een tweede controle per treffer** (A en B). Elke treffer gaat nog door `access.resolve()`
    vóór de snippet. Wijkt die af van het filter, dan valt de treffer weg en komt er één logregel
    `search_access_mismatch` met het node-id (geen vraag, geen naam). In de tests mag die regel
    nooit voorkomen: een fixture laat de test dan falen.
16. **Wat nooit via zoeken gaat.**
    - Een publieke link heeft geen zoekroute. De filter op namen in de browser blijft zoals nu.
    - Plaatsnamen en personen staan niet in `fts_node`. Anders vindt Bram een foto van Eva op
      "Utrecht", terwijl alleen Eva de plek mag zien. Zoeken op plaats en persoon gaat via de
      groepen en filters, en die werken alleen op eigen, levende bestanden (Keuze 25a).
    - Tekst die OCR in een foto vond (X1), staat alleen in de index bij de node van de eigenaar
      die dat aanzette (`photo_ocr`, Keuze 23). Een kopie van een ander krijgt hem niet.
    - Albums zijn geen treffers. De suggesties tonen albums die je mag zien op naam.

### Tekst uit bestanden

17. **Tekst lezen in de zandbak, in batches** (A, herzien in r2: S6, C3, S15).
    - **Waarom de worker.** In 0.7.0 rendert PDFium pagina 1 bij een upload, en andere pagina's
      alleen als iemand ze bekijkt. De index leest vanzelf tot 2.000 pagina's van elke PDF van
      elke gebruiker, ook van wat een gast in een gedeelde map zet. Het renderproces draait als
      uid 10001, met `/data` (de database en alle blobs) en het netwerk; alleen de omgeving is
      leeg. De worker heeft geen netwerk, geen `/data`, een eigen uid en grenzen per kind, en
      draait hetzelfde image met dezelfde `/opt/venv`: pypdfium2 en de kantoorlezer zijn er al
      (V9).
    - **Media aan** (de standaard, B47): een opdracht `text` in de worker (soort `background`).
      De worker start per batch één kind, `python -I -m app.render text`, via `run.py`:
      `RLIMIT_DATA` 768 MB, `RLIMIT_CPU` 60 s, `RLIMIT_FSIZE` 0, `RLIMIT_NOFILE` 256, `nice` 19,
      de idle IO-klasse, een eigen procesgroep en `PDEATHSIG`. De app geeft per blob één fd.
    - **Eerst niet-dumpbaar, dan pas de bestanden.** Een kind uit een leesbaar programma (python)
      is dumpbaar tot het zelf `PR_SET_DUMPABLE 0` zet. Een ander proces van uid 10002 (een
      gekraakte ffmpeg) kan in die tijd via `/proc/<pid>/fd` de bestanden van deze opdracht
      openen (de les van golf 1 in fase 5). Daarom krijgt het kind bij de start alleen een socket
      op fd 3. Het zet zichzelf op niet-dumpbaar, meldt "klaar", en krijgt dan pas de fd's van de
      blobs via `SCM_RIGHTS`. Spike S11 en `test_media_container.sh` bewijzen het.
    - **Batches** (C3). Eén kind doet tot 16 blobs of 20 s CPU. Per blob gaat er een resultaat
      terug over de pipe, in lengte-gemarkeerde stukken (JSON, dan de tekst). Zo kost de start van
      het kind (113 ms, V2) per blob ±7 ms. De app schrijft daarna elke blob in een eigen korte
      transactie (Keuze 20). Faalt een batch, dan halveert de app hem en probeert opnieuw, tot de
      fout bij één blob ligt.
    - **Per blob een budget.** De lezer kijkt tussen pagina's en onderdelen naar de klok: na 30 s
      CPU stopt hij met wat er dan is (`truncated`, code `too_slow`). Stopt een kind met één blob
      door `RLIMIT_CPU`, dan `failed` met code `too_slow`, zonder nieuwe poging (S15).
    - **Media uit**: hetzelfde kind als renderproces van de app (`previews.render`, zoals de
      voorbeelden van 0.7.0), met `RLIMIT_AS` 1 GiB en `RLIMIT_CPU` 60 s, batches tot 50 blobs
      via hun pad. `/beheer` zegt dan "Tekst lezen: buiten de zandbak (media staat uit)".
    - **PDF**: pypdfium2 (B20), hooguit 2.000 pagina's.
    - **docx, xlsx, pptx, odt, ods, odp**: de kantoorlezer van B21 (`app/office/reader.py`):
      `Package` (een grens per onderdeel, ook tegen een liegend zip-bestand), `Budget` en `sax()`
      (expat, weigert `DOCTYPE` en entiteiten). Z2 voegt een stand toe die alle tekst leest:
      kop- en voetteksten, voetnoten, sprekersnotities en alle cellen. Grenzen: ≤ 2.000
      onderdelen, elk ≤ 50 MB uitgepakt, samen ≤ 200 MB, verhouding ≤ 100:1.
    - **txt, md, csv**: UTF-8, UTF-16 met BOM, anders cp1252. **html**: tags weg met
      `html.parser`. **rtf**: een eigen stripper.
    - **doc, xls, ppt en rtf** die OnlyOffice al naar PDF zet (fase 4): de tekst uit die PDF, via
      de haak na de omzetting. De worker krijgt ook die PDF als fd.
    - **audio en video**: titel, artiest en album (mutagen, of de probe van de worker) in `meta`.
    - Hooguit 1.048.576 tekens per blob; daarna `truncated`. Bestanden boven 512 MB: alleen de
      naam (`skip`).
    - Wat in het renderproces van de app blijft: de pagina's die iemand bekijkt (B20) en de
      foto's van photometa (B22). Dat doet 0.7.0 al; fase 6 maakt het niet breder (R6).
18. **De tekstlaag van B20** (B, herzien in r2: S11).
    `GET /api/v1/files/{id}/pages/{n}/words?token=` geeft de woorden met hun plek (0–1) op die
    pagina.
    - Een PDF met tekst: de woorden komen uit dezelfde render als het beeld van de pagina (stand
      `page` geeft `page.webp` én `words.json`). Ze blijven in de cache naast
      `pages/<w>/<n>.webp` (`pages/words/<n>.json`), met dezelfde limiter (`page_render_workers`,
      2) en dezelfde opruimregel. Nooit een eigen proces per verzoek.
    - Het download-token van de viewer, met de versie erin (zoals de pagina's, `viewer.py:552`).
      Zo horen de woorden altijd bij de versie die je bekijkt. `ETag` op (sha, pagina,
      `render.VERSION`), `Cache-Control: private`. Per gebruiker hooguit 20 per seconde.
    - Een scan (X1): uit `ocr_page`. Een foto met OCR: uit `photo_ocr`, alleen voor de eigenaar.
    - Niet via een publieke link (`404`), zoals in revisie 1.
    - De viewer legt de woorden doorzichtig over de pagina: selecteren en kopiëren werkt, en de
      woorden van de zoekvraag (`?q=` in de URL van de viewer) zijn gemarkeerd. Dit zit in de
      lazy chunk van de PDF-viewer.
    - Test: 50 verzoeken tegelijk starten nooit meer dan `page_render_workers` processen.
19. **Eén tabel voor het werk per blob** (B). `analysis(blob_sha, step, version, state, tries,
    code, owner_id, updated_at)`. `step` is `text`, `hash` of `ocr`; `state` is `wait`, `busy`,
    `done`, `none` (niets te halen), `skip` of `failed`. `owner_id` is de eigenaar voor wie het
    werk in de rij staat (Keuze 20). Een hogere `version` in de code zet de rijen terug op
    `wait`. Hooguit 3 pogingen met wachttijd, behalve na `too_slow` (Keuze 17); daarna `failed`
    met een code, zichtbaar op `/beheer` met de sha, nooit met de naam van andermans bestand.
20. **De backfill: kort schrijven, om de beurt per eigenaar** (herzien in r2: C3, S15).
    - Bij de start zet de app per 500 de blobs zonder rij in `analysis`, met als eigenaar die van
      de nieuwste levende node met die sha.
    - Een batch kiest om de beurt per eigenaar: elke eigenaar met werk krijgt hooguit 4 plekken,
      en wie het langst niet aan de beurt was, gaat voor. Binnen een eigenaar de nieuwste eerst.
      Een nieuwe upload van Bram wacht zo hooguit één batch op de 100 trage PDF's van Eva.
    - Elke blob wordt geschreven in een eigen korte transactie (via `write_text()`, Keuze 3),
      zodat de schrijver vrij blijft (WAL, één schrijver).
    - Zolang hij loopt, geeft `/search` `indexing: {"done": 0.43}`.
    - Poort over de hele keten (starten, lezen, schrijven): ≥ 1.000 documenten per minuut per
      core; verwachting 1.500–2.000 (§4.5, §3.10).
21. **De tekst opschonen.** NFKC, witruimte samen, zachte afbreekstreepjes (U+00AD) en tekens zonder
    breedte weg. De opgeslagen tekst houdt ß, zodat een snippet "große" toont. In de view
    `search_src` gaat ß en ẞ naar "ss" (`replace`): wat FTS ziet, en wat `parse()` maakt, is dan
    gelijk (Keuze 5, S5). Geen andere taalregels (geen stemming): "facturen" vindt "factuur" niet,
    wel "factu" als voorvoegsel. Stemming voor het Nederlands kan later als eigen tokenizer
    [niet getest].

### OCR (X1, wacht op Vraag 1)

22. **Tesseract in de mediaworker, op een genormaliseerd beeld** (A, herzien in r2: S7, C10,
    C14). Een opdracht `ocr` in de worker (soort `background`), nooit in de app. Per pagina twee
    kinderen na elkaar, verbonden met een pipe die de app niet ziet:
    - **Kind 1**: `python -I -m app.render ocr_page` (met de handdruk van Keuze 17). Het maakt van
      de bron een grijs beeld als PGM: een PDF-pagina op 300 dpi; een foto met
      `ImageOps.exif_transpose`, HEIC via pillow-heif, en de grens `preview_max_pixels`. Dan de
      eigen deskew (Pillow, projectie over ±12°, 150–380 ms, H4). Tesseract krijgt dus nooit de
      ruwe blob: leptonica kan geen HEIC, kijkt niet naar de EXIF-draaiing, en zijn decoders voor
      TIFF, JP2K, GIF en WebP hebben geen grens op de pixels (S7).
    - **De worker** leest de kop van de PGM (`P5`, breedte × hoogte ≤ de grens) vóór kind 2
      start. Iets anders: `failed`, code `ocr_bad_image`.
    - **Kind 2**: `tesseract stdin stdout -l nld+eng --psm 3 tsv`, met `OMP_THREAD_LIMIT=1`,
      `RLIMIT_DATA` 512 MB en een tijdsgrens van 60 s. tesseract leest PGM van stdin (V7). De
      TSV komt via een pipe terug (hooguit 64 KB per bericht).
    - **OSD alleen als het nodig is.** Is de gemiddelde zekerheid van de woorden < 60, dan de OSD
      van tesseract (`--psm 0`, ±2 s, V7), draaien met 90°, 180° of 270°, en nog één keer OCR.
      De meeste scans staan rechtop.
    - Woorden met zekerheid ≥ 60 gaan naar de tekst. De vakjes horen bij het beeld zoals de viewer
      het toont (na de EXIF-draaiing). De hoek van de deskew staat bij de pagina; de app draait de
      middens van de vakjes terug, zodat de markering op de goede plek ligt.
    - De hele keten kost per pagina 3–8 s op één core: renderen 0,2 s, deskew 0,4 s, OCR 1,9–2,9 s,
      met OSD +2 s en nog een OCR (V7; de critici maten met PNG en OSD altijd ±6,5 s). Poort
      ≥ 8 pagina's per minuut per core, gemeten in de job `ocr` van CI (§4.5).
    - tesseract wordt `0711` in het image, zoals ffmpeg (een leesbaar programma is dumpbaar terwijl
      het de fd's van een opdracht heeft). `"tesseract"` komt in `guard.BINARIES`, en
      `test_media_no_ffmpeg_outside.py` eist dat de naam buiten de worker nergens staat.
      `test_ocr_container.sh` controleert de modus `0711` (C14).
23. **Welke bestanden, en waar de tekst heen gaat** (herzien in r2: S8, S15, C11).
    - **PDF-pagina's** met minder dan 20 tekens tekst. De tekst gaat naar `blob_text.ocr`, net als
      de tekst van een PDF met tekstlaag: wie het document mag lezen, mag de tekst ook zien. De
      plekken gaan naar `ocr_page`. Een PDF die al ≥ 200.000 tekens tekst heeft, krijgt geen OCR
      (code `ocr_has_text`): de schrijfactie zou met kopieën te lang duren (C11).
    - **Foto's** alleen als de eigenaar "Tekst in foto's herkennen" aanzet
      (`photo_pref.ocr_photos`, standaard uit) of zelf op "Tekst herkennen" drukt. De tekst gaat
      naar `photo_ocr`, per node van die eigenaar (Keuze 25a), en komt alleen in de index bij die
      node. Een kopie van Bram krijgt hem niet, en Brams OCR van zijn kopie komt niet bij Eva.
    - `POST /files/{id}/ocr`: voor een foto alleen de eigenaar; voor een PDF wie schrijfrecht
      heeft. Per gebruiker hooguit 20 verzoeken per uur en 200 pagina's per dag
      (`429 ocr_limit`).
    - Hooguit 200 pagina's per document. Eén opdracht tegelijk in de rij `ocr`, om de beurt per
      eigenaar (Keuze 20), alleen als media aan staat en OCR aan staat.
24. **Zonder tesseract.** De code van X1 kan in het repo staan zonder dat het image tesseract
    heeft. `caps` van de worker zegt dan `ocr: false`; de rijen blijven `none` met code
    `ocr_missing`, en worden weer `wait` zodra `caps` OCR meldt. `/beheer` zegt "OCR: niet
    geïnstalleerd (Vraag 1)".

### EXIF, GPS en plaatsen

25. **GPS lezen, apart bewaren** (A en B, herzien in r2: S3, S14). `render_meta.VERSION` wordt 2
    en leest de GPS-IFD (JPEG, HEIC, TIFF, PNG `eXIf`, WebP), in de bestaande batch van
    `photometa.py` (200 per renderproces).
    - **XMP als tweede bron**, zonder defusedxml: Pillow 13 leest XMP alleen met defusedxml, en
      dat zit er niet in (V9). We lezen de ruwe bytes uit `im.info["xmp"]`, hooguit 1 MB, met
      `office.reader.sax()` (weigert `DOCTYPE` en entiteiten), en alleen `exif:GPSLatitude`,
      `exif:GPSLongitude` en `exif:GPSAltitude`.
    - **Controle op elke waarde**: NaN en oneindig weg (`IFDRational(0, 0)` geeft NaN), |lat| > 90
      of |lon| > 180 weg, (0, 0) is "geen plek", en zonder `GPSLatitudeRef` of
      `GPSLongitudeRef` ook geen plek.
    - **Video's**: de worker-opdracht `meta` geeft ook `location` en ISO 6709
      (`com.apple.quicktime.location.ISO6709`, `©xyz`) door, met dezelfde controle.
    - **Wat waar komt.** Per blob alleen een ja of nee: `blob_meta.gps` (1, 0, of `NULL` als het
      onbekend is, zoals bij een video zonder media). Dat zegt alleen dat het bestand een plek
      bevat, niet welke, en staat er ook als de eigenaar locatie op "uit" heeft (Keuze 30). De
      coördinaten zelf gaan alleen naar `photo_geo`, per node, voor een eigenaar met locatie
      "mij" (Keuze 25a, 28), met `x16`/`y16` (de tegel op zoom 16) voor de kaart.
    - Nooit in `preview_meta`: `SECRET_META` blijft, en een test doorloopt elk antwoord van
      `/viewer` en `/public` op `lat`, `lon` en `geo`.
25a. **Foto-gegevens horen bij een bestand van één eigenaar** (r2: S8, S9, S17).
    - **Wat.** Alles wat een foto zegt over een plek of een persoon, en tekst die OCR in een foto
      vond: `photo_geo`, `photo_person`, `photo_ocr` en later (X2) `face`. Elke rij hoort bij één
      node (FK naar `node` met `ON DELETE CASCADE`), met `owner_id` erbij voor de index. Nooit
      per blob: een kopie bij een ander (een kopie houdt de sha en krijgt de eigenaar van de
      doelmap, V9) mag niets erven, en de toestemming is per eigenaar.
    - **Schrijven** alleen voor de eigenaar van de node, en alleen als zijn instelling het toelaat
      (`photo_pref`), gecontroleerd in dezelfde transactie als het schrijven.
    - **Prullenbak**: de rijen blijven, maar `own_live` laat ze weg. **Terugzetten**: ze zijn er
      weer. **Definitief wissen**: weg via de cascade. **Een gebruiker wissen**: weg via de
      cascade.
    - **Andere eigenaar** (verplaatst naar de map van een ander): een trigger op `node.owner_id`
      wist de rijen van die node. De nieuwe eigenaar krijgt de plek pas als photometa v2 de foto
      voor hem leest, en alleen met zijn eigen "mij". Personen van de oude eigenaar gaan niet mee.
    - **Nieuwe inhoud** (`blob_id` verandert): een trigger wist `photo_geo` en `photo_ocr` van die
      node; de backfill leest de nieuwe inhoud. `photo_person` blijft (dezelfde foto, bewerkt),
      zonder kader.
    - **Kopie door dezelfde eigenaar**: de personen gaan mee naar de kopie (een haak in de
      kopie), de plek wordt opnieuw gelezen. **Kopie door een ander**: niets gaat mee.
    - **Eigen en levend.** Eén regel, `own_live(n)` in `app/search/live.py` (Z0, vast):
      `n.owner_id = :me AND n.dead = 0 AND n.kind = 'file'`. Herinneringen, de kaart, de
      plaatsen, de duplicaten, personen (foto's, aantallen en omslagen) en de verversing van slimme
      albums gebruiken hem allemaal. In 0.7.0 eisen de tijdlijn en albums al "levend"
      (`on_timeline`, `live_items`, V9); met `dead` is dat één kolom.
    - Test (`test_own_live.py`): Eva's eigen foto in de prullenbak staat niet in herinneringen,
      kaart, plaatsen, duplicaten, personen of suggesties, en komt na terugzetten terug. Een kopie
      in Brams boom: Bram ziet er geen plek, persoon of OCR-tekst van Eva bij.
26. **Camera en lens** in `blob_meta`, gevouwen als "merk model", voor het filter `camera`. Dat is
    geen plek en geen persoon, dus per blob. De camera mag ook in de kolom `meta` van de index.
27. **Alleen de eigenaar ziet de plek** (A). `GET /files/{id}/location` geeft anderen `404`
    (geen verschil met "geen plek"). De kaart, de plaatsenlijst en het filter `place` werken
    alleen op eigen, levende bestanden. Een publieke link nooit, ook niet in een gedeeld album.
28. **Een schakelaar per gebruiker** (B, herzien in r2: S8). `photo_pref.location`: `mij`
    (standaard) of `uit`.
    - Bij `uit` wist één job alle rijen in `photo_geo` met `owner_id` = die gebruiker, en de kaart
      is meteen leeg (de routes kijken naar `photo_pref`). Niets anders blijft staan: ook niet
      omdat een ander dezelfde foto heeft.
    - Zolang `uit` geldt, schrijft niets een rij voor hem: de backfill, een nieuwe upload,
      photometa v2 en `/admin/search/rebuild` met `geo` controleren het per eigenaar, in dezelfde
      transactie.
    - Weer `mij`: de backfill leest zijn foto's opnieuw.
    - `blob_meta.gps` (ja of nee, Keuze 25) blijft: de waarschuwing bij delen heeft hem nodig, en
      hij zegt niet waar.
29. **Plaatsnamen en landen offline** (A en B, herzien in r2: S10, C12; Vraag 5).
    - **Het land uit polygonen.** De dichtstbijzijnde plaats geeft bij een grens vaak het verkeerde
      land (4,1% van de punten rond NL, BE, DE, LU en FR), en op zee toch een land (92% van de
      punten op de Noordzee) (V6). Het land komt daarom uit een punt-in-polygoon-test op Natural
      Earth 10m admin-0 (publiek domein, tag v5.1.2, sha256 vast): 0,81 ms per foto in puur
      Python, 3,4 MB in de tabel (V6). Buiten elk land: geen land ("op zee").
    - **De plaats**: de dichtstbijzijnde plaats uit GeoNames cities1000 (CC BY 4.0) binnen 30 km
      én in hetzelfde land, met provincie. Anders alleen het land. 0,057 ms per foto (H6).
    - **Grenzen van de bron**: Natural Earth 10m is op ±1 km nauwkeurig bij een grens, en heeft de
      enclaves van Baarle-Hertog niet (V6). Daar zegt het platform Nederland. Dat staat bij de
      bronvermelding.
    - **Downloaden in `install.sh`, niet in de Dockerfile** (C12). Een `RUN`-stap die niet fataal
      faalt, blijft als lege laag in de cache staan, en `.env` komt niet in de build. Daarom haalt
      `install.sh` beide bestanden naar `DATA_ROOT/geo/` (op de LXC `/tank/platform/geo/`): de
      sdist van `reverse_geocoder` 1.5.1 van PyPI (daarin `rg_cities1000.csv`) en
      `ne_10m_admin_0_countries.geojson` van `raw.githubusercontent.com`, elk met sha256. Ontbreekt
      een bestand, dan probeert elke run het opnieuw. Mislukt het, dan gaat de installatie door
      zonder plaatsnamen.
    - **Bouwen in de app.** `app.cli geo-load` (alleen de standaardbibliotheek) maakt daaruit
      `/data/geo/places.sqlite`: een R*Tree van de plaatsen, en de landen als float32 met zlib, met
      een R*Tree op hun kaders. De app doet dat bij de start als het bestand ontbreekt of de sha256
      van de bron anders is. `PLAATSEN=uit` in `.env` slaat downloaden en bouwen over. `/beheer`
      zegt "Plaatsnamen: niet geladen" als het er niet is.
    - Bronvermelding op de kaart en op `/over`: "Plaatsnamen: GeoNames, CC BY 4.0. Landen: Natural
      Earth (publiek domein)."
    - Provincienamen zijn Engels ("North Holland"). Nederlandse namen vragen de alternateNames:
      later.
    - Tests: tegen de polygonen rond Kerkrade en Herzogenrath, Zundert en Wuustwezel, Lommel en
      Bergeijk, Frisange en Hettange, en op zee; plus brute-force haversine voor de plaats.
30. **Delen met een plek: waarschuwen, en goed tellen** (A, J7, Vraag 4; herzien in r2: S3).
    - `GET /photos/share-check?node=` telt voor elk soort deling: een map (de levende bestanden
      eronder, via het pad), een album (zijn items die gedeeld worden, Keuze 42) en een los
      bestand (dat bestand). Hij telt met `blob_meta.gps`, dus ook voor een eigenaar met locatie
      "uit". Een video waarvan het onbekend is (media uit), telt als "onbekend".
    - Het deeldialoog (`web/src/ShareDialog.tsx`) roept hem aan bij elke deling: met een persoon,
      een groep, een gast en een publieke link, op een map, een bestand en een album. Het zegt:
      "12 foto's hierin hebben een locatie in het bestand. Wie ze downloadt, krijgt die mee." En
      zo nodig: "Van 3 video's weten we het niet (media staat uit)."
    - De bytes blijven gelijk, zoals in 0.7.0. GPS uit downloads halen is de bewerking van fase 10
      (Vraag 4b).
    - Tests: een album met een publieke link, een los bestand, een eigenaar met "uit", een video
      zonder media.

### De kaart

31. **Een eigen kaart, zonder tegels** (A, herzien in r2: C15). Landsgrenzen uit Natural Earth
    110m (publiek domein) als statisch bestand `/kaart/world-110m.<hash>.json`, gemaakt door
    `tools/kaart/maak_wereld.py` uit de GeoJSON van tag v5.1.2 (sha256). CI maakt hem opnieuw en
    vergelijkt. Hij zit niet in de JS: de route laadt hem met `fetch` (de CSP heeft
    `connect-src 'self'`). Eigen grenzen in `web/scripts/budget.mjs`: `mapData` ≤ 60 kB gzip,
    `mapRoute` ≤ 30 kB. Webmercator, getekend als SVG; slepen, knijpen en het toetsenbord (pijlen,
    `+`, `-`). Geen tegels van buiten, de CSP blijft gelijk (`img-src 'self' data: blob:`). Lazy
    route `/fotos/kaart`.
32. **Clusters rekent de server** (B). Op zoom `z` (0–14) is een cel
    `(x16 >> (14 − z), y16 >> (14 − z))`: vier cellen per tegelbreedte. Boven zoom 14 losse
    foto's. De SQL staat in Keuze 4a: `GROUP BY` over eigen, levende foto's in het venster,
    ≤ 500 cellen, met een omslag (de nieuwste). Poort p95 ≤ 150 ms bij 55.000 foto's met GPS
    (S10, §4.4). Plaatsnamen op de kaart pas vanaf zoom 6.
33. **Een lijst naast de kaart** (A; herzien in r2: S9). "Plaatsen": de plaatsen met het aantal
    foto's, voor wie geen kaart gebruikt of met een schermlezer werkt. Een klik zet het filter
    `place`. Het aantal komt per verzoek uit `GROUP BY` op `ix_photo_geo_place (owner_id,
    place_id)` met `own_live`, niet uit een opgeslagen telling: die zou na prullenbak,
    terugzetten, wissen of "uit" niet meer kloppen.

### Duplicaten

34. **Precies gelijk** (A en B). Dezelfde `blob_id` bij twee of meer eigen, levende bestanden
    (Keuze 4a). De opslag is al per sha, dus een kopie kost geen ruimte; de lijst helpt bij
    opruimen.
35. **Bijna gelijk, met een tweede controle** (B, herzien in r2: S1, C3).
    - **Waar.** De pHash rekent photometa v2 in zijn bestaande batch (Keuze 25), niet een job per
      voorbeeld: uit het voorbeeld van 256 px (J6), of uit het origineel met `draft()` als er geen
      voorbeeld is. In `blob_hash` met vier stukken van 16 bits (`h0`–`h3`). Een pHash is geen
      plek of persoon, dus per blob.
    - **Vlak is geen hash.** Is de spreiding van het 32 × 32-grijsbeeld < 3, dan `state = 'flat'`
      en geen hash. pHash kijkt niet naar helderheid: alle bijna egale foto's (in de broekzak,
      lucht, een muur) hadden afstand 0 (V5).
    - **Kandidaten** delen minstens één stuk van 16 bits en hebben Hamming-afstand ≤ 10 (0,6 ms
      per zoektocht, H5).
    - **De tweede controle**, voor elk kandidaat-paar, in het renderproces uit de twee
      voorbeelden: de verhouding (breedte/hoogte) binnen 2%, de dHash-afstand ≤ 10, en de
      gemiddelde afwijking van 64 × 64 grijs (MAD64) ≤ 3,0. Echte varianten (half zo groot,
      JPEG q35) haalden ≤ 2,18; verschillende brieven op één sjabloon ≥ 3,94 (V5). De grens van
      3,0 is voorlopig: Z3 meet hem op de fixtures van §3.6 en legt hem vast in de test.
    - **Tekst telt mee.** Hebben beide blobs tekst (`blob_text`), en is die tekst anders (een hash
      van de gevouwen tekst), dan zijn ze nooit "bijna zeker gelijk".
    - **Het niveau** in `dup_pair.level`: `zeker` als pHash ≤ 6 én de tweede controle slaagt;
      anders `lijkt` (pHash ≤ 10). Bijsnijden en draaien vindt pHash maar deels (recall 0,71 en
      0,85 bij afstand 10, H5); dat zijn hooguit `lijkt`-paren.
36. **Groepen zijn klieken, en nooit vanzelf weg** (herzien in r2: S1, C6).
    - `GET /duplicates?kind=near` bouwt groepen uit de paren van eigen, levende foto's (Keuze 4a).
      Een groep is een kliek: elk paar in de groep haalt de grens. De paren gaan op volgorde van
      afstand; een foto komt alleen bij een groep als hij met elk lid een paar is. Nooit
      "verbonden via een ander": bij brieven gaf dat één groep van 149 van de 150.
    - Het niveau van een groep is dat van zijn zwakste paar. "Bijna zeker gelijk" alleen als elk
      paar `zeker` is; anders "lijkt erop".
    - De web toont de voorbeelden naast elkaar, met datum, grootte en map. Er staat nooit iets
      vooraf aangevinkt voor de prullenbak. "Niet gelijk" zet de groep in `dup_ignore`.
    - Opruimen gaat via de prullenbak, met de bestaande batchroute.
    - Fixtures (§3.6): brieven op één sjabloon, gescande formulieren, screenshots en vlakke foto's.
      Poort in `test_phash.py`: 0 groepen `zeker` met twee verschillende originelen.

### Personen en gezichten

37. **Personen met de hand in de kern** (A, herzien in r2: S8). `person` per eigenaar;
    `photo_person` koppelt een persoon aan een node (Keuze 25a), met of zonder kader (`x`, `y`,
    `w`, `h`, 0–1) en `source` (`hand`, later `auto`). In het infopaneel van de viewer: "Persoon
    toevoegen" met suggesties uit je eigen personen; een kader tekenen met de muis of met het
    toetsenbord (pijlen verplaatsen, Shift+pijlen maken groter of kleiner).
38. **Zoeken op persoon.** "anna" geeft bovenaan de groep personen (eigen personen waarvan de naam
    zo begint); een klik zet het filter `person`. `/fotos/personen` toont je personen met aantal
    en omslag (eigen en levend). Per persoon: hernoemen, samenvoegen, verbergen, wissen. Alleen de
    eigenaar ziet personen: wie een gedeelde foto bekijkt, ziet er geen (`[]`), en de API geeft
    voor een persoon van een ander `404`.
39. **Gezichten automatisch: X2, na "ja" op Vraag 2** (A, met de worker van B; herzien in r2:
    S17). YuNet (MIT) en SFace (Apache-2.0) via onnxruntime, in een eigen container `analyse` uit
    hetzelfde image met een optionele extra (`numpy`, `onnxruntime`): uid 10003,
    `network_mode: none`, `read_only`, `cpus: 1.0`, `mem_limit: 2g`, `cpu_shares: 64`. Modellen met
    sha256 in `models.lock`. InsightFace mag niet (licentie). Wat nu al vastligt, omdat een
    vector van een gezicht biometrische data is van mensen die zelf niets kozen:
    - aan per gebruiker (standaard uit), en de beheerder kan het verbieden;
    - `face` per node (Keuze 25a), alleen voor eigen, levende bestanden;
    - "uit", of een verbod van de beheerder: één job wist alle `face`-rijen en vectoren van die
      eigenaar (test: daarna 0 rijen);
    - een vector staat nooit in een antwoord van de API;
    - een persoon wissen wist zijn `face_person`-rijen;
    - een gevonden gezicht is een voorstel ("Is dit Anna?"); het wordt pas een persoon na
      bevestigen;
    - de reservekopieën (Keuze 52) houden de vectoren tot ze weggedraaid zijn. Dat staat op
      `/beheer` en in de README.
    - Tabellen `face` en `face_person` in migratie 0011.

### Beeld: semantisch zoeken en objecten

40. **Niet in fase 6** (A, Vraag 3). SigLIP 2 (Apache-2.0, meertalig) kost ±200–400 MB en 0,9–1,9
    core-uur per 55.000 foto's. CLIP is Engelstalig. pgvector vraagt PostgreSQL (cluster). De
    filters en de API laten ruimte voor een filter `label` en een stand `mode=image` later.

### Herinneringen, slimme albums en filters

41. **Herinneringen op verzoek** (A en B). `GET /photos/memories?day=JJJJ-MM-DD` (standaard vandaag
    in `PLATFORM_TIMEZONE`): eigen, levende foto's (`own_live`) van deze dag in eerdere jaren. Per
    jaar één bereik op `ix_node_owner_taken` (`taken_day` gelijk aan `J·10000 + MMDD`), dus geen
    scan. Regels om te verbergen in `memory_hide` (`day`, `folder`, `person`). Uit te zetten per
    gebruiker (`photo_pref.memories`). Bovenaan `/fotos`: "Vandaag, 3 jaar geleden".
42. **Slimme albums houden je eigen keuze, en delen niet vanzelf** (B, J10; herzien in r2: S2).
    - Een album kan een regel krijgen (`album_rule`, JSON met dezelfde filters als zoeken). De
      verversing zet treffers erbij met `album_item.source = 'rule'` en haalt alleen `rule`-items
      weg die niet meer passen. Haalt de eigenaar een `rule`-item weg, dan komt het in
      `album_skip` en komt het niet terug. Items met de hand blijven altijd. Alleen eigen, levende
      bestanden, hooguit 10.000. Verversen na een wijziging van de regel, elke nacht, en 60 s na
      een reeks uploads.
    - **Delen.** Een album deelt in 0.7.0 elk item, en een publieke link geeft elk item als
      origineel, met GPS (V9). Een regel zou dan foto's publiceren die de eigenaar later uploadt,
      zonder dat hij iets doet. Daarom `album_item.shared`: een item met de hand is altijd
      gedeeld (1); een `rule`-item krijgt `album_rule.share_new` (standaard 0).
    - `access._via_albums` en `public._in_album` geven alleen items met `shared = 1`.
    - Wie een regel zet op een album dat gedeeld is of een publieke link heeft, of een album met
      een regel deelt, krijgt de vraag: "Nieuwe foto's die passen, worden ook gedeeld. Ja of
      nee?" Ja zet `share_new` op 1 en alle huidige `rule`-items op `shared = 1`
      (`POST /albums/{id}/rule/confirm`). Nee laat ze alleen voor de eigenaar zichtbaar.
    - De eigenaar ziet alles, met een merkje "alleen voor jou" bij wat niet gedeeld is. Het
      deeldialoog zegt hoeveel items uit de regel komen en hoeveel daarvan gedeeld worden.
    - e2e: een album met een regel en een publieke link, daarna een nieuwe foto die past: die is
      via de link niet te zien tot de eigenaar "ja" kiest.
43. **Eén register voor filters** (B). `app/search/filters.py`: elk filter meldt zich aan met een
    functie die SQL toevoegt. Zo werken dezelfde filters op `/search`, `/photos` en de regels van
    slimme albums, en hoeven de pakketten niet in hetzelfde bestand te schrijven. De filters op
    foto's (`person`, `place`, `country`, `camera`) gebruiken `own_live`.

    | Filter | Waarop | Meer keer |
    |---|---|---|
    | `type` | `image`, `video`, `audio`, `document`, `pdf`, `spreadsheet`, `presentation`, `folder` (via `node.ftype`) | OF |
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

45. **De ingang** (A, herzien in r2: C15). Een zoekveld in de bovenbalk, hooguit 0,8 kB in de
    initiële JS (nu 96,4 van 100 kB); de combobox is lazy. Ctrl/Cmd+K zet de focus erop. Op
    Bestanden blijft `/` het naamfilter (J9). Enter opent `/zoeken?q=…`.
46. **Suggesties** (B). Een lazy chunk na de eerste toets (wacht 120 ms), een ARIA-combobox
    (`listbox`, `aria-activedescendant`), met groepen: namen, personen, plaatsen, tags en albums,
    elk ≤ 5. Esc sluit. Dit is het eerste wat valt als de begroting knelt.
47. **De pagina `/zoeken`** (A en B). Per treffer: icoon, naam, pad en snippet (gemarkeerde woorden
    als `<mark>`, via tekst). Chips voor de filters. "Meer laden" (offset). Meldingen: "De index
    is nog bezig (43%)" en de twee teksten van Keuze 9. Pijlen en Enter; Enter opent de viewer met
    `?q=` voor de markering in de tekstlaag.
48. **Nieuwe lazy routes**: `/fotos/kaart`, `/fotos/personen`, `/fotos/personen/:id`, `/dubbel`.
    Elk met een begroting in `web/scripts/budget.mjs`. Teksten in NL en EN, in een eigen gebied
    per pakket (`search`, `photos_map`, `dupes`, `photos_people`), en foutcodes in een eigen
    catalogus per pakket (`web/src/i18n/errors/search.ts`, `photos_map.ts`, `people.ts`). Z0 maakt
    ze aan. Een toetsenbordlijst `docs/fase-6/toetsenbord-z*.md`. De waarschuwing in het
    deeldialoog laadt pas als het dialoog opengaat: `ShareDialog` zit statisch in Bestanden
    (43,5 van 45 kB).

### Werk en CPU

49. **Twee rijen, eerlijk per eigenaar** (A, herzien in r2: S15). `index` (tekst, hash,
    GPS-backfill) draait altijd, ook zonder media: `active_lanes` (`jobs.py:570–574`) zet hem
    erbij. `ocr` (X1) draait alleen als media aan staat en OCR aan staat. Elke rij één job
    tegelijk; een job is een batch (Keuze 17), samengesteld om de beurt per eigenaar (Keuze 20).
50. **Wijken** (B). `jobs.Defer` (fase 5) voor `index` en `ocr` als er ≥ 2 transcodes lopen of
    OnlyOffice start. De naamindex wacht nooit: die zit in de triggers.

### Beheer, uitrol, update en terug

51. **Een tab "Zoeken en foto's" op `/beheer`.** De motor (`fts` of `like`), de stand van de
    index (namen klaar; tekst n van m; mislukt met sha en code; een herbouw met zijn voortgang),
    waar tekst gelezen wordt (in de zandbak of erbuiten), tijdsgrens geraakt per uur (ronde B en
    C), `search_busy` per uur, OCR (geïnstalleerd, aan, pagina's in de rij), plaatsnamen (geladen,
    bron, datum), de uitkomst van `bench --los`, en een zin over de reservekopieën met vectoren
    van gezichten (X2). Knoppen: index herbouwen, mislukte opnieuw, achtergrondwerk pauzeren.
52. **Een reservekopie vóór de migratie, met elk image** (A, herzien in r2: C7, C13).
    - `install.sh` maakt de kopie met alleen de standaardbibliotheek:
      `compose run --rm --no-deps platform python -I -c '<sqlite3.Connection.backup>'`, terwijl de
      oude app nog draait. Dat werkt met het image van 0.7.0 en van 0.8.0; `app.cli db-backup`
      bestaat in 0.7.0 niet (V9).
    - Het bestand heet naar de alembic-head en de tijd: `/data/backup/platform-0008-<tijd>.db`.
    - Per head blijft de nieuwste kopie, en nooit gaat de laatste kopie van vóór een migratie weg.
      Zo staat de kopie van 0008 er na twee nieuwe runs op 0010 nog.
    - Lukt de kopie niet, dan stopt het script: er is dan nog niets veranderd.
    - `app.cli db-backup` (Z0) doet hetzelfde voor de hand en voor `/beheer`.
53. **Terug naar 0.7.0 is beschreven en getest** (A, herzien in r2: C7, §3.10). Het script komt
    van de commit van 0.7.0, niet van de branch: dan draait het oude script met het oude image.
    `test_install_search.sh` draait precies de stappen van §3.10.
54. **Meten op de LXC** (A). `app.cli bench --los` maakt een boom van 100.000 in
    `/data/cache/zoekbench` (±2 minuten, ±500 MB), meet zoeken voor "eigenaar" en "alles", en
    wist hem weer. De uitkomst komt op `/beheer`. Al in week 1 kan Jonas de losse versie uit Z0
    draaien in zijn container van 0.7.0, zonder iets te installeren (§3.7, S9).

### Vragen voor Jonas

1. **Mag tesseract (Apache-2.0, ±20 MB met Nederlands en Engels) in het image, voor OCR?**
   Standaard: **nee, tot je ja zegt.** We bouwen X1 wel, en testen het in CI met een echte
   tesseract op de runner. Zonder ja is een scan alleen op naam te vinden. Met ja komt er één
   regel in de Dockerfile (plus `chmod 0711`) en staat OCR op `/beheer` aan voor PDF-scans
   (foto's per gebruiker). OCR kost 3–8 s per pagina op één core, gemiddeld ±70–110 minuten per
   1.000 pagina's, en draait alleen als de mediaworker rustig is.
2. **Mogen gezichten automatisch gevonden worden?** Dat vraagt numpy en onnxruntime (±120 MB, in
   een eigen container zonder netwerk) en twee modellen (YuNet 0,2 MB, SFace 39 MB). Let op: de
   vector van een gezicht is biometrische data, ook van mensen op de foto die zelf niets kozen.
   Keuze 39 zegt hoe we daarmee omgaan (alleen eigen foto's, uit = alles weg, nooit in de API).
   Standaard: **nee**. Personen benoem je met de hand, en dat is doorzoekbaar. Met ja: X2
   (±5–6 dagen), per gebruiker aan te zetten, ±1 uur rekenen per 55.000 foto's.
3. **Zoeken op wat er op een foto staat ("strand", "hond")?** Dat vraagt SigLIP 2 (±200–400 MB) en
   ±1–2 uur rekenen per 55.000 foto's. Standaard: **niet in fase 6.** pgvector pas met het
   cluster.
4. **Wie ziet de plek van een foto?** Standaard: **alleen de eigenaar.** Nooit via een publieke
   link. Het deeldialoog waarschuwt dat een download de locatie in het bestand meeneemt; de bytes
   blijven gelijk. Andere antwoorden:
   (b) GPS uit downloads halen voor iedereen behalve de eigenaar (de bewerking van fase 10 naar
   voren, ±2 dagen);
   (c) "wie ik deel, ziet de plek ook" als keuze per gebruiker.
5. **Mogen de plaatsen van GeoNames (CC BY 4.0, 15 MB) en de landsgrenzen van Natural Earth
   (publiek domein, ±5 MB) bij de installatie gedownload worden, met een vermelding van de bron?**
   Standaard: **ja**, uit vaste kopieën met sha256 (GeoNames-data uit 2015), niet fataal, uit te
   zetten met `PLAATSEN=uit`. Wil je een nieuwere lijst, dan maken we een vaste kopie van
   `cities1000.zip` via een CI-job die je met de hand start.
6. **Wat ligt er onder de kaart?** Standaard: **alleen landsgrenzen** (Natural Earth, publiek
   domein), geen tegels van buiten. Straten vragen kaarttegels. Die van OpenStreetMap zijn niet
   voor zwaar gebruik, en een eigen tegelserver is groot (tientallen GB). Dat is later.
7. **Mag fase 6 ±5 weken duren in plaats van 3?** Met review per golf (zoals in fase 5) is de
   hele fase ±26 werkdagen (§5). Standaard: **ja, ±5 weken met alles.** Na golf 2 (±dag 14) zijn
   zoeken binnen 200 ms (ook in de web), tekst uit documenten, personen, herinneringen en slimme
   albums af. GPS, plaatsen, de kaart, de duplicaten en OCR komen daarna. Andere antwoorden:
   (b) **±4 weken**: na golf 2 alleen nog de uitrol (Z7) en de integratie, ±19 werkdagen. Z3, Z5
   en X1 (GPS, plaatsen, kaart, duplicaten, OCR) schuiven naar een fase 6b.
   (c) **3 weken**: alleen zoeken. Z0, golf 1, en dan Z4 met een korte Z7 ernaast (reservekopie,
   terugrollen en de tab op `/beheer`), ±15 werkdagen. Alle fotofuncties schuiven naar fase 6b.

---

## 2. Pakketten

### 2.0 Overzicht, eigendom en regels

Herzien in r2 (C8, C9). Revisie 1 had drie pakketten tegelijk in golf 1 en geen tijd voor
review. Fase 5 liet zien wat een golf echt kost: twee pakketten, dan een dag voor review, de
fixer en de nacontrole. Zo plannen we nu ook.

| Golf | Pakketten | Dagen | Na deze golf werkt |
|---|---|---|---|
| 0 | Z0 Fundament (Z0-api, Z0-web en Z0-e2e naast elkaar) | 3 + 1 review | migratie 0010, de contracten, de spike S1–S11, `bench --los` |
| 1 | Z1 Zoekmachine + Z2 Tekst | 4 + 1 review | zoeken op naam en inhoud binnen 200 ms (API), tekst uit documenten, de woorden per pagina (API) |
| 2 | Z4 Zoeken in de web + Z6 Personen, herinneringen, slimme albums en filters | 4 + 1 review | zoeken in de web; personen benoemen en vinden; herinneringen; slimme albums |
| 3 | Z3 Foto-gegevens (API) + Z5 Kaart en duplicaten (web) | 4 + 1 review | GPS, plaatsen, de kaart, duplicaten, de waarschuwing bij delen |
| 4 | Z7 Beheer en uitrol + X1 OCR | 3 + 1 review | `/beheer`, reservekopie, terugrollen, OCR (met ja op Vraag 1) |
| 5 | Integratie | 3 | samenvoegen, de metingen van §4.4 en §4.5, 0.8.0 |
| na fase 6 | X2 Gezichten automatisch | 5–6 | alleen na ja op Vraag 2 |

Samen ±26 werkdagen: 4 + 5 + 5 + 5 + 4 + 3. Dat is ±5 weken. Het stappenplan gaf 3 weken; dat is
Vraag 7. Na golf 2 (±dag 14) is het zoeken af, met personen, herinneringen en slimme albums.

**Waarom deze volgorde.** Z1 en Z2 zijn de kern; Z4 en Z6 bouwen op het register van filters
(Z1) en op de tekst (Z2). Z3 en Z5 hangen het minst aan de rest, en kunnen dus het best schuiven
(Vraag 7b). Z5 bouwt tegen de bevroren contracten van §3.4, met vaste antwoorden in vitest; zijn
e2e draait na de samenvoeging met Z3, op de reviewdag van golf 3. X1 komt na Z2, omdat hij een
ingang toevoegt aan `app/render.py`, dat in golf 1 van Z2 is.

**De reviewdag** (zoals fase 5): een review op veiligheid en haalbaarheid, de fixer met een test
per punt, en een nacontrole die elke fix even terugdraait om te zien dat de test faalt. Daarna
samenvoegen en alle checks (§4.6).

**Wat schuift, in deze volgorde, als een golf uitloopt:** plaatsnamen op de kaart (zoom ≥ 6),
dan de suggesties (de zoekpagina werkt zonder), dan de web van de bijna-duplicaten (de API
blijft), dan X1. Slimme albums schuiven niet half: zonder `album_item.shared` en de vraag bij
delen (Keuze 42) komen ze er niet in. Niets uit deze lijst raakt een "klaar als" van de kern.

**Niet in fase 6:** X2 (tot ja), objecten en semantisch zoeken (Vraag 3), GPS uit downloads
halen (fase 10, of Vraag 4b), de kaart in 50m en straten, Nederlandse provincienamen, stemming.

**Eigendom.** Een bestand heeft één eigenaar per golf. Z0 zet de stubs en de hotspots; daarna
bewerkt alleen de eigenaar het bestand. Herzien in r2 (C9): de paden kloppen nu met het repo, en
bestanden die twee pakketten nodig hadden, hebben er één.

| Bestand of map | Eigenaar |
|---|---|
| `api/alembic/versions/0010_zoeken.py`, `app/models.py` (nieuwe modellen), `app/search/schema.py`, `app/search/live.py` (`own_live`), `app/search/types.py` | Z0 |
| de hotspots van §3.5 (`main.py`, `cli.py`, `jobs.py`, `files.py`, `trash.py`, `userdelete.py`, `web/src/routes.tsx`, `web/src/Beheer.tsx`, `web/scripts/budget.mjs`) | Z0 |
| `app/search/{engine,query,access,snippets,deadline,like,suggest,limits,bench}.py`, `routers/search.py` | Z1 |
| `app/search/filters.py` (register en de filters `type`, `from`/`to`, `tag`, `library`, `in`) | Z1 |
| `app/analysis.py`, `app/extract/*`, `app/office/reader.py` (alleen een nieuwe stand "alle tekst"), `app/render.py` (ingang `text`), `app/previews.py` (de terugval zonder media), `routers/pages_words.py` | Z2 |
| `app/media/worker/ops/text.py`, de handdruk in `app/media/worker/run.py` | Z2 |
| `app/geo/*`, `app/dupes/*`, `app/render_meta.py`, `app/photometa.py` (v2), `app/media/worker/ops/meta.py` (locatie), `routers/{geo,photos_map,duplicates}.py`, `tools/geo/*`, `tools/kaart/*`, `app.cli geo-load` | Z3 |
| filters `place`, `country`, `camera` in `app/search/filters_photo.py` | Z3 |
| `web/src/search/**` (vervangt `web/src/Search.tsx` en `searchQuery.ts`), de zoekknop in `web/src/App.tsx`, `web/src/viewer/PdfView.tsx`, `web/src/viewer/PageViewer.tsx`, `web/src/viewer/TextLayer.tsx` | Z4 |
| `web/src/photos/map/**`, `web/src/dupes/**`, de waarschuwing in `web/src/ShareDialog.tsx` (lazy), de inhoud van `LocationRow` in `web/src/viewer/InfoPanel.tsx` | Z5 |
| `app/people/*`, `app/memories.py`, `app/albums_rule.py`, `routers/{people,memories,photo_prefs}.py`, filter `person`, `routers/photos.py`, `routers/albums.py`, `access._via_albums`, `public._in_album` | Z6 |
| `web/src/photos/people/**`, `web/src/photos/Memories.tsx`, `web/src/photos/{PhotosPage,AlbumPage,AddToAlbum}.tsx`, de regel in het albumdialoog, `PeopleRow` in het infopaneel | Z6 |
| `app/search/admin.py`, `routers/admin_search.py`, `web/src/admin/SearchTab.tsx`, `infra/single/*` (download van `geo`, de reservekopie, `.env.example`), `docs/runbooks/zoeken.md` | Z7 |
| `app/media/worker/ops/ocr.py`, `app/ocr/*`, de ingang `ocr_page` in `app/render.py` (golf 4, na Z2), de regel in `Dockerfile` met `chmod 0711`, `"tesseract"` in `app/media/worker/guard.py`, `infra/single/tests/test_ocr_container.sh` | X1 |

`InfoPanel.tsx` krijgt in Z0 twee lege plekken (`LocationRow`, `PeopleRow`); Z5 en Z6 vullen
elk hun eigen plek in een eigen bestand.

**Regels** (zoals fase 5):

- elk pakket in een eigen worktree; hooguit 3 tegelijk (de sandbox heeft 8 GB en hooguit 3 zware
  processen). In een golf zijn dat twee pakketten en de e2e;
- samenvoegen in de volgorde van de tabel; na elke samenvoeging alle checks (§4.6);
- geen nieuwe afhankelijkheid in `api/pyproject.toml` of `web/package.json` (B19). X1 voegt alleen
  na ja één regel aan de Dockerfile toe. X2 voegt alleen na ja een optionele extra toe;
- elk pakket eindigt met `docs/fase-6/z<n>.md`: wat er gebouwd is, wat afwijkt van dit ontwerp,
  en wat open is;
- een pakket met schermen levert een toetsenbordlijst `docs/fase-6/toetsenbord-z<n>.md`;
- elke "klaar als" hieronder heeft een test met een naam. Zonder die test is het pakket niet af;
- elk pakket met eigen teksten heeft een eigen i18n-gebied en een eigen foutcatalogus (Z0 maakt
  ze leeg aan, Keuze 48), zodat twee pakketten nooit in hetzelfde bestand schrijven;
- `e2e/slim.sh` (Z0) draait de snelle e2e lokaal in ±4 minuten. Hij moet groen zijn vóór een
  pakket "klaar" meldt; de volle e2e en `zoek-perf` draaien op de reviewdag (C16, C17).

### Z1 — Zoekmachine

**Doel.** `GET /api/v1/search` en `GET /api/v1/search/suggest` over de index, binnen 200 ms over
100.000 bestanden, met het toegangsfilter en grenzen voor de hele app.

**Bouwt:**

- `query.py`: `parse()` (Keuze 5) met de twee vormen per woord en ß naar "ss";
- `access.py`: de wortels en hun bereiken zonder staat (Keuze 14): een `OR` tot en met 64,
  daarboven `json_each`;
- `engine.py`: `FtsEngine` (Keuzes 4, 4a, 6, 7) en `select_engine()`; `like.py`: `LikeFallback`
  (de code van nu, verplaatst);
- `deadline.py` (Keuze 9): de timer met `interrupt()`, en de rondes A, B en C;
- `snippets.py` (Keuze 8): venster van 20.000 tekens, één reguliere expressie, in een thread,
  met het budget per verzoek en voor de app;
- `limits.py` (Keuze 13): 2 vragen tegelijk met een rij van 20, 1 per gebruiker, de snelheid per
  gebruiker, `429 search_busy`;
- de tests van de log zonder query string (Keuze 13); de regel zelf zet Z0 in `main.py`;
- `filters.py`: het register en de filters zonder foto's; de vorm "alleen filters" van Keuze 4a;
- `suggest.py`: namen (`fts_name` en voorvoegsel), tags, albums; personen en plaatsen via de
  haken van Z3 en Z6 (tot dan leeg);
- de tweede controle (Keuze 15), wissen in stukken (Keuze 11, in `routers/trash.py` en
  `userdelete.py` via een gedeelde helper);
- de herbouw in stukken met `search_state.cursor` (Keuze 12);
- `bench.py`: het zaad van 100.000 (§3.6), en `app.cli search-bench`, `search-rebuild`,
  `search-check`, `search-triggers`.

**Tests:**

- `test_search_query.py`: tabel met invoer en uitdrukking, met Één, café, Straße, GROẞE, grosse,
  İstanbul en ﬁets; fuzz met vaste seed: `parse()` geeft nooit een fout van FTS5;
- `test_search_access.py`: de wereld van §4.2, met 64, 65 en 1.000 wortels, en een deling die in
  hetzelfde verzoek wordt ingetrokken;
- `test_search_reference.py` (§4.3); `test_search_churn.py` op 3.53.1 en 3.46.1, ook met
  herhaalde schrijfacties;
- `test_search_no_replace.py` (de wachter van Keuze 3): een AST- en tekstscan van `app/` op
  `OR REPLACE`, `OR IGNORE`, `ON CONFLICT`, `.merge(`, `on_conflict_do_*` en `prefix_with("OR`
  bij de vier tabellen;
- `test_search_plans.py`: de plannen van Keuze 4 en 4a, met en zonder `ANALYZE`, voor beide
  vormen van de bereiken;
- `test_search_deadline.py`: een vraag die de grens raakt, geeft ≥ 1 rij als er een zichtbare
  treffer is in de namen of de eerste rijen van ronde B; een `interrupt()` die te laat komt, raakt
  de volgende vraag niet; de timer is uit vóór de verbinding terug in de pool gaat;
- `test_search_limits.py`: 3 vragen tegelijk geeft één keer wachten; 25 tegelijk geeft
  `429 search_busy`; een tweede vraag van dezelfde gebruiker stopt de eerste;
- `test_search_fallback.py`: zonder FTS5, met `PLATFORM_SEARCH_INDEX=uit`, en tijdens een herbouw;
- `test_search_rebuild.py`: een upload tijdens een herbouw wacht ≤ 1 s, en de index is daarna
  gelijk aan een volledige herbouw (`integrity-check`);
- `test_search_logs.py`: geen `q`, `bbox`, `z`, `place`, `country`, `person` of `day` in de log,
  ook niet bij de kaart op zoom 14 en bij `/photos?place=`;
- `test_search_snippets.py`: ß, een treffer na teken 20.000 (`snippet: null`, `match: "text"`),
  het budget van 40 ms met een nepklok, alleen tekst (geen HTML);
- `test_search_purge_batches.py`: 10.911 nodes, geen transactie langer dan 1 s.

**Klaar als:** alle tests hierboven groen op 3.53.1 en in `python:3.12-slim` (3.46.1, zoals V8);
`e2e/slim.sh` groen; de poorten van §4.4 die bij Z1 horen (`zoek-perf`) groen; geen
`search_access_mismatch` in de hele suite.

### Z2 — Tekst uit bestanden, backfill en de woorden per pagina

**Doel.** De inhoud van documenten in `blob_text`, gelezen in de zandbak, en de woorden per
pagina van B20 als API.

**Bouwt:**

- `app/analysis.py`: de tabel `analysis` met `owner_id`, `register_step()`, `queue()`, de rij
  `index`, `Defer`, en `write_text()` als enige schrijver van `blob_text` (Keuzes 3, 19, 49, 50);
- `app/extract/`: `pdf.py`, `office.py` (de stand "alle tekst" van `app/office/reader.py`),
  `plain.py`, `html.py`, `rtf.py`, `media.py`, `normalize.py` (Keuze 21); de ingang `text` in
  `app/render.py`, met de klok per blob (30 s CPU);
- de opdracht `text` in de worker (`ops/text.py`): één kind per batch van ≤ 16 blobs, met de
  handdruk (socket op fd 3, eerst niet-dumpbaar, dan `SCM_RIGHTS`) in `run.py` (Keuze 17);
- de terugval zonder media in `previews.render` (batches tot 50, de melding op `/beheer`);
- de haken: `files.add_ref` (nieuwe sha), de omzetting van OnlyOffice (oude formaten), media
  (titels);
- de backfill om de beurt per eigenaar (Keuze 20), en `indexing` in het antwoord van `/search`;
- `GET /api/v1/files/{id}/pages/{n}/words?token=` (Keuze 18): uit dezelfde render als de pagina,
  in de cache, met de limiter van de viewer;
- `GET /api/v1/files/{id}/analysis` (de stand per stap, voor de viewer en `/beheer`).

**Tests:**

- `test_extract_*.py` per formaat met de fixtures van §3.6; zip-bom, XML met `DOCTYPE`, een PDF
  van 2.001 pagina's, tekst van 2 MB (`truncated`), UTF-16, cp1252;
- `test_extract_subprocess.py`: RLIMIT, stopt bij een kapot bestand zonder de app te raken; een
  blob die te lang duurt, wordt `too_slow` zonder nieuwe poging; een kapotte blob in een batch van
  16 wordt gevonden door te halveren;
- `test_extract_no_pdfium_in_app.py` (zoals `test_media_no_ffmpeg_outside.py`): op het pad van
  de tekst roept het app-proces pypdfium2 en de kantoorlezer nooit zelf aan; alleen het kind doet
  dat;
- `test_media_text_handshake.py` (marker `media`): een tweede proces van uid 10002 kan
  `/proc/<pid>/fd` van het kind niet openen (`EACCES`), ook niet in de eerste milliseconden;
- `test_analysis_versions.py`; `test_backfill_fair.py`: Bram's nieuwe upload wacht hooguit één
  batch op de 100 trage PDF's van Eva;
- `test_backfill.py`: WAL: een gewone upload wacht nooit langer dan 1 s tijdens de backfill, ook
  met het profiel "kopieën" (1 MB tekst, 10 kopieën, C11);
- `test_pages_words.py`: toegang (lezen nodig, publiek `404`), het token met de versie, `ETag`;
  50 verzoeken tegelijk starten nooit meer dan `page_render_workers` processen;
- `e2e/test_zoek_z2.py`: upload docx → zoeken op een woord uit de tekst → snippet; hetzelfde met
  media uit (de terugval).

**Klaar als:** de tests groen; `e2e/slim.sh` groen; `e2e/perf/zoek_backfill.py` haalt
≥ 1.000 documenten per minuut per core over de hele keten (§4.5).

### Z3 — Foto-gegevens: GPS, plaatsen, camera, pHash, duplicaten en de kaart-API

**Doel.** Alles wat een foto over zichzelf zegt, per node bewaard, alleen voor de eigenaar en
alleen met zijn toestemming.

**Bouwt:**

- `render_meta.py` VERSION 2, de GPS-lezers (EXIF, XMP via `office.reader.sax()`, met de controle
  op elke waarde), de locatie in de worker-opdracht `meta` (`ops/meta.py`), `photometa.py` v2
  (Keuze 25);
- `blob_meta` (camera, lens, `gps`), `photo_geo` per node, `photo_pref.location` en de job die bij
  "uit" alles van die eigenaar wist (Keuzes 25a, 28);
- `tools/geo/` (de sha256 van beide bronnen), `app.cli geo-load`, `app/geo/places.py` (R*Tree
  voor plaatsen, punt-in-polygoon voor landen, niet fataal) (Keuze 29);
- pHash, de vlakke beelden en de tweede controle in photometa v2; `blob_hash`, `dup_pair` met
  `level`, `dup_ignore`, de klieken (Keuzes 35, 36);
- de filters `place`, `country`, `camera` in het register; de suggesties "plaatsen";
- de routes `GET /files/{id}/location`, `GET /photos/map`, `GET /photos/map/items`,
  `GET /photos/places`, `GET /geo/attribution`, `GET /duplicates`, `POST /duplicates/ignore`,
  `GET /photos/share-check` (Keuze 30).

**Tests:**

- `test_geo_read.py`: Pillow schrijft de GPS-IFD, heen en terug; HEIC; XMP met een "billion
  laughs" (geweigerd); `IFDRational(0, 0)`, |lat| > 90, (0, 0) en zonder `Ref` (geen plek); video
  met ISO 6709 via ffmpeg, marker `media`;
- `test_geo_never_public.py`: elke route onder `/public` en de publieke albumpagina: geen `lat`,
  `lon`, `place`, `geo`;
- `test_geo_access.py` (de wereld); `test_own_live.py` (Keuze 25a);
- `test_photo_rows_lifecycle.py`: de rijen van §3.9 voor prullenbak, terugzetten, wissen, andere
  eigenaar, nieuwe inhoud, "uit" en de kopie door Bram;
- `test_geo_load.py`: `geo-load` bouwt `places.sqlite` uit een kleine vaste CSV en GeoJSON die de
  test zelf maakt (stdlib); een verkeerde sha256 of een kapot bestand: geen plaatsen, de app start
  gewoon;
- `test_places.py` (marker `geo`, met de echte bronnen; in CI gedownload met sha256, lokaal uit
  de cache): de punten bij de grenzen van Keuze 29 en op zee, tegen de polygonen; de plaats tegen
  brute-force haversine op 1.000 punten; 180°;
- `test_phash.py`: de varianten van V5 (half zo groot, JPEG q35, 3–8% bijgesneden, ±4° gedraaid);
  vlakke beelden krijgen geen hash; de poort: 0 groepen `zeker` met twee verschillende originelen
  op de fixtures van §3.6; de grens van MAD64 ligt vast in de test;
- `test_dupes.py` (klieken; nooit iets vooraf gekozen); `test_map_clusters.py`;
  `test_location_off.py`; `test_share_check.py` (map, album met publieke link, los bestand,
  eigenaar met "uit", video zonder media).

**Klaar als:** de tests groen; `e2e/slim.sh` groen; `/photos/map` p95 ≤ 150 ms en `/duplicates`
p95 ≤ 300 ms in `zoek-perf`, of de tabel uit Keuze 4a als S10 dat zei.

### Z4 — Zoeken in de web

**Doel.** Het zoeken zoals een gebruiker het ziet, met het toetsenbord.

**Bouwt:** het veld in de bovenbalk en Ctrl/Cmd+K (Keuze 45); de suggesties (Keuze 46);
`/zoeken` met chips, snippets, "Meer laden" en de meldingen, ook de twee teksten van Keuze 9
(Keuze 47); de tekstlaag in de PDF-viewer met de markering van `?q=` (Keuze 18), in de lazy
chunk van de viewer; de teksten in het gebied `search` en de codes in
`web/src/i18n/errors/search.ts`; de begroting.

**Tests:** vitest voor `query.ts` (URL heen en terug), de combobox (pijlen, Esc, Enter,
`aria-activedescendant`), de snippets (alleen tekst, geen HTML), de meldingen bij `partial` en
`names_only`; `e2e/test_zoek_z4.py` (alleen toetsenbord: Ctrl+K, typen, pijl, Enter, filter,
Meer, de viewer met gemarkeerde woorden); `e2e/perf/zoek_ui.py` (rapport: eerste suggestie na een
toets, tekenen van `/zoeken`).

**Klaar als:** de tests groen; initiële JS ≤ 100 kB en Bestanden ≤ 45 kB; de toetsenbordlijst.

### Z5 — De kaart, de duplicaten en de waarschuwing in de web

**Doel.** Foto's op een kaart zonder tegels van buiten, opruimen van dubbele bestanden, en een
eerlijke waarschuwing bij delen.

**Bouwt:** `tools/kaart/maak_wereld.py` en `/kaart/world-110m.<hash>.json` (Keuze 31); de kaart
als SVG met projectie, slepen, zoom en toetsen; clusters van de server; de lijst "Plaatsen"
(Keuze 33); `/dubbel` met de groepen, de twee niveaus, "niet gelijk", en "naar de prullenbak" via
de batchroute, zonder iets vooraf te kiezen (Keuze 36); de waarschuwing in het deeldialoog,
lazy, bij elke soort deling (Keuze 30); de inhoud van `LocationRow`; de bronvermelding; de
gebieden `photos_map` en `dupes` en de catalogus `photos_map.ts`.

**Tests:** vitest voor de projectie (heen en terug, 180°), de clusters op het scherm, de toetsen,
de teksten van de waarschuwing (ook "onbekend"); `e2e/test_zoek_kaart.py` (telt verzoeken naar
andere hosts: 0; alleen toetsenbord); `e2e/test_zoek_dubbel.py` (niets vooraf aangevinkt);
`e2e/test_share_warning.py` (map, album, los bestand, publieke link).

**Klaar als:** de tests groen; `mapData` ≤ 60 kB en `mapRoute` ≤ 30 kB; initiële JS ≤ 100 kB en
Bestanden ≤ 45 kB (de waarschuwing is lazy); de toetsenbordlijst.

### Z6 — Personen, herinneringen, slimme albums en filters op foto's

**Doel.** Personen benoemen en zoeken; herinneringen; albums die zichzelf vullen, zonder iets
vanzelf te delen.

**Bouwt:**

- API: `person`, `photo_person` per node (Keuze 37), de routes van §3.4, het filter `person`, de
  groep personen in `/search` en de suggesties (Keuze 38), de haak bij een kopie door dezelfde
  eigenaar (Keuze 25a);
- `memories.py` en `memory_hide` (Keuze 41), met `own_live`;
- `albums_rule.py`, `album_rule` met `share_new`, `album_item.source` en `.shared`, `album_skip`,
  de verversing, `POST /albums/{id}/rule/confirm`, en `shared = 1` in `access._via_albums` en
  `public._in_album` (Keuze 42);
- `photo_pref` en `/me/photo-settings` (Keuze 44);
- web: "Persoon toevoegen" met kader in het infopaneel, `/fotos/personen`, de persoonpagina,
  "Vandaag, N jaar geleden" op `/fotos`, de regel en de vraag over delen in het albumdialoog,
  het merkje "alleen voor jou", filters op `/fotos`; het gebied `photos_people` en de catalogus
  `people.ts`.

**Tests:** `test_people.py` (alleen de eigenaar; samenvoegen; wissen; een kopie van dezelfde
eigenaar heeft de persoon ook, een kopie van Bram niet); `test_people_privacy.py` (de wereld: Bram
ziet geen personen op Eva's foto); `test_memories.py` (schrikkeldag, tijdzone, verbergen, de
prullenbak); `test_album_rules.py` (hand-items blijven; `album_skip`; grens 10.000; alleen eigen,
levende bestanden; een `rule`-item is niet gedeeld tot "ja"); `test_album_share_new.py` (Bram en
de publieke link zien een nieuw `rule`-item niet; na `confirm` wel);
`test_search_filters.py` (elk filter en elke combinatie van twee, tegen de referentie);
`e2e/test_zoek_personen.py` (alleen toetsenbord: persoon toevoegen met kader, zoeken op de naam,
filter); `e2e/test_album_regel.py` (een album met een regel en een publieke link, een nieuwe foto
die past: niet te zien via de link tot de eigenaar "ja" kiest).

**Klaar als:** de tests groen; `e2e/slim.sh` groen; "een persoon benoemen en vinden" werkt in
e2e; `/photos/memories` p95 ≤ 50 ms in `zoek-perf`.

### Z7 — Beheer, uitrol, update en terug

**Doel.** Jonas kan updaten, zien wat er gebeurt, en terug.

**Bouwt:** de tab op `/beheer` (Keuze 51) en `app/search/admin.py`; de reservekopie met alleen de
standaardbibliotheek in `install.sh`, en de opruimregel per alembic-head (Keuze 52); het
downloaden van de twee bronnen voor plaatsen naar `DATA_ROOT/geo/` met sha256, en `PLAATSEN` in
`.env.example` (Keuze 29); `app.cli bench --los` (de versie van Z0, nu met de uitkomst op
`/beheer`); `docs/runbooks/zoeken.md` (bijwerken, de index herbouwen, terug naar 0.7.0, de
meting); de belastingtest naast OnlyOffice en media (§4.5); de README.

**Tests:** `infra/single/tests/test_install_search.sh`: de reservekopie gemaakt met het image van
0.7.0 én van 0.8.0; een mislukte reservekopie stopt het script vóór elke wijziging; na twee runs
op 0010 staat de kopie van 0008 er nog; `PLAATSEN=uit`; een download die faalt, is niet fataal en
wordt de volgende run opnieuw geprobeerd; terug naar 0.7.0 met het script van de commit van
0.7.0, en weer vooruit (precies de stappen van §3.10). `e2e/test_zoek_beheer.py`; vitest voor de
tab.

**Klaar als:** de tests groen; de CI-job `infra` doet de weg terug echt; de toetsenbordlijst.

### X1 — OCR (wacht op Vraag 1)

**Doel.** Tekst uit scans en foto's van tekst, doorzoekbaar.

**Bouwt:** `Op.OCR` in de worker (`app/media/worker/ops/ocr.py`): kind 1 (`app.render ocr_page`
met de handdruk) maakt een grijs PGM, de worker controleert de kop, kind 2 is tesseract op stdin
met `RLIMIT_DATA` 512 MB en 60 s, OSD alleen bij een lage zekerheid (Keuze 22); `app/ocr/` in de
app (de rij `ocr`, `ocr_page`, `photo_ocr`, de knop "Tekst herkennen", `photo_pref.ocr_photos`,
de grenzen per gebruiker, `ocr_has_text`) (Keuzes 23, 24); tesseract `0711` en in
`guard.BINARIES`; de CI-job `ocr` (§4.6).

**Wat op Jonas wacht:** alleen de regel `apt-get install tesseract-ocr tesseract-ocr-nld
tesseract-ocr-eng tesseract-ocr-osd` in de Dockerfile, met `chmod 0711`. Zonder ja wordt X1 wel
samengevoegd, maar blijft OCR uit (Keuze 24). Zegt Jonas nee vóór golf 4 begint, dan valt X1 weg.

**Tests:** `test_ocr_unit.py` (deskew op 0°, 3°, 7°, −5°; TSV lezen; zekerheid; de kop van de PGM;
geen tesseract → `ocr_missing`; OSD alleen onder 60); `test_ocr_limits.py` (`429 ocr_limit`; een
foto van een ander → `404`; `ocr_has_text`); `test_ocr_real.py` (marker `ocr`, echte tesseract op
gemaakte scans van §3.6: recall per woord ≥ 0,95 op 0° en 3°, ≥ 0,9 op 7° en 90°; een foto met
EXIF-draaiing 6 en een HEIC); `infra/single/tests/test_ocr_container.sh` (modus `0711`, tesseract
alleen in de worker); `e2e/test_zoek_ocr.py` (marker `ocr`: een PDF zonder tekstlaag en een foto
van een bon → zoeken op een woord dat alleen in het beeld staat → snippet → de viewer markeert het;
een kopie van Bram vindt de tekst uit Eva's foto niet).

**Klaar als:** de tests groen in de job `ocr`; de poort ≥ 8 pagina's per minuut per core (§4.5);
met ja ook `media-image` groen met tesseract erin (+ ≤ 30 MB).

### X2 — Gezichten automatisch (na ja op Vraag 2; niet in fase 6)

**Doel.** Gezichten vinden en groeperen, als voorstel voor de personen van Z6.

**Bouwt:** de container `analyse` (Keuze 39) met `compose.analyse.yaml`, de sleutel en de
handdruk van de mediaworker (`wire.py` met een eigen label), YuNet en SFace met sha256 in
`models.lock`, `face` per node en `face_person` in migratie 0011, groeperen per eigenaar, de
voorstellen in de web, de schakelaar per gebruiker en het verbod van de beheerder, de job die bij
"uit" alles wist.

**Tests:** een vaste lijst van foto's uit het publiek domein (NASA) met sha256, opgenomen uitvoer
van de modellen voor de nep-worker, en met de echte modellen: zuiverheid ≥ 0,9 en recall ≥ 0,8
op groepen. `test_faces_off.py`: na "uit" 0 rijen in `face` voor die eigenaar; geen vector in
enig antwoord van de API. `test_analyse_container.sh` (uid 10003, geen netwerk, `read_only`).

**Klaar als:** de tests groen; de belastingtest met X2 aan houdt de poorten van §4.5.

---

## 3. Fundament (Z0, eerst en alleen)

Z0 duurt ±3 dagen plus een reviewdag, in drie delen naast elkaar: **Z0-api** (migratie, modellen,
contracten, `own_live`, stubs, hotspots, spike), **Z0-web** (routes, lege pagina's, de zoekknop,
de i18n-gebieden en catalogi, begrotingen) en **Z0-e2e** (harnas, fixtures, de wereld, de
referentie, `e2e/slim.sh`, CI-jobs). Z0 bouwt geen functie af; het legt vast waar de pakketten op
bouwen.

### 3.1 Migratie 0010 (`0010_zoeken.py`, down_revision `0009`)

Alles in ruwe SQL via `op.execute` (zoals 0005): FTS5, views en triggers kent Alembic niet. De
SQL van de triggers staat ook in `app/search/schema.py`; een test eist dat ze gelijk zijn. Op
PostgreSQL maakt de migratie alleen de gewone tabellen en kolommen (Keuze 10).

Herzien in r2 (C1, C2, C6, S8, S9, S13, S2). Deze vorm is gedraaid met 3.000 willekeurige
wijzigingen, een herbouw in stukken met wijzigingen ertussen, en daarna nog 1.000:
`integrity-check` ok op 3.45.1, 3.46.1 en 3.53.1 (V10).

**Kolommen op bestaande tabellen** (`ALTER TABLE … ADD COLUMN`, geen kopie van de tabel):

```sql
ALTER TABLE node ADD COLUMN num INTEGER;               -- vast nummer, rowid in FTS
ALTER TABLE node ADD COLUMN mpath TEXT;                -- '/1/7/42/'
ALTER TABLE node ADD COLUMN dead INTEGER NOT NULL DEFAULT 0;
ALTER TABLE node ADD COLUMN ftype TEXT GENERATED ALWAYS AS (<CASE op kind en mime>) VIRTUAL;
CREATE UNIQUE INDEX ix_node_num ON node (num);
CREATE INDEX ix_node_mpath ON node (mpath);
CREATE INDEX ix_node_trash ON node (mpath) WHERE deleted_at IS NOT NULL;
CREATE INDEX ix_node_live_mod ON node (modified_at DESC, num DESC) WHERE dead = 0;
CREATE INDEX ix_node_ftype_mod ON node (ftype, modified_at DESC, num DESC) WHERE dead = 0;
CREATE INDEX ix_node_owner_blob ON node (owner_id, blob_id) WHERE dead = 0;
ALTER TABLE album_item ADD COLUMN source TEXT NOT NULL DEFAULT 'hand';   -- 'hand' of 'rule'
ALTER TABLE album_item ADD COLUMN shared INTEGER NOT NULL DEFAULT 1;     -- Keuze 42
```

`ftype` is `folder`, `image`, `video`, `audio`, `pdf`, `document`, `spreadsheet`,
`presentation` of `other`. De `CASE` staat in `schema.py`; `test_ftype.py` eist dat hij gelijk is
aan de groepen in `app/mime.py`. Een virtuele kolom kost geen trigger en geen ruimte; toevoegen
met `ALTER TABLE`, een partiële index erop en weer weghalen werkt op beide versies (V10).

**Nieuwe tabellen.** Elke `blob_sha` is een FK naar `blob(sha256) ON DELETE CASCADE`; elke
`node_id` naar `node(id) ON DELETE CASCADE`; elke `user_id`/`owner_id` naar
`user(id) ON DELETE CASCADE`. Foto-gegevens over een plek, een persoon of tekst in een foto staan
per node (Keuze 25a); wat over de inhoud zelf gaat, per blob.

| Tabel | Kolommen | Sleutel en indexen | Pakket |
|---|---|---|---|
| `search_seq` | `id` (= 1), `next` | PK `id` | Z1 |
| `search_state` | `id` (= 1), `cursor` INT NULL (herbouw bezig tot en met dit `num`), `started_at` NULL, `what` NULL | PK `id` | Z1 |
| `blob_text` | `blob_sha`, `body` TEXT '', `ocr` TEXT '', `meta` TEXT '', `truncated` INT 0, `updated_at` | PK `blob_sha` | Z2 |
| `ocr_page` | `blob_sha`, `page` INT, `angle` REAL, `words` BLOB (zlib, JSON `[[t, x, y, w, h], …]`) | PK (`blob_sha`, `page`) | X1 |
| `analysis` | `blob_sha`, `step` (`text`, `hash`, `ocr`), `version` INT, `state` (`wait`, `busy`, `done`, `none`, `skip`, `failed`), `tries` INT 0, `code` TEXT NULL, `owner_id` NULL (FK `user` SET NULL), `queued_at`, `updated_at` | PK (`blob_sha`, `step`); `ix_analysis_todo (step, state, owner_id, queued_at)` | Z2 |
| `blob_meta` | `blob_sha`, `camera` TEXT NULL (gevouwen "merk model"), `lens` TEXT NULL, `gps` INT NULL (1, 0, of onbekend) | PK; `ix_blob_meta_camera (camera)` | Z3 |
| `photo_geo` | `node_id`, `owner_id`, `lat` REAL, `lon` REAL, `alt` REAL NULL, `x16` INT, `y16` INT, `place_id` INT NULL, `country` TEXT NULL, `admin1` TEXT NULL, `source` (`exif`, `xmp`, `video`) | PK `node_id`; `ix_photo_geo_tile (owner_id, x16, y16)`, `ix_photo_geo_place (owner_id, place_id)`, `ix_photo_geo_country (owner_id, country)` | Z3 |
| `blob_hash` | `blob_sha`, `state` (`ok`, `flat`, `none`), `phash` INT NULL (64 bits, met teken), `h0`–`h3` INT NULL (16 bits), `version` INT | PK; `ix_hash_0 (h0)` … `ix_hash_3 (h3)` | Z3 |
| `dup_pair` | `a`, `b` (sha, `a < b`), `dist` INT, `mad` REAL, `level` (`zeker`, `lijkt`) | PK (`a`, `b`), WITHOUT ROWID; `ix_dup_b (b)` | Z3 |
| `dup_ignore` | `user_id`, `key` TEXT (sha256 van de gesorteerde shas) | PK (`user_id`, `key`) | Z3 |
| `person` | `id` UUID, `owner_id`, `name` TEXT, `search_name` TEXT, `hidden` INT 0, `cover_node` NULL (FK `node` SET NULL), `created_at`, `updated_at` | PK; `ix_person_owner (owner_id, hidden, search_name)` | Z6 |
| `photo_person` | `person_id` (FK `person` CASCADE), `node_id`, `owner_id`, `x`, `y`, `w`, `h` REAL NULL, `source` (`hand`, `auto`) 'hand', `created_at` | PK (`person_id`, `node_id`); `ix_pp_node (node_id)`, `ix_pp_owner (owner_id, person_id)` | Z6 |
| `photo_ocr` | `node_id`, `owner_id`, `text` TEXT, `words` BLOB (zlib, per foto één "pagina"), `angle` REAL, `updated_at` | PK `node_id` | X1 |
| `photo_pref` | `user_id`, `location` (`mij`, `uit`) 'mij', `memories` INT 1, `ocr_photos` INT 0, `faces` (`uit`, `aan`) 'uit', `updated_at` | PK `user_id` | Z6 |
| `memory_hide` | `user_id`, `kind` (`day`, `folder`, `person`), `ref` TEXT | PK (`user_id`, `kind`, `ref`) | Z6 |
| `album_rule` | `album_id` (FK `node` CASCADE), `rule` TEXT (JSON), `share_new` INT 0, `confirmed_at` NULL, `refreshed_at`, `count` INT, `code` TEXT NULL | PK `album_id` | Z6 |
| `album_skip` | `album_id`, `node_id` (beide FK `node` CASCADE) | PK (`album_id`, `node_id`) | Z6 |

Weg ten opzichte van revisie 1: `blob_geo` (nu `photo_geo` per node) en `place_count` (een telling
die na prullenbak, terugzetten, wissen of "uit" niet meer klopt, S9). `photo_person` heeft
`node_id` in plaats van `blob_sha` (S8).

`setting` (sleutel `search` voor de schakelaars van `/beheer`) en `job` bestaan al.

**De view en de FTS-tabellen.** Nieuw in r2: `blob_id` en `node_id` in de view (de triggers
zoeken erop, C1), ß en ẞ naar "ss" (S5), en `photo_ocr` per node (S8).

```sql
CREATE TABLE search_seq (id INTEGER PRIMARY KEY CHECK (id = 1), next INTEGER NOT NULL);
CREATE TABLE search_state (id INTEGER PRIMARY KEY CHECK (id = 1), cursor INTEGER,
                           started_at TEXT, what TEXT);

CREATE VIEW search_src AS
  SELECT n.num AS num, n.blob_id AS blob_id, n.id AS node_id, n.search_name AS name,
         coalesce((SELECT group_concat(m.title, ' ') FROM media_item m WHERE m.node_id = n.id), '') AS extra,
         SS(coalesce(t.body, '')) AS body,
         SS(trim(coalesce(t.ocr, '') || ' ' || coalesce(po.text, ''))) AS ocr,
         SS(coalesce(t.meta, '')) AS meta
  FROM node n
  LEFT JOIN blob_text t ON t.blob_sha = n.blob_id
  LEFT JOIN photo_ocr po ON po.node_id = n.id;

CREATE VIRTUAL TABLE fts_node USING fts5(name, extra, body, ocr, meta,
  content='search_src', content_rowid='num', tokenize='unicode61 remove_diacritics 2');
CREATE VIRTUAL TABLE fts_name USING fts5(search_name,
  content='node', content_rowid='num', tokenize='trigram');
```

`SS(x)` is `replace(replace(x, 'ß', 'ss'), 'ẞ', 'ss')`. De opgeslagen tekst houdt de ß, zodat de
snippet "große" toont (Keuze 21). De view neemt ook albums mee: dan klopt de `delete` in FTS altijd
met wat erin staat. De vraag sluit ze uit (Keuze 4).

**De macro's.** In `schema.py` staan ze uitgeschreven.

- `IN(n)`: `((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR n <= (SELECT cursor FROM
  search_state WHERE id = 1))`. Buiten een herbouw is `cursor` `NULL` en geldt alles. Tijdens
  een herbouw schrijven de triggers alleen voor wat al opnieuw gevuld is (Keuze 12).
- `DEL(w)`: `INSERT INTO fts_node(fts_node, rowid, name, extra, body, ocr, meta) SELECT 'delete',
  num, name, extra, body, ocr, meta FROM search_src WHERE (w) AND IN(num);`
- `INS(w)`: `INSERT INTO fts_node(rowid, name, extra, body, ocr, meta) SELECT num, name, extra,
  body, ocr, meta FROM search_src WHERE (w) AND IN(num);`
- `NDEL(n, s)` en `NINS(n, s)`: hetzelfde voor `fts_name`, met `SELECT … WHERE IN(n)`.
- `END(p)`: `substr(p, 1, length(p) - 1) || '0'`.

**De triggers op `node`:**

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
  NINS((SELECT num FROM node WHERE id = NEW.id), NEW.search_name)
END;

-- naam of inhoud. Bij nieuwe inhoud eerst de foto-rijen van de oude inhoud weg (Keuze 25a);
-- de triggers van photo_ocr houden FTS daarbij gelijk. Dan de oude rij uit FTS.
CREATE TRIGGER node_bu_text BEFORE UPDATE OF search_name, blob_id ON node
WHEN OLD.num IS NOT NULL AND (OLD.search_name IS NOT NEW.search_name OR OLD.blob_id IS NOT NEW.blob_id) BEGIN
  DELETE FROM photo_ocr WHERE node_id = OLD.id AND OLD.blob_id IS NOT NEW.blob_id;
  DELETE FROM photo_geo WHERE node_id = OLD.id AND OLD.blob_id IS NOT NEW.blob_id;
  DEL(num = OLD.num)
  NDEL(OLD.num, OLD.search_name)
END;
CREATE TRIGGER node_au_text AFTER UPDATE OF search_name, blob_id ON node
WHEN NEW.num IS NOT NULL AND (OLD.search_name IS NOT NEW.search_name OR OLD.blob_id IS NOT NEW.blob_id) BEGIN
  INS(num = NEW.num)
  NINS(NEW.num, NEW.search_name)
END;

-- een andere eigenaar: niets van de oude eigenaar blijft aan de node hangen (Keuze 25a)
CREATE TRIGGER node_au_owner AFTER UPDATE OF owner_id ON node
WHEN OLD.owner_id IS NOT NEW.owner_id BEGIN
  DELETE FROM photo_ocr WHERE node_id = NEW.id;
  DELETE FROM photo_geo WHERE node_id = NEW.id;
  DELETE FROM photo_person WHERE node_id = NEW.id;
END;

-- wissen, ook via de FK-cascade [gemeten: de cascade start deze trigger]
CREATE TRIGGER node_bd BEFORE DELETE ON node WHEN OLD.num IS NOT NULL BEGIN
  DEL(num = OLD.num)
  NDEL(OLD.num, OLD.search_name)
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

**De triggers op `blob_text`, `photo_ocr` en `media_item`.** Een `media_item` of `photo_ocr`
waarvan `node_id` verandert, bestaat niet; een test houdt dat vast.

```sql
CREATE TRIGGER bt_bi BEFORE INSERT ON blob_text BEGIN DEL(blob_id = NEW.blob_sha) END;
CREATE TRIGGER bt_ai AFTER  INSERT ON blob_text BEGIN INS(blob_id = NEW.blob_sha) END;
CREATE TRIGGER bt_bu BEFORE UPDATE ON blob_text BEGIN DEL(blob_id = OLD.blob_sha) END;
CREATE TRIGGER bt_au AFTER  UPDATE ON blob_text BEGIN INS(blob_id = NEW.blob_sha) END;
CREATE TRIGGER bt_bd BEFORE DELETE ON blob_text BEGIN DEL(blob_id = OLD.blob_sha) END;
CREATE TRIGGER bt_ad AFTER  DELETE ON blob_text BEGIN INS(blob_id = OLD.blob_sha) END;

CREATE TRIGGER po_bi BEFORE INSERT ON photo_ocr BEGIN DEL(node_id = NEW.node_id) END;
CREATE TRIGGER po_ai AFTER  INSERT ON photo_ocr BEGIN INS(node_id = NEW.node_id) END;
CREATE TRIGGER po_bu BEFORE UPDATE ON photo_ocr BEGIN DEL(node_id = OLD.node_id) END;
CREATE TRIGGER po_au AFTER  UPDATE ON photo_ocr BEGIN INS(node_id = NEW.node_id) END;
CREATE TRIGGER po_bd BEFORE DELETE ON photo_ocr BEGIN DEL(node_id = OLD.node_id) END;
CREATE TRIGGER po_ad AFTER  DELETE ON photo_ocr BEGIN INS(node_id = OLD.node_id) END;

CREATE TRIGGER mi_bi BEFORE INSERT ON media_item BEGIN DEL(num = (SELECT num FROM node WHERE id = NEW.node_id)) END;
CREATE TRIGGER mi_ai AFTER  INSERT ON media_item BEGIN INS(num = (SELECT num FROM node WHERE id = NEW.node_id)) END;
CREATE TRIGGER mi_bu BEFORE UPDATE OF title ON media_item BEGIN DEL(num = (SELECT num FROM node WHERE id = OLD.node_id)) END;
CREATE TRIGGER mi_au AFTER  UPDATE OF title ON media_item BEGIN INS(num = (SELECT num FROM node WHERE id = NEW.node_id)) END;
CREATE TRIGGER mi_bd BEFORE DELETE ON media_item BEGIN DEL(num = (SELECT num FROM node WHERE id = OLD.node_id)) END;
CREATE TRIGGER mi_ad AFTER  DELETE ON media_item BEGIN INS(num = (SELECT num FROM node WHERE id = OLD.node_id)) END;
```

Bij `blob_text` werkt de `DEL` vóór het invoegen alleen goed als er nog geen rij is; daarom is
een gewone `INSERT` de enige vorm, na een `SELECT` in `write_text()` (Keuze 3). Een rij die er
al is, krijgt een `UPDATE`.

**Vullen in de migratie** [gemeten op 104.696 nodes, SQLite 3.46.1: 2,8 s]:

```sql
UPDATE node SET num = rowid;                                      -- uniek; daarna los van rowid
INSERT INTO search_seq VALUES (1, coalesce((SELECT max(num) FROM node), 0) + 1);
INSERT INTO search_state VALUES (1, NULL, NULL, NULL);
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

Een `rebuild` in één statement mag hier: de app draait tijdens de migratie niet. Daarna nooit
meer (Keuze 12). Zoeken op naam werkt meteen na de migratie. De inhoud komt met de backfill
(Keuze 20).

**De herbouw in stukken** (`app.cli search-rebuild`, `/admin/search/rebuild` met `names` of
`text`):

```sql
-- één keer, in een eigen transactie
INSERT INTO fts_node(fts_node) VALUES ('delete-all');
INSERT INTO fts_name(fts_name) VALUES ('delete-all');
UPDATE search_state SET cursor = 0, started_at = :nu, what = :wat WHERE id = 1;
-- per stuk van 2.000, elk in een eigen korte transactie
INSERT INTO fts_node(rowid, name, extra, body, ocr, meta)
  SELECT num, name, extra, body, ocr, meta FROM search_src WHERE num > :cur AND num <= :hi;
INSERT INTO fts_name(rowid, search_name) SELECT num, search_name FROM node WHERE num > :cur AND num <= :hi;
UPDATE search_state SET cursor = :hi WHERE id = 1;          -- het laatste stuk: cursor = NULL
```

`:hi` is het 2.000e `num` na `:cur`; het laatste stuk loopt tot `search_seq.next - 1`, gelezen in
dezelfde transactie. Een node die tijdens de herbouw nieuw is, krijgt een `num` boven de cursor
en komt dus in het laatste stuk. Gemeten op 200.000 rijen: `delete-all` 17–250 ms, het langste
stuk 101–142 ms (V4).

**Downgrade naar 0009:** eerst alle triggers, dan de view, de twee FTS-tabellen (hun
schaduwtabellen gaan mee), de nieuwe tabellen, de zes indexen op `node`, dan
`ALTER TABLE node DROP COLUMN` voor `ftype`, `dead`, `mpath` en `num`, en `ALTER TABLE album_item
DROP COLUMN` voor `shared` en `source` (SQLite ≥ 3.35, beide builds; V10 voor `ftype`). Geen
kopie van `node`. `db-downgrade` waarschuwt vóór "ja": de index, de plekken, de hashes, de
personen en de regels van albums gaan weg; bestanden, delingen en albums blijven. Een item dat
`shared = 0` had, is na de downgrade weer gedeeld zoals in 0.7.0: `db-downgrade` noemt het aantal
en vraagt of de regel-items eerst uit het album moeten (standaard ja).

**Tests** (`tests/test_migration_0010.py`, zoals die van 0008):

- leeg naar head; 0009 met data naar 0010 en terug, en weer heen: bestanden, delingen en albums
  blijven gelijk; `num` uniek; elk pad klopt met de ouders; `dead` klopt met de prullenbak;
  `integrity-check` van beide FTS-tabellen slaagt;
- `compare_metadata` tussen de modellen en de migratie, met de schaduwtabellen van FTS
  (`fts_*_data`, `_idx`, `_docsize`, `_config`) en de view eruit gefilterd;
- `test_schema_triggers.py`: de triggers in `sqlite_master` zijn gelijk aan `schema.TRIGGERS`;
- zonder FTS5 (een SQLite zonder, via een vlag in de test): de migratie slaat FTS over, de app
  draait met `LikeFallback`;
- een migratie van 100.000 nodes duurt < 10 s (marker `zoek_perf`);
- de downgrade met een album met `rule`-items die niet gedeeld waren: de vraag, en na "ja" zijn
  die items uit het album.

### 3.2 Instellingen

Nieuw in `.env` (`install.sh` zet de standaard één keer):

```
# Zoeken en foto's (fase 6)
PLAATSEN=aan                # aan | uit: plaatsnamen en landen downloaden en laden (Vraag 5)
```

In de container van de app (compose zet ze, of de standaard geldt):

| Variabele | Standaard | Wat |
|---|---|---|
| `PLATFORM_SEARCH_INDEX` | `aan` | `uit` zet `LikeFallback` terug (Keuze 10) |
| `PLATFORM_SEARCH_DEADLINE_MS` | `120` | grens van ronde A; ronde B heeft 40 ms (Keuze 9) |
| `PLATFORM_SEARCH_INFLIGHT` | `2` | FTS-vragen tegelijk voor de hele app (Keuze 13) |
| `PLATFORM_SEARCH_QUEUE` | `20` | wachtenden; daarboven `429 search_busy` |
| `PLATFORM_SEARCH_MAX_TEXT` | `1048576` | tekens per blob |
| `PLATFORM_SEARCH_TIMING` | `false` | `Server-Timing` op `/search` (alleen tests en metingen) |
| `PLATFORM_TEXT_BATCH` | `16` | blobs per kind in de worker (Keuze 17) |
| `PLATFORM_PLACES_DB` | `/data/geo/places.sqlite` | gemaakt door `geo-load`; ontbreekt hij, dan geen namen |
| `PLATFORM_GEO_SRC` | `/data/geo` | waar `install.sh` de bronnen neerzet |
| `PLATFORM_INDEX_DEFER_TRANSCODES` | `2` | `index` en `ocr` wachten bij zoveel transcodes |
| `PLATFORM_OCR_MAX_PAGES` | `200` | per document (X1) |
| `PLATFORM_OCR_PER_HOUR` | `20` | verzoeken per gebruiker per uur (X1) |
| `PLATFORM_OCR_PAGES_PER_DAY` | `200` | pagina's per gebruiker per dag (X1) |

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
    after: int | None = None                        # ronde B: verder na deze rowid (Keuze 9)

class SnippetPart(BaseModel): t: str; m: bool
class Hit(BaseModel):
    node: NodeOut; path: str
    match: Literal["name", "title", "text", "ocr", "meta"]
    snippet: list[SnippetPart] | None
class SearchPage(BaseModel):
    results: list[Hit]; more: bool; offset: int
    partial: bool                                   # tijdsgrens geraakt (Keuze 9)
    ranked: bool                                    # False na ronde A
    names_only: bool                                # ronde C
    after: int | None                               # voor de volgende pagina na ronde B
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
@asynccontextmanager
async def deadline(conn: AsyncConnection, ms: int) -> AsyncIterator[Deadline]: ...
# zet loop.call_later(ms, raw.interrupt); .hit na afloop; de timer is altijd uit vóór de
# verbinding terug naar de pool gaat. Een vraag die stopt, geeft OperationalError("interrupted"),
# die deadline() opvangt.

# app/search/limits.py
async def admit(user: User) -> AsyncContextManager[None]: ...   # 429 search_busy of rate_limited

# app/search/live.py (Z0, vast; Keuze 25a)
def own_live(n: Alias, me: UUID) -> ColumnElement[bool]: ...    # owner, dead = 0, kind = 'file'
OWN_LIVE_SQL = "{n}.owner_id = :me AND {n}.dead = 0 AND {n}.kind = 'file'"

# app/search/filters.py
def register(name: str, apply: ApplyFilter, suggest: SuggestFn | None = None) -> None: ...
# app/search/hooks.py (zoals media.hooks: pakketten melden zich aan)
def register_groups(fn: GroupsFn) -> None: ...          # Z3 plaatsen, Z6 personen
def register_suggest(fn: SuggestFn) -> None: ...

# app/analysis.py
class Step(StrEnum): TEXT = "text"; HASH = "hash"; OCR = "ocr"
VERSIONS: dict[Step, int] = {Step.TEXT: 1, Step.HASH: 1, Step.OCR: 1}
def register_step(step: Step, lane: str, eligible: Eligible, run: Runner) -> None: ...
async def queue(db: AsyncSession, sha: str, steps: Iterable[Step], *, owner: UUID | None,
                first: bool = False) -> None: ...
async def write_text(db: AsyncSession, sha: str, *, body: str | None = None,
                     ocr: str | None = None, meta: str | None = None,
                     truncated: bool = False) -> None: ...   # SELECT, dan INSERT of UPDATE

# app/extract/result.py
@dataclass(frozen=True)
class Extracted:
    sha: str
    state: Literal["done", "none", "skip", "failed"]
    chars: int; truncated: bool; scan_pages: list[int]; meta: str; code: str | None
    # de tekst zelf volgt als eigen stuk op de pipe (lengte vooraf)

# app/media/wire.py
class Op(StrEnum): ...; TEXT = "text"; OCR = "ocr"     # de worker geeft not_built tot Z2 en X1

# app/geo/types.py
@dataclass(frozen=True)
class Location: lat: float; lon: float; alt: float | None; source: Literal["exif", "xmp", "video"]
@dataclass(frozen=True)
class Place: id: int | None; name: str | None; admin1: str | None; cc: str | None; km: float | None

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
publieke link. Voor elk pad onder `/search`, `/photos` en `/people` gaat de query string niet in
de toegangslog (Keuze 13).

**Zoeken**

| Methode en pad | Vraag | Antwoord |
|---|---|---|
| `GET /search` | `q` (≤ 200), `type` (meer keer), `from`, `to`, `person` (meer keer), `place` of `country`, `tag` (meer keer), `library`, `camera`, `in`, `offset` (0–1.000), `limit` (1–50, standaard 30), `after` | `SearchPage` (§3.3). Zonder `q` en zonder filter: `400 search_bad_filter`. Te druk: `429 search_busy` met `Retry-After: 1`. |
| `GET /search/suggest` | `q` (1–200) | `{"names": [NameRef], "people": [PersonRef], "places": [PlaceRef], "tags": [TagRef], "albums": [AlbumRef]}`, elk ≤ 5 |

`Hit` blijft `{node, path}` zoals in 0.7.0 en krijgt `match` en `snippet` erbij. De web van nu
leest alleen `results` en `more`, en werkt dus gewoon door.

**Tekst**

| Methode en pad | Wie | Antwoord |
|---|---|---|
| `GET /files/{id}/pages/{n}/words?token=` | het download-token van de viewer (met de versie) | `{"page": n, "words": [{"t", "x", "y", "w", "h"}], "source": "pdf" \| "ocr"}`; `ETag`, `Cache-Control: private`; `404 not_found` als er geen is; 20 per seconde per gebruiker |
| `GET /files/{id}/analysis` | leesrecht | `{"text": "done", "hash": "done", "ocr": "none", "truncated": false, "code": null}` |
| `POST /files/{id}/ocr` (X1) | een foto: alleen de eigenaar; een PDF: schrijfrecht | `202`; `409 ocr_running`; `503 ocr_off`; `429 ocr_limit`; `409 ocr_has_text` |

**Foto's, plaats en kaart** (alleen eigen, levende bestanden: `own_live`)

| Methode en pad | Antwoord |
|---|---|
| `GET /files/{id}/location` | `{"lat", "lon", "alt", "place": {"name", "admin1", "cc"} \| null}`; `404 not_found` als er geen is of je hem niet mag zien |
| `GET /photos/map?bbox=w,s,e,n&z=0..14&<filters>` | `{"cells": [{"x", "y", "n", "lat", "lon", "cover": uuid}]}` (≤ 500) |
| `GET /photos/map/items?bbox=&<filters>&cursor=` | `{"items": [PhotoItem], "cursor"}` |
| `GET /photos/places?<filters>` | `{"places": [{"id", "name", "admin1", "country", "n"}]}` (`n` per verzoek geteld) |
| `GET /photos/share-check?node=` | `{"with_location": int, "unknown": int}` voor een map, een album (alleen de items die gedeeld worden) of een los bestand van jezelf; geteld met `blob_meta.gps` |
| `GET /geo/attribution` | `{"places": {"source", "license", "date"} \| null, "countries": {"source", "license", "version"} \| null, "world": {"source", "license"}}` |
| `GET /photos` (bestaat) | krijgt de filters van Keuze 43 |
| `GET /photos/memories?day=JJJJ-MM-DD` | `{"years": [{"years_ago", "day", "items": [PhotoItem]}]}` |
| `GET`, `POST /photos/memories/hide`, `DELETE /photos/memories/hide/{kind}/{ref}` | `{"kind": "day" \| "folder" \| "person", "ref": str}` |
| `GET`, `PATCH /me/photo-settings` | `{"location": "mij" \| "uit", "memories": bool, "ocr_photos": bool}` |

**Duplicaten** (alleen eigen, levende bestanden)

| Methode en pad | Antwoord |
|---|---|
| `GET /duplicates?kind=exact\|near&cursor=` | `{"groups": [{"key", "kind", "level": "zeker" \| "lijkt" \| null, "dist": int \| null, "items": [{"node", "path", "size", "taken_at"}]}], "cursor"}` |
| `POST /duplicates/ignore` | `{"key"}` → `204`; `400 dupes_bad_key` |

Opruimen gaat via de bestaande batchroute van de prullenbak.

**Personen** (alleen eigen; een id van een ander geeft `404 not_found`)

| Methode en pad | Antwoord |
|---|---|
| `GET /people?hidden=0\|1` | `{"people": [{"id", "name", "count", "cover": uuid \| null, "hidden"}]}` (`count` en `cover` met `own_live`) |
| `POST /people` | `{"name"}` → persoon; `409 people_name_taken` |
| `PATCH /people/{id}` | `{"name"?, "hidden"?, "cover"?}` |
| `POST /people/{id}/merge` | `{"into": uuid}` → persoon; `400 people_merge_self` |
| `DELETE /people/{id}` | `204` |
| `GET /people/{id}/photos?cursor=` | `PhotoItem`-lijst |
| `GET /files/{id}/people` | `{"people": [{"person": PersonRef, "box": [x, y, w, h] \| null, "source"}]}`; voor een ander `[]` |
| `POST /files/{id}/people` | `{"person_id"}` of `{"name"}`, `"box"?` → `201` (alleen de eigenaar van de node) |
| `DELETE /files/{id}/people/{person_id}` | `204` |

**Slimme albums:**

| Methode en pad | Antwoord |
|---|---|
| `PUT /albums/{id}/rule` | `{"rule": SearchParams zonder offset, limit en after, "share_new"?: bool}` → `200`, met `"needs_confirm": true` als het album gedeeld is of een publieke link heeft en `share_new` niet gezet is |
| `POST /albums/{id}/rule/confirm` | `{"share_new": bool}` → `200`. `true` zet `share_new` op 1 en alle `rule`-items op `shared = 1`; `false` laat ze voor de eigenaar |
| `DELETE /albums/{id}/rule` | `204`; `rule`-items blijven, met hun `shared` |
| `POST /albums/{id}/refresh` | `202` |
| `GET /albums/{id}` (bestaat) | geeft de eigenaar `rule`, `share_new`, `refreshed_at`, `count` en per item `source` en `shared`; anderen zien alleen items met `shared = 1` |

Een nieuwe deling of publieke link op een album met een regel en `share_new = 0` geeft in het
antwoord `"rule_items_private": n`; het deeldialoog toont dan de vraag van Keuze 42. Codes
`album_rule_invalid`, `album_rule_too_many`.

**Beheer:** `GET /admin/search` (de stand van Keuze 51), `PATCH /admin/search` (`{"ocr"?,
"paused"?}`), `POST /admin/search/rebuild` (`{"what": "names" | "text" | "geo" | "hash" | "ocr"}`
→ `202`; `409 search_rebuild_running`), `GET /admin/search/failed?cursor=` (sha, stap, code,
tijd), `POST /admin/search/retry`.

**Gebeurtenissen** (SSE, zoals `preview.ready`): `search.ready {"nodes": [uuid], "step"}` naar
wie de nodes kan zien (de viewer ververst de tekstlaag); `people.changed {}` naar de eigenaar;
`album.changed` (bestaat) na een verversing, alleen naar wie het item mag zien;
`search.progress` alleen naar beheerders, elke 10 s tijdens een backfill of herbouw.

**Foutcodes**, elk in de catalogus van zijn pakket (NL en EN; `test_error_catalog.py`):

- `web/src/i18n/errors/search.ts` (Z1, Z4): `search_query_too_long`, `search_bad_offset`,
  `search_bad_filter`, `search_busy`, `search_rebuild_running`;
- `web/src/i18n/errors/photos_map.ts` (Z3, Z5): `dupes_bad_key`, `geo_not_loaded`;
- `web/src/i18n/errors/people.ts` (Z6): `people_name_taken`, `people_merge_self`,
  `album_rule_invalid`, `album_rule_too_many`, `memories_bad_hide`;
- `web/src/i18n/errors/photos.ts` (bestaat; X1 voegt toe): `ocr_off`, `ocr_running`,
  `ocr_limit`, `ocr_has_text`.

Codes in `analysis.code` (alleen op `/beheer`, niet als HTTP-fout): `too_slow`, `ocr_missing`,
`ocr_bad_image`, `ocr_has_text`, `zip_limit`, `xml_refused`, `pages_limit`. Bestaande HTTP-codes:
`not_found`, `rate_limited`, `guest`, `read_only`.

### 3.5 Hotspots die Z0 zelf aanpast

| Bestand | Wat |
|---|---|
| `app/jobs.py` | `LANES` + `index`, `ocr`; `active_lanes` (§3.3); `test_jobs_lanes.py` bij |
| `app/main.py` | routers mounten (stubs geven `501` tot hun pakket); de query string weg uit de log voor `/api/v1/search`, `/api/v1/photos` en `/api/v1/people`, en `q` in `SECRET_PARAMS`; lifespan: de backfills (stubs) |
| `app/config.py` | de instellingen van §3.2 |
| `app/cli.py` | `search-bench`, `search-rebuild`, `search-check`, `search-triggers`, `geo-load` (stubs), en `db-backup` meteen af: `sqlite3.Connection.backup` naar `/data/backup/platform-<head>-<tijd>.db`, terwijl de app draait; migreert niets; per head de nieuwste (Keuze 52) |
| `app/routers/files.py` | in `add_ref()`: `search.hooks.blob_added(sha, owner)` na de commit; bij kopiëren de haak `photo_copy(src, dst)` (Z6 vult hem: personen mee als de eigenaar gelijk is) |
| `app/routers/trash.py`, `app/userdelete.py` | wissen via de gedeelde helper in stukken (stub die het nu in één keer doet; Z1 maakt de stukken) |
| `app/previews.py` | `register_ready(fn)`: na `preview.ready` (voor photometa v2) |
| `app/render.py` | ingang `text` (stub die `none` geeft); de plek voor `ocr_page` (stub) |
| `app/media/wire.py` | `Op.TEXT`, `Op.OCR` (de worker geeft `not_built` tot Z2 en X1) |
| `app/routers/search.py` | via `select_engine`; tot Z1 alleen `LikeFallback` |
| `web/src/App.tsx`, `routes.tsx`, `router.ts` | het zoekveld (leeg), Ctrl/Cmd+K, de nieuwe routes met lege pagina's |
| `web/src/Beheer.tsx` | de tab "Zoeken en foto's" (leeg) |
| `web/src/viewer/InfoPanel.tsx` | plekken `LocationRow`, `PeopleRow` (elk een eigen bestand) |
| `web/src/ShareDialog.tsx` | een lazy plek voor de waarschuwing (`import()` bij openen) |
| `web/src/i18n/areas/*`, `web/src/i18n/errors/*` | de lege gebieden `search`, `photos_map`, `dupes`, `photos_people` (NL en EN) en de catalogi `search.ts`, `photos_map.ts`, `people.ts` |
| `web/scripts/budget.mjs` | regels voor de nieuwe chunks: `search`, `mapRoute` ≤ 30 kB, `mapData` ≤ 60 kB, `dupes`, `people` |
| `e2e/slim.sh` | de snelle e2e lokaal (±4 minuten): de wereld, zoeken, één upload met tekst, de toetsen |

### 3.6 Het harnas en de fixtures

- **De boom van 100.000** (`api/app/search/bench.py`, B's generator als module): 30 gebruikers,
  ±5.900 mappen tot diepte 5, 100.000 bestanden, 20.000 documenten met samen ±235 miljoen tekens
  (Zipf, NL en EN met diakrieten, kunstwoorden), 2.000 kopieën bij een ander, 3% van de mappen
  en 1% van de bestanden in de prullenbak, delingen naar personen, groepen en gasten, 55.000
  foto's waarvan 60% met GPS (in `photo_geo` van hun eigenaar). Via de echte migratie en de echte
  triggers. Profielen: "groot" (200 documenten van 1 MB tekst) en "kopieën" (1 MB tekst met 10
  kopieën, C11).
- **De wereld** (`api/tests/search/wereld.py`): Eva, Bram, Cas, Gijs, Dirk, Ada, Fien, Hans en
  Iris met hun mappen, delingen, albums en bestanden (§4.2). Gedeeld door alle tests op toegang.
- **De referentie** (`api/tests/search/referentie.py`): zoeken in gewoon Python over dezelfde
  boom van 2.000 nodes: de definitie van "wat moet gevonden worden" (§4.3).
- **Gemaakte bestanden** (`api/tests/search/fixtures.py`, gemaakt bij de test en bewaard in
  `api/.pytest_cache/search`, zoals de media-fixtures; niets binairs in het repo):
  - PDF met tekst: een eigen kleine PDF-schrijver (Helvetica, stdlib), 1–20 pagina's, twee
    kolommen, U+00AD, een emoji, "Straße" en "GROẞE";
  - PDF als scan: een JPEG-pagina (Pillow) zonder tekstlaag;
  - docx, xlsx, pptx, odt, ods, odp: zip met vaste XML-sjablonen (stdlib), met voetnoten, kop-
    en voetteksten, sprekersnotities, 20.000 cellen; en een zip-bom en een XML met `ENTITY`;
  - scans voor OCR (X1): Pillow tekent tekst met DejaVu (uit Debian in de job) op grijs papier
    met ruis, op 0°, 3°, 7°, −5° en 90°, als PNG en JPEG q80, NL en EN;
  - foto's met EXIF en GPS: Pillow schrijft de GPS-IFD; een telefoonfoto met EXIF-draaiing 6;
    HEIC via pillow-heif; XMP met GPS, en XMP met een "billion laughs"; `IFDRational(0, 0)`;
    video met `location` via ffmpeg (marker `media`);
  - bijna-duplicaten (S1): elk origineel met vier varianten; 150 brieven op één sjabloon; gescande
    formulieren met andere invulling; screenshots van één app met andere tekst; 150 bijna egale
    foto's (zwart, lucht, muur); en andere foto's;
  - punten voor plaatsen: de grensparen van Keuze 29 en punten op de Noordzee.
- **Plan-tests** (`api/tests/search/plans.py`): `assert_plan(sql, args, require_first=…,
  forbid=["AUTOMATIC"], forbid_index_terms=["parent_id=?"])`, voor de vormen van Keuze 4 en 4a.
- **e2e** (`e2e/search_harness.py`): start de app met een gezaaide database (de wereld, of 1.000,
  of 100.000) en geeft ingelogde gebruikers met elke rol. `e2e/slim.sh` gebruikt de wereld.

### 3.7 De spike (Z0-api, dag 1–2)

| # | Wat | Waarom | Klaar als |
|---|---|---|---|
| S1 | De tijdsgrens via `interrupt()` uit een timer, door aiosqlite en de pool van SQLAlchemy heen, met de rondes A, B en C | Keuze 9; een progress-handler is te grof (V3) | een vraag van 2 s stopt na 120 ± 30 ms; een vraag met een zichtbare treffer geeft ≥ 1 rij; de verbinding werkt daarna gewoon; een late `interrupt()` raakt de volgende vraag niet |
| S2 | De triggers met de ORM: hernoemen, verplaatsen, prullenbak, de batch van 500, kopie, `userdelete`, `purge_expired`, de import van fase 5, een andere eigenaar, nieuwe inhoud; en de herbouw in stukken met uploads ertussen | Keuze 3, 12, 25a | na elke stap is de index gelijk aan een herbouw; een upload tijdens de herbouw wacht ≤ 1 s |
| S3 | 0010 heen en terug met data; `compare_metadata` | §3.1 | precies 0009 na de downgrade |
| S4 | De plan-tests in `python:3.12-slim`, met en zonder `ANALYZE` | J12 | groen in CI in het image |
| S5 | Tesseract in Debian 13: grootte, en of ffmpeg `libtesseract` al meebrengt | Vraag 1 | een getal in `docs/fase-6/spike.md` (alleen gemeten in CI; het image verandert niet) |
| S6 | `world-110m.<hash>.json` maken en meten | Keuze 31 | ≤ 60 kB gzip, of een andere vereenvoudiging |
| S7 | Het zaad van 100.000 in CI | §4.4 | < 3 minuten |
| S8 | De twee vormen per woord (J1) en ß op 3.46.1 en 3.53.1 | Keuze 5, 21 | ß, ẞ, ligaturen en İ vinden naam én tekst; "grosse" en "große" vinden elkaar, ook "strasse" en "Straße" |
| S9 | `tools/zoekbench/bench.py`: alleen stdlib, maakt het schema van 0010 en een boom van 100.000 in een tijdelijke map, en meet zoeken voor "eigenaar" en "alles" | Keuze 54, B5 | draait in de container van 0.7.0 met `dc exec -T platform python -I - --los < bench.py`, in < 5 minuten, en ruimt op |
| S10 | De vormen van Keuze 4a op 3.46.1, onder "eigenaar" en "alles": alleen filters, de kaart, de duplicaten | C6 | per vorm een p95; haalt een vorm zijn poort niet, dan de tabel of de poort uit Keuze 4a, vastgelegd in `spike.md` |
| S11 | De handdruk van Keuze 17 in de echte worker-container: een tweede proces van uid 10002 probeert `/proc/<pid>/fd/N` van het kind te openen, vóór en na "klaar" | S6 | altijd `EACCES` (of het pad bestaat niet); nooit de inhoud van een blob |

Met S9 kan Jonas al in week 1 meten wat zijn CPU haalt, zonder iets te installeren. Z7 maakt er
`app.cli bench --los` van, met de uitkomst op `/beheer`.

### 3.8 Klaar als (Z0)

- 0010 heen en terug, ook zonder FTS5;
- `own_live` en `schema.TRIGGERS` staan vast, met hun tests;
- alle routers gemount (stubs), de web-app bouwt met de lege pagina's en blijft binnen budget;
- de spike S1–S11 staat in `docs/fase-6/spike.md`, met wat het ontwerp daardoor verandert;
- de fixtures maken alles van §3.6; de wereld en de referentie hebben eigen tests;
- `e2e/slim.sh` is groen;
- de CI-jobs van §4.6 bestaan en zijn groen op de stubs;
- api, api-media, web, e2e, office en media-image zijn groen zoals in 0.7.0.

### 3.9 Elke gebeurtenis, wat er gebeurt, en het bewijs

Herzien in r2: de rijen voor foto-gegevens per node (S8, S9, S17) en voor slimme albums (S2).

| Gebeurtenis | Index (namen en paden) | Inhoud (`blob_text`) | Foto-gegevens per node | Bij zoeken en foto's | Test |
|---|---|---|---|---|---|
| upload | trigger, zelfde transactie | job `text` als de sha nieuw is | photometa v2 leest GPS als de eigenaar "mij" heeft | naam meteen; inhoud na de job (seconden) | `test_upload_name_then_text` |
| kopie door dezelfde eigenaar | trigger | niets: de sha is er al | personen gaan mee (haak); plek opnieuw gelezen | meteen, met inhoud | `test_copy_same_owner` |
| kopie door een ander (Bram kopieert Eva's foto) | trigger | niets | niets gaat mee; Bram krijgt een plek alleen met zijn eigen "mij" | Bram ziet geen plek, persoon of OCR-tekst van Eva | `test_copy_other_owner` |
| hernoemen | trigger (`search_name`) | niets | blijft | meteen | `test_rename` |
| verplaatsen binnen de eigen boom | trigger: paden van de submap | niets | blijft | meteen | `test_move` |
| andere eigenaar (`owner_id` verandert) | trigger: paden | niets | `node_au_owner` wist `photo_geo`, `photo_person`, `photo_ocr` van die node | de oude eigenaar ziet niets meer; de nieuwe pas als photometa v2 voor hem leest, met zijn eigen "mij" | `test_owner_change_wipes` |
| inhoud vervangen, nieuwe versie | trigger (`blob_id`) | job voor de nieuwe sha | `photo_geo` en `photo_ocr` weg; personen blijven, zonder kader | de oude tekst nooit meer | `test_replace_no_old_text` |
| versie terugzetten | trigger (`blob_id`) | de oude sha heeft zijn rij nog | als bij vervangen | meteen | `test_restore_version` |
| naar de prullenbak (bestand of map) | trigger: `dead` voor de submap | niets | rijen blijven; `own_live` laat ze weg | weg uit zoeken, kaart, plaatsen, herinneringen, personen, duplicaten en slimme albums | `test_trash_subtree`, `test_own_live` |
| terug uit de prullenbak | trigger: `dead` | niets | rijen zijn er weer | terug | `test_restore` |
| definitief wissen, gebruiker wissen | cascade → `node_bd`, per 2.000 | niets | weg via de cascade | weg | `test_purge_cascade` |
| blob opgeruimd | — | cascade: `blob_text`, `blob_meta`, `blob_hash`, `ocr_page` | — (per node al weg) | — | `test_gc_cascade` |
| locatie "uit" | — | — | één job wist alle `photo_geo` van die eigenaar; niets schrijft er nog een | kaart en plaatsen meteen leeg; `blob_meta.gps` blijft voor de waarschuwing | `test_location_off` |
| locatie weer "mij" | — | — | de backfill leest zijn foto's opnieuw | na de job | `test_location_on_again` |
| OCR in een foto (X1) | — | — | `photo_ocr` van die node; de triggers werken FTS bij | alleen bij die node van die eigenaar | `test_photo_ocr_per_node` |
| OCR in een PDF (X1) | — | `ocr` gevuld (trigger werkt FTS bij) | — | wie het document mag lezen | `test_ocr_searchable` |
| delen met persoon, groep of gast | niets | niets | niets | `shared_roots` neemt het meteen mee; foto-gegevens blijven van de eigenaar | `test_share_now_visible` |
| deling ingetrokken, verlopen, lid uit groep | niets | niets | niets | meteen weg (en de tweede controle) | `test_unshare_now_gone` |
| album | niet als treffer | — | — | alleen in de suggesties | `test_album_not_searched` |
| slimme album: nieuwe foto past | — | — | `album_item` met `source = 'rule'`, `shared = share_new` | met `share_new = 0` alleen voor de eigenaar; via een deling of link niet te zien | `test_album_share_new` |
| slimme album: eigenaar zegt "ja" | — | — | alle `rule`-items `shared = 1` | nu ook voor wie het album gedeeld kreeg | `test_album_confirm` |
| slimme album: item naar de prullenbak | trigger: `dead` | — | item blijft in `album_item`; `own_live` laat het weg bij verversen | weg uit het album (zoals in 0.7.0 via `live_items`) | `test_album_rule_trash` |
| publieke link | — | — | — | geen zoekroute; geen snippet, plek of persoon; in een album alleen `shared = 1` | `test_public_never_search` |
| tag erbij of eraf | — | — | — | bij de vraag, meteen | `test_tag_now` |
| titel in de mediabibliotheek | trigger op `media_item` | — | — | meteen | `test_media_title` |
| OnlyOffice maakt een PDF (oude formaten) | — | haak → job `text` | — | na de job | `test_officepdf_text` |
| persoon benoemd of weg | — | — | `photo_person` van die node | groep en filter meteen | `test_person_now` |
| herbouw van de index | `delete-all`, dan stukken met `search_state.cursor` | — | — | `LikeFallback` tot hij klaar is | `test_search_rebuild` |

### 3.10 De update en terug, voor Jonas

**Bijwerken naar 0.8.0** (hetzelfde commando als altijd, met de branch, J3):

```sh
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/claude/stappenplan-uitwerken-lu4uv3/infra/single/install.sh | PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 bash
```

Zonder `PLATFORM_BRANCH` haalt het script de code van `main` (`install.sh:21`) [code]. Zo staat
het ook in `infra/single/README.md`. Staat 0.8.0 later op `main`, dan vervalt dat deel.

Wat het script doet, in deze volgorde (herzien in r2: C7, C12, C13):

1. De gewone stappen tot en met de instellingen (ongewijzigd); `PLAATSEN=aan` erbij als hij
   ontbreekt.
2. **Nieuw:** de bronnen voor plaatsen (Keuze 29), als `PLAATSEN=aan` en ze er nog niet zijn:
   naar `DATA_ROOT/geo/` (op de LXC `/tank/platform/geo/`), elk met sha256. Lukt dat niet, dan
   zegt het script "Plaatsnamen: niet geladen" en gaat door; de volgende run probeert het weer.
3. Office netjes laten opslaan (`checkpoint`, zoals nu).
4. **Nieuw:** een reservekopie van de database, terwijl de oude app nog draait, met het image dat
   er nu is (dat van 0.7.0) en alleen de standaardbibliotheek:

   ```sh
   "${compose[@]}" run --rm --no-deps platform python -I -c '<backup>'
   ```

   `<backup>` leest de alembic-head (`0008`), en schrijft met `sqlite3.Connection.backup` naar
   `/data/backup/platform-0008-<tijd>.db` (op de LXC `/tank/platform/backup/`). Daarna blijft per
   head alleen de nieuwste, en de laatste kopie van vóór een migratie gaat nooit weg. Lukt de kopie
   niet, dan stopt het script hier: er is dan nog niets veranderd. Bij een eerste installatie (nog
   geen database) slaat het script dit over. Dit werkt met elk image, want `app.cli db-backup`
   bestaat in 0.7.0 niet (V9).
5. Het nieuwe image bouwen en de app starten (`up -d --build`, zoals nu, `install.sh:552`); de
   migratie 0010 draait (seconden); de app is gezond (`wait_health`). Bij de start maakt de app
   `/data/geo/places.sqlite` uit de bronnen (`geo-load`, ±1 minuut).
6. Media, dan office (zoals nu, B47).
7. Het script zegt: "Zoeken op naam werkt meteen. De inhoud wordt nu op de achtergrond gelezen;
   volg het op /beheer → Zoeken en foto's."

**Wat er daarna gebeurt** (op een CPU als in de sandbox; `bench --los` meet het op de LXC).
Herzien in r2 met de start van de kinderen (V2), de batches en de polygonen (V6):

| Stap | Werk voor 100.000 bestanden (20.000 documenten, 55.000 foto's) | Tijd op één core |
|---|---|---|
| tekst uit documenten, in de worker | lezen (200.000 pagina's × 2,2 ms, plus Office) + een kind per 16 blobs (±7 ms per blob) + schrijven per blob | ±10–15 min; poort ≤ 20 min (≥ 1.000 per minuut) |
| index via de triggers | 235 miljoen tekens | ±30 s [gemeten] |
| GPS, camera en pHash (photometa v2, batches van 200) | 55.000 × (0,24 ms EXIF + ±1,5 ms pHash + bestanden openen) | ±2,5–5 min [het openen niet getest] |
| land en plaats | 55.000 × (0,81 ms polygonen + 0,06 ms plaats) | ±48 s [gemeten per stuk] |
| tweede controle bij duplicaten | alleen kandidaat-paren, twee voorbeelden elk | seconden tot een minuut, afhankelijk van het aantal paren |
| OCR (X1, alleen na ja) | per gescande pagina 3–8 s | ±70–110 min per 1.000 pagina's |

**Terug naar 0.7.0** (als 0.8.0 niet goed is). Herzien in r2 (C7): het script komt van de commit
van 0.7.0 zelf, niet van de branch. Zo draait het oude script met het oude image. `dc` is de
functie uit `infra/single/README.md` ("Handige commando's"): `docker compose` met precies de
bestanden die het script gebruikt.

```bash
# 1. kijken of 0.7.0 er is, en het script van 0.7.0 in een bestand, vóór je iets stopt
git -C /opt/platform/src fetch -q origin
git -C /opt/platform/src cat-file -e '1130e1bbe86d9e4242c0effb588d7671599f5d8a^{commit}' && echo "goed: 0.7.0 is er"
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/1130e1bbe86d9e4242c0effb588d7671599f5d8a/infra/single/install.sh -o /root/platform-install-0.7.0.sh && echo "goed: het script is er"
# 2. stoppen, zodat niets meer naar de nieuwe tabellen schrijft
dc stop platform media
# 3. het schema terug, met het image van nu (vraagt om "ja")
dc run --rm --no-deps platform python -m app.cli db-downgrade 0008
# 4. 0.7.0 installeren, met het script van 0.7.0
PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 PLATFORM_SRC_REF=1130e1bbe86d9e4242c0effb588d7671599f5d8a bash < /root/platform-install-0.7.0.sh
```

- De downgrade moet met het image van 0.8.0 (dat kent 0010). Daarom eerst stoppen, dan
  `db-downgrade`, dan pas het oude script. Zeggen de twee regels van stap 1 niet allebei "goed",
  dan stop je daar (zoals in het runbook van media).
- De downgrade gooit de index, de plekken, de hashes, de personen en de regels van albums weg.
  Bestanden, delingen, albums en alles van fase 1–5 blijven. Items uit een regel die niet gedeeld
  waren, haalt `db-downgrade` eerst uit het album (de vraag van §3.1): in 0.7.0 deelt een album
  elk item.
- Lukt de downgrade niet, dan is er de reservekopie van stap 4: stoppen, het bestand
  `platform-0008-<tijd>.db` terugzetten als `platform.db`, het script van 0.7.0.
- **Weer vooruit:** het script van de branch, zonder `PLATFORM_SRC_REF`. De index vult zich
  opnieuw (zie de tabel). Personen die je met de hand benoemde, zijn weg: dat staat in het runbook
  en in de bevestiging van `db-downgrade`.
- `1130e1b` is de laatste commit vóór Z0; de app-code is gelijk aan `92a0d04` (CI groen op alle
  jobs). Komen er vóór Z0 nog commits, dan zet Z7 hier de laatste. Bij de integratie komt er een
  tag `v0.7.0` op. De CI-job `infra` doet precies deze stappen (`test_install_search.sh`).

---

## 4. Tests en meten

### 4.0 Klaar als → bewijs

| Klaar als (stappenplan) | Bewijs | Job |
|---|---|---|
| Een zoekopdracht over 100.000 bestanden antwoordt binnen 200 ms | `api/tests/search/test_perf_100k.py` en `e2e/perf/zoek_100k.py` (§4.4): p95 ≤ 200 ms per toegangsprofiel (de mediaan van de p95 over 3 rondes), via HTTP, op SQLite 3.46.1; op de LXC `bench --los` | `zoek-perf` (hard) |
| OCR-tekst is doorzoekbaar | `e2e/test_zoek_ocr.py` (marker `ocr`, echte tesseract): een PDF zonder tekstlaag en een foto van een bon → zoeken op een woord dat alleen in het beeld staat → snippet → de viewer markeert het | `ocr` (hard). In het image pas na ja op Vraag 1. |
| Gezichten zijn te benoemen en te zoeken | `e2e/test_zoek_personen.py`: een persoon met kader benoemen, zoeken op de naam, filteren; `test_people_privacy.py`. Automatisch vinden: X2, na ja op Vraag 2. | `e2e`, `api` (hard) |
| Full-text voor pdf, docx, xlsx, pptx, txt, md | `api/tests/extract/test_*.py` per formaat; `e2e/test_zoek_z2.py` | `api`, `e2e` |
| EXIF en GPS, kaart, offline plaatsnamen | `tests/geo/*`; `e2e/test_zoek_kaart.py` (telt verzoeken naar andere hosts: 0) | `api`, `e2e` |
| Duplicaten en bijna-duplicaten | `tests/dupes/*` (met de poort van Keuze 36), `e2e/test_zoek_dubbel.py` | `api`, `e2e` |
| Herinneringen en slimme albums | `tests/photos/test_memories.py`, `tests/albums/test_album_rules.py`, `test_album_share_new.py`, `e2e/test_album_regel.py` | `api`, `e2e` |
| Filters: type, datum, persoon, locatie, tag, bibliotheek | `tests/search/test_search_filters.py`: elk filter en elke combinatie van twee, tegen de referentie | `api` |
| Alles met het toetsenbord (sinds fase 3) | e2e met alleen het toetsenbord per scherm; `docs/fase-6/toetsenbord-z*.md` | `e2e` |

Eerlijk: zonder ja op Vraag 1 haalt het image "OCR-tekst is doorzoekbaar" niet; de code is
bewezen in CI. Zonder ja op Vraag 2 is "gezichten" half: benoemen en zoeken wel, vinden niet.
Met antwoord (b) of (c) op Vraag 7 schuiven kaart, duplicaten en OCR (en bij (c) ook personen,
herinneringen en slimme albums) naar fase 6b. Zo staat het in de README en op `/beheer`.

### 4.1 De lagen

| Laag | Waar | Draait met | Wat |
|---|---|---|---|
| Puur | `api/tests/{search,extract,geo,dupes,photos,albums,people}/` | `uv run pytest` | `parse`, snippets, pHash en de tweede controle, plaatsen, de GPS-lezers, filters, regels, herinneringen |
| API | `api/tests/test_*.py` | httpx tegen de app (ASGI) | routes, toegang, events, foutcodes, wissen, grenzen |
| Migratie en triggers | `tests/test_migration_0010.py`, `test_search_churn.py` | 3.53.1, en lokaal en in CI in `python:3.12-slim` (3.46.1, zoals V8) | heen, terug, zonder FTS5, churn, herbouw |
| Worker | `api/tests/media/test_text_*.py`, `test_ocr_*.py` | markers `media` en `ocr` | de opdrachten `text` en `ocr`, de handdruk |
| e2e | `e2e/test_zoek_*.py` | Chromium (Playwright van buiten, B29), marker `zoek` | de schermen, alleen met het toetsenbord |
| Web | `web/src/**/*.test.ts(x)` | vitest | `query.ts`, combobox, projectie, clusters, snippets als tekst, de waarschuwing |
| Prestaties | marker `zoek_perf`, `e2e/perf/zoek_*.py` | §4.4–4.5 | tijden, backfill, CPU naast de rest |
| Installatie | `infra/single/tests/test_install_search.sh`, `test_ocr_container.sh` | bash | reservekopie, plaatsen, terugrollen, tesseract `0711` |

**Snel lokaal** (C16): `e2e/slim.sh` draait de tests met marker `zoek` of `triggers` in
`python:3.12-slim`, met de venv van de host alleen-lezen erin en `--network none`, en daarna de
snelle e2e. Zo kan elk pakket 3.46.1 aantonen vóór het samenvoegen, zonder te pushen.

**Wachters die altijd draaien** (in `api`):

- `test_schema_triggers.py`: de triggers in `sqlite_master` zijn gelijk aan `schema.TRIGGERS`;
- `test_search_no_raw.py`: geen `MATCH` naast een f-string of `%`; alleen `parse()` maakt de
  uitdrukking (AST);
- `test_search_no_replace.py`: op `node`, `blob_text`, `media_item` en `photo_ocr` geen
  `INSERT OR REPLACE`, `OR IGNORE`, `ON CONFLICT`, `.merge(`, `on_conflict_do_update`,
  `on_conflict_do_nothing` of `prefix_with("OR`; en `blob_text` alleen via `write_text()` (AST en
  tekst);
- `test_no_batch_alter_node.py`: geen `batch_alter_table("node")` in een migratie na 0010;
- `test_no_temp_table.py`: geen `CREATE TEMP` in `app/search` (Keuze 14);
- `test_own_live.py`: elke fotofunctie gebruikt `own_live` (Keuze 25a);
- `test_search_logs.py`: zoek op `geheimwoord7`, vraag de kaart op zoom 14 rond een bekende plek
  en filter op een plaats; daarna staat geen van die woorden of getallen in de logs, ook niet in
  de toegangslog, en niet bij `search_access_mismatch`;
- `test_geo_never_public.py`: alle routes onder `/api/v1/public` en de publieke albumpagina,
  met een foto met GPS: geen `lat`, `lon`, `place` of `geo` in enig antwoord (JSON doorlopen),
  en niet in `preview_meta`;
- `test_no_mismatch.py`: een fixture die elke test laat falen als de log
  `search_access_mismatch` bevat;
- `test_media_no_ffmpeg_outside.py` (bestaat): nu ook `tesseract`;
- `test_extract_no_pdfium_in_app.py`: op het pad van de tekst geen pypdfium2 in het app-proces;
- `test_error_catalog.py` (bestaat): elke nieuwe foutcode in NL en EN, in de catalogus van zijn
  pakket.

### 4.2 De toegangsmatrix

Eén vaste wereld (`api/tests/search/wereld.py`), gedeeld door `test_search_access.py`,
`tests/geo/test_geo_access.py`, `tests/people/test_people_privacy.py`, `test_own_live.py` en de
e2e `test_zoek_toegang.py` (B).

- **Eva** (eigenaar), locatie `mij`. Haar map `Werk` is gedeeld met Bram, met groep `Team` en
  met gast Gijs. Een submap van `Werk` is ook apart gedeeld (dubbel pad). Een publieke link op
  `Werk/Foto's`. Een album `Utrecht` met een regel (`place=Utrecht`), gedeeld met Bram en met een
  publieke link, `share_new = 0`.
- **Bram** (deling lezen, en schrijven op `Werk/Inbox`), **Cas** (lid van `Team`), **Gijs**
  (gast), **Dirk** (niets gedeeld), **Ada** (beheerder, niets gedeeld), **Fien** (deling
  verlopen), **Hans** (deling ingetrokken), **Iris** (gewist).
- In `Werk`: een document met `zebrakwarts` in de tekst; een scan met `ocrwoordx` alleen in het
  beeld (X1); een foto met GPS (Utrecht), persoon "Anna" en OCR-tekst `bonwoordy`; een kopie en
  een verkleinde kopie van die foto; een bestand in de prullenbak; een map die uit `Werk` naar
  Eva's eigen map is verplaatst. **Bram heeft een kopie** van de foto in zijn eigen map.

**Wat ziet wie** (ja = zichtbaar; nee = afwezig, niet "geweigerd"):

| Wie | Treffer en snippet | Suggestie | Plaats, kaart, `/location` | Download | Personen, OCR-tekst uit foto's | Duplicaten, herinneringen |
|---|---|---|---|---|---|---|
| Eva | ja | ja | ja | origineel | ja | ja |
| Bram, Cas, Gijs | ja, binnen `Werk` | namen binnen `Werk`; geen personen of plaatsen | nee (`404`); "Utrecht" vindt de foto niet | origineel (zoals 0.7.0; Vraag 4) | nee (`[]`, `404`); `bonwoordy` vindt niets | nee (alleen eigen) |
| Bram, op zijn eigen kopie | ja (zijn bestand) | ja | nee: geen plek van Eva; alleen een eigen plek met zijn eigen "mij" | origineel | nee: geen "Anna", geen `bonwoordy` | alleen zijn eigen |
| Bram, in het album `Utrecht` | — | — | — | items met de hand: ja; `rule`-items: nee tot Eva "ja" zegt | nee | — |
| Dirk, Ada | nee | nee | nee | `404` | nee | nee |
| Fien, Hans | nee, in hetzelfde verzoek na verlopen of intrekken | nee | nee | `404` | nee | nee |
| Iris (gewist) | — | — | — | — | haar personen weg (cascade, 0 rijen) | — |
| Bram: bestand in de prullenbak | nee | nee | nee | `404` | nee | nee |
| Bram: de verplaatste map | nee, meteen na het verplaatsen | nee | nee | `404` | nee | nee |
| Publieke link | geen zoekroute (`404`) | alleen het filter op namen in de browser (zoals nu) | nooit | origineel (zoals nu); in het album alleen `shared = 1` | nooit | nooit |

Per cel een test. Daarnaast:

- **Geen kanaal via de volgorde** (B). Twee werelden die alleen verschillen in een bestand dat
  Bram niet ziet: Bram krijgt dezelfde set treffers en dezelfde snippets, zonder score of totaal.
  bm25 telt wel over alles en kan de volgorde van bijna gelijke treffers een beetje verschuiven;
  de test eist alleen gelijke sets. Het restrisico staat bij R7.
- **Bram zet met schrijfrecht OCR aan** op Eva's foto in `Werk/Inbox`: `404` (alleen de
  eigenaar). Op een PDF daar: `202`, met de grenzen per gebruiker (Keuze 23).
- **Eva zet locatie op `uit`**: `/location` geeft `404`, de kaart is leeg, en na de job zijn er
  0 rijen in `photo_geo` met `owner_id` = Eva. Bram's kopie houdt alleen wat Bram zelf heeft.
- **Eva's eigen foto in de prullenbak** staat niet in herinneringen, kaart, plaatsen, duplicaten,
  personen, suggesties of de verversing van slimme albums, en komt na terugzetten terug.

### 4.3 Wat zoeken betekent: de referentie, de vragenlijst en churn

- **De referentie is de definitie** (B). Elke vraag draait op de boom van 2.000 nodes door de app
  en door `referentie.py`. De sets moeten gelijk zijn. De referentie vouwt elk woord van de tekst
  en de vraag met `fold()` en ß naar "ss", en kent het voorvoegsel op het laatste woord.
- **De vragenlijst** (`api/tests/search/vragen.tsv`, 200 vragen): hele woorden, voorvoegsels,
  stukken van namen, korte woorden, diakrieten, ß en "ss" in beide richtingen (`grosse`,
  `strasse`, `Straße`, `GROẞE`), cijfers, frases, lege uitkomsten, vragen met operators (`AND`,
  `NEAR`, `*`, `"`, `:`) die gewone tekst moeten blijven.
- **De volgorde** wordt apart getest: een treffer op de naam staat boven een treffer in de tekst
  met hetzelfde woord; een treffer op een heel woord boven een stuk van een naam; bij gelijke
  score op `num`.
- **Churn** (B, uitgebreid in r2: C2, S13): 2.000 willekeurige wijzigingen met een vaste seed
  (upload, kopie, hernoemen, verplaatsen, prullenbak in en uit, versie, tekst erbij en opnieuw
  geschreven, OCR in een foto, andere eigenaar, definitief wissen, gebruiker wissen), met een
  herbouw in stukken halverwege. Na elke 100: `integrity-check`, de index tegen een herbouw, en
  42 vragen tegen de referentie. 0 verschillen, op 3.53.1 (`api`) en 3.46.1 (`zoek-perf` en
  `e2e/slim.sh`).

### 4.4 De benchmark: 100.000 bestanden binnen 200 ms

**Opzet** (B; `api/tests/search/test_perf_100k.py`, marker `zoek_perf`; de HTTP-kant in
`e2e/perf/zoek_100k.py`):

1. Een gemigreerde database met de boom van §3.6 (`--n 100000 --seed 6`).
2. De app start zoals in productie (`python -m app.serve`, WAL, `busy_timeout` 5000), in een
   container van `python:3.12-slim`: dezelfde SQLite 3.46.1 als het image. De test controleert
   `sqlite_version()`.
3. Zes toegangsprofielen: eigenaar (1 wortel); ontvanger met 5 en met 20 delingen; groepslid;
   gast (2 delingen); "alles" (30 hoofdmappen, het ergste geval). Plus de profielen "groot" (200
   documenten van 1 MB) en "kopieën" voor de snippets en het schrijven.
4. 20 vragen om op te warmen, dan de 200 van §4.3, 3 rondes, één voor één, via HTTP met een
   ingelogde sessie, vanuit een ander proces.
5. Gemeten bij de client. De stappen uit `Server-Timing` (`roots`, `fts`, `round`, `names`,
   `check`, `snip`) alleen met `PLATFORM_SEARCH_TIMING=1`.

**Poorten per profiel** (herzien in r2: C6, C17, S12, C5). Een p95 is de mediaan van de p95 van
de 3 rondes:

| Wat | Poort | Soort |
|---|---|---|
| `GET /search` | p95 ≤ 200 ms | hard |
| `GET /search` | p99 ≤ 300 ms | rapport |
| volgende pagina's (`offset` 30–990) | p95 ≤ 200 ms | hard |
| alleen filters, zonder vraag (Keuze 4a) | p95 ≤ 150 ms als S10 een SQL-p95 ≤ 100 ms mat, anders ≤ 200 ms | hard |
| `GET /search/suggest` | p95 ≤ 50 ms | hard |
| `snip` uit `Server-Timing`, ook in "groot" | p95 ≤ 40 ms | hard |
| `/photos/map` (55.000 met GPS) | p95 ≤ 150 ms | hard |
| `/duplicates` (55.000 foto's) | p95 ≤ 300 ms | hard |
| `/photos/memories` | p95 ≤ 50 ms | hard |
| 4 clients tegelijk, elk de vragenlijst | p95 ≤ 300 ms | hard |
| `GET /health` tijdens die 4 clients | p95 ≤ 50 ms (de event loop blijft vrij) | hard |
| hoe vaak ronde B of C nodig was | rapport | rapport |
| eerste vraag na een herstart | rapport (doel ≤ 300 ms) | rapport |

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
haalt het nog. De mediaan over 3 rondes haalt één trage ronde weg. Alleen een rapport (p99, de
max) mag na twee keer falen in een week zonder wijziging in `app/search` naar de integratie
verhuizen. **De p95 ≤ 200 ms blijft altijd hard in CI** (C17). Dat staat als regel in
`docs/fase-6/e2e.md`.

**Op de LXC:** `bench --los` (Keuze 54) meet "eigenaar" en "alles" met dezelfde vragen. Haalt
de CPU van Jonas p95 ≤ 200 ms niet, dan zegt `/beheer` het; de tijdsgrens van 120 ms met de
rondes B en C (Keuze 9) houdt het antwoord dan nog onder de 200 ms, met de eerlijke melding.

### 4.5 Meten: backfill en CPU naast OnlyOffice en media

**Snelheid van de backfill** (`e2e/perf/zoek_backfill.py`, elk 3 keer, één core, niets anders
actief). Herzien in r2 (C3, C10): de poort meet de hele keten, met het starten van de kinderen en
het schrijven.

| Stap | Invoer (gemaakt) | Poort per core | Verwachting |
|---|---|---|---|
| tekst, in de worker | 2.000 documenten: 40% PDF (1–20 pagina's), 30% docx, 10% xlsx, 10% pptx, 10% txt, html, rtf | ≥ 1.000 per minuut | 1.500–2.000 per minuut [H3, H8, V2] |
| tekst, zonder media (de terugval) | dezelfde 2.000 | rapport | iets sneller (batches van 50) |
| GPS, camera, pHash (photometa v2) | 5.000 foto's | ≥ 5.000 per minuut | ±11.000–22.000 [H5, §3.10] |
| land en plaats | 55.000 punten | ≥ 20.000 per minuut | ±69.000 [V6, H6] |
| index bijwerken | 20.000 teksten via de triggers | rapport | ±26 s [gemeten] |
| schrijven met kopieën | 1 MB tekst, 10 kopieën | een upload ernaast wacht ≤ 1 s | ±650 ms per schrijfactie [C11] |
| OCR (X1) | 100 gescande pagina's, A4, 300 dpi, op 0°, 3°, 7°, −5° en 90° | ≥ 8 pagina's per minuut | 10–20 [V7] |

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
| de app (`GET /files/root`) | p95 ≤ 200 ms; `GET /health` p95 ≤ 50 ms |
| geheugen | geen container `OOMKilled`; `MemAvailable` ≥ 1,5 GiB |
| achtergrondwerk | loopt binnen 90 s na de laatste transcode weer |

**Waar:** in CI (`e2e-media-perf`) een korte versie als rapport (60 s, 1 sessie, zonder
OnlyOffice); in de integratie de hele test in de sandbox als poort (2 sessies media en 3
bewerkers, binnen 8 GB); op de LXC via het runbook `zoeken.md`.

### 4.6 CI

Geen nieuwe actions: alleen wat `ci.yml` al gebruikt.

| Job | Wanneer | Wat erbij komt | Soort |
|---|---|---|---|
| `api` (bestaat) | elke push | alle nieuwe tests, de wachters van §4.1, churn op 3.53.1; `test_places.py` met de bronnen voor plaatsen (gedownload met sha256) | hard |
| `api-media` (bestaat) | elke push | de opdracht `text` met de nep-worker en de echte; `test_media_text_handshake.py` | hard |
| `zoek-perf` (nieuw) | elke push | in `python:3.12-slim`: migratie, plan-tests (S4), churn op 3.46.1, de boom van 100.000, §4.4 | hard (de p95), rapport (p99, max) |
| `ocr` (nieuw, X1) | elke push | `apt-get install tesseract-ocr tesseract-ocr-nld tesseract-ocr-eng tesseract-ocr-osd fonts-dejavu-core` op de runner; `pytest -m ocr`; `e2e -m ocr`; de poort van §4.5 | hard |
| `e2e` (bestaat) | elke push | marker `zoek` (Chromium) | hard |
| `media-image` (bestaat) | elke push | de handdruk (S11) in de echte worker-container; met ja op Vraag 1 ook tesseract (+ ≤ 30 MB) en `test_ocr_container.sh` | hard |
| `infra` (bestaat) | elke push | `test_install_search.sh` (reservekopie met het image van 0.7.0 en van 0.8.0, de download van `geo`, terug naar 0.7.0 en weer vooruit) | hard |
| `web` (bestaat) | elke push | begrotingen (ook `mapData`, `mapRoute`), i18n streng, vitest; `world-110m.<hash>.json` opnieuw maken en vergelijken | hard |
| `perf` (bestaat, rapport) | elke push | `e2e/perf/zoek_ui.py` | rapport |
| `e2e-media-perf` (bestaat, rapport) | elke push | `zoek_samen.py --kort` | rapport |

**Minuten.** ±10–15 minuten runner-tijd per push erbij; de jobs lopen naast elkaar.

### 4.7 Toetsenbord, toegankelijkheid en talen

- Elk nieuw scherm met alleen het toetsenbord in e2e, en een lijst
  `docs/fase-6/toetsenbord-z<n>.md` (zoals fase 5).
- De combobox volgt het ARIA-patroon: `role="combobox"`, `aria-expanded`, `aria-controls`,
  `aria-activedescendant`; de groepen zijn `role="group"` met een label.
- De melding bij `partial` is een `role="status"`: een schermlezer hoort dat de treffers niet per
  se de beste zijn.
- De kaart heeft een tekstalternatief: de lijst "Plaatsen" (Keuze 33). Een cluster is een knop met
  "Utrecht, 34 foto's".
- Een kader tekenen kan met het toetsenbord (Keuze 37).
- De waarschuwing bij delen en de vraag bij een slim album staan in het dialoog zelf, met de focus
  erop, niet als losse melding.
- Alle tekst in NL en EN; i18n streng in CI; foutcodes in de catalogus van het pakket.

---

## 5. Volgorde

Herzien in r2 (C8). Zoals fase 5: per golf twee pakketten in eigen worktrees, de e2e en de
metingen achter één slot (nooit meer dan 3 zware processen), dan een dag voor review, fixer en
nacontrole, dan samenvoegen in de volgorde van de tabel en alle checks.

| Dagen | Wat | Samenvoegen |
|---|---|---|
| 1–3 | **Z0**: Z0-api, Z0-web en Z0-e2e naast elkaar; de spike S1–S11 op dag 1–2. | Z0-api, Z0-web, Z0-e2e |
| 2 | Jonas krijgt de vragen als kaarten, en S9 om zijn CPU te meten. | — |
| 4 | Review van Z0, fixer, nacontrole. | — |
| 5–8 | **Golf 1**: Z1 en Z2 naast elkaar. | Z1, dan Z2 |
| 9 | Review, fixer, nacontrole van golf 1. | — |
| 10–13 | **Golf 2**: Z4 en Z6 naast elkaar. | Z4, dan Z6 |
| 14 | Review van golf 2. Zoeken, personen, herinneringen en slimme albums zijn af. Bij antwoord (b) op Vraag 7 volgen hierna alleen Z7 en de integratie. | — |
| 15–18 | **Golf 3**: Z3 en Z5 naast elkaar (Z5 tegen de contracten). | Z3, dan Z5 |
| 19 | Review van golf 3; de e2e van Z5 tegen de echte Z3. | — |
| 20–22 | **Golf 4**: Z7, en X1 als Jonas niet "nee" zei. | Z7, dan X1 |
| 23 | Review van golf 4. | — |
| 24–26 | **Integratie**: alle checks, de belastingtest (§4.5) als poort, metingen naar `docs/fase-6/metingen.md`, de README, 0.8.0, tag `v0.7.0`. | — |
| na ja op Vraag 2 | **X2**, ±5–6 dagen, als eigen golf met migratie 0011. | — |

**Wat er gebeurt als Jonas antwoordt:**

- **Vraag 1 ja** (op elk moment vóór de integratie): de regel in de Dockerfile (met `chmod 0711`)
  komt in X1 of in de integratie. Na de integratie: een kleine 0.8.1. **Nee**: X1 valt weg; de
  knop "Tekst herkennen" en de rij `ocr` blijven uit.
- **Vraag 2 ja**: X2 na fase 6, of als Jonas het belangrijker vindt dan de kaart, in plaats van
  golf 3 (die schuift dan).
- **Vraag 4 (b)**: ±2 dagen in golf 4 (een pakket naast Z7), of in fase 10.
- **Vraag 5 nee**: `install.sh` downloadt niets; de kaart toont geen plaatsnamen en geen landen,
  en het filter `country` is er niet.
- **Vraag 6 tegels**: niet in fase 6.
- **Vraag 7 (b)**: na dag 14 volgen alleen Z7 en de integratie. **(c)**: na dag 9 volgt Z4 met
  een korte Z7 ernaast (reservekopie, terugrollen, de tab op `/beheer`), dan de integratie. De
  rest komt in een fase 6b met hetzelfde ontwerp.

Niemand hoeft op een antwoord te wachten om verder te bouwen: elke vraag heeft een standaard.

### 5.1 Risico's

| # | Risico | Gevolg | Wat we doen |
|---|---|---|---|
| R1 | De CPU van Jonas is veel trager dan de sandbox | 200 ms niet gehaald op de LXC | S9 in week 1; de rondes A, B en C geven altijd iets binnen ±165 ms SQL (Keuze 9); `prefix='3'`; `limit` 20 |
| R2 | De planner kiest na `ANALYZE` of `PRAGMA optimize` een ander plan | seconden in plaats van ms (107 s in de proef) | `CROSS JOIN`; `INDEXED BY` bij alleen filters; plan-tests met en zonder statistieken (S4) |
| R3 | Triggers maken grote schrijfacties traag (import, kopie van een grote map, een tekst met veel kopieën) | upload of import trager | S2 meet het; de import van fase 5 in de churn-test; batches blijven 500; `ocr_has_text`; profiel "kopieën" (C11) |
| R4 | De index loopt uit de pas met de bron | verkeerde of ontbrekende treffers | wachters (§4.1), churn met `integrity-check`, de wekelijkse `search-check`, de herbouw in stukken |
| R5 | Een latere migratie maakt `node` opnieuw | triggers weg, index bevriest | `test_schema_triggers.py`, `test_no_batch_alter_node.py`, `search-triggers` met herbouw |
| R6 | Met media uit leest het renderproces van de app de tekst van alle bestanden | een fout in PDFium of de kantoorlezer raakt een proces met `/data` en netwerk | met media aan (de standaard) leest de worker; met media uit grenzen zoals 0.7.0, en `/beheer` zegt "buiten de zandbak" (Keuze 17) |
| R7 | bm25 telt over alle gebruikers | de volgorde van bijna gelijke treffers zegt een heel klein beetje over andermans bestanden | geen score, geen totaal, geen schrappen; de test eist gelijke sets; het restrisico staat in de README |
| R8 | De database groeit (±370 MB per 100.000 bestanden) | schijf en reservekopieën groter | `/beheer` toont de grootte; per alembic-head één reservekopie (Keuze 52) |
| R9 | ß, ligaturen en andere vormen | gemiste treffers | ß naar "ss" aan beide kanten; twee vormen per woord (Keuze 5, 21, S8); de referentie vouwt zelf |
| R10 | De initiële JS heeft nog 3,6 kB ruimte; Bestanden 1,5 kB | het zoekveld of de waarschuwing past niet | het veld ≤ 0,8 kB; de waarschuwing lazy; alles verder lazy; de suggesties eerst schrappen |
| R11 | Wissen van een grote gebruiker houdt de schrijver vast | andere schrijvers wachten | per 2.000 met commit (Keuze 11); `test_search_purge_batches.py` |
| R12 | De backfill vult de WAL en houdt de schrijver bezet | trage uploads tijdens de backfill | één blob per transactie; `test_backfill.py` (een upload wacht ≤ 1 s) |
| R13 | De bronnen voor plaatsen komen niet binnen bij de installatie | geen plaatsnamen en geen landen | niet fataal; de volgende run probeert opnieuw; `/beheer` zegt het; `PLAATSEN=uit` |
| R14 | Tekst van 1 MB maakt snippets traag | `/search` boven 200 ms | venster van 20.000 tekens, één regex, 40 ms per verzoek, 100 ms CPU per seconde voor de app (Keuze 8); profiel "groot" met een harde poort |
| R15 | Jonas antwoordt niet | OCR en automatische gezichten niet in 0.8.0; de planning van ±5 weken geldt | de kern is af zonder; X1 staat klaar; dat staat eerlijk in de README |
| R16 | De runner in CI is wisselend snel | `zoek-perf` faalt zonder reden | de mediaan van 3 rondes; alleen p99 en max mogen naar een rapport; de p95 blijft hard |
| R17 | Twee verschillende bestanden heten "bijna zeker gelijk" | iemand gooit het verkeerde weg | vlakke beelden zonder hash, de tweede controle, klieken, tekst telt mee, nooit iets vooraf aangevinkt, alles via de prullenbak (Keuze 35, 36) |
| R18 | Vectoren van gezichten (X2) zijn biometrische data van derden | privacy, ook in reservekopieën | per node, alleen eigen, "uit" wist alles, nooit in de API, de beheerder kan het verbieden, de reservekopieën staan op `/beheer` (Keuze 39) |
| R19 | Natural Earth 10m is ±1 km nauwkeurig en kent de enclaves van Baarle niet | een foto vlak bij een grens krijgt het verkeerde land | staat bij de bronvermelding; de plaats moet in hetzelfde land liggen; later een fijnere bron (Keuze 29) |
| R20 | Een eigenaar snapt "alleen voor jou" bij een slim album niet | hij denkt dat Bram iets ziet wat Bram niet ziet, of andersom | het merkje per item, de telling in het deeldialoog, de vraag bij elke deling (Keuze 42) |
| R21 | Vraag 7 blijft open terwijl de bouw loopt | onduidelijk wat in 0.8.0 komt | de volgorde zet de kern vooraan; na dag 9 en dag 14 is er telkens een bruikbare stand |

---

## 6. Verwerkte en afgewezen kritiek

Revisie 1 kreeg 35 punten van kritiek: 18 over veiligheid en privacy (S1–S18) en 17 over
haalbaarheid en bouw (C1–C17). Ik heb elk punt nagelopen in de code van `1130e1b`, in de notities
en, waar het kon, met een meting (V1–V10). Geen punt bleek onjuist; alle 35 zijn verwerkt. Wat
hieronder bij "Afgewezen kritiek" staat, zijn delen van voorgestelde fixes die ik anders deed, met
de reden.

### Verwerkt

**Veiligheid en privacy**

| Kritiek | Fix |
|---|---|
| S1. De drempels voor bijna-duplicaten zetten veel verschillende bestanden in één groep "bijna zeker gelijk": brieven op één sjabloon, scans, bijna egale foto's. | Vlakke beelden (spreiding 32 × 32 < 3) krijgen geen hash; "zeker" alleen na een tweede, onafhankelijke controle (verhouding, dHash, MAD64); tekst die verschilt is nooit "zeker"; groepen zijn klieken; nooit iets vooraf aangevinkt; fixtures met brieven, formulieren, screenshots en vlakke foto's, poort 0 foute "zeker"-groepen. Gemeten in V5. Keuze 35, 36, §3.6, Z3. |
| S2. Een slim album zet later vanzelf foto's erbij, en in 0.7.0 ziet iedereen met wie het album gedeeld is elk item, ook via een publieke link als origineel met GPS. | `album_item.shared`; `rule`-items zijn standaard niet gedeeld (`album_rule.share_new` = 0); `_via_albums` en `_in_album` geven alleen `shared = 1`; de vraag bij een regel op een gedeeld album en bij elke deling; `POST /albums/{id}/rule/confirm`; e2e met een publieke link. Keuze 42, §3.4, §4.2, Z6. |
| S3. De waarschuwing bij delen telde verkeerd: niet voor een album of een los bestand, niet bij een eigenaar met "uit", niet voor video's. | `blob_meta.gps` (alleen ja of nee) ook bij "uit"; `share-check` telt een map, een album (de items die gedeeld worden) en een los bestand, met "onbekend" voor video's zonder media; aangeroepen bij elke soort deling. Keuze 25, 30, §3.4, Z3, Z5. |
| S4. De tijdsgrens geeft nooit "wat er al is": een gerangschikte vraag die stopt, geeft 0 rijen. | `interrupt()` vanuit een timer; ronde B zonder rangorde (stroomt in volgorde van rowid) en ronde C op de namen; `partial`, `ranked: false`, `names_only`; S1 eist ≥ 1 rij als er een zichtbare treffer is. Gemeten in V3. Keuze 9, §3.7. |
| S5. ß werkt maar in één richting: "grosse" vindt "große" in de tekst niet. | ß en ẞ naar "ss" in `parse()` en in de view `search_src`; de opgeslagen tekst houdt ß voor de snippet; "grosse", "strasse" en "GROẞE" in S8 en de vragenlijst. Keuze 5, 21, §3.1, §4.3. |
| S6. Tekst lezen gebeurde in het renderproces van de app, met `/data` en netwerk, voor alle PDF's van alle gebruikers, terwijl de worker hetzelfde image met pypdfium2 heeft. | Een opdracht `text` in de mediaworker als media aan staat; de handdruk (eerst niet-dumpbaar, dan pas de fd's); met media uit de terugval met een melding op `/beheer`; `test_extract_no_pdfium_in_app.py`; R6 herschreven; spike S11. Keuze 17, §3.7, Z2, R6. |
| S7. Tesseract kreeg de ruwe blob: geen HEIC, geen EXIF-draaiing, decoders zonder grens op pixels. | Kind 1 maakt een genormaliseerd grijs beeld (EXIF-draaiing, HEIC, `preview_max_pixels`, deskew); de worker controleert de kop vóór tesseract; tesseract met `RLIMIT_DATA` en tijdsgrens; een foto met EXIF-draaiing 6 en een HEIC in de fixtures en de e2e. Keuze 22, §3.6, X1. |
| S8. Gegevens uit een foto (plek, OCR-tekst, personen) stonden per blob, maar toestemming en levensloop zijn per eigenaar; een kopie bij een ander erfde ze. | Alles per node met `owner_id` (`photo_geo`, `photo_person`, `photo_ocr`, later `face`); triggers wissen bij een andere eigenaar of nieuwe inhoud; "uit" wist alles van die eigenaar; OCR van een foto alleen door de eigenaar; de tekst alleen bij die node in de index. Gemeten in V10. Keuze 23, 25a, 28, 37, §3.1, §3.9. |
| S9. De fotofuncties hadden geen regel voor "levend": een foto in de prullenbak bleef in herinneringen, kaart en tellingen; `place_count` liep uit de pas. | Eén regel `own_live` in `app/search/live.py` voor alle fotofuncties; `place_count` weg, tellen per verzoek met `ix_photo_geo_place`; rijen voor prullenbak, terugzetten en wissen in §3.9; `test_own_live.py`. Keuze 25a, 33, §3.9, §4.1. |
| S10. Het land uit de dichtstbijzijnde plaats is bij een grens vaak fout en geeft op zee toch een land; de test vergeleek met dezelfde fout. | Het land uit een punt-in-polygoon-test op Natural Earth 10m (v5.1.2, sha256); een plaats alleen in hetzelfde land en binnen 30 km; op zee geen land; tests tegen de polygonen bij vier grenzen en op zee. Gemeten in V6. Keuze 29, Z3. |
| S11. De tekstlaag startte een proces per verzoek, zonder cache, limiter of grens, en zonder versie. | De woorden uit dezelfde render als de pagina, in de cache, met de limiter van de viewer; het download-token met de versie; `ETag`; 20 per seconde per gebruiker; test met 50 verzoeken tegelijk. Keuze 18, §3.4, Z2. |
| S12. Zoeken had geen grens voor de hele app; drie gebruikers konden de enige event loop stilleggen. | Hooguit 2 FTS-vragen tegelijk en 20 wachtenden, daarboven `429 search_busy`; 1 vraag per gebruiker; snippets in een thread met een budget van 100 ms CPU per seconde; harde poorten voor 4 clients en voor `GET /health`. Keuze 8, 13, §4.4. |
| S13. "Index herbouwen" was één statement dat de schrijver minuten vasthoudt, terwijl `busy_timeout` 5 s is. | `delete-all`, dan stukken van 2.000 met een commit; `search_state.cursor`; de triggers schrijven alleen onder de cursor; `LikeFallback` tijdens de herbouw; `search-triggers` herbouwt altijd; test met een upload tijdens de herbouw. Gemeten in V4 en V10. Keuze 12, §3.1. |
| S14. XMP lezen kan niet met Pillow zonder defusedxml, en de GPS-waarden werden niet gecontroleerd (NaN, buiten bereik, (0, 0), zonder `Ref`). | XMP uit de ruwe bytes met `office.reader.sax()`, hooguit 1 MB, alleen drie velden; elke waarde gecontroleerd; tests met een "billion laughs", NaN en een ontbrekende `Ref`. Keuze 25, §3.6, Z3. |
| S15. Eén gebruiker of een gast met schrijfrecht kon de rij voor tekst urenlang bezetten, en OCR voor alle PDF's van een ander laten draaien. | Om de beurt per eigenaar (hooguit 4 plekken per batch); 30 s CPU per blob en geen nieuwe poging na `too_slow`; OCR: 20 verzoeken per uur en 200 pagina's per dag per gebruiker; test met 100 trage PDF's. Keuze 17, 19, 20, 23. |
| S16. De toegangslog bewaarde `bbox`, `z`, `place` en `country`; een kaart op zoom 14 wijst vaak het huis aan. | De hele query string uit de log voor `/api/v1/search`, `/api/v1/photos` en `/api/v1/people`; `q` ook in `SECRET_PARAMS`; `test_search_logs.py` met de kaart op zoom 14 en een plaatsfilter. Keuze 13, §4.1. |
| S17. Voor vectoren van gezichten (biometrische data van derden) was niets besloten over wissen, de API en reservekopieën. | Nu vastgelegd: per node, alleen eigen en levende bestanden; "uit" of een verbod wist alles (test: 0 rijen); nooit in de API; een persoon wissen wist zijn koppelingen; de reservekopieën staan op `/beheer` en in de README; de kaart van Vraag 2 noemt het. Keuze 39, Vraag 2, R18. |
| S18. Boven 64 wortels kwamen de bereiken in een tijdelijke tabel op een verbinding uit de pool: ongetest, en een volgende gebruiker kon andermans wortels krijgen. | Geen tijdelijke tabel meer: boven 64 wortels één JSON-parameter met `json_each` (in 3.46.1, V8); `test_no_temp_table.py`; tests met 64, 65 en 1.000 wortels en een deling die in hetzelfde verzoek wordt ingetrokken. Keuze 4, 14. |

**Haalbaarheid en bouw**

| Kritiek | Fix |
|---|---|
| C1. De view `search_src` had geen kolom `blob_id`, dus de eerste schrijfactie naar `blob_text` faalde; de metingen gingen over een andere vorm. | `n.blob_id AS blob_id` in de view; de hele SQL van §3.1 gedraaid met churn en een herbouw in stukken, `integrity-check` ok op drie versies (V1, V10); `test_migration_0010.py` draait de echte SQL. Keuze 3, §3.1. |
| C2. Een upsert en `INSERT OR IGNORE` maken een external-content-index kapot; de wachter verbood alleen `OR REPLACE` en `.merge()`. | De wachter verbiedt ook `OR IGNORE`, `ON CONFLICT`, `on_conflict_do_*` en `prefix_with("OR`, op vier tabellen; één helper `write_text()` (eerst `SELECT`, dan `INSERT` of `UPDATE`); churn met herhaalde schrijfacties. Gemeten in V1. Keuze 3, §4.1, §4.3. |
| C3. Per blob een nieuw renderproces kost meer dan de tijd per stuk; de tijden voor Jonas waren 6 tot 70 keer te laag. | Batches: één kind voor tot 16 blobs in de worker (50 zonder media); per blob een resultaat over de pipe; halveren bij een fout; pHash in de bestaande batch van photometa; §3.10 en §4.5 opnieuw gerekend met de start van een kind (V2). Keuze 17, 20, 35. |
| C4. De tijdsgrens gaf altijd een lege pagina, en de progress-handler had grote gaten. | `interrupt()` uit een timer in plaats van de handler; de rondes A, B en C; S1 eist ≥ 1 rij; de teksten in de web zeggen eerlijk wat er is. Gemeten in V3. Keuze 9. |
| C5. Snippets over tekst van 1 MB pasten niet in 40 ms. | Alleen `substr(…, 1, 20000)` lezen; één keer vouwen en één regex; de klok per document; anders `snippet: null` met "gevonden in de tekst"; `snip` p95 ≤ 40 ms als harde poort, ook in "groot". Keuze 8, §4.4. |
| C6. Voor drie harde poorten (alleen filters, de kaart, duplicaten) was er geen SQL, geen index en geen meting. | Keuze 4a legt de drie vormen vast, met `ix_node_live_mod`, `ix_node_ftype_mod` op de virtuele kolom `ftype`, `ix_photo_geo_tile` en `ix_node_owner_blob`; spike S10 meet ze op 3.46.1; een poort die niet haalbaar is, wordt 200 ms of krijgt een tabel. Keuze 4a, §3.1, §3.7. |
| C7. De weg terug brak: het script van 0.8.0 roept `db-backup` aan met het image van 0.7.0, waar dat niet bestaat. | De reservekopie met alleen de standaardbibliotheek, met elk image; de weg terug haalt het script van de commit van 0.7.0, niet van de branch; `test_install_search.sh` draait precies die stappen. Keuze 52, 53, §3.10. |
| C8. De planning vulde 15 of 16 van de 15 dagen, zonder tijd voor review, en met drie pakketten tegelijk. | Golven van twee met een reviewdag, e2e achter één slot: ±26 werkdagen; Vraag 7 laat Jonas kiezen tussen ±5 weken, ±4 weken of 3 weken met wat er dan schuift. §2.0, §5, Vraag 7. |
| C9. Bestanden zonder eigenaar, twee pakketten in hetzelfde bestand, en twee paden die niet bestaan. | De tabel in §2.0 aangevuld (`photos.py`, `albums.py`, `PhotosPage.tsx`, `AlbumPage.tsx`, `AddToAlbum.tsx`, `PdfView.tsx`, `PageViewer.tsx`, `LocationRow`, `ocr_page` in `render.py`); i18n-gebieden en catalogi per pakket; de paden `web/src/ShareDialog.tsx` en `web/scripts/budget.mjs`. §2.0, §3.5. |
| C10. De OCR-keten kost ±6,5 s per pagina, niet 2–3 s; de poort van 18 pagina's per minuut was niet haalbaar. | PGM in plaats van PNG (6–20 ms tegen 154–174 ms); OSD alleen bij een lage zekerheid; poort ≥ 8 pagina's per minuut, gemeten in de job `ocr`; de kosten bij Vraag 1 opnieuw: ±70–110 minuten per 1.000 pagina's. Gemeten in V7. Keuze 22, Vraag 1, §4.5. |
| C11. Een tekst met veel kopieën houdt de schrijver bij elke schrijfactie lang vast; een OCR-update leest alles opnieuw. | `body`, `ocr` en `meta` in één `UPDATE`; een PDF met ≥ 200.000 tekens krijgt geen OCR (`ocr_has_text`); OCR van foto's per node, niet per blob; het profiel "kopieën" in `test_backfill.py` en in §4.5. Keuze 1, 3, 23. |
| C12. Downloaden in een `RUN`-stap die niet fataal faalt, laat een lege laag in de cache; `.env` komt niet in de build. | `install.sh` downloadt naar `DATA_ROOT/geo/` met sha256 en probeert het elke run opnieuw; `app.cli geo-load` bouwt `places.sqlite`; `test_geo_load.py` met een kleine vaste bron, het downloaden in CI. Keuze 29, §3.10, Z7. |
| C13. Na twee runs op 0.8.0 was de enige kopie van vóór 0010 weggedraaid. | De alembic-head in de bestandsnaam; per head de nieuwste; de laatste kopie van vóór een migratie gaat nooit weg; `test_install_search.sh` controleert het na twee runs. Keuze 52. |
| C14. Eén apt-regel is niet genoeg: tesseract blijft leesbaar en dus dumpbaar, staat niet in de bewaking en heeft geen test. | `chmod 0711` in dezelfde regel; `"tesseract"` in `guard.BINARIES` en in `test_media_no_ffmpeg_outside.py`; `test_ocr_container.sh` controleert de modus. Keuze 22, X1. |
| C15. De kaart haalde de begroting niet, en Z4 en Z5 deelden dezelfde ruimte zonder verdeling. | De wereld als statisch bestand met een eigen grens (`mapData` ≤ 60 kB, `mapRoute` ≤ 30 kB); het zoekveld ≤ 0,8 kB, de combobox lazy; de waarschuwing laadt pas met het dialoog; "initiële JS ≤ 100 kB en Bestanden ≤ 45 kB" ook in de klaar-als van Z5. Keuze 31, 45, 48, Z5. |
| C16. Groen op 3.46.1 was niet aan te tonen vóór een push. | `e2e/slim.sh` met `python:3.12-slim`, de venv alleen-lezen en `--network none` (V8); groen in slim in de klaar-als van Z0, Z1, Z2, Z3 en Z6. §2.0, §4.1. |
| C17. Een harde max over 600 vragen faalt soms toevallig, en dan verloor de belangrijkste klaar-als zijn bewijs in CI. | Geen harde max meer; p99 ≤ 300 ms als rapport; de mediaan van de p95 over 3 rondes is hard; alleen p99 en max mogen naar een rapport, de p95 nooit. §4.4, R16. |

### Afgewezen kritiek

| Kritiek | Waarom niet |
|---|---|
| S1: een tweede controle met dHash ≤ 8. | Echte varianten (JPEG opnieuw gecomprimeerd) haalden een dHash tot 10 (V5). Met ≤ 8 zou "zeker" juist de echte kopieën missen. De grens is ≤ 10; de MAD64 (≤ 3,0) en de verhouding doen het scheiden: brieven op één sjabloon zaten op ≥ 3,94. |
| S7: het grijze beeld als PNG. | PNG maken kostte 154–174 ms per pagina, PGM 6–20 ms (V7; C10 zegt hetzelfde). tesseract leest PGM van stdin. De worker controleert dan de kop van de PGM in plaats van die van de PNG. |
| S8 en S17: de gegevens per (`owner_id`, `blob_sha`). | Per node is eenvoudiger en sluit beter: de FK-cascade ruimt alles op bij wissen, de trigger op `owner_id` en `blob_id` regelt een andere eigenaar en nieuwe inhoud, en een bewerkte foto houdt zijn personen. Per (eigenaar, sha) zou bij elke prullenbak, kopie en wissen eigen opruimcode vragen ("de laatste levende node van die eigenaar"). |
| S10: testen in Baarle. | Natural Earth 10m heeft de enclaves van Baarle-Hertog niet (V6): de test zou alleen de bron testen, niet onze code. We testen bij vier andere grenzen en op zee, en de beperking staat bij de bronvermelding (R19). |
| S18: één vorm zonder staat voor elk aantal wortels. | Tot en met 64 wortels is een `OR` van bereiken gemeten en snel (J11), en de planner gebruikt daar de index op `mpath`. Daarboven `json_each`, zoals voorgesteld. Beide zonder staat; beide met een plan-test. Laat S4 zien dat `json_each` net zo snel is, dan blijft er één vorm over. |
| C3: batches tot 50 blobs. | In de worker 16: de start kost dan al maar ±7 ms per blob (113 ms / 16, V2), klein naast het lezen. Een kleinere batch geeft minder werk opnieuw bij halveren, minder open fd's per kind, en Bram wacht korter achter Eva (Keuze 20). Zonder media 50, zoals voorgesteld. |
| C4: de progress-handler op elke 100 stappen, of eerst kandidaten zonder `ORDER BY` met `LIMIT 5000`. | Ook bij elke 100 stappen zat er 144–201 ms tussen twee aanroepen, omdat FTS5 het werk zelf doet (V3). Een voorvoegsel als `ver*` gaf ook zonder `ORDER BY` 0 rijen bij de grens: FTS5 voegt eerst alle lijsten samen. Daarom `interrupt()` en ronde C op de namen. |
| C5: een kolom `snip_src` van 20.000 tekens per blob. | Niet nodig: `substr(body, 1, 20000)` in SQL leest alleen dat stuk, en de critici maten 33 ms voor 30 documenten met dat venster. Een extra kolom kost ruimte en een trigger meer. |
| C8: fase 6 inkorten tot 15 dagen door slimme albums en herinneringen te schrappen. | Wat eruit gaat, is een keuze van Jonas, niet van het ontwerp. Vraag 7 geeft hem drie antwoorden met wat er dan schuift, en de standaard is ±5 weken met alles. De golven staan zo dat elk antwoord een afgeronde stand geeft. |
| C11: één FTS-rij per blob, of een grens van 250 ms per schrijfactie met 10 kopieën. | Een index per blob vraagt een join naar `node` voor elke treffer en een tweede rangorde, en maakt OCR-tekst per node (S8) lastig. Met 1 MB tekst en 10 kopieën kost een schrijfactie ±650 ms (C11); 250 ms halen we zo niet. Wel: één `UPDATE`, geen OCR boven 200.000 tekens, en de poort "een upload ernaast wacht ≤ 1 s" in het profiel "kopieën". |
