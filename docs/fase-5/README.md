# Fase 5 — Media en streaming

Status: **golf 3 samengevoegd, de review en de nacontrole van golf 3 verwerkt: M0 tot en met
M6** (bouwontwerp revisie 2; 30 oktober 2026).
M0 is het fundament (tag `fase5-basis`): migratie 0008, de contracten, de kern van de worker,
F1–F4, de rijen en de haken ([m0.md](m0.md)). Daarop staan nu:

- **M1, de worker in isolatie** ([m1.md](m1.md)): ffmpeg draait alleen nog in de worker, ook voor
  de previews van video's. De app start de lokale worker, start hem opnieuw met backoff, en kijkt
  elke 30 s of hij gezond is. Er is een container met de afscherming van Keuze 11
  (`compose.media.yaml`); die bewijst CI in `media-image`.
- **M2, bibliotheken** ([m2.md](m2.md)): een map wordt een bibliotheek. De scanner maakt de
  items zonder internet: een eigen parser voor bestandsnamen, groeperen in series en delen, en
  NFO's (gelezen in de worker). Een bestaande verzameling komt binnen met de import uit
  `/data/import` (standaard kopiëren). In de web-app `/media`, een bibliotheek, een item en een
  serie, alles met het toetsenbord ([toetsenbord-m2.md](toetsenbord-m2.md)).
- **M3, afspelen** ([m3.md](m3.md), [clientprofiel.md](clientprofiel.md)): het profiel van de
  client, de beslissing, de sessies met een controle bij elk verzoek, en publieke links. Direct
  play werkt helemaal, ook met de worker weg of zonder ffmpeg.
- **M4, de stromen** ([m4.md](m4.md)): remux en transcode als fMP4 op een rooster van 2 s, met
  `map.json` voor MSE en HLS-playlists voor native spelers. De producent herstart bij een sprong
  en na een fout, houdt 60 s voorsprong, en de toelating laat alleen toe wat de CPU kan (de
  treden van de ladder). De keyframe-index komt bij de analyse, of bij play voor een video
  buiten een bibliotheek.
- **M5, ondertitels, sprites en hoofdstukken** ([m5.md](m5.md)): elke tekstondertitel (SRT, ASS,
  WebVTT, ingebed of los) als WebVTT via de sessie, op mediatijd; een beeldondertitel (PGS,
  VobSub) brandt de producer van M4 in. De sprites voor de zoekbalk met `trick.vtt`, en de
  hoofdstukken met het begin van de aftiteling.
- **M6, de speler** ([m6.md](m6.md)): `/kijken` en de speler in de viewer van Bestanden. Direct,
  MSE (de segmenten van M4) en native HLS, zonder bibliotheek (geen hls.js), met kwaliteit,
  audiospoor, ondertitels, sprites, hoofdstukken, de kaart "Volgende aflevering", herstel na een
  verlopen sessie of een herstart van de app, en het toetsenbord
  ([toetsenbord-m6.md](toetsenbord-m6.md)). Lazy geladen, binnen het budget.

De rest (M7–M10: voortgang, metadata online, muziek, beheer en uitrol) komt in de volgende
golven. Media staat standaard uit: zonder `PLATFORM_MEDIA_ENABLED` is de app 0.6.0 plus F1–F4,
en de previews van video's werken zoals in 0.6.0 (via een worker voor één keer, M1).

Bij het samenvoegen van golf 1 (M1, dan M3) gingen er geen bestanden van beide pakketten door
elkaar; alleen `ci.yml` botste met het weghalen van de job `media-spike`. Wat er toen nog
gebeurde, staat onder "Bij het samenvoegen" in [m1.md](m1.md) en [m3.md](m3.md):

- play noemt de worker als die weg is of geen ffmpeg heeft, ook bij een transcode zonder plek
  (eerst gaf dat "druk"); getest met de echte lokale worker van M1
  (`test_media_zonder_worker.py`);
- de opnames van de probe voor de nep-worker zijn opnieuw opgenomen met de codec-strings van M3
  (een M0-hotfix);
- stap 4 van het verhaal van fase 3 leest geen handtekening meer als GPS.

De uitslag op de samengevoegde stand van golf 1, in de sandbox: api 2628 goed en 1 overgeslagen;
`-m media` (onder `taskset -c 0-2`, twee keer) 93 goed en 3 overgeslagen (`stream`, M4); web
groen (eerste lading JS 95,6 van 100 KB); de e2e van fase 3 en 4 109 goed; `-m office` 30 goed;
e2e `-m media` (twee keer) 14 goed en 2 overgeslagen (H.264: de Chromium 141 hier heeft geen
`avc1`; in CI moet hij spelen). Het image, de worker-container en Chrome for Testing ziet pas CI.

Bij het samenvoegen van golf 2 (M4, dan M5) ging M4 vooruit zonder samenvoegcommit (de tak stond
nog op zijn basis), en kwam M5 met cherry-pick. Alleen `test_media_routes_guarded.py` (M0) botste:
beide pakketten zetten hun routes in `BUILT`, en beide bleven. Wat er toen nog gebeurde, staat
onder "Bij het samenvoegen (golf 2)" in [m4.md](m4.md), [m5.md](m5.md) en [m3.md](m3.md):

- play (M3) geeft de echte staat van M5: het geforceerde tekstspoor volgt het gekozen audiospoor
  (Keuze 29), een losse ondertitel krijgt zijn staat uit de cache, de hoofdstukken komen uit
  `chapters.items`, en `trick` gebruikt de instellingen van het verzoek
  (`test_media_play_samen.py`);
- PGS en VobSub ingebrand door de producer van M4, gemeten in de pixels van de stroom, ook na
  een sprong (S9; #5 van §4.3), en de SRT en ASS van een transcode op de tijdlijn van die stroom
  (het ondertiteldeel van #4);
- #12 van §4.3 zonder de speler: de worker gedood tijdens een transcode; de stroom loopt binnen
  10 s door (hier binnen 2 s), en er blijft geen ffmpeg over;
- een e2e-test van M4 faalde elke keer met 503 `media_busy` (reden `worker`): net na twee uploads
  houden hun previews de twee plaatsen "kort" van de worker bezet, en krijgt de probe van play
  busy. Zo hoort de app te antwoorden (Retry-After 2); de helpers van de e2e proberen het nu
  opnieuw zoals de speler dat moet doen. Zie "Open" in [m4.md](m4.md).

De uitslag op de samengevoegde stand van golf 2, in de sandbox: api 3066 goed en 1 overgeslagen;
`-m media` (onder `taskset -c 0-2`, twee keer) 164 goed, niets overgeslagen; web groen (648
tests, eerste lading JS 95,6 van 100 KB, i18n strikt zonder meldingen); de e2e van fase 3 en 4
110 goed; `-m office` 30 goed; e2e `-m media` (twee keer) 27 goed en 3 overgeslagen (H.264: de
Chromium 141 hier heeft geen `avc1`; in CI moet hij spelen). Het image, de worker-container
(S3 punt 17 en R12) en Chrome for Testing ziet pas CI.

Na de review van golf 2 kwamen de fixer en de na-controle. De na-controle draaide de test van
elke fix tegen de code van vóór de fix, en vond drie dingen die ze met een test rechtzette:

- de fix van S3 gaf remux ook een grens van 1 per publieke link. Een link heeft al 2 sessies (de
  derde sluit de oudste), en met die grens kreeg de tweede kijker van een mkv 503. Die grens is
  weer weg; de 3 remuxen per gebruiker blijven ([m4.md](m4.md), afwijking 13);
- een wissel (`replace`, een ander audiospoor of een lagere trede) telde de plaats van de oude
  sessie nog mee in de toelating: de wissel van de ene transcode via een link gaf 503, en een
  eigen transcode kreeg een lagere trede. Nu telt die plaats niet mee ([m4.md](m4.md), afwijking
  16; de twee bestanden van M3 staan in [m3.md](m3.md));
- de tik van `subconv.steps` zonder cues had geen test ([m5.md](m5.md)).

"Klaar als" van M4 en M5, per punt hier bewezen of in CI, staat onder "De na-controle (golf 2)"
in [m4.md](m4.md) en [m5.md](m5.md). De uitslag op de stand na de na-controle, in de sandbox: api
3109 goed en 1 overgeslagen (twee volle rondes); `-m media` (onder `taskset -c 0-2`, twee keer)
164 goed; web groen (648 tests, eerste lading JS 95,6 van 100 KB, i18n strikt zonder meldingen);
de e2e van fase 3 en 4 110 goed; `-m office` 30 goed; e2e `-m media` 27 goed en 3 overgeslagen
(H.264, zoals hierboven); `media_perf` 2 goed, alle starttijden ruim onder hun grens (de sprong
1,56 van 2 s). In een eerste volle ronde faalde één keer
`test_search.py::test_snel_genoeg_met_twintigduizend_bestanden` (fase 2): de zoekvraag op een tag
duurde 1,12 s (grens 1 s), terwijl de na-controle in dezelfde checkout ook andere tests draaide.
Die vraag kost 0,07 s, ook midden in de volle ronde (gemeten: geen volle ronde van de garbage
collector en geen andere threads op dat moment), en de test slaagde daarna in twee volle rondes
en tien losse, ook met een veel grotere heap en naast een bezette core. Waar die ene seconde
vandaan kwam, is niet gevonden; de test (en de code) van fase 2 bleef ongemoeid.
*Bij het samenvoegen van golf 3:* gevonden, zie hieronder.

Bij het samenvoegen van golf 3 (M2, dan M6) kwamen beide pakketten met cherry-pick, met hun
eigen commits en boodschappen, zonder botsingen: ze raakten geen bestand van elkaar. Wat er
daarna nog gebeurde, staat onder "Bij het samenvoegen (golf 3)" in [m2.md](m2.md) en
[m6.md](m6.md):

- **De scanner analyseert inhoud die nieuw is in een bibliotheek, ook met een probe.** Sinds de
  speler van M6 in de viewer van Bestanden afspeelt, heeft een video vaak al een probe (van
  play) voor hij in een bibliotheek komt. De scanner zette de analyse alleen klaar zonder probe,
  dus kwamen subs en sprites er voor zo'n video nooit. De e2e van M5 vond het: hij wachtte 60 s
  op de sprites van `vp9_opus.webm`, die de e2e van M0 al had afgespeeld.
- **De speler volgt de volgorde van een serie zoals M2 die geeft:** absolute nummering eerst,
  dan de seizoenen, de specials achteraan. De kaart "Volgende aflevering" gaat nooit van een
  gewone aflevering naar een special of terug ([m6.md](m6.md), afwijking 17).
- **De e2e op de echte code:** de stand-in van M2 in de e2e van M6 is weg (een echte bibliotheek
  en een echte scan), en de e2e van M2 speelt na Afspelen en "Verder op 0:03" echt af in de
  speler.
- **Vier tests die soms faalden, elk met de oorzaak** (geen van vieren lag aan de app):
  - `test_place_file_exact.py` (fase 4): de nep-Document Server zette de klok in zijn zip, en
    twee aanroepen aan weerszijden van een grens van 2 s gaven een andere sha; nu een vaste
    datum.
  - De snelheidstest van zoeken (fase 2, zie golf 2): in een volle run heeft het proces 1,5
    miljoen objecten van eerdere tests, en een volle ronde van de garbage collector kost dan
    1,07 s (gemeten met een spion op `gc.callbacks`). Die viel soms binnen een gemeten vraag. De
    meting gaat nu zoals `timeit`: eerst een volle ronde, tijdens de vragen geen. Het zoeken zelf
    was nooit traag.
  - De e2e van M6 die van de pagina weggaat: de route van de test hield in Playwright soms de
    DELETE met `keepalive` bij `pagehide` vast tot de context dichtging (13 van 130 keer, los
    nagebootst). De test haalt zijn route nu eerst weg ([e2e.md](e2e.md), De browser en de
    hulpjes).
  - De e2e van M6 met de sprong voorbij de cache: de nieuwe producent (de laatste 15 s, remux)
    was in ±90 ms klaar, en de spion op de processen kijkt elke 0,1 s. De sprong gaat nu naar
    het midden van 300 s; de producent wacht dan op de tegendruk en leeft tot de test weggaat.

De maat van een item (voor `decodingInfo` op `/kijken`) staat niet in het `Item` van M0; die
blijft open ([m6.md](m6.md), afwijking 5). *Bij de review van golf 3:* gedaan, uit de meta van de
preview, zonder het `Item` te veranderen.

De uitslag op de samengevoegde stand van golf 3, in de sandbox: api 3268 goed en 1 overgeslagen
(twee volle rondes); `-m media` (onder `taskset -c 0-2`, twee keer) 165 goed, niets overgeslagen;
web groen (799 tests, eerste lading JS 96,0 van 100 KB, de route Bestanden 43,6 van 45 KB,
`WatchPage` 22,7 van 45 KB, de speler 21,7 van 35 KB, i18n strikt zonder meldingen); de e2e van
fase 3 en 4 110 goed en `-m office` 30 goed (elk twee keer: voor en na de fixes van de e2e van
M6); e2e `-m media` 48 goed en 4 overgeslagen (H.264: de
Chromium 141 hier heeft geen `avc1`; in CI moet hij spelen): twee keer na elkaar groen na de twee
fixes van de e2e van M6 hierboven (de twee runs ervoor hadden elk één van die twee rood).
`-m media_perf` voor M6 (10 runs, de worker op cores 0–2): Ma direct mediaan 339 ms (grens 2000),
Mb transcode 1197 ms (grens 5000), Mc zoeken direct 1860, remux 856 en transcode 1938 ms (grens
3000; direct ligt boven de 1500 uit de tabel van §4.5, die alleen ter informatie is). Het image,
de worker-container en Chrome for Testing ziet pas CI.

De review van golf 3 had 19 bevindingen voor M2 en M6 (S1–S5, C1–C8, K1–K6); ze klopten alle
19. Elke bevinding staat met haar uitkomst, de commit en de tests onder "De review (golf 3)" in
[m2.md](m2.md) en [m6.md](m6.md). De belangrijkste:

- **Veiligheid:** een NFO met `encoding="punycode"` legde de worker minutenlang stil (nu alleen
  coderingen van een vaste lijst); een verplaatste import hield 0666 en de eigenaar van de bron
  (nu 0600, en een bestand van een ander via een kopie); items en posters bleven na de prullenbak
  of een verhuizing zichtbaar tot de volgende scan (nu per vraag nagekeken); `._Film.mkv` van een
  Mac werd een tweede film.
- **Wat verloren ging:** de rest van een volledige ronde in stukken, de bron van een import die
  stopte tussen rename en `place_file`, en de stappen van de bibliotheek als de analyse al zonder
  bibliotheek liep.
- **De speler:** na een vol quotum bleef er een gat in de buffer; `retry()` en de lus van de
  lader liepen tegelijk aan de SourceBuffer (de speler stopte na een herstart van de worker); en
  de speler keek naar `/api/v1/health`, een route die niet bestaat (nu `/health`). Die laatste
  twee vond de nieuwe e2e van §4.3 #12 (de worker gedood tijdens een transcode). `/kijken` meet
  nu op de maat van het item.

Geen M0-hotfix. De uitslag na de review, in de sandbox: api 3309 goed en 1 overgeslagen; `-m
media` (onder `taskset -c 0-2`, drie keer) 165 goed; web groen (806 tests, eerste lading JS 96,0
van 100 KB, `WatchPage` 23,1 van 45 KB, de speler 22,0 van 35 KB, i18n strikt zonder meldingen);
de e2e van fase 3 en 4 110 goed, `-m office` 30 goed, `-m media` 49 goed en 4 overgeslagen (alle
vier H.264, dat Chromium 141 niet kan). De e2e van de gedode worker drie keer los groen. Ma met
H.264 (nieuw, K1) meet alleen CI.

Na de review kwam de nacontrole. Die draaide elke nieuwe test op de code van vóór het herstel
(elke test faalde daar) en vond twee dingen, elk met een test rechtgezet:

- **Een timeout van `nfo` bleef voor altijd in de cache** (S1). Na de vaste lijst van coderingen
  ligt een timeout eerder aan de schijf of aan de lus; een goede NFO bleef zo weg, ook na Opnieuw
  scannen. Nu geldt hij alleen voor die scan ([m2.md](m2.md), "De nacontrole (golf 3)").
- **Stap 1 van het verhaal van fase 3 las de herstelcodes te vroeg.** De kop "Herstelcodes" staat
  er eerst, met "Laden…" eronder (de lijst is een lazy component in een Suspense); de test las de
  lijst zonder te wachten en kreeg één keer `[]`, en daarna faalden de andere 12 stappen. Nu wacht
  `e2e/test_integratie.py` op de tien codes. De app verandert niet.

"Klaar als" van M2 en M6, per punt hier bewezen of in CI, staat onder "De nacontrole (golf 3)"
in [m2.md](m2.md) en [m6.md](m6.md). Geen M0-hotfix. De uitslag op de stand na de nacontrole, in
de sandbox: api 3309 goed en 1 overgeslagen; `-m media` (onder `taskset -c 0-2`) 165 goed; web
groen (806 tests, eerste lading JS 96,0 van 100 KB, `/kijken` 23,1 van 45 KB, de speler 22,0 van
35 KB, i18n strikt zonder meldingen); de e2e van fase 3 en 4 110 goed, `-m office` 30 goed,
`-m media` 49 goed en 4 overgeslagen (alle vier H.264). `media_perf` van M6 (VP9, 10 keer, de
worker op cores 0–2): Ma 390 ms, Mb 1561 ms, Mc 1922 / 859 / 2193 ms, alle binnen hun grens, ook
de slechtste.

## Doel

Films, series en muziek afspelen op elk apparaat, met zo weinig mogelijk omzetten. Uit het
stappenplan:

- bibliotheken met een scanner en het lezen van bestandsnamen;
- metadata (TMDB, MusicBrainz), posters, en zelf koppelen en corrigeren;
- per apparaat kiezen: direct afspelen, remux of transcode;
- een worker voor ffmpeg, met HDR naar SDR;
- sessies met een eigen cache, en opruimen na afloop;
- ondertitels, sprites voor de zoekbalk, en hoofdstukken;
- kijkvoortgang, "Verder kijken" en de volgende aflevering;
- muziek, met albums, artiesten, afspeellijsten en gapless.

Klaar als: een 1080p-film start binnen 2 s (direct play) en 5 s (transcode); zoeken werkt;
3 gelijktijdige transcodes zonder haperen; voortgang synchroniseert tussen apparaten.

## Hoe het werkt, in het kort

- Een film is een gewoon bestand in de boom. Een bibliotheek is een map met een soort erop.
  Een bestaande verzameling komt binnen via een import uit `/data/import`.
- ffmpeg draait alleen in een eigen worker-container: geen netwerk, geen data, een eigen
  gebruiker (uid 10002). De app geeft hem één open bestand en een pipe terug.
- De browser meet zelf wat hij kan. De app kiest dan direct, remux of transcode. Direct en remux
  kosten bijna geen CPU; dat is de normale weg.
- Transcode maakt segmenten van precies 2 s. Een eigen speler in het web (geen hls.js), en native
  HLS voor Safari en iOS.
- Alleen de CPU. Het platform meet zelf hoe snel die is (`bench`), en laat alleen toe wat past.
  Een GPU is klaargezet, niet gebouwd (B5 is open).
- Bijwerken en terugrollen met hetzelfde ene `install.sh`. Lukt iets met media niet, dan werkt
  alles van fase 4 gewoon door.

## Wat er komt

- **M0 — Fundament en spike:** migratie 0008, de contracten, de kern van de worker, de reparaties
  aan downloads (F1–F4), de rijen voor achtergrondwerk, een nep-worker en nep-providers voor de
  tests, en de spike in CI vanaf de eerste dag.
- **M1 — Worker en isolatie:** de worker-container, de afscherming van ffmpeg, en de previews
  van video's via de worker.
- **M2 — Bibliotheken:** de scanner, de eigen parser voor bestandsnamen, groeperen, NFO, en de
  import.
- **M3 — Afspelen:** het profiel van de client, de beslissing, de sessies en de toegang per
  verzoek.
- **M4 — Stromen:** de producent, de knipper, de playlists, de toelating en de cache.
- **M5 — Ondertitels, sprites en hoofdstukken.**
- **M6 — De speler:** `/kijken`, met toetsenbord, ondertitels en zoeken.
- **M7 — Voortgang:** "Verder kijken" en synchroniseren via SSE.
- **M8 — Metadata online:** TMDB en MusicBrainz, met een eigen sleutel, en zelf koppelen.
- **M9 — Muziek en gapless.**
- **M10 — Beheer en uitrol:** het tabblad Media op `/beheer`, de belastingtest, en `install.sh`.

## Planning

±22 werkdagen: M0 ±6, de parallelle golf ±9, de integratie ±3, en ±4 dagen marge voor wat de
spike en de LXC laten zien. Het stappenplan geeft 5 weken (25 dagen). Dat past, krap. Het
ontwerp zegt wat als eerste schuift als het uitloopt (§5).

Wat de sandbox laat zien, staat in [haalbaarheid.md](haalbaarheid.md): de afscherming van de
worker, de kosten van de bron, de tijden van fMP4, en de eerste start op 3 cores.

## Vragen voor Jonas

1. Staan er al films, series of muziek op `/tank`, en waar? Standaard: we bouwen de import, en
   hij kopieert. Verplaatsen kan als de bron onder `/tank/platform/import` staat; daarvoor moet
   jij op de Proxmox-host `chown -R 110001:110001 /tank/platform/import` doen.
2. Welke CPU, en is er een GPU (B5)? Standaard: alleen de CPU. Graag de uitvoer van `lscpu` en
   `ls -l /dev/dri` op de Proxmox-host.
3. "3 gelijktijdige transcodes": drie op gemengde kwaliteit, of hooguit twee op 1080p?
   Standaard: drie (de eerste 1080p, de andere twee 720p). Een 4K-bron telt dubbel.
4. Heb je een TMDB-sleutel? Standaard: geen, alleen lokale metadata.
5. Mag een publieke link transcoderen? Standaard: nee, alleen direct en remux.
6. Profielen onder één account (bijvoorbeeld voor kinderen)? Standaard: nee, voortgang per
   account. De kolom `profile` staat klaar.
7. Een eigen uid voor de kinderen van de worker (Keuze 11)? Voorstel van M1: nee. Het ergste
   wat een gekraakte ffmpeg dan kan, is media stilleggen; lezen kan hij niets ([m1.md](m1.md),
   keuze 6).

## Documenten

- [bouwontwerp.md](bouwontwerp.md): het ontwerp (revisie 2, leidend); revisie 1 in
  [bouwontwerp-r1.md](bouwontwerp-r1.md). De keuzes staan als voorstel in B39–B48 in
  [beslissingen](../fase-0/beslissingen.md).
- [haalbaarheid.md](haalbaarheid.md): de metingen in de sandbox bij revisie 2 (V1–V8), met de
  commando's.
- [spike.md](spike.md): de spike van M0 (S1–S13), met een uitkomst per stap en de commando's van
  de worker na de spike.
- [m0.md](m0.md): wat M0 bouwde, waar het van het ontwerp afwijkt, en wat nog open is;
  [m0-web.md](m0-web.md) voor het webdeel, [toetsenbord-m0.md](toetsenbord-m0.md) voor het
  toetsenbord.
- [m1.md](m1.md): de worker in isolatie (M1), met de referentie van `bench` en wat alleen CI
  bewijst.
- [m2.md](m2.md): bibliotheken, de scanner, namen en de import (M2);
  [toetsenbord-m2.md](toetsenbord-m2.md) voor het toetsenbord.
- [m3.md](m3.md): afspelen (M3); [clientprofiel.md](clientprofiel.md) beschrijft het profiel dat
  de client meet en stuurt (versie 1).
- [m4.md](m4.md): de stromen (M4), met de metingen van "Klaar als" (de start van een stroom
  zonder browser).
- [m5.md](m5.md): ondertitels, sprites en hoofdstukken (M5).
- [m6.md](m6.md): de speler (M6), met de metingen van "Klaar als" in de browser;
  [toetsenbord-m6.md](toetsenbord-m6.md) voor het toetsenbord.
- [e2e.md](e2e.md): de tests van media (de markers `media`, `media_perf` en `provider_real`),
  het harnas, de nep-servers, de jobs in CI, en hoe je ze lokaal draait.
