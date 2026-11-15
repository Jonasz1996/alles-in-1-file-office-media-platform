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

`api/tests/beeld/test_tokenizer.py` (job `api`, 89 tests, ±3 s): de tabel van het ontwerp,
"hond op het strand", "ΣΟΦΙΑ ΟΔΟΣ", byte fallback, afkappen, `tokenizer.bin` gelijk aan de JSON
(ook met fuzz op een vaste seed), geen kwadratische tijd op lange of vreemde invoer, geen tekst in
een fout, kapotte en te grote bestanden, en BZ7 op een bestand met 256.000 stukken.

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
    een schaal per rij (Gather, Cast, Mul); de Conv van de patches blijft fp32;
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
het echte bestand) met de standaardbibliotheek. `test_beeld_omzetten.py` (71 tests): de
voorwaartse stap van een eigen numpy-versie in de test, fp32 ≤ 1·10⁻⁵, int8 cosinus ≥ 0,99, twee
omzettingen in twee processen met dezelfde sha256, maxrss en tijd per proces (`wait4`), `meta.json`,
F16 en BF16, 18 liegende koppen (een kop langer dan het bestand of van 2⁶³ bytes, offset buiten
het bestand of negatief, overlap, een gat, afgekapt, een onbekend dtype, een lengte die niet bij
de vorm past, geen JSON…; ook als proces: code 2 en geen traceback), een bron die niet klopt
(`model_type` clip, een laag te veel of te weinig, een tensor die ontbreekt of te veel is, NaN,
een andere voorbewerking…), een uitvoermap die niet leeg is, een fout halverwege die niets
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
  User-Agent noemt geen persoon, e-mail of repository. `test_beeld_tools.py` (42 tests) draait hem
  tegen een eigen HTTP-server met een nep-API van Hugging Face en van Commons.

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
   `workflow_dispatch`, geen cache.
2. `uv sync --frozen --extra analyse`.
3. BZ1 `bereik`, dan `bron` (de vijf bestanden, of het voorstel), dan twee keer omzetten met de
   opdrachtregel van de dienst (de eerste met `--fp32`), dan `vergelijk` (BZ2: sha256 van beide,
   tijd en maxrss ≤ 1,5 GB, `models.lock`), dan `set` (BZ4: de 120 foto's).
4. pytest `-m beeld_echt` voor BZ3/BZ7, BZ4 en BZ6 (die onder `taskset -c 0`); een `SKIPPED` telt
   als rood.
5. `uitslag`: rood zolang er een voorstel ligt ("models.lock mist de regels van SigLIP",
   "beeld.tsv ontbreekt"). Dan het verslag via `actions/upload-artifact@v4` en als laatste stap
   het blok `=== kort ===`.

`tests/analyse/test_beeld_ci.py` (6 tests) houdt die vorm vast: alleen `push` en `pull_request`,
geen cache, de paden, geen `${{` in het script van de poort, dezelfde opdrachtregel als
`OMZET_BOOT` (na de samenvoeging ook tegen `app/analyse/runtime.py` van X2a), elke BZ een stap, en
`beeld_echt` in geen andere job.

## De go/no-go van X3

Keuze 73 en K2-12 noemen drie delen. De beslissing valt na CI, niet hier.

| Deel | Waar bewezen | Stand |
|---|---|---|
| De kleine vorm rekent als een eigen numpy-versie | `test_beeld_omzetten.py` in `api-analyse` (hier gedraaid) | **gehaald hier**: fp32 grootste verschil 2,5·10⁻⁷ (beeld) en 1,1·10⁻⁷ (tekst), grens 1·10⁻⁵; int8 kleinste cosinus 0,99969 (beeld) en 0,99985 (tekst), grens 0,99; twee keer dezelfde sha256 |
| Twee keer omzetten geeft dezelfde sha256 (BZ2) | de stap `vergelijk` in `beeld-model` | **in CI na de push**. Met het echte model nog niet gedaan: huggingface.co is vanuit de sandbox dicht. Op de kleine vorm hier wel (drie keer, zelfde bytes) |
| De kwaliteitspoort is groen (BZ4) | `test_beeld_kwaliteit.py` in `beeld-model` | **in CI na de push**, en pas als `beeld.tsv` er is: de eerste run stelt de set voor en is rood. Daarna een tweede run met de set |

Wat de eerste run na de push laat zien: of de runner bij de bron kan (BZ1), de commit van
`google/siglip2-base-patch32-256`, de voorgestelde regels voor `models.lock` en de voorgestelde
set. De job is dan rood met "models.lock mist de regels van SigLIP" en "beeld.tsv ontbreekt"; zo
is het bedoeld. BZ3 en BZ7 draaien in die eerste run al op het echte tokenizer.json. BZ4 en BZ6
draaien ook, op de voorgestelde set, en hun getallen staan in het verslag, maar de uitslag is pas
geldig met de vastgezette set.

## Metingen

Hier gemeten, in de sandbox, onder `taskset -c 0-2`. Met het echte model is hier niets gemeten
(de bron is dicht); die getallen komen uit de job.

| Wat | Hoe | Uitkomst |
|---|---|---|
| Omzetter, kleine vorm (2 lagen, breedte 64), met `--fp32` | drie processen zoals de dienst, `wait4` | 0,22 / 0,23 / 0,24 s; maxrss 71,5 MiB elk; dezelfde sha256 |
| Kleine vorm, fp32 tegen numpy | grootste absolute verschil | beeld 2,47·10⁻⁷, tekst 1,10·10⁻⁷ (grens 1·10⁻⁵) |
| Kleine vorm, int8 tegen numpy | kleinste cosinus | beeld 0,99969, tekst 0,99985 (grens 0,99) |
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
   - `modellen_ophalen` in `install.sh` haalt elke regel met een URL. Met de bronregels erin haalt
     een installatie met `ANALYSE=aan` ook de 1,51 GB van `model.safetensors`, en houdt die, ook
     zonder zoeken op beeld; de uitkomsten (url `-`) slaat hij over;
   - caps meldt elke regel. Na het omzetten haalt `install.sh` de bron weg (Keuze 73), dus
     `siglip2-b32-256/bron/*` staat dan op `ontbreekt`; en zonder omzetting staan de uitkomsten op
     `ontbreekt`. Gezichten vragen `ready(FACES_MODELS)` (a0.md, Open 4), maar `/beheer` moet het
     goed tonen.

   Beide moeten de regels onder `*/bron/` overslaan (of `lock.py` houdt ze apart), en de
   uitkomsten van X3 horen pas mee te tellen als beeld aan staat. Dat is X3b, met de functies voor
   beeld in `install.sh`. Tot dan: de pins (punt 2) niet op een tak zetten waar `ANALYSE=aan`
   gebruikt wordt, of samen met die wijziging. Op de LXC van Jonas staat `ANALYSE` tot 0.8.1 uit.
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
7. **De job `api-analyse`** is van X2a; de tests in `tests_analyse` van X3a (ook
   `test_beeld_omzetten.py`, de go/no-go deel 1) draaien in CI pas als die job er is.
8. **BZ2 op twee runners** (Afwijkingen): of een andere x86-CPU dezelfde bytes geeft, bewijst pas
   een tweede run op een andere runner, of de LXC (de sha256 in `models.lock` vangt het).
9. **De dienst `analyse-omzetten`** (compose van X2a) draait als `omzetten` (10004) met groep
   `analyse`, `read_only` en een `/tmp` van 64 MB. De omzetter schrijft alleen in `/uit` en gebruikt
   geen `/tmp`; met de echte bron in de container is hij nog niet gestart (X3b, `install.sh`).

## Klaar als

Elke regel van "Bouwt", "Tests" en "Klaar als" van X3 die X3a raakt (bouwontwerp, X3; §4.6).

| Regel | Test | Uitkomst |
|---|---|---|
| **Bouwt:** `app/analyse/tokenizer.py` met `tokenizer.bin` (Keuze 74) | `tests/beeld/test_tokenizer.py` (89) | groen |
| **Bouwt:** `app/analyse/onnxw.py` (gedeeld met de nepmodellen van X2) | `test_onnxw.py` (64), `test_onnxw_ort.py` (42) | groen |
| **Bouwt:** de omzetter `siglip.py`, `meta.json` (Keuze 73) | `test_beeld_omzetten.py` (71): `test_fp32_beeld_gelijk_aan_numpy`, `test_fp32_tekst_gelijk_aan_numpy`, `test_int8_cosinus_minstens_0_99`, `test_twee_omzettingen_dezelfde_sha256`, `test_meta_json`, `test_een_kop_die_liegt*` | groen |
| **Bouwt:** de dienst `analyse-omzetten` | in `compose.analyse.yaml` van X2a (`25ac3e7`); de opdrachtregel is dezelfde: `test_de_omzetter_start_zoals_de_dienst_analyse_omzetten` en `test_beeld_omzetten.py` (`_start_meet`) | de dienst is van X2a; de vergelijking met `OMZET_BOOT` na de samenvoeging |
| **Bouwt:** trust-on-first-use | — | niet in X3a: hoort bij `install.sh` (X3b) |
| **Bouwt:** de CI-job `beeld-model` (§4.6) | `test_beeld_ci.py` (6), yamllint | groen; de job zelf **in CI na de push** |
| **Tests:** `api/tests/beeld/test_tokenizer.py` (de tabelrij) | zie Bouwt | groen |
| **Tests:** `api/tests_analyse/test_beeld_omzetten.py` (de tabelrij: fp32 ≤ 1·10⁻⁵, int8 ≥ 0,99, twee keer dezelfde sha256, maxrss, een liegende kop) | `test_maxrss_en_tijd_gemeten`, `test_een_kop_die_liegt_als_proces` en de rest | groen (71) |
| **Tests:** `api/tests_analyse/test_beeld_kwaliteit.py` (marker `beeld_echt`) | de poorten van Keuze 83: `test_nl_precisie_at_5`, `test_combinaties_in_de_top_10`, `test_int8_tegen_fp32`, `test_meer_zoals_deze`, `test_zelftest_met_drie_kleuren`; de rekenstukken in `test_beeld_echt_hulp.py` | hulp groen; de poorten **in CI na de push**, groen pas na de pins |
| **Tests:** `api/tests_analyse/test_beeld_echt_perf.py` | het model-deel van BZ6 (≥ 500 per minuut, vraag p95 ≤ 250 ms) | **in CI na de push**; de rest van BZ6 is X3b |
| **Tests:** `api/tests/test_analyse_grens.py` (van X2) dekt de omzetter | X2a; hier `test_zonder_numpy_in_de_app` en `test_no_numpy_in_app.py` | groen hier |
| **Klaar als:** `test_tokenizer.py` is groen, en BZ3 (met het bestand van SigLIP 2 zelf, zonder Gemma) is groen | `test_tokenizer.py`; BZ3: `test_bz3_de_lijst_met_het_echte_bestand`, `test_bz3_een_eigen_bpe_op_2000_zinnen`, `test_bz3_tokenizer_bin_is_from_json` | `test_tokenizer.py` groen; BZ3 **in CI na de push** |
| **Klaar als:** de omzetter is gelijk aan de eigen numpy-versie op de kleine vorm, en geeft twee keer dezelfde sha256 | `test_beeld_omzetten.py` | groen |
| **Klaar als:** de go/no-go na X3a is gehaald (Keuze 73, K2-12) | deel 1 hier; deel 2 (BZ2) en 3 (BZ4) in `beeld-model` | **open**: de beslissing valt na CI |
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
