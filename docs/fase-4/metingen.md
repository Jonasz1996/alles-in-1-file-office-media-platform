# Metingen fase 4

De metingen van §4.7 van het [bouwontwerp](bouwontwerp.md), in de sandbox, bij de integratie.
**Deel 1**: openen, de cpu en het geheugen van de app, opslaan, de invoercontrole en het
groottebudget. **Deel 2**: de XML-grens bij 2 GB, conversies, veel bewerkers tegelijk, stoppen en
een kill van DS, en de keuze van `max_editors`.

Het blok tussen `<!-- gemeten -->` en `<!-- /gemeten -->` schrijft `e2e/perf/office_run.py`;
alle getallen staan in [metingen.json](metingen.json). De rest is handwerk. Opnieuw meten: zie
"De metingen (§4.7)" in [e2e.md](e2e.md).

## Kort

Deel 1:

- **Gehaald:** koud openen via de app (2,6–3,0 s, doel 5 s), de cpu van de app bij koud openen
  (0,87–0,94 core-seconde, doel 1; krap bij docx), het geheugen met 50 tunnels (+14,8 MB, doel
  20 MB), opslaan (0,09–0,11 s van callback tot versie, doel 2 s), de invoercontrole (0,015 en
  0,29 s cpu, doel 1 s), de eerste lading (+0,37 KB, doel 0,5 KB) en het chunk van de editor
  (3,9 KB, doel < 50 KB).
- **Niet gehaald:** warm openen (2,3–2,5 s, doel 2 s) en de groei van de route Bestanden
  (+2,94 KB, doel < 1 KB).
- **Wat de metingen verder lieten zien:** de presentatie-editor kreeg via de app de lijst met
  ingebouwde thema's niet (404 op `themes//themes.js`; bij de afronding hersteld, o1.md afwijking
  19), elke opening via een link wacht ±0,3 s
  op React, en de fout A22 (de pptx-editor komt niet klaar) zit in DS zelf. Zie
  [Bevindingen](#bevindingen).

Deel 2:

- **Gehaald:** conversie klein (1,1 s) en van 401 pagina's (10,2 s, doel 60 s); 401 pagina's
  omzetten terwijl iemand opent (openen 7,6–8,1 s, doel 10 s, `/healthcheck` steeds `true`);
  20 bewerkers (5 × 4) 60 s typen: alles wat de editors hadden, is opgeslagen, en DS gebruikte
  daarbij 0,05 core en +120 MB van zijn 2 cpu's en 2 GB; stoppen met open editors (alles
  opgeslagen, `docker stop` 30,5 s, doel 60 s); een kill van DS (weg is wat na het laatste
  tussentijds opslaan getypt is, plus ±2 s; gemeten tot 287 s). De grens zelf is niet 5 maar
  ±5,5 minuten: de klok slaat op bij de eerste ronde (elke 30 s) na 5 minuten; het slechtste
  geval is niet gemeten (zie [Kill van DS](#kill-van-ds-met-open-editors-ja)).
- **Niet gehaald:** de XML-grens bij 2 GB voor een docx met heel veel pagina's: bij 96.937 lege
  pagina's stopte de kernel x2t 3 van de 3 keer (OOM, 1,72 GB); docservice bleef wel. x2t kan er
  bij 2 GB ±75.000 aan; de grens van 4 MiB XML laat er ±97.000 toe.
- **`max_editors`:** de standaard van 20 per document kan blijven; zie
  [max_editors](#max_editors-keuze-27).
- **Wat deel 2 verder liet zien:** een docx met veel opmaak net onder de grens opent niet in de
  editor (bevinding 4); één trage `/healthcheck` zet DS 30 s op down (5); met veel bewerkers op
  een volle machine vallen soms de eerste woorden weg in de editor zelf (6); en na een kill kwam DS
  één keer niet meer op door een oud pid-bestand van supervisord (7; bij de afronding hersteld,
  o6.md afwijking 24).

## Omstandigheden

- **Machine:** de sandbox, een Firecracker-VM met 4 vCPU (Intel Xeon @ 2,10 GHz), 15,7 GiB RAM,
  kernel 6.18.44, geen swap. Python 3.12.13 (de `.venv` van `api`), Playwright met Chromium
  141.0.7390.37 uit `/opt/pw-browsers`, headless. CI draait Chrome for Testing 156; dat is hier
  niet te halen, dus deze getallen zijn van Chromium 141.
- **DS:** OnlyOffice Docs Community 10.0.0.1 op digest `ca17889f…` (het image van
  mirror.gcr.io), een eigen container `meet-ds` via `e2e/office_ds.sh` met
  `infra/single/office/local-production-linux.json`. De afscherming van Keuze 29 en de grenzen
  van `compose.office-limits.yaml` zijn toegepast: `docker info` gaf `MemoryLimit` en
  `CPUCfsQuota` true, en de container had 2 GiB geheugen (ook als swapgrens), 2 cpu's en
  `pids_limit` 512 (`docker inspect`). Gemeten na de font-herstart (189 s na de start).
- **De app:** per meting een eigen server (`python -m app.serve`, de productie-build van
  `web/dist` op `e2c47d9`), met de timers van productie: de ronde en de gezondheid elke 30 s,
  het herstel elke 10 minuten (niet die van de e2e, elke 1 of 2 s). DS bereikt de interne
  luisteraar als `host.docker.internal`; DS hangt aan de standaardbridge, zoals in de job
  `office` (§4.5).
- **Rust:** er draaide niets anders van deze run. Vóór elke meting keek ik met `ps` en `top`;
  alleen dockerd, containerd, DS en het proces van de sessie zelf. Elk script dat tijd of cpu meet,
  wacht vóór elke herhaling tot de hele machine (DS meegeteld) onder 0,3 core zit over 3 s
  (`wait_quiet`): in de uitslag hieronder was dat hooguit 0,12 core (60 keer bij openen, 6 bij de
  tunnels, 2 bij de invoercontrole; opslaan wacht ook, maar legt het niet vast). Tijdens de tweede
  set (de tabel) deed de sessie zelf niets; zie [Herhaling](#herhaling) voor de eerste set.
- **Deel 2** (28 oktober, 01:10–04:00 UTC): dezelfde machine en dezelfde container `meet-ds`,
  met dezelfde grenzen (`docker inspect`: `Memory` en `MemorySwap` 2147483648, `NanoCpus` 2e9,
  `PidsLimit` 512; `docker info`: cgroup v1, cgroupfs, `MemoryLimit` en `CPUCfsQuota` true). De
  OOM-kills van x2t in `dmesg` tonen dat de grens echt gold. De app op dezelfde code als deel 1:
  sinds `e2c47d9` is er niets veranderd in `api/`, `web/` of `infra/`, alleen in `e2e/perf/` en
  `docs/`. Elke zware meting liep onder `office_watchdog.py` (stopt de meting bij minder dan
  2 GiB vrij); die greep nergens in, het minst vrije geheugen was 7,0 GB (20 bewerkers op één
  document, 1 × 20; bij 5 × 4 was het 7,6 GB). Vóór
  elke meting `ps`, `top` en `free -m`: alleen dockerd, containerd, DS en de sessie, geen
  Chromium van een eerdere run. De metingen van stoppen en killen stopten, killden en startten de
  container zelf (6 keer); één keer heb ik hem met de hand weer op gang gebracht (bevinding 7).
  Elk proefbestand is per run nieuw (een seed), zodat DS niets uit zijn cache van conversies
  haalt.
- **Een eerdere poging van deel 2** stopte met exitcode 137 en een herstart van de hele sandbox
  (ook dockerd). Daarna met de waakhond en `free -m` herhaald: bij de proef met 2 bewerkers kwam
  de cgroup van de sessie tot 1,1 GB (minst vrij 14,1 GB), bij 20 bewerkers bleef er minstens
  7,0 GB vrij (1 × 20; bij 5 × 4 7,6 GB). Het geheugen van de editors was het dus niet; wat die
  herstart wel deed, is niet meer na te gaan (de `dmesg` van daarvoor is weg).
- **De commit bij de uitslag:** bij office_editors, office_outage_stop en office_outage_kill staat
  `1761998`. Dat is de commit waarop de uitkomsten in `metingen.json` zijn gezet (`--import`),
  niet de precieze versie van de scripts: die stonden toen nog deels niet in een commit, en een
  import legt dat niet vast. Pas `1cd3d52` (03:50, na de laatste import om 03:47) zette de
  scripts erin zoals ze hier gebruikt zijn: "opgeslagen" telt wat de editor zelf had, 1 × 20
  krijgt een eigen naam, `editable_s` na een stop of kill, en het wachten op de font-herstart
  kijkt in de log van de container. Wie opnieuw meet, neemt dus `1cd3d52` of later; op
  `1761998` mist de uitslag een deel van wat hieronder staat. Een import zet er nu ook bij of de
  werkmap schoon was (`dirty`), net als een gewone meting.

## Uitslag

<!-- gemeten -->
Gemaakt door `e2e/perf/office_run.py`; niet met de hand bewerken. Alle getallen:
`metingen.json`.

- office_open: 2026-10-27T23:31:16+00:00, commit `e2c47d9`
- office_tunnels: 2026-10-27T23:37:57+00:00, commit `e2c47d9`
- office_save: 2026-10-27T23:39:52+00:00, commit `e2c47d9`
- office_inspect: 2026-10-27T23:40:10+00:00, commit `e2c47d9`
- office_budget: 2026-10-27T23:40:30+00:00, commit `e2c47d9`
- office_xml: 2026-10-28T01:28:02+00:00, commit `08f45fb`
- office_convert: 2026-10-28T01:40:08+00:00, commit `ea647e4`
- office_editors: 2026-10-28T03:47:47+00:00, commit `1761998`
- office_outage_stop: 2026-10-28T02:39:40+00:00, commit `1761998`
- office_outage_kill: 2026-10-28T03:35:15+00:00, commit `1761998`

- Machine: 4 cpu's (Intel(R) Xeon(R) Processor @ 2.10GHz), 15,7 GiB RAM, kernel 6.18.44-fc-v165, Python 3.12.13
- Chromium 141.0.7390.37 (Playwright, headless)
- DS: container `meet-ds` (mirror.gcr.io/onlyoffice/documentserver@sha256:ca17889f10b11141cc6441836aebdd2835cf3e1b49681b2fd6e155c94b7850b3), geheugen 2,0 GiB, 2,0 cpu's, pids 512; grenzen toegepast: ja

| Wat | Doel | Gemeten | Gehaald | Hoe |
|---|---|---|---|---|
| Koud openen via de app | ≤ 5,0 s | docx 2,97 s (2,74–3,02, n=5); xlsx 2,87 s (2,79–3,06, n=5); pptx 2,61 s (2,5–2,96, n=5) | ja | office_open.py, 5× per soort (proef.docx/.xlsx/.pptx, elke keer een nieuw bestand), page.goto(/bewerken/<id>) tot de statusregel Bewerken zegt; koud: een nieuw browserprofiel, warm: het tweede bestand in datzelfde profiel (profiel disk); mediaan (kleinste–grootste) |
| Warm openen | ≤ 2,0 s | docx 2,47 s (2,26–2,61, n=5); xlsx 2,54 s (2,37–2,56, n=5); pptx 2,34 s (2,17–2,62, n=5) | nee | office_open.py, dezelfde runs (zie koud openen) |
| Cpu van de app bij koud openen | ≤ 1 core-seconde | docx 0,94 s (0,82–1,12, n=5) (322 verzoeken, 21,5 MB); xlsx 0,87 s (0,82–0,91, n=5) (306 verzoeken, 10,2 MB); pptx 0,9 s (0,73–0,94, n=5) (299 verzoeken, 9,8 MB) | ja | office_open.py, dezelfde koude runs: utime + stime van het serverproces van vóór page.goto tot 2 s zonder nieuw verzoek, min het verbruik in rust; verzoeken: de toegangslog van de server, bytes: Resource Timing (mediaan) |
| Geheugen van de app met 50 tunnels | ≤ +20 MB | +14,82 MB (14,18–15,12, n=3) open, +14,88 MB (14,23–15,15, n=3) na 30 s; alle 50 open: ja | ja | office_tunnels.py, 3 rondes (elk een nieuwe server), 10 gebruikers × 5 docx, VmRSS mediaan van 3; de eerste keer per server telt |
| Opslaan: callback tot versie in de database | ≤ 2 s voor 1 MB | Nu opslaan (6) 0,088 s (0,073–0,107, n=9); sluiten (2) 0,106 s (0,092–0,113, n=3) | ja | office_save.py, docx van 1,05 MiB (een foto), 3 rondes × (3× Nu opslaan + sluiten) in de echte editor; CallbackClock: van de eerste byte van de callback tot de kop van het antwoord (de app antwoordt na de commit) |
| Invoercontrole: xlsx 10 MB XML, pptx 80 MB foto's | ≤ 1 s cpu, in een thread | xlsx 0,015 s (0,015–0,016, n=5) cpu, lus max 1,2 ms; pptx 0,293 s (0,257–0,328, n=5) cpu, lus max 2,2 ms | ja | office_inspect.py, 5× per bestand, inspect.check() zoals de app hem aanroept (cache leeg), cpu = time.thread_time() van de controle; xlsx 10,0 MiB XML (net onder de grens van 10 MiB) in 1,6 MB, pptx 20 foto's (JPEG, deflate) 80,6 MB |
| Budget: eerste lading | groeit ≤ 0,5 KB | 93,75 → 94,12 KB (+0,37 KB) | ja | office_budget.py: npm run build en budget.mjs --json, 3ccee0a en e2c47d9 |
| Budget: chunk van de editor | < 50 KB gzip | 3,94 KB | ja | office_budget.py: npm run build en budget.mjs --json, 3ccee0a en e2c47d9 |
| Budget: route Bestanden | groeit < 1 KB | 39,42 → 42,36 KB (+2,94 KB) | nee | office_budget.py: npm run build en budget.mjs --json, 3ccee0a en e2c47d9 |
| XML-grens bij 2 GB: net onder de grens per soort, ook opmaak, formules, veel pagina's | geen OOM van x2t; docservice blijft | docx (opmaak, tabellen) 3,98 MiB XML (355 hoofdstukken, 355 tabellen): 3/3 omgezet, tot klaar of mislukt 22,5 s (22,5–23,5, n=3), x2t tot 539 MB, DS tot 1028 MB, OOM 0; docx (heel veel pagina's) 3,98 MiB XML (96937 pagina's): 0/3 omgezet, tot klaar of mislukt 29,4 s (26,8–66,5, n=3), x2t tot 1722 MB, DS tot 2048 MB, OOM 3; pptx 3,97 MiB XML (401 dia's): 3/3 omgezet, tot klaar of mislukt 8,5 s (8,3–9,4, n=3), x2t tot 411 MB, DS tot 853 MB, OOM 0; xlsx (formules) 9,98 MiB XML (15376 rijen, 6 formules per rij): 3/3 omgezet, tot klaar of mislukt 18,5 s (18,5–18,5, n=3), x2t tot 576 MB, DS tot 1105 MB, OOM 0; odt 1,98 MiB XML (235 hoofdstukken): 3/3 omgezet, tot klaar of mislukt 12,5 s (12,3–13,5, n=3), x2t tot 453 MB, DS tot 950 MB, OOM 0; ods 1,98 MiB XML (1392 rijen, 4 formules per rij): 3/3 omgezet, tot klaar of mislukt 3,4 s (3,3–3,4, n=3), x2t tot 190 MB, DS tot 671 MB, OOM 0; odp 1,98 MiB XML (955 dia's): 3/3 omgezet, tot klaar of mislukt 12,5 s (11,4–12,5, n=3), x2t tot 587 MB, DS tot 1096 MB, OOM 0. docservice bleef hetzelfde proces: ja. Geopend in de editor (hooguit 120 s): docx 0/3, docx_pages 2/3 (5,0 s (4,8–5,1, n=2)), pptx 3/3 (4,2 s (4,1–4,3, n=3)), xlsx 3/3 (3,6 s (3,5–4,0, n=3)). Grens van x2t bij alleen pagina-einden (rechtstreeks naar DS): 25.000 pagina's (1,03 MiB XML): 3/3 omgezet, x2t tot 709 MB, OOM 0; 50.000 pagina's (2,06 MiB XML): 3/3 omgezet, x2t tot 1229 MB, OOM 0; 75.000 pagina's (3,08 MiB XML): 3/3 omgezet, x2t tot 1757 MB, OOM 0 | nee | office_xml.py, 3 rondes × 7 bestanden (elke ronde nieuwe bestanden, net onder de grens van inspect: 16 KiB eronder), via de app: upload → voorbeeld → pdf-taak → DS /converter; DsWatch elke 0,25 s (geheugen van de cgroup van DS, rss van x2t, OOM-kills uit dmesg, het proces van docservice, /healthcheck); daarna elk bewerkbaar bestand openen in de editor |
| Conversie klein / 401 pagina's | ±1 s / ≤ 60 s (22 s in de proef) | docx 1,1 s (1,02–1,12, n=5) (DS alleen 0,54 s); xlsx 1,1 s (1,03–1,11, n=5) (DS alleen 0,64 s); pptx 1,11 s (1,02–1,12, n=5) (DS alleen 0,54 s); 401 pagina's 10,2 s (9,1–10,2, n=3) (DS alleen 9,5 s (9,0–9,7, n=3); elke PDF 401/401/401 pagina's, x2t tot 405 MB) | ja | office_convert.py: klein 5× per soort (proef.docx/.xlsx/.pptx, elke keer een ander bestand), 401 pagina's 3× (een docx van 61,8 kB met 2,48 MiB XML); via de app gemeten van het ophalen van de bron door DS (/internal/office/src) tot de pdf in de app staat (de pagina vraagt elke 0,1 s), ‘DS alleen’ is hetzelfde bestand rechtstreeks naar /converter (async, elke 0,1 s gevraagd) |
| Conversie van 401 pagina's terwijl een editor opent | openen ≤ 10 s; /healthcheck blijft true | conversie eerst: openen 7,8 s (7,6–8,1, n=3) (3/3), conversie 11,3 s (10,9–11,3, n=3); editor eerst: openen 2,9 s (2,9–2,9, n=3) (3/3), conversie 9,1 s (9,1–10,1, n=3); /healthcheck 169× gevraagd, 0× niet true, traagste 51 ms | ja | office_convert.py, 3× per volgorde: ‘conversie eerst’ = upload van de docx van 401 pagina's, wachten tot DS de bron ophaalt, 2 s later een proef.docx openen; ‘editor eerst’ = openen en uploaden zodra de sessie er is; /healthcheck elke 0,5 s |
| 20 bewerkers (5 documenten × 4), 60 s typen | alle teksten opgeslagen; cpu en RAM van DS en app vastgelegd | 2 (1 × 2): 145/145 woorden opgeslagen (3 rondes), open 6/6, openen mediaan 4,4 s (3,9–4,5, n=3), DS 0,02 cores (piek 0,07), DS 367 MB, app 0,02 cores en 162 MB, browser 1208 MB, minst vrij 13887 MB; 8 (2 × 4): 532/532 woorden opgeslagen (van de woorden die de editor zelf had: 0 niet opgeslagen; 0 getypte woorden kwamen nooit in de editor) (3 rondes), open 24/24, openen mediaan 15,1 s (14,2–16,3, n=3), DS 0,06 cores (piek 0,22), DS 404 MB, app 0,04 cores en 140 MB, browser 4211 MB, minst vrij 10908 MB; 20 (1 × 20): 1014/1033 woorden opgeslagen (van de woorden die de editor zelf had: 0 niet opgeslagen; 19 getypte woorden kwamen nooit in de editor) (3 rondes), open 60/60, openen mediaan 56,2 s (56,1–56,5, n=3), DS 0,06 cores (piek 0,29), DS 419 MB, app 0,05 cores en 218 MB, browser 8472 MB, minst vrij 6982 MB; 20 (5 × 4): 1033/1043 woorden opgeslagen (van de woorden die de editor zelf had: 0 niet opgeslagen; 10 getypte woorden kwamen nooit in de editor) (3 rondes), open 60/60, openen mediaan 58,2 s (52,8–58,5, n=3), DS 0,05 cores (piek 0,19), DS 422 MB, app 0,04 cores en 273 MB, browser 7840 MB, minst vrij 7569 MB | ja | office_editors.py, opgeschaald 2 → 8 → 20 bewerkers, elk 3 rondes (nieuwe documenten van ±150 kB XML), één Chromium met een context per bewerker, elk typt eigen woorden in een eigen alinea (110 ms per toets, 0,8–1,6 s pauze), daarna Sluiten; opgeslagen = het woord staat in de docx na status 2; wat de editor zelf had = zijn eigen tekst (sdkjs) vlak voor Sluiten; cpu: de cgroup van DS en utime + stime van de app, elke seconde; RAM: rss van de cgroup van DS, VmRSS van de app, PSS van de browser; ook 1x20 op één document (voor max_editors) |
| Stoppen met open editors | alles opgeslagen; docker stop ≤ 60 s | docker stop 30,5 s (30,5–30,5, n=3), 0× gekild (137); opgeslagen meteen na de stop 149/149 woorden, na de herstart 149/149; status 2 kwam 1,3 s (0,9–1,9, n=6) na het begin van de stop; van de tekst die elke editor zelf had: 0 niet opgeslagen, 0 getypte woorden nooit in de editor; bewerken kon weer 123 s (120–123, n=3) na docker start | ja | office_outage.py --mode stop, 3 rondes: 2 documenten × 2 bewerkers typen 30 s, 3 s later `docker stop -t 60` op de container van DS (zoals compose met stop_grace_period 60 s); daarna de inhoud in de app, DS weer starten, wachten tot de app DS weer ok vindt, dan 90 s en nog eens |
| Kill van DS met open editors | verlies ≤ de tijd sinds het laatste tussentijds opslaan (≤ 5 min) | (review bij de afronding: die tijd is hooguit ±5,5 min, want de klok slaat op bij de eerste ronde van 30 s na 5 min; het slechtste geval is niet gemeten) van vóór de forcesave van de klok bewaard: 1463/1463 woorden; van erna 0/719 (weg: 730; na de herstart in totaal 1465/2195); verloren venster 32, 30, 152, 150, 287, 285 s (het eerste verloren woord was 1,1–2,2 s vóór de forcesave getypt: het zat nog niet in die opslag); de klok sloeg 325 s (323–328, n=6) na het openen; 1× kwam DS na docker start niet terug (zie bevindingen); bewerken kon weer 126 s (114–982, n=3) na docker start | ja | office_outage.py --mode kill, 3 rondes: 2 documenten × 2 bewerkers typen tot de klok (checkpoint_minutes 5) een forcesave (status 6, timer) gaf, dan nog 30, 150 of 285 s, dan `docker kill`; daarna DS starten, wachten tot de app DS weer ok vindt, dan 150 s; verloren venster = van de forcesave tot de kill; ‘bewaard’ = het woord staat in de docx van de app, vóór = getypt tot 3 s vóór de forcesave |

Waar de tijd van openen zit (medianen in s; DS = van het verzoek om api.js tot klaar):

| Soort | | Via de app | Schil | POST …/session | DS via de app | Rechtstreeks: totaal | Rechtstreeks: DS | Verzoeken bij de app | Over het net |
|---|---|---|---|---|---|---|---|---|---|
| docx | koud | 2,97 | 0,45 | 0,03 | 2,47 | 2,74 | 2,73 | 322 | 21,47 MB |
| docx | warm | 2,47 | 0,43 | 0,03 | 1,98 | 2,06 | 2,04 | 12 | 0,02 MB |
| xlsx | koud | 2,87 | 0,46 | 0,03 | 2,38 | 2,74 | 2,73 | 306 | 10,22 MB |
| xlsx | warm | 2,54 | 0,43 | 0,03 | 2,07 | 2,21 | 2,2 | 12 | 0,02 MB |
| pptx | koud | 2,61 | 0,46 | 0,03 | 2,12 | 2,42 | 2,41 | 299 | 9,76 MB |
| pptx | warm | 2,34 | 0,43 | 0,03 | 1,87 | 1,93 | 1,92 | 15 | 0,03 MB |

Wat faalt:

- office_open: docx: warm openen mediaan 2.474 s > 2.0 s
- office_open: xlsx: warm openen mediaan 2.537 s > 2.0 s
- office_open: pptx: warm openen mediaan 2.343 s > 2.0 s
- office_budget: route Bestanden +2.94 KB, niet < 1.0 KB
- office_xml: docx: 0 van 3 geopend in de editor
- office_xml: docx_pages: 3 OOM-kill(s) (x2t, x2t, x2t)
- office_xml: docx_pages: /healthcheck 7 keer niet true
- office_xml: docx_pages: 0 van 3 omgezet (failed, failed, failed)
- office_xml: docx_pages: 2 van 3 geopend in de editor
- office_editors: 20 bewerkers: ronde 1: 3 van 345 woorden niet opgeslagen (0 met één teken verschil, 3 had de editor zelf al niet)
- office_editors: 20 bewerkers: ronde 2: 5 van 350 woorden niet opgeslagen (0 met één teken verschil, 5 had de editor zelf al niet)
- office_editors: 20 bewerkers: ronde 3: 2 van 348 woorden niet opgeslagen (0 met één teken verschil, 2 had de editor zelf al niet)
- office_editors: 1x20 bewerkers: ronde 1: 11 van 351 woorden niet opgeslagen (0 met één teken verschil, 11 had de editor zelf al niet)
- office_editors: 1x20 bewerkers: ronde 2: 3 van 338 woorden niet opgeslagen (0 met één teken verschil, 3 had de editor zelf al niet)
- office_editors: 1x20 bewerkers: ronde 3: 5 van 344 woorden niet opgeslagen (1 met één teken verschil, 5 had de editor zelf al niet)
<!-- /gemeten -->

"Via de app" is de hele weg van een link naar de editor: `page.goto("/bewerken/<id>")` tot de
statusregel van de editorpagina "Bewerken" zegt (na `onDocumentReady`). "Schil" is van de
navigatie tot `POST …/session` begint; "DS" is van het verzoek om `api.js` tot klaar (de editor in
de browser, met alles wat hij door de proxy en de tunnel haalt). "Rechtstreeks" is de controle: in
dezelfde run hetzelfde bestand rechtstreeks bij DS, zonder de app (een gastpagina op een andere
origin die `api.js` van DS laadt; de bron komt uit de DocStub van het harnas). Die telt niet mee
voor de doelen. Rechtstreeks werkt de service worker van DS wel; die geeft zijn bestanden uit Cache
Storage, en dan zegt Resource Timing niets bruikbaars over de bytes over het net. Daarom heeft de
controle geen kolom "Over het net".

## Wat de getallen betekenen

### Koud openen via de app: ja

Mediaan 2,6–3,0 s, ruim onder de 5 s en onder de 4,1 s van de proef via nginx. Koud is: een nieuw
browserprofiel (een lege HTTP-cache op schijf, zoals een gewone browser) en een bestand dat DS nog
niet kent. Koud is de app per opening 0,13–0,23 s trager dan DS rechtstreeks: de schil (±0,45 s)
en `POST …/session` (±0,03 s) erbij, maar het deel van DS is via de app zelfs iets sneller
(2,12–2,47 s tegen 2,41–2,73 s). De proxy en de tunnel kosten bij openen dus niets meetbaars.
Waarschijnlijk komt dat verschil van de service worker van DS: rechtstreeks zet die bij de eerste
opening elk bestand in Cache Storage (bij docx 52 MB uitgepakt); via de app is er geen, want de
app geeft op dat pad een eigen service worker zonder fetch-handler (`oo/paths.py`, o1.md). Niet
apart gemeten. Een koude opening via de app haalt 246–269 verzoeken bij `/office/` en 299–322 in
totaal (de service worker van de app haalt bij het eerste bezoek zijn bestanden), en 9,8 MB
(pptx), 10,2 MB (xlsx) of 21,5 MB (docx) over het net. De docx is zwaarder omdat DS voor
`proef.docx` ook het CJK-font WenQuanYi Zen Hei laadt (`fonts/242`, 9,2 MB gzip, 16,8 MB
uitgepakt). Dat doet DS ook rechtstreeks: daar is de docx uitgepakt ±19 MB groter dan de xlsx.

### Warm openen: nee

Mediaan 2,34–2,54 s, boven het doel van 2 s. Warm is: het tweede bestand in hetzelfde profiel.
Dan komt bijna alles uit de cache van de browser: 3 tot 7 verzoeken bij `/office/` (de proef zei
5) en 12–15 bij de app in totaal, ±25 KB over het net.

Waar de 2,5 s zit (docx, mediaan):

- **DS in de browser: ±2,0 s.** Van `api.js` tot klaar, met alles uit de cache. Rechtstreeks bij DS
  duurt een warme opening 1,93–2,21 s. Het doel van 2 s ligt dus op of onder wat DS 10.0.0.1 hier
  zelf haalt, zonder de app. In de proef was warm via nginx 2,3–3,4 s.
- **De schil van de app: ±0,43 s.** De pagina, `boot.js` en de schil (uit de cache), dan
  `POST /api/v1/auth/refresh` (klaar na 90–140 ms), en dan **±0,3 s niets**: in alle 30 openingen
  begint het eerste verzoek van de editorpagina (`GET /api/v1/office/status`) 312–325 ms na het
  einde van de refresh, terwijl het chunk van de editorpagina 10–40 ms na de refresh al binnen is.
  Dat is de vertraging van React voor een lazy onderdeel (zie bevinding 2).
- **`POST …/session`: ±0,03 s.**

Zonder die 0,3 s van React zou warm via de app ±2,0–2,25 s zijn: nog steeds net boven het doel,
omdat DS zelf al zo lang doet. Wat dit voor Jonas betekent: de editor opent warm in ±2,5 s op deze
VM met headless Chromium; een desktopbrowser op een snellere cpu doet het deel van DS sneller, het
deel van de app (±0,45 s) blijft. Niet gemeten: openen vanuit Bestanden (de schil staat dan al;
de wachttijd van React voor het eerste bezoek aan `/bewerken/` blijft).

### Cpu van de app bij koud openen: ja, maar krap bij docx

Mediaan 0,87–0,94 core-seconde voor één koude opening, met 299–322 verzoeken. Dat is de server
van de pagina tot 2 s zonder nieuw verzoek, min wat hij in rust doet. De kosten zitten vooral in
het aantal verzoeken (±3 ms per verzoek door de proxy), minder in de bytes: de docx haalt 11 MB
meer dan de xlsx en kost ±0,1 s meer. Eén van de vijf docx-openingen kwam boven de 1 s (1,12 s);
in de eerste set lag docx op 0,97–1,00 s. Het doel geldt per opening, dus voor 20 mensen die
tegelijk openen is dat ±20 core-seconden; dat hoort bij de meting met 20 bewerkers (deel 2).
Warm kost een opening 0,10–0,22 s.

### Geheugen van de app met 50 tunnels: ja

+14,2 tot +15,1 MB (VmRSS) met 50 open tunnels, ±0,3 MB per tunnel, en na 30 s open houden
nauwelijks meer (+0,03 tot +0,06 MB). Alle 50 tunnels waren echt: tien gebruikers met elk vijf
sessies (`POST …/session`), elke tunnel door de poort, de StreamGuard en de pomp naar DS, en DS
gaf elke tunnel `documentOpen`. Na het sluiten blijft het geheugen staan (Python geeft het niet
terug aan het systeem), maar een tweede keer 50 tunnels op dezelfde server kost +0,06 tot
+0,36 MB: geen lek. 50 tunnels open houden kost ±0,05 core (pings en de controle elke 15 s);
openen, 10 s wachten en sluiten van de hele golf kostte ±8 s cpu (niet verder ontleed; het hoort
bij de 20 bewerkers van deel 2).

### Opslaan: van de callback tot de versie in de database: ja

0,073–0,113 s voor een docx van 1,05 MiB (opgeslagen ±1,12 MB): downloaden bij DS, `place_file`,
de commit en dan pas het antwoord. Dat is ver onder de 2 s. Gemeten in de echte editor: een woord
typen, "Nu opslaan" (status 6), drie keer, en dan "Sluiten" (status 2). Elke callback gaf een
nieuwe inhoud in de database, en elke ronde precies één versie (de inhoud van vóór de sessie;
daarna vervangt een opslag zonder nieuwe versie, Keuze 17). Wat de gebruiker ziet, duurt langer
en komt van DS: van de klik op "Nu opslaan" tot de callback 0,6–0,75 s (DS bouwt de docx), van
"Sluiten" tot de callback ±5,7 s (DS wacht na de laatste gebruiker).

### Invoercontrole: ja

Een werkblad met 10,0 MiB XML (net onder de grens van 10 MiB, met getallen, tekst en formules)
kost 0,015 s cpu; een presentatie met 20 foto's (80,6 MB JPEG, deflate) 0,29 s. Beide in een
thread (gecontroleerd), en de lus van de app liep intussen door (de grootste vertraging van een
tikker van 10 ms was 2,2 ms). De tijd zit in het uitpakken; een werkblad dat minder goed comprimeert
dan dit (1,6 MB voor 10 MiB XML) kost iets meer, maar dezelfde orde.

### Budget: eerste lading en editor: ja; Bestanden: nee

- **Eerste lading:** 93,75 → 94,12 KB (+0,37 KB, `office/listener.ts` in de schil). Binnen de
  0,5 KB, en binnen de 100 KB van het budget.
- **Editor:** het chunk van `EditorPage.tsx` is 3,94 KB gzip (verwacht ±8 KB). Erbij komen
  0,43 KB CSS, 0,85 KB woordenboek (`office.nl`) en 0,10 KB gedeelde types.
- **Bestanden:** 39,42 → 42,36 KB, +2,94 KB, terwijl Keuze 33 < 1 KB zei. Keuze 33 rekende alleen
  op het menu Nieuw. Wat er echt bij kwam (bytes gzip):

  | Wat | Chunk | Erbij |
  |---|---|---|
  | het menu Nieuw (`office/newItems.ts`) | Files | 550 B |
  | de status van office (`office/index.ts`, `office/api.ts`): Nieuw en Bewerken alleen als office aan staat | office, api | 1.020 B |
  | "Downloaden als PDF" in de lijst (O3: `office/pdf.ts`, `office/actions.ts`) | pdf | 794 B |
  | de acties in de lijst (Bewerken, PDF) | browser | 221 B |
  | de teksten (`browser.nl`) | browser.nl | 300 B |
  | de viewer en kleine verschuivingen | viewer en andere | 41 B |

  Het menu Nieuw zelf blijft dus met zijn teksten onder de 1 KB; de status van office en de
  PDF-actie zaten niet in de schatting. De harde grens van de route (45 KB) houdt: er is nog
  2,6 KB ruimte. Wie de 1 KB wil halen, laadt `pdf.ts` en `actions.ts` pas bij het eerste gebruik
  en de status van office in een kleiner stuk; dat is een keuze, niet gedaan in deze stap.

### XML-grens bij 2 GB: nee, door de docx met heel veel pagina's

Zes van de zeven soorten houden bij 2 GB ruim: net onder de grens (16 KiB eronder) zette DS ze 3
van de 3 keer om, x2t gebruikte hooguit 587 MB en de hele container hooguit 1,1 GB van zijn 2 GB.
Ook met echte opmaak (een docx met 355 hoofdstukken en tabellen), formules (een xlsx van
15.376 rijen met 6 formules per rij) en 401 dia's. De uitzondering is een docx met alleen
pagina-einden: 96.937 lege pagina's in 3,98 MiB XML (zoals `pb4000k.docx` van de spike). Daar
stopte de kernel x2t 3 van de 3 keer op de grens van de cgroup, bij 1,72 GB
(`Memory cgroup out of memory: Killed process … (x2t) … anon-rss:1763184kB` in `dmesg`, ook in
het log van de waakhond). Docservice bleef elke keer hetzelfde proces, de app zette de pdf-taak
op mislukt (`Omzetten naar PDF mislukt (code -3)`, één poging) en de volgende bestanden gingen
gewoon. Het doel "geen OOM van x2t" is dus niet gehaald; "docservice blijft" wel.

- **Wat de OOM verder deed:** in de derde ronde gaf `/healthcheck` van DS 7 keer geen `true` (de
  traagste 5,0 s): de container zat op zijn grens en alles in hem wachtte op geheugen. De
  gezondheidslus van de app viel net in dat moment, zette DS op down, en de editor die het
  script daarna opende, meldde "De editor is nu niet bereikbaar" (bevinding 5).
- **Openen in de editor** (geen doel van §4.7, wel gemeten, hooguit 120 s): de pptx en de xlsx
  net onder de grens in 3,5–4,3 s, de docx met de pagina's 2 van de 3 keer, in ±5 s (x2t maakt
  daarvoor `Editor.bin` van 77 MB; het zware is de PDF van 97.000 pagina's). De derde keer
  meldde de editorpagina "De editor is nu niet bereikbaar": dat was de ronde waarin de OOM DS
  op down had gezet (hierboven). De docx met opmaak kwam 0
  van de 3 keer klaar (bevinding 4).
- **Waar de grens ligt** (`office_xml.py --pages 25000,50000,75000`, 3 keer elk, rechtstreeks
  naar `/converter`): x2t groeit met ±21 KB per pagina. 25.000 lege pagina's (1,03 MiB XML):
  690–709 MB, 12,3–12,7 s; 50.000 (2,06 MiB): 1,20–1,23 GB, 25,3–25,9 s; 75.000 (3,08 MiB):
  1,65–1,76 GB, 42–45 s, en de container zat toen op zijn 2 GB (de kernel gaf de paginacache
  terug); 96.937 (3,98 MiB): OOM. Alles onder 75.000 ging 3 van de 3 keer goed, zonder OOM. De
  grens van 4 MiB XML laat dus ±97.000 pagina-einden toe, terwijl x2t bij 2 GB er hooguit
  ±75.000 aankan. Voorstel: de invoercontrole telt ook de pagina-einden (`w:br w:type="page"`,
  `w:pageBreakBefore`, secties) en weigert boven bijvoorbeeld 50.000 (1,2 GB; dan blijft er ruimte
  voor docservice en een tweede conversie, al is een tweede conversie tegelijk niet gemeten). Niet
  veranderd: dat is een wijziging van `inspect.py` met eigen tests.
- **De rest heeft ruimte:** een ods van 1,98 MiB XML kostte x2t maar 190 MB, een odt en odp
  ±450–590 MB. De ODF-grens van 2 MB is dus voorzichtig (de spike zag x2t pas bij een odt van
  7 MB vastlopen). Niet veranderd: dat is een keuze voor het ontwerp, niet voor een meting.
- **Hoe:** `office_xml.py --rounds 3` onder de waakhond (16 min, geen ingreep, minst vrij 13,2 GB).
  Elke ronde nieuwe bestanden (een seed per run): DS bewaart conversies onder een key die de sha
  volgt, en een tweede keer hetzelfde bestand kwam in een eerdere poging meteen uit zijn cache
  (ook de fout -3), zonder x2t.

### Conversie klein / 401 pagina's: ja

Een klein bestand (proef.docx, .xlsx, .pptx, elke keer met een ander zip-commentaar, dus een
nieuwe sha) staat via de app 1,02–1,12 s na het ophalen van de bron door DS als PDF in de app
(mediaan 1,1 s, 5 keer per soort). "±1 s" lezen we als: rond 1 s, en dat is het. DS zelf doet het
in 0,53–0,65 s (hetzelfde soort bestand rechtstreeks naar `/converter`, elke 0,1 s gevraagd). Het
verschil is vooral de vraagronde van de app: `oo/convert.py` vraagt DS elke 1,0 s
(`POLL_SECONDS`) of hij klaar is, dus een conversie van 0,6 s telt als ±1 s. Een eerste vraag na
0,25 s zou ±0,4 s per klein bestand schelen; niet veranderd, het doel is gehaald.

De docx van 401 pagina's (zoals `groot.docx` van de proef: 19.240 korte alinea's, 2,48 MiB XML)
kost via de app 9,1–10,2 s (mediaan 10,2 s, 3 keer), rechtstreeks 9,0–9,7 s, en elke PDF had 401
pagina's. Ruim onder de 60 s, en sneller dan de 22 s van de proef; waar dat verschil vandaan komt,
is niet nagegaan. x2t gebruikte hooguit 405 MB, de container 822 MB.

### Conversie van 401 pagina's terwijl een editor opent: ja

- **Conversie eerst** (de echte botsing: upload van de docx van 401 pagina's, DS haalt de bron,
  2 s later opent een gebruiker proef.docx): openen 7,6–8,1 s, tegen 2,6–3,0 s koud zonder
  conversie (deel 1). DS gebruikte dan tot 1,6–1,8 van zijn 2 cpu's (de drukste seconde). Binnen
  de 10 s, maar de editor merkt de conversie wel.
- **Editor eerst** (openen, en de upload zodra de sessie er is): openen 2,9 s, als zonder
  conversie. De app laat de conversie wachten zolang een sessie opent (`sessions.busy`, Keuze 25):
  DS haalde de bron pas 6,5–6,7 s na het begin van het openen.
- `/healthcheck` van DS elke 0,5 s: 169 keer gevraagd, altijd `true`, het traagste antwoord 51 ms.
- De conversie zelf duurde 9,1–11,3 s; de PDF had steeds 401 pagina's.

### 20 bewerkers (5 documenten × 4), 60 s typen: ja

Opgeschaald in drie stappen, elke stap 3 rondes met nieuwe documenten (een docx van ±150 kB XML,
±25 pagina's). Elke bewerker is een eigen account in een eigen context van één Chromium, opent
het document via de app (`/office/`, proxy en tunnel), typt 60 s eigen woorden (`r1d2u3w004`:
ronde, document, bewerker, woord) in een eigen alinea, en klikt op Sluiten. Vlak voor Sluiten
leest het script ook de tekst die de editor zelf had (uit sdkjs); daarna kijkt het welke woorden
na status 2 in de docx van de app staan.

| Bewerkers | Open | Openen (mediaan per ronde) | Opgeslagen | DS cpu bij typen (piek) | DS RAM | App cpu typen / openen (piek) | App RAM | Browser | Minst vrij |
|---|---|---|---|---|---|---|---|---|---|
| 2 (1 × 2) | 6/6 | 4,4 s (3,9–4,5) | 145/145 | 0,02 (0,07) | 367 MB | 0,02 / 0,49 (0,78) | 162 MB | 1,2 GB | 13,9 GB |
| 8 (2 × 4) | 24/24 | 15,1 s (14,2–16,3) | 532/532 | 0,06 (0,22) | 404 MB | 0,04 / 0,54 (1,11) | 140 MB | 4,2 GB | 10,9 GB |
| 20 (5 × 4) | 60/60 | 58,2 s (52,8–58,5) | 1033/1043 | 0,05 (0,19) | 422 MB | 0,04 / 0,40 (1,12) | 273 MB | 7,8 GB | 7,6 GB |
| 20 (1 × 20) | 60/60 | 56,2 s (56,1–56,5) | 1014/1033 | 0,06 (0,29) | 419 MB | 0,05 / 0,42 (1,18) | 218 MB | 8,5 GB | 7,0 GB |

Cpu in cores (gemiddeld over de fase, piek = de drukste seconde), RAM is het hoogste: rss van de
cgroup van DS, VmRSS van de app, PSS van alle processen van Chromium. DS begon elke ronde op
±300 MB. De laatste regel (20 op één document) is een extra meting voor `max_editors`; het doel
van §4.7 is de regel 5 × 4.

- **Opgeslagen: ja.** Van elk woord dat de editor zelf had, stond na status 2 elk woord in de docx:
  bij 20 bewerkers 1033 van de 1033 (en 1014 van de 1014 met 20 op één document), bij 8 532 van
  de 532. De 10 woorden die bij 20 ontbreken,
  kwamen nooit in de editor: ze staan ook niet in zijn eigen tekst vlak voor Sluiten, dus ze zijn
  niet bij het opslaan verloren. Alle 10 waren het eerste tot vierde woord van een bewerker
  (`w001`–`w004`), getypt vlak na het openen terwijl de machine op 3,8 van zijn 4 cores zat (de
  20 tabbladen van Chromium). Zie bevinding 6. Ook een eerdere ronde van 8 (vóór het lezen van de
  eigen tekst) miste 3 eerste woorden van één bewerker.
- **DS doet bijna niets.** Met 20 bewerkers tegelijk gebruikte DS 0,05 core bij typen (piek 0,19)
  en 0,03 bij openen, van zijn 2 cpu's; zijn geheugen ging van ±300 naar hooguit 422 MB, van
  zijn 2 GB. Het drukste moment is het opslaan na Sluiten: x2t bouwt dan de 5 docx tegelijk
  (0,6 core gemiddeld, 1,2–1,4 in de drukste seconde, x2t tot 144 MB). Geen OOM, `/healthcheck`
  elke 2 s gevraagd en steeds `true`.
- **De app ook niet bij typen** (0,04 core; het verkeer van de WebSockets door de tunnel). Bij
  openen wel: elke koude opening haalt de statische bestanden van de editor door de proxy (deel 1:
  ±0,9 core-seconde per opening), dus 20 tegelijk gaven tot 1,1 core in de drukste seconde. Het
  geheugen van de app bleef onder 273 MB.
- **Het openen duurt lang, maar dat is de sandbox.** 20 editors die tegelijk openen (elke 0,5 s
  één) vragen 20 keer de editor van Chromium op 4 cores; de machine zat dan vol (3,9 cores bij
  openen, 3,8 bij typen, bijna alles Chromium), terwijl DS en de app samen onder 0,5 core
  bleven. Al bij 2 bewerkers zat de machine bij openen op 3,3 cores. In het echt heeft elke
  bewerker een eigen computer. Geen doel van §4.7.
- **Van Sluiten tot alles opgeslagen:** 6,2 s (2), 7,6 s (8), 11,0 s (20). DS wacht na de laatste
  bewerker eerst een paar seconden en bouwt dan de docx.
- **Hoe:** `office_editors.py --docs 1 --per-doc 2`, `--docs 2 --per-doc 4` en `--docs 5
  --per-doc 4`, elk met `--rounds 3 --seconds 60`, onder de waakhond (geen ingreep, `free -m`
  elke seconde bijgehouden). 110 ms per toets, 0,8–1,6 s pauze tussen woorden.

### Stoppen met open editors: ja

- **Opgeslagen: alles.** 3 rondes: 2 documenten × 2 bewerkers typen 30 s, 3 s later
  `docker stop -t 60 meet-ds` (zoals `stop_grace_period: 60s` in `compose.office.yaml`; compose
  stopt DS vóór de app, dus de app draait door, zoals hier). Van de 149 getypte woorden stonden
  er 149 in de docx van de app meteen na de stop, en nog steeds na de herstart. Ook de tekst die
  elke editor zelf had vlak voor de stop: 0 woorden die er niet in kwamen, 0 die niet bewaard
  werden.
- **`docker stop` 30,5 s** (30,46–30,47 s, 3 keer), exitcode 0, nooit gekild (137). Status 2
  (opgeslagen) kwam voor elk document 0,9–1,9 s na het begin van de stop; de app antwoordde in
  0,08–0,11 s.
- **Waar de 30 s heen gaan:** het startscript van DS vangt SIGTERM en roept
  `documentserver-prepare4shutdown` aan, een `PUT /internal/cluster/inactive` op docservice.
  Docservice begint meteen ("start shutdown:true", "active connections: 4"), slaat elk open
  document op (de status 2 hierboven), maar het verzoek komt pas na 30,0 s terug (de log van de
  container: "Preparing for shutdown" om 02:35:34,708, "Done" om 02:36:04,749). Met open editors
  duurt een stop dus altijd ±30 s; de 60 s van Keuze 29 laat 30 s over.
- **Na de herstart** antwoordde DS na 4,3 s op `/healthcheck`, maar bewerken kon pas weer
  120–123 s na `docker start`: elke start doet de font-herstart (±110 s na de start,
  `ds:converter: started` in de log), en de app wacht daarop (`health.py`, settling), want die
  herstart gooit open sessies weg. Zo bedoeld (ontwerp-1), wel goed om te weten bij een update:
  ±2 minuten geen editor.
- In ronde 1 zei docker `OOMKilled: true`: dat is de OOM van x2t uit de XML-meting in hetzelfde
  leven van de container (docker houdt die vlag tot de volgende start), niet de stop.
- **Hoe:** `office_outage.py --mode stop` onder de waakhond (15 min, geen ingreep, minst vrij
  12,8 GB). Na de stop leest het script de inhoud via de app, start DS (`docker start`), wacht tot
  de app DS weer ok vindt, wacht 90 s (de ronde en het herstel van Keuze 21) en leest nog eens.

### Kill van DS met open editors: ja

- **Wat er weg is, is wat na het laatste tussentijds opslaan getypt is.** 3 rondes: 2 documenten
  × 2 bewerkers typen tot de klok van de app (`checkpoint_minutes` 5) voor elk document een
  forcesave gaf (status 6, `userdata` timer, 323–328 s na het openen), dan nog 30, 150 of 285 s,
  dan `docker kill meet-ds`. Van de woorden die tot 3 s vóór die forcesave getypt waren, stonden
  er 1463 van de 1463 in de docx van de app; van wat erna kwam 0 van de 719. Het verloren venster
  was 30–32, 150–152 en 285–287 s: precies de tijd sinds de forcesave.
- **Plus ±2 s.** Het eerste verloren woord was telkens 1,1–2,2 s vóór de forcesave getypt: de editor
  stuurt zijn wijzigingen in stukjes, en wat nog onderweg was, zat niet in die opslag. Het verlies
  is dus "de tijd sinds het laatste tussentijds opslaan, plus de laatste ±2 s ervoor".
- **Hoe lang dat hooguit is (review bij de afronding).** ~~binnen de 5 minuten van Keuze 18~~:
  niet 5 minuten. De klok van de app (`sweep.py`, `_timer`) kijkt alleen bij een ronde, elke
  `office_sweep_seconds` (30 s), en stuurt de forcesave bij de eerste ronde waarin de laatste
  opslag 5 minuten (`checkpoint_minutes`) of ouder is. Dat zie je ook hier: de eerste forcesave
  kwam 323–328 s na het openen, niet na 300 s. Tussen twee forcesaves van de klok zit dus tot
  ±5,5 minuten (5 minuten plus één ronde, plus de ±0,7 s die DS nodig heeft), en een kill vlak
  vóór de volgende verliest zoveel, plus de ±2 s. Gemeten zijn kills 30, 150 en 285 s na de
  forcesave; het slechtste geval, vlak vóór de tweede forcesave, is **niet** gemeten. Het volgt
  uit dezelfde regel als de drie metingen (weg is precies wat na de laatste forcesave kwam).
  Gehaald blijft "verlies is de tijd sinds het laatste tussentijds opslaan" (plus ±2 s); de
  "≤ 5 min" van het doel is ±5,5 min.
- **Na de herstart:** de ronde sloot alle 6 sessies als `lost` (Keuze 21), en er kwam niets meer
  bij: meteen na de kill en na de herstart stonden dezelfde 1465 woorden in de documenten (de
  1463 en 2 uit de laatste 3 s vóór een forcesave). De kill zelf duurde 0,45–0,5 s.
- **Ronde 2: DS kwam na `docker start` niet terug** (bevinding 7). De container draaide, maar
  supervisord startte niet, dus docservice en de converter ook niet; nginx gaf 502 op
  `/healthcheck`. Na 14 minuten heb ik het oude pid-bestand weggehaald en de container
  herstart (`rm /var/run/supervisord.pid`, `docker restart`); daarna liep alles. Het verlies van
  ronde 2 is gemeten vóór de herstart en telt gewoon. Bewerken kon weer 114 en 126 s na
  `docker start` (ronde 1 en 3); in ronde 2 pas na 982 s, door die ingreep.
- **Hoe:** `office_outage.py --mode kill` onder de waakhond (52 min, geen ingreep van de
  waakhond, minst vrij 12,6 GB). Na de kill leest het script de inhoud via de app (welke woorden
  staan erin, met het moment waarop elk woord getypt is), start DS, wacht tot de app DS weer ok
  vindt, wacht 150 s (de ronde) en leest nog eens.

### max_editors (Keuze 27)

**Aanbeveling: de standaard van 20 per document laten staan.** Niet veranderd; dat is een keuze
voor het ontwerp, en de meting geeft geen reden om hem te veranderen.

- **20 op één document gaat.** In 3 rondes kwamen alle 20 open, en van wat de editors zelf hadden,
  ging bij het opslaan niets verloren (1014 van de 1014). DS gebruikte bij typen 0,06 core (piek
  0,29) en hooguit 419 MB, bij het opslaan na Sluiten 1,2–1,3 core in de drukste seconde; de app
  0,05 core bij typen en tot 1,2 core bij het openen, hooguit 218 MB. Dat is evenveel als 20
  bewerkers over 5 documenten: voor DS kost een bewerker bij typen ±0,003 core en ±6 MB.
- **Waarom niet hoger:** boven 20 is niet gemeten, en hier ook niet te meten: de 20 tabbladen van
  Chromium hielden de 4 cores van de sandbox vol (3,95) en namen 8,5 GB. Wat met het aantal
  meegroeit, is het openen: elke koude opening gaat met ±0,9 core-seconde door de proxy van de
  app (deel 1), dus 20 mensen die tegelijk openen, geven de app tot 1,2 core. En bij 20 vielen
  vaker de eerste woorden in de editor zelf weg dan bij 2 of 8 (bevinding 6). Een hogere
  standaard zou dus een niet-gemeten belofte zijn.
- **Waarom niet lager:** DS (2 cpu's, 2 GB) houdt bij 20 ruim over: hooguit 15 % van zijn cpu bij
  typen en 21 % van zijn geheugen. Een lagere grens beschermt hier niets.
- `max_editors` geldt per document; het totaal over alle documenten begrenst hij niet. Gemeten is
  hooguit 20 tegelijk in totaal.

**In 0.6.0 blijft de standaard 20** (`OfficeConfig.max_editors` in `api/app/office/state.py`;
de beheerder kan hem op `/beheer` → Office op 1 tot 500 zetten). De meting steunt 20 en niet
meer, en geeft geen reden om lager te gaan; daarom is niets veranderd aan de instellingen, de
contracten, de web-app, de tests (ook niet de "20" in `e2e/test_office_o5.py`) of het runbook.
Wil Jonas meer dan 20 mensen in één document, dan is dat eerst een meting op zijn LXC, met de
cijfers van `/beheer` → Office en `docker stats` (runbook, "De belasting meten").

## Bevindingen

1. **De presentatie-editor krijgt via de app de ingebouwde thema's niet.** De pptx-editor vraagt
   bij elke opening drie keer `/office/<V>/sdkjs/slide/themes//themes.js` (met twee schuine
   strepen). Het padbeleid van de app (`oo/paths.py`, §3.10) weigert een leeg segment, dus de app
   geeft 404. DS zelf (nginx voegt de strepen samen) geeft 200 met `AscCommon.g_defaultThemes`,
   de lijst van 36 ingebouwde thema's. Bewijs: in de editor via de app is
   `AscCommon.g_defaultThemes` er niet, rechtstreeks bij DS heeft hij 36 namen (dezelfde
   container, dezelfde `proef.pptx`); de toegangslog van elke koude pptx-opening heeft
   `GET office (404)` 3 keer, docx en xlsx geen. Gevolg: in Bewerken van een presentatie
   ontbreken de ingebouwde ontwerpen (Ontwerp/thema wijzigen). ~~Niet hersteld in deze stap: het
   padbeleid is de toegangscontrole, en een leeg segment toestaan is geen kleine wijziging.
   Voorstel: voor de klasse STATIC een leeg segment binnen het pad samenvoegen zoals nginx, en
   dan het samengevoegde pad doorsturen (nooit het rauwe), met een test in de tests van
   `oo/paths.py` en een controle in `office_real` dat `g_defaultThemes` er is.~~ Bij de afronding
   van de integratie zo hersteld, in de proxy: het padbeleid blijft een leeg segment weigeren,
   en alleen een pad dat samengevoegd STATIC is, gaat samengevoegd naar DS
   ([o1.md](o1.md), afwijking 19). Met de echte DS heeft de pptx-editor via de app nu 36
   ontwerpen, en geeft `/office/` bij het openen nergens 404
   (`test_office_o2_real.py::test_werkblad_en_presentatie_openen`).
2. **Elke opening via een link wacht ±0,3 s op React.** Het chunk van de editorpagina is 10–40 ms
   na de refresh binnen, maar de pagina komt pas ±300 ms na het tonen van de terugval van
   `<Suspense>` op het scherm: React 19.3 houdt het tonen van een lazy onderdeel na een terugval
   tot 300 ms tegen (`FALLBACK_THROTTLE_MS = 300` in `react-dom`). In alle 30 openingen (koud en
   warm) begint `GET /api/v1/office/status` 312–325 ms na het einde van de refresh. Dat is
   ±0,3 s van de ±0,45 s van de schil, en het geldt ook voor andere lazy routes van de schil bij
   een eerste bezoek. Geen fout, wel verloren tijd; een oplossing (de route laden zonder
   terugval) hoort bij de schil en is niet gedaan in deze stap.
3. **A22 zit in DS zelf, en verklaart de uitschieter van 21,8 s.** In de eerste set kwam één
   warme pptx-opening **rechtstreeks bij DS** niet klaar (geen `onDocumentReady` binnen 90 s),
   met in de iframe `Uncaught TypeError: Cannot read properties of undefined (reading 'extend')`
   in `ColorPaletteExt.js:47`: precies A22 uit de spike, nu zonder de app ertussen. In de spike
   gebeurde dat rechtstreeks 0 van 17 keer, en daarom leek het aan de proxy te liggen. Via de app
   kwam A22 in deze metingen niet voor (0 van 62 openingen, 0 keer opnieuw geladen). De
   uitschieter van een eerdere poging (warm pptx 21,8 s) past daar precies bij: 20 s wachten op
   `onAppReady`, dan laadt de editorpagina de editor één keer opnieuw (O2), en een warme opening
   duurt ±1,8 s. Dat de pagina die herhaling heeft, blijft dus nodig.

4. **Een docx met veel opmaak net onder de grens opent niet in de editor.** De docx van
   3,98 MiB XML (355 hoofdstukken, elk met kopjes, gekleurde en gemarkeerde tekst en een tabel)
   kwam in de sandbox 0 van 3 keer klaar binnen 120 s. DS deed zijn deel snel (`Editor.bin` 2 s na
   het openen, x2t 54–73 MB); de renderer van Chromium bleef daarna op 100 % van één core staan
   met ±1 GB geheugen, en DS sloot na 45 s de WebSocket (`/doc/<key>/c/` 101, 45,0 s in de log
   van DS). In een eerdere poging stond de renderer na 8 minuten nog zo. Pptx en xlsx net onder
   hun grens openen wel (3,5–4,3 s). Of een snellere machine hem wel opent, is niet gemeten. De
   grens van 4 MB beschermt DS (geen OOM), maar is voor een docx met zoveel opmaak te ruim om
   hem ook te bewerken. Niet veranderd in deze stap. Bijvangst in het meetscript: een
   geannuleerde aanroep van Playwright komt pas terug als de renderer weer antwoordt, dus
   `office_crowd.py` stopt de browser dan zelf (op PID).
5. **Eén trage `/healthcheck` zet DS 30 s op down.** De gezondheidslus (`app/office/health.py`)
   kijkt elke 30 s, en één mislukte controle is genoeg voor down; terug naar ok kan pas bij de
   volgende ronde. Een OOM van x2t maakt de hele container een paar seconden traag (hierboven:
   7 keer geen `true`, tot 5,0 s); valt de ronde daarin, dan kan tot 30 s niemand een editor
   openen ("De editor is nu niet bereikbaar"), terwijl DS na de OOM gewoon doorwerkt. Gezien in
   ronde 3 van de XML-meting. Voorstel: pas down na twee mislukte controles na elkaar, of na een
   mislukte controle na 5 s opnieuw kijken. Niet veranderd: de toestanden van Keuze 28 en hun
   tests horen bij een eigen stap.
6. **Met veel bewerkers en een volle machine vallen soms de eerste woorden weg, in de editor
   zelf.** Bij 20 bewerkers (5 × 4) ontbraken 10 van de 1043 getypte woorden (3, 5 en 2 per
   ronde). Geen van die 10 stond in de tekst die de editor zelf had vlak voor Sluiten (gelezen
   uit sdkjs met `asc_EditSelectAll` en `pluginMethod_GetSelectedText`), en elk woord dat daar
   wel stond, zat na status 2 in de docx: het opslaan verloor niets. Alle 10 waren het eerste tot
   vierde woord van een bewerker (bijvoorbeeld `r2d4u3w001`–`w003`), getypt in de eerste
   seconden na het openen, terwijl de 20 tabbladen van Chromium de 4 cores vol hielden. Met 20
   op één document hetzelfde: 19 van de 1033, allemaal `w001`–`w005`. Bij 2 bewerkers 0 van 145,
   bij 8 0 van 532; een eerdere ronde van 8 miste 3 eerste woorden van één bewerker. Dat is dezelfde soort als afwijking 32 van [o2.md](o2.md) (de editor laat vlak na
   het openen een toets vallen), maar dan hele woorden. Een echte gebruiker ziet het woord niet
   verschijnen; het is geen stil verlies. Niet onderzocht waar in de editor de toetsen heen
   gaan; de app ziet ze nooit.
7. **Na een kill kwam DS één keer niet meer op: een oud pid-bestand van supervisord.** Na de
   tweede `docker kill` (ronde 2 van de kill-meting, 03:01 UTC) draaide de container, maar zonder
   supervisord, dus zonder docservice en converter; nginx gaf 502 op `/healthcheck`, 14 minuten
   lang. In de log van de container staat "Starting supervisor: " zonder "supervisord.", en
   `/var/run/supervisord.pid` was nog van het vorige leven (pid 123, 02:48:48). Het startscript
   van het image doet `service supervisor start`, en dat is `start-stop-daemon --start --pidfile
   /var/run/supervisord.pid --startas …`, zonder `--exec`: bestaat er een proces met dat nummer,
   dan denkt het dat supervisord al draait en start het niets. In een nieuwe container lopen de
   nummers elke keer bijna gelijk op (supervisord kreeg in deze container pid 131, 129, 129, 129
   en 123), dus de kans dat het oude nummer net bezet is, is groot. Het bestand blijft na elke
   stop staan (ook na `docker stop`: de kernel stopt supervisord, niemand ruimt op); de andere 5
   herstarts van vannacht gingen goed. Na `rm /var/run/supervisord.pid` en `docker restart` kwam
   hij gewoon op. Gevolg in productie: `restart: unless-stopped` start de container wel opnieuw,
   maar DS blijft weg tot iemand ingrijpt; de app meldt na 2 minuten `office_down` (O5), editors
   openen niet. Herstel: `docker compose exec office rm -f /var/run/supervisord.pid` en
   `docker compose restart office`, of `docker compose up -d --force-recreate office` (alles van
   DS staat in volumes). Voorstel: in `compose.office.yaml` een `entrypoint` die eerst dat
   bestand weghaalt en dan `/app/ds/run-document-server.sh` start, met een test die DS een paar
   keer killt en start; en de herstelregel in het runbook. Niet veranderd in deze stap: het is
   infra, en zonder die test is het niet aangetoond. Bij de afronding van de integratie zo
   gebouwd ([o6.md](o6.md), afwijking 24): het `entrypoint` van `compose.office.yaml` en
   `e2e/office_ds.sh` haalt het bestand weg en start dan het script van het image. Het bewijs
   maakt het oude nummer zeker bezet (een pid-bestand met `1`, dan `docker restart`): met het
   entrypoint van het image bleef `/healthcheck` 502 en was er geen supervisord, met het nieuwe
   was hij na 36 s 200. De installatietest doet dat in stap S, met `docker compose stop` en
   `start`. Het bestand blijft niet alleen na een kill staan, maar na elke stop: het risico gold
   dus elke herstart van dezelfde container, ook een gewone herstart van de LXC.

## Herhaling

De tabel is de tweede set (`office_run.py`, alle vijf de scripts na elkaar, 23:20–23:40 UTC). Een
eerste set van `office_open.py` (23:05–23:18 UTC, ook 5 runs per soort, met de controle) gaf
hetzelfde beeld:

| | docx | xlsx | pptx |
|---|---|---|---|
| koud via de app | 3,14 s (2,89–3,25) | 2,96 s (2,68–3,34) | 2,70 s (2,65–2,79) |
| warm via de app | 2,56 s (2,47–4,76) | 2,62 s (2,36–2,72) | 2,37 s (2,32–2,43) |
| cpu bij koud openen | 0,99 s (0,97–1,00) | 0,84 s (0,78–0,94) | 0,82 s (0,73–0,88) |
| rechtstreeks koud / warm | 2,77 / 2,09 s | 2,75 / 2,17 s | 2,48 / 2,11 s |

In die eerste set werkte de sessie zelf soms tegelijk (het proces van de sessie zat op dat moment
op ±1 core); de warme docx van 4,76 s viel in zo'n moment. Daarom is de tweede set gemeten
terwijl de sessie alleen wachtte.
