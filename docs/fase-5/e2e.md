# Fase 5 — de tests van media

Hoe de tests van media werken, hoe je elke marker draait (lokaal en in CI), het harnas, de
nep-servers, en wat waar bewezen wordt. Gemaakt in M0-e2e. Het ontwerp staat in
[bouwontwerp.md](bouwontwerp.md), §3.6, §3.8, §4.2, §4.3 en §4.8. Wat M0 verder bouwde, staat in
[m0.md](m0.md). De eindlijst van alle e2e van media, met wat elke test bewijst, staat onder
[Alle e2e van media](#alle-e2e-van-media-070); de uitslag bij 0.7.0 onder
[Uitslag hier](#uitslag-hier).

## In het kort

| Marker | Waar | Tegen | Job in CI | Poort |
|---|---|---|---|---|
| (geen) | `api/tests` | de nep-worker, de nep-stroom en de nep-providers; nooit echte ffmpeg nodig | `api` | hard |
| `media` | `api/tests` | de lokale worker met echte ffmpeg (6.1 van Ubuntu), op de fixtures | `api-media` | hard |
| `media` | `e2e/` | de echte app met media aan, de lokale worker, echte ffmpeg en Chromium | `e2e-media` | hard |
| `media_perf` | `e2e/` (de api-tests hebben hem niet; de metingen van de integratie zijn scripts in `api/tests/media/metingen`) | zoals `media`, met de worker op 3 cores en 40 Mbit/s en 20 ms | `e2e-media-perf` | alleen een rapport |
| `provider_real` | beide | de echte TMDB, MusicBrainz en CAA, met een eigen sleutel | geen (alleen met de hand) | — |
| (image) | `api/tests/media/image_kern.py` en de spike | het echte platform-image met ffmpeg 7.1 | `media-image` | hard |

- `api/pyproject.toml` laat `media`, `media_perf` en `provider_real` standaard weg (`addopts`);
  `-m media` kiest ze. In `e2e/` sluit de job `e2e` ze uit met zijn markerexpressie.
- Beide `pytest`-configuraties houden `--strict-markers`: een tikfout in een marker faalt.
- Een test met de nep-worker vervangt nooit een test met echte ffmpeg, en andersom. De nep houdt
  zich aan de opnames van de echte worker (`tests/media/recorded/`), en `-m media` controleert
  dat die opnames nog kloppen.

## Draaien

Alles vanuit `api/`. Voor de e2e eerst `npm run build` in `web/` (de server serveert de gebouwde
web-app). Playwright komt via `uv run --with playwright`, zoals de andere e2e. In de sandbox kiest
het harnas zelf `/opt/pw-browsers/chromium` (met `--no-proxy-server`); elders `E2E_CHROMIUM` of de
Chromium van `playwright install chromium`.

```sh
cd api

# api zonder media (zoals de job api)
uv run pytest -q

# api met de lokale worker en echte ffmpeg (zoals api-media)
taskset -c 0-2 uv run pytest -q -m media -rs

# e2e zonder media (zoals de job e2e)
uv run --with playwright pytest ../e2e -m "not perf and not media and not media_perf and not office and not office_real and not provider_real"

# e2e met media (zoals e2e-media)
uv run --with playwright pytest ../e2e -m media -rs

# het rapport van media (zoals e2e-media-perf); de JSON ook in een map met E2E_PERF_OUT
E2E_PERF_REPORT_ONLY=1 E2E_PERF_OUT=/tmp/media-perf \
  uv run --with playwright pytest ../e2e -m media_perf -rs -s

# de kern van de worker zonder Docker, op de ffmpeg van deze machine
uv run python -I tests/media/image_kern.py --ffmpeg 6.
```

ffmpeg en ffprobe moeten op het `PATH` staan. Zonder ffmpeg slaan de tests met `media` zich over
("ffmpeg ontbreekt (fixtures)" in `api/tests`, "geen ffmpeg en ffprobe" in `e2e/`). In CI telt
dat als mislukt.

De fixtures komen in `api/.pytest_cache/media/<hash>/`, op de hash van het recept. De eerste run
maakt ze (hier 30,0 s koud op 3 cores, 38 fixtures); daarna is het een cache. `fixtures.prune()`
ruimt oude mappen op.

### De omgeving

| Variabele | Wat |
|---|---|
| `MEDIA_REQUIRE_H264=1` | een test met H.264 of AAC faalt als de browser die codec niet heeft, in plaats van over te slaan. Standaard uit; in `e2e-media` en `e2e-media-perf` op `"1"`, sinds S13 zei dat Chrome for Testing H.264 en AAC kan |
| `E2E_MEDIA_WORKER` | het pad van een socket: de app start dan geen lokale worker maar verbindt met die (de worker-container van M1). Zet `PLATFORM_MEDIA_WORKER` voor de app |
| `E2E_PERF_REPORT_ONLY=1` | meten en rapporteren, nooit falen op een grens (zoals `perf`) |
| `E2E_PERF_OUT` | een map; `media_perf` schrijft daar `media-m0-s13.json` |
| `E2E_ARTIFACTS` | waar de schermafdrukken bij een fout komen (CI uploadt die map) |
| `E2E_CHROMIUM` | een eigen Chromium |

## Het harnas

`e2e/media_harness.py` en het deel "media" van `e2e/conftest.py`. Het harnas doet alleen wat de
app al kan: geen route en geen code in de app alleen voor de tests.

### De fixtures

| Fixture | Scope | Wat |
|---|---|---|
| `fake_providers` | sessie | de nep-providers (TMDB, het plaatjeshost, MusicBrainz, CAA) als eigen proces op 127.0.0.1 (`tests/media/fake_providers/process.py`), op pid te stoppen |
| `media_app` | sessie | `start_media`: een eigen `Server` met media aan en de omgeving hieronder, een beheerder, en wachten tot de socket van de worker er is. Slaat over zonder ffmpeg |
| `media` | test | de `MediaApp` voor deze test: weer gestart als een deadline hem stopte (`ensure_running`), en voor `media_perf` de worker op cores 0–2 (`pin`) |
| `ffmpeg_count` | test | de teller van regel 13 (zie hieronder) |
| `media_browser` | sessie | Chromium met `--autoplay-policy=no-user-gesture-required` |
| `media_browsers` | test | `MediaBrowsers` op de server van `media`: elke context krijgt het init-script voor het eerste beeld, en voor `media_perf` elke pagina 40 Mbit/s en 20 ms |
| `media_deadline` | test (autouse) | elke test met `media` of `media_perf` een eigen deadline |
| `pw` | sessie | één Playwright voor alle browsers van de sessie (de gewone `browser` gebruikt hem ook) |

De omgeving van de app (`media_env`):

| Variabele | Waarde |
|---|---|
| `PLATFORM_MEDIA_ENABLED` | `true` |
| `PLATFORM_MEDIA_SCAN_TICK` | `0.5` |
| `PLATFORM_MEDIA_MAX_TRANSCODES` | `3` |
| `PLATFORM_MEDIA_MAX_PROCS` | `3` |
| `PLATFORM_MEDIA_TMDB_URL`, `…_TMDB_IMAGE_URL`, `…_MUSICBRAINZ_URL`, `…_CAA_URL` | de nep-providers |
| `PLATFORM_MEDIA_PROVIDER_PRIVATE_OK` | `true` (de nep luistert op 127.0.0.1) |
| `PLATFORM_MEDIA_WORKER` | alleen met `E2E_MEDIA_WORKER` |

De lokale worker is een kind van de app, in een eigen sessie, met `--local --max-procs 3
--max-transcodes 3` (`app.media.local`).

### `MediaApp`

- `restart(outage_s=…)`: de app stoppen op pid (de worker gaat mee), zoveel seconden wachten, en
  weer starten op dezelfde poort (R06). Wat daarna nog in de boom stond, haalt hij op pid weg en
  geeft hij terug.
- `kill()`: de app met SIGKILL weg, en elke wees uit zijn boom ook (op pid en starttijd). Dat
  doet de deadline.
- `worker_pid()`, `wait_worker()`, `tree()`: de worker en de procesboom, via `/proc`.
- `pin(cpus)`: de worker en elke draad op die cores (zoals `taskset -a -p`). Een machine met
  minder cores geeft wat er is.
- `user()`, `folder()`, `upload(account, naam, map)`: een fixture van `tests/media/fixtures.py`
  via de API in een map (`PUT /files/{map}/upload`), met de sha256.
- `analyse(sha)` en `wait_probe(sha)`: de job `media.analyse` van de app in de rij `media`
  zetten, en wachten op de rij in `media_probe`. M0 heeft nog geen scanner; M2 zet die job
  straks zelf klaar. De app en zijn worker doen het werk, het harnas leest alleen.
- `cli("media-status", "--wait", "20")`: `python -m app.cli` met dezelfde omgeving als de app.

### `ffmpeg_count`

Een draad kijkt elke 0,5 s in `/proc` naar `ffmpeg` en `ffprobe` in de procesboom van de app:
de kinderen van de worker, en de previews die tot M1 nog in de app draaien. Wie hij één keer
zag, volgt hij op pid en starttijd, ook als die van ouder wisselt (een gedode worker).

- Een vierde tegelijk: de test faalt, met de regels van de processen.
- Na de test wacht hij hooguit 15 s op 0. Blijft er een over, dan stopt hij die op pid en faalt
  de test.
- Met `media_perf` faalt ook een ffmpeg van de worker buiten diens cores.

### De deadline

90 s per test met `media`, 300 s met `media_perf` (§4.2). Verloopt hij, dan:
1. een dump van alle draden (`faulthandler`, ook naar stderr);
2. de app, de worker en elke ffmpeg weg op pid;
3. de test faalt met de dump; de volgende test start de app weer.

Nooit `pkill` of `killall`: alles stopt op pid.

### De browser en de hulpjes

| Hulpje | Wat |
|---|---|
| `open_app(page)` | naar `/` en wachten tot het hoofdmenu er is: dan is het token ververst. Navigeren tijdens het verversen logt uit |
| `first_frame_ms(page)` | de tijd tot het eerste beeld, via `requestVideoFrameCallback` (het init-script hangt hem aan elke `<video>` bij `loadstart`), vanaf de navigatie of `mark_start` |
| `wait_playing(page, s)` | wachten tot de `<video>` minstens `s` seconden speelt |
| `codec_support(page, codecs)` | per codec `MediaSource.isTypeSupported` en `canPlayType` |
| `codec_supported(page, codec, how)` | `mse`, `native` of `any` |
| `require_codec(page, codec, how)` | overslaan met de reden als de browser het niet kan; met `MEDIA_REQUIRE_H264=1` faalt een codec van H.264 of AAC dan |

Een test die van de pagina weggaat en wil zien dat de speler zijn sessie sluit (de DELETE met
`keepalive` bij `pagehide`), haalt eerst zijn routes weg (`context.unroute`). Met een route zet
Playwright 1.64 de onderschepping van CDP aan voor elk verzoek van de context, ook voor wat de
route niet raakt, en een fetch met `keepalive` bij `pagehide` bleef dan soms hangen tot de context
dichtging: 13 van 130 keer met de route, 0 van 120 zonder (los nagebootst, *bij het samenvoegen
van golf 3*). Een aanroep van Playwright laat hem niet los. In `e2e/test_media_m6.py` doen
`Plays.stop` en de fixture `pages_away` dat.

## De nep-servers

| Nep | Waar | Voor |
|---|---|---|
| nep-worker (`tests/media/fake_worker.py`) | api-tests | het protocol over een echte Unix-socket, met de plaatsen, de tijdgrenzen en fouten op bestelling. Antwoordt met de opnames van de echte worker (`tests/media/recorded/`, opnieuw met `uv run python -m tests.media.record`) |
| nep-stroom (`tests/media/fake_stream.py`) | api-tests, en straks de e2e van M6 | `play` en `/api/v1/stream/…` in de vorm van §3.4, uit vooraf gemaakte segmenten |
| nep-providers (`tests/media/fake_providers/`) | api-tests (ASGI) en e2e (als proces) | TMDB, plaatjes, MusicBrainz en CAA, met fouten op bestelling en een logboek |

De e2e gebruiken nu de nep-providers (de app krijgt hun adressen). De nep-worker en de
nep-stroom zijn voor de api-tests; in de e2e draait altijd de echte lokale worker.

## Wat waar bewezen wordt

### `e2e/test_media_m0.py` (de rooktest van M0)

| Test | Marker | Wat het bewijst |
|---|---|---|
| `test_de_app_start_met_de_lokale_worker` | `media` | de worker is een kind van de app met `--local --max-procs 3 --max-transcodes 3`; `media-status --wait 20` geeft 0 en `Media: aan (lokaal, zonder afscherming, ffmpeg <versie>)`; `/api/v1/admin/media` is nog de stub (503 `media_off`) |
| `test_een_upload_wordt_geprobed` | `media` | `vp9_opus.mkv` via de API geüpload en door de job van de app en de worker geprobed: `ready`, matroska, 6 s, VP9 640×360 en twee Opus-sporen (nld, eng); `broken.mp4` geeft `failed` met `unreadable` |
| `test_de_viewer_speelt_vp9` | `media` | de viewer van fase 3 speelt `vp9_opus.webm` met media aan: een eerste beeld, 0,5 s spelen, 640×360, geen melding "kan dit formaat niet afspelen" |
| `test_de_viewer_speelt_h264` | `media` | idem voor `h264_aac.mp4`, als de browser H.264 en AAC heeft; anders overgeslagen met de reden (Chromium 141 van Playwright heeft ze niet) |
| `test_herstart_met_een_onderbreking` | `media` | `restart(outage_s=1)`: de oude worker is weg, er is een nieuwe, dezelfde poort, `media-status` weer 0, niets bleef over |
| `test_alles_op_pid_weg_en_weer_starten` | `media` | `kill()` haalt de app en de wees-worker weg op pid; `ensure_running` brengt alles terug |
| `test_ffmpeg_count_ziet_een_vierde_en_een_wees` | `media` | de teller zelf, met vier slapers die `ffmpeg` heten: hij ziet er vier (en faalt dan), volgt ze als hun ouder stopt, en stopt ze op pid |
| `test_de_deadline_dumpt_de_draden` | `media` | de deadline zelf (0,2 s): een dump van alle draden, dan stoppen |
| `test_s13_de_browser_en_het_eerste_beeld` | `media_perf` | S13: de versie van de browser, `isTypeSupported` en `canPlayType` voor `avc1.640028`, `mp4a.40.2` en de twee samen; drie keer het eerste beeld van VP9 in de viewer onder 40 Mbit/s en 20 ms, met de worker op cores 0–2. Een rapport (`media-m0-s13.json`); hard is alleen dat de meting liep |

### De api-tests met `media`

63 tests: `test_media_worker_core.py` (9), `test_media_probe.py` (39: elke fixture tegen een
tabel, nette fouten, een fuzz), `test_fake_worker.py` (14: het contract tegen de echte lokale
worker, en of de opnames nog kloppen) en `test_media_off.py` (1: media aan). De scenario's met
`stream` slaan tegen de echte worker over tot M4 ("de echte worker doet stream nog niet (M4)");
dat mag in `api-media`. `test_fake_stream.py` en `test_media_logs.py` maken ook fixtures met
ffmpeg, maar draaien zonder marker in de job `api` (die heeft ffmpeg voor de previews).

### `media-image`

`api/tests/media/image_kern.py`, alleen-lezen in het image gezet (`/srv/tests`), zonder netwerk
en capabilities, onder `--cpus 3`:
1. de fixtures gemaakt in het image (in `/tmp`, één ffmpeg tegelijk);
2. de lokale worker van M0 met `PLATFORM_MEDIA_MAX_PROCS` 3;
3. caps: `PROTOCOL`, ffmpeg en ffprobe 7.x, niets ontbreekt, lavfi en de witte lijst van demuxers,
   lokaal en zonder afscherming;
4. probe van `h264_aac.mp4`, `vp9_opus.mkv`, `hevc10_hdr10.mkv` en `interlaced.ts`: `ready`, met de
   container en de sporen van de opname; `broken.mp4` en `random.mkv` geven hun nette fout;
5. na het stoppen geen ffmpeg of ffprobe meer;
6. met `--alles` elke opname tegen een verse probe, alleen als rapport (de opnames zijn van
   6.1.1; een verschil op 7.1 staat in `kern.json` onder `opnames`).

Daarna de worker in zijn container: `infra/single/tests/test_media_container.sh` (M1) met
`--selftest`, de afscherming van Keuze 11 en 47, en sinds M4 de stroom: S3 punt 17 van de spike
(een remux in de worker van een fd van de app naar een pipe van de app), een transcode op het
rooster van 2 s, en het deel van R12 dat zonder installatie kan (tijdens die transcode één ffmpeg
in de container `media` en geen in die van de app, met `docker top`). Elke controle is hard.

*Bij het samenvoegen van golf 2:* tot M4 draaide hier nog S3 van de spike
(`api/tests/media/spike/ci.py --stappen S3`, met een afgeleid image). Die stappen zijn weg sinds
M4 (een M0-hotfix van M4 in `ci.yml`, [m4.md](m4.md)): wat S3 nog toetste, doet het script nu
tegen het echte image. De job `media-spike` is weg sinds de tag `fase5-basis` (§3.7).

## De jobs in CI

| Job | Wat | Faalt als |
|---|---|---|
| `api` | alle api-tests zonder `media`, `media_perf` en `provider_real` | een test faalt |
| `api-media` | ffmpeg van Ubuntu, de fixtures koud maken (met de tijd in het log), dan `pytest -m media -rs`, alles onder `taskset -c 0-2`; `timeout-minutes: 20` | een test faalt, of slaat over omdat ffmpeg ontbreekt. Duurt het maken van de fixtures langer dan 30 s (§3.8), dan een waarschuwing, geen fout |
| `e2e` | de e2e van fase 3 en 4, zonder media | een test faalt |
| `e2e-media` | de web-app bouwen, Chrome for Testing (`playwright install --with-deps chromium`, zoals `e2e`), ffmpeg, dan `pytest ../e2e -m media -rs`; `timeout-minutes: 20`, `MEDIA_REQUIRE_H264` `"1"` | een test faalt (ook een H.264-test die de codec mist), of alles slaat over omdat ffmpeg ontbreekt |
| `e2e-media-perf` | zoals `e2e-media` (ook `MEDIA_REQUIRE_H264` `"1"`), dan `-m media_perf`; toont de browser en S13, en zet één regel in de samenvatting van de run; artefact `media-prestaties` | nooit (`continue-on-error`, `E2E_PERF_REPORT_ONLY=1`) |
| `media-image` | het platform-image (de cache van `images`), de kern in het image, dan de worker in zijn container (`test_media_container.sh` met `--selftest`, M1; S3 punt 17 en het deel van R12, M4); artefact `media-image` met `kern.json` en `selftest.json` | een controle van de kern of van de container faalt |

Geen nieuwe action: alles met `actions/checkout`, `setup-uv`, `setup-node`, `setup-buildx`,
`build-push-action` en `upload-artifact`, zoals de andere jobs. `yamllint -s .` (job `infra`)
controleert de workflow.

**S13 en `MEDIA_REQUIRE_H264`.** `e2e-media-perf` zet de regel "S13: <versie>, H.264 en AAC via
MSE: ja/nee" in de samenvatting, en `media-m0-s13.json` in het artefact. De eerste run (op
`e35445c`) zei "S13: 156.0.8078.4, H.264 en AAC via MSE: ja", en `e2e-media` speelde H.264 goed
(8 goed, geen overgeslagen). Dus staat `MEDIA_REQUIRE_H264` nu op `"1"` in `e2e-media` (review K2;
`api/tests/test_media_ci.py` houdt dat zo). Dan faalt `test_de_viewer_speelt_h264` als de browser
H.264 of AAC kwijt is. Zegt een latere run "nee" (een nieuwe Chrome for Testing zonder de codecs),
zet de vlag dan niet stil terug: dat is een vondst voor §6 van het bouwontwerp.

## Wat de pakketten toevoegen

- **M1:** de worker-container. `media-image` krijgt `python -m app.media.worker --selftest` op 7.1
  en `infra/single/tests/test_media_container.sh` (de afscherming van Keuze 11); die nemen S3
  over. De e2e kunnen dan tegen de container met `E2E_MEDIA_WORKER`. Daarna draaien de previews
  in de worker, en telt `ffmpeg_count` alleen nog daar.
  *Bij het samenvoegen van golf 1:* zo gebouwd, behalve dat S3 blijft tot M4 (de remux op een
  pipe van de app heeft `stream` van M4 nodig; [m1.md](m1.md), Open). `e2e/test_media_m1.py`
  bewijst met een spion op `/proc` dat elke ffmpeg onder de worker draait.
  *Bij het samenvoegen van golf 2:* M4 nam S3 over in `test_media_container.sh` (punt 17, met
  `stream`) en haalde de stappen van de spike uit `media-image`.
- **M2:** de scanner zet `media.analyse` zelf klaar; `MediaApp.analyse` is dan alleen nog voor
  een test die het meteen wil.
  *Bij het samenvoegen van golf 3:* zo gebouwd. `e2e/test_media_m2.py` maakt bibliotheken met het
  toetsenbord en de import met de CLI, en speelt na Afspelen en "Verder op 0:03" echt af in de
  speler van M6. De e2e van M5 en M6 maken hun items met `POST /api/v1/media/libraries` en een
  echte scan (`library_item` in `test_media_m5.py`, `scanned` in `test_media_m6.py`).
- **M3–M6:** play, de stromen en de speler, elk met een eigen `e2e/test_media_m<N>.py`. M6 zet
  de tijden Ma en Mb in `media_perf`. *Bij het samenvoegen van golf 1:* `e2e/test_media_m3.py`
  speelt direct via een echte sessie (de meting in de browser, intrekken, `replaced`, een
  publieke link; H.264 alleen in CI). De e2e van M6 schakelt pas van `fake_stream` naar de echte
  routes als ook M4 er is (§5).
  *Review golf 1 (K5):* #10 en #11 van de tabel staan er nu ook in: een deling weg terwijl een
  ander kijkt (een lezing van `/file` die loopt, breekt af na hooguit 15 s en één stuk),
  uitloggen midden in een `/file`-stream (meteen), en de 403 `media_public_transcode` van een
  publieke link, gevraagd vanuit de publieke pagina. De lezing die loopt, doet de test met een
  eigen socket (een kleine `SO_RCVBUF`), niet met `fetch` in de browser: Chromium las de body
  van een `fetch` helemaal van de socket, ook toen de pagina na 1,2 MB stopte met lezen (`ss`
  gaf lege rijen, de server was al klaar). Wat de `<video>` doet met wat hij al had na het
  afbreken, is van de speler (M6). Remux en transcode via een link (#10) wachten op M4.
  *Bij het samenvoegen van golf 2 (M4 en M5):* `e2e/test_media_m4.py` speelt remux en transcode
  via MediaSource tegen de echte routes (ook via een publieke link, #10), en heeft nu ook #12
  zonder de speler (de worker gedood tijdens een transcode: binnen 10 s weer een segment, geen
  ffmpeg over). `e2e/test_media_m5.py` heeft #5 (PGS en VobSub ingebrand door de producer,
  gemeten in een canvas, ook na een sprong: S9) en het ondertiteldeel van #4 (de SRT en ASS van
  `hevc10_hdr10.mkv` als `<track>` op de stroom van een transcode, op de tijdlijn van die
  stroom). De speler zelf (de rest van #2–#4) is van M6.
  *Bij het samenvoegen van golf 3 (M2 en M6):* `e2e/test_media_m6.py` speelt de speler tegen de
  echte routes van M3, M4 en M5, en sinds golf 3 ook tegen de echte bibliotheek van M2 (de
  stand-in in de browser is weg). `e2e/test_media_m6_perf.py` zet Ma, Mb en Mc in `media_perf`.
- **M7 en M9** (golf 4): `e2e/test_media_m7.py` speelt met twee contexts van dezelfde gebruiker
  (A speelt en pauzeert, B ziet het binnen 2 s; de p95 van 20 keer is "Klaar als"), en de rijen
  Verder kijken en Volgende aflevering met het toetsenbord. `e2e/test_media_m2.py` zet "Verder op
  0:03" sindsdien met de echte `PUT …/progress` in plaats van een rij in `playback_state`.
  `e2e/test_media_m9.py` meet gapless met een AudioWorklet (FLAC en Opus geëist, MP3 en AAC
  vastgelegd) en speelt albums, artiesten, de muziekbalk en afspeellijsten met het toetsenbord.
  Het worklet staat tijdens de test in `web/dist/__e2e-m9/` (page.route ziet geen worklet-module;
  [m9.md](m9.md), afwijking 1). *Bij het samenvoegen van golf 4:* beide op de stand met de
  springer van M6 (CI-run 41002597837) samen groen; zie [m7.md](m7.md) en [m9.md](m9.md).
- **M10:** in `media-image` het bewijs van R12 (installeren vanaf de commit van 0.6.0, bijwerken
  naar deze versie, een transcode via curl en dan `docker top`: geen ffmpeg in de app, één in
  `media`) en de rooktest van de belastingtest; `/api/v1/admin/media` en de rest van
  `media-status`.
- **M8 en M10** (golf 5): `e2e/test_media_m8.py` laat 20 films met scene-namen koppelen tegen de
  nep-providers (als proces, de fixture `fake_providers`), met Nakijken en Koppelen met het
  toetsenbord. `e2e/test_media_m10.py` loopt het tabblad Media op `/beheer` af (het toetsenbord,
  een sessie stoppen, een import), en `e2e/test_media_m2.py` start de import sindsdien via
  `/beheer` in plaats van met de CLI. *Bij het samenvoegen van golf 5:* de fixture `tmdb_key` van
  de e2e van M8 zet de sleutel via het veld op `/beheer` → Media (eerst fout, dan goed, met de
  bronvermelding van TMDB), niet meer met een hulpproces.

## Alle e2e van media (0.7.0)

De eindlijst bij de integratie (stap 4 van §5): 68 tests met `media` en 6 met `media_perf`, in
13 bestanden onder `e2e/`. Alle tests met `media` draaien tegen de echte app met media aan, de
lokale worker, echte ffmpeg en Chromium. Daarbovenop laat de bewaking van `conftest.py` elke
test met een browser falen op een schending van de CSP, een gelogde of onafgevangen fout, of een
ontbrekend gebied van i18n, en `ffmpeg_count` laat hem falen bij een vierde ffmpeg of een die
overblijft. Vier tests met H.264 slaan in de sandbox over (Chromium 141 heeft geen
H.264 en AAC) en moeten in CI spelen (`MEDIA_REQUIRE_H264=1`); ze staan hieronder met "(H.264)".
De `provider_real`-tests zijn api-tests (`api/tests`), geen e2e.

### `test_media_m0.py`: het harnas (8 + 1)

| Test | Wat het bewijst |
|---|---|
| `test_de_app_start_met_de_lokale_worker` | de worker is een kind van de app met de grenzen van het harnas; `media-status` ziet zijn caps, en `/beheer` → Media ziet de lokale worker zonder afscherming |
| `test_een_upload_wordt_geprobed` | een upload wordt door de job van de app en de worker geprobed (VP9, twee Opus-sporen); een kapot bestand geeft `failed` met `unreadable` |
| `test_de_viewer_speelt_vp9` | de viewer speelt VP9 + Opus met media aan |
| `test_de_viewer_speelt_h264` | idem voor H.264 + AAC (H.264) |
| `test_herstart_met_een_onderbreking` | de app weg en terug op dezelfde poort met een nieuwe worker; niets blijft over |
| `test_alles_op_pid_weg_en_weer_starten` | de deadline haalt de app en de wees-worker op pid weg, en de volgende test start ze weer |
| `test_ffmpeg_count_ziet_een_vierde_en_een_wees` | de teller zelf: hij ziet een vierde ffmpeg en een wees, en stopt ze op pid |
| `test_de_deadline_dumpt_de_draden` | de deadline dumpt alle draden en stopt dan |
| `test_s13_de_browser_en_het_eerste_beeld` (`media_perf`) | S13: wat de browser zegt over H.264 en AAC, en het eerste beeld van VP9 in de viewer onder 40 Mbit/s en 20 ms (een rapport) |

### `test_media_m1.py`: de worker in isolatie (3)

| Test | Wat het bewijst |
|---|---|
| `test_de_preview_van_een_video_komt_van_de_worker` | elke ffmpeg en ffprobe voor een preview is een nakomeling van de worker, nooit van de app of het renderproces |
| `test_de_worker_weg_en_terug` | kill -9 op de worker: de app start hem na 1 s opnieuw, en een nieuwe video krijgt weer een preview |
| `test_met_media_uit_krijgt_een_video_een_preview_zoals_in_0_6_0` | met media uit start het renderproces een worker voor één keer, en daarna blijft er geen over |

### `test_media_m2.py`: bibliotheken, de scanner en de import (3)

| Test | Wat het bewijst |
|---|---|
| `test_bibliotheek_maken_en_kijken_met_het_toetsenbord` | een map wordt een bibliotheek met het toetsenbord; de titels komen uit de namen, een poster.jpg wordt de poster; Afspelen en "Verder op 0:03" spelen in de speler op `/kijken`; een upload daarna verschijnt vanzelf |
| `test_series_de_start_en_opnieuw_scannen` | een bibliotheek met series: Net toegevoegd, de serie met seizoen en afleveringen, Afspelen S01E01 en Opnieuw scannen |
| `test_import_uit_data_import_via_beheer` | Nieuwe bibliotheek op `/media`, dan een import uit `/data/import` via `/beheer` met de mappenkiezer en Kopiëren: de items in de bibliotheek, de bron blijft, de taak eindigt als `done` |

### `test_media_m3.py`: direct play via een sessie (8)

| Test | Wat het bewijst |
|---|---|
| `test_vp9_speelt_via_de_sessie_en_stopt_na_de_prullenbak` | de codecs gemeten in de browser, play doet zelf de probe, direct play; na de prullenbak 403 `revoked` |
| `test_uitloggen_stopt_alleen_die_login` | uitloggen stopt de sessie van die login; een andere login speelt door |
| `test_een_nieuwe_versie_geeft_replaced` | een nieuwe versie van het bestand: 410 `replaced` |
| `test_een_publieke_link_speelt_zonder_inloggen` | een publieke link speelt zonder in te loggen; de link weg: binnen 15 s 403 |
| `test_een_deling_intrekken_breekt_een_lopende_lezing_af` | een deling weg terwijl een ander kijkt: een lopende lezing van `/file` breekt na hooguit 15 s af |
| `test_uitloggen_breekt_een_lopende_lezing_meteen_af` | uitloggen breekt een lopende lezing meteen af |
| `test_publiek_omzetten_geeft_403_ook_in_de_browser` | een bron die omzetten nodig heeft, via een publieke link: 403 `media_public_transcode` |
| `test_h264_speelt_via_de_sessie_en_stopt_na_blokkeren` | H.264 + AAC direct, en na blokkeren 403 `blocked` (H.264) |

### `test_media_m4.py`: remux en transcode via de stroom (6)

| Test | Wat het bewijst |
|---|---|
| `test_een_mkv_buiten_een_bibliotheek_speelt_als_remux` | zonder bibliotheek en zonder opgeslagen probe: play doet de probe en de keyframe-index, en kiest remux |
| `test_een_publieke_link_speelt_remux_en_transcode_niet` | via een publieke link: remux speelt, omzetten geeft 403, de link weg: binnen 15 s 403 `revoked` |
| `test_publiek_omzetten_als_het_aan_staat` | met `public_transcode` aan: hooguit 720 en één per link; een tweede kijker krijgt 503 `media_busy` met Retry-After |
| `test_een_transcode_speelt_op_het_rooster` | een transcode op het rooster van 2 s, elk segment met een keyframe vooraan (VP9 hier, H.264 waar de browser het kan) |
| `test_h264_uit_hevc_hdr_via_mse` | HEVC 10-bit HDR10 naar H.264 + AAC met tone-mapping via MSE (H.264) |
| `test_de_worker_gedood_tijdens_een_transcode` | §4.3 #12 zonder speler: de worker gedood, zijn ffmpeg gaat mee, een nieuw segment binnen 10 s, nooit meer dan 3 ffmpeg |

### `test_media_m4_perf.py` (`media_perf`, 1)

`test_m4_de_start_van_een_stroom`: de virtuele speler zonder browser, van play tot en met
segment 0: alleen, naast 2 andere transcodes, na een sprong naar 70 %, en remux (een rapport;
de getallen in [metingen.md](metingen.md), Mb).

### `test_media_m5.py`: ondertitels, sprites en hoofdstukken (5)

| Test | Wat het bewijst |
|---|---|
| `test_ondertitels_als_track_en_ass_bovenaan` | een ingebed WebVTT-spoor, een losse SRT (CP1252) en ASS als `<track>` op de goede tijd; de ASS-cue bovenaan, de SRT onderaan; hoofdstukken en het begin van de aftiteling |
| `test_sprites_via_de_sessie_en_403_na_de_prullenbak` | de analyse maakt sprites, elk adres uit `trick.vtt` laadt via de sessie, de cue wijst het goede vak aan; na de prullenbak 403 |
| `test_beeldondertitel_ingebrand_via_de_producer[pgs.mkv]` en `[vobsub.mkv]` | PGS en VobSub worden ingebrand, gemeten in de pixels, ook na een sprong (S9) |
| `test_ondertitels_op_de_tijd_van_een_transcode` | de SRT en ASS van een HDR-transcode als `<track>` op de tijdlijn van de stroom |

### `test_media_m6.py`: de speler (21)

| Test | Wat het bewijst |
|---|---|
| `test_direct_het_profiel_het_eerste_beeld_en_zoeken` | de meting van [clientprofiel.md](clientprofiel.md), het eerste beeld, zoeken met de toets 5 |
| `test_remux_zoeken_en_audiospoor_a_b_a` | zoeken bij remux, en audiospoor A, B, A op dezelfde positie |
| `test_een_sprong_voorbij_de_cache_start_een_nieuwe_producent` | een sprong naar 90 %: een nieuwe ffmpeg met `-ss` |
| `test_transcode_hdr_naar_720_met_ondertitels` | de trede 720, de redenen in Info, de ondertitels als `<track>` |
| `test_transcode_naar_h264_in_ci` | een transcode naar H.264 in de speler (H.264) |
| `test_een_ander_audiospoor_bij_een_transcode` | een ander audiospoor bij een transcode: de positie blijft, en de regel voor geforceerde ondertitels |
| `test_kwaliteit_720_naar_480` | een andere kwaliteit via het menu: een nieuwe sessie, de positie blijft, het beeld 480 hoog |
| `test_de_kaart_volgende_aflevering` | de kaart "Volgende aflevering" bij de aftiteling, Esc annuleert, dan vanzelf door; met een echte serie |
| `test_cd1_gaat_vanzelf_naar_cd2` | een film in twee delen (cd1, cd2) is één item; na deel 1 meteen deel 2, zonder de kaart |
| `test_pip_vraagt_beeld_in_beeld` | P vraagt beeld-in-beeld |
| `test_alleen_met_het_toetsenbord` | de lijst van [toetsenbord-m6.md](toetsenbord-m6.md), alle menu's |
| `test_de_app_herstart_midden_in_het_afspelen` | de app herstart: een nieuwe sessie op de positie (±3 s), weer spelen binnen 10 s |
| `test_de_app_dertig_seconden_weg` | idem met de app 30 s weg |
| `test_de_worker_gedood_tijdens_een_transcode` | §4.3 #12 met de speler: hij loopt binnen 10 s na de naad weer, zonder fout |
| `test_de_worker_gedood_met_een_gat_in_het_geluid` | idem met een gat in het geluid van de nieuwe run: de speler springt erover |
| `test_sprites_bij_hover_en_hoofdstukken` | de sprite en het hoofdstuk boven de tijdbalk, Page Down |
| `test_de_speler_in_de_viewer_en_de_fallback` | de speler in de viewer van Bestanden (Esc sluit, de sessie dicht), en de terugval |
| `test_de_viewer_valt_terug_als_play_nee_zegt` | zegt play nee, dan de `<video>` van fase 3 |
| `test_weg_van_de_pagina_sluit_de_sessie` | weg van de pagina: de sessie meteen dicht (binnen 5 s 410) |
| `test_403_de_speler_stopt_en_zegt_waarom` | de prullenbak tijdens het spelen: de speler stopt, zegt waarom, en vraagt geen nieuwe sessie |
| `test_na_twee_haperingen_een_trede_lager` | §4.3 #19: na twee keer haperen zelf een trede lager, met een melding |

### `test_media_m6_perf.py` (`media_perf`, 4)

`test_m6_ma_eerste_beeld_direct`, `test_m6_ma_eerste_beeld_direct_h264` (H.264),
`test_m6_mb_eerste_beeld_transcode` en `test_m6_mc_zoeken_en_weer_lopen`: Ma, Mb en Mc van
"Klaar als" in de browser, elk 10 keer onder 40 Mbit/s en 20 ms (een rapport; de getallen in
[metingen.md](metingen.md)).

### `test_media_m7.py`: de kijkvoortgang (3)

| Test | Wat het bewijst |
|---|---|
| `test_twee_tabbladen_verder_kijken_binnen_2_s` | twee tabbladen van één gebruiker: de stand staat binnen 2 s bij het andere, en de p95 van 20 keer (Klaar als) |
| `test_verder_kijken_met_het_toetsenbord` | de rij Verder kijken met het toetsenbord; een stand van een ander apparaat zet de kaart vooraan |
| `test_volgende_aflevering_en_gezien_markeren` | een aflevering uitgekeken: de volgende binnen 2 s klaar; Gezien markeren schuift door |

### `test_media_m8.py`: metadata online (1)

`test_twintig_films_vanzelf_en_met_de_hand`: de sleutel van TMDB via `/beheer` (eerst fout, dan
goed); van 20 films met scene-namen ≥ 18 vanzelf goed tegen de nep-providers, de rest onder
Nakijken; Koppelen na een 429 met Retry-After, en Bewerken met het toetsenbord; de sleutel gaat
alleen in de header naar TMDB, en staat niet in de log, de database, de pagina's of de API.

### `test_media_m9.py`: muziek en gapless (6)

| Test | Wat het bewijst |
|---|---|
| `test_gapless_de_fase_springt_niet[flac-flac]` en `[opus-opus]` | gapless (Klaar als): op elke overgang springt de fase van de sinus hooguit 1 sample, zonder gat |
| `test_gapless_mp3_en_aac_vastgelegd[mp3-mp3]` en `[aac-m4a]` | dezelfde meting voor MP3 en AAC, alleen vastgelegd, niet geëist |
| `test_albums_artiesten_en_de_muziekbalk_met_het_toetsenbord` | albums, artiesten en de muziekbalk met het toetsenbord; de muziek speelt door bij navigeren |
| `test_afspeellijsten_met_het_toetsenbord` | een afspeellijst maken, een album erbij, de volgorde, Delete, hernoemen en verwijderen |

### `test_media_m10.py`: `/beheer` → Media (4)

| Test | Wat het bewijst |
|---|---|
| `test_het_tabblad_met_het_toetsenbord` | Alt+8, de blokken, Opruimen met Esc en met bevestigen; de belastingtest zegt hoe hij aan gaat |
| `test_een_sessie_stoppen` | de beheerder stopt een sessie; de speler zegt waarom en vraagt geen nieuwe; daarna geen ffmpeg meer |
| `test_een_import_starten` | Verplaatsen naar een andere eigenaar; een bestand dat nog geschreven wordt, blijft staan ("in gebruik") |
| `test_de_worker_zonder_ffmpeg` | §4.3 #13: zonder ffmpeg de melding op `/beheer`, direct play werkt, en een HEVC-film geeft Downloaden (Keuze 46) |

## Uitslag hier

In de sandbox (Chromium 141.0.7390.37, ffmpeg 6.1.1, 4 cores):

- `pytest -m media` in `api/` onder `taskset -c 0-2`: 60 goed, 3 overgeslagen (stream, M4), in
  37 s.
- `pytest ../e2e -m media`: 7 goed, 1 overgeslagen (H.264: Chromium 141 heeft geen `avc1`), in
  28 s; eerder vier keer na elkaar groen.
- `pytest ../e2e -m media_perf`: 1 goed in 12 s. S13 in Chromium 141: H.264 en AAC via MSE nee;
  het eerste beeld van VP9 in de viewer 1326, 1139 en 822 ms onder 40 Mbit/s en 20 ms, de
  worker op cores 0–2, hooguit 1 ffmpeg tegelijk.
- De e2e van de job `e2e` zonder media: 109 goed; `-m office`: 30 goed.
- `image_kern.py --alles --ffmpeg 6.`: 12 van 12 controles, 41 van 41 opnames gelijk, 28,6 s.
- De jobs met het image (`media-image`) en Chrome for Testing (`e2e-media`, `e2e-media-perf`)
  draaien alleen in CI. Hun eerste uitslag staat hieronder.

In CI, de eerste run (run 40787463797 op `e35445c`, 28 oktober 2026; alle 15 jobs groen):

- `api-media`: 60 goed, 3 overgeslagen (stream, M4).
- `e2e-media`: 8 goed, 165 niet gekozen, geen overgeslagen: ook `test_de_viewer_speelt_h264`
  speelde, in Chrome for Testing 156.0.8078.4.
- `e2e-media-perf`: "S13: 156.0.8078.4, H.264 en AAC via MSE: ja"; het eerste beeld van VP9 in de
  viewer 1270,9, 835,5 en 794,6 ms. Daarna staat `MEDIA_REQUIRE_H264` op `"1"` (review K2).
- `api-media`: de 38 fixtures in 25,7 s (onder de 30 s van §3.8).
- `media-image`: de kern op ffmpeg 7.1.5: 12 van 12 controles, 41 van 41 opnames gelijk aan een
  verse probe, in 26,6 s; S3 17 van 17 controles goed.
