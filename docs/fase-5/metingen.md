# Metingen fase 5

De metingen Ma–Mp van §4.5 en de belastingtest van §4.6 van het [bouwontwerp](bouwontwerp.md)
(revisie 2), in de sandbox, bij de integratie (stap 4 van §5, deel 2). Plus punt 9 van "Naar de
integratie" in [m10.md](m10.md): Keuze 42 op echte namen.

Alle getallen staan in [metingen.json](metingen.json). De scripts staan in
`api/tests/media/metingen/` (pytest neemt die map niet mee); elk script is één commando vanuit
`api/`, zie [Opnieuw meten](#opnieuw-meten). Ma, Mb en Mc in de browser zijn de e2e
`e2e/test_media_m6_perf.py` (marker `media_perf`), Mi en Mn de e2e van M7 en M9.

## Kort

| Meting | Grens | Gemeten: mediaan / slechtste | Uitkomst |
|---|---|---|---|
| Ma: direct, het eerste beeld | ≤ 2 s | 436 / 450 ms (VP9); CI met H.264 477 / 491 ms | gehaald |
| Mb: transcode, het eerste beeld | e2e ≤ 5 s; zonder browser ≤ 2,5 / 3,0 s, naast 2 ≤ 5 s | e2e (bron 720p) 1663 / 1844 ms (VP9), CI met H.264 1512 / 1532 ms; zonder browser (bron 1080p) 1,34 / 1,66 s, naast 2 1,23 / 1,59 s | gehaald; 1080p in de browser: LXC |
| Mc: zoeken | tabel 1,5 / 1,0 / 3,0 s; "Klaar als" ≤ 3 s | direct 2020 / 2095, remux 885 / 918, transcode 2343 / 2485 ms | "Klaar als" gehaald; direct haalt de tabel niet (bevinding 4) |
| Md: 3 transcodes | §4.6 punt 7, per fase | a 4 van 5, b 5 van 5 | niet gehaald: haperen na de sprong (bevinding 3) |
| Md met een docx elke 30 s | idem | a 2 van 5, b 5 van 5; 85 × docx klaar in 4,6 / 8,3 s | niet gehaald: haperen na de sprong (bevinding 3) |
| Md met een herstart van DS | §4.6 d | a 1 van 2 (haperen na de sprong, niet door de herstart); b 0 van 2 (503) | niet gehaald (bevinding 7) |
| Md fase c: de app herstart | weer spelen ≤ 10 s na "ready", daarna niet haperen | 1,9 / 7,2 s; 4 van 5 zonder haperen | niet gehaald: 1 keer haperen (bevinding 3) |
| Me: 2 × 1080 | 0 keer haperen | 0, in 10 fasen | gehaald, met lagere treden dan §4.5 |
| Mf: HDR | 0 keer haperen | 0, in 15 runs | gehaald |
| Mg: de API | p95 ≤ 200 ms | 70,9 / 80,4 ms (Md, fase a); met OnlyOffice ten hoogste 104,5 ms | gehaald |
| Mh: geheugen | ≤ 3 GB | ten hoogste 793 MB | gehaald |
| Mi: voortgang | p95 ≤ 2 s | 0,097 / 0,101 s | gehaald |
| Mj: knippen | ≤ 0,05 core per stroom | 0,007 / 0,007 core | gehaald |
| Mk: de factor `f` | binnen 15 % | 44–59 % te hoog voorspeld (mediaan per trede) | niet gehaald (bevinding 6) |
| Ml: scannen | ≤ 60 s per 1.000 bestanden | 0,67 / 0,75 s | gehaald |
| Mm: tegendruk | ≈ 0 | 0,00 / 0,01 CPU-s in 4 s | gehaald |
| Mn: gapless | ≤ 1 sample | FLAC 0, Opus 0,014 | gehaald |
| Mo: namen | ≤ 0,5 s per 10.000 | 0,385 / 0,428 s | gehaald |
| Mp: grootte | Keuze 33 | 14 van 14 regels binnen | gehaald |
| Keuze 42 (punt 9) | — | precisie 0,971 (`nep`) en 0,953 (`ruim`); naar Nakijken 21 en 18 van 125 | geen fout tegen de keuze; voorstellen voor Jonas (bevinding 5) |

Twee fouten in onze code gevonden en gerepareerd, elk met een test (bevinding 1 en 2); een
derde vonden de checks van dit deel (de worker en een SIGTERM vlak na zijn start, `bf2f766`,
zie [integratie.md](integratie.md#metingen)). Wat niet gehaald werd, is niet bijgesteld om te
slagen; de oorzaak staat bij elke bevinding:

- **haperen na een sprong of een herstart van een producent** (bevinding 3): de startvoorrang
  van Keuze 26 geldt alleen tot het eerste segment van een sessie. Het gebeurde in 5 van de 12
  runs van fase a met 3 sessies (vaker met OnlyOffice ernaast) en in 1 van de 5 van fase c;
  nooit zonder sprong of herstart (fase b: 0 keer in 15 runs). Een voorstel voor het ontwerp;
  in fase 6 gebouwd als B71 (voorstel), en half opgelost (zie bevinding 3);
- **direct play bij zoeken** (bevinding 4): Chromium zelf, en het netwerk;
- **Mk** (bevinding 6): de tabel van Keuze 24 is met opzet een bovengrens;
- **de herstart van DS** (bevinding 7): met de `f` van de sandbox laat de reserve bij `B − 1,0`
  geen plaats over voor een nieuwe kijker; vanaf `f` ±1,07 al niet.

De treden van Vraag 3 en §4.5 vragen een `f` rond 1,0; de sandbox mat 1,22–1,40. Dat is alleen
op de LXC te zien ([Wat alleen de LXC of CI kan laten zien](#wat-alleen-de-lxc-of-ci-kan-laten-zien)).

## Omstandigheden

- **Machine:** de sandbox, een Firecracker-VM met 4 vCPU (Intel Xeon @ 2,10 GHz), 15,7 GiB RAM,
  geen swap, geen GPU; ffmpeg 6.1.1 (Ubuntu). Het image heeft ffmpeg 7.1.5; die getallen komen
  uit CI.
- **Regel 14:** de worker draait altijd onder `taskset -c 0-2` (de lokale worker van `kit.App`,
  en de fixture `media` van de e2e voor `media_perf`), met `PLATFORM_MEDIA_MAX_PROCS=3`
  (regel 13). Er liepen nooit meer dan 3 ffmpeg tegelijk: elk script telt ze elke 0,5 s
  (`ffmpeg_max` in metingen.json, overal ≤ 3).
- **Geheugen:** een waakhond (`kit.Watchdog`) kijkt elke seconde naar `MemAvailable` en stopt
  op pid wat de meting startte als het onder 1,5 GiB zakt. Hij greep nergens in; het minst vrije
  geheugen was 12,3 GiB (fase c; per script in metingen.json). Op `/` bleef ≥ 16 GB vrij.
- **Wat er naast liep:** een andere sessie draaide in dezelfde sandbox de e2e van
  `office_real`, vastgezet op core 3: gemiddeld 0,4–1,1 core (`anders_cores` per fase in
  metingen.json). De worker had cores 0–2 dus voor zich, maar de app (niet vastgezet), de
  spelers en de browser deelden core 3 met die sessie. Elk script wacht vóór elke herhaling tot
  cores 0–2 onder 0,6–0,8 core zitten over 3 s (`kit.quiet`; de gemeten waarde staat er per run
  bij als `rust_voor_cores`). Eerst keek `quiet` naar de hele machine; door de sessie op core 3
  kwam die nooit onder de grens, dus kijkt hij nu alleen naar de cores van de worker.
- **De factor `f`:** elk script meet eerst `bench` (zoals de knop op /beheer) en gebruikt die.
  In de sandbox kwam `f` op 1,22–1,40 en `f_dec` op 1,22–1,53 (per script in metingen.json): de
  sandbox is 20–40 % trager dan de referentie-CPU van de tabel van Keuze 24, en niet elke keer
  even traag. Daardoor kiest de toelating hier lagere treden dan op de LXC, en niet elke run
  dezelfde (zie [Md](#md-3-transcodes)).
- **Browser:** Chromium 141.0.7390.37 uit `/opt/pw-browsers`, headless, zonder H.264 (S13). CI
  draait Chrome for Testing 156.0.8078.4, met H.264.
- **Code:** `a38604d` plus de fixes van dit deel (zie [Bevindingen](#bevindingen)). Md liep
  vóór de fix van de reserve (bevinding 1); met de `f` van die run geeft de fix dezelfde treden
  (nagerekend). Fase c, Mb zonder browser, Mf en Keuze 42 liepen opnieuw na hun fix; de
  varianten van Md met OnlyOffice liepen op `bd93489` (na beide fixes).
- **CI:** run [41341675278](https://github.com/Jonasz1996/alles-in-1-file-office-media-platform/actions/runs/41341675278)
  (1 november, commit `d850c4f` van de lead), job `e2e-media-perf` (124952942710). Die commit
  zit niet onder `a38604d` (beide komen uit `6e7bd75`), maar sinds `6e7bd75` is er in
  `api/app`, `web/src/player` en de e2e van de metingen niets anders dat Ma–Mc raakt: de lead
  veranderde de installatietest, `e2e/test_media_m6.py` en de viewer van bestanden.

## Ma: direct play, het eerste beeld

**Hoe.** De e2e `test_m6_ma_eerste_beeld_direct` (marker `media_perf`), 10 keer: een film van
1080p24, 19,6 Mbit/s, 120 s (VP9 en Opus in WebM hier; in CI ook H.264 en AAC in MP4 met
`test_m6_ma_eerste_beeld_direct_h264`), het netwerk via CDP op 40 Mbit/s en 20 ms, van de klik
(een navigatie binnen de app) tot de eerste `requestVideoFrameCallback`.

| | Mediaan | Slechtste | Grens |
|---|---|---|---|
| Sandbox, VP9 (Chromium 141) | 436 ms | 450 ms | ≤ 2 s |
| CI, VP9 (Chrome for Testing 156) | 346 ms | 369 ms | ≤ 2 s |
| CI, H.264 + AAC | 477 ms | 491 ms | ≤ 2 s |

**Gehaald**, met ruim marge. H.264 in de browser kan alleen CI (en de LXC): Chromium 141 hier heeft
geen H.264 en geen AAC, en de test met H.264 slaat hier over.

## Mb: transcode, het eerste beeld

**Hoe, in de browser.** De e2e `test_m6_mb_eerste_beeld_transcode`, 10 keer: `hevc10_hdr10.mkv`
(HEVC 10 bit HDR10, 1280×720, 6 s) naar 720, van de klik tot het eerste beeld. Hier naar VP9, in
CI naar H.264. De bron is dus 720p, geen 1080p zoals "Klaar als" vraagt; een 1080p-bron mat
alleen de speler zonder browser (hieronder). Een 1080p-film in de browser laat de LXC zien
(review van de integratie, K4).

| | Mediaan | Slechtste | Grens |
|---|---|---|---|
| Sandbox, naar VP9 | 1663 ms | 1844 ms | ≤ 5 s |
| CI, naar H.264 | 1512 ms | 1532 ms | ≤ 5 s |

**Hoe, zonder browser.** `start.py`: een speler in Python zoals die van M4, van `POST /play` tot
en met segment 0 (map.json, de init en `0.m4s`), 5 keer. De bron: 1080p24 H.264 met ruis,
6 Mbit/s, een keyframe per 2 s, zoals de bronnen van de belastingtest; het profiel vraagt
`max.bitrate_kbps` 4000, dus transcode (reden `bitrate`).

| Zonder browser (`f` 1,23) | Mediaan | Slechtste | Trede | Grens |
|---|---|---|---|---|
| 1080 naar 1080, alleen (`max_transcodes` 1) | 1,34 s | 1,66 s | 1080 veryfast | ≤ 2,5 s / ≤ 3,0 s |
| 1080 naar 720, alleen (kwaliteit 720) | 0,72 s | 0,77 s | 720 veryfast | ≤ 2,5 s / ≤ 3,0 s |
| naast 2 lopende transcodes (de derde) | 1,23 s | 1,59 s | 480 veryfast | ≤ 5 s |
| alle drie tegelijk (15 starts) | 1,14 s | 2,43 s | 1080 + 480 + 480 | — |

**Gehaald**, alle vier. "Alleen" draait met `max_transcodes` 1: met 3 plaatsen houdt de reserve
van Keuze 26 twee plaatsen vast, en dan kiest de toelating hier 720 voor de eerste kijker; met
één plaats meet het de start van 1080 naar 1080 zoals §4.5 vraagt. Naast 2 andere krijgt de
derde kijker in de sandbox 480 veryfast (de eerste twee kregen 1080 veryfast en 480 veryfast);
op de LXC verwacht §4.5 720 superfast. De eerste run van dit script vond bevinding 1: de derde
kijker kreeg 503 met een plaats vrij.

CI (`e2e-media-perf`, de virtuele speler van M4 zonder browser, 3 runs, de slechtste): 1080
alleen 0,98 s, naast twee andere 1,98 s, een sprong naar 70 % 1,28 s, remux 0,15 s.

## Mc: zoeken

**Hoe.** De e2e `test_m6_mc_zoeken_en_weer_lopen`, 10 keer per soort: `currentTime` naar 70 %
(een stuk dat nog niet gemaakt is), tot de video 0,25 s voorbij het doel speelt (die 0,25 s zit
erin). Direct is de WebM van Ma (20 Mbit/s, het netwerk op 40 Mbit/s en 20 ms), remux een film
van 300 s, en transcode de HEVC van 60 s naar 720.

| | Sandbox: mediaan / slechtste | CI: mediaan / slechtste | Tabel §4.5 | "Klaar als" |
|---|---|---|---|---|
| direct | 2020 / 2095 ms | 1814 / 1922 ms | 1,5 s | ≤ 3 s |
| remux | 885 / 918 ms | 855 / 856 ms | 1,0 s | ≤ 3 s |
| transcode | 2343 / 2485 ms | 2316 / 2367 ms | 3,0 s | ≤ 3 s |

Remux en transcode halen de tabel, en alle drie de 3 s van "Klaar als". **Direct haalt de 1,5 s
van de tabel niet**, hier en in CI (bevinding 4). Met het netwerk op 400 Mbit/s in plaats van
40 (verder gelijk, 10 keer; de plugin `meet_netwerk.py` verandert alleen `PERF_NETWORK` van de
e2e):

| Op 400 Mbit/s en 20 ms | Mediaan | Slechtste |
|---|---|---|
| direct | 1487 ms | 1628 ms |
| remux | 791 ms | 832 ms |
| transcode | 2173 ms | 2274 ms |

Het netwerk is bij direct dus ±0,5 s; wat overblijft (±1,2 s plus de 0,25 s spelen) is
Chromium zelf.

## Md: 3 transcodes

**Hoe.** `belasting.py`: de belastingtest van §4.6 in de app, gestart zoals de knop op /beheer
(`POST /api/v1/admin/media/loadtest {streams: 3, seconds: 300, codec: "h264"}`), 5 keer, met de
fasen a (gespreid, met een sprong op 60 s) en b (tegelijk). Het script meet ernaast de processen
(elke 0,5 s via `/proc`), de API van buitenaf (Mg) en elke 2 s het overzicht van /beheer (de
trede en de snelheid per sessie, het budget). De bronnen zijn de drie van de worker (`synth`,
3 × 300 s 1080p H.264, 6 Mbit/s); de grenzen per fase zijn die van §4.6 punt 7, en het rapport
van de app beslist.

**De treden.** De standaard van Vraag 3 is 1080 veryfast + 720 superfast + 720 superfast, maar de
toelating kiest de treden met de `f` van de machine (Keuze 26). Met `f` 1,40 (en `f_dec` 1,53) van
deze run koos ze elke keer **720 veryfast + 480 veryfast + 480 veryfast**: de derde kijker moet
passen met wat de eerste twee al gebruiken, en 720 superfast kost bij deze `f` te veel. De
combinatie van Vraag 3 vraagt `f` ≤ ±1,0 (met `f_dec` = `f`): die is alleen op de LXC te meten
(als de CPU van Jonas zo snel is als de referentie).

| Fase | Geslaagd | Start: mediaan / slechtste | Haperen | Snelheid: mediaan / laagste | Grens |
|---|---|---|---|---|---|
| a (gespreid, sprong op 60 s) | 4 van 5 | 1,09 / 2,01 s | 1 keer (0,6 s) in run 1, verder 0 | 2,56× / 2,08× | start ≤ 5 s, 0 keer haperen, ≥ 1,1×, API ≤ 200 ms |
| b (tegelijk) | 5 van 5 | 1,68 / 2,49 s | 0 | 1,85× / 1,63× | start ≤ 5 s, 0 keer haperen |

**Fase a haalt het in 1 van de 5 runs niet:** in run 1 duurde de sprong van de eerste speler
3,0 s tot het eerste segment, en daarna haperde hij één keer 0,6 s (bevinding 3). In de andere
runs duurde de sprong 0,01–2,5 s, zonder haperen. Er liepen nooit meer dan 3 ffmpeg tegelijk, de
cache groeide 28 MB per minuut.

### Md met OnlyOffice: een docx elke 30 s

**Hoe.** `belasting.py --office --docx-elke 30`: dezelfde belastingtest, met de echte DS
10.0.0.1 ernaast (`e2e/office_ds.sh`, met de grenzen van `compose.office-limits.yaml`: 2 cpu's,
2 GiB; niet vastgezet, dus ook op cores 0–2). Elke 30 s uploadt het script een docx van ±50
pagina's (2.400 alinea's, de generator van fase 4), en de app zet hem om naar pdf (O3; gemeten
tot de pdf er is). 5 runs (één en daarna nog vier; `f` 1,30 en 1,22).

| Fase | Geslaagd | Start: mediaan / slechtste | Haperen | Snelheid: mediaan / laagste |
|---|---|---|---|---|
| a | 2 van 5 | 1,64 / 2,35 s | 2, 1 en 1 keer in de runs 3–5 (samen 0,85, 0,91 en 0,10 s), verder 0 | 2,10× / 1,57× |
| b | 5 van 5 | 1,97 / 3,03 s | 0 | 1,71× / 1,47× |

Alle 85 docx werden pdf: 4,6 s mediaan, 8,3 s de langste (fase a 4,6 / 8,3 s, fase b 3,7 /
7,3 s). DS gebruikte ten hoogste 671 MB, de worker met zijn ffmpeg 692 MB, en de API bleef op
een p95 van 71,9 / 96,1 ms (fase a) en 48,4 / 67,2 ms (fase b).

**Fase a haalt het in 3 van de 5 runs niet**, en elke keer op dezelfde manier: de speler
waarvan de sprong op 60 s een nieuwe producent vroeg (zoeken 1,9–3,3 s; de 1080 in alle drie, de
720 er in run 3 bij) haperde daarna één keer 0,1–0,9 s. De speler waarvan de sprong in de cache
viel (0,01 s) haperde nooit, en fase b (geen sprong) ook nooit. Dat is bevinding 3, hier vaker:
de treden lagen hoger (1080 superfast + 720 superfast + 480 veryfast bij `f` 1,22; de eerste run,
met 720 veryfast + 720 veryfast + 480 veryfast bij `f` 1,30, haalde het), en DS zette ernaast om
met tot 2 cores die ook op 0–2 mogen. De machine zat in fase a op 2,5–3,1 van de 4 cores, waarvan
0,5–1,1 voor DS en de andere sessie samen (`anders_cores`).

### Md met een herstart van DS

**Hoe.** `belasting.py --office --ds-herstart 200`: 200 s na het begin van fase a doet het
script `docker restart` op DS (10,5 s), en loopt de test door; elke 2 s leest het /beheer (het
budget, en de staat van office). 2 runs (`f` 1,24 en 1,37). De tweede kwam erbij om de oorzaak
van fase b te vinden: hij bewaart het verloop van het budget rond de herstart
(`ds_herstart.verloop` in metingen.json). Dit is fase d van §4.6, in de test van de app.

| | Run 1 | Run 2 | Grens (§4.6 d) |
|---|---|---|---|
| fase a, met de herstart op 200 s | geslaagd | 2 keer haperen (0,73 en 0,71 s), na de sprong op 60 s | de lopende streams haperen niet |
| de app ziet de herstart | — | 29 s erna: office `starting`, het budget van 3,0 naar 2,0 | — |
| fase b: 3 nieuwe kijkers, meteen daarna | 3 × 503 `media_busy` (cpu, `Retry-After` 30) | idem | een nieuwe kijker krijgt een lagere trede |

**De lopende streams haperden niet door de herstart.** Op 200 s had elke speler zijn bron al tot
het eind gevraagd (30 s buffer, en de producenten liepen op 2× met 60 s voorsprong); de twee keer
haperen in run 2 hoort bij de sprong (zoeken 4,75 en 0,85 s, bevinding 3).

**Een nieuwe kijker krijgt geen lagere trede, maar 503** (bevinding 7). De app ziet de herstart bij
de volgende ronde van de gezondheidslus (elke 30 s), en DS is dan `starting`: zijn
font-herstart komt nog (2 à 3 minuten na een start, fase 4; hooguit 15 minuten). Zo lang is het
budget `B − 1,0` = 2,0 (Keuze 26). De goedkoopste trede met de reserve voor de twee andere
plaatsen kost 3 × kosten(480 veryfast) = 3 × (0,38 × `f` + 0,18 × `f_dec`): 2,07 in run 1 en
2,31 in run 2, boven 0,9 × 2,0 = 1,8. In run 1 zag het overzicht van het script de overgang niet
meer vóór fase b begon; het 503 is hetzelfde.

### Md fase c: de app herstart

**Hoe.** `belasting.py --fase-c`: de spelers van de belastingtest buiten de app
(`app.cli media-loadtest --extern`, zoals op de LXC), 3 sessies, 300 s. Na 60 s
stopt het script de app op pid en start hem weer (in plaats van `dc restart platform`); de
spelers vragen dan elk een nieuwe sessie op hun plek. 5 keer, `f` 1,22 (`f_dec` 1,27). Grens
(§4.6 punt 7): alle drie spelen ≤ 10 s na "ready" weer, op de goede positie, en haperen daarna
niet.

| | Mediaan | Slechtste | Grens |
|---|---|---|---|
| de app weg tot "ready" | 3,2 s | 3,4 s | — |
| weer spelen na "ready" (15 herstarts) | 1,9 s | 7,2 s | ≤ 10 s |
| weer spelen na het wegvallen | 5,2 s | 7,5 s | — |
| start van de sessies (15) | 1,68 s | 4,20 s | ≤ 5 s |
| haperen per run | 0 | 1 (1,5 s) | 0 |

**4 van de 5 runs gehaald.** In run 3 kwam de speler op 1080 pas 6,7 s na "ready" weer (de
andere twee na 1,8 en 2,1 s), en haperde daarna één keer 1,5 s; zie bevinding 3. De treden na de
herstart waren elke keer 1080 + 720 + 480. De API van buitenaf bleef op een p95 van 22,6 /
31,5 ms (mediaan / slechtste run), de worker met zijn ffmpeg op ten hoogste 793 MB. Vóór de fix
van bevinding 2 faalde fase c in elke run.

## Me: 2 × 1080

**Hoe.** `belasting.py --sessies 2 --max-transcodes 2`: de belastingtest met 2 sessies, 300 s,
de fasen a en b, 5 keer. §4.5 verwacht 1080 veryfast + 1080 superfast; de treden kiest de
toelating met de `f` van de run (1,28, `f_dec` 1,47).

| Fase | Geslaagd | Start: mediaan / slechtste | Haperen | Snelheid: mediaan / laagste | Treden |
|---|---|---|---|---|---|
| a | 5 van 5 | 1,76 / 2,53 s | 0 | 2,37× / 2,01× | 1080 veryfast + 720 superfast |
| b | 5 van 5 | 1,80 / 3,24 s | 0 | 1,86× / 1,60× | 1080 veryfast + 720 superfast |

**Gehaald:** 0 keer haperen, in alle 10 fasen. De tweede kijker kreeg 720 superfast in plaats van
1080 superfast: bij deze `f` kost 1080 superfast te veel naast 1080 veryfast. De combinatie van
§4.5 vraagt een snellere CPU; op de LXC meten. De API (van buitenaf, p95) bleef op 24,6 /
49,3 ms (fase a) en 26,4 / 40,2 ms (fase b); de worker met zijn ffmpeg op ten hoogste 582 MB.

## Mf: HDR

**Hoe.** `hdr.py`: een eigen app met `hdr_1080` aan, een bron van HEVC 10 bit HDR10 (PQ,
BT.2020) 1080p24 met ruis, 150 s, en spelers zoals die van `start.py` die 120 s op 1× afspelen
met 30 s buffer (haperen: een segment dat er nog niet is als de afspeeltijd het nodig heeft).
Drie varianten, elk 5 keer, `f` 1,26 en `f_dec` 1,25:

- **alleen:** 1 HDR-kijker met kwaliteit 1080, `max_transcodes` 1. Met `auto` gaat HDR live
  hooguit naar 720, ook met `hdr_1080` ("`auto` bij HDR is 720 ... 1080 alleen als iemand hem
  kiest", Keuze 27); de eerste versie van het script vroeg `auto`, mat dus 720, en is
  weggegooid;
- **naast 2:** de HDR-kijker (kwaliteit 720), dan 2 SDR-kijkers (de bron van Mb, kwaliteit 720),
  `max_transcodes` 3; §4.5 verwacht 720 superfast voor de twee;
- **naast 1:** hetzelfde met 1 SDR-kijker en `max_transcodes` 2.

| Variant | Treden (de toelating) | Haperen (5 runs) | Cores van de worker (mediaan) | Grens |
|---|---|---|---|---|
| alleen | 1080 superfast | 0 | 2,22 | 0 keer haperen |
| naast 2 | 720 superfast (HDR) + 480 veryfast + 480 veryfast | 0 | 2,10 | 0 keer haperen |
| naast 1 | 720 veryfast (HDR) + 720 veryfast | 0 | 2,30 | 0 keer haperen |

**Gehaald:** 0 keer haperen in alle 15 runs. Alleen kreeg 1080 superfast, niet veryfast: 1080
veryfast HDR kost hier ±2,9 cores (2,30 met `f` en `f_dec`) en past niet onder 0,9 × B = 2,7.
Naast 2 andere kregen de
SDR-kijkers 480 veryfast in plaats van 720 superfast: de treden hangen aan `f` (zie Md).

## Mg: de API onder belasting

**Hoe.** Tijdens Md vraagt `belasting.py` elke seconde `GET /api/v1/files/root` en
`/api/v1/media/home` van buitenaf (een eigen http-client, met de cookie van de beheerder), en
neemt de p95 per fase. Het rapport van de app heeft zijn eigen p95 van binnenuit (de spelers
van de test vragen hetzelfde).

| | Mediaan | Slechtste | Grens |
|---|---|---|---|
| fase a, van buitenaf (p95) | 70,9 ms | 80,4 ms | ≤ 200 ms |
| fase b, van buitenaf (p95) | 40,6 ms | 48,8 ms | ≤ 200 ms |
| het rapport van de app (p95) | 54,0 ms | 65,9 ms | ≤ 200 ms |

**Gehaald.** Ook naast OnlyOffice: met een docx elke 30 s een p95 van 71,9 / 96,1 ms in fase a
en 48,4 / 67,2 ms in fase b, met de herstart van DS 104,5 en 90,1 ms in fase a (de herstart valt
erin). In fase c (de spelers buiten de app) 22,6 / 31,5 ms.

## Mh: geheugen

**Hoe.** De hoogste RSS van de worker met al zijn ffmpeg samen (elke 0,5 s via `/proc`), tijdens
Md en Mf. Het rapport van de app geeft alleen de worker zelf (±206 MB).

| Meting | Hoogste RSS: mediaan / slechtste run | Grens |
|---|---|---|
| Md, 3 transcodes | 566 / 567 MB (fase a), 570 / 570 MB (fase b) | ≤ 3 GB |
| Md met een docx elke 30 s | 686 / 686 MB (a), 692 / 692 MB (b) | ≤ 3 GB |
| Md met een herstart van DS | 686 en 605 MB (a) | ≤ 3 GB |
| Md fase c | 704 / 793 MB | ≤ 3 GB |
| Me, 2 × 1080 | 580 / 580 MB (a), 582 / 582 MB (b) | ≤ 3 GB |
| Mf, HDR | alleen 477, naast 2 600, naast 1 540 MB | ≤ 3 GB |

**Gehaald**, met ruim marge: het meeste is ffmpeg zelf (±200 MB per stroom). DS (een
eigen container, grens 2 GiB) kwam op ten hoogste 671 MB.

## Mi: voortgang synchroniseren

**Hoe.** De e2e `test_twee_tabbladen_verder_kijken_binnen_2_s` (M7), 5 keer, elk met 20 keer
een PUT van de voortgang in tabblad A tot het label in tabblad B verandert.

| | Mediaan | Slechtste | Grens |
|---|---|---|---|
| p95 per run (20 keer) | 0,097 s | 0,101 s | ≤ 2 s |

**Gehaald.** De langste enkele keer was 0,649 s (run 1); de eerste keer, met het opzetten van
de verbinding, 0,57–0,69 s.

## Mj: de kosten van het knippen

**Hoe.** `start.py`: 3 spelers die op 1× afspelen (30 s buffer, zoals de speler), en 60 s lang
de CPU van het proces van de app (het knippen, de cache en de http), gedeeld door 3. 5 keer.

| | Mediaan | Slechtste | Grens |
|---|---|---|---|
| CPU van de app per stroom | 0,007 core | 0,007 core | ≤ 0,05 core |

**Gehaald**; gelijk aan het prototype van S6 (≈ 0,007 core voor 3 × 6 Mbit/s).

## Mk: de factor f

**Hoe.** `start.py`, per trede 5 keer: de toelating gestuurd naar die trede (`max_transcodes` 1,
`cpu_budget` net onder de betere trede, de kwaliteit), dan één speler die na segment 0 niets meer
vraagt, zodat de producent vrij loopt tot de rem (60 s voorsprong). In het venster van 4 s
voorsprong tot 4 s voor de rem: de snelheid (de groei van de voorsprong op /beheer) en de CPU van
de worker met zijn ffmpeg (`/proc`). Gemeten kosten = cores / snelheid (de cores voor 1,0×);
voorspeld = `decide.cost` met de `f` en `f_dec` van de run (1,23 en 1,40). De bron is die van Mb
(1080p24 H.264, `testsrc2` met ruis, 6 Mbit/s).

| Trede | Gemeten: mediaan / hoogste | Voorspeld | Verschil: mediaan / grootste | Snelheid: mediaan / laagste |
|---|---|---|---|---|
| 1080 veryfast | 0,82 / 0,85 | 1,78 | −54 % / −57 % | 3,50× / 3,24× |
| 1080 superfast | 0,60 / 0,61 | 1,20 | −50 % / −52 % | 4,69× / 4,42× |
| 720 veryfast | 0,53 / 0,54 | 0,94 | −44 % / −49 % | 5,19× / 4,87× |
| 720 superfast | 0,37 / 0,40 | 0,79 | −53 % / −57 % | 6,39× / 6,10× |
| 480 veryfast | 0,29 / 0,32 | 0,72 | −59 % / −60 % | 7,46× / 7,11× |

**Niet gehaald** (grens: binnen 15 %; bevinding 6). De voorspelling ligt voor elke trede 1,8–2,4
keer boven wat het kost: de toelating rekent veilig, en kiest dus lagere treden dan de CPU aan
kan. Dat past bij de snelheden van Md (720 veryfast + 2 × 480 veryfast liepen op 2,1–2,8×).

## Ml: scannen

**Hoe.** `scannen.py`: per run een nieuwe map met 1.000 bestanden (600 in één map, die de scanner
in delen doet, en 4 × 100 in submappen), als rijen in de database met één gedeelde blob, dus
zonder analyse. Dan `POST /api/v1/media/libraries` (de eerste scan start vanzelf) en de tijd tot
de bibliotheek `idle` is met 1.000 items. 5 keer.

| | Mediaan | Slechtste | Grens |
|---|---|---|---|
| 1.000 bestanden | 0,67 s | 0,75 s | ≤ 60 s |

**Gehaald.**

## Mm: tegendruk

**Hoe.** Na elke run van Mk, als de voorsprong 30 segmenten (60 s) is en niet meer groeit: 4 s de
CPU van de worker met zijn ffmpeg. 25 keer (5 treden × 5).

| | Mediaan | Slechtste | Grens |
|---|---|---|---|
| CPU-seconden in 4 s | 0,00 | 0,01 | ≈ 0 |

**Gehaald**: met de rem staat ffmpeg stil (geblokkeerd op de pipe) en kost niets.

## Mn: gapless

**Hoe.** De e2e `test_gapless_de_fase_springt_niet` (M9), 5 keer: drie nummers van een sinus die
doorloopt, als FLAC en als Opus; de opname van de browser, en de fasefout op elke overgang in
samples.

| | Grootste sprong | Grens |
|---|---|---|
| FLAC (5 keer gelijk) | 0,0 sample | ≤ 1 sample |
| Opus (5 keer gelijk) | 0,014 sample | ≤ 1 sample |

**Gehaald.** Het laatste sample dat klinkt, lag bij Opus 1 sample van het verwachte eind (de test
staat 2 toe), bij FLAC er precies op.

## Mo: namen

**Hoe.** `namen.py`: de 10.000 namen van de fuzz-test van M2 (dezelfde vaste seed) door
`names.parse`, op één core van de worker, na een ronde om op te warmen, 5 keer, elke keer met de
cache van de parser leeg.

| | Mediaan | Slechtste | Grens |
|---|---|---|---|
| 10.000 namen | 0,385 s | 0,428 s | ≤ 0,5 s |

**Gehaald.**

## Mp: grootte

**Hoe.** `npm run budget` na `npm run build` (web/scripts/budget.mjs, de grenzen van Keuze 33),
op `a38604d`; dit deel veranderde niets in `web/`.

| Chunk | Grootte | Grens |
|---|---|---|
| eerste lading JS | 96,4 KB | 100 KB |
| speler (`src/player`) | 25,4 KB | 35 KB |
| mediapagina's (de grootste: `player/WatchPage.tsx`) | 25,8 KB | 45 KB |
| muziek (`src/music`) | 30,6 KB | 40 KB |
| route Bestanden | 43,6 KB | 45 KB |

**Gehaald**; alle 14 regels van het budget zijn binnen de grens (metingen.json).

## Keuze 42 op echte namen (punt 9)

Punt 9 van "Naar de integratie" ([m10.md](m10.md); m8.md open punt 8): een titel die het begin
van een andere is, in hetzelfde jaar, koppelt vanzelf ("Night Harbor" tegen "Night Harbor Blues":
gelijkenis 0,8 plus 0,2 voor het jaar). Is dat in de praktijk een probleem?

**Hoe.** `koppelen.py`: een corpus van 125 bestandsnamen zoals ze in een bibliotheek staan
(`koppelen_namen.tsv`: 93 films en 32 afleveringen van 20 series; scene-namen, jaren, 17 namen
waarvan de titel het begin van een andere is, vervolgen, remakes, buitenlandse titels, getallen,
en 7 video's die niet bij TMDB staan), met het goede id in nepdata van TMDB
(`koppelen_tmdb.json`; de titels zijn verzonnen, zodat niets op echte data van TMDB leunt).
Elke naam gaat zoals de scanner hem leest door `names.parse` en `lookup.identify` (de
bibliotheek in nl-NL), tegen de nep-TMDB in het proces met dezelfde http-client als de app.
Afleveringen koppelen op de serie, één beslissing per serie. Er is geen echte TMDB (geen
sleutel, geen internet), dus twee manieren van zoeken:

- `nep`: de nep zoals hij is: een titel komt terug als elk woord van de vraag erin staat;
- `ruim`: een bovengrens voor de echte TMDB, die ruimer zoekt: elke titel die ≥ 0,5 lijkt op de
  vraag (zo komt "The Hunters" bij "The Hunt").

**Uitkomst met de regel zoals hij is** (Keuze 42: automatisch bij ≥ 0,85 en ≥ 0,1 boven de
tweede):

| Zoeken | Namen | Automatisch goed | Automatisch fout | Nakijken (de goede in de top 5) | Precisie |
|---|---|---|---|---|---|
| `nep`, films | 93 | 77 | 3 | 13 (2) | 0,963 |
| `nep`, afleveringen | 32 | 24 | 0 | 8 (5) | 1,000 |
| **`nep`, samen** | 125 | 101 | 3 | **21** (7) | **0,971** |
| `ruim`, films | 93 | 78 | 5 | 10 (6) | 0,940 |
| `ruim`, afleveringen | 32 | 24 | 0 | 8 (7) | 1,000 |
| **`ruim`, samen** | 125 | 102 | 5 | **18** (13) | **0,953** |

Precisie is automatisch goed gedeeld door alles wat automatisch koppelde. Van de 7 video's die
niet bij TMDB staan, gaan er 5 (`nep`) of 4 (`ruim`) naar Nakijken.

**Waar het fout gaat.** Elke automatisch foute koppeling is een titel die het begin is van een
andere titel, of daar dichtbij ligt, terwijl de goede **niet tussen de kandidaten** staat:

| Naam | Gekozen | Waarom de goede ontbrak |
|---|---|---|
| `Cold.Meridian.2018…` | "Cold Meridian Rising" (2018), 0,995 | "Cold Meridian" is van 2017; met jaar 2018 komt alleen de andere terug, en dat is al een zekere keuze, dus zoekt `lookup.search` niet nog eens zonder jaar |
| `Wild.Orchard.2014…` | "Wild Orchard Road" (2014), 1,0 | idem, "Wild Orchard" is van 2013 |
| `Family.Weekend.2018…` | "Family Weekend Getaway" (2018), 0,986 | een eigen video, niet bij TMDB |
| `Harbor.of.Night.2014.SWEDISH…` (`ruim`) | "Harbor of Nightfall" (2014), 1,0 | de goede heet in nl-NL "Haven van de nacht" (0,63) |
| `The.Hunt.2020…` (`ruim`) | "The Hunters" (2020), 0,937 | niet bij TMDB |

Het geval van punt 9 zelf, beide titels bij TMDB in hetzelfde jaar ("Night Harbor" en "Night
Harbor Blues"; groep `voorvoegsel`, 12 namen), ging nergens fout: de goede krijgt 1,0 + 0,2, de
langere ±0,8 + 0,2, en dat is genoeg verschil. Fout gaat het alleen als de kortere titel ontbreekt:
dan is de langere de enige kandidaat, met 0,8 voor de titel en 0,2 voor het jaar boven 0,85.

**Geen fout tegen de tekst van Keuze 42**, dus niet gerepareerd: de score is die van de keuze
("gelijkenis ..., jaar gelijk +0,2", "automatisch bij een score ≥ 0,85 en ≥ 0,1 boven de
tweede"), en "zoeken op titel en jaar" is wat `lookup.identify` doet. Twee voorstellen, door
hetzelfde script doorgerekend zonder de app te veranderen (de keuze is aan Jonas):

| Regel | `nep`: goed / fout / Nakijken | Precisie | `ruim`: goed / fout / Nakijken | Precisie |
|---|---|---|---|---|
| zoals hij is | 101 / 3 / 21 | 0,971 | 102 / 5 / 18 | 0,953 |
| `breed`: altijd ook zonder jaar en in en-US zoeken (nu alleen zonder zekere keuze), alles samen rangschikken | 102 / 1 / 22 | 0,990 | 104 / 2 / 19 | 0,981 |
| `tekst`: automatisch alleen als de titel zelf ≥ 0,9 lijkt | 101 / 0 / 24 | 1,000 | 101 / 0 / 24 | 1,000 |
| `breed` + `tekst` | 102 / 0 / 23 | 1,000 | 103 / 0 / 22 | 1,000 |

`breed` lost het jaar ernaast en de buitenlandse titel op (de goede staat dan in de lijst en wint
met genoeg verschil), maar niet de video die niet bij TMDB staat. `tekst` stuurt alle vijf naar
Nakijken, voor 2 tot 3 namen meer op die lijst; een id uit de naam blijft zeker. Samen: geen
foute koppeling en de meeste automatisch goed. `breed` kost per naam altijd 4 zoekvragen, nu 1
bij een zekere keuze (de emmer voor TMDB geeft 10 per seconde, Keuze 39).

## Bevindingen

Een grens die niet gehaald werd, is een bevinding: de oorzaak gezocht, en gerepareerd met een
test als het een fout in onze code is. Getallen zijn nergens bijgesteld om te slagen.

1. **De reserve van Keuze 26 rekende het decoderen met `f` in plaats van `f_dec`**
   (gerepareerd, `3420059`). Mb zonder browser gaf de derde kijker 503 `media_busy` (cpu) met een
   plaats vrij: met de bench van die run (`f` 1,353, `f_dec` 1,554) hield de reserve
   0,56 × `f` = 0,758 vast, terwijl 480 veryfast van de referentiebron 0,794 kost. Het ontwerp
   zegt "alle kosten × `f` (en `d` × `f_dec`)", en de reserve is er juist voor dat "de laatste
   toegestane kijker altijd nog kan starten". `decide.reserve_cost(f, f_dec)` rekent nu
   (0,56 − 0,18) × `f` + 0,18 × `f_dec`; met `f` = `f_dec` is dat 0,56 × `f` zoals de
   rekenvoorbeelden van het ontwerp. Test: `test_de_laatste_kijker_past_ook_als_f_dec_boven_f_ligt`.
2. **De belastingtest buiten de app (fase c) liep vast** (gerepareerd, `bd93489`). Vóór de fix
   faalde fase c in elke run (3 van 3 voor hij afgebroken werd; `fase_c_voor_de_fix` in
   metingen.json): één speler kreeg 500 bij het openen, want drie probes tegelijk geven
   `WorkerBusy` (de worker heeft 2 plaatsen "kort") en `loadtest/play` vertaalde die niet; en na
   de herstart bleven de andere twee voor altijd staan, want een `ConnectError` stopte de
   fetcher zonder een nieuwe sessie te vragen. Nu: 503 `media_busy` {reason: worker} met
   `Retry-After` (zoals play), geen verbinding of 502 telt als een sessie die weg is (net als
   410), en de speler buiten de app wacht bij 503 `media_busy` op `Retry-After`. Vier tests in
   `test_media_loadtest.py`.
3. **Na een herstart van een producent haperde een speler één keer** (Md fase a run 1, na de
   sprong; Md fase c run 3, na de herstart van de app; niet gerepareerd). De sprong
   op 60 s gaat 90 s vooruit, voorbij de cache, en start een nieuwe ffmpeg met `-ss`. Het eerste
   segment kwam na 3,0 s; de afspeeltijd ligt midden in dat segment, dus de speler had minder
   dan één segment buffer, en het volgende kwam 0,6 s te laat. De startvoorrang van Keuze 26
   (de andere producenten lezen hooguit 10 s vooruit) geldt alleen tot segment 0 van een sessie,
   niet na een sprong; zo staat het in het ontwerp, en zo is het gebouwd. In Md zelf gebeurde
   het in 1 van de 5 runs, met de treden van de sandbox. *Voorstel* (voor het ontwerp, niet hier beslist): de
   startvoorrang ook voor de eerste twee segmenten na een herstart van een producent. Op de LXC
   meten of het daar ook gebeurt.
   In fase c is het hetzelfde: na de herstart van de app vragen de drie spelers tegelijk een
   nieuwe sessie, en de speler op 1080 (de zwaarste trede, drie ffmpeg die tegelijk beginnen)
   kreeg zijn eerste segment pas 6,7 s na "ready". De startvoorrang geldt hier wel (elke nieuwe
   sessie heeft hem tot haar eerste segment), maar het volgende segment kwam 1,5 s te laat. De
   virtuele speler is hier strenger dan de browser: hij begint na de herstart met alleen het
   segment op zijn plek, waar de speler in de browser zijn buffer houdt (`mse.ts`, `switchTo`
   zonder flush: tot 30 s) en dus doorspeelt. Niet versoepeld: §4.6 vraagt "haperen daarna
   niet", en de strengere lezing is de veilige.
   Met OnlyOffice ernaast gebeurde het vaker: in 3 van de 5 runs van fase a met een docx elke
   30 s en in 1 van de 2 met de herstart van DS, elke keer bij een speler waarvan de sprong een
   nieuwe producent vroeg, en bij hogere treden (1080 superfast + 720 superfast + 480 veryfast).
   Samen: 5 van de 12 runs van fase a met 3 sessies. In fase b, zonder sprong, nooit (15 runs).
   Zolang de startvoorrang niet ook na een sprong geldt, haalt fase a de grens in de sandbox dus
   niet betrouwbaar.
   *Fase 6 (integratie I1, 0.8.0):* de voorrang geldt nu ook na elke herstart van een stroom
   (B71, voorstel): de andere producenten lezen dan hooguit 10 s vooruit, tot de nieuwe run 10 s
   media maakte, hooguit 20 s. Dat is half de oplossing. De metingen van fase 6
   ([metingen.md](../fase-6/metingen.md), M7) vonden twee oorzaken. Na de herstart maakt de
   producent onder drukte te weinig: daar is de voorrang voor. En landt de sprong in de laatste
   21 ms van een segment (het rooster begint op 21 ms), dan moet het volgende segment er binnen
   ±0,2 s zijn; dat haalt geen herstart, en daar helpt voorrang niet. Wat de poort dan telt,
   kiest Jonas. Fase c is niet opnieuw gemeten.
4. **Zoeken bij direct play haalt de 1,5 s van de tabel niet** (niet van ons). Bij direct play
   (`src=`) beslist Chromium wanneer hij na een sprong weer speelt: hij haalt eerst een stuk van
   het bestand binnen (m6.md: ±4 s beeld) en decodeert vanaf het keyframe vóór het doel. De
   server geeft alleen byte-ranges, zonder worker. Op 40 Mbit/s kost het 2,0 s (CI 1,8 s), op
   400 Mbit/s 1,49 s mediaan en 1,63 s slechtste. De 3 s van "Klaar als" haalt hij ruim; de
   1,5 s van de tabel alleen met een snel netwerk, en dan nog niet elke keer. In de app is er
   niets aan te doen zonder direct play anders te bouwen (via MSE). Op het LAN van de LXC is het
   als de meting op 400 Mbit/s.
5. **Keuze 42: een titel die het begin is van een andere titel** (niet gerepareerd; de keuze is
   aan Jonas). Zie [Keuze 42 op echte namen](#keuze-42-op-echte-namen-punt-9).
6. **De voorspelling van de kosten ligt 44–60 % boven wat het kost** (Mk; niet van ons, de keuze
   is aan Jonas). `decide.cost` is de tabel van Keuze 24 × `f`, en die tabel is met opzet een
   bovengrens: gemeten met een bron met zware ruis ("met echte inhoud kost x264 minder dan de
   tabel ... De tabel is dus een bovengrens", Keuze 24; J2: 1080 alleen 3,5–3,8× met echte
   inhoud tegen 2,54× met de testbron). De bron van Mk is dezelfde ruis (`testsrc2`,
   `noise=alls=20`, zoals `bench`), maar als bestand van 6 Mbit/s: de encoder van de bron haalt
   het meeste van de ruis al weg, en dan kost hij net als echte inhoud minder. De snelheden van
   Mk zeggen hetzelfde: 1080 veryfast liep op 3 cores op 3,5×, waar de tabel × `f` (1,78) op
   ±1,7× uitkomt. `f` zelf klopt dus niet minder: hij schaalt een bovengrens. Mk vraagt "binnen
   15 %", Keuze 24 een bovengrens; dat kan voor echte inhoud niet allebei. Niets bijgesteld.
   *Voorstel* (voor het ontwerp, niet hier beslist): Mk als "gemeten ≤ voorspeld, en niet meer
   dan 2× te hoog" lezen, of de tabel op de LXC ijken met een paar echte films van Jonas (de
   snelheid per trede staat op /beheer; de belastingtest kan dat sinds de review van de
   integratie ook met eigen films: `media-loadtest --film <id>`, het runbook van media).
   Zolang rekent de toelating veilig en kiest ze lagere treden dan de CPU aankan (Md, Me).
7. **Tijdens de start van DS krijgt een nieuwe kijker 503 in plaats van een lagere trede** (Md
   met een herstart van DS; niet van ons, de keuze is aan Jonas). Zolang DS `starting` is (tot
   zijn font-herstart, 2 à 3 minuten na een start), is het budget `B − 1,0` = 2,0 (Keuze 26). De
   regel houdt dan voor elke andere vrije plaats de kosten van 480 veryfast vast; met 3 plaatsen
   past de eerste kijker alleen als 3 × kosten(480 veryfast) ≤ 0,9 × 2,0, dus bij `f` ≤ ±1,07
   (met `f_dec` = `f`; bij `f` = 1 is het 1,68 en krijgt hij 480 veryfast, zoals §4.6 d
   verwacht). De sandbox had `f` 1,24 en 1,37: geen trede past, en alle drie kregen 503
   `media_busy` (cpu, `Retry-After` 30). De code doet wat de keuze zegt; met deze `f` laat de
   regel geen plaats over. In de browser wacht de speler `Retry-After` af en probeert het
   opnieuw (tot 30 keer, `WAIT_MAX` in `web/src/player/session.ts`), met de melding dat de
   server te druk is: de kijker begint zodra DS klaar is, zonder fout. De virtuele speler van de
   belastingtest probeert het niet opnieuw, dus fase b faalt. *Voorstel* (voor het ontwerp, niet
   hier beslist): `max_transcodes` tellen als hooguit het aantal keer 480 veryfast dat in
   0,9 × `B` past (bij `f` 1,37 en `B` 2,0: ⌊1,8 / 0,77⌋ = 2). Dan krijgen twee nieuwe kijkers een
   lagere trede en wacht de derde. Het helpt ook buiten de start van DS: met `B` 3,0 en 3
   plaatsen past nu bij `f` boven ±1,6 niets meer. Op de LXC (`f` ±1,0) zou het ook zonder dit
   voorstel moeten gaan; daar meten met een herstart van DS (het runbook van media).

## Wat alleen de LXC of CI kan laten zien

- **De treden van Vraag 3** (1080 veryfast + 720 superfast + 720 superfast): de toelating kiest
  ze alleen bij `f` ≤ ±1,0. De sandbox heeft `f` 1,22–1,40 en kiest lager (Md). Op de LXC:
  `bench`, dan de belastingtest (het runbook van media), en de uitkomst hier erbij.
- **Me met 1080 veryfast + 1080 superfast** en **Mf naast 2 andere**: idem, de treden hangen
  aan `f`.
- **Fase c met `dc restart platform`**: hier herstart het harnas de app op pid (3,1–3,5 s weg);
  op de LXC duurt een herstart van de container langer, en de proxy ervoor geeft 502.
- **Fase d met de DS van het image** en zijn grenzen (`compose.office-limits.yaml`), naast media
  op dezelfde 4 cores: hier draait DS 10.0.0.1 met dezelfde grenzen, maar op de cores van de
  sandbox, met de andere sessie ernaast. Of een nieuwe kijker tijdens de start van DS een
  lagere trede krijgt, hangt aan `f` (bevinding 7): op de LXC met `f` ±1,0 wel.
- **De belastingtest met echte films:** bij deze metingen nam `POST …/loadtest` `sources` aan
  (§4.6 punt 1: "Met `sources` kan het met echte films van de beheerder"), maar gebruikte het
  niet. In de review van de integratie (K1) is het gebouwd: met `sources` (of `media-loadtest
  --film <id>`) speelt elke speler een film van de beheerder. Het ijken van de tabel met echte
  films (bevinding 6) kan dus op de LXC; de films van Jonas zijn er alleen daar.
- **Mb in de browser met een bron van 1080p:** de e2e gebruikt `hevc10_hdr10.mkv` van 720p; een
  1080p-bron mat hier alleen de speler zonder browser (1,34 / 1,66 s). Een HEVC-film van 1080p op
  `/kijken` hoort bij de lijst voor de LXC (review van de integratie, K4).
- **H.264 in de browser** (Ma met H.264 en AAC, Mb naar H.264): CI, met Chrome for Testing (de
  getallen hierboven), en de LXC.
- **ffmpeg 7.1.5 van het image**: de sandbox heeft 6.1.1. De e2e van CI (`e2e-media-perf`)
  draait de worker van het image niet; dat doet `media-image` (in de installatietest de rooktest
  van de belastingtest, `--sessies 1 --duur 30`).
- **Safari, iPhone en webOS**: fase 9.
- **Keuze 42 met de echte TMDB**: het corpus zoekt tegen de nep (`nep`) en tegen een ruimere
  nep (`ruim`); hoe ruim de echte TMDB zoekt, ligt daartussen of erbuiten. Met een sleutel op de
  LXC: de Nakijken-lijst na de eerste scan van de echte bibliotheek.

## Opnieuw meten

Alles vanuit `api/`, na elkaar (nooit twee tegelijk: elke meting start een eigen app en worker, en
regel 13 telt de ffmpeg van de hele machine). Elk script schrijft zijn JSON met `--uit`; met
`--werkmap` blijven de bronnen staan voor een volgende keer (de belastingtest maakt er ±675 MB).
Docker en `e2e/office_ds.sh` zijn alleen nodig voor `--office`.

```sh
# Ma, Mb en Mc in de browser (10 keer elk; het rapport in $E2E_PERF_OUT)
E2E_PERF_REPORT_ONLY=1 E2E_PERF_OUT=/tmp/perf \
  uv run --with playwright pytest ../e2e/test_media_m6_perf.py -m media_perf -rs -s
# Mc op 400 Mbit/s (de plugin meet_netwerk verandert alleen het netwerk van de e2e)
MEET_DOWN_MBIT=400 E2E_PERF_REPORT_ONLY=1 E2E_PERF_OUT=/tmp/perf-400 \
  PYTHONPATH=tests/media/metingen uv run --with playwright pytest \
  ../e2e/test_media_m6_perf.py -m media_perf -k mc_zoeken -p meet_netwerk -rs -s
# Md, Mg, Mh: de belastingtest, 5 keer
uv run python -m tests.media.metingen.belasting --herhaal 5 --uit /tmp/md.json
# Md met OnlyOffice: een docx elke 30 s, en een herstart van DS 200 s na het begin van fase a
uv run python -m tests.media.metingen.belasting --office --docx-elke 30 --herhaal 5 \
  --uit /tmp/md-docx.json
uv run python -m tests.media.metingen.belasting --office --ds-herstart 200 --herhaal 2 \
  --uit /tmp/md-ds.json
# Md fase c: de spelers buiten de app, de app herstart na 60 s
uv run python -m tests.media.metingen.belasting --fase-c --herhaal 5 --uit /tmp/fasec.json
# Me: 2 × 1080
uv run python -m tests.media.metingen.belasting --sessies 2 --max-transcodes 2 --herhaal 5 \
  --uit /tmp/me.json
# Mb zonder browser, Mk, Mm, Mj
uv run python -m tests.media.metingen.start --herhaal 5 --uit /tmp/start.json
# Mf (en Mh met HDR)
uv run python -m tests.media.metingen.hdr --herhaal 5 --uit /tmp/mf.json
# Mi en Mn (5 keer elk)
uv run --with playwright pytest ../e2e/test_media_m7.py -m media -k twee_tabbladen -rs -s
uv run --with playwright pytest ../e2e/test_media_m9.py -m media -k gapless_de_fase -rs -s
# Ml, Mo, Keuze 42
uv run python -m tests.media.metingen.scannen --herhaal 5 --uit /tmp/ml.json
uv run python -m tests.media.metingen.namen --herhaal 5 --uit /tmp/mo.json
uv run python -m tests.media.metingen.koppelen --uit /tmp/koppelen.json
# Mp (vanuit web/)
npm run build && npm run budget
```

De e2e voor Ma–Mc zet de worker zelf onder `taskset -c 0-2` (de fixture `media` met
`media_perf`); de scripts doen dat via `kit.App`. Op de LXC gaat het met wat in het image zit:
`bench` en de belastingtest op /beheer, of `docker compose exec platform python -m app.cli
media-loadtest --sessies 3 --duur 300 --json uit.json`, en voor fase c `--extern` terwijl Jonas
`dc restart platform` doet ([het runbook](../runbooks/media.md)).
