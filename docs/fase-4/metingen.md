# Metingen fase 4

De metingen van §4.7 van het [bouwontwerp](bouwontwerp.md), in de sandbox, bij de integratie.
**Deel 1**: openen, de cpu en het geheugen van de app, opslaan, de invoercontrole en het
groottebudget. **Deel 2**: de XML-grens bij 2 GB, conversies, veel bewerkers tegelijk, stoppen en
een kill van DS. Wat nog niet gemeten is, staat onderaan.

Het blok tussen `<!-- gemeten -->` en `<!-- /gemeten -->` schrijft `e2e/perf/office_run.py`;
alle getallen staan in [metingen.json](metingen.json). De rest is handwerk. Opnieuw meten: zie
"De metingen (§4.7)" in [e2e.md](e2e.md).

## Kort

- **Gehaald:** koud openen via de app (2,6–3,0 s, doel 5 s), de cpu van de app bij koud openen
  (0,87–0,94 core-seconde, doel 1; krap bij docx), het geheugen met 50 tunnels (+14,8 MB, doel
  20 MB), opslaan (0,09–0,11 s van callback tot versie, doel 2 s), de invoercontrole (0,015 en
  0,29 s cpu, doel 1 s), de eerste lading (+0,37 KB, doel 0,5 KB) en het chunk van de editor
  (3,9 KB, doel < 50 KB).
- **Niet gehaald:** warm openen (2,3–2,5 s, doel 2 s) en de groei van de route Bestanden
  (+2,94 KB, doel < 1 KB).
- **Wat de metingen verder lieten zien:** de presentatie-editor krijgt via de app de lijst met
  ingebouwde thema's niet (404 op `themes//themes.js`), elke opening via een link wacht ±0,3 s
  op React, en de fout A22 (de pptx-editor komt niet klaar) zit in DS zelf. Zie
  [Bevindingen](#bevindingen).

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

- Machine: 4 cpu's (Intel(R) Xeon(R) Processor @ 2.10GHz), 15,7 GiB RAM, kernel 6.18.44-fc-v165, Python 3.12.13
- Chromium 141.0.7390.37 (Playwright, headless)
- DS: container `meet-ds` (mirror.gcr.io/onlyoffice/documentserver@sha256:ca17889f10b11141cc6441836aebdd2835cf3e1b49681b2fd6e155c94b7850b3), geheugen 2,0 GiB, 2,0 cpu's, pids 512; grenzen toegepast: ja

| Wat | Doel | Gemeten | Gehaald | Hoe |
|---|---|---|---|---|
| Koud openen via de app | ≤ 5,0 s | docx 2,97 s (2,74–3,02, n=5); xlsx 2,87 s (2,79–3,06, n=5); pptx 2,61 s (2,5–2,96, n=5) | ja | office_open.py, 5× per soort (proef.docx/.xlsx/.pptx, elke keer een nieuw bestand), page.goto(/bewerken/<id>) tot de statusregel Bewerken zegt; koud: een nieuw browserprofiel, warm: het tweede bestand in datzelfde profiel (profiel disk); mediaan (kleinste–grootste) |
| Warm openen | ≤ 2,0 s | docx 2,47 s (2,26–2,61, n=5); xlsx 2,54 s (2,37–2,56, n=5); pptx 2,34 s (2,17–2,62, n=5) | nee | office_open.py, dezelfde runs (zie koud openen) |
| Cpu van de app bij koud openen | ≤ 1 core-seconde | docx 0,94 s (0,82–1,12, n=5) (322 verzoeken, 21,5 MB); xlsx 0,87 s (0,82–0,91, n=5) (306 verzoeken, 10,2 MB); pptx 0,9 s (0,73–0,94, n=5) (299 verzoeken, 9,8 MB) | ja | office_open.py, dezelfde koude runs: utime + stime van het serverproces van vóór page.goto tot 2 s zonder nieuw verzoek, min het verbruik in rust; verzoeken: de toegangslog van de server, bytes: Resource Timing (mediaan) |
| Geheugen van de app met 50 tunnels | ≤ +20 MB | +14,82 MB (14,18–15,12, n=3) open, +14,88 MB (14,23–15,15, n=3) na 30,0 s; alle 50 open: ja | ja | office_tunnels.py, 3 rondes (elk een nieuwe server), 10 gebruikers × 5 docx, VmRSS mediaan van 3; de eerste keer per server telt |
| Opslaan: callback tot versie in de database | ≤ 2 s voor 1 MB | Nu opslaan (6) 0,088 s (0,073–0,107, n=9); sluiten (2) 0,106 s (0,092–0,113, n=3) | ja | office_save.py, docx van 1,05 MiB (een foto), 3 rondes × (3× Nu opslaan + sluiten) in de echte editor; CallbackClock: van de eerste byte van de callback tot de kop van het antwoord (de app antwoordt na de commit) |
| Invoercontrole: xlsx 10 MB XML, pptx 80 MB foto's | ≤ 1 s cpu, in een thread | xlsx 0,015 s (0,015–0,016, n=5) cpu, lus max 1,2 ms; pptx 0,293 s (0,257–0,328, n=5) cpu, lus max 2,2 ms | ja | office_inspect.py, 5× per bestand, inspect.check() zoals de app hem aanroept (cache leeg), cpu = time.thread_time() van de controle; xlsx 10,0 MiB XML (net onder de grens van 10 MiB) in 1,6 MB, pptx 20 foto's (JPEG, deflate) 80,6 MB |
| Budget: eerste lading | groeit ≤ 0,5 KB | 93,75 → 94,12 KB (+0,37 KB) | ja | office_budget.py: npm run build en budget.mjs --json, 3ccee0a en e2c47d9 |
| Budget: chunk van de editor | < 50 KB gzip | 3,94 KB | ja | office_budget.py: npm run build en budget.mjs --json, 3ccee0a en e2c47d9 |
| Budget: route Bestanden | groeit < 1 KB | 39,42 → 42,36 KB (+2,94 KB) | nee | office_budget.py: npm run build en budget.mjs --json, 3ccee0a en e2c47d9 |
| XML-grens bij 2 GB: net onder de grens per soort, ook opmaak, formules, veel pagina's | geen OOM van x2t; docservice blijft | docx (opmaak, tabellen) 3,98 MiB XML (355 hoofdstukken, 355 tabellen): 3/3 omgezet, tot klaar of mislukt 22,5 s (22,5–23,5, n=3), x2t tot 539 MB, DS tot 1028 MB, OOM 0; docx (heel veel pagina's) 3,98 MiB XML (96937 pagina's): 0/3 omgezet, tot klaar of mislukt 29,4 s (26,8–66,5, n=3), x2t tot 1722 MB, DS tot 2048 MB, OOM 3; pptx 3,97 MiB XML (401 dia's): 3/3 omgezet, tot klaar of mislukt 8,5 s (8,3–9,4, n=3), x2t tot 411 MB, DS tot 853 MB, OOM 0; xlsx (formules) 9,98 MiB XML (15376 rijen, 6 formules per rij): 3/3 omgezet, tot klaar of mislukt 18,5 s (18,5–18,5, n=3), x2t tot 576 MB, DS tot 1105 MB, OOM 0; odt 1,98 MiB XML (235 hoofdstukken): 3/3 omgezet, tot klaar of mislukt 12,5 s (12,3–13,5, n=3), x2t tot 453 MB, DS tot 950 MB, OOM 0; ods 1,98 MiB XML (1392 rijen, 4 formules per rij): 3/3 omgezet, tot klaar of mislukt 3,4 s (3,3–3,4, n=3), x2t tot 190 MB, DS tot 671 MB, OOM 0; odp 1,98 MiB XML (955 dia's): 3/3 omgezet, tot klaar of mislukt 12,5 s (11,4–12,5, n=3), x2t tot 587 MB, DS tot 1096 MB, OOM 0. docservice bleef hetzelfde proces: ja. Geopend in de editor (hooguit 120 s): docx 0/3, docx_pages 2/3 (5,0 s (4,8–5,1, n=2)), pptx 3/3 (4,2 s (4,1–4,3, n=3)), xlsx 3/3 (3,6 s (3,5–4,0, n=3)) | nee | office_xml.py, 3 rondes × 7 bestanden (elke ronde nieuwe bestanden, net onder de grens van inspect: 16 KiB eronder), via de app: upload → voorbeeld → pdf-taak → DS /converter; DsWatch elke 0,25 s (geheugen van de cgroup van DS, rss van x2t, OOM-kills uit dmesg, het proces van docservice, /healthcheck); daarna elk bewerkbaar bestand openen in de editor |
| Conversie klein / 401 pagina's | ±1 s / ≤ 60 s | – | niet gemeten | office_convert.py |
| Conversie van 401 pagina's terwijl een editor opent | openen ≤ 10 s; /healthcheck blijft true | – | niet gemeten | office_convert.py: niet gemeten |
| 20 bewerkers (5 documenten × 4), 60 s typen | alle teksten opgeslagen; cpu en RAM van DS en app vastgelegd | – | niet gemeten | office_editors.py: niet gemeten |
| Stoppen met open editors | alles opgeslagen; docker stop ≤ 60 s | – | niet gemeten | office_outage.py --mode stop: niet gemeten |
| Kill van DS met open editors | verlies ≤ de tijd sinds het laatste tussentijds opslaan (≤ 5 min) | – | niet gemeten | office_outage.py --mode kill: niet gemeten |

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
  net onder de grens in 3,5–4,3 s, de docx met de pagina's in ±5 s (x2t maakt daarvoor
  `Editor.bin` van 77 MB; het zware is de PDF van 97.000 pagina's). De docx met opmaak kwam 0
  van de 3 keer klaar (bevinding 4).
- **De rest heeft ruimte:** een ods van 1,98 MiB XML kostte x2t maar 190 MB, een odt en odp
  ±450–590 MB. De ODF-grens van 2 MB is dus voorzichtig (de spike zag x2t pas bij een odt van
  7 MB vastlopen). Niet veranderd: dat is een keuze voor het ontwerp, niet voor een meting.
- **Hoe:** `office_xml.py --rounds 3` onder de waakhond (16 min, geen ingreep, minst vrij 13,2 GB).
  Elke ronde nieuwe bestanden (een seed per run): DS bewaart conversies onder een key die de sha
  volgt, en een tweede keer hetzelfde bestand kwam in een eerdere poging meteen uit zijn cache
  (ook de fout -3), zonder x2t.

## Bevindingen

1. **De presentatie-editor krijgt via de app de ingebouwde thema's niet.** De pptx-editor vraagt
   bij elke opening drie keer `/office/<V>/sdkjs/slide/themes//themes.js` (met twee schuine
   strepen). Het padbeleid van de app (`oo/paths.py`, §3.10) weigert een leeg segment, dus de app
   geeft 404. DS zelf (nginx voegt de strepen samen) geeft 200 met `AscCommon.g_defaultThemes`,
   de lijst van 36 ingebouwde thema's. Bewijs: in de editor via de app is
   `AscCommon.g_defaultThemes` er niet, rechtstreeks bij DS heeft hij 36 namen (dezelfde
   container, dezelfde `proef.pptx`); de toegangslog van elke koude pptx-opening heeft
   `GET office (404)` 3 keer, docx en xlsx geen. Gevolg: in Bewerken van een presentatie
   ontbreken de ingebouwde ontwerpen (Ontwerp/thema wijzigen). Niet hersteld in deze stap: het
   padbeleid is de toegangscontrole, en een leeg segment toestaan is geen kleine wijziging.
   Voorstel: voor de klasse STATIC een leeg segment binnen het pad samenvoegen zoals nginx, en
   dan het samengevoegde pad doorsturen (nooit het rauwe), met een test in de tests van
   `oo/paths.py` en een controle in `office_real` dat `g_defaultThemes` er is.
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

## Nog niet gemeten

| Wat | Doel |
|---|---|
| Conversie klein / 401 pagina's | ±1 s / ≤ 60 s |
| Conversie van 401 pagina's terwijl een editor opent | openen ≤ 10 s; `/healthcheck` blijft `true` |
| 20 bewerkers (5 documenten × 4), 60 s typen | alle teksten opgeslagen; cpu en RAM van DS en app vastgelegd |
| Stoppen met open editors | alles opgeslagen; `docker stop` ≤ 60 s |
| Kill van DS met open editors | verlies ≤ de tijd sinds het laatste tussentijds opslaan (≤ 5 min) |
