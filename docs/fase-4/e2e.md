# Fase 4 — de e2e van office

Hoe de e2e van office werken, hoe je ze draait (met de nep-DS en met de echte DS), en hoe je
lokaal met een echte DS ontwikkelt. Gemaakt in O0-e2e. Het ontwerp staat in
[bouwontwerp.md](bouwontwerp.md), §3.6, §4.4 en §4.5. De uitslag van de spike staat in
[spike.md](spike.md).

## In het kort

| Marker | Tegen | Waar in CI | Bestanden |
|---|---|---|---|
| `office` | de nep-DS (`python -m tests.fake_docserver`) als eigen proces | job `e2e`, in elke run | `e2e/test_office_o<N>.py` |
| `office_real` | de echte DS 10.0.0.1, op digest | job `office`, in elke run (harde poort) | `e2e/test_office_o<N>_real.py` |

- Een test met de echte DS vervangt nooit een test met de nep. De nep draait ook als Docker Hub
  en de mirror een dag niet willen.
- Zonder `E2E_REAL_DS` slaat pytest de tests met `office_real` over. In de job `office` telt
  overslaan als mislukt.
- Elk pakket (O1 tot O5) voegt zijn eigen `test_office_o<N>.py` en `test_office_o<N>_real.py`
  toe. Het harnas en de fixtures zijn van O0 (frozen).

| Bestand | Wat |
|---|---|
| `e2e/office_harness.py` | het harnas: `FakeDS`, `DocStub`, `Office`, de gastpagina en de hulpjes |
| `e2e/conftest.py` | de fixtures `office`, `real_office`, `office_browsers` en `real_browsers`, en de optie `office_frames` van de bewaking |
| `e2e/office_ds.sh` | de echte DS starten, wachten, logs ophalen en stoppen (CI en lokaal) |
| `e2e/fixtures/office/` | `proef.docx`, `.xlsx`, `.pptx`, `.doc`, `.xls`, `.ppt` en `.rtf`, met een [README](../../e2e/fixtures/office/README.md) over hoe ze gemaakt zijn |
| `e2e/test_office_o0.py` | O0 met de nep-DS: alles dicht zonder office, de status en de proxy, twee editors via proxy en tunnel, DS weg en terug |
| `e2e/test_office_o0_real.py` | de rooktest met de echte DS (§4.5 punt 0) |
| `api/tests/fake_docserver/recorded/` | wat de echte DS deed (de opnames uit de spike); de nep-DS en `api/tests/test_fake_docserver.py` houden zich daaraan |

## Draaien

Alles vanuit `api/`, na `npm run build` in `web/` (de server serveert de gebouwde web-app).
Playwright komt via `uv run --with playwright`, zoals de andere e2e. Chromium zoals in de andere
e2e (in de sandbox `/opt/pw-browsers/chromium` met `--no-proxy-server`).

### Met de nep-DS

```sh
cd api
uv run --with playwright pytest ../e2e -m office -q
```

De fixture `office` start per sessie één nep-DS en één server met office aan. Er is geen Docker
nodig. De gewone e2e (`-m "not perf"`) draait deze tests ook.

### Met de echte DS

```sh
bash e2e/office_ds.sh start     # image op digest ophalen en een nieuwe container starten
bash e2e/office_ds.sh wait      # wachten tot DS gezond is en zijn font-herstart voorbij is
cd api
E2E_REAL_DS=http://127.0.0.1:18080 E2E_REAL_DS_SECRET_FILE=/tmp/office-ds/jwt.secret \
  uv run --with playwright pytest ../e2e -m office_real -rs
cd .. && bash e2e/office_ds.sh stop   # de container weg; het image blijft
```

- `start` maakt de geheimen zelf (JWT en secure link), per keer, met 600-rechten, in
  `OFFICE_DS_DIR` (standaard `$RUNNER_TEMP/office-ds`, anders `/tmp/office-ds`). Ze staan nooit op
  de opdrachtregel en nooit in een log. `stop` haalt ze weer weg.
- Een nieuwe container bouwt zijn fonts en herstart daarna docservice en de converter, 2,5 tot 3
  minuten na de start (spike, extra bevinding 1). `wait` wacht daarom eerst op die herstart
  (in de log van supervisord `success: converter` na `stopped: converter`; docservice is dan al
  terug) en daarna op `/healthcheck == true`, hooguit 6 minuten. Een test die tijdens die
  herstart draait, ziet een editor die even wegvalt.
- Start DS als eerste, en bouw dan de web-app: dan bouwt DS zijn fonts in die tijd.

### De omgeving

Voor de tests:

| Variabele | Wat |
|---|---|
| `E2E_REAL_DS` | het adres van de echte DS, bijv. `http://127.0.0.1:18080`. Zonder: `office_real` wordt overgeslagen |
| `E2E_REAL_DS_SECRET_FILE` | het bestand met het JWT-geheim van die DS (van `office_ds.sh`: `<OFFICE_DS_DIR>/jwt.secret`). Het geheim zelf staat nooit in de omgeving |
| `E2E_REAL_DS_APP_HOST` | hoe DS de host bereikt, standaard `host.docker.internal` (`office_ds.sh` zet `--add-host=host.docker.internal:host-gateway`) |
| `E2E_ARTIFACTS` | waar schermafdrukken en de meldingen uit de editor komen; standaard een map `artefacten` onder de basetemp van pytest |
| `E2E_HEADED=1` | Chromium met venster (lokaal kijken) |

Voor `office_ds.sh`:

| Variabele | Standaard | Wat |
|---|---|---|
| `OFFICE_DS_NAME` | `e2e-office-ds` | naam van de container |
| `OFFICE_DS_PORT` | `18080` | poort op 127.0.0.1 |
| `OFFICE_DS_DIR` | `$RUNNER_TEMP/office-ds` of `/tmp/office-ds` | map voor de geheimen |
| `OFFICE_DS_CONFIG` | leeg | een `local-production-linux.json` voor DS, read-only gemount. Na O6 is dat `infra/single/office/local-production-linux.json` (§4.5 stap 2) |
| `OFFICE_DS_WAIT_SECONDS` | `360` | hoe lang `wait` wacht |

Zonder `OFFICE_DS_CONFIG` draait DS met zijn eigen standaard. Het verschil dat er nu toe doet:
**3 pogingen voor een callback** (±7 s), niet de 8 (±255 s) uit de spike. Een test die de app
langer dan een paar seconden weg laat, gebruikt tot O6 een eigen configuratie (zoals de
herhaling van stap 5 in [spike.md](spike.md)), of verwacht de forgotten files.

Het image: eerst `mirror.gcr.io/onlyoffice/documentserver@sha256:ca17…`, dan Docker Hub, drie
rondes. Daarna zet het script de naam `onlyoffice/documentserver:10.0.0.1` erop (`docker tag`),
zoals `install.sh`. Een image dat er al is, haalt het niet opnieuw op.

## Hoe het harnas werkt

### De fixtures

| Fixture | Scope | Wat |
|---|---|---|
| `office` | sessie | `start_fake_office`: een `FakeDS`, een `DocStub` en een eigen `Server` met office aan. Geeft een `Office` |
| `real_office` | sessie | `start_real_office`: wacht op `/healthcheck` (hooguit 6 minuten), dan een `DocStub` en een `Server` tegen `E2E_REAL_DS`. Overgeslagen zonder `E2E_REAL_DS`; faalt als `E2E_REAL_DS_SECRET_FILE` geen bestand is |
| `office_browsers` | test | `Browsers` op de server van `office`. Elke melding van de bewaking laat de test falen, ook een uit de nep-editor |
| `real_browsers` | test | `Browsers` op de server van `real_office`, met `office_frames="record"` (zie hieronder) |

De gewone fixtures `server` en `admin` hebben geen office. Daarmee bewijst
`test_zonder_office_is_alles_dicht` dat de app zonder office precies fase 3 is.

`conftest.py` importeert `office_harness` pas in een fixture, omdat het harnas zelf uit
`conftest` importeert.

### De server met office aan

`office_env` zet voor de `Server`:

| Variabele | Waarde | Waarom |
|---|---|---|
| `PLATFORM_OFFICE_URL` | het adres van DS | |
| `PLATFORM_OFFICE_SECRET` | het geheim van DS | nep: per sessie gemaakt; echt: uit het bestand |
| `PLATFORM_OFFICE_APP_URL` | `http://<host>:<interne poort>` | nep: `127.0.0.1`; echt: `host.docker.internal` |
| `PLATFORM_OFFICE_INTERNAL_PORT` | een vrije poort | de fixture kiest hem zelf |
| `PLATFORM_OFFICE_INTERNAL_HOST` | `0.0.0.0` (alleen echt) | DS in Docker moet de interne luisteraar bereiken |
| `PLATFORM_OFFICE_SWEEP_SECONDS` | `1` | de ronde (§4.4) |
| `PLATFORM_OFFICE_RECOVERY_SECONDS` | `2` | het herstel (§4.4) |
| `PLATFORM_OFFICE_HEALTH_SECONDS` | `1` | zodat een test "DS weg" en "DS terug" binnen enkele seconden ziet |
| `PLATFORM_OFFICE_DEV_UNGATED` | `1` | in O0 forwarden proxy en tunnel alleen hiermee; na O1 doet hij niets meer |

Na de start wacht de fixture tot `/api/v1/office/status` `state: "ok"` zegt. Dat is de grote
controle van de gezondheid (versie, licentie, tokens geëist) die slaagt.

### `FakeDS`

De nep-DS als eigen proces: `python -m tests.fake_docserver --port … --secret-file …
--link-secret-file … --time-scale 0.05 --save-delay 0.2`.

- Een vrije poort, de geheimen in een tijdelijke map met 600-rechten, een omgeving zonder
  `PLATFORM_*`.
- `stop()` stopt op PID (SIGTERM, dan SIGKILL), `start()` start opnieuw op dezelfde poort met
  dezelfde geheimen, `restart()` doet beide.
- `control(name, body)` roept een stuurpunt `/__fake/<name>` aan, `info(key)` geeft wie er op
  een key zit, `log()` en `callbacks(key)` wat hij deed.
- De tijd loopt 20 keer zo snel (`--time-scale 0.05`): de pogingen voor een callback en het
  wachten na de laatste die sluit.

### `DocStub`

Een kleine HTTP-luisteraar in het testproces die voor DS de kant van de app speelt.

**Waarom:** in O0 zijn de interne routes van de app nog stubs (503 `office_off`). Zonder de
DocStub kan DS geen document ophalen en geen callback kwijt. O2 bouwt de echte interne
luisteraar. Vanaf dan gebruiken de tests van O2 en later de echte config uit `POST …/session`, en
is de DocStub alleen nog voor wat de app niet zelf doet (zoals een bron voor een conversie in de
rooktest).

- `put(name, data)` geeft een adres `/doc/<name>` voor `document.url`; `source(name, data)` een
  adres `/src/<name>` voor een conversie.
- Een GET controleert het download-JWT in `Authorization` met `tokens.check_download`, precies
  zoals de app dat doet. Hij geeft `Repr-Digest`. `downloads` houdt bij wat er gevraagd werd.
- `callback_url(tag)` is het adres voor `editorConfig.callbackUrl`. Een POST controleert het
  callback-JWT met `tokens.callback_data`. Bij status 2 of 6 haalt hij het bestand op via
  `urls.rewrite(..., kind="data")` (zoals de app) en bewaart het: `last_saved(key)`.
  `callbacks(key)` geeft de velden zonder het token, plus of er een token in de kop en in de body
  stond.
- `answer` zet het antwoord op een callback: `"ok"`, `"503"` of `"error1"`.
- `stop()` en `start()` gebruiken dezelfde poort, zodat een test "de app weg" kan spelen.
- Tegen de echte DS luistert hij op `0.0.0.0` en geeft hij adressen met `host.docker.internal`.

### De gastpagina

`serve_host(page, office, config)` zet met `page.route` een pagina op de oorsprong van de app
(`/__e2e/office/…`), met **precies de CSP van de app** (gelezen van `/`). Die pagina laadt
`api.js` via `/office/` en opent de editor met `config`. Geen inline script of stijl: de config
staat als JSON in de pagina, het script en de stijl zijn eigen routes. Zo gaat alles door de
echte proxy en tunnel, zoals straks de editorpagina van de web-app (O2). Zodra `/bewerken/<id>`
er is, openen de tests van O2 en later die pagina in plaats van de gastpagina.

De gastpagina heeft geen `onRequestSaveAs`: met die handler vraagt de echte editor bij
"Downloaden als" de pagina om de opslag, in plaats van zelf te downloaden (spike).

### De hulpjes

| Hulpje | Wat |
|---|---|
| `new_key(tag)` | een nieuwe documentkey |
| `editor_config(office, key=…, url=…, user_id=…, user_name=…, file_type="docx", mode="edit", lang="nl", callback=…, customization=…)` | een config voor `DocsAPI.DocEditor`, ondertekend met `tokens.browser_token` |
| `open_editor(page, office, config, timeout=60)` | de gastpagina openen en wachten op `onDocumentReady` (of falen op `onError`); geeft de tijd in seconden |
| `events(page)` | de gebeurtenissen van de editor tot nu toe |
| `frame_editor(page)` | de iframe `frameEditor` |
| `is_fake(page)` | of de editor de nep is |
| `type_in_editor(page, text)` | typen: in de nep in het tekstvak "Nep-editor"; in de echte DS een klik in het document (`#id_main_view`, `#ws-canvas-outer` of `#editor_sdk`), dan Ctrl+End, Enter en `keyboard.type` |
| `wait_callback(stub, key, status, timeout=60, after=0)` | wachten op een callback met die status |
| `fake_info(office, key)` | de gebruikers op een key, uit de nep-DS (zonder O5) |
| `docx_text(data)` | de tekst van een docx |
| `fixture(name)` | de bytes van een bestand uit `e2e/fixtures/office/` |
| `office.status()`, `office.wait_state(state, seconds)` | `/api/v1/office/status` als beheerder |
| `office.ds(fn)` | `fn(DocServer)` met de instellingen van de server (async, voor commando's en conversies) |
| `office.settings()` | `Settings` zoals die van de server, voor `tokens` en `urls` in een test |

## De bewaking

Elke context krijgt de bewaking van de andere e2e (CSP-schendingen, `i18n:`-fouten, gelogde en
onafgevangen fouten). Die draait ook in iframes, en een melding noemt nu de frame waar hij
vandaan kwam.

- `office_browsers` (nep-DS): alles faalt, ook een melding uit de nep-editor.
- `real_browsers` (echte DS): meldingen uit frames onder `/office/` (de editor van OnlyOffice
  zelf) komen in `office_records` en laten de test niet falen. De fixture schrijft ze naar
  `<test>-office-frames.txt` in `E2E_ARTIFACTS`. Meldingen van de pagina van de app zelf falen
  zoals altijd. Zo zien we wat de editor van DS logt, zonder dat een fout in DS (zoals de race in
  `ColorPaletteExt.js`, A22) de test van de app laat falen.

Bij een gefaalde test maken beide fixtures een schermafdruk van elke open pagina, in
`E2E_ARTIFACTS`.

## Een test toevoegen (per pakket)

- Met de nep: `e2e/test_office_o<N>.py` met `pytestmark = pytest.mark.office`, de fixtures
  `office` en `office_browsers`.
- Met de echte DS: `e2e/test_office_o<N>_real.py` met `pytestmark = pytest.mark.office_real`, de
  fixtures `real_office` en `real_browsers`. De PR van het pakket moet door de job `office`.
- Elke browser in een eigen context met `service_workers="block"`, tenzij de test de service
  worker van DS zelf bekijkt.
- Processen alleen op PID stoppen (`FakeDS.stop`, `Server.stop`), nooit op een patroon.
- Geen geheim in een assert-melding of een log: `Office.secret` staat niet in de `repr`, en de
  DocStub bewaart de tokens niet.
- De sessie deelt één `office` en één `real_office`. Een test die DS stopt of de DocStub een
  ander antwoord laat geven, zet het in een `finally` terug.
- Met de echte DS: een key per test (`new_key`), en een document openen kost 2,4 tot 4,8 s
  (spike, stap 11). Een pptx opent via de proxy soms niet (A22, 4 van 22 keer): een test met een
  pptx doet wat de editorpagina van O2 gaat doen (geen `onAppReady` binnen 20 s → één keer
  opnieuw laden), en schrijft het op als dat nodig was. Nooit vaker dan één keer.

## CI

### Job `e2e`

Draait de gewone e2e (`-m "not perf and not office and not office_real"`) en daarna, als eigen
stap, `-m office` met de nep-DS. Samen is dat wat `-m "not perf"` lokaal doet (§4.1), maar een
fout in office staat zo apart in de uitslag. Vindt `-m office` geen enkele test, dan faalt de stap
(pytest geeft dan 5).

### Job `office`

Zoals §4.5, met `e2e/office_ds.sh`:

1. `office_ds.sh start` als eerste stap: het image op digest (mirror, dan Docker Hub, drie rondes)
   en een nieuwe container. Zo bouwt DS zijn fonts terwijl de web-app bouwt.
2. uv, Node, `uv sync --frozen`, de web-app bouwen, Chromium zoals in de job `e2e`.
3. `office_ds.sh wait`: gezond en de font-herstart voorbij, hooguit 6 minuten.
4. `pytest ../e2e -m office_real -rs` met `E2E_REAL_DS`, `E2E_REAL_DS_SECRET_FILE` en
   `E2E_ARTIFACTS`. Overgeslagen of geen enkele test geslaagd telt als mislukt.
5. Bij een fout: `office_ds.sh logs` (de logs van Docker, supervisord en docservice) en de
   artefacten als upload. Altijd: `office_ds.sh stop`.

De job is een harde poort, net als de e2e (B29). Valt hij door een pull van het image, dan
opnieuw draaien; het image is op digest vastgepind. Tot O6 draait DS er met zijn eigen standaard
(zie boven).

Verschil met productie: in CI hangt DS aan de standaardbridge (met uitgang), niet aan het
interne netwerk. Proxy, tunnel, cookie en tokens zijn gelijk.

## Ontwikkelen met een echte DS (Keuze 33)

`vite dev` stuurt `/api` en `/office` (met `ws: true`) naar poort 8000. Gewone uvicorn heeft onze
`Tunnel` niet, dus draait de API met `app.serve` op poort 8000. DS start je met
`office_ds.sh` (hierboven), of zelf:

```sh
dir="$HOME/.office-dev" && mkdir -p "$dir" && chmod 700 "$dir"
(umask 077 && od -An -N32 -tx1 /dev/urandom | tr -d ' \n' >"$dir/jwt.secret" \
  && od -An -N32 -tx1 /dev/urandom | tr -d ' \n' >"$dir/link.secret")
JWT_SECRET="$(cat "$dir/jwt.secret")" SECURE_LINK_SECRET="$(cat "$dir/link.secret")" \
  docker run -d --name office-dev -p 127.0.0.1:18080:80 \
  --add-host=host.docker.internal:host-gateway \
  -e JWT_ENABLED=true -e JWT_SECRET -e JWT_HEADER=Authorization -e SECURE_LINK_SECRET \
  -e WELCOME_PAGE_ENABLED=false -e PLUGINS_ENABLED=false \
  onlyoffice/documentserver@sha256:ca17889f10b11141cc6441836aebdd2835cf3e1b49681b2fd6e155c94b7850b3
```

Dan de API (in `api/`) en de web-app (in `web/`):

```sh
PLATFORM_HTTP_PORT=8000 PLATFORM_OFFICE_URL=http://127.0.0.1:18080 \
  PLATFORM_OFFICE_SECRET="$(cat "$HOME/.office-dev/jwt.secret")" \
  PLATFORM_OFFICE_APP_URL=http://host.docker.internal:8081 \
  PLATFORM_OFFICE_INTERNAL_HOST=0.0.0.0 PLATFORM_OFFICE_DEV_UNGATED=1 \
  uv run python -m app.serve
npm run dev
```

- `PLATFORM_OFFICE_DEV_UNGATED=1` is nodig tot O1: zonder gaat `/office/` niet door. Na O1 doet
  hij niets meer; bij de integratie gaat hij weg. Nooit in productie.
- De interne luisteraar luistert met `0.0.0.0` op alle adressen van je machine, op poort 8081.
  Doe dit alleen op een machine waar dat mag (of zet een firewall op 8081).
- In O0 laadt `api.js` zo via `/office/`, maar een document openen kan pas met O2: de interne
  routes zijn nog stubs.
- Wacht na een nieuwe container 2,5 tot 3 minuten op de font-herstart voordat je een editor
  opent.
- Opruimen: `docker rm -f office-dev && rm -rf "$HOME/.office-dev"`. Het image blijft.
