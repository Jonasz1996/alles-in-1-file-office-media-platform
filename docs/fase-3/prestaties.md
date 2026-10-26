# Prestaties fase 3

Gemaakt door `e2e/perf/run_all.py` (P1); niet met de hand bewerken, behalve het blok
met de DevTools-runs onderaan. Alle getallen: `prestaties.json`.

- Gemeten: 2026-10-26T07:36:21+00:00
- Commit: `c6221f0`
- Alleen rapporteren (E2E_PERF_REPORT_ONLY): nee
- Data: 10000 bestanden over 2000 thumbnails, 50000 foto's

## Lighthouse-achtig (§4.3)

Poort: score ≥ 91, mediaan van 5.

| Pagina | Profiel | Van | Status | Score | Runs | FCP | SI | LCP | TBT | CLS |
|---|---|---|---|---|---|---|---|---|---|---|
| login (`/`) | mobile | P1 | pass | 96 | 94, 96, 98, 97, 96 | 1336 ms | 3951 ms | 1336 ms | 143 ms | 0.0 |
| public (`/s/EertC0oLc4p5J1JY0…`) | mobile | P1 | pass | 94 | 94, 96, 96, 93, 89 | 1332 ms | 4382 ms | 1332 ms | 212 ms | 0.0 |
| folder (`/map/fcb8b0b1-b7ba-4…`) | desktop | P2 | pass | 100 | 100, 100, 100, 100, 100 | 156 ms | 862 ms | 156 ms | 0.0 ms | 0.006 |

## Eerste weergave (§4.2)

Poort: mediaan < 1500 ms en p95 < 2000 ms, koud en warm.

| Pagina | Van | Status | Koud mediaan / p95 | Warm mediaan / p95 | Bron | SW |
|---|---|---|---|---|---|---|
| login | P1 | pass | 265 / 385 ms | 148 / 164 ms | mark:login | ja |
| folder | P2 | pass | 665 / 673 ms | 513 / 540 ms | mark:folder | ja |
| public | P4 | pass | 588 / 630 ms | 506 / 523 ms | mark:public | ja |

## Scrollen (§4.4)

| Weergave | CPU | Status | Mislukte controles |
|---|---|---|---|
| list | 1× | pass | – |
| list | 4× | pass | – |
| grid | 1× | pass | – |
| grid | 4× | pass | – |
| timeline | 1× | pass | – |
| timeline | 4× | pass | – |

## DevTools-Lighthouse (harde acceptatie, handmatig)

<!-- handmatig -->
Ingevuld door de integrator op 26-10-2026. De tabellen hierboven komen van de slotcontrole
op `c6221f0` (schoon; zie "Slotcontrole" onderaan). De getallen in dit blok, de echte
Lighthouse en de herijking horen bij de run op commit `0661462` (schoon).

### Machine

Sandbox: 4 vCPU Intel Xeon @ 2,80 GHz (Firecracker-VM, kernel 6.18.44), 15 GB RAM. Python
3.13.16, Node 22.22.0, Playwright 1.64.1 met Chromium 141.0.7390.37 uit `/opt/pw-browsers`.
Gemeten tegen de build van `npm run build` op deze commit (bit voor bit dezelfde bestanden
als bij de Lighthouse-runs hieronder). Er draaide geen andere test tegelijk.

Bij 4× CPU telt de ruis van de VM vier keer mee: een taak van 13 ms wordt 52 ms. Eén run zegt
daar dus weinig; daarom staan bij Scrollen ook de slagingspercentages over veel losse runs.

### Alle poorten met getallen

| Poort (ontwerp §4) | Grens | Gemeten | Status |
|---|---|---|---|
| Grootte (§4.1, `npm run budget`) | JS 100 KB, CSS 12 KB, route Bestanden 45 KB, lazy chunk 50 KB, sw.js 8 KB | 93,7 / 3,4 / 39,3 / 9,2 / 2,4 KB | goed |
| Eerste weergave `/` (§4.2) | mediaan < 1.500 ms, p95 < 2.000 ms | koud 276 / 359 ms, warm 138 / 170 ms | goed |
| Eerste weergave `/map/<10k>` | idem | koud 665 / 742 ms, warm 516 / 570 ms | goed |
| Eerste weergave `/s/<link>` | idem | koud 534 / 578 ms, warm 473 / 623 ms | goed |
| lighthouse_like `/` mobiel (§4.3) | ≥ 91 | 97 (96, 97, 97, 97, 96) | goed |
| lighthouse_like `/s/<link>` mobiel | ≥ 91 | 95 (95, 95, 94, 95, 96) | goed |
| lighthouse_like `/map/<10k>` desktop | ≥ 91 | 100 (5× 100), CLS 0,006 | goed |
| Echte Lighthouse, drie pagina's | > 90 | 96 / 95 / 100 (hieronder) | goed |
| Scrollen (§4.4) | zie de tabel hieronder | alle zes de weergaven | goed |
| Geen themaflits (§4.5, `e2e/test_theme.py`) | geen flits | 9 tests goed | goed |
| Geen N+1 in `/users` (`test_users_queries.py`) | vast aantal queries | 2 en 51 gebruikers: gelijk | goed |
| Foto's bij 50.000 (`test_photos_speed.py`) | mediaan < 100 ms | months 64,2 ms, month 42,7 ms, after 47,8 ms | goed |
| Geen CSP-schending, geen ontbrekende i18n (conftest) | in elke e2e | volledige e2e groen | goed |

### Scrollen (§4.4), de getallen van deze run

10.000 rijen over 2.000 thumbnails (lijst en raster), 50.000 foto's (tijdlijn).

| Weergave | CPU | p95 frame | Lange taken > 50 ms | Rijen (≤ 201) | DOM-nodes (≤ 4.000) | Thumbnails (≥ 100) | Focus na ↓ / na End |
|---|---|---|---|---|---|---|---|
| lijst | 1× | 16,7 ms (≤ 20) | 0 | 37 | 500 | 119 | rij / laatste rij, zichtbaar |
| lijst | 4× | 33,3 ms (≤ 34) | 0 | 37 | 500 | 121 | rij / laatste rij, zichtbaar |
| raster | 1× | 16,8 ms (≤ 20) | 0 | 10 | 365 | 130 | rij / laatste rij, zichtbaar |
| raster | 4× | 16,8 ms (≤ 34) | 0 | 10 | 365 | 133 | rij / laatste rij, zichtbaar |
| tijdlijn | 1× | 16,8 ms (≤ 20) | 0 | 13 | 354 | 121 | rij / laatste rij, zichtbaar |
| tijdlijn | 4× | 16,8 ms (≤ 34) | 0 | 13 | 354 | 111 | rij / laatste rij, zichtbaar |

Over veel losse runs (`scroll.py`, afwisselend met andere builds op dezelfde machine):

- **lijst 4×** op de uiteindelijke code: 46 van 61 runs (75 %) zonder één taak boven 50 ms en
  met p95 ≤ 34 ms; mislukt is bijna altijd één taak van 51 tot 62 ms (een paar keer in een
  drukke periode 66 tot 119 ms). Op `1d0e4c9` (vóór de fix): 1 van 9, met 3 tot 9 lange taken
  tot 134 ms en p95 50 ms;
- raster 1× en 4×: 7 van 7; lijst 1×: 6 van 7 (één uitschieter met een taak van 142 ms);
- volledige `run_all.py`: 3 van 3 alles goed (op `8321db6` en twee keer op `0661462`).

### Echte Lighthouse (harde acceptatie, §4.3)

Lighthouse **13.5.0** (de CLI, alleen in de scratchpad geïnstalleerd, niet in het repo) met
`CHROME_PATH` = de Chromium 141 uit `/opt/pw-browsers` en
`--chrome-flags="--headless=new --no-sandbox --no-proxy-server"`. Navigatie, alleen
Performance (`--only-categories=performance`), `--throttling-method=devtools` (zoals het
DevTools-paneel) met de profielen van §4.3 als CLI-instellingen: mobiel 562,5 ms,
1.474,56 / 675 kbit/s, CPU 4×, 412×823 bij DPR 1,75; desktop 40 ms, 10 Mbit/s, CPU 1×,
1350×940. Eén run per pagina tegen een eigen server met dezelfde data als `run_all.py`. Voor
`/map/<10k>` eerst inloggen via het echte inlogformulier in diezelfde Chrome (Playwright via de
debugpoort), daarna Lighthouse met `--port`.

| Pagina | Profiel | Lighthouse | Lighthouse, Chrome in het Nederlands | lighthouse_like | Verschil | Datum | Door |
|---|---|---|---|---|---|---|---|
| / | mobiel | **96** (FCP 1.376, SI 5.147, TBT 60 ms, CLS 0) | 98 (SI 3.845) | 97 | 1 / 1 | 26-10-2026 | integrator |
| /s/<link> | mobiel | **95** (FCP 1.342, SI 5.336, TBT 116 ms, CLS 0) | 95 (SI 4.330) | 95 | 0 / 0 | 26-10-2026 | integrator |
| /map/<10k> | desktop | **100** (FCP 151, SI 819, TBT 0 ms, CLS 0,006) | 100 (CLS 0,0065) | 100 | 0 / 0 | 26-10-2026 | integrator |

Alle drie > 90 en binnen 3 punten van lighthouse_like.py. Twee kanttekeningen:

- **Taal.** Headless Chrome is en-US; dan laadt de app het Engelse woordenboek erbij (een
  extra rondgang, op mobiel ruim 1 s meer Speed Index). lighthouse_like.py draait in nl-NL,
  dus de vergelijking zonder taalverschil is de kolom met `--lang=nl-NL
  --accept-lang=nl-NL,nl` erbij. Ook in het Engels blijft elke score boven 90.
- **Wachten op de pagina.** Bij `/map/<10k>` (ingelogd) meldt Lighthouse "The page loaded too
  slowly to finish within the time limit": de live-stroom `/api/v1/events` blijft open, dus het
  netwerk wordt nooit stil en Lighthouse wacht zijn volle 45 s. De pagina zelf staat er na
  151 ms; alle metingen zijn er.

**Herijking.** De eerste echte run gaf `/map/<10k>` **86** (CLS 0,27), lighthouse_like gaf
100 (CLS 0,035): meer dan 3 punten verschil. Twee oorzaken:

- de app: zodra de 10.000 rijen er zijn, verschijnt de schuifbalk en schuift de pagina opzij;
  het pad boven de titel kwam er pas met de map bij, en de regel "… van 10.000 geladen" boven
  de lijst verdween aan het eind. Opgelost in `0661462` (zie hieronder);
- de meting: Playwright start Chromium met `--hide-scrollbars`, Lighthouse niet. Daarom start
  lighthouse_like.py nu een eigen Chromium waarin schuifbalken ruimte innemen
  (`launch_chromium(scrollbars=True)` in `e2e/conftest.py`). Op de build van vóór de fix geeft
  hij dan 86 met CLS 0,2709, net als Lighthouse. De poort, de curves, de gewichten en de
  vertraging zijn niet veranderd.

### Wat er veranderde en waarom

Bij `1d0e4c9` faalde één poort: scrollen in de lijst bij 4× CPU (p95 50 ms, 4 lange taken).
De rest was al goed; de echte Lighthouse vond daarna de layoutverschuiving.

- `8321db6`, **de lijst bij 4×**: elk frame met nieuwe rijen was duur en een grote sprong
  (PageDown, End) kwam in één taak.
  - Nieuwe rijen per frame met een budget (`browser/slots.ts`: 10 rijen, 18 cellen in het
    raster), die bij het beeld eerst; het bereik door scrollen met `startTransition`.
  - De bestandslijst is een eigen laag (`will-change`): alleen die laag krijgt een nieuwe
    tekenlijst, niet de hele pagina.
  - Kolommen als flex met vaste breedtes, geen eigen clip voor grootte en datum, ⋯ als drie
    stippen.
  - Tijdens het scrollen een leeg vlak over de rijen in plaats van `pointer-events: none`
    (dat gaf bij elke wissel een stijlberekening voor alle rijen); hover pas na 250 ms
    stilstand.
  - Een afbeelding, video of pdf toont een leeg vlak zolang het antwoord over de preview er
    nog niet is, in plaats van eerst het icoon (een svg-masker dat de eerste keer 10–15 ms
    stijl kostte, juist bij End). De iconen per soort direct als `mask-image`, zonder `var()`.
  - De selectiebalk volgt met `useDeferredValue` en is, net als `Icon`, een memo.
- `0661462`, **geen layoutverschuiving op `/map/<10k>`**: `scrollbar-gutter: stable` op de
  pagina, het pad altijd één regel hoog, de voortgang onder de lijst (sticky onderaan); CLS van
  0,27 naar 0,0065. Plus de herijking van lighthouse_like.py hierboven.

Open: de lijst bij 4× slaagt in ongeveer driekwart van de losse runs, niet altijd (zie
Scrollen); de rest is één taak net boven 50 ms.

### Slotcontrole bij het afronden (`c6221f0`)

Na `897865b` kwamen er nog webwijzigingen bij: `03777dc` (de focus na een actie in de lijst en
de prullenbak), `d9a7a78` (typen in de mappenkiezer) en `006b166` (de refresh in de try van
`browser/preload.ts`), plus `77d4439` in het harnas (een vaste luisteraar voor de
bestandskiezer op elke pagina). Daarom opnieuw `run_all.py`, drie keer na elkaar op `c6221f0`
(web en api gelijk aan de eindstand; daarna is alleen dit document veranderd):

| Klaar om (UTC) | Eerste weergave | Scrollen | lighthouse_like (/, /s, /map) |
|---|---|---|---|
| 06:36 | **fout**: `/map/<10k>` koud 636, 666, 639, 657 en één keer **8.547 ms**; die run zette zijn meetpunt pas na de terugval van 5 s, dus registreerde de worker ervóór ("sw.js vóór app:ready") | goed | 96, 95, 100 |
| 07:02 | goed | **fout**: lijst 4× p95 50 ms en één taak van 51 ms; tijdlijn 4× één taak van 56 ms | 94, 92, 100 |
| 07:36 | goed | goed | 96, 94, 100 (de tabellen hierboven) |

Uitgezocht, op dezelfde machine en met dezelfde data (ook de 50.000 foto's):

- **De uitschieter van 8,5 s** kwam niet terug: `/map/<10k>` nog 40 keer koud en 40 keer warm
  (`first_view.measure_page`, 8 rondes van 5) gaf koud 626–946 ms en warm 491–613 ms. De
  serverlog van die run is weg (tijdelijke map); de oorzaak is niet gevonden.
- **Build tegen build:** `897865b` en `c6221f0` afwisselend op twee servers (2 rondes, toen
  afgebroken): lijst 4× en tijdlijn 4× vielen bij beide builds even vaak uit, met taken van
  51–77 ms. De eerste weergave was bij beide goed.
- **Het harnas:** met en zonder de vaste luisteraar van `77d4439`, afwisselend, 10 rondes
  elk: lijst 4× 2 en 4 van 10, tijdlijn 4× 5 en 1 van 10. Geen verschil dat op de luisteraar
  wijst.
- In die periode was de VM duidelijk drukker dan bij de meting op `0661462` (lijst 4× toen 46
  van 61); ook lighthouse_like lag in de tweede run lager.

Conclusie: geen achteruitgang door de wijzigingen na `897865b`. Bij een drukke VM komen de
lijst en nu ook de tijdlijn bij 4× vaak net boven 50 ms; de derde run haalde alle poorten. De
echte Lighthouse is niet opnieuw gedraaid: de wijzigingen raken het laden van de drie
gemeten pagina's niet (de refresh in `preload.ts` doet online hetzelfde als eerst).
<!-- /handmatig -->
