# Fase 3 — e2e-tests en prestatiemetingen

De e2e-tests draaien de echte server (`python -m app.serve`) met de gebouwde web-app en
sturen een echte Chromium aan met Playwright (Python). Playwright is gereedschap, geen
afhankelijkheid: het staat niet in `api/pyproject.toml`, `uv.lock` of `web/package.json`
(B19, Vraag 1 in het bouwontwerp). `uv run --with playwright` haalt het per keer erbij.

Eigenaar: P0 (frozen na P0). Pakketten voegen hun eigen `e2e/test_<pkg>*.py` en
`e2e/seed_<pkg>.py` toe en gebruiken de fixtures hieronder; wijzigen in deze bestanden gaat
via de P0-hotfix-lane.

| Bestand | Wat |
|---|---|
| `e2e/conftest.py` | server, accounts, browser, bewaking, fixtures en hulpjes voor 2FA en CDP |
| `e2e/seed.py` | testdata rechtstreeks in SQLite: blobs met previews, grote mappen, publieke link |
| `e2e/test_smoke.py` | inloggen → Bestanden → uploaden → viewer → thema → uitloggen |
| `e2e/test_theme.py` | geen themaflits bij het laden |
| `e2e/perf/first_view.py` | eerste weergave < 1,5 s (ontwerp §4.2) |
| `e2e/perf/scroll.py` | 10.000 bestanden soepel scrollen (ontwerp §4.4) |
| `e2e/pytest.ini` | markers `perf`, `slow` en `p1` … `p6` (`--strict-markers`: een pakket gebruikt alleen deze); de meetscripts zijn ook testmodules |
| `e2e/ruff.toml` | dezelfde ruff-regels als `api/` |

## Getest paar

- Playwright (Python) **1.64.0** via `uv run --with playwright` (uv haalt het van PyPI).
- Chromium **141** (revisie 1194) in `/opt/pw-browsers/chromium` in de sandbox. Playwright
  1.64 verwacht eigenlijk revisie 1248, maar stuurt deze Chromium via `executable_path`
  zonder problemen aan.
- In de sandbox staat Playwright 1.64.0 ook in de systeem-Python (`python3 -c "import
  playwright"`), maar de tests draaien in de omgeving van de API (`uv run`), dus via
  `--with playwright`.

**Nooit `playwright install` in de sandbox.** Elders (CI, een eigen machine) wel, één keer:

```sh
cd api && uv run --with playwright python -m playwright install --with-deps chromium
```

Welke Chromium: `E2E_CHROMIUM` als die gezet is, anders `/opt/pw-browsers/chromium` als die
bestaat, anders die van Playwright zelf. Chromium start altijd met `--no-proxy-server`: anders
gaat ook verkeer naar 127.0.0.1 via de proxy van de sandbox. De Python-kant (httpx) negeert
proxy-variabelen (`trust_env=False`) en de harnas zet `127.0.0.1,localhost` in `NO_PROXY`.

## Draaien

Eerst de web-app bouwen; de server serveert `web/dist`:

```sh
cd web && npm ci && npm run build      # npm ci alleen als node_modules ontbreekt
```

De functionele tests (een harde poort, ook in CI):

```sh
cd api && uv run --with playwright pytest ../e2e -m "not perf" -q
```

De metingen (traag; in CI alleen rapporteren):

```sh
cd api && uv run --with playwright pytest ../e2e -m perf -s          # JSON in de uitvoer
cd api && uv run --with playwright python ../e2e/perf/first_view.py  # los, eigen server
cd api && uv run --with playwright python ../e2e/perf/scroll.py --modes list,grid,timeline
```

De losse scripts starten hun eigen server met een tijdelijke datamap, maken een beheerder,
vullen de data (2.000 blobs, een map van 10.000, een publieke link; voor de tijdlijn ook
50.000 foto's) en schrijven JSON naar stdout (en met `--out bestand.json` ook naar een
bestand). Opties: `first_view.py --runs 5 --pages login,folder,public --n 10000`,
`scroll.py --modes list,grid,timeline --rates 1,4 --n 10000 --photos 50000`. De exitcode is 1
als een harde poort faalt.

Omgevingsvariabelen:

| Variabele | Effect |
|---|---|
| `E2E_CHROMIUM` | pad naar de Chromium die gestart wordt |
| `E2E_WEB_DIST` | een andere gebouwde web-app dan `web/dist` |
| `E2E_HEADED=1` | Chromium met venster (lokaal debuggen) |
| `E2E_KEEP_DATA=1` | de datamap van een los meetscript niet weggooien |
| `E2E_PERF_OUT=<map>` | de JSON van de metingen ook als `<map>/first_view.json` en `scroll.json` |
| `E2E_PERF_REPORT_ONLY=1` | metingen falen nooit op een poort (de `perf`-job in CI) |

## De server

`Server` (in `conftest.py`) start `python -m app.serve` vanuit `api/` met:

- `PLATFORM_DATA_DIR` een tijdelijke map, `PLATFORM_WEB_DIST` = `web/dist`;
- `PLATFORM_HOST=127.0.0.1` en vrije poorten voor http en https (https gaat nooit open: er is
  geen certificaat);
- `PLATFORM_RATE_LIMITS=false` (alleen voor deze testserver; de server logt een waarschuwing).

Klaar is hij als `/health` 200 geeft. Stoppen gaat via zijn PID: SIGTERM, na 30 s SIGKILL
(nooit `pkill`). Na een herstart houdt hij dezelfde poort. De log staat naast de datamap
(`data-server.log`); bij een mislukte start staat het einde ervan in de foutmelding.

## Fixtures

| Fixture | Bereik | Wat |
|---|---|---|
| `server` | sessie | de draaiende `Server`; `server.url`, `server.call(account, method, path, …)` |
| `admin` | sessie | de eerste beheerder, zie hieronder |
| `user_factory` | test | `user_factory(role="user", name=…, quota_bytes=…)` → `Account` via `POST /users` |
| `browser` | sessie | Chromium (Playwright, sync API) |
| `browsers` | test | maakt contexts met bewaking; faalt de test bij een treffer |
| `new_context` | test | `new_context(**opties)` → niet ingelogde context (nl-NL, Europe/Amsterdam, 1280×800, `base_url` = de server) |
| `context_as` | test | `context_as(account, **opties)` → ingelogde context |
| `page` | test | een pagina in een nieuwe, niet ingelogde context |
| `seeded` | test | `seeded(fn)`: server stoppen → `fn(data_dir)` → starten; geeft terug wat `fn` gaf |
| `perf_data` | sessie | `PerfData(owner, folder_id, public_url, blobs, n)` voor de metingen |

**`admin`** gaat zoals de echte eerste start: `POST /auth/setup` → `POST /auth/2fa/setup` en
`/auth/2fa/enable` met een code uit `app.totp` → `PUT /admin/security {"require_2fa": false}`
→ `POST /auth/2fa/disable` met een **herstelcode** en het wachtwoord. Daarna logt de beheerder
(en elke gebruiker van `user_factory`) in zonder code.

**`context_as(account)`** logt in met `context.request.post("/api/v1/auth/login")`: het
refresh-cookie komt zo in de context, en de app start meteen ingelogd (geen TOTP).

**2FA-gevallen** (P1) gebruiken elk een eigen gebruiker: `enable_2fa(server, account)` geeft
`{"secret", "step", "recovery_codes"}`, en `totp_code(secret, after_step=step)` wacht op een
nieuwe stap van 30 s voordat hij een volgende code geeft (de server neemt dezelfde stap geen
twee keer aan).

## Bewaking

Elke context krijgt een `addInitScript` dat meeluistert naar:

- `securitypolicyviolation` (elke CSP-schending, ook van een inline script of stijl);
- `console.error`-regels die met `i18n:` beginnen (`t()` voor een gebied dat niet geladen is).

De treffers komen via een binding naar Python. Eén treffer en de test faalt bij het opruimen,
met de lijst en het adres van de pagina. Een test die er bewust een veroorzaakt, haalt ze weg
met `browsers.guard(context).take()` (zie `test_bewaking_vangt_csp_en_i18n`).

## Testdata (`seed.py`)

Schrijft rechtstreeks in de SQLite van een **gestopte** server (dus via `seeded`). Importeert
`app.models` en `app.previews.directory`, zodat de rijen en de previewmappen precies zijn wat
de app verwacht.

- `make_blobs(data_dir, n=2000) → [sha]`: n verschillende afbeeldingen (WebP; de eerste 50 van
  1024 px, de rest 256 px) als blob, met `previews/<sha[:2]>/<sha>/256.webp` (allemaal) en
  `1024.webp` (de eerste 50), `preview="ready"`, `preview_meta = {"width", "height", "sizes"}`
  en de huidige `meta_version`/`render_version` (geen backfill bij het starten). Vast per
  index: opnieuw aanroepen voegt niets dubbel toe.
- `seed_folder(data_dir, owner, n=10_000, blobs, *, name=None, parent_id=None,
  photos_only=False, step=7 min) → folder_id`: een nieuwe map met n rijen, om de beurt over de
  blobs; 5 % mappen, 50 % afbeeldingen (`foto-…webp`), 45 % andere bestanden
  (`document-…dat`); `search_name` via `app.text.fold`, `taken_at`/`taken_day` voor
  afbeeldingen zoals `photodate.stamp_values`, `ref_count` bijgewerkt. `photos_only` geeft
  alleen afbeeldingen (de tijdlijn).
- `seed_public(data_dir, owner, blobs=None) → "/s/<token>"`: een map met 200 items (10 mappen,
  50 afbeeldingen, 140 andere) en een publieke leeslink zonder wachtwoord.

`owner` is een `Account`, een gebruikers-id of een e-mailadres. 10.000 rijen plus 2.000 blobs
kosten een paar seconden; 50.000 foto's ook.

## Rooktest en thema

`test_smoke.py` doet het rondje: inloggen met het formulier → Bestanden (lege map) → een
bestand uploaden met de knop (bestandskiezer) → openen (de viewer van P0, `?bestand=` in het
adres) en sluiten → thema Donker (blijft na herladen), Licht, Systeem → uitloggen (herladen
logt niet weer in). Daarnaast: `context_as` start ingelogd, de link van `seed_public` opent
zonder inloggen met 200 items en echte thumbnails (via `seeded`), en de bewaking vangt echt
een CSP-schending en een i18n-fout.

### DOM-contract van de rooktest

`test_smoke.py` en `test_theme.py` zoeken alleen op exacte toegankelijke namen en teksten
(`exact=True`), zodat de pakketten de rest vrij kunnen veranderen. Wat blijft:

| Van | Wat precies zo blijft |
|---|---|
| P1 (inloggen) | kop "Inloggen"; velden met het label precies "E-mailadres" en "Wachtwoord" (een knop "Wachtwoord tonen" ernaast mag); knop "Inloggen" |
| P0 (schil) | navigatie "Hoofdmenu" met link "Bestanden" (`aria-current=page`); de accountknop met de naam van de gebruiker; menu "Accountmenu van <naam>" met "Thema" → `menuitemradio` "Licht", "Donker", "Systeem", en "Uitloggen" |
| P2 (bestanden) | de tekst "Deze map is leeg." in een lege map; een bestand toont zijn naam één keer als tekst in `#inhoud` (een icoon ervoor mag), en een **klik op die naam opent de viewer** (de naam is een link of knop in de rij; klikken elders in de rij mag selecteren) |
| P3 (uploaden) | een knop met precies de naam "Uploaden" die de bestandskiezer opent ("Map uploaden" ernaast mag) |
| P4 (viewer) | een dialoog met de naam van het bestand en een knop "Sluiten"; `?bestand=` in het adres zolang hij open is |
| P4 (publieke pagina) | een h1 die met "Openbaar" begint; elk item is een rij (`<tr>` of `role=row`), een kopregel heeft `columnheader` en telt niet mee |

`test_theme.py` zet `platform-theme` in localStorage (en de voorkeur van het systeem via
`color_scheme`) en kijkt met een MutationObserver uit een init-script mee:

- bij het eerste kind van `<body>` staat `data-theme` al goed, net als
  `<meta name=theme-color>`. De stylesheet is op dat moment meestal nog niet binnen (de parser
  wacht daar niet op), dus `--bg` is dan leeg, en dan is er ook nog niets getekend (geen
  paint-entries): tekenen kan pas als de stylesheet er is. Is hij er wel, dan heeft `--bg` al
  de kleur van het gekozen thema;
- in de eerste `requestAnimationFrame` (vlak vóór de eerste paint) hebben `--bg` en de
  achtergrond van `<body>` de kleur van het gekozen thema;
- `data-theme` verandert tijdens het laden niet meer (geen flits door de app).

Dat voor licht, donker, licht op een donker systeem en "systeem" (licht en donker), op `/` en
op een diepe link, plus: wisselen in het menu en herladen.

## Metingen

Beide scripts geven per pagina of weergave een status:

- `pass` / `fail`: de pagina voldoet aan het contract hieronder, en de poort is hard (behalve
  met `E2E_PERF_REPORT_ONLY=1`);
- `not yet`: het contract is er nog niet (een later pakket bouwt het); het script meet wat het
  kan en rapporteert, maar faalt niet.

Altijd hard: de bewaking (CSP, i18n), en dat het harnas zelf werkt (elke run geeft een
meting; de 10k-map laadt en er zijn frames opgenomen).

### Eerste weergave (`first_view.py`, ontwerp §4.2)

- Profiel via CDP: latency 2 ms, 100 Mbit/s op en neer, CPU 1×.
- **Koud:** per run een nieuwe context (geen service worker, geen HTTP-cache). **Warm:** in
  dezelfde context, via `about:blank`, nadat de pagina rustig is (idle, en 500 ms geen
  lopende verzoeken buiten de live stroom) en de service worker (als de app er een
  registreert) actief is.
- Gemeten: navigationStart → `performance.mark("app:ready:<wat>")`.
- Poort: mediaan van 5 < 1.500 ms en p95 (nearest-rank, bij 5 runs de hoogste) < 2.000 ms,
  koud en warm.
- Geen `navigator.serviceWorker.register` en geen `/sw.js` vóór het meetpunt (`sw_before_ready`).

| Pagina | Adres | Meetpunt (contract) | Van | Terugval zolang het meetpunt er niet is |
|---|---|---|---|---|
| `login` | `/`, uitgelogd | `app:ready:login` | P1 | het moment dat `form input[type=email]` in de DOM staat |
| `folder` | `/map/<10k>`, ingelogd | `app:ready:folder`, na de eerste rijen | P2 | `app:ready:route` (de schil, P0) |
| `public` | `/s/<link>` | `app:ready:public` | P4 | `app:ready:route` |

Het script wacht in de eerste run 3 s na de terugval op het eigen meetpunt; komt het niet, dan
zet deze build het nog niet en meldt de pagina `not yet`. Komt het in de eerste run wel en in
een latere niet, dan is de status `fail`. Wordt een pagina binnen 30 s niet eens klaar (ook
geen terugval), dan faalt de meting altijd.

### Scrollen (`scroll.py`, ontwerp §4.4)

Op `/map/<10k>` in lijst en raster, en op `/fotos` met 50.000 foto's van een eigen gebruiker;
elk bij CPU 1× en 4×. Methode: rAF-framerecorder en long-task-observer, focus op de lijst,
6 s lang elke 50 ms `mouse.wheel(0, 400)`, dan één ArrowDown, 20 × PageDown en End.

| Check | Grens |
|---|---|
| p95 frame-interval bij CPU 1× | ≤ 20 ms |
| p95 frame-interval bij CPU 4× | ≤ 34 ms |
| long tasks tijdens het scrollen | geen boven 50 ms |
| gerenderde rijen | ≤ 200 (plus 1 vastgehouden focusrij) |
| DOM-nodes | ≤ 4.000 |
| thumbnails | ≥ 100 verschillende adressen geladen en gedecodeerd |
| na wheel plus ArrowDown | `document.activeElement` zit in een rij |
| na End | de laatste rij zichtbaar en gefocust; in de lijst `aria-rowindex` ≥ 10.000 |

Contract met de pagina's (P2 voor lijst en raster, P5 voor de tijdlijn):

- een element met `aria-rowcount` en daarin rijen `[role=row]` met `aria-rowindex`
  (zonder dat is de weergave `not yet`); een kopregel bevat `[role=columnheader]` en telt niet
  mee als rij;
- de roving tab stop is een rij of cel met `tabindex="0"`; het script zet daar de focus;
- de toets **G** wisselt naar het raster, dat `[data-view="grid"]` draagt;
- thumbnails zijn `<img>`-elementen met een adres onder `/api/v1/previews/`;
- de laatste rij heeft `aria-rowindex` gelijk aan `aria-rowcount`.

### JSON

`first_view`: `{"tool", "server", "runs", "profile", "gate", "pages": {naam: {"url", "mark",
"owner", "status", "sources", "service_worker", "sw_before_ready", "cold": {"runs",
"median_ms", "p95_ms", "gate_ok"}, "warm": {…}, "details", "problems"}}, "problems", "ok"}`.

`scroll`: `{"tool", "server", "n", "blobs", "timeline_photos", "views": [{"path", "mode",
"cpu", "status", "reason", "load_s", "virtualized", "frames", "longest_task_ms", "checks":
{naam: {"value", "limit", "ok"}}, "problems"}], "problems", "ok"}`.

`e2e/perf/run_all.py` (P1) bundelt dit later in `docs/fase-3/prestaties.json` en
`prestaties.md`.

### Meting bij P0 (sandbox, oktober 2026)

Eerste weergave, 5 runs, alles `not yet` (de terugval):

| Pagina | Bron | Koud mediaan / p95 | Warm mediaan / p95 |
|---|---|---|---|
| `/` uitgelogd | DOM (inlogformulier) | 190 / 230 ms | 83 / 99 ms |
| `/map/<10k>` | `app:ready:route` | 532 / 622 ms | 403 / 424 ms |
| `/s/<link>` | `app:ready:route` | 443 / 972 ms | 343 / 352 ms |

Geen service worker (de plaatshouder van P0 registreert niets), dus ook geen `sw.js` vóór
het meetpunt. De eerste koude run van `/s/<link>` is steeds trager (~900 ms) dan de rest.

Scrollen, lijst op `/map/<10k>` (de oude tabel met 10.000 rijen, nog niet gevirtualiseerd):

| CPU | p95 frame | long tasks > 50 ms (langste) | rijen | DOM-nodes | thumbnails |
|---|---|---|---|---|---|
| 1× | 100 ms | 140 (483 ms) | 10.000 | 169.076 | 744 |
| 4× | 2.483 ms | 235 (2.429 ms) | 10.000 | 169.076 | 688 |

Raster (geen `[data-view=grid]`) en tijdlijn (`/fotos` is nog een plaatshouder, geen rijen)
zijn `not yet`. Na ArrowDown en End staat de focus op `<body>`: de tabel heeft geen roving
focus.

Om de harde kant van `scroll.py` te controleren, draaide `measure_view` ook eenmalig tegen
een synthetische gevirtualiseerde lijst die aan het contract voldoet (window-scroll, 44 px
rijen, overscan 10, de focusrij blijft gemount): status `pass`, p95 frame 16,7 ms bij 1× en
33,3 ms bij 4×, 41 rijen, 130 DOM-nodes, focus na ArrowDown op rij 2 en na End op rij 10.000.
Bij 4× levert headless Chromium dus zelfs voor een bijna lege pagina frames van ~33 ms: de
grens van 34 ms laat weinig ruimte.

## Gevonden bij het bouwen

De bewaking ving meteen een echte fout: op `/s/<link>` vertaalde `AfterNavigation` in
`App.tsx` de paginatitel tijdens het renderen, terwijl het gebied `public` nog laadde (de
component rendert naast de pagina die suspendt). De titel wordt nu pas in het effect vertaald,
dat loopt als de pagina er staat.
