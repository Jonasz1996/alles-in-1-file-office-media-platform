# Fase 5 — Media en streaming

Status: **gebouwd en getest, versie 0.7.0** (2 november 2026). Alle pakketten (M0 tot en met
M10) en de integratie staan op de tak. In de sandbox zijn vier van de vijf eisen van "Klaar als"
gehaald; de vijfde (drie omzettingen tegelijk zonder haperen) bijna, zie hieronder. Het laatste
bewijs is de LXC zelf: stap 5, [Op de LXC](#op-de-lxc-stap-5).

Films, series, eigen video's en muziek afspelen in de browser, met zo weinig mogelijk omzetten,
via dezelfde poorten 80 en 443 als de rest van het platform. Een film is een gewoon bestand in de
boom; een bibliotheek is een map met een soort erop. ffmpeg draait alleen in een eigen,
afgeschermde container (de mediaworker), uit hetzelfde image als de app. Met media uit werkt
alles zoals in 0.6.0.

Jonas heeft de beslissingskaarten van fase 5 (B39–B48) nog niet beantwoord. Alles is gebouwd op
de standaarden van het ontwerp: de import kopieert, alleen de CPU, drie omzettingen tegelijk,
geen sleutel van TMDB en alleen lokale metadata, MusicBrainz uit, een publieke link zet niets
om, voortgang per account ([Vragen voor Jonas](#vragen-voor-jonas)).

## Klaar als

Uit het stappenplan: een 1080p-film start binnen 2 s (direct play) en 5 s (transcode); seek
werkt; 3 gelijktijdige transcodes zonder haperen; voortgang synchroniseert tussen apparaten. De
getallen zijn mediaan / slechtste, uit [metingen.md](metingen.md) (de worker op 3 cores, zoals
op de LXC).

| Eis | Gemeten | Uitkomst |
|---|---|---|
| Direct play: het eerste beeld ≤ 2 s | 436 / 450 ms (VP9, sandbox); 477 / 491 ms (H.264, Chrome for Testing in CI) | gehaald |
| Omzetten: het eerste beeld ≤ 5 s | 1663 / 1844 ms (VP9, sandbox); 1512 / 1532 ms (H.264, CI); zonder browser alleen 1,34 / 1,66 s, naast 2 andere omzettingen 1,23 / 1,59 s | gehaald |
| Zoeken werkt (weer spelen ≤ 3 s) | remux 885 / 918 ms, omzetten 2343 / 2485 ms, direct 2020 / 2095 ms (op 400 Mbit/s 1487 / 1628 ms) | gehaald; direct haalt de 1,5 s uit de tabel van het ontwerp alleen op een snel netwerk (Chromium zelf) |
| 3 omzettingen tegelijk zonder haperen | drie tegelijk gestart: 5 van 5 zonder haperen; gespreid met een sprong: 4 van 5 (één keer 0,6 s haperen na de sprong); met OnlyOffice ernaast 2 van 5; de API ≤ 104,5 ms (grens 200), de worker ≤ 793 MB (grens 3 GB) | bijna: zonder sprong gehaald; na een sprong of een herstart hapert soms één speler 0,1–1,5 s (bevinding 3 in [metingen.md](metingen.md#bevindingen)) |
| Voortgang synchroniseert (≤ 2 s) | 0,097 / 0,101 s (p95 van 20) | gehaald |

"Zonder haperen" is hier strenger dan in een browser: de virtuele speler van de belastingtest
wacht op elk segment, de speler in de browser buffert tot 30 s. De sandbox is 20–40 % trager dan
de referentie-CPU (`f` 1,22–1,40); daarom koos de toelating hier lagere kwaliteiten dan op een
CPU zo snel als de referentie (720 + 480 + 480 in plaats van 1080 + 720 + 720). Welke het op de
LXC worden, laat de meting daar zien.

## Wat er is

- **Bibliotheken en de import.** Een map wordt een bibliotheek (films, series, muziek of eigen
  video's) met **Als bibliotheek gebruiken** of **Nieuwe bibliotheek** op `/media`. De scanner
  haalt titels, jaren, seizoenen en afleveringen uit de namen (een eigen parser, getest op meer
  dan 500 namen) en uit `.nfo`-bestanden, posters uit de map, zonder internet. Hij kijkt bij elke
  wijziging in de app, bij de start en elke 6 uur. Een bestaande verzameling komt binnen met de
  import uit `/tank/platform/import` op `/beheer` → Media: standaard kopiëren, verplaatsen kan
  binnen hetzelfde dataset ([m2.md](m2.md)).
- **Afspelen: direct, remux en omzetten.** De browser meet zelf wat hij kan, en de app kiest:
  **direct** (de bytes zoals ze zijn, 0 cores), **remux** (een andere verpakking, bijna geen CPU)
  of **omzetten** (H.264, of VP9 voor een browser zonder H.264, op 1080p, 720p of 480p, in stukken
  van 2 s). HDR wordt bij omzetten SDR (standaard op 720p). Elk verzoek kijkt of je nog mag
  kijken; intrekken stopt binnen 15 s, uitloggen meteen. Een publieke link speelt direct en remux
  ([m3.md](m3.md), [m4.md](m4.md), [clientprofiel.md](clientprofiel.md)).
- **Ondertitels, sprites en hoofdstukken.** Elke tekstondertitel (SRT, ASS, WebVTT; ingebed of
  los naast de film) als WebVTT in de speler; een beeldondertitel (PGS, VobSub) wordt in het beeld
  gebrand. Sprites voor de zoekbalk (een beeld per 10 s) voor wat in een bibliotheek staat, en de
  hoofdstukken met het begin van de aftiteling ([m5.md](m5.md)).
- **De speler.** Een eigen speler op `/kijken` en in de viewer van Bestanden (geen hls.js), met
  native HLS voor Safari en iOS: kwaliteit, audiospoor, ondertitels, beeld-in-beeld, sprites bij
  de zoekbalk, de kaart "Volgende aflevering", herstel na een herstart van de app of de worker,
  één kwaliteit lager na twee keer haperen, en Downloaden als de server niet kan omzetten. Alles
  met het toetsenbord ([m6.md](m6.md), [toetsenbord-m6.md](toetsenbord-m6.md)).
- **Kijkvoortgang.** Per account, op elk apparaat binnen 2 s (SSE). Gezien bij 90 %, bij de
  aftiteling of 3 minuten voor het eind; de rijen Verder kijken en Volgende aflevering op
  `/media`, en Gezien markeren ([m7.md](m7.md)).
- **Muziek en gapless.** Albums, artiesten en eigen afspeellijsten op `/muziek`, en een
  muziekbalk die doorspeelt terwijl je verder klikt. Nummers sluiten naadloos aan: bewezen voor
  FLAC en Opus ([m9.md](m9.md)).
- **Metadata online.** Met een eigen sleutel van TMDB: posters, beschrijvingen in het Nederlands
  en koppelen vanzelf; wat twijfelt staat onder **Nakijken**, met Koppelen en Bewerken. Muziek via
  MusicBrainz en de Cover Art Archive als je dat aanzet. De sleutel staat versleuteld in de
  database en gaat alleen naar TMDB. Zonder sleutel (de standaard) gaat er niets naar buiten
  ([m8.md](m8.md)).
- **`/beheer` → Media en het runbook.** Het tabblad Media (Alt+8): de worker en zijn afscherming,
  ffmpeg, de meting van de CPU, het budget, wie er kijkt (met Stop), de achtergrond (met Mislukte
  opnieuw), de bibliotheken, de cache, de online metadata, de import, de instellingen en de
  belastingtest. Meldingen bovenaan `/beheer`, `app.cli media-status`, en media in hetzelfde ene
  `install.sh`, dat nooit stopt door media. Wat Jonas op de server doet: het runbook
  [media.md](../runbooks/media.md) ([m10.md](m10.md)).

De integratie (stap 4 van §5) werkte de open punten zonder eigenaar af, mat alles in de sandbox,
zette de standaarden op de metingen en draaide de installatietest met terugrollen naar 0.6.0
([integratie.md](integratie.md)).

## Media aan en uit

- **Op de server:** media staat na het bijwerken aan, met de worker in zijn eigen container
  (`MEDIA_SANDBOX=aan` in `/opt/platform/.env`). `MEDIA_SANDBOX=uit` is geen uit-knop maar een
  nooduitgang: ffmpeg draait dan in de app zelf, zonder afscherming (het runbook, "De
  nooduitgang").
- **Op `/beheer` → Media → Instellingen:** het vinkje **Media aan**, zonder herstart. Uit: niets
  van media draait, afspelen geeft een nette melding, je bestanden en bibliotheken blijven.
- **Terug naar de code van 0.6.0:** liever niet; liever media uit. Moet het toch, dan met de vijf
  regels van het runbook ("Terug naar 0.6.0", met de volledige commit). Zo nagelopen in de
  installatietest ([integratie.md](integratie.md#installatietest-en-terugrollen)).

## De grenzen

| Wat | De grens | Waarom, en waar het staat |
|---|---|---|
| Omzettingen tegelijk | 3, standaard; de eerste kijker hoger, de rest lager (bij een CPU als de referentie 1080 + 720 + 720) | Vraag 3; een 4K-bron telt dubbel, HDR zwaarder. Direct en remux tellen niet mee |
| Een tragere CPU | boven `f` 1,6 (de meting op `/beheer`) past met 3 plaatsen geen kijker meer: zet **Omzettingen tegelijk** dan op 2 (past tot 2,4) | de reserve van Keuze 26; het runbook, "De belastingtest op de LXC" |
| Terwijl OnlyOffice start (2 à 3 minuten) | een core minder; boven `f` ±1,07 wacht een nieuwe kijker tot DS klaar is ("de server is druk", de speler probeert het zelf opnieuw) | Keuze 26; bevinding 7 |
| HDR | omzetten alleen van een bron tot 1080p, standaard naar 720p; 4K HDR alleen direct of remux | Keuze 27: tone-mapping kost te veel CPU |
| Zoeken bij direct play | ±2 s op 40 Mbit/s, ±1,5 s op het LAN | Chromium zelf (bevinding 4) |
| Haperen na een sprong | soms één keer 0,1–1,5 s als drie omzettingen lopen | de voorrang bij de start geldt alleen voor het eerste segment (bevinding 3, een voorstel) |
| Cache | 20 GB voor de stukken van het kijken en de muziek; 5 GB voor sprites en ondertitels; onder 10 GB vrij op de schijf geen nieuwe remux of omzetting | Keuze 31; [integratie.md](integratie.md#standaarden) |
| Geheugen | de worker hooguit 3 GB (gemeten: 793 MB) | Mh |
| Een upload | hooguit 50 GB (`MAX_UPLOAD_BYTES`) | grotere verzamelingen via de import |
| Gapless | FLAC en Opus op het sample; MP3 en AAC niet gegarandeerd | m9.md; een lang nummer dat FLAC wordt, kan 415 geven (hooguit `cache_gb` / 8) |
| Een publieke link | zet niets om (direct en remux wel) | Vraag 5 |
| Namen | 95–97 % van de automatische koppelingen goed op een testset van 125 namen (tegen de nep-TMDB); wat twijfelt gaat naar Nakijken | Keuze 42, punt 9 in metingen.md |
| Browsers | getest in Chromium 141 (sandbox) en Chrome for Testing 156 (CI); Safari, iPhone, Firefox en webOS niet | fase 9 |
| Nodes | één node: sessies in het geheugen van de app | Keuze 48; het cluster later |

## Wat geschrapt of verschoven is, en waarom

Uit het stappenplan (§0.1 en Keuze 50 van het [bouwontwerp](bouwontwerp.md)):

- **guessit:** een eigen parser voor bestandsnamen, met een testset van meer dan 500 namen (B14:
  geen nieuwe afhankelijkheden).
- **inotify:** alles komt via de app binnen, en de bestanden heten naar hun inhoud; inotify zou
  niets zien. De scanner luistert naar de app, en loopt bij de start en elke 6 uur alles na.
- **TVDB:** kost geld; TMDB heeft ook series. AcoustID, fanart.tv en OpenSubtitles ook niet.
- **hls.js:** een eigen speler op MSE (B19), en native HLS voor Safari en iOS.
- **De tabel `device`:** de browser stuurt zijn profiel mee met elk afspeelverzoek; apparaten
  registreren hoort bij de apps (fase 9).
- **"3 transcodes":** drie, maar niet alle drie op 1080p: de CPU kan dat niet zonder GPU. De
  eerste kijker hoger, de andere lager (Vraag 3). Hardwareversnelling is klaargezet, niet
  gebouwd (B5 is open).
- **Ook niet in fase 5:** een fotobibliotheek (de tijdlijn van fase 3 is dat al), profielen en
  kinderslot (de kolom `profile` staat klaar; Vraag 6), externe mappen (fase 7), casten
  (Chromecast, DLNA), meerdere versies van één film, songteksten en intro overslaan,
  afspeellijsten delen, AV1 coderen, alvast omzetten, automatisch van kwaliteit wisselen (één
  kwaliteit per sessie, één stap lager na haperen), en de volledige belastingtest in CI (alleen
  een rooktest; de echte meting in de sandbox en op de LXC).

## Vragen voor Jonas

1. **De beslissingskaarten B39–B48** ([beslissingen](../fase-0/beslissingen.md)): nog niet
   beantwoord, dus nog "voorstel". In gebruik zijn de standaarden:
   - Vraag 1, de import: **kopiëren** (verplaatsen kan als de bron onder
     `/tank/platform/import` staat; dan op de Proxmox-host
     `chown -R 110001:110001 /tank/platform/import`);
   - Vraag 2, de CPU: **alleen de CPU**, geen GPU;
   - Vraag 3: **drie omzettingen tegelijk**, de eerste hoger, de andere lager;
   - Vraag 4: **geen sleutel van TMDB**, alleen lokale metadata;
   - Vraag 5: een publieke link zet **niets om**;
   - Vraag 6: voortgang **per account**, geen profielen.
2. **Vraag 7, een eigen uid voor de kinderen van de worker** (Keuze 11): het voorstel van M1 is
   nee. Het ergste wat een gekraakte ffmpeg dan kan, is media stilleggen; lezen kan hij niets
   ([m1.md](m1.md), keuze 6).
3. **Een sleutel van TMDB?** Gratis, voor niet-commercieel gebruik. Neem het API Read Access
   Token (de lange, die met `eyJ` begint) en zet het op `/beheer` → Media → Online metadata. Dan
   komen er posters en beschrijvingen, en gaan de titels van je films naar TMDB.
4. **De CPU en een GPU:** de uitvoer van `lscpu` en `ls -l /dev/dri` op de Proxmox-host. Dat
   zegt of een GPU later kan (het runbook, "Later: een GPU").
5. **Voorstellen uit de metingen** (niet beslist, [metingen.md](metingen.md#bevindingen)):
   voorrang bij de start ook na een sprong (bevinding 3); `max_transcodes` afleiden uit de meting
   (bevinding 7); de kostentabel op de LXC ijken met echte films (bevinding 6); en twee manieren
   om minder titels verkeerd te koppelen (Keuze 42, bevinding 5).
6. **Twee keuzes die niemand nam:** een bibliotheek die in een andere verhuist weigeren (dat
   verandert de bestandsroutes van fase 3; `/beheer` zegt het nu alleen), en een grens aan het
   aantal afspeellijsten ([integratie.md](integratie.md#niet-gedaan-met-de-reden)).

## Op de LXC (stap 5)

Jonas, ±30 minuten, na het bijwerken naar 0.7.0 (de [README](../../README.md)). De commando's
staan in het runbook [media.md](../runbooks/media.md); de uitkomst gaat in
[metingen.md](metingen.md).

- [ ] **De status:** `dc ps` toont `platform-media-1` als `healthy`, en
  `dc exec -T platform python -m app.cli media-status` zegt
  `Media: aan (afgeschermd, ffmpeg 7.1.5)` (het runbook, "De status lezen").
- [ ] **De meting (bench):** `/beheer` → Media → **Meting** → **Meten** (±1 minuut). Noteer `f`.
  Boven 1,6: zet **Omzettingen tegelijk** op 2.
- [ ] **De belastingtest met de knop:** `/beheer` → Media → **Belastingtest**, als er niemand
  kijkt (10 tot 20 minuten; eerst aanzetten, het runbook "De belastingtest op de LXC"). Fase a en
  b; als het kan ook c (de app herstart) en d (office herstart). Bewaar de uitkomst.
- [ ] **Een film op telefoon en laptop:** één die de browser zelf kan (een mp4 met H.264) en één
  die omgezet moet worden (een mkv met HEVC); zoeken, een ondertitel aan, en "Verder kijken" op
  het andere toestel binnen 2 s.
- [ ] **Voor Vraag 2:** `lscpu` en `ls -l /dev/dri` op de Proxmox-host.

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
- [m7.md](m7.md): de kijkvoortgang (M7), met de meting van het synchroniseren;
  [toetsenbord-m7.md](toetsenbord-m7.md) voor het toetsenbord.
- [m9.md](m9.md): muziek en gapless (M9), met de meting van gapless in de browser;
  [toetsenbord-m9.md](toetsenbord-m9.md) voor het toetsenbord.
- [m8.md](m8.md): metadata online (M8), met het contract voor /beheer;
  [toetsenbord-m8.md](toetsenbord-m8.md) voor het toetsenbord.
- [m10.md](m10.md): beheer en uitrol (M10), met de installatietest en wat alleen CI bewijst;
  [toetsenbord-m10.md](toetsenbord-m10.md) voor het toetsenbord, en het runbook
  [media.md](../runbooks/media.md).
- [integratie.md](integratie.md): de integratie (stap 4 van §5): de open punten die ze afwerkte
  of liet liggen (met de reden), de wijzigingen aan contracten, de metingen, de standaarden, de
  installatietest met terugrollen naar 0.6.0, en wat alleen CI of de LXC kan bewijzen.
- [metingen.md](metingen.md): de metingen Ma–Mp en de belastingtest in de sandbox, met de
  bevindingen; de getallen in [metingen.json](metingen.json).
- [e2e.md](e2e.md): de tests van media (de markers `media`, `media_perf` en `provider_real`),
  het harnas, de nep-servers, de jobs in CI, hoe je ze lokaal draait, en de lijst van alle e2e
  van media met wat elke test bewijst.
- Het runbook [media.md](../runbooks/media.md): wat Jonas op de server doet (de status, de
  nooduitgang, terug naar 0.6.0, de cache, de sleutel van TMDB, de import, de belastingtest en
  later een GPU).

## Hoe het gebouwd is

Het ontwerp gaf ±22 werkdagen: M0 alleen (±6), dan M1–M10 in een parallelle golf (±9), de
integratie (±3) en marge (§5). Hieronder per golf wat er samenkwam, de reviews en de
nacontroles; de details staan per pakket in `m0.md` tot en met `m10.md`.

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
- **M7, de kijkvoortgang** ([m7.md](m7.md)): de speler meldt zijn stand, de app houdt hem in het
  geheugen en schrijft hooguit eens per 10 s (pauze en stop meteen), en elk ander tabblad of
  apparaat ziet hem via SSE binnen 2 s. Gezien bij 90 %, de aftiteling of 3 minuten voor het
  eind; de rijen Verder kijken en Volgende aflevering op `/media`, met het toetsenbord
  ([toetsenbord-m7.md](toetsenbord-m7.md)).
- **M9, muziek en gapless** ([m9.md](m9.md)): albums, artiesten en afspeellijsten op `/muziek`,
  de muziekbalk die blijft spelen bij navigeren, en nummers die naadloos aansluiten (één
  MediaSource; de gegevens van `audio_info`, eigen lezers in de worker; `audio.mp4` via
  `remux_audio`). Bewezen voor FLAC en Opus in Chromium 141; alles met het toetsenbord
  ([toetsenbord-m9.md](toetsenbord-m9.md)).
- **M8, metadata online** ([m8.md](m8.md)): TMDB, MusicBrainz en de Cover Art Archive via één
  uitgaande client (alleen https, geen privé-adressen, een stroomonderbreker per provider), de
  sleutel van TMDB versleuteld in de database en alleen in de header naar TMDB. Een online
  bibliotheek koppelt vanzelf; wat twijfelt staat onder Nakijken, en Koppelen en Bewerken gaan
  met het toetsenbord ([toetsenbord-m8.md](toetsenbord-m8.md)). Posters en hoezen komen via ons
  eigen adres. Zonder sleutel (de standaard) blijft alles lokaal.
- **M10, beheer en uitrol** ([m10.md](m10.md)): het tabblad Media op `/beheer` (de worker,
  ffmpeg, de meting, wie er kijkt en Stop, de wachtrij, de bibliotheken, de cache, de providers
  met de sleutel van TMDB, de import en de instellingen; [toetsenbord-m10.md](toetsenbord-m10.md)),
  de meldingen, `app.cli media-status`, de belastingtest in de app, en media in `install.sh`
  (bijwerken en terugrollen, met de installatietest), met het runbook
  [media.md](../runbooks/media.md).

In de app staat media standaard uit: zonder `PLATFORM_MEDIA_ENABLED` is de app 0.6.0 plus
F1–F4, en de previews van video's werken zoals in 0.6.0 (via een worker voor één keer, M1).
`install.sh` zet de vlag aan (`compose.media.yaml`, sinds M10).

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

Bij het samenvoegen van golf 4 (M7, dan M9) kwamen beide pakketten met cherry-pick, met hun
eigen commits en boodschappen. Ze bouwden op `bcd90ed`; daarna stonden er al negen commits van de
lead op de tak (de springer van de speler en de e2e van M6 na CI-run 41002597837), en die raakten
geen bestand van M7 of M9. Eén botsing: `test_media_api_stubs.py` (M0), waar beide hun routes in
`BUILT` zetten; beide bleven. Wat er daarna nog gebeurde, staat onder "Bij het samenvoegen (golf
4)" in [m7.md](m7.md), [m9.md](m9.md) en [m2.md](m2.md):

- **De pagina's van M2 tonen de stand uit het geheugen van M7.** De Reader van M2 las alleen
  `playback_state`; een tik of sprong (die M7 tot 10 s in het geheugen houdt) kwam op de pagina
  van een item of een serie en in de lijst van een bibliotheek pas na het schrijven, terwijl
  Keuze 36 ≤ 2 s wil, ook voor de balkjes. Nu legt `progress.overlay` het geheugen over elke
  kaart van de Reader. `/media/home` neemt Net toegevoegd alleen nog uit `home_rows`.
- **Een test van de worker-kern die soms faalde** (M0-hotfix, alleen de test): de lijst van de
  fd's van een kind kwam soms precies terwijl de dynamische lader van `sleep` `libc.so.6` open
  had (fd 4, even later weer dicht). De test wacht nu tot de lader klaar is; een fd die echt
  meekomt, vangt hij nog steeds.

De uitslag op de samengevoegde stand, in de sandbox: api 3448 goed en 1 overgeslagen; `-m media`
(onder `taskset -c 0-2`, twee keer) 174 goed; web groen (872 tests, eerste lading JS 96,3 van 100
KB, de route Bestanden 43,6 van 45 KB, `/media` 14,2 van 45 KB, `/kijken` 24,7 van 45 KB, de
speler 24,9 van 35 KB, muziek 30,2 van 40 KB, i18n strikt zonder meldingen); de e2e van fase 3
en 4 110 goed; `-m office` 30 goed; e2e `-m media` 59 goed en 4 overgeslagen (alle vier H.264,
dat Chromium 141 niet kan). "Klaar als" van M7 hier: synchroniseren p95 0,081 s over 20 keer
(grens 2 s). Van M9: de fase springt 0,0 samples bij FLAC en hooguit 0,014 bij Opus (grens 1).
AAC gapless, de worker-container met `audio_info` en `remux_audio`, en Chrome for Testing ziet
pas CI. Jonas heeft de beslissingskaarten van fase 5 nog niet beantwoord: M7 en M9 bouwen op de
standaarden; B39–B48 staan niet als besloten.

De review van golf 4 had 15 bevindingen voor M7 en M9 (S1–S5, C1–C7, K1–K3); ze klopten alle
15. Elke bevinding staat met haar uitkomst, de commit en de tests onder "De review (golf 4)" in
[m7.md](m7.md) en [m9.md](m9.md). De belangrijkste:

- **Veiligheid:** `audio.mp4` had geen grens: een Opus van 6 kbps werd als FLAC ±106 × zo groot
  (nu een budget per remux uit de probe, `DISK_MIN` vrij, en een remux zonder lezer stopt); één
  te groot getal in een PUT van de voortgang hield alle voortgang van die gebruiker tegen (nu
  422, en elke stand apart geschreven); een `started_at` buiten de klok gaf 500; een valse Ogg
  hield de worker 0,4–0,7 s per bron vast (nu een CRC in C en hooguit vier pagina's per bron);
  albums en artiesten bleven zichtbaar na een verhuizing uit de bibliotheek (nu per map
  nagekeken, zoals de Reader van M2).
- **Wat niet klopte:** vragen tegelijk startten elk een remux op hetzelfde bestand; "(Disc 1)"
  en "(Disc 2)" werden twee albums; "A feat. B" maakte een compilatie; het oude nummer speelde
  door terwijl het nieuwe laadde (en zijn eind sloeg het gekozen nummer over); een vervangen
  bestand telde alleen in Verder kijken, niet op de pagina's van M2.
- **Wat ontbrak:** tests voor `discarded` in de remux, het opruimen van de buffer en de snelle
  weg van "volgende"; de noten bij de bouw in het ontwerp (§2.0, §2 M7 en M9, §3.3, Keuze 36–38;
  `sessions.create_audio` gebruikt niemand).

Geen M0-hotfix, geen migratie. De uitslag na de review, in de sandbox: api 3466 goed, 1
overgeslagen en 1 fout: `test_fuzz_10000_namen_met_vaste_seed` (M2) mat 0,514 s tegen een grens
van 0,5 s. `names.py`, `classify.py` en de test zijn sinds `bcd90ed` niet veranderd; los meet de
parser hier nu 0,41–0,68 s per 10.000 namen, de test slaagde los 9 van de 10 keer. De marge is
op deze sandbox te klein; dat is aan M2 (open punt). `-m media` (onder `taskset -c 0-2`, drie
keer) 174 goed; web groen (878 tests, eerste lading JS 96,3 van 100 KB, de speler 24,9 van 35
KB, muziek 30,2 van 40 KB, i18n strikt zonder meldingen); de e2e van fase 3 en 4 110 goed;
`-m office` 30 goed; e2e `-m media` 59 goed en 4 overgeslagen (alle vier H.264).

Na de review kwam de nacontrole. Die draaide elke nieuwe test op de code van vóór het herstel
(elke test faalde daar) en vond twee dingen in M7, elk met een test rechtgezet:

- **De job `media.voortgang` kwam bij elke nieuwe versie, en in de rij `default`** (K2). Ook een
  document of een film die niemand speelde, kreeg een job die tot 30 minuten op een probe
  wachtte, in de rij van fase 3 en 4, die ook zonder media draait (Keuze 14). Nu alleen voor een
  node waarvan iemand een stand heeft, en in de rij `media_scan` ([m7.md](m7.md), "De
  nacontrole (golf 4)").
- **Het afknippen in `apply()` had geen test** (S2). Nu een test die faalt als een van de vier
  grenzen (seq, positie, duur, audio) weg is.

In M9 vond de nacontrole niets om te herstellen; wel drie dingen om te weten, in [m9.md](m9.md):
een lang nummer dat FLAC wordt, krijgt 415 (`cache_gb / 8` per nummer: Opus stereo tot ±2,5 uur
bij `cache_gb` 20); een schijf die niet te meten is, blokkeert elke remux van audio (503); een
eerste pagina vol albums die net uit de bibliotheek gingen, toont even "Nog geen albums" boven
Meer laden. En: de job "e2e media" in CI draait `pytest -rs`, dus de metingen van een geslaagde
test (de p95 van M7, de fase van gapless, het resultaat van AAC) toont CI niet; alleen een fout
laat ze zien.

"Klaar als" van M7 en M9, per punt hier bewezen of in CI, staat onder "De nacontrole (golf 4)"
in [m7.md](m7.md) en [m9.md](m9.md). Geen M0-hotfix, geen migratie. De uitslag op de stand na de
nacontrole, in de sandbox: api 3469 goed en 1 overgeslagen (`test_fuzz_10000_namen_met_vaste_seed`
slaagde deze keer; de marge blijft een open punt van M2); `-m media` (onder `taskset -c 0-2`) 174
goed; web groen (878 tests, eerste lading JS 96,3 van 100 KB, de route Bestanden 43,6 van 45 KB,
`/kijken` 24,7 van 45 KB, de speler 24,9 van 35 KB, muziek 30,2 van 40 KB, i18n strikt zonder
meldingen); de e2e van fase 3 en 4 110 goed; `-m office` 30 goed; e2e `-m media` 59 goed en 4
overgeslagen (alle vier H.264, dat Chromium 141 niet kan). "Klaar als" van M7 hier:
synchroniseren p95 0,085 s over 20 keer (max 0,086 s; grens 2 s). Van M9: de fase springt 0,0
samples bij FLAC en hooguit 0,014 bij Opus (grens 1), het eind ligt 0 en 1 sample naast (grens
2). Jonas heeft de beslissingskaarten van fase 5 nog niet beantwoord: B39–B48 staan niet als
besloten.

Bij het samenvoegen van golf 5 (M8, dan M10) kwamen beide pakketten met cherry-pick, met hun
eigen commits en boodschappen (zeven van M8, veertien van M10). Ze bouwden op `0f40abb`; daarna
stond er één commit van de lead op de tak (`431ba5f`, een test van de bewaking), die geen bestand
van M8 of M10 raakte. Eén botsing:
`test_media_api_stubs.py` (M0), waar beide hun routes in `BUILT` zetten; beide bleven. Het
contract van M8 voor M10 (`providers.status()` en de functies van `secrets.py`) klopte met wat
M10 verwachtte. Wat er daarna nog gebeurde, staat onder "Bij het samenvoegen (golf 5)" in
[m8.md](m8.md), [m10.md](m10.md) en [m2.md](m2.md):

- **/beheer gebruikt de sleutelkluis van M8 rechtstreeks.** De omweg van M10 (`tmdb_hooks()`,
  zonder M8 503) is weg. Na een sleutel die werkt (Bewaren of Testen) en na MusicBrainz aan
  staat er meteen een ronde `media.lookup` klaar, niet pas na de housekeeping van 5 minuten.
- **Een 401 bij het opzoeken staat op /beheer:** de melding `media_provider_auth`, en "auth" als
  laatste fout (als zin, in NL en EN), ook als er al een netwerkfout stond.
- **De bronvermelding van TMDB op /beheer** (Keuze 40): de zin en de link van M8 in het blok
  Online metadata.
- **Een 422 geeft de invoer niet meer terug** (`app/errors.py`, fase 3): een te lange sleutel of
  een te lang wachtwoord stond in het antwoord van een validatiefout (open punt 5 van M8).
- **De e2e van M8 zet de sleutel via /beheer**, met het toetsenbord, eerst fout en dan goed; het
  hulpproces is weg. Dat vond twee fouten in de test zelf: Tab liep de hele pagina rond als de
  focus al op het veld stond, en een 204 heeft geen body om te lezen.
- **Nieuw: `test_media_beheer_samen.py`**: de routes van M10 tegen de echte kluis van M8 en de
  nep-providers (de sleutel, een 401, 24 uur onbereikbaar, MusicBrainz aan).
- **m2.md, afwijking 1 is dicht:** de import-e2e van M2 gaat sinds M10 via `/beheer`.

Geen M0-hotfix bij het samenvoegen, geen migratie. De M0-hotfixes van de pakketten zelf staan in
[m8.md](m8.md) en [m10.md](m10.md). De uitslag op de samengevoegde stand, in de sandbox: api 3767
goed en 2 overgeslagen (ruff en format schoon, ook `../e2e`); `-m media` (onder `taskset -c
0-2`, twee keer) 175 goed; web groen (907 tests, eerste lading JS 96,4 van 100 KB, de route
Bestanden 43,6 van 45 KB, `/kijken` 25,5 van 45 KB, de speler 25,1 van 35 KB, muziek 30,5 van
40 KB, i18n strikt zonder meldingen); de e2e van fase 3 en 4 110 goed; `-m office` 30 goed; e2e
`-m media` 63 goed en 4 overgeslagen (alle vier H.264, dat Chromium 141 niet kan), met
`test_media_m8.py` los drie keer na elkaar groen. Het image met de installatietest
(`media-image`), de worker-container en Chrome for Testing ziet pas CI ([m10.md](m10.md), "Wat
alleen CI kan bewijzen"). Jonas heeft de beslissingskaarten van fase 5 nog niet beantwoord: M8 en
M10 bouwen op de standaarden (de import kopieert; geen TMDB-sleutel, alleen lokale metadata);
B39–B48 staan niet als besloten.

De review van golf 5 vond 21 punten (S1–S4, C1–C9, K1–K8). Alle 21 zijn gefixt. Elke fix in de
code heeft een test die zonder de fix faalde; S4, K1 en K7 waren tests die ontbraken, K4, K5 en
K8 alleen tekst. De uitkomst per punt staat onder "De review (golf 5)" in [m8.md](m8.md) en
[m10.md](m10.md). Het belangrijkste:

- **Geen stuurtekens meer in de terminal van root** (S1): `media-status`, ook `--sessions` en dus
  `watchers()` van `install.sh`, maakt van elk stuurteken een vraagteken.
- **De belastingtest in de container** (S2): het blok in het runbook zet `PLATFORM_MEDIA_LOADTEST`
  nu ook bij de worker. De installatietest gebruikt dat blok zelf.
- **Het opzoeken van M8:** een plaatje hoort bij de koppeling van nu (C1); één ding dat steeds
  faalt, houdt de rest niet meer tegen (C2); niets gaat naar buiten als het uit staat (C4).
- **Sprites komen terug na Opruimen** (C3); **/beheer toont de map** van elke bibliotheek (K3,
  een M0-hotfix van één optioneel veld).
- **Alleen het lange TMDB-token werkt** (K2), en het runbook en /beheer zeggen dat.
- **Nieuw:** `test_media_providers_real.py` (`-m provider_real`, met de hand) en de e2e van de
  worker zonder ffmpeg (`test_de_worker_zonder_ffmpeg`).

**Naar de integratie** (K4): de open punten zonder eigenaar staan met hun eigenaar in
[m10.md](m10.md), "Naar de integratie": een mislukte ondertitel of sprite opnieuw (M5), de
grens voor `subs` en `trick`, photometa na een tijd zonder worker, media in de balk van Opslag
(M0, afwijking 14), een probe die niet kan uitvoeren (M0), de open punten 1–3 van M8 (M2),
Downloaden op `/kijken` zonder ffmpeg (M6), stuurtekens bij de bron (M2) en Keuze 42 op echte
namen.

Na de review kwam de nacontrole. Die draaide de tests van de review op de code van vóór het
herstel (20 rood) en per fix met alleen dat stuk teruggezet; elke fix had een test die faalde.
Ze vond vier dingen, elk met een test rechtgezet:

- **S1 maakte van witruimte niets** (`334b480`): een tab of regeleinde in een titel van TMDB,
  MusicBrainz of een PATCH viel weg, dus `De\tNacht` werd "DeNacht". Nu wordt het een spatie.
- **C1 hield de still van een aflevering niet tegen** (`0943a7f`): een serie die met de hand een
  andere werd terwijl de still nog wachtte, kreeg de still van de oude serie. De wachtende art
  van elke aflevering die terug naar lokaal gaat, is nu weg.
- **C7 toetste de aanroep niet** (`0943a7f`): nu een test per plek (het opzoeken en het venster)
  dat een late 401 de nieuwe sleutel niet dichtzet.
- **Het runbook van de belastingtest met `MEDIA_SANDBOX=uit`** (`57005cf`): sinds S2 noemt het
  blok de dienst `media`, die er dan niet is; het runbook zegt nu wat dan te doen.

Verder in de lijst van M0-hotfixes van [m10.md](m10.md): `AdminLibrary.path` (K3), die er nog
niet in stond. Geen bevroren contract gebroken; met media uit verandert er niets. "Klaar als"
van M8 en M10, per punt hier bewezen of in CI, staat onder "De nacontrole (golf 5)" in
[m8.md](m8.md) en [m10.md](m10.md). De installatietest (`test_install_media.sh --stappen`) gaf
hier 54 van 54, met een leeg pakket ffmpeg (geen Debian-spiegel); de transcode erin ziet pas CI.
De uitslag op de stand na de nacontrole, in de sandbox: api 3793 goed en 2 overgeslagen (ruff
en format schoon, ook `../e2e`); `-m media` (onder `taskset -c 0-2`) 175 goed; web groen (907
tests, eerste lading JS 96,4 van 100 KB, de route Bestanden 43,6 van 45 KB, `/kijken` 25,5 van
45 KB, de speler 25,1 van 35 KB, muziek 30,5 van 40 KB, i18n strikt zonder meldingen); de e2e
van fase 3 en 4 110 goed; `-m office` 30 goed; e2e `-m media` 64 goed en 4 overgeslagen (alle
vier H.264, dat Chromium 141 niet kan), met de 20 films van M8 en de worker zonder ffmpeg van
M10. Jonas heeft de beslissingskaarten van fase 5 nog niet beantwoord: B39–B48 staan niet als
besloten.
