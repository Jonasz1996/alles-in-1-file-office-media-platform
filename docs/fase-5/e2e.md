# Fase 5 — de tests van media

Hoe de tests van media werken, hoe je elke marker draait (lokaal en in CI), het harnas, de
nep-servers, en wat waar bewezen wordt. Gemaakt in M0-e2e. Het ontwerp staat in
[bouwontwerp.md](bouwontwerp.md), §3.6, §3.8, §4.2, §4.3 en §4.8. Wat M0 verder bouwde, staat in
[m0.md](m0.md).

## In het kort

| Marker | Waar | Tegen | Job in CI | Poort |
|---|---|---|---|---|
| (geen) | `api/tests` | de nep-worker, de nep-stroom en de nep-providers; nooit echte ffmpeg nodig | `api` | hard |
| `media` | `api/tests` | de lokale worker met echte ffmpeg (6.1 van Ubuntu), op de fixtures | `api-media` | hard |
| `media` | `e2e/` | de echte app met media aan, de lokale worker, echte ffmpeg en Chromium | `e2e-media` | hard |
| `media_perf` | `e2e/` (en later `api/tests`) | zoals `media`, met de worker op 3 cores en 40 Mbit/s en 20 ms | `e2e-media-perf` | alleen een rapport |
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
| `MEDIA_REQUIRE_H264=1` | een test met H.264 of AAC faalt als de browser die codec niet heeft, in plaats van over te slaan. Standaard uit; in `e2e-media` op `"1"`, sinds S13 zei dat Chrome for Testing H.264 en AAC kan |
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
| `e2e-media-perf` | zoals `e2e-media`, dan `-m media_perf`; toont de browser en S13, en zet één regel in de samenvatting van de run; artefact `media-prestaties` | nooit (`continue-on-error`, `E2E_PERF_REPORT_ONLY=1`) |
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
- **M10:** in `media-image` het bewijs van R12 (installeren vanaf de commit van 0.6.0, bijwerken
  naar deze versie, een transcode via curl en dan `docker top`: geen ffmpeg in de app, één in
  `media`) en de rooktest van de belastingtest; `/api/v1/admin/media` en de rest van
  `media-status`.

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
