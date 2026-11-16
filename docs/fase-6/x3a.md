# X3a: de tokenizer en de omzetter van SigLIP 2

X3a is het deel van X3 in golf 5, naast X2a ([bouwontwerp.md](bouwontwerp.md), "X3 — Zoeken op
wat er op een foto staat"; Keuzes 73, 74, 77 en 83; §4.6; K2-03, K2-12, K2-14, K2-20). X3a
bouwt wat X3b in golf 6 nodig heeft om op beeld te zoeken, en het bewijs dat het model in onze
eigen vorm werkt:

- `app/analyse/onnxw.py`, een ONNX-schrijver met alleen de standaardbibliotheek (stap 0);
- `app/analyse/tokenizer.py` met het formaat `tokenizer.bin` (Keuze 74);
- de omzetter `app/analyse/worker/omzetten/` (Keuze 73) en de licentie van SigLIP 2;
- de tests van de omzetter tegen een eigen numpy-versie (`api-analyse`) en de tests met het
  echte model (marker `beeld_echt`);
- de vragen van de kwaliteitspoort, de lijst verwachte tokens van BZ3 en het hulpmiddel
  `tools/beeld/beeld_model.py`, dat de set voorstelt en nakijkt;
- de CI-job `beeld-model` (BZ1–BZ4, het model-deel van BZ6, BZ7).

Niet in X3a (golf 6, X3b): de opdrachten `embed_image`, `embed_text`, `rank` en `forget` met hun
cache, `app/beeld/**`, de routes, de web, de functies voor beeld in `install.sh`,
`test_install_beeld.sh` en `app.cli beeld-*`. Migratie 0012 en de triggers zijn van A0 en
bevroren; golf 5 voegt geen migratie toe.

X3a is gebouwd op de tak `wip/golf5-x3a`, vanaf `a284b47`, in de worktree `wt-g5-x3a`, naast
X2a (`wip/golf5-x2a`, dat vanaf stap 0 `b1a128e` begon). Er is niet gepusht. Op de LXC van Jonas
verandert er niets: X3a raakt `install.sh` en compose niet, en de versie blijft 0.8.0.

## Gebouwd

### Stap 0: `onnxw.py` (`b50bde3`, `b1a128e`)

Zie het resultaat van stap 0 in de workflow: de schrijver (IR 8, opset 17, `com.microsoft` 1
alleen als een node het gebruikt), dezelfde bytes bij dezelfde aanroepen, gewichten als buffer of
als callable die pas bij het schrijven een buffer geeft, en schrijven in stukken met de sha256
onderweg. `tests/analyse/test_onnxw.py` (64, job `api`) leest elk veld terug met een eigen
protobuf-lezer; `tests_analyse/test_onnxw_ort.py` (42) laat onnxruntime 1.31.1 elke bewerking
narekenen tegen numpy (grootste verschil 6,4·10⁻⁷).

### De tokenizer (`f3c241f`)

`app/analyse/tokenizer.py`, alleen de standaardbibliotheek:

- SentencePiece-BPE met byte fallback zoals `tokenizers` het tokenizer.json van SigLIP 2 uitvoert:
  eerst de toegevoegde tokens (links het langste), dan kleine letters **per teken** (de slot-sigma,
  B5), spatie wordt `▁`, dan per teken een stuk, of `<0xXX>` per byte, of `<unk>`; samenvoegen op
  rang, bij gelijke rang de meest linkse (een heap over een gelinkte lijst); bij een dubbel paar in
  de merges telt het laatste, zoals de Rust-bibliotheek;
- afkappen op 63, `<eos>` (1), `<pad>` (0) rechts tot 64;
- `tokenizer.bin` (`PLTOKBIN`, versie 1): de stukken als één blok UTF-8 met offsets, de merges als
  paren van ids met hun rang (gesorteerd, zoeken met `bisect`), de tekens die zelf een stuk zijn,
  de 256 byte-ids en de toegevoegde tokens. Laden is een paar keer `array.frombytes`;
- elke `str` is een geldige vraag: losse surrogaten worden U+FFFD, alleen de eerste 2.000 tekens
  tellen, en een vraag staat nooit in een fout of op stderr. Een kapot bestand geeft
  `TokenizerFout` bij het laden.

`api/tests/beeld/test_tokenizer.py` (job `api`, 89 tests; na de review 94, na de nacontrole 95;
±3 s): de tabel van het ontwerp, "hond op het strand", "ΣΟΦΙΑ ΟΔΟΣ", byte fallback, afkappen,
`tokenizer.bin` gelijk aan de JSON (ook met fuzz op een vaste seed), geen kwadratische tijd op lange
of vreemde invoer, geen tekst in een fout, kapotte en te grote bestanden, en BZ7 op een bestand met
256.000 stukken.

### De omzetter (`5d8adeb`, `e4c8c99`)

`app/analyse/worker/omzetten/`:

- `siglip.py`: de opdrachtregel `--bron /bron --uit /uit [--fp32]`, gestart zoals de dienst
  `analyse-omzetten` het doet: `python -I -c OMZET_BOOT /srv --bron /bron --uit /uit`. numpy komt
  uit `/opt/analyse`, dat pas vóór de eerste import achteraan in `sys.path` komt. Code 0 klaar, 2
  een bron of map die niet klopt (één Nederlandse zin, geen traceback, de uitvoermap blijft leeg),
  1 iets onverwachts. Op stdout de regels voor `models.lock`, op stderr de tijd en maxrss;
- `safetensors.py`: de kop met de standaardbibliotheek, streng: een grens op de grootte van de kop,
  JSON, elk dtype bekend (F32, F16, BF16 voor gewichten), vorm × grootte = lengte, offsets binnen
  het bestand, aaneengesloten en zonder overlap. Een gewicht komt via `numpy.memmap` en wordt
  meteen float32;
- `bouw.py`: de betekenis van elke laag uit `config.json` en de namen (`model_type` moet `siglip`
  zijn; `hidden_act` tanh of erf; een tensor die ontbreekt, te veel is of een andere vorm heeft,
  is een fout). Twee grafen:
  - beeld: Conv met stap p, `position_embedding`, de lagen, `post_layernorm`, de MAP-kop (probe,
    `in_proj`, `out_proj`, layernorm, MLP), L2;
  - tekst: `token_embedding` + `position_embedding` (64), de lagen, `final_layer_norm`, de laatste
    positie, `text_model.head`, L2;
  - int8 zoals Keuze 73: elke Linear `DynamicQuantizeMatMul` met int8-gewichten, een schaal per
    kolom en nulpunt 0; q, k en v van een laag in één MatMul; de embedding van de tekst in int8 met
    een schaal per rij (Gather, Cast, Mul); de Conv van de patches blijft fp32 [CI op `7569d48`:
    het gewicht van een Linear nu met |q| ≤ 64, de embedding met 127; zie daar];
  - elke initializer is een callable: er staat één gewicht tegelijk in het geheugen, de embedding
    in blokken van 4.096 rijen;
  - uitvoer: `beeld.int8.onnx`, `tekst.int8.onnx`, `tokenizer.bin`, `meta.json` (`logit_scale`,
    `logit_bias`, de voorbewerking uit `preprocessor_config.json` met de grootte, de resample van
    Pillow, schaal, gemiddelde en std, en de tekstkant: lengte, pad, eos, kleine letters) en
    `siglip2-Apache-2.0.txt`; met `--fp32` ook `beeld.fp32.onnx` en `tekst.fp32.onnx` (alleen
    voor BZ4). Altijd dezelfde bytes: geen BLAS, geen tijd of pad in de uitvoer, gesorteerde
    sleutels;
  - `tokenizer_config.json` mag tot 16 MB zijn (`MAX_TOKENIZER_CONFIG`), de andere JSON tot 1 MB.
    Zegt hij `padding_side` of `truncation_side` iets anders dan "right", `add_bos_token` aan,
    `add_eos_token` uit, of een `pad_token`/`eos_token` dat niet bij tokenizer.json past, dan stopt
    de omzetter. `max_position_embeddings` onder 64 in `config.json` ook.
- `app/analyse/licenties/siglip2-Apache-2.0.txt`: byte voor byte de Apache-tekst die A0 voor SFace
  gebruikte (`test_models_lock.py::test_de_licenties` heeft de nieuwe exacte lijst).

`api/tests_analyse/siglip_klein.py` maakt de kleine vorm (2 lagen, breedte 64, dezelfde namen als
het echte bestand) met de standaardbibliotheek. `test_beeld_omzetten.py` (71 tests; na de review
86): de voorwaartse stap van een eigen numpy-versie in de test, fp32 ≤ 1·10⁻⁵, int8 cosinus ≥ 0,99,
twee omzettingen in twee processen met dezelfde sha256, maxrss en tijd per proces (`wait4`),
`meta.json`, F16 en BF16, 18 liegende koppen (een kop langer dan het bestand of van 2⁶³ bytes,
offset buiten het bestand of negatief, overlap, een gat, afgekapt, een onbekend dtype, een lengte
die niet bij de vorm past, geen JSON…; ook als proces: code 2 en geen traceback), een bron die niet
klopt (`model_type` clip, een laag te veel of te weinig, een tensor die ontbreekt of te veel is,
NaN, een andere voorbewerking…), een uitvoermap die niet leeg is, een fout halverwege die niets
achterlaat, en dat de app numpy niet laadt.

### De vragen, de lijst van BZ3 en het hulpmiddel (`b820fdd`, `a2fb1de`)

- `api/tests/analyse/sets/beeld_vragen.tsv`: 24 vragen in het Nederlands (twee per categorie), 6
  combinaties met een vaste foto en 6 in het Engels. `test_beeld_sets.py` houdt de vorm vast.
- `api/tests/analyse/sets/beeld_tokens.tsv`: 27 regels verwachte stukken, met de hand afgeleid en
  nooit met onze tokenizer gemaakt: het gemeten voorbeeld van B5, kleine letters, losse tekens,
  woorden die één stuk zijn, bytes (`<0xEE>`…) en afkappen. De job zoekt de id van elk stuk op in
  de woordenlijst van het echte bestand (gewone JSON) en kijkt per soort een voorwaarde na; klopt
  die niet, dan zegt de test dat de lijst niet klopt, niet de tokenizer.
- `tools/beeld/beeld_model.py` (alleen de standaardbibliotheek, `python3 -I` op de runner, nooit in
  het image), met de stappen `bereik` (BZ1: huggingface.co, commons.wikimedia.org,
  upload.wikimedia.org en images-assets.nasa.gov), `bron`, `vergelijk`, `set` en `uitslag`. Alles
  wat hij haalt gaat eerst naar een `.part` en dan pas, na grootte en sha256, op zijn plek. De
  User-Agent noemt geen persoon, e-mail of repository. `test_beeld_tools.py` (42 tests; na de
  review 45) draait hem tegen een eigen HTTP-server met een nep-API van Hugging Face en van Commons.

### De tests met het echte model (`147ca76`)

Marker `beeld_echt`, alleen in de job `beeld-model`; `addopts` sluit hem uit, en zonder model of
set faalt een test (`pytest.fail`), hij slaat nooit over. `tests_analyse/beeld_echt.py` heeft de
gedeelde stukken: het model laden met onnxruntime (CPU, één draad), de voorbewerking uit
`meta.json`, de kans `sigmoid(scale · cos + bias)`, het verslag (`bzN.json`, `kort.txt`).

- `test_beeld_tokenizer_echt.py` (BZ3, BZ7): de lijst van `beeld_tokens.tsv` op het echte
  tokenizer.json, via `from_json` en via `tokenizer.bin`; een eigen, naïeve BPE in de test
  (gewone JSON, rang en meest links, byte fallback per teken) tegen onze tokenizer op 2.000
  zinnen met een vaste seed; `tokenizer.bin` gelijk aan `from_json`; BZ7 in drie verse processen.
- `test_beeld_kwaliteit.py` (BZ4): de poorten van Keuze 83 met de omgezette int8- en fp32-modellen,
  de tokenizer en `256.webp` zoals `app.render.image` hem maakt. Daarnaast als rapport: Engels,
  `256.webp` tegen `1024.webp` (Keuze 77), de verdeling van de kansen voor `MIN_TEKST` en
  `MIN_FOTO`.
- `test_beeld_echt_perf.py` (BZ6, het model zelf, onder `taskset -c 0`): foto's per minuut per
  core uit `256.webp` met het beeldmodel in int8, en één vraag in woorden (tokenizer plus
  tekstmodel), 200 keer warm.
- `test_beeld_echt_hulp.py` (niet `beeld_echt`, in `api-analyse`, 10 tests): de rekenstukken van
  die tests zonder het model, zodat een rode poort in de job een uitkomst van het model is en
  niet van de test.

### De job `beeld-model` (`3184131`)

In `.github/workflows/ci.yml`, als §4.6 zegt. Zie [de runbook](../runbooks/analyse.md#zoeken-op-beeld-x3)
voor wat een mens na een run doet.

1. Een stap `poort` met `git diff --name-only` (checkout met `fetch-depth: 0`) over
   `app/analyse/worker/omzetten`, `tokenizer.py`, `onnxw.py` en `models.lock`: bij een push van
   `before` naar de commit, bij een PR van de basis naar de kop, bij `synchronize` van `before`
   naar `after`. Een regel `beeld-model: ja` in de PR zet hem ook aan. De tekst van de PR komt via
   `env` binnen, nooit als expressie in het script, en komt niet in het log. Geen
   `workflow_dispatch`. De bron komt nooit uit een cache, de foto's van `beeld.tsv` wel (sinds
   [CI op `f16ed5a`](#ci-op-f16ed5a)).
2. `uv sync --frozen --extra analyse`.
3. BZ1 `bereik`, dan `bron` (de vijf bestanden, of het voorstel), dan twee keer omzetten met
   precies de opdrachtregel van de dienst (`uit1`, `uit2`) en een derde keer met `--fp32`
   (`uitfp32`, alleen voor BZ4), dan `vergelijk` (BZ2: sha256 van `uit1` en `uit2`, tijd en
   maxrss ≤ 1,5 GB, `models.lock`; van `uitfp32` dezelfde vijf bestanden, maxrss alleen in het
   log), dan `set` (BZ4: de 120 foto's). BZ4 rekent met `uitfp32`, BZ3 en BZ6 met `uit1` (sinds
   de review van golf 5, uitrol F1 en F2).
4. pytest `-m beeld_echt` voor BZ3/BZ7, BZ4 en BZ6 (die onder `taskset -c 0`); een `SKIPPED` telt
   als rood.
5. `uitslag`: rood zolang er een voorstel ligt ("models.lock mist de regels van SigLIP",
   "beeld.tsv ontbreekt"). Dan het verslag via `actions/upload-artifact@v4` en als laatste stap
   het blok `=== kort ===`.

`tests/analyse/test_beeld_ci.py` (6 tests bij X3a, nu 8) houdt die vorm vast: alleen `push` en
`pull_request`, de bron nooit in een cache (sinds `f16ed5a` alleen de foto's van `beeld.tsv`), de
paden, geen `${{` in het script van de poort, dezelfde opdrachtregel als `OMZET_BOOT` (na de
samenvoeging ook tegen `app/analyse/runtime.py` van X2a), elke BZ een stap, en `beeld_echt` in geen
andere job.

## De go/no-go van X3

Keuze 73 en K2-12 noemen drie delen. De beslissing valt na CI, niet hier.

| Deel | Waar bewezen | Stand |
|---|---|---|
| De kleine vorm rekent als een eigen numpy-versie | `test_beeld_omzetten.py` in `api-analyse` (hier gedraaid) | **gehaald hier**: fp32 grootste verschil 2,5·10⁻⁷ (beeld) en 1,1·10⁻⁷ (tekst), grens 1·10⁻⁵; int8 kleinste cosinus 0,99969 (beeld) en 0,99985 (tekst), grens 0,99 [CI op `7569d48`: met \|q\| ≤ 64 voor een Linear beeld 0,99941, tekst 0,99970]; twee keer dezelfde sha256 |
| Twee keer omzetten geeft dezelfde sha256 (BZ2) | de stap `vergelijk` in `beeld-model` | **in CI na de push**. Met het echte model nog niet gedaan: huggingface.co is vanuit de sandbox dicht. Op de kleine vorm hier wel (drie keer, zelfde bytes) |
| De kwaliteitspoort is groen (BZ4) | `test_beeld_kwaliteit.py` in `beeld-model` | **in CI na de push**, en pas als `beeld.tsv` er is: de eerste run stelt de set voor en is rood. Daarna een tweede run met de set |

Wat de eerste run na de push laat zien: of de runner bij de bron kan (BZ1), de commit van
`google/siglip2-base-patch32-256`, de voorgestelde regels voor `models.lock` en de voorgestelde
set. De job is dan rood met "models.lock mist de regels van SigLIP" en "beeld.tsv ontbreekt"; zo
is het bedoeld. BZ3 en BZ7 draaien in die eerste run al op het echte tokenizer.json. BZ4 en BZ6
draaien ook, op de voorgestelde set, en hun getallen staan in het verslag, maar de uitslag is pas
geldig met de vastgezette set.

[Nacontrole golf 5: deel 1 is op de kop van de nacontrole opnieuw gemeten, met dezelfde getallen
(zie De nacontrole). Deel 2 is met het echte model nergens gedaan; de review deed het op ware
grootte met willekeurige gewichten (hieronder, "BZ2 op ware grootte"). Deel 3 is nergens gedaan:
de job is pas streng met de pins in `models.lock` en `beeld.tsv`, en het runbook zegt de pins pas
samen met X3b te zetten (uitrol F3). Een groene, strenge BZ4 komt dus op zijn vroegst met X3b; de
eerste run geeft alleen de getallen op de voorgestelde set. §5 van het ontwerp vraagt voor de
go/no-go deel 1 en BZ1, Keuze 73 en K2-12 alle drie. Wat geldt, beslist een mens; hier is niets
beslist.]

## Metingen

Hier gemeten, in de sandbox, onder `taskset -c 0-2`. Met het echte model is hier niets gemeten
(de bron is dicht); die getallen komen uit de job.

| Wat | Hoe | Uitkomst |
|---|---|---|
| Omzetter, kleine vorm (2 lagen, breedte 64), met `--fp32` | drie processen zoals de dienst, `wait4` | 0,22 / 0,23 / 0,24 s; maxrss 71,5 MiB elk; dezelfde sha256 |
| Kleine vorm, fp32 tegen numpy | grootste absolute verschil | beeld 2,47·10⁻⁷, tekst 1,10·10⁻⁷ (grens 1·10⁻⁵) |
| Kleine vorm, int8 tegen numpy | kleinste cosinus | beeld 0,99969, tekst 0,99985 (grens 0,99) [CI op `7569d48`: met \|q\| ≤ 64 voor een Linear beeld 0,99941, tekst 0,99970] |
| Kleine vorm, grootte | bytes | `beeld.int8.onnx` 981.742, `tekst.int8.onnx` 161.642 (fp32: 1.412.840 en 517.284) |
| BZ7 synthetisch: `tokenizer.bin` met 256.000 stukken en ±600.000 merges (13,6 MB) | `from_bin` vijf keer; maxrss-groei in een vers proces | 0,117 s; +13,3 MB (grens 0,5 s en 100 MB); de test in `api` eist het |
| Tokenizer, één vraag | `encode` op de kleine woordenlijst | ±50 µs |
| `onnxw`, 300 MiB aan gewichten schrijven | callables tegen arrays (stap 0) | maxrss 30 → 55 MiB met callables (één tensor tegelijk); dezelfde sha256 |
| De job op een nepbron (rook-test met het kleine model en 120 gemaakte foto's) | de stappen van de job met de hand | de code van BZ3, BZ4, BZ6 en BZ7 loopt; BZ4 rood, zoals verwacht bij willekeurige gewichten; BZ6 3,7 ms per foto (niets waard: het model is klein) |

Verwachting voor het echte model (Keuze 73, 77): bron 1,55 GB; maxrss van één omzetting ≤ 1,5 GB
(BZ2); BZ6 750–870 foto's per minuut per core [berekend, K2-04], poort ≥ 500; tekstmodel 62–74 ms
[B6]. Niet gemeten.

## Afwijkingen

- **`beeld_echt` staat in `addopts`** (`not beeld_echt`), zodat de gewone run en `api-analyse`
  hem niet verzamelen. De job geeft `-m beeld_echt`; een `-m` op de opdrachtregel vervangt die
  van `addopts`.
- **BZ2 zet twee keer om op één runner**, niet op twee runners. De job moet de 1,55 GB dan maar één
  keer halen. Of een andere CPU dezelfde bytes geeft, blijft open (Open 8).
- **BZ6 is alleen het model zelf**: foto's per minuut per core uit `256.webp` en één vraag in
  woorden. Warm met `werk` vol, bij 55.000 en 100.000 foto's, tijdens de backfill en de hele
  `GET /search` horen bij X3b (die opdrachten bestaan pas dan). De p95 ≤ 250 ms van de vraag is
  daarom hier een voorwaarde, niet de poort van BZ6.
- **BZ7 meet** de mediaan van drie verse processen voor de tijd, en de grootste groei van maxrss
  (niet de absolute maxrss van een proces met Python erin).
- **BZ3 zonder `tokenizers`** (Vraag 12b staat op de standaard nee): naast de lijst vergelijkt de
  job onze tokenizer met een eigen, naïeve BPE in de test op 2.000 zinnen. Die BPE is langzaam en
  los van `tokenizer.py` geschreven; ze bewijst het algoritme, de lijst bewijst het bestand.
- **"Meer zoals deze" in BZ4**: per foto met een categorie het deel van de top 4 (zonder de foto
  zelf) dat een categorie met hem deelt; de foto's zonder categorie vragen niet, maar tellen wel
  mee als buur.
- **De zelftest met drie kleuren is in BZ4 een harde poort**, niet alleen een rapport: Keuze 83
  zegt dat BZ4 de zelftest van de container controleert, en zonder die zelftest is de toestand
  `model_fout`.
- **BZ1 noemt ook commons.wikimedia.org**, naast de drie hosts van de tabel: de set komt via de
  API van Commons (Vraag 12a). NASA krijgt alleen een regel; er is geen NASA-set (Vraag 8 staat
  op de standaard nee).
- **De opdrachtregel staat als tekst in `ci.yml`** (`BOOT`), omdat `runtime.py` van X2a nog niet
  op deze tak staat. `test_beeld_ci.py` vergelijkt hem met `OMZET_BOOT` zodra dat bestand er is.
- **`tokenizer_config.json` tot 16 MB**, de andere JSON van de bron tot 1 MB: bij Gemma 3 noemt
  dat bestand elk toegevoegd token (±1,2 MB). De echte grootte staat daarna in `models.lock`.
- **`padding_side`**: de klasse in transformers zegt "left", het conversiescript van Hugging Face
  zette (voor zover gelezen) "right" in het bestand. De omzetter volgt Keuze 74 (rechts) en stopt
  als het bestand "left" zegt; zegt het niets, dan rechts. Alleen BZ4 bewijst dat dit klopt
  (Open 5).
- **Bronbestanden in `models.lock`** krijgen de naam `siglip2-b32-256/bron/<naam>` met de URL op
  de commit; de uitkomsten `siglip2-b32-256/<naam>` met url `-`. Zie Open 1 voor caps.
- `write_embed()` staat in de tabel van X3a in het ontwerp (dag 1), maar de opdracht van deze golf
  noemt het niet en de stap `embed` die hem gebruikt is van X3b. Niet gebouwd.

## Voor de samenvoeging met X2a

- `.gitignore`, `.dockerignore` en `api/.dockerignore`: X2a heeft dezelfde regels (van stap 0);
  identiek, geen conflict.
- `api/tests_analyse/`: X3a voegt `beeld_echt.py`, `siglip_klein.py` en vijf testbestanden toe;
  `conftest.py` is van stap 0, en X2a verandert hem niet. De telling van `api-analyse` (K2-10)
  moet X3a's tests meetellen. Hier: 123 tests zonder `beeld_echt` (42 van stap 0, 71
  `test_beeld_omzetten.py`, 10 `test_beeld_echt_hulp.py`) en 11 gedeselecteerd. X2a zet op
  `b0245cc` in `ci.yml` `ANALYSE_TESTS: "107"` (met de 42 van stap 0) en `ANALYSE_DESELECTED: "0"`;
  na de samenvoeging wordt dat **188** (107 + 81) en **11**. Nakijken met
  `pytest --collect-only -q tests_analyse -m "not beeld_echt"` op de samengevoegde boom. X2a geeft
  al `-m "not beeld_echt"`; dat blijft zo.
- De licenties: X2a zet bij SFace en YuNet de bestanden van opencv_zoo neer (`002b079`, met de
  APPENDIX van Apache bij SFace). `test_de_licenties` hield eerst vast dat de licentie van SigLIP 2
  gelijk is aan die van SFace; dat was na de samenvoeging rood geweest. Nu (`f197621`) staat de
  tekst van SigLIP 2 vast op zijn sha256 (de tekst van A0) en begint die van SFace met dezelfde
  bytes, met of zonder APPENDIX; nagekeken tegen het bestand van X2a. Twee dingen van X2a tellen de
  licenties: `test_install_analyse.sh` eist `ls "$MODELLEN_ROOT"/*.txt | wc -l` = 2, en
  `modellen_ophalen` zet elk bestand uit `licenties/` naast de modellen. Na de samenvoeging zijn
  het er 3: de test krijgt 3, of `install.sh` zet alleen de licenties neer van modellen die in de
  lock staan (dan blijft het 2 tot X3b). De stap in `api-analyse` die de licenties met opencv_zoo
  vergelijkt, noemt alleen YuNet en SFace; die blijft goed.
- `.github/workflows/ci.yml`: X3a voegt de job `beeld-model` aan het eind toe; X2a voegt
  `api-analyse` en `analyse-image` toe. Twee nieuwe blokken; een conflict alleen op de plek.
- `docs/runbooks/analyse.md`: X2a maakt hem ook (`b0245cc`, "Runbook: de container analyse"),
  X3a met alleen het deel beeld. Dat is een add/add-conflict: neem het bestand van X2a, zet het
  deel "Zoeken op beeld (X3)" van X3a eronder (met zijn drie subkoppen) en maak van de titel
  "de analyse (gezichten en zoeken op beeld)". De link in dit bestand wijst naar het anker
  `#zoeken-op-beeld-x3`; dat blijft zo als de kop blijft.
- onnxruntime 1.31.1 schrijft bij `import onnxruntime`, dus vóór
  `disable_telemetry_events()` (a0.md, Open 2), een apparaat-id in `$HOME`:
  `.cache/Microsoft/DeveloperTools/.onnxruntime/deviceid` (36 bytes) en `onnxruntime.db` (24 KB).
  Zonder schrijfbare `HOME` (zo draaide hier `nobody`) zegt hij op stderr "Failed to persist
  telemetry device ID; using an in-memory identifier". Een `strace` van de import en 3 s daarna
  zag geen socket buiten `AF_UNIX`. Voor de worker van X2a (container `read_only`) betekent dat
  één regel op stderr per kind; voor X3a niets: de omzetter laadt onnxruntime niet.
- `app/analyse/runtime.py` (X2a) heeft `OMZET_BOOT` gelijk aan de `BOOT` van `test_beeld_ci.py` en
  `ci.yml`, en `x-omzet-boot` in `compose.analyse.yaml` is dezelfde regel (nagekeken op `b0245cc`).

## Open

1. **De bronregels in `models.lock` en X2.** Twee dingen van X2a lezen elke regel van
   `models.lock`:
   - ~~`modellen_ophalen` in `install.sh` haalt elke regel met een URL. Met de bronregels erin haalt
     een installatie met `ANALYSE=aan` ook de 1,51 GB van `model.safetensors`, en houdt die, ook
     zonder zoeken op beeld; de uitkomsten (url `-`) slaat hij over;~~ [Review golf 5, uitrol F3:
     `modellen_ophalen` slaat de regels onder `siglip2-b32-256/` nu over (`03908c9`), ook laag 3
     en `test_analyse_container.sh`; die bronnen zijn van de stap `BEELD` van X3b.]
   - caps meldt elke regel. Na het omzetten haalt `install.sh` de bron weg (Keuze 73), dus
     `siglip2-b32-256/bron/*` staat dan op `ontbreekt`; en zonder omzetting staan de uitkomsten op
     `ontbreekt`. Gezichten vragen `ready(FACES_MODELS)` (a0.md, Open 4), maar `/beheer` moet het
     goed tonen.

   Beide moeten de regels onder `*/bron/` overslaan (of `lock.py` houdt ze apart), en de
   uitkomsten van X3 horen pas mee te tellen als beeld aan staat. Dat is X3b, met de functies voor
   beeld in `install.sh`. Tot dan: de pins (punt 2) niet op een tak zetten waar `ANALYSE=aan`
   gebruikt wordt, of samen met die wijziging. Op de LXC van Jonas staat `ANALYSE` tot 0.8.1 uit.
   [Golf 5: zo nagekeken met `test_lock_siglip.py`; het blijft open voor X3b, zie De
   samenvoeging. Review golf 5, uitrol F3: het deel van `install.sh` is gedaan; caps blijft open
   voor X3b, en `beeld_model.py uitslag` zegt nu dat de regels pas samen met X3b in
   `models.lock` horen.]
2. **De pins**: na de eerste run zet een mens de regels uit `voorstel-models.lock` in
   `app/analyse/models.lock` en `voorstel-beeld.tsv` als `api/tests/analyse/sets/beeld.tsv` (zie
   de runbook). In dezelfde commit krijgt `test_models_lock.py::test_de_twee_modellen_van_gezichten`
   de nieuwe exacte lijst (nu staan daar alleen de twee regels van de gezichten). Daarna moet
   `beeld-model` groen zijn; dat is de go/no-go.
3. **`MIN_TEKST` en `MIN_FOTO`**: BZ4 geeft de verdeling van de kansen (`bz4.json`); X3b zet de
   grenzen in een test.
4. **De voorbewerking in de worker** (X3b) moet `meta.json` volgen zoals `beeld_echt.voorbewerk`:
   RGB, resize naar (breedte, hoogte) met de resample van Pillow uit `meta.json`, × schaal,
   (x − gemiddelde) / std, NCHW.
5. **`padding_side`** (zie Afwijkingen): bewezen door BZ4, niet door een test hier.
6. **BZ1 kan zeggen dat de runner niet bij huggingface.co of Commons komt.** Dan gaat BZ2–BZ6 naar
   de LXC (`beeld-kwaliteit`, `beeld-bench`, trust-on-first-use; R41). Die commando's zijn van X3b.
7. ~~**De job `api-analyse`** is van X2a; de tests in `tests_analyse` van X3a (ook
   `test_beeld_omzetten.py`, de go/no-go deel 1) draaien in CI pas als die job er is.~~ [Golf 5:
   de job is er en telt de 81 van X3a mee (188 en 11, `ac6950c`); in CI na de push.]
8. **BZ2 op twee runners** (Afwijkingen): of een andere x86-CPU dezelfde bytes geeft, bewijst pas
   een tweede run op een andere runner, of de LXC (de sha256 in `models.lock` vangt het).
9. **De dienst `analyse-omzetten`** (compose van X2a) draait als `omzetten` (10004) met groep
   `analyse`, `read_only` en een `/tmp` van 64 MB. De omzetter schrijft alleen in `/uit` en gebruikt
   geen `/tmp`; met de echte bron in de container is hij nog niet gestart (X3b, `install.sh`).
   [Golf 5: met de kleine vorm wel, via de dienst zelf (`test_analyse_container.sh`, zie De
   samenvoeging); met de echte bron blijft het van X3b.]

## Klaar als

Elke regel van "Bouwt", "Tests" en "Klaar als" van X3 die X3a raakt (bouwontwerp, X3; §4.6).

| Regel | Test | Uitkomst |
|---|---|---|
| **Bouwt:** `app/analyse/tokenizer.py` met `tokenizer.bin` (Keuze 74) | `tests/beeld/test_tokenizer.py` (95; 89 bij X3a) | groen |
| **Bouwt:** `app/analyse/onnxw.py` (gedeeld met de nepmodellen van X2) | `test_onnxw.py` (64), `test_onnxw_ort.py` (42) | groen |
| **Bouwt:** de omzetter `siglip.py`, `meta.json` (Keuze 73) | `test_beeld_omzetten.py` (86; 71 bij X3a): `test_fp32_beeld_gelijk_aan_numpy`, `test_fp32_tekst_gelijk_aan_numpy`, `test_int8_cosinus_minstens_0_99`, `test_twee_omzettingen_dezelfde_sha256`, `test_meta_json`, `test_een_kop_die_liegt*` | groen |
| **Bouwt:** de dienst `analyse-omzetten` | in `compose.analyse.yaml` van X2a (`25ac3e7`); de opdrachtregel is dezelfde: `test_de_omzetter_start_zoals_de_dienst_analyse_omzetten` en `test_beeld_omzetten.py` (`_start_meet`) | de dienst is van X2a; de vergelijking met `OMZET_BOOT` na de samenvoeging |
| **Bouwt:** trust-on-first-use | — | niet in X3a: hoort bij `install.sh` (X3b) |
| **Bouwt:** de CI-job `beeld-model` (§4.6) | `test_beeld_ci.py` (8; 6 bij X3a), yamllint | groen; de job zelf **in CI na de push** |
| **Tests:** `api/tests/beeld/test_tokenizer.py` (de tabelrij) | zie Bouwt | groen |
| **Tests:** `api/tests_analyse/test_beeld_omzetten.py` (de tabelrij: fp32 ≤ 1·10⁻⁵, int8 ≥ 0,99, twee keer dezelfde sha256, maxrss, een liegende kop) | `test_maxrss_en_tijd_gemeten`, `test_een_kop_die_liegt_als_proces` en de rest | groen (86; 71 bij X3a) |
| **Tests:** `api/tests_analyse/test_beeld_kwaliteit.py` (marker `beeld_echt`) | de poorten van Keuze 83: `test_nl_precisie_at_5`, `test_combinaties_in_de_top_10`, `test_int8_tegen_fp32`, `test_meer_zoals_deze`, `test_zelftest_met_drie_kleuren`; de rekenstukken in `test_beeld_echt_hulp.py` | hulp groen; de poorten **in CI na de push**, groen pas na de pins |
| **Tests:** `api/tests_analyse/test_beeld_echt_perf.py` | het model-deel van BZ6 (≥ 500 per minuut, vraag p95 ≤ 250 ms) | **in CI na de push**; de rest van BZ6 is X3b |
| **Tests:** `api/tests/test_analyse_grens.py` (van X2) dekt de omzetter | dat bestand bestaat niet (nagekeken in de nacontrole); de grens staat in `tests/analyse/test_no_numpy_in_app.py::test_geen_import_buiten_de_worker` en `test_beeld_omzetten.py::test_zonder_numpy_in_de_app` | groen hier |
| **Klaar als:** `test_tokenizer.py` is groen, en BZ3 (met het bestand van SigLIP 2 zelf, zonder Gemma) is groen | `test_tokenizer.py`; BZ3: `test_bz3_de_lijst_met_het_echte_bestand`, `test_bz3_een_eigen_bpe_op_2000_zinnen`, `test_bz3_tokenizer_bin_is_from_json` | `test_tokenizer.py` groen; BZ3 **in CI na de push** |
| **Klaar als:** de omzetter is gelijk aan de eigen numpy-versie op de kleine vorm, en geeft twee keer dezelfde sha256 | `test_beeld_omzetten.py` | groen |
| **Klaar als:** de go/no-go na X3a is gehaald (Keuze 73, K2-12) | deel 1 hier; deel 2 (BZ2) en 3 (BZ4) in `beeld-model` | **open**: deel 1 gehaald (opnieuw gemeten in de nacontrole); deel 2 en 3 nergens gedaan; een strenge BZ4 pas met de pins, die met X3b komen (zie De nacontrole). Niet beslist |
| **Klaar als:** de echte omzetting is gedaan (BZ2) in `beeld-model`, en de sha256's staan in `models.lock` | `beeld_model.py vergelijk`; `test_vergelijk_*` in `test_beeld_tools.py` | **in CI na de push**; de regels zet een mens erin na de eerste run |
| **Klaar als:** de kwaliteitspoort van Keuze 83 (BZ4) is groen | `test_beeld_kwaliteit.py` | **in CI na de push**, na de pins |
| **Klaar als:** ≥ 500 foto's per minuut per core met het echte model (BZ6, Vraag 14) | `test_bz6_fotos_per_minuut_en_een_vraag` | **in CI na de push** |
| **Klaar als:** het deel beeld in het runbook, en `docs/fase-6/x3.md` | `docs/runbooks/analyse.md` (deel X3a); dit bestand | het runbook-deel van X3a staat er; `x3.md` schrijft X3b |
| **Klaar als:** `e2e/slim.sh` is groen, en alle bestaande jobs blijven groen | zie Gedraaid | zie Gedraaid |
| BZ1: een regel per host in `=== kort ===` | `test_bereik_meldt_elke_host` | groen hier (nep-server); de echte hosts **in CI na de push** |
| BZ7: `tokenizer.bin` ≤ 0,5 s en ≤ 100 MB | `test_bz7_laden_onder_een_halve_seconde_en_100_mb` (synthetisch, `api`); `test_bz7_tokenizer_bin_laden` (echt) | synthetisch groen (0,117 s, +13,3 MB); echt **in CI na de push** |

Vraag 8, 10, 11, 12a, 12b, 13 en 14 staan op hun standaard; geen ervan is hier beslist.

## Gedraaid

Op de kop van X3a, in de sandbox (root; CI draait als gewone gebruiker, zie `ci-nobody`). Elke
zware run alleen, achter het gedeelde slot van de workflow (`flock`), onder `taskset -c 0-2`.

| Wat | Waar | Uitkomst |
|---|---|---|
| `ruff check .` en `ruff format --check .` in `api/` | `aac2434`, `f197621` | groen (692 bestanden) |
| De hele API-suite, `uv run pytest -q -rs` | `aac2434`, alleen | 6351 passed, 2 skipped (Pebble draait niet; geen `provider-shapes.json`, allebei al zo vóór golf 5), 240 deselected, in 2635 s |
| `test_models_lock.py` na `f197621` | `f197621` | 23 passed |
| `tests_analyse` in `.venv-analyse`, `-m "not beeld_echt"` | `f197621` | 123 passed, 11 deselected, 10,6 s |
| Hetzelfde als `nobody` (een venv op `/opt/ci-py` met een `.pth` naar `.venv-analyse`, zodat ook de omzetter met `python -I` numpy vindt) | `f197621` | 123 passed, 11 deselected, 11,1 s |
| `/tmp/ci-nobody/run.sh` op `tests/beeld`, `test_onnxw.py`, `test_beeld_ci.py`, `test_beeld_sets.py`, `test_beeld_tools.py`, `test_models_lock.py`, `test_no_numpy_in_app.py` | `f197621` | 233 passed, 20,6 s |
| `-m beeld_echt` zonder model of set | `f197621` | 1 failed en 10 errors, elk met "BEELD_MODEL ontbreekt" of dergelijke: niets slaat over |
| `e2e/slim.sh` alleen (`SLIM_CPUS=0-2`) | `f197621` | groen in 1085 s: stap 1 (zoek en triggers in python:3.12-slim, SQLite 3.46.1) 1306 passed in 867 s; stap 3 (e2e, marker zoek) 101 passed in 206 s |
| yamllint op `ci.yml` (`.yamllint.yml` van de repo) | `f197621` | geen fouten |

**Een rode run, en waarom het niet aan golf 5 ligt.** De eerste volle suite (op `aac2434`, op
2026-11-12) had één rode test:
`tests/search/test_search_rebuild.py::test_een_upload_wacht_hooguit_1_s_en_de_index_klopt`, met
een langste upload van 1066 ms (grens 1000 ms; 7 stukken van de herbouw, de langste 43 ms; 45
uploads). Op dat moment draaiden de e2e en de volle suite van X2a op dezelfde drie cores. De test
meet de wachttijd van een upload tijdens een herbouw in stukken; die wachttijd hangt aan fsync
(z7.md, het doorgestreepte punt over deze test, en `app/dbgate.py` van I1). Een meting van
`fdatasync` op 4 KiB, alleen: 1190 keer, mediaan 1,1 ms, p99 6,9 ms, de langste 120,9 ms (één keer
boven 100 ms). Onder de last van twee suites en een e2e tegelijk is fsync niet apart gemeten: de
test was rood met die last erbij en groen zonder. Dezelfde suite alleen, achter het slot (op 2026-11-15), was groen, deze test ook. Golf 5 raakt
`app/search`, de uploads en `dbgate.py` niet. Niets losser gemaakt.

Niet hier gedraaid: de job `beeld-model` zelf (huggingface.co en Commons zijn vanuit de sandbox
dicht; zie de go/no-go), de e2e-set van CI (golf 5 raakt de web en de routes niet; slim.sh draait
de e2e van zoeken), en de shell-tests van `infra/single` (X3a raakt `install.sh` en compose niet).

## De samenvoeging (golf 5)

Samengevoegd op 15 november 2026 op `claude/stappenplan-uitwerken-lu4uv3` (vanaf `6040cd2`):
eerst deze tak (`a4ec847`, zonder conflicten), dan X2a (`ca66d3b`, twee conflicten: `ci.yml` en
het runbook). Hoe, en wat X2a raakt, staat in "De samenvoeging (golf 5)" in [x2a.md](x2a.md);
hier wat X3a raakt, langs "Voor de samenvoeging met X2a". Niets gepusht, geen tag.

- `.gitignore`, `.dockerignore` en `api/.dockerignore`: identiek, geen conflict.
- **De telling** (Open 7, `ac6950c`): `api-analyse` draait nu ook de tests van X3a in
  `tests_analyse`; `ANALYSE_TESTS` 188 (107 + 81) en `ANALYSE_DESELECTED` 11, zoals hierboven
  voorspeld. `test_beeld_ci.py::test_beeld_echt_draait_nergens_anders` eiste dat geen job behalve
  `beeld-model` `beeld_echt` noemt, en werd rood op de `-m "not beeld_echt"` van `api-analyse`. Nu
  eist hij voor `api-analyse` precies die ene uitsluiting en voor elke andere job niets.
- **De opdrachtregel** (Afwijkingen, `BOOT`): `runtime.py` staat er nu, dus
  `test_de_omzetter_start_zoals_de_dienst_analyse_omzetten` vergelijkt `BOOT` altijd met
  `runtime.OMZET_BOOT` (het "als het bestand er is" is weg, `ac6950c`).
  `test_beeld_omzetten.py` start de omzetter met `OMZET_BOOT` uit `app.analyse.runtime` in plaats
  van met een eigen kopie van de regel (`44c8140`).
- **De omzetter in de dienst `analyse-omzetten`** (Open 9, `44c8140`): `test_analyse_container.sh`
  van X2a zet de kleine vorm (`siglip_klein.py`) twee keer om met de dienst zelf, in de container
  (uid 10004, groep 10003, `/bron` alleen lezen, geen netwerk): twee keer dezelfde vijf regels,
  10004:10003 als eigenaar, de licentie met de juiste sha256; 0,2 s en maxrss 39 MiB. De
  uitkomst van de kleine vorm in de stand-in:

  | sha256 (begin) | bytes | naam |
  |---|---|---|
  | `774d08df` | 981742 | `siglip2-b32-256/beeld.int8.onnx` |
  | `afc6f447` | 161642 | `siglip2-b32-256/tekst.int8.onnx` |
  | `b70f6c67` | 4423 | `siglip2-b32-256/tokenizer.bin` |
  | `0d542e0c` | 10174 | `siglip2-b32-256/siglip2-Apache-2.0.txt` |
  | `0cb26113` | 2099 | `siglip2-b32-256/meta.json` |

  Met de echte bron in de container blijft het van X3b.
- **De regels van `beeld-model` in `models.lock`** (Open 1): `tests/analyse/test_lock_siglip.py`
  (`44c8140`, `b8a103c`) maakt het voorstel met `beeld_model.vergelijk` en zet het achter de regels
  van de gezichten: de parser en `line()`, de vingerafdruk van de gezichten (gelijk), de lus van
  `install.sh` in bash, `caps` (`ontbreekt` voor de vijf uitkomsten), de monitor
  (`models_missing` met precies die vijf; gezichten kunnen door) en `Models.entry`. Zo staat vast
  wat Open 1 zegt: met de pins en zonder X3b is de stand `models_missing` ~~en haalt `install.sh`
  de bron~~ [Review golf 5, uitrol F3: `install.sh` haalt de bron niet meer, `03908c9`]. De
  commit en de bytes in de test zijn een voorbeeld; geen sha256 is verzonnen voor `models.lock`.
- **De licenties**: drie naast de modellen; `test_install_analyse.sh` eist nu precies de drie namen
  in plaats van het getal 2 (`ac6950c`). `test_models_lock.py::test_de_licenties` (de tekst van
  SigLIP 2 op zijn sha256, SFace begint met dezelfde bytes) is groen tegen het bestand van X2a.
- **Het runbook**: het bestand van X2a, met de titel "de analyse (gezichten en zoeken op beeld)";
  het deel "Zoeken op beeld (X3)" met zijn drie subkoppen staat vóór "Wat er nog niet is". Het
  anker `#zoeken-op-beeld-x3` werkt nog.
- **onnxruntime en `$HOME`**: het kind van X2a krijgt `HOME=/tmp` (een tmpfs in de container), en
  de container heeft geen netwerk; a0.md, Open 2 is doorgestreept. De omzetter laadt onnxruntime
  niet.

### Klaar als, na de samenvoeging

| Regel | Bewijs | Uitkomst |
|---|---|---|
| **Bouwt:** de dienst `analyse-omzetten` (de vergelijking met `OMZET_BOOT`) | `test_beeld_ci.py::test_de_omzetter_start_zoals_de_dienst_analyse_omzetten` (altijd), `test_compose_analyse.py`, `test_beeld_omzetten.py`; `test_analyse_container.sh` met de kleine vorm | groen; de dienst 26 regels goed tegen de stand-in; `analyse-image` in CI na de push |
| Open 7: `test_beeld_omzetten.py` (go/no-go deel 1) in `api-analyse` | `ci.yml` (188 en 11); `tests_analyse` op de samengevoegde kop | 188 groen, 11 niet gekozen (root 49,9 s, nobody 48,3 s) |
| De regels van `beeld-model` gelezen door de code van X2a | `test_lock_siglip.py` (5) | groen |
| **Klaar als:** `e2e/slim.sh` is groen, en alle bestaande jobs blijven groen | zie Gedraaid op de samengevoegde kop in [x2a.md](x2a.md) | groen: alles wat hier kan draaien (de API-suite, `tests_analyse`, `e2e/slim.sh`, de set van `e2e`, `office`, de installatie, de container); `api-analyse`, `analyse-image` en `beeld-model` in CI na de push |

### Open na de samenvoeging

- Open 1–6 en 8 blijven staan; Open 7 is gedaan, Open 9 voor de kleine vorm.
- **De pins en X3b horen samen** (Open 1 en 2): wat er gebeurt als de tien regels zonder X3b in
  `models.lock` komen, staat in x2a.md onder "Open na de samenvoeging".
- Alleen in CI, na de push: `beeld-model` (BZ1–BZ4, BZ6, BZ7), en de tests van X3a in
  `api-analyse`.

### Gedraaid op de samengevoegde kop

Zie "Gedraaid op de samengevoegde kop" in [x2a.md](x2a.md): één run voor beide delen.

## De review (golf 5)

Drie reviews van de samengevoegde kop (`deccbee`): "veilig", "uitrol" en "contract". Hoe er
gewerkt is, wat X2a raakt en wat er gedraaid is, staat in "De review (golf 5)" in
[x2a.md](x2a.md); hier wat X3a raakt. Elke bevinding eerst zelf nagekeken; ze klopten alle. Per
bevinding een test die zonder de reparatie faalt (de oude code in een kopie). Niets gepusht, geen
tag.

| Bevinding | Wat er was | Wat er gedaan is | Test (rood zonder de reparatie) | Commit |
|---|---|---|---|---|
| veilig F2 (should) | `safetensors.py` keek niet of `dtype` tekst is en of de vorm redelijke getallen heeft. Een dtype als lijst of object gaf `TypeError` (`unhashable type`), een vorm met reuzen van getallen `ValueError` (een getal van meer dan 4300 cijfers in de f-string van de melding): een traceback (code 1) in plaats van code 2 met één regel | `OmzetFout` voor een dtype dat geen tekst is, een as boven 2^40, en een vorm groter dan het bestand (ook als product van redelijke assen) | `test_beeld_omzetten.py::test_een_kop_die_liegt[dtype lijst, dtype object, vorm met reuzen, vorm van 1000 assen, vorm groter dan het bestand]` en `::test_wat_eerst_een_traceback_gaf_is_nu_code_2[dtype lijst, vorm met reuzen]` (als proces, zoals compose: code 2, één regel `omzetten: …`, geen traceback, niets in `/uit`) | `4a6cbb2` |
| veilig F3 (should) | `bouw.py` keek met `act not in GELU` naar `hidden_act` uit `config.json`; een lijst of object gaf `TypeError` | `OmzetFout` als `hidden_act` geen tekst is ("hidden_act = list") | `::test_een_bron_die_niet_klopt[hidden_act lijst, hidden_act object]`, `::test_wat_eerst_een_traceback_gaf_is_nu_code_2[hidden_act lijst]` | `4a6cbb2` |
| veilig F4 (should) | De tokenizer las een normalizer van het soort `Sequence` recursief zonder grens: 1200 diep (geldige JSON) gaf `RecursionError`; `added_tokens` als getal gaf `TypeError` | `MAX_NESTING` (4; SigLIP 2 heeft er één): dieper is `TokenizerFout`; `added_tokens` die geen lijst is ook; en een vangnet voor `RecursionError` in `from_json` | `tests/beeld/test_tokenizer.py::test_json_die_niet_kan` (diep genest, 5 en 1200; `added_tokens` 5 en `true`) en `test_een_sequence_in_een_sequence_tot_de_grens_kan` (de grens laat de normalizer van SigLIP 2 in nog drie Sequences toe); [nacontrole: het vangnet zelf had geen test, zonder bleef alles groen; nu `test_het_vangnet_als_een_grens_mist`, `e609341`] `test_beeld_omzetten.py::…[tokenizer.json diep genest]` (twee keer, ook als proces) | `4a6cbb2` |
| uitrol F1 (blocking) | `meta.json` noemde de fp32-grafen als de omzetting `--fp32` kreeg. De job deed de eerste van de twee omzettingen met `--fp32`, dus gaf die een ander `meta.json` dan de tweede: BZ2 altijd FOUT, en nooit een voorstel voor `models.lock` | `meta.json` noemt alleen de vaste uitvoer (`UITVOER`); met of zonder `--fp32` zijn de vijf bestanden byte voor byte gelijk. `ci.yml` zet twee keer om precies zoals de dienst (`uit1`, `uit2`), dan een derde keer met `--fp32` (`uitfp32`), alleen voor BZ4 | `test_beeld_omzetten.py::test_met_en_zonder_fp32_dezelfde_vijf_bestanden`: op de oude `bouw.py` rood. Op ware grootte (zie hieronder) gaf de oude code "BZ2 FOUT meta.json: twee omzettingen geven 2f48409f… en 344211ae…" | `0313774` |
| uitrol F2 (blocking) | De grens van 1,5 GB voor maxrss gold ook voor de omzetting met `--fp32`; op ware grootte is die 1582 MiB (de fp32-embedding van 256.000 × 768 is 786 MB) | De grens geldt voor de omzettingen zoals de dienst. `beeld_model.py vergelijk` kreeg `--uit-fp32` en `--log-fp32`: tijd en maxrss van die omzetting staan in het log zonder grens, maar de vijf bestanden moeten dezelfde zijn als zonder `--fp32`. BZ4 rekent met `uitfp32`, BZ3 en BZ6 met `uit1`. "De job `beeld-model`" hierboven en het runbook zeggen het | `test_beeld_ci.py::test_bz2_begrenst_alleen_de_omzettingen_zoals_de_dienst` (op de oude `ci.yml` rood); `test_beeld_tools.py::test_vergelijk_met_fp32_heeft_geen_grens_op_maxrss`, `::test_vergelijk_met_fp32_andere_uitvoer_is_rood`, `::test_vergelijk_met_fp32_wil_de_fp32_grafen_en_beide_opties` (op de oude `beeld_model.py` 3 rood) | `0313774` |
| uitrol F3 (should), het deel van X3a | De uitslag van `beeld-model` zei alleen dat er een voorstel ligt; niets over wat de pins zonder X3b doen | `beeld_model.py uitslag` zegt ook: "zet die regels pas samen met X3b in models.lock: zonder de stap BEELD van install.sh maakt niemand de omzetting, en meldt de container ze als ontbrekend (models_missing)". De rest (`install.sh`, laag 3, de container) staat in x2a.md | `test_beeld_tools.py::test_uitslag_is_rood_zolang_er_een_voorstel_ligt` (de nieuwe exacte lijst): op de oude `beeld_model.py` rood | `03908c9` |
| contract G5-C3 (should) | Niets legde de schalen van int8 vast. Een schaal per tensor in plaats van per kolom, een vaste schaal voor de embedding of een Linear die fp32 bleef: alle 84 tests van `test_beeld_omzetten.py` bleven groen (de cosinus ≥ 0,99 op de kleine vorm merkt het niet; `test_int8_echt_int8` kijkt alleen of de op er is) | Twee tests; de code klopte. Een eigen lezer van het protobuf (zonder `onnxw` en zonder `onnx`) leest beide int8-grafen: elke Linear is `DynamicQuantizeMatMul` (com.microsoft) met B int8 [in, uit], `B_scale` [uit] en een nulpunt [uit] van nullen; de schalen en de int8 gelijk aan max \|kolom\| / 127 uit de bron; elke kolom haalt 127 [CI op `7569d48`: voor een Linear nu / 64 en 64; de embedding blijft 127]; geen `MatMul` of `Gemm` met een gewicht; de embedding een schaal [woorden, 1] per rij. En een uitvoer van `fc1`, een van `k_proj` (in qkv na q) en een rij van de embedding 100 × zo groot: alleen hun eigen schaal wordt 100 ×, de rest blijft byte voor byte gelijk | `test_beeld_omzetten.py::test_int8_een_schaal_per_kolom_en_per_rij` en `::test_int8_een_uitschieter_raakt_alleen_zijn_eigen_kolom`. Vijf mutaties van `bouw.py`: MQ1 (een schaal per tensor), MQ3 (een vaste schaal voor de embedding) en MQ5 (de kop van tekst fp32) lieten de 84 oude tests groen; alle vijf (ook MQ2 de as verwisseld, MQ4 een nulpunt van 1) maken een nieuwe test rood | `e0cce6e` |

**De oude code tegen de nieuwe tests** (veilig F2–F4), in een kopie van de boom vóór `4a6cbb2`:
`test_beeld_omzetten.py` 11 rood, 72 groen: van de twaalf nieuwe gevallen gaf alleen "vorm van
1000 assen" al een `OmzetFout`, en dat geval blijft als bewaking. `test_tokenizer.py` (met alleen
de constante `MAX_NESTING` erbij, zodat het bestand laadt) 4 rood, 90 groen: de vier gevallen van
`test_json_die_niet_kan`; `test_een_sequence_in_een_sequence_tot_de_grens_kan` was al groen en
bewaakt dat de grens SigLIP 2 zelf niet weigert.

**BZ2 op ware grootte** (uitrol F1 en F2). huggingface.co is vanuit de sandbox dicht, dus een bron
op de maat van SigLIP 2 B/32-256 met willekeurige gewichten (breedte 768, MLP 3072, 12 koppen, 12
lagen, 256.000 woorden; 1,5 GB `model.safetensors`, gemaakt met de vormen van `siglip_klein.py`),
en de stappen van BZ2 uit `ci.yml` na, onder `taskset -c 0-2`. De bron is daarna weg.

| Omzetting | Vóór de reparatie | Na de reparatie |
|---|---|---|
| met `--fp32` | 18,7 s, maxrss 1582 MiB: "BZ2 FOUT omzetting 1: maxrss 1582 MiB > 1536" | `uitfp32`: 18,0 s, maxrss 1582 MiB, "(geen grens)"; dezelfde sha256 voor alle 5 bestanden als `uit1` |
| zoals de dienst | 9,4 s, maxrss 294 MiB; "BZ2 FOUT meta.json" (de andere was met `--fp32`) | `uit1` 10,1 s en `uit2` 9,6 s, maxrss 293 MiB elk; dezelfde sha256 voor alle 5 bestanden |
| `vergelijk` | rood (FOUT) | code 3: een voorstel voor `models.lock` (de bron staat er nog niet in) |

### Klaar als, na de review

| Regel | Bewijs | Uitkomst |
|---|---|---|
| Een bron die liegt (dtype, vorm, `hidden_act`, een tokenizer die te diep nest) geeft code 2 met één regel, geen traceback | `test_beeld_omzetten.py` (nu 86), `tests/beeld/test_tokenizer.py` | groen |
| BZ2 kan groen worden: twee omzettingen zoals de dienst met dezelfde sha256 en ≤ 1,5 GB; de fp32 apart | `test_met_en_zonder_fp32_dezelfde_vijf_bestanden`, `test_beeld_ci.py`, `test_beeld_tools.py`; BZ2 op ware grootte (hierboven) | groen; 293 MiB tegen de grens van 1536; met het echte model in `beeld-model` na de push |
| De schalen van int8 liggen vast in een test | de twee tests van G5-C3 | groen |
| De uitslag zegt de pins pas samen met X3b te zetten | `test_uitslag_is_rood_zolang_er_een_voorstel_ligt` | groen |
| De telling van `api-analyse` | `ci.yml` (209 en 11; na de nacontrole 211 en 11) | zie Gedraaid na de review in [x2a.md](x2a.md) en De nacontrole |

### Open na de review

- De punten onder "Open na de samenvoeging" blijven staan; Open 1 is voor `install.sh` gedaan
  (uitrol F3), caps blijft van X3b.
- Alleen in CI, na de push: `beeld-model` (BZ1–BZ4, BZ6, BZ7) met het echte model, nu met drie
  omzettingen, en de tests van X3a in `api-analyse`.

### Gedraaid na de review

Zie "Gedraaid na de review" in [x2a.md](x2a.md): één run voor beide delen.

## De nacontrole (golf 5)

Op de kop van de review (`90305da`), op 15 november 2026. Hoe er gewerkt is, wat X2a raakt en wat
er gedraaid is, staat in "De nacontrole (golf 5)" in [x2a.md](x2a.md); hier wat X3a raakt. Niets
gepusht, geen tag.

### De reparaties weggehaald

Per commit één regel van de reparatie tegelijk terug naar de oude code, in een losse worktree;
dan de tests van die bevinding, daarna de reparatie terug en weer groen.

| Commit (bevinding) | Wat weggehaald | Rood | Oordeel |
|---|---|---|---|
| `4a6cbb2` (veilig F2) | geen controle of `dtype` tekst is | 3 | gezien |
| | geen grens van 2^40 op een as | 1 (in het proces; als proces vangt de grens op de grootte van het bestand het ook) | gezien |
| | geen controle of de vorm in het bestand past | 1 | gezien |
| `4a6cbb2` (veilig F3) | geen controle of `hidden_act` tekst is | 3 | gezien |
| `4a6cbb2` (veilig F4) | geen `MAX_NESTING` | 1 (5 diep; 1200 diep vangt dan het vangnet) | gezien |
| | geen controle of `added_tokens` een lijst is | 2 | gezien |
| | geen vangnet voor `RecursionError` in `from_json` | 0 | **gat**: `MAX_NESTING` weigert elke diepte voordat de recursie diep wordt, dus geen test kwam bij het vangnet. Gedicht in `e609341`: `tests/beeld/test_tokenizer.py::test_het_vangnet_als_een_grens_mist` zet `MAX_NESTING` op 10^9 en eist bij 1200 diep precies `TokenizerFout("tokenizer.json: te diep genest")`; zonder het vangnet `RecursionError`. `test_tokenizer.py` nu 95 |
| `0313774` (uitrol F1) | `meta.json` noemt weer de fp32-grafen | 1: `test_met_en_zonder_fp32_dezelfde_vijf_bestanden` | gezien |
| | de lus in `ci.yml` weer met `--fp32` op de eerste omzetting | 1 (`test_beeld_ci.py`) | gezien |
| `0313774` (uitrol F2) | BZ4 rekent weer met `uit1` in plaats van `uitfp32` | 1 (`test_beeld_ci.py`) | gezien |
| | `vergelijk` in `ci.yml` zonder `--uit-fp32` en `--log-fp32` | 1 (`test_beeld_ci.py`) | gezien |
| | in `beeld_model.py vergelijk`: de sha256 van de fp32-omzetting niet vergeleken, het bestaan niet nagekeken, de twee opties niet als paar, de grens van maxrss ook op fp32, de vergelijking niet aangeroepen, `main` zonder de opties | 1, 1, 1, 3, 3 en 1 (`test_beeld_tools.py`) | gezien |
| `03908c9` (uitrol F3, het deel van X3a) | `uitslag` zonder de zin over X3b | 1: `test_uitslag_is_rood_zolang_er_een_voorstel_ligt` | gezien |
| `e0cce6e` (G5-C3) | de mutaties van de review nagedaan: MQ1 (een schaal per tensor), MQ3 (een vaste schaal voor de embedding), MQ5 (de kop van tekst fp32) | 2, 2 en 1 (alleen de twee nieuwe tests; de 84 oude bleven groen) | gezien |

### Klaar als, nagelopen

Elke test die Klaar als hierboven noemt, bestaat, op één na: **`api/tests/test_analyse_grens.py`
bestaat niet**. Het ontwerp noemt dat bestand; de grens (geen numpy en geen onnxruntime buiten de
worker, ook niet via de omzetter) staat in `tests/analyse/test_no_numpy_in_app.py::test_geen_import_buiten_de_worker`
en `test_beeld_omzetten.py::test_zonder_numpy_in_de_app`; de rij zegt dat nu. Verder verbeterd, in de
tekst zelf:

- de getallen van de tests waren die van X3a: nu `test_tokenizer.py` 95 (89 bij X3a, 94 na de
  review), `test_beeld_omzetten.py` 86 (71), `test_beeld_tools.py` 45 (42), `test_beeld_ci.py` 7 (6);
- de go/no-go: zie hieronder; de rij in Klaar als zei alleen "de beslissing valt na CI";
- de rij van veilig F4 zegt nu dat het vangnet tot `e609341` geen test had;
- de telling van `api-analyse`: 211 en 11.

### De go/no-go, eerlijk geplaatst

Keuze 73 en K2-12 noemen drie delen; §5 van het ontwerp vraagt voor de go/no-go op dag 33 deel 1
en of BZ1 bij de bron kan. Wat er nu van elk deel bewezen is:

| Deel | Waar | Stand na de nacontrole |
|---|---|---|
| 1. De kleine vorm rekent als een eigen numpy-versie | `test_beeld_omzetten.py` (86) in `api-analyse`; opnieuw gemeten op `e609341` met een eigen script dat de rekensom van die tests gebruikt, drie omzettingen met `--fp32` zoals `_start_meet` | **gehaald hier**: fp32 grootste verschil 2,47·10⁻⁷ (beeld) en 1,1·10⁻⁷ (tekst), grens 1·10⁻⁵; int8 kleinste cosinus 0,99969 en 0,99985, grens 0,99 [CI op `7569d48`: met \|q\| ≤ 64 voor een Linear beeld 0,99941, tekst 0,99970; de grootte gelijk]; de drie omzettingen byte voor byte gelijk (zeven bestanden); 0,20, 0,14 en 0,17 s, maxrss 72,8 MiB; `beeld.int8.onnx` 981.742 en `tekst.int8.onnx` 161.642 bytes (fp32 1.412.840 en 517.284). Dezelfde getallen als bij X3a. In CI (`api-analyse`) na de push |
| 2. Twee keer omzetten geeft dezelfde sha256 (BZ2) | de stap `vergelijk` in `beeld-model` | [CI op `26c49ca`: **gehaald**, zie onderaan] **met het echte model nergens gedaan**: huggingface.co is vanuit de sandbox dicht. Wel: de review deed BZ2 op ware grootte met willekeurige gewichten (hierboven, `uit1`, `uit2` en `uitfp32` gelijk, 293 MiB). In CI na de push |
| 3. De kwaliteitspoort is groen (BZ4) | `test_beeld_kwaliteit.py` in `beeld-model` | **nergens gedaan**. De eerste run van `beeld-model` rekent BZ4 op de voorgestelde set en is rood (geen pins, geen `beeld.tsv`); die getallen zijn een eerste beeld, geen uitslag. Streng wordt BZ4 pas in de run na de commit met de pins en `beeld.tsv`, en het runbook en `beeld_model.py uitslag` zeggen de pins pas samen met X3b te zetten (uitrol F3). Een strenge, groene BZ4 komt dus op zijn vroegst met X3b |

De beslissing is niet hier genomen en niet als genomen gemarkeerd. Of de go/no-go valt op deel 1
en BZ1 (§5), of op alle drie (Keuze 73), en wat dan met het volgorde-probleem van deel 3 gebeurt,
is aan een mens; zie Open na de nacontrole.

### Klaar als, na de nacontrole

| Regel | Bewijs | Uitkomst |
|---|---|---|
| Het vangnet van de tokenizer heeft een test | `test_het_vangnet_als_een_grens_mist` (`e609341`) | groen; zonder het vangnet rood (`RecursionError`) |
| Elke reparatie van de review aan X3a heeft een test die haar ziet | De reparaties weggehaald, hierboven | ja |
| De go/no-go na X3a (Keuze 73, K2-12) | de tabel hierboven | **open**: deel 1 gehaald (opnieuw gemeten), deel 2 en 3 nergens gedaan; niet beslist |
| De grens van de omzetter in de app | `test_no_numpy_in_app.py::test_geen_import_buiten_de_worker`, `test_beeld_omzetten.py::test_zonder_numpy_in_de_app` (niet `test_analyse_grens.py`, dat er niet is) | groen op `e609341`: de eerste in de API-suite en als gewone gebruiker, de tweede in `tests_analyse` als root en als nobody |
| `tests/beeld`, `test_beeld_ci.py`, `test_beeld_tools.py`, `test_lock_siglip.py` als gewone gebruiker | `/tmp/ci-nobody/run.sh` | 452 groen in 134,5 s, op `e609341` |
| `tests_analyse` met de tests van X3a | `ci.yml` (211 en 11) | klopt: 211 groen en 11 niet gekozen, als root en als nobody (zie Gedraaid in de nacontrole in [x2a.md](x2a.md)) |

### Open na de nacontrole

- De punten onder "Open na de review" blijven staan.
- **De volgorde van de go/no-go en de pins.** Deel 3 (BZ4) is pas streng met de pins, en de pins
  horen bij X3b; de go/no-go hoort vóór X3b te vallen. Dat botst. Twee wegen zonder iets losser
  te maken: de go/no-go op deel 1 en BZ1 (zoals §5), met BZ4 als poort van X3b; of de pins en
  `beeld.tsv` vastzetten op een eigen tak zonder `install.sh` te raken, alleen om `beeld-model`
  streng te laten draaien. Niet beslist; dat is aan Jonas.
- Alleen in CI, na de push: `beeld-model` (BZ1–BZ4, BZ6, BZ7) met het echte model, en de tests
  van X3a in `api-analyse` (211 en 11).

## CI op 26c49ca

De eerste run van `beeld-model` met het echte model (run 43755501967; het blok `=== kort ===`):

| Stap | Uitkomst |
|---|---|
| BZ1 | huggingface.co, commons.wikimedia.org en upload.wikimedia.org bereikbaar (HTTP 200); images-assets.nasa.gov 403 (de set komt alleen van Commons, Vraag 12a) |
| BZ2 | de bron `google/siglip2-base-patch32-256` op commit `94dffa8cb1179de3e03f091dbc3917e5d5a9ae84`. Twee omzettingen in 8,0 en 7,6 s, maxrss 354 MiB (grens 1536), dezelfde sha256 voor alle vijf bestanden; met `--fp32` 18,9 s en 1607 MiB (geen grens), ook gelijk. Het voorstel voor `models.lock` (tien regels) staat in het log; de pins gaan pas mee met X3b (uitrol F3) — **goed** |
| BZ3 | de eigen BPE: 2.000 zinnen (25.383 tokens), 0 verschillen, 0 na afkappen; `tokenizer.bin` gelijk aan `from_json(...).to_bin()`. De lijst: 27 rijen, 0 verschillen, maar 8 voorwaarden niet gehaald: U+E000 en U+E001 staan in stukken van de woordenlijst van SigLIP 2 (icoonlettertypen gebruiken ze). Een fout in de lijst, niet in de tokenizer, en de test zei dat zo — **rood** |
| BZ7 | `tokenizer.bin` (12,4 MB) laden in 0,135 s (poort ≤ 0,5 s), 0,0 MB erbij (≤ 100 MB) — **goed** |
| BZ6 | beeld int8 op één core 39,8 ms per foto, 1.508 per minuut (poort ≥ 500); een vraag (tokenizer en tekst int8) p50 35,3 ms, p95 35,5 ms (≤ 250 ms), met laden 533 ms; maxrss 843 MiB — **goed** |
| BZ4 | niet gerekend: het voorstel van de set stopte op HTTP 429 van commons.wikimedia.org, na ±10 s vragen en drie snelle pogingen (2, 4 en 8 s) — **rood** |
| Uitslag | rood zoals bedoeld: `models.lock` heeft de regels van SigLIP nog niet |

Hersteld na de run:

- **De lijst van BZ3** (`beeld_tokens.tsv`): de rijen met U+E000 en U+E001 gebruiken nu U+F0000 en
  U+10FFFD, die in deze run de voorwaarde haalden. De twee rijen met drie bytes gebruiken de
  niet-tekens U+FDD0 en U+FDD1 (nooit toegewezen); alleen die zijn nieuw tegen het echte bestand.
  Staan ze toch in een stuk, dan zegt de test het weer, per rij.
- **Commons** (`tools/beeld/beeld_model.py`): één vraag per seconde aan de API en aan upload,
  Retry-After volgen bij 429 en 503 (seconden of een HTTP-datum, hooguit 120 s per keer), zes
  pogingen met 2 tot 32 s ertussen. Tests: `test_na_een_429_wacht_hij_zo_lang_als_retry_after_zegt`
  en `test_tussen_twee_vragen_aan_commons_zit_een_pauze`.
- **Wanneer de job draait**: ook als `tools/beeld`, de tests met marker `beeld_echt` of hun lijsten
  veranderen. Zonder dat had deze reparatie de job niet opnieuw laten draaien; `test_beeld_ci.py`
  eist nu ook dat elk pad bestaat (behalve `beeld.tsv`, die met X3b komt).

**De go/no-go na CI.** Deel 1 gehaald (hierboven), deel 2 (BZ2) nu ook met het echte model, en
BZ1 bij de bron kan. Daarmee is de voorwaarde van §5 (deel 1 en BZ1) gehaald; deel 3 (BZ4) nog
niet. Standaard, tot Jonas iets anders zegt: X3b gaat door, met BZ4 streng als poort van X3b
(de eerste weg onder Open na de nacontrole). Een rode BZ4 met de pins houdt X3b tegen.

## CI op 625e946

`beeld-model` op `625e946` (run 43756857465), na de reparaties hierboven:

| Stap | Uitkomst |
|---|---|
| BZ1 | zoals op `26c49ca` |
| BZ2 | dezelfde commit, en dezelfde sha256 voor alle tien regels als op `26c49ca`: twee runs op andere runners, dezelfde omzetting. 7,4 en 7,3 s, maxrss 352 MiB; met `--fp32` 13,6 s en 1606 MiB — **goed** |
| BZ3 | de lijst: 27 rijen, 0 voorwaarden niet gehaald, 0 verschillen (U+FDD0 en U+FDD1 staan in geen stuk); de eigen BPE weer 0 verschillen op 2.000 zinnen — **goed** |
| BZ7 | laden 0,169 s, 0,0 MB erbij — **goed** |
| BZ6 | 76,5 ms per foto, 785 per minuut (poort ≥ 500); een vraag p95 71,3 ms. Deze runner was ongeveer half zo snel als die van `26c49ca` (39,8 ms): de poort houdt op beide — **goed** |
| BZ4 | Commons gaf geen 429 meer, maar de keuze stopte op de vierde combinatie: "geen bruikbare foto voor fiets+stad" (CC0 of publiek domein, zonder mens, JPEG van ≥ 640 px, met P180 fiets én stad) — **rood** |
| Uitslag | rood zoals bedoeld (`models.lock` zonder de regels van SigLIP) |

Hersteld na de run:

- **Een tekort stopt de keuze niet meer** (`stel_voor` in `tools/beeld/beeld_model.py`): elke
  combinatie, categorie en elk ander onderwerp zonder genoeg foto's komt met het aantal bekeken
  kandidaten in `=== kort ===`, en pas daarna volgt de fout. Ontbreekt een combinatie, dan telt de
  job ook de paren van `RESERVE_COMBIS` (alleen de eerste pagina per licentie, niets gedownload).
  Zo zegt één run alles wat Commons niet heeft. Test:
  `test_een_tekort_stopt_de_keuze_niet_en_de_reserves_worden_geteld` (rood op de oude tool).
- **"fiets in de stad" wordt "berg met sneeuw"** in `beeld_vragen.tsv`. Keuze 83 noemt alleen "auto
  in de sneeuw" en "hond op het strand"; de andere vier koos X3a. Commons had in de pagina's die
  de job bekijkt geen bruikbare foto met fiets én stad. Of "berg met sneeuw" en "stad" alleen
  genoeg foto's hebben, zegt de volgende run (met de reserves als het niet zo is).

## CI op f16ed5a

`beeld-model` op `f16ed5a` (run 43757977938), na de reparaties hierboven. De stap BZ4 `set`
(zonder `beeld.tsv`, dus een voorstel) koos deze keer alle foto's: geen tekort, de reserves waren
niet nodig. `stel_voor` haalt elk gekozen origineel meteen op (`neem`, dan `haal`) van
upload.wikimedia.org. Na ±9 minuten stopte de stap met "FOUT set: strand-1.jpg: HTTP 429", na de
zes pogingen van `_open` (2, 4, 8, 16 en 32 s ertussen, of langer als Retry-After dat zei, hooguit
120 s). BZ4 rekende dus niet: **rood**.

**De waarschijnlijke oorzaak.** De robotregels van Wikimedia (wikitech, Robot_policy, het deel over
media) vragen: samen hooguit 2 vragen tegelijk, samen hooguit 25 Mbps gemeten over stukken van
10 s, alleen originelen of de vaste maten van de thumbnails (liever thumbnails), en na een 429
Retry-After volgen. De job vroeg één ding tegelijk en hooguit één ding per seconde per host
(`PAUZE_S`), maar een origineel is tot 25 MiB (`MAX_FOTO`) en komt op een runner in een fractie
van een seconde binnen. Zo kon de job ver boven die 25 Mbps uitkomen. Zeker weten doen we het
niet: de tool schreef geen Retry-After en niets van het antwoord in het log.

Hersteld na de run (`tools/beeld/beeld_model.py` en `ci.yml`):

- **De originelen blijven.** Een thumbnail kan Wikimedia opnieuw maken, met andere bytes; dan
  klopt de sha256 in `beeld.tsv` niet meer.
- **Een grens op de bandbreedte van upload.wikimedia.org**: gemiddeld hooguit `BANDBREEDTE` =
  2,5 MB/s (20 Mbps, onder de 25 Mbps). Na elk stuk van 1 MiB wacht `haal` tot alles wat er van
  die host binnenkwam, op 2,5 MB/s binnen had kunnen zijn. Die schuld loopt door naar de volgende
  download, en een pauze spaart geen tegoed op (anders mag na stilte een hele foto weer op volle
  snelheid, en dat telt in een stuk van 10 s). Hugging Face en andere hosts houden hun snelheid.
  Bij gemiddeld 5 MB per foto kost de set zo ±4 minuten; het uiterste (120 × 25 MiB) ±21
  minuten. Test: `test_van_upload_hooguit_bandbreedte_andere_hosts_niet`.
- **Wat Wikimedia zei, in het log.** Geeft `_open` het op na een 429 of 5xx, dan noemt de fout de
  code, de laatste Retry-After die hij zag (of "geen Retry-After") en de eerste regel met tekst van
  het antwoord: alleen afdrukbare ASCII (de rest wordt `?`), hooguit 160 tekens (`MAX_REGEL`), en
  niets anders van het antwoord. Bijvoorbeeld "strand-1.jpg: HTTP 429 (Retry-After 7; antwoord:
  Too many requests)". Andere fouten houden hun korte vorm ("HTTP 404", "niet bereikbaar (…)").
  Test: `test_een_opgegeven_429_zegt_wat_wikimedia_zei`.
- **Streng haalt alleen wat ontbreekt of niet klopt.** Met `beeld.tsv` haalt `set` een foto die al
  in `set/` staat als gewoon bestand met de juiste grootte en sha256 (`sha256_van`) niet opnieuw;
  een die niet klopt, ontbreekt of een symlink is, haalt hij opnieuw, via `.part` eroverheen. De
  regel in het blok zegt hoeveel er uit de cache kwamen: "BZ4 set: 120 foto's kloppen met
  beeld.tsv (116 uit de cache, 4 van Commons)". Test:
  `test_streng_haalt_alleen_wat_ontbreekt_of_niet_klopt`; `test_set_met_beeld_tsv_is_streng`
  verwacht nu "(0 uit de cache, 120 van Commons)".
- **Een cache voor de foto's, nooit voor de bron.** `$RUNNER_TEMP/beeld/set` staat in een cache
  onder de sha256 van `api/tests/analyse/sets/beeld.tsv`, alleen als die er is
  (`hashFiles(...) != ''`). Een voorstel krijgt geen cache; de bron komt elke keer van
  huggingface.co. Eerst was dat één stap `actions/cache@v4` direct vóór BZ4 `set`; sinds de review
  hieronder (R1) zijn het twee stappen van dezelfde action en versie: `actions/cache/restore@v4`
  direct vóór `set` en `actions/cache/save@v4` direct erna. `test_beeld_ci.py` houdt ze vast: het
  pad, de sleutel, geen restore-keys, de voorwaarden, de plek rond `set`, en geen andere
  `actions/cache` in de job.

Teruggedraaid getest (de oude `beeld_model.py` terug, de nieuwe tests ongewijzigd): de drie nieuwe
tests en `test_set_met_beeld_tsv_is_streng` zijn rood. 3.658.073 bytes van upload in 1,0 s (alleen
`PAUZE_S`), boven de 25 Mbps; "strand-1.jpg: HTTP 429" zonder Retry-After of antwoord; 120 vragen
aan upload in plaats van 4. Twee mutaties apart, ook rood: tegoed opsparen na een pauze, en een
symlink als foto uit de cache tellen.

### De review van de reparatie

Eén review van `61c2139`, vier bevindingen. Elke bevinding eerst zelf nagekeken; ze klopten alle
vier (R2 deels, zie daar). Per bevinding met code een test die zonder de reparatie faalt (de
`beeld_model.py` en `ci.yml` van `61c2139` in een kopie terug: 3 rood, 57 groen).

| Bevinding | Wat er was | Wat er gedaan is | Test (rood zonder de reparatie) |
|---|---|---|---|
| R1 (should) | `actions/cache@v4` bewaart in zijn post-stap, en alleen na een groene job (`post-if: "success()"` in zijn `action.yml`). De job blijft rood zolang `models.lock` de regels van SigLIP mist (tot X3b), ook met `beeld.tsv` en een goede `set`: dan haalde elke strenge run de 120 originelen opnieuw, en deed de cache niets | Twee stappen van dezelfde action en tag (de submappen `restore` en `save` van `actions/cache`, `v4`; dezelfde repository en `dist/`, geen nieuwe action): ophalen met `id: set-cache` vóór `set`, bewaren direct na `set`, ook als de job daarna rood eindigt. Bewaren alleen als `set` goed was (nooit een halve set: een sleutel bewaart maar één keer) en de cache ze nog niet had (`cache-hit != 'true'`) | `test_beeld_ci.py::test_de_foto_s_gaan_direct_na_set_in_de_cache_ook_als_de_job_rood_eindigt` (nieuw) en `::test_alleen_push_en_pull_request_en_alleen_de_set_in_de_cache` (nu: in `beeld-model` alleen `restore@v4` en `save@v4`, in elke andere job alleen `actions/cache@v4`) |
| R2 (nit) | Twee zinnen over "geen cache" lazen krom, en "(6 tests)" in "De job `beeld-model`" stond naast feiten van na `f16ed5a`. Die 6 klopte voor `3184131` (6 bij X3a), dus niet fout, wel verwarrend | Herschreven: "de bron nooit uit een cache, de foto's van `beeld.tsv` wel"; "(6 tests bij X3a, nu 8)" en in Klaar als "(8; 6 bij X3a)" | geen (tekst) |
| R3 (nit) | `set/` kan uit de cache komen en tar houdt symlinks. `_klopt` weigert een `<sleutel>.jpg` die een symlink is, maar `haal` opende `<sleutel>.jpg.part` met `"wb"`: door een link heen, en `os.replace` zette de link daarna op de plek van de foto | `haal` haalt een `.part` die er al staat eerst weg (ook een symlink, niet zijn doel) en opent met `"xb"`: nooit door een link | `test_beeld_tools.py::test_haal_schrijft_nooit_door_een_part_die_er_al_staat`: op de oude code schreef `haal` "de echte foto" in het bestand buiten `set/` |
| R4 (nit) | De fixture `nep` zette `time.sleep` stil maar niet `_klaar` en `_vorige` terug; zonder slapen loopt `_klaar` voor elke download van `/upload/` verder voor op de klok, en een latere test die slapen telt, kon een rem van een eerdere test zien | `nep` begint elke test met lege `_vorige` en `_klaar` | geen nieuwe: vandaag hangt geen test ervan af (alle 60 groen in de volgorde van het bestand) |

### Open na f16ed5a

- ~~**De cache bewaart alleen na een groene job.** `actions/cache` bewaart in zijn post-stap alleen
  als de job geslaagd is. Zolang de job rood eindigt (de kwaliteitspoort, of `uitslag` omdat
  `models.lock` de regels van SigLIP nog mist terwijl `beeld.tsv` er al is), haalt elke strenge run
  de 120 originelen opnieuw, wel binnen de grens. Altijd bewaren vraagt `actions/cache/save`, een
  andere action; niet gedaan.~~ [De review van de reparatie, R1: `actions/cache/save@v4` direct
  na `set`, van dezelfde action en tag.]
- **`timeout-minutes: 60` is niet veranderd.** Met de grens duurt het ophalen langer (zie boven);
  of het past, zegt de volgende run.
- ~~**Of de bandbreedte de oorzaak was**, zegt de volgende run: komt er weer een 429, dan staat er
  nu wat Wikimedia zei.~~ [CI op `9ebf298`: nee. Ook op `BANDBREEDTE` stopte de stap op een 429
  (Retry-After 600), na ±30 minuten; de set haalt nu thumbnails, zie "CI op 9ebf298".]

## CI op 7569d48

`api-analyse` op `7569d48` (run 43758619443) was rood: drie tests die op elke eerdere runner groen
waren, faalden op één runner.

| Test | Uitkomst |
|---|---|
| `test_onnxw_ort.py::test_dynamic_quantize_matmul[True]` en `[False]` | verschil met numpy 0,16686297 (grens `TOL` 1·10⁻⁵) |
| `test_beeld_omzetten.py::test_int8_cosinus_minstens_0_99` | beeld 0,9795, 0,9885 en 0,9727 (grens `COS` 0,99) |

**De oorzaak.** `DynamicQuantizeMatMul` maakt van de invoer uint8 (0..255, met een nulpunt per
oproep) en rekent dan uint8 maal int8 (MLAS, U8S8). Op een x86-CPU zonder AVX-VNNI en zonder
AVX512-VNNI doet MLAS dat met `vpmaddubsw`: die telt twee buren langs K op (a·b + a'·b') in int16,
en kapt af op −32.768..32.767. Met een gewicht tot 127 kan zo'n paar 255·127·2 = 64.770 zijn. Die
runner had geen VNNI. De sandbox heeft AVX512-VNNI en AVX-VNNI; daar, en op de eerdere runners,
rekende het goed. Een nabootsing in numpy van de afgekapte paren, met de invoer van
`test_dynamic_quantize_matmul`, geeft 0,16686297783599: gelijk aan CI op 8 cijfers. De
documentatie van onnxruntime ([Quantize ONNX models](https://onnxruntime.ai/docs/performance/model-optimizations/quantization.html))
noemt dit: U8S8 gebruikt op AVX2 en AVX512 zonder VNNI `vpmaddubsw`, dat kan verzadigen, en
`reduce_range` (het gewicht in 7 bits) is daarvoor bedoeld.
Zonder VNNI zijn onder meer AMD Zen 2 en Zen 3 en Intel Xeon E5 v3 en v4 en Skylake-SP: gewone
CPU's in een thuislab. Welke CPU de host van Jonas heeft, is niet bekend (BZ9).

**De reparatie** (`bouw.py`, `Q_MAX = 64`). Elke Linear krijgt een schaal per kolom van
max |kolom| / 64, en q = rint(w / s) tussen −64 en 64. Het blijft int8, en `DynamicQuantizeMatMul`
met nulpunt 0. Het ergste paar is nu 255·64·2 = 32.640: dat past in int16, op elke CPU. De
kwantiseerder van onnxruntime doet het met `reduce_range` net zo (int8 −64..64). De embedding van
de tekst blijft 127: die gaat door Gather, Cast en Mul, zonder matmul in gehele getallen.
Afgevallen: het gewicht als uint8 (U8U8). Dat kapt ook niet af, maar onnxruntime kiest
`vpmaddubsw` voor U8S8 juist om de snelheid, en noemt U8U8 pas als `reduce_range` niet genoeg
helpt. Hoeveel trager U8U8 hier zou zijn, is niet gemeten; BZ6 (poort ≥ 500 foto's per minuut
per core, verwachting 750–870 [berekend, K2-04]) heeft weinig ruimte.

**De tests.** Alleen de eerste hangt af van de CPU:

- `test_onnxw_ort.py::test_dynamic_quantize_matmul` neemt het gewicht nu uit `bouw.per_kolom`, met
  hetzelfde rooster (de invoer als uint8 van 0 tot 255), `TOL` 1·10⁻⁵ en beide varianten. Op een
  CPU zonder VNNI is dit weer de echte toets van onnxruntime.
- `test_zonder_vnni_rekent_de_omzetter_exact`: een model van `vpmaddubsw` in numpy (paren langs K,
  afgekapt op int16, opgeteld in int32, dan het nulpunt eraf). Met het gewicht van `per_kolom`, de
  ergste invoer per kolom (255 waar q > 0 en 0 elders, en andersom), het rooster van de test
  hierboven en de nulpunten 0, 128 en 255 geeft het model precies het product. Het ergste paar haalt
  ±32.640.
- `test_zonder_vnni_kapt_een_gewicht_boven_64_af[65]` en `[127]`: de grens is scherp. Met q tot 65
  (of 127) kapt het model op de ergste invoer af.
- `test_het_model_zonder_vnni_geeft_het_verschil_van_ci`: met q tot 127 geeft het model voor
  `test_dynamic_quantize_matmul` het verschil van CI (0,166862974, op 1·10⁻⁸), met 64 geen
  verschil. Zo is het model de CPU van die runner.
- `test_beeld_omzetten.py`: de schalen van elke Linear zijn nu max |kolom| / 64 en elke kolom haalt
  64; de embedding blijft per rij met 127. Nieuw: `test_int8_geen_gewicht_van_een_linear_boven_64`
  leest beide int8-grafen met de eigen lezer van het protobuf: elk gewicht dat B is van een
  `DynamicQuantizeMatMul` heeft |q| ≤ 64, en het enige andere int8-gewicht is de embedding.
  `test_meta_json` eist nu `omzetter` 2 (`bouw.VERSIE`): dezelfde bron geeft andere int8-bytes.

Teruggezet naar `Q_MAX = 127` (hier, met VNNI): vijf tests rood.
`test_dynamic_quantize_matmul[True]` en `[False]` (q haalt 127, niet 64; onnxruntime zelf rekent
hier goed), `test_zonder_vnni_rekent_de_omzetter_exact` (het model kapt af: 30.472 in plaats van
57.375), `test_int8_een_schaal_per_kolom_en_per_rij` (de schaal van
`vision_model.encoder.layers.0.self_attn.qkv`) en `test_int8_geen_gewicht_van_een_linear_boven_64`
(127 > 64). De drie andere nieuwe tests zetten `Q_MAX` zelf en blijven groen. `ANALYSE_TESTS` in
`ci.yml` gaat van 211 naar 216 (vijf nieuwe tests).

**Wat het kost.** Eén bit van het gewicht. Op de kleine vorm (hier gemeten, met VNNI): de kleinste
cosinus van int8 tegen de numpy-versie was beeld 0,99969 en tekst 0,99985 met 127, en is beeld
0,99941 en tekst 0,99970 met 64 (grens 0,99). Wat het bij het echte model kost, meet BZ4 (int8
tegen fp32) zodra de set en de pins er zijn.

**Gevolg voor de pins.** De sha256 van `beeld.int8.onnx` en `tekst.int8.onnx` verandert, en
daarmee ook die van `meta.json` (die noemt de sha256 van beide, en `omzetter` gaat van 1 naar 2).
De grootte blijft gelijk (de vormen veranderen niet; op de kleine vorm nagemeten). De regels die
`beeld-model` vóór deze reparatie voorstelde (runs 43755501967 op `26c49ca`, 43756857465 op
`625e946` en 43757977938 op `f16ed5a`), gelden dus niet meer voor die drie bestanden. Op `7569d48`
draaide `beeld-model` niet (geen pad van de job veranderd). Die regels staan nog nergens in het
repo. Neem de pins pas uit de eerste run van `beeld-model` op een commit met `Q_MAX = 64` (die
draait vanzelf: `bouw.py` is een pad van de job). Een oude regel in `models.lock` zou
`beeld_model.py vergelijk` rood maken (een andere sha256), maar kost dan een ronde CI.

## CI op 9ebf298

`beeld-model` op `9ebf298` (run 43762807071), de eerste run met `Q_MAX = 64` (zie "CI op
7569d48") en met de reparaties van `f16ed5a`:

| Stap | Uitkomst |
|---|---|
| BZ2 | twee omzettingen met dezelfde sha256 voor alle vijf bestanden. Drie pins zijn nieuw (`Q_MAX = 64` geeft andere int8-bytes, en `meta.json` noemt hun sha256 en `omzetter` 2); de andere regels van het voorstel zijn dezelfde als in de runs ervoor. **Goed** |
| BZ1, BZ3 en BZ7 | **goed** |
| BZ6 | beeld int8 op één core 1.470 foto's per minuut (poort ≥ 500); een vraag p95 36,6 ms (≤ 250 ms). **Goed** |
| BZ4 | niet gerekend: de stap `set` (een voorstel, zonder `beeld.tsv`) liep van 21:18:18 tot 21:48:47 en stopte na ±30 minuten op "kat-1.jpg: HTTP 429" van upload.wikimedia.org, met Retry-After 600 (de eerste 429 kwam ±10 minuten eerder; daarna wachtte de tool vijf keer 120 s). **Rood** |

De nieuwe pins (BZ2):

| sha256 | bytes | naam |
|---|---|---|
| `96c602da850a77869c26ac77889f52bfd60ad298eed94f1ad5b97c821a73e6f0` | 102672680 | `siglip2-b32-256/beeld.int8.onnx` |
| `afc9dd55d41c59d10c9e2aa3c275f6f6a8a144e4c24c7c652ba20406c0b1003f` | 284307578 | `siglip2-b32-256/tekst.int8.onnx` |
| `cc84a88be5c2dfb27a8bca85389ba6baaa5cf152f43dceef13a3ecd2a51830e6` | 2135 | `siglip2-b32-256/meta.json` |

Ze gaan, zoals de andere regels van SigLIP, pas met X3b in `models.lock` (uitrol F3).

**De bandbreedte was niet de oorzaak.** Van upload.wikimedia.org kwam gemiddeld hooguit
`BANDBREEDTE` (20 Mbps, onder de 25 Mbps van de robotregels), één vraag tegelijk, met `PAUZE_S`
ertussen; toch stopte de stap weer op een 429, nu na ±30 minuten in plaats van ±9 (de eerste
429 van `kat-1.jpg` kwam ±10 minuten voor het stoppen). Wat wel past bij de run: begin
2026 trof een 429 met een lange Retry-After juist originelen voor clients zonder browser, terwijl
thumbnails bleven laden (phabricator T413570), en de robotregels van Wikimedia voor media vragen
liever thumbnails, en dan alleen de vaste maten. Twee dingen deed de tool zelf verkeerd: `_open`
wachtte na "Retry-After 600" hooguit 120 s (`MAX_WACHT_S`) en vroeg dus vijf keer te vroeg
opnieuw, en de fout nam als uitleg de eerste regel van het antwoord, "<!DOCTYPE html>", die niets
zei. Hoe ver de stap was toen hij stopte, stond nergens.

Hersteld na de run (`tools/beeld/beeld_model.py`; de tests in
`api/tests/analyse/test_beeld_tools.py`):

1. **Een thumbnail in plaats van het origineel.** `details` vraagt imageinfo met `iiurlwidth` =
   `DUIM_BREEDTE` = 1280 en neemt `thumburl`, `thumbwidth` en `thumbheight` mee. 1280 is de
   kleinste vaste maat ≥ 1024 (`$wgThumbnailSteps`: 20, 40, 60, 120, 250, 330, 500, 960, 1280,
   1920 en 3840 px; mediawiki.org, "Common thumbnail sizes", en phabricator T402792). De app geeft
   het model nooit het origineel, maar 256.webp (of 1024.webp als BZ4 dat zegt, Keuze 77; de
   gezichten uit 1024.webp, Keuze 60), en een thumbnail met een lange kant ≥ 1280 geeft in
   `app.render.image` dezelfde maten van 1024.webp en 256.webp als het origineel. Dat geldt in
   een voorstel en streng: `beeld.tsv` heeft nu de URL, de grootte en de sha256 van precies het
   bestand dat `set` ophaalt. De kolom `bron` blijft de bestandspagina van het origineel (de
   naamsvermelding). De controles op het origineel blijven: CC0 of publiek domein, JPEG, geen
   mens, korte kant ≥ `MIN_ZIJDE` (640) en niet meer dan `MAX_PIXELS`. Erbij:
   - de thumbnail komt van upload (`UPLOAD`), alleen afdrukbare ASCII, met `/thumb/` in het pad,
     een naam die met `1280px-` begint en `thumbwidth` 1280; `lees_set` weigert een thumbnail
     van een andere maat;
   - **een grens op de korte kant van de thumbnail.** De kwaliteitstest maakt er met
     `app.render.image` 1024.webp en 256.webp van (lange kant 1024 en 256, nooit vergroot), en
     de voorbewerking van `meta.json` maakt daar 256 × 256 van, zonder bijsnijden. Is de korte
     kant van 1024.webp kleiner dan 256, dan rekt die voorbewerking ook 1024.webp op die kant op,
     en meet "256 tegen 1024" daar niets. Dus eist `_genoeg` een korte kant van 1024.webp ≥ 256:
     voor een liggende thumbnail van 1280 breed een hoogte ≥ 320; een staande mag tot 4 × 1280 =
     5120 hoog zijn;
   - is het origineel niet breder dan 1280, dan geeft MediaWiki het origineel zelf als
     `thumburl`. Dat mag, met de maten van het origineel, en alleen als het niet groter is dan
     `MAX_FOTO`. MediaWiki geeft dan ook als `thumbwidth` en `thumbheight` de maten van het
     origineel (`ApiQueryImageInfo`, "T25834"); `_duim` neemt ze toch van het origineel, en
     een thumbwidth die de gevraagde breedte zegt, is een geval dat de test alleen voor de
     zekerheid kent (de review van de reparatie, R4). Zo'n smal origineel blijft een vraag om
     een origineel (zie "Open na 9ebf298");
   - `MAX_FOTO` (25 MiB) begrenst nu elke download van de set, niet meer het origineel: een groot
     origineel met een goede thumbnail mag, want `set` haalt het nooit.

   **Het risico van een nieuwe thumbnail.** Wikimedia maakt thumbnails zelf en kan er later een
   opnieuw maken, met andere bytes (andere software). Streng is dan rood op de sha256, met de
   sleutel erbij ("kat-2.jpg: sha256 …, verwacht …"), nooit stil. De cache in CI op de sha256 van
   `beeld.tsv` houdt de eerste goede kopie, dus de job merkt het pas als die cache weg is. Dan
   zet een mens de regel van die foto opnieuw (het runbook zegt hoe). Een origineel heeft dat
   risico niet, maar juist dat gaf de 429.

   Tests: `test_set_zonder_beeld_tsv_stelt_120_fotos_voor` (elke vraag om imageinfo met
   `iiurlwidth` 1280; van upload alleen thumbnails van 1280 en één smal origineel; een groot
   origineel komt als thumbnail; een panorama met een thumbnail van 1280 × 213 valt af; url,
   bytes en sha256 van de thumbnail, `bron` van het origineel),
   `test_de_thumbnail_van_een_goede_foto` (6 gevallen),
   `test_een_foto_zonder_goede_thumbnail_valt_af` (16 gevallen, waaronder 1280 × 319, 1280 ×
   5121, een thumbnail elders of van 1024 breed), `test_de_maat_van_de_thumbnail` (1280 is de
   kleinste vaste maat ≥ 1024; `PREVIEW` en `INVOER` zijn `render.SIZES`) en
   `test_een_set_die_niet_klopt[thumbnail van 1024]`.
2. **Retry-After altijd gevolgd.** Na een 429 of 503 vraagt `_open` nooit eerder opnieuw dan
   Retry-After zegt (seconden of een HTTP-datum). `MAX_WACHT_S` is nu 600 s: zegt Retry-After
   meer, dan stopt `_open` meteen, zonder nog een vraag. Daarnaast een budget `WACHT_TOTAAL_S` =
   900 s per proces (dus per stap), waarin ook de eigen 2 tot 32 s na een 5xx zonder Retry-After
   tellen: past de volgende wachttijd er niet meer in, dan ook meteen stoppen. De fout zegt de
   code, de Retry-After en waarom, bijvoorbeeld "kat-1.jpg: HTTP 429 (Retry-After 601; langer dan
   MAX_WACHT_S (600 s), dus meteen gestopt; antwoord: Too many requests)". Tests:
   `test_na_een_429_wacht_hij_zo_lang_als_retry_after_zegt` (nu met Retry-After 600, helemaal
   gewacht), `test_retry_after_600_wacht_hij_helemaal_nooit_eerder` (op een nepklok: de volgende
   vraag ≥ 600 s later, ook met een HTTP-datum) en `test_te_lang_of_buiten_het_budget_stopt_meteen`.
   [De review van de reparatie, R2: ook een getal met decimalen telt, zoals "11.000"; een
   Retry-After die er is maar niet te lezen is, stopt `_open` meteen ("niet te lezen, dus meteen
   gestopt").]
3. **Wat Wikimedia zei.** De uitleg van een opgegeven 429 of 5xx is nu de tekst van `<title>`,
   anders de eerste regel met tekst zonder de tags van HTML (zonder script, style en commentaar);
   nog steeds alleen afdrukbare ASCII en hooguit `MAX_REGEL` (160) tekens. Andere fouten houden
   hun korte vorm. Test: `test_de_uitleg_is_de_titel_of_de_eerste_regel_met_tekst` (5 gevallen,
   waaronder de pagina van Wikimedia: "Too many requests - please contact noc@wikimedia.org");
   `test_een_opgegeven_429_zegt_wat_wikimedia_zei` is ongewijzigd groen. [De review van de
   reparatie, R1: de titel van de echte foutpagina van Wikimedia is altijd "Wikimedia Error";
   nu telt een titel die alleen dat of "Error" zegt niet, en komt daarna de eerste `<p>`, waar
   Wikimedia de reden zet. De test heeft nu 8 gevallen.]
4. **Hoe ver de stap kwam.** Gaat `set` mis, dan zegt de fout bij welke foto en hoeveel er al
   binnen was, bijvoorbeeld "(bij foto 37 van 120, 12,3 MB opgehaald)"; in een voorstel is het
   totaal het doel van Keuze 83 (120). Komt het voorstel te kort, dan zegt de fout hoeveel er wel
   kwamen, bijvoorbeeld "(113 van 120 foto's opgehaald, 1,1 MB)", niet "bij foto". Gaat het goed, dan eindigt
   de regel van het blok op de bytes en de tijd ("…, 120 van Commons; <MB> in <s> s)"). Het blok
   `=== kort ===` krijgt er geen regels bij. Tests: `test_een_set_die_misgaat_zegt_hoe_ver_hij_kwam`
   (een 404 bij foto 37 in een voorstel, en streng een thumbnail die Wikimedia opnieuw maakte, bij
   foto 50), `test_een_tekort_stopt_de_keuze_niet_en_de_reserves_worden_geteld` (nu ook het
   aantal in de fout), `test_set_met_beeld_tsv_is_streng` en
   `test_streng_haalt_alleen_wat_ontbreekt_of_niet_klopt` (met de nieuwe regel). [De review van
   de reparatie, R3 en R6: de fout zegt ook of set een thumbnail of een origineel ophaalde, de
   regel van een goede set hoeveel er als origineel kwamen ("…, 120 van Commons, 1 als
   origineel; <MB> in <s> s)"), en een fout terwijl set de reserves telt, zegt hoeveel foto's
   er kwamen.]

De User-Agent is niet veranderd (algemeen, zonder persoon, e-mail of repository).

Teruggedraaid getest (de `beeld_model.py` van `9ebf298` terug, de nieuwe tests ongewijzigd; daarna
de nieuwe weer terug en `git status` nagekeken): in `tests/analyse` 26 rood, 5 met een fout en
336 groen. Rood: de drie tests van Retry-After (de oude wachtte 120 s na "Retry-After 600" en
kende `WACHT_TOTAAL_S` niet), alle 5 van de uitleg ("<!DOCTYPE html>" in plaats van de titel),
`test_een_set_die_niet_klopt[thumbnail van 1024]`, alle 6 goede foto's en 10 van de 16 slechte
(`_duim` bestond niet, en de oude `_bruikbaar` nam ze), en `test_de_maat_van_de_thumbnail`. De 5
fouten zijn de tests met de nep van Commons (die vraagt `DUIM_BREEDTE`). Met alleen
`DUIM_BREEDTE = 1280` erbij in de oude tool zijn alle 5 rood om de goede reden (31 rood, 336
groen): het voorstel haalde de originelen, de regel van het blok zonder bytes en tijd, en geen
voortgang in de fout (ook niet bij een tekort). Groen op de oude tool, zoals bedoeld: de 6 slechte foto's die al op het origineel afvielen
(`MIN_ZIJDE`, `MAX_PIXELS`, geen JPEG, een mens, CC BY, een smal origineel groter dan
`MAX_FOTO`). Ook groen op de oude tool, zoals bedoeld: `test_een_geldige_set_komt_door_de_controle`.
Die eist nu dat een thumbnail van 1280 op upload goed is, en de oude `lees_set` nam elke URL op
upload; het is de goede kant van `test_een_set_die_niet_klopt[thumbnail van 1024]`, en die is
rood (de review van de reparatie, R8). Met de nieuwe tool: `tests/analyse` 367 groen (op
`9ebf298` 335).

### De review van de reparatie

Eén review van `077d986`, acht bevindingen. Elke bevinding eerst zelf nagekeken; ze klopten alle
acht. Per bevinding met code een test die zonder de reparatie faalt (de `beeld_model.py` van
`077d986` in een kopie terug: in `tests/analyse` 9 rood, 363 groen; daarna de nieuwe terug, met
`cmp` en `git status` nagekeken).

| Bevinding | Wat er was | Wat er gedaan is | Test (rood zonder de reparatie) |
|---|---|---|---|
| R1 (should) | De uitleg nam eerst `<title>`. Op de echte foutpagina van Wikimedia is die altijd "Wikimedia Error", met de kop `<h1>Error</h1>`; de reden staat in de eerste `<p>` eronder, bij een 5xx een vaste zin (wikimedia/operations-puppet: `modules/mediawiki/templates/errorpage.html.erb`, `modules/varnish/manifests/common/errorpage.pp` en `modules/varnish/templates/errorpage.inc.vcl.erb`; phabricator T413570). Een echte 429 had dus "antwoord: Wikimedia Error" gegeven. De nep `WIKIMEDIA_429` had de reden in de titel, en de tests waren groen | De titel blijft eerst (de beslissing), maar wat alleen "Error" of "Wikimedia Error" zegt, telt niet: dan de eerste `<p>`, dan de eerste regel met tekst. `<details>` (daar zet Wikimedia het IP-adres van de client) komt nooit mee. `MAX_ANTWOORD` (4096) is genoeg: de pagina is ±2,8 kB, de `<p>` begint na ±2,2 kB. De reden uit de statusregel (het voorstel van de review) niet: bij een 429 is dat dezelfde tekst als de `<p>`, en de beslissing zegt: niets anders van het antwoord | `test_de_uitleg_is_de_titel_of_de_eerste_regel_met_tekst`: `WIKIMEDIA_429` en `WIKIMEDIA_503` in de vorm en de maat van de echte pagina, en een algemene titel en kop met `<details>` (3 gevallen rood: "Wikimedia Error"); nergens "Wikimedia Error" in de fout |
| R2 (should) | `_retry_after` las alleen hele getallen. "11.000" (zo stuurde Wikimedia het in januari 2026, phabricator T414173) werd 0, en dan vroeg `_open` na zijn eigen 2 s opnieuw, eerder dan gevraagd. Een onleesbare waarde werd ook 0 | Een getal met decimalen telt. Een Retry-After die er is maar niet te lezen is (ook "-5" of "11,5"), stopt `_open` meteen, zonder nog een vraag: "HTTP 429 (Retry-After straks; niet te lezen, dus meteen gestopt; …)" | `test_retry_after_met_decimalen_telt_en_onleesbaar_stopt_meteen` (nieuw; op de nepklok ≥ 11 en ≥ 12,5 s tussen de vragen; de oude vroeg na 2 s) |
| R3 (should) | Een origineel dat niet breder is dan 1280, haalt set als origineel: het soort vraag waarop begin 2026 de 429 kwam. De fout zei niet of hij een thumbnail of een origineel ophaalde, en zonder `beeld.tsv` is de sleutel achteraf niet aan een URL te koppelen. Zo kon de volgende run "Of thumbnails de 429 voorkomen" niet beantwoorden | De fout zegt wat set ophaalde: "(bij foto 37 van 120, een thumbnail van 1280 px, 12,3 MB opgehaald)" of "…, een origineel, …". De regel van een goede set zegt hoeveel er als origineel kwamen ("…, 1 als origineel; <MB> in <s> s)"). Een punt erbij in Open | `test_een_set_die_misgaat_zegt_hoe_ver_hij_kwam` (nu ook een 429 met de pagina van Wikimedia op het smalle origineel), `test_set_zonder_beeld_tsv_stelt_120_fotos_voor`, `test_set_met_beeld_tsv_is_streng` en `test_streng_haalt_alleen_wat_ontbreekt_of_niet_klopt` |
| R4 (nit) | Hier en in de nep van Commons stond dat MediaWiki bij een origineel als `thumburl` als `thumbwidth` de gevraagde breedte kan geven. MediaWiki geeft dan de maten van het origineel (`ApiQueryImageInfo`, "T25834") | Tekst verbeterd; de nep geeft nu ook de maten van het origineel. Het geval "smal, thumbwidth van de vraag" blijft in de test, als geval voor de zekerheid | geen (de code hing er niet van af; `_duim` neemt de maten van het origineel) |
| R5 (nit) | "kwam de 429 na ±30 minuten": na ±30 minuten stopte de stap; de eerste 429 kwam ±10 minuten eerder (daarna wachtte de oude tool vijf keer 120 s) | Herschreven, hier, in de opmerkingen in `beeld_model.py` en in de tests. De boodschap van `077d986` blijft zoals hij is | geen (tekst) |
| R6 (nit) | Een fout van de API van Commons terwijl set de reserves telt (de keuze is dan klaar, er wordt niets meer opgehaald), zei "bij foto 117 van 120" | `voortgang.gekozen` staat aan zodra de keuze klaar is en er een tekort is, vóór de reserves | `test_een_fout_bij_het_tellen_van_de_reserves_zegt_hoeveel_er_kwamen` (nieuw: "116 van 120 foto's opgehaald") |
| R7 (nit) | "(de set heeft ≥ 640)" in `test_beeld_kwaliteit.py` gold alleen nog voor de lange kant: een thumbnail mag 1280 × 320 zijn | Herschreven: de lange kant van elke foto is ≥ 640 (1280 voor een thumbnail, ≥ `MIN_ZIJDE` voor een smal origineel). Geen grens veranderd | geen (opmerking) |
| R8 (nit) | De tabel van de run miste BZ1, en het teruggedraaide testen noemde niet dat `test_een_geldige_set_komt_door_de_controle` op de oude tool groen is | BZ1 in de tabel; een zin erbij | geen (tekst) |

Rood op de oude tool: de nieuwe test van Retry-After (de tweede vraag 2 s na "11.000"), 3
gevallen van de uitleg ("antwoord: Wikimedia Error"), de drie regels van een goede set (zonder
"als origineel"), de nieuwe test van de reserves ("bij foto 117 van 120") en
`test_een_set_die_misgaat_zegt_hoe_ver_hij_kwam` (ook rood met alleen de soort uit de fout
weggehaald, in een kopie van de nieuwe tool). Groen op de oude tool, zoals bedoeld: de 5
gevallen van de uitleg die `077d986` al goed deed (een titel met tags, een lege titel en dan de
eerste regel, nu met `<span>` in plaats van `<p>` zodat hij de eerste regel blijft toetsen,
geen tekst, te lang, en het nieuwe geval dat een titel die iets zegt voor de `<p>` gaat),
`test_een_tekort_stopt_de_keuze_niet_en_de_reserves_worden_geteld` (alleen de opzet naar
`_tekort`) en `test_de_thumbnail_van_een_goede_foto[smal, thumbwidth van de vraag]` (R4).
Met de nieuwe tool: `tests/analyse` 372 groen.

### Open na 9ebf298

- **Of thumbnails de 429 voorkomen**, zegt de volgende run van `beeld-model` (`tools/beeld` is
  een pad van de job). Komt er toch een 429, dan staat er nu wat Wikimedia zei, hoe lang hij
  wilde dat we wachtten, hoe ver de stap was, en of het een thumbnail of een origineel was.
- **Een smal origineel blijft een vraag om een origineel.** Is het origineel niet breder dan
  1280, dan haalt `set` het origineel zelf: het soort vraag waarop begin 2026 de 429 kwam. Hoeveel
  dat er in de echte set zijn, zegt de regel van een goede set ("… als origineel"). Komt de 429
  alleen nog op originelen, dan kan `details` zo'n foto weigeren; niet gedaan, want dan kiest
  de set andere foto's (de review van de reparatie, R3).
- **Helpt het niet**, dan is de volgende stap een User-Agent met contactgegevens, zoals de
  richtlijn van Wikimedia voor de User-Agent vraagt. Dat zijn gegevens van de eigenaar: dat kan
  alleen met zijn toestemming, en is niet gedaan. De User-Agent blijft algemeen.
- **`timeout-minutes: 60` is niet veranderd.** `set` wacht nu hooguit `WACHT_TOTAAL_S` (900 s)
  na 429 en 5xx, bovenop het ophalen van 120 thumbnails op `BANDBREEDTE`.

## CI op 3148c2f

`beeld-model` op `3148c2f` (run 43772387468), de eerste run met de thumbnails van 1280 px (zie
"CI op 9ebf298"):

| Stap | Uitkomst |
|---|---|
| BZ2 | twee omzettingen met dezelfde sha256 voor alle vijf bestanden, en dezelfde pins als op `9ebf298`. **Goed** |
| BZ1, BZ3 en BZ7 | **goed** |
| BZ6 | beeld int8 op één core 776 foto's per minuut (poort ≥ 500); een vraag p95 71,7 ms (≤ 250 ms). **Goed** |
| BZ4 | niet gerekend: de stap `set` (een voorstel, zonder `beeld.tsv`) liep van 23:01:28 tot 23:19:10 en stopte op "FOUT set: commons.wikimedia.org: HTTP 429 (Retry-After 50; 50 s wachten past niet in wat er over is van WACHT_TOTAAL_S (32 van 900 s), dus meteen gestopt; antwoord: You are making too many requests to the API.) (bij foto 1 van 120, 0,0 MB opgehaald)". **Rood** |

**Deze keer was het de API van Commons.** Niet upload.wikimedia.org maar commons.wikimedia.org
(`/w/api.php`) gaf de 429, en al vóór de eerste foto: van de 900 s in `WACHT_TOTAAL_S` was 868 s
gewacht na een 429 voordat de eerste foto van de eerste combinatie binnen was. Of al de eerste
vraag van `set` een 429 kreeg, zegt het log niet; de vraag van BZ1 aan dezelfde API, om 23:00:32,
kreeg nog HTTP 200. Of de thumbnails de 429 op upload voorkomen, zei deze run dus niet. Hoeveel
vragen de API had gekregen en hoeveel daarvan 429 waren, stond nergens.

Wat erbij past: Wikimedia kondigde op 2 maart 2026 op wikitech-l "New global API rate limits" aan
([de aankondiging](https://www.mail-archive.com/wikitech-l@lists.wikimedia.org/msg97202.html)):
eerst lage grenzen voor anonieme vragen, vanaf april hogere voor wie zich identificeert. De
getallen staan op mediawiki.org,
["Wikimedia APIs/Rate limits"](https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits); die
pagina was vanaf hier niet te lezen. Wie haar citeert (in september 2026): zonder identificatie
(alleen een IP-adres) 10 vragen per minuut, per IP; met een User-Agent volgens de richtlijn van
Wikimedia (met contact: een e-mailadres, een URL of een gebruikerspagina) 200 per minuut;
aangemeld meer ([craigtrim/axiom, issue 22](https://github.com/craigtrim/axiom/issues/22), 30
september 2026;
[fuzheado/Wikipedia-AI-Skills, `SKILL.md`](https://github.com/fuzheado/Wikipedia-AI-Skills/blob/main/.claude/skills/wikimedia-api-access/SKILL.md):
10, 200 en 2000 per minuut, "last_verified" 8 september 2026). Een oudere kopie van de pagina,
uit de tijd van de invoering
([een spiegel](https://backiee.wasmer.app/https_www_mediawiki_org/wiki/Special:MyLanguage/Wikimedia_APIs/Rate_limits)),
noemde 500 per uur zonder identificatie en "low" voor alleen een User-Agent, in fasen ingevoerd in
maart en april 2026; de aankondiging zelf noemt geen getallen. Officieel bevestigd zijn de
getallen hier dus niet. De User-Agent van de job heeft geen contact (zo besloten: geen gegevens
van de eigenaar), dus valt de job in de laagste klasse, en `set` vroeg de API één keer per seconde
(`PAUZE_S`). Dat `f16ed5a` (20:25) en `9ebf298` (21:18) eerder die dag met hetzelfde tempo wel
door de keuze kwamen, past bij een budget per IP: hoeveel ervan al op is, hangt af van wat de
runner en zijn IP eerder deden.

Hersteld na de run (`tools/beeld/beeld_model.py`, `.github/workflows/ci.yml`; de tests in
`api/tests/analyse/test_beeld_tools.py`):

1. **De API van Commons op 8 s.** Tussen twee vragen aan de API zit nu minstens `PAUZE_API_S` =
   8 s. Met een vaste tussentijd g zitten er in een venster van 60 s hooguit floor(60 / g) + 1
   vragen: bij 8 s dus 8 (op 0, 8, …, 56 s), onder de 10, met ruimte voor een klok die anders
   telt; in een uur hooguit 451, onder de 500 van de oudere kopie. Elke poging telt: `_open` laat
   ook een nieuwe poging na een 429 of 5xx door `_pauze` gaan, en het wachten na Retry-After en de
   pauze lopen samen, ze tellen niet op (3 s Retry-After en dan nog 5 s pauze; 20 s Retry-After en
   dan geen pauze meer). De pauze geldt per dienst, niet meer per host: de API en upload hebben
   elk hun eigen klok (upload blijft `PAUZE_S` = 1 s plus `BANDBREEDTE`, en een download loopt
   door in de pauze van de API); Hugging Face en andere hosts wachten niet. `MAX_WACHT_S`,
   `WACHT_TOTAAL_S` en het volgen van Retry-After zijn niet veranderd.

   **Hoeveel vragen een voorstel doet** (geteld uit de code, `_kandidaten` en `stel_voor`). Per
   zoekopdracht (een combinatie uit `beeld_vragen.tsv`, een categorie of een ander onderwerp) en
   per pagina van 50 drie vragen: zoeken, imageinfo en wbgetentities; een lege pagina één. Een
   zoekopdracht stopt zodra hij genoeg heeft (een combinatie na de eerste bruikbare foto, de rest
   na vijf), meestal op de eerste pagina van de eerste licentie. Met 6 combinaties, 12 categorieën
   en 12 andere onderwerpen is dat minstens 30 × 3 = 90 vragen: 12 minuten op 8 s (de nep van
   Commons in de tests doet er precies 90). Met een paar tekorten of tweede pagina's ±150 tot 200:
   20 tot 27 minuten. Hooguit 2 licenties × 4 pagina's × 3 = 24 per zoekopdracht, 720 samen, plus
   de reserves alleen bij een tekort (5 paren, de eerste pagina per licentie: 30): 750 vragen, 100
   minuten. De 120 thumbnails lopen grotendeels door in de pauzes van de API. Met `beeld.tsv`
   vraagt `set` de API niets.

   **`timeout-minutes` van `beeld-model` van 60 naar 90.** Op `3148c2f` duurde alles buiten `set`
   ±2 minuten (de bron 26 s, drie omzettingen 29 s, BZ3 en BZ7 8 s, BZ6 42 s); BZ4 zelf draaide
   nog niet en is niet gemeten. Met 60 minuten bleef naast 15 minuten wachten (`WACHT_TOTAAL_S`)
   en ±15 minuten voor de rest ±30 minuten voor de API over: ±225 vragen, krap voor een voorstel
   met een paar tekorten. 90 minuten draagt ±450 vragen. Een nieuwe poging is daarbij een vraag
   als elke andere: hij kost minstens `PAUZE_API_S`, ook als het wachten dat `WACHT_TOTAAL_S`
   telt korter is (de eigen 2 of 4 s na een 5xx, of na een 429 zonder of met een korte
   Retry-After, kosten elk 8 s). Na een Retry-After van 50 s, zoals op `3148c2f`, is er geen
   verschil. Het uiterste van de code (750 vragen, 100
   minuten) past niet: dan heeft Commons bijna niets bruikbaars, en is de set toch rood.
   `timeout-minutes` is een grens van CI, geen poort.

   Tests: `test_tussen_twee_vragen_aan_commons_zit_een_pauze` (op de nepklok 8 s en 7,75 s aan de
   API, 1 s aan upload, niets aan Hugging Face, en elke vraag telt),
   `test_elke_poging_aan_de_api_wacht_pauze_api_s` (nieuw: een nieuwe poging na een 429 wacht
   Retry-After en de rest van de pauze, niet de som),
   `test_een_voorstel_vraagt_de_api_hooguit_8_keer_per_minuut` (nieuw: een heel voorstel op de
   nepklok, met op elke tiende vraag een 429; nooit twee vragen aan de API binnen 8 s, in geen
   venster van 60 s meer dan 8) en `test_te_lang_of_buiten_het_budget_stopt_meteen` (het deel aan
   de API nu op de nepklok: de pauze zit tussen de pogingen en telt niet in het budget).
2. **Wat de API kreeg en zei.** Per proces en per dienst van Commons (de API en upload) telt de
   tool elke poging, hoe vaak het antwoord 429 of 5xx was, en wanneer elke 429 kwam. De fout van
   `set` en de regel van een goede set zeggen het verkeer sinds het begin van de stap, kort, zoals
   "(bij foto 1 van 120, 0,0 MB opgehaald; API: 23 vragen, 17 keer 429, eerste 429 na 3 s;
   upload: 0 vragen; contact: nee)"; `… keer 5xx` alleen als die er waren. Een `eerste 429 na`
   van een paar seconden bij weinig vragen zegt dat het budget van het IP al op was voordat de
   stap begon. Aan het begin van `set` staat in het log (niet in het blok) het tempo: "BZ4 set: de
   API van Commons hooguit één vraag per 8 s, upload één per 1 s; contact: nee". Tests:
   `test_een_api_die_429_blijft_zeggen_staat_met_de_tellers_in_de_fout` (nieuw: de API zegt op
   elke tweede vraag 429 met Retry-After 50, tot `WACHT_TOTAAL_S` op is: "… (0 van 900 s), dus
   meteen gestopt; antwoord: You are making too many requests to the API.) (bij foto 7 van 120,
   … opgehaald; API: 38 vragen, 19 keer 429, eerste 429 na 8 s; upload: 6 vragen, 0 keer 429;
   contact: nee)"), en met het verkeer erbij `test_set_zonder_beeld_tsv_stelt_120_fotos_voor`,
   `test_set_met_beeld_tsv_is_streng`, `test_streng_haalt_alleen_wat_ontbreekt_of_niet_klopt`,
   `test_een_tekort_stopt_de_keuze_niet_en_de_reserves_worden_geteld`,
   `test_een_fout_bij_het_tellen_van_de_reserves_zegt_hoeveel_er_kwamen` en
   `test_een_set_die_misgaat_zegt_hoe_ver_hij_kwam`. De nep van Commons onthoudt nu de status van
   elk antwoord, en de tests leggen het verkeer dat de tool meldt naast wat de nep zag.
3. **Een contact in de User-Agent, standaard uit.** Met contact geldt 200 per minuut, maar het
   zijn gegevens van de eigenaar, en die heeft daar niet mee ingestemd. Daarom alleen het
   mechanisme: is de omgevingsvariabele `BEELD_CONTACT` gezet en niet leeg, dan is de User-Agent
   naar Commons (de API en upload; nooit naar Hugging Face of een andere host)
   "beeld-model-meting/1.0 (<contact>; CI van een zelf-gehost fotoplatform)", in de vorm van de
   richtlijn van Wikimedia, en zakt de pauze van de API naar `PAUZE_S`. Streng nagekeken vóór de
   eerste vraag: hooguit 200 tekens, alleen afdrukbare ASCII zonder ";", "(" of ")" (die breken de
   vorm; een regeleinde zou er een kopregel bij maken), en een https://-URL of een e-mailadres;
   anders een fout ("FOUT set: BEELD_CONTACT: …", zonder de waarde) en geen enkele vraag. Het log
   en het verslag zeggen alleen "contact: ja" of "contact: nee". `ci.yml` zet `BEELD_CONTACT`
   niet; het runbook zegt hoe het met toestemming van de eigenaar kan
   ([analyse.md](../runbooks/analyse.md), "Een contact in de User-Agent"). Tests:
   `test_zonder_beeld_contact_gaat_er_geen_contact_mee` (niet gezet en leeg: precies de algemene
   User-Agent, 8 s, "contact: nee", en geen workflow noemt `BEELD_CONTACT`),
   `test_met_beeld_contact_alleen_naar_commons_en_nooit_in_het_log` (4 gevallen: een URL, een URL
   met poort, een e-mailadres, precies 200 tekens; alleen Commons krijgt het, de API dan op 1 s,
   en het staat niet in het log, niet in `kort.txt` en niet in een ander bestand van de werkmap)
   en `test_een_beeld_contact_dat_niet_klopt_gaat_nooit_weg` (13 gevallen; ook `bereik` stopt
   zonder één vraag).

De algemene User-Agent is niet veranderd: "beeld-model-meting/1.0 (CI van een zelf-gehost
fotoplatform)", zonder persoon, e-mail of repository.

Teruggedraaid getest (de `beeld_model.py` van `3148c2f` terug, de nieuwe tests ongewijzigd; daarna
de nieuwe weer terug, met `cmp` en `git status` nagekeken): in `test_beeld_tools.py` 30 rood en 81
groen. Rood: de vier tests van de pauze (de oude vroeg de API opnieuw zodra Retry-After of de
terugval op was, na 2, 3 of 4 s, en wachtte 1,0 en 0,75 s in plaats van 8,0 en 7,75 s), de zes
tests van `set` met het verkeer in de regel of de fout (de oude tekst, zonder "; API: …; contact:
nee"), `test_een_api_die_429_blijft_zeggen_staat_met_de_tellers_in_de_fout` (een fout zonder
tellers), beide gevallen van `test_zonder_beeld_contact_gaat_er_geen_contact_mee` (geen regel met
het tempo), alle 4 van `test_met_beeld_contact_alleen_naar_commons_en_nooit_in_het_log` en alle 13
van `test_een_beeld_contact_dat_niet_klopt_gaat_nooit_weg` (de oude kende `BEELD_CONTACT` niet:
`bereik` gaf 0 en vroeg gewoon). Vijf daarvan stopten op een naam die de oude tool niet had
(`PAUZE_API_S`, `MAX_CONTACT`). Met alleen `PAUZE_API_S`, `MAX_CONTACT` en `UA_MET_CONTACT` erbij
in de oude tool zijn ze alle 5 rood om de goede reden: twee vragen aan de API 1 s na elkaar
("1.0 >= 8.0"), en de vraag van `bereik` aan de API zonder contact in de User-Agent. Met de nieuwe
tool: `test_beeld_tools.py` 111 groen, `tests/analyse` 394 groen.

### De review van de reparatie

Eén review van `a32694b`, vijf bevindingen. Elke bevinding eerst zelf nagekeken, ook de bronnen
en het log van run 43772387468; ze klopten alle vijf. Per bevinding met code een test die zonder
de reparatie faalt.

| Bevinding | Wat er was | Wat er gedaan is | Test (rood zonder de reparatie) |
|---|---|---|---|
| R1 (should) | De beslissing vraagt ook de 5xx te tellen, maar geen test keek naar de teller van de 5xx of naar "… keer 5xx" in de regel: in elke test van `set` gaf de nep alleen 200, 404 of 429. Van welke 429 "eerste 429 na" rekent, lag ook niet vast. De reviewer haalde de telling, de tekst en de index weg (drie mutaties): alle tests bleven groen | Alleen een test; de code klopte | `test_een_5xx_telt_apart_en_een_tweede_set_telt_vanaf_zijn_eigen_begin` (nieuw): de API zegt 503 (Retry-After 3) en 502 (zonder), de eerste thumbnail eerst 503, en één 429; de regel "API: 93 vragen, 1 keer 429, 2 keer 5xx, eerste 429 na 64 s; upload: 121 vragen, 0 keer 429, 1 keer 5xx", ook na een 5xx minstens `PAUZE_API_S` tussen twee vragen aan de API. Dan een tweede `set` in hetzelfde proces: alleen zijn eigen verkeer, geen 5xx van de eerste, en de eerste 429 gemeten van zijn eigen begin tot zijn eigen eerste 429 ("na 28 s"; de eerste vraag wacht nog de rest van de pauze na de vorige `set`). Rood op elk van de drie mutaties |
| R2 (should) | "vanaf de eerste vraag een 429" (in de noot van het bouwontwerp, een opmerking in `beeld_model.py` en een docstring): het log zegt alleen dat de 429's vóór de eerste foto kwamen, en de vraag van BZ1 aan dezelfde API kreeg om 23:00:32 nog HTTP 200. En "sinds maart 2026 10 vragen per minuut per IP" stond in het bouwontwerp en in de boodschap van `a32694b` als feit; de officiële pagina was ook voor de reviewer niet te lezen (hier opnieuw geprobeerd: `url_not_found`), de aankondiging noemt geen getallen, en 10 en 200 per minuut komen alleen uit bronnen van augustus en september 2026. "Stand november 2026" was dus te los | "al vóór de eerste foto (steeds) een 429" in het bouwontwerp, `beeld_model.py` en de test; in het bouwontwerp en het runbook "volgens wie mediawiki.org citeert (september 2026)"; hier "Wie haar citeert (in september 2026)", met de datum van `SKILL.md` en een link naar de spiegel van de oudere kopie (500 per uur, "low" voor alleen een User-Agent). De boodschap van `a32694b` blijft zoals hij is; de boodschap van deze reparatie zegt het | geen (tekst) |
| R3 (nit) | De opmerking bij `timeout-minutes` zei hooguit 15 minuten wachten na 429 en 5xx. Een nieuwe poging gaat ook door `_pauze`: hij kost max(wachten, `PAUZE_API_S`), maar `WACHT_TOTAAL_S` telt alleen het wachten. Met de eigen 2 of 4 s kost een nieuwe poging 8 s (in `test_te_lang_of_buiten_het_budget_stopt_meteen`: 6 s geteld, 16 s verstreken) | "plus `PAUZE_API_S` per nieuwe poging" in `ci.yml` en hierboven; de ±450 vragen die 90 minuten dragen, tellen nieuwe pogingen mee. Na een Retry-After van 50 s, zoals op `3148c2f`, is er geen verschil; 90 minuten blijft | geen (opmerking) |
| R4 (nit) | Het runbook zette `BEELD_CONTACT` op de `env:` van de job. Dan toont GitHub het bovenaan elke stap van de job (in het log van `3148c2f` staat de `env:` van de job bovenaan 14 stappen), en kijkt elke stap van `beeld_model.py` het na: een fout erin hield ook `bron`, `vergelijk` en `uitslag` tegen, die Commons nooit vragen | Het runbook zet de regel op de twee stappen die Commons vragen (`BZ1 bereik per host` en `BZ4 de 120 foto's`), met een voorbeeld; de opmerking over een secret voor een e-mailadres blijft | geen (tekst) |
| R5 (nit) | `_uitleg` neemt tot 160 tekens tekst van de server over in de fout en in `kort.txt`. Een foutpagina die de User-Agent herhaalt, zette zo `BEELD_CONTACT` in het log | `_zonder_contact`: vóór `_antwoord` gaat het contact uit de tekst van de server (en uit Retry-After), ook HTML-gecodeerd ("&amp;"), en als het antwoord na `MAX_ANTWOORD` ophoudt ook het langste begin ervan aan het eind; op zijn plaats "[contact]" (zonder < en >, want `_antwoord` haalt tags weg). Meer dan het voorstel van de review (alleen de letterlijke vorm, na het inkorten): dan bleven een gecodeerd contact en een afgekapt begin staan, en kon het inkorten tot 160 tekens een stuk van het contact laten staan | `test_een_antwoord_dat_de_user_agent_herhaalt_zet_het_contact_niet_in_het_log` (nieuw, 3 gevallen: in de titel, HTML-gecodeerd in de eerste `<p>` met een `&` in de URL, en afgekapt na 4096 bytes midden in het contact): de fout zegt precies "antwoord: Too many requests from beeld-model-meting/1.0 ([contact]; …)", en nergens in het log of de werkmap staat het contact of een begin ervan. Op de tool van `a32694b` alle 3 rood; met alleen de letterlijke vorm 2 rood |

Teruggedraaid getest (een kopie van de tool terug, de nieuwe tests ongewijzigd; daarna de nieuwe
weer terug, met `cmp` en `git status` nagekeken), `test_beeld_tools.py`: de tool van `a32694b` 3
rood (de 3 gevallen van R5), 112 groen; de drie mutaties van R1 (de 5xx niet geteld, "… keer
5xx" weg, de eerste 429 van het proces in plaats van die van `set`) elk 1 rood (de nieuwe test van
R1), 114 groen; alleen de letterlijke vorm van het contact 2 rood, 113 groen; de tool van
`3148c2f` 34 rood (de 30 van hierboven en de 4 nieuwe), 81 groen. Met de nieuwe tool:
`test_beeld_tools.py` 115 groen, `tests/analyse` 398 groen.

### Open na 3148c2f

- **Of het contact genoeg is**, zegt de volgende run van `beeld-model` (`tools/beeld` is een pad
  van de job). Sinds "Het contact aan" hieronder vraagt de CI de API met contact, op 1 s; de 8 s
  zijn de terugval zonder contact. Het budget hoort bij het IP van de runner (of, met contact,
  bij de User-Agent), en GitHub deelt runners en hun IP's: wat ander verkeer al opmaakte, ziet de
  job niet. Was het al op, dan zegt de fout dat nu, bijvoorbeeld "API: 6 vragen, 6 keer 429,
  eerste 429 na 0 s; …; contact: ja". Dan blijft een langere pauze over.
- ~~**`BEELD_CONTACT` staat uit.**~~ Aan sinds 2026-11-15, zie hieronder.
- **Of thumbnails de 429 van upload voorkomen**, is nog open (zie "Open na 9ebf298"): deze run
  kwam niet tot de eerste foto.
- **Hoe lang BZ4 duurt** (de kwaliteitspoort in int8 en fp32) is nog nooit gemeten; de ruimte in
  `timeout-minutes: 90` voor de rest van de job is een schatting.

### Het contact aan (2026-11-15)

Jonas vond op 2026-11-15 goed dat de CI de link naar zijn openbare repository als contact in de
User-Agent naar Wikimedia stuurt, en alleen dat: geen e-mail, geen naam. Daarom zet `ci.yml` in de
job `beeld-model` `BEELD_CONTACT: ${{ github.server_url }}/${{ github.repository }}` in een eigen
`env:` van precies de twee stappen die Commons vragen, `BZ1 bereik per host` en `BZ4 de 120 foto's
(beeld.tsv, of een voorstel uit Commons)`; niet op de job, zodat het niet bovenaan elke stap staat
en een fout erin `bron`, `vergelijk` en `uitslag` niet tegenhoudt. De User-Agent naar Commons is
dan `beeld-model-meting/1.0 (https://github.com/<eigenaar>/<repository>; CI van een zelf-gehost
fotoplatform)`, naar Hugging Face en NASA blijft hij algemeen, en `set` vraagt de API hooguit één
keer per seconde (`PAUZE_S`; met contact is de grens volgens de bronnen 200 per minuut). De tool
schrijft het contact nooit in het log of in `kort.txt` ("contact: ja"); GitHub toont de waarde wel
bovenaan die twee stappen, en voor een openbare link is dat geen probleem.

Tests: `test_zonder_beeld_contact_gaat_er_geen_contact_mee` eiste dat geen workflow
`BEELD_CONTACT` noemt; die eis is weg (de test kijkt nog steeds de tool zonder contact na). In de
plaats daarvan eist `test_alleen_bz1_en_bz4_set_geven_wikimedia_de_link_naar_het_repository` in
`test_beeld_ci.py` precies deze waarde op precies die twee stappen, en nergens anders: niet op een
workflow of job, niet in een andere stap, niet uit een secret, niet in `run` of `with`; en dat die
twee stappen precies de stappen zijn die `beeld_model.py bereik` en `set` draaien. Rood
nagekeken op drie mutaties van `ci.yml` (het contact weg bij `set`, erbij bij `bron`, een
e-mailadres als waarde). `test_met_beeld_contact_alleen_naar_commons_en_nooit_in_het_log` heeft
een vijfde geval in de vorm die `ci.yml` meestuurt (`https://github.com/<eigenaar>/<repo>`, met
punt, liggend streepje en underscore). Uitzetten staat in het runbook
([analyse.md](../runbooks/analyse.md), "Een contact in de User-Agent").

## CI op 45bb926

`beeld-model` op `45bb926` (run 43787944528), de eerste run met de link naar het repository als
contact (zie "Het contact aan"):

| Stap | Uitkomst |
|---|---|
| BZ1 | huggingface.co, commons.wikimedia.org en upload.wikimedia.org HTTP 200; images-assets.nasa.gov HTTP 403. **Goed** |
| BZ2 | twee omzettingen (8,1 en 7,5 s, maxrss 354 MiB) met dezelfde sha256 voor alle vijf bestanden; de omzetting met `--fp32` (18,8 s, 1607 MiB) ook. **Goed** |
| BZ3, BZ6 en BZ7 | **goed**; BZ6 beeld int8 op één core 2609 foto's per minuut (poort ≥ 500), een vraag p95 19,5 ms (≤ 250 ms) |
| BZ4 | niet gerekend: `set` (een voorstel, zonder `beeld.tsv`) liep van 01:45:53 tot 01:56:26 en stopte op "FOUT set: Commons: 30 tekort(en) voor de set; zie de regels BZ4 set hierboven (0 van 120 foto's opgehaald, 0,0 MB; API: 630 vragen, 0 keer 429; upload: 0 vragen; contact: ja)". **Rood** |

**Niet de API: het voorstel nam niets.** Met contact vroeg `set` de API 630 keer in 10,5 minuten,
één keer per seconde, en kreeg geen enkele 429: het tempo van "Het contact aan" hield. Maar van
de kandidaten nam hij er geen: "geen bruikbare foto" voor alle 6 combinaties (5 tot 106
kandidaten bekeken), "maar 0 bruikbare foto's" voor alle 12 categorieën (400 kandidaten bekeken,
eten 294) en alle 12 andere onderwerpen (400; klok 272, gitaar 279, insect 377), en 0 voor alle 5
reserves. Dus 30 tekorten, en niets opgehaald ("upload: 0 vragen"). Dezelfde keuze vond op
`9ebf298` nog alle 120, toen als originelen. Sindsdien vraagt `details` de thumbnail van 1280 px
(iiurlwidth), en nam `_duim` alleen een thumbnail op `UPLOAD` (zie "CI op 9ebf298"); op `3148c2f`
kwam de keuze niet zover (de 429 van de API). Dit was dus de eerste run waarin `_duim` echte
antwoorden zag. Waarom elke kandidaat afviel, zei het log niet: alleen hoeveel er bekeken waren.

**Waarom, uit de tweede hand.** Wikimedia haalde de cache van de thumbnails uit het
upload-cluster naar een eigen host, thumb.wikimedia.org: phabricator
[T427465](https://phabricator.wikimedia.org/T427465), "Move thumbnail caching from upload cluster
to text", uitgerold vanaf 2026-08-19 en "everywhere" sinds 2026-09-07. Het pad blijft hetzelfde
(`/wikipedia/commons/thumb/<a>/<ab>/<naam>/<breedte>px-<naam>`), alleen de host verandert, en
MediaWiki geeft die host nu in de thumburl van imageinfo
([fuzheado/Wikipedia-AI-Skills, PR #26](https://github.com/fuzheado/Wikipedia-AI-Skills/pull/26),
2026-09-03). De oude URL's op upload werken nog; een 301 naar thumb.wikimedia.org komt
"eventually". De originelen blijven op upload; het pad van een origineel op thumb.wikimedia.org
geeft een 301 naar https://commons.wikimedia.org/. Een verslag op dev.to ("Wikimedia thumbnails
now: thumb.wikimedia.org, a fixed list of widths, and WebP only for some clients", nagekeken op
2026-11-14) zegt verder: thumb.wikimedia.org geeft alleen de vaste maten (op een andere HTTP
400); hij kiest het formaat op dezelfde URL (`.jpg`) naar de Accept van de vraag (met
`image/webp,*/*`, en met de Accept van Chrome, WebP; zonder Accept, met `image/jpeg` of met
`image/avif,*/*` JPEG), en het antwoord in WebP heeft geen `Vary`; met 1 s tussen twee vragen gaf
hij geen 429. Al deze bronnen zijn uit de tweede hand: de sandbox komt niet bij Wikimedia, en
niets ervan is hier nagemeten. Dat de thumburl in run 43787944528 op thumb.wikimedia.org stond,
zegt het log ook niet; het is de verklaring die past bij alle 30 tekorten tegelijk (elke
zoekopdracht, beide licenties, ook de reserves, terwijl de API zonder fout antwoordde). De
volgende run zegt het: de regel van een tekort noemt nu de redenen (punt 5).

Hersteld na de run (`tools/beeld/beeld_model.py`; de tests in
`api/tests/analyse/test_beeld_tools.py`):

1. **Thumbnails van thumb.wikimedia.org (`THUMB`).** `_duim` neemt een thumbnail van 1280 px op
   `THUMB` of `UPLOAD` (het pad van `_is_duim`, thumbwidth 1280); geeft MediaWiki het origineel
   zelf als thumburl (een origineel dat niet breder is), dan alleen op `UPLOAD`. Het voorstel
   schrijft de URL in `beeld.tsv` precies zoals de API hem gaf, dus meestal op
   thumb.wikimedia.org; de kolom bron blijft de bestandspagina. `lees_set` neemt een thumbnail van
   1280 px op thumb of upload en een origineel alleen op upload; anders een fout met het
   regelnummer: "url hoort bij https://thumb.wikimedia.org/ of https://upload.wikimedia.org/",
   "url is een thumbnail, maar niet 1280 px" of "url op https://thumb.wikimedia.org/ is geen
   thumbnail; een origineel hoort bij https://upload.wikimedia.org/". De nep van Commons in de
   tests geeft de thumburl nu op zijn eigen thumb (`/duim/`), behalve voor vier foto's van boot
   (de oude vorm, op upload). Tests: `test_ci_op_45bb926_de_thumbnails_staan_op_thumb_wikimedia_org`
   (nieuw: een voorstel zonder tekort, 115 van de 120 op thumb en 5 op upload, elke URL precies
   zoals de nep hem gaf), `test_de_thumbnail_van_een_goede_foto` (een geval "op
   thumb.wikimedia.org" erbij), `test_een_foto_zonder_goede_thumbnail_valt_af` (6 gevallen erbij:
   het origineel op thumb als thumburl en als url, een thumbnail op een vreemde host, op een host
   met een liggend streepje en op een te lange host, en geen bestandspagina),
   `test_een_geldige_set_komt_door_de_controle` (beide vormen van een thumbnail) en
   `test_een_set_die_niet_klopt` (3 gevallen erbij: een thumbnail over http, een van 1024 px op
   thumb, een origineel op thumb).
2. **Een eigen dienst `thumb`.** Naast de API en upload is thumb.wikimedia.org een dienst van
   Commons (`DIENSTEN`: API, thumb, upload): hooguit één vraag per `PAUZE_S` (1 s; het verslag op
   dev.to zag bij 1 s geen 429), gemiddeld hooguit `BANDBREEDTE` (de rem is nu per host: thumb
   en upload elk hun eigen), elke vraag geteld, en met `BEELD_CONTACT` het contact in de
   User-Agent. Het verkeer in de regel van een goede set en in de fout van `set` heeft drie
   diensten, altijd in die volgorde: "API: 90 vragen, 0 keer 429; thumb: 115 vragen, 0 keer 429;
   upload: 5 vragen, 0 keer 429; contact: nee"; een dienst zonder vragen als "upload: 0 vragen",
   één vraag als "1 vraag". De regel aan het begin van `set`: "BZ4 set: de API van Commons
   hooguit één vraag per 1 s, thumb één per 1 s, upload één per 1 s; contact: ja". Tests:
   `test_tussen_twee_vragen_aan_commons_zit_een_pauze` (thumb erbij: 1 s, een eigen klok),
   `test_van_upload_hooguit_bandbreedte_andere_hosts_niet` (thumb net zo) en elke test met het
   verkeer in de regel of de fout (de nep telt `/duim/` als thumb).
3. **Altijd JPEG.** Elke download van de set (een voorstel en met `beeld.tsv`) vraagt `Accept:
   image/jpeg`, en `haal` kijkt de eerste bytes na (`FF D8 FF`). Is het iets anders, dan stopt
   hij zodra hij 12 bytes heeft, met een fout met de sleutel: "kat-2.jpg: WebP in plaats van
   JPEG" (RIFF … WEBP) of "kat-2.jpg: geen JPEG", en niets op schijf. Zonder dat zou een cache
   die de `Vary` mist, de sha256 in `beeld.tsv` aan WebP kunnen binden, of WebP in de
   kwaliteitstest zetten. De downloads van Hugging Face en de bron blijven zoals ze waren: geen
   Accept en geen controle. Tests: `test_haal_met_jpeg_vraagt_jpeg_en_kijkt_de_eerste_bytes_na`
   (nieuw: de nep geeft WebP op de Accept van een browser, zoals thumb.wikimedia.org; WebP op een
   vraag om JPEG: een WebP van 3 MiB stopt na het eerste stuk, hooguit 1 MiB; HTML en een GIF van
   6 bytes zijn "geen JPEG"; een JPEG van 4 bytes mag) en
   `test_de_set_vraagt_elke_foto_als_jpeg` (nieuw: 240 downloads in een voorstel en een strenge
   set, alle met `image/jpeg`, de API zonder; een cache die WebP geeft is "auto+sneeuw.jpg: WebP
   in plaats van JPEG (bij foto 1 van 120, …" en `set/` blijft leeg). In
   `test_streng_haalt_alleen_wat_ontbreekt_of_niet_klopt` houdt de opnieuw gemaakte thumbnail de
   eerste vier bytes van een JPEG.
4. **Omleidingen.** urllib volgt elke omleiding, ook naar een andere host, en neemt de
   kopregels mee: met `BEELD_CONTACT` zou het contact dan meegaan naar die host, en de eigenaar
   gaf alleen toestemming voor Wikimedia. Nu gaat elke vraag door `_opener` met `_Omleiding`:
   - begon de vraag bij een dienst van Commons (de API, thumb of upload), dan volgt hij alleen een
     omleiding naar https op commons.wikimedia.org, thumb.wikimedia.org of upload.wikimedia.org
     (poort 443). Elke andere is een fout zonder vraag daarheen en zonder nieuwe poging: "HTTP 302
     naar localhost, niet gevolgd (van Commons alleen naar https op commons.wikimedia.org,
     thumb.wikimedia.org of upload.wikimedia.org)", met alleen de host (nooit de URL), en "een
     onbekende host" als het geen gewone hostnaam is;
   - de User-Agent komt per stap opnieuw van `_user_agent`, voor de nieuwe URL: het contact gaat
     alleen naar een dienst van Commons. Accept gaat mee;
   - de pauze en de telling, zo besloten: elke stap naar een dienst van Commons is een vraag als
     elke andere. Hij wacht eerst `_pauze` van die dienst en telt bij die dienst (een 301 van
     upload naar thumb telt één vraag bij upload en één bij thumb, en wacht de pauze van thumb).
     Een stap naar een andere URL (commons.wikimedia.org buiten de API, zoals de 301 van het pad
     van een origineel op thumb) wacht niet, telt niet en krijgt geen contact. Een 429 of 5xx na
     een omleiding telt bij de dienst die hem gaf. De rem (`BANDBREEDTE`) geldt voor de host
     die de bytes gaf;
   - een vraag die elders begon (Hugging Face, dat naar zijn CDN omleidt) volgt hij zoals
     urllib, zonder contact, pauze of telling.

   Tests: `test_een_omleiding_van_commons_naar_elders_volgt_hij_nooit` (nieuw: thumb, de API en
   upload elk naar elders, ook naar dezelfde host en poort over een ander schema; nooit een vraag
   aan de tweede server, elke vraag één keer, niets op schijf),
   `test_omleidingen_alleen_naar_https_op_de_hosts_van_commons` (nieuw: de echte hosts; http, een
   andere poort, een andere host en een kapotte URL vallen erbuiten),
   `test_een_omleiding_binnen_commons_volgt_hij_met_pauze_en_telling` (nieuw: de 301 van een oude
   URL op upload naar thumb, met contact, Accept, de pauze van thumb en de telling; de 301 van een
   origineel op thumb naar commons.wikimedia.org zonder contact, pauze of telling, en daar "geen
   JPEG") en `test_hugging_face_naar_zijn_cdn_volgt_hij_zonder_contact` (nieuw).
5. **Waarom een kandidaat afviel.** `_waarom` geeft van elke kandidaat die de set niet neemt de
   eerste reden uit een vaste reeks (`REDENEN`): geen image/jpeg, origineel niet op upload, geen
   bestandspagina, te klein, te groot, mens erop, licentie, geen thumbnail van 1280 px,
   thumbnail op een andere host, te langgerekt, onleesbare gegevens, en in de keuze zelf al
   gekozen en (bij een ander onderwerp) beeldt een categorie af. Bij "thumbnail op een andere
   host" staat de host erbij als het een gewone hostnaam is van hooguit 64 tekens (letters,
   cijfers, liggende streepjes en punten), anders "een onbekende host"; nooit de URL. De regel
   van een tekort en die van een reserve noemen de vier vaakste redenen met hun aantal, de
   meeste eerst en bij een gelijk aantal op de tekst (dus elke run in dezelfde volgorde), en de
   rest samen: "BZ4 set: Commons: maar 0 bruikbare foto's voor auto (24 kandidaten bekeken;
   afgevallen: 14 thumbnail op een andere host (127.0.0.1), 2 geen image/jpeg, 2 licentie, 2 mens
   erop, 4 om een andere reden)", "BZ4 reserve zonsondergang+strand: 0 bruikbare foto's (eerste
   pagina per licentie; afgevallen: 1 thumbnail op een andere host (127.0.0.1))". Bij een reserve
   telt elke kandidaat één keer en telt "al gekozen" niet. Op `45bb926` had de regel zo
   "afgevallen: … thumbnail op een andere host (thumb.wikimedia.org)" gezegd. Tests:
   `test_een_tekort_zegt_waarom_de_kandidaten_afvielen` (nieuw: de toestand van `45bb926` van de
   andere kant, de tool verwacht thumb.wikimedia.org en de nep geeft de thumbnails elders; 29
   tekorten, elk met redenen, precies voor auto+sneeuw, auto, fiets, bloem en een reserve, en
   geen URL in het log), `test_waarom_een_foto_afvalt` (nieuw: de reden van elk geval van
   `test_een_foto_zonder_goede_thumbnail_valt_af`, uit `REDENEN`, zonder URL, wachtwoord of poort),
   `test_afgevallen_noemt_de_vier_vaakste_redenen` (nieuw) en
   `test_een_tekort_stopt_de_keuze_niet_en_de_reserves_worden_geteld` (de redenen van kat+sneeuw,
   eten en gitaar precies).
6. **BZ1 noemt thumb.wikimedia.org**, na commons.wikimedia.org: de wortel
   `https://thumb.wikimedia.org/`. Leidt een host om, dan zegt de regel waarheen en wat daar het
   antwoord was (bijvoorbeeld "BZ1 thumb.wikimedia.org: bereikbaar (HTTP 301 naar
   commons.wikimedia.org, daar HTTP 200)"), of dat de tool hem niet volgde ("bereikbaar (HTTP 302
   naar …, niet gevolgd (…))"); wat de wortel van thumb.wikimedia.org antwoordt, is hier niet
   bekend. Test:
   `test_bereik_meldt_thumb_ook_als_de_wortel_omleidt` (nieuw: beide, en geen vraag aan de andere
   host).

Wat niet veranderde: de zoekvragen, de keuze, `PAUZE_API_S`, `PAUZE_S`, `BANDBREEDTE`,
`MAX_WACHT_S`, `WACHT_TOTAAL_S`, de User-Agent, `BEELD_CONTACT` in `ci.yml` (alleen op `BZ1 bereik
per host` en `BZ4 de 120 foto's`; de opmerkingen in `ci.yml` noemen geen hosts die nu niet meer
kloppen, dus `ci.yml` is niet aangeraakt) en `test_beeld_ci.py`.

Teruggedraaid getest (de `beeld_model.py` van `45bb926` terug, de nieuwe tests ongewijzigd; daarna
de nieuwe weer terug, met `cmp` en `git status` nagekeken): in `test_beeld_tools.py` 58 rood en 100
groen. Rood:
- de 22 gevallen van `test_waarom_een_foto_afvalt` en
  `test_afgevallen_noemt_de_vier_vaakste_redenen` (de oude gaf geen reden);
- 8 tests van `set` op "Commons: 29 tekort(en) … (6 van 120 foto's opgehaald, …)", want de oude
  `_duim` nam geen thumbnail op de thumb van de nep, met
  `test_ci_op_45bb926_de_thumbnails_staan_op_thumb_wikimedia_org` voorop; om dezelfde reden
  `test_een_fout_bij_het_tellen_van_de_reserves_zegt_hoeveel_er_kwamen` (6 in plaats van 116),
  `test_een_tekort_stopt_de_keuze_niet_en_de_reserves_worden_geteld` en
  `test_een_tekort_zegt_waarom_de_kandidaten_afvielen` (een tekort zonder "afgevallen: …");
- 5 van `lees_set`: `test_een_geldige_set_komt_door_de_controle` ("regel 17: url hoort bij
  https://upload.wikimedia.org/"), 3 gevallen van `test_een_set_die_niet_klopt` (de oude tekst) en
  `test_de_thumbnail_van_een_goede_foto` "op thumb.wikimedia.org";
- 11 met thumb in een regel: de regel aan het begin van `set` in de 2 gevallen van
  `test_zonder_beeld_contact_gaat_er_geen_contact_mee` en de 5 van
  `test_met_beeld_contact_alleen_naar_commons_en_nooit_in_het_log`, het verkeer zonder "thumb: …"
  in `test_een_api_die_429_blijft_zeggen_staat_met_de_tellers_in_de_fout` en in de 3 gevallen van
  `test_een_antwoord_dat_de_user_agent_herhaalt_zet_het_contact_niet_in_het_log`;
- de 2 tests van BZ1; de pauze van thumb ("[8.0, 7.75, 1.0]": de vraag aan thumb zonder pauze) en
  zijn rem (3,2 MB van thumb zonder wachten);
- de 4 nieuwe tests van de omleidingen en van `haal` met `jpeg`.

27 daarvan stopten op een naam of argument dat de oude tool niet had (`_waarom` 22 keer,
`MAX_REDENEN`, `THUMB`, `ACCEPT`, en 2 keer `jpeg`). Groen op de oude tool, terecht:
`test_een_foto_zonder_goede_thumbnail_valt_af` (de oude nam die gevallen ook niet) en
`test_hugging_face_naar_zijn_cdn_volgt_hij_zonder_contact` (urllib deed dat al). Met alleen de
nieuwe namen erbij in de oude tool (`THUMB`, `ACCEPT`, `JPEG_BEGIN`, `ALLEEN_COMMONS`,
`MAX_HOSTNAAM`, `MAX_REDENEN`, `GEEN_DUIM`, `ANDERE_HOST`, `REDENEN`, en `DIENSTEN` met thumb, waar
de oude `("API", "upload")` had): 55 rood en 103 groen. Groen werden alleen de 3 gevallen van
`test_een_antwoord_dat_de_user_agent_herhaalt_zet_het_contact_niet_in_het_log`: de oude `verkeer`
loopt over `DIENSTEN` en zei nu "thumb: 0 vragen", en het contact hield hij al uit het log. De 55
blijven rood om de goede reden: 28 op een bewering over gedrag (geen thumbnail op thumb, de oude
regels, geen pauze of rem voor thumb), 3 op het argument `jpeg` van `haal` en 24 op een functie die
er niet was (`_waarom` 22 keer, `_afgevallen`, `_commons_hosts`). Met de nieuwe tool:
`test_beeld_tools.py` 158 groen (met `test_beeld_ci.py` 167), `tests/analyse` 442 groen.

### De review van de reparatie

Eén review van `d9a6f1c`, acht bevindingen. Elke bevinding eerst zelf nagekeken: de vier over
tests met de mutant van de reviewer op de tool, R5 met een directe aanroep, de drie over tekst in
de documenten zelf. Ze klopten alle acht. R1 tot en met R4 vonden een gat in de tests, niet in de
code; R5 was een fout in de code (zonder lek). Per bevinding met code een test die zonder de code
faalt.

| Bevinding | Wat er was | Wat er gedaan is | Test (rood zonder de reparatie) |
|---|---|---|---|
| R1 (should) | De bewaker van `_Omleiding` neemt mee waar de vraag begon (`begin`), zodat een keten van thumb via commons.wikimedia.org buiten de API (een host van Commons, maar geen dienst) naar een andere host bij de tweede omleiding stopt. Geen test had zo'n keten: met `begin = req.full_url` (mutant M7) bleven alle tests groen, en ging er een vraag naar die andere host | Alleen een test; de code klopte | `test_een_keten_via_commons_buiten_een_dienst_naar_elders_volgt_hij_niet` (nieuw): thumb geeft een 301 naar `/omweg` op de host van de nep, die een 302 naar de tweede server; de API net zo. Precies "kat-1.jpg: HTTP 302 naar localhost, niet gevolgd (…)", geen vraag aan de tweede server, de omweg zonder contact, niets op schijf. Rood op M7 ("DID NOT RAISE") |
| R2 (should) | Dat `Accept: image/jpeg` ook bij een nieuwe poging meegaat (na een 503 of 429), testte niets; alleen de eerste poging en een omleiding. Met Accept alleen bij de eerste poging (M27) bleven alle tests groen | Alleen een test | `test_elke_nieuwe_poging_van_een_foto_vraagt_weer_jpeg` (nieuw): thumb zegt 503, dan 429 (Retry-After 3), dan 200, op de nepklok; drie keer `image/jpeg`, en thumb telt 3 vragen, 1 keer 429 en 1 keer 5xx. Rood op M27 (`['image/jpeg', None, None]`) |
| R3 (should) | Een 429 of 5xx na een gevolgde omleiding telt bij de dienst die hem gaf (`e.geturl()`), maar geen test keek ernaar: met de URL van het begin (M8) bleven alle tests groen, en stond een 429 van thumb bij upload in de fout | Alleen een test | `test_na_een_omleiding_telt_en_remt_de_host_die_antwoordde` (nieuw): upload geeft een 301 naar thumb, thumb een 429 en dan 200; thumb 2 vragen en 1 keer 429, upload 2 vragen en 0 keer 429, Accept bij alle vier. Rood op M8 (`(2, 0, 0) == (2, 1, 0)`) |
| R4 (nit) | De rem (`BANDBREEDTE`) rekent de bytes bij de host die ze gaf (`antwoord.geturl()`); met de URL van het begin (M9) bleven alle tests groen. In de nep staan thumb en upload op dezelfde host (127.0.0.1), dus het voorstel van de review (de host van thumb in `_klaar`) zag daar geen verschil | Alleen een test, met thumb op localhost en upload op 127.0.0.1 (dezelfde nep) | Dezelfde test als R3: na de download staat alleen `localhost` in `_klaar`, en de laatste slaap is precies de grootte van de thumbnail gedeeld door `BANDBREEDTE`. Rood op M9 (`{'127.0.0.1'} == {'localhost'}`), ook de tweede test van R5 |
| R5 (nit) | De bewaker leest de host met urlsplit (kleine letters, poort 443 ook als die er staat), `_dienst` met startswith op de hele URL. Een omleiding naar `https://THUMB.wikimedia.org/…` of `https://thumb.wikimedia.org:443/…` kwam door de bewaker, maar als geen dienst: zonder pauze, telling, rem en contact, terwijl de docstring van `_Omleiding` en het runbook zeggen dat een stap naar een dienst wacht en telt. Nagekeken: `_dienst` gaf `None` en `_user_agent` de User-Agent zonder contact. Geen lek: het contact bleef weg | `_dienst` leest de oorsprong nu met `_oorsprong`, net als de bewaker, en daarna het begin van het pad. Zo is elke dienst een host van Commons, en is het enige wat de bewaker doorlaat zonder dienst te zijn commons.wikimedia.org buiten de API. Het contact gaat zo ook mee naar `https://THUMB.wikimedia.org/…`: dezelfde host, binnen de toestemming. `_duim` en `lees_set` blijven streng op de URL zoals de API hem geeft | `test_de_dienst_leest_de_host_zoals_de_bewaker` (nieuw, met de echte URL's: hoofdletters en `:443` bij de API, thumb en upload; http, een andere poort, een andere host en een kapotte URL zijn geen dienst) en `test_een_omleiding_naar_thumb_in_hoofdletters_wacht_telt_en_remt` (nieuw: upload geeft een 301 naar `LOCALHOST`, en die stap heeft het contact, wacht `PAUZE_S`, telt en remt bij thumb). Met de oude `_dienst` beide rood (`None == 'API'`; de stap zonder contact) |
| R6 (should) | Stap 4 van het runbook zei dat de URL in `voorstel-beeld.tsv` op upload.wikimedia.org staat. Nu staat hij meestal op thumb.wikimedia.org, zoals de API hem gaf; wie stap 4 volgde, vond goede regels fout | Stap 4: de URL zoals de API hem gaf, op thumb.wikimedia.org of in de oude vorm op upload.wikimedia.org (`…/thumb/…/1280px-…`); een origineel dat niet breder is, op upload.wikimedia.org | geen (tekst) |
| R7 (nit) | Het runbook raadde aan `THUMB` aan te passen als bijna alle kandidaten "thumbnail op een andere host" zeggen, zonder te zeggen dat `THUMB` ook het contact krijgt en een toegestaan doel van omleidingen is. De toestemming van Jonas geldt alleen voor Wikimedia | Erbij: alleen naar een host van Wikimedia (`*.wikimedia.org`); een andere host vraagt eerst toestemming van Jonas. Net zo in de opmerking bij `THUMB` in `beeld_model.py` | geen (tekst) |
| R8 (nit) | De rij BZ1 in de tabel van het bouwontwerp noemde huggingface.co, upload.wikimedia.org en images-assets.nasa.gov; `bereik` vraagt ook commons.wikimedia.org (al sinds `b820fdd`) en nu thumb.wikimedia.org | Een noot in die rij, zoals de andere noten in het bouwontwerp (de vraag zelf blijft staan): de vijf hosts van `bereik` | geen (tekst) |

Een omleiding naar een dienst van Commons blijft een vraag als elke andere (punt 4 hierboven): na
R5 geldt dat ook als de host in hoofdletters of met `:443` komt.

Teruggedraaid getest (per bevinding de code die de test vastlegt op de tool gezet, met een kopie
van de goede tool ernaast; daarna die kopie terug, met `cmp` en `git status` nagekeken),
`test_beeld_tools.py` met de 5 nieuwe tests: M7 1 rood en 162 groen; M27 2 rood (de tests van R2
en R3) en 161 groen; M8 1 rood en 162 groen; M9 2 rood (de test van R3 en R4 en de tweede van R5)
en 161 groen; de oude `_dienst` (startswith) 2 rood (de twee van R5) en 161 groen. Elk rood om de
reden in de tabel. Met de nieuwe tool: `test_beeld_tools.py` 163 groen (met `test_beeld_ci.py`
172), `tests/analyse` 447 groen; ruff schoon. `ci.yml` en `test_beeld_ci.py` zijn niet
veranderd.

### Open na 45bb926

- **Of thumb.wikimedia.org de 120 geeft**, zegt de volgende run van `beeld-model` (`tools/beeld`
  is een pad van de job). Dat de thumburl daar staat, is uit de tweede hand; staat hij ergens
  anders, dan zegt de regel van elk tekort nu waar ("thumbnail op een andere host (…)").
- **Of 1 s en `BANDBREEDTE` genoeg zijn voor thumb**, met 115 thumbnails na elkaar: het verslag
  op dev.to zag bij 1 s geen 429, maar met hoeveel vragen staat er niet. Een 429 daar zegt de
  fout nu met "thumb: … keer 429".
- **WebP.** Of thumb.wikimedia.org of een cache ooit WebP geeft op een vraag om JPEG, is niet
  gezien; gebeurt het, dan is `set` rood op die foto ("WebP in plaats van JPEG"), nooit stil.
- **De 301 van upload naar thumb** ("eventually") volgt de tool, met de pauze en de telling van
  thumb. Een `beeld.tsv` met oude URL's op upload blijft dan werken; de sha256 hoort bij de
  bytes, niet bij de host.
- **Of thumbnails de 429 van upload voorkomen** (zie "Open na 9ebf298") is niet meer de vraag:
  de thumbnails komen nu van een andere host. Upload geeft alleen nog de originelen die niet
  breder zijn dan 1280 px.
- **Hoe lang BZ4 duurt** (de kwaliteitspoort in int8 en fp32) is nog nooit gemeten.
