> Werkdocument: het bouwontwerp van fase 6 (zoeken en foto's), revisie 3. Revisie 3 is leidend.
> Revisie 2 staat in [bouwontwerp-r2.md](bouwontwerp-r2.md), revisie 1 in
> [bouwontwerp-r1.md](bouwontwerp-r1.md).
> Jonas besliste op 3 november 2026 over de omvang. De vragen die nog open zijn, staan onder
> "Vragen voor Jonas". Tot hij antwoordt, bouwen we op de standaard die daar staat.
> Bijgewerkt na de spike (Z0, 3 november 2026). Wat de spike veranderde, staat erin als
> "Herzien na de spike (Sx)"; de uitkomsten per stap staan in [spike.md](spike.md). Revisie 3
> blijft verder de tekst.

# Fase 6 (zoeken en foto's): bouwontwerp, revisie 3

Basis van revisie 3: branch `wip/fase6-beeld` @ `245d87a` (0.7.1, migraties 0001–0009), en de
code van Z0 op `wip/z0-api` (acht commits, `d1c7a07` tot `7fcd92c`), alleen gelezen.
Revisie 2 had als basis `1130e1b` (0.7.0); migratie 0009 kwam daarna, in 0.7.1.

## Wat er sinds revisie 2 veranderd is, en waarom

Jonas vroeg op 3 november 2026 "ja voeg alles toe". Daarna koos hij op een kaartje "Ook
gezichten". Het kaartje vroeg: "Wil je met 'alles' ook automatische gezichtsherkenning, en een
kaart met tegels van buiten?" Het antwoord was dus: gezichten ja, tegels van buiten nee.

| Vraag in revisie 2 | Wat Jonas koos | Gevolg in revisie 3 |
|---|---|---|
| 1. tesseract in het image | ja ("alles") | X1 is een gewoon pakket van fase 6, met tesseract in het image (Keuze 22–24). |
| 2. gezichten automatisch | ja, in fase 6 (het kaartje) | X2 hoort bij fase 6 (Keuze 39, 56–69). numpy en onnxruntime komen als optionele extra, alleen in de afgeschermde container `analyse`. De app importeert ze nooit. De modellen downloadt `install.sh`. |
| 3. zoeken op wat er op een foto staat | ja, in fase 6 ("alles") | X3 hoort bij fase 6 (Keuze 40, 70–84). |
| 7. de omvang | alles | De planning wordt ±43 werkdagen, in twee opleveringen (§5, Vraag 13). |
| de kaart (het kaartje) | geen tegels van buiten | Alleen landsgrenzen, zoals Keuze 31. De CSP blijft gelijk. |
| 5 en 6 | geen eigen antwoord: de kaartjes zijn ingetrokken, omdat "alles" ze dekt | De standaard geldt: GeoNames en Natural Earth bij de installatie, en alleen grenzen onder de kaart. |
| 4. wie ziet de plek van een foto | **niet beantwoord** | De standaard geldt: alleen de eigenaar, met de waarschuwing in het deeldialoog. Dat is geen keuze van Jonas. |

Het kaartje noemde alleen gezichten en tegels. Dat zoeken op beeld erbij hoort, volgt uit "ja voeg
alles toe" en uit de oude Vraag 3. Omdat Jonas daar niet apart over koos, staat beeld per gebruiker
standaard uit tot het gemeten is (Vraag 11).

Twee ontwerpen voor X2 en X3 (scratch, niet in het repo) zijn daarna door twee critici nagelopen:
16 punten over privacy, veiligheid en licenties (K1-01 tot K1-16), en 24 over haalbaarheid,
planning en contracten (K2-01 tot K2-24). De critici toonden de meeste punten aan met een proef in
de sandbox. §6 zegt per punt wat er veranderde, en waarom een deel niet.

De grootste veranderingen:

- **Een kleine Z0b vóór golf 1** (Keuze 55, K2-01). Z0 bevriest de contracten, zodat golf 1–4
  naast elkaar kunnen bouwen. X2 en X3 voegen velden, waarden en een rij toe aan die contracten.
  Dat gebeurt nu in één dag vóór de review van Z0, en niet midden in golf 5.
- **Eén fundament voor X2 en X3: A0** (2 dagen). De container `analyse`, de handdruk, één model
  voor het werk in de container (een kind `zoeken` en een kind `werk`, elk met `busy` en een
  `cancel`; K2-02), migratie 0011 (gezichten) en 0012 (beeld).
- **De container is minstens zo dicht als de mediaworker** (Keuze 56, K1-04, K1-05, K1-07,
  K1-16). Elk kind is niet-dumpbaar vóór het een fd krijgt, heeft `RLIMIT_DATA` en de wachter van
  `guard.py`, opent alleen WebP, en schrijft niets naar `docker logs`. De omzetter van X3 is een
  eigen dienst zonder sleutel en zonder socket.
- **De modellen staan buiten `$DATA_ROOT`** (Keuze 58, K1-06). In een map die helemaal van root is.
  `install.sh` weigert symlinks. De worker rekent de sha256 over precies de bytes die hij laadt
  (K1-12).
- **Vectoren van gezichten zijn versleuteld, met een sleutel per eigenaar** (Keuze 68, K1-01).
  "Uit" wist de sleutel. Dan is elke kopie van de vectoren onleesbaar, ook in snapshots en in de
  offsite-kopie van Jonas, zolang de map met sleutels daar niet in zit. De tekst bij het aanzetten
  belooft niets meer wat niet waar is.
- **Wie een foto uploadde, telt mee** (Keuze 69, K1-02). Een foto die een ander in jouw map zette,
  wordt niet gescand, tenzij die ander het zelf aan heeft. Een upload via een publieke link nooit.
  Dat is Vraag 9.
- **De kosten zijn opnieuw gemeten** (K2-04, K2-05). Een foto met één gezicht kost ±142–164 ms,
  niet ±112–126. YuNet krijgt een beeld dat tot een veelvoud van 32 is opgevuld; zonder dat
  crasht hij op een 3:2-preview. Beeld rekent uit de preview van 256 px.
- **Beeld staat per gebruiker standaard uit** (Vraag 11, K1-09, K2-22), tot gemeten is of "Meer
  zoals deze" op een portret dezelfde persoon terugvindt (BZ10).
- **Eerlijke tijden voor beeld** (K2-13). Gewoon zoeken houdt zijn 200 ms. Zoeken op beeld krijgt
  een eigen grens van 250 ms (Vraag 14), gemeten bij 55.000 én 100.000 foto's.
- **De planning is ±43 werkdagen** (§5, K2-11): eerst 0.8.0 met de kern (±dag 27), daarna 0.8.1
  met gezichten en beeld (±dag 43). Met één oplevering is het ±41 werkdagen. Dat is Vraag 13.

### Wat revisie 2 veranderde

Revisie 2 verwerkte de kritiek op revisie 1: 18 punten over veiligheid en privacy (S1–S18) en 17
over haalbaarheid en bouw (C1–C17). Elk punt is toen nagelopen in de code van `1130e1b`, in de
notities en, waar het kon, met een meting in de sandbox (V1–V10 hieronder; de commando's staan in
[haalbaarheid.md](haalbaarheid.md)). Geen punt bleek onjuist. §6 zegt per punt wat er veranderde.

De grootste veranderingen van revisie 2:

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
  niet in de 3 weken van het stappenplan; daarom Vraag 7 (C8). Revisie 3 maakt er ±43 van, met
  gezichten en beeld (§5).
- **De weg terug werkt met elk image** (Keuze 52, 53; C7, C13).

Revisie 1 voegde twee ontwerpen samen. Ontwerp A ("zelf bouwen, geen nieuwe afhankelijkheden,
eerlijk over wat er zonder Jonas niet lukt") is de basis voor de vorm: de pakketten, de vragen,
de uitrol en het terugrollen. Ontwerp B ("de hele fase, goed afgeschermd") is de basis voor de
zoekmachine zelf: het schema, de triggers, het toegangsfilter en de tests. Waar ze elkaar
tegenspraken, is het nagemeten of in de code nagekeken (J1–J14 hieronder).

## Bronnen, bewijs en afkortingen

Bronnen (scratch, niet in het repo):

- `fase6/tech.md`: onderzoek naar FTS5, OCR, EXIF, GPS, plaatsnamen, kaarten, gezichten en
  beeldmodellen, met licenties en een bron per bewering;
- `fase6/feasibility.md`: de proef in de sandbox (H1–H8). De getallen staan ook in
  [haalbaarheid.md](haalbaarheid.md);
- `fase6/code-map.md`: waar fase 6 in de code inhaakt, met regelnummers;
- `fase6/judging.md`: het oordeel over A en B;
- `fase6/critic/`, `fase6/critic-sec/` en `fase6/verify/`: de scripts van de critici en van
  revisie 2 (V1–V10).

Bij revisie 3 kwamen erbij (ook scratch, `fase6-beeld/`):

- `ontwerp-gezichten.md` en `ontwerp-beeld.md`: de twee ontwerpen voor X2 en X3, waar Keuze 55–84
  uit komen;
- `spike/`: de spike B1–B7 (numpy en onnxruntime, eigen ONNX-modellen, bronnen, licenties, de
  tokenizer, rekenkosten, vectoren in SQLite), met `verslag.md`;
- `gezichten/`: groeperen en wissen (`groep.py`, `wissen.py`);
- `kritiek1/` en `kritiek2/`: de proeven van de twee critici van revisie 3 (K1, K2);
- `r3/proef-target/`: de proef met `--target /opt/analyse` (Keuze 57).

De commando's en uitkomsten staan in [haalbaarheid.md](haalbaarheid.md).

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
- **X-pakket**: een pakket naast de kern (Z0–Z7). In revisie 2 wachtte het op een antwoord van
  Jonas; sinds 3 november 2026 horen X1, X2 en X3 bij fase 6;
- **Z0b**: één dag na Z0, vóór de review van Z0, die de contracten van X2 en X3 vastlegt
  (Keuze 55);
- **A0**: het fundament van X2 en X3 (2 dagen): de contracten van de container `analyse`,
  migratie 0011 en 0012, de nep-worker (§3.1a, §3.11);
- **X2a, X2b, X3a, X3b**: de twee delen van X2 en van X3, in golf 5 en golf 6 (§2.0);
- **`analyse`**: de afgeschermde container voor gezichten en beeld (uid 10003, geen netwerk,
  geen `/data`; §3.11);
- **beeld**: zoeken op wat er op een foto staat (X3);
- **B1–B7**: de spike van X2 en X3 in de sandbox; **BZ1–BZ10**: wat voor X3 nog in CI of op de
  LXC gemeten moet worden (X3);
- **K1-xx en K2-xx**: de punten van de twee critici van revisie 3 (§6).

**Nagemeten bij revisie 3** (de commando's in [haalbaarheid.md](haalbaarheid.md)):

- **B1. numpy en onnxruntime** [gemeten]. 5 wheels, samen 40 MB; geïnstalleerd ±140 MB, in een
  slanke container 153 MB. Ook offline in `python:3.12-slim`. Met `--target /opt/analyse` en
  alleen het verschil tussen de basis en de extra (`uv export`): precies vijf pakketten (numpy,
  onnxruntime, protobuf, flatbuffers, packaging), 118–121 MB, en voor uid 10001 en 10002 geeft
  `find_spec("numpy")` `None` (Keuze 57, K2-09, K2-15).
- **B2. Zelf ONNX-modellen maken** [gemeten]. Met alleen de standaardbibliotheek; onnxruntime en
  numpy geven dezelfde uitkomst tot 6,3·10⁻⁸. Twee keer bouwen geeft dezelfde sha256.
- **B3. Bronnen** [gemeten]. YuNet en SFace komen van media.githubusercontent.com, vast op een
  commit van opencv_zoo, met sha256. huggingface.co is vanuit de sandbox niet te bereiken (code
  000); of een runner van CI het kan, is niet aangetoond (BZ1).
- **B4. Licenties** [doc]. YuNet MIT, SFace Apache-2.0, SigLIP 2 Apache-2.0. InsightFace mag niet.
- **B5. De tokenizer zonder `transformers`** [gemeten]. 0 verschillen op 2.181 zinnen tegen
  `tokenizers`, met kleine letters per teken. Gemeten met een ander bestand van dezelfde soort;
  met het bestand van SigLIP 2 zelf is het nog niet bewezen (BZ3, K2-14).
- **B6. Rekenkosten** [gemeten, één core]. YuNet en SFace vonden in de testfoto één gezicht
  (score 0,915). Een zoekvraag over 100.000 vectoren in numpy: ±35–51 ms. De critici maten opnieuw
  met de echte bron, een WebP van 1024 × 768: ±142–164 ms per foto met één gezicht, ±69–80 ms per
  foto voor beeld uit de preview van 256 px (K2-04).
- **B7. Vectoren als BLOB in SQLite** [gemeten]. 100.000 beeldvectoren: 205 MB als float16.
  150.000 gezichten: 89,7 MB.
- **Uit de kritiek** [gemeten door de critici, nagelopen]: YuNet crasht op een hoogte die geen
  veelvoud van 32 is (K2-05); een map met `o-rwx` in `/opt/venv` geeft een leeg namespace-pakket
  (K2-09); een test die numpy bovenaan importeert, breekt de job `api` al bij het verzamelen
  (K2-10); `busy_timeout` 5000 laat een schrijver ±4,8 s wachten achter een checkpoint (K2-08);
  SQLite schrijft bij sorteren naar een tijdelijk bestand buiten de database (K1-03); `python`
  is na `exec` dumpbaar (K1-04); `compose run` erft de sleutel en de socket (K1-05); de app kan
  onder `$DATA_ROOT` een symlink planten (K1-06).

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
  aan `92a0d04`). Bij de integratie komt daar een tag `v0.7.0` op. Dit zijn de waarden van
  revisie 2: daarna kwam 0.7.1, met migratie 0009. Z7 zet ze op de laatste commit vóór Z0, met
  head `0009` (§3.10).
- **J6. Het voorbeeld van 256 px is al gedraaid volgens EXIF** (`render.py:357` en `:370`)
  [code]. De pHash daarop heeft geen eigen draaiing nodig.
- **J7. GPS uit downloads halen** hoort bij de bewerking "metadata en GPS-locatie weghalen" van
  fase 10. In 0.7.0 gaat de originele foto al met GPS mee bij een download. Fase 6 verandert daar
  niets aan, en waarschuwt (Keuze 30, Vraag 4).
- **J8. Geen nieuwe afhankelijkheid zonder Jonas.** B19 is besloten. Tesseract, onnxruntime,
  numpy en de modellen waren daarom vragen. Beantwoord op 3 november 2026: ja, met "alles"
  (Vraag 1–3). Een ander nieuw pakket, model of dataset blijft een vraag (Vraag 8, 10 en 12).
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
| B14 en B19: zelf bouwen, en geen nieuwe afhankelijkheid in `pyproject.toml` of `package.json`. | Jonas koos op 3 november 2026 "alles": tesseract in het image, numpy en onnxruntime als optionele extra alleen voor de container `analyse`, en de modellen bij de installatie. De basis van `pyproject.toml` en `package.json` verandert niet. Een ander nieuw pakket, model of dataset blijft een vraag (Vraag 8, 10, 12). |
| B15: standalone draait op SQLite. | pgvector bestaat hier niet. Vectoren van gezichten en beeld staan als BLOB in SQLite (Keuze 61, 76) en worden in de container vergeleken. pgvector pas met het cluster. |
| B17: zoeken in de eigen database, geen aparte zoekdienst. | FTS5 zit in SQLite. Dat past. |
| B18 en B23: een album en een publieke link geven elk item als origineel, met GPS (V9). | Een slim album deelt nieuwe foto's niet vanzelf (Keuze 42). De waarschuwing bij delen telt ook albums (Keuze 30). |
| B20: de PDF-viewer krijgt in fase 6 een tekstlaag. | De woorden met hun plek komen uit dezelfde render als het beeld van de pagina (Keuze 18). |
| B22: GPS lezen we in fase 3 niet, "dat hoort bij fase 6". | GPS wordt nu bewust weggegooid (`render_meta.py:21–22`, `media/worker/ops/meta.py:32`, `previews.SECRET_META:69`, `photodate.split_capture`) [code]. Het gaat open, maar alleen naar een eigen tabel per bestand en eigenaar, nooit naar `preview_meta`. |
| B40: onvertrouwde bestanden lezen hoort in de worker, zonder netwerk en zonder `/data`. | Tekst lezen en OCR draaien in de worker als media aan staat (Keuze 17, 22). Alleen zonder media blijft het in het renderproces, zoals de voorbeelden van 0.7.0. |
| InsightFace: gewichten alleen voor niet-commercieel onderzoek [doc]. | Valt af. YuNet (MIT) en SFace (Apache-2.0) kunnen wel (X2, Keuze 58). |
| CLIP is Engelstalig; SigLIP 2 is meertalig en Apache-2.0 [doc]. Als int8 ±390 MB, de bron in fp32 ±1,5 GB [B4, B6]. | Zoeken op beeld komt met SigLIP 2 B/32 in fase 6 (X3, Keuze 70–84). Er komen geen labels of objecten als filter (Keuze 71). |
| Zoeken nu: alleen op naam, 590–670 ms bij 101.000 nodes [gemeten]. | Haalt de 200 ms niet. Er komt een index. |
| SQLite in het image is 3.46.1 [gemeten]. | Elke meting en elke plan-test draait in `python:3.12-slim`, ook in de sandbox (V8). |
| Op de LXC draaien al OnlyOffice (≤ 2 GB) en de mediaworker (≤ 3 GB, `cpus: 3.0`) (B43, B47). | Indexeren en de container `analyse` wijken voor transcodes. Laagste voorrang, eerlijk per eigenaar. `analyse` krijgt ≤ 3 GB en `cpus: 1.0`: samen 8 van de 16 GB (§3.11, K2-06). |
| De app is één Python-proces met één event loop (`serve.py`). | Zoeken krijgt een grens voor de hele app, niet alleen per gebruiker (Keuze 13). |
| CPU en GPU van Jonas zijn onbekend (B5). | Alles werkt op de CPU. Een tijdsgrens per vraag beschermt de 200 ms (Keuze 9). `bench --los` meet zijn LXC in week 1. |
| Jonas gaf het startsein voor fase 6 (3 november 2026) zonder de kaarten van fase 5 te beantwoorden. | De standaarden van fase 5 blijven. Fase 6 verandert er niets aan. |
| De LXC van Jonas (CT 146) heeft één rootdisk van 500 GB op ZFS en geen `/tank`. Alles staat daarop: `DATA_ROOT` (`/opt/platform/data/platform`), de modellen en de sleutels. Het voorbeeld in `infra/single/README.md:11–12` (64 GB en een `/tank` van 700 GB) is niet zijn opzet (K2-24). | De modellen (±0,4 GB, en ±1,55 GB tijdelijk bij het omzetten) en de vectoren met hun cache (±0,45 GB per 100.000 foto's; alles samen ±0,9–1,1 GB) passen. `install.sh` kijkt met `df` naar de echte mappen (Keuze 58, 84). |
| Jonas maakt zelf snapshots van `/data` en een kopie buiten huis (`stappenplan.md:145–151`). | "Uit" bij gezichten moet ook daar werken. Daarom versleutelde vectoren met een sleutel per eigenaar, buiten `DATA_ROOT` (Keuze 68). Op één rootdisk zit de sleutel wel in een snapshot of backup van de hele container; in zo'n kopie blijven oude vectoren leesbaar tot die kopie weg is. |
| Het stappenplan geeft fase 6 drie weken. | Met "alles" en review per golf is het ±43 werkdagen, in twee opleveringen (§5, Vraag 13). |

### 0.2 De invalshoek

| | Principe | Wat het hier betekent |
|---|---|---|
| P1 | **Toegang eerst** | Een treffer verlaat de server pas na het toegangsfilter én een tweede controle per treffer. Een snippet maken we pas daarna. Zoeken kan alleen met een account; een publieke link zoekt nooit. |
| P2 | **Zelf bouwen met wat er is** | SQLite (FTS5, R*Tree), Pillow, pillow-heif, pypdfium2, mutagen, de eigen kantoorlezer (B21), ffmpeg in de worker, de standaardbibliotheek. Eigen code voor de rest. |
| P3 | **Elk extern ding is een vraag** | Met een standaard waarmee de bouw doorgaat. Jonas zei ja op tesseract, numpy, onnxruntime en de modellen van X2 en X3. Een ander pakket, model of dataset blijft een vraag (Vraag 8, 10, 12). |
| P4 | **Eerlijk meten** | De 200 ms meten we via HTTP, in `python:3.12-slim` (SQLite 3.46.1), met het toegangsfilter, voor zes soorten gebruikers. De poort is de p95; de rest is een rapport. In CI en op de LXC van Jonas. |
| P5 | **De update breekt niets** | 0010 voegt kolommen, tabellen en triggers toe. Zoeken op naam werkt meteen; de inhoud vult zich. Een schakelaar zet het oude zoeken terug. Vóór de migratie een reservekopie die met elk image werkt. Terug naar 0.7.0 is getest. |
| P6 | **Privacy standaard dicht** | Plek, personen en tekst uit foto's horen bij een bestand van één eigenaar, met zijn toestemming (Keuze 25a). Nooit in `preview_meta`, nooit via een publieke link, nooit in een log. Een gedeeld album deelt alleen wat de eigenaar koos. Geen totalen of scores naar buiten. |
| P7 | **Achtergrondwerk wijkt, en is eerlijk** | Drie rijen (`index`, `ocr`, `analyse`), `nice` 19 en de idle IO-klasse, korte transacties, batches, om de beurt per eigenaar, en wachten als er ≥ 2 transcodes lopen of OnlyOffice start. `analyse` begint pas na de backfill van de index. |
| P8 | **Onvertrouwde bestanden in de zandbak** | Wat de app vanzelf leest (tekst, OCR), leest de worker. Gezichten en beeld rekent de container `analyse`, op de preview, nooit op de blob. Een decoder krijgt een begrensd, genormaliseerd beeld, nooit de ruwe blob als het anders kan. |

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

  preview.ready (X2, X3; eigenaar zette het aan, zelf geüpload, Keuze 69)
        ├─► face_scan ──► rij "analyse" ──► container analyse, kind `werk` (YuNet, SFace)
        │                   ──► face (per node, vector versleuteld) ──► groeperen ──► voorstel
        └─► analysis(sha, 'embed') ──► rij "analyse" ──► kind `werk` (SigLIP 2, 256 px)
                            ──► blob_embed ──► bestand per eigenaar (.vec) ──► kind `zoeken`

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

zoeken op beeld: GET /api/v1/search?mode=image   (X3; 1 vraag tegelijk in de app)
  1. de tekst (of `like`) naar het kind `zoeken`: tekstmodel, dan de vectoren van de eigenaar
  2. top 1.000 `num`'s terug; de app filtert ze met SQL (eigen en levend, de filters)
  3. tweede controle per treffer; te laat (800 ms) → de treffers van gewoon zoeken, met een melding
```

### 0.4 Wat "klaar als" in dit ontwerp betekent

| Klaar als (stappenplan) | Kern (Z0–Z7) | X1–X3 (gekozen op 3 november 2026) |
|---|---|---|
| Een zoekopdracht over 100.000 bestanden antwoordt binnen 200 ms. | Ja. Een harde poort op de p95 in CI via HTTP in `python:3.12-slim` (§4.4), en `bench --los` op de LXC. | Zoeken op beeld rekent eerst een taalmodel en krijgt een eigen grens van 250 ms (Vraag 14, K2-13). Gewoon zoeken houdt 200 ms. Op de CPU van Jonas is het pas bewezen als `bench --los` en `beeld-bench` daar slagen. |
| OCR-tekst is doorzoekbaar. | Tekst uit PDF's met een tekstlaag, Office, tekstbestanden en (met OnlyOffice) oude .doc, .xls en .ppt is doorzoekbaar. Dat is geen OCR. | **X1**: tesseract in het image (Keuze 24). Scans en, als de eigenaar het aanzet, foto's van tekst zijn doorzoekbaar. |
| Gezichten zijn te benoemen en te zoeken. | Personen benoemen op een foto (met de hand, met of zonder kader), samenvoegen, verbergen, zoeken en filteren. | **X2**: gezichten automatisch vinden en voorstellen, per gebruiker aan te zetten (standaard uit). De kwaliteit op echte gezichten meet Jonas op zijn LXC (Vraag 8). |
| "Gezichten en objecten met lokale modellen; semantisch zoeken". | — | **X3**: zoeken op wat er op een foto staat, met woorden of "Meer zoals deze", in SQLite in plaats van pgvector (B15). Geen objecten als filter (Keuze 71). Standaard uit per gebruiker (Vraag 11). |

Loopt het uit, dan schuift eerst iets van X3, en pas daarna iets van X2 (§2.0, Vraag 13). De kern
schuift niet voor X2 of X3.

---

## 1. Keuzes

Elke keuze zegt wat, waarom, en waar het bewijs zit. "(A)" of "(B)" zegt uit welk ontwerp hij
komt; "(r2)" en "(r3)" zijn van revisie 2 en 3, met de kritiek erbij.

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
     schrijfactie. Keuze 20 en 23 begrenzen dat niet: die gaan over hoeveel inhouden er per
     transactie gaan, niet over het aantal kopieën van één inhoud (review golf 1, H1: 100
     kopieën gaven 2,8 s wachten, 250 "database is locked"). Daarom deelt `write_text` een
     budget over de kopieën: wat één schrijfactie in `fts_node` zet (body en ocr, opgeteld over
     alle nodes met die sha) is hooguit `TEXT_BUDGET`, 12 MiB tekens. Elke node krijgt dus
     `TEXT_BUDGET` gedeeld door het aantal nodes met die sha, en nooit minder dan 1000 tekens.
     Tot 12 nodes blijft 1 MB heel; daarboven zet een afgekapte body `truncated`. Rest: een
     kopie die er ná het schrijven bij komt, kost bij de volgende schrijfactie nog één keer
     kopieën × de oude tekst (de 'delete' in FTS5 heeft de oude tekst nodig); daarna is hij
     weer klein.
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

     **Herzien na de spike (S4, S10), alleen filters.** De SQL-p95 is ≤ 2,4 ms, dus poort 150 ms.
     Boven 64 wortels (`json_each`) is "vanaf de paden" een `SCAN` over heel `ix_node_mpath`:
     dan altijd langs de tijd, ook onder 20.000 nodes.
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

     **Herzien na de spike (S10).** Niet gehaald: bij 55.000 foto's met GPS p95 217–244 ms voor
     Nederland en 328–351 ms voor de wereld (3.46.1 en 3.53.1). De join op `node` per foto kost
     het; zonder join 22–34 ms. Dus komt de tabel `geo_cell`. Die staat nog niet in 0010.

     **Herzien na de review van Z0 (haalbaar F1 en F2).** Geen `geo_cell`. Opgeslagen tellingen
     per cel lopen na prullenbak of terugzetten uit de pas tot de job `geo` weer draait, en kunnen
     de filters van Keuze 43 niet toepassen: hetzelfde gebrek waarom `place_count` verdween (S9).
     En de tabel had geen eigenaar en geen migratie (0010 is van Z0, A0 heeft 0011 en 0012). Nu
     houdt `photo_geo` zelf de stand van zijn node bij, in dezelfde transactie, met triggers in
     0010 (§3.1): `dead` en `taken_at` zijn altijd die van de node, en een rij hoort alleen bij een
     bestand van de eigenaar van de node. Dan is `own_live_geo(g)` (`g.owner_id = :me AND
     g.dead = 0`, `app/search/live.py`) precies `own_live`, en kan de kaart zonder join:

     ```sql
     SELECT g.x16 >> :s AS cx, g.y16 >> :s AS cy, count(*) AS n,
            max(g.taken_at) AS t, g.node_id AS cover   -- de nieuwste foto van de cel
     FROM photo_geo AS g
     WHERE g.owner_id = :me AND g.dead = 0
       AND g.x16 BETWEEN :x0 AND :x1 AND g.y16 BETWEEN :y0 AND :y1
     GROUP BY cx, cy
     LIMIT 501
     ```

     met `ix_photo_geo_tile (owner_id, dead, x16, y16, taken_at, node_id)`: dekkend, dus geen
     rij per foto. Gemeten met `tools/zoekbench/bench.py --vormen` (55.000 foto's met GPS, 5
     rondes): p95 wereld 44,7 ms en Nederland 30,4 ms op 3.53.1, 66,2 en 33,4 ms op 3.46.1. De
     vorm met de join in dezelfde run: 241 en 194 ms (3.53.1), 219 en 146 ms (3.46.1). Zonder
     `taken_at` en `node_id` in de index 89 en 70 ms. Het bijhouden kost bij een map met 10.000
     foto's met plek, waarvan 2.000 in de prullenbak: prullenbak 0,37 s, terugzetten 0,31–0,34 s,
     verplaatsen 0,23 s; zonder plekken 0,17–0,22, 0,13–0,19 en 0,20–0,23 s (3.53.1; marker
     `zoek_perf`, grens 1 s). Een datumfilter gaat op `g.taken_at`; een filter op persoon, tag,
     map of camera doet de join met `node` over de al smallere set, met een eigen poort (Z3).
     **Herzien na de review van golf 3 (contract F1).** Ook plaats en land gaan op `photo_geo`
     zelf (`place_id`, `country`; datum op `taken_at` met de grens van de dag als zwevende
     tijd), zonder join: p95 79 ms op 3.46.1 en 78 ms op 3.53.1, onder de poort van 150 ms. Soort,
     map, camera, persoon en tag doen de join; per foto in het venster kost dat ±5 µs (de rij
     uit `node` en zijn `ftype`), bij de hele wereld op zoom 0–2 alle 55.000. Hun eigen poort is
     p95 ≤ 500 ms: gemeten 340 ms op 3.46.1 en 310 ms op 3.53.1 (`tests/photos/test_map_perf.py`,
     8 filters om de beurt). Onder de 150 ms kan dat alleen met soort en camera als kolom op
     `photo_geo`, dus een migratie (z3.md, Open).
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

     **Herzien na de spike (S4, S10).** De vorm met `IN mine` is zonder ANALYZE een kruisproduct:
     SQLite maakt van de twee lijsten gelijkheden op de sleutel `(a, b)` van `dup_pair`. Bij
     55.000 foto's stopte hij niet binnen 2 s. Deze vorm geeft dezelfde paren in p95 5–7 ms, en
     `dup_group` is niet nodig. Precies gelijk: p95 95–114 ms.

     ```sql
     SELECT p.a, p.b, p.dist, p.level FROM dup_pair AS p
     WHERE EXISTS (SELECT 1 FROM node AS n WHERE n.owner_id = :me AND n.blob_id = p.a
                   AND n.dead = 0 AND n.kind = 'file')
       AND EXISTS (SELECT 1 FROM node AS n WHERE n.owner_id = :me AND n.blob_id = p.b
                   AND n.dead = 0 AND n.kind = 'file')
     ```
5. **De vraag ontleden** (B, met ß in r2: S5). `app/search/query.py`, `parse()`:
   - hooguit 200 tekens en 10 woorden, anders `400 search_query_too_long`;
   - NFKC, kleine letters, ß en ẞ naar "ss", dan splitsen op alles wat geen letter of cijfer is.
     **Herzien na de spike (S8):** een teken op een letter (Unicode-categorie M) hoort bij het
     woord. Anders valt "İstanbul" (`i` + U+0307 na NFKC en kleine letters) in "i" en "stanbul".
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
6. **Rangorde** (B). bm25 met gewichten naam 10, titel 6, tekst 1, OCR 0,8, meta 0,5 (en OCR van
   een foto 0,8, de kolom `pocr` van de review van Z0). Bij gelijke
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
   - **Herzien na de spike (S1).** Ronde A stopt bij een voorvoegsel 2–22 ms na zijn grens: FTS5
     voegt de lijsten samen tussen twee keer kijken naar de interrupt. Met een vaste 40 ms voor B
     kwam het antwoord tot 191 ms. Daarom krijgt B de rest van 120 + 40 ms, minstens 1 ms. Zo
     gaf `"ver"*` op 20.000 documenten 162,6–171,5 ms (30 keer), en bij 100.000 nodes is het
     p95 161–162 ms, max 178 ms; op 3.46.1 met 164.000 nodes één keer 191 ms. "±5 ms" is dus een
     p95 van ±162 ms met een staart tot ±190 ms. Ronde C zelf kost ≤ 1,5 ms; ronde B hielp bij
     een voorvoegsel hooguit 1 op de 13 keer.
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
    **Herzien na de review van Z0 (haalbaar F3).** De stukken maken de aanroepers, niet
    `delete_nodes` (die commit nooit): `routers/trash.py` heeft `purge_chunks`, zoals
    `userdelete._drop_nodes`. Per stuk: commit, `BlobStore.lock`, opnieuw lezen wat er nog weg
    moet, versies weg, `ref_count` omlaag, `delete_nodes`, commit. Opnieuw lezen omdat een ander
    tussen twee stukken het slot krijgt: een node gaat alleen mee als hij er nog is, dood is (hij
    of een map erboven in de prullenbak) en niet verplaatst is (`mpath` als in het plan); wat er
    intussen onder kwam, gaat mee (anders nam de cascade het mee zonder `ref_count`). Wordt de map
    intussen teruggezet, dan komt terug wat nog niet weg was, en antwoordt het wissen
    `422 not_trashed`. Gemeten (3.53.1): de hele boom van 10.911 nodes in één transactie hield een
    upload 0,78 s op; in stukken komt hij na het eerste stuk aan de beurt. **Na de nacontrole van
    Z0:** dood volgt uit de keten van ouders (`access.ancestry`), niet uit `node.dead`, en de
    volgorde (kinderen vóór hun ouders) uit de diepte in de boom, niet uit `mpath`: die kolommen
    houden alleen de triggers van 0010 bij, en op PostgreSQL maakt 0010 geen triggers (§3.1).
12. **Herstellen en controleren, zonder de schrijver vast te houden** (herzien in r2: S13).
    - `search-rebuild` is nooit één statement: een `rebuild` van FTS5 houdt de schrijver minuten
      vast, en `busy_timeout` is 5 s (V9). In plaats daarvan: `delete-all` (17–250 ms, V4), dan
      opnieuw vullen per 2.000 `num` met een commit na elk stuk (≤ 142 ms per stuk op 3.46.1, V4).
      Een stuk is ook hooguit 4 MiB tekst (body en ocr, `CHUNK_TEXT`; minstens één node), en na
      elk stuk wacht de herbouw 0,1 s zodat een wachtende upload de schrijver krijgt: op de
      groot-boom hield één stuk van 2.000 nodes de schrijver 4,7 s vast (review golf 1, H3).
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
    - **Herzien na de spike (S2).** Een stuk begint met een schrijfactie (`UPDATE search_state
      SET cursor = cursor WHERE id = 1 RETURNING cursor`) en leest pas daarna `:hi` en
      `search_seq.next`. Met pysqlite (en dus aiosqlite) opent een SELECT geen transactie; BEGIN
      komt pas vóór de eerste schrijfactie. Leest het laatste stuk `next` daarvoor, dan mist de
      index een node die er net bij kwam (in de test 3 in `fts_node`, 18 in `fts_name`). Bij
      100.000 nodes is het langste stuk 201–294 ms en wacht een upload tot 687 ms.
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
    - **Herzien na de spike (S11).** `/proc/<pid>/fd` is na "klaar" dicht (`EACCES`), maar
      zonder Yama is de handdruk niet genoeg. Een proces van uid 10002 opent vóór "klaar"
      `/proc/<pid>/mem` of hangt er met ptrace aan; die fd en die tracer blijven na
      `PR_SET_DUMPABLE 0` werken, en de blobs staan na "klaar" in het geheugen van het kind. De
      aanvaller in de test las zo de blob. Daarom start het kind uit een kopie van python die
      niemand mag lezen (0711, root, met een `pyvenv.cfg`), zoals ffmpeg al (Keuze 11 en 47 van
      fase 5): dan is het vanaf de exec niet-dumpbaar, zonder venster. De handdruk blijft, als
      tweede slot. stderr van het kind is `/dev/null`; fouten gaan over de socket. Een vork na
      "klaar" sluit de `mem`-fd ook af, maar niet een tracer van vóór "klaar".
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

### OCR (X1)

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
    - **De index gaat vóór** (review golf 4, uitrol F1). `ocr` en `text` delen de ene plaats
      `background` van de worker. Heeft de stap `text` een batch met de worker, dan begint OCR
      geen nieuwe pagina (ook geen overzicht van de pagina's), en wacht `text` op de pagina die
      OCR nog leest. Zo wacht de index hooguit één pagina, en houdt een achterstand van OCR hem
      niet op. Geen eigen plaats voor OCR: die zou de grens voor live werk (`live_max`) met één
      verlagen. Na elke pagina kijkt de pomp ook naar pauze, wijken (Keuze 50) en uit op
      `/beheer` (uitrol F3).
24. **tesseract in het image** (herzien in r3: Jonas koos ja). In de apt-RUN van de Dockerfile
    (regel 19–21) komen `tesseract-ocr tesseract-ocr-nld tesseract-ocr-eng tesseract-ocr-osd`,
    en in de RUN van media (regel 42–45) `chmod 0711 /usr/bin/tesseract`. **Herzien na S5:**
    spike S5 mat in CI 69 MiB (21,1 MB download; de talen 20 MB, de rest tesseract, leptonica
    en 12 bibliotheken). De oude poort (+ ≤ 30 MB) is niet te halen; Vraag 1 noemde ±20 MB.
    Daarom Vraag 15, en Jonas koos op 4 november 2026 "Ja, erin": tesseract in het image, de
    poort in `images` + ≤ 75 MB. De code blijft werken zonder tesseract (een
    image dat ouder is, of een eigen build): `caps` van de worker zegt dan `ocr: false`, de rijen
    blijven `none` met code `ocr_missing`, en worden weer `wait` zodra `caps` OCR meldt. `/beheer`
    zegt dan "OCR: niet geïnstalleerd". OCR van PDF-scans staat aan; OCR van foto's blijft per
    gebruiker standaard uit (Keuze 23).

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
    - **Prullenbak**: de rijen blijven, maar `own_live` laat ze weg (in `photo_geo` zet een
      trigger `dead` mee: `own_live_geo`, review Z0). **Terugzetten**: ze zijn er
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
      (`on_timeline`, `live_items`, V9); met `dead` is dat één kolom. Herzien na de review van
      Z0 (haalbaar F2): de kaart en de plaatsen lezen alleen `photo_geo`, met `own_live_geo(g)`
      (`g.owner_id = :me AND g.dead = 0`). Dat is dezelfde regel: de triggers van 0010 houden
      `photo_geo.dead` gelijk aan `node.dead`, en een rij hoort bij een bestand van de eigenaar.
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
29. **Plaatsnamen en landen offline** (A en B, herzien in r2: S10, C12; Vraag 5 is ingetrokken,
    de standaard geldt).
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
      `install.sh` beide bestanden naar `DATA_ROOT/geo/` (bij Jonas `/opt/platform/data/platform/geo/`): de
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
    **Herzien na de spike (S6):** de vorm. Per land `a3`, `a2`, `nl`, `en` en de ringen per vlak;
    een ring is één lijst gehele getallen: het eerste punt in honderdsten van een graad, daarna
    verschillen, zonder sluitpunt. Bron 838.726 bytes (sha256 `6866c877…c977f`), uitvoer
    85.456 bytes, gzip -9 33.686 bytes.
32. **Clusters rekent de server** (B). Op zoom `z` (0–14) is een cel
    `(x16 >> (14 − z), y16 >> (14 − z))`: vier cellen per tegelbreedte. Boven zoom 14 losse
    foto's. De SQL staat in Keuze 4a: `GROUP BY` over eigen, levende foto's in het venster,
    ≤ 500 cellen, met een omslag (de nieuwste). Poort p95 ≤ 150 ms bij 55.000 foto's met GPS
    (S10, §4.4). Plaatsnamen op de kaart pas vanaf zoom 6.
    **Herzien na de spike (S10):** niet gehaald met de join; zie Keuze 4a, de tabel `geo_cell`.
    **Herzien na de review van Z0 (haalbaar F1 en F2):** geen `geo_cell`; `photo_geo` houdt
    `dead` en `taken_at` van de node bij, en de kaart gaat zonder join (Keuze 4a), p95 ≤ 67 ms.
33. **Een lijst naast de kaart** (A; herzien in r2: S9). "Plaatsen": de plaatsen met het aantal
    foto's, voor wie geen kaart gebruikt of met een schermlezer werkt. Een klik zet het filter
    `place`. Het aantal komt per verzoek uit `GROUP BY` op `ix_photo_geo_place (owner_id,
    place_id)` met `own_live`, niet uit een opgeslagen telling: die zou na prullenbak,
    terugzetten, wissen of "uit" niet meer kloppen. Herzien na de review van Z0 (haalbaar F2):
    op `ix_photo_geo_place (owner_id, dead, place_id)` met `own_live_geo`, zonder join.

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
39. **Gezichten automatisch: X2, in fase 6** (herzien in r3: Jonas koos "Ook gezichten"). YuNet
    (MIT) en SFace (Apache-2.0) via onnxruntime, in de afgeschermde container `analyse`. De regels
    van revisie 2 blijven, en zijn strenger geworden: aan per gebruiker (standaard uit), een verbod
    van de beheerder, `face` per node (Keuze 25a), alleen eigen en levende bestanden, "uit" wist
    alles, nooit een vector in de API, en een gezicht is een voorstel tot je bevestigt. Hoe, staat
    in Keuze 55–69: de container (56), de extra (57), de modellen (58), de versleuteling met een
    sleutel per eigenaar (68), en wie de foto uploadde (69).

### Beeld: zoeken op wat er op een foto staat

40. **Zoeken op beeld: X3, in fase 6** (herzien in r3: "alles"). SigLIP 2 B/32 (Apache-2.0,
    meertalig) in dezelfde container, met vectoren als BLOB in SQLite in plaats van pgvector
    (B15). Standaard uit per gebruiker tot het gemeten is (Vraag 11). Hoe, staat in Keuze 70–84.

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
    `ocr_photos` (X1), `faces` (X2) en `beeld` (X3). `faces` en `beeld` staan standaard uit; Z0b
    legt ze vast (Keuze 55). `GET` en `PATCH /me/photo-settings`.

### De web

45. **De ingang** (A, herzien in r2: C15). Een zoekveld in de bovenbalk, hooguit 0,8 kB in de
    initiële JS (nu 96,4 van 100 kB); de combobox is lazy. Ctrl/Cmd+K zet de focus erop. Op
    Bestanden blijft `/` het naamfilter (J9). Enter opent `/zoeken?q=…`. *Noot (Z4): in de code
    was `/` het zoekveld van Bestanden en Mod+F het naamfilter. Z4 houdt één zoekveld, dat
    bovenaan: `/` zet daar de focus in, Mod+F blijft het filter (z0.md, Open 12;
    [toetsenbord-z4.md](toetsenbord-z4.md)).*
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

49. **Drie rijen, eerlijk per eigenaar** (A, herzien in r2: S15, en in r3: K2-07). `index` (tekst,
    hash, GPS-backfill, en het bestand per eigenaar van X3) draait altijd, ook zonder media:
    `active_lanes` zet hem erbij [code: wt-z0api `app/jobs.py:81`, `:702–713`]. `ocr` (X1) draait
    alleen als media aan staat en OCR aan staat. `analyse` (X2, X3) draait alleen als de container
    er is, en begint pas als de backfill van de index klaar is (K2-07). Elke rij één job
    tegelijk; een job is een batch (Keuze 17), samengesteld om de beurt per eigenaar (Keuze 20).
50. **Wijken** (B). `jobs.Defer` (fase 5) voor `index`, `ocr` en `analyse` als er ≥ 2 transcodes
    lopen of OnlyOffice start. De naamindex wacht nooit: die zit in de triggers.

### Beheer, uitrol, update en terug

51. **Een tab "Zoeken en foto's" op `/beheer`.** De motor (`fts` of `like`), de stand van de
    index (namen klaar; tekst n van m; mislukt met sha en code; een herbouw met zijn voortgang),
    waar tekst gelezen wordt (in de zandbak of erbuiten), tijdsgrens geraakt per uur (ronde B en
    C), `search_busy` per uur, OCR (geïnstalleerd, aan, pagina's in de rij), plaatsnamen (geladen,
    bron, datum), en de uitkomst van `bench --los`. Daarnaast de blokken "Gezichten" en "Zoeken op
    beeld" (Keuze 67, 82): alleen aantallen, nooit een vector of een naam. Knoppen: index
    herbouwen, mislukte opnieuw, achtergrondwerk pauzeren.
52. **Een reservekopie vóór de migratie, met elk image** (A, herzien in r2: C7, C13).
    - `install.sh` maakt de kopie met alleen de standaardbibliotheek:
      `compose run --rm --no-deps platform python -I -c '<sqlite3.Connection.backup>'`, terwijl de
      oude app nog draait. Dat werkt met het image van 0.7.0 en van 0.8.0; `app.cli db-backup`
      bestaat in 0.7.0 niet (V9).
    - Het bestand heet naar de alembic-head en de tijd: `/data/backup/platform-0008-<tijd>.db`.
      Dat is het voorbeeld van revisie 2; vanaf 0.7.1 is de head `0009`, en Z7 zet de namen
      daarop (§3.10).
    - Per head blijft de nieuwste kopie, en nooit gaat de laatste kopie van vóór een migratie weg.
      Zo staat de kopie van 0008 er na twee nieuwe runs op 0010 nog.
    - Lukt de kopie niet, dan stopt het script: er is dan nog niets veranderd.
    - `app.cli db-backup` (Z0) doet hetzelfde voor de hand en voor `/beheer`.
    - **Zonder gezichten en zonder beeld** (herzien in r3: K1-01, K1-08). De kopie gaat via een
      `.part`, en daarin wist de snippet `face_person`, `face`, `face_scan`, `face_owner` en
      `blob_embed` vóór de `rename` (X2, "Privacy"). Na het terugzetten rekent de container ze
      opnieuw.
53. **Terug naar 0.7.0 is beschreven en getest** (A, herzien in r2: C7, §3.10). Het script komt
    van de commit van 0.7.0, niet van de branch: dan draait het oude script met het oude image.
    `test_install_search.sh` draait precies de stappen van §3.10.
54. **Meten op de LXC** (A). `app.cli bench --los` maakt een boom van 100.000 in
    `/data/cache/zoekbench` (±2 minuten, ±500 MB), meet zoeken voor "eigenaar" en "alles", en
    wist hem weer. De uitkomst komt op `/beheer`. Al in week 1 kan Jonas de losse versie uit Z0
    draaien in zijn container van 0.7.0, zonder iets te installeren (§3.7, S9).

### Fundament voor gezichten en beeld (Z0b en A0)

Nieuw in r3. Gezichten (X2) en beeld (X3) delen één container, één handdruk, één rij en één
lijst met modellen. De keuzes hieronder leggen vast wat ze delen; Keuze 56–69 zijn van X2,
Keuze 70–84 van X3.

55. **Z0b: de contracten van X2 en X3 komen vóór golf 1** (K2-01). Z0 bevriest de contracten,
    zodat golf 1–4 naast elkaar kunnen bouwen. X2 en X3 voegen er velden, waarden en een rij aan
    toe. In golf 5 zou dat bestanden raken die dan van Z4, Z6 en Z7 zijn, en tests met exacte
    lijsten breken. Daarom doet een kleine Z0b (1 dag) dat nu, vóór de review van Z0. Migratie
    0010 is nog niet gepusht (`wip/z0-api` heeft geen upstream), dus dat kan zonder schade.
    - **Wat Z0b vastlegt** (de lijst per bestand staat in §3.3):
      - de velden en waarden van Keuze 66 en 80, elk met een standaard, zodat een oude client
        hetzelfde antwoord krijgt;
      - `Step.EMBED`, de rij `analyse` in `LANES` (alleen actief met de container, Keuze 59);
      - de sleutels `faces` en `beeld` in `PHOTO_PREFS`, met de standaard `uit`;
      - de routes van X2 en X3 als stub (`501 not_built("X2")` of `"X3"`);
      - de lege plekken in de web (`LikeRow`, het blok "Gezichten" en "Zoeken op beeld" op
        `/beheer`), de i18n-gebieden `photos_faces` en `beeld`, de catalogi `faces.ts` en
        `beeld.ts`, en de regels in `budget.mjs`;
      - `previews.register_large(fn)` (Keuze 65).
    - **De tests met exacte lijsten** gaan in Z0b mee: ze eisen de nieuwe exacte lijst. Geen test
      wordt zwakker.
    - **Ook in Z0b:** de docstring van `api/app/prefs.py` noemt Vraag 3 waar Vraag 2 bedoeld is;
      en `node_bu_text` maakt het kader van `photo_person` bij nieuwe inhoud nog niet leeg, terwijl
      Keuze 25a dat eist [code: wt-z0api `app/search/schema.py:221–227`]. Z0b zet beide recht in
      0010.
      **Na Z0b:** de docstring staat recht; 0010 is gepusht, dus `node_bu_text` doet 0011 (A0;
      [z0b.md](z0b.md)).
    - **Wat niet in Z0b:** de tabellen van gezichten en beeld. Die komen in 0011 en 0012, in A0
      (§3.1a).

### Gezichten automatisch (X2)

56. **Een eigen container `analyse`, minstens zo dicht als de mediaworker** (herzien in r3: K1-04,
    K1-07, K1-16, K2-02, K2-06, K2-07). De details staan in §3.11.
    - **Image en gebruiker.** Hetzelfde image `platform:local`, als uid 10003. Geen `/data`: elke
      preview komt als open fd over de socket in `analyse-run`.
    - **Afscherming.** `network_mode: none`, `read_only`, `cap_drop: [ALL]`, `no-new-privileges`,
      `pids_limit: 64`. Geen `init: true`.
    - **Eén werkmodel voor X2 en X3** (K2-02). pid 1 doet de handdruk en houdt de sleutel. Hij
      start twee kinderen: `werk` (`nice` 19: decoderen, gezichten, beeld embedden, groeperen) en
      `zoeken` (`nice` 0: de tokenizer, het tekstmodel en de vectoren van X3). Elk kind krijgt één
      opdracht tegelijk; een tweede voor hetzelfde kind krijgt `busy`. Een beeldvraag wacht dus
      nooit op een groepsronde. Elke opdracht heeft een deadline, en een opdracht `cancel` breekt
      hem af.
    - **Elk kind is niet-dumpbaar** (K1-04). Het eerste wat een kind doet, nog vóór het een fd
      krijgt, is `prctl(PR_SET_DUMPABLE, 0)`. Een ander proces van uid 10003 kan dan niet in
      `/proc/<kind>/fd`. `python` is `0755` in het image, dus zonder deze regel zou het kind na
      `exec` wel dumpbaar zijn [code: `Dockerfile:35–45`; gemeten: K1-04].
    - **Een kind leeft niet lang.** pid 1 start `werk` opnieuw na `ANALYSE_CHILD_BATCHES` (8)
      opdrachten, en na 10 minuten. Een kind dat gekraakt wordt, ziet zo hooguit 8 batches.
    - **Grenzen per kind** (K1-07). `RLIMIT_DATA` (`werk` 1 GB, `zoeken` 1,5 GB) en de wachter van
      `app/media/worker/guard.py`, die een kind boven zijn budget stopt. Dat werkt ook als de host
      geen cgroup-grenzen kent.
    - **Logs** (K1-16). De stderr van een kind gaat via een pipe naar pid 1. pid 1 logt alleen een
      code en een lengte, nooit de inhoud.
    - **Grenzen van de container**, als de host ze kent: `mem_limit: 3g` (K2-06), `cpus: 1.0`,
      `cpu_shares: 256` (K2-07).
    - **Geen terugval.** Zonder container zijn er geen gezichten en geen zoeken op beeld. Er is
      geen `ANALYSE_SANDBOX=uit`.
57. **numpy en onnxruntime als optionele extra, in een eigen map** (herzien in r3: K2-09, K2-15).
    - **Waar.** `[project.optional-dependencies] analyse` in `api/pyproject.toml`. De basis
      verandert niet (B19).
    - **Installatie.** Niet in `/opt/venv`, maar met `uv pip install --target /opt/analyse`. De map
      is `0750 root:analyse`. Alleen de worker zet hem op `sys.path`. Een map met `o-rwx` in
      `/opt/venv` werkt niet: uid 10001 krijgt dan bij `import numpy` een leeg namespace-pakket in
      plaats van een `ImportError`, en Pillow en pypdfium2 denken dan dat numpy er is
      [gemeten: K2-09].
    - **Wat erin zit:** numpy, onnxruntime, protobuf, flatbuffers en packaging (vijf pakketten, met
      de versies in `uv.lock`) [gemeten: spike B1; K2-15].
    - **Grootte.** +118–141 MB [gemeten: spike B1 en de proef met `--target`].
    - **De wacht.** De job `api` blijft zonder extra draaien. De job `images` eist:
      `importlib.util.find_spec("numpy")` is `None` voor uid 10001 en 10002, en `numpy.ndarray`
      bestaat voor uid 10003.
58. **Twee modellen, vast met sha256, buiten `$DATA_ROOT`** (herzien in r3: K1-06, K1-12, K1-13).
    - **Welke.** YuNet 2026may (MIT) en SFace 2021dec fp32 (Apache-2.0), van een vaste commit van
      opencv_zoo (§3.11) [gemeten: spike B4, B6].
    - **`models.lock`.** `api/app/analyse/models.lock` noemt per model de sha256, de grootte, de
      naam en de URL. X3 voegt zijn regels toe.
    - **Waar ze staan.** In `MODELLEN_ROOT` (standaard `/opt/platform/modellen`). Elke map op dat
      pad is van root. `$DATA_ROOT` is van de app (10001), en daar zou root bij de installatie
      symlinks volgen die een overgenomen app heeft geplant [gemeten: K1-06].
    - **Download.** Naar een map van `mktemp -d` waar alleen root bij kan. `install.sh` weigert een
      doel dat een symlink is, en doet nooit `install -d` of `chmod` onder een map van de app.
      Mislukt de download, dan stopt de installatie niet.
    - **Laden.** De worker leest de bytes van een model één keer, rekent de sha256 over precies die
      bytes, en maakt daarmee `InferenceSession(bytes, providers=["CPUExecutionProvider"])`. Hij
      controleert dat `get_providers()` alleen `CPUExecutionProvider` geeft. Het wiel heeft ook de
      `AzureExecutionProvider` [gemeten: spike B1].
    - **Licenties.** De licentieteksten staan in het image (`api/app/analyse/licenties/`) en
      `install.sh` schrijft ze naast de modellen. YuNet en SFace staan op `/over`.
    - **Uitzetten.** Met `ANALYSE=uit` in `.env` is er geen download en geen container.
59. **De app merkt zelf of de analyse er is.**
    - **Vier standen:** `ok`, `down` (met een reden), `models_missing` en `off`.
    - **Monitor.** Elke 30 s een handdruk met `caps`, zoals bij media.
    - **`off`.** `PLATFORM_ANALYSE_ENABLED` staat niet aan. Dan kan niemand gezichten of beeld
      aanzetten (`409 faces_unavailable`, `409 beeld_not_installed`), en draait de rij niet.
    - **`down` en `models_missing`.** Alles blijft in de rij staan. Er worden geen pogingen
      geteld.
60. **Gezichten vinden in de preview van 1024** (herzien in r3: K1-07, K2-04, K2-05).
    - **Invoer.** De `1024.webp` die de app al maakte (gedraaid volgens EXIF). Nooit het origineel.
      Het kind opent hem met `Image.open(fd, formats=["WEBP"])`, met een grens van 4 MP, en de
      waarschuwing voor een decompressiebom als fout (zoals `render.py:397–399`).
    - **Opvullen tot een veelvoud van 32.** YuNet 2026may faalt op een hoogte of breedte die geen
      veelvoud van 32 is, zoals de gewone 3:2-preview van 1024 × 683 [gemeten: K2-05]. Het kind
      vult rechts en onder aan met nullen; de coördinaten blijven gelijk.
    - **Grenzen.** Een score ≥ 0,9; een gezicht van minstens 40 px (de korte kant in de preview);
      hooguit 40 gezichten per foto, de grootste eerst.
    - **Per gezicht:** een kader (0–1, zoals in Keuze 37), de score, en een vector van SFace
      (128 × float32, genormaliseerd).
    - **Wat niet.** Alleen foto's, geen video.
    - **Tijd.** ±142–164 ms voor een foto met één gezicht, met het decoderen van een WebP van
      1024 × 768 erbij [berekend: K2-04]. Hooguit 10 s per foto.
61. **Gegevens per node, in migratie 0011** (herzien in r3: K1-02, K1-03, K2-17, K2-18).
    - **Tabellen.** `face`, `face_person`, `face_scan`, `face_owner` en `node_origin`, met
      `node_id`, `owner_id` en `ON DELETE CASCADE` (Keuze 25a). De SQL staat in §3.1a.
    - **Triggers.** Bij een andere eigenaar, bij nieuwe inhoud en bij het wissen van een persoon. Ze
      staan in `app/search/schema.py`, in dezelfde `TRIGGERS` als die van 0010 en 0012 (K2-17).
    - **Lezen.** Alles wat leest, gebruikt `own_live`. De vectoren voor een groepsronde komen via
      de index `ix_face_new`, zodat SQLite niets hoeft te sorteren (K1-03).
62. **Groeperen en voorstellen gebeurt in de worker, zonder namen** (herzien in r3: K2-06).
    - **Wat de worker krijgt.** De app geeft per eigenaar een verzegelde memfd met vectoren en
      indexen. De worker ziet geen UUID's, namen of nodes, en per opdracht één eigenaar.
    - **Voorstellen.** Per open gezicht: de persoon met de hoogste overeenkomst met zijn
      bevestigde gezichten, boven een drempel en met een marge.
    - **Groepen.** Wederzijdse buren boven een drempel. Twee groepen worden pas samengevoegd na een
      controle op het gemiddelde, zodat er geen ketens ontstaan.
    - **Hooguit 60.000 open gezichten per ronde** (`PLATFORM_FACES_GROUP_MAX`), de nieuwste eerst.
      Dat is precies wat de poort test, en houdt het geheugen van de container onder de grens
      (K2-06).
    - **Kosten.** 60.000 gezichten in ±38–40 s op één core, 285 MB maxrss [gemeten:
      `gezichten/uit/groep2_60000.json`].
63. **Bevestigen zet een persoon op de foto. Z6 blijft de bron.**
    - **"Ja".** `face_person` wordt `ja`, en er komt een `photo_person` met `source = 'auto'` en
      het kader. Zoeken, filters, herinneringen en slimme albums lezen alleen `photo_person` (Z6).
    - **"Nee".** De afwijzing wordt onthouden.
    - **Met de hand getekend.** Een kader dat iemand zelf tekende, wordt bij overlap (IoU ≥ 0,5)
      meteen "ja".
    - **Z6.** Samenvoegen, een persoon van een foto halen en een persoon wissen roepen een haak aan
      (`app/people/hooks.py`, A0).
64. **Privacy: de harde regels** (vervangt de lijst in Keuze 39; herzien in r3: K1-01, K1-15).
    - per gebruiker, standaard uit, met een eerlijke uitleg vóór het aanzetten (X2, "De web");
    - de beheerder kan het verbieden;
    - "uit" en een verbod wissen alles van die eigenaar, ook de bytes, en de sleutel (Keuze 68);
    - nooit een vector in de API, de logs, de gebeurtenissen, een bericht van de worker of een
      reservekopie van het platform;
    - een persoon wissen wist zijn koppelingen en de vectoren van zijn gezichten;
    - "niet tonen" en "ongedaan maken" zetten een checkpoint klaar, zoals wissen (K1-15);
    - alleen eigen, levende foto's, en alleen foto's die de eigenaar zelf uploadde of die een
      ander met gezichten aan uploadde (Keuze 69);
    - een kopie bij een ander krijgt niets, en een publieke link ook niet.

    De tabel met regels en tests staat in X2, "Privacy".
65. **Achtergrondwerk in de rij `analyse`** (wijzigt Keuze 49 en 50; herzien in r3: K2-07).
    - **Eén job tegelijk.** Een batch van 16 foto's, om de beurt per eigenaar (hooguit 4 per
      eigenaar), de nieuwste foto's eerst.
    - **Pas na de index.** De backfill van gezichten en beeld begint pas als de backfill van de
      index klaar is. Zo vallen ze na de update niet samen.
    - **Wijken.** `Defer` bij ≥ 2 transcodes, als OnlyOffice start, en bij "pauzeren".
    - **Zacht.** Het kind `werk` draait met `nice 19` en één thread.
    - **Kosten.** 100.000 foto's met één gezicht kosten ±3,9–4,6 core-uur [berekend: K2-04].
66. **De web: voorstellen waar je je personen al ziet.**
    - **`/fotos/personen`.** Twee blokken: "Is dit Anna?" en "Wie is dit?".
    - **De pagina van een persoon.** "Misschien ook Anna".
    - **De viewer.** In het infopaneel de gevonden gezichten.
    - **Toetsenbord.** Alles werkt met J, N en V.
    - **Laden.** In een lazy chunk; de initiële JS groeit niet.
67. **Een blok "Gezichten" op `/beheer`** (wijzigt Keuze 51; herzien in r3: K2-21). Het blok toont
    de stand van de container en de modellen, de rij, aantallen (nooit per gebruiker, nooit een
    gezicht, en mislukte foto's alleen als aantal per code), de schakelaar "Gezichtsherkenning
    toestaan", en de zin over reservekopieën en snapshots.
68. **Vectoren van gezichten versleuteld, met een sleutel per eigenaar** (nieuw in r3: K1-01).
    - **Waarom.** Jonas maakt elk uur ZFS-snapshots die tot 4 weken blijven, en elke nacht een
      offsite-kopie die alleen kan groeien (append-only) [doc: `docs/stappenplan.md:147–151`,
      `:289`]. Daar staan de vectoren nog in, ook na "uit". Wissen in de database alleen is dus
      niet genoeg.
    - **Hoe.** `face.vec` is AES-GCM met een sleutel van 32 bytes per eigenaar: een nonce van 12
      bytes, de versleutelde 512 bytes en een tag van 16 bytes (540 B). De `face.id` is de
      associated data. `cryptography` is al een basisafhankelijkheid
      [code: `api/pyproject.toml:19`].
    - **Waar de sleutels staan.** In `SLEUTELS_ROOT` (standaard `/opt/platform/sleutels/gezichten`,
      `0700`, van uid 10001), in de container van de app op `/keys`. Dat is buiten `DATA_ROOT`,
      dus niet in een snapshot of kopie van alleen de data. Op de LXC van Jonas staat alles op één
      rootdisk: een snapshot of backup van de hele container bevat de sleutels wél. Het runbook
      zegt hoe je ze eruit houdt: `SLEUTELS_ROOT` op een eigen mount point met "Backup" uit, zodat
      vzdump hem overslaat. Een snapshot van de container in Proxmox neemt elk mount point mee; in
      zo'n snapshot blijven oude vectoren leesbaar tot hij weg is.
    - **"Uit".** De job wist eerst de sleutel (`unlink` en `fsync` van de map), dan de rijen. Vanaf
      dat moment is elke kopie van de vectoren onleesbaar waar de sleutel niet in zit: een snapshot
      of kopie van alleen de data, de offsite-kopie van de data, de WAL en tijdelijke bestanden.
      Een kopie die de sleutel wel heeft (een snapshot of backup van de hele container van vóór
      "uit"), houdt de oude vectoren leesbaar tot die kopie weg is.
    - **Wie de sleutel gebruikt.** Alleen de app. De worker krijgt de vectoren ontsleuteld in een
      verzegelde memfd, en ziet nooit een sleutel.
    - **Een kopie terugzetten.** Een rij die niet te ontsleutelen is, telt als gewist; die foto
      krijgt een nieuwe scan.
    - **Wat het niet oplost.** Wie de server beheert, kan bij een lopende app de sleutels lezen.
      Dat staat in de tekst bij het aanzetten.
69. **Wie een foto uploadde, telt mee** (nieuw in r3: K1-02; Vraag 9).
    - **Het probleem.** Een bestand dat een ander in jouw map zet, wordt van jou: bij een upload in
      een gedeelde map, via een publieke uploadlink, en bij een kopie naar jouw map
      [code: `api/app/routers/uploads.py:344`, `api/app/models.py:209–211`,
      `api/app/routers/files.py:1350–1351`]. Zonder regel zou zo'n foto gescand worden onder jouw
      toestemming.
    - **Wat 0011 vastlegt.** `node_origin(node_id, created_by, via_link)`, gevuld bij upload, kopie
      en `place_file`. `created_by` is NULL bij een upload via een link.
    - **De regel (standaard van Vraag 9).** `faces.plan` slaat een node over als `created_by` niet
      de eigenaar is, tenzij `created_by` zelf gezichten aan heeft. Een upload via een link nooit
      (code `not_own_upload`). Voor beeld (X3) geldt hetzelfde met `beeld`.
    - **Bestanden van vóór 0011** hebben geen rij. Die tellen als door de eigenaar zelf geüpload;
      wie ze echt uploadde, weet het platform niet. Zo staat het in Vraag 9.

### Zoeken op beeld (X3): model en rekenen

70. **Zoeken op beeld komt in fase 6, als pakket X3** (X3; vervangt Keuze 40).
    - Wat het is: een vraag in woorden, of een eigen foto, wordt een vector. De container vergelijkt
      die met de vectoren van je eigen foto's en geeft de best passende terug.
    - Zonder pgvector. B15 zegt: standalone op SQLite. De vectoren staan als BLOB in SQLite
      (Keuze 76). Zoeken is een brute-force-scan met numpy in de container: 35–42 ms over
      100.000 × 768 op één core [gemeten, B6]. pgvector pas met het cluster.
    - Afwijking van het stappenplan. Daar staat "semantisch zoeken via pgvector". Het doel blijft
      gelijk, de weg is anders (B15).
    - X3 bouwt op de container `analyse` van A0 en X2a (Keuze 56, 75) en komt dus na A0 (§5).

71. **Wat X3 bewust niet doet** staat in het overzicht "Wat X3 doet, en wat niet" bij X3 (§2). Twee
    punten met een reden die verder gaat dan tijd:
    - **Geen foto's van anderen.** Een eigenaar kiest zelf of zijn foto's bekeken worden
      (Keuze 82). Als Bram via een deling op beeld zou kunnen zoeken in Eva's foto's, telt Eva's
      keuze "uit" niet meer. Bovendien houdt een bestand per eigenaar de vraag snel (Keuze 76).
    - **Niet mengen met gewoon zoeken.** Gewoon zoeken gaat over alles wat je mag lezen en
      rangschikt met bm25; beeld gaat over je eigen foto's en rangschikt met een cosinus. Eén lijst
      zou een score van het ene naast een score van het andere zetten, zonder dat die iets met
      elkaar te maken hebben. En het zoekpad van Keuze 9 met zijn grens van 200 ms blijft dan
      precies zoals het is.

72. **Het model: SigLIP 2 B/32-256, beeld en tekst in int8** (`google/siglip2-base-patch32-256`).
    - **Licentie van de gewichten:** Apache-2.0 ("License: apache-2.0" in de bestandsboom op
      Hugging Face) [doc, B4].
    - **Nederlands:** recall 72,72 in de tabel "Dutch" van docs.immich.app [doc, B4]. Het model is
      meertalig: een vraag in het Engels werkt ook.
    - **Grootte in int8:** beeld ±103 MB en tekst ±284 MB, samen ±390 MB [gemeten op een proxy met
      dezelfde vormen, B6]. In fp32 is het ±1,5 GB op schijf, en het beeldmodel alleen al neemt +578
      MB RSS [gemeten, B6]. Dat past niet goed in het budget van de container (Keuze 75). Daarom
      int8. (Het ontwerp zei "±200–400 MB": dat klopt alleen voor int8.)
    - **Kosten op één core:** beeld 66–77 ms per foto (p95 81–89), tekst 62–63 ms per vraag
      (p95 69–74) [gemeten op de proxy, B6]. De rekentijd hangt af van de vormen, niet van de
      waarden; de proxy was even groot als de echte export (378,2 tegen 378 MB in fp32).
    - **Wat int8 kost aan kwaliteit is niet gemeten.** BZ4 meet het tegen fp32, met een poort.
    - **Afgevallen op licentie** (citaten in B4): InsightFace (alleen niet-commercieel onderzoek),
      MobileCLIP (Apple AMLR, "exclusively for Research Purposes"), nllb-clip (cc-by-nc-4.0),
      clip-ViT-B-32-multilingual-v1 (het beeldmodel is OpenAI CLIP: "Any deployed use case of the
      model - whether commercial or not - is currently out of scope"), en de ONNX-exports van Immich
      (geen licentietag; alleen fp32).
    - **Wel mogelijk, niet gekozen:**

      | Model | Nederlands | Beeld, int8, 1 core | Waarom niet |
      |---|---|---|---|
      | SigLIP 2 B/16 | 75,33 | 216,7 ms [B6] | ±3× duurder: +4,2 core-uur per 100.000 foto's (Vraag 10c) |
      | SigLIP i18n-256 | 72,06 | niet bekeken | niet beter, en patch 16 is duurder |
      | XLM-R B/32 laion5B (MIT) | 70,81 | niet bepaald | zwakker; de tokenizer (unigram) is niet nagebouwd |

    - **Wat Jonas verliest met B/32 tegen B/16:** ±2,6 punten recall in het Nederlands.
    - **Wat het model niet kan:** het beschrijft wat er te zien is, niet van wie het is. "Mijn auto
      in de sneeuw" vindt auto's in de sneeuw. Tussen je eigen foto's is dat meestal de jouwe.

73. **Het model komt op de LXC tot stand: downloaden en omzetten met eigen code** (Vraag 10;
    herzien in r3: K1-05, K1-06, K1-12, K2-12).
    - **Waarom.** Er is geen int8-ONNX van SigLIP 2 om te downloaden [B4]. De exports van Immich
      hebben geen licentietag. `onnxruntime.quantization` vraagt het pakket `onnx`
      (`ModuleNotFoundError`) [gemeten, B4], en de officiële export vraagt torch en transformers.
      Dat zijn drie nieuwe pakketten. Die nemen we niet zelf aan.
    - **Downloaden.** `install.sh` haalt op de host (die heeft internet) vijf bestanden van
      huggingface.co, vast op één commit van `google/siglip2-base-patch32-256`, elk met sha256 en
      grootte in `models.lock`: `model.safetensors` (1,51 GB), `tokenizer.json` (34,4 MB),
      `config.json`, `preprocessor_config.json` en `tokenizer_config.json` [doc, B4]. De commit en
      de sha256's zijn nog niet bekend: huggingface.co is vanuit de sandbox dicht. BZ1 en BZ2 zetten
      ze vast [niet getest]. Zonder vaste sha256 downloadt `install.sh` niets (K1-12).
    - **Omzetten in een eigen dienst** (K1-05). Niet met `compose run ... analyse`: dan krijgt de
      omzetter de sleutel en de socketmap van de dienst `analyse` [gemeten: K1-05]. Daarom een eigen
      dienst `analyse-omzetten` in `compose.analyse.yaml` (profiel `omzetten`), met een eigen uid
      10004, zonder `PLATFORM_ANALYSE_WORKER_KEY`, zonder `analyse-run` en zonder `/models`. Hij
      krijgt `network_mode: none`, `read_only`, `cap_drop: [ALL]` en `no-new-privileges`, de bron
      alleen-lezen en een lege uitvoermap: `python -I -m app.analyse.worker.omzetten.siglip --bron
      /bron --uit /uit`.
      - De kop van safetensors is JSON met offsets: die leest de standaardbibliotheek. De gewichten
        komen per tensor via `numpy.memmap`, zodat nooit het hele bestand in het geheugen staat.
      - De graaf schrijft de eigen ONNX-schrijver `app/analyse/onnxw.py` (uit de spike: alleen de
        standaardbibliotheek, IR 8, opset 17). De spike bouwde er al dezelfde vormen mee: 12 lagen,
        breedte 768, MLP 3072, 12 koppen, MAP-kop, en de tekstkant met 64 tokens [gemeten, B6].
      - De betekenis van elke laag (soort GELU, `layer_norm_eps`, welk token de tekst samenvat)
        komt uit `config.json` en de namen in safetensors, niet uit ons geheugen.
      - int8 met numpy, in de vorm van de proxy van B6: `DynamicQuantizeMatMul` met een schaal per
        kolom, en de embedding in int8 met een schaal per rij.
    - **Uitkomst** in `MODELLEN_ROOT/siglip2-b32-256/`: `beeld.int8.onnx`, `tekst.int8.onnx`,
      `tokenizer.bin` (Keuze 74), `meta.json` (de `logit_scale` en `logit_bias` van het model, en de
      voorbewerking uit `preprocessor_config.json`) en de licentie. Root kopieert ze uit de
      uitvoermap (Keuze 84). Daarna gaat de bron weg (1,55 GB).
    - **Altijd dezelfde uitkomst.** Twee keer bouwen gaf dezelfde sha256 voor de nepmodellen
      [gemeten, B2]. numpy rekent per element volgens IEEE, dus op elke x86-CPU hetzelfde [niet
      getest; BZ2 bouwt twee keer, op twee runners]. De sha256 van de uitkomst staat dan in
      `models.lock`, en `install.sh` vergelijkt.
    - **Als CI de bron niet kan halen** (BZ1): dan is er nog geen sha256 van de uitkomst. Dan geldt
      trust-on-first-use (K1-12): root schrijft de berekende sha256 na de eerste omzetting in een
      bestand van root (`MODELLEN_ROOT/siglip2-b32-256.sha256`), de worker weigert daarna elke
      wijziging, en `/beheer` zegt "Het model is niet nagerekend in CI".
    - **Waarom niet zelf een omgezette kopie hosten.** Dan verspreiden wij de gewichten (Apache-2.0
      staat dat toe, met de licentie en een NOTICE), en hangt `install.sh` af van een release van
      onze repo. Omzetten op de LXC kost 1,55 GB downloaden en een paar minuten rekenen [niet
      getest; BZ2 meet tijd en geheugen]. Het andere antwoord staat in Vraag 10b.
    - **Een harde go/no-go** (K2-12). De omzetter is het grootste technische risico van X3. Na de
      4 dagen van X3a moet hij drie dingen halen: de kleine vorm rekent als een eigen numpy-versie
      (Keuze 83), twee keer omzetten geeft dezelfde sha256 (BZ2), en de kwaliteitspoort is groen
      (BZ4). Lukt dat niet, dan gaat X3 naar een fase 6b (Vraag 13).
    - **Wat zonder torch niet bewezen is:** of onze graaf getal voor getal rekent als het
      origineel. Grove fouten vangt de kwaliteitspoort (BZ4). Met ja op Vraag 12b vergelijkt de job
      `beeld-model` ook met de officiële implementatie, alleen in CI en nooit in het image.

74. **De tokenizer: eigen code, alleen de standaardbibliotheek** (`app/analyse/tokenizer.py`).
    - SentencePiece-BPE met byte fallback, zoals `b5_bpe.py` van de spike: een spatie wordt `▁`,
      een teken dat niet in de woordenlijst staat wordt `<0xXX>`, en samenvoegen gaat op rang, bij
      gelijke rang de meest linkse (een heap over een gelinkte lijst).
    - Voor SigLIP 2: kleine letters per teken, afkappen op 63, `<eos>` = 1, `<pad>` = 0 tot 64
      tokens [doc: `tokenizer_config.json` en de documentatie van transformers, B5]. Kleine letters
      per teken en niet met `str.lower()` op de hele tekst: dat gaf 1 verschil (de Griekse
      slot-sigma) [gemeten, B5].
    - Het algoritme is in de sandbox bewezen tegen de Rust-bibliotheek `tokenizers` 0.23.3: 0
      verschillen op 2.181 zinnen, met een ander bestand van dezelfde soort (het tokenizer.json van
      Gemma, van npm, Apache-2.0 volgens npm) [gemeten, B5]. Dat bestand komt nooit in het repo,
      in CI of in de documenten (K1-14, K2-14). Of het bestand van SigLIP 2 (34,4 MB) net zo werkt,
      bewijst BZ3: met het bestand van SigLIP 2 zelf, een vaste lijst verwachte tokens in het repo,
      en met ja op Vraag 12b ook tegen `tokenizers`.
    - JSON laden kostte 1,35 s en 245 MB maxrss [gemeten, B5]. Het omzetten (Keuze 73) schrijft
      daarom `tokenizer.bin`: de stukken als één blok UTF-8 met offsets, en de merges als paren
      van ids met hun rang. Doel: laden ≤ 0,5 s en ≤ 100 MB [niet gemeten; BZ7].
    - De tokenizer draait in de container, naast het tekstmodel. De app laadt hem nooit. Hij heeft
      geen numpy nodig [B5], dus zijn tests draaien in de gewone job `api`.
    - Encode kost 0,11 ms (mediaan) [gemeten, B5]. Voorbeeld: "hond op het strand" wordt
      `h | ond | ▁op | ▁het | ▁strand | <eos>`.

75. **Rekenen in de container `analyse`, niet in de app** (herzien in r3: K1-04, K1-07, K1-08,
    K2-02, K2-06, K2-23).
    - **Waarom niet in de app.**
      1. De app importeert numpy en onnxruntime nooit. In het image kan uid 10001 ze ook niet
         vinden: ze staan in `/opt/analyse` (Keuze 57).
      2. Zoeken zonder numpy kost 5,3–10,7 s per vraag over 100.000 vectoren [berekend uit B6].
      3. Een beeld decoderen hoort in de zandbak (P8).
      4. ±1 GB aan modellen en vectoren hoort niet in het ene proces met de event loop
         (`serve.py`).
    - **Waarom niet in de mediaworker.** Daar zijn numpy en onnxruntime niet te vinden, en zijn
      CPU-budget is voor transcodes (B43).
    - **Dezelfde container en dezelfde grenzen als X2** (Keuze 56): uid 10003, `network_mode:
      none`, `read_only`, `cpus: 1.0`, `mem_limit: 3g`, `cpu_shares: 256`, de handdruk met
      `ANALYSE_WORKER_KEY` en het label `platform-analyse-worker`. Beelden en vectoren gaan als fd
      mee (`SCM_RIGHTS`). De container ziet `/data` niet.
    - **Opdrachten.** Ze staan in de `Op` van `app/analyse/wire.py`, die A0 met deze namen maakt:

      | Op | Kind | In | Uit |
      |---|---|---|---|
      | `embed_image` | `werk` | ≤ 16 previews als fd | per stuk 768 × float16 (1.536 B) in een memfd, of een code (`image_bad`) |
      | `embed_text` | `zoeken` | tekst (≤ 200 tekens) | 768 × float16 in een memfd; voor de kwaliteitsmeting en de tests |
      | `rank` | `zoeken` | fd van het vectorbestand, de eigenaar, zijn `gen`, en een tekst óf een `num` (voor "Meer zoals deze"), `k` ≤ 1.000 | `[[num, s], …]`, beste eerst; `s` is de cosinus × 10.000 (blijft in de app). Met `k = 0` alleen het bestand in het geheugen zetten (opwarmen). |
      | `forget` | `zoeken` | de eigenaar | haalt zijn vectoren uit het geheugen (bij "uit", K1-08) |
      | `cancel` | pid 1 | het nummer van een opdracht | breekt de opdracht af (K2-02) |
      | `caps` | pid 1 | — | per modelbestand `ok`, `ontbreekt` of `anders`; en `beeld: {model, dim, scale, bias}` |

      Een vector gaat nooit in een bericht. `rank` met een `num` zoekt de vector zelf op in het
      bestand van de eigenaar (K1-08).
    - **Een vraag gaat voor achtergrondwerk.** Het kind `zoeken` (nice 0) is een ander kind dan
      `werk` (nice 19), met elk een eigen `busy` (Keuze 56). Een beeldvraag krijgt dus nooit `busy`
      door een groepsronde. [niet getest: BZ6 meet een vraag terwijl `werk` vol draait.]
    - Elk model op één thread (`intra_op_num_threads=1`, `OPENBLAS_NUM_THREADS=1`), altijd
      `CPUExecutionProvider`. Met 3 threads kost een vraag ±57 ms, maar de container heeft één core
      [gemeten, B6].
    - **Geheugen** (K2-06). pid 1 ±40 MB. `zoeken`: de import 53 MB, het tekstmodel +340 MB, de
      tokenizer ≤ 100 MB [doel, BZ7], de cache 400 MB, en bij het herladen van een bestand van
      100.000 foto's tijdelijk +307 MB. `werk`: gezichten 224 MB, beeldmodel +165 MB, groeperen van
      60.000 gezichten +232 MB [gemeten: B6, `groep2_60000.json`; samen: K2-06]. Alles tegelijk is
      ±1,9 GB. Daarom `mem_limit: 3g`, met `RLIMIT_DATA` per kind (Keuze 56). BZ5 meet het met een
      groepsronde en een herlaadactie tegelijk.
    - **De cache.** De vectoren in het geheugen hebben een eigen grens (`ANALYSE_BEELD_CACHE_MB` =
      400, ±130.000 foto's); de eigenaar die het langst niet zocht, gaat eruit. De sleutel is
      (eigenaar, `gen`, sha256 van het bestand), niet alleen `gen` (K2-23). Heeft één eigenaar meer
      foto's dan de grens, dan geeft `rank` de code `too_big`, en valt de app terug op gewoon zoeken
      (R43).

### Zoeken op beeld (X3): opslag en achtergrondwerk

76. **Een vector per inhoud in SQLite, en een bestand per eigenaar als cache** (migratie 0012).
    - **Per blob, niet per node.** Een beeldvector komt alleen uit de pixels, zoals de pHash
      (Keuze 35) en de previews. Er zit geen plek of persoon in die de eigenaar koos (Keuze 25a), en
      een kopie kost geen tweede berekening. Wie hem mag gebruiken, regelt het zoeken (Keuze 79),
      niet de opslag.
    - **`blob_embed`**: `id` INTEGER PRIMARY KEY, `blob_sha` UNIQUE (FK `blob` CASCADE), `model`
      TEXT, `version` INT, `vec` BLOB (768 × float16, L2-genormaliseerd: 1.536 B), `updated_at`.
      Een gewone rowid-tabel: met de sha als TEXT-sleutel in een WITHOUT ROWID-tabel werd f32
      460,8 MB in plaats van 410,6 MB [gemeten, B7].
    - **Ruimte:** 205,3 MB per 100.000 foto's (2.053 B per rij); alles lezen 0,25–0,35 s
      [gemeten, B7]. Voor de 55.000 foto's van het zaad ±113 MB [berekend]. float16 omdat zoeken in
      f32 daarna recall 1,0 geeft [gemeten, B6]. int8 is 82 MB, maar recall 0,96 en 113 ms per scan
      [gemeten, B6, B7].
    - **Schrijven alleen via `write_embed()`** (zoals `write_text()`, Keuze 3): eerst een
      `SELECT`, dan `INSERT` of `UPDATE`, één korte transactie per blob. In dezelfde transactie gaat
      `beeld_owner.gen` +1 voor elke eigenaar van een bestand met die sha.
    - **Het bestand per eigenaar**: `/data/cache/beeld/<user_id>.vec` (bij Jonas
      `/opt/platform/data/platform/cache/beeld/`). Een kop (magic, model, dim, aantal, `gen`), dan de `num`'s
      (int64), dan de vectoren (f16) als één matrix. Voor 55.000 foto's ±85 MB [berekend]. Het is
      een cache: is hij weg, dan maakt de job hem opnieuw. Hij gaat niet mee in de reservekopie.
    - **Wat erin staat:** alle bestanden van die eigenaar met `ftype = 'image'` en een vector, ook
      die in de prullenbak, behalve wat Keuze 69 uitsluit. De vraag filtert met `own_live` (Keuze
      78). Zo hoeven prullenbak en terugzetten het bestand niet te raken.
    - **De vraag die het bestand maakt**, per 2.000 gelezen, naar een tijdelijk bestand en dan
      `os.replace`:

      ```sql
      SELECT n.num, e.vec FROM node AS n
      JOIN blob_embed AS e ON e.blob_sha = n.blob_id          -- unieke index op blob_sha
      WHERE n.owner_id = :me AND n.kind = 'file' AND n.ftype = 'image'   -- ix_node_owner_taken
        AND e.version = :v
      ORDER BY n.num
      ```

    - **`beeld_owner`** (`user_id` PK, FK `user` CASCADE; `gen`, `built_gen`, `built_rows`,
      `built_at`). `gen` gaat omhoog bij elke wijziging die het bestand raakt. De job `beeld.vec`
      (rij `index`, die altijd draait) maakt het bestand opnieuw als `gen > built_gen`: hooguit eens
      per 5 minuten per eigenaar, en al na 10 s als die eigenaar net zocht en het bestand ouder is
      dan 30 s. Zo schrijft een backfill van ±1,1–1,2 uur [K2-04] het bestand ±13–14 keer, niet bij
      elke batch.
    - **Triggers in 0012** (in `app/search/schema.py`, in dezelfde `TRIGGERS` als 0010 en 0011;
      K2-17): een nieuw beeldbestand, een andere eigenaar of inhoud, en definitief wissen (K1-08)
      zetten `gen` +1. Alleen een gewone
      `INSERT … SELECT … WHERE NOT EXISTS` en een `UPDATE`, geen upsert: zo hoeft de wachter van
      Keuze 3 niets uit te zonderen.

      ```sql
      -- BUMP(u): de rij van u erbij als hij er nog niet is, dan gen + 1
      INSERT INTO beeld_owner (user_id, gen) SELECT u, 0
        WHERE NOT EXISTS (SELECT 1 FROM beeld_owner WHERE user_id = u);
      UPDATE beeld_owner SET gen = gen + 1 WHERE user_id = u;

      CREATE TRIGGER node_ai_beeld AFTER INSERT ON node
      WHEN NEW.kind = 'file' AND NEW.ftype = 'image' BEGIN
        BUMP(NEW.owner_id)
      END;
      CREATE TRIGGER node_au_beeld AFTER UPDATE OF owner_id, blob_id ON node
      WHEN NEW.kind = 'file' AND NEW.ftype = 'image'
       AND (OLD.owner_id IS NOT NEW.owner_id OR OLD.blob_id IS NOT NEW.blob_id) BEGIN
        BUMP(OLD.owner_id)
        BUMP(NEW.owner_id)
      END;
      -- definitief wissen: het bestand per eigenaar zonder die foto (K1-08). BUMP alleen als de
      -- gebruiker nog bestaat; BZ8 test het ook bij het wissen van een gebruiker.
      CREATE TRIGGER node_ad_beeld AFTER DELETE ON node
      WHEN OLD.kind = 'file' AND OLD.ftype = 'image' BEGIN
        BUMP(OLD.owner_id)
      END;
      ```

      `NEW.ftype` is de virtuele kolom uit 0010. Of een trigger hem in `WHEN` leest op 3.46.1, is
      [niet getest]; BZ8 test het, en anders komt de `CASE` op `mime` uit `schema.py` erin. Per
      upload van een foto kost dit twee kleine schrijfacties; BZ8 meet een import van 500.
    - **`photo_pref.beeld`** TEXT NULL. `NULL` betekent: de standaard uit `PHOTO_PREFS` (Vraag 11);
      anders `aan` of `uit`. Zo verandert een ander antwoord op Vraag 11 alleen de code, niet de
      tabel.
    - **Downgrade naar 0011:** eerst de triggers, dan de twee tabellen en de kolom (`DROP COLUMN`,
      geen kopie). `db-downgrade` zegt: "De beeldindex gaat weg. Je foto's blijven." De cachemap
      blijft staan; het runbook zegt hoe je hem wist.
    - **Op PostgreSQL** (het latere cluster) maakt 0012 alleen de tabellen. Beeld staat daar uit,
      zoals FTS (Keuze 10).

77. **Achtergrondwerk: de stap `embed`, uit de preview van 256, om de beurt per eigenaar**
    (herzien in r3: K1-02, K2-04, K2-07).
    - `Step.EMBED = "embed"` in `analysis` (per blob; past in `String(8)`), in de rij `analyse`.
      `Defer` zoals Keuze 50 (≥ 2 transcodes, of OnlyOffice start), en `Defer(300)` als de container
      weg is. Een stap per blob past hier, anders dan bij de gezichten van X2: de toestemming hangt
      niet aan één node.
    - **Wanneer.** Na `preview.ready` (`previews.register_ready`, Z0), bij de start
      (`search.hooks.register_backfill`, per 500), als een gebruiker beeld aanzet, en na een nieuwe
      `VERSIONS[Step.EMBED]`. Alleen als de beheerder beeld aan heeft, er een levend beeldbestand is
      van een eigenaar met beeld aan dat hij zelf uploadde of dat een ander met beeld aan uploadde
      (Keuze 69), en er een preview is. `owner_id` in `analysis` is die eigenaar (Keuze 20).
    - **Pas na de index** (K2-07). De backfill van beeld begint pas als de backfill van de index
      klaar is, net als die van gezichten (Keuze 65).
    - **De invoer:** `256.webp`, als fd. Nooit de ruwe blob (P8). Het model wil toch 256 × 256, en
      `256.webp` decoderen kost 2,4 ms, tegen 20,8–23,0 ms voor `1024.webp` plus 8,8 ms schalen
      [gemeten: K2-04]. BZ4 meet of de kwaliteit gelijk blijft tegen `1024.webp`. Zakt precisie@5
      meer dan 0,02, dan rekent X3 uit `1024.webp`, in één opdracht met de gezichten, zodat er één
      keer gedecodeerd wordt. Zonder preview: `none` met code `no_preview`, en weer `wait` bij de
      volgende `preview.ready`.
    - **In de container** (kind `werk`): Pillow decodeert alleen WebP, met een grens op de pixels;
      de voorbewerking volgt `meta.json` (vierkant, zonder bijsnijden); dan het beeldmodel in int8,
      L2-normaliseren en float16 terug.
    - **Batches** van ≤ 16, om de beurt per eigenaar (elke eigenaar ≤ 4 plekken, Keuze 20). In de
      rij `analyse` om en om met de gezichten. Elke blob een eigen korte transactie.
    - **Kosten.** Per foto 69–80 ms op één core, uit `256.webp` [berekend: K2-04].

      | Foto's | Alleen beeld, 1 core (`cpus: 1.0`) | Met gezichten (X2) aan |
      |---|---|---|
      | 55.000 (het zaad) | ±1,1–1,2 uur | ±3,3–3,7 uur |
      | 100.000 | ±1,9–2,2 uur | ±5,8–6,8 uur |

      [berekend: 69–80 ms per foto; gezichten 2,2–2,5 en 3,9–4,6 uur, K2-04.] Op 4 cores zou het
      een kwart zijn, maar de container heeft `cpus: 1.0`, om media en OnlyOffice te beschermen
      (B43). Zonder VNNI op de CPU van Jonas kan int8 duidelijk trager zijn (R42).
    - **Poort:** ≥ 500 foto's per minuut per core met het echte model (BZ6). Verwachting 750–870
      [berekend: K2-04].
    - **Voortgang:** `/search?mode=image` geeft `indexing: {"done": …}` over je eigen foto's, en
      `search.progress` krijgt `what: "beeld"` voor beheerders.
    - **Een nieuw model of een nieuwe omzetter:** `VERSIONS[Step.EMBED]` + 1 zet alles op `wait`.
      Tot het klaar is, zoekt beeld over wat al opnieuw berekend is, met `indexing`.

### Zoeken op beeld (X3): zoeken en toegang

78. **Eén zoekveld, een eigen stand, zoeken op Enter.**
    - **Waar.** Op `/zoeken` een keuze tussen "Namen en tekst" (standaard) en "Wat er op de foto
      staat" (`mode=image` in de URL). In de suggesties van het veld in de bovenbalk een laatste
      regel "Zoek 'strand' op wat er op je foto's staat". In de viewer "Meer zoals deze"
      (`like=<node>`). Op `/fotos` een knop "Zoeken op beeld". Enter in de bovenbalk blijft gewoon
      zoeken.
    - **Het verloop van een vraag:**

      ```
      GET /api/v1/search?mode=image&q=strand      (Enter op /zoeken, stand "Wat er op de foto staat")
        0. beeld aan bij de beheerder en bij jou?           nee → 409 beeld_off
        1. grenzen: 1 per gebruiker (een nieuwe vraag stopt de oude), ≤ 2 per seconde;
           1 tegelijk voor de hele app, met een rij van 10      daarboven → 429 search_busy
        2. in de cache (gebruiker, vraag of like, gen, filters; 60 s)?   ja → naar 5
        3. fd van /data/cache/beeld/<jij>.vec, met gen in de kop                       (≤ 1 ms)
        4. container analyse, opdracht rank, met een eigen klok van 800 ms:
             tokenizer (0,1 ms) → tekstmodel int8 (62–74 ms) → cosinus met 55.000 × 768 f32
             (±20–28 ms) → top 1.000 (num, s)
           te laat, container weg of geen model → gewoon zoeken (Keuze 9), met fallback
        5. SQL: num in de top, own_live, ftype = 'image', de filters; volgorde van rank   (±5 ms)
        6. de pagina (offset, limit); tweede controle met access.resolve (P1, Keuze 15)
        7. SearchPage: mode "image", match "image", geen snippet, geen score
      ```

    - **De SQL van stap 5** (de top als één JSON-parameter, zonder tijdelijke tabel: Keuze 14):

      ```sql
      SELECT n.id FROM json_each(:nums) AS r
      CROSS JOIN node AS n ON n.num = r.value                   -- ix_node_num
      WHERE n.owner_id = :me AND n.dead = 0 AND n.kind = 'file'  -- OWN_LIVE_SQL
        AND n.ftype = 'image'
        AND <filters uit het register (Keuze 43)>
      ORDER BY r.key
      ```

      Met de klok van Z1 (`deadline()`, 40 ms) en een plan-test (`assert_plan`: begint bij
      `json_each`, gebruikt `ix_node_num`).
    - **De klok.** `deadline()` van Z0 stopt alleen SQLite (`deadline.py`), en `admit()` telt geen
      werk in een container (`limits.py`). De tocht naar de container krijgt daarom een eigen klok:
      `anyio.fail_after(PLATFORM_BEELD_DEADLINE_MS)`, standaard 800 ms. Is hij op, of is de
      container weg, of ontbreekt het model, dan geeft de app de treffers van gewoon zoeken (de
      rondes van Keuze 9) met `fallback: "slow" | "down" | "no_model"`. Zo is er nooit een lege
      pagina door de klok. De container maakt het opwarmen wel af; de volgende vraag is dan snel.
    - **Een filter dat weinig overlaat.** De container geeft de beste 1.000; de filters werken
      daarna. Een filter dat weinig foto's overlaat, kan zo minder treffers geven dan er zijn. De
      web zegt dan: "Misschien zijn er meer. Maak de vraag ruimer of haal een filter weg." Een lijst
      toegestane foto's meesturen kost een extra vraag per keer; dat is niet gemeten en niet nodig
      voor het gewone gebruik.
    - **Pagina's.** `offset` 0–1.000 en `limit` 1–50, zoals Keuze 7. De gerangschikte lijst blijft
      60 s in het geheugen van de app (hooguit 200 lijsten), zodat "Meer laden" de container niet
      opnieuw vraagt.
    - **Een drempel.** SigLIP geeft per paar `sigmoid(scale · cos + bias)`. Onder een vaste grens
      (`MIN_TEKST` voor een vraag in woorden, `MIN_FOTO` voor "Meer zoals deze") toont de app niets.
      BZ4 stelt die grenzen vast op de set van Keuze 83, en een test houdt ze vast.
    - **Tijden** [berekend uit B6]. Warm, bij 55.000 eigen foto's, ±85 ms in de container
      (0,1 + 62–63 + ±20–25); de p95's opgeteld ±100 ms. Daar komen de socket, de SQL, JSON en HTTP
      bij [niet gemeten].

      | Wat | Poort | Soort |
      |---|---|---|
      | `GET /search?mode=image`, warm, niets anders actief | p95 ≤ 250 ms | hard (BZ6) |
      | hetzelfde bij 100.000 eigen foto's (in de container al ±125 ms: tekstmodel p95 ±74, scan p95 ±51 [K2-13]) | p95 ≤ 250 ms | hard (BZ6) |
      | hetzelfde terwijl `werk` vol draait | doel ≤ 300 ms | rapport |
      | de eerste vraag na een nieuw bestand: dat is al vooraf geladen (lezen en f16 → f32 kost ±300 ms per 100.000 [gemeten, B6]) | p95 ≤ 250 ms | hard (BZ6; K2-13) |
      | alles behalve het tekstmodel (nepmodel, 55.000 echte f16-vectoren) | p95 ≤ 100 ms via HTTP | hard (`api-analyse`) |

    - **Opwarmen.** Zodra je de stand "Wat er op de foto staat" kiest, vraagt de web
      `GET /photos/beeld`. Dat zet je bestand al in het geheugen van de container (`rank` met
      `k = 0`), hooguit eens per minuut per gebruiker. En na elk nieuw bestand stuurt de job
      `beeld.vec` meteen zelf een `rank` met `k = 0`, als die eigenaar de afgelopen 24 uur op beeld
      zocht (K2-13).
    - **Waarom niet de 200 ms van het stappenplan.** Die gaat over gewoon zoeken (Keuze 9) en blijft
      precies zo: zonder `mode=image` raakt X3 het zoekpad niet. Het tekstmodel alleen kost al
      62–74 ms op één core; met HTTP en de SQL is 200 ms niet zeker, zeker niet bij 100.000 foto's
      (K2-13). Daarom een eigen poort van 250 ms, en de terugval. Dat wijkt af van het stappenplan;
      daarom Vraag 14.

79. **Toegang: alleen eigen, levende foto's; geen score en geen vector.**
    - **Drie lagen.** De app geeft de container alleen de fd van jouw eigen bestand. De SQL van
      Keuze 78 eist `own_live`. Daarna gaat elke treffer nog door `access.resolve()`; wijkt die af,
      dan valt de treffer weg en komt er `search_access_mismatch` in het log, zoals bij Z1 (Keuze
      15).
    - **Bram en een kopie.** Bram vindt zijn eigen kopie van Eva's foto als hij beeld aan heeft: het
      is zijn bestand, en de vector van die inhoud bestaat al. Van Eva krijgt hij niets mee (geen
      plek, persoon of OCR-tekst). Eva's eigen foto's, ook als ze met Bram gedeeld zijn, vindt Bram
      niet via beeld.
    - **"Meer zoals deze"** (`like`): alleen vanuit een eigen, levende foto met een vector. De app
      stuurt de `num` van die foto naar de container, nooit de vector (K1-08). Een node
      van een ander, of een die je niet ziet: `404 not_found` (geen verschil met "bestaat niet").
      Een eigen foto zonder vector: `409 beeld_no_vector`.
    - **Publieke link:** geen zoekroute (Keuze 16). **Gast:** heeft geen eigen bestanden;
      `403 guest` zoals nu.
    - **Nooit in de API:** de vector, de score, het aantal vectoren per gebruiker. Een test loopt
      elk antwoord van elke route na op de sleutels `vec`, `embedding` en `score`, op lijsten van
      768 getallen, en op base64 van 1.536 bytes.
    - **Logs.** `QUERY_PATHS` (`main.py:106`) haalt de query string van `/api/v1/search` (ook
      `mode` en `like`) en `/api/v1/photos/beeld` al uit de toegangslog. De container logt nooit de
      tekst van een vraag, alleen tijden en aantallen. De cache van de app houdt de vraag 60 s in
      het geheugen, nooit op schijf. De stderr van een kind in de container gaat naar pid 1, die
      alleen een code en een lengte logt (Keuze 56, K1-16). Test met `geheimwoord7`: in de app, in
      de echte container (laag 2, ook met een geforceerde uitzondering), en in `docker logs` in de
      job `analyse-image`.

80. **De contracten: wat erbij komt, allemaal additief** (herzien in r3: K2-01, K2-17, K2-21). De
    vaste bestanden van Z0 (`types.py`, `analysis.py`, `prefs.py`, `schema.py`) krijgen alleen
    velden en waarden **met een standaard** erbij. Een oude client stuurt `mode` niet en krijgt
    precies wat hij nu krijgt; de nieuwe velden in het antwoord kan hij negeren (de web van nu leest
    alleen `results` en `more`). Geen test wordt zwakker: elke test die nu een exacte lijst eist,
    eist straks de nieuwe exacte lijst.

    | Bestand (eigenaar) | Toevoeging | Test die meegaat |
    |---|---|---|
    | `app/search/types.py` (Z0, daarna Z1) | `Match` + `"image"`. `SearchParams.mode: Literal["text", "image"] = "text"` en `SearchParams.like: UUID \| None = None`, **niet** in `SearchRule`: een regel van een slim album met `mode` geeft dan vanzelf 422 (`extra="forbid"`, `AlbumRuleIn.rule`). `SearchPage.mode: Literal["text", "image"] = "text"` (welke stand antwoordde) en `SearchPage.fallback: Literal["slow", "down", "no_model"] \| None = None`. `AnalysisCode` + `"no_preview"`, `"image_bad"`. Geen `FailedItem` voor beeld en gezichten: `AdminSearch.failed_by_code` geeft alleen aantallen per code (K2-21). `RebuildWhat` + `"beeld"`. `AdminSearch.beeld: AdminBeeld \| None = None`, `AdminSearchPatch.beeld: bool \| None = None`. `SearchProgressEvent.what` + `"beeld"`. Geen nieuw type gebeurtenis. | `test_contracts.py` (de nieuwe velden en hun standaard; een vraag zonder `mode` geeft hetzelfde antwoord als nu); `test_api_stubs.py` blijft groen (EVENTS gelijk) |
    | `app/analysis.py` (Z0, daarna Z2) | `Step.EMBED = "embed"`, `VERSIONS[Step.EMBED] = 1` | `test_contracts.py:208` eist `['text', 'hash', 'ocr', 'embed']` |
    | `app/prefs.py` (Z0, daarna Z6) | `PHOTO_PREFS["beeld"] = "uit"` (Vraag 11); `beeld` in `PhotoSettings` en `PhotoSettingsPatch`; `photo_prefs()` geeft bij `NULL` de standaard. De docstring zegt "Vraag 3" waar "Vraag 2" bedoeld is: mee rechtzetten. | `test_contracts.py:277–282` en `:300` eisen de vijf sleutels |
    | `app/search/schema.py` (Z0) | de triggers van 0011 en 0012 in dezelfde `TRIGGERS` (K2-17); de controle vergelijkt met 0010 + 0011 + 0012 | `test_schema_triggers.py`: de lijst van voorvoegsels krijgt `node_*_beeld`; de vergelijking wordt een uitbreiding, geen verzwakking |
    | `app/routers/search.py` (Z1) | vóór `select_engine`: `if p.mode == "image": return await beeld.search(...)` | de tests van Z1 blijven gelijk; `test_beeld_search.py` |
    | `tests/search/test_own_live.py` (Z0) | `"beeld"` in `PHOTO_FUNCTIONS`, zodat de wachter `app/beeld/` meeneemt | zelf |
    | `web/src/search/types.ts` (Z4) | `mode`, `like` in `SearchParams`; `MatchKind` + `"image"`; `SearchPage.mode`, `.fallback`; `ANALYSIS_CODES` + 2 | vitest; typen kosten 0 B in de eerste lading |
    | `web/src/admin/searchApi.ts` (Z7) | `AdminSearch.beeld` | vitest van de tab |
    | `web/src/photos/people/types.ts` (Z6) | `PhotoSettings.beeld` | vitest |
    | `web/scripts/budget.mjs` (Z0) | grens `beeld` ≤ 15 kB; `viewer/LikeRow` in `ZOEK_SLOTS` | de job `web` |
    | `web/src/i18n/types.ts` (Z0) | het gebied `beeld` | i18n streng |

    **Wie.** Z0b (Keuze 55), vóór de review van Z0. Dan bouwen Z1, Z4, Z6 en Z7 er meteen tegen.
    Nieuwe routes geven tot X3 er is `501 not_built("X3")` (`routers/stubs.py`), en
    `test_api_stubs.py` krijgt per route een rij in `ENDPOINTS`.

    **Nieuwe routes** (alle met inlog en 2FA; geen via een publieke link):

    | Methode en pad | Antwoord |
    |---|---|
    | `GET /search?mode=image&q=` of `&like=<node>` | `SearchPage` (hierboven). `q` 1–200 tekens óf `like`. Toegestaan: `type` (alleen `image`), `from`, `to`, `person`, `place` of `country`, `tag`, `camera`, `in`, `offset`, `limit`. Een ander type, `after`, of geen `q` en geen `like`: `400 search_bad_filter`. Uit: `409 beeld_off`. Te druk: `429 search_busy` met `Retry-After: 1`. |
    | `GET /photos/beeld` | `{"state": "ok" \| "bezig" \| "uit" \| "beheer_uit" \| "niet_geinstalleerd" \| "weg", "done": float \| null}`; warmt je bestand op (Keuze 78) |
    | `GET`, `PATCH /me/photo-settings` (Z6) | krijgt `"beeld": "aan" \| "uit"` |
    | `GET`, `PATCH /admin/search` (Z7) | krijgt het blok `beeld` (Keuze 82) en `{"beeld": bool}` |
    | `POST /admin/search/rebuild` (Z7) | `{"what": "beeld"}`: alles opnieuw berekenen |
    | `DELETE /admin/search/beeld` | `202`: de beeldindex wissen (Keuze 82) |

    **Foutcodes** in een eigen catalogus `web/src/i18n/errors/beeld.ts` (NL en EN;
    `test_error_catalog.py`): `beeld_off`, `beeld_no_vector`, `beeld_not_installed` (de beheerder
    zet aan zonder model). Bestaande codes: `search_bad_filter`, `search_busy`, `rate_limited`,
    `not_found`, `guest`. Codes in `analysis.code` (alleen op `/beheer`): `no_preview`, `image_bad`;
    `model_missing` en `analyse_missing` zijn van X2.

### Zoeken op beeld (X3): de web

81. **Waar je het ziet, de teksten en het toetsenbord.**
    - **De plekken.**

      | Plek | Wat | Eigenaar van het bestand | Kosten |
      |---|---|---|---|
      | `/zoeken` | de keuze "Namen en tekst" / "Wat er op de foto staat" (`role="radiogroup"`); in de stand beeld een raster van foto's in plaats van de lijst | Z4 (de keuze); X3 (het raster) | keuze ≤ 0,5 kB in `search`; het raster in een eigen lazy chunk `src/search/beeld/**` ≤ 15 kB |
      | suggesties in de bovenbalk | een laatste optie "Zoek '…' op wat er op je foto's staat" | Z4 | ≤ 0,3 kB in de chunk van de suggesties |
      | `/zoeken`, stand tekst, zonder foto's in de treffers | een regel "Geen foto met deze naam? Zoek op wat er op je foto's staat." | Z4 | in `search` |
      | viewer, infopaneel | "Meer zoals deze" (een eigen foto) | X3, nieuwe lazy plek `viewer/LikeRow.tsx` naast `PeopleRow` (de plek zet Z0 of X3 dag 1) | ≤ 3 kB, lazy |
      | `/fotos` | een knop "Zoeken op beeld" die `/zoeken?mode=image` opent, met de focus in het veld | Z6 | ≤ 0,2 kB |
      | Account → Foto's | de schakelaar "Zoeken op beeld" | Z6 (het blok), X3 (de regel) | in de chunk van Account |
      | `/beheer` → Zoeken en foto's | het blok "Zoeken op beeld" (Keuze 82) | Z7 (de tab), X3 (het blok) | in de chunk van `/beheer` |

      De eerste lading krijgt er niets bij (er was nog 2.698 B over). Het gebied `beeld` (NL en EN)
      laadt met het raster.
    - **De teksten** (Nederlands; het Engels is volledig, B25):

      | Waar | Tekst |
      |---|---|
      | keuze | "Namen en tekst" · "Wat er op de foto staat" |
      | veld, stand beeld | plaatshouder "Beschrijf de foto, bijvoorbeeld: hond op het strand"; eronder "Zoekt in je eigen foto's. Druk op Enter." |
      | raster | label "Foto's die passen bij 'strand'"; per foto de naam en de datum |
      | niets gevonden | "Geen foto's gevonden die hierbij passen." |
      | backfill bezig | "Je foto's worden nog bekeken: 43% klaar. Wat nog niet bekeken is, vind je nog niet." |
      | te weinig na filters | "Misschien zijn er meer. Maak de vraag ruimer of haal een filter weg." |
      | `fallback: "slow"` | "Zoeken op beeld duurde te lang. Dit zijn de treffers op naam en tekst." |
      | `fallback: "down"` | "Zoeken op beeld werkt nu niet. Dit zijn de treffers op naam en tekst." |
      | `fallback: "no_model"` | "Zoeken op beeld is nog niet klaar: het model ontbreekt. Dit zijn de treffers op naam en tekst." |
      | `beeld_off` (jij) | "Zoeken op beeld staat uit. Je zet het aan bij Account → Foto's." |
      | `beeld_off` (beheerder) | "Zoeken op beeld staat uit voor dit platform." |
      | `beeld_no_vector` | "Deze foto is nog niet bekeken. Probeer het straks opnieuw." |
      | viewer | "Meer zoals deze" |
      | suggestie | "Zoek 'strand' op wat er op je foto's staat" |
      | schakelaar | "Zoeken op beeld. Je foto's worden op de achtergrond bekeken, zodat je ze kunt vinden op wat erop staat ('strand', 'hond'). Dat gebeurt op deze server; er gaat niets naar buiten. Alleen jij kunt zo in je eigen foto's zoeken. Uit wist wat alleen voor jou bewaard is. Snapshots die de beheerder zelf van de server maakt, houden het tot ze verlopen." |

    - **Het toetsenbord** (geen nieuwe sneltoets; alles met Tab, pijlen, Enter en Esc; de lijst komt
      in `docs/fase-6/toetsenbord-x3.md`):

      | Waar | Toets | Wat |
      |---|---|---|
      | `/zoeken` | Tab | naar de keuze van de stand |
      | de keuze | ← en → | andere stand; de URL krijgt `mode=image` of niet |
      | het veld, stand beeld | Enter | zoeken (niet tijdens het typen) |
      | het raster | pijlen | naar de foto ernaast, erboven of eronder |
      | het raster | Home, End | de eerste of de laatste geladen foto |
      | het raster | Enter | open in de viewer; terug komt op dezelfde foto uit |
      | het raster | Esc | terug naar het veld |
      | suggesties in de bovenbalk | ↓ tot de laatste optie, Enter | zoeken op beeld |
      | viewer, infopaneel | Tab naar "Meer zoals deze", Enter | `/zoeken?mode=image&like=…` |

      De meldingen (backfill, terugval, te weinig) staan in een `role="status"`, zodat een
      schermlezer ze hoort. Het raster is een `role="grid"` met `aria-label`; elke foto heeft de
      naam en de datum als tekst.

### Zoeken op beeld (X3): beheer en privacy

82. **Aan, uit en wissen; `/beheer`** (herzien in r3: K1-08, K1-09, K2-21, K2-22).
    - **Per gebruiker** `photo_pref.beeld`, standaard **uit** (Vraag 11). Uit: meteen
      `409 beeld_off` (de route leest `photo_pref`). Eén job wist `<jij>.vec`, stuurt `forget` naar
      de container, wist de rijen van `blob_embed` die geen andere eigenaar met beeld aan nog nodig
      heeft, en zet die stappen in `analysis` op `skip`. Test: daarna 0 rijen voor de foto's die
      alleen van jou zijn, geen bestand, en de container heeft niets meer van jou in zijn
      geheugen. Weer aan: de backfill zet je foto's klaar.
    - **De beheerder** zet beeld voor het hele platform aan of uit in `setting` (sleutel `search`,
      die bestaat, dus geen migratie). Uit: elke route geeft `409 beeld_off`, de stap `embed` doet
      niet meer mee, en de container laadt de beeldmodellen niet. De vectoren blijven tot de knop
      **"Beeldindex wissen"** (`DELETE /admin/search/beeld`): die wist alle rijen van `blob_embed`,
      alle bestanden in de cache, en het geheugen van de container (test: 0 rijen, lege map).
    - **Waarom uit bij de beheerder niet vanzelf alles wist** (anders dan bij gezichten, Keuze 64):
      opnieuw berekenen kost ±1,9–2,2 core-uur per 100.000 foto's. De beheerder kiest. Per
      gebruiker wist uit wel: daar gaat het om de keuze van die persoon.
    - **Het blok op `/beheer`** (`AdminBeeld`):
      - "Zoeken op beeld: aan" (of uit), met de knoppen "Zet uit voor iedereen", "Opnieuw berekenen"
        en "Beeldindex wissen" (met bevestiging);
      - "Model: SigLIP 2 B/32-256 (Google, Apache-2.0), int8. In orde." Of: "ontbreekt; install.sh
        haalt het bij de volgende run", of "niet nagerekend in CI";
      - "Container analyse: draait" (of "draait niet");
      - "Bekeken: 41.230 van 55.012 foto's", en mislukt als aantal per code (K2-21);
      - "Het afgelopen uur: 12 vragen, p95 140 ms; terug naar gewoon zoeken: 0 keer te traag, 0 keer
        zonder container";
      - "Ruimte: 113 MB in de database, 85 MB in de cache, 400 MB aan modellen";
      - de uitkomst van `app.cli beeld-bench` en van `app.cli beeld-kwaliteit` (BZ4, BZ6), met
        datum;
      - de vaste zin: "De vectoren beschrijven wat er op een foto staat. Reservekopieën die het
        platform maakt, bevatten ze niet. Maak je zelf snapshots van /data (ZFS, restic), dan staan
        ze daar wel in, tot je die snapshot weggooit."
    - **Is een beeldvector biometrisch?** Dat is niet gemeten (K1-09).
      - Een gezichtsvector (SFace, X2) is gemaakt om **dezelfde persoon** terug te vinden, op elke
        foto, ook van mensen die zelf niets kozen. Daarom staat hij standaard uit, wist uit altijd
        alles, en kan de beheerder hem verbieden (Keuze 64).
      - Een SigLIP-vector is gemaakt om een **beschrijving in woorden** bij een hele foto te vinden.
        Maar "Meer zoals deze" op een portret vindt waarschijnlijk andere foto's van dezelfde
        persoon [niet getest]. Dan is het een zachte vorm van gezichtsherkenning. BZ10 meet het.
        Ligt de kans boven toeval, dan geeft `like` niets op een foto met een gevonden gezicht, en
        gelden voor beeld dezelfde regels als voor gezichten.
      - Een beeldvector is **niet anoniem**: met het model kun je vragen of er iets op staat
        ("naakt", een logo, een kind). Daarom: alleen de eigenaar zoekt, alleen in eigen foto's,
        nooit een vector of score in de API of het log, en weg met de blob, met "uit" en met
        "wissen".
      - Tot BZ10 gemeten is, staat beeld per gebruiker standaard uit (Vraag 11).
    - **Reservekopieën** (K1-08). De kopie van Keuze 52 wist `blob_embed` vóór hij zijn echte naam
      krijgt, net als de tabellen van gezichten. Het is een cache die opnieuw te berekenen is. Na
      het terugzetten van een kopie berekent de backfill de vectoren opnieuw.
    - **Definitief wissen.** De trigger `node_ad_beeld` zet `gen` +1 bij het wissen van een node,
      zodat het bestand per eigenaar opnieuw gemaakt wordt zonder die foto (K1-08).
    - **Een gebruiker wissen:** `beeld_owner` gaat weg via de cascade; het bestand in de cache ruimt
      het opruimwerk van `jobs.py` op (bestanden zonder rij in `beeld_owner`), en de container
      krijgt `forget`.

### Zoeken op beeld (X3): kwaliteit en uitrollen

83. **Kwaliteit meten zonder internet in de sandbox, met harde poorten** (herzien in r3: K1-09,
    K2-03, K2-20).
    - **Drie lagen, elk met zijn eigen bewijs.**
      1. **De keten**, overal, ook in de sandbox. Nepmodellen die de test zelf bouwt met
         `onnxw.py`, zonder numpy (B2): een "beeldmodel" dat kleuren naar een vector zet en een
         "tekstmodel" dat woorden als "rood" en "blauw" daarop laat lijken. Zo bewijst de test
         tokenizer → model → rank → SQL → API, met een vaste uitkomst. ORT en numpy gaven dezelfde
         uitkomst tot 6,3·10⁻⁸ [gemeten, B2].
      2. **De omzetter**, in `api-analyse`. Een kleine SigLIP-vorm met willekeurige gewichten
         (2 lagen, breedte 64, dezelfde namen als het echte safetensors), gemaakt in de test met de
         standaardbibliotheek. De test rekent dezelfde voorwaartse stap met een eigen,
         onafhankelijke numpy-versie en eist ≤ 1·10⁻⁵ verschil voor fp32, cosinus ≥ 0,99 voor int8,
         en twee keer dezelfde sha256.
      3. **De echte kwaliteit**, alleen waar huggingface.co en de foto's bereikbaar zijn: de
         CI-job `beeld-model` (die draait als de omzetter, de tokenizer of `models.lock` verandert;
         K2-03), of op de LXC met `app.cli beeld-kwaliteit`.
    - **De set** (`api/tests/analyse/sets/beeld.tsv`): per foto de URL, de sha256, de licentie
      (alleen publiek domein of CC0) en de categorie. 12 categorieën × 5 foto's (strand, hond, kat,
      sneeuw, auto, fiets, berg, bos, stad, eten, boot, zonsondergang) plus 60 andere foto's, samen
      120. Liefst zonder herkenbare mensen. Nooit in het repo of het image; alleen gedownload in de
      job, met sha256 (Vraag 12a). De vragen staan in `beeld_vragen.tsv`: 24 in het Nederlands met
      één begrip (twee per categorie), 6 combinaties met een vaste foto ("auto in de sneeuw", "hond
      op het strand"), en 6 in het Engels als controle. De sandbox komt niet bij Wikimedia of NASA
      [gemeten, B3]; X3 stelt de lijst samen via de job, die van kandidaten de sha256 en de
      licentie afdrukt, compact in het blok `=== kort ===` en in het verslag via `upload-artifact`
      (K2-20).
    - **De poorten.** Vastgelegd vóór de eerste echte run. Lager alleen met een nieuwe keuze in dit
      ontwerp, nooit stil in de test.

      | Wat | Poort |
      |---|---|
      | 24 vragen in het Nederlands met één begrip | gemiddelde precisie@5 ≥ 0,6, en geen vraag met 0 goede foto's in de top 5 |
      | 6 combinaties | de bedoelde foto in de top 10 bij ≥ 5 van de 6 |
      | int8 tegen fp32 (dezelfde set, dezelfde vragen) | precisie@5 zakt ≤ 0,05 |
      | "Meer zoals deze" | gemiddeld ≥ 0,7 van de top 4 uit dezelfde categorie |
      | "Meer zoals deze" op een portret (BZ10): hoe vaak staat dezelfde persoon in de top 5? | rapport; boven toeval geldt de regel van Keuze 82 |
      | twee keer omzetten | dezelfde sha256 |
      | de 6 vragen in het Engels | rapport |

    - **Haalt het model de poort niet,** dan in deze volgorde: de tekstkant in fp32 meten (past dat
      in het geheugen? BZ5), dan B/16 (Vraag 10c). Haalt niets de poort, dan levert X3 alleen
      "Meer zoals deze" (daar is alleen het beeldmodel voor nodig), en gaat er een kaart naar Jonas
      dat zoeken met woorden niet goed genoeg is.
    - **Een zelftest bij de start** van de container: drie beelden die Pillow tekent (een rood, een
      blauw en een groen vlak) en de vragen "rood", "blauw" en "groen". De goede moet bovenaan.
      Faalt het, dan is de toestand `model_fout` en zoekt beeld niet [niet getest of SigLIP dit goed
      doet; BZ4 controleert de zelftest met het echte model].

84. **Uitrollen, uitzetten en ruimte** (herzien in r3: K1-05, K1-06, K1-12, K1-13, K2-19, K2-24).
    - **`.env`:** `BEELD=aan` (aan | uit: het model voor zoeken op beeld downloaden en omzetten).
      `ANALYSE=uit` zet de hele container uit, en dus ook beeld.
    - **`install.sh`** (in de stap "Gezichten en beeld", met de gedeelde downloadhulp):
      1. `want_beeld`: `BEELD` uit `.env`, standaard aan; alleen als de container `analyse` er is.
      2. Staan de uitvoerbestanden er al met de juiste sha256: niets doen.
      3. Vrije ruimte: `df --output=avail "$MODELLEN_ROOT"` ≥ 2,5 GB voor de bron, de uitkomst en de
         marge, en `df --output=avail "$DATA_ROOT"` voor de vectoren en de cache. `free_gb` kijkt
         alleen naar de map van Docker, en is er voor het image (K2-19). Te weinig: een melding, en
         door.
      4. De vijf bronbestanden naar een map van `mktemp -d` onder `MODELLEN_ROOT` (van root, 0700),
         per URL `curl -fsSL --retry 3 --max-filesize <bytes>` naar een tijdelijk bestand, sha256
         vergelijken, dan `mv`. Een doel dat een symlink is, weigert het script (K1-06).
      5. Een lege uitvoermap van uid 10004 (0700). Dan `compose --profile omzetten run --rm
         --no-deps analyse-omzetten` met de bron alleen-lezen en de uitvoermap, met de grenzen van
         `compose.analyse-limits.yaml` als die meegaan. Geen netwerk, geen sleutel, geen socket
         (Keuze 73).
      6. Als root, per verwacht bestand: weigeren als het geen gewoon bestand is (`[ -L ]`,
         `[ ! -f ]`), de sha256 vergelijken met `models.lock` (of trust-on-first-use, Keuze 73),
         dan `install -m 0644 -o root -g root` naar een nieuwe map van root, en die map atomisch op
         zijn plek zetten in `MODELLEN_ROOT/siglip2-b32-256/`. Nooit `chmod` of `mv` op een bestand
         van uid 10004.
      7. De bron en de uitvoermap weg (1,55 GB).
      8. De licentieteksten naast het model (K1-13).
      9. De container `analyse` opnieuw starten als de sha256 van de modelmap veranderde.
      Nooit fataal: bij een fout zegt het script "Zoeken op beeld: het model is niet geladen. De
      volgende run probeert het opnieuw." en gaat door. Gewoon zoeken en de rest werken.
    - **Ruimte** (K2-24). De LXC van Jonas heeft één rootdisk van 500 GB (ZFS), zonder `/tank`.
      Het voorbeeld in `infra/single/README.md:11–12` noemt 64 GB en een optionele `/tank` van
      700 GB; ook daar past het.

      | Wat | Grootte | Waar |
      |---|---|---|
      | de bron, alleen tijdens de installatie | 1,55 GB [doc, B4] | `MODELLEN_ROOT` (rootdisk) |
      | het model in int8 | ±390 MB [gemeten op de proxy, B6], plus `tokenizer.bin` [niet gemeten] | `MODELLEN_ROOT/siglip2-b32-256/` |
      | de modellen van gezichten | 38,9 MB [gemeten: spike B4] | `MODELLEN_ROOT` |
      | vectoren van beeld in de database | 205 MB per 100.000 foto's [gemeten, B7] | `platform.db` (niet in de reservekopieën) |
      | vectoren van gezichten in de database | ±70–90 MB per 100.000 gezichten [B7 mat ±60 MB zonder UUID's en indexen; de rest is een schatting, zie X2] | `platform.db` (niet in de reservekopieën) |
      | de cache per eigenaar | ±154 MB per 100.000 foto's [berekend: K2-24] | `/data/cache/beeld/` |
      | numpy en onnxruntime in het image | +118–141 MB, gedeeld met X2 | het image (twee keer tijdens een update) |

      Samen ±0,9–1,1 GB bij 100.000 foto's, met de modellen (±0,43 GB) en het image al
      meegeteld; daarvan groeit ±0,45 GB mee met het aantal foto's [berekend: K2-24]. Dat past.
    - **Uitzetten:** `BEELD=uit` in `.env` en `install.sh` opnieuw: geen download. Of zonder
      herinstallatie op `/beheer`. De map met het model blijft staan; het runbook
      (`docs/runbooks/analyse.md`, met een deel beeld) zegt hoe je hem wist.
    - **Terug naar 0.7.x** gaat zoals §3.10: `db-downgrade` naar de head van die versie gaat ook
      langs 0012 en 0011.
      Het model en de cache blijven op schijf staan; het runbook zegt hoe je ze weghaalt.
    - **Bronvermelding** op `/over`: "Zoeken op beeld: SigLIP 2 van Google, Apache-2.0." Ook YuNet
      (MIT) en SFace (Apache-2.0) staan daar (K1-13).

### Vragen voor Jonas

**Beantwoord op 3 november 2026.** Jonas vroeg "ja voeg alles toe", en koos daarna op een kaartje
"Ook gezichten" (en dus geen tegels van buiten).

| Vraag (revisie 2) | Wat Jonas koos |
|---|---|
| 1. Mag tesseract in het image, voor OCR? | Ja ("alles"). X1 is een gewoon pakket (Keuze 24). De vraag noemde ±20 MB; S5 mat 69 MiB, daarom Vraag 15 (ook ja). |
| 2. Mogen gezichten automatisch gevonden worden? | Ja, in fase 6 (het kaartje "Ook gezichten"). X2, Keuze 56–69. |
| 3. Zoeken op wat er op een foto staat? | Ja, in fase 6 ("alles"). X3, Keuze 70–84. Het kaartje noemde beeld niet apart; daarom Vraag 11. |
| 5. GeoNames en Natural Earth bij de installatie downloaden? | Geen eigen antwoord: het kaartje is ingetrokken, omdat "alles" het dekt. De standaard geldt: ja, met sha256, niet fataal, uit te zetten met `PLAATSEN=uit`. |
| 6. Wat ligt er onder de kaart? | Alleen landsgrenzen, geen tegels van buiten (het kaartje). De CSP blijft gelijk. |
| 7. Mag fase 6 langer duren? | Ja, met alles. Met gezichten en beeld is het ±43 werkdagen (§5). Hoe we opleveren, is Vraag 13. |

**Nog open.** Elke vraag heeft een standaard. Niemand hoeft op een antwoord te wachten.

4. **Wie ziet de plek van een foto?** (niet beantwoord; ongewijzigd sinds revisie 2)
   Standaard: **alleen de eigenaar.** Nooit via een publieke link. Het deeldialoog waarschuwt dat
   een download de locatie in het bestand meeneemt; de bytes blijven gelijk. Andere antwoorden:
   (b) GPS uit downloads halen voor iedereen behalve de eigenaar (de bewerking van fase 10 naar
   voren, ±2 dagen);
   (c) "wie ik deel, ziet de plek ook" als keuze per gebruiker.
8. **Mogen echte gezichten van NASA in CI gebruikt worden om de herkenning te meten?** (nieuw in
   r3, herschreven na K1-10)
   Wat er dan gebeurt: een CI-job op de runners van GitHub (in de VS) downloadt ±40 foto's van
   echte mensen, maakt van elk gezicht een vector, en koppelt die aan hun naam ("van links naar
   rechts" uit de beschrijving van NASA). Dat is biometrie van echte mensen, bij naam, buiten je
   huis. Of dat mag onder de AVG (art. 9), is onzeker; dat is niet door een jurist nagekeken.
   Standaard: **nee.** Dan meten we de kwaliteit op je eigen LXC: `app.cli analyse-kwaliteit`
   rekent de precisie van de voorstellen uit tegen jouw eigen "ja" en "nee", en geeft alleen
   getallen. Niets verlaat de LXC. In CI draaien alleen de synthetische lagen (X2, "Tests").
   Met ja: de set alleen in CI, met sha256, nooit in het repo, het image of een artefact, en
   zonder namen in de log.
9. **Worden foto's gescand die een ander in jouw map zette?** (nieuw in r3, K1-02)
   Het gaat om uploads van een ander in een gedeelde map, uploads via een publieke uploadlink, en
   kopieën die een ander naar jouw map maakt. Die worden van jou.
   Standaard: **niet scannen, tenzij die ander het zelf aan heeft.** Een upload via een link
   nooit. Bestanden van vóór de update met gezichten en beeld tellen als door jou geüpload: wie
   ze echt uploadde, weet het platform niet. Geldt voor gezichten en voor beeld (Keuze 69).
   Anders: (b) alles in jouw map scannen onder jouw keuze; (c) nooit iets scannen wat je niet zelf
   uploadde.
10. **Hoe komt het model voor zoeken op beeld op je LXC?** (nieuw in r3; was Vraag 20 in het
    ontwerp van X3)
    Standaard: **(a) downloaden en omzetten op de LXC.** `install.sh` haalt de originele gewichten
    van Google (Apache-2.0, 1,55 GB) van huggingface.co en zet ze één keer om met eigen code, in
    een eigen container zonder netwerk en zonder sleutel (Keuze 73). Er komt geen nieuw pakket bij,
    en wij verspreiden zelf geen gewichten.
    Andere antwoorden:
    (b) wij zetten het model om in een CI-job en zetten het als bestand bij een release van onze
        repo (±400 MB). Dan download je minder, maar verspreiden wij de gewichten, met hun licentie;
    (c) het grotere model B/16: ±2,6 punten beter in het Nederlands (75,33 tegen 72,72), maar elke
        foto bekijken kost ±3× zo veel (+4,2 core-uur per 100.000 foto's) [B6].
11. **Staat zoeken op beeld standaard aan voor elke gebruiker?** (nieuw in r3, K1-09, K2-22)
    Standaard: **uit**, tot BZ10 gemeten heeft of "Meer zoals deze" op een portret dezelfde
    persoon terugvindt. Wie het wil, zet het aan bij Account → Foto's. Dan rekent de server ook
    alleen voor wie het wil: de backfill kost ±1,1–1,2 core-uur per 55.000 foto's (Keuze 77).
    Anders: (b) **aan** voor iedereen. Dan starten na de update de backfills van alle gebruikers,
    na die van de index. Een later besluit is goedkoop: `photo_pref.beeld` is `NULL` tot iemand
    kiest.
12. **Wat mag CI gebruiken om zoeken op beeld na te rekenen?** (nieuw in r3)
    (a) Een vaste set van 120 foto's uit het publiek domein (CC0 of publiek domein, elk met sha256,
        liefst zonder herkenbare mensen), alleen gedownload in de job `beeld-model`, nooit in het
        repo of het image. Standaard: **ja.** Zonder die set is er geen harde poort op de
        kwaliteit; dan meet `app.cli beeld-kwaliteit` op de LXC.
    (b) De officiële bibliotheken (torch, transformers en tokenizers) als meetlat, alleen in de job
        `beeld-model`, nooit in het image. Die vergelijkt onze omzetting en onze tokenizer getal
        voor getal met het origineel. Standaard: **nee.** Dan bewijzen alleen de kwaliteitspoort en
        BZ3 dat het klopt.
13. **Leveren we in twee delen op?** (nieuw in r3, K2-11)
    Standaard: **ja, twee delen.** 0.8.0 met de kern (zoeken, tekst, OCR, foto-gegevens, kaart,
    duplicaten, personen, herinneringen, slimme albums) rond dag 27; daarna 0.8.1 met gezichten en
    beeld rond dag 43. Zo heb je de kern ±3 weken eerder.
    Anders: (b) één oplevering, 0.8.0 rond dag 41 (twee dagen minder, omdat er één integratie is).
    Loopt het uit, dan schrappen we in deze volgorde: eerst zoeken met woorden op beeld (de
    tokenizer en de tekstkant; "Meer zoals deze" blijft), dan beeld helemaal (naar een fase 6b),
    dan de groepen "Wie is dit?" van gezichten (de voorstellen blijven).
14. **Mag zoeken op beeld 250 ms duren?** (nieuw in r3, K2-13)
    Het stappenplan zegt 200 ms. Gewoon zoeken houdt die 200 ms. Zoeken op beeld rekent eerst een
    taalmodel (±62–74 ms) en vergelijkt dan alle foto's; bij 100.000 foto's is dat in de container
    al ±125 ms, zonder de rest van de app.
    Standaard: **250 ms voor zoeken op beeld, 200 ms blijft voor gewoon zoeken.** Is het te traag,
    dan krijg je de treffers van gewoon zoeken, met een melding.
15. **Mag tesseract het image 69 MB groter maken?** (nieuw na spike S5, 4 november 2026)
    Vraag 1 noemde ±20 MB, en daar zei je ja op. S5 mat in CI 69 MiB: de talen zijn 20 MB, de
    rest zijn tesseract, leptonica en 12 bibliotheken die nog niet in het image zaten. Kleiner
    gaat niet zonder de talen of OCR te verliezen.
    **Beantwoord op 4 november 2026: "Ja, erin"** (kaartje). Tesseract komt in het image, met
    + ≤ 75 MB als poort in `images`. Het andere antwoord was (b) geen OCR.

Vraag 1–7 kwamen uit revisie 2; daarvan is alleen Vraag 4 nog open. Vraag 8–15 zijn nieuw;
Vraag 15 is beantwoord. De
oude nummers 8 en 20–22 uit de twee ontwerpen voor X2 en X3 bestaan niet meer.

---

## 2. Pakketten

### 2.0 Overzicht, eigendom en regels

Herzien in r2 (C8, C9) en in r3 (K2-01, K2-11). Fase 5 liet zien wat een golf echt kost: twee
pakketten, dan een dag voor review, de fixer en de nacontrole. Zo plannen we nu ook. Revisie 3
voegt Z0b, A0 en twee golven voor X2 en X3 toe, en levert in twee delen op (Vraag 13).

| Golf | Pakketten | Dagen | Na deze golf werkt |
|---|---|---|---|
| 0 | Z0 Fundament (Z0-api, Z0-web en Z0-e2e naast elkaar) | 3 | migratie 0010, de contracten, de spike S1–S11, `bench --los` |
| 0b | Z0b: de contracten van X2 en X3 (Keuze 55) | 1 + 1 review (samen met Z0) | de velden, de rij `analyse`, `Step.EMBED`, de sleutels `faces` en `beeld`, de stubs van X2 en X3 |
| 1 | Z1 Zoekmachine + Z2 Tekst | 4 + 1 review | zoeken op naam en inhoud binnen 200 ms (API), tekst uit documenten, de woorden per pagina (API) |
| 2 | Z4 Zoeken in de web + Z6 Personen, herinneringen, slimme albums en filters | 4 + 1 review | zoeken in de web; personen benoemen en vinden; herinneringen; slimme albums |
| 3 | Z3 Foto-gegevens (API) + Z5 Kaart en duplicaten (web) | 4 + 1 review | GPS, plaatsen, de kaart, duplicaten, de waarschuwing bij delen |
| 4 | Z7 Beheer en uitrol + X1 OCR | 3 + 1 review | `/beheer`, reservekopie, terugrollen, OCR |
| I1 | Integratie 1 | 3 | samenvoegen, de metingen van §4.4 en §4.5, **0.8.0** met de kern |
| A0 | A0 Fundament van X2 en X3 | 2 | de contracten van de container `analyse`, migratie 0011 en 0012, de nep-worker, het blok in de Dockerfile en de extra |
| 5 | X2a De container ‖ X3a Tokenizer en omzetter | 4 + 1 review | de container `analyse` met de modellen; de tokenizer en de omzetter; de go/no-go van X3 (dag 33) |
| 6 | X2b Gezichten ‖ X3b Zoeken op beeld | 5 + 1 review | gezichten vinden en voorstellen; zoeken op beeld met woorden en "Meer zoals deze" |
| I2 | Integratie 2 | 3 | de belastingtest met `analyse` vol, de metingen, **0.8.1** met gezichten en beeld |

Samen ±43 werkdagen (≈ 9 weken): 3 + 1 + 1 (Z0, Z0b, review), 3 × 5 (golf 1–3), 4 (golf 4),
3 (I1), 2 (A0), 5 (golf 5), 6 (golf 6) en 3 (I2). Met één oplevering (Vraag 13b) vervalt I1, en
duurt de laatste integratie 4 dagen: ±41 werkdagen. De dagen staan in §5.

**Waarom deze volgorde.** Z1 en Z2 zijn de kern; Z4 en Z6 bouwen op het register van filters
(Z1) en op de tekst (Z2). Z3 en Z5 hangen het minst aan de rest. Z5 bouwt tegen de bevroren
contracten van §3.4, met vaste antwoorden in vitest; zijn e2e draait na de samenvoeging met Z3, op
de reviewdag van golf 3. X1 komt na Z2, omdat hij een ingang toevoegt aan `app/render.py`, dat in
golf 1 van Z2 is. X2 en X3 komen na de eerste integratie: ze bouwen op Z1, Z2, Z4, Z6 en Z7, en de
kern hoeft niet op hen te wachten (K2-11). Hun contracten liggen al vast in Z0b, zodat golf 1–4
niets hoeven te veranderen (K2-01).

**De reviewdag** (zoals fase 5): een review op veiligheid en haalbaarheid, de fixer met een test
per punt, en een nacontrole die elke fix even terugdraait om te zien dat de test faalt. Daarna
samenvoegen en alle checks (§4.6).

**Wat schuift, in deze volgorde, als een golf uitloopt.**

- **In golf 1–4:** plaatsnamen op de kaart (zoom ≥ 6), dan de suggesties (de zoekpagina werkt
  zonder), dan de web van de bijna-duplicaten (de API blijft), dan X1 (naar 0.8.1). Slimme albums
  schuiven niet half: zonder `album_item.shared` en de vraag bij delen (Keuze 42) komen ze er niet
  in. Niets uit deze lijst raakt een "klaar als" van de kern.
- **In golf 5 en 6** (Vraag 13): eerst zoeken met woorden op beeld (de tokenizer en de tekstkant;
  "Meer zoals deze" blijft), dan beeld helemaal (naar een fase 6b), dan de groepen "Wie is dit?"
  van gezichten (de voorstellen blijven). Binnen X2 staat de volgorde bij X2, "Wat schuift". Geen
  privacyregel en geen poort schuift.
- **De go/no-go van X3** (Keuze 73, K2-12): haalt de omzetter aan het eind van dag 33 zijn test
  niet, dan gaat X3 naar een fase 6b, en krijgt X2b de dagen van X3b als marge.

**Niet in fase 6:** GPS uit downloads halen (fase 10, of Vraag 4b), de kaart in 50m en straten,
tegels van buiten (Jonas koos op 3 november 2026 voor "geen tegels"), Nederlandse
provincienamen, stemming. Bij beeld: labels of objecten als filter, video's, en foto's die met je
gedeeld zijn (Keuze 71). Bij gezichten: foto's van anderen in jouw map, tenzij die ander het zelf
aan heeft (Keuze 69, Vraag 9).

**Eigendom.** Een bestand heeft één eigenaar per golf. Z0 en Z0b zetten de stubs en de hotspots;
daarna bewerkt alleen de eigenaar het bestand. Herzien in r2 (C9) en in r3: het pad van de
migraties is `api/migrations/versions/` (K2-18). Herzien na de review van Z0 (haalbaar F1): de
kaart heeft geen eigen tabel en geen eigen migratie. `photo_geo` met `dead`, `taken_at`, de
dekkende `ix_photo_geo_tile` en de triggers `geo_*` staan in 0010 (Z0); Z3 schrijft alleen de
rijen en leest met `own_live_geo`.

| Bestand of map | Eigenaar |
|---|---|
| `api/migrations/versions/0010_zoeken.py`, `app/models.py` (nieuwe modellen), `app/search/schema.py`, `app/search/live.py` (`own_live`, `own_live_geo`), `app/search/types.py` | Z0 |
| de hotspots van §3.5 (`main.py`, `cli.py`, `jobs.py`, `files.py`, `trash.py`, `userdelete.py`, `web/src/routes.tsx`, `web/src/Beheer.tsx`, `web/scripts/budget.mjs`) | Z0 |
| de contracten van X2 en X3 in de bestanden van Z0 (§3.3, §3.5: velden, `Step.EMBED`, de rij `analyse`, `PHOTO_PREFS`, `register_large`, de stubs, de lege plekken in de web, de i18n-gebieden `photos_faces` en `beeld`, de catalogi `faces.ts` en `beeld.ts`) | Z0b |
| `app/search/{engine,query,access,snippets,deadline,like,suggest,limits,bench}.py`, `routers/search.py` | Z1 (de tak voor de vraag van 0.7.0 in `routers/search.py` blijft tot Z4 en gaat dan weg: "De overgang" in §3.4) |
| `app/search/filters.py` (register en de filters `type`, `from`/`to`, `tag`, `library`, `in`) | Z1 |
| `app/analysis.py`, `app/extract/*`, `app/office/reader.py` (alleen een nieuwe stand "alle tekst"), `app/render.py` (ingang `text`), `app/previews.py` (de terugval zonder media), `routers/pages_words.py` | Z2 |
| `app/media/worker/ops/text.py`, de handdruk in `app/media/worker/run.py` | Z2 |
| `app/geo/*`, `app/dupes/*`, `app/render_meta.py`, `app/photometa.py` (v2), `app/media/worker/ops/meta.py` (locatie), `routers/{geo,photos_map,duplicates}.py`, `tools/geo/*`, `tools/kaart/*`, `app.cli geo-load` | Z3 |
| filters `place`, `country`, `camera` in `app/search/filters_photo.py` | Z3 |
| `web/src/search/**` (vervangt `web/src/Search.tsx` en `searchQuery.ts`), de zoekknop in `web/src/App.tsx`, `web/src/viewer/PdfView.tsx`, `web/src/viewer/PageViewer.tsx`, `web/src/viewer/TextLayer.tsx` | Z4 |
| `web/src/photos/map/**`, `web/src/dupes/**`, de waarschuwing in `web/src/ShareDialog.tsx` (lazy), de inhoud van `LocationRow` in `web/src/viewer/InfoPanel.tsx` | Z5 |
| `app/people/*` (behalve `app/people/faces/**`), `app/memories.py`, `app/albums_rule.py`, `routers/{people,memories,photo_prefs}.py`, filter `person`, `routers/photos.py`, `routers/albums.py`, `access._via_albums`, `public._in_album` | Z6 |
| `web/src/photos/people/**`, `web/src/photos/Memories.tsx`, `web/src/photos/{PhotosPage,AlbumPage,AddToAlbum}.tsx`, de regel in het albumdialoog, `PeopleRow` in het infopaneel | Z6 |
| `app/search/admin.py`, `routers/admin_search.py`, `web/src/admin/SearchTab.tsx`, `infra/single/*` (download van `geo`, de reservekopie, `.env.example`), `docs/runbooks/zoeken.md` | Z7 |
| `app/media/worker/ops/ocr.py`, `app/ocr/*`, de ingang `ocr_page` in `app/render.py` (golf 4, na Z2), de regel in `Dockerfile` met `chmod 0711`, `"tesseract"` in `app/media/worker/guard.py`, `infra/single/tests/test_ocr_container.sh` | X1 |
| `api/migrations/versions/0011_gezichten.py` en `0012_beeld.py`, de modellen van gezichten en beeld in `app/models.py`, de triggers van 0011 en 0012 in `app/search/schema.py` (K2-17), `app/people/faces_types.py`, `app/people/hooks.py`, `app/analyse/wire.py` met de enum `Op`, het formaat van `app/analyse/models.lock`, `app/analyse/licenties/`, `api/tests/analyse/fake_worker.py` | A0 |
| de hotspots van A0 (§3.5): `db.py`, `migrate.py`, `jobs.py` (het checkpoint van elk uur), `routers/people.py` (drie haken), `files.py` en de uploads (`node_origin`), `main.py`, `cli.py`, `config.py`, `Dockerfile`, `api/pyproject.toml` en `uv.lock` (de extra) | A0 |
| `app/analyse/{client,runtime,health,state,status,monitor}.py`, `app/analyse/worker/**` (behalve `ops/beeld.py` en `omzetten/`), `infra/single/compose.analyse*.yaml`, de functies van analyse in `install.sh`, `infra/single/tests/test_analyse_container.sh`, `test_install_analyse.sh`, `api/tests/analyse/**`, `api/tests_analyse/` (de map en de telling), de jobs `api-analyse` en `analyse-image` | X2a |
| `app/people/faces/**`, `app/routers/people_faces.py`, `web/src/photos/faces/**`, `web/src/admin/FacesBlock.tsx`, het gebied `photos_faces`, `errors/faces.ts`, `api/tests/people/faces/**`, `e2e/test_zoek_gezichten*.py`, `app.cli analyse-kwaliteit`, `docs/fase-6/x2.md`, `toetsenbord-x2.md`, `docs/runbooks/gezichten.md` | X2b |
| X3a en X3b: zie de tabel bij X3 | X3 |

`InfoPanel.tsx` krijgt in Z0 twee lege plekken (`LocationRow`, `PeopleRow`), en in Z0b een derde
(`LikeRow`, voor X3); Z5, Z6 en X3 vullen elk hun eigen plek in een eigen bestand.

**Regels** (zoals fase 5):

- elk pakket in een eigen worktree; hooguit 3 tegelijk (de sandbox heeft 8 GB en hooguit 3 zware
  processen). In een golf zijn dat twee pakketten en de e2e;
- samenvoegen in de volgorde van de tabel; na elke samenvoeging alle checks (§4.6);
- geen nieuwe afhankelijkheid in de basis van `api/pyproject.toml` of in `web/package.json`
  (B19). X1 voegt tesseract toe aan de apt-regel van de Dockerfile. A0 voegt de optionele extra
  `analyse` toe (precies vijf pakketten, Keuze 57); de app importeert ze nooit
  (`test_no_numpy_in_app.py`). Een ander nieuw pakket, model of dataset is een nieuwe vraag aan
  Jonas;
- elk pakket eindigt met `docs/fase-6/<pakket>.md` (`z1.md` … `x3.md`): wat er gebouwd is, wat
  afwijkt van dit ontwerp, en wat open is;
- een pakket met schermen levert een toetsenbordlijst `docs/fase-6/toetsenbord-<pakket>.md`;
- elke "klaar als" hieronder heeft een test met een naam. Zonder die test is het pakket niet af;
- elk pakket met eigen teksten heeft een eigen i18n-gebied en een eigen foutcatalogus (Z0 en Z0b
  maken ze leeg aan, Keuze 48 en 55), zodat twee pakketten nooit in hetzelfde bestand schrijven;
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
- de tweede controle (Keuze 15); wissen in stukken (Keuze 11) is na de review van Z0 (haalbaar
  F3) al door Z0 gebouwd, in `routers/trash.py` en `userdelete.py` via de gedeelde helper;
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
  ook niet bij de kaart op zoom 14 en bij `/photos?place=`; ook niet bij een databasefout (de
  engine heeft `hide_parameters=True`; staat er al sinds de review van Z0, veilig F6, in
  `test_log_hiding.py`);
- `test_search_snippets.py`: ß, een treffer na teken 20.000 (`snippet: null`, `match: "text"`),
  het budget van 40 ms met een nepklok, alleen tekst (geen HTML);
- `test_search_purge_batches.py`: 10.911 nodes, geen transactie langer dan 1 s (staat er al
  sinds de review van Z0, haalbaar F3: een upload komt tussen twee stukken door en is binnen
  1 s klaar).

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
`web/src/i18n/errors/search.ts`; de begroting. Herzien na de review van Z0 (haalbaar F4): de web
stuurt de vraag van §3.4 (`limit` ≤ 50, geen `archive`), en Z4 haalt in dezelfde merge de tak voor
de vraag van 0.7.0 uit `routers/search.py` weg, met de grenzen van 0.7.0 in `searchQuery.ts`,
`Search.tsx`, `api/tests/test_search.py` en `e2e/test_zoek_z0.py` ("De overgang" in §3.4).
Na de review van Z0b (contract F3) ook wat Keuze 81 aan Z4 geeft: de keuze "Namen en tekst" /
"Wat er op de foto staat", de laatste suggestie en de regel "Geen foto met deze naam?"; in de
stand beeld laadt `/zoeken` het raster van X3 lazy uit `src/search/beeld/BeeldResults.tsx`, met
het gebied `beeld` (de plek zette Z0b; tot X3 toont hij niets).

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
- `photo_pref` en `/me/photo-settings` (Keuze 44). PATCH roept eerst `prefs.check_switch()`
  aan, vóór er iets geschreven wordt, en daarna `prefs.switched()`; GET geeft
  `prefs.photo_settings()`. Zo zet niemand gezichten of beeld aan zonder X2 of X3 en zonder de
  analyse, en hoeft X2 deze route niet open (na de review van Z0b: veilig V3, contract F1 en F4).
  Test van Z6: `PATCH {faces: "aan"}` geeft `409 faces_unavailable`;
- web: "Persoon toevoegen" met kader in het infopaneel, `/fotos/personen`, de persoonpagina,
  "Vandaag, N jaar geleden" op `/fotos`, de regel en de vraag over delen in het albumdialoog,
  het merkje "alleen voor jou", filters op `/fotos`; het gebied `photos_people` en de catalogus
  `people.ts`;
- de plekken voor X2 en X3 (na de review van Z0b, contract F3): `PeoplePage` en `PersonPage`
  houden de lazy plekken `photos/faces/PeopleFaces.tsx` en `PersonFaces.tsx` (gebied
  `photos_faces`; `src/lazySlots.test.ts` eist ze). Het blok Foto's onder Account laadt lazy
  `photos/faces/FacesSwitch.tsx` (gebied `photos_faces`) en `search/beeld/BeeldSwitch.tsx`
  (gebied `beeld`), en `/fotos` krijgt de knop "Zoeken op beeld" (Keuze 81). Tot X2 en X3 ze
  vullen, tonen de plekken niets.

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

**Bouwt:** de tab op `/beheer` (Keuze 51) en `app/search/admin.py`, met de blokken, de
schakelaars en het herbouwen van gezichten en beeld via `search.hooks.admin_block`,
`admin_switch` en `admin_rebuild` (na de review van Z0b, contract F4: zonder X2 of X3 het blok
`null` en `501 not_built`); de reservekopie met alleen de
standaardbibliotheek in `install.sh`, en de opruimregel per alembic-head (Keuze 52); het
downloaden van de twee bronnen voor plaatsen naar `DATA_ROOT/geo/` met sha256, en `PLAATSEN` in
`.env.example` (Keuze 29); `app.cli bench --los` (de versie van Z0, nu met de uitkomst op
`/beheer`); `docs/runbooks/zoeken.md` (bijwerken, de index herbouwen, terug naar 0.7.1, de
meting); de belastingtest naast OnlyOffice en media (§4.5); de README.

**Tests:** `infra/single/tests/test_install_search.sh`: de reservekopie gemaakt met het image van
0.7.1 én van 0.8.0; een mislukte reservekopie stopt het script vóór elke wijziging; na twee runs
op 0010 staat de kopie van 0009 er nog; `PLAATSEN=uit`; een download die faalt, is niet fataal en
wordt de volgende run opnieuw geprobeerd; terug naar 0.7.1 met het script van de commit van
0.7.1 (`80d5bb8`), en weer vooruit (precies de stappen van §3.10). `e2e/test_zoek_beheer.py`;
vitest voor de tab. Herzien in golf 4 (Z7): 0.7.1, `80d5bb8` en `0009` in plaats van 0.7.0 en
`0008`, zoals in §3.10 (waarom: daar, "Waarom niet meer `1130e1b` en 0.7.0").

**Klaar als:** de tests groen; de CI-job `infra` doet de weg terug echt; de toetsenbordlijst.

### X1 — OCR

**Doel.** Tekst uit scans en foto's van tekst, doorzoekbaar.

**Wat Jonas koos:** ja, op 3 november 2026 ("alles"). tesseract komt in het image (Keuze 24).

**Bouwt:** `Op.OCR` in de worker (`app/media/worker/ops/ocr.py`): kind 1 (`app.render ocr_page`
met de handdruk) maakt een grijs PGM, de worker controleert de kop, kind 2 is tesseract op stdin
met `RLIMIT_DATA` 512 MB en 60 s, OSD alleen bij een lage zekerheid (Keuze 22); `app/ocr/` in de
app (de rij `ocr`, `ocr_page`, `photo_ocr`, de knop "Tekst herkennen", `photo_pref.ocr_photos`,
de grenzen per gebruiker, `ocr_has_text`) (Keuzes 23, 24); de regel `tesseract-ocr
tesseract-ocr-nld tesseract-ocr-eng tesseract-ocr-osd` in de apt-RUN van de Dockerfile, tesseract
`0711` en in `guard.BINARIES`; de CI-job `ocr` (§4.6). Zonder tesseract (een ouder image) blijft
OCR uit met `ocr_missing` (Keuze 24).

**Tests:** `test_ocr_unit.py` (deskew op 0°, 3°, 7°, −5°; TSV lezen; zekerheid; de kop van de PGM;
geen tesseract → `ocr_missing`; OSD alleen onder 60); `test_ocr_limits.py` (`429 ocr_limit`; een
foto van een ander → `404`; `ocr_has_text`); `test_ocr_real.py` (marker `ocr`, echte tesseract op
gemaakte scans van §3.6: recall per woord ≥ 0,95 op 0° en 3°, ≥ 0,9 op 7° en 90°; een foto met
EXIF-draaiing 6 en een HEIC); `infra/single/tests/test_ocr_container.sh` (modus `0711`, tesseract
alleen in de worker); `e2e/test_zoek_ocr.py` (marker `ocr`: een PDF zonder tekstlaag en een foto
van een bon → zoeken op een woord dat alleen in het beeld staat → snippet → de viewer markeert het;
een kopie van Bram vindt de tekst uit Eva's foto niet).

**Klaar als:** de tests groen in de job `ocr`; de poort ≥ 8 pagina's per minuut per core (§4.5);
`media-image` groen met tesseract in het image en `test_ocr_container.sh`; het image is
hooguit 75 MB groter door tesseract (`images`; S5 mat 69 MiB, Vraag 15).

### A0 — Fundament van X2 en X3

**Doel.** Eén fundament waar X2 en X3 naast elkaar op bouwen: dezelfde container, dezelfde
handdruk, hetzelfde werkmodel en de migraties, vast vóór golf 5 begint (K2-02, K2-17).

**Wanneer.** Dag 28–29, na de eerste integratie (0.8.0), vóór golf 5. Eén worktree; de e2e
ernaast.

**Bouwt:**

- `app/analyse/wire.py` met het label van de handdruk en de enum `Op` (`caps`, `cancel`, `faces`,
  `group`, `embed_image`, `embed_text`, `rank`, `forget`); de stubs geven `not_built` tot X2 en
  X3 (§3.11);
- het werkmodel met twee kinderen (`werk` en `zoeken`), elk met `busy`, een deadline per
  opdracht en `cancel` (Keuze 56, K2-02), als contract en in de nep-worker;
- `api/tests/analyse/fake_worker.py` met beide kinderen, zonder numpy;
- `client.py` en `monitor.py` als contract (de standen van Keuze 59), met de stubs van de
  routes;
- migratie 0011 en 0012 met hun modellen en triggers (§3.1a), en `node_origin` met het vullen bij
  upload, kopie en `place_file` (Keuze 69);
- het formaat van `models.lock` en de map `app/analyse/licenties/` (Keuze 58, K1-13);
- het blok in de Dockerfile, de extra `analyse` in `api/pyproject.toml` en `uv.lock`, en
  `/run/analyse` (§3.11, Keuze 57);
- de hotspots van §3.5: `db.py` (`secure_delete`, `temp_store`), `migrate.py` (een checkpoint na
  elke migratie), `jobs.py` (het checkpoint van elk uur, met een eigen verbinding en
  `busy_timeout` 0, K2-08), `routers/people.py` (drie haken: samenvoegen, een persoon van een foto
  halen, een persoon wissen), `main.py`, `cli.py` en `config.py`.

**Tests:** `test_migration_0011.py` en `test_migration_0012.py` (heen en terug met data;
`compare_metadata`); `test_migration_0010.py` gaat naar `upgrade('0010')` (K2-16);
`test_schema_triggers.py` met 0010, 0011 en 0012 (K2-17); `test_analyse_wire.py` (de handdruk, de
enum, `busy` per kind, `cancel`); `test_fake_analyse_worker.py` (beide kinderen, het protocol);
`test_db_pragmas.py` (elke verbinding uit de pool: `secure_delete` 1 en `temp_store` 2, K1-03);
`test_node_origin.py` (upload, kopie, `place_file`, een uploadlink); `test_no_numpy_in_app.py`
(de app en de job `api` zonder de extra).

**Klaar als:** de tests groen; `uv sync --frozen` zonder extra installeert geen numpy;
`uv export` met en zonder extra verschilt in precies vijf pakketten; X2a en X3a kunnen beginnen
zonder een bestand van A0 te wijzigen.

### X2 — Gezichten automatisch (X2a en X2b)

**Doel.** Gezichten vinden en groeperen, als voorstel voor de personen van Z6. Wat de gebruiker
bevestigt, wordt een persoon op de foto; niets wordt vanzelf bevestigd.

**Wat Jonas koos:** ja, in fase 6 (het kaartje "Ook gezichten", 3 november 2026).

**Hangt af van.** Z0b (de contracten), A0 (de container-contracten, 0011, de nep-worker), Z6
(personen, `photo_person`), Z7 (`/beheer`, `install.sh`) en Z2 (de wachtrij met batches).

**Wanneer en hoe lang** (§5).

| Deel | Dagen | Wat |
|---|---|---|
| X2a (golf 5, dag 30–32) | 3 | de container: Dockerfile-blok en extra (van A0) in gebruik, compose, `install.sh` (de modellen, de sleutels), de worker (`caps`, zelftest, de kinderen, de wachter), de monitor, `api-analyse` (laag 2 en 3) en `analyse-image` |
| X2b (golf 6, dag 35–39) | 5 | de keten, de rij, groeperen, de keuzes, privacy en versleuteling, de API, de web, `/beheer`, de tests van laag 1 en 4, de documenten |

**Eigendom.** Zie §2.0 (de rijen A0, X2a en X2b).

**Bouwt:**

- de container `analyse` en de dienst in `compose.analyse.yaml`, met de grenzen (§3.11);
- YuNet en SFace met sha256 in `models.lock`, buiten `$DATA_ROOT` (Keuze 58);
- de stap `faces` op de preview van 1024, opgevuld tot een veelvoud van 32 (Keuze 60, K2-05);
- `face`, `face_person`, `face_scan`, `face_owner` (0011 van A0) gevuld door de app;
- groeperen per eigenaar in het kind `werk`, met `GROUP_MAX` 60.000 (Keuze 62);
- bevestigen, afwijzen en samenvoegen, met Z6 als bron (Keuze 63);
- de harde privacyregels, de versleuteling per eigenaar en de regel van Keuze 69 (Keuze 64, 68,
  69);
- de rij `analyse`, pas na de backfill van de index (Keuze 65, K2-07);
- de API, de web en het blok op `/beheer` (Keuze 66, 67);
- `app.cli analyse-kwaliteit` en `analyse-bench` (Vraag 8).

De details staan hieronder; de levensloop is een aanvulling op §3.9.

#### Gezichten vinden (Keuze 60)

**De keten, voor één foto.**

1. **De app opent de preview.** `previews/<sha[:2]>/<sha>/1024.webp`, alleen-lezen. Die is al
   gedraaid volgens EXIF, en nooit groter gemaakt [code: wt-z0api `app/previews.py`].
2. **De app geeft twee fd's door** via `SCM_RIGHTS`: de preview en een memfd voor de uitvoer.
   De worker kan de bestanden van 10001 zelf niet openen, ook niet via `/proc` (V1, fase 5).
3. **Het kind `werk` decodeert.** Alleen WebP (`Image.open(fd, formats=["WEBP"])`), met
   `MAX_IMAGE_PIXELS` op 4 MP en de waarschuwing voor een decompressiebom als fout, zoals
   `render.py:397–399`; anders `too_big` (K1-07).
4. **YuNet.** Op de preview, rechts en onder opgevuld met nullen tot een veelvoud van 32. Zonder
   dat faalt YuNet 2026may op bijvoorbeeld 1024 × 683 [gemeten: K2-05]. De coördinaten blijven
   gelijk. Een score ≥ 0,9: dat is de standaard van OpenCV, en voorlopig.
5. **Uitvoer decoderen en NMS.** Gezichten met een korte kant onder 40 px vallen af. Dat
   betekent ook minder voorbijgangers. Er blijven hooguit 40 gezichten over, de grootste eerst.
6. **Uitlijnen** op 5 punten (Umeyama) naar 112 × 112.
7. **SFace.** Een vector van 128 × float32, L2-genormaliseerd.
8. **Het antwoord.**
   - De vectoren gaan als ruwe bytes in de memfd: n × 512 B, in dezelfde volgorde.
   - Het JSON-antwoord bevat alleen de kaders, de scores, `px` en n.
   - Een vector staat dus nooit in een bericht. Een bericht dat in een log belandt, kan er
     geen bevatten.

**Wat de app controleert** voor hij schrijft:

- n ≤ 40;
- elk kader binnen 0–1;
- de score binnen 0–1;
- de memfd is precies n × 512 B;
- elke waarde is eindig;
- |norm − 1| ≤ 0,001.

Klopt één van deze niet, dan wordt de foto `failed` met `bad_output`, zonder nieuwe poging.

**De tijd per stap, op één core, één thread** [gemeten: spike B6, en opnieuw voor de kritiek:
K2-04]:

| Stap | Tijd |
|---|---|
| een WebP van 1024 × 768 decoderen | 20,8–23,0 ms (p95 22–30,6) [K2-04] |
| voorbewerken | 13,8–15,8 ms [B6, op 1024 × 640] |
| YuNet 2026may op 1024 × 768 | 40,3–42,9 ms (p95 43,9–50,7) [K2-04]; op 1024 × 640 29,7–33,6 ms |
| uitvoer decoderen + NMS | 0,2–0,3 ms [B6] |
| uitlijnen, per gezicht | 1,0–1,1 ms [B6] |
| SFace fp32, per gezicht | 52,1–59,5 ms [B6] |
| socket, controle, versleutelen en schrijven in de app | ±2–5 ms [schatting] |
| **een foto met één gezicht** | **±142–164 ms** [berekend: K2-04] |
| **elk gezicht extra** | **+53–61 ms** [B6] |

De spike decodeerde een kleine JPEG (7–8 ms) en mat YuNet op 1024 × 640. Een telefoonfoto (4:3)
geeft een preview van 1024 × 768, en de bron is een WebP. Daarom rekent het ontwerp met ±25–30%
meer dan de eerste schatting van ±112–126 ms (K2-04). De rijen hierboven opgeteld geven
±130–148 ms; ±142–164 ms is dus aan de veilige kant. De poort van ≥ 250 foto's per minuut meet
het echte getal op de runner.

Drie threads maken het niet sneller [gemeten: spike B6]. Daarom één thread, en `cpus: 1.0`.

**De opdracht `faces`.**

- **Verzoek:**
  `{"op": "faces", "min_px": 40, "max": 40, "score": 0.9, "timeout": 10}`, met de fd's
  `[preview, uit]`.
- **Antwoord:**
  `{"code": "ok", "w": 1024, "h": 768, "faces": [{"x", "y", "w", "h", "score", "px"}], "n": k}`.

**De codes in `face_scan.code`.**

| Code | Stand | Nieuwe poging |
|---|---|---|
| `model_missing` | blijft `wait` | als de modellen er zijn |
| `no_preview` | blijft `wait` | als de 1024 er is (X2, "Achtergrondwerk") |
| `too_small` | `skip` | nee: het origineel is ≤ 256 px, dus er is geen 1024 |
| `unreadable` | `failed` | nee |
| `too_big` | `failed` | nee |
| `bad_output` | `failed` | nee |
| `timeout` | na 3 keer `failed` | tot 3 pogingen |

#### Groeperen en voorstellen (Keuze 62)

**De opdracht `group`.**

- **Invoer.** Een verzegelde memfd van de app (`F_SEAL_WRITE`, `F_SEAL_GROW` en `F_SEAL_SHRINK`):

  ```
  'PFG1'                               4 B
  n, known, persons, rejects           4 × u32
  n × 512 B                            alle vectoren (eerst de bevestigde, dan de open)
  known × int32                        persoon-index van de eerste `known` vectoren
  rejects × (int32 gezicht, int32 persoon)
  ```

  De drempels gaan in het JSON-verzoek: `{"op": "group", "mode": "suggest" | "full", "k": 10,
  "t_link": 0.50, "t_suggest": 0.45, "margin": 0.05, "min_group": 3}`.
- **Uitvoer.** Een memfd van de app, met per gezicht `(int32 voorstel, float32 score, int32 groep)`.
  Een voorstel van −1 betekent geen voorstel; groep −1 betekent geen groep. De app leest precies
  n × 12 B; een andere grootte is `bad_output`.
- **Wat de worker niet ziet.** Hij ziet indexen, geen UUID's, namen of nodes. Per opdracht ziet
  hij één eigenaar. De app ontsleutelt de vectoren (Keuze 68) en schrijft ze in de memfd; de worker
  ziet nooit een sleutel.

**Voorstellen** (`suggest`):

1. Per open gezicht de hoogste cosinus met de bevestigde gezichten van elke persoon. Dat gaat
   met `np.maximum.reduceat`, in blokken van 512.
2. De beste persoon wordt het voorstel, als hij aan drie dingen voldoet:
   - zijn score is ≥ `t_suggest` (0,45);
   - hij ligt minstens `margin` (0,05) boven de tweede persoon;
   - het paar is niet afgewezen.
3. Personen die verborgen zijn, laat de app vooraf weg.
4. De drempel 0,45 is strenger dan 0,363, de drempel die OpenCV voor SFace noemt [doc]. Zo
   komen er minder foute voorstellen.

**Groepen** (`full`), voor "Wie is dit?":

1. **Buren.** Per gezicht hooguit `k` = 10 buren met een cosinus ≥ `t_link` (0,50). Eerst de
   drempel, dan pas sorteren: `np.nonzero(s >= t_link)`, `lexsort`, rang < k, in blokken van 512.
2. **Randen.** Alleen wederzijdse buren.
3. **Samenvoegen.** Union-find, de sterkste rand eerst. Twee groepen gaan pas samen als de
   gemiddelde cosinus tussen ze ≥ `t_link − 0,05` is, gemeten op een steekproef van hooguit
   32 × 32. Zo ontstaat er geen keten van A via B naar C als A en C niet op elkaar lijken.
4. **Wat telt.** Alleen groepen van ≥ 3 gezichten.
5. **Wat meedoet.** Alleen open gezichten: niet bevestigd en niet verborgen. Hooguit
   `PLATFORM_FACES_GROUP_MAX` (60.000, K2-06), de nieuwste eerst. De app leest ze via de index
   `ix_face_new`, zodat SQLite niets sorteert en niets naar een tijdelijk bestand schrijft (K1-03).

**Gemeten.** `groep.py` op synthetische vectoren (128 × float32, identiteiten met ruis), op één
core (`taskset -c 2`, `OPENBLAS_NUM_THREADS=1`, numpy 2.5.4), 2 runs (3 bij 5.000). Er draaide
toen ook een pytest op de machine, dus de tijden zijn eerder te hoog dan te laag
[gemeten: `gezichten/uit/groep2_*.json`].

| Gezichten | Buren | Groepen (union-find) | Voorstellen (10% bekend) | maxrss |
|---|---|---|---|---|
| 5.000 | 0,19–0,25 s | 0,11 s | 0,03–0,04 s | 60 MB |
| 20.000 | 3,5 s | 0,5–0,8 s | 0,45–0,56 s | 122 MB |
| 60.000 | 31,8–33,6 s | 2,0 s | 3,9–4,3 s | 285 MB |
| 100.000 | ±90 s | ±3,5 s | ±7 s | ±450 MB [schatting, n²; daarom de grens van 60.000] |

- **De eerste vorm was te zwaar.** `argpartition` over hele rijen, in blokken van 1.024, kostte
  bij 60.000 74–76 s en 1.376 MB [gemeten: `groep_60000.json`]. Daarom eerst de drempel.
- **De kwaliteit op synthetische data:** zuiverheid 1,0, recall 0,986–0,989, en 0–5 foute
  voorstellen op 20.467. **Dat zegt niets over echte gezichten.** Dat meet CI (X2, "Tests").

**Wanneer de app groepeert.**

| Wanneer | Wat | Kosten bij 60.000 |
|---|---|---|
| na elke batch van `faces.scan` | `suggest` voor alleen de nieuwe gezichten van die batch (≤ 640) tegen de bevestigde | < 0,1 s |
| 30 s na de laatste keuze van de eigenaar ("ja", "nee", samenvoegen) | `suggest` voor alle open gezichten | ≤ 5 s |
| hooguit één keer per uur per eigenaar terwijl er nieuwe gezichten komen, en als zijn rij leeg is | `full`: groepen en voorstellen | ±40 s |
| na een nieuwe versie | `full` | ±40 s |

**Schrijven.**

- In stukken van 2.000 gezichten per transactie.
- Bij `full` gaat `face_owner.run` één omhoog.
- Een groep heet naar buiten `<run>:<cluster>`. Vraagt de web naar een oude run, dan antwoordt
  de app `409 faces_gone` en laadt de web opnieuw.

#### Bevestigen, afwijzen, samenvoegen, en de personen van Z6 (Keuze 63)

**Eén regel.** Een `photo_person` met `source = 'auto'` bestaat precies zolang er op die node
minstens één gezicht met `ja` voor die persoon is. Dat houdt één functie gelijk:
`app/people/faces/links.py: sync_links(session, node, person)`. Een rij met `source = 'hand'`
raakt X2 nooit aan.

| Actie | Wat er gebeurt |
|---|---|
| **Ja** (`confirm`, met `person_id` of een nieuwe `name`) | `face_person` wordt `ja`. Een `ja` voor een ander op hetzelfde gezicht wordt `nee`: "nee, dit is Bram" is één stap. Is er nog geen `photo_person`, dan komt er een met `source = 'auto'` en het kader. Is er een zonder kader, dan krijgt die het kader. Een nieuwe naam maakt de persoon via de code van Z6. `face.suggest_*` wordt leeg. `face_owner.verdict_at` wordt nu. |
| **Nee** (`reject`) | `face_person` wordt `nee` voor dat paar. Was het `ja`, dan doet `sync_links` zijn werk. Het voorstel wordt leeg; de volgende ronde stelt dit paar nooit meer voor. |
| **Niet tonen** (`hide`) | `vec`, `suggest_*` en `cluster` worden NULL, `hidden` wordt 1. Een `ja` gaat weg (met `sync_links`), en de afwijzingen ook. Voor vreemden op de achtergrond. Daarna `faces.wipe` met scope `checkpoint` (K1-15). |
| **Ongedaan maken** (`reset`, hooguit 10 minuten na de keuze, ≤ 200 gezichten) | De keuzes voor die gezichten gaan weg, met `sync_links`. Een gezicht dat verborgen was, krijgt zijn vector pas terug na "Opnieuw zoeken" voor die foto. Daarna ook `faces.wipe` met scope `checkpoint` (K1-15). |
| **Een kader met de hand** (Z6, al bestaand of nieuw) | Een gezicht met IoU ≥ 0,5 tegen dat kader wordt meteen `ja`. Zo leveren de personen die Jonas al met de hand benoemde, meteen bekende gezichten. Dan komen er ook meteen voorstellen. |
| **Een persoon zonder kader** (Z6) | Staat er op een foto precies één persoon zonder kader, en vond YuNet precies één gezicht, dan wordt dat gezicht een voorstel met `suggest_why = 'tag'`. Geen `ja`: het gezicht kan ook een ander zijn. |
| **Een persoon van een foto halen** (Z6, `DELETE /files/{id}/people/{person}`) | Haak `person_removed`: de `ja` van die persoon op die node wordt `nee`. |
| **Samenvoegen** (Z6, `POST /people/{id}/merge`) | Haak `persons_merged`, in dezelfde transactie en vóór het wissen van de bron. `ja` van de bron gaat naar het doel; een `nee` van het doel op hetzelfde gezicht vervalt. `nee` van de bron gaat mee, behalve waar het doel `ja` heeft. `suggest_id` gaat naar het doel. Daarna `suggest` na 30 s. |
| **Een persoon verbergen** (Z6) | Geen voorstellen meer naar die persoon. Bevestigde gezichten blijven. |
| **Een persoon wissen** (Z6) | De trigger `person_bd_face` maakt zijn bevestigde gezichten "niet tonen", zonder vector. Zijn `face_person` gaat weg via de cascade. Haak `person_deleted`: `faces.wipe` met `scope = checkpoint`. Het dialoog van Z6 krijgt een zin erbij (X2, "De web"). |
| **Hernoemen** (Z6) | Niets. |
| **Een nieuwe versie** (`FACES_VERSION` hoger) | `faces.plan` met `scope = version`, per eigenaar in stukken. De `face`-rijen met `hidden = 0` gaan weg, met `secure_delete`. `face_scan` wordt `wait`. Bij de nieuwe scan wordt een gezicht met IoU ≥ 0,5 tegen een kader in `photo_person` (hand of auto) meteen `ja`. Een gezicht met IoU ≥ 0,5 tegen een verborgen gezicht wordt meteen weer verborgen, zonder vector. **De afwijzingen gaan verloren**; de bevestigde personen niet. |

**De haken.**

- A0 maakt `app/people/hooks.py` met `person_removed`, `persons_merged` en `person_deleted`.
- In `routers/people.py` van Z6 komen drie aanroepen.
- Zonder aanmelding doen de haken niets, zoals `search.hooks`.

**Een kopie door dezelfde eigenaar.** Dat gaat via `search.hooks.register_photo_copy` [code:
wt-z0api `app/search/hooks.py`]. Twee gevallen:

- **De bron is klaar.** Is de bron `done` op de huidige versie, met dezelfde blob, dan kopieert
  de haak de `face`-rijen (met nieuwe ids), de keuzes en de `face_scan`. Er wordt dan niet
  opnieuw gerekend.
- **De bron is nog niet klaar.** Dan komt de kopie op `wait`.

**Een kopie door een ander.** Daar gaat nooit iets mee (X2, "Privacy").

#### Privacy: de harde regels (Keuze 64, 68, 69)

| Regel | Hoe | Test |
|---|---|---|
| Per gebruiker, standaard uit (een kinderprofiel kan het niet zelf aanzetten, K1-11) | `photo_pref.faces` = `uit` [code: wt-z0api `app/models.py:998`]. `PATCH faces=aan` alleen na het dialoog met uitleg (X2, "De web"). Een gast krijgt `403 guest`. | `test_faces_privacy.py` |
| De beheerder kan het verbieden | Setting `search`, veld `faces_allowed` (standaard `true`). Een verbod zet iedereen op `uit` en zet per eigenaar `faces.wipe` klaar. `PATCH faces=aan` geeft dan `403 faces_forbidden`. Weer toestaan zet niemand vanzelf aan. | `test_faces_privacy.py` |
| "Uit" wist alles, ook de vectoren | Zie "Het wissen" hieronder | `test_faces_off.py`, `test_faces_wipe_bytes.py` |
| Nooit een vector in de API | Geen model in `faces_types.py` heeft een veld voor een vector of een score. De vector gaat ook nooit in een bericht van de worker (X2, "Gezichten vinden"). | `test_faces_no_vector.py`: alle routes, met merktekens in de database |
| Nooit in de logs en gebeurtenissen | `people.changed` heeft geen inhoud [code: wt-z0api `app/search/types.py:286`]. `QUERY_PATHS` verbergt de query string onder `/api/v1/people` [code: wt-z0api `app/main.py`]. Een fout van de worker logt een code, nooit bytes. | `test_faces_no_vector.py` (caplog, events) |
| Een persoon wissen wist zijn koppelingen | `face_person` via de cascade, `photo_person` via Z6. De vectoren van zijn bevestigde gezichten gaan ook weg (`person_bd_face`), zodat hij niet terugkomt als "Wie is dit?". | `test_faces_verdicts.py`, `test_faces_wipe_bytes.py` |
| Versleuteld, met een sleutel per eigenaar (Keuze 68) | `face.vec` is AES-GCM; "uit" wist eerst de sleutel. Een kopie van de database van vóór "uit" heeft dan geen enkele leesbare vector. | `test_faces_crypto.py` |
| Foto's van een ander in jouw map (Keuze 69) | `node_origin`: niet scannen als `created_by` niet de eigenaar is, tenzij die gezichten aan heeft; via een link nooit (`not_own_upload`) | `test_faces_privacy.py`: Bram (uit) uploadt in Eva's map, en een gast uploadt via een link: daarna 0 `face`-rijen |
| Alleen eigen, levende foto's | Schrijven alleen voor de eigenaar, als `faces = 'aan'` en het verbod uit staat. Dat wordt in dezelfde schrijftransactie (`BEGIN IMMEDIATE`) gelezen als het schrijven. Elke lezing gebruikt `own_live`. | `test_faces_scan.py` (uit tijdens een job: niets geschreven), `test_own_live.py` |
| Een kopie bij een ander | Bram kopieert Eva's foto: niets gaat mee. Heeft Bram gezichten aan, dan rekent de worker voor zijn node opnieuw, met zijn eigen keuze. | `test_faces_copy.py` |
| Een publieke link | Er is geen route voor gezichten onder `/s/…`. De publieke viewer heeft geen `PeopleRow`. | `test_faces_privacy.py` |
| Een deling | Bram ziet een gedeelde foto van Eva: `GET /files/{id}/faces` geeft `[]`. Een uitsnede van Eva geeft `404`. Een foto die hij niet mag zien, geeft `404`. | `test_faces_privacy.py` |
| De beheerder ziet alleen aantallen | `/beheer` toont aantallen voor de hele server, nooit per gebruiker, en nooit een gezicht of uitsnede | `test_faces_privacy.py` |
| Reservekopieën | De kopie van het platform wist de tabellen met gezichten, vóór hij klaar is | `test_faces_backup.py` |
| Uitsneden | `GET /people/faces/{id}/crop` maakt een WebP in het renderproces, met `Cache-Control: no-store`. Er komt niets op schijf. Hooguit 20 per seconde per gebruiker (de limiter van de viewer). | `test_faces_api.py` |

**Het wissen.** `app/people/faces/wipe.py` doet dit in de job `faces.wipe` (rij `default`).

**Wanneer de job draait:**

| Scope | Wanneer |
|---|---|
| `all` | "uit", een verbod, een gebruiker die gewist wordt |
| `unnamed` | de knop "Niet benoemde gezichten wissen" |
| `checkpoint` | na het wissen van een persoon |

**Wat de job doet:**

0. Bij `all`: eerst de sleutel van die eigenaar weg (`unlink`, dan `fsync` van de map; Keuze 68).
   Vanaf dan is elke kopie van zijn vectoren onleesbaar.
1. `face_owner.wiping` wordt 1. Dan plant `faces.plan` niets meer voor die eigenaar.
2. `PRAGMA secure_delete = ON` op de verbinding, ook al zet de connect-haak het al.
3. `DELETE` in stukken van 2.000, elk met een commit (zoals Keuze 11):
   - bij `all`: `face_person`, `face`, `face_scan` en `face_owner` van die eigenaar;
   - bij `unnamed`: open gezichten worden verborgen (`vec = NULL`, `hidden = 1`), zodat ze
     niet terugkomen.
4. `PRAGMA wal_checkpoint(TRUNCATE)` op een eigen verbinding met `busy_timeout = 0`, opnieuw met
   backoff (vanaf 50 ms, verdubbelend tot 5 s), tot `busy = 0`, hooguit 5 minuten. Lukt het dan
   nog niet, dan `Defer(60)`. `/beheer` zegt dan "wissen wacht op een lezer". Met de gewone
   `busy_timeout` van 5 s zou elke poging alle schrijvers ±5 s laten wachten [gemeten: K2-08].
5. Klaar: `wiping` wordt 0 (bij `all` is de rij al weg).

Een wisronde die halverwege stopt, gaat bij de start verder (`register_backfill`) en in de
ronde van elk uur.

Ook nodig voor het wissen:

- **De connect-haak** (`app/db.py:29–32`, hotspot van A0) zet op elke verbinding `PRAGMA
  secure_delete = ON` en `PRAGMA temp_store = MEMORY`. Zonder de tweede schrijft SQLite bij een
  sortering zonder index rijen naar een tijdelijk bestand op schijf, buiten de database en de WAL:
  41.232 van de 41.232 schrijfacties bevatten het merkteken; met `MEMORY` waren het er 0 [gemeten:
  K1-03]. `test_db_pragmas.py` eist beide op elke verbinding uit de pool. Debian en Ubuntu bouwen
  SQLite al met `SECURE_DELETE` [gemeten: 3.45.1 en 3.46.1]. Er verandert dus niets aan de snelheid.
  De regel maakt het alleen onafhankelijk van hoe SQLite gebouwd is.
- **Het werk van elk uur** in `jobs.py` (`HOUSEKEEPING` = 3600 s [code: wt-z0api `jobs.py:63`])
  probeert één keer `wal_checkpoint(TRUNCATE)`, op dezelfde manier (eigen verbinding,
  `busy_timeout = 0`). Zo blijft na een gewone verwijdering
  (prullenbak leeg, een node wissen) hooguit ±1 uur iets in de WAL staan, als er geen lezer
  openstaat [niet getest].

**Gemeten.** `wissen.py` maakte 3.000 rijen in WAL-modus. Een derde had een vector met een
merkteken van 16 B, 32 keer per vector: 32.000 merktekens. Die derde werd gewist in stukken van
400 [gemeten: `gezichten/uit/wissen_host.txt` (3.45.1) en `wissen_slim.txt` (3.46.1); beide
gelijk]:

| Manier | Merktekens in `.db` | in `-wal` | Checkpoint (busy, log, klaar) |
|---|---|---|---|
| gewone DELETE, PASSIVE | 0 | 96 | 0, 444, 444 |
| `secure_delete` + TRUNCATE | 0 | 0 | 0, 0, 0 |
| hetzelfde, met een open lezer | 32.000 | 96 | 1, 444, 0 |
| na het sluiten van de lezer, opnieuw TRUNCATE | 0 | 0 | 0, 0, 0 |

Wat dit betekent:

- Een TRUNCATE die niet lukt, laat alles in het bestand staan.
- Daarom probeert de job het opnieuw tot `busy = 0`.
- De test eist 0 in allebei de bestanden.

Een vector op NULL zetten (bij "niet tonen" en bij het wissen van een persoon) is een UPDATE en
geen DELETE. Of `secure_delete` de oude bytes dan ook overschrijft, is [niet getest].
`test_faces_wipe_bytes.py` test dat pad. Faalt hij, dan wordt "niet tonen" een DELETE en een
INSERT zonder vector. Na "niet tonen" en "ongedaan maken" volgt altijd `faces.wipe` met scope
`checkpoint` (K1-15); de test draait die job en eist dan 0 merktekens.

**Reservekopieën** (wijzigt Keuze 52). Het gaat om de snippet met de standaardbibliotheek in
`install.sh` en om `app.cli db-backup` (Z0). Ze gebruiken één tekst. `install.sh` draait die met
het oude image; `db-backup` importeert hem.

1. `Connection.backup` naar `<naam>.part`.
2. Op de kopie: `PRAGMA journal_mode = OFF`. Er komt dan geen journaal met de oude pagina's.
3. Op de kopie: `PRAGMA secure_delete = ON`.
4. `DELETE FROM` `face_person`, `face`, `face_scan`, `face_owner` en `blob_embed` (X3, K1-08),
   voor elke tabel die bestaat.
5. Commit, `fsync`, en `rename` naar de echte naam.

Mislukt een stap, dan mislukt de kopie, en stopt het script (Keuze 52).

Na het terugzetten van een kopie zijn er geen gezichten. Wie gezichten aan had, krijgt een
nieuwe scan (±2,2–2,5 uur voor 55.000 foto's [K2-04]). Bevestigde personen staan in `photo_person`,
en die komen bij de scan terug als `ja` (IoU).

Met de versleuteling vangt een snapshot tussen stap 1 en 4 alleen versleutelde vectoren.

Wat het platform niet kan:

- **Snapshots** van `/data` die Jonas zelf maakt (ZFS, restic) houden de rijen tot die snapshot
  weg is. Na "uit" zijn ze daar onleesbaar, zolang de map met sleutels (`SLEUTELS_ROOT`) niet zelf
  in die snapshots of in de offsite-kopie zit (Keuze 68).
- **Wie de server beheert**, kan bij een lopende app de sleutels lezen.
- **Vrijgegeven blokken.** Wat het bestandssysteem ermee doet, valt buiten het platform; zonder
  sleutel zijn ze onleesbaar.

Dat staat in de README, in het runbook `gezichten.md`, en in één zin op `/beheer`.

#### Achtergrondwerk (Keuze 65)

**De rij.** `jobs.LANES` krijgt `"analyse"`. `active_lanes` zet hem erbij als
`settings.analyse_enabled` aan staat [code: wt-z0api `app/jobs.py:69–81`, `:702`]. Er loopt één
job tegelijk (zoals elke rij).

**De jobs.**

| Job | Rij | Wat |
|---|---|---|
| `faces.plan` | `default` | Zet `face_scan` op `wait`, in stukken van 500 met een commit. Alleen voor eigen, levende foto's (`ftype = 'image'`) van eigenaren met gezichten aan, zonder `wiping`, en alleen foto's die de eigenaar zelf uploadde of een ander met gezichten aan (Keuze 69; anders `skip` met `not_own_upload`). Pas als de backfill van de index klaar is (Keuze 65). Scopes: `owner` (net aangezet), `sha` (een preview is klaar), `sweep` (elk uur: wat ontbreekt, een oude versie, `busy` ouder dan 10 minuten terug naar `wait`), `version`. |
| `faces.scan` | `analyse` | Een batch van 16, om de beurt per eigenaar. Daarna `suggest` voor de nieuwe gezichten per eigenaar. Er is altijd hooguit één `faces.scan` in de rij (dedupe). Hij zet zichzelf opnieuw klaar zolang er werk is. |
| `faces.group` | `analyse` | `suggest` of `full` voor één eigenaar (dedupe per eigenaar en mode) |
| `faces.wipe` | `default` | X2, "Privacy" |

**Hoe een batch wordt samengesteld.**

- Eerst de eigenaar die het langst niet aan de beurt was (`face_owner.served_at`).
- Per eigenaar hooguit 4 foto's, de nieuwste eerst (`prio` = de datum van de tijdlijn).
- Opent de eigenaar een foto die nog wacht in de viewer, dan gaat die naar voren:
  `GET /files/{id}/faces` zet `prio` vooraan, zoals `to_front` bij previews.

**Wanneer een foto in de rij komt.**

1. **Aanzetten.** `faces.plan` met scope `owner`.
2. **Een nieuwe preview.** De preview van 1024 komt pas in de tweede ronde (`preview.large`).
   `previews.announce` loopt alleen na de eerste ronde en na een mislukking [code: wt-z0api
   `app/previews.py:670`, `:834`]. A0 voegt daarom `previews.register_large(fn)` toe (hotspot):
   die loopt na `preview.large`. Beide haken zetten `faces.plan` met scope `sha` klaar. Is de
   1024 er nog niet, dan blijft de foto op `wait` met `no_preview`.
3. **Een kopie.** Zie X2, "Bevestigen".
4. **Elk uur.** De ronde `sweep`, als vangnet: een andere eigenaar, een nieuwe inhoud, of een
   gemiste haak.

**Wijken** (wijzigt Keuze 50).

- **Wanneer.** Vóór de batch en tussen twee foto's controleert de job drie dingen:
  - lopen er ≥ 2 transcodes (`PLATFORM_INDEX_DEFER_TRANSCODES`)?
  - start OnlyOffice?
  - staat het achtergrondwerk op `/beheer` op pauze?

  Zo ja, dan schrijft de job wat klaar is en doet hij `Defer(30)`.
- **De poort.** Het achtergrondwerk loopt binnen 90 s na de laatste transcode weer (§4.5).
- **Zacht draaien.** Het kind `werk` draait met `nice 19`; de container heeft `cpu_shares: 256`
  (K2-07). Met 64 kreeg ook de beeldvraag van X3 maar ±0,12–0,18 core naast de mediaworker
  [berekend: K2-07].

**Fouten.**

| Fout | Wat de job doet | Telt als poging |
|---|---|---|
| `WorkerDown` | `Defer(300)` | nee |
| `WorkerBusy` | `Defer(30)` | nee |
| `model_missing` | `Defer(3600)`, de foto blijft `wait` | nee |
| `unreadable`, `too_big`, `bad_output` | `failed`, geen nieuwe poging | — |
| `timeout` | tot 3 keer, daarna `failed` | ja |
| een crash van de app | `busy` gaat bij de start terug naar `wait` (`jobs.recover`) | nee |

**Gebeurtenissen.** `people.changed` gaat naar de eigenaar, hooguit één keer per 10 s per
eigenaar.

**Schrijven.**

- Per batch één schrijftransactie (`BEGIN IMMEDIATE`): ≤ 16 foto's en ≤ 640 gezichten.
- Eerst leest de transactie de instelling en het verbod.
- Dan controleert hij per node: nog steeds eigen, levend, dezelfde blob, en `face_scan` nog
  `busy` op deze versie. Zo niet, dan gooit hij het resultaat van die node weg.

**Wat de backfill kost.** Met `cpus: 1.0`, uit de preview, met de tijden van "Gezichten vinden":

| Per foto | 100.000 foto's | 55.000 foto's (Jonas) |
|---|---|---|
| **1 gezicht: ±142–164 ms** | **±3,9–4,6 uur** | **±2,2–2,5 uur** |
| elk gezicht extra: +53–61 ms | +1,5–1,7 uur per extra gezicht per foto | +0,8–0,9 uur |

- **Herkomst.** K2-04 (de WebP-decode en 1024 × 768 gemeten); het extra gezicht uit spike B6.
- **Groeperen erbij.** ±40 s per uur bij 60.000 gezichten: ≤ 2% extra.
- **Op een drukke server** duurt het langer: `Defer` en `cpu_shares` geven de voorgrond voorrang.
- **De CPU van Jonas.** `app.cli analyse-bench` meet het op de LXC: 200 gemaakte foto's door de
  echte worker, plus SFace op 200 uitgelijnde vlakken. Hij geeft ms per foto en per gezicht, en
  de tijd voor zijn eigen aantal foto's. Het runbook noemt hem; `/beheer` toont de uitkomst.

#### API (onder `/api/v1`)

De typen staan in `app/people/faces_types.py` (A0, bevroren). De velden in de typen van Z0 zet
Z0b (Keuze 55). De routes staan in
`app/routers/people_faces.py`. Die router komt vóór `routers/people.py`, zodat
`/people/faces/...` nooit bij `/people/{person_id}` belandt. Elke nieuwe route krijgt een rij in
`ENDPOINTS` van `test_api_stubs.py`. Z0b zet ze als stub (`501 not_built("X2")`).

| Route | Wie | Wat |
|---|---|---|
| `GET /me/photo-settings` | ingelogd | erbij: `faces` (`aan`/`uit`), `faces_forbidden`, `faces_available` |
| `PATCH /me/photo-settings` `{faces}` | ingelogd, geen gast | `aan`: alleen als de analyse beschikbaar is en het niet verboden is; zet `faces.plan` klaar. `uit`: zet `faces.wipe` klaar en antwoordt meteen; de status toont "wissen". De route is van Z6: X2 meldt zich aan met `prefs.register_switch("faces", …)`, en de regel staat in `prefs.check_switch` (na de review van Z0b). |
| `GET /people/faces/status` | eigenaar | `{setting, forbidden, analyse: ok/down/models_missing/off, photos: {total, done, waiting, failed, skipped}, faces: {found, named, open, groups}, wiping}` |
| `GET /people/faces/suggestions?person=&cursor=&limit=60` | eigenaar | zonder `person`: per persoon `{person, count, sample[6]}`; met `person`: de gezichten, beste eerst |
| `GET /people/faces/groups?cursor=` | eigenaar | `[{key: "<run>:<cluster>", count, faces[12]}]`, de grootste eerst |
| `GET /people/faces/groups/{key}?cursor=` | eigenaar | alle gezichten van één groep, per 60 |
| `POST /people/faces/confirm` `{faces[≤200], person_id \| name}` | eigenaar | X2, "Bevestigen" |
| `POST /people/faces/reject` `{faces[≤200], person_id}` | eigenaar | X2, "Bevestigen" |
| `POST /people/faces/hide` `{faces[≤200]}` | eigenaar | X2, "Bevestigen" |
| `POST /people/faces/reset` `{faces[≤200]}` | eigenaar | ongedaan maken, ≤ 10 minuten |
| `POST /people/faces/wipe-unnamed` | eigenaar | `faces.wipe` met scope `unnamed`; `{queued: n}` |
| `GET /files/{id}/faces` | eigenaar van de node | `[{id, box, person \| null, suggest: {person_id, name, sure, why} \| null}]`; een ander die de foto mag zien: `[]`; anders `404` |
| `POST /files/{id}/faces/rescan` | eigenaar | de rijen van die node met `hidden = 0` weg, dan `wait` vooraan; hooguit 20 per uur |
| `GET /people/faces/{id}/crop?size=96\|192` | eigenaar | WebP, `Cache-Control: no-store`. Met de inlog als Bearer-header, zoals elke route hier: de web haalt hem met een fetch (`faceCrop`) en maakt er een object-URL van, geen adres in een `<img>` (na de review van Z0b, veilig V2) |

Een gezicht naar buiten (`FaceOut`) bevat: `id`, `node_id`, `box` (`x`, `y`, `w`, `h`),
`person`, `suggest`. Daarbij:

- **`sure`** is `true` als de score ≥ `t_suggest + 0,10` is. De score zelf gaat nooit naar
  buiten.
- **`why`** is `sim` of `tag`.

**Foutcodes** (catalogus `web/src/i18n/errors/faces.ts`):

| Code | Status | Wanneer |
|---|---|---|
| `faces_forbidden` | 403 | de beheerder verbiedt het |
| `faces_off` | 409 | de eigenaar heeft gezichten uit |
| `faces_unavailable` | 409 | de stand is `off` |
| `faces_too_many` | 422 | meer dan 200 gezichten in één verzoek |
| `faces_gone` | 409 | een gezicht of groep bestaat niet meer (nieuwe scan of ronde) |
| `guest` | 403 | bestaat al |

**Uitbreidingen van de typen van Z0** (Z0b zet ze vast, Keuze 55).

- `AdminSearch.faces`: het blok van Keuze 67.
- `AdminSearchPatch.faces_allowed`.
- `RebuildWhat` krijgt `"faces"`: de mislukte foto's opnieuw, en de versie nalopen.
- Geen `FailedItem` voor gezichten: `AdminSearch.failed_by_code` geeft alleen aantallen per code
  (K2-21).
- `SearchProgressEvent.what` krijgt `"faces"`.
- `PhotoSettings` en `PhotoSettingsPatch` krijgen `faces`.

`extra=forbid` blijft.

#### De web (Keuze 66)

**Bestanden.**

- `web/src/photos/faces/**`, lazy. De plekken in de bestanden van Z4 en Z6 zette Z0b (na de
  review, contract F3): `PeopleFaces.tsx` (op `/fotos/personen`), `PersonFaces.tsx` (de pagina
  van een persoon), `FacesRow.tsx` (het infopaneel, na Personen, alleen bij een foto) en
  `FacesSwitch.tsx` (Account → Foto's). X2 vult ze; de bestanden van Z4 en Z6 blijven dicht.
- Het i18n-gebied `photos_faces`, en de catalogus `web/src/i18n/errors/faces.ts`.
- `budget.mjs`: `ZOEK_MODULE` en `ZOEK_PAGES` krijgen de chunk van gezichten (≤ 12 kB gzip
  [schatting]).
- Niets in de initiële JS; daar is nog 2.698 B ruimte.
- De toetsenbordlijst: `docs/fase-6/toetsenbord-x2.md`.

**Waar het staat.**

- **Bij de foto-instellingen (Z6):** de schakelaar "Gezichten herkennen in mijn foto's". Staat
  hij uit, dan staat er bovenaan `/fotos/personen` één regel met "Lees wat dat betekent". Een
  kinderprofiel (later, stappenplan) kan hem niet zelf aanzetten (K1-11).
- **`/fotos/personen`, het blok "Is dit …?"** Een kaart per persoon met voorstellen: zes
  uitsneden, "Is dit Anna? 12 foto's". Open je de kaart, dan zie je een raster van alle
  voorstellen voor Anna.
- **`/fotos/personen`, het blok "Wie is dit?"** Een kaart per groep (≥ 3 gezichten). Open je de
  kaart, dan zie je een raster. Alles is eerst geselecteerd; Spatie haalt er een uit. Je typt
  een naam, met suggesties uit je eigen personen of "Nieuwe persoon: …". Alleen de gezichten die
  je zag en die geselecteerd zijn, gaan naar `confirm`. Nooit "de hele groep".
- **De pagina van een persoon:** het blok "Misschien ook Anna".
- **De viewer, het infopaneel (`PeopleRow`, Z6):**
  - alleen voor de eigenaar, en alleen bij foto's;
  - de kaders van de gevonden gezichten, met een naam of "?";
  - "?" opent "Wie is dit?", met het voorstel ingevuld maar niet bevestigd.
- **Status.** Een regel bovenaan `/fotos/personen` met de voortgang. Hij vraagt elke 10 s naar
  de status, zolang hij te zien is en er nog werk is, en verder bij `people.changed`.

**Toetsenbord** (scope `faces`):

| Toets | Wat |
|---|---|
| pijlen | naar het volgende of vorige gezicht in het raster (roving tabindex) |
| J (of Y in het Engels) | ja |
| N | nee |
| V (of H in het Engels) | niet tonen |
| Spatie | selecteren of niet (in "Wie is dit?") |
| Enter | de foto openen in de viewer |
| Ctrl+Z | de laatste keuze ongedaan maken (zolang de melding staat) |
| Escape | het raster sluiten; de focus gaat terug naar de kaart |

Na J, N of V gaat de focus naar het volgende gezicht. Een melding met `aria-live` zegt wat er
gebeurde.

**Teksten** (Nederlands; het Engels met dezelfde betekenis):

| Plek | Tekst |
|---|---|
| schakelaar | Gezichten herkennen in mijn foto's |
| dialoog bij aanzetten | Het platform zoekt gezichten in je eigen foto's en stelt voor wie het is. Daarvoor maakt het van elk gezicht een reeks getallen. Zo'n reeks is biometrische data, ook van mensen op je foto's die zelf niets kozen. De getallen staan versleuteld op deze server, alleen bij jouw foto's. Andere gebruikers zien ze nooit. Wie de server beheert, kan er technisch bij. Reservekopieën van het platform bevatten ze niet; snapshots die de beheerder zelf maakt wel, versleuteld. Zet je dit uit, dan wissen we de gezichten, de getallen en de sleutel. Daarna zijn ook die kopieën niet meer te lezen. Gebruik je het platform voor anderen dan je gezin, lees dan eerst de uitleg in de README. |
| knoppen | Aanzetten · Annuleren |
| dialoog bij uitzetten | Gezichten uitzetten? We wissen alle gevonden gezichten en hun getallen. Personen die je zelf benoemde of bevestigde, blijven op je foto's staan. |
| knoppen | Uitzetten en wissen · Annuleren |
| status | Gezichten zoeken: 1.234 van 55.000 foto's · Klaar: 3.456 gezichten in 2.100 foto's · Wacht op de analyse · De beheerder heeft gezichtsherkenning uitgezet · Gezichten worden gewist … |
| niet geïnstalleerd | Gezichtsherkenning is op deze server niet geïnstalleerd. |
| voorstel | Is dit Anna? · Ja · Nee · Niet tonen |
| groep | Wie is dit? · Naam · Nieuwe persoon: … · 8 van 9 geselecteerd · Bevestigen |
| persoonpagina | Misschien ook Anna |
| melding | 12 gezichten bevestigd als Anna. · Ongedaan maken |
| knop | Niet benoemde gezichten wissen |
| dialoog bij die knop | We wissen 2.345 gezichten die je niet benoemde, met hun getallen. Ze komen niet terug, ook niet na een nieuwe zoekronde. |
| dialoog van Z6 bij persoon wissen (erbij) | De gezichten die je als Anna bevestigde, worden gewist en niet opnieuw voorgesteld. |

#### `/beheer` (Keuze 67)

Het blok "Gezichten" staat in de tab "Zoeken en foto's" (`web/src/admin/FacesBlock.tsx`, in de
chunk van de tab).

| Regel | Wat |
|---|---|
| Analyse | `ok` / `down` met de reden / `models_missing` met de naam / `off` ("`ANALYSE=uit`") |
| Modellen | per model: naam, versie, sha256 ok |
| Grenzen | `cpus` en `mem` van de container, of "zonder grenzen" (geen cgroup-controllers) |
| Gebruikers met gezichten aan | een aantal, geen namen |
| Rij `analyse` | wachtend, per uur gedaan, mislukt (per code), wacht op (transcodes, OnlyOffice, pauze) |
| Totaal | gezichten, waarvan benoemd; ±ruimte in de database (aantal × 0,8 kB) |
| Snelheid | de laatste uitkomst van `analyse-bench`, en de geschatte tijd voor wat er wacht |
| Wissen | "wissen wacht op een lezer", als dat zo is |
| Schakelaar | "Gezichtsherkenning toestaan" (aan). Uitzetten vraagt: "Alle gezichten en getallen van alle gebruikers worden gewist. Wie het aan had, staat daarna op uit." |
| Kwaliteit | de laatste uitkomst van `app.cli analyse-kwaliteit` (Vraag 8), of "niet gemeten" |
| Zin | "Reservekopieën die het platform maakt, bevatten geen gezichten. Maak je zelf snapshots van /data (ZFS, restic), dan staan ze daar versleuteld in. Na 'uit' zijn ze daar niet meer te lezen, zolang de map met sleutels niet in die snapshots zit." |
| Knoppen | "Mislukte opnieuw" (`RebuildWhat = faces`); pauzeren is de knop die er al is |

Er zijn twee commando's:

- **`app.cli analyse-status [--wait N]`** gebruikt `install.sh`. Hij geeft exit 0 bij `ok`.
- **`app.cli analyse-bench`** staat in het runbook.

#### Levensloop (rijen voor §3.9)

| Gebeurtenis | Gezichten | Wat de eigenaar ziet | Test |
|---|---|---|---|
| upload door een ander in jouw map, of via een link | `skip` met `not_own_upload`, tenzij die ander gezichten aan heeft (Keuze 69) | niets | `test_faces_privacy.py` |
| upload van een foto (gezichten aan) | `wait` na de preview; `done` na de batch | voorstellen na seconden tot minuten | `test_faces_scan.py` |
| kopie door dezelfde eigenaar | rijen en keuzes gekopieerd als de bron klaar is; anders `wait` | meteen | `test_faces_copy.py` |
| kopie door een ander | niets; met zijn eigen gezichten aan een eigen scan | Bram ziet niets van Eva | `test_faces_copy.py` |
| andere eigenaar | `node_au_owner_face` wist `face` en `face_scan` | de oude eigenaar niets meer; de nieuwe na zijn eigen scan | `test_faces_lifecycle.py` |
| inhoud vervangen | `node_au_blob_face` wist; kaders in `photo_person` leeg; nieuwe scan na de preview | personen blijven, zonder kader | `test_faces_lifecycle.py` |
| prullenbak | rijen blijven; `own_live` laat ze weg | weg uit voorstellen, groepen en tellingen | `test_faces_lifecycle.py`, `test_own_live.py` |
| terug uit de prullenbak | rijen zijn er weer | terug | `test_faces_lifecycle.py` |
| definitief wissen, gebruiker wissen | cascade, `secure_delete`; checkpoint binnen het uur | weg | `test_faces_lifecycle.py`, `test_faces_wipe_bytes.py` |
| gezichten "uit" of verbod | de sleutel weg, dan `faces.wipe` met scope `all` | meteen "wissen", dan leeg | `test_faces_off.py` |
| "niet tonen" of "ongedaan maken" | `vec` NULL; `faces.wipe` met scope `checkpoint` (K1-15) | weg uit voorstellen en groepen | `test_faces_wipe_bytes.py` |
| persoon wissen | zijn gezichten "niet tonen", zonder vector | komt niet terug | `test_faces_verdicts.py` |
| nieuwe versie van de modellen | rijen weg, opnieuw zoeken, opnieuw koppelen via de kaders | bevestigde personen blijven; afwijzingen niet | `test_faces_version.py` |
| reservekopie terugzetten | geen gezichten; nieuwe scan | bevestigde personen blijven | `test_faces_backup.py` |

#### Tests

##### Testen zonder echte gewichten

In de sandbox zijn geen foto's van echte gezichten. De modellen zijn er wel te downloaden, van
media.githubusercontent.com. Daarom zijn er vier lagen. De tests van laag 2–4 importeren numpy of
onnxruntime; ze staan daarom in `api/tests_analyse/`, buiten de `testpaths` van de job `api`. Anders
geeft de job `api` (zonder de extra) een fout bij het verzamelen, nog vóór de markers tellen
[gemeten: K2-10]. De job `api-analyse` draait die map, en telt het aantal tests.

| Laag | Wat | Waar het draait |
|---|---|---|
| 1. De nep-worker | `api/tests/analyse/fake_worker.py` spreekt het echte protocol, met de echte handdruk. `faces`: hij zoekt met Pillow gekleurde schijven in de preview. Elke kleur is een identiteit; de vector is een vaste eenheidsvector per kleur (seed), plus een kleine ruis per node. `group`: de referentie in puur Python (`api/tests/analyse/groep_ref.py`, ≤ 2.000 gezichten). Zonder numpy. | job `api`, job `e2e`, de sandbox |
| 2. De echte worker met nepmodellen | `app/analyse/onnxw.py` (uit de spike; alleen de standaardbibliotheek) bouwt de modellen in de test (`fake_models.py`). **Nep-YuNet:** dezelfde in- en uitgangen als YuNet (dynamische `height` en `width`, 12 uitgangen voor de stappen 8, 16 en 32). De score komt uit een `AveragePool` op kanaal 0 van de invoer. Een helder vlak in dat kanaal is dus een "gezicht" op een vaste plek, en de kaders en punten zijn vaste waarden. **Nep-SFace:** `[1,3,112,112]` → `[1,128]` met vaste willekeurige gewichten (B2). Zo test de echte keten het decoderen, de kaders, NMS, de minimale grootte, het maximum van 40, het uitlijnen, het normaliseren, de memfd, de grens per foto en de kleurvolgorde. Twee keer bouwen geeft dezelfde sha256 [gemeten: spike B2]. | job `api-analyse`, de sandbox (met de venv van de spike) |
| 3. Echte modellen, zonder echte gezichten | Downloaden volgens `models.lock`, de sha256 controleren, laden in onnxruntime 1.31.1. Op gemaakte beelden (ruis, een verloop, een getekend landschap): 0 gezichten, de vormen van de uitvoer kloppen, en de tijd per foto wordt gemeld. | de sandbox en job `api-analyse` |
| 4. Echte modellen met echte gezichten | Standaard (Vraag 8): `app.cli analyse-kwaliteit` op de LXC, tegen de eigen "ja" en "nee" van Jonas; alleen getallen, niets verlaat de LXC (K1-10). Met ja op Vraag 8 ook de set van NASA in CI. Hier worden de kwaliteit, de kleurvolgorde en de drempels gemeten. | de LXC; met ja ook job `api-analyse` |

**De kleurvolgorde** is met één gezicht niet te zien (cosinus 0,895 tegen 0,897) [gemeten:
spike B6]. Daarom:

- legt laag 2 de volgorde vast die de keten gebruikt (het vlak moet in het juiste kanaal staan);
- beslist laag 4 welke volgorde de juiste is.

**De set van NASA** (alleen met ja op Vraag 8).

- **De bron.** Officiële portretten en foto's van bemanningen.
- **De namen.** NASA noemt ze in de beschrijving van links naar rechts. Die volgorde, gelegd op
  de x van de gevonden gezichten, geeft het label.
- **Wat niet telt.** Een foto waar het aantal gezichten niet past bij het aantal namen, telt
  niet mee; het aantal zulke foto's staat in het rapport.
- **Hoe groot.** De set moet ≥ 80 gelabelde gezichten van ≥ 8 personen hebben.
- **Het lockbestand.** `api/tests/analyse/data/fotos.lock` heeft per foto de URL, de sha256 en
  de namen van links naar rechts. Geen beelden en geen vectoren.
- **Hoe het lockbestand ontstaat.** De sandbox kan NASA niet bereiken [gemeten: spike]. Zolang
  `fotos.lock` ontbreekt, zoekt de stap `maak_set.py` in `api-analyse` via `images-api.nasa.gov`
  kandidaten. Hij zet het voorstel voor `fotos.lock` (sha256 en naam van het bestand per regel,
  zonder namen van mensen) in het blok `=== kort ===` en in het verslag via `upload-artifact`
  (K2-20), en de poort faalt met "fotos.lock ontbreekt". X2b controleert de tekst en commit hem. Dat
  is één rode run, zonder nieuwe workflow en zonder nieuwe action.

##### De tests, per bestand

| Bestand | Laag | Wat het eist |
|---|---|---|
| `api/tests/analyse/test_analyse_wire.py` | 1 | de handdruk met het label van analyse; een bewijs van media wordt geweigerd; een verkeerde sleutel; een sleutel gelijk aan die van media of aan `secret_key` (`key_reused`); `MAX_MESSAGE`; een onbekende op geeft `bad_request`; de ops van X3 geven `not_built` |
| `api/tests/analyse/test_analyse_monitor.py` | 1 | de standen en hun redenen; `Defer(300)` en `Defer(30)` zonder poging; `analyse_problem()` in het Nederlands; `off` zonder `PLATFORM_ANALYSE_ENABLED` |
| `api/tests/analyse/test_no_numpy_in_app.py` | — | AST: geen `import numpy` of `onnxruntime` buiten `app/analyse/worker/`; `python -I -c "import app.main, app.cli"` laadt numpy niet |
| `api/tests/analyse/test_fake_analyse_worker.py` | 1 | de nep-worker volgt `analyse_contract.py`; hetzelfde contract draait in laag 2 tegen de echte worker |
| `api/tests/test_compose_analyse.py` | — | BOOT gelijk aan `app/analyse/runtime.BOOT`; geen sleutel in `command`; `user: analyse`; `network_mode: none`; `read_only`; `cap_drop`; `pids_limit`; de modellen `:ro`; de grenzen in het eigen bestand |
| `api/tests/search/test_migration_0010.py` (aangepast, K2-16) | — | het patroon van Z0: `upgrade('0010')` en eist `['0010']`; de head testen 0011 en 0012 |
| `api/tests/people/faces/test_migration_0011.py` | — | heen en terug met data, ook `node_origin`; de tabellen en indexen; het plan gebruikt `ix_face_open` en `ix_face_scan_todo`; na terug 0 merktekens |
| `api/tests/search/test_schema_triggers.py` (uitgebreid) | — | `TRIGGERS` (0010 + 0011 + 0012) is gelijk aan de migraties (K2-17) |
| `api/tests/test_db_pragmas.py` | — | elke verbinding uit de pool heeft `secure_delete` 1 en `temp_store` 2 (K1-03) |
| `api/tests/people/faces/test_faces_crypto.py` | 1 | `face.vec` is 540 B en niet gelijk aan de vector; de sleutel per eigenaar; na "uit" is de sleutel weg en geeft een kopie van de database van vóór "uit" geen leesbare vector; een rij die niet te ontsleutelen is, telt als gewist |
| `api/tests/people/faces/test_faces_wipe_latency.py` | 1 | een upload tijdens het wissen met een open lezer wacht ≤ 100 ms extra (K2-08) |
| `api/tests/people/faces/test_faces_scan.py` | 1 | plannen; om de beurt (≤ 4 per eigenaar), nieuwste eerst, de viewer vooraan; `no_preview` → `wait`, dan na `preview.large` klaar; `too_small` → `skip`; alleen foto's; `unreadable` en `bad_output` zonder nieuwe poging; `timeout` 3 keer; `Defer` zonder poging; `busy` → `wait` na een crash; "uit" tijdens een job: niets geschreven |
| `api/tests/people/faces/test_faces_group_app.py` | 1 | de vorm van de memfd; een uitvoer met een verkeerde grootte wordt geweigerd; afwijzingen en verborgen personen zitten er niet in; wachten 30 s na de laatste keuze; hooguit één keer per uur `full`; `run` en `409 faces_gone` |
| `api/tests/people/faces/test_faces_verdicts.py` | 1 | ja met een bestaande en een nieuwe naam; nee; niet tonen; ongedaan maken; "nee, dit is Bram"; ≤ 200; `sync_links`; IoU met een kader met de hand; `suggest_why = tag`; de drie haken van Z6; persoon wissen geeft "niet tonen" zonder vector |
| `api/tests/people/faces/test_faces_off.py` | 1 | na "uit": 0 rijen in `face`, `face_person`, `face_scan` en `face_owner` voor die eigenaar; personen en `photo_person` blijven; andere eigenaren ongemoeid; twee keer is hetzelfde; een herstart halverwege maakt het af |
| `api/tests/people/faces/test_faces_wipe_bytes.py` | 1 | vectoren met een merkteken. Na "uit", "niet benoemde wissen", persoon wissen, "niet tonen", definitief wissen en terugmigreren: 0 merktekens in `platform.db` en `-wal`. Met een open lezer probeert de job het opnieuw tot hij lukt. `PRAGMA secure_delete` is 1 op elke verbinding uit de pool. |
| `api/tests/people/faces/test_faces_no_vector.py` | 1 | elke route (uit OpenAPI, ook `/beheer`, `/viewer` en `/public`) met merktekens in de database: de bytes, base64, hex en de rij getallen komen nergens voor; ook niet in caplog en in gebeurtenissen |
| `api/tests/people/faces/test_faces_privacy.py` | 1 | de wereld: Eva, Bram, een gast, de beheerder, een publieke link. Bram krijgt `[]` of `404`; een uitsnede van een ander `404`; geen route onder `/s/`; de beheerder alleen aantallen; een gast `403 guest`; een verbod wist iedereen en geeft `403 faces_forbidden`; weer toestaan zet niemand aan |
| `api/tests/people/faces/test_faces_copy.py` | 1 | een kopie door dezelfde eigenaar zonder scan; een kopie door Bram zonder iets; Bram met gezichten aan krijgt een eigen scan |
| `api/tests/people/faces/test_faces_version.py` | 1 | `LOCK_SHA` volgt `models.lock`; een nieuwe versie: opnieuw zoeken, `ja` terug via de kaders, verborgen blijft verborgen, afwijzingen weg |
| `api/tests/people/faces/test_faces_lifecycle.py` | 1 | de rijen van X2, "Levensloop" |
| `api/tests/people/faces/test_faces_backup.py` | 1 | de kopie van `db-backup` en van de snippet van `install.sh`: 0 rijen in de tabellen met gezichten, 0 merktekens, geen `-journal` en geen `-wal`; de database die draait, is niet veranderd |
| `api/tests/people/faces/test_faces_api.py` | 1 | de contracten en codes; de volgorde van de routers; `no-store` op de uitsnede; 20 per seconde; "opnieuw zoeken" 20 per uur |
| `api/tests/search/test_own_live.py` (uitgebreid) | — | `routers/people_faces.py` in de lijst |
| `api/tests/test_api_stubs.py` (uitgebreid) | — | een rij per nieuwe route |
| `api/tests_analyse/test_worker_faces.py` | 2 | de echte worker met nepmodellen: 1024 × 683, 683 × 1024 en 1023 × 767 werken (opvullen tot 32, K2-05); kaders goed omgerekend; NMS bij twee vlakken die overlappen; < 40 px valt af; > 40 gezichten worden er 40; de kleurvolgorde vast; de vector is genormaliseerd; de vectoren in de memfd, niet in de JSON; de grens per foto (`terminate`, daarna `SIGKILL` en een nieuw kind); de limiet op pixels; geen beeld → `unreadable` |
| `api/tests_analyse/test_worker_group.py` | 2 | numpy geeft dezelfde groepen en voorstellen als `groep_ref.py` op 2.000 synthetische gezichten; alleen wederzijdse buren; een keten A–B–C waar A en C niet lijken, blijft gesplitst; afwijzingen; de marge; twee keer is hetzelfde |
| `api/tests_analyse/test_group_perf.py` | 2 | **poort**: 60.000 synthetische gezichten, één core, één thread, in een nieuw proces: `full` ≤ 60 s en maxrss ≤ 400 MB; zuiverheid ≥ 0,99 en recall ≥ 0,95 op de synthetische set |
| `api/tests_analyse/test_worker_sandbox.py` | 2 | de sleutel is na de start weg uit `os.environ`; dumpable is 0 in pid 1 en in elk kind, vóór het eerste fd; een tweede proces van uid 10003 krijgt `EACCES` op `/proc/<kind>/fd` (K1-04); het kind heeft geen sleutel in zijn omgeving; een zelftest die faalt (ook `CapEff` ≠ 0 of `NoNewPrivs` ≠ 1), geeft `RefusingWorker`; een WebP van 6 MP geeft `too_big`; een kind dat te veel geheugen pakt, gaat weg zonder dat de worker valt (K1-07); een uitzondering met een vector met merkteken en de vraag `geheimwoord7`: niets daarvan in de vastgelegde stderr (K1-16) |
| `api/tests_analyse/test_analyse_kinderen.py` | 2 | een beeldvraag tijdens een groepsronde krijgt geen `busy`; `cancel` breekt een opdracht af; `werk` start opnieuw na 8 batches (K2-02, K1-04) |
| `api/tests_analyse/test_analyse_contract_real.py` | 2 | `analyse_contract.py` tegen de echte worker |
| `api/tests_analyse/test_models_real.py` | 3 | download met sha256; laden met `CPUExecutionProvider`; 0 gezichten op gemaakte beelden; de vormen; de tijd per foto (rapport) |
| `api/tests_analyse/test_faces_quality.py` | 4 | **poorten** op de set van NASA: zie hieronder |
| `infra/single/tests/test_analyse_container.sh` | container | uid 10003; alleen `lo`; `touch /x` faalt; `CapEff` is 0; `NoNewPrivs`; `pids_limit`; `/models` alleen-lezen; `/data` leeg; zonder sleutel exit 2; de handdruk met de goede sleutel ok en met een verkeerde niet; `/proc/1/environ` niet te lezen voor uid 10003; de grenzen in `docker inspect` (als ondersteund); `healthy` |
| `infra/single/tests/test_install_analyse.sh` | infra | een symlink die uid 10001 plant: `install.sh` weigert, zonder het doel aan te raken (K1-06); de modellen in `MODELLEN_ROOT`, nooit onder `$DATA_ROOT`; de sleutel één keer, nooit vervangen; modellen van een lokale HTTP-server met nepbestanden en een eigen lock; een verkeerde sha256 wordt niet geïnstalleerd en is niet fataal; `--max-filesize`; een tweede run slaat geldige modellen over; `ANALYSE=uit`: geen download, geen container, `--remove-orphans` haalt hem weg; `--force-recreate` als een model verandert; de uitvoer van `analyse-status`; terug naar 0.8.0 (`db-downgrade 0010`: de tabellen van 0011 en 0012 weg, de sleutels en modellen blijven) en weer vooruit (§3.10) |
| `web/src/photos/faces/faces.test.ts` | web | toetsen (pijlen, J/Y, N, V/H, Spatie, Ctrl+Z, Escape); alleen gezichten die te zien en geselecteerd zijn, gaan naar `confirm`; de teksten in nl en en; de status |
| `e2e/test_zoek_gezichten.py` (marker `zoek`, nep-worker) | e2e | alleen het toetsenbord: aanzetten (met het dialoog); foto's met schijven uploaden; eerst geen "Is dit …?"; één foto met de hand benoemen met een kader (Z6); "Is dit Anna?" komt; J; zoeken op "anna" vindt ze; N; "Wie is dit?" → naam → bevestigd; uitzetten → 0 gezichten (API), Anna blijft op de bevestigde foto's; Bram ziet niets |
| `e2e/test_zoek_gezichten_echt.py` (marker `analyse`) | container | de echte containers, de echte modellen, geen netwerk: een scan loopt van begin tot eind, de status wordt "klaar"; een gemaakte foto zonder gezicht geeft 0 gezichten; `docker logs` bevat geen merkteken |
| `api/tests/people/faces/test_faces_perf.py` (job `zoek-perf`) | perf | **poort**: 60.000 gezichten in de database; `suggestions`, `groups` en `/files/{id}/faces` hebben p95 ≤ 100 ms; `status` p95 ≤ 50 ms |

##### Harde poorten

| Poort | Grens | Gemeten of verwacht |
|---|---|---|
| groeperen van 60.000 synthetische gezichten (de grens van een ronde) | ≤ 60 s en ≤ 400 MB maxrss | ±38–40 s (buren, groepen en voorstellen) en 285 MB [gemeten] |
| bytes na wissen | 0 merktekens in `.db` en `-wal` | 0 [gemeten, `wissen.py`] |
| een vector naar buiten | 0 in elke route, log en gebeurtenis | — |
| gezichten vinden (LXC, of met ja op Vraag 8 in CI) | ≥ 95% van de portretten heeft precies het goede gezicht gevonden | [niet getest] |
| zuiverheid van groepen (LXC, of met ja op Vraag 8) | ≥ 0,9 | [niet getest] |
| recall van groepen (LXC, of met ja op Vraag 8) | ≥ 0,8 | [niet getest] |
| precisie van voorstellen (LXC: tegen de eigen "ja" en "nee"; met ja op Vraag 8 ook in CI) | ≥ 0,95 | [niet getest] |
| kleurvolgorde (LXC, of met ja op Vraag 8) | dezelfde persoon minus verschillende personen, gemiddelde cosinus: met de gekozen volgorde ≥ 0,05 hoger dan met de andere | [niet getest] |
| snelheid op de runner | ≥ 250 foto's per minuut per core met één gezicht | ±365–420 [berekend uit K2-04] |
| routes van gezichten bij 60.000 | p95 ≤ 100 ms | [niet getest] |
| image | ≤ +160 MB | +118–141 MB [gemeten: B1 en de proef met `--target`] |
| initiële JS | 0 B erbij | — |
| §4.5 met X2 aan | dezelfde poorten als zonder | [niet getest] |

**De drempels.**

- `test_faces_quality.py` meldt de zuiverheid, de recall en de precisie voor `t_link` en
  `t_suggest` van 0,35 tot 0,65.
- X2b zet de waarden die de poorten halen in `app/people/faces/drempels.py`. De poort test
  precies die waarden.
- Verandert een drempel, dan gaat `FACES_VERSION` omhoog.
- 0,50 en 0,45 zijn tot dan voorlopig.

##### Wat er gemeten wordt (rapport)

- de tijd per foto en per gezicht met de echte modellen: in de sandbox (gemaakte foto's), in CI,
  en op de LXC (`analyse-bench`);
- de maxrss van het kind, bij `faces` en bij `full`;
- de groei van het image (de job `images`);
- §4.5 stap 2 met `analyse` vol (5.000 foto's): in de integratie in de sandbox (echte modellen,
  gemaakte foto's), en op de LXC via het runbook;
- de backfill op de LXC: foto's per uur op `/beheer`, naast de schatting van X2, "Achtergrondwerk".

**Klaar als:**

- [ ] `uv sync --frozen` zonder extra installeert geen numpy en geen onnxruntime;
      `test_no_numpy_in_app.py` is groen.
- [ ] In het image vindt uid 10001 en 10002 numpy en onnxruntime niet (`find_spec` is `None`), en
      uid 10003 wel (`numpy.ndarray` bestaat).
      Het image is ≤ 160 MB groter (`images`).
- [ ] `test_analyse_container.sh` is groen: uid 10003, geen netwerk, `read_only`, geen
      capabilities, sleutel nodig, handdruk ok, `/proc/1/environ` dicht, elk kind niet-dumpbaar,
      `docker logs` zonder merkteken.
- [ ] `install.sh`: de modellen met sha256 in `MODELLEN_ROOT`, niet fataal, een geplante symlink
      wordt geweigerd, en `ANALYSE=uit` werkt (`test_install_analyse.sh`).
- [ ] `/beheer` toont de juiste stand bij `ok`, `down` (elke reden), `models_missing` en `off`
      (`test_analyse_monitor.py`, `e2e`).
- [ ] Migratie 0011 werkt heen en terug met data, en de triggers zijn gelijk aan `schema.py`.
- [ ] De lagen 1 en 2 zijn groen: scan, groeperen, keuzes, levensloop, kopieën en versie.
- [ ] Privacy: `test_faces_off.py`, `test_faces_wipe_bytes.py` (0 merktekens),
      `test_faces_no_vector.py`, `test_faces_privacy.py` (ook Keuze 69), `test_faces_crypto.py`,
      `test_faces_backup.py` en `test_db_pragmas.py` zijn groen.
- [ ] Poort: 60.000 gezichten groeperen in ≤ 60 s en ≤ 400 MB.
- [ ] De kwaliteit op echte gezichten: `analyse-kwaliteit` heeft op de LXC gedraaid (of, met ja op
      Vraag 8, de poorten in CI), en de drempels staan in `drempels.py`. Tot dan staat in de README
      en op `/beheer` dat de kwaliteit niet gemeten is.
- [ ] `e2e/test_zoek_gezichten.py` (alleen het toetsenbord) is groen, en
      `docs/fase-6/toetsenbord-x2.md` is af.
- [ ] `e2e/test_zoek_gezichten_echt.py` is groen in `analyse-image`.
- [ ] `test_faces_perf.py` haalt p95 ≤ 100 ms in `zoek-perf`.
- [ ] De initiële JS is niet gegroeid; de chunk van gezichten blijft binnen zijn begroting.
- [ ] De belastingtest van §4.5 met X2 aan houdt de poorten (integratie).
- [ ] `analyse-bench` heeft op de LXC gedraaid, of het runbook zegt hoe Jonas hem draait.
- [ ] De documenten zijn bijgewerkt: README (wat er bewaard wordt, de toestemming, de
      versleuteling, de reservekopieën en snapshots, de AVG, de bronnen en licenties, de vijf
      pakketten van de extra), het runbook `docs/runbooks/gezichten.md` (de map met sleutels niet
      in snapshots), `docs/fase-6/x2.md`, en B57 en B61 en verder in `beslissingen.md`.
- [ ] `e2e/slim.sh` is groen.

**Wat schuift, in deze volgorde, als golf 5 uitloopt:**

1. de groepen "Wie is dit?": de voorstellen blijven;
2. de knop "Niet benoemde gezichten wissen": "niet tonen" per gezicht blijft;
3. voorstellen om personen samen te voegen;
4. een uitsnede van een gezicht als omslag van een persoon.

Niets uit deze lijst raakt een privacyregel of een poort.

### X3 — Zoeken op wat er op een foto staat (X3a en X3b)

**Doel.** Je eigen foto's vinden op wat erop staat, met woorden of vanuit een foto, zonder dat er
iets naar buiten gaat en zonder dat de app zelf rekent.

**Wat X3 doet, en wat niet** (Keuze 71).

| | Wel in X3 | Niet in fase 6 | Wat Jonas daardoor mist |
|---|---|---|---|
| Zoeken met tekst | Vrije tekst in het Nederlands of Engels, op Enter | Zoeken terwijl je typt | Je drukt op Enter. Elke vraag kost ±70–125 ms van de ene core van de container (K2-13). |
| Zoeken met een foto | "Meer zoals deze" vanuit een eigen foto | Een foto van buiten uploaden om mee te zoeken | Je zoekt alleen vanuit een foto die al van jou is. |
| Welke bestanden | Eigen, levende foto's (`ftype = 'image'`), ook HEIC en RAW via hun preview | Video's; foto's die met je gedeeld zijn | "strand" vindt geen video. In een gedeelde familiemap vind je via beeld alleen je eigen kopieën (Keuze 79). |
| Filters | Combineren met datum, persoon, plaats, land, tag, camera en map (Keuze 43) | Een filter `label`, groepen of chips per onderwerp | Geen lijst "dit staat in je foto's". De drempel per label is niet gemeten. |
| Slimme albums | — | Een regel op beeld | "Alle foto's met een hond" vult zich niet vanzelf. Elke verversing zou de container vragen; de regel kent het veld `mode` niet (Keuze 80). |
| Plek in de lijst | Een eigen stand op `/zoeken` | Beeld en gewoon zoeken gemengd in één lijst | Twee standen. De web wijst de weg (Keuze 81). |

**Hangt af van.**

- **Z0b**: de contracten van Keuze 80 en de stubs (Keuze 55).
- **A0**: de enum `Op` met de opdrachten van X3, het werkmodel met twee kinderen, de nep-worker,
  `models.lock` en de connect-haak (Keuze 55). X3a begint daarop, naast X2a.
- **X2a**: de container `analyse` zelf (compose, de sleutel, de handdruk, `client.py`, `runtime.py`,
  `health.py`), de rij `analyse`, de extra in `/opt/analyse` en de downloadhulp in `install.sh`
  (§3.11). X3b heeft ze nodig; X3a nog niet.
- **Z1**: het register van filters, `deadline()`, `access.resolve()`, de rondes van Keuze 9 (voor
  de terugval).
- **Z2**: de wachtrij van `analysis` met batches.
- **Z4**: de pagina `/zoeken` en de suggesties. **Z6**: de foto-instellingen. **Z7**: de tab op
  `/beheer`.

**Wanneer en hoe lang** (§5). 8 werkdagen in twee delen, naast X2. X3 schrijft niet in de
bestanden van gezichten. De contracten staan er al (Z0b), dus die dag van revisie 2 vervalt; de
omzetter krijgt meer tijd, omdat hij het grootste risico is (K2-12).

| Deel | Dagen | Wat |
|---|---|---|
| X3a (golf 5, dag 30–33) | 1 | migratie 0012, de triggers in `TRIGGERS`, `write_embed()` |
| | 1,5 | `tokenizer.py` en `tokenizer.bin`; `onnxw.py` (gedeeld met de nepmodellen van X2) |
| | 1,5 | de omzetter met zijn test tegen een eigen numpy-versie, de dienst `analyse-omzetten`, de job `beeld-model` (BZ1–BZ4) |
| | | **go/no-go** aan het eind van dag 33 (Keuze 73): nee is X3 naar een fase 6b (Vraag 13) |
| X3b (golf 6, dag 35–38) | 1 | de opdrachten `embed_image`, `embed_text`, `rank` en `forget` in de kinderen `werk` en `zoeken`; de cache |
| | 1,5 | het zoekpad in de app, het bestand per eigenaar, de jobs, de grenzen, `/photos/beeld` |
| | 1,5 | de web, `install.sh`, de CI-jobs, de documenten |

**Eigendom.**

| Bestand of map | Eigenaar |
|---|---|
| `api/migrations/versions/0012_beeld.py`, de modellen `BlobEmbed` en `BeeldOwner`, de triggers van 0012 in `TRIGGERS` (`app/search/schema.py`, K2-17) | X3a |
| de dienst `analyse-omzetten` in `compose.analyse.yaml` | X3a (het bestand is van X2a) |
| `app/beeld/*` (`search.py`, `vecfile.py`, `jobs.py`, `limits.py`, `cache.py`, `admin.py`, `types.py`), `routers/beeld.py` | X3 |
| `app/analyse/tokenizer.py`, `app/analyse/worker/omzetten/*` | X3a |
| `app/analyse/worker/ops/beeld.py` | X3b |
| `app/analyse/onnxw.py` | X3a; de nepmodellen van X2a gebruiken hem ook |
| de regels voor SigLIP in `app/analyse/models.lock` | X3a (het formaat is van A0) |
| `app.cli beeld-bench`, `beeld-kwaliteit`, `beeld-status` | X3 |
| `web/src/search/beeld/**`, `web/src/viewer/LikeRow.tsx`, het gebied `beeld`, `web/src/i18n/errors/beeld.ts` | X3 |
| `infra/single/tests/test_install_beeld.sh`, de functies voor beeld in `install.sh` | X3 (`install.sh` is gedeeld met Z7 en X2) |
| `docs/fase-6/x3.md`, `docs/fase-6/toetsenbord-x3.md`, het deel beeld in `docs/runbooks/analyse.md` | X3 |

**Instellingen** (in de app, compose zet ze of de standaard geldt):

| Variabele | Standaard | Wat |
|---|---|---|
| `PLATFORM_BEELD_DEADLINE_MS` | `800` | de klok op de tocht naar de container (Keuze 78) |
| `PLATFORM_BEELD_INFLIGHT` | `1` | beeldvragen tegelijk voor de hele app |
| `PLATFORM_BEELD_QUEUE` | `10` | wachtenden; daarboven `429 search_busy` |
| `PLATFORM_BEELD_TOP_K` | `1000` | kandidaten uit de container |
| `PLATFORM_BEELD_BATCH` | `16` | previews per oproep van `embed_image` |
| `PLATFORM_BEELD_REBUILD_S` | `300` | het bestand per eigenaar hooguit zo vaak opnieuw |
| `PLATFORM_BEELD_CACHE` | `/data/cache/beeld` | de map met de bestanden per eigenaar |
| `ANALYSE_BEELD_CACHE_MB` (een constante in de worker) | `400` | vectoren in het geheugen van de container |

**Bouwt:**

- de invulling van de contracten die Z0b vastlegde (Keuze 55, 80): de stubs met
  `501 not_built("X3")` worden echte routes;
- migratie 0012 (`blob_embed`, `beeld_owner`, `photo_pref.beeld`, de triggers in `TRIGGERS`, ook
  `node_ad_beeld`), down_revision 0011; het model in `models.py` (§3.1a);
- `write_embed()`, de stap `embed` (`register_step`, rij `analyse`, de haak `register_ready`, de
  backfill na die van de index, de versie, de regel van Keuze 69), de job `beeld.vec` met het
  opwarmen, de job die bij "uit" wist en `forget` stuurt, en het wissen door de beheerder
  (Keuzes 76, 77, 82);
- `app/analyse/tokenizer.py` met `tokenizer.bin` (Keuze 74);
- de omzetter `app/analyse/worker/omzetten/siglip.py`, `meta.json`, de dienst `analyse-omzetten`
  en trust-on-first-use (Keuze 73, §3.11);
- de opdrachten `embed_image`, `embed_text`, `rank` en `forget` in de kinderen `werk` en `zoeken`
  van A0, met de cache van vectoren (sleutel: eigenaar, `gen` en sha256; Keuze 75);
- het zoekpad `app/beeld/search.py`: de grenzen, de cache van 60 s, de klok met terugval, de SQL met
  `json_each`, de tweede controle (Keuze 78, 79);
- `GET /photos/beeld` met opwarmen; `beeld` in de foto-instellingen; het blok op `/beheer`;
- `install.sh` (`BEELD`, downloaden, omzetten, controleren, opruimen) en `.env.example`;
- de web van Keuze 81;
- de CI-job `beeld-model` en de tests in `api-analyse` (§4.6).

**Tests per bestand.**

| Test | Job | Wat hij eist |
|---|---|---|
| `api/tests/beeld/test_tokenizer.py` | `api` | een kleine `tokenizer.json` die de test zelf maakt: tabel met invoer en tokens; "hond op het strand"; kleine letters per teken ("ΣΟΦΙΑ ΟΔΟΣ"); byte fallback voor een teken buiten de woordenlijst; afkappen op 63 plus `<eos>`, opvullen tot 64; `tokenizer.bin` geeft precies hetzelfde als de JSON; fuzz met een vaste seed: nooit een fout |
| `api/tests/beeld/test_migration_0012.py` | `api` | leeg naar head; 0011 met data naar 0012 en terug, en weer heen; `compare_metadata`; head is `0012`; `test_migration_0011.py` gaat naar `upgrade('0011')` en eist `['0011']`, zoals Z0 dat met 0009 deed (K2-16); `photo_pref.beeld` is `NULL` |
| `api/tests/search/test_schema_triggers.py` (uitgebreid) | `api` | `sqlite_master` is gelijk aan `TRIGGERS` (0010 + 0011 + 0012, K2-17); de voorvoegsels kennen `node_*_beeld` |
| `api/tests/beeld/test_beeld_gen.py` | `api` | `gen` +1 bij een nieuwe foto, een kopie, een andere eigenaar, nieuwe inhoud en definitief wissen (`node_ad_beeld`, K1-08), ook bij het wissen van een gebruiker zonder fout; niet bij de prullenbak, terugzetten of hernoemen; niet bij een document; `NEW.ftype` in `WHEN` werkt op 3.53.1 en in `python:3.12-slim` (3.46.1); een import van 500 foto's wordt hooguit 10% trager dan zonder de triggers |
| `api/tests/beeld/test_vecfile.py` | `api` | het bestand uit de rijen: kop, `num`'s op volgorde, f16-matrix; met de foto's in de prullenbak; zonder die van een ander; zonder wat Keuze 69 uitsluit; zonder een andere versie; `os.replace`; hooguit eens per 5 minuten, en na 10 s na een vraag |
| `api/tests/beeld/test_embed_step.py` | `api` | alleen beelden met een preview en een eigenaar met beeld aan; niet de upload van een ander die beeld uit heeft, en nooit een upload via een link (`not_own_upload`, Keuze 69); pas na de backfill van de index (K2-07); de grootste preview als fd, nooit het pad van de blob; batches van 16, om de beurt per eigenaar; `Defer` bij ≥ 2 transcodes en zonder container; `no_preview`; een nieuwe versie zet alles terug; `write_embed()` is de enige schrijver (AST) en zet `gen` van elke eigenaar |
| `api/tests/beeld/test_beeld_search.py` | `api` (nep-worker) | alleen eigen, levende foto's, op de volgorde van `rank`; elk filter; `like` stuurt de `num`, nooit de vector (K1-08); pagina's uit de cache zonder tweede oproep; de terugval bij te laat, weg en geen model, met de juiste `fallback`; `400 search_bad_filter` bij `after`, een ander type, of zonder `q` en `like`; `409 beeld_off`; een tweede vraag van dezelfde gebruiker stopt de eerste; 12 tegelijk geeft `429 search_busy`; de plan-test van de SQL |
| `api/tests/beeld/test_beeld_access.py` | `api` | de wereld van §4.2: Bram vindt Eva's gedeelde foto niet via beeld; Bram vindt zijn eigen kopie wel, zonder iets van Eva; `like` op een foto van Eva geeft `404`; Eva's foto in de prullenbak niet, na terugzetten wel; publiek: geen route; gast: `403` |
| `api/tests/beeld/test_beeld_never_out.py` | `api` | elk antwoord van elke route, met beeld aan: geen `vec`, `embedding` of `score`, geen lijst van 768 getallen, geen base64 van 1.536 bytes |
| `api/tests/search/test_search_logs.py` (uitgebreid) | `api` | `geheimwoord7` via `mode=image` staat nergens in de logs van de app en van de nep-worker; de echte container test `test_analyse_kinderen.py` (K1-16) |
| `api/tests/beeld/test_beeld_off.py` | `api` | zonder keuze (`NULL`) is beeld uit (Vraag 11); gebruiker uit: geen bestand, 0 rijen voor foto's die alleen van hem zijn, `skip` in `analysis`, `forget` naar de container (K1-08), meteen `409`; een ander met dezelfde foto houdt zijn vector; beheerder uit: `409` en de stap doet niet mee; wissen: 0 rijen in `blob_embed`, lege map; weer aan: de backfill |
| `api/tests/search/test_contracts.py` (van Z0b; X3 laat hem groen) | `api` | de nieuwe velden met hun standaard; een vraag zonder `mode` geeft een antwoord als nu; `SearchRule` met `mode` faalt (een regel van een album kan het niet) |
| `api/tests_analyse/test_beeld_ops.py` | `api-analyse` | met nepmodellen: `embed_image` op een preview-fd geeft 768 × f16 met lengte 1; `rank` geeft dezelfde top als een brute-force-berekening in de test; `like` met een `num`; `forget`; een kop die niet klopt geeft een code; `k` ≤ 1.000; `too_big`; een vraag die wordt afgebroken; twee keer dezelfde uitkomst |
| `api/tests_analyse/test_beeld_cache.py` | `api-analyse` | de sleutel is (eigenaar, `gen`, sha256): twee eigenaren met dezelfde `gen` en elk een eigen bestand krijgen elk hun eigen treffers (K2-23); `forget` haalt alleen die eigenaar weg; boven 400 MB gaat de eigenaar die het langst niet zocht eruit; het opwarmen na een nieuw bestand |
| `api/tests_analyse/test_beeld_omzetten.py` | `api-analyse` | de kleine SigLIP-vorm met willekeurige gewichten: fp32 ≤ 1·10⁻⁵ van een eigen numpy-versie in de test, int8 cosinus ≥ 0,99, twee keer dezelfde sha256, maxrss gemeten; een safetensors met een kop die liegt (offset buiten het bestand) geeft een fout, geen crash |
| `api/tests_analyse/test_beeld_perf.py` | `api-analyse` | 55.000 en 100.000 willekeurige f16-vectoren en een nep-tekstmodel: p95 ≤ 100 ms via HTTP voor alles behalve het tekstmodel, onder `taskset -c 0`; ook de eerste vraag na een nieuw bestand, dat al is opgewarmd (K2-13) |
| `api/tests/test_analyse_grens.py` (van X2) | `api` | numpy en onnxruntime alleen onder `app/analyse/worker/`: dekt de omzetter en `ops/beeld.py` vanzelf |
| `api/tests_analyse/test_beeld_kwaliteit.py` | `beeld-model`, of `app.cli beeld-kwaliteit` op de LXC (marker `beeld_echt`) | de poorten van Keuze 83 met het echte model |
| `api/tests_analyse/test_beeld_echt_perf.py` | idem | p95 ≤ 250 ms warm; ≥ 500 foto's per minuut per core; het rapport met `werk` vol en de eerste vraag; maxrss van de container |
| `infra/single/tests/test_install_beeld.sh` | `infra` | met een kleine nep-bron en een nep-URL: `BEELD=uit` haalt niets en zet niets om; een verkeerde sha256 wordt geweigerd; een tweede run slaat een goed model over; te weinig ruimte is niet fataal; `analyse-omzetten` heeft geen netwerk, geen variabele met `KEY`, geen `analyse-run` en geen `/models` (`compose config`, K1-05); een doel dat een symlink is, wordt geweigerd zonder het te raken (K1-06); de ruimte telt via `df` op `MODELLEN_ROOT` en `DATA_ROOT` (K2-19); de bron is weg na het omzetten; een mislukking stopt het script niet |
| `web/src/search/beeld/beeld.test.ts` | `web` | de URL heen en terug (`mode`, `like`); de pijlen in het raster; de meldingen per `fallback` en bij `indexing`; alleen tekst, geen HTML |
| `e2e/test_zoek_beeld.py` | `e2e` (nep-worker met vaste uitvoer) | alleen het toetsenbord: naar `/zoeken`, de stand kiezen, "strand", Enter, pijl, Enter opent de viewer, Esc; "Meer zoals deze"; de melding bij de terugval; de schakelaar staat standaard uit en gaat aan via Account → Foto's; uit geeft de melding; geen verzoek naar een andere host |

**Elke gebeurtenis** (aanvulling op §3.9, voor beeld):

| Gebeurtenis | Vector (`blob_embed`) | Bestand per eigenaar | Bij zoeken op beeld | Test |
|---|---|---|---|---|
| upload van een foto (eigenaar met beeld aan; zelf geüpload, of door iemand met beeld aan) | na `preview.ready`: job `embed` | trigger: `gen` +1; opnieuw na ≤ 5 min, of 10 s na een vraag | vindbaar na de job en het bestand (minuten) | `test_beeld_upload_then_found` |
| upload door een ander in jouw map, of via een uploadlink | geen job, tenzij die ander beeld aan heeft; via een link nooit (`not_own_upload`, Keuze 69) | niet in jouw bestand | niet te vinden op beeld; gewoon zoeken wel | `test_beeld_other_uploader` |
| kopie door dezelfde eigenaar | er is al een vector | trigger: `gen` +1 | vindbaar na het bestand | `test_beeld_copy_same_owner` |
| kopie door Bram | er is al een vector (per blob) | trigger: Bram `gen` +1 | Bram vindt zijn kopie als hij beeld aan heeft; niets van Eva gaat mee | `test_beeld_copy_other_owner` |
| andere eigenaar | blijft | trigger: beide `gen` +1 | de oude eigenaar meteen niet meer (`own_live`); de nieuwe na het bestand, met zijn eigen keuze | `test_beeld_owner_change` |
| nieuwe inhoud, of een versie terug | job voor de nieuwe sha, als die er nog geen heeft | trigger: `gen` +1 | de oude inhoud nooit meer bij deze node | `test_beeld_replace` |
| naar de prullenbak, en terug | blijft | blijft | meteen weg; na terugzetten meteen terug | `test_beeld_trash` |
| definitief wissen | weg met de blob (cascade), als niemand hem meer heeft | trigger `node_ad_beeld`: `gen` +1, het bestand opnieuw zonder die foto (K1-08); tot dan laat `own_live` hem weg | weg | `test_beeld_purge` |
| gebruiker wissen | — | `beeld_owner` weg (cascade); het bestand weg bij het opruimwerk; `forget` in de container | — | `test_beeld_user_delete` |
| gebruiker zet beeld uit | rijen die niemand met beeld aan nog nodig heeft: weg | weg; `forget` in de container (K1-08) | meteen `409 beeld_off` | `test_beeld_off` |
| gebruiker zet beeld weer aan | de backfill | opnieuw | na de jobs, met `indexing` | `test_beeld_on_again` |
| beheerder zet beeld uit | blijft (tot wissen) | blijft | `409 beeld_off` voor iedereen | `test_beeld_admin_off` |
| beheerder wist | alles weg | alles weg | `indexing` 0%, de backfill als beeld aan staat | `test_beeld_wipe` |
| een nieuw model | alles weer `wait` | opnieuw per eigenaar | over wat al klaar is, met `indexing` | `test_beeld_version` |
| container weg of model weg | jobs wachten (`Defer(300)`, zonder model `Defer(3600)`; §3.11) | blijft | gewoon zoeken met `fallback` | `test_beeld_fallback` |
| publieke link | — | — | geen route | `test_public_never_search` |

**Klaar als:**

- [ ] De contracten van Keuze 80 (uit Z0b) zijn ingevuld; geen route van X3 geeft nog `501`. Een
      vraag zonder `mode` geeft hetzelfde als vóór X3, en geen test is zwakker geworden.
- [ ] 0012 gaat heen en terug met data; de triggers zijn gelijk aan `schema.py`; BZ8 is groen.
- [ ] `test_tokenizer.py` is groen, en BZ3 (met het bestand van SigLIP 2 zelf, zonder Gemma) is
      groen.
- [ ] De omzetter is gelijk aan de eigen numpy-versie op de kleine vorm, en geeft twee keer dezelfde
      sha256.
- [ ] De go/no-go na X3a is gehaald (Keuze 73, K2-12).
- [ ] De echte omzetting is gedaan (BZ2) in de job `beeld-model`, en de sha256's staan in
      `models.lock`. Kan CI de bron niet halen (BZ1), dan op de LXC met trust-on-first-use, en
      zegt `/beheer` "niet nagerekend in CI".
- [ ] De opdrachten werken in de container met nepmodellen, en `rank` is gelijk aan brute force.
- [ ] Zoeken: alleen eigen, levende foto's; elk filter; de terugval; geen vector en geen score in
      enig antwoord; schone logs; `429`; een nieuwe vraag stopt de oude.
- [ ] Uit en wissen: 0 rijen, geen bestand, en `forget` heeft de cache van die eigenaar geleegd.
      `blob_embed` gaat niet mee in de reservekopie. De beheerder kan uitzetten.
- [ ] De kwaliteitspoort van Keuze 83 (BZ4) is groen, in `beeld-model` of met `beeld-kwaliteit` op
      de LXC.
- [ ] De tijden: p95 ≤ 100 ms zonder het tekstmodel in `api-analyse`; p95 ≤ 250 ms warm, ook bij
      100.000 foto's en bij de eerste vraag na een nieuw bestand, en ≥ 500 foto's per minuut per
      core met het echte model (BZ6, Vraag 14).
- [ ] Het geheugen: maxrss van de container ≤ 2,5 GB met een groepsronde van 60.000, een volle
      cache en een herlaadactie tegelijk, onder `mem_limit: 3g` (BZ5, K2-06).
- [ ] BZ10 heeft gemeten of "Meer zoals deze" op een portret dezelfde persoon terugvindt. De
      uitkomst staat in `x3.md`; boven toeval geldt de regel van Keuze 82.
- [ ] `test_install_beeld.sh` is groen in `infra`; `BEELD=uit` werkt; een mislukking is niet fataal.
- [ ] De web: vitest en e2e met alleen het toetsenbord zijn groen; de eerste lading +0 B; het raster
      ≤ 15 kB; `toetsenbord-x3.md`.
- [ ] Het blok op `/beheer`, het deel beeld in het runbook, de README en `docs/fase-6/x3.md`.
- [ ] `e2e/slim.sh` is groen, en alle bestaande jobs (ook `zoek-perf` met zijn 200 ms) blijven
      groen.

**Bewijs dat nog nodig is (BZ1–BZ10).**

Zoals de spike van §3.7: wat de sandbox niet kan, gebeurt in CI of op de LXC, met een uitkomst in
`docs/fase-6/x3.md`. Een CI-job drukt aan het eind altijd een blok `=== kort ===` af, omdat de logs
alleen de staart tonen.

| # | Wat | Waarom | Klaar als |
|---|---|---|---|
| BZ1 | Kan een runner bij huggingface.co, upload.wikimedia.org en images-assets.nasa.gov? | De sandbox niet (code 000) [gemeten, B3]; CI is niet aangetoond | een regel per host in `=== kort ===`. Nee: de echte stappen BZ2–BZ6 gaan naar de LXC (`beeld-kwaliteit`, `beeld-bench`) |
| BZ2 | De echte omzetting: de commit en de sha256 van de vijf bronbestanden; twee keer omzetten, op twee runners; tijd en maxrss | Keuze 73 | de sha256's in `models.lock`; twee keer dezelfde uitkomst; maxrss ≤ 1,5 GB |
| BZ3 | De tokenizer met het tokenizer.json van SigLIP 2 zelf, tegen een vaste lijst verwachte tokens in het repo; met ja op Vraag 12b ook tegen `tokenizers`. Nooit met het bestand van Gemma (K1-14, K2-14) | het algoritme is alleen bewezen met een ander bestand van dezelfde soort [B5] | 0 verschillen op de lijst in het repo; met ja op Vraag 12b ook op de 2.181 zinnen van B5 |
| BZ4 | De kwaliteitspoort, int8 tegen fp32, de drempels `MIN_TEKST` en `MIN_FOTO`, de zelftest met drie kleuren | Keuze 83 | de poorten gehaald; de drempels in een test |
| BZ5 | Het geheugen van de container met een groepsronde van 60.000, de cache vol (400 MB) en een herlaadactie van 100.000 tegelijk | Keuze 75; geschat ±1,9 GB [K2-06] | maxrss ≤ 2,5 GB onder `mem_limit: 3g`; `RLIMIT_DATA` per kind houdt |
| BZ6 | Tijden op één core met het echte model: warm, met `werk` vol, de eerste vraag na een nieuw bestand, bij 55.000 en 100.000 foto's, en tijdens de index-backfill (K2-07); foto's per minuut | Keuze 77, 78 | p95 ≤ 250 ms warm en bij de eerste vraag; ≥ 500 per minuut (verwacht 750–870 [K2-04]); het rapport |
| BZ7 | `tokenizer.bin`: laden en RSS | JSON kostte 1,35 s en 245 MB [B5] | ≤ 0,5 s en ≤ 100 MB |
| BZ8 | De triggers met `NEW.ftype` in `WHEN`, en hun kosten bij een import van 500 | Keuze 76 | werkt op 3.46.1 en 3.53.1; ≤ 10% trager |
| BZ9 | `app.cli beeld-bench` op de LXC van Jonas (na de installatie) | de CPU van Jonas is onbekend; int8 hangt af van VNNI | de uitkomst op `/beheer`; haalt hij 250 ms niet, dan zegt `/beheer` het en vangt de terugval het op |
| BZ10 | Vindt "Meer zoals deze" op een portret dezelfde persoon terug? Op de LXC met de bevestigde personen van Jonas (`app.cli beeld-kwaliteit --personen`, alleen getallen; niets verlaat de LXC), of in CI met ja op Vraag 8 | K1-09: niet getest | hoe vaak dezelfde persoon in de top 5 staat, tegen toeval. Boven toeval: de regel van Keuze 82, en beeld blijft standaard uit (Vraag 11) |

---

## 3. Fundament (Z0, Z0b en A0)

Z0 duurt ±3 dagen plus een reviewdag, in drie delen naast elkaar: **Z0-api** (migratie, modellen,
contracten, `own_live`, stubs, hotspots, spike), **Z0-web** (routes, lege pagina's, de zoekknop,
de i18n-gebieden en catalogi, begrotingen) en **Z0-e2e** (harnas, fixtures, de wereld, de
referentie, `e2e/slim.sh`, CI-jobs). Z0 bouwt geen functie af; het legt vast waar de pakketten op
bouwen.

Nieuw in r3. **Z0b** (dag 4, vóór de review van Z0) voegt de contracten van X2 en X3 toe aan
dezelfde bestanden: velden met een standaard, `Step.EMBED`, de rij `analyse`, de sleutels `faces`
en `beeld`, de stubs en de lege plekken in de web (Keuze 55; §3.3, §3.5). Zo hoeven golf 1–4
niets te veranderen als X2 en X3 komen (K2-01). Migratie 0010 is dan nog niet gepusht. **A0**
(dag 28–29, na 0.8.0) legt het fundament van de container `analyse` en de migraties 0011 en 0012
(§3.1a, §3.11). Wat A0 bouwt, staat in §2 bij A0.

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
| `photo_geo` | `node_id`, `owner_id`, `lat` REAL, `lon` REAL, `alt` REAL NULL, `x16` INT, `y16` INT, `place_id` INT NULL, `country` TEXT NULL, `admin1` TEXT NULL, `source` (`exif`, `xmp`, `video`); herzien na de review van Z0: `dead` INT 0 en `taken_at` NULL, die van de node (triggers, nooit zelf schrijven) | PK `node_id`; `ix_photo_geo_tile (owner_id, dead, x16, y16, taken_at, node_id)`, `ix_photo_geo_place (owner_id, dead, place_id)`, `ix_photo_geo_country (owner_id, dead, country)` | Z3 |
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

**Herzien na de spike (S8).** `extra` gaat ook door `SS()`: `SS(coalesce((SELECT group_concat(…)
…), ''))`. Anders vond "grosse" de film "Die große Stille" niet. Zo staat het in `schema.py` en
in 0010. En de tekst krijgt NFKC vóór hij in `blob_text` komt (Keuze 21): `unicode61` breekt een
ligatuur als "ﬁ" niet op.

**Herzien na de review van Z0 (veilig F3).** OCR van een foto staat niet meer in de kolom `ocr`,
maar in een eigen kolom `pocr` (`SS(coalesce(po.text, ''))`), achteraan in de view en in
`fts_node(name, extra, body, ocr, meta, pocr, …)`; `ocr` is alleen `SS(coalesce(t.ocr, ''))`. In
één kolom kon een treffer, zijn bm25 en zijn snippet bij een ander uit de foto-OCR van de
eigenaar komen, en FTS5 kon die twee daarna niet meer scheiden. Z1 zoekt voor een node van een
ander met het kolomfilter `{name extra body ocr meta}: …` (zonder `pocr`), en alleen voor eigen
nodes met alle kolommen; de snippet komt bij een ander nooit uit `pocr`. De macro's `DEL` en
`INS` nemen `pocr` mee. Test: `test_ocr_van_een_foto_staat_alleen_in_pocr`
(`tests/search/test_schema_triggers.py`). Het gewicht van `pocr` in bm25 is dat van `ocr` (0,8).

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

**Herzien na de review van Z0 (veilig F2).** Die ene `UPDATE` doet per rij van de submap een
`EXISTS` over alle nodes in de prullenbak van de submap: O(submap × prullenbak). Een map met
10.000 bestanden, waarvan 2.000 in de prullenbak, terugzetten of verplaatsen kostte 5,9 en 6,2 s
op 3.46.1, met het schrijfslot vast. Nu twee stappen, elk lineair; dezelfde map in 0,09 en 0,14 s:

```sql
UPDATE node SET dead =
    coalesce((SELECT p.dead FROM node p WHERE p.id = NEW.parent_id), 0)
 WHERE mpath >= CUR AND mpath < END(CUR);
UPDATE node SET dead = 1
  FROM (SELECT mpath AS tp FROM node WHERE deleted_at IS NOT NULL
          AND mpath >= CUR AND mpath < END(CUR)) AS t                   -- ix_node_trash
 WHERE node.mpath >= t.tp AND node.mpath < END(t.tp);
```

`tests/search/test_triggers_dead.py`: `dead` klopt op elke diepte, het werk groeit niet met de
prullenbak, en (marker `zoek_perf`) terugzetten en verplaatsen blijven onder 0,5 s.

**Herzien na de review van Z0 (haalbaar F1 en F2): `photo_geo` volgt zijn node.** De kaart leest
`photo_geo` zonder join (Keuze 4a). Daarvoor zet `DEAD_SUBTREE` als derde stap `dead` in
`photo_geo` voor de submap, alleen waar hij verandert (een gewone verplaatsing schrijft daar
niets), en vier triggers houden de rest vast:

```sql
-- derde stap van DEAD_SUBTREE: per node van de submap één zoekstap op de sleutel van photo_geo
UPDATE photo_geo SET dead = (SELECT n.dead FROM node n WHERE n.id = photo_geo.node_id)
 WHERE node_id IN (SELECT id FROM node WHERE mpath >= CUR AND mpath < END(CUR))
   AND dead IS NOT (SELECT n.dead FROM node n WHERE n.id = photo_geo.node_id);

CREATE TRIGGER node_au_taken AFTER UPDATE OF taken_at ON node
WHEN OLD.taken_at IS NOT NEW.taken_at BEGIN
  UPDATE photo_geo SET taken_at = NEW.taken_at WHERE node_id = NEW.id;
END;
-- alleen bij een bestand, van de eigenaar van de node
CREATE TRIGGER geo_bi BEFORE INSERT ON photo_geo
WHEN NOT EXISTS (SELECT 1 FROM node n WHERE n.id = NEW.node_id
                 AND n.owner_id = NEW.owner_id AND n.kind = 'file') BEGIN
  SELECT RAISE(ABORT, 'photo_geo: alleen bij een bestand, van de eigenaar van de node');
END;
-- een nieuwe rij krijgt dead en taken_at van de node (het ORM schrijft 0 en NULL)
CREATE TRIGGER geo_ai AFTER INSERT ON photo_geo BEGIN
  UPDATE photo_geo SET (dead, taken_at) =
    (SELECT n.dead, n.taken_at FROM node n WHERE n.id = NEW.node_id)
  WHERE node_id = NEW.node_id;
END;
-- node_id en owner_id veranderen nooit (een andere eigenaar: node_au_owner wist de rij), en
-- dead en taken_at zijn altijd die van de node
CREATE TRIGGER geo_bu BEFORE UPDATE ON photo_geo
WHEN NEW.node_id IS NOT OLD.node_id OR NEW.owner_id IS NOT OLD.owner_id
  OR NEW.dead IS NOT (SELECT n.dead FROM node n WHERE n.id = NEW.node_id)
  OR NEW.taken_at IS NOT (SELECT n.taken_at FROM node n WHERE n.id = NEW.node_id) BEGIN
  SELECT RAISE(ABORT, 'photo_geo: node_id, owner_id, dead en taken_at volgen de node');
END;
```

Ook zonder FTS5 (`TRIGGERS_NO_FTS`). `tests/search/test_triggers_geo.py`: na elke stap
(prullenbak, ook via een map erboven, terug, verplaatsen, een andere opnamedatum, een andere
eigenaar) zijn `dead` en `taken_at` die van de node en toont `own_live_geo` precies wat
`own_live` met de join toont; een rij bij een map, een album, de node van een ander of een node
die niet bestaat wordt geweigerd, en zelf `dead`, `taken_at`, `owner_id` of `node_id` zetten
ook; de stap in `photo_geo` zoekt per node op de sleutel; en (marker `zoek_perf`) een map met
10.000 foto's met plek blijft onder 1 s. `check_tree` (`tests/search/migratie.py`) controleert
`photo_geo` nu bij elke test die hem gebruikt.

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

**Herzien na de spike (S2).** "Gelezen in dezelfde transactie" vraagt dat het stuk eerst schrijft
(`UPDATE search_state SET cursor = cursor WHERE id = 1 RETURNING cursor`, dat ook `:cur` geeft) en
pas dan `:hi` en `search_seq.next` leest; zie Keuze 12.

**Downgrade naar 0009:** eerst alle triggers, dan de view, de twee FTS-tabellen (hun
schaduwtabellen gaan mee), de nieuwe tabellen, de zes indexen op `node`, dan
`ALTER TABLE node DROP COLUMN` voor `ftype`, `dead`, `mpath` en `num`, en `ALTER TABLE album_item
DROP COLUMN` voor `shared` en `source` (SQLite ≥ 3.35, beide builds; V10 voor `ftype`). Geen
kopie van `node`. `db-downgrade` waarschuwt vóór "ja": de index, de plekken, de hashes, de
personen en de regels van albums gaan weg; bestanden, delingen en albums blijven. Een item dat
`shared = 0` had, is na de downgrade weer gedeeld zoals in 0.7.0: `db-downgrade` noemt het aantal
en vraagt of de regel-items eerst uit het album moeten (standaard ja). Herzien na de review van
Z0 (veilig F1): het antwoord doet pas iets na het laatste "ja", en de items gaan eruit in dezelfde
transactie als de downgrade. Zegt iemand daarna "nee", of is de invoer op, dan blijft alles staan.

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

### 3.1a Migratie 0011 (gezichten) en 0012 (beeld)

Nieuw in r3 (Keuze 61, 69 en 76). 0010 blijft van Z0. De tabellen van gezichten komen in 0011
(A0 en X2a), die van beeld in 0012 (A0 en X3a). Beide zijn additief: geen bestaande tabel
verandert, behalve een nieuwe kolom `photo_pref.beeld` in 0012.

**Bestanden** (K2-18). `api/migrations/versions/0011_gezichten.py` (down_revision `0010`) en
`api/migrations/versions/0012_beeld.py` (down_revision `0011`). Elke migratie heeft een eigen,
bevroren kopie van haar SQL, zoals 0010.

**Eén plek voor de triggers** (K2-17). De triggers van 0011 en 0012 komen in
`app/search/schema.py`, in dezelfde `TRIGGERS` (en `TRIGGERS_NO_FTS`) als die van 0010. Z0b zet
die plek klaar (Keuze 55): `TRIGGERS_0010` en `TRIGGERS_NO_FTS_0010` zijn het bevroren deel van
0010, en `test_de_migratie_is_een_kopie_van_schema` vergelijkt de kopie in 0010 alleen daarmee.
A0 zet er `TRIGGERS_0011` en `TRIGGERS_0012` naast (elk gelijk aan de kopie in zijn migratie) en
voegt ze aan `TRIGGERS` toe (review Z0b, contract F5). Zo blijft
`test_triggers_in_de_database_zijn_schema_triggers` één
vergelijking: wat in `sqlite_master` staat, is precies `TRIGGERS` [code: wt-z0api
`tests/search/test_schema_triggers.py:33–34`]. `test_elke_trigger_op_een_bekende_tabel` krijgt
`person` erbij, voor `person_bd_face` [code: `test_schema_triggers.py:88–95`].

**Wijzigingen in bestaande tests** (K2-16).

- `test_migration_0010.py` gaat naar `upgrade('0010')` en eist `['0010']`, zoals Z0 het voor
  0009 deed (commit `d1c7a07`). Nu doet hij op regel 52 nog `upgrade('head')` [code: wt-z0api
  `tests/search/test_migration_0010.py:52–54`]. De test wordt dus niet zwakker.
- `test_migration_0011.py` en `test_migration_0012.py` testen de head, heen en terug.

**De tabellen van 0011.**

- Elke `node_id` verwijst naar `node(id) ON DELETE CASCADE`.
- Elke `owner_id` verwijst naar `user(id) ON DELETE CASCADE`.
- Elke `person_id` verwijst naar `person(id)`.
- UUID's zijn zoals in de andere tabellen (`Uuid`).

| Tabel | Kolommen | Sleutel en indexen |
|---|---|---|
| `face` | `id` UUID; `node_id`; `owner_id`; `x`, `y`, `w`, `h` REAL (0–1, zoals getoond); `score` REAL; `px` INT (de korte kant in de preview); `vec` BLOB NULL (540 B: AES-GCM over 128 × float32, Keuze 68; NULL is gewist); `version` INT; `cluster` INT NULL; `suggest_id` NULL (FK `person` SET NULL); `suggest_score` REAL NULL; `suggest_why` TEXT NULL (`sim` of `tag`); `hidden` INT 0; `created_at` | PK `id`; `ix_face_node (node_id)`; `ix_face_open (owner_id, suggest_id, suggest_score DESC) WHERE suggest_id IS NOT NULL AND hidden = 0`; `ix_face_cluster (owner_id, cluster) WHERE cluster IS NOT NULL AND hidden = 0`; `ix_face_new (owner_id, created_at DESC) WHERE hidden = 0` (K1-03) |
| `face_person` | `face_id` (FK `face` CASCADE); `person_id` (FK `person` CASCADE); `owner_id`; `verdict` (`ja` of `nee`); `created_at` | PK (`face_id`, `person_id`); `ux_fp_ja (face_id) WHERE verdict = 'ja'` (UNIQUE); `ix_fp_person (owner_id, person_id, verdict)` |
| `face_scan` | `node_id`; `owner_id`; `version` INT; `state` (`wait`, `busy`, `done`, `skip`, `failed`); `faces` INT 0; `tries` INT 0; `code` TEXT NULL; `prio` TEXT (de datum van de tijdlijn); `queued_at`; `updated_at` | PK `node_id`; `ix_face_scan_todo (state, owner_id, prio DESC)` |
| `face_owner` | `owner_id`; `run` INT 0 (het nummer van de laatste groepsronde); `grouped_at` NULL; `suggested_at` NULL; `verdict_at` NULL; `served_at` NULL; `wiping` INT 0 | PK `owner_id` |
| `node_origin` | `node_id` (FK `node` CASCADE); `created_by` NULL (FK `user` SET NULL; NULL bij een upload via een link); `via_link` INT 0 | PK `node_id` (Keuze 69, K1-02) |

**Wat de kolommen betekenen.**

- **`hidden = 1` is "niet tonen".** `vec`, `suggest_*` en `cluster` zijn dan NULL. Alleen het
  kader blijft, zodat een nieuwe scan hetzelfde gezicht niet terugbrengt (X2, "Bevestigen").
  `hide` en "ongedaan maken" zetten meteen een `faces.wipe` met `scope=checkpoint` klaar (K1-15).
- **`suggest_score` gaat nooit naar buiten.** De API geeft alleen `sure` (X2, "API").
- **`face_owner`** onthoudt per eigenaar wanneer er gegroepeerd is, wanneer hij aan de beurt
  was, en of er een wisronde loopt.
- **`ix_face_new`** is er voor het lezen van de nieuwste open gezichten voor een groepsronde.
  Zonder die index sorteert SQLite in een tijdelijk bestand (K1-03).
- **`node_origin`** wordt gevuld bij upload, kopie en `place_file`. Een node zonder rij (van vóór
  0011) telt als door de eigenaar geüpload (Vraag 9).

**De triggers van 0011:**

```sql
-- een andere eigenaar: geen gezicht van de oude eigenaar blijft aan de node hangen (Keuze 25a)
CREATE TRIGGER node_au_owner_face AFTER UPDATE OF owner_id ON node
WHEN OLD.owner_id IS NOT NEW.owner_id BEGIN
  DELETE FROM face WHERE node_id = NEW.id;
  DELETE FROM face_scan WHERE node_id = NEW.id;
END;

-- nieuwe inhoud: de gezichten van de oude inhoud weg. Personen blijven, zonder kader (Keuze 25a)
CREATE TRIGGER node_au_blob_face AFTER UPDATE OF blob_id ON node
WHEN OLD.blob_id IS NOT NEW.blob_id BEGIN
  DELETE FROM face WHERE node_id = NEW.id;
  DELETE FROM face_scan WHERE node_id = NEW.id;
  UPDATE photo_person SET x = NULL, y = NULL, w = NULL, h = NULL WHERE node_id = NEW.id;
END;

-- een persoon wissen: zijn bevestigde gezichten worden "niet tonen", zonder vector (Keuze 64).
-- face_person gaat weg via de cascade; suggest_id via SET NULL.
CREATE TRIGGER person_bd_face BEFORE DELETE ON person BEGIN
  UPDATE face SET vec = NULL, hidden = 1, suggest_id = NULL, suggest_score = NULL,
                  suggest_why = NULL, cluster = NULL
  WHERE id IN (SELECT face_id FROM face_person WHERE person_id = OLD.id AND verdict = 'ja');
END;
```

**Een opmerking over `photo_person`.** De regel die het kader leegmaakt, hoort eigenlijk bij
Z0. Keuze 25a zegt "zonder kader", maar `node_bu_text` in Z0 doet dat niet [code: wt-z0api
`app/search/schema.py:221–227`]. Z0b kan het in 0010 repareren, zolang 0010 niet gepusht is
(Keuze 55). Dan is deze regel dubbel maar onschuldig. Anders doet 0011 het.
**Na Z0b:** 0010 is gepusht, dus 0011 doet het (A0; [z0b.md](z0b.md)).

**Eigen en levend.**

- **Waar de code staat.** In `app/people/faces/`. `test_own_live.py` loopt al alles onder
  `people` na [code: wt-z0api `tests/search/test_own_live.py:31–42`].
- **Erbij in die test.** `routers/people_faces.py` in `PHOTO_FUNCTIONS`, en
  `people/faces_types.py` in `NOT_A_QUERY`.
- **Elke query** op `face` doet een join met `node` en gebruikt `own_live(n)`, ook de query
  die vectoren voor de worker leest. Zo komt een foto in de prullenbak niet in een voorstel, een
  groep of een telling. Zet je hem terug, dan is hij er weer.

**Ruimte.**

- **Gemeten.** 598 B per gezicht (kader, score, 128 × float32, index op `node_id`; 150.000
  gezichten in 89,7 MB) [gemeten: spike B7]. De versleuteling voegt 28 B per vector toe (nonce en
  tag; Keuze 68).
- **Met UUID's en de vier indexen.** ±0,7–0,9 kB per gezicht [schatting].
- **Per 100.000 gezichten.** ±70–90 MB, plus ±15 MB voor `face_scan` per 100.000 foto's
  [schatting].
- `/beheer` toont het aantal (Keuze 67).

**Terug naar 0010** (downgrade, nodig voor §3.10):

1. `PRAGMA secure_delete = ON` (de connect-haak zet hem al, Keuze 64).
2. `DELETE` op de vijf tabellen.
3. De triggers en tabellen weg.
4. `app/migrate.py` doet na elke migratie `wal_checkpoint(TRUNCATE)`. Dat is een hotspot van
   A0.

De sleutels in `SLEUTELS_ROOT` blijven staan; zonder rijen zijn ze niets waard, en het runbook
zegt hoe je ze wist. `test_migration_0011.py` test heen en terug met gezichten erin, en eist
daarna 0 merktekens in `.db` en `-wal` (X2, "Privacy").

**Migratie 0012 in het kort** (Keuze 76; de details staan daar en bij X3):

| Wat | Hoe |
|---|---|
| `blob_embed` | `id` INTEGER PRIMARY KEY, `blob_sha` UNIQUE (FK `blob` CASCADE), `model`, `version`, `vec` (768 × float16, 1.536 B), `updated_at`. 205 MB per 100.000 foto's [gemeten, B7]. Niet in de reservekopie (Keuze 52, K1-08). |
| `beeld_owner` | `user_id` PK (FK `user` CASCADE), `gen`, `built_gen`, `built_rows`, `built_at`. |
| `photo_pref.beeld` | TEXT NULL; NULL is de standaard uit `PHOTO_PREFS`, en die is "uit" (Vraag 11). |
| triggers | `node_ai_beeld`, `node_au_beeld` en `node_ad_beeld` (K1-08) zetten `gen` +1, in dezelfde `TRIGGERS`. |
| terug naar 0011 | eerst de triggers, dan de twee tabellen en de kolom. De cachemap blijft staan; het runbook zegt hoe je hem wist. |

### 3.2 Instellingen

Nieuw in `.env` (`install.sh` zet de standaard één keer):

```
# Zoeken en foto's (fase 6)
PLAATSEN=aan                # aan | uit: plaatsnamen en landen downloaden en laden (Keuze 29)
ANALYSE=aan                 # aan | uit: de container analyse (gezichten en beeld, Keuze 56)
ANALYSE_WORKER_KEY=         # 64 hex; install.sh maakt hem één keer (§3.11)
BEELD=aan                   # aan | uit: het model voor zoeken op beeld (Keuze 84)
MODELLEN_ROOT=/opt/platform/modellen          # van root, buiten DATA_ROOT (Keuze 58)
SLEUTELS_ROOT=/opt/platform/sleutels/gezichten  # sleutels per eigenaar (Keuze 68)
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
| `PLATFORM_PLACES_DB` | `<geo_src>/places.sqlite` (in het image `/data/geo/places.sqlite`) | gemaakt door `geo-load`; ontbreekt hij, dan geen namen. Herzien na de review van Z0 (veilig F5): volgt `geo_src`, niet vast `/data` |
| `PLATFORM_GEO_SRC` | `<data_dir>/geo` (in het image `/data/geo`) | waar `install.sh` de bronnen neerzet; volgt `data_dir` zoals `blob_dir` (review Z0, veilig F5) |
| `PLATFORM_INDEX_DEFER_TRANSCODES` | `2` | `index`, `ocr` en `analyse` wachten bij zoveel transcodes |
| `PLATFORM_OCR_MAX_PAGES` | `200` | per document (X1) |
| `PLATFORM_OCR_PER_HOUR` | `20` | verzoeken per gebruiker per uur (X1) |
| `PLATFORM_OCR_PAGES_PER_DAY` | `200` | pagina's per gebruiker per dag (X1) |

De instellingen van de container `analyse` en van gezichten (`PLATFORM_ANALYSE_*`,
`PLATFORM_FACES_*`) staan in §3.11; die van beeld (`PLATFORM_BEELD_*`) bij X3.

Eén nieuw geheim: `ANALYSE_WORKER_KEY`, zoals `MEDIA_WORKER_KEY`. `install.sh` maakt hem één keer,
in `/opt/platform/.env` (0600), en vervangt hem nooit. Hij staat nooit in `command`, een log of
een document. De sleutels per eigenaar (Keuze 68) zijn geen instelling: ze staan als bestanden in
`SLEUTELS_ROOT`, buiten `DATA_ROOT` (wat dat betekent voor snapshots en backups: Keuze 68).

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
# herzien na de review van Z0 (haalbaar F2): photo_geo zonder join, dezelfde regel (§3.1)
def own_live_geo(g: Alias, me: UUID) -> ColumnElement[bool]: ...  # owner, dead = 0
OWN_LIVE_GEO_SQL = "{g}.owner_id = :me AND {g}.dead = 0"

# app/search/filters.py
def register(name: str, apply: ApplyFilter, suggest: SuggestFn | None = None) -> None: ...
# app/search/hooks.py (zoals media.hooks: pakketten melden zich aan)
def register_groups(fn: GroupsFn) -> None: ...          # Z3 plaatsen, Z6 personen
def register_suggest(fn: SuggestFn) -> None: ...

# app/analysis.py
class Step(StrEnum): TEXT = "text"; HASH = "hash"; OCR = "ocr"; EMBED = "embed"   # EMBED: Z0b
VERSIONS: dict[Step, int] = {Step.TEXT: 1, Step.HASH: 1, Step.OCR: 1, Step.EMBED: 1}
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
PHOTO_PREFS = {"location": "mij", "memories": True, "ocr_photos": False, "faces": "uit",
               "beeld": "uit"}                      # beeld: Z0b (Vraag 11)
# na de review van Z0b (veilig V3, contract F1 en F4): X2 en X3 melden hun schakelaar aan; Z6
# roept check_switch vóór het schrijven aan en switched erna, in dezelfde transactie
def register_switch(key: Literal["faces", "beeld"], switch: Switch) -> None: ...
async def check_switch(db, user, patch, *, analyse_enabled: bool) -> None: ...
    # gast 403 guest; aan: 403 faces_forbidden / 409 beeld_off, 409 faces_unavailable /
    # 409 beeld_not_installed zonder aanmelding of zonder PLATFORM_ANALYSE_ENABLED; uit mag altijd
async def switched(db, user, before: dict, patch) -> None: ...
async def photo_settings(db, user, *, analyse_enabled: bool) -> PhotoSettings: ...

# app/search/hooks.py (na de review van Z0b, contract F4): het deel van X2 en X3 op /beheer
def register_admin(name: Literal["faces", "beeld"], part: AdminPart) -> None: ...
async def admin_block(name, db) -> Any: ...          # None zonder aanmelding
async def admin_switch(name, db, value: bool) -> None: ...   # zonder aanmelding 501 not_built
async def admin_rebuild(name, db) -> None: ...       # zonder aanmelding 501 not_built
```

Rijen in `jobs.py`:

```python
LANES = ("default", "preview", "index", "media_scan", "media", "media_net", "import", "ocr",
         "analyse")                                  # analyse: Z0b
def active_lanes(s: Settings) -> tuple[str, ...]:
    return (BASE_LANES                               # default, preview, index
            + (MEDIA_LANES if s.media_enabled else ())
            + (("ocr",) if s.media_enabled and s.ocr_enabled else ())
            + (("analyse",) if s.analyse_enabled else ()))
```

`preview` kwam in 0.7.1 (`jobs.py` op `245d87a`). `analyse` draait alleen met de container
(Keuze 59), en de stappen in die rij beginnen pas na de backfill van de index (Keuze 65, 77,
K2-07).

**Wat Z0b toevoegt** (Keuze 55, K2-01). Alles is additief, met een standaard, zodat een oude
client hetzelfde antwoord krijgt:

- de velden en waarden van Keuze 66 (gezichten) en 80 (beeld) in `app/search/types.py`, de
  modellen van `/photos` en de web-types, met hun standaard;
- `Step.EMBED`, de rij `analyse`, `beeld` in `PHOTO_PREFS` (hierboven);
- `previews.register_large(fn)` (Keuze 65);
- de routes van X2 en X3 als stub (`501 not_built("X2")` of `"X3"`);
- in de web: `LikeRow` in het infopaneel, de blokken "Gezichten" en "Zoeken op beeld" op
  `/beheer`, de gebieden `photos_faces` en `beeld`, de catalogi `faces.ts` en `beeld.ts`, en de
  regels in `budget.mjs`;
- in 0010: `node_bu_text` maakt het kader van `photo_person` leeg bij nieuwe inhoud (Keuze 25a),
  en de docstring van `app/prefs.py` noemt de goede vraag.

De tests met een exacte lijst (de rijen, de stappen, de routes, de sleutels) eisen in Z0b de
nieuwe lijst. Geen test wordt zwakker.

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

**De overgang van de vraag van 0.7.0** (herzien na de review van Z0, haalbaar F4). De web van nu
(`web/src/search/searchQuery.ts`, `MAX_LIMIT = 200`; `Search.tsx`) en tests van 0.7.0
(`api/tests/test_search.py`, `e2e/test_zoek_z0.py`) sturen nog de vraag van 0.7.0: `type=archive`
en `limit` tot 200. Die past niet in `SearchParams` (geen `archive`, `limit` 1–50). Tot Z4 de web
op de vraag hierboven zet, neemt `GET /search` beide aan: een vraag die niet in `SearchParams`
past, gaat altijd naar `LikeFallback`, ook als Z1 `FtsEngine` aanzet; de rest gaat via
`select_engine`, met een gecontroleerde `SearchParams`. Z4 haalt die tak weg, samen met de
grenzen van 0.7.0 in de web en de tests. Test: `tests/search/test_search_overgang.py`. Het
harnas van de e2e (`e2e/search_harness.py`) start de app met `PLATFORM_SEARCH_INDEX=uit`: de
referentie op naam is die van 0.7.0; wie de index wil, geeft `zoekindex="aan"`.

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
| `app/routers/trash.py`, `app/userdelete.py` | wissen via de gedeelde helper in stukken. Herzien na de review van Z0 (haalbaar F3): de stukken maakt Z0 al, bij de aanroepers: `trash.purge_chunks` per `CHUNK` (2.000) nodes, `userdelete._drop_nodes` per 500, elk stuk een eigen transactie onder `BlobStore.lock` die opnieuw leest wat er nog weg moet; `delete_nodes` commit nooit. Z1 bouwt hier niets meer aan; `test_search_purge_batches.py` staat er al |
| `app/previews.py` | `register_ready(fn)`: na `preview.ready` (voor photometa v2) |
| `app/render.py` | ingang `text` (stub die `none` geeft); de plek voor `ocr_page` (stub) |
| `app/media/wire.py` | `Op.TEXT`, `Op.OCR` (de worker geeft `not_built` tot Z2 en X1) |
| `app/routers/search.py` | via `select_engine`; tot Z1 alleen `LikeFallback`. Herzien na de review van Z0 (haalbaar F4): een vraag van 0.7.0 die niet in `SearchParams` past (`type=archive`, `limit` boven 50) gaat altijd naar `LikeFallback`, tot Z4 de web op §3.4 zet (zie "De overgang" in §3.4) |
| `web/src/App.tsx`, `routes.tsx`, `router.ts` | het zoekveld (leeg), Ctrl/Cmd+K, de nieuwe routes met lege pagina's |
| `web/src/Beheer.tsx` | de tab "Zoeken en foto's" (leeg) |
| `web/src/viewer/InfoPanel.tsx` | plekken `LocationRow`, `PeopleRow` (elk een eigen bestand) |
| `web/src/ShareDialog.tsx` | een lazy plek voor de waarschuwing (`import()` bij openen) |
| `web/src/i18n/areas/*`, `web/src/i18n/errors/*` | de lege gebieden `search`, `photos_map`, `dupes`, `photos_people` (NL en EN) en de catalogi `search.ts`, `photos_map.ts`, `people.ts` |
| `web/scripts/budget.mjs` | regels voor de nieuwe chunks: `search`, `mapRoute` ≤ 30 kB, `mapData` ≤ 60 kB, `dupes`, `people` |
| `e2e/slim.sh` | de snelle e2e lokaal (±4 minuten): de wereld, zoeken, één upload met tekst, de toetsen |

**Hotspots van Z0b en A0** (nieuw in r3). Z0b raakt alleen bestanden die al van Z0 zijn, op de
dag vóór de review van Z0. A0 komt na de eerste integratie, als alle bestanden weer vrij zijn.

| Bestand | Wie | Wat |
|---|---|---|
| `app/search/types.py`, `app/analysis.py`, `app/prefs.py`, `app/jobs.py` | Z0b | de velden, `Step.EMBED`, `beeld`, de rij `analyse` (§3.3) |
| `app/previews.py` | Z0b | `register_large(fn)`: na de grote preview (Keuze 65) |
| `app/main.py` | Z0b | de stubs van X2 en X3 mounten (`501`); `q` van `mode=image` blijft in `SECRET_PARAMS` |
| `app/search/schema.py`, `api/migrations/versions/0010_zoeken.py` | Z0b | `node_bu_text` maakt het kader leeg (Keuze 25a); de plek voor de triggers van 0011 en 0012 |
| `web/src/viewer/InfoPanel.tsx`, `web/src/Beheer.tsx`, `web/src/i18n/areas/*`, `web/src/i18n/errors/*`, `web/scripts/budget.mjs` | Z0b | `LikeRow`; de blokken; `photos_faces` en `beeld`; `faces.ts` en `beeld.ts`; de regels voor hun chunks |
| `app/db.py` | A0 | `secure_delete = ON` en `temp_store = MEMORY` in de connect-haak (Keuze 64, K1-03) |
| `app/migrate.py` | A0 | `wal_checkpoint(TRUNCATE)` na elke migratie (§3.1a) |
| `app/jobs.py` | A0 | het checkpoint van elk uur, op een eigen verbinding met `busy_timeout` 0 en opnieuw met backoff (K2-08) |
| `app/routers/people.py` | A0 | drie haken: samenvoegen, een persoon van een foto halen, een persoon wissen (Keuze 63) |
| `app/routers/files.py`, de uploads, `files.place_file` | A0 | `node_origin` vullen bij upload, kopie en `place_file` (Keuze 69) |
| `app/main.py`, `app/cli.py`, `app/config.py` | A0 | de monitor van `analyse` in de lifespan; `analyse-status`, `analyse-bench`, `analyse-kwaliteit`, `beeld-*` (stubs); de instellingen van §3.11 |
| `Dockerfile`, `api/pyproject.toml`, `uv.lock` | A0 | het blok van §3.11 en de extra `analyse` |

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
| S5 | Tesseract in Debian 13: grootte, en of ffmpeg `libtesseract` al meebrengt | Keuze 24 (Jonas koos ja) | een getal in `docs/fase-6/spike.md`, gemeten in CI. **Gemeten: 69 MiB**, ffmpeg brengt het niet mee; de poort (+ ≤ 30 MB) wordt + ≤ 75 MB (Vraag 15: ja) |
| S6 | `world-110m.<hash>.json` maken en meten | Keuze 31 | ≤ 60 kB gzip, of een andere vereenvoudiging |
| S7 | Het zaad van 100.000 in CI | §4.4 | < 3 minuten |
| S8 | De twee vormen per woord (J1) en ß op 3.46.1 en 3.53.1 | Keuze 5, 21 | ß, ẞ, ligaturen en İ vinden naam én tekst; "grosse" en "große" vinden elkaar, ook "strasse" en "Straße" |
| S9 | `tools/zoekbench/bench.py`: alleen stdlib, maakt het schema van 0010 en een boom van 100.000 in een tijdelijke map, en meet zoeken voor "eigenaar" en "alles" | Keuze 54, B5 | draait in de container van 0.7.0 met `dc exec -T platform python -I - --los < bench.py`, in < 5 minuten, en ruimt op |
| S10 | De vormen van Keuze 4a op 3.46.1, onder "eigenaar" en "alles": alleen filters, de kaart, de duplicaten | C6 | per vorm een p95; haalt een vorm zijn poort niet, dan de tabel of de poort uit Keuze 4a, vastgelegd in `spike.md` |
| S11 | De handdruk van Keuze 17 in de echte worker-container: een tweede proces van uid 10002 probeert `/proc/<pid>/fd/N` van het kind te openen, vóór en na "klaar" | S6 | altijd `EACCES` (of het pad bestaat niet); nooit de inhoud van een blob |

Met S9 kan Jonas al in week 1 meten wat zijn CPU haalt, zonder iets te installeren. Z7 maakt er
`app.cli bench --los` van, met de uitkomst op `/beheer`.

**Herzien na de spike (S9).** De bench heeft bij 100.000 ±1,1 GB vrij nodig in zijn map: 1.056 MB
met de WAL, 525 MB na een checkpoint. Hij duurt ±70 s op 3 cores. De uitslag van S1–S11 staat in
[spike.md](spike.md).

### 3.8 Klaar als (Z0)

- 0010 heen en terug, ook zonder FTS5;
- `own_live` en `schema.TRIGGERS` staan vast, met hun tests;
- `photo_geo` volgt zijn node (prullenbak, terug, verplaatsen, opnamedatum, andere eigenaar) en
  de kaart zonder join is `own_live` (herzien na de review van Z0, haalbaar F2):
  `tests/search/test_triggers_geo.py`, de vorm `kaart` in `test_plans.py` en
  `test_spike_plannen.py`, en S10 in `tools/zoekbench/bench.py --vormen` (p95 ≤ 150 ms);
- alle routers gemount (stubs), de web-app bouwt met de lege pagina's en blijft binnen budget;
- de spike S1–S11 staat in `docs/fase-6/spike.md`, met wat het ontwerp daardoor verandert;
- de fixtures maken alles van §3.6; de wereld en de referentie hebben eigen tests;
- `e2e/slim.sh` is groen;
- de CI-jobs van §4.6 bestaan en zijn groen op de stubs;
- api, api-media, web, e2e, office en media-image zijn groen zoals in 0.7.0;
- Z0b: de contracten van X2 en X3 staan erin (§3.3), elke route van X2 en X3 geeft
  `501 not_built`, een vraag zonder `mode` geeft hetzelfde antwoord als zonder Z0b, en de tests met
  een exacte lijst eisen de nieuwe lijst.

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
| naar de prullenbak (bestand of map) | trigger: `dead` voor de submap | niets | rijen blijven; `own_live` laat ze weg; `photo_geo.dead` volgt in dezelfde transactie (`own_live_geo`) | weg uit zoeken, kaart, plaatsen, herinneringen, personen, duplicaten en slimme albums | `test_trash_subtree`, `test_own_live`, `test_triggers_geo` |
| terug uit de prullenbak | trigger: `dead` | niets | rijen zijn er weer; `photo_geo.dead` ook | terug | `test_restore`, `test_triggers_geo` |
| andere opnamedatum (`taken_at`) | — | — | `node_au_taken`: `photo_geo.taken_at` volgt | de omslag van een cel op de kaart | `test_triggers_geo` |
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

Gezichten en beeld voegen rijen aan deze tabel toe. Die staan bij X2, "Levensloop", en bij X3,
"Elke gebeurtenis". Twee regels gelden voor beide: een foto die een ander in jouw map zette, wordt
niet gescand tenzij die ander het zelf aan heeft (Keuze 69), en een andere eigenaar of nieuwe
inhoud wist de gezichten van de oude (de triggers van 0011) en maakt het bestand van beeld opnieuw
(de triggers van 0012).

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
   naar `DATA_ROOT/geo/` (bij Jonas `/opt/platform/data/platform/geo/`), elk met sha256. Lukt dat niet, dan
   zegt het script "Plaatsnamen: niet geladen" en gaat door; de volgende run probeert het weer.
3. Office netjes laten opslaan (`checkpoint`, zoals nu).
4. **Nieuw:** een reservekopie van de database, terwijl de oude app nog draait, met het image dat
   er nu is (dat van 0.7.1) en alleen de standaardbibliotheek:

   ```sh
   "${compose[@]}" run --rm --no-deps -T platform python -I -c "$text" /data/platform.db /data/backup </dev/null
   ```

   `$text` is `api/app/backup.py` van de nieuwe broncode (Z7; `app.cli db-backup` gebruikt
   dezelfde). Hij leest de alembic-head (`0009`), en schrijft met `sqlite3.Connection.backup` naar
   `/data/backup/platform-0009-<tijd>.db` (bij Jonas `/opt/platform/data/platform/backup/`), via
   een `.part` waarin de rijen van gezichten en beeld eerst weg gaan (Keuze 52), met rechten 600.
   Daarna blijft per head alleen de nieuwste, en de laatste kopie van vóór een migratie gaat nooit
   weg. Lukt de kopie niet, dan stopt het script hier: er is dan nog niets veranderd. Bij een
   eerste installatie (nog geen database) slaat het script dit over. Dit werkt met elk image, want
   `app.cli db-backup` bestaat in 0.7.1 niet (V9).
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
| OCR (X1) | per gescande pagina 3–8 s | ±70–110 min per 1.000 pagina's |

**Terug naar 0.7.1** (als 0.8.0 niet goed is). Herzien in r2 (C7): het script komt van de commit
van 0.7.1 zelf, niet van de branch. Zo draait het oude script met het oude image. `dc` is de
functie uit `infra/single/README.md` ("Handige commando's"): `docker compose` met precies de
bestanden die het script gebruikt. Herzien in golf 4 (Z7): 0.7.1 en `80d5bb8` in plaats van 0.7.0
en `1130e1b` (zie hieronder).

```bash
# 1. kijken of 0.7.1 er is, en het script van 0.7.1 in een bestand, vóór je iets stopt
git -C /opt/platform/src fetch -q origin
git -C /opt/platform/src cat-file -e '80d5bb8ce9cd937534f06a5bc5e4f54ec8509ad1^{commit}' && echo "goed: 0.7.1 is er"
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/80d5bb8ce9cd937534f06a5bc5e4f54ec8509ad1/infra/single/install.sh -o /root/platform-install-0.7.1.sh && echo "goed: het script is er"
# 2. stoppen, zodat niets meer naar de nieuwe tabellen schrijft
dc stop platform media
# 3. het schema terug, met het image van nu (vraagt om "ja")
dc run --rm --no-deps platform python -m app.cli db-downgrade 0009
# 4. 0.7.1 installeren, met het script van 0.7.1
PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 PLATFORM_SRC_REF=80d5bb8ce9cd937534f06a5bc5e4f54ec8509ad1 bash < /root/platform-install-0.7.1.sh
```

- De downgrade moet met het image van 0.8.0 (dat kent 0010). Daarom eerst stoppen, dan
  `db-downgrade`, dan pas het oude script. Zeggen de twee regels van stap 1 niet allebei "goed",
  dan stop je daar (zoals in het runbook van media).
- De downgrade gooit de index, de plekken, de hashes, de personen en de regels van albums weg.
  Bestanden, delingen, albums en alles van fase 1–5 blijven. Items uit een regel die niet gedeeld
  waren, haalt `db-downgrade` eerst uit het album (de vraag van §3.1): in 0.7.1 deelt een album
  elk item.
- Lukt de downgrade niet, dan is er de reservekopie van stap 4: stoppen, het bestand
  `platform-0009-<tijd>.db` terugzetten als `platform.db`, het script van 0.7.1
  (`docs/runbooks/zoeken.md`, "Terug naar 0.7.1").
- **Weer vooruit:** het script van de branch, zonder `PLATFORM_SRC_REF`. De index vult zich
  opnieuw (zie de tabel). Personen die je met de hand benoemde, zijn weg: dat staat in het runbook
  en in de bevestiging van `db-downgrade`.

  ```bash
  curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/claude/stappenplan-uitwerken-lu4uv3/infra/single/install.sh | PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 bash
  ```

- `80d5bb8` is de laatste commit vóór Z0: 0.7.1, met migratie 0009. Alleen docs verschillen van
  `245d87a`, de laatste commit met code van 0.7.1. Een tag `v0.7.1` erop is een keuze van Jonas;
  de commando's werken zonder. De CI-job `infra` doet precies deze stappen
  (`test_install_search.sh --stappen`, stap 6 en 7), met het script van die commit.
- **Waarom niet meer `1130e1b` en 0.7.0** (revisie 2 noemde die, met head `0008`): na `1130e1b`
  kwamen 0.7.1 (`e0496c6`, met migratie 0009) en nog twee commits tot `245d87a`. Jonas draait dus
  0.7.1. Terug naar 0.7.0 zou de indexen van 0009 weghalen en een script nemen dat 0.7.1 nooit
  draaide. Z7 zette in golf 4 de laatste commit vóór Z0 hier, met de head `0009` in `db-downgrade`
  en in de naam van de reservekopie; de stappen zelf bleven gelijk.

**Van 0.8.1 terug naar 0.8.0** (alleen met twee opleveringen, Vraag 13). Hetzelfde als hierboven,
met het script van de commit van 0.8.0 en `db-downgrade 0010`. De tabellen van 0011 en 0012 gaan
weg: de gezichten en de beeldindex. Alles van 0010 blijft. Ook `node_origin` gaat weg (hij zit in
0011): gaat Jonas daarna weer vooruit, dan tellen alle bestanden als door de eigenaar geüpload,
zoals bestanden van vóór de update (Vraag 9). De sleutels in `SLEUTELS_ROOT` en de modellen in
`MODELLEN_ROOT` blijven op schijf; het runbook (`docs/runbooks/analyse.md`) zegt hoe je ze wist.
`test_install_analyse.sh` doet die stappen (§4.6).

### 3.11 De container `analyse` (A0, X2a en X3a; Keuze 56–59, 68, 73, 75, 84)

Nieuw in r3. A0 legt de contracten vast: de handdruk, de enum `Op` met de opdrachten van X2 én
X3, het werkmodel met twee kinderen en de nep-worker. A0 zet ook het blok in de Dockerfile en de
optionele extra `analyse` in `api/pyproject.toml` en `uv.lock` (hieronder), zodat golf 5 op een
vast image bouwt. X2a bouwt de container, de worker en het
deel van `install.sh` voor gezichten. X3a voegt de dienst `analyse-omzetten` toe, X3b de
opdrachten van beeld.

**Dockerfile.** De regel `RUN uv sync --frozen --no-dev` (regel 24) blijft zoals hij is: de
basis van `/opt/venv` verandert niet (B19). Direct daarna komt dit blok (K2-09, K2-15).

```dockerfile
# Analyse (fase 6, Keuze 57): de extra `analyse` staat NIET in /opt/venv, maar in /opt/analyse
# (0750 root:analyse). Alleen de kinderen van de worker zetten die map in sys.path. Voor 10001 en
# 10002 bestaat numpy dan niet, ook niet als iemand de map zelf in sys.path zet (find_spec is
# None; haalbaarheid.md, K2-09). In /opt/venv met o-rwx zou `import numpy` een leeg
# namespace-pakket geven. Precies vijf pakketten: een zesde laat de build falen, want een nieuw
# pakket is een vraag aan Jonas (B19). chown en chmod in dezelfde RUN: een latere laag kopieert
# alle bestanden opnieuw.
# 10004 (omzetten) is de gebruiker van analyse-omzetten (X3, K1-05): groep analyse, zodat hij
# numpy kan lezen, maar zonder sleutel, zonder socket en zonder /models.
RUN groupadd --system --gid 10003 analyse \
    && useradd --system --uid 10003 --gid 10003 --no-create-home analyse \
    && useradd --system --uid 10004 --gid 10003 --no-create-home omzetten \
    && uv export --frozen --no-dev --no-emit-project --no-hashes --extra analyse \
       | grep -E '^[a-z]' | sort > /tmp/alles.txt \
    && uv export --frozen --no-dev --no-emit-project --no-hashes \
       | grep -E '^[a-z]' | sort > /tmp/basis.txt \
    && comm -23 /tmp/alles.txt /tmp/basis.txt > /tmp/analyse.txt \
    && test "$(wc -l < /tmp/analyse.txt)" -eq 5 \
    && uv pip install --python /opt/venv/bin/python --no-deps --compile-bytecode \
       --target /opt/analyse -r /tmp/analyse.txt \
    && rm /tmp/alles.txt /tmp/basis.txt /tmp/analyse.txt \
    && chown -R root:analyse /opt/analyse \
    && chmod -R o-rwx /opt/analyse && chmod 0750 /opt/analyse
```

In de `RUN` van media (`Dockerfile:42–45`) komt één regel bij: `install -d -o analyse -g platform -m
2750 /run/analyse`. Dat is hetzelfde patroon als `/run/media`: de socket krijgt groep platform, en
de app kan er niets in maken. `/srv/zelftest-10001` bestaat al; de zelftest van de analyse gebruikt
hem ook. tesseract (X1) komt in de `RUN` van apt (regel 19–21), niet hier.

De extra in `api/pyproject.toml`:

```toml
[project.optional-dependencies]
# Alleen voor de container analyse (fase 6, Keuze 57). De Dockerfile zet ze in /opt/analyse,
# nooit in /opt/venv. De app importeert ze nooit (test_no_numpy_in_app.py). onnxruntime brengt
# protobuf, flatbuffers en packaging mee.
analyse = [
    "numpy>=2.5.4",
    "onnxruntime>=1.31.1",
]
```

`uv.lock` pint vijf pakketten: numpy 2.5.4, onnxruntime 1.31.1, protobuf 7.36.2, flatbuffers
25.12.19 en packaging 26.3. packaging staat nu alleen via pytest in de dev-groep, dus is ook
nieuw in het image (K2-15) [gemeten: spike B1; `uv export` met en zonder de extra, zie
haalbaarheid.md]. Op schijf ±118–141 MB [gemeten: spike B1 en de proef met `--target`].

**`infra/single/compose.analyse.yaml`**

```yaml
# Analyse (fase 6, Keuze 56–59, 73): gezichten en beeld in een eigen container, zonder netwerk en
# zonder data. De app geeft elke preview als open file descriptor over de socket in analyse-run.
# install.sh neemt dit bestand mee, tenzij ANALYSE=uit in /opt/platform/.env staat.
#
# ANALYSE_WORKER_KEY (64 hex; install.sh maakt hem één keer en vervangt hem nooit) gaat naar de
# app en naar analyse, maar nooit in `command` en nooit naar analyse-omzetten (K1-05). Zonder
# sleutel stopt de worker met code 2, en zegt de app "down: key_missing". De rest van het
# platform werkt gewoon.
x-analyse-boot: &boot "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('app.analyse.worker',run_name='__main__',alter_sys=True)"
x-omzet-boot: &omzet "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('app.analyse.worker.omzetten.siglip',run_name='__main__',alter_sys=True)"

services:
  platform:
    environment:
      PLATFORM_ANALYSE_ENABLED: "true"
      PLATFORM_ANALYSE_WORKER: /run/analyse/worker.sock
      PLATFORM_ANALYSE_WORKER_KEY: ${ANALYSE_WORKER_KEY:-}
      PLATFORM_FACES_KEYS: /keys
    volumes:
      - analyse-run:/run/analyse
      # Keuze 68: de sleutels per eigenaar staan buiten DATA_ROOT, niet in een kopie van alleen de data
      - ${SLEUTELS_ROOT:-/opt/platform/sleutels/gezichten}:/keys

  analyse:
    image: platform:local
    pull_policy: never
    restart: unless-stopped
    user: analyse  # uid 10003: kan de bestanden van 10001 niet openen, en /opt/analyse wel
    # Geen `init: true`: docker-init is dumpbaar en heeft de sleutel in zijn omgeving (m1.md)
    command: [python, -I, -c, *boot, /srv, --socket, /run/analyse/worker.sock, --models, /models]
    environment:
      PLATFORM_ANALYSE_WORKER_KEY: ${ANALYSE_WORKER_KEY:-}
      OPENBLAS_NUM_THREADS: "1"
      OMP_NUM_THREADS: "1"
    network_mode: none
    read_only: true
    tmpfs:
      - /tmp:size=64m,mode=1777
      - /data:size=1m,mode=0700,uid=10003,gid=10003  # overschaduwt VOLUME /data
    volumes:
      - analyse-run:/run/analyse
      - ${MODELLEN_ROOT:-/opt/platform/modellen}:/models:ro  # van root, buiten $DATA_ROOT (K1-06)
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    pids_limit: 64
    ulimits:
      core: 0
      nofile: {soft: 256, hard: 256}
    healthcheck:
      test: [CMD, /usr/bin/env, -i, "PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin", python, -I, -c, *boot, /srv, --check, /run/analyse/worker.sock]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
    stop_grace_period: 5s

  # X3a (Keuze 73, 84; K1-05): zet SigLIP 2 één keer om. Alleen met `--profile omzetten`, en
  # alleen via install.sh. Geen sleutel, geen analyse-run, geen /models: een gekraakte omzetter
  # kan zich niet als worker voordoen en geen model vervangen. Root kopieert de uitkomst.
  analyse-omzetten:
    image: platform:local
    pull_policy: never
    profiles: [omzetten]
    user: omzetten  # uid 10004, groep analyse: leest /opt/analyse, verder niets van 10003
    command: [python, -I, -c, *omzet, /srv, --bron, /bron, --uit, /uit]
    environment:
      OPENBLAS_NUM_THREADS: "1"
      OMP_NUM_THREADS: "1"
    network_mode: none
    read_only: true
    tmpfs:
      - /tmp:size=64m,mode=1777
      - /data:size=1m,mode=0700,uid=10004,gid=10003
    volumes:
      - ${OMZET_BRON:?}:/bron:ro   # mktemp -d van root onder MODELLEN_ROOT (Keuze 84, stap 4)
      - ${OMZET_UIT:?}:/uit        # een lege map van 10004 (stap 5)
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    pids_limit: 16
    ulimits:
      core: 0

volumes:
  analyse-run:
```

`test_install_beeld.sh` controleert met `docker compose config` dat `analyse-omzetten` geen
variabele met `KEY` heeft, geen `analyse-run` en geen `/models` (K1-05). `test_install_analyse.sh`
controleert dat `/keys` en `/models` niet onder `$DATA_ROOT` liggen.

**`infra/single/compose.analyse-limits.yaml`.** `install.sh` neemt dit bestand alleen mee als
`limits_supported` ja zegt, zoals bij office en media.

```yaml
# Fase 6, Keuze 56 (K2-06, K2-07). 3g: alles tegelijk is ±1,9 GB (§3.11, het geheugen).
# Met media (3g) en office (2g) is dat 8 van de 16 GB van de LXC.
# cpu_shares 256 en niet 64: de container rekent ook de interactieve beeldvraag. Het kind werk
# heeft daarbinnen nice 19, dus de rest van de LXC gaat voor.
services:
  analyse:
    mem_limit: 3g
    memswap_limit: 3g
    cpus: 1.0
    cpu_shares: 256
  analyse-omzetten:
    mem_limit: 3g
    memswap_limit: 3g
    cpus: 1.0
```

**Het geheugen** (K2-06). Per proces, met alles tegelijk: een groepsronde, een volle cache en het
herladen van een bestand van 100.000 foto's.

| Proces | Wat | Geheugen |
|---|---|---|
| pid 1 | de handdruk, de rij per kind | ±40 MB [K2-06] |
| `werk` | gezichten vinden (YuNet en SFace) | maxrss 224 MB [gemeten: spike B6] |
| `werk` | het beeldmodel erbij | +165 MB [K2-06, uit B6] |
| `werk` | groeperen van 60.000 gezichten | +232 MB boven de import; maxrss 284,8 MB [gemeten: `groep2_60000`] |
| `zoeken` | de import | 53 MB [K2-06] |
| `zoeken` | het tekstmodel | +340 MB [gemeten: B6, de proxy] |
| `zoeken` | de tokenizer | ≤ 100 MB [doel: BZ7] |
| `zoeken` | de cache (`ANALYSE_BEELD_CACHE_MB`) | 400 MB |
| `zoeken` | een bestand van 100.000 foto's herladen (f16 → f32) | +307 MB, tijdelijk [K2-06] |
| samen | | ±1,9 GB [berekend: K2-06] |

Groeperen van 100.000 gezichten zou ±+400 MB kosten [geëxtrapoleerd: K2-06]. Daarom is
`PLATFORM_FACES_GROUP_MAX` 60.000: dat is wat de poort meet. `RLIMIT_DATA` is 1 GB voor `werk`
(±621 MB nodig) en 1,5 GB voor `zoeken` (±1,2 GB nodig). BZ5 meet het geheel met een groepsronde
en een herlaadactie tegelijk.

**De handdruk en de sleutel.**

- **Eigen code voor de handdruk.** `app/analyse/wire.py` heeft een eigen `PROTOCOL = 1` en een
  eigen label `platform-analyse-worker`. Het bewijs is
  `hmac_sha256(key, f"platform-analyse-worker|{PROTOCOL}|{nonce}|{uid}")`, met de uid van de app
  uit `SO_PEERCRED`.
- **Gedeelde code met media.** Het framing, `MAX_MESSAGE` (64 KB), `KEY_MIN` (32), `peer_uid` en
  het doorgeven van fd's (`SCM_RIGHTS`) importeert hij uit `app.media.wire`. Het label van media
  ligt vast in `media/wire.py:156` en verandert niet.
- **Geen hergebruik van een bewijs.** Een bewijs voor de ene worker werkt nooit bij de andere.
- **De client.** `app/analyse/client.py` doet wat `media.client._hello` doet: hij controleert
  de uid in het antwoord tegen `os.geteuid()`, en het bewijs [code: media/client.py].
- **De sleutel.**
  - `.env` heeft `ANALYSE_WORKER_KEY` (64 hex).
  - `add_key` maakt hem één keer en vervangt hem nooit.
  - In de app en in `analyse` heet hij `PLATFORM_ANALYSE_WORKER_KEY`. `analyse-omzetten` krijgt
    hem nooit.
  - De app weigert een sleutel die korter is dan 32 tekens, of gelijk aan `secret_key` of aan
    `MEDIA_WORKER_KEY`. De stand wordt dan `down`, met de reden `key_reused` of `key_missing`.
- **In pid 1.** Hij haalt de sleutel bij de start uit de omgeving (`os.environ.pop`) en zet
  `PR_SET_DUMPABLE` op 0, zoals de mediaworker. Zo kan geen ander proces van uid 10003
  `/proc/1/environ` lezen. De kinderen krijgen de sleutel nooit.
- **Opdrachten** (de enum `Op`, vanaf A0 compleet): `caps`, `cancel`, `faces`, `group`,
  `embed_image`, `embed_text`, `rank` en `forget` (Keuze 75). Wat nog niet gebouwd is, antwoordt
  `not_built`. Een vector staat nooit in een bericht: altijd in een verzegelde memfd, en `rank`
  krijgt voor "Meer zoals deze" een `num`, geen vector (K1-08).
- **Codes.** `ok`, `bad_request`, `not_built`, `busy`, `timeout`, `cancelled`, `unreadable`,
  `too_big`, `image_bad`, `model_missing`, `sandbox` en `internal`.

**De worker** (`app/analyse/worker/`; K2-02, K1-04, K1-07, K1-16).

- **pid 1** (`__main__.py`, `server.py`) neemt verbindingen aan, doet de handdruk en houdt de
  sleutel. Hij heeft per kind een rij van één: een tweede opdracht voor hetzelfde kind krijgt
  `busy`, een opdracht voor het andere kind niet. Elke opdracht heeft een deadline in het bericht;
  `cancel` met het nummer van een opdracht breekt hem af.
- **Twee kinderen** (`child.py`), elk met een eigen soort werk:

  | Kind | `nice` | Doet | Leeft |
  |---|---|---|---|
  | `werk` | 19 | previews decoderen (Pillow), gezichten (YuNet en SFace), beeld embedden, groeperen | `ANALYSE_CHILD_BATCHES` (8) opdrachten of 10 minuten; dan start pid 1 een nieuw |
  | `zoeken` | 0 | de tokenizer, het tekstmodel, de vectoren per eigenaar in de cache, `rank` en `forget` | lang (de cache); decodeert nooit een beeld |

  - pid 1 start elk kind met `exec` en een schone omgeving, met alleen `PATH` en de variabelen van
    OpenBLAS. Het kind heeft de sleutel dus nooit gezien.
  - **Het eerste wat een kind doet**, nog vóór het een fd krijgt: `prctl(PR_SET_DUMPABLE, 0)`
    (K1-04). `python` is `0755` in het image, dus zonder die regel is een kind na `exec`
    dumpbaar, en leest een ander proces van uid 10003 via `/proc/<kind>/fd` zijn memfd's
    [gemeten: K1-04]. Daarna zet het kind `RLIMIT_DATA` (`werk` 1 GB, `zoeken` 1,5 GB), `nice`,
    en pas dan `/opt/analyse` in `sys.path`.
  - **De wachter.** pid 1 draait de `Watch` van `app/media/worker/guard.py`: een kind met meer
    RssAnon + RssShmem dan zijn eigen `RLIMIT_DATA` gaat met `SIGKILL` weg (bij zijn PID), en de
    opdracht krijgt `too_big` (K1-07). Dat werkt ook als de host geen cgroup-grenzen kent.
  - **Decoderen.** `Image.open(fd, formats=["WEBP"])`, `MAX_IMAGE_PIXELS` 4 MP, en
    `warnings.simplefilter("error", Image.DecompressionBombWarning)` zoals
    `api/app/render.py:397–399`. Een preview tussen 4 en 8 MP is dus een fout (`too_big`), geen
    waarschuwing (K1-07).
  - **De modellen.** Het kind leest de bytes van een model één keer, rekent de sha256 over
    precies die bytes en vergelijkt met `models.lock` (of met het bestand van trust-on-first-use
    van X3, Keuze 73). Dan `InferenceSession(bytes, providers=["CPUExecutionProvider"])` met
    `intra_op_num_threads = 1`, en hij eist dat `get_providers()` alleen
    `CPUExecutionProvider` geeft. Het wiel heeft ook de `AzureExecutionProvider` [gemeten: spike
    B1] (K1-12).
  - **De logs** (K1-16). De stderr van een kind is een pipe naar pid 1. pid 1 logt per regel
    alleen dat er iets was en hoe lang het was, en bij een crash de exitcode. De inhoud gaat
    nergens heen, ook niet naar `docker logs`.
- **Een grens per opdracht.** Is de deadline op, dan zet pid 1 eerst `RunOptions.terminate`. Na
  5 s extra stopt hij het kind met `SIGKILL` (bij zijn PID) en start hij een nieuw kind. Een crash
  in onnxruntime of Pillow raakt alleen dat kind.
- **Wat de worker vasthoudt.** `werk` houdt niets vast tussen opdrachten: na elke opdracht zijn
  de memfd's dicht en de arrays weg, en na 8 opdrachten is het hele proces weg. `zoeken` houdt wel
  de vectoren van beeld van meerdere eigenaren in zijn cache (Keuze 75). Daarom decodeert
  `zoeken` nooit een beeld, is hij niet-dumpbaar, en haalt `forget(owner)` bij "uit" de vectoren
  van die eigenaar meteen weg (K1-08). De zin van revisie 2 "De worker houdt niets vast" is
  geschrapt (K1-04).
- **De zelftest** (`selftest.py`, zoals bij media) controleert:
  - de uid is niet 10001;
  - `CapEff` is 0 en `NoNewPrivs` is 1 in `/proc/self/status` (K1-04, zoals
    `media/worker/selftest.py:241–245`);
  - er is alleen `lo` in `/sys/class/net`;
  - `/data` is leeg;
  - `/srv/zelftest-10001` is niet te openen om te schrijven;
  - `/models` is niet schrijfbaar, en `/keys` bestaat niet.

  Faalt één van die controles, dan antwoordt een `RefusingWorker` op alles met `sandbox`, en
  zegt de app `down: sandbox`.

**Hoe de app merkt dat de analyse er niet is (Keuze 59).** `app/analyse/monitor.py` doet elke
30 s een handdruk met `caps`, zoals de mediaworker (m1.md). `analyse_problem()` geeft de reden
in het Nederlands, voor `/beheer`, het log en `app.cli analyse-status`.

| Stand | Wanneer | Wat de app doet |
|---|---|---|
| `off` | `PLATFORM_ANALYSE_ENABLED` is niet `true`: `ANALYSE=uit`, of een compose zonder `compose.analyse.yaml` | De rij `analyse` draait niet. `PATCH faces=aan` geeft `409 faces_unavailable`, beeld aanzetten `409 beeld_not_installed`. Wat er al is, blijft, en "uit" en wissen werken (die draaien in de rij `default`). Bevestigen en afwijzen werken; nieuwe voorstellen wachten. |
| `down` | Geen socket, verbinding geweigerd, verkeerde handdruk, `key_missing`, `key_reused`, `sandbox`, of geen antwoord binnen 5 s | Jobs wachten met `Defer(300)` en tellen geen poging. `/beheer` toont de reden. De web toont "Wacht op de analyse". Zoeken op beeld valt terug op gewoon zoeken. |
| `models_missing` | `caps` meldt een model dat ontbreekt of een verkeerde sha256 | `faces.scan` en `beeld.embed` wachten met `Defer(3600)`. Groeperen werkt wel: dat gebruikt alleen numpy. `/beheer` toont welk model ontbreekt. |
| `ok` | De handdruk is goed en de modellen kloppen | Alles draait. |

De instellingen van gezichten in de container van de app (een aanvulling op §3.2; die van beeld
staan bij X3):

| Variabele | Standaard | Wat |
|---|---|---|
| `PLATFORM_ANALYSE_ENABLED` | `false` | `compose.analyse.yaml` zet hem op `true` |
| `PLATFORM_ANALYSE_WORKER` | `/run/analyse/worker.sock` | de socket |
| `PLATFORM_ANALYSE_WORKER_KEY` | leeg | 64 hex; leeg is `down: key_missing` |
| `PLATFORM_FACES_KEYS` | leeg | de map met de sleutels per eigenaar (`/keys`, Keuze 68); leeg is `409 faces_unavailable` |
| `PLATFORM_FACES_BATCH` | `16` | foto's per job (Keuze 65) |
| `PLATFORM_FACES_MIN_PX` | `40` | het kleinste gezicht, als korte kant in de preview |
| `PLATFORM_FACES_MAX_PER_PHOTO` | `40` | het maximum aantal gezichten per foto |
| `PLATFORM_FACES_GROUP_MAX` | `60000` | open gezichten per groepsronde; daarboven de nieuwste (K2-06) |

`ANALYSE_CHILD_BATCHES` (8) en `ANALYSE_BEELD_CACHE_MB` (400) zijn constanten in de worker, niet
in de omgeving. `PLATFORM_INDEX_DEFER_TRANSCODES` (2) geldt ook voor de rij `analyse`. De
drempels staan niet in de omgeving: ze horen bij de versie van de modellen (X2, "Groeperen").

Nieuw in `.env` (`install.sh` zet de standaard één keer):

```
# Gezichten en beeld (fase 6, X2 en X3)
ANALYSE=aan                 # aan | uit: de container analyse en de modellen (Keuze 56, 58)
ANALYSE_WORKER_KEY=         # 64 hex; install.sh maakt hem één keer en vervangt hem nooit
BEELD=aan                   # aan | uit: het model voor zoeken op beeld downloaden (Keuze 84)
MODELLEN_ROOT=/opt/platform/modellen          # van root, buiten $DATA_ROOT (K1-06)
SLEUTELS_ROOT=/opt/platform/sleutels/gezichten  # buiten $DATA_ROOT; snapshots en backups: Keuze 68
```

`BEELD=aan` betekent dat het model er staat. Of een gebruiker zoeken op beeld aan heeft, is een
eigen keuze per gebruiker, standaard uit (Vraag 11).

#### De modellen (Keuze 58)

| Model | Licentie | Bytes | sha256 | Wat |
|---|---|---|---|---|
| `face_detection_yunet_2026may.onnx` | MIT | 229.738 | `ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0` | vindt gezichten, met een dynamische invoer |
| `face_recognition_sface_2021dec.onnx` | Apache-2.0 | 38.696.353 | `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79` | een vector van 128 getallen per gezicht (fp32) |

Wat niet kan:

- **De int8bq-varianten** draaien niet in onnxruntime 1.31.1 [gemeten: spike].
- **InsightFace** mag niet: de gewichten zijn niet voor commercieel gebruik.
- **int8 is niet nodig.** Beide modellen zijn fp32, en de snelheid is gemeten in fp32. VNNI op
  de CPU van Jonas maakt hier dus niets uit.

**`api/app/analyse/models.lock`.** Het formaat is `sha256 bytes naam url`, één regel per model.
`#` begint een opmerking. X3 voegt zijn regels toe aan hetzelfde bestand.

```
ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0 229738 face_detection_yunet_2026may.onnx https://media.githubusercontent.com/media/opencv/opencv_zoo/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_detection_yunet/face_detection_yunet_2026may.onnx
0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79 38696353 face_recognition_sface_2021dec.onnx https://media.githubusercontent.com/media/opencv/opencv_zoo/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_recognition_sface/face_recognition_sface_2021dec.onnx
```

**De licenties** (K1-13). `api/app/analyse/licenties/` heeft `yunet-MIT.txt` (met de regel
"Copyright (c) 2020 Shiqi Yu"), `sface-Apache-2.0.txt` en (X3) `siglip2-Apache-2.0.txt`.
`install.sh` schrijft ze naast de modellen, en `/over` noemt alle drie. Bij een eigen omzetting
van SigLIP 2 staat in de licentie van de uitkomst dat wij hem omzetten (Apache-2.0 §4(b)). De
herkomst van de trainingsdata is niet nagekeken (R36).

**Waar `install.sh` ze haalt** (K1-06). Er komen nieuwe functies, zoals die van media:

- `want_analyse` leest `ANALYSE` uit de omgeving: `aan`, `uit` of leeg.
- `analyse_supported` kijkt of `compose.analyse.yaml` er is (0.7.x heeft hem niet).
- `merge_analyse_env` doet `add_key ANALYSE aan`, `add_key ANALYSE_WORKER_KEY "$(hex)"`,
  `add_key BEELD aan`, `add_key MODELLEN_ROOT …` en `add_key SLEUTELS_ROOT …`.
- `analyse_dirs` maakt `MODELLEN_ROOT` (`0755 root:root`) en `SLEUTELS_ROOT` (`0700`, van 10001)
  aan. Elke map op die paden moet van root zijn, behalve de laatste van `SLEUTELS_ROOT`. Ligt een
  van beide onder `$DATA_ROOT`, of is een deel van het pad een symlink, dan weigert het script met
  een melding. Nooit `install -d`, `chmod` of `chown` onder een map van de app.
- `modellen_ophalen` leest `models.lock` uit de bron die klaarstaat. Per regel:
  - Is het bestand er al met de goede sha256, dan slaat hij het over.
  - Anders: `curl -fsSL --retry 3 --max-filesize <bytes>` naar een map van `mktemp -d` onder
    `MODELLEN_ROOT` (van root, `0700`). Daarna controleert hij de grootte en de sha256, en zet
    hij het bestand met `install -m 0644 -o root -g root` op zijn plek. Is het doel een symlink
    (`[ -L ]`), dan weigert hij.
  - Mislukt een download, dan is dat **niet fataal**. Het script zegt "Let op: <naam> kon niet
    geladen worden; gezichten wachten tot de volgende installatie", en de container start
    gewoon (`models_missing`).
  - Hij deelt de helper `ophalen <url> <bytes> <sha256> <doel>` met de download van `geo` (Z7).
  - Bestanden `*.onnx` die niet meer in `models.lock` staan, gaan weg.
  - De licentieteksten komen naast de modellen.
- **De vrije ruimte** (K2-19). `df --output=avail "$MODELLEN_ROOT"` voor de modellen (en voor
  X3 de tijdelijke 1,55 GB), en `df --output=avail "$DATA_ROOT"` voor de vectoren en de cache.
  `free_gb` kijkt naar de map van Docker en blijft alleen voor het image.
- `decide_analyse` zet `ANALYSE_ON`.
- `compose_files` krijgt een vierde argument, voor `compose.analyse.yaml` en (met
  `limits_supported`) `compose.analyse-limits.yaml`.
- `analyse_start` doet `up -d --no-deps analyse`. Is er een model veranderd, dan met
  `--force-recreate`. Daarna `exec -T platform python -m app.cli analyse-status --wait 20`.
  Een fout is alleen een waarschuwing, zoals bij `media_start`.
- `slot_analyse` hoort bij het slot van de installatie, net als bij media.
- `media_dirs` volgt dezelfde symlinks onder `$DATA_ROOT` (`install.sh:130`, bijvangst van
  K1-06). Dat lost Z7 op, met dezelfde test.

`test_install_analyse.sh` plant als uid 10001 een symlink op de plek van een model en van een
map, en eist dat `install.sh` weigert zonder het doel aan te raken (K1-06).

**Uitzetten.** Dat kan op drie niveaus:

| Niveau | Hoe | Wat er gebeurt |
|---|---|---|
| De server | `ANALYSE=uit` in `.env`, daarna `install.sh` | Geen download en geen container. `--remove-orphans` haalt een draaiende `analyse` weg. De stand is `off`. De modellen blijven in `MODELLEN_ROOT` tot je de map weggooit; het script zegt waar die staat. |
| De beheerder | "Gezichtsherkenning toestaan" uit, op `/beheer` | Alles van iedereen wordt gewist (eerst de sleutels), en iedereen staat op "uit" (Keuze 64) |
| Een gebruiker | De schakelaar bij de foto-instellingen | Alleen zijn eigen gezichten (standaard uit) |

**De versie.** `app/people/faces/version.py` heeft `FACES_VERSION = 1` en `LOCK_SHA`, de sha256 van
`models.lock`. `test_faces_version.py` faalt als `models.lock` verandert terwijl `FACES_VERSION`
gelijk blijft. Ook een wijziging in de stappen (X2, "Gezichten vinden") of de drempels (X2,
"Groeperen") verhoogt de versie. Wat een nieuwe versie doet, staat bij X2, "Bevestigen".

**De bron vermelden.** In de README en op `/over`, naast de bronvermelding van de plaatsen:
YuNet (MIT, opencv_zoo), SFace (Apache-2.0, opencv_zoo) en SigLIP 2 (Apache-2.0, Google; X3),
met hun licentietekst uit `api/app/analyse/licenties/` (Apache-2.0 §4(b)). Ook de
pakketten die met onnxruntime meekomen: protobuf, flatbuffers en packaging.

---

## 4. Tests en meten

### 4.0 Klaar als → bewijs

| Klaar als (stappenplan) | Bewijs | Job |
|---|---|---|
| Een zoekopdracht over 100.000 bestanden antwoordt binnen 200 ms | `api/tests/search/test_perf_100k.py` en `e2e/perf/zoek_100k.py` (§4.4): p95 ≤ 200 ms per toegangsprofiel (de mediaan van de p95 over 3 rondes), via HTTP, op SQLite 3.46.1; op de LXC `bench --los` | `zoek-perf` (hard) |
| OCR-tekst is doorzoekbaar | `e2e/test_zoek_ocr.py` (marker `ocr`, echte tesseract): een PDF zonder tekstlaag en een foto van een bon → zoeken op een woord dat alleen in het beeld staat → snippet → de viewer markeert het; `test_ocr_container.sh` met tesseract in het image | `ocr`, `media-image` (hard) |
| Gezichten zijn te benoemen en te zoeken | `e2e/test_zoek_personen.py`: een persoon met kader benoemen, zoeken op de naam, filteren; `test_people_privacy.py`. Automatisch vinden (X2): `e2e/test_zoek_gezichten.py` (nep-worker, alleen het toetsenbord) en `e2e/test_zoek_gezichten_echt.py` (de echte container) | `e2e`, `api`, `api-analyse`, `analyse-image` (hard) |
| Privacy van gezichten | `test_faces_off.py`, `test_faces_wipe_bytes.py` (0 merktekens), `test_faces_no_vector.py`, `test_faces_privacy.py` (ook Keuze 69), `test_faces_crypto.py`, `test_faces_backup.py`, `test_db_pragmas.py` | `api` (hard) |
| Gezichten en objecten, semantisch zoeken (X3) | `e2e/test_zoek_beeld.py` (nep-worker, alleen het toetsenbord); `test_beeld_search.py`, `test_beeld_access.py`, `test_beeld_never_out.py`, `test_beeld_off.py`; de kwaliteitspoort van Keuze 83 (BZ4); p95 ≤ 250 ms warm (BZ6) | `api`, `e2e`, `api-analyse`; `beeld-model` of de LXC |
| Full-text voor pdf, docx, xlsx, pptx, txt, md | `api/tests/extract/test_*.py` per formaat; `e2e/test_zoek_z2.py` | `api`, `e2e` |
| EXIF en GPS, kaart, offline plaatsnamen | `tests/geo/*`; `e2e/test_zoek_kaart.py` (telt verzoeken naar andere hosts: 0) | `api`, `e2e` |
| Duplicaten en bijna-duplicaten | `tests/dupes/*` (met de poort van Keuze 36), `e2e/test_zoek_dubbel.py` | `api`, `e2e` |
| Herinneringen en slimme albums | `tests/photos/test_memories.py`, `tests/albums/test_album_rules.py`, `test_album_share_new.py`, `e2e/test_album_regel.py` | `api`, `e2e` |
| Filters: type, datum, persoon, locatie, tag, bibliotheek | `tests/search/test_search_filters.py`: elk filter en elke combinatie van twee, tegen de referentie | `api` |
| Alles met het toetsenbord (sinds fase 3) | e2e met alleen het toetsenbord per scherm; `docs/fase-6/toetsenbord-z*.md` | `e2e` |

Eerlijk: de kwaliteit van gezichten en beeld op echte foto's is in de sandbox niet te meten (geen
echte gezichten, huggingface.co dicht). Die poorten draaien op de LXC van Jonas
(`analyse-kwaliteit`, `beeld-kwaliteit`), of in CI als de runner erbij kan (BZ1) en Jonas ja zegt
op Vraag 8 en 12. Tot dan staat in de README en op `/beheer` dat de kwaliteit niet gemeten is.
Schuift X3 naar een fase 6b (§2.0), dan blijft "semantisch zoeken" open. Zo staat het ook in de
README.

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
- `test_own_live.py`: elke fotofunctie gebruikt `own_live` (Keuze 25a). Herzien na de review van
  Z0 (veilig F4): de wachter kijkt per functie (en buiten functies per opdracht), niet per
  bestand; naar `node` én `photo_geo`, `photo_person`, `photo_ocr`, `blob_hash` en `dup_pair`
  (de tabel na `FROM`/`JOIN`, of het model); en neemt `own_live_geo` aan voor `photo_geo`;
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
| gezichten (X2), in de container | gemaakte foto's van 1024 × 768 met één gezicht | ≥ 250 foto's per minuut | ±365–420 [K2-04] |
| beeld (X3), in de container | dezelfde foto's, uit de preview van 256 | ≥ 500 foto's per minuut | ±750–870 [K2-04] |

**CPU en geheugen naast OnlyOffice en media** (`e2e/perf/zoek_samen.py`, B). Drie stappen, elk
5 minuten:

1. **Zonder achtergrondwerk** (`paused`): de belastingtest van fase 5 (`app.cli media-loadtest
   --sessies 3 --duur 300 --extern`), 5 bewerkers in OnlyOffice die typen en opslaan, en 2
   zoekvragen per seconde. De nulmeting.
2. **Met achtergrondwerk, zoals standaard**: hetzelfde, terwijl `index` vol staat (5.000
   documenten, 5.000 foto's, 500 scans voor X1) en daarna `analyse` vol (5.000 foto's met
   gezichten en beeld aan). `Defer` staat aan. Tijdens de backfill van de index ook één beeldvraag
   per 5 s: de p95 en het aandeel terugval komen in het rapport (K2-07).
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
| zoeken op beeld (X3) | p95 ≤ 250 ms, ook met `werk` vol (Vraag 14); de terugval is een rapport |
| de container `analyse` | geen `OOMKilled`; maxrss ≤ 2,5 GB (BZ5) |

**Waar:** in CI (`e2e-media-perf`) een korte versie als rapport (60 s, 1 sessie, zonder
OnlyOffice); in de integratie de hele test in de sandbox als poort (2 sessies media en 3
bewerkers, binnen 8 GB); op de LXC via het runbook `zoeken.md`.

### 4.6 CI

Geen nieuwe actions: alleen wat `ci.yml` al gebruikt (onder meer `actions/checkout@v4`,
`astral-sh/setup-uv@v6`, `docker/setup-buildx-action@v3`, `docker/build-push-action@v6`,
`actions/upload-artifact@v4`). Nieuwe jobs mogen; ze gebruiken die actions. Een job die iets meet,
drukt aan het eind een blok `=== kort ===` af, omdat de logs alleen de staart tonen.

| Job | Wanneer | Wat erbij komt | Soort |
|---|---|---|---|
| `api` (bestaat) | elke push | alle nieuwe tests, de wachters van §4.1, churn op 3.53.1; `test_places.py` met de bronnen voor plaatsen (gedownload met sha256); `uv sync --frozen` **zonder** de extra, en `test_no_numpy_in_app.py`; laag 1 van X2 en de tests van X3 met de nep-worker. `api/tests_analyse/` valt buiten zijn `testpaths` (K2-10) | hard |
| `api-media` (bestaat) | elke push | de opdracht `text` met de nep-worker en de echte; `test_media_text_handshake.py` | hard |
| `api-analyse` (nieuw, X2a) | elke push | `uv sync --frozen --extra analyse`; `api/tests_analyse/` met een telling van het aantal tests; laag 2 (nepmodellen) en 3 (de echte modellen van `models.lock`); de omzetter op de kleine vorm; met ja op Vraag 8 ook de set van NASA. Geen artefact met beelden of vectoren | hard |
| `analyse-image` (nieuw, X2a) | elke push | build; de modellen; `test_analyse_container.sh`; `test_analyse_kinderen.py`; `e2e/test_zoek_gezichten_echt.py` | hard |
| `beeld-model` (nieuw, X3a) | in `push` en `pull_request`, alleen als een stap met `git diff --name-only` (checkout met `fetch-depth: 0`) een wijziging ziet in `app/analyse/worker/omzetten/**`, `app/analyse/tokenizer.py`, `app/analyse/onnxw.py` of `app/analyse/models.lock`, of als de tekst van de PR een regel `beeld-model: ja` heeft (K2-03). Er is geen `workflow_dispatch` | BZ1–BZ4 en BZ6: de bron downloaden, omzetten, de sha256's vergelijken met `models.lock`, de tokenizer (BZ3), de kwaliteitspoort, de tijden. Zonder cache. `=== kort ===` en het verslag via `upload-artifact` (K2-20) | hard als hij draait |
| `zoek-perf` (nieuw) | elke push | in `python:3.12-slim`: migratie, plan-tests (S4), churn op 3.46.1, de boom van 100.000, §4.4; `test_faces_perf.py` | hard (de p95), rapport (p99, max) |
| `ocr` (nieuw, X1) | elke push | `apt-get install tesseract-ocr tesseract-ocr-nld tesseract-ocr-eng tesseract-ocr-osd fonts-dejavu-core` op de runner; `pytest -m ocr`; `e2e -m ocr`; de poort van §4.5 | hard |
| `e2e` (bestaat) | elke push | marker `zoek` (Chromium), ook `test_zoek_gezichten.py` en `test_zoek_beeld.py` met de nep-worker; de marker-regel krijgt `not analyse` | hard |
| `media-image` (bestaat) | elke push | de handdruk (S11) in de echte worker-container; tesseract in het image en `test_ocr_container.sh`; de installatietest met `ANALYSE=uit`, zodat deze job geen modellen downloadt | hard |
| `images` (bestaat) | elke push | de grootte van het image (tesseract + ≤ 75 MB, Vraag 15; de extra + ≤ 160 MB); `find_spec("numpy")` en `find_spec("onnxruntime")` zijn `None` voor uid 10001 en 10002, en `numpy.ndarray` bestaat voor uid 10003 | hard |
| `infra` (bestaat) | elke push | `test_install_search.sh` (reservekopie met het image van 0.7.1 en van 0.8.0, de download van `geo`, terug naar 0.7.1 en weer vooruit; herzien in golf 4, zie §3.10); `compose config` voor de combinaties met `analyse`; `test_install_analyse.sh` en `test_install_beeld.sh` (symlinks, sha256, `df`, niet fataal) | hard |
| `web` (bestaat) | elke push | begrotingen (ook `mapData`, `mapRoute`, en de chunks van gezichten en beeld), i18n streng, vitest; `world-110m.<hash>.json` opnieuw maken en vergelijken | hard |
| `perf` (bestaat, rapport) | elke push | `e2e/perf/zoek_ui.py` | rapport |
| `e2e-media-perf` (bestaat, rapport) | elke push | `zoek_samen.py --kort`, met `analyse` (nep-worker) | rapport |

**Markers.** `api/pyproject.toml` (pytest) en `e2e/pytest.ini` krijgen `analyse`, `analyse_echt`
en `beeld_echt`, zoals `media`.

**Minuten.** ±10–15 minuten runner-tijd per push voor de kern [schatting], en ±8–12 voor X2
[schatting], vooral de build van `analyse-image` en de modellen (±39 MB). `beeld-model` draait
alleen als zijn bestanden veranderen; dan kost hij ±1,55 GB downloaden en het omzetten. De jobs
lopen naast elkaar.

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

Herzien in r2 (C8) en in r3 (K2-01, K2-11). Zoals fase 5: per golf twee pakketten in eigen
worktrees, de e2e en de metingen achter één slot (nooit meer dan 3 zware processen), dan een dag
voor review, fixer en nacontrole, dan samenvoegen in de volgorde van de tabel en alle checks.

| Dagen | Wat | Samenvoegen |
|---|---|---|
| 1–3 | **Z0**: Z0-api, Z0-web en Z0-e2e naast elkaar; de spike S1–S11 op dag 1–2. | Z0-api, Z0-web, Z0-e2e |
| 4 | **Z0b**: de contracten van X2 en X3 in de bestanden van Z0 (Keuze 55). 0010 is dan nog niet gepusht. | Z0b |
| 5 | Review van Z0 en Z0b, fixer, nacontrole. | — |
| 6–9 | **Golf 1**: Z1 en Z2 naast elkaar. | Z1, dan Z2 |
| 10 | Review, fixer, nacontrole van golf 1. | — |
| 11–14 | **Golf 2**: Z4 en Z6 naast elkaar. | Z4, dan Z6 |
| 15 | Review van golf 2. Zoeken, personen, herinneringen en slimme albums zijn af. | — |
| 16–19 | **Golf 3**: Z3 en Z5 naast elkaar (Z5 tegen de contracten). | Z3, dan Z5 |
| 20 | Review van golf 3; de e2e van Z5 tegen de echte Z3. | — |
| 21–23 | **Golf 4**: Z7 en X1. | Z7, dan X1 |
| 24 | Review van golf 4. | — |
| 25–27 | **Integratie 1**: alle checks, de belastingtest (§4.5) als poort, metingen naar `docs/fase-6/metingen.md`, de README, **0.8.0**, tag `v0.7.0`. | — |
| 28–29 | **A0**: het fundament van X2 en X3 (§2, §3.1a, §3.11). | A0 |
| 30–33 | **Golf 5**: X2a (dag 30–32) en X3a (dag 30–33) naast elkaar. Aan het eind van dag 33 de go/no-go van X3 (Keuze 73). | X2a, dan X3a |
| 34 | Review van golf 5. | — |
| 35–39 | **Golf 6**: X2b (dag 35–39) en X3b (dag 35–38) naast elkaar. | X3b, dan X2b |
| 40 | Review van golf 6. | — |
| 41–43 | **Integratie 2**: alle checks, de belastingtest met `analyse` vol (§4.5), de metingen van X2 en X3, de README, **0.8.1**. | — |

Samen ±43 werkdagen (≈ 9 weken). Na dag 27 is 0.8.0 er met de kern; na dag 43 0.8.1 met
gezichten en beeld (Vraag 13). Met één oplevering (Vraag 13b) vervalt de integratie op dag 25–27,
en duurt de laatste integratie 4 dagen: 0.8.0 rond dag 41.

**De go/no-go van X3** (aan het eind van dag 33, K2-12). Ja als de omzetter op de kleine vorm
gelijk is aan de eigen numpy-versie (fp32 ≤ 1·10⁻⁵, int8 cosinus ≥ 0,99) en twee keer dezelfde
sha256 geeft, en als BZ1 zegt of de runner bij de bron kan. Nee: X3 gaat naar een fase 6b met
dit ontwerp, X3b vervalt in golf 6, en X2b krijgt die dagen als marge. De kern en X2 wachten
nergens op.

**Wat er gebeurt als Jonas antwoordt:**

- **Vraag 4 (b)**: GPS uit downloads halen, ±2 dagen: een pakket naast Z7 in golf 4 (die wordt
  dan 4 dagen), of in fase 10. **(c)**: een keuze per gebruiker, in Z6.
- **Vraag 8 ja**: de set van NASA in `api-analyse` (in X2b). Nee is de standaard: de kwaliteit
  meet Jonas op zijn LXC.
- **Vraag 9 (b) of (c)**: één regel in de stap `faces` en `embed` (Keuze 69); geen dagen erbij.
- **Vraag 10 (b)**: de omzetting in een CI-job en een bestand bij een release; `install.sh`
  downloadt dan het omgezette model. Zelfde dagen, maar wij verspreiden de gewichten. **(c)**: B/16
  in plaats van B/32: dezelfde omzetter, ±3× zo veel rekenen [B6].
- **Vraag 11 (b)**: `beeld` aan voor iedereen; de backfills starten na die van de index.
- **Vraag 12**: (a) nee betekent geen kwaliteitspoort in CI, alleen op de LXC; (b) ja voegt in
  `beeld-model` een vergelijking met torch toe (in X3a).
- **Vraag 13 (b)**: één oplevering rond dag 41 (zie hierboven).
- **Vraag 14 nee**: dan geldt 200 ms ook voor beeld. Dat halen we bij 100.000 foto's niet zeker
  (K2-13); de terugval naar gewoon zoeken gebeurt dan vaker, en de poort in CI wordt 200 ms.

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
| R15 | Jonas antwoordt niet op Vraag 4 en 8–14 | onduidelijk wie de plek ziet, hoe de kwaliteit gemeten wordt, en hoe we opleveren | elke vraag heeft een standaard waarmee de bouw doorgaat; de README zegt welke standaard geldt; Vraag 1, 2, 3, 5, 6 en 7 zijn beantwoord of ingetrokken |
| R16 | De runner in CI is wisselend snel | `zoek-perf` faalt zonder reden | de mediaan van 3 rondes; alleen p99 en max mogen naar een rapport; de p95 blijft hard |
| R17 | Twee verschillende bestanden heten "bijna zeker gelijk" | iemand gooit het verkeerde weg | vlakke beelden zonder hash, de tweede controle, klieken, tekst telt mee, nooit iets vooraf aangevinkt, alles via de prullenbak (Keuze 35, 36) |
| R18 | Vectoren van gezichten (X2) zijn biometrische data van derden | privacy, ook in WAL, tijdelijke bestanden, reservekopieën en snapshots | per node, alleen eigen en levend, standaard uit, een verbod; versleuteld met een sleutel per eigenaar buiten `/data` ("uit" wist eerst de sleutel); `secure_delete`, `temp_store` in het geheugen, TRUNCATE tot het lukt, een test op merktekens; nooit in de API, de logs of een bericht; de reservekopie zonder gezichten (Keuze 64, 68; K1-01, K1-03) |
| R19 | Natural Earth 10m is ±1 km nauwkeurig en kent de enclaves van Baarle niet | een foto vlak bij een grens krijgt het verkeerde land | staat bij de bronvermelding; de plaats moet in hetzelfde land liggen; later een fijnere bron (Keuze 29) |
| R20 | Een eigenaar snapt "alleen voor jou" bij een slim album niet | hij denkt dat Bram iets ziet wat Bram niet ziet, of andersom | het merkje per item, de telling in het deeldialoog, de vraag bij elke deling (Keuze 42) |
| R21 | Vraag 13 blijft open terwijl de bouw loopt | onduidelijk of 0.8.0 de kern is of alles | de standaard is twee opleveringen; de volgorde zet de kern vooraan, en na dag 15 en dag 27 is er telkens een bruikbare stand |
| R22 | De drempels zijn op echte gezichten niet te meten in de sandbox | te veel of te weinig voorstellen | `analyse-kwaliteit` op de LXC tegen Jonas' eigen "ja" en "nee" (Vraag 8; met ja ook de set van NASA in CI); de drempels in `drempels.py` met de versie; 0,45 is strenger dan die van OpenCV (0,363); niets wordt vanzelf bevestigd |
| R23 | De CPU van Jonas is trager dan de sandbox | de backfill van gezichten duurt langer dan ±2,2–2,5 uur per 55.000 foto's [K2-04] | `analyse-bench` op de LXC; `/beheer` toont de schatting; fp32, dus VNNI maakt voor gezichten niet uit; `cpus: 1.0` houdt drie cores vrij |
| R24 | Het image wordt 118–141 MB groter, ook met `ANALYSE=uit` [B1] | meer schijf en een tragere update | gemeten; de poort ≤ +160 MB; een apart image kan later, als de grootte gaat tellen |
| R25 | De backfill concurreert met OCR, tekst en media | trager werk op de voorgrond | `Defer`, `nice` 19, `cpu_shares: 256`, één thread; pas na de backfill van de index (K2-07); §4.5 met `analyse` vol als poort in de integratie |
| R26 | Een open lezer houdt de WAL vast | gewiste bytes blijven langer staan | opnieuw tot `busy = 0`, op een eigen verbinding met `busy_timeout` 0 (K2-08); daarna `Defer(60)`; `/beheer` zegt het; de test met een lezer |
| R27 | CI kan de modellen of de set van NASA niet bereiken | `api-analyse` en `analyse-image` rood | `curl --retry 3`; vaste commit en sha256; verdwijnt een bron, dan faalt de lock duidelijk en is een nieuwe bron een nieuwe vraag. Nooit overslaan. |
| R28 | protobuf, flatbuffers en packaging komen mee met onnxruntime (K2-15) | meer code in het image | vastgepind in `uv.lock`; precies vijf pakketten, anders faalt de build; alleen de kinderen van de worker laden ze; genoemd in de README; geen nieuwe vraag, want ze horen bij het "ja" voor onnxruntime |
| R29 | Iemand bevestigt per ongeluk (J) | een verkeerde persoon op een foto | de melding met "Ongedaan maken" en Ctrl+Z; `reset`; een persoon van een foto halen maakt het "nee" |
| R30 | Een gemaakte preview laat onnxruntime of Pillow crashen | de worker hangt of valt om | alles in het kind, met `RLIMIT_DATA`, de wachter en een harde grens (`terminate`, daarna `SIGKILL`); `failed` zonder nieuwe poging; de container is afgeschermd (K1-07) |
| R31 | `beeld-model` draait niet als het moet (K2-03) | een andere omzetting of tokenizer zonder echte meting | de trigger op de paden via `git diff`, of een regel in de PR-tekst; `models.lock` verandert alleen samen met een groene run; de reviewdag controleert het |
| R32 | De omzetter lukt niet in de dagen van X3a (K2-12) | X3 schuift | de go/no-go op dag 33; dan X3 naar een fase 6b en X2 gaat door (§5) |
| R33 | Het geheugen van de container loopt op met groeperen, de cache en een herlaadactie tegelijk (K2-06) | `OOMKilled`, of zonder cgroup-grenzen een krappe LXC | `mem_limit: 3g`; `RLIMIT_DATA` en de wachter per kind; `GROUP_MAX` 60.000; de cache ≤ 400 MB; BZ5 meet het samen |
| R34 | Een overgenomen app vervangt een model of plant een symlink (K1-06, K1-12) | een ander model geladen, of root schrijft buiten de map | `MODELLEN_ROOT` van root, buiten `$DATA_ROOT`; `install.sh` weigert symlinks; de worker rekent de sha256 over de bytes die hij laadt |
| R35 | Z0b past niet in dag 4 (K2-01) | de review van Z0 schuift | Z0b is klein en additief; lukt het niet, dan schuift golf 1 één dag, en wordt het geen werk midden in golf 5 |
| R36 | De herkomst van de trainingsdata van YuNet, SFace en SigLIP 2 is niet nagekeken (K1-13) | een juridisch risico dat we niet kennen | de licenties van de gewichten staan het gebruik toe [B4]; de README zegt eerlijk dat de trainingsdata niet is nagekeken |
| R37 | De AVG bij gebruik buiten het gezin (K1-11) | bijzondere persoonsgegevens (art. 9 AVG) zonder toestemming van wie op de foto staat | standaard uit; een alinea in de README en het runbook; niet door een jurist nagekeken |
| R38 | De map met sleutels komt toch in een snapshot of de kopie buiten huis (K1-01) | "uit" maakt die kopieën niet onleesbaar | `SLEUTELS_ROOT` buiten `/data`; het runbook zegt hoe je hem uitsluit; `/beheer` en de tekst bij het aanzetten zeggen het |
| R39 | Twee opleveringen (Vraag 13) | twee keer bijwerken en twee keer integreren | hetzelfde `install.sh` en de reservekopie per head; met (b) één oplevering rond dag 41 |
| R40 | Onze eigen graaf rekent net anders dan het origineel | slechtere treffers | de omzetter tegen een eigen numpy-versie (vorm); de kwaliteitspoort met het echte model (BZ4); met ja op Vraag 12b een vergelijking met torch |
| R41 | CI komt niet bij huggingface.co of de foto's | geen echte meting in CI | BZ1 zegt het meteen; dan `beeld-kwaliteit` en `beeld-bench` op de LXC als poort, en trust-on-first-use voor de sha256 (Keuze 73); `/beheer` zegt "niet nagerekend in CI" |
| R42 | De CPU van Jonas heeft geen VNNI | int8 duidelijk trager: vragen boven 250 ms, een langere backfill | `beeld-bench` op de LXC (BZ9); de klok met terugval; de backfill wijkt toch al |
| R43 | Het geheugen loopt over 3 GB met gezichten, beeld en grote eigenaren | de container wordt `OOMKilled`, of zonder cgroup-grenzen de LXC krap | een grens op de cache (400 MB) met `too_big`; `tokenizer.bin`; `RLIMIT_DATA` en de wachter per kind; `GROUP_MAX` 60.000; BZ5 (K2-06, K1-07) |
| R44 | Onze tokenizer wijkt af van die van SigLIP 2 | verkeerde tokens, slechte treffers | BZ3 met het bestand van SigLIP 2 zelf; de kwaliteitspoort vangt grove fouten |
| R45 | int8 kost te veel kwaliteit | slechtere treffers | poort: int8 tegen fp32 ≤ 0,05 (BZ4); anders de tekstkant in fp32 als het geheugen het toelaat |
| R46 | Een vraag komt terwijl de backfill op dezelfde core loopt | trage vragen | twee kinderen met `nice` 19 voor `werk`; `cpu_shares: 256`; de backfills na die van de index; het rapport in BZ6 en §4.5 (K2-07) |
| R47 | Een vector zegt iets over wat er op een privéfoto staat, of herkent een persoon | privacy | standaard uit (Vraag 11) tot BZ10; alleen de eigenaar; nooit in API, bericht of log; niet in de reservekopie; weg met de blob, met "uit" (ook `forget`) en met "wissen"; staat op `/beheer` en in de README (Keuze 82) |
| R48 | Te weinig schijf: 1,55 GB tijdelijk, ±400 MB model, 205 MB per 100.000 foto's in de database | de installatie of de backfill loopt vast | `df` op `MODELLEN_ROOT` (≥ 2,5 GB) en `DATA_ROOT` vóór de download, niet fataal (K2-19); de groottes op `/beheer`; de bron weg na het omzetten |

---

## 6. Verwerkte en afgewezen kritiek

### Revisie 3: verwerkte en afgewezen kritiek (K1 en K2)

De ontwerpen voor X2 en X3 kregen 40 punten van kritiek: 16 over privacy, veiligheid en licenties
(K1-01 tot K1-16: 1 blokkerend, 10 belangrijk, 5 klein) en 24 over haalbaarheid, planning en
contracten (K2-01 tot K2-24: 3 blokkerend, 11 belangrijk, 10 klein). Elk punt is nagelopen in de
code (van `245d87a` en, alleen gelezen, van Z0), in de spike en, waar het kon, met de proef van de
critici in de sandbox. Geen punt bleek onjuist. Alle 40 zijn verwerkt; van een paar voorgestelde
fixes is een deel anders gedaan. Dat staat onder "Afgewezen (revisie 3)", met de reden.

#### Verwerkt: privacy, veiligheid en licenties (K1)

| Kritiek | Fix |
|---|---|
| K1-01 (blokkerend). De tekst bij het aanzetten belooft dat niemand de vector ziet en dat reservekopieën hem niet hebben; met de snapshots van Jonas is dat niet waar. | Vectoren versleuteld met een sleutel per eigenaar in `SLEUTELS_ROOT`, buiten `/data` en de snapshots; "uit" wist eerst de sleutel (Keuze 68). De tekst is eerlijk over de beheerder en over snapshots. De reservekopie van het platform zonder gezichten (Keuze 52). R38. |
| K1-02. Een bestand dat een ander in jouw map zet, wordt gescand onder jouw toestemming. | `node_origin` in 0011; niet scannen tenzij die ander het zelf aan heeft, een uploadlink nooit (Keuze 69, Vraag 9). Geldt ook voor beeld. |
| K1-03. SQLite schrijft vectoren naar tijdelijke bestanden buiten de database. | `temp_store = MEMORY` in de connect-haak (A0), met `test_db_pragmas.py`; `ix_face_new` zodat er niet gesorteerd wordt (§3.1a). |
| K1-04. De container is zwakker afgeschermd dan de mediaworker. | Elk kind eerst `prctl(PR_SET_DUMPABLE, 0)`, vóór het een fd krijgt; `werk` start opnieuw na 8 opdrachten of 10 minuten; CapEff 0 en NoNewPrivs 1 in de zelftest; een test met een tweede proces van uid 10003 (Keuze 56, §3.11). |
| K1-05. De omzetter via `compose run … analyse` erft de sleutel en de socket. | Een eigen dienst `analyse-omzetten` (profiel `omzetten`, uid 10004), zonder sleutel, socket en `/models`, zonder netwerk; `compose config` in `test_install_beeld.sh` (Keuze 73, §3.11). |
| K1-06. De app kan onder `$DATA_ROOT` de map met modellen vervangen. | `MODELLEN_ROOT` (`/opt/platform/modellen`), elke map van root; downloaden in `mktemp -d`; symlinks geweigerd; `test_install_analyse.sh` plant er een (Keuze 58, 84). |
| K1-07. Geen eigen grenzen op geheugen en decoderen. | `RLIMIT_DATA` per kind, de wachter van `guard.py`, alleen WebP openen, 4 MP en de waarschuwing voor een decompressiebom als fout (`render.py:397–399`) (Keuze 56, 60). |
| K1-08. "Uit" bij beeld wist niet alles. | `blob_embed` niet in de reservekopie; `forget` naar de container; `node_ad_beeld`; de tekst zegt wat er blijft (Keuze 76, 82). |
| K1-09. Beeld standaard aan zonder te meten of een portret iemand herkent. | Standaard uit (Vraag 11) tot BZ10 gemeten heeft. |
| K1-10. Vraag 8 ging over auteursrecht, niet over biometrie. | Vraag 8 herschreven; standaard nee; `analyse-kwaliteit` op de LXC, alleen getallen. |
| K1-11. De AVG wordt niet genoemd. | Een alinea in de README en in `docs/runbooks/gezichten.md` (huishoudelijke uitzondering; anders art. 9). Niet door een jurist nagekeken (R37). |
| K1-12. De sha256 wordt over andere bytes gerekend dan die geladen worden. | Eén keer lezen, de sha256 over die bytes, dan `InferenceSession(bytes)` met alleen `CPUExecutionProvider` (Keuze 58). |
| K1-13. De naamsvermelding en de licentieteksten ontbreken. | `api/app/analyse/licenties/` met de teksten van YuNet (MIT, met de copyrightregel), SFace en SigLIP 2 (Apache-2.0); naast de modellen; `/over` en de README noemen alle drie. De trainingsdata: R36. |
| K1-14. De licentie van de Gemma-tokenizer stond verkeerd in de spike. | Gemma komt nergens meer voor (BZ3); het label in [haalbaarheid.md](haalbaarheid.md) is verbeterd naar Apache-2.0. |
| K1-15. "Niet tonen" laat de vector in de WAL. | `hide` en `reset` zetten een `faces.wipe` met `scope=checkpoint` klaar (X2, "Privacy"). |
| K1-16. stderr van het kind komt in `docker logs`. | Via een pipe naar pid 1, die alleen een code en een lengte logt; `test_analyse_kinderen.py` (Keuze 56). |

#### Verwerkt: haalbaarheid, planning en contracten (K2)

| Kritiek | Fix |
|---|---|
| K2-01 (blokkerend). X2 en X3 wijzigen in golf 5 de contracten die Z0 bevriest. | Z0b op dag 4, vóór de review van Z0 (Keuze 55, §3.3). |
| K2-02 (blokkerend). Twee werkmodellen voor één container. | Eén model in A0: de kinderen `werk` en `zoeken`, `busy` per kind, een deadline en `cancel` (Keuze 56, §3.11). |
| K2-03 (blokkerend). `beeld-model` met `workflow_dispatch` kan niet draaien. | Een trigger op de paden of een regel in de PR-tekst, binnen `push` en `pull_request` (§4.6). |
| K2-04. De kosten per foto waren 25–30% te laag. | Opnieuw gerekend met een WebP van 1024 × 768: X2 ±142–164 ms per foto; X3 uit de preview van 256 (±69–80 ms). De poorten ≥ 250 en ≥ 500 per minuut (§4.5). BZ4 meet of 256 even goed is. |
| K2-05. YuNet crasht op een hoogte die geen veelvoud van 32 is. | Opvullen met nullen tot een veelvoud van 32; een test met 1024 × 683, 683 × 1024 en 1023 × 767 (Keuze 60). |
| K2-06. Het geheugen komt tot ±2,0 GB. | `GROUP_MAX` 60.000, `mem_limit: 3g`, de tabel in §3.11; BZ5 meet met groeperen erbij. |
| K2-07. `cpu_shares: 64` maakt een beeldvraag traag. | `cpu_shares: 256`; `analyse` pas na de backfill van de index; een beeldvraag in §4.5 stap 2. |
| K2-08. Het checkpoint blokkeert schrijvers ±5 s. | Een eigen verbinding met `busy_timeout` 0 en opnieuw met backoff; `test_faces_wipe_latency.py` (≤ 100 ms extra). |
| K2-09. `o-rwx` in `/opt/venv` geeft een leeg namespace-pakket. | `--target /opt/analyse` (0750 root:analyse); `find_spec` in `images` (Keuze 57); nagemeten in de sandbox (B1). |
| K2-10. Tests die numpy importeren, breken de job `api`. | `api/tests_analyse/` buiten de `testpaths`, met een telling in `api-analyse` (X2, "Tests"). |
| K2-11. De planning klopte niet. | ±43 werkdagen in twee opleveringen, of ±41 met één (§5, Vraag 13). |
| K2-12. De omzetter heeft geen referentie. | De test tegen een eigen numpy-versie, een go/no-go op dag 33, en met ja op Vraag 12b torch (Keuze 73). |
| K2-13. 200 ms is voor beeld niet te halen bij 100.000 foto's. | 250 ms voor beeld, 200 ms blijft voor gewoon zoeken (Vraag 14); meten bij 55.000 en 100.000 en de eerste vraag na een nieuw bestand; het bestand vooraf laden. |
| K2-14. De tokenizer leunt op het bestand van Gemma. | Alleen het bestand van SigLIP 2 en een vaste lijst in het repo (BZ3). |
| K2-15. packaging wordt niet genoemd. | Vijf pakketten, in Keuze 57, de README en de licenties. |
| K2-16. `test_migration_0010` werd zwakker. | `upgrade('0010')` en `['0010']`, zoals Z0 met 0009 deed (§3.1a). |
| K2-17. De triggers stonden op twee plekken. | Eén `TRIGGERS` in `app/search/schema.py` (§3.1a). |
| K2-18. Het pad van de migraties was fout. | `api/migrations/versions/` (§2.0, §3.1a). |
| K2-19. De ruimte werd op de verkeerde map gecontroleerd. | `df` op `MODELLEN_ROOT` en `DATA_ROOT` (Keuze 84). |
| K2-20. Voorstellen staan alleen in de log. | In `=== kort ===` en in het verslag via `upload-artifact`. |
| K2-21. Gezichten passen niet in `FailedItem`. | Alleen aantallen per code (Keuze 67, 80). |
| K2-22. Beeld stond standaard aan. | Vraag 11, standaard uit; de backfill na die van de index. |
| K2-23. De cache had alleen `gen` als sleutel. | (eigenaar, `gen`, sha256), met `test_beeld_cache.py` (Keuze 75). |
| K2-24. Het ontwerp ging uit van 500 GB. | Die 500 GB klopt voor de LXC van Jonas: één rootdisk op ZFS, zonder `/tank`. De tabel in Keuze 84 rekent de ruimte uit; die past ook op het voorbeeld van `infra/single/README.md` (64 GB). |

#### Afgewezen (revisie 3)

| Kritiek | Waarom niet |
|---|---|
| K1-01, deel 3: de reservekopie via het geheugen maken (`:memory:`, dan `VACUUM INTO`). | Dat werkt (de critici toonden het), maar kost zoveel geheugen als de hele database (±370 MB per 100.000 bestanden, R8, plus de vectoren), in de container van de app terwijl die draait. Met de versleuteling (Keuze 68) staan in de `.part` alleen versleutelde vectoren, en `secure_delete` met `journal_mode = OFF` overschrijft ze vóór de `rename`. |
| K1-01, deel 2: de plek van de sleutels als vraag aan Jonas. | De standaard (`/opt/platform/sleutels/gezichten`) ligt buiten `DATA_ROOT`; `SLEUTELS_ROOT` in `.env` verandert hem. Wat dat betekent voor snapshots en backups van de hele container, staat in Keuze 68 en in het runbook, met een eigen mount point als manier om ze eruit te houden. Dat is beheer van de LXC, geen keuze in het ontwerp. |
| K1-04: `zoeken` en `werk` elk een eigen uid. | De aanval (een tweede proces van dezelfde uid leest `/proc/<kind>/fd`) sluit `PR_SET_DUMPABLE` 0 al voor elk kind, met een test. Een tweede uid vraagt een eigen groep op `/opt/analyse` en `/models`, en meer om te testen, voor geen andere winst. |
| K1-04: het kind `werk` opnieuw starten bij elke andere eigenaar. | De rij gaat om de beurt per eigenaar (Keuze 65), dus dat is bijna elke batch: dan laadt het kind steeds de modellen opnieuw. Na 8 opdrachten of 10 minuten houdt de blootstelling klein. |
| K2-03: het model in `actions/cache` op sha256. | `ci.yml` gebruikt `actions/cache` nu niet, en een nieuwe action mag niet. `beeld-model` draait alleen als zijn bestanden veranderen, dus de download is zeldzaam. |
| K2-06: de `.vec` in f16 houden en per blok omzetten. | Twee van de drie voorgestelde wegen zijn genomen (`GROUP_MAX` 60.000 en `mem_limit: 3g`). Per blok omzetten is een optimalisatie die BZ5 kan afdwingen; zonder meting voegt het code toe op het pad van elke vraag. |
| K2-07: het kind `zoeken` in een eigen kleine container. | Dat verdubbelt de handdruk, de sleutel en het tekstmodel in het geheugen. `nice` 0 tegen 19, `cpu_shares: 256` en de backfills na die van de index doen hetzelfde; BZ6 meet het. Haalt BZ6 de 250 ms niet, dan komt dit terug. |
| K2-04: één decode delen voor gezichten en beeld. | Het andere voorstel van K2-04 is gekozen: beeld rekent uit de preview van 256 (±2,4 ms decoderen). Een gedeelde opdracht zou X2 en X3 aan elkaar koppelen. |

### Revisie 2: verwerkte en afgewezen kritiek (S en C)

Revisie 1 kreeg 35 punten van kritiek: 18 over veiligheid en privacy (S1–S18) en 17 over
haalbaarheid en bouw (C1–C17). Ik heb elk punt nagelopen in de code van `1130e1b`, in de notities
en, waar het kon, met een meting (V1–V10). Geen punt bleek onjuist; alle 35 zijn verwerkt. Wat
hieronder bij "Afgewezen kritiek" staat, zijn delen van voorgestelde fixes die ik anders deed, met
de reden.

#### Verwerkt

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

#### Afgewezen kritiek

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
