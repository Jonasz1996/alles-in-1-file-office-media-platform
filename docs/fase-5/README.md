# Fase 5 — Media en streaming

Status: **golf 2 samengevoegd: M0, M1, M3, M4 en M5** (bouwontwerp revisie 2; 29 oktober 2026).
M0 is het fundament (tag `fase5-basis`): migratie 0008, de contracten, de kern van de worker,
F1–F4, de rijen en de haken ([m0.md](m0.md)). Daarop staan nu:

- **M1, de worker in isolatie** ([m1.md](m1.md)): ffmpeg draait alleen nog in de worker, ook voor
  de previews van video's. De app start de lokale worker, start hem opnieuw met backoff, en kijkt
  elke 30 s of hij gezond is. Er is een container met de afscherming van Keuze 11
  (`compose.media.yaml`); die bewijst CI in `media-image`.
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

Er is nog geen speler: dat is M6. De API speelt nu alle drie de wegen (direct, remux,
transcode); de e2e-tests spelen remux en transcode met MediaSource in de pagina, zoals de speler
het straks doet. De rest (M2, M6–M10) komt in de volgende golven. Media staat standaard uit:
zonder `PLATFORM_MEDIA_ENABLED` is de app 0.6.0 plus F1–F4, en de previews van video's werken
zoals in 0.6.0 (via een worker voor één keer, M1).

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
- [m3.md](m3.md): afspelen (M3); [clientprofiel.md](clientprofiel.md) beschrijft het profiel dat
  de client meet en stuurt (versie 1).
- [m4.md](m4.md): de stromen (M4), met de metingen van "Klaar als" (de start van een stroom
  zonder browser).
- [m5.md](m5.md): ondertitels, sprites en hoofdstukken (M5).
- [e2e.md](e2e.md): de tests van media (de markers `media`, `media_perf` en `provider_real`),
  het harnas, de nep-servers, de jobs in CI, en hoe je ze lokaal draait.
