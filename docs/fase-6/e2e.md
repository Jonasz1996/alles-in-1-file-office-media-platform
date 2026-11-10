# Fase 6: de tests van zoeken

Hoe de tests van zoeken en foto's werken, hoe je elke marker draait (lokaal en in CI), welke
meetscripts er zijn, en welke job in CI hard is en welke alleen een rapport geeft. Gemaakt in I1.
Het ontwerp staat in [bouwontwerp.md](bouwontwerp.md), §4.0 tot en met §4.6. Het harnas, de
fixtures, de wereld en `e2e/slim.sh` kwamen met Z0-e2e; de uitleg daarvan staat in
[z0-e2e.md](z0-e2e.md). Hoe de tests van media werken, staat in [fase 5](../fase-5/e2e.md). De
uitslag van I1 komt onder [Uitslag I1](#uitslag-i1); de integratie zelf in
[integratie.md](integratie.md) en de getallen in [metingen.md](metingen.md).

## In het kort

| Marker | Waar | Tegen | Job in CI | Poort |
|---|---|---|---|---|
| (geen), `zoek`, `triggers` | `api/tests` | de app in het proces, met de nep-worker. `zoek` staat op de tests van zoeken en foto's: in `tests/search` zet `conftest.py` hem op elke test, elders staat hij per bestand. `triggers` zet dezelfde `conftest.py` op de bestanden over de triggers, de migratie 0010 en de churn | `api`; `triggers` ook in `zoek-perf`, op 3.46.1 | hard |
| `geo` | `api/tests/geo` | de echte bronnen van plaatsen en landen, gehaald met `tools/geo/haal.py` (met sha256) | `api` (een eigen stap) | hard |
| `zoek_perf` | `api/tests/search`, `api/tests/photos` | `python:3.12-slim` (SQLite 3.46.1, zoals het image), op 3 cores | `zoek-perf` | hard (de p95), rapport (de p99, de max) |
| `ocr` | `api/tests` en `e2e/` | de echte tesseract van Ubuntu met nld, eng en osd, en DejaVu Sans | `ocr` | hard |
| `zoek` | `e2e/` | de echte app; in Chromium met alleen het toetsenbord (`test_zoek_z2.py` alleen via de API) | `e2e` | hard |
| (meetscript) | `e2e/perf/zoek_ui.py` | Chromium met het profiel van `first_view.py` | `perf` | alleen een rapport |
| (meetscript) | `e2e/perf/zoek_samen.py --kort` en `e2e/perf/zoek_backfill.py` | de app met media aan, de lokale worker en echte ffmpeg | `e2e-media-perf` | alleen een rapport |

- `api/pyproject.toml` laat `media`, `media_perf`, `provider_real`, `zoek_perf`, `ocr` en `geo`
  standaard weg (`addopts`). `zoek` en `triggers` draaien gewoon mee; ze zijn er om te kiezen,
  niet om over te slaan.
- `e2e/pytest.ini` kent `zoek` en `ocr`. De job `e2e` sluit `ocr` uit met zijn markerexpressie;
  `zoek` draait daar mee.
- Beide `pytest`-configuraties houden `--strict-markers`: een tikfout in een marker faalt.
- Een test met de nep-worker vervangt nooit een test met de echte tesseract, en andersom: de
  marker `ocr` draait alleen waar tesseract er echt is, en overslaan telt in de job `ocr` als
  mislukt.

## De regel van C17

> **De p95 ≤ 200 ms blijft altijd hard in CI** (C17).

Dit is de regel van §4.4 van het ontwerp. De poort van zoeken over 100.000 bestanden
(`api/tests/search/test_perf_100k.py`, met `e2e/perf/zoek_100k.py` als client, in de job
`zoek-perf`) wordt nooit een rapport, wordt niet ruimer en wordt niet overgeslagen, ook niet als
een runner traag is. Alleen een rapport (de p99, de max) mag na twee keer falen in een week
zonder een wijziging in `app/search` naar de integratie verhuizen. Faalt de p95, dan zoek je de
oorzaak in de code. De andere poorten met "hard" in de tabel van §4.4 (de herinneringen, de kaart
en de duplicaten) staan in dezelfde job en zijn ook hard.

## Draaien

Alles vanuit `api/`. Voor de e2e eerst `npm run build` in `web/` (de server serveert de gebouwde
web-app). Playwright komt via `uv run --with playwright`, en de browser kiest het harnas zoals
in de rest van de e2e ([fase 5](../fase-5/e2e.md#draaien)).

```sh
cd api

# api zoals de job api (zoek en triggers draaien mee)
uv run pytest -q

# alleen de tests van zoeken
uv run pytest -q -m "zoek or triggers"

# geo met de echte bronnen (zoals de stap in de job api)
python3 -I ../tools/geo/haal.py --naar /tmp/geo
PLATFORM_GEO_TEST_SRC=/tmp/geo uv run pytest -q -m geo tests/geo

# ocr met de echte tesseract (zoals de job ocr): api, e2e en de poort van §4.5
taskset -c 0-2 uv run pytest -q -m ocr -rs
taskset -c 0-2 uv run --with playwright python -m pytest ../e2e -m ocr -rs
taskset -c 0 uv run python -m tests.ocr.poort

# de e2e zoals de job e2e (zoek draait mee, ocr niet)
uv run --with playwright python -m pytest ../e2e -m "not perf and not media and not media_perf and not office and not office_real and not provider_real and not ocr"

# alleen de e2e van zoeken (zoals stap 3 van slim.sh)
uv run --with playwright python -m pytest ../e2e -m "zoek and not ocr and not perf and not media and not media_perf"

# de poort van §4.4 (zoals zoek-perf; lang)
taskset -c 0-2 uv run pytest -q -s -m zoek_perf tests/search/test_perf_100k.py
```

Lokaal draait `zoek_perf` op de SQLite van de host, niet op 3.46.1. De getallen die tellen, komen
uit de job `zoek-perf` in `python:3.12-slim`.

## `e2e/slim.sh`

De snelle e2e van zoeken (§4.1, "Snel lokaal", C16): groen vóór een pakket "klaar" meldt. Drie
stappen:

1. De api-tests met `(zoek or triggers) and not ocr and not media and not media_perf and not
   zoek_perf` in `python:3.12-slim` (SQLite 3.46.1, zoals het image). Met `--network none`, de
   venv van de host alleen-lezen erin, en de gemaakte bestanden van `tests/search/fixtures.py` in
   een eigen map (`api/.pytest_cache/slim`). Zo toont een pakket 3.46.1 aan zonder te pushen.
2. De web-app bouwen als een bestand in `web/` nieuwer is dan `web/dist/index.html`.
3. De e2e met `zoek and not ocr and not perf and not media and not media_perf` tegen die build,
   op de host.

```sh
e2e/slim.sh                 # alle drie de stappen
e2e/slim.sh --alleen-db     # alleen stap 1
e2e/slim.sh --alleen-e2e    # alleen stap 2 en 3
SLIM_CPUS=0-2 e2e/slim.sh   # alles op die cores (docker --cpuset-cpus en taskset)
```

Aan het eind staat een blok `=== kort ===`. Stap 1 heeft docker en het image
`python:3.12-slim` nodig. Draai `slim.sh` alleen, niet naast een andere zware run. Ook alleen was
een test met een grens van 1 s bij een upload er één keer rood (zie "Open na de nacontrole" in
[z7.md](z7.md)): een upload wachtte zonder rij op het schrijfslot van SQLite. Dat is opgelost in
I1 (D1) met de schrijfpoort (`app/dbgate.py`, [z2.md](z2.md), Open 12). Die grens hangt wel aan
de `fsync` van de schijf (tot ±200 ms extra houdt hij), dus ook daarom alleen. CI draait `slim.sh`
zelf niet; de job `infra`
haalt het script alleen door `shellcheck`.

## De meetscripts in `e2e/perf`

Elk script drukt aan het eind een blok `=== kort ===` af en schrijft met `--json` alles als JSON
(`zoek_samen.py` en `zoek_ui.py` ook via `$E2E_PERF_OUT`). `zoek_100k.py` en `zoek_ui.py` drukken geen vraag af, alleen
soorten en getallen.

| Script | Wat het meet | Wie het draait | Soort |
|---|---|---|---|
| `zoek_100k.py` | de client van §4.4: per rol 3 rondes van de vragen van §4.3, met volgende pagina's, alleen filters en suggesties; daarna 4 clients tegelijk met `GET /health` ernaast | `api/tests/search/test_perf_100k.py` (marker `zoek_perf`) in de job `zoek-perf`; met de hand ook tegen een app die al draait | hard (de p95) |
| `zoek_backfill.py` | de snelheid van de backfill van de tekst (§4.5), de hele keten op één core, met de worker en met de terugval zonder media | met de hand (de poort voor de worker); in CI kort in `e2e-media-perf` | poort met de hand, rapport in CI |
| `zoek_samen.py` | zoeken naast media en OnlyOffice (§4.5, deel B), in drie stappen: pauze, achtergrond en zonder Defer | met de hand (de hele test, met `--office`); in CI `--kort` in `e2e-media-perf` | poort met de hand (stap 2), rapport in CI |
| `zoek_ui.py` | zoeken in de browser: de eerste suggestie na een toets en het tekenen van `/zoeken`, koud en warm | met de hand; in CI in `perf` | rapport |

Hoe je ze met de hand draait, staat bovenaan elk script. De metingen van I1 met deze scripts
staan in [metingen.md](metingen.md).

De woorden van een poort verschillen per script. `zoek_backfill.py` drukt `poort ≥ N: gehaald` of
`poort ≥ N: NIET gehaald` af (`e2e/perf/zoek_backfill.py:352`), net als de poort van OCR
(`api/tests/ocr/poort.py:234`); `zoek_samen.py` drukt `gehaald`, `niet gehaald` of `niet te meten`
af (`e2e/perf/zoek_samen.py:636`). Dat blijft zo: `e2e/test_zoek_metingen.py` toetst beide vormen
letterlijk (`:45-49` met `NIET`, `:286` zonder), dus gelijk maken breekt een test, welke kant je
ook kiest. De blokken kort in `ci.yml` zoeken niet op deze woorden. `NIET gehaald` en `niet
gehaald` betekenen hetzelfde.

## De e2e van zoeken

Alle bestanden hieronder hebben de marker `zoek`, behalve `test_zoek_ocr.py` (marker `ocr`).
Elke test in de browser werkt met alleen het toetsenbord; de toetsen staan in de lijsten
`toetsenbord-z*.md` en `toetsenbord-x1.md`. `test_zoek_z2.py` praat alleen met de API, zonder
browser.

- `test_zoek_z0.py` (Z0): het harnas in de echte app; de wereld van §4.2, zoeken op naam, een
  upload met tekst en de toetsen van het zoeken.
- `test_zoek_z2.py` (Z2): tekst uit bestanden; een upload, de stap `text`, en het woord staat in
  de index met een snippet; de backfill na een herstart. De test met de worker heeft ook de marker
  `media` en draait in de job `e2e-media`.
- `test_zoek_z4.py` (Z4): het veld bovenaan, `/zoeken` en de viewer met de markering.
- `test_zoek_personen.py` en `test_album_regel.py` (Z6): personen met een kader, zoeken en
  filteren op een persoon; een album met een regel en een publieke link.
- `test_zoek_kaart.py`, `test_zoek_dubbel.py` en `test_share_warning.py` (Z5): de kaart zonder
  verzoek naar een andere host, dubbele bestanden op `/dubbel`, en de waarschuwing over de locatie
  in het deeldialoog.
- `test_zoek_beheer.py` (Z7): de tab "Zoeken en foto's" op `/beheer`.
- `test_zoek_ocr.py` (X1, marker `ocr`): een PDF zonder tekstlaag en een foto van een bon, zoeken,
  de snippet, de markering in de viewer, en een kopie van Bram die de tekst van Eva niet vindt.

## De jobs in CI

Een harde job maakt de run rood als hij faalt. Een job met alleen een rapport heeft
`continue-on-error: true` in `.github/workflows/ci.yml`: de run blijft groen, en de getallen staan
in het log (het blok `=== kort ===`) en in het artefact.

| Job | Wat van zoeken | Soort |
|---|---|---|
| `api` | de hele suite, met `zoek` en `triggers` (op de runner, niet in `python:3.12-slim`); daarna `-m geo` met de echte bronnen | hard |
| `api-media` | `-m media`, met onder meer de opdracht `text` in de echte worker (`tests/extract/test_media_text_handshake.py`) | hard |
| `ocr` | `-m ocr` in `api` en in `e2e`, dan de poort van §4.5 (`python -m tests.ocr.poort` onder `taskset -c 0`) | hard |
| `zoek-perf` | in `python:3.12-slim`: de plannen (S4), de vormen per woord (S8), het schema van de bench (S9), de kaart (S6), de churn en de hele set triggers (marker `triggers` zonder `zoek_perf`, `media`, `media_perf` en `ocr`, zoals stap 1 van `slim.sh`), de marker `zoek_perf` (S1, S2, S3, S7), de poorten van §4.4 (zoeken, herinneringen, kaart, duplicaten), de vormen van Keuze 4a (S10) en de wereld uit Natural Earth | hard |
| `e2e` | de e2e met `zoek`, naast de rest zonder `ocr` | hard |
| `media-image` | de handdruk (S11) en `test_ocr_container.sh` in het echte image | hard |
| `images` | de grootte van het image met tesseract (Vraag 15) | hard |
| `infra` | `test_install_search.sh`, de functies en `--stappen` (de update en terug naar 0.7.1); `shellcheck` over onder meer `e2e/slim.sh` | hard |
| `web` | typecheck, vitest, de build, i18n streng en de begrotingen | hard |
| `perf` | `e2e/perf/zoek_ui.py`, na `run_all.py` | rapport (`continue-on-error`) |
| `e2e-media-perf` | `zoek_samen.py --kort` en `zoek_backfill.py` (kort), na `-m media_perf` | rapport (`continue-on-error`) |

Geen nieuwe actions (§4.6): de jobs gebruiken alleen wat `ci.yml` al gebruikte. De jobs van X2 en
X3 (`api-analyse`, `analyse-image`, `beeld-model`) komen later, niet in I1.

**Wat I1 hier veranderde.** Twee punten van I1 raken deze tabel, en de tabel staat er al mee.
I1 (A3, `f211d7b`) gaf `zoek-perf` de stap "Churn en de triggers op 3.46.1"
(`.github/workflows/ci.yml:262-267`): de 34 tests met de marker `triggers` die niet ook
`zoek_perf` hebben (de vier met `zoek_perf` in die bestanden draait de stap daarna al, één keer).
Daarvoor draaiden ze op 3.46.1 alleen in `slim.sh`. I1 (S11, `86a4c47`) haalde de meting S5 van
de spike uit `media-image`: tesseract zit sinds X1 in het image, en wat het kost, bewaakt de job
`images`. `api/tests/search/test_zoek_ci.py` houdt beide vast. Of de nieuwe stap in CI groen is
en hoe lang `zoek-perf` dan duurt (geschat 22 tot 24 minuten, de timeout is 75), laat pas de run
na de push van I1 zien.

## Uitslag I1

Nog leeg. Dit vult stap B van I1, met de checks en de metingen op de kop van I1 en de run in CI
daarna. De getallen komen in [metingen.md](metingen.md), de checks in
[integratie.md](integratie.md).

Zie ook: [z7.md](z7.md) en [x1.md](x1.md) voor de laatste golf, en het runbook
[zoeken.md](../runbooks/zoeken.md) voor de metingen op de LXC.
