# X2a: de container analyse (golf 5)

X2a is het eerste deel van X2, "Gezichten automatisch" ([bouwontwerp.md](bouwontwerp.md), X2 en
§3.11; Keuze 56–60, 68, 75; de rij X2a in §2.0). Het bouwt de container `analyse` op het
fundament van A0 ([a0.md](a0.md), [a0-analyse.md](a0-analyse.md)): de worker met zijn twee
kinderen, de zelftest, `caps`, de opdracht `faces` (stap 3–8 van "De keten"), de compose, het deel
van `install.sh`, de tests van laag 2 en 3, en de CI-jobs `api-analyse` en `analyse-image`.

Wat X2a **niet** bouwt: de keten in de app (de rij, plannen, schrijven, versleutelen), `group`,
de API, de web, `/beheer`, `analyse-bench` en `analyse-kwaliteit` (X2b), en alles van beeld (X3).
`group`, `embed_image`, `embed_text`, `rank` en `forget` geven `not_built`.

**Onzichtbaar tot 0.8.1.** De versie blijft 0.8.0; er is geen nieuwe migratie (0001–0012 staan
vast). `install.sh` doet zonder `ANALYSE` in `.env` precies wat 0.8.0 deed: de standaard staat in
één constante, `ANALYSE_STANDAARD=uit`, en I2 zet hem op `aan`. Tests en CI zetten hem aan met
`ANALYSE=aan`. `app/cli.py` houdt `ANALYSE_STUBS` (`analyse-bench` blijft een stub).

Gebouwd op de tak `wip/golf5-x2a`, vanaf `b1a128e` (A0 met stap 0 van X3a: `onnxw.py` en
`api/tests_analyse/`), in de worktree `/home/user/wt-g5-x2a`, naast X3a. Niet gepusht.

## Gebouwd

### De worker (`api/app/analyse/worker/`)

Start: `python -I -c runtime.BOOT /srv --socket /run/analyse/worker.sock --models /models`
(compose). BOOT zet `/srv` vooraan in `sys.path` en draait het pakket als `__main__`.

- **`__main__.py`**: als eerste `PR_SET_DUMPABLE` 0 (vóór de import van de app), dan de sleutel
  uit `os.environ` halen (`pop`; nooit uit argv). Korter dan 32 tekens: melding op stderr en exit
  2. `--check SOCKET` is de healthcheck (`health.alive`), `--selftest` de zelftest als JSON,
  `--local` voor de tests van laag 2 (geen zelftest, sandbox `off`). Zonder `--local` eerst de
  zelftest; faalt die, dan een `RefusingWorker`, en de logregel zegt dat.
- **`server.py`** (pid 1, zonder numpy): de socket (0660), per verbinding hello en het bewijs over
  de uid van `SO_PEERCRED`, dan één vraag (`wire.parse`). `caps` en `cancel` doet pid 1 zelf; een
  opdracht voor een kind krijgt een nummer en een plaats in de rij van één per kind (`busy` voor
  een tweede), dan `started`, dan het kind. De deadline telt vanaf `started`: daarna `terminate`
  in het kind, na `KILL_GRACE_S` `SIGKILL` en een nieuw kind (`timeout`). `cancel` met het nummer,
  of de verbinding sluiten, breekt af. Eerst is het kind weer vrij, dan pas gaat `done` weg. De
  wachter (`guard.Watch` van media) meet het geheugen van de kinderen; boven hun `RLIMIT_DATA`
  gaat een kind weg en wordt de opdracht `too_big`. Het log heeft alleen op, nummer, kind, code
  en tijden.
- **`child.py`**: de kinderen `werk` en `zoeken` (`wire.CHILDREN`). Lui gestart bij de eerste
  opdracht, met `exec` en een schone omgeving (`env()`: PATH, LC_ALL, HOME en de draden van
  OpenBLAS; nooit de sleutel), in een eigen procesgroep. fd 3 is de socketpair met pid 1, fd 4 de
  pijp voor "stop", fd 2 een pijp naar pid 1 (pid 1 logt alleen dat er een regel was en hoe lang).
  Vóór de exec: `PR_SET_PDEATHSIG` (SIGKILL), `RLIMIT_DATA` (werk 1 GiB, zoeken 1,5 GiB),
  `RLIMIT_CORE` 0, `RLIMIT_NOFILE` 256, `RLIMIT_FSIZE` 64 MiB en nice (werk 19). In de container
  start een kind alleen uit `/opt/venv/bin/python-tekst` (0711, van root): een programma dat het
  kind niet mag lezen, maakt het vanaf de exec niet-dumpbaar (K1-04). Het kind zet zelf nog eens
  `PR_SET_DUMPABLE` 0, zet `/opt/analyse` in `sys.path` (alleen in het kind), laadt numpy en
  onnxruntime, zet de telemetrie van onnxruntime uit en zegt "klaar". `werk` leeft 8 opdrachten
  of 10 minuten (na de achtste meteen weg; de negende krijgt een nieuw), na een timeout of crash
  altijd een nieuw. Een uitzondering in een kind wordt `internal` met alleen de soort fout.
- **`caps.py`**: per regel van `models.lock` `ok`, `ontbreekt` of `anders` (grootte, sha256 over
  de bytes die het leest, `O_NOFOLLOW`; per bestand één keer gerekend, op apparaat, inode, grootte,
  mtime en ctime), de opdrachten (`caps`, `cancel`, `faces`), en de grenzen van de cgroup (v2
  `cpu.max`/`memory.max`, of v1), zonder numpy.
- **`models.py`** (alleen in het kind): de bytes één keer lezen (`O_NOFOLLOW`), de sha256 over
  precies die bytes, dan `InferenceSession(bytes)` met alleen `CPUExecutionProvider` en één draad;
  `get_providers()` moet precies dat zijn (het wiel heeft ook Azure). Klopt een model niet:
  `model_missing`.
- **`selftest.py`**: uid 10003 (niet 10001), `CapEff` 0, `NoNewPrivs` 1, alleen `lo` en geen
  verbinding naar buiten, `/` alleen-lezen, `/data` leeg, geen `/keys`, `/models` niet
  schrijfbaar, `/srv/zelftest-10001` niet te openen om te schrijven (ook niet via
  `/proc/self/fd`), `python-tekst` 0711 van root en niet leesbaar, en met `--socket` de map van de
  socket (2750, analyse, groep platform).
- **`ops/faces.py`**: stap 3–8 van "De keten". De preview met `pread` vanaf 0 (de positie van de
  fd deelt hij met de app), hooguit 16 MB; alleen WebP, `MAX_IMAGE_PIXELS` 4 MP en de
  waarschuwing voor een decompressiebom als fout (`too_big`), geen WebP `unreadable`, kapot
  `image_bad`. YuNet in BGR, opgevuld tot een veelvoud van 32 (K2-05); het decoderen en NMS zoals
  OpenCV `FaceDetectorYN` (IoU 0,3); `min_px`, `score` en `max` uit de args (de grootste eerst);
  uitlijnen met Umeyama naar het sjabloon van SFace (112 × 112, bilineair); SFace in RGB, 128 ×
  float32, L2-genormaliseerd. De vectoren als ruwe bytes in de memfd `uit` (eerst leeggemaakt),
  in het JSON alleen de kaders, de score, `px` en n.

### De app-kant (`api/app/analyse/`)

- **`runtime.py`**: `BOOT`, `OMZET_BOOT`, `KEY_ENV`, `worker_argv()` en `serve_argv()`.
- **`health.py`**: `alive(socket)`, de healthcheck: een hello zonder sleutel; een hello terug is 0,
  een weigering (`sandbox`) of geen antwoord binnen 4 s is 1.
- **`state.py`**: `view(found, lock_path)` voor `/beheer` (X2b en X3): de stand, de reden (sleutel
  van `monitor.TEXT`), `problem` (de zin), per model naam, versie (12 tekens van de sha256 uit
  `models.lock`) en `ok`, en de grenzen (cpus, MB). Alleen uit `monitor.status()`; geen socket,
  geen query.
- **`status.py`** en `app/cli.py`: `analyse-status` print de eerste regel zoals A0 (`Analyse: ok`
  of `Analyse: <stand> (<reden>)`) en, als de worker antwoordt, per model of het klopt, de grenzen
  en de opdrachten.
- `client.py` en `monitor.py` (A0) zijn niet veranderd.

### Container en installatie (`infra/single/`)

- **`compose.analyse.yaml`**: de dienst `analyse` (`platform:local`, `user: analyse`,
  `network_mode: none`, `read_only`, `cap_drop: [ALL]`, `no-new-privileges`, `pids_limit` 64,
  `core` 0 en `nofile` 256, geen `init`, `/tmp` en `/data` als tmpfs, `analyse-run:/run/analyse`,
  `${MODELLEN_ROOT}:/models:ro`, de healthcheck met `env -i`, `stop_grace_period` 5s); voor de
  dienst `platform` de socket, `PLATFORM_ANALYSE_*` en `SLEUTELS_ROOT:/keys`; de dienst
  `analyse-omzetten` (profiel `omzetten`, uid 10004, zonder sleutel, socket en `/models`) voor
  X3a. De sleutel alleen in `environment`, nooit in `command` of de healthcheck.
- **`compose.analyse-limits.yaml`**: 3 GB, 1 cpu en `cpu_shares` 256 (en 3 GB, 1 cpu voor de
  omzetter).
- **`install.sh`**: `ANALYSE_STANDAARD`, `want_analyse`, `analyse_supported`, `analyse_shown`,
  `merge_analyse_env`, `analyse_path_ok`, `analyse_dirs`, `model_name_ok`, `ophalen`, `model_dir`,
  `zet_neer`, `modellen_ophalen`, `decide_analyse`, het vierde argument van `compose_files`,
  `analyse_start` en `slot_analyse`. Nooit fataal: een map die niet klopt geeft een run zonder
  analyse, een model dat niet komt `models_missing`, een start die faalt een waarschuwing.
- **`.env.example`**: het blok over `ANALYSE` (niet ingevuld: geen regel is uit).

### Tests

- `api/tests_analyse/` (job `api-analyse`): `fake_models.py` (nep-YuNet en nep-SFace met
  `onnxw.py`), `echt.py` (de echte worker als proces, of pid 1 in de test), `proef.py` en
  `pid1_proef.py` (een eigen BOOT voor de kinderen), `test_worker_faces.py` (19),
  `test_worker_sandbox.py` (16), `test_analyse_kinderen.py` (12), `test_analyse_contract_real.py`
  (13: `analyse_contract.py` van A0 tegen de echte worker) en `test_models_real.py` (5, laag 3).
  Met `test_onnxw_ort.py` van X3a (42) samen **107**, niets gedeselecteerd.
- `api/tests/analyse/test_analyse_caps.py` (nieuw: 14 tests, 23 met de parameters),
  `test_analyse_monitor.py` (van 23 naar 31 tests, 36 met de parameters: de regels van
  `analyse-status`, `state.view`), `api/tests/test_compose_analyse.py` (nieuw: 10 tests, 11 met de
  parameters).
- `infra/single/tests/test_install_analyse.sh` (120 controles) en `test_analyse_container.sh`.

### CI (`.github/workflows/ci.yml`)

- **`api-analyse`**: `uv sync --frozen --extra analyse` in `.venv-analyse`; `pytest -q
  tests_analyse -m "not beeld_echt" -rs` onder `taskset`, en de laatste regel moet precies
  `107 passed` zijn (`ANALYSE_TESTS`, en `ANALYSE_DESELECTED` 0); de modellen in een cache op de
  sha256 van `models.lock`; daarna de twee licenties byte voor byte tegen die van opencv_zoo op
  de commit uit `models.lock`; `laag3.json` als artefact (tijden en maxrss, geen beelden).
- **`analyse-image`**: het platform-image zoals `media-image`, dezelfde cache van modellen, dan
  `test_analyse_container.sh`; de regels `goed`/`FOUT` in de samenvatting.
- **`infra`**: `docker compose config` met `compose.analyse.yaml` en de grenzen, met en zonder
  het profiel `omzetten`; `test_install_analyse.sh` na die van zoeken.

### Documenten

- [docs/runbooks/analyse.md](../runbooks/analyse.md): het deel van X2 (wat het script doet, de
  stand en elke reden, de afscherming, uit, de sleutels, terug naar 0.8.0).
- `infra/single/README.md`: `dc()` met de bestanden van de analyse, "Wat er draait", en de
  sectie "Gezichten: de container analyse (tot 0.8.1 uit)".

## Vormen die X2b (en X3) gebruiken

Wat het ontwerp open liet en X2a vastlegde; X2b bouwt erop.

1. **`faces` aanroepen.** `client.call(settings, Op.FACES, wire.FacesArgs(min_px=40, max=40,
   score=0.9), fds=[preview × k, uit])` met k ≤ `wire.PHOTOS_PER_OP`, elke preview een fd die
   alleen-lezen open is (de worker leest met `pread` vanaf 0 en raakt de positie niet), `uit` een
   memfd (`wire.out_memfd`). Antwoord: `FacesData.photos` in de volgorde van de previews; per foto
   `code` (`ok`, `unreadable`, `too_big`, `image_bad`), `w` en `h` van de preview, `faces` (x, y, w,
   h als deel van de preview, `score`, `px` de korte kant in px binnen de preview) en `n`. `uit`
   heeft daarna precies `vector_bytes()` bytes: n × 512 per foto, achter elkaar. Een foto die niet
   ok is, heeft niets in `uit`.
2. **Codes voor de hele opdracht.** `model_missing` (vóór de eerste foto), `busy` (werk is bezig),
   `timeout` (de deadline; daarna een nieuw kind), `cancelled`, `too_big` (het kind ging boven
   zijn `RLIMIT_DATA`; pid 1 blijft), `internal` (zonder waarden).
3. **Een opdracht toevoegen** (X2b `group`, X3 `embed_*`, `rank`, `forget`): de op in
   `server.BUILT`, een handler in `child.handlers(kind)` (`Callable[[Job], dict]`, met `Job.n`,
   `args`, `fds`, `deadline_ms`, `stop` en `models`), tussen de stappen `job.stop.check()`, en
   `OpFailed(code, detail)` met een detail zonder waarden. `caps.built` volgt `BUILT` vanzelf.
4. **De stand voor `/beheer`.** `state.view(monitor.status())` geeft `AnalyseView` (analyse,
   reason, problem, models, limits); de web vertaalt de sleutel van `reason`.
5. **`analyse-status`** schrijft de eerste regel zoals A0 (install.sh leest die), daarna de regels
   van `status.lines`.
6. **De tests.** `tests_analyse/echt.py` (`EchteWorker`, `binnen()`, `faces()`, `webp()`) en
   `fake_models.install()` zijn er voor de tests van laag 2 van X2b (`test_worker_group.py`,
   `test_group_perf.py`). Komt er een test bij, dan gaat `ANALYSE_TESTS` in `ci.yml` mee omhoog.
7. **`install.sh`**: de sleutels in `.env` heten `ANALYSE`, `ANALYSE_WORKER_KEY`, `MODELLEN_ROOT`
   en `SLEUTELS_ROOT`; `SLEUTELS_ROOT` is 0700 van 10001 en komt als `/keys` in de app
   (`PLATFORM_FACES_KEYS=/keys`).

## Wat X3a nodig heeft

`onnxw.py` is niet veranderd. Voor X3a liggen klaar, of moeten bij het samenvoegen mee:

- de dienst `analyse-omzetten` in `compose.analyse.yaml` start `runtime.OMZET_BOOT`, en die draait
  `app.analyse.worker.omzetten.siglip` als `__main__` met `--bron /bron --uit /uit`: X3a maakt dat
  pakket. `OMZET_BRON` en `OMZET_UIT` hebben een standaard die niet bestaat (zie Afwijkingen 1);
- het kind `zoeken` bestaat (nice 0, 1,5 GiB, lang levend, decodeert nooit een beeld) maar heeft
  nog geen handlers: X3 vult `child.handlers(Child.ZOEKEN)`; `embed_image` gaat naar `werk`;
- `caps.beeld` blijft leeg (X3);
- `models.lock` mag regels met url `-` hebben (de host maakt het bestand: `install.sh` haalt niets
  en `caps` zegt `ontbreekt` tot het er is), en namen met een map (`model_dir`);
- `install.sh` zet `BEELD` niet in `.env` (Afwijkingen 9);
- in `ci.yml`: `ANALYSE_TESTS` is nu 107 (met de 42 van `test_onnxw_ort.py`); na het samenvoegen
  het nieuwe aantal, en `ANALYSE_DESELECTED` het aantal tests met `beeld_echt`. De telling moet
  exact blijven.

## Metingen

Alles in de sandbox, onder `taskset -c 0-2`, op één draad in het kind.

| Wat | Uitkomst | Hoe |
|---|---|---|
| YuNet op een gemaakte foto zonder gezicht (1024 × 683) | 23,3 ms per foto (als nobody 24,4 ms) | `test_models_real.py::test_de_tijd_per_foto_en_de_maxrss_van_het_kind`, de echte modellen van `models.lock`, op `63e7b56` (eerdere runs: 25,4 en 30,4 ms) |
| SFace per gezicht (uitlijnen en vector) | 24,8 ms (als nobody 24,3 ms) | idem (eerder 24,8 en 24,2 ms) |
| Foto's per minuut per core met één gezicht | 1248 (als nobody 1231; poort ≥ 250) | 60000 / (foto + gezicht); eerder 1194 en 1099 |
| maxrss van `werk` met de echte modellen | 239 MB (als nobody 238 MB; grens 1 GiB) | `ru_maxrss` van het kind, idem |
| VmData van `werk` met de nepmodellen | 102 MB | laag 2 |
| `tests_analyse` | 107 passed in 34,3 s, als root en als nobody | op `63e7b56` (op 12 november 35,2 en 35,5 s) |
| `test_install_analyse.sh` | 120 controles in 5 s | |
| `test_analyse_container.sh` tegen de stand-in | 25 regels goed in 67 s | zie Gedraaid (op 12 november 64 s) |
| De stand-in (geen echt image) | 901 MB op schijf, 213 MB inhoud | `docker image inspect` |

De groei van het echte image meet de job `images` (A0: ≤ 160 MB); X2a voegt geen pakket toe.

## Afwijkingen

1. **`OMZET_BRON` en `OMZET_UIT` met een standaard die niet bestaat**, in plaats van `${…:?}`.
   Docker Compose (v5.6.0) laat `${…:?}` ook falen voor een dienst in een profiel dat niet aan
   staat; dan faalt elke gewone run van `install.sh`. De binds gebruiken de lange vorm met
   `/nonexistent/…` en `create_host_path: false`: zonder bron start de omzetter niet ("bind source
   path does not exist"), en dat toetst `test_analyse_container.sh`.
2. **Een eigen `ophalen` voor de modellen** (`--retry 3`, `--max-time` 600 s plus 1 s per MB,
   `--max-filesize`); de bronnen van geo houden hun eigen curl van 0.8.0. Het ontwerp zei: dezelfde
   helper delen.
3. **Neerzetten met `install` in een eigen tijdelijke map en dan `mv -fT`** (een rename die geen
   symlink volgt), in plaats van `install -m 0644 -o root -g root` direct op het doel.
4. **De licenties naast de modellen** komen uit `api/app/analyse/licenties/` met hetzelfde
   neerzetten; een symlink op die plek wordt overgeslagen met een melding.
5. **De vrije ruimte alleen op `MODELLEN_ROOT`** (wat er nodig is plus 100 MB). `df` op
   `DATA_ROOT` (vectoren en cache, K2-19) is niet gebouwd: dat hoort bij de rij van X2b (Open).
6. **Een map die door anderen dan root te beschrijven is** (zonder sticky bit) weigert het script
   ook; het ontwerp noemde alleen "niet van root" en symlinks.
7. **`--force-recreate` alleen als er een model veranderde of verdween**, zoals het ontwerp zegt;
   een gewone tweede run start niets opnieuw.
8. **Een gevraagde `ANALYSE=uit` zonder regel in `.env` voegt geen regel toe**: een ontbrekende
   `ANALYSE` blijft ontbreken tot 0.8.1, zodat `.env` byte voor byte die van 0.8.0 blijft.
9. **`BEELD` komt niet in `.env`** (`merge_analyse_env`): dat is van X3 en hoort pas bij beeld.
10. **`app/cli.py`** (van A0) is aangeraakt: `_analyse_status` gebruikt `status.collect`, en de
    uitleg noemt de extra regels. Drie exacte verwachtingen in `test_analyse_monitor.py` gaan nu
    over de nieuwe, volledige uitvoer (de eerste regel bleef gelijk).
11. **Vormen die het ontwerp open liet**: `RLIMIT_FSIZE` 64 MiB in de kinderen (de memfd's van de
    uitvoer), de stop-pijp op fd 4, `Kid.ready` (het kind zegt "klaar" binnen 60 s), stderr van
    een kind alleen als lengte in het log.
12. **De kinderen in de echte container** toetst `test_analyse_container.sh` (als root op de host:
    niet-dumpbaar, `CapEff` 0, `NoNewPrivs`, nice 19, `RLIMIT_DATA`, geen sleutel; SIGKILL van
    `werk`; na 8 opdrachten een nieuw). `test_analyse_kinderen.py` blijft laag 2 in `api-analyse`:
    in X2a kan alleen `werk` in de container draaien, want elke opdracht van `zoeken` is
    `not_built`. Het ontwerp zette `test_analyse_kinderen.py` in `analyse-image`.
13. **De licenties van A0 vervangen door de bytes van opencv_zoo** op `48b10fa` (a0.md, Open 6):
    yunet met "Copyright (c) 2020 Shiqi Yu <shiqi.yu@gmail.com>" en zonder laatste regeleinde, sface
    met de APPENDIX. `test_models_lock.py` blijft groen; CI vergelijkt ze voortaan byte voor byte.
14. **`x-omzet-boot` over twee regels** in `compose.analyse.yaml` (yamllint, regels ≤ 160): een `\`
    aan het eind van een regel tussen dubbele aanhalingstekens plakt de delen zonder spatie;
    `test_compose_analyse.py` eist nog steeds gelijkheid met `runtime.OMZET_BOOT`.
15. **`test_analyse_container.sh` haalt ontbrekende modellen zelf** (curl met `--max-filesize`,
    grootte en sha256, `.part` en dan `mv`), en `analyse-image` deelt de cache van modellen met
    `api-analyse`.
16. **`e2e/test_zoek_gezichten_echt.py` staat nog niet in `analyse-image`**: die test hoort bij de
    keten van X2b (Open).
17. **`--selftest` los** (vanuit `exec`) kijkt niet naar de socket: die is van het proces dat al
    draait. De zelftest bij de start doet het wel.
18. **De test van `/proc`** (`test_proc_van_pid1_en_het_kind_is_dicht`) eiste eerst dat de map
    `/proc/<pid>` van root was. Buiten root faalde dat: de map (0555) houdt ook bij een
    niet-dumpbaar proces de uid van het proces (`task_dump_owner` in de kernel), en als root was
    de vergelijking altijd waar. Nu moeten `environ`, `mem` en `fd` van root zijn; buiten root
    blijft de controle op EACCES. Gevonden met een run als nobody (zie Gedraaid).

## Open

1. **CI na de push**: `api-analyse` en `analyse-image` hebben nog niet gedraaid. Het echte image
   bouwt alleen in CI; lokaal draaide `test_analyse_container.sh` tegen een stand-in (de echte
   Dockerfile zonder de web-stap en zonder apt).
2. **De telling na het samenvoegen met X3a** (`ANALYSE_TESTS`, `ANALYSE_DESELECTED` in `ci.yml`).
3. **X2b**: de keten in de app, `group` (ops en `test_worker_group.py`, `test_group_perf.py`),
   privacy en versleuteling, de API, de web, `/beheer`, `analyse-bench` (nu een stub) en
   `analyse-kwaliteit`, `e2e/test_zoek_gezichten_echt.py` in `analyse-image`, `df` op `DATA_ROOT`
   (K2-19), het runbook `gezichten.md` en de README over wat er bewaard wordt.
4. **X3**: `BEELD` in `.env`, de handlers van `zoeken`, `caps.beeld`, de omzetter.
5. **De kleurvolgorde** (BGR naar YuNet, RGB naar SFace) ligt vast in laag 2; welke de juiste is,
   beslist laag 4 (Vraag 8: standaard op de LXC).
6. De standaarden van de vragen blijven staan (Vraag 8 nee, 10 (a), 11 uit, 12b nee, 13 twee
   leveringen, 14 250 ms); B49–B70, Vraag 4 en Vraag 8–14 zijn niet als besloten gemarkeerd.
7. **`e2e/test_office_o2.py::test_samen_bewerken_geeft_een_versie_met_beide_teksten` was één
   keer rood** (de office-e2e met de nep-DS, terwijl ernaast de volle API-suite van X3a op
   dezelfde cores draaide): na Sluiten bleef Bram op `/bewerken/…`. Alleen draaide hij 15 keer
   groen (10 keer op 12 november, 5 keer onder het slot op 15 november, 7–13 s per keer). De
   oorzaak is een race tussen de test en de opmaak, niet iets van golf 5: `web/` en `e2e/` zijn
   gelijk aan `a284b47` (`git diff` leeg). Een meting met een plugin die vóór en na elke klik op
   Sluiten de plek van de knop op elke pagina logt: ±70 ms nadat Anna sluit, verliest Brams
   statusregel "met 1 ander" (de presence van de server), en daardoor schuift zijn Sluiten van
   x = 743,6 naar x = 824,3 px (`.editor-bar` is een flexrij; de kop met de knoppen groeit als de
   status smaller wordt). De test klikt bij Bram direct nadat Anna's pagina weg is; valt het
   bericht tussen de controle van Playwright (stabiel, raakpunt) en de klik zelf, dan landt de
   klik 80 px links van de knop, op de lege kop. De rode run past daarbij: de status was
   "Bewerken · Opgeslagen om 15:03" (fase `ready`, dus `close()` liep nooit). Onder belasting is
   dat venster groter. Herstel (niet van X2a): in de test eerst wachten tot Brams status geen
   "met 1 ander" meer zegt, of in de web de breedte van de status vastzetten zodat Sluiten niet
   verschuift.

## Klaar als

Alleen de regels van X2 (en de tests per bestand) die bij X2a horen.

| Regel uit het ontwerp | Bewijs | Uitkomst |
|---|---|---|
| Bouwt: de container `analyse` in `compose.analyse.yaml`, met de grenzen | `test_compose_analyse.py` (11 met de parameters); `test_analyse_container.sh` (docker inspect) | groen (lokaal, stand-in); `analyse-image` in CI na de push |
| Bouwt: YuNet en SFace met sha256 in `models.lock`, buiten `$DATA_ROOT` | `test_models_real.py::test_de_modellen_kloppen_met_models_lock`, `…verkeerde_sha256…`; `test_install_analyse.sh` ("analyse_dirs", "modellen_ophalen") | groen |
| Bouwt: de stap `faces` op de preview van 1024, opgevuld tot een veelvoud van 32 | `test_worker_faces.py::test_elke_maat_werkt_dankzij_het_opvullen` en de rest van het bestand (19) | groen |
| Tests: `test_analyse_monitor.py` (de standen en redenen, Defer, `analyse_problem`, off) | `tests/analyse/test_analyse_monitor.py` (31 tests, 36 met de parameters) | groen |
| Tests: `test_compose_analyse.py` (BOOT, geen sleutel in command, user, netwerk, read_only, cap_drop, pids_limit, `:ro`, grenzen apart) | `tests/test_compose_analyse.py` (11 met de parameters, ook `docker compose config` met en zonder profiel) | groen |
| Tests: `test_worker_faces.py` (laag 2) | 19 tests: maten, kaders, NMS, min_px, score, 40, kleurvolgorde, normalisatie en memfd, 16 previews, unreadable/image_bad, pixels, model_missing, anders, deadline met terminate, SIGKILL en een nieuw kind | groen |
| Tests: `test_worker_sandbox.py` (sleutel weg, dumpable 0, EACCES op `/proc/<kind>/fd`, geen sleutel in het kind, `RefusingWorker` ook bij CapEff/NoNewPrivs, 6 MP `too_big`, te veel geheugen, K1-16 `geheimwoord7`) | 16 tests; ook als nobody | groen |
| Tests: `test_analyse_kinderen.py` (geen `busy` voor een beeldvraag tijdens werk, `cancel`, werk na 8) | 12 tests (ook na 10 minuten, deadline, de verbinding sluiten, PDEATHSIG, grenzen en nice, `/opt/analyse` alleen in het kind) | groen; in `api-analyse`, niet in `analyse-image` (Afwijkingen 12) |
| Tests: `test_analyse_contract_real.py` | 13 tests | groen |
| Tests: `test_models_real.py` (laag 3: download met sha256, CPU, 0 gezichten, vormen, tijd) | 5 tests, met de echte modellen | groen |
| Tests: `test_analyse_container.sh` (uid 10003, alleen `lo`, `touch /x`, CapEff 0, NoNewPrivs, pids_limit, `/models` ro, `/data` leeg, zonder sleutel exit 2, handdruk goed en fout, `/proc/1/environ` dicht, grenzen, `healthy`) | 25 regels goed tegen de stand-in | groen (lokaal); in CI na de push |
| Tests: `test_install_analyse.sh` (symlink, `MODELLEN_ROOT`, sleutel één keer, lokale server, verkeerde sha256, `--max-filesize`, tweede run, uit, `--force-recreate`, `analyse-status`, terug naar 0.8.0 en vooruit) | 120 controles | groen |
| Klaar als: `test_analyse_container.sh` groen (… elk kind niet-dumpbaar, `docker logs` zonder merkteken) | zie hierboven | lokaal groen; in CI na de push |
| Klaar als: `install.sh` (sha256, niet fataal, symlink, `ANALYSE=uit`) | `test_install_analyse.sh` | groen |
| Klaar als: `/beheer` toont de juiste stand bij ok, down, models_missing, off | `test_analyse_monitor.py` en `state.view`; de web en de e2e zijn van X2b | het deel van X2a groen |
| Klaar als: `uv sync --frozen` zonder extra geen numpy (`test_no_numpy_in_app.py`) | in de volle suite van de job `api` (lokaal op `63e7b56`: 6268 groen) | groen (geen pakket erbij; `uv.lock` niet veranderd) |
| Klaar als: in het image vinden 10001/10002 numpy niet, 10003 wel; ≤ 160 MB | A0 (`test_analyse_image.py`, `images`); in de container: pid 1 zonder numpy, `werk` met onnxruntime | stand-in groen; in CI na de push |
| Klaar als: de lagen 1 en 2 groen | laag 2 voor `faces` (X2a); de rest van X2b | het deel van X2a groen |
| Harde poort: ≥ 250 foto's per minuut per core met één gezicht | 1248 (sandbox; als nobody 1231) | gehaald; op de runner na de push |
| Klaar als: `e2e/slim.sh` groen | `SLIM_CPUS=0-2 e2e/slim.sh`, alleen, op `63e7b56` | groen in 1048 s (1306 en 101 groen) |
| Docs: het runbook en de README (het deel van X2a) | `docs/runbooks/analyse.md`, `infra/single/README.md` | er |
| Licenties gelijk aan opencv_zoo (a0.md, Open 6) | de stap in `api-analyse`; lokaal `cmp` met de bytes van raw.githubusercontent.com | gelijk; in CI na de push |

## Gedraaid

Lokaal in de sandbox, als root en als nobody (zoals CI), naast X3a. De zware runs onder
`taskset -c 0-2`, elk alleen (onder een gedeeld slot). De code op `63e7b56`, tenzij de rij een
andere commit noemt; na `63e7b56` kwam alleen deze doc erbij.

| Wat | Hoe | Uitkomst |
|---|---|---|
| ruff | `ruff check` en `ruff format --check` in `api/` | schoon, 699 bestanden |
| de API-suite | `pytest` met de standaardselectie, op `b0245cc` | 6267 groen, 1 rood, 2 overgeslagen, 240 niet gekozen, in 2578 s. Rood was `test_error_catalog.py::test_elke_code_van_de_api_heeft_een_engelse_tekst`: die test leest de bron van `app/` en eist voor elke letterlijke `"code": "…"` een tekst in de catalogus van de web, en vond `"code": "ok"` in `ops/faces.py`. Hersteld in `63e7b56` (`Code.OK.value`, dezelfde waarde op de draad); dat bestand daarna 6 groen |
| de API-suite opnieuw | idem, op `63e7b56` | 6268 groen, 2 overgeslagen (Pebble, `provider-shapes.json`), 240 niet gekozen, in 2799 s; ook `test_no_numpy_in_app.py` en `test_error_catalog.py` |
| `tests_analyse` als root | `.venv-analyse` (met de extra `analyse`), `-m "not beeld_echt"`, de echte modellen | 107 groen in 34,3 s; laag 3 zie Metingen |
| `tests_analyse` als nobody | `setpriv` naar 65534, een venv en de modellen in `/tmp/ci-nobody` | 107 groen in 34,3 s |
| API als gewone gebruiker | `/tmp/ci-nobody/run.sh` met `tests/analyse`, `tests/test_compose_analyse.py` en `tests/test_cli_db.py` | 297 groen in 125 s |
| `e2e/slim.sh` | `SLIM_CPUS=0-2`, alleen | groen in 1048 s: zoek en triggers op SQLite 3.46.1 1306 groen, e2e `zoek` 101 groen |
| e2e, de set van CI | de regel `-m` van de job `e2e`, op `b0245cc` | 238 groen, 135 niet gekozen, in 959 s. X2a raakt `web/` en `e2e/` niet; na `b0245cc` veranderde alleen één regel in de worker, die de e2e niet laadt |
| e2e `office` | `-m office`, op `b0245cc`, naast de volle API-suite van X3a | 29 groen, 1 rood (`test_office_o2.py::test_samen_bewerken_geeft_een_versie_met_beide_teksten`); die test daarna 15 keer alleen: 15 groen. De oorzaak staat in Open 7 |
| web | `npm run build` | goed (`web/` is niet veranderd) |
| infra, statisch | `shellcheck` met de regel van CI, `bash -n`, `yamllint -s .`, `docker compose config -q` zoals de job `infra` (zonder analyse, met analyse, met het profiel `omzetten`) | schoon; drie keer goed |
| infra, de installatie | `test_install_analyse.sh`, `test_install_env.sh`, `test_install_media.sh`, `test_install_search.sh`; de scripts in `infra/` zijn sinds `48ec2fc` gelijk | 120, alle, 127 en 209 controles geslaagd |
| de container | `test_analyse_container.sh` tegen een stand-in van `63e7b56` (de echte Dockerfile zonder de web-stap en zonder apt), met de echte modellen en `ANALYSE_TEST_COMPOSE` (alleen `nofile` van `platform` lager: de sandbox staat 65536 niet toe) | 25 regels goed in 67 s (onder meer uid 10003, alleen `lo`, de zelftest, `faces` op zes previews, als root op de host pid 1 en `werk` niet-dumpbaar, SIGKILL van `werk`, na 8 opdrachten een nieuw kind, `docker logs` zonder sleutel, merkteken of `geheimwoord7`); 0 modellen gehaald; de stand-in is daarna weg |
