# Prestaties fase 3

Gemaakt door `e2e/perf/run_all.py` (P1); niet met de hand bewerken, behalve het blok
met de DevTools-runs onderaan. Alle getallen: `prestaties.json`.

- Gemeten: 2026-10-26T13:19:18+00:00
- Commit: `25eadb9`
- Alleen rapporteren (E2E_PERF_REPORT_ONLY): nee
- Data: 10000 bestanden over 2000 thumbnails, 50000 foto's

## Lighthouse-achtig (§4.3)

Poort: score ≥ 91, mediaan van 5.

| Pagina | Profiel | Van | Status | Score | Runs | FCP | SI | LCP | TBT | CLS |
|---|---|---|---|---|---|---|---|---|---|---|
| login (`/`) | mobile | P1 | pass | 97 | 97, 96, 98, 96, 97 | 1336 ms | 3929 ms | 1336 ms | 122 ms | 0.0 |
| public (`/s/Q0CpFYzkoeiY7wias…`) | mobile | P1 | pass | 95 | 96, 96, 95, 93, 95 | 1348 ms | 4359 ms | 1348 ms | 165 ms | 0.0 |
| folder (`/map/654a8f2e-fa5c-4…`) | desktop | P2 | pass | 100 | 100, 100, 100, 100, 100 | 152 ms | 829 ms | 152 ms | 0.0 ms | 0.006 |

## Eerste weergave (§4.2)

Poort: mediaan < 1500 ms en p95 < 2000 ms, koud en warm.

| Pagina | Van | Status | Koud mediaan / p95 | Warm mediaan / p95 | Bron | SW |
|---|---|---|---|---|---|---|
| login | P1 | pass | 244 / 344 ms | 137 / 147 ms | mark:login | ja |
| folder | P2 | pass | 628 / 678 ms | 502 / 528 ms | mark:folder | ja |
| public | P4 | pass | 573 / 591 ms | 491 / 615 ms | mark:public | ja |

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
Ingevuld door de integrator op 26-10-2026. De tabellen hierboven komen van de laatste van drie
runs op `25eadb9` (schoon; zie "Slotcontrole op `25eadb9`" onderaan). De getallen van "Machine"
tot en met "Wat er veranderde", de echte Lighthouse en de herijking horen bij de run op commit
`0661462` (schoon). Daarna volgen de slotcontrole van de integratie (`c6221f0`) en wat er bij
4× CPU en de koude eerste weergave nog is uitgezocht (`ac4914d`, `25eadb9`).

### Machine

Sandbox: 4 vCPU Intel Xeon @ 2,80 GHz (Firecracker-VM, kernel 6.18.44), 15 GB RAM. Python
3.12.13 (de `.venv` van `api`, ook voor de server), Node 22.22.0, Playwright 1.64.1 met
Chromium 141.0.7390.37 uit `/opt/pw-browsers`.
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

- **lijst 4×** op `0661462`: 46 van 61 runs (75 %) zonder één taak boven 50 ms en met p95 ≤
  34 ms (later, met de lijst van `c7012d5`, 3 tot 8 van 20: zie "Scrollen bij 4× nader
  bekeken"; `5ed8da5` en `c7012d5` afwisselend, 5 runs elk, gaven 4 en 3 van 5); mislukt is
  bijna altijd één taak van 51 tot 62 ms (een paar keer in een drukke periode 66 tot 119 ms).
  Op `1d0e4c9` (vóór de fix): 1 van 9, met 3 tot 9 lange taken tot 134 ms en p95 50 ms;
- raster 1× en 4×: 7 van 7; lijst 1×: 6 van 7 (één uitschieter met een taak van 142 ms);
- volledige `run_all.py`: 3 van 3 alles goed (op `8321db6` en twee keer op `0661462`); later 1
  van 3 op `c6221f0` en 2 van 3 op `25eadb9` (de slotcontroles hieronder).

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

Open: de lijst bij 4× slaagt niet altijd. Hoe vaak wel, hangt sterk af van hoe druk de VM is
(zie Scrollen). Wat er in de mislukte runs zit, staat onder "Scrollen bij 4× nader bekeken".

### Slotcontrole bij het afronden van de integratie (`c6221f0`)

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
  serverlog van die run is weg (tijdelijke map); de oorzaak is niet gevonden. Of het aan
  achtergrondtaken lag, staat verderop.
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

### Scrollen bij 4× nader bekeken (`ac4914d`)

Waarom slaagden lijst en tijdlijn bij 4× niet altijd? Een harnas in de scratchpad (niet in het
repo) roept `scroll.measure_view` ongewijzigd aan: dezelfde invoer, dezelfde drempels, 4× CPU.
Per run neemt het een CDP-trace op en leest het `/proc/stat` (steal). Builds draaien
afwisselend tegen een eigen server met dezelfde data als `run_all.py`. Elke lange taak uit de
PerformanceObserver is gekoppeld aan die taak in de trace. Een trace kost zelf ook CPU: met
trace slaagde de lijst bij 4× maar 1 van 18 keer. De traces laten dus zien wat er in de taken
zit. Hoe vaak een run slaagt, staat in de tabel "Voor en na" (zonder trace).

**De lijst.** In 18 getracete runs van de lijst met de code van `c7012d5` (17 mislukt, 9 met
een lange taak) zaten 14 lange taken.

- In 9 ervan had de hoofdthread de hele taak de CPU (tdur/dur ≥ 0,95). Het werk daarin, in
  zelftijd: lay-out 9–26 ms, tekenen (PrePaint, Paint, Layerize) 12–29 ms, stijl 1–9 ms,
  script 0–18 ms. Er zat geen garbage collection in.
- In de andere 5 stond de thread 20–92 % van de taak stil. Met alleen de CPU-tijd was elk
  van die 5 onder de 50 ms gebleven. Dat komt niet van de host: steal was in die runs
  1,0–3,2 %. Het is het gedrang op de 4 vCPU's in de VM (de server, de compositor en de
  driver draaien mee).
- Tijdens het wielen is de hoofdthread ongeveer 75 % van de tijd bezig. Een frame met nieuwe
  rijen kost gemiddeld 23 ms: lay-out 7,0, taak 4,7, Paint 3,4, Layerize 3,3, PrePaint 2,4 en
  stijl 1,6. Een frame zonder nieuwe rijen kost 10 ms. React kost samen zo'n 225 ms per
  seconde.
- De lay-out van dezelfde ~78 vuile objecten varieert van 6,7 ms (p50) via 11,5 (p90) tot 30
  ms (max), ook als de thread al die tijd CPU heeft. Daardoor schiet een deel van de frames
  naar drie vsyncs (de p95 van 50 ms).
- Wat die lay-out kost, is vooral de tekst van de nieuwe rijen. Een proefbuild met vaste
  teksten (elke rij dezelfde naam, grootte en datum) slaagde 3 van 3 keer, met 56 ms per
  seconde minder lay-out. Alleen de namen gelijk was nog 0 van 3.
- Elk tegen dezelfde basis geprobeerd, en niets was meetbaar beter:
  - een budget van 6 in plaats van 10 rijen per frame;
  - geen clip, geen masker en geen afgeronde hoeken;
  - `contain: strict` per rij;
  - spans in plaats van knoppen, wat het tekenen juist duurder maakte;
  - `text-rendering: optimizeSpeed`.
- Daarnaast zijn er lege taken (`PostTaskToMainThread` zonder app-code erin, 1,2–1,6 ms
  elk). Samen 48 ms per seconde in het wielen.

Conclusie voor de lijst: het meeste is werk van de app, namelijk het opmaken en tekenen van
nieuwe rijen met hun tekst. Dat zit bij 4× precies op de rand van het budget. De rest is
gedrang in de VM. Die tekst weglaten kan niet, en de kleine wijzigingen hielpen niet. De lijst
is daarom niet veranderd.

**De tijdlijn.** Daar zaten twee oorzaken in de app (opgelost in `ac4914d`):

- `.photo-grid[data-scrolling] { pointer-events: none }` erft naar elke cel. Elke wissel gaf
  daardoor een stijlberekening voor alle cellen (172–325 elementen, 5–14 ms), vaak in het
  eerste wielframe. Met 150 ms wisselde het bij 4× ook midden in het scrollen. Nu ligt er een
  leeg vlak over de cellen, net als in de lijst, en reageert het raster pas na 250 ms weer op
  de muis.
- ArrowDown na het wielen springt terug naar de foto met de focus. Dat tekende een heel
  scherm nieuwe cellen plus de marge in één frame: 302 van de 393 objecten vuil, 62 ms. Nu
  komt eerst wat in beeld is en een frame later de marge (`firstStep` en `nextStep` in
  `photos/rows.ts`).

De resterende lange taken komen vooral bij PageDown. Een voorbeeld van 53 ms (tdur = dur):
stijl 4,6, lay-out 11,1, PrePaint 4,7, de focus in een rAF 6,9, Paint 6,2 en Layerize 5,0. Dat
is ook werk van de app. De focus in dezelfde taak zetten (variant `tlf`) maakte geen verschil.

**Voor en na**, 20 runs per build per weergave, afwisselend op dezelfde machine, zonder trace
(steal 0,9–2,3 %):

| Build | Lijst 4× | Tijdlijn 4× | Lange taken tijdlijn (wiel / ArrowDown / PageDown / End) |
|---|---|---|---|
| `c7012d5` (voor) | 8 van 20 | 6 van 20 | 14 / 7 / 4 / 0 |
| `ac4914d` (na) | 3 van 20 | **13 van 20** | 4 / 0 / 5 / 0 |
| na, plus de focus in dezelfde taak (`tlf`, niet gehouden) | 6 van 20 | 13 van 20 | 4 / 0 / 3 / 1 |

De lijstcode is in alle drie gelijk. 8, 3 en 6 van 20 laten dus zien hoe groot de spreiding
is. Vooraf, in een run met alleen `c7012d5`: lijst 4 en tijdlijn 8 van 20.

**Het doel van 18 van 20 is niet gehaald.** De lijst haalt 3–8 en de tijdlijn 13 van 20. Wat
overblijft is niet aantoonbaar iets buiten de app. Het grootste deel van de lange taken is
lay-out en tekenen terwijl de thread CPU heeft. Een kleiner deel valt alleen door het gedrang
in de VM boven 50 ms. De drempels, de vertraging en de meting zelf zijn niet veranderd.

### De koude eerste weergave van `/map/<10k>` en de achtergrondtaken (`25eadb9`)

Was de koude weergave van 8,5 s in de slotcontrole van `c6221f0` het gevolg van
achtergrondtaken die het kritieke pad ophouden? Dat kan via het schrijfslot van SQLite
(`busy_timeout` 5000) of via een volle event loop. Zo nagespeeld:

- dezelfde data als `run_all.py`;
- `meta_version` en `render_version` van alle blobs op 0 en de server herstarten. Dan lopen
  de backfill van de fotometadata en het opnieuw maken van de previews, zoals na een update;
- 2 rondes van 20 koude eerste weergaven (`first_view.measure_once`), met de API-verzoeken
  uit resource timing;
- een probe die elke 200 ms `GET /health` en `GET /api/v1/auth/me` doet (alleen lezen).

| | Tijdens de backfill | Daarna |
|---|---|---|
| `c7012d5`: eerste weergave (mediaan, min–max) | 1.046 ms (659–1.277), 12 runs | 658 ms (603–717), 28 runs |
| `c7012d5`: `/auth/refresh` | mediaan 545 ms, max 752 | mediaan 152 ms |
| `25eadb9`: eerste weergave | 740 ms (645–798), 8 runs | 652 ms (596–840), 32 runs |
| `25eadb9`: `/auth/refresh` | mediaan 211 ms, max 243 | mediaan 156 ms |
| backfill klaar na | `c7012d5` 10–12 s, `25eadb9` 6 s | |

De lezende verzoeken van de probe werden tijdens de backfill niet trager (`/health` p50
3,5–4,1 ms, `/auth/me` p50 39–55 ms). De event loop zat dus niet vol. De oorzaak was het
schrijfslot. SQLite heeft één schrijver, en een stuk van de backfill (200 blobs) las en
schreef per blob. Na de eerste UPDATE hield die transactie het slot vast over alle 200
rondes. `/auth/refresh` schrijft de nieuwe refresh-token en moest daarop wachten. In
`25eadb9` leest een stuk eerst alles. Daarna schrijft het in twee executemany's
(`photodate.set_blob_meta` en `retime_nodes`). `test_een_stuk_schrijft_kort` telt de
statements tussen de eerste schrijfactie en de COMMIT: nu 2, zonder de fix 71.

Ter controle, zoals in `run_all.py`: alleen herstarten na het vullen. Dan staat er geen enkele
taak in de wachtrij (alle tellers 0), en 40 koude runs gaven 618–811 ms. In `run_all.py` lopen
dus geen achtergrondtaken. De vertraging hierboven is echt en opgelost, maar ze is hooguit
0,6 s. Ze verklaart de 8,5 s niet. Die uitschieter kwam niet terug: niet in de 120 koude runs
van deze proef (hooguit 1.277 ms) en niet in de drie `run_all.py` hieronder. Er is geen
serverlog van, dus de oorzaak blijft onbekend.

### Slotcontrole op `25eadb9`

Na `ac4914d` (de tijdlijn) en `25eadb9` (de backfill) zijn er drie `run_all.py` na elkaar
gedraaid op `25eadb9`, schoon en niet alleen-rapporteren. Er draaide niets anders. De eerste
twee schreven naar een map in de scratchpad, de derde naar dit document:

| Klaar om (UTC) | Eerste weergave (`/map/<10k>` koud, mediaan / p95 van 5) | Scrollen | lighthouse_like (/, /s, /map) |
|---|---|---|---|
| 12:58 | goed (688 / 715 ms) | goed | 97, 94, 100 |
| 13:08 | goed (676 / 725 ms) | **fout**: lijst 4× p95 50 ms en één taak van 51 ms | 98, 94, 100 |
| 13:19 | goed (628 / 678 ms) | goed | 97, 95, 100 (de tabellen hierboven) |

Twee van de drie haalden alle poorten. De tweede haalde de lijst bij 4× niet, zoals hierboven
beschreven. Raster en tijdlijn waren bij 1× en 4× alle drie keer goed. Geen van de 15 koude
eerste weergaven van `/map/<10k>` kwam boven 725 ms. Het groottebudget (`npm run budget`) op
deze code: JS 93,8, CSS 3,4, route Bestanden 39,4, grootste lazy chunk 9,2 en sw.js 2,4 KB,
alles binnen de grens.

De echte Lighthouse is niet opnieuw gedraaid. Sinds `0661462` veranderde in de web-app vooral
wat er na het laden gebeurt (focus en toetsen, uploads, delen, de viewer, previews na een andere
volgorde, de tijdlijn); de refresh in `preload.ts` doet online hetzelfde als eerst.
lighthouse_like gaf hier 97–98, 94–95 en 100, net als op `0661462` (97, 95, 100).
<!-- /handmatig -->
