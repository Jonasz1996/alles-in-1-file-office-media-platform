# P10 install-ci: de weg 0.8.1 -> 0.8.2 -> terug in CI

Golf 4 van het plan voor 0.8.2. Doel: de update van één node, van 0.8.1 naar 0.8.2 en terug,
draait in CI. Commits: `be5ac0b` (het image) en `fd06f28` (de installatietests).

## Wat er veranderde

| Wat | Waar | Waarom |
|---|---|---|
| Een tweede echte modus `--stappen-081` | `infra/single/tests/test_install_search.sh` | 0.8.1 (`ae163ac`, schema `0012`) met zijn eigen script, de analyse aan (de standaard sinds 0.8.1) en de modellen onbereikbaar; bijwerken naar deze versie (`0014`); terug naar 0.8.1 met precies het runbook; weer vooruit. install.sh mag nergens stoppen op de modellen. |
| Het paar van 0.8.1 naast dat van 0.8.0 | idem, deel 1 (de functies) | `BACK081_SHA` en `BACK081_HEAD=0012`; de controle van de docs kijkt ook het blok van 0.8.1 na. |
| "Terug naar 0.8.1 van `ae163ac`" | `docs/runbooks/zoeken.md`, `infra/single/README.md` | Die weg stond nog nergens. Op SQLite doen 0013 en 0014 niets: `db-downgrade 0012` noemt niets dat weggaat. |
| De stap in de job `infra` | `.github/workflows/ci.yml` | Na `--stappen`, met een eigen log en een eigen blok in Samenvatting. |
| `test_install_update.sh` bij de tijd | `infra/single/tests/test_install_update.sh` | Zie hieronder. |
| De job `install-update` | `.github/workflows/ci.yml` | De test van fase 4 als root op een schone runner, met Chromium van Playwright. |
| Geen cache van uv in het image | `Dockerfile`, job `analyse-image` | `uv sync --no-cache`: de ±146 MiB in `/root/.cache/uv` is weg (a0.md, Open 9). De job kijkt dat er hooguit 1 MiB over is. |
| De kinderen van de worker in het image | job `analyse-image` | `test_analyse_kinderen.py` als `analyse`, zonder netwerk en alleen-lezen, in het echte image. pytest komt met zijn sha256 uit uv.lock in een map van de runner; het image verandert niet. Precies 15 passed. |

## `--stappen-081`

De analyse wil een pad van root (`analyse_path_ok` in install.sh). Een werkmap die de test zelf
maakt, wordt daarom van root met 1777, zoals `/tmp`. Een gegeven werkmap
(`INSTALL_TEST_DIR`) die anderen kunnen schrijven, weigert de test; die blijft zoals hij was.

De nep-curl geeft voor `media.githubusercontent.com` en `huggingface.co` altijd "geen netwerk".
Per stap kijkt de test na: exit 0, `Analyse: models_missing` van de container, beide modellen
van gezichten één keer geprobeerd, geen `.onnx` en geen tijdelijke map, en zoeken op beeld "niet
geladen". Stap 3 volgt het runbook: `dc stop platform media` (de analyse blijft draaien),
`db-backup` (een kopie van `0014`), `db-downgrade 0012` met "ja", en het script van `ae163ac`
met `PLATFORM_SRC_REF`.

## `test_install_update.sh`

Deze test (fase 4: office over een update) stond nooit in CI en liep sinds fase 4 niet meer.
Wat er niet meer klopte:

- Media staat sinds fase 5 standaard aan, de analyse sinds 0.8.1. De eigen compose-aanroepen van
  de test (B2, S, K, R3) namen alleen `compose.yaml` en office. Een losse `up -d` maakte de app
  dan opnieuw zonder media, en `--remove-orphans` ruimde de mediaworker op. Nu gaan ze via `dc`,
  de functie uit de README: de bestanden volgen de containers die er zijn.
- Stap B zet `ANALYSE=uit` (één keer; `.env` onthoudt het): de test gaat over office en haalt
  geen modellen. De analyse over een update toetst `--stappen-081`.
- De test moet root zijn (.env is 600, de broncode van root). Hij zegt dat nu meteen. Met sudo is
  de checkout van een andere gebruiker; git leest die met `safe.directory`.

## Gedraaid (lokaal, zonder Docker)

- `shellcheck` op alle bestanden van de job `infra`: schoon. `bash -n` op beide tests.
- `bash infra/single/tests/test_install_search.sh` (de functies): 215 controles goed, ook de
  docs van 0.8.1 en de weigering van `--stappen-081`.
- `uvx yamllint -s .github/workflows/ci.yml`: schoon.
- `ruff check` en `ruff format --check` in `api/`: schoon.
- Gerichte pytest op SQLite: `test_analyse_image.py`, `test_beeld_ci.py`, `test_beeld_tools.py`,
  `test_zoek_ci.py`, `test_media_ci.py`, `test_postgres_ci.py`, `test_cli_db.py`: 261 passed.
- `db-downgrade` op SQLite van `0014` naar `0012` (de vraag, dan meteen "De database staat nu op
  revisie 0012.") en naar `0006` (de regels van wat weggaat).
- De kinderen zoals in het image: een venv zonder dev met de extra `analyse`, pytest via
  `--target`: 15 passed.

## Wat CI moet bewijzen

Hier kon het niet (geen Docker-daemon). Op de PR-run:

1. Job `infra`: `--stappen-081` groen, het blok Samenvatting met "controles: N goed, 0 fout", en
   `--stappen` nog steeds groen.
2. Job `install-update`: "Alle controles geslaagd." en "Alles van de test is weg".
3. Job `analyse-image`: de stap "Geen cache van uv in het image" groen; de kinderen precies
   "15 passed".
4. Jobs `images` (platform) en `media-image`: bouwen zoals eerst.

## Open

- `api/Dockerfile` (het image `api` van de job `images`) heeft nog `uv sync --frozen --no-dev`
  zonder `--no-cache`. Dat bestand hoort niet bij P10. **Golf 4b**: gedaan, nu ook met
  `--no-cache`; `test_geen_cache_van_uv_in_de_images` (`tests/analyse/test_analyse_image.py`)
  houdt het vast voor beide Dockerfiles.
- `test_install_update.sh` liep nergens echt. Is de job rood, dan zegt de log welke stap; een
  rode stap daar is een echte bevinding over office na een update, geen reden om de test te
  verzwakken.
