> Werkdocument: het bouwontwerp van fase 5 (media en streaming), revisie 2. Revisie 2 is leidend.
> Revisie 1 staat in [bouwontwerp-r1.md](bouwontwerp-r1.md).
> Nog niet besloten. De vragen aan Jonas staan onder "Vragen voor Jonas". Tot hij antwoordt,
> bouwen we op de standaard die daar staat.
> Bijgewerkt na de spike (M0, 28 oktober 2026). Wat de spike veranderde, staat erin met
> "(spike)"; de uitkomsten per stap staan in [spike.md](spike.md), het overzicht in §6
> ("Uitslag van de spike (M0)"). Revisie 2 blijft verder de tekst.

# Fase 5 (media en streaming): bouwontwerp, revisie 2

Basis: branch `claude/stappenplan-uitwerken-lu4uv3` @ `263abd7` (v0.6.0, migraties 0001–0007).

Revisie 2 verwerkt de kritiek op revisie 1: 22 punten over veiligheid en media (SEC-1–8,
MED-1–14) en 23 over haalbaarheid (R01–R23). Ik heb elk punt nagelopen in de code, in de notities
en, waar het kon, met een meting in de sandbox (V1–V8 hieronder). Geen enkel punt bleek onjuist.
Wat ik niet overnam, zijn delen van voorgestelde fixes. §6 zegt per punt wat er veranderde, en
waarom een deel niet.

De grootste veranderingen:

- **De worker draait als eigen gebruiker** `media` (uid 10002), niet als 10001 (Keuze 11). Met
  uid 10001 kon ffmpeg een blob via `/proc/self/fd/3` openen om te schrijven. Dat is nagemeten
  (V1). ffmpeg is nu 0711, de worker zet zichzelf op niet-dumpbaar, en de app maakt elke pipe
  (SEC-1).
- **Intrekken werkt ook midden in een stream** (Keuze 21). `/file` en `audio.mp4` kijken per stuk
  of de sessie nog leeft. Een sessie pint de inhoud (sha, grootte). Een vervangen bestand geeft
  `410` (SEC-2, SEC-3).
- **De kosten tellen de bron mee** (Keuze 26). Decoderen van 4K en HEVC10 kost apart. VP9 kost
  hetzelfde als x264. HDR boven 1080p geeft een nette weigering (MED-1, R02, V3).
- **Beeld en geluid lopen gelijk** (Keuze 22, 23). `+negative_cts_offsets`, `-g 100000` en
  `-fpsmax 60`. De knipper rekent met getekende offsets (MED-2, MED-3, V4, V5).
- **Een mkv buiten een bibliotheek speelt als remux** (Keuze 17). De keyframe-index komt bij het
  afspelen, binnen de 5 s (R03).
- **De worker heeft grenzen per soort werk** en een totaal aantal processen (Keuze 12). Een
  zoekactie wacht tot de oude ffmpeg weg is (R11, R14, MED-12).
- **De spike draait in CI vanaf dag 1** (§3.7). Wat het image moet bewijzen, bewijst de job
  `media-image` (R01, R12).
- **Terug naar 0.6.0 volgt de volgorde van fase 4** (Keuze 47). Eerst stoppen, dan het schema,
  dan het script met een vaste commit (R05).

Revisie 1 voegde twee ontwerpen samen. Ontwerp A ("eenvoudig, veilig en eerlijk over de
hardware") is de basis. Van ontwerp B ("zo veel mogelijk eigen code, en de API van fase 5 is
meteen die van fase 9") komen de stukken waar B gelijk had of beter bouwt. Eén stuk is nieuw:
het segmentrooster. Daar hadden A en B allebei een rooster dat hapert als de CPU druk is. Waar A
en B elkaar tegenspraken, heb ik het in de sandbox nagemeten (J1–J4 hieronder).

Bronnen (scratch, niet in het repo):

- `fase5/feasibility.md`: de proef in de sandbox (ffmpeg 6.1.1, Chromium 141, 4 vCPU, geen GPU);
- `fase5/media.md`: onderzoek naar HLS, MSE, codecs, HDR, ondertitels, Jellyfin, Plex, Kodi,
  TMDB en MusicBrainz, met een bron per bewering;
- `fase5/code-map.md`: waar fase 5 in de code inhaakt, met regelnummers;
- `fase5/judging.md`: het oordeel over A en B, per onderdeel en per lens;
- `fase5/design-a/` (D1–D7), `fase5/proef/` (N1–N23) en `fase5/judge/` (J1–J4): de scripts.

Bij een bewering staat waar hij vandaan komt:

- **[gemeten]**: gedraaid in de sandbox;
- **[doc]**: officiële documentatie, een RFC of broncode;
- **[niet getest]**: niet bevestigd. Daar ontwerpen we voorzichtig. De spike (§3.7) of een test
  (§4) beslist.

Afkortingen:

- **app**: ons platform (API en web-app in één container);
- **worker**: de mediaworker, het enige proces dat ffmpeg start (Keuze 11);
- **sessie**: één keer afspelen van één bestand door één speler;
- **plan**: wat een sessie afspeelt: modus, trede, audiospoor, ondertitel, tone-mapping;
- **trede**: een hoogte plus een preset, zoals "720p superfast";
- **producent**: één lopende ffmpeg die de segmenten van één sessie maakt;
- **MSE**: Media Source Extensions. **MMS**: ManagedMediaSource (Safari);
- **sid**: het id van een afspeelsessie.

**Nagemeten bij deze revisie** (scripts in `fase5/judge/`, ffmpeg 6.1.1, Docker 29.9.1):

- **J1. Een file descriptor gaat van de app-container naar een afgeschermde worker-container.**
  De worker draaide met `--network none --read-only --tmpfs /tmp --tmpfs /data --cap-drop ALL
  --security-opt no-new-privileges --pids-limit 256 --user 10001`, met alleen een benoemd volume
  op `/run/media`. De app opende een bestand alleen-lezen en stuurde de fd met
  `socket.send_fds`. Uitkomst:
  - de worker leest en zoekt via de fd; de hash van een stuk midden in het bestand klopt;
  - schrijven op die fd geeft `EBADF`;
  - het lege volume nam eigenaar 10001 en modus 0700 over van de map in het image;
  - in de worker: verbinden naar buiten faalt, `/` is alleen lezen, `/data` is leeg, en het pad
    van het bestand bestaat daar niet;
  - `readlink /proc/self/fd/N` toont wel het pad uit de app (`/data/blobs/…/<sha>`). De worker
    weet dus welke sha hij leest. Dat is geen lek: hij heeft de inhoud toch al.

  Dit was in de sandbox, niet in een LXC [niet getest in de LXC]. Revisie 2: met `--user 10001`
  kan de worker de blob wel via `/proc/self/fd/N` opnieuw openen om te schrijven (V1). De worker
  krijgt daarom een eigen uid.
- **J2. Hoe snel fragmenten uit de pipe komen,** met een echte 1080p H.264-bron (60 s,
  5,9 Mbit/s), x264 veryfast, een IDR elke 2 s, fMP4 naar `pipe:1`:

  | Run | Snelheid | Eerste fragment van 2 s |
  |---|---|---|
  | 1 × 1080p | 3,5–3,8× | 0,8 s warm, 1,9 s koud |
  | 3 × 720p tegelijk | 2,3–2,4× | 1,3–1,4 s |
  | 3 × 1080p tegelijk | 1,4× | 2,7–3,0 s |

  Echte inhoud is lichter dan de testbron met ruis uit de proef (2,54×, 1,67× en 0,97×). Het
  ontwerp rekent met de proef: dat is de voorzichtige kant.
- **J3. Haperen per segmentrooster.** De aankomsttijden van J2, nagespeeld voor een speler die
  pas een segment krijgt als het helemaal af is:

  | Run | 2 s, dan 6 s (A) | 2, 2, 2 s, dan 6 s (B) | overal 2 s (deze revisie) |
  |---|---|---|---|
  | 3 × 720p, 2,3× | 0,6–0,9 s haperen | 0 | 0 |
  | 3 × 720p, nagebootst op 1,7× | 1,6–2,1 s | 0–0,6 s | 0 |
  | 3 × 1080p, 1,4× | 2,2–2,6 s | 1,0–1,4 s | 0 |

  De regel erachter: het eerste lange segment komt pas op tijd als de producent minstens 3×
  realtime haalt (A), 1,67× (B) of 1× (overal 2 s). Daarom heeft deze revisie overal segmenten
  van 2 s (Keuze 22). A's belastingtest zag dit niet: zijn virtuele speler wachtte op segment 0
  én 1 voor hij begon.
- **J4. Twee details van de muxer en de keyframes** (naar een pipe):
  - zonder `+frag_discont` in `-movflags` begint `tfdt` altijd op 0, ook met `-copyts
    -output_ts_offset 1 -avoid_negative_ts disabled`. Met de vlag: na `-ss 10` is de eerste
    `tfdt` 11 s. De commando's van A misten de vlag;
  - `t` in `-force_key_frames expr:` telt vanaf het begin van de uitvoer, niet in brontijd. Na
    `-ss 11` vallen de keyframes op brontijd 11, 13, 15. Met herstarts alleen op een veelvoud van
    2 s geeft `expr:gte(t,n_forced*2)` dus precies het rooster (B's N10 zag dit ook).

**Nagemeten bij revisie 2** (sandbox: Intel Xeon @ 2,10 GHz, familie 6 model 207, 4 vCPU onder
KVM, ffmpeg 6.1.1, Docker 29; de commando's staan in [haalbaarheid.md](haalbaarheid.md)):

- **V1. Opnieuw openen via `/proc`** (SEC-1). Een blob van 10001 met modus 0600. De map is alleen
  lezen, het proces heeft geen caps en `no_new_privs`, en houdt alleen fd 3 (`O_RDONLY`):
  - als uid 10001: het pad openen om te schrijven faalt, maar `os.open('/proc/self/fd/3',
    O_WRONLY)` lukt. De blob was daarna veranderd;
  - als uid 10002: lezen via fd 3 werkt, opnieuw openen geeft `Permission denied`. De blob bleef
    gelijk.
- **V2. Wat een buurproces van een ander ziet** (SEC-1). Onder dezelfde uid:
  - een kind van een programma met modus 0711 is niet dumpbaar. `fd` en `io` geven
    `PermissionError`. `status`, `stat` en `limits` blijven leesbaar, dus het meten van `VmRSS`
    werkt nog;
  - de worker zelf zet `prctl(PR_SET_DUMPABLE, 0)`. Daarna kan een kind niet meer in
    `/proc/<worker>/fd/N`;
  - in Docker: een map `/run/media` uit het image met eigenaar `media`, groep `platform` en modus
    2750 komt zo in een leeg benoemd volume. De worker (uid 10002) maakt de socket met modus 0660,
    en die krijgt de groep van de map. De app (uid 10001, groep 999) verbindt. Schrijven in de map
    kan de app niet.
- **V3. Wat decoderen en coderen kost,** in cores voor 1,0× (CPU-tijd gedeeld door de mediaduur),
  fMP4 naar `pipe:1`:

  | Bron → uitvoer | Cores voor 1,0× |
  |---|---|
  | H.264 1080p24 (echte bron), alleen decoderen | 0,18–0,19 |
  | HEVC 10-bit 1080p24, alleen decoderen | 0,31 |
  | HEVC 10-bit 2160p24, alleen decoderen | 1,31 |
  | H.264 1080p → 720 x264 veryfast | 0,60 |
  | H.264 1080p → 720 VP9 realtime (`-cpu-used 8 -row-mt 1`) | 0,57–0,59 |
  | HEVC10 HDR 1080p → 720 tone-mapping, x264 veryfast | 1,35 |
  | HEVC10 HDR 1080p → 720 tone-mapping, VP9 realtime | 1,27 |
  | HEVC10 HDR 2160p → 720 tone-mapping, x264 veryfast | 2,28 (eerste fragment na 3,4 s) |

  Decoderen schaalt met de pixels: 2160p kost 4,2× zoveel als 1080p. VP9 realtime kost hetzelfde
  als x264 veryfast. HDR naar SDR is ±1,65× de SDR-trede (inclusief het decoderen van HEVC10).
- **V4. Tijden in de fragmenten** (MED-2). Gelezen uit `tfdt` en `trun` van het eerste fragment:

  | Run | `tfdt` | `trun` | Eerste offsets |
  |---|---|---|---|
  | transcode, movflags van revisie 1 | 12800 (tijdschaal 12288) | v0 | 1024, 1536, 512 |
  | transcode, met `+negative_cts_offsets` | 12800 | v1 | 0, 512, −512 |
  | remux, movflags van revisie 1 | 16336 (tijdschaal 16000) | v0 | 1328, 2000, 672 |
  | remux, met `+negative_cts_offsets` | 16336 | v1 | 0, 672, −656 |

  Zonder de vlag komt het eerste beeld 83–104 ms na het geluid. Met de vlag is het eerste beeld
  gelijk aan `tfdt`.
- **V5. Keyframes bij een bron van 240 fps** (MED-3). Met het commando van revisie 1 vallen ze op
  0,008, 1,05, 2,008, 3,05, 4,008 en 5,05 s: x264 maakt elke 250 beelden een extra IDR. Met
  `-g 100000` alleen op 0,008, 2,008 en 4,008 s. `-fpsmax 60` geeft 60 fps. VP9 legt ze met en
  zonder `-g 100000` op 0, 2, 4 en 6 s.
- **V6. Een harde geheugengrens** (SEC-7). 4K HEVC → 720 x264 veryfast met `-threads 4`:
  - `RLIMIT_DATA` 1200 MB: klaar, 5,3 s, piek 520 MB RSS;
  - `RLIMIT_DATA` 400 MB: stopt na 0,4 s met code 187;
  - `-max_alloc 536870912 -max_pixels 35389440` decodeert 4K gewoon.
- **V7. Het eerste fragment op 3 cores** (R07, R08). `taskset -c 0-2`, de echte 1080p-bron,
  het commando van §3.3:

  | Run | Eerste fragment | Haperen |
  |---|---|---|
  | 1 × 1080 veryfast alleen | 2,59 s | 0 |
  | 1080 vf + 2 × 720 superfast, alle drie op t = 0 | 4,04 / 4,00 / 3,28 s | 0,28 s (1080) |
  | dezelfde drie, gestart op 0, 10 en 20 s | 1,86 / 3,25 / 1,25 s | 0 |

  Alleen haalt 1080 de 2,5 s net niet. Drie tegelijk blijven onder de 5 s.
- **V8. Fixtures maken** (R10, R13):
  - srt naar `dvdsub` kan niet ("Subtitle encoding currently only possible from text to text or
    bitmap to bitmap"). Een eigen `.sup` (PGS) naar `dvdsub` werkt: 1280×720, pakketten op 0,
    10, 17 en 30 s;
  - een bron van 1080p24 maken (240 beelden, alleen genereren): `testsrc2` met ruis 1,05 s,
    `cellauto` 1,95 s, `life` 8,9 s. Met x264 ultrafast op 6 Mbit/s kost 20 s media 3,3 s
    (`testsrc2`) tot 5,0 s (`cellauto`). Een bron van 300 s kost dus 50–75 s;
  - `-f matroska -fd 4 fd:` schrijft naar een geërfde fd van een gewoon bestand, met Cues: de fd
    is zoekbaar.

**Uit de eerdere proeven,** en nog steeds de basis:

- D1: ffmpeg leest een bestand via een geërfde fd (`-protocol_whitelist fd,pipe -f <demuxer>
  -fd 3 -i fd:`), ook met zoeken (mp4 met de `moov` achteraan, mkv met Cues).
- D2: fMP4 naar een pipe geeft eerst `ftyp`+`moov` (de init), dan per keyframe `moof`+`mdat`.
  De app kan dat knippen. ffmpeg schrijft geen enkel bestand.
- D4 en N10: een herstart op een grens van het rooster geeft dezelfde videotijden als een run
  vanaf 0. Het eerste audiofragment wijkt 32–54 ms af.
- D6: tegendruk werkt. Leest de app 4 s niet uit de pipe, dan staat ffmpeg stil (0,14 s CPU in die
  4 s) en loopt daarna gewoon door.
- D7: een uitvoer-pipe moet erfbaar zijn (`os.set_inheritable` en `pass_fds`).
- N1–N6: de downloadroute houdt een databaseverbinding vast tijdens het streamen (16 gepauzeerde
  streams geven na 30 s een 500), `HEAD` geeft 404, het downloadtoken leeft 10 minuten, de CSP
  mist `media-src`, en stoppen wacht op open streams.
- N13: de Chromium van Playwright heeft geen H.264, AAC, HEVC en AC-3. Wel VP9, AV1, Opus, FLAC
  en MP3 (als `audio/mpeg`).
- N16 en N17: de kostentabel van x264 en van tone-mapping (Keuze 26).
- N18: de omzetter van ffmpeg naar WebVTT verliest `\an8` en kleur.
- N20: gapless klopt op het sample voor FLAC en MP3 met LAME-header. AAC uit ffmpeg houdt
  688 samples stilte aan het eind.
- N22: het app-image (Debian 13) heeft volgens de pakketgegevens ffmpeg 7.1.5. Niet gedraaid: apt
  is dicht in de sandbox.

---

## 0. Uitgangspunten

### 0.1 Wat er sinds het stappenplan veranderd is

- **Geen inotify.** Alles komt via de app binnen: upload, import, versies, prullenbak. De
  bestanden in `/data/blobs` heten naar hun sha; een naam of map verandert alleen in de database.
  inotify zou dus niets zien. De scanner luistert naar `files.notify` en loopt bij de start en
  elke 6 uur alles na (Keuze 5). Een map buiten de app (alleen lezen) hoort bij fase 7.
- **Geen guessit** (B14). Een eigen bestandsnaamparser met een testset van ≥ 500 namen
  (Keuze 7).
- **Geen hls.js** (B19). Een eigen speler. Een eigen MSE-lader van 176 regels werkte in de proef:
  spelen na 0,22 s, zoeken en weer lopen in 0,37 s [gemeten].
- **Geen TVDB.** TVDB kost geld [doc]. TMDB heeft ook series.
- **Geen Celery of Redis** (B17), **geen nginx of Traefik.** Eigen jobs, met een eigen rij voor
  media (Keuze 14). De app serveert zelf playlists en segmenten.
- **Geen fotobibliotheek.** De tijdlijn en albums van fase 3 zijn de fotobibliotheek. De soorten
  zijn films, series, muziek en eigen video's.
- **Een import naast uploaden.** Een verzameling van honderden GB via de browser uploaden kan,
  maar is onhandig. Een import vanuit `/data/import` start op `/beheer` (Keuze 2).
- **Eén node.** Sessies zitten in het geheugen. "Gepind op één node, hervatten op een andere"
  wordt: de speler vraagt bij `410` zelf een nieuwe sessie op zijn positie. Dat vangt nu een
  herstart van de app op, en later de uitval van een node (Keuze 48).
- **Het datamodel wijkt af van het stappenplan:**
  - de analyse hangt aan de inhoud (`media_probe`, per sha), niet aan het item. Twee kopieën zijn
    één analyse;
  - geen tabel `device`: het clientprofiel gaat mee met elk afspeelverzoek (Keuze 20). Apparaten
    registreren hoort bij fase 9;
  - `playback_state` krijgt een kolom `profile` (nu altijd leeg). Profielen zelf komen later
    (Vraag 6);
  - geen kolom `scan_interval` op een bibliotheek.
- **"3 gelijktijdige transcodes zonder haperen" kan, maar niet alle drie op 1080p** [gemeten]:
  - 3 × 1080p veryfast haalt 0,97× (testbron) tot 1,4× (echte bron, J2). Te krap;
  - 3 × 720p veryfast haalt 1,67× tot 2,4×;
  - 2 × 1080p veryfast haalt 1,37×;
  - HDR naar SDR kost 2,3 cores op 1080p en 1,21 op 720p;
  - een bron van 4K kost alleen al aan decoderen 1,31 cores (HEVC10, V3). De kosten tellen de bron
    dus mee (Keuze 26).

  De toelating geeft daarom 1080p aan de eerste kijker en een lagere trede aan de tweede en derde,
  zodat de derde altijd nog past (Keuze 26, Vraag 3).
- **4K HDR live omzetten past niet** (3,2 cores voor 1,0× naar 1080p, 2,28 naar 720p, V3). HDR
  naar SDR live alleen van een bron tot 1920×1088, en standaard op 720p. Een grotere HDR-bron
  geeft een nette weigering met de keuze om direct of remux te spelen (Keuze 27).
- **Hardwareversnelling: klaargezet, niet gebouwd.** B5 is open. Er is geen `/dev/dri`. Die
  doorgeven is een wijziging op de host die alleen Jonas kan doen. Het platform werkt altijd op de
  CPU (Keuze 28).
- **De server schrijft alle playlists zelf.** ffmpeg schrijft een VOD-playlist pas aan het eind,
  en laat `CODECS` weg bij VP9 en Opus [gemeten].
- **De Chromium van Playwright heeft geen H.264 en geen AAC** [gemeten]. De doelcodec is dus een
  uitkomst van het profiel. Tests spelen VP9 of AV1 met Opus. CI draait Chrome for Testing 156.
  Die heeft H.264 en AAC waarschijnlijk wel [niet getest].
- **De downloadroute heeft vier gaten** die afspelen raken (N1–N6). Het fundament repareert ze
  (§3.5).
- **Tijd.** Het stappenplan geeft 5 weken. Dit ontwerp: ±22 werkdagen, met marge (§5).

### 0.2 De invalshoek

| Principe | Wat het hier betekent |
|---|---|
| Weinig bewegende delen | Eén extra container (de worker), uit hetzelfde image. Geen Redis, geen wachtrij-server, geen tweede database. Sessies in het geheugen van de app. Segmenten in een gewone map, per sessie. |
| Direct play eerst | Een bestand dat de client kan spelen, gaat als bytes met Range: 0 cores. Remux kost bijna niets. Transcode alleen als het niet anders kan. |
| Eén segmentmodel | ffmpeg schrijft fMP4 naar een pipe. De app knipt. Bij transcode is elk segment precies 2 s; bij remux volgen de segmenten de echte keyframes. |
| ffmpeg apart | ffmpeg draait alleen in de worker, als eigen gebruiker (uid 10002): geen netwerk, geen data, alleen een open bestand en een pipe terug (Keuze 11, 12). |
| Het profiel komt uit de client | De browser test zelf wat hij kan, met de echte codec-strings van het item. Geen lijst van browsers (Keuze 20). |
| Toegang per verzoek | Elk verzoek onder `/api/v1/stream/` controleert de sessie en, met een cache van hooguit 15 s, het recht van nu. Een lange respons (`/file`, `audio.mp4`) kijkt dat per stuk opnieuw na (Keuze 21). |
| Nep voor alles buiten ons | Een nep-worker, een nep-stream, en nep-servers voor TMDB, MusicBrainz en de Cover Art Archive. Geen test praat met een echte dienst (§3.6). |
| Update kan niets breken | Media is een extra laag. Zonder worker werkt alles van fase 4. `install.sh` stopt nooit door media. Terug naar 0.6.0 kan, in de volgorde van fase 4 (Keuze 47). |
| Eerlijke cijfers | Elk "klaar als" heeft een meting met een grens (§4.7). Het platform meet de eigen CPU en zet het budget daarop (Keuze 26). |
| Liever schrappen | Keuze 50 noemt wat eruit ging, en waarom. |

### 0.3 Hoe een film bij de browser komt (alleen 80 en 443)

```
browser ── POST /api/v1/media/{node}/play {profiel, keuzes} ──▶ app: decide(probe, profiel, keuzes, budget)

direct     <video src=/api/v1/stream/{sid}/file>    ◀── app leest de blob (Range)             0 cores
remux      MSE ◀── /api/v1/stream/{sid}/{n}.m4s     ◀── app knipt ◀─pipe─ worker: ffmpeg -c copy   ±0
transcode  MSE ◀── /api/v1/stream/{sid}/{n}.m4s     ◀── app knipt ◀─pipe─ worker: ffmpeg x264   0,6–2,3
native HLS (Safari, iOS, later TV) ◀── /api/v1/stream/{sid}/master.m3u8 → v.m3u8 → dezelfde segmenten

 ┌──────────── container "platform" (app) ─────────────┐        ┌──── container "media" (worker) ────┐
 │ decide, sessies, toegang, toelating, knipper,       │  fd    │ uid 10002, geen netwerk, alleen    │
 │ playlists, cache in /data/media                     │ ─────▶ │ lezen, geen /data, geen geheimen   │
 │ opent elke blob en maakt elke pipe zelf (uid 10001) │ ◀───── │ ffmpeg leest fd:, schrijft pipe:1  │
 └─────────────────────────────────────────────────────┘  pipe  └────────────────────────────────────┘
                  Unix-socket in het benoemde volume media-run (/run/media)
```

- De app opent het bestand (alleen lezen) en geeft de worker de file descriptor over een
  Unix-socket. ffmpeg ziet geen pad, geen `/data`, geen database en geen netwerk (J1). Omdat de
  worker een andere uid heeft dan de eigenaar van de blob, kan hij de fd niet via `/proc` opnieuw
  openen om te schrijven (V1).
- ffmpeg schrijft fMP4 naar een pipe die de app maakte. De app knipt die in een init en
  segmenten, schrijft ze in de cache van de sessie, en maakt zelf `map.json` en de playlists.
- Metadata van TMDB en MusicBrainz haalt de app zelf op. De worker heeft geen internet.
- In het LAN zonder domein werkt alles over `http://<ip>/…`. Met een domein over https.

---

## 1. Keuzes

Elke keuze geeft de standaard en waarom. Waar Jonas moet kiezen, verwijst de keuze naar een vraag
onderaan.

### Inname en bibliotheken

1. **Media woont in de boom.** Een film is een gewoon bestand, net als een foto of een docx.
   - Ontdubbelen, quotum, delen, versies, prullenbak en back-up werken dan al (fase 2 en 3).
   - De BlobStore blijft de enige plek met inhoud. Elke analyse hangt aan een sha, en een sha
     verandert nooit.
   - De toegang is één regel: wie de node mag lezen, mag hem afspelen (`access.resolve`).
   - Grote bestanden gaan al hervatbaar via tus. `max_upload_bytes` is 50 GiB. Dat wordt
     instelbaar in `.env` (`MAX_UPLOAD_BYTES`; compose zet `PLATFORM_MAX_UPLOAD_BYTES`).
2. **Importeren uit `/data/import`.** Voor een bestaande verzameling.
   - De bronmap is `/tank/platform/import` op de LXC. De container ziet die al als
     `/data/import`, via de bestaande koppeling `${DATA_ROOT}:/data`. Compose verandert niet.
   - Starten gaat op `/beheer` → Media → Importeren (alleen beheerders): een bronmap onder
     `/data/import`, een doelmap (de mapkiezer van fase 3), een eigenaar, en kopiëren of
     verplaatsen. Hetzelfde kan als commando:
     `docker compose exec platform python -m app.cli media-import /data/import/Films --naar /Films --eigenaar <e-mail> [--verplaats]`.
   - **Kopiëren** (standaard): elk bestand gaat de BlobStore in, de bron blijft staan. Er is
     tijdelijk twee keer de ruimte nodig.
   - **Verplaatsen:** de app berekent de sha op de plek en doet daarna `BlobStore.adopt` (een
     `rename`). Dat kost geen extra ruimte. Het werkt alleen binnen één bestandssysteem. Geeft
     `rename` `EXDEV` (een ander ZFS-dataset), dan kopieert de app dat bestand en haalt de bron
     daarna weg. Er is dan nooit meer extra ruimte nodig dan het grootste bestand.
   - **Een bestand dat nog geschreven wordt, komt er niet in.** Per bestand, op de fd die de
     import zelf opende:
     1. `fstat` vóór het tellen: een gewoon bestand, en `mtime` minstens 60 s oud. Bij verplaatsen
        ook `st_nlink == 1` (een harde link zou de blob delen met een pad buiten de app);
     2. de sha tellen uit die fd;
     3. opnieuw `fstat`: grootte, `mtime_ns` en `ctime_ns` moeten gelijk zijn;
     4. pas dan `rename`, en daarna controleren dat het nieuwe pad hetzelfde `st_ino` heeft.

     Klopt één stap niet, dan komt het bestand in de lijst "overgeslagen" met de reden "in
     gebruik". Een volgende import pakt het op.
   - Ontdubbelen: staat de inhoud er al, dan komt er alleen een node bij.
   - Quotum: telt mee voor de eigenaar, net als een upload. Past het niet, dan stopt de import met
     `no_room` en een lijst van wat wel binnenkwam.
   - Hervatbaar en idempotent: een map die al bestaat, wordt hergebruikt. Dezelfde naam met
     dezelfde sha wordt overgeslagen. Een andere sha wordt een nieuwe versie, zoals bij uploaden.
   - Veilig: de import volgt nooit een symlink, ook niet bij de bronmap zelf:
     - de bronmap gaat open met `O_DIRECTORY | O_NOFOLLOW`. Elke stap daaronder gaat met
       `openat(dirfd, naam, O_NOFOLLOW)`, ten opzichte van de map erboven. Een pad als string
       wordt nooit meer geopend;
     - `realpath(bronmap)` moet onder `realpath('/data/import')` liggen en hetzelfde `st_dev`
       hebben. Anders `media_import_path`. Zo wijst een symlink die op de host is gezet (naar
       `/data`, met de database en de geheimen) nergens heen;
     - speciale bestanden (fifo, socket, apparaat) worden overgeslagen;
     - een bestand dat niet leesbaar is, of bij verplaatsen niet weg te halen, komt in de lijst
       "overgeslagen" met de reden.
   - Vooraf kijken: de import loopt de bronmap eerst na zonder te tellen. Kan de app een deel niet
     lezen, of bij verplaatsen niet verplaatsen, dan zegt `/beheer` hoeveel bestanden het zijn en
     stelt hij "kopiëren" voor.
   - Rechten op de LXC: de LXC is unprivileged. Wat de Proxmox-host op `/tank` zette, ziet de LXC
     als `nobody:nogroup`, en root in de LXC kan dat niet chownen. Het commando hoort dus op de
     Proxmox-host: `chown -R 110001:110001 /tank/platform/import` (uid 10001 plus de 100000 van
     de standaard id-map). Dat verzwakt de afscherming niet. Alleen lezen is genoeg voor
     kopiëren. De README zegt het, met een controle in de LXC (`ls -ln`).
   - De import ontleedt niets: hij leest bytes en telt de sha, net als een upload.
   - Hij draait als job `media.import` in een eigen rij (Keuze 14). De voortgang komt via SSE.
3. **Een bestaande map rechtstreeks gebruiken: fase 7.** Naast elkaar:

   | | In de boom (upload of import) | Map rechtstreeks (alleen lezen) |
   |---|---|---|
   | Opslag | in `/data/blobs`; met verplaatsen geen extra ruimte | blijft waar hij staat |
   | Ontdubbelen, quotum, versies, prullenbak | ja | nee, of elk met een uitzondering |
   | Toegang | per node, met delen, links en gasten | per bibliotheek |
   | Veranderen buiten de app | kan niet | kan wel; dan is er wel inotify of een scan nodig |
   | Compose | ongewijzigd | een extra koppeling per map |

   Standaard: **in de boom**. De map rechtstreeks staat al in fase 7. Zie Vraag 1.
4. **Wat een bibliotheek is.** Een map in de boom met een soort erop. Meer niet.
   - Tabel `media_library`, met `kind` = `movies`, `shows`, `music` of `videos` (eigen video's:
     geen online metadata, gesorteerd op datum en map).
   - Alleen de eigenaar van de map maakt er een bibliotheek van. De map moet een `dir` zijn en
     niet in de prullenbak liggen.
   - Geen nesting: een bibliotheek boven of onder een andere geeft 409 `media_library_nested`. Zo
     hoort elk bestand bij hooguit één bibliotheek.
   - **Wie hem ziet:** de eigenaar, en iedereen die de map zelf mag lezen via een deling
     (`access.resolve`, B18). Een gast ziet dus een gedeelde bibliotheek. Een deling van alleen
     een submap toont de bibliotheek niet; de bestanden spelen dan wel vanuit Bestanden. Wie mag
     schrijven, mag ook metadata verbeteren (Keuze 43).
   - Weghalen haalt alleen de mediarijen weg. De bestanden blijven.
   - De map in de prullenbak: de bibliotheek is verborgen. Definitief weg: CASCADE.
   - Hooguit 20 bibliotheken per gebruiker.
   - Per bibliotheek: naam, taal van de metadata (`nl-NL`) en "online opzoeken" aan of uit
     (Keuze 39).
   - Een video buiten een bibliotheek speelt ook, met dezelfde beslissing. Alleen het
     achtergrondwerk (sprites, alvast ondertitels) is voor bibliotheken.
5. **De scanner: gebeurtenissen plus een ronde bij de start en elke 6 uur.**
   - `files.notify` en `files.notify_many` zien elke verandering in de boom. Ze krijgen één
     haakregel: `media.hooks.touched(nodes, type)`. Voor het scannen doet die niets zolang er
     geen bibliotheek bestaat (een vlag in het geheugen). Anders zet hij de node-id's in een set in
     het geheugen (hooguit 50.000; daarboven: een volledige ronde voor die eigenaar). Geen I/O.
   - Los van bibliotheken roept dezelfde haak bij een nieuwe versie, de prullenbak en weghalen
     altijd `media.sessions.close_node(node_id)` aan. Ook dat is alleen geheugen. Een sessie op
     een bestand dat net vervangen is, stopt dus meteen (Keuze 21).
   - Een eigen lus van 30 s (niet de housekeeping: die tikt per 60 s en alles na elkaar) zet voor
     de geraakte nodes een job `media.scan` klaar, met key `media.scan:<library_id>`.
   - Bij de start en elke 6 uur: `media.reconcile` loopt elke bibliotheek na. Dat vangt ook wat
     een crash tussen de commit en de lus verloor. Een eigen tabel voor de set is dus niet nodig.
   - **De scan** vergelijkt de levende bestanden onder de map (een recursieve CTE, zoals
     `photos.live_dirs`) met `media_item`:
     - nieuw: classificeren, ontleden, groeperen, rij maken, analyse klaarzetten (Keuze 14);
     - andere naam, map of inhoud: opnieuw ontleden, behalve de velden in `locked`;
     - in de prullenbak (zelf of een map erboven): `present = false`. De rij blijft, met voortgang
       en koppeling. Terugzetten zet `present` weer aan;
     - naar buiten de bibliotheek verplaatst: rij weg. `playback_state` blijft (die hangt aan de
       node).
   - In stukken van 500 (`jobs.chain`), zodat geen taak zijn tijdslimiet raakt.
   - Een knop "Opnieuw scannen" per bibliotheek doet hetzelfde, en ontleedt ook de namen opnieuw
     (na een nieuwe parserversie, `NAMES_VERSION`).
   - **Classificeren op extensie:**
     - video: `mkv mp4 m4v mov webm avi ts m2ts mts mpg mpeg ogv wmv`;
     - audio: `mp3 flac m4a aac opus ogg oga wav wma`;
     - ondertitels: `srt vtt ass ssa`. Losse beeldondertitels (`sup`, `sub` met `idx`) koppelen
       we in fase 5 niet: decide kan ze niet spelen, en de speler toont nooit een spoor dat niet
       kan. Ze staan in `names-open.tsv`. Ingebedde PGS en VobSub werken wel (Keuze 29);
     - `nfo`;
     - plaatjes met een bekende naam (Keuze 9).
   - Extra's (`Extras`, `Featurettes`, `Trailers`, `Behind The Scenes`, `Deleted Scenes`, en
     `-trailer`, `-featurette`, `-deleted` in de naam) hangen aan hun film en tellen niet mee in
     de lijsten. Een sample (`sample` in de naam en korter dan 5 minuten) is verborgen.
   - **Externe ondertitels** horen bij een video als de naam ermee begint, met de vlaggen van
     Jellyfin: `Film (2010).nl.srt`, `.nl.forced.srt`, `.en.sdh.srt`, `.default`, `.hi`, `.cc`.
     Taal als ISO 639-1 of -2, of een naam (`dutch`, `nederlands`, `english`).
6. **Identiteit: de inhoud per sha, het item per node.**
   - `media_probe` hangt aan de sha (CASCADE). Twee kopieën delen hun analyse, keyframes,
     ondertitels en sprites.
   - `media_item` en `playback_state` hangen aan de node (CASCADE). Hernoemen of verplaatsen binnen
     de bibliotheek houdt het item, de koppeling en de voortgang.
   - Een nieuwe versie (andere sha) houdt het item. De analyse gebeurt opnieuw. De positie gaat
     terug naar 0 als ook de duur meer dan 5 % verschilt.
   - Opgeruimde inhoud: `previews.discard` roept `media.hooks.on_discard(sha)` aan, net als
     `convert.on_discard` van office. Die sluit eerst elke sessie op die sha (reden `replaced`),
     en gooit daarna de ondertitels, sprites en audio-cache van die sha weg. Zo verdwijnt nooit
     een bestand onder een lopende sessie.
7. **Een eigen bestandsnaamparser** (`app/media/names.py`, pure functies, geen I/O).
   - `parse(parts: list[str], kind) -> Parsed`. `parts` is het pad vanaf de map van de
     bibliotheek. `Parsed` heeft: `title`, `year`, `season`, `episode`, `episode_end`,
     `absolute`, `date`, `part`, `edition`, `extra`, `ids` (`imdb`, `tmdb`, `tvdb`), de map van
     de serie, en een zekerheid tussen 0 en 1.
   - Werkwijze: tokens maken (punten, underscores en streepjes worden spaties, behalve in een
     datum of id), ruis wegknippen vanaf het eerste ruistoken (resolutie, bron, codec, audio,
     groep, `PROPER`, `REPACK`), het jaar zoeken (het laatste jaar tussen 1900 en volgend jaar dat
     niet de hele titel is), en de map erboven gebruiken als de naam weinig zegt.
   - **Vóór het zoeken naar een aflevering** gaan tokens eruit die op een aflevering lijken:
     resolutie (`\d{3,4}x\d{3,4}`, `720p`, `2160p`), codec (`x264`, `x265`, `H.264`, `H.265`,
     `HEVC`), audio (`5.1`, `7.1`, `2.0`, `DDP5.1`) en bron (`WEB-DL`, `BluRay`). `NxNN` telt alleen
     met N ≤ 99 en zonder cijfer ervoor of erna. Zo wordt `Film.1920x1080.mkv` geen seizoen 1920,
     en `Show.x264.mkv` geen aflevering.
   - Wat hij kent:
     - Jellyfin: `Naam (2010)/Naam (2010) [imdbid-tt0133093] - 1080p.mkv`, `{edition-…}`;
     - Plex: `{tmdb-603}`, `{imdb-tt…}`, `{edition-Director's Cut}`;
     - Kodi en scene: `Naam.2010.1080p.BluRay.x264-GROEP.mkv`;
     - delen: `cd1`, `part2`, `pt.2`, `disc 1`;
     - series: `S01E02`, `s1e2`, `1x02`, `S01E02-E03`, `S01E02E03`, `S01.E02`, `Season 1
       Episode 2`, `Seizoen 1 Aflevering 2`, `102` alleen in een seizoensmap;
     - `Season 00` of een map `Specials` is seizoen 0;
     - datums voor dagelijkse shows: `Show 2024-03-01`, `2024.03.01`;
     - absolute nummering (`[Groep] Titel - 012 [1080p]`): `season = None`, `absolute = 12`,
       sorteren op dat nummer. Nooit seizoen 1 erbij verzonnen (Keuze 8);
     - lastige titels: `2001 A Space Odyssey (1968)`, `1917 (2019)`, `Blade Runner 2049 (2017)`,
       `Mr. Robot`, Unicode en punten in de titel.
   - Muziek: tags gaan voor. Het pad is de terugval: `Artiest/Album (Jaar)/01 - Titel.flac`, met
     submappen `Disc 2` en `CD2`.
   - **De testset:** `api/tests/media/names.tsv`, minstens 500 regels, zelf geschreven. Groepen
     (zoals B ze opsomde): films, scene-tokens, ids, edities en delen, extra's en samples,
     SxxEyy-varianten, meerdere afleveringen, specials, datums, absolute nummering, Nederlands,
     jaartallen in de titel, Unicode, leestekens, muziek en rommel (`video.mkv`, `1.mp4`,
     `DSC_0001.MOV`). Plus een groep "lijkt op een aflevering": `1920x1080`, `x264`, `5.1`,
     `7.1`, `H.264`, `2x2` in een titel, `100x` (ook in names-open als we ze niet aankunnen).
   - De poort: **100 %**. Een vorm die we bewust niet aankunnen, staat in `names-open.tsv` met een
     reden. Elke gevonden fout wordt eerst een nieuwe regel.
   - Eigen fuzz (geen hypothesis): 10.000 namen uit een vaste seed, uit dezelfde bouwstenen. Nooit
     een uitzondering, het jaar altijd binnen bereik, nooit een lege titel als er letters zijn.
     En een eigenschap die over de uitkomst gaat: een naam die zonder afleveringstoken is gebouwd
     (wel met resolutie, codec, audio en jaar), geeft nooit een aflevering. Ook gaat een titel
     die met de bouwstenen is gemaakt, ongeschonden terug. 10.000 namen in minder dan 0,5 s.
   - `python -m app.cli media-parse <pad>…` toont wat de parser ziet. Op `/beheer` staat per
     bibliotheek een lijst "Weinig zeker" (zekerheid < 0,5).
8. **Groeperen.**
   - Afleveringen hangen aan een serie (`media_show`), via de map van de serie, anders via de
     genormaliseerde titel.
   - Een aflevering zonder seizoen (absolute nummering) houdt `season = NULL` en heeft
     `absolute`. De serie toont die afleveringen in één groep "Afleveringen", op volgorde van het
     nummer. Er komt nooit een seizoen 1 bij dat er niet stond. `names.tsv` en de groeptest
     controleren allebei die regel (`[Groep] Titel - 012`: `season` leeg, `absolute` 12).
   - `cd1` en `cd2` van dezelfde film zijn delen. De speler speelt ze na elkaar.
   - Twee bestanden van dezelfde film (een andere editie of resolutie) zijn twee items. Geen
     "versies" van één titel: dat maakt UI en beslissing zwaarder (Keuze 50).
9. **Lokale metadata eerst.**
   - Titel, jaar, seizoen en aflevering uit de naam (Keuze 7).
   - Een NFO in de stijl van Kodi gaat voor de naam: `movie.nfo`, `<naam>.nfo`, `tvshow.nfo`,
     `season.nfo`, of een NFO per aflevering. Velden: `title`, `originaltitle`, `sorttitle`,
     `year`, `plot`, `genre`, `uniqueid` (tmdb, imdb, tvdb), `season`, `episode`. Een NFO met
     alleen een link naar IMDb of TMDB geeft het id.
     - Gelezen in de worker (opdracht `nfo`), zoals alle inhoud van gebruikersbestanden.
     - Hooguit 1 MB. Elke `<!DOCTYPE` of `<!ENTITY` wordt geweigerd vóór het parsen. Dan
       `xml.etree.ElementTree`: geen entiteiten, geen XXE.
   - Plaatjes naast het bestand, met de namen van Jellyfin en Kodi: `poster`, `folder`, `cover`,
     `fanart`, `backdrop`, `<naam>-poster`, `<naam>-fanart`, `season01-poster`, `banner`, `logo`,
     als `.jpg`, `.png` of `.webp`.
     - `media_item.art` verwijst naar die afbeelding als node (`{"poster": {"node": "<id>"}}`).
     - Tonen gaat via de bestaande previews van die afbeelding. Er wordt niets gekopieerd.
   - Geen plaatje: het previewbeeld dat fase 3 al van de film maakt.
   - Muziek: tags uit de probe, en een ingebedde hoes (`attached_pic`) via de worker (`frame`).
   - Online opzoeken is een extra stap, per bibliotheek aan of uit (Keuze 39). Zonder internet
     werkt alles.
10. **Muziektags** komen uit de probe (format en stream):
    - `title`, `artist`, `album`, `album_artist`, `track` (n/totaal), `disc`, `date`/`year`,
      `genre`;
    - `compilation` (`cpil`, `TCMP`);
    - de MusicBrainz-id's van Picard (`MUSICBRAINZ_ALBUMID`, "MusicBrainz Album Id").

    Meerdere artiesten alleen als de tag zelf meerdere waarden heeft. We splitsen nooit op
    `feat.` of `&`.

### Isolatie en de worker

11. **ffmpeg draait alleen in de mediaworker: een eigen container zonder netwerk en zonder data.**
    - **Waarom.** ffmpeg leest bestanden van gebruikers, ook van publieke links. ffmpeg is groot,
      in C geschreven, en heeft elk jaar CVE's. Nu draait ffmpeg in de app-container
      (`render.py`). Een lek daar geeft `/data`: alle bestanden, de database en de geheimen. Met de
      worker krijgt een lek alleen de bestanden die op dat moment openstaan. Er is geen netwerk om
      iets weg te sturen.
    - Hetzelfde image `platform:local`, een ander commando:
      `python -I -m app.media.worker --socket /run/media/worker.sock`. Eén keer bouwen, dus
      dezelfde ffmpeg, of de worker nu een container is of een kindproces (Keuze 13).
      *Noot bij de bouw van M0:* zo vindt Python `app` niet (het project is niet geïnstalleerd,
      en `-I` laat de werkmap en `PYTHONPATH` weg). De lokale worker start als
      `python -I -c <BOOT> <api-map> …`; M1 kiest hetzelfde of installeert het project in het
      image ([m0.md](m0.md), afwijking 3).
    - **Een eigen gebruiker.** De app (uid 10001) is eigenaar van elke blob (modus 0600, uit
      `mkstemp`). Draait ffmpeg ook als 10001, dan kan hij een fd die alleen lezen is via
      `/proc/self/fd/3` opnieuw openen om te schrijven, en zo een blob veranderen (V1). De blobs
      zijn ontdubbeld: één veranderde blob raakt elke node met die sha, en de back-up. Daarom:
      - het Dockerfile maakt een tweede gebruiker: `useradd --system --uid 10002 --no-create-home
        media`. De worker draait met `user: media` in compose;
      - de blobs blijven 0600 van 10001. Een doorgegeven fd leest gewoon; opnieuw openen via
        `/proc` faalt (V1);
      - `chmod 0711 /usr/bin/ffmpeg /usr/bin/ffprobe` in het image (eigenaar root). Een proces
        uit een programma dat het niet mag lezen, is niet dumpbaar. Een andere job kan dan niet in
        zijn `/proc/<pid>/fd` (V2);
      - de worker zet bij de start `prctl(PR_SET_DUMPABLE, 0)` (via `ctypes`). Dan kan ook een
        gekraakte ffmpeg niet bij de fd's die de worker van andere jobs vasthoudt (V2);
      - de app maakt elke pipe, ook die voor ondertitels. Pipes zijn dan van 10001;
      - `VmRSS` uit `/proc/<pid>/status` blijft leesbaar voor de worker (V2). Het meten van het
        geheugen werkt dus nog.
    - De afscherming (compose in M1):
      - `user: media`;
      - `network_mode: none`;
      - `read_only: true`;
      - `tmpfs` op `/tmp` (64 MB) en op `/data` (1 MB, leeg). Die laatste overschaduwt `VOLUME
        /data` van het image, zodat er geen anoniem volume ontstaat;
      - `cap_drop: [ALL]`, `no-new-privileges`, `pids_limit: 256`, `core: 0`, `nofile: 1024`;
      - geen datavolume. Alleen het benoemde volume `media-run` op `/run/media`, voor de socket.
        Het Dockerfile maakt de map met `install -d -o media -g platform -m 2750 /run/media`. Een
        leeg benoemd volume neemt dat over. De worker maakt de socket met modus 0660; die krijgt
        de groep `platform` van de map (setgid). De app verbindt, maar kan in de map niets maken
        [gemeten V2; niet getest in de LXC].
    - Grenzen alleen als de cgroups ze aankunnen (dezelfde `limits_supported` als office):
      `mem_limit: 3g`, `memswap_limit: 3g`, `cpus: 3.0`. Zonder die grenzen bewaakt de worker
      zelf het geheugen per job (Keuze 12).
    - Ook de previews van video's en `render_meta` gaan via de worker. Een test bewaakt dat buiten
      `app/media/worker/` niets ffmpeg of ffprobe start (`test_media_no_ffmpeg_outside.py`).
    - **Wat overblijft:** de jobs van één moment delen de worker en dezelfde uid (10002). Door de
      modus 0711 en `PR_SET_DUMPABLE` kan een gekraakte ffmpeg niet in `/proc/<pid>/fd` van een
      andere job of van de worker (V2). Hij kan wel signalen sturen naar een andere ffmpeg van
      dezelfde uid, en zo een andere stream stoppen. Dat is hinder, geen lek. Schrijven in een
      blob kan niet meer: de blob is van 10001 (V1). Per job een eigen uid of container is
      zwaarder dan het waard is. Dit staat bij de risico's.
    - De zelftest bij de start (`selftest`, in S3 en S10) bewijst dit op de LXC zelf:
      - `os.open('/proc/self/fd/3', O_WRONLY)` op een blob faalt in de worker;
      - een kind van de worker kan `/proc/<worker>/fd` niet lezen;
      - `id -u` in de worker is 10002.

      Faalt de zelftest, dan start de worker niet, en `/beheer` toont `media_worker_down` met de
      reden.
    - **Afgewezen: een worker over http op een intern netwerk** (ontwerp B). Fase 4 (M7) liet zien
      dat een container op een `internal: true`-netwerk de host bereikt via de gateway, en de app
      luistert intern op 8081. Ook een alleen-lezen koppeling van alle blobs valt weg: een lek
      leest dan elke film. Die koppeling blijft alleen de terugval als S3 op de LXC faalt (§3.7).
      (spike) S3 slaagde in CI met het echte image: 17 van 17 controles, met deze afscherming. De
      terugval is niet nodig; op de LXC bewijst de zelftest het.
12. **Het protocol: getypte opdrachten, file descriptors over een Unix-socket.**
    - Eén verbinding per opdracht. De eerste boodschap draagt de fd's als `SCM_RIGHTS`.
    - **(spike) Een fd uit `SCM_RIGHTS` deelt zijn positie** (de file offset) met de app en met
      elk kind. In S3 stond hij na ffprobe op 65536, en ffmpeg las `fd:` vanaf daar (rc 183).
      Daarom:
      - de app opent de blob per opdracht opnieuw, en geeft een fd nooit aan twee opdrachten;
      - de worker ontvangt met `recvmsg` en `MSG_CMSG_CLOEXEC` (`socket.recv_fds` zet geen
        CLOEXEC);
      - vóór elke exec `os.lseek(fd, 0, SEEK_SET)` op elke invoer-fd, en nooit twee kinderen
        tegelijk op dezelfde fd;
      - `dup2` van de bron naar fd 3 (en 4) alleen in het kind, nooit in de asyncio-ouder: daar
        kan fd 3 de epoll van de event loop zijn (S6).
    - Boodschappen: 4 bytes lengte (big endian), dan JSON, hooguit 64 KB. Wat groter kan worden,
      past daar toch in:
      - de keyframe-index komt al in de opgeslagen vorm terug (zlib, varint-delta's, base64). Is
        die na compressie groter dan 48 KB, dan dunt de worker hem uit tot hooguit één keyframe per
        seconde (genoeg voor de regel van 6 s; Keuze 23). De staat is dan toch `ready`;
      - de probe houdt hooguit 64 streams, 64 hoofdstukken en geen bijlagen (alleen hun aantal).
        Wat er afvalt, staat in `truncated`;
      - een test stuurt een index van 50.000 keyframes (een film van 3 uur met een GOP van 0,2 s)
        en eist dat het antwoord past.
    - Vraag: `{"v": 1, "op": "<naam>", "args": {…}}`, met fd's [invoer, uitvoer…].
    - Antwoorden (meerdere per verbinding):
      - `{"ev": "started"}`;
      - `{"ev": "progress", "t_ms": …, "speed": …}`;
      - `{"ev": "result", "data": {…}}`;
      - `{"ev": "done"}`;
      - `{"ev": "error", "code": "…", "detail": "…"}`. `detail` is hooguit 2.000 tekens van stderr.
        Er staan geen paden in: ffmpeg kent alleen `fd:`.
    - **De verbinding sluiten is stoppen:** de worker doet `killpg` (SIGTERM, na 2 s SIGKILL).
    - De worker bouwt zelf de argv uit gevalideerde argumenten (pydantic, in de worker). Codecs,
      formaten en filters komen alleen uit vaste lijsten. Er gaat nooit een vrije string naar
      ffmpeg. De argv is getest als golden file per opdracht.
    - Elke invoer krijgt:
      - `-protocol_whitelist fd,pipe`. Geen `file:`, `http:` of `concat:`, dus geen trucs met een
        playlist in een AVI;
      - `-f <demuxer>` uit de bestaande lijst (`render.container`) plus `mp3`, `flac`, `aac`, en
        `-format_whitelist` met dezelfde lijst;
      - `-max_pixels 35389440` (8192×4320) op de invoer, en globaal `-max_alloc 536870912`
        (512 MB per blok). 4K decodeert daarmee gewoon (V6). Een bron die groter zegt te zijn,
        weigert de probe al: 415 `media_unsupported`.
    - Eén uitzondering op "alleen fd's als invoer": `synth` (alleen voor de belastingtest, §4.6).
      Die heeft geen invoer-fd, maar een bron uit een vaste lijst van `lavfi`-bronnen met vaste
      parameters (`testsrc2` en `cellauto`, elk met een andere seed). Er gaat geen vrije string
      naar `lavfi`. De worker zet `lavfi` dan alleen voor die opdracht in de whitelist.
      (spike) `lavfi` is in ffmpeg 7.1 een invoerapparaat: het staat onder `ffmpeg -devices`, niet
      onder `-demuxers` (S1). `caps` en de zelftest zoeken het daar. Alleen `synth` en `bench`
      gebruiken het, zonder invoer-fd; in de witte lijst van een invoer-fd komt het nooit.
    - **De opdrachten:**

      | op | fd's | doet |
      |---|---|---|
      | `caps` | — | versie van ffmpeg, encoders, filters (`zscale`, `tonemap`, `bwdif`, `overlay`), demuxers (`lavfi` uit `-devices`; spike), hwaccels, `/dev/dri`, `PROTOCOL` |
      | `bench` | — | korte `testsrc2` met ruis per trede, zonder invoer; geeft cores voor 1,0× (Keuze 26) |
      | `probe` | in | ffprobe JSON (format, streams, chapters), bij HDR ook de side data van het eerste frame; plus de codec-strings (Keuze 16) |
      | `keyframes` | in | tijden van keyframes: eigen lezers voor Matroska-Cues en MP4-`stss`; terugval: ffprobe op pakketten |
      | `audio_info` | in | gapless: rate, samples, delay, padding (Keuze 18) |
      | `nfo` | in | de velden van Keuze 9 |
      | `frame` | in, uit | één beeld (previews van video's, ingebedde hoes) |
      | `meta` | in | wat `render_meta` nu met ffprobe leest |
      | `subs` | in, uit×N | tekstsporen of een los bestand naar WebVTT, met de eigen omzetter (Keuze 29) |
      | `sprites` | in, uit | scrub-sprites als JPEG-tegels (Keuze 30) |
      | `stream` | in, uit | remux of transcode naar fMP4 (Keuze 22–27) |
      | `remux_audio` | in, uit | één nummer naar fMP4 voor gapless (Keuze 38) |
      | `synth` | uit | een bron voor de belastingtest uit de vaste lijst, H.264/AAC in mkv (§4.6) |

    - De worker start ffmpeg met:
      - een eigen procesgroep en een kale omgeving (`PATH`, `LC_ALL=C`, `HOME=/tmp`);
      - grenzen via `resource` vóór `exec`: `RLIMIT_CORE` 0, `RLIMIT_NOFILE` 256, en
        `RLIMIT_FSIZE` 0 (spike, S10: ffmpeg verdraagt het; een bestand schrijven geeft SIGXFSZ
        en 0 bytes). `RLIMIT_AS` kan niet: ffmpeg reserveert veel virtueel geheugen voor
        threads (N8). **`RLIMIT_DATA` kan wel** (V6): `job_memory_mb` (1.200) voor live en
        sprites, 512 MB voor `probe`, `keyframes`, `frame`, `meta`, `nfo` en `audio_info`;
      - `-threads 4` als bovengrens per proces (spike, S10: 0,744 cores tegen 0,743 zonder, dus
        geen kosten);
      - `nice -n 5` live, `nice -n 15` en `ionice -c 3` op de achtergrond. `ionice` helpt alleen
        met de BFQ-scheduler; op ZFS weinig [doc].
      - *Noot bij de bouw van M0:* `RLIMIT_FSIZE` 0 voor elk kind behalve `synth` (dat schrijft
        een bestand); `RLIMIT_DATA` 1.200 MB voor `stream`, `remux_audio`, `sprites`, `bench` en
        `synth`, 512 MB voor de rest ([m0.md](m0.md), afwijking 10).
    - **Geheugen per job:** `RLIMIT_DATA` is de harde grens, ook zonder cgroups. Een bron die
      tussen twee metingen GB's vraagt, krijgt een fout van `malloc` en stopt. De worker leest
      daarnaast elke 2 s `VmRSS` voor `/beheer` (gemeten: 140–568 MB per proces). De grens van de
      container is het vangnet.
    - **Plaatsen per soort werk.** De worker telt verbindingen per soort, en kinderen in totaal:

      | soort | opdrachten | hooguit |
      |---|---|---|
      | transcode | `stream` met transcode | `max_transcodes` (de grens van de toelating, Keuze 26) |
      | remux en audio | `stream` met remux, `remux_audio` | 6 |
      | achtergrond | `subs`, `sprites`, `bench`, `keyframes` met de terugval, `synth` | 1 |
      | kort | `probe`, `keyframes`, `audio_info`, `nfo`, `frame`, `meta` | 2 |
      | controle | `caps` (uit de cache; start geen proces) | altijd; telt niet mee |

      - Daarbij een grens op het totaal aantal ffmpeg- en ffprobe-processen:
        `PLATFORM_MEDIA_MAX_PROCS` (standaard 10, in het harnas en de belastingtest 3). Een
        proces dat nog stopt, telt mee tot `waitpid` klaar is.
      - Vol: `{"ev": "error", "code": "busy", "class": "<soort>"}`. De app maakt daar 503
        `media_busy` van met reden `worker` en `Retry-After: 2`. Op de achtergrond wordt het
        `jobs.Defer(30)`.
      - `caps` voor de gezondheid gaat over een eigen verbinding die niet meetelt. De worker
        antwoordt uit de cache van de start; er start geen proces. Zo zegt de gezondheid nooit
        `media_worker_down` omdat de worker druk is.
      - De Grant van de toelating (Keuze 26) reserveert de plaats in de worker al. Een play die de
        toelating goedkeurt, krijgt dus geen `busy` van de worker.
    - **Eén producent per sessie.** Bij zoeken stopt de oude producent (SIGTERM, na 2 s SIGKILL).
      De nieuwe start pas na `waitpid` van de oude. Zoekverzoeken binnen 300 ms worden samengevoegd:
      alleen het laatste start een producent. Zo staan er per sessie hooguit 2 ffmpeg tegelijk (één
      die stopt, één die start), en telt de stoppende mee in de plaatsen. Een test doet 10 keer
      zoeken in 1 s.
    - Uitvoer altijd via pipes die de app maakt (D7), ook voor ondertitels. De app leest ze met
      asyncio. (spike) Stopt de app een producent, dan sluit hij de pipe zelf: een gepauzeerde
      asyncio-transport krijgt na een kill geen EOF (S6).
13. **Zonder sandbox, en als de worker weg is.**
    - `PLATFORM_MEDIA_WORKER` leeg: de app start zelf een lokale worker. Dat is een kindproces
      `python -I -m app.media.worker --socket <data_dir>/media/run/worker.sock` (gebouwd als
      `python -I -c <BOOT> <api-map> --socket …`, [m0.md](m0.md) afwijking 3), met een kale
      omgeving, een eigen procesgroep en herstart met backoff. Hetzelfde protocol en dezelfde
      code; alleen de afscherming ontbreekt. De lokale worker draait als de app zelf (10001); de
      zelftest meldt dat als `sandbox: off`, niet als fout.
      - Voor ontwikkelen, tests en de API-jobs in CI.
      - Op de LXC alleen met `MEDIA_SANDBOX=uit` (de nooduitgang als de container daar niet wil).
        `/beheer` toont dan de melding `media_sandbox_off`.
    - `PLATFORM_MEDIA_WORKER` gezet maar niet bereikbaar: **nooit** stil terugvallen op lokaal. Dat
      zou de afscherming stilletjes uitzetten.
      - Direct play werkt door: geen ffmpeg nodig. Zonder probe valt het terug op fase 3
        (`<video src>`; de browser probeert het).
      - Remux en transcode geven 503 `media_worker_down`.
      - Previews van video's: de taak zet zichzelf opnieuw klaar (`jobs.Defer`, Keuze 14), zonder
        een poging te tellen.
      - `/beheer` toont `media_worker_down`.
14. **Achtergrondwerk in eigen rijen.**
    - `jobs.register` krijgt `lane` en `timeout`. Er draait een Runner per rij:
      - `default`: zoals nu (2 workers, 300 s). Fase 3 en 4 merken niets;
      - `media_scan`: 1 worker, voor `media.scan` en `media.reconcile`. Kort werk, dus een nieuwe
        upload verschijnt ook als er honderden analyses wachten;
      - `media`: 1 worker (`PLATFORM_MEDIA_JOB_WORKERS`), voor `media.analyse`;
      - `media_net`: 1 worker, voor `media.lookup` en `media.art`. Wachten op een provider houdt
        de analyse niet op;
      - `import`: 1 worker, zodat een import van uren de rest niet ophoudt.
    - `claim()` pakt alleen soorten uit zijn eigen rij. Binnen een rij blijft de volgorde van nu
      (`run_after`, dan `created_at`). Een kolom `priority` is dus niet nodig, en `job` krijgt
      geen nieuwe kolom.
    - **De housekeeping draait één keer.** Nu start elke Runner zijn eigen lus voor de
      housekeeping (`Runner.start`). Met vijf Runners zou `trash.retention` vijf keer tegelijk
      lopen. Daarom `Runner(settings, lane=…, housekeeping=False)` voor elke rij behalve `default`.
      `test_jobs_lanes.py` telt per periode één run van elke housekeeping, met alle Runners aan.
    - Een nieuwe uitzondering `jobs.Defer(seconds)`: terug in de rij met `run_after`, en de poging
      telt niet. Voor: de worker is druk of weg, een provider geeft 429, er is geen netwerk.
    - Soorten:

      | soort | rij | timeout | wanneer |
      |---|---|---|---|
      | `media.scan` | media_scan | 300 s | na een wijziging, per 500 nodes (Keuze 5) |
      | `media.reconcile` | media_scan | 1.800 s | bij de start en elke 6 uur; zet alleen scans klaar |
      | `media.analyse` | media | 3.600 s | per nieuwe sha in een bibliotheek; stappen hieronder |
      | `media.lookup` | media_net | 300 s | als online opzoeken aan staat (Keuze 39) |
      | `media.art` | media_net | 120 s | een plaatje van een provider ophalen en omzetten |
      | `media.import` | import | 86.400 s | op verzoek (Keuze 2) |

    - `media.analyse` heeft stappen, elk met een eigen staat:
      `probe → keyframes → subs → sprites (alleen video) → audio_info (alleen muziek)`. Een
      mislukte stap houdt de rest niet tegen.
    - Lopen er 2 of meer live transcodes, dan wacht de analyse: `Defer(60)`.
    - Een film van 2 uur kost ±2–3 minuten, vooral schijf. 500 films: 8–16 uur op één core
      [geschat uit feasibility §5.1]. Na de update is er geen inhaalslag: er zijn nog geen
      bibliotheken.

### Analyse

15. **De probe, per blob, in de worker.**
    - Tabel `media_probe`, sleutel `blob_sha` (CASCADE). `version` (`PROBE_VERSION = 1`) zorgt dat
      een nieuwe versie opnieuw kan proben.
    - `ffprobe -show_format -show_streams -show_chapters` via `fd:` kost 63–129 ms [gemeten]. Bij
      HDR nog één frame met `-read_intervals %+#1 -show_frames` voor de mastering-gegevens, MaxCLL
      en Dolby Vision (96 ms extra, N21).
    - Wanneer: bij het scannen (achtergrond), en anders bij het eerste afspelen. Dan wacht het
      verzoek hooguit 5 s, voor de probe en de keyframes samen (Keuze 17). Daarna 409
      `media_probe_pending` met `Retry-After: 2`.
    - Grenzen: hooguit 64 streams en 64 hoofdstukken in het antwoord (Keuze 12). Breedte maal
      hoogte boven 8192×4320 geeft 415 `media_unsupported` (reden `too_large`).
    - `streams` opgeschoond: `index`, `type`, `codec`, `codec_string`, `profile`, `level`,
      `width`, `height`, `fps`, `pix_fmt`, `bit_depth`, `color` (primaries, transfer, matrix),
      `hdr`, `interlaced`, `channels`, `layout`, `sample_rate`, `lang`, `title`, `default`,
      `forced`, `hearing_impaired`, `bitrate`.
    - HDR uit de probe: transfer `smpte2084` → `hdr10`; `arib-std-b67` → `hlg`; DOVI-record
      profiel 5 → `dv5` (geen bruikbare basislaag); profiel 7 en 8 → hun basislaag.
16. **Codec-strings maakt de worker, uit de extradata.** RFC 6381-strings zijn wat de browser
    test (`isTypeSupported`, `canPlayType`, `decodingInfo`). ffprobe geeft ze niet, en profiel en
    level alleen zijn niet genoeg voor `hvc1` en `av01`. Een eigen lezer
    (`app/media/codecs.py`, pure functies) maakt ze uit de configuratie van de stream:
    - H.264: `avc1.PPCCLL` uit de `avcC`;
    - HEVC: `hvc1.<profiel>.<compat>.<tier><level>.<constraints>` uit de `hvcC` (in mkv: de
      `CodecPrivate`, dezelfde bytes);
    - **Annex-B** (MPEG-TS en m2ts, soms ook mkv): de extradata beginnen dan met `00 00 01` of
      `00 00 00 01`. De lezer herkent dat en leest profiel, constraints en level uit de eerste SPS
      (H.264), of uit VPS en SPS (HEVC), na het weghalen van de emulatiebytes (`00 00 03`);
    - AV1: `av01.<profiel>.<level><tier>.<bitdiepte>` uit de `av1C`;
    - VP9: `vp09.<profiel>.<level>.<bitdiepte>` uit de `vpcC`, anders uit ffprobe;
    - Dolby Vision: `dvh1.<profiel>.<level>` uit de `dvcC`/`dvvC`;
    - audio: `mp4a.40.2`, `mp4a.40.5`, `opus`, `flac`, `ac-3`, `ec-3`, `mp3`.

    De module draait in de worker (de extradata zijn bytes van een gebruiker). Tests: een tabel van
    bekende extradata naar strings, plus de strings van alle fixtures, ook `a.ts` en `a.m2ts`
    (H.264 en HEVC met Annex-B).
17. **De keyframe-index.**
    - In `media_probe.keyframes`: tijden in ms als varint-delta's, met zlib. Een film van 2 uur met
      een keyframe per 2 s is ±4 KB. Plus `max_gop_ms`. Hooguit 48 KB na compressie; daarboven
      dunt de worker uit tot één keyframe per seconde (Keuze 12).
    - Eerst de eigen lezers in de worker (`worker/containers.py`): Matroska-Cues via de
      `SeekHead` (een kleine EBML-lezer, met een grens op diepte en grootte) en MP4
      `stss`/`stts`/`ctts`/`elst`. Een `moov` achteraan kost twee keer lezen, nooit het hele
      bestand. Hooguit 4 MB lezen.
    - **Bij het afspelen, meteen na de probe.** Is er nog geen index, dan draait `play` de opdracht
      `keyframes` (alleen de eigen lezers) binnen dezelfde 5 s als de probe, voor elke video: ook
      buiten een bibliotheek, achter een publieke link, en voor de mkv's die er al voor de update
      stonden. Het resultaat gaat in `media_probe`. Gemeten kost de probe 63–129 ms; de Cues lezen
      is één of twee keer zoeken in het bestand [niet getest op een harde schijf; S4].
    - De terugval `ffprobe -select_streams v:0 -show_entries packet=pts_time,flags` leest het hele
      bestand: minuten op een harde schijf. Alleen op de achtergrond (rij `media`).
    - `keyframes_state`: `none`, `ready`, `sparse` (Cues met gaten > 20 s: terugval nodig),
      `failed`.
    - Geven de eigen lezers niets (geen Cues, geen `stss`), dan wordt het nu transcode (reden
      `keyframes_missing`) en zet `play` de terugval in de rij. Achter een publieke link mag
      transcode niet: dan 409 `media_probe_pending` met reden `keyframes` en `Retry-After: 30`.
      De speler toont "Dit bestand wordt nog voorbereid".
18. **Gapless-gegevens.** Per nummer het precieze aantal geldige samples, en hoeveel er aan het
    begin en eind af moeten. Eigen lezers in de worker (`audio_info`), begrensd:
    - MP3: de LAME- of Lavc-header in het Xing/Info-frame (`enc_delay`, `enc_padding`), plus de
      vaste vertraging van de decoder (529 samples). mutagen leest de Lavc-tag niet (N20).
      (spike) De 529 telt niet mee: delay = `enc_delay` is exact in Chromium 141, met +529 gaan
      er 529 samples te veel weg (S8). Firefox en Safari zijn niet getest;
    - AAC in MP4: `iTunSMPB` (voorloop, opvulling, lengte); anders de `elst` en de duur;
    - Opus: `pre_skip` uit de `OpusHead`, en de laatste granulepositie (alleen de laatste
      Ogg-pagina). (spike) Opus gaat via `remux_audio` naar FLAC (Keuze 38): dan is de delay 0,
      en `samples` = laatste granule − `pre_skip` (S8);
    - FLAC: `total_samples` uit `STREAMINFO`. Geen voorloop;
    - Vorbis en WAV: geen voorloop.

### Afspelen

19. **De beslissing: een pure functie met vaste regels.**
    - `decide(probe, profile, choice, policy, budget) -> Plan` in `app/media/decide.py`. De web-app,
      later de TV-app en de Android-app sturen allemaal een profiel naar hetzelfde eindpunt.
    - `choice`: audiospoor, ondertitel, kwaliteit (`auto`, `source` of een trede), start, `hdr`
      (`tonemap` of `raw`).
    - `policy`: ingelogd of publieke link, en wat dan mag.
    - `V` = het eerste videospoor (geen `attached_pic`). `A` = het gekozen audiospoor, anders het
      spoor met `default`, anders het eerste. Heeft de bron geen audio (een schermopname, een
      timelapse), dan is er geen `A`: het plan krijgt `audio: "none"`, de argv geen `-map` en geen
      `-c:a` voor audio, en de mime alleen de videocodec. `S` = de gekozen ondertitel, of geen.
    - **Regels, in volgorde:**
      1. **Geen video:** het muziekpad (Keuze 38).
      2. `video_ok` als alles klopt:
         - de codec-string van `V` is `file` (direct) of `mse` (remux) in het profiel, en
           `smooth` als `decodingInfo` het wist;
         - H.264 Hi10P (`avc1.6E00xx`) telt als elke andere string: alleen als hij in het
           profiel staat, en `smooth` als `decodingInfo` het wist. De basisprofielen `webos-5` en
           `ios-safari` sluiten hem uit, en `web` doet dat voor Firefox (op `client.name`). Zo
           speelt anime in 10-bit direct in Chromium, en wordt het elders omgezet;
         - `V.hdr` is `none`, of de client kan precies die HDR en heeft een HDR-scherm;
           Dolby Vision profiel 5 alleen met `dv`;
         - niet interlaced;
         - de bron is hooguit 8192×4320 (anders weigert de probe al, Keuze 15);
         - de kwaliteit is `auto` of `source`, of de gekozen trede is ≥ de bron;
         - de bitrate past onder `max.bitrate_kbps` van het profiel (0 = geen grens).
      3. `audio_ok` als de codec van `A` in het profiel staat.
      4. `subs_ok` als `S` leeg of tekst is (`subrip`, `ass`, `ssa`, `webvtt`, `mov_text`). Een
         beeldondertitel (`hdmv_pgs_subtitle`, `dvd_subtitle`, `dvb_subtitle`) moet ingebrand
         worden, dus transcode. Een geforceerd beeldspoor gaat alleen aan als de gebruiker het
         kiest. Anders blijft de modus staan en geeft het plan `forced_available: "e<i>"`
         (Keuze 29).
      5. **Direct** als alles klopt:
         - container mp4/mov/m4v of webm, en `canPlayType` van mime plus codecs is niet leeg;
         - `video_ok`, `audio_ok` en `subs_ok`;
         - `A` is het eerste audiospoor, of het profiel heeft `track_switch` (alleen Safari heeft
           `audioTracks`).

         Een `moov` achteraan mag: met Range kost dat bijna niets (N15).
      6. **Remux** als alles klopt:
         - `V` speelt via MSE of MMS (of via native HLS);
         - de codec past in fMP4: H.264, HEVC (als `hvc1`), AV1 of VP9;
         - de keyframe-index is er (`play` leest hem meteen na de probe, Keuze 17), en
           `max_gop_ms` ≤ 20.000;
         - bitrate × `max_gop_ms` is hooguit 64 MB, zodat één GOP in één `mdat` en in het
           venster van de cache past (Keuze 31). Anders reden `gop_too_long`;
         - `subs_ok`.

         Audio: kopiëren als `audio_ok`, anders omzetten (Keuze 23).
      7. **Transcode** als de policy het toestaat en de toelating een trede geeft (Keuze 26).
         Anders 503 `media_busy` (ingelogd) of 403 `media_public_transcode` (publieke link).
    - `Plan` = `{mode, reasons[], video: "copy" | {codec, rung, preset}, audio: "copy" | "none" |
      {codec, channels, bitrate}, burn, tonemap, forced_available, cost, places}`. `places` is 1,
      of 2 voor een zwaar plan (Keuze 26).
    - `reasons` zijn vaste codes: `container`, `video_codec`, `audio_codec`, `audio_track`,
      `subtitle_burn`, `hdr`, `dolby_vision`, `interlaced`, `quality`, `bitrate`, `not_smooth`,
      `keyframes_missing`, `gop_too_long`, `too_large`, `budget`, `no_worker`.
    - De speler toont ze onder "Info" in gewone taal ("Omgezet: AC-3 kan deze browser niet
      afspelen"). `/beheer` telt ze per dag. Zo zie je waarom de CPU werkt.
20. **Het clientprofiel: gemeten in de client zelf, niet uit een lijst.**
    - De speler vraagt `GET /api/v1/media/{node_id}/codecs`. Dat geeft de vaste lijst kandidaten
      (`avc1.42E01E`, `avc1.64001f`, `avc1.640028`, `avc1.640033`, `hvc1.1.6.L120.90`,
      `hvc1.2.4.L150.90`, `vp09.00.40.08`, `vp09.02.40.10`, `av01.0.08M.08`, `av01.0.08M.10`,
      `mp4a.40.2`, `mp4a.40.5`, `ac-3`, `ec-3`, `opus`, `flac`, `mp3`), plus de echte strings van
      dit item.
    - Per string drie toetsen:
      - `MediaSource.isTypeSupported` (of `ManagedMediaSource`), in `video/mp4`, en voor VP9, AV1 en
        Opus ook in `video/webm` → `mse`;
      - `video.canPlayType` → `file`;
      - `navigator.mediaCapabilities.decodingInfo` → `smooth` en `power`. Voor het item met zijn
        echte maat en bitrate; voor de kandidaten op 1080p. HDR met `transferFunction` `pq` of
        `hlg`, `colorGamut: "rec2020"`.
    - Verder: `matchMedia("(dynamic-range: high)")`, native HLS
      (`canPlayType("application/vnd.apple.mpegurl")`), de schermhoogte maal `devicePixelRatio`,
      kanalen, en of `audioTracks` er is.
    - Het profiel (versie 1):

      ```json
      {
        "v": 1,
        "base": "web",
        "client": {"kind": "web", "name": "Chrome 141", "app": "0.7.0"},
        "engines": {"mse": true, "mms": false, "native_hls": false},
        "file": ["video/mp4", "video/webm", "audio/mpeg", "audio/flac", "audio/ogg"],
        "codecs": {
          "vp09.00.40.08": {"mse": true, "file": true, "smooth": true},
          "avc1.640028":   {"mse": false, "file": false},
          "opus":          {"mse": true, "file": true}
        },
        "hdr": {"pq": false, "hlg": false, "dv": false, "screen": false},
        "max": {"height": 1440, "bitrate_kbps": 0},
        "audio": {"channels": 2, "track_switch": false}
      }
      ```

    - De server laat alleen strings door die op een bekend patroon passen, hooguit 64, en het hele
      profiel hooguit 8 KB (`ClientProfile` in pydantic; onbekende velden gaan weg).
    - De web-app bewaart de uitkomst voor de kandidaten per browserversie in `localStorage` (alleen
      gemak, in try/catch). De strings van het item test hij elke keer opnieuw; dat kost minder dan
      10 ms.
    - **Basisprofielen** voor clients die niet alles kunnen testen of bekende eigenaardigheden
      hebben, als data in `api/app/media/profiles/*.json`: `web`, `ios-safari`, `webos-5`,
      `android-exo` en `windows-webview2`. De client noemt er één (`"base": "webos-5"`); wat hij zelf
      meet, gaat voor. Een basisprofiel zet ook vlaggen voor de playlists, zoals "geen
      `EXT-X-INDEPENDENT-SEGMENTS`" voor webOS [doc]. `docs/fase-5/clientprofiel.md` beschrijft het
      profiel met een versienummer, voor fase 9.
    - Het profiel is geen beveiliging. Wie liegt, krijgt iets wat hij niet kan spelen. De kosten
      begrenst de toelating toch.
21. **Sessies en het adres: de sid is een sleutel in het pad.**
    - `POST /api/v1/media/{node_id}/play` (Bearer) maakt een sessie in het geheugen
      (`app/media/sessions.py`).
    - **De sessie pint de inhoud:** bij het maken onthoudt hij `(sha, size)` van de node. Elke
      keer dat de app iets opent voor de sessie (`/file`, een producent, ondertitels, audio,
      sprites), gebruikt hij `BlobStore.path(gepinde sha)`, en kijkt hij of de node nog naar die
      sha wijst. Zo niet: 410 `media_session_gone` met reden `replaced`. De speler vraagt een
      nieuwe sessie, en de regel over de positie bij een nieuwe versie geldt (Keuze 6). Plan,
      keyframe-index, init en de segmenten in de cache horen zo altijd bij één bestand.
    - De bron van een sessie is `SessionSource = Node | LoadtestFile`. `LoadtestFile` is alleen
      voor de belastingtest (§4.6): een bestand in `media/loadtest/` dat de worker met `synth`
      maakte. Alleen een beheerder met de vlag `MEDIA_LOADTEST=1` kan zo'n sessie maken.
    - `sid = <node_name>.<base64url(24 willekeurige bytes)>`. De naam van de node is voor later
      (Keuze 48).
    - Alle verzoeken van de speler gaan naar `/api/v1/stream/{sid}/…` zonder header: een `<video>`
      en native HLS kunnen geen Bearer sturen. Geen cookies, dus geen gedoe met SameSite en CSRF.
    - Direct play gaat óók via de sessie: `GET` en `HEAD /api/v1/stream/{sid}/file`, met Range,
      `If-Range` en de sha als ETag. Zo vervalt het downloadtoken van 10 minuten voor video (N4).
    - **Elk verzoek** gaat langs `sessions.guard`:
      1. de sid opzoeken (geheugen). Onbekend of verlopen: 410 `media_session_gone`. De speler
         vraagt dan stil een nieuwe sessie op zijn positie. Is de sessie gesloten (de vlag
         `closed`), dan de grafsteen hieronder;
      2. de toegang van nu, met een cache van hooguit 15 s:
         - de gebruiker is actief en niet geblokkeerd;
         - de refresh-family van de login is niet ingetrokken (`security.family_revoked`);
         - het bestand is nog leesbaar (eigenaar, of `access.resolve`) en niet in de prullenbak;
         - bij een publieke link: de link leeft, het bestand zit erin, en de vingerafdruk van het
           wachtwoord is gelijk (`security.password_print`);
      3. mislukt 2: 403 `media_access_gone`, en de sessie gaat dicht. De speler stopt en zegt
         waarom. Hij probeert het niet opnieuw.
    - **Een grafsteen.** Sluit de app een sessie zelf (een beheerder drukt op Stop, `close_user`,
      een ingetrokken toegang), dan onthoudt hij de sid 30 minuten met de reden. Die sid geeft dan
      403 `media_access_gone` met `reason` (`stopped`, `revoked`, `blocked`), niet 410. Anders zou
      de speler stil een nieuwe sessie vragen, en zou Stop niets doen.
    - **Intrekken werkt meteen.**
      - Waar nu `office_tunnel.close_user` wordt aangeroepen (overal uitloggen, blokkeren), komt
        `media.sessions.close_user` erbij. In `revoke_family` (één login intrekken,
        `auth.py:515`) staat `office_tunnel.close_login(family)`; daar komt
        `media.sessions.close_login(family)` naast. Een sessie onthoudt daarvoor de family van de
        login die hem maakte.
      - `close_user`, `close_login`, `close_node`, Stop en een mislukte controle in de guard zetten
        de vlag `session.closed` (in het geheugen).
      - **Een lange respons kijkt zelf.** `/file` en `audio.mp4` zijn één respons die minuten kan
        lopen. De app stuurt ze via een eigen iterator. Die kijkt na elk stuk (hooguit 4 MB, of na
        5 s) naar `session.closed`, en doet stap 2 van de guard opnieuw (met de cache van 15 s).
        Is de sessie dicht, dan breekt de iterator de respons af en gaat de verbinding dicht.
      - Een ingetrokken deling werkt zo binnen 15 s, ook midden in een direct-play-stream. Wat de
        browser al gebufferd had, speelt nog af.
      - Test: een `/file`-stream die de client pauzeert, dan intrekken, dan weer lezen. De
        verbinding sluit binnen één stuk.
    - Grenzen: 12 uur absoluut, 30 minuten zonder verzoek, 8 per gebruiker (de oudste gaat dicht),
      2 per publieke link, 64 in totaal. `replace: <sid>` sluit de oude sessie in dezelfde beurt,
      zodat een wissel niet tegen de grens loopt.
    - In de cachemap staat niet de sid maar een eigen uuid van de sessie.
    - Een herstart van de app: de sessies zijn weg. De speler krijgt 410, vraagt een nieuwe sessie
      op zijn positie en loopt na 1–3 s door.
22. **Segmenten: fMP4, audio en video samen, en bij transcode elk segment precies 2 s.**
    - Eén init (`ftyp`+`moov` met beide sporen) en segmenten (`moof`+`mdat` met beide `traf`'s),
      zoals ffmpeg ze naar de pipe schrijft (D2).
    - **Waarom audio en video samen:**
      - één SourceBuffer en één pipe;
      - de eis van webOS "audio- en videosegmenten even lang" klopt vanzelf [doc];
      - native HLS kent gemuxte fMP4.

      De prijs: een ander audiospoor is een nieuwe sessie (Keuze 32), 0,3–1,5 s. Aparte
      audio-rendities met een eigen fragmentschrijver (ontwerp B) zijn veel meer code voor een
      wissel die zelden gebeurt (Keuze 50).
    - **Het rooster bij transcode** (nieuw in deze revisie, J3):
      - een IDR elke 2 s: `-force_key_frames expr:gte(t,n_forced*2) -forced-idr 1
        -sc_threshold 0`, en `-g 100000` zodat de encoder zelf geen IDR's maakt. Zonder dat maakt
        x264 elke 250 beelden een extra IDR, dus bij een bron boven 125 fps midden in een segment
        (V5);
      - `-fpsmax 60`: een bron van 240 fps wordt 60 fps. Zo klopt het level van de tabel;
      - (spike) het rooster hangt aan het eerste videobeeld: `origin` = `start_time` van het
        videospoor − S, altijd ≥ 0 (S2, en nagemeten in spike.md, "Extra"). Segment 0 loopt van
        mediatijd 0 tot `origin + 2`, segment k (k ≥ 1) van `origin + 2k` tot `origin + 2k + 2`.
        Meestal is `origin` 0,021 s (de voorloop van AAC), minder dan één beeld. Revisie 2 zei:
        segment k van 2k tot 2k + 2;
      - (spike) het aantal komt uit het laatste videobeeld: `N = ⌊(laatste − origin) / 2⌋ + 1`,
        met `laatste` de pts van het laatste videobeeld in mediatijd (`last_frame_ms` uit de
        probe). Revisie 2 zei `N = ceil(D/2)` met de duur van het formaat; dat klopte in 0 van de
        10 runs van S2. Het laatste segment loopt tot het eind van de producent en is korter. Een
        fragment voorbij N (`-fpsmax` vult het eind aan) hoort bij het laatste segment;
      - de knipper deelt fragmenten in op de pts van hun eerste sample, net als bij remux
        (Keuze 23). Normaal is elk fragment één segment. Komt er toch een extra keyframe, dan
        hoort dat fragment bij hetzelfde segment. `fps240.mp4` in S2 eist precies één IDR per 2 s;
      - `TARGETDURATION` is 2, of (spike) de afgeronde duur van segment 0 als die vanaf 2,5 s is
        (een late videostart). `EXTINF` is 2,000, behalve (spike) segment 0 (`origin + 2`, meestal
        2,021) en het laatste (tot het eind); de echte grens valt op het eerste frame na
        `origin + 2k`, dus hooguit één frame later. (spike) `EXTINF` en `map.json` tellen in
        mediatijd met dit rooster; de HLS-lint (§3.6) eist dat. De tijden in `tfdt` zijn de
        waarheid voor de speler;
      - herstarten gebeurt alleen op een grens, (spike) met `-ss (origin + 2k)` vóór de invoer
        (revisie 2: `-ss 2k`). Dan geeft dezelfde uitdrukking hetzelfde rooster, want `t` telt
        vanaf het eerste videobeeld van de uitvoer (J4; nagemeten), en `-ss` vóór de invoer telt
        in mediatijd. Met `-ss 2k` schuift het rooster `origin` op. `origin` gaat in hele ms naar
        beneden, zodat `-ss` het eerste beeld van het segment niet overslaat;
      - (spike) **MPEG-TS zoekt niet precies.** Zonder index begint de uitvoer na `-ss` op de
        volgende keyframe van de bron (S2: `interlaced.ts` 1,02 s te laat). Een herstart mist dan
        tot één GOP. Het contract gaat voor TS niet uit van exact zoeken; M4 kiest de aanpak
        (bijvoorbeeld eerder beginnen en weggooien wat vóór de grens ligt, of TS altijd vanaf een
        keyframe uit de index).
    - **Waarom overal 2 s, en niet eerst 2 s en dan 6 s:**
      - een segment kan pas weg als het af is. Een segment van 6 s direct na het eerste vraagt dat
        de producent 3× realtime haalt. Bij 3 × 720p haalt hij 1,7–2,4×, en dan hapert de speler
        0,6–2,1 s (J3). Met overal 2 s is 1× genoeg, ook voor native HLS;
      - het eerste segment is er na 0,8–1,9 s, en na een sprong ±1,3 s [gemeten J2, D4];
      - de prijs: één verzoek per 2 s per kijker, en een playlist van ±3.600 regels (±70 KB) voor
        2 uur. Apple's advies van 6 s is voor distributie [doc]; 2 s mag [doc: RFC 8216 §4.3.3.1].
    - **Het rooster bij remux:** uit de keyframe-index (Keuze 23).
    - **Tijden:** `tfdt = mediatijd + 1 s`. Daarvoor `-copyts -output_ts_offset (1 − S)
      -avoid_negative_ts disabled` met `+frag_discont` in `-movflags` (J4). `S` = `start_time` van
      de bron. Zo is niets negatief, ook niet met de voorloop van AAC. De speler zet
      `timestampOffset = −1`. Ondertitels staan in dezelfde mediatijd (Keuze 29).
    - **Beeld en geluid gelijk:** ook `+negative_cts_offsets` in `-movflags`. Zonder die vlag
      schrijft de muxer `trun` versie 0 met positieve offsets. Het eerste beeld komt dan een
      B-frame te laat: +104 ms bij transcode, +83 ms bij remux, en de ondertitels lopen even veel
      voor (V4). Met de vlag is het `trun` versie 1, en is de pts van het eerste beeld gelijk aan
      `tfdt`. De knipper leest de offsets dus getekend.
      - Chromium speelt `trun` v1 [niet getest in een browser; S2 meet het].
        (spike) S2 zag `trun` versie 1 in de uitvoer (H.264 met B-frames bij transcode, HEVC bij
        remux), met de pts van het eerste beeld gelijk aan `tfdt` (V4, binnen één beeld). In een
        browser is het nog niet getest: de Chromium van Playwright heeft geen H.264, en VP9 heeft
        geen B-frames (S4). Dat komt met S13 in `e2e-media-perf`.
      - Safari kunnen we hier niet testen. Dat wordt een apparaattest in fase 9, en staat bij de
        risico's. Speelt native HLS v1 niet goed, dan krijgt alleen native HLS de offsets van v0,
        met een `elst` in de init die de vertraging terugzet [niet getest].
    - **Generaties van de init:** elke herstart van een producent levert een init. De app
      vergelijkt de `stsd` van elk spoor met die van de eerste init van de sessie. Verschillen in
      `mvhd`, `tkhd` en `mdhd` tellen niet: de tijden staan in elke `tfdt` (N10: twee inits
      verschillen al vanaf byte 271).
      - Gelijk (verwacht): dezelfde generatie.
      - Anders: generatie +1. Elk segment van die run heeft de kop `X-Init: <gen>`. De MSE-speler
        voegt eerst de nieuwe init toe.
      - Native HLS kan die kop niet lezen. Daar sluit de app de sessie met 410 op die plek, en de
        speler laadt opnieuw. `/beheer` telt het. Verwacht: nooit.
      - Of `elst` mee moet in de vergelijking, beslist de spike (S5).
        (spike) Niet: er staat geen `elst` in de init, en na elke herstart is de `stsd` per spoor
        gelijk (S5). De vergelijking is de `stsd` per spoor. Een andere `stsd` (een andere
        trede) speelt in Chromium in dezelfde SourceBuffer, na de nieuwe init (S4).
    - `map.json` voor de MSE-speler:
      `{"gen": 1, "mime": "video/mp4; codecs=\"avc1.64001f,mp4a.40.2\"", "offset": -1.0,
      "init": "init.mp4?g=1", "segments": {"grid_ms": 2000, "count": 3600}}`. Bij remux staat er een
      lijst `[[begin_ms, duur_ms], …]` uit de index.
      (spike) Bij transcode staan `origin_ms` en `last_frame_ms` erbij: `"segments": {"grid_ms":
      2000, "origin_ms": 21, "count": 3600, "last_frame_ms": 7199979}` (een film van 2 uur op
      24 fps, video vanaf 0, S = −0,021). Begin van segment k ≥ 1: `origin_ms + k × grid_ms`;
      segment 0 begint op 0.
    - `master.m3u8` en `v.m3u8` voor native HLS: de app schrijft ze zelf (§2 M4). Een test eist dat
      `map.json` en `v.m3u8` dezelfde segmenten beschrijven.
23. **Remux van echte keyframes.**
    - De regel van Jellyfin: segment n loopt van de eerste keyframe ≥ n·6 s tot de eerste keyframe
      ≥ (n+1)·6 s. Een lange GOP geeft een langer segment. `TARGETDURATION` is het langste, naar
      boven afgerond. De grenzen staan vooraf vast; de playlist klopt dus precies.
    - Remux haalt tientallen keren realtime (het eerste segment na 0,15 s, video kopiëren plus
      audio omzetten 21×, feasibility §3.2). Daarom mag het hier 6 s zijn.
      (spike) Zonder `-readrate`: met tegendruk was het eerste segment er na 0,10 s, met
      `-readrate 1` pas na 7,46 s (S6). De knipper kost 1,2 ms CPU per MB.
    - ffmpeg kopieert; elke GOP is een fragment (D5). De app voegt fragmenten samen tot de grens
      van het volgende segment. Welk fragment bij welk segment hoort, beslist de pts van zijn
      eerste sample: `tfdt` plus de compositie-offset uit de `trun`, getekend (versie 1, met
      `+negative_cts_offsets`, Keuze 22). Niet `tfdt` alleen: met B-frames is dat de
      decodeertijd.
    - Een herstart op segment k: `-ss (b_k − 0,5)` vóór de invoer, want met kopiëren begint ffmpeg
      op de keyframe ervoor (N11). De app gooit fragmenten met een pts vóór `b_k` weg.
      (spike) Bij MPEG-TS niet: zonder index begint de uitvoer na `-ss` op de volgende keyframe
      (S2). Met `b_k − 0,5` is dat meestal `b_k` zelf, maar zeker is het niet; M4 test het met
      `interlaced.ts` (Keuze 22, MPEG-TS).
    - (spike) Een HEVC-bron met open GOP heeft CRA-keyframes, geen IDR. Die staan in de index als
      keyframe, en remux knipt er ook. De controle "elk fragment begint met een IDR" geldt alleen
      voor transcode. Na een sprong naar zo'n segment kunnen de eerste beelden (RASL) wegvallen
      [niet getest in een browser].
    - HEVC krijgt `-tag:v hvc1` (Apple wil `hvc1`, nooit `hev1` [doc]).
    - Audio: kopiëren als de client de codec kan en hij in mp4 past (AAC, Opus, FLAC; AC-3 en
      E-AC-3 alleen als het profiel ze kan). Anders AAC-LC stereo 160 kbit/s (`-c:a aac -ac 2
      -b:a 160k`), of Opus 128 kbit/s als AAC niet kan. MP3 in MP4 via MSE kan niet in Chromium
      (N13), dus MP3 wordt AAC. Dat kost 0,05 cores [gemeten].
    - mkv nooit direct, altijd remux: het kost bijna niets.
    - Een GOP die te groot is voor één `mdat` en voor het venster van de cache (bitrate ×
      `max_gop_ms` boven 64 MB, zoals 4K met 80 Mbit/s en een GOP van 10 s), geeft geen remux
      maar transcode, met reden `gop_too_long` (Keuze 19).
24. **Transcode: één trede per sessie.**

    | trede | grootte | maxrate / bufsize | H.264-level | string | cores voor 1,0× SDR [gemeten] | HDR |
    |---|---|---|---|---|---|---|
    | 1080 veryfast | ≤ 1920×1080 | 6M / 12M | 4.0 (4.2 boven 30 fps) | `avc1.640028` | 1,42 | 2,30 [gemeten] |
    | 1080 superfast | idem | idem | idem | idem | 0,95 | 1,57 [geschat] |
    | 720 veryfast | ≤ 1280×720 | 3500k / 7000k | 3.1 (3.2 boven 30 fps) | `avc1.64001f` | 0,74 | 1,21 [gemeten] |
    | 720 superfast | idem | idem | idem | idem | 0,62 | 1,02 [geschat] |
    | 480 veryfast | ≤ 854×480 | 1500k / 3000k | 3.0 (3.1 boven 25 fps; spike) | `avc1.64001e` (`avc1.64001f` bij 3.1) | 0,56 | 0,92 [geschat] |

    - De SDR-kosten zijn gemeten met een H.264-bron van 1080p24, de HDR-kosten met een
      HEVC10-bron van 1080p24 (N16, N17). Waar HDR niet gemeten is, geldt de factor 1,65 op de
      SDR-kosten (gemeten 1,62–1,64, naar boven afgerond). Een andere bron telt apart mee
      (Keuze 26).
    - De tabel is gemeten op 24 fps. Meer beelden per seconde (tot 60, door `-fpsmax 60`) kosten
      naar verhouding meer: × fps/24 [geschat; S11]. De toelating rekent dat mee (Keuze 26).
      (spike) S11 mat × 1,20–1,23 voor 30 tegen 24 fps (de regel geeft × 1,25): de regel blijft.
      Met echte inhoud kost x264 minder dan de tabel (0,89 / 0,59 / 0,43 voor 1080 / 720 / 480
      veryfast, tegen een bron met ruis voor de tabel). De tabel is dus een bovengrens.
    - (spike) Level 3.0 is voor 480 te laag vanaf 30 fps: 854×480 is 1.620 macroblokken, en 3.0
      haalt er 40.500 per seconde, dus 25 fps. Boven 25 fps wordt het 3.1.

    - Nooit vergroten. Een trede boven de bron valt weg. Een bron onder 480 houdt zijn eigen hoogte
      (even).
    - `auto` is de kleinste van: de bron, de schermhoogte (afgerond op een trede) en 1080. Bij HDR
      is `auto` 720 (Keuze 27). Welke preset, beslist de toelating (Keuze 26).
    - Video: `-c:v libx264 -preset {veryfast|superfast} -crf 23 -maxrate R -bufsize 2R
      -profile:v high -level:v L -pix_fmt yuv420p -g 100000 -fpsmax 60`, met het rooster van
      Keuze 22. Schalen naar de echte beeldverhouding, met een even breedte.
    - Audio: kopiëren als de client de codec kan en hij in mp4 past, anders AAC-LC stereo
      160 kbit/s. Zonder audiospoor: niets (Keuze 19).
    - **Een client zonder H.264 of AAC** (zoals de Chromium van Playwright) krijgt VP9 met Opus:
      `-c:v libvpx-vp9 -deadline realtime -cpu-used 8 -row-mt 1 -g 100000` en `-c:a libopus -b:a
      128k -ac 2 -ar 48000`. Dat is voor tests en zeldzame clients. Gemeten kost VP9 realtime
      evenveel als x264 veryfast (0,57–0,59 tegen 0,60, V3). De toelating rekent VP9 dus als 1,0×
      x264. S11 meet het opnieuw in de sandbox, waar VP9 gebruikt wordt. AV1 coderen bouwen we
      niet.
      (spike) S11: VP9 tegen x264 0,82–1,05 per trede, dus 1,0× blijft. **VP9 met HDR haalt op
      1080 geen realtime** op 3 cores (0,99×): een client zonder H.264 met een HDR-bron krijgt
      hooguit 720.
    - Interlaced: eerst `bwdif=mode=send_frame:parity=auto:deint=interlaced`.
    - (spike) Een beeldondertitel inbranden: eerst het beeld schalen, de ondertitel naar W×H
      schalen, dan `overlay` (S9: getest, en goedkoper dan `overlay` vóór het schalen, wat
      revisie 2 hier zei). De ondertitels komen uit een tweede fd (Keuze 29, §3.3).
25. **Zoeken: herstarten op een segmentgrens.**
    - De speler vraagt segment n.
    - Staat het in de cache van de sessie: meteen.
    - Loopt de producent en ligt n hooguit 3 segmenten (6 s) voor op wat er is: wachten.
    - Anders: de producent stoppen (de verbinding dicht) en een nieuwe starten op de grens van n
      (`-ss (origin + 2n)` bij transcode (spike; revisie 2: `-ss 2n`), `-ss (b_n − 0,5)` bij remux;
      bij MPEG-TS niet exact, Keuze 22). De nieuwe start pas als de oude weg
      is (`waitpid`, na 2 s SIGKILL), en zoekverzoeken binnen 300 ms worden één herstart
      (Keuze 12). Wat al in de cache staat, blijft geldig: hetzelfde rooster en, als de `stsd`
      gelijk is, dezelfde init.
    - Wachten duurt hooguit 20 s. Daarna 503 `media_timeout` met `Retry-After: 2`. Na 3 keer achter
      elkaar gaat de sessie dicht.
    - Het verschil in audio na een herstart (32–54 ms, D4) vangt de speler op door over het gat te
      springen (Keuze 32).
      (spike) Chromium springt niet zelf: een gat van 54 ms gaf ≥ 10 s stilstand (S4). De
      springer is dus nodig. Een natuurlijke herstart gaf in S4 overlap (39,8 ms), geen gat.
26. **Toelating en het CPU-budget.**
    - **De kosten van een plan tellen de bron mee.** Decoderen van een 4K- of HEVC10-bron kost
      apart (V3): 4K HEVC10 alleen decoderen is al 1,31 cores. Daarom:

      `kosten = (tabel(trede, hdr) × fps/24 + d(bron) − d(ref) + extra) × f`

      - `tabel(trede, hdr)`: de cores van Keuze 24;
      - `d(bron)` = `d_codec` × (breedte × hoogte × fps) / (1920 × 1080 × 24). `d_codec` is 0,18
        voor H.264 en 0,31 voor HEVC [gemeten V3]; (spike) VP9 0,18 en AV1 0,25 (8 bit; S11 mat
        0,150 en 0,222; revisie 2: 0,31); 10 bit en de rest 0,31 [geschat];
      - `d(ref)` = wat in de tabel al zit: 0,18 (H.264 1080p24) voor SDR, 0,31 (HEVC10 1080p24)
        voor HDR;
      - `extra`: inbranden +0,15, deinterlacen +0,2 [geschat; S11]. (spike) S9 mat +0,1 voor
        inbranden bij 720 (0,69 tegen 0,585 cores); de +0,15 blijft;
      - VP9 kost hetzelfde als x264 (V3; spike, S11: 0,82–1,05); remux kost 0,02, alleen audio
        omzetten 0,05.

      Nagerekend tegen de metingen (V3 en de critici):
      - 4K HEVC → 1080 veryfast: 1,42 + 1,24 − 0,18 = 2,48 (gemeten 2,42);
      - 4K HEVC → 720 veryfast: 0,74 + 1,24 − 0,18 = 1,80 (gemeten 1,89);
      - 4K HDR → 720: met de 1,35 die V3 voor 1080p HDR → 720 mat, 1,35 + 1,24 − 0,31 = 2,28
        (gemeten 2,28).
    - **Een zwaar plan telt dubbel.** Kost een plan meer dan 1,8 × `f` (meer dan 1080 veryfast van
      een bron van 30 fps), dan gebruikt het 2 van de `max_transcodes` plaatsen. Zo past een
      4K-bron wel, en blijft er plaats voor precies één kijker meer.
    - **Zelf meten.** De opdracht `bench` draait 10 s `testsrc2` met ruis, 1080p naar 720p
      veryfast. Dat geeft de factor `f` = (cores voor 1,0× hier) / 0,74 (de sandbox).
      (spike) Niet 0,74: dat is de tabelprijs van 720 veryfast van een bestand, niet van deze
      bench. Met 0,74 kwam `f` op de runner op 2,1, waar hij ±1 hoort te zijn (S7). Het wordt
      `f` = (cores voor 1,0× hier) / (de cores van dezelfde bench in de sandbox). M1 meet die
      referentie in de sandbox, en ook die van `f_dec`.
      - Daarnaast decodeert `bench` 4 s HEVC10 van 1080p, die hij eerst zelf met libx265 maakt
        (naar een pipe, niet naar schijf). Dat geeft `f_dec` voor `d(bron)`.
      - Alle kosten × `f` (en `d` × `f_dec`).
      - Bij de eerste start en met de knop "Opnieuw meten" op `/beheer`, alleen als er niets
        draait (anders 409 `media_bench_busy`).
      - Opgeslagen in `Setting "media"`. De spike meet de spreiding (S7).
        (spike) Op de runner: `f` 0,7 % en `f_dec` 1,9 % over drie runs. Eén run per meting is
        genoeg.
    - **Het budget** `B` = de cpu-grens van de worker, en anders het aantal cores − 1. Op de LXC is
      dat 3,0. `/beheer` kan het lager zetten.
      - OnlyOffice mag zelf 2 cores gebruiken. Samen is dat 5 op 4 cores. Meestal doet DS weinig.
        Maar na een herstart van DS maakt hij zijn fonts opnieuw, en na een update valt dat samen
        met het moment dat alle streams terugkomen.
      - Daarom: zolang de office-monitor zegt dat DS start of fonts maakt (de staat uit
        `office.health`), is het budget `B − 1,0`. Lopende streams blijven lopen; alleen nieuwe
        plannen krijgen een lagere trede. Het CPU-gebruik van de office-container zelf lezen kan
        niet: Docker geeft elke container een eigen cgroup-namespace.
    - **De regel bij `play`.** Een nieuwe transcode krijgt de eerste trede uit
      `[1080 veryfast, 1080 superfast, 720 veryfast, 720 superfast, 480 veryfast]`, begrensd door
      de bron, het scherm, de keuze van de gebruiker en 720 bij een publieke link, waarvoor geldt:

      `in gebruik + kosten(plan) + reserve ≤ 0,9 × B`, met
      `reserve = (max_transcodes − plaatsen in gebruik − plaatsen(plan)) × kosten(480 veryfast)`.

      Zo kan de laatste toegestane kijker altijd nog starten (het idee van ontwerp B). Met de
      cijfers van de proef, een H.264-bron van 1080p24, `B` = 3,0 en `max_transcodes` = 3:
      - kijker 1: 1080 veryfast (1,42 + 2 × 0,56 = 2,54 ≤ 2,7);
      - kijker 2: 1080 veryfast past niet, 720 veryfast net niet (2,72); 720 superfast (2,60);
      - kijker 3: 720 superfast (2,66).

      Met `max_transcodes` = 2 (het andere antwoord op Vraag 3): 1080 veryfast en 1080 superfast
      (2,37).

      Twee gevallen die revisie 1 fout deed:
      - kijker 1 met een HDR-bron van 1080p (HEVC10), in een browser met alleen VP9: 720 veryfast
        HDR (1,21 + 2 × 0,56 = 2,33). Revisie 1 rekende VP9 dubbel, en dan paste het niet;
      - kijker 1 met een 4K HEVC-bron (SDR): 1080 veryfast kost 2,48 en past niet (2,48 + 0,56 =
        3,04). 1080 superfast kost 2,01, telt dubbel, en past (2,01 + 0,56 = 2,57). Daarna past
        nog één kijker met een 1080p-bron: 720 superfast (2,63). Een derde krijgt `media_busy`.
      - De Grant reserveert ook de plaats in de worker (Keuze 12).
    - Een gekozen trede die niet past: een lagere, met de reden `budget`. De speler zegt dan
      "lagere kwaliteit: de server is druk". Past ook 480 niet: 503 `media_busy` met
      `reason: cpu` en `retry_after`.
    - Grenzen: `max_transcodes` 3 (`PLATFORM_MEDIA_MAX_TRANSCODES` is het plafond; `/beheer` kan
      alleen lager), 2 per gebruiker (`reason: user`), 1 per publieke link. Minder dan 10 GB vrij:
      `reason: disk` (Keuze 31).
    - **Afremmen door tegendruk** (D6): de app leest de pipe niet verder als de producent 60 s
      voor ligt op het laatste verzoek van de speler. Dat telt in seconden mediatijd, niet in
      segmenten (een remux-segment is 6 s of langer). ffmpeg blokkeert dan en kost niets.
      (spike) Daarom geen `-readrate`, ook niet bij transcode: de tegendruk zet ffmpeg stil (S2,
      S6), en `-readrate 1` levert het eerste segment pas na zijn hele lengte.
    - **Startvoorrang.** Zolang een sessie nog geen segment 0 heeft, lezen de andere producenten
      hooguit 10 s vooruit. Een nieuwkomer hoeft dan niet te vechten met producenten die hun
      buffer vullen. Na een herstart vragen alle spelers tegelijk een nieuwe sessie; ook dan
      krijgt elke eerste segment voorrang. Gemeten op 3 cores zonder die rem: drie tegelijk
      gestart geven het eerste fragment na 3,3–4,0 s (V7).
    - **Pauze:** 120 s geen verzoek. Dan gaat de producent dicht (dat maakt 250–570 MB vrij) en
      blijft de sessie. Verder spelen start hem op het volgende segment.
    - **Vast:** 30 s geen nieuw fragment terwijl de app leest. Dan stopt de app de producent en
      start hem één keer opnieuw op hetzelfde segment (Keuze 46).
27. **HDR naar SDR, en wat als het niet kan.**
    - De worker kijkt bij het starten (`caps`) of `zscale` en `tonemap` er zijn. Debian bouwt
      ffmpeg met libzimg [doc, pakketgegevens]; de spike controleert het in het image (S1).
      (spike) Bevestigd: in 7.1.5 zijn `zscale` en `tonemap` er, en de keten PQ en HLG naar SDR
      draait (S1).
    - De keten, op de doelgrootte, met de bron expliciet:
      `zscale=w=W:h=H:tin=smpte2084:min=bt2020nc:pin=bt2020:rin=tv:t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p`,
      plus `-color_primaries bt709 -color_trc bt709 -colorspace bt709`. Bij HLG
      `tin=arib-std-b67`. Dolby Vision profiel 7 en 8 gebruiken hun basislaag.
    - `auto` bij HDR is 720 (1,21 cores). 1080 alleen als iemand hem kiest en het budget het
      toelaat (2,30 cores). (spike) Met VP9 (een client zonder H.264) nooit 1080: VP9 met HDR
      haalt daar geen realtime (S11).
    - **4K HDR live nooit, en dat is een regel in decide.** Een HDR-bron groter dan 1920×1088
      geeft 422 `media_hdr` met reden `too_large` (ook 2,28 cores naar 720 is te veel naast
      anderen, V3). `can_raw` is dan alleen `true` als direct of remux kan: een client met een
      HDR-scherm, of "toch afspelen zonder kleurcorrectie" via remux. Een test in
      `test_media_decide.py`: "hevc 2160p HDR, auto" geeft deze weigering; en in
      `test_media_admission.py` komt dat plan nooit bij de toelating.
    - **Ontbreekt de tone-mapping**, dan 422 `media_hdr` met `can_raw: true`. De speler biedt
      "Toch afspelen zonder kleurcorrectie" aan (`choice.hdr = "raw"`): direct of remux als de
      client de codec kan, anders transcode zonder tone-mapping. Met fletse kleuren, en met de
      uitleg erbij.
    - Dolby Vision profiel 5 kan niet: zonder libplacebo en een GPU worden de kleuren paars en
      groen. 422 `media_hdr` met `can_raw: false` en de reden `dolby_vision`.
    - HDR naar een HDR-scherm: alleen direct of remux (kost 0). HDR opnieuw coderen (x265 10-bit)
      bouwen we niet.
28. **Hardwareversnelling: klaargezet, niet gebouwd.**
    - `caps` meldt `/dev/dri/renderD*`, `ffmpeg -hwaccels`, en een korte proef met
      `-init_hw_device vaapi=va:/dev/dri/renderD128`.
    - `/beheer` toont "GPU: niet gevonden" of "gevonden, nog niet gebruikt".
    - De encoder zit achter één naad (`app/media/ladder.py`: `Encoder`). VAAPI of QSV erbij is later
      één plek, plus een eigen kostentabel en een driver in het image (een keuze voor B5).
    - Wat Jonas dan op de host moet doen (`/dev/dri` in de LXC, de groep `render`, `devices` in
      compose), staat als "later" in het runbook. Het is nooit een eis, en nooit een verzwakking
      van de LXC.
    - Eerlijk: met een iGPU kunnen HDR en 3 × 1080p waarschijnlijk wel [niet getest]. Zonder niet
      [gemeten].

### Ondertitels, sprites en de cache

29. **Ondertitels: tekst als WebVTT in `<track>`, beeld alleen ingebrand.**
    - **Ingebedde tekstsporen** (`subrip`, `ass`, `ssa`, `webvtt`, `mov_text`): de worker pakt ze
      allemaal in één run uit, in hun eigen vorm, elk naar een pipe die de app maakte (Keuze 11):
      ASS en SSA met `-c:s copy`, de rest als SRT (`-c:s srt`). Altijd met `-copyts`. Dan maakt een **eigen omzetter** er WebVTT van
      (`app/media/subs.py`, pure functies, aangeroepen in de worker). Waarom zelf: de omzetter van
      ffmpeg gooit `\an8` (boven in beeld) en kleur weg (N18). Onze omzetter:
      - ASS `{\an1..9}` wordt `line:`, `position:` en `align:` in VTT;
      - `<b>`, `<i>` en `<u>` blijven; eenvoudige kleur wordt `<c.kleur>` met een vaste lijst
        klassen in onze CSS;
      - andere ASS-codes gaan weg; tekeningen (`\p1`) worden overgeslagen;
      - tekenset: eerst een BOM, dan geldig UTF-8, anders CP1252 (het oude Nederlands);
      - tijden in ms, met de `start_time` van de bron eraf, zodat ze in mediatijd staan
        (Keuze 22). Zonder dat schoof een ondertitel 21 ms [gemeten].
    - Uitpakken leest het hele bestand. Bij 15 GB op een harde schijf is dat een minuut of meer.
      Voor een bibliotheek gebeurt het op de achtergrond (stap `subs`). Voor een los bestand pas
      als iemand de ondertitel kiest: dan pakt de worker dat ene spoor meteen uit, en de speler zegt
      "ondertitel wordt voorbereid" (202 met `Retry-After: 5`).
    - **Losse bestanden** (`.srt`, `.vtt`, `.ass`, `.ssa`, Keuze 5): dezelfde omzetter, via de
      worker (opdracht `subs`), gecachet per blob.
    - **De app controleert elke VTT** die terugkomt: hooguit 5 MB per spoor, 20.000 cues en
      2.000 tekens per cue, alleen de tags en klassen van de lijst.
    - Serveren: `GET /api/v1/stream/{sid}/subs/{id}.vtt` (`text/vtt; charset=utf-8`). `id` is
      `e<index>` voor een ingebed spoor en `x<i>` voor een los bestand.
      - `x<i>` is een plek in de lijst `subs` die de sessie bij `play` vastlegt. Nooit een node-id
        uit het adres.
      - Bij `play` filtert de app die lijst: alleen losse bestanden die de gebruiker van nu mag
        lezen (`access.resolve`). Bij een publieke link alleen bestanden die ook in de link zitten
        (`inside`).
      - Een `x<i>` buiten de lijst geeft 404. Tests in `test_media_sessions.py`: de node-id van
        een ondertitel van een ander geeft 404; een los bestand buiten de publieke link staat niet
        in de lijst.
    - De speler maakt er `<track kind="subtitles" srclang label>` van. Dat werkt met MSE (spike,
      S4: de cues gaan 1–8 ms na hun tijd af), en hoort te werken met native HLS [niet getest in
      Safari].
    - **Geforceerd:** heeft de gebruiker geen ondertitel gekozen, en is er een geforceerd
      **tekst**spoor in de taal van het audiospoor, dan staat dat aan. Een geforceerd
      **beeld**spoor (meestal PGS op een Blu-ray-rip) gaat nooit vanzelf aan: dat zou direct play
      of remux stil in een transcode veranderen, en achter een publieke link een 403 geven. Dan
      blijft de modus staan, en geeft het plan `forced_available: "e<i>"`. De speler biedt
      "Geforceerde ondertitel inbranden (kost omzetten)" aan. Tests in `test_media_decide.py`:
      PGS geforceerd met een profiel dat direct kan, geeft `direct`; een publieke link met PGS
      geforceerd geeft `remux`, geen 403.
    - **Beeldondertitels** (PGS, VobSub, DVB) kunnen niet naar tekst zonder OCR. Ze worden alleen
      ingebrand: transcode met `[0:s:N]scale=W:H[s];[v][s]overlay=eof_action=pass`. Dat kan alleen
      voor een ingebed spoor (uit de ene invoer-fd). Losse `.sup`, `.sub` en `.idx` koppelen we in
      fase 5 niet (Keuze 5). De speler zegt bij de keuze "wordt ingebrand, kost omzetten". Dit
      schuift als eerste als het uitloopt (§5).
      - (spike) **De ondertitels komen uit een tweede invoer-fd** van dezelfde blob, die op
        `max(0, T − 30)` begint: `[1:{N}]scale=W:H[s];[v][s]overlay=eof_action=pass` (§3.3). Met
        één invoer mist na een herstart een ondertitel die vóór de keyframe van het zoeken begon
        (S9: met `-ss 4` ontbrak die van 2,0 s). De app opent de blob daarvoor twee keer (twee
        fd's, elk een eigen positie; Keuze 12). Het blijft alleen voor een ingebed spoor.
      - (spike) Technisch werkt het, voor PGS en VobSub (S9). De terugval "inbranden schuift" van
        §3.7 is dus niet nodig; als de planning uitloopt, schuift het nog steeds als eerste.
      - (spike) Een ondertitel die langer dan 30 s staat, mist na een herstart nog steeds. Netter
        is de begintijd uit de stap `subs`; dat is een open punt voor M5.
    - Niet in fase 5: tekst inbranden (libass en lettertypen in de worker), gesegmenteerde
      VTT-playlists voor native spelers, en ondertitels als HLS-rendities. `<track>` volstaat.
30. **Sprites en hoofdstukken.**
    - **Sprites** alleen voor video in een bibliotheek, op de achtergrond (stap `sprites`).
      - Commando: `ffmpeg -skip_frame nokey … -vf fps=1/10,scale=160:-2,tile=10x10 -an -sn -c:v
        mjpeg -q:v 5 -f image2pipe pipe:1`.
      - Een film van 2 uur kost ±1 minuut op één core [gemeten], vooral schijf, en ±1,4 MB.
      - De app knipt de JPEG's (SOI/EOI), controleert de grootte, en schrijft ze in
        `media/trick/<sha>/<i>.jpg`. In `media_probe.trick`: `{interval: 10, w: 160, h: 90, cols:
        10, rows: 10, count}`.
      - Daarnaast maakt de app `trick.vtt`: per 10 s een cue met `<i>.jpg#xywh=x,y,160,90`. Zo kan
        elke speler (ook die van fase 9) de sprites gebruiken.
      - Ze komen via de sessie: `GET /api/v1/stream/{sid}/trick.vtt` en
        `GET /api/v1/stream/{sid}/trick/{i}.jpg`, allebei langs `sessions.guard`. De relatieve
        adressen in `trick.vtt` werken dan zoals ze er staan. Intrekken geldt ook voor de sprites,
        en de regel "alleen relatieve URI's" van de lint klopt. Revisie 1 had ondertekende adressen
        per plaatje; een relatief adres in `trick.vtt` verliest dan de handtekening.
      - Een test haalt elk adres uit `trick.vtt` op en eist 200.
      - De beelden vallen op keyframes, dus hooguit een paar seconden naast de tik. Goed genoeg om
        te scrubben. I-frame-playlists bouwen we niet: webOS kent ze niet, en sprites doen hetzelfde
        [doc].
    - **Hoofdstukken** uit de probe: `[{start_ms, end_ms, title}]`, titel hooguit 200 tekens. Op de
      zoekbalk als streepjes, in een menu, en met PageUp en PageDown.
    - **De aftiteling:** een hoofdstuk met een titel als `credits`, `aftiteling`, `end credits` of
      `outro` markeert het begin ervan. Dat telt voor "gezien" (Keuze 36) en voor de kaart
      "Volgende aflevering" (Keuze 32).
31. **De cache en het opruimen.** Alles onder `data_dir/media/`, dus op `/tank/platform` en niet
    op de rootdisk van 64 GB:

    | map | inhoud | grens |
    |---|---|---|
    | `sessions/<uuid>/g<gen>/init.mp4`, `<n>.m4s` | segmenten per sessie | `cache_gb` 20, samen met `audio/` |
    | `subs/<sha>/<track>.vtt` | ondertitels per blob | klein |
    | `trick/<sha>/<i>.jpg`, `trick.vtt` | sprites | ±1,4 MB per film van 2 uur |
    | `art/<sha>/<maat>.webp` | posters en hoezen van providers | ±0,5 GB per 1.000 films en 5.000 afleveringen |
    | `audio/<sha>.mp4` | remux voor gapless | LRU, telt mee in `cache_gb` |
    | `run/` | de socket van de lokale worker | — |

    - Een sessie dicht: haar map weg.
    - Bij het starten: `sessions/` helemaal leeg. De sessies van vóór de start bestaan niet meer.
    - **Een venster in bytes per sessie:** `cache_gb` gedeeld door het aantal live sessies. Komt
      een sessie erboven, dan haalt de app meteen haar oudste segmenten achter de speler weg (niet
      die binnen 60 s achter de speler). Zo blijft ook een 4K-remux met een hoge bitrate binnen de
      grens.
    - Elke 5 minuten (housekeeping `media.sweep`):
      - sessies 30 minuten stil: dicht;
      - boven `cache_gb`: eerst de oudste stille sessies dicht, dan de oudste segmenten achter de
        speler, hoe jong ze ook zijn;
      - minder dan 10 GB vrij op `data_dir`: geen nieuwe transcode en geen nieuwe remux (503
        `media_busy` met `reason: disk`) en de melding `media_cache_full`. Direct play kan nog.
    - Wat het kost: 1080p ±45 MB per minuut, dus ±5,4 GB per film van 2 uur; 720p ±3,1 GB (N16).
    - Eén cache per sessie, niet gedeeld tussen sessies (ontwerp B deelde per plan). Delen helpt
      alleen als twee mensen tegelijk dezelfde film in hetzelfde plan kijken. Dat is zeldzaam in
      één huishouden, en het kost een tabel, een LRU en tellers.
    - `usage.py` krijgt het deel `media` voor `/beheer` (`subs`, `trick`, `art`, `audio`,
      `sessions`).

### De speler

32. **Een eigen speler (B19): geen hls.js.**
    - `web/src/player/`, als lazy chunk:
      - `caps.ts`: het profiel van Keuze 20;
      - `session.ts`: `play`, vernieuwen en sluiten;
      - `mse.ts`: de lader voor MSE en MMS (zie hieronder);
      - `native.ts`: `video.src = master.m3u8` (Safari en iOS zonder MMS, later webOS als dat
        beter werkt);
      - `direct.ts`: `video.src = /api/v1/stream/{sid}/file`;
      - `tracks.ts`: ondertitels als `<track>`, de regel voor geforceerd, en een ander audiospoor
        (een nieuwe sessie met `replace`, op de positie van nu);
      - `keys.ts`: de toetsen als pure functie;
      - `progress.ts`: Keuze 36;
      - `ui/Player.tsx` en `player.css`: de bediening.
    - **De lader `mse.ts`:**
      - leest `map.json` en maakt één SourceBuffer, `timestampOffset = −1`;
      - bij MMS (Safari) `disableRemotePlayback = true`, want MMS speelt anders niet [doc]. AirPlay
        valt dan weg;
      - buffert 30 s vooruit en gooit weg wat meer dan 60 s achter ligt. Bij `QuotaExceededError`:
        eerst achter weggooien, dan vooruit tot 10 s, en opnieuw;
      - zoeken: `abort()`, het juiste segment berekenen, ophalen;
      - een nieuwe init als `X-Init` verandert, (spike) in dezelfde SourceBuffer: ook een andere
        `stsd` (een andere trede) speelt daar zonder stilstand (S4);
      - **springt over gaten** tot 0,5 s in `buffered`. (spike) Bij `waiting`: ligt het begin van
        het volgende gebufferde stuk binnen 0,5 s, dan na 250 ms daarheen. Revisie 2 zei: als
        `currentTime` 1 s niet loopt. Chromium springt niet zelf (S4: een gat van 54 ms gaf
        ≥ 10 s stilstand; met de springer na 250 ms was het 260 ms);
      - **na 2 haperingen in 60 s** bij transcode: één trede lager (een nieuwe sessie met
        `replace`). Geen automatische ABR.
    - **Foutgedrag per code:**
      - `410 media_session_gone`: stil een nieuwe sessie op dezelfde plek, hooguit 3 keer per
        minuut;
      - `403 media_access_gone`: stoppen, en zeggen waarom;
      - `503 media_busy` of `media_timeout`: na `Retry-After` opnieuw, met een melding;
      - een netwerkfout, of 502 of 503 zonder code (de app herstart of wordt bijgewerkt): blijven
        proberen met 1, 2, 4 en 8 s, en dan elke 10 s, zolang er buffer is plus 120 s. Ondertussen
        kijkt de speler naar `/api/v1/health`. Is die weer goed, dan meteen opnieuw. Komt er dan
        410, dan een nieuwe sessie op de positie van nu.
      - Een update legt de app ±30–60 s stil: stoppen, de container starten, migraties en de
        gezondheidscheck. Met 30 s buffer plus 120 s haalt de speler dat. Pas daarna zegt hij
        "De server is niet bereikbaar" en stopt hij.
    - **De bediening:**
      - spelen, de zoekbalk met buffer, hoofdstukken en een sprite bij hover en slepen;
      - tijd en volume;
      - kwaliteit (Auto, Origineel, 1080, 720, 480);
      - audiospoor en ondertitels;
      - picture-in-picture (`requestPictureInPicture`, op iOS `webkitSetPresentationMode`);
      - volledig scherm op de container van de speler, zodat onze knoppen er blijven;
      - "Info" met de redenen uit `Plan`.
    - **Volgende aflevering:** bij het begin van de aftiteling (of 30 s voor het eind zonder
      hoofdstuk) een kaart met een aftelling van 10 s. Esc of een knop annuleert. Uit te zetten.
      Een film in delen (`cd1`, `cd2`) speelt het volgende deel zonder vragen.
    - De Media Session API: titel, serie, hoes, en de acties spelen, pauze, zoeken, vorige en
      volgende aflevering.
    - De viewer van fase 3 (`viewer/MediaView.tsx`) laadt deze speler voor video. Een video in
      Bestanden krijgt dus dezelfde beslissing, en geen downloadtoken meer.
33. **Toetsenbord, a11y, i18n en de grootte.**
    - Toetsen op de afspeelpagina:

      | toets | doet |
      |---|---|
      | Spatie, K | spelen en pauze |
      | J, L | 10 s terug en vooruit |
      | ←, → | 5 s terug en vooruit |
      | ↑, ↓ | volume |
      | M | geluid uit en aan |
      | F | volledig scherm |
      | C | ondertitels aan en uit (de laatste keuze) |
      | A | volgend audiospoor |
      | P | picture-in-picture |
      | PageUp, PageDown | vorig en volgend hoofdstuk |
      | 0–9 | naar 0–90 % |
      | Home, End | begin en eind |
      | N | volgende aflevering (alleen op `/kijken`) |
      | I | info |
      | ? | de lijst van toetsen |
      | Esc | volledig scherm uit, de kaart annuleren, of terug |

      - De toetsen werken niet als de focus in een menu of invoerveld staat.
      - Shift+N is al van de app (de nieuwe versie laden, `AppStatus.tsx`). Daarom is "volgende
        aflevering" N. Op andere pagina's is N al "nieuw" (Bestanden, Albums, Gebruikers); daar
        speelt geen speler.
      - **In de viewer van fase 3** (een video in Bestanden) krijgt de speler alleen Spatie, K, J,
        L, M, C, A, P en 0–9. ←, →, Home, End, F, I, D, E en ? blijven van de viewer. Daar zoeken
        J en L.
      - Een vitest (`keys.test.ts`) legt de combinaties van de scopes global, viewer, player en
        `/kijken` naast elkaar. Elke combinatie is uniek, of staat in een lijst van bewuste
        overschrijvingen.
      - Voor later (fase 9, klein): op een TV gaan de pijltjes langs de knoppen (roving tabindex),
        en de terugtoets van webOS (code 461) werkt als Esc.
    - A11y:
      - elke knop is een `<button>` met een label via `t()`;
      - de zoekbalk is `role="slider"`, met `aria-valuetext` "12:34 van 1:45:00";
      - de bediening verdwijnt na 3 s stilte, maar nooit zolang er iets focus heeft;
      - een live region meldt een andere kwaliteit of spoor;
      - `prefers-reduced-motion` zet de overgangen uit;
      - een checklist in `docs/fase-5/toetsenbord-m6.md`.
    - i18n: de gebieden `media`, `player` en `music` (nl en en), de codes in
      `i18n/errors/media.ts`, en taalnamen via `Intl.DisplayNames`.
    - **Grootte** (gzip, nieuwe regels in `budget.mjs`):
      - de speler hooguit 35 KB (`player`; de proef was 3,1 KB voor de kern);
      - elke mediapagina hooguit 45 KB (`mediaRoute`);
      - de chunk `music` (pagina's, gapless, balk) hooguit 40 KB;
      - de eerste lading blijft ≤ 100 KB (nu 93,8). Er komt alleen de registratie van de routes bij
        (±0,5 KB).

### Toegang

34. **Publieke links en gasten.**
    - `POST /api/v1/public/media/{node_id}/play`, met de sleutel van de link (`CurrentLink`), net als
      `/public/download`. Het bestand moet in de link zitten (`inside`), en de link moet lezen
      toestaan.
    - Alleen direct en remux. Transcode alleen als `public_transcode` aan staat (Vraag 5), en dan
      hooguit 720, 1 per link, en nooit de laatste plek: de reserve van Keuze 26 telt een link niet
      mee.
    - 2 sessies per link, en `play` hooguit 30 per minuut per IP.
    - De sessie hoort bij `share_id`. Elk verzoek kijkt of de link nog leeft (verlopen, verwijderd,
      wachtwoord veranderd; Keuze 21).
    - Geen voortgang voor publieke kijkers.
    - **Gasten** (B18) zijn gewone gebruikers met minder rechten. Ze zien wat met ze gedeeld is, en
      spelen af met dezelfde regels en grenzen.
    - **Sprites** gaan via de sessie (Keuze 30). Intrekken geldt dus ook daarvoor.
    - **Art** (posters, fanart, hoezen) gaat via ondertekende adressen, zoals previews. Die zijn
      gebonden aan de inhoud (sha), niet aan een gebruiker, en gelden 12 uur. Wie een sha kent,
      kent de inhoud al. Dit is het model van fase 3, en we houden het: een poster zegt minder
      dan de previews die fase 3 al zo serveert.
35. **Geen lekken.**
    - De sid komt nooit in een log: `_hide` in `main.py` krijgt het patroon
      `(/api/v1/stream/+(?:[a-z0-9-]{1,63}\.)?)[^/?\s"]+` → `\1***`. Een test speelt een sessie af
      en zoekt de sid in alle logregels (`test_media_logs.py`).
      *Noot (M0, review S5):* het eerste patroon (`(/api/v1/stream/[^/.\s"]+\.)[^/?\s"]+`) liet
      de sid staan bij een lege naam van de node (`.<geheim>`), een dubbele `/` of een sid zonder
      punt. Nu gaat het hele stuk na `/api/v1/stream/` weg; alleen een nette naam van de node
      (`[a-z0-9-]`, dan een punt) blijft staan. Voor M3: maak de sid met zo'n nette naam ervoor
      (anders komt hij er niet in, maar lekt hij ook niet); `PLATFORM_NODE_NAME` zelf valideert
      M0 niet, want dat kan een werkende 0.6.0 laten stoppen.
    - `Referrer-Policy: same-origin` staat al.
    - `Cache-Control`: `map.json` en playlists `no-store`; init, segmenten en VTT `private,
      max-age=3600`; `/file` zoals downloads nu (`private, max-age=0, must-revalidate`).
    - Elk antwoord onder `/stream/` krijgt `Cross-Origin-Resource-Policy: same-origin`, en nooit
      `Content-Disposition`.
    - De service worker slaat `/api/` al over (B28). Een test bevestigt dat ook `/api/v1/stream/`
      nooit in de cache van de PWA komt.
    - Een sid staat nooit in `localStorage`. Het profiel wel (geen geheim).
    - Geen CORS: alles is dezelfde origin.

### Voortgang

36. **Kijkvoortgang en synchroniseren via SSE.**
    - Tabel `playback_state`, sleutel `(user_id, profile, node_id)`. `profile` is in fase 5 altijd
      `''` (de gebruiker zelf). Zo kunnen profielen later zonder migratie van de sleutel (Vraag 6).
    - `PUT /api/v1/media/progress/{node_id}` met
      `{position_ms, duration_ms, event: "tick"|"pause"|"seek"|"stop"|"end", origin, seq,
      started_at, audio?, subtitle?}`. `origin` is een willekeurig id per tabblad of apparaat.
      `seq` telt per `origin` op. `started_at` is de servertijd uit het antwoord van `play`.
      - Elke 15 s tijdens het spelen, en meteen bij pauze, zoeken, stoppen, het einde en
        `visibilitychange` naar verborgen. Die laatste met `fetch(…, {keepalive: true})`: een beacon
        kan geen PUT met Bearer.
      - Ticks hooguit één per 5 s per gebruiker en bestand (429 `rate_limited`); de rest gaat altijd
        door.
    - **Eerst het event, dan de database.** De app houdt de laatste stand in het geheugen en
      publiceert meteen `playback.progress` naar de eigen verbindingen van de gebruiker:
      `{node_id, position_ms, duration_ms, watched, origin, updated_at}`. Schrijven gebeurt hooguit
      eens per 10 s per gebruiker, en meteen bij pauze, stoppen, einde en gezien. Een crash verliest
      dus hooguit 10 s voortgang. SQLite blijft rustig.
    - **Volgorde.** Een PUT kan laat komen: een `keepalive` van een tabblad dat net dichtging, of
      een tik van een ander apparaat. Daarom:
      - een PUT met een `(origin, seq)` die niet nieuwer is dan de laatste van die `origin`, telt
        niet (204, geen event);
      - een PUT van een sessie die begon vóór de laatste overgang naar gezien of het laatste
        `end` voor die node (`started_at` < `watched_at`), telt niet;
      - gezien gaat alleen weg door "Niet gezien" (expliciet), of door een nieuwe sessie (na
        `watched_at`) die verder komt dan 60 s.

      `playback_state` krijgt daarvoor `seq`, `play_started_at` en `watched_at` (§3.1). Tests in
      `test_media_progress.py` voor beide volgordes: een late tik na gezien, en een late tik van
      een ander apparaat.
    - Een apparaat negeert zijn eigen `origin`. Een ander apparaat werkt "Verder kijken" en de
      balkjes bij. Staat daar hetzelfde item gepauzeerd open, dan springt het naar de nieuwe
      positie. Doel: ≤ 2 s na de PUT.
    - **Gezien** als de positie ≥ 90 % is, of voorbij het begin van de aftiteling (Keuze 30), of
      minder dan 3 minuten van het eind (alleen bij items langer dan 10 minuten). Dan
      `watched = true`, `play_count + 1` (één keer per sessie) en positie 0.
    - Een vervangen bestand (`blob_sha` anders én de duur meer dan 5 % anders): positie terug naar 0.
    - **Verder kijken:** positie > 60 s, niet gezien, de nieuwste eerst, hooguit 20. De speler vraagt
      "Verder kijken vanaf 12:05?" (standaard ja na 5 s).
    - **Volgende aflevering:** na de laatst geziene aflevering van een serie de eerstvolgende
      ongeziene. Alleen voor series met activiteit in de laatste 90 dagen.
    - **Gezien markeren:** `POST /api/v1/media/progress/batch` met `{node_ids[≤1.000], watched}`, of
      `{show_id, season?, watched}`.
    - Alleen wat de gebruiker nu mag lezen, komt in de lijsten. Iets wat hij niet meer mag lezen:
      PUT geeft 404.

### Muziek

37. **Albums, artiesten en afspeellijsten.**
    - **Albums** (`media_album`): bij elkaar op (bibliotheek, albumartiest of "Diverse artiesten"
      bij een compilatie, albumtitel genormaliseerd). Zonder albumartiest: nummers in dezelfde map
      met dezelfde albumtag. Schijven (`disc`, mappen `CD1` en `CD2`) horen samen.
    - **Compilatie:** de tag `TCMP`/`cpil`, een albumartiest als "Various Artists" of "Diverse
      artiesten", of veel artiesten zonder albumartiest in één map.
    - **Artiesten:** geen tabel. Een lijst van `album_artist` (anders `artist`) met het aantal
      albums. Het beeld van een artiest is de hoes van een album.
    - **Sorteren** op de sorteertags (`TSOP`, `ALBUMARTISTSORT`), anders zonder lidwoord vooraan
      ("The", "De", "Het").
    - **Hoes:** eerst `cover.jpg` of `folder.jpg` in de map, dan de ingebedde (`frame` op het spoor
      `attached_pic`), dan de Cover Art Archive (Keuze 41).
    - **Afspeellijsten** (`media_playlist`, `media_playlist_item`): alleen van jezelf, hooguit 5.000
      nummers. Een nummer dat je niet meer mag lezen, wordt bij het lezen overgeslagen, niet
      weggegooid: misschien komt de deling terug. Delen bouwen we niet.
    - De wachtrij van wat nu speelt staat in de client (`sessionStorage`), niet op de server.
    - **De muziekbalk:** een kleine balk onderaan de shell (lazy, alleen als er muziek speelt):
      hoes, titel, artiest, spelen, vorige, volgende, tijdlijn, volume en de rij. Hij blijft spelen
      terwijl je door de app navigeert (één `<audio>` in de shell). Media Session voor de
      mediatoetsen en het vergrendelscherm.
38. **Gapless.**
    - Bij `play` van een nummer: mode `direct` (`<audio src>`), of mode `audio` voor de MSE-speler:
      - `GET /api/v1/stream/{sid}/audio.mp4`: het hele nummer als fMP4;
      - gemaakt met `remux_audio`: `-c:a copy -f mp4 -movflags
        +frag_keyframe+empty_moov+default_base_moof -frag_duration 10000000 -use_editlist 0`.
        (spike) `-c:a {copy | flac}`: `copy` voor FLAC en AAC, `flac` voor Opus, ALAC en WAV;
      - in de cache `audio/<sha>.mp4`, en geserveerd terwijl het groeit. Remuxen kost minder dan
        1 s per nummer. (spike) Behalve Opus naar FLAC: 1,8 s CPU voor een nummer van 4 minuten,
        en 23,5 MB tegen 2,65 MB in de cache (8,9 ×; S8). Met 20 s vooruit laden is de tijd geen
        probleem;
      - ALAC en WAV worden FLAC in fMP4 (verliesvrij);
      - (spike) **Opus ook.** Opus in fMP4 is in Chromium 141 niet gapless: +631 en +619 samples
        per overgang, de opvulling van het laatste frame, ondanks `appendWindowEnd`. Via FLAC is
        het exact, met delay 0 (S8). WebM gaf 1 ms overlap; dat is een terugval als de cache te
        groot wordt (een nieuw contract);
      - MP3 gaat rauw als `audio/mpeg`, want MSE kent MP3 in MP4 niet [gemeten].
    - Het antwoord geeft `gapless: {rate, delay, samples}`, uit `audio_info` (Keuze 18).
    - `-use_editlist 0`: de browser krijgt alle samples, ook de voorloop. Wij knippen zelf, en
      precies. Zo maakt het niet uit of een browser een edit list wel of niet toepast.
    - **De speler `music/gapless.ts`** gebruikt één MediaSource en één SourceBuffer voor de hele
      wachtrij. Voor nummer i:
      - `timestampOffset = T_i − delay_i/rate`, met `T_i` = de som van `samples_j/rate`;
      - `appendWindowStart = T_i` en `appendWindowEnd = T_i + samples_i/rate`;
      - `changeType()` als de codec wisselt;
      - MP3 als `audio/mpeg` draagt geen tijden in de stroom. Daar staat de buffer op
        `mode = "sequence"` [doc: MSE byte stream formats]; S8 legt de precieze `delay` vast.
        (spike) delay = `enc_delay` (576 bij LAME), zonder de 529 van de decoder: exact in
        Chromium 141 (S8);
      - het volgende nummer alvast laden als er 20 s over is. Alleen het huidige en het volgende
        staan in de buffer.
    - Een ander nummer kiezen of ver zoeken: een nieuwe MediaSource.
    - Terugval zonder MSE of MMS, of als MSE de codec niet kent: `<audio>` per nummer, met het
      volgende alvast geladen. Dan is er een gat van tientallen ms, en de speler zegt het.
    - **Eerlijk:**
      - precies voor FLAC en Opus (spike: Opus via FLAC, S8), en voor AAC met `iTunSMPB`;
      - AAC uit ffmpeg zonder `iTunSMPB` houdt een gat van 15,6 ms (688 samples), want het aantal
        echte samples staat nergens [gemeten];
      - MP3 via MSE met de LAME-header: [niet getest] (S8). De test is de waarheid, niet deze tekst.
        (spike) Exact in Chromium 141 met delay = `enc_delay` (S8); Firefox en Safari zijn niet
        getest.

### Metadata online

39. **De adapter, en één weg naar buiten.**
    - `app/media/providers/base.py`: `Protocol MetadataProvider` met `search`, `details`, `season`,
      `images` en `check` (voor de knop "Testen"). Eigen dataclasses: niets van het JSON van een
      provider komt buiten zijn eigen module. Een nieuwe provider is één bestand plus één regel.
    - Twee implementaties: `tmdb.py`, en `musicbrainz.py` met `caa.py`.
    - **Online staat per bibliotheek aan of uit** (`media_library.online`). Het kan alleen aan als
      er een sleutel is. Bij een nieuwe bibliotheek van films of series staat het dan aan; bij
      muziek en eigen video's uit.
    - **`app/media/providers/http.py` is de enige uitgaande client:**
      - één httpx-client, `trust_env=False`;
      - een token bucket per host: TMDB 10 per seconde, MusicBrainz 1, CAA 5;
      - de User-Agent `alles-in-1-platform/<versie> ( https://github.com/Jonasz1996/alles-in-1-file-office-media-platform )`.
        Nooit een e-mailadres;
      - grenzen: JSON hooguit 2 MB, een plaatje hooguit 10 MB, timeout 10 s verbinden en 20 s in
        totaal;
      - alleen https naar de hosts uit de instellingen. Omleidingen alleen naar een lijst
        (`coverartarchive.org`, `archive.org`, `*.archive.org`), hooguit 3;
      - nooit een privé-, loopback-, link-local- of ULA-adres, en de controle zit in het
        verbinden zelf. Een losse controle vóór het verbinden helpt niet: httpx zoekt de naam
        dan zelf nog eens op, en kan een ander antwoord krijgen. Daarom:
        - de client zoekt de naam één keer op (`getaddrinfo` in een thread), controleert elk adres
          met `ipaddress`, en verbindt met dat adres;
        - het verzoek gaat naar `https://<ip>/…` met de kop `Host: <naam>` en de extensie
          `sni_hostname` van httpcore, zodat TLS de naam controleert (httpx 0.28.1 en httpcore
          1.0.9 hebben die extensie; geen nieuwe dependency);
        - bij elke omleiding opnieuw, voor de nieuwe host;
        - een test met een nep-resolver die de tweede keer `127.0.0.1` geeft.

        Alleen de tests zetten `PLATFORM_MEDIA_PROVIDER_PRIVATE_OK`;
      - 429 en 503: `Retry-After` volgen, met een plafond van 60 s;
      - een stroomonderbreker per provider: na 5 fouten op rij 5 minuten dicht, dan één proef;
      - de log toont alleen host, pad zonder query, status en tijd. Nooit een sleutel.
    - Alle verzoeken naar buiten draaien als jobs in de rij `media_net` (`media.lookup`, `media.art`),
      nooit in een verzoek van een gebruiker. Uitzondering: zoeken in het koppelvenster
      (Keuze 43), dat hooguit 8 s wacht.
    - **Offline:** alles werkt uit lokale gegevens. De jobs wachten (`Defer`) en proberen het
      later. Pas na 24 uur fouten: de melding `media_provider_failing`.
40. **TMDB: elke installatie brengt haar eigen sleutel.**
    - Jonas maakt een gratis account op TMDB (niet-commercieel gebruik) en vult het "API Read
      Access Token" in op `/beheer` → Media → Providers (alleen beheerders). Het gaat als
      `Authorization: Bearer` en staat dus nooit in een adres. Er staat nooit een sleutel in het
      repo.
    - Bij opslaan test de app hem eerst (`GET /3/configuration`). Fout: 422 `media_provider_auth`,
      en hij wordt niet bewaard. Later een 401: dezelfde melding op `/beheer`, en de provider gaat
      dicht tot een nieuwe sleutel.
    - Opslag: versleuteld met AES-GCM in `Setting "media_secrets"`. De sleutel daarvoor is
      `HKDF-SHA256(app-geheim, info="platform media secrets v1")` (`cryptography`, al een
      afhankelijkheid). Dat beschermt tegen een gelekte kopie van `platform.db` zonder het
      app-geheim; tegen iemand met de hele `/data` niet. Dat zegt de README zo.
    - Nooit terug in een antwoord (alleen `{"set": true, "set_at", "ok"}`), nooit in een log, en
      niet in de foutmeldingen van httpx.
    - Eindpunten [doc]: `/3/configuration`, `/3/search/movie`, `/3/search/tv`,
      `/3/movie/{id}?append_to_response=external_ids`, `/3/tv/{id}`, `/3/tv/{id}/season/{n}`,
      `/3/find/{imdb_id}?external_source=imdb_id`. Taal uit de bibliotheek (`nl-NL`); een lege
      samenvatting haalt de app nog één keer in `en-US`.
    - Plaatjes van `image.tmdb.org`: `w500` (poster), `w780`, `w1280` (achtergrond), `w300`
      (still). Nooit `original`.
    - **Bronvermelding** (eis van TMDB [doc]): de zin "This product uses the TMDB API but is not
      endorsed or certified by TMDB." met een link, op `/beheer` en in het infopaneel ("Gegevens:
      TMDB"). Het logo alleen als de voorwaarden dat toestaan [niet getest].
41. **MusicBrainz en de Cover Art Archive.**
    - **Standaard uit.** Het stuurt album- en artiestnamen naar een derde. Aan te zetten op
      `/beheer`.
    - Geen sleutel nodig. Wel 1 verzoek per seconde en een User-Agent met contact [doc]. Als contact
      staat de URL van het repo erin.
    - Met een MBID uit de tags (Picard): `GET /ws/2/release/{mbid}?inc=release-groups+artist-credits&fmt=json`.
      Anders zoeken: `GET /ws/2/release/?query=release:"…" AND artist:"…"&fmt=json&limit=5`.
    - De hoes: `GET {CAA}/release/{mbid}/front-500`. Dat geeft een 307 naar archive.org; die volgen
      we via de lijst van Keuze 39.
    - Een verzameling van 1.000 albums kost zo ±50 minuten aan verzoeken. Dat draait op de
      achtergrond en mag lang duren.
42. **Koppelen** (`app/media/providers/match.py`, pure functies).
    - Eerst id's: een id uit de naam of de NFO (`{tmdb-278}`, `[imdbid-tt0111161]`, `uniqueid`)
      geeft een directe lookup (`/find` of `/movie/{id}`).
    - Anders zoeken op titel en jaar. Score per kandidaat:
      - gelijkenis van de genormaliseerde titel met `title` en `original_title` (kleine letters,
        geen accenten, geen lidwoord vooraan, geen leestekens; `difflib.SequenceMatcher` uit de
        stdlib);
      - jaar gelijk +0,2, één jaar verschil +0,1, anders −0,3, geen jaar 0;
      - populariteit alleen als scheidsrechter (hooguit 0,02).
    - Automatisch bij een score ≥ 0,85 en ≥ 0,1 boven de tweede. Anders `match_state = "review"`:
      de lijst "Nakijken", met de 5 beste kandidaten bewaard.
    - Series: koppelen op de serie, daarna per seizoen de afleveringen (`/tv/{id}/season/{n}`), één
      keer per seizoen.
    - Muziek: album + albumartiest + aantal nummers; elk nummer verschil kost 0,1.
43. **Handmatig koppelen en corrigeren.** Alleen wie het bestand mag bewerken (eigenaar, of een
    deling met schrijven).
    - `GET /api/v1/media/items/{node_id}/candidates?q=&year=` geeft kandidaten: titel, jaar,
      originele titel, het begin van de samenvatting, en een poster via ons eigen adres. De app
      haalt die posters zelf op, in een tijdelijke map (`media/art-tmp`, na 24 uur weg). De
      browser praat nooit met TMDB.
    - `POST /api/v1/media/items/{node_id}/match` met `{provider: "tmdb", id: "movie:603"}` haalt de
      details op. Velden in `locked` blijven. `match_state = "manual"`; een scan verandert de
      koppeling daarna niet meer.
    - `PATCH /api/v1/media/items/{node_id}` met `{title, sort_title, year, overview, season,
      episode}` zet de velden en zet ze in `locked`. `{"unlock": ["title"]}` geeft een veld terug.
    - `DELETE /api/v1/media/items/{node_id}/match`: terug naar alleen lokaal; `locked` leeg.
    - Series koppel je op het niveau van de serie: `POST /api/v1/media/shows/{id}/match`.
    - Elke wijziging stuurt `media.item` naar wie de bibliotheek ziet.
44. **Plaatjes van providers.**
    - De app haalt ze op via `http.py`: alleen `image/jpeg`, `image/png` of `image/webp`, hooguit
      10 MB.
    - Het renderproces van de previews (`python -I`, kale omgeving, een geheugengrens) decodeert ze
      en maakt WebP in 256, 512 en 1.280 px. Zo wordt elk plaatje opnieuw gecodeerd; vreemde
      inhoud overleeft dat niet.
    - Bewaard als `art/<sha>/<maat>.webp`, op de sha van het bronplaatje. Getoond via
      `GET /api/v1/media/art/{sha}/{maat}.webp?exp=…&sig=…` (12 uur geldig).
    - De CSP blijft `img-src 'self' data: blob:`. Er komt geen externe host bij.
    - Eens per dag gooit de housekeeping plaatjes weg waar geen item, serie of album meer naar
      verwijst.

### Beheer, fouten en uitrol

45. **Het tabblad Media op `/beheer` (Alt+8).**
    - `GET /api/v1/admin/media` geeft:
      - de worker: modus (`container` of `lokaal`), staat, sinds wanneer, de uitkomst van de
        zelftest (Keuze 11), en de plaatsen per soort in gebruik (Keuze 12);
      - ffmpeg: versie, encoders, filters, wat ontbreekt, hwaccel, GPU;
      - de benchmark: factor en datum;
      - het budget: gebruikt en totaal, en of het tijdelijk `B − 1,0` is omdat DS start
        (Keuze 26);
      - de lopende sessies: gebruiker, titel, mode, trede, snelheid, voorsprong, sinds. Met een knop
        Stop per sessie;
      - de redenen van de afgelopen 7 dagen (Keuze 19);
      - de achtergrondbaan: wachtend, bezig, mislukt. Met een knop "Mislukte opnieuw";
      - de bibliotheken: eigenaar, map, soort, aantal, laatste scan, fouten, "Weinig zeker", en
        "Opnieuw scannen";
      - de cache per soort, en wat er vrij is. Met een knop Opruimen;
      - de providers: sleutel gezet, laatste goed, laatste fout, de stroomonderbreker, "Testen";
      - de import: bronmap, doelmap, eigenaar, kopiëren of verplaatsen, en de voortgang live;
      - `MediaConfig`.
    - Eindpunten:
      - `PUT /api/v1/admin/media/config`;
      - `POST /api/v1/admin/media/bench` (202, of 409 `media_bench_busy`);
      - `DELETE /api/v1/admin/media/sessions/{id}`;
      - `POST /api/v1/admin/media/retry`;
      - `POST /api/v1/admin/media/cache/clear` met `{what: "sessions"|"audio"|"trick"}`;
      - `PUT` en `DELETE /api/v1/admin/media/tmdb`, en `POST /api/v1/admin/media/tmdb/test`;
      - `GET /api/v1/admin/media/import/sources` en `POST /api/v1/admin/media/import` (202);
      - `POST` en `GET /api/v1/admin/media/loadtest` (§4.6).
    - `MediaConfig` in `Setting "media"`:
      - `enabled` (true);
      - `cpu_budget` (3,0; 0,5–16);
      - `max_transcodes` (3) en `max_user_transcodes` (2);
      - `public_transcode` (false);
      - `hdr_1080` (false: HDR live alleen op 720 tenzij aan);
      - `cache_gb` (20);
      - `musicbrainz` (false);
      - `bench` (alleen lezen).
    - Meldingen, via de bestaande haak in `admin.py`: `media_worker_down`, `media_ffmpeg_missing`,
      `media_cache_full`, `media_sandbox_off`, `media_provider_auth`, `media_provider_failing`.
46. **Als ffmpeg ontbreekt of een transcode faalt.**
    - **ffmpeg ontbreekt of `caps` faalt:** de staat `ffmpeg_missing` en de melding. Direct play
      werkt door, zoals bij een worker die weg is (Keuze 13). De speler zegt eerlijk: "Dit bestand
      kan de browser niet afspelen, en de server kan het nu niet omzetten." Met de knop
      "Downloaden".
    - **De probe faalt:** het item krijgt de staat `failed` met de reden, en een badge "Kan niet
      afspelen".
    - **ffmpeg stopt met een fout, of de waakhond "vast" grijpt in** (Keuze 26):
      1. de app herstart de producent één keer, op hetzelfde segment;
      2. faalt dat ook, dan krijgt dat segment 502 `media_transcode_failed`;
      3. de speler toont "Opnieuw" (een nieuwe sessie op de positie) en "Lagere kwaliteit".

      De eerste 2.000 tekens van stderr gaan in het log. Er staan geen paden in.
    - **Een segment komt niet binnen 20 s:** 503 `media_timeout`. Na 3 keer achter elkaar gaat de
      sessie dicht.
    - **De worker herstart** (crash, update): alle producenten stoppen. De sessies houden hun cache.
      Het volgende verzoek start een nieuwe producent zodra de worker er weer is.
47. **Uitrol en bijwerken: hetzelfde ene commando.**
    - Nieuw in `infra/single/`: `compose.media.yaml` en `compose.media-limits.yaml` (§2 M1 en M10).
    - Het Dockerfile krijgt drie regels (Keuze 11):
      - `RUN useradd --system --uid 10002 --no-create-home media`;
      - `RUN install -d -o media -g platform -m 2750 /run/media`;
      - `RUN chmod 0711 /usr/bin/ffmpeg /usr/bin/ffprobe` (na de `apt-get install`).

      In `python:3.12-slim` (Debian 13) geeft `useradd --system` de groep `platform` het gid 999,
      niet 10001 [gemeten]. Daarom noemt het Dockerfile de groep bij naam. De app-container blijft
      `user: platform`; alleen de worker is `user: media`.
    - `compose.media.yaml` zet ook `stop_grace_period: 30s` voor `platform` (F3).
    - `install.sh` volgt het patroon van office (§2 M10):
      - `MEDIA_SANDBOX` (`aan` of `uit`) uit de omgeving wint van `.env`;
      - `decide_media` breekt nooit af;
      - `compose_files` neemt de media-bestanden mee;
      - de mappen: `install -d -m 750 -o 10001 -g 10001 "$DATA_ROOT"/{media,import}`;
      - eerst de app (`up -d --build --remove-orphans platform` en `wait_health`), dan `media`
        (`up -d --no-deps media`, hetzelfde image met `pull_policy: never`), dan office. Zo zit
        media nooit in de weg van de app;
      - het oude app-image (`old_app`) gaat pas weg na `media_start` en office. Nu haalt het
        script het weg direct na `up platform` (`install.sh:443–447`); dan draait de oude
        media-container er nog op, faalt het weghalen stil, en blijft bij elke update een heel
        image achter op de rootdisk. `test_install_media.sh` eist na twee updates één image
        `platform:local`, en dat de oude id weg is.
    - Na de start: `app.cli media-status --wait 20`. Dat print bijvoorbeeld "Media: aan
      (afgeschermd, ffmpeg 7.1.5, GPU: geen)", of een waarschuwing. Het stopt het script nooit.
    - Vóór het vervangen: `app.cli media-status --sessions` waarschuwt als er iemand kijkt. Die
      streams staan tot ±60 s stil. De speler blijft het proberen zolang hij buffer heeft plus
      120 s, en loopt daarna zelf door (Keuze 32).
    - **Terug naar 0.6.0,** in de volgorde die fase 4 al leerde (`docs/runbooks/office.md`):
      0. controleren dat de commit van 0.6.0 er is, vóór er iets stopt:
         `git -C /opt/platform/src fetch -q origin` en dan
         `git -C /opt/platform/src cat-file -e '263abd74d914a9811901e7570ed410866364f4b5^{commit}'`.
         Faalt dat, dan hier stoppen. Office blijft aan: 0.6.0 kent office;
      1. de app en de worker stoppen, zodat niets meer naar de mediatabellen schrijft:
         `dc stop platform media` (`dc` is `docker compose` met de bestanden uit `.env`);
      2. het schema terug, met het image van nu:
         `dc run --rm --no-deps platform python -m app.cli db-downgrade 0007`;
      3. het script van nu, met een vaste commit:
         `curl -fsSL …/install.sh | PLATFORM_SRC_REF=263abd74d914a9811901e7570ed410866364f4b5 bash`.
         Zonder die commit haalt het script `main`, en die migreert bij de start weer naar 0008.
         `media_supported` ziet in 0.6.0 geen `compose.media.yaml`, kiest `MEDIA_ON=0`, en
         `--remove-orphans` haalt de worker weg.

      Bij de integratie komt er een tag `v0.6.0` op die commit, zodat het runbook een naam kan
      gebruiken. Het volume `media-run` en de mappen `media/` en `import/` blijven staan en doen
      geen kwaad. Stap 4 van de installatietest (§2 M10) draait precies deze commando's, en
      controleert na de start dat de schemaversie 0007 is en de app 0.6.0.
    - De migratie 0008 maakt alleen nieuwe tabellen. Er is geen backfill.
    - Tijdens een update kan de app even een oudere worker treffen. `caps` geeft `PROTOCOL`. Bij een
      verschil is de worker `down` (reden: versie) tot de nieuwe draait. Direct play werkt intussen
      door.
48. **Cluster later: ontworpen om te groeien, niet gebouwd.**
    - De sid begint met de naam van de node. Een router kan `/api/v1/stream/<node>.*` later naar die
      node sturen, of 410 geven als die node weg is.
    - Valt een node uit, dan krijgt de speler 410 en vraagt hij een nieuwe sessie. Die landt op een
      andere node. Dat is "hervatten op een andere node" uit het stappenplan.
    - Voortgang, probes, items en art staan in de database en de blobstore, dus gedeeld. Segmenten
      zijn lokaal per node, en dat mag.
    - Toelating, worker en cache zijn per node. De beslissing kiest later ook een node.
    - De SSE-hub zit in het geheugen. Het cluster vraagt een broker (bijvoorbeeld `LISTEN/NOTIFY` op
      Postgres). `playback.progress` verandert daar niet voor.
    - Scannen moet later op één node (een leider). Niet nu.
49. **De apps van fase 9 zonder ombouw.**
    - Het profiel (Keuze 20) staat in `docs/fase-5/clientprofiel.md`, met een versienummer. Fase 9
      voegt alleen basisprofielen toe.
    - Alle afspeeladressen werken zonder header en zonder cookie. ExoPlayer, de `<video>` van webOS
      en WebView2 kunnen ze meteen gebruiken.
    - De playlists volgen RFC 8216 en de regels van Apple en LG [doc], en een eigen lint test ze
      (§3.6).
    - Voortgang en "Verder kijken" zijn gewone REST en SSE.
    - Offline downloads van een omgezette versie komen later (fase 9).
50. **Wat we schrappen, en waarom.**

    | Geschrapt | Waarom | Gevolg |
    |---|---|---|
    | Hardwareversnelling (VAAPI, QSV) | B5 open, geen `/dev/dri`, niet te testen | klaargezet (Keuze 28); 1080 + 720 + 720 in plaats van 3 × 1080 |
    | TVDB, AcoustID, fanart.tv, OpenSubtitles | betaald, een sleutel, of een nieuwe afhankelijkheid (chromaprint) | TMDB en MusicBrainz volstaan; een nieuwe provider is later één bestand |
    | Profielen en kinderslot in de UI | ontwerp van PIN en rechten; weinig winst | per account; de kolom `profile` staat klaar (Vraag 6) |
    | Externe mappen | tweede opslagmodel | fase 7; de import dekt een bestaande verzameling |
    | Fotobibliotheek | de tijdlijn bestaat al | — |
    | Automatische ABR, adviezen "hoger" en "lager" | complex; één trede per sessie volstaat | `auto` bij de start, één stap omlaag na haperen |
    | Aparte audio-rendities met een eigen audio-fragmentschrijver | veel code voor een zeldzame wissel | ander audiospoor = nieuwe sessie (0,3–1,5 s) |
    | Segmenten van 6 s bij transcode, en halve segmenten als stroom | haperen als de CPU druk is (J3) | overal 2 s |
    | Parallelle tredes | kost CPU die er niet is | — |
    | Een cache gedeeld tussen sessies (per plan) | zeldzaam nut, tabel en LRU | cache per sessie |
    | Een worker over http op een intern netwerk; alle blobs alleen-lezen in de worker | bereikt de host (fase 4, M7); een lek leest elke film | fd over een Unix-socket, geen netwerk (Keuze 11) |
    | I-frame-playlists, ondertitels als HLS-rendities, gesegmenteerde VTT | `<track>` volstaat; scrubben via sprites | — |
    | Tekst inbranden (libass) | lettertypen en fontconfig in de worker | tekst als `<track>`; alleen beeld inbranden |
    | Alvast transcoderen | schijf en CPU voor iets wat misschien nooit speelt | — |
    | HDR doorgeven via transcode, DV profiel 5, 4K HDR live | x265 10-bit en libplacebo zijn te zwaar | HDR alleen direct of remux |
    | AV1 coderen | kosten niet gemeten, VP9 dekt de tests | — |
    | ASS-stijlen volledig | grote renderer | positie, `<b>`, `<i>`, `<u>` en eenvoudige kleur |
    | Casten (Chromecast, DLNA) | eigen protocol, eigen SDK | AirPlay vanuit Safari werkt op `<video>` (niet met MMS) |
    | Meerdere versies van één film | UI en beslissing worden complex | twee bestanden zijn twee items |
    | Songteksten, ReplayGain, intro overslaan | niet in de doelen | — |
    | Afspeellijsten delen | rechtenmodel | alleen van jezelf |
    | Sprites en achtergrondwerk buiten bibliotheken | CPU en schijf | wel afspelen, met ondertitels op verzoek |
    | Een tabel voor de "vuile" nodes | de ronde bij de start vangt wat een crash verloor | een set in het geheugen |
    | Voorkeuren per gebruiker (talen, ondertitelmodus) | klein nut nu | de taal van het audiospoor en "geforceerd" volstaan |
    | De volledige belastingtest in CI | gedeelde runner, wisselende snelheid | met de hand, in de sandbox en op de LXC (§4.6) |
    | Losse beeldondertitels (`.sup`, `.sub` met `.idx`) | een tweede invoer-fd en extra demuxers in de whitelist | ingebedde PGS en VobSub werken; losse in `names-open.tsv` |
    | Een kolom `priority` in `job` | een migratie van een bestaande tabel | eigen rijen per soort werk (Keuze 14) |

### Vragen voor Jonas

1. **Staan er al films, series of muziek op `/tank`, en waar?** Standaard: **we bouwen de
   import, en hij kopieert** (Keuze 2). De bron blijft dan staan. Daarvoor is tijdelijk twee keer
   de ruimte nodig. Staat de bron onder `/tank/platform/import` (hetzelfde dataset), dan kan ook
   "verplaatsen": geen extra ruimte. Staan de bestanden op een ander dataset en moeten ze daar
   blijven, zonder kopie? Dan is dat de map rechtstreeks van fase 7 (Keuze 3), en schuift dat deel
   naar voren. Let op: de LXC is unprivileged. Voor verplaatsen moet jij op de Proxmox-host
   `chown -R 110001:110001 /tank/platform/import` doen. Voor kopiëren is lezen genoeg.
2. **Welke CPU en is er een GPU (B5)?** Standaard: **alleen de CPU**. Het platform meet zichzelf
   (`bench`). Graag de uitvoer van `lscpu` en `ls -l /dev/dri` op de Proxmox-host. Een iGPU kan
   later (±2–3 dagen). Dan moet jij `/dev/dri` aan de LXC geven. Dat is nooit een eis.
3. **"3 gelijktijdige transcodes": drie op gemengde kwaliteit, of hooguit twee op 1080p?**
   Standaard: **drie**. De eerste kijker krijgt 1080p, de tweede en derde 720p superfast, zodat de
   derde altijd nog past (Keuze 26). Direct play en remux tellen niet mee, en dat is de normale
   weg. HDR telt zwaarder: één HDR-film op 1080p is ongeveer twee gewone. Een 4K-bron telt dubbel:
   dan passen er twee. Het andere antwoord: hooguit twee, allebei 1080p, en de derde krijgt
   "probeer het straks". De belastingtest op de LXC (§4.6) laat zien wat jouw CPU echt haalt.
4. **Heb je een TMDB-sleutel (gratis account, niet-commercieel)?** Standaard: **geen, alleen
   lokale metadata**: titels uit namen en NFO, posters uit de map of een beeld uit de film. Met een
   sleutel komen er posters en Nederlandse samenvattingen bij, en gaan titels van je films naar
   TMDB. MusicBrainz blijft uit tot je het aanzet.
5. **Mag een publieke link transcoderen?** Standaard: **nee**. Alleen direct en remux. Een bezoeker
   zonder account kost dan geen CPU. Bij ja: hooguit 720p, 1 per link, en nooit de laatste plek.
6. **Profielen onder één account (bijvoorbeeld voor kinderen)?** Standaard: **nee, voortgang per
   account**. Kinderen krijgen een eigen account of gastaccount. De kolom `profile` staat klaar.
   Profielen kosten ±2–3 dagen, en een ontwerp voor PIN en rechten.

---

## 2. Pakketten

### 2.0 Overzicht, eigendom en regels

| Pakket | Naam | Grootte | Draait |
|---|---|---|---|
| **M0** | Fundament en spike (§3) | L | **eerst, alleen.** Werkers: M0-api, M0-web, daarna M0-e2e. De spike draait vanaf de eerste commit als CI-job `media-spike` op `pull_request`, in het echte image |
| M1 | Worker en isolatie | M | parallel |
| M2 | Bibliotheek, scanner, namen en import | L | parallel |
| M3 | Afspelen: profiel, beslissing, sessies en toegang | L | parallel |
| M4 | Stromen: producent, knipper, playlists, toelating en cache | L | parallel (het langste pakket) |
| M5 | Ondertitels, sprites en hoofdstukken | M | parallel |
| M6 | Speler (web) | L | parallel, eerst tegen de nep-stream van M0 |
| M7 | Voortgang | S | parallel |
| M8 | Metadata online | M | parallel |
| M9 | Muziek en gapless | M | parallel |
| M10 | Beheer en uitrol | M | parallel |

Ontwerp A had afspelen op de server als één pakket. Dat is te groot voor één werker in de golf.
Daarom staan de beslissing en de toegang (M3), de stroom zelf (M4) en de ondertitels en sprites
(M5) apart. Ze praten alleen via de contracten van §3.3.

Elk pakket werkt in een eigen git-worktree vanaf de tag `fase5-basis` (na M0). "Frozen" betekent:
daarna verandert alleen de *M0-hotfix-lane* het, met een kleine PR op de basis waar iedereen op
rebaset.

**API** (paden onder `api/`)

| Bestand | Eigenaar |
|---|---|
| `app/models.py` (de tabellen van §3.1), `migrations/versions/0008_media.py`, `app/config.py`, `app/serve.py` (F3), `app/main.py` (routers, lifespan, CSP, `_hide`), `app/jobs.py` (rijen, `timeout`, `Defer`), `app/cli.py` (de commando's als dunne aanroep), `app/routers/files.py` (F1, F2, de haak in `notify`), `app/routers/public.py` (F1, F2), `app/routers/auth.py` (de haken `close_user` en `close_login`), `app/previews.py` (alleen de haak `on_discard`), `app/usage.py` en `app/routers/admin.py` (alleen de haken), `app/media/{__init__,wire,client,state,runtime,types,errors,hooks,analysis}.py`, `app/media/worker/{__main__,server,run,args}.py`, `app/media/worker/ops/{caps,probe}.py`, `app/media/providers/base.py`, de stubs (§3.3), `tests/media/{fixtures,sup,fake_worker,worker_contract,record,fake_stream,media_contract,hls_lint}.py`, `tests/media/recorded/`, `tests/media/fake_providers/` (de kern), tests `test_migration_0008.py`, `test_media_wire.py`, `test_media_worker_core.py`, `test_media_probe.py`, `test_fake_worker.py`, `test_download_pool.py`, `test_download_head.py`, `test_jobs_lanes.py`, `test_media_routes_guarded.py`, `test_media_logs.py` | M0 (frozen) |
| `app/media/worker/ops/{frame,meta,bench}.py`, `app/media/worker/{guard,selftest}.py`, `app/media/local.py`, `app/media/health.py`, `app/render.py` (de tak video), `app/render_meta.py` (de tak video), `app/previews.py` (alleen de tak video), tests `test_media_worker_guard.py`, `test_media_no_ffmpeg_outside.py`, `test_previews_video_worker.py`, `test_media_local.py`, `test_media_health.py` | M1 |
| `app/media/{names,classify,scanner,library,items,importer}.py`, `app/media/worker/ops/nfo.py`, `app/routers/media_library.py`, `tests/media/{names,names-open}.tsv`, tests `test_media_names.py`, `test_media_classify.py`, `test_media_scanner.py`, `test_media_library_api.py`, `test_media_nfo.py`, `test_media_import.py` | M2 |
| `app/media/{decide,profile,codecs,sessions,public}.py`, `app/media/profiles/*.json`, `app/routers/{media_play,stream_file,public_media}.py`, tests `test_media_decide.py`, `test_media_profile.py`, `test_media_codecs.py`, `test_media_sessions.py`, `test_media_public.py`, `test_media_direct.py` | M3 |
| `app/media/{ladder,admission,producer,fmp4,grid,hls,cache,streams}.py`, `app/media/worker/ops/{stream,keyframes}.py`, `app/media/worker/containers.py`, `app/routers/stream.py`, tests `test_media_ladder.py`, `test_media_admission.py`, `test_media_producer.py`, `test_media_fmp4.py`, `test_media_grid.py`, `test_media_restart.py`, `test_media_hls.py`, `test_media_cache.py`, `test_media_containers.py`, `test_media_contract.py` | M4 |
| `app/media/{subconv,subtitles,trick,chapters}.py`, `app/media/worker/ops/{subs,sprites}.py`, `app/routers/media_subs.py`, tests `test_media_subconv.py`, `test_media_subtitles.py`, `test_media_trick.py`, `test_media_chapters.py` | M5 |
| `app/media/progress.py`, `app/routers/media_progress.py`, tests `test_media_progress.py`, `test_media_progress_api.py` | M7 |
| `app/media/providers/{http,tmdb,musicbrainz,caa,match,secrets,art}.py`, `app/media/lookup.py`, `app/routers/media_match.py`, `tests/media/fake_providers/**` behalve de kern, tests `test_media_http.py`, `test_media_providers_contract.py`, `test_media_match.py`, `test_media_secrets.py`, `test_media_art.py`, `test_media_lookup.py` | M8 |
| `app/media/{music,playlists}.py`, `app/media/worker/ops/{audio_info,remux_audio}.py`, `app/routers/{media_music,stream_audio}.py`, tests `test_media_music.py`, `test_media_audio_info.py`, `test_media_playlists.py`, `test_media_stream_audio.py` | M9 |
| `app/media/{admin,monitor,status,loadtest}.py`, `app/media/worker/ops/synth.py`, `app/routers/admin_media.py`, `app/usage.py` (het deel `media`), tests `test_admin_media.py`, `test_media_monitor.py`, `test_media_status.py`, `test_media_loadtest.py`, `test_media_synth.py` | M10 |

M0 zet elke module van een ander pakket als stub met de signaturen van §3.3, zodat alles compileert
en de app zonder media precies 0.6.0 is, plus de reparaties F1–F4.

*Noot bij de bouw van M0:* `MediaConfig` en `BenchResult` staan in `app/config.py`
(`media/admin.py` geeft `MediaConfig` door), en `media/local.py` en `media/health.py` van M1
bestaan al als kleine versie, zodat de kern zonder Docker werkt ([m0.md](m0.md), afwijking 2
en 17).

**Web** (paden onder `web/src/`)

| Bestand | Eigenaar |
|---|---|
| `routes.tsx` (lazy routes `/media/*`, `/kijken/:node`, `/muziek/*`), `App.tsx` (menu-items Media en Muziek, een plek voor de muziekbalk), `browser/actions.ts` (haak "Als bibliotheek gebruiken"), `viewer/MediaView.tsx` (haak: laadt de speler), `Beheer.tsx` (achtste tabblad), `pwa/routing.ts` (alleen de test), `media/{types,api,index}.ts`, `i18n/errors/media.ts`, `i18n/types.ts` (gebieden `media`, `player`, `music`), het blok `media` in `i18n/areas/{browser,viewer,admin}.{nl,en}.ts`, `../scripts/budget.mjs` (regels `mediaRoute`, `player`, `music`), `../vite.config.ts` | M0 (frozen) |
| `media/{LibraryHome,Library,ItemPage,ShowPage,NewLibraryDialog}.tsx`, `media/{grid,library}.ts`, `media/media.css`, `media/*.test.ts`, `i18n/areas/media.*` | M2 |
| `player/**` behalve `player/progress.ts`, `i18n/areas/player.*` | M6 |
| `player/progress.ts`, `media/rows/{ContinueRow,NextUpRow}.tsx`, `media/progressSync.ts`, tests | M7 |
| `media/match/{MatchDialog,EditDialog}.tsx`, `media/match/match.ts`, tests | M8 |
| `music/**`, `i18n/areas/music.*` | M9 |
| `admin/MediaTab.tsx`, `admin/media.test.ts`, het blok `media` in `i18n/areas/admin.*` voorbij de haak | M10 |

**Repo**

| Bestand | Eigenaar |
|---|---|
| `e2e/media_harness.py`, `e2e/conftest.py` (fixtures `media`, `fake_providers`, `ffmpeg_count`), `e2e/pytest.ini` (markers `media`, `media_perf`, `provider_real`), `docs/fase-5/{spike,e2e}.md`, `.github/workflows/ci.yml` (de jobs `media-spike`, `api-media`, `e2e-media`, `e2e-media-perf` en `media-image`, de markers in `e2e`, en `WEB_CSP` in de job `images`), `web/nginx.conf` (`media-src` in alle 5 CSP-koppen, F4), `api/tests/test_csp_nginx.py` | M0 (frozen) |
| `Dockerfile` (de gebruiker `media`, de map `/run/media`, `chmod 0711` van ffmpeg en ffprobe), `infra/single/compose.media.yaml`, `infra/single/compose.media-limits.yaml`, `infra/single/tests/test_media_container.sh` | M1 |
| `docs/fase-5/clientprofiel.md` | M3 |
| `docs/fase-5/provider-shapes.json` | M8 |
| `infra/single/install.sh`, `infra/single/compose.yaml` (alleen `PLATFORM_MAX_UPLOAD_BYTES`), `infra/single/.env.example`, `infra/single/README.md`, `infra/single/tests/test_install_media.sh`, `docs/runbooks/media.md`, `.github/workflows/ci.yml` (alleen de job `infra`) | M10 |
| `e2e/test_media_<pkg>*.py`, `docs/fase-5/<pkg>.md`, `docs/fase-5/toetsenbord-<pkg>.md` | elk pakket zijn eigen |

**Regels** (dezelfde als in fase 4, plus):

1. Alleen eigen bestanden wijzigen; de rest alleen importeren.
2. Geen migraties en geen `models.py` buiten M0. Een schemawens gaat naar de hotfix-lane.
3. **Geen nieuwe pakketten** in `pyproject.toml` of `package.json`, ook geen dev-deps.
4. **Geen ffmpeg of ffprobe buiten `app/media/worker/`.** `test_media_no_ffmpeg_outside.py`
   controleert het (M1). Vanaf M1 geldt het ook voor `render.py`. (spike) De regel gaat over
   `app/`: de spike-scripts en de fixtures onder `api/tests/` roepen ffmpeg zelf aan.
5. **Bytes van gebruikersbestanden ontleden gebeurt alleen in de worker.** Ook de
   codec-strings, de keyframe-index, NFO, ondertitels en gapless-koppen. De app leest alleen wat de
   worker teruggeeft (fMP4-boxen, VTT, JPEG, JSON), en altijd met een grens. De import en direct
   play lezen bytes zonder ze te ontleden.
6. **Elk eindpunt onder `/api/v1/stream/` gaat langs `sessions.guard`.**
   `test_media_routes_guarded.py` loopt alle routes langs.
7. Elke API-fout via `errors.fail` met een code uit `i18n/errors/media.ts`. M0 zet ze er allemaal in
   (§3.4). `test_error_catalog.py` controleert het.
8. **Geen databaseverbinding vasthouden** tijdens het wachten op een segment, een aanroep naar de
   worker of een stream. Eerst commit en close (de les van F1).
9. **Uitgaand verkeer alleen via `app/media/providers/http.py`** (Keuze 39).
10. **Elke lezer van onvertrouwde bytes heeft een fuzztest met een vaste seed**: nooit een hang,
    nooit een ongevangen fout, altijd binnen de tijd (§4.2).
11. UI-tekst via `t()` in de eigen gebieden, met Engels. Alles met het toetsenbord bereikbaar, met
    een checklist in `docs/fase-5/toetsenbord-<pkg>.md`.
12. Commentaar en commitberichten in het Nederlands.
13. Nooit meer dan 3 ffmpeg tegelijk in een test of meting, en stoppen alleen op pid. De worker
    dwingt het af: het harnas en de belastingtest zetten `PLATFORM_MEDIA_MAX_PROCS=3` (Keuze 12).
    De fixture `ffmpeg_count` kijkt tijdens de test elke 0,5 s (een thread) en faalt bij een
    vierde. Na elke e2e-test: 0.
14. **Elke prestatiemeting draait met de grens van de LXC:** de lokale worker onder
    `taskset -c 0-2`, of de worker-container met `--cpus 3`. Ook `bench`. Zonder die grens meet de
    sandbox 4 cores, en de LXC geeft de worker er 3 (V7).

**Afhankelijkheden** (alleen via de contracten en stubs van M0):

| Wie | Gebruikt | Gemaakt door |
|---|---|---|
| iedereen | `client.call`, `wire`, `state.MediaState`, `types.*`, `analysis.step`, `hooks` | M0 |
| M3, M4, M5, M9 | de lokale worker in tests | M0 (de kern); M1 (bewaking en supervisie) |
| M3 | `streams.open(session, plan)`, `streams.close(session)` | M4 (M0-stub geeft 503 `media_off`) |
| M3 | `codecs.strings(...)` in de probe | M3; tot dan geeft de M0-stub alleen wat ffprobe weet |
| M3 | `keyframes.read_now(sha)` bij `play` (Keuze 17) | M4 (M0-stub geeft `none`, dan transcode) |
| M3 (via de haken van M0) | `sessions.close_login(family)`, `sessions.close_node(node_id)` | M3; M0 zet de haken in `auth.py`, `files.notify` en `on_discard` |
| M4, M5, M9 | `sessions.guard`, `sessions.create_audio` | M3 (M0-stub) |
| M4 | `admission.reserve(plan)` voor de toelating | M4 (eigen); M3 vraagt het via `decide(…, budget)` |
| M6 (e2e) | `POST …/play` en `/api/v1/stream/…` | M0 (`fake_stream`, contract-gelijk); M3, M4 en M5 (echt, bij het samenvoegen) |
| M2 | `progress.home_rows()` voor `/media/home` | M7 (M0-stub geeft lege rijen) |
| M2 | `analysis.enqueue(sha)`; de stappen | M0 (probe); M4 (keyframes); M5 (subs, sprites); M9 (audio_info) |
| M7 | `chapters.credits_ms(probe)` voor "gezien" | M5 (M0-stub geeft `None`) |
| M8 | `items.apply_metadata(node_id, fields, source)` | M2 (M0-stub) |
| M10 | `state`, `admission.snapshot()`, `sessions.list()`, `cache.sizes()`, `providers.status()`, `importer.start()` | M2, M3, M4, M8 (M0-stubs) |
| M10 | `app.cli media-status` | M0 (het commando); M10 (`status.py`) |

---

### M1 — Worker en isolatie

**Doel.** ffmpeg draait alleen nog in de worker. Op de LXC is dat een afgeschermde container. Ook
de previews van video's gaan erdoorheen.

**Maakt**
- `app/media/worker/guard.py`:
  - `RLIMIT_DATA` per soort als harde grens, en `VmRSS` elke 2 s voor `/beheer` (Keuze 12);
  - `nice`, `ionice`, de andere `RLIMIT`'s van Keuze 12, de procesgroep, de kale omgeving;
  - de plaatsen per soort en het totaal `PLATFORM_MEDIA_MAX_PROCS`, met stoppende processen erbij;
  - één producent per sessie: de nieuwe pas na `waitpid` van de oude, en 300 ms samenvoegen;
  - (spike) vóór elke exec `lseek(fd, 0)` op elke invoer-fd, nooit twee kinderen tegelijk op één
    fd, en `dup2` naar fd 3 (en 4) alleen in het kind (Keuze 12, S3);
  - `prctl(PR_SET_DUMPABLE, 0)` bij de start van de worker.
- `app/media/worker/ops/frame.py`, `meta.py` en `bench.py` (Keuze 12, 26).
- `app/media/worker/selftest.py`: `python -I -m app.media.worker --selftest`. Dat controleert:
  - versie 7.1.x;
  - de encoders `libx264`, `aac`, `libopus`, `libvpx-vp9`, `mjpeg`, en (spike) `libx265` (voor
    `bench`) en `flac` (voor Opus in `remux_audio`);
  - de filters `zscale`, `tonemap`, `bwdif`, `overlay`, `scale`, `fps`, `tile`;
  - de protocollen `fd` en `pipe`;
  - (spike) de demuxers van de witte lijst in `ffmpeg -demuxers`, en `lavfi` in `ffmpeg -devices`
    (S1); een weigering door `-format_whitelist` en door `-protocol_whitelist`;
  - `nice` en `ionice` in het image;
  - een transcode van 4 s `testsrc2` naar fMP4 op een pipe: twee fragmenten, `tfdt` op 1,000 en
    3,000 (`+frag_discont`, J4), `trun` versie 1 en de eerste offset 0 (`+negative_cts_offsets`,
    V4);
  - de afscherming (Keuze 11): `id -u` is 10002; ffmpeg en ffprobe hebben modus 0711;
    `os.open('/proc/self/fd/3', O_WRONLY)` op een fd van een bestand van 10001 faalt; een kind
    kan `/proc/<worker>/fd` niet lezen. De lokale worker (zonder sandbox) meldt hier
    `sandbox: off` in plaats van een fout;
    *Noot (M0, review S6):* `caps.sandbox` is `unchecked` voor elke worker zonder `--local` (de
    stub van `media-status` zegt dan "afscherming niet gecontroleerd"). Pas als deze zelftest
    slaagt, zet M1 hem op `on` (in de cache van caps); alleen `on` heet "afgeschermd", in
    `media-status` en op `/beheer`. Zonder `--local` en zonder zelftest is er geen bewijs: de
    worker kan ook gewoon op de host draaien, als de app;
  - `RLIMIT_DATA` van 1.200 MB laat 1080 veryfast met `-threads 4` gewoon draaien (V6).

  Exit 1 bij een fout. CI gebruikt het als harde poort.
- `app/media/local.py`: de lokale worker als kindproces (Keuze 13): starten, herstarten met backoff
  (1, 2, 4 … 60 s), stoppen met de app.
- `app/media/health.py`: elke 30 s verbinden en `caps`, over de controleverbinding die niet
  meetelt (Keuze 12). Dat schrijft `MediaState.worker` (`ok`, `down`, `ffmpeg_missing`) en zet
  de caps in de cache. `busy` van de worker betekent `ok`, niet `down`.
- `infra/single/compose.media.yaml`:
  ```yaml
  # Media (fase 5): ffmpeg in een eigen container zonder netwerk en zonder data. De app geeft
  # elk bestand als open file descriptor over de socket in media-run (Keuze 11 en 12).
  # install.sh neemt dit bestand mee tenzij MEDIA_SANDBOX=uit in /opt/platform/.env staat.
  services:
    platform:
      environment:
        PLATFORM_MEDIA_WORKER: /run/media/worker.sock
      volumes:
        - media-run:/run/media
      stop_grace_period: 30s         # F3: de lifespan sluit netjes, ook met lange streams

    media:
      image: platform:local          # hetzelfde image als de app, één keer gebouwd
      pull_policy: never
      restart: unless-stopped
      user: media                    # uid 10002: kan blobs van 10001 niet opnieuw openen (V1)
      command: ["python", "-I", "-m", "app.media.worker", "--socket", "/run/media/worker.sock"]
      network_mode: none
      read_only: true
      tmpfs:
        - /tmp:size=64m,mode=1777
        - /data:size=1m,mode=0700,uid=10002,gid=10002   # overschaduwt VOLUME /data: geen anoniem volume
      volumes:
        - media-run:/run/media
      cap_drop: [ALL]
      security_opt: ["no-new-privileges:true"]
      pids_limit: 256
      ulimits:
        core: 0
        nofile: {soft: 1024, hard: 1024}
      healthcheck:
        test: ["CMD", "python", "-I", "-m", "app.media.worker", "--check", "/run/media/worker.sock"]
        interval: 30s
        timeout: 5s
        retries: 3
      stop_grace_period: 5s

  volumes:
    media-run:
  ```
  *Noot bij de bouw van M0:* het `command` en de `healthcheck` met `python -I -m
  app.media.worker` vinden `app` niet in het image (`WORKDIR /srv`, niets geïnstalleerd). M1
  start de worker zoals `media/local.py` (`python -I -c <BOOT> /srv …`) of installeert het
  project, en zet `PLATFORM_MEDIA_ENABLED: "true"` bij `platform` ([m0.md](m0.md), afwijking 1
  en 3).
- `infra/single/compose.media-limits.yaml`:
  ```yaml
  # Grenzen voor de worker. Alleen als `docker info` "true true" zegt (zoals office).
  # 3 transcodes samen zijn ±1,7 GB [gemeten]; 3 GB laat marge.
  services:
    media:
      mem_limit: 3g
      memswap_limit: 3g
      cpus: 3.0
  ```
- `infra/single/tests/test_media_container.sh` (in de CI-job `media-image`, met Docker). Hij doet
  wat J1 deed, nu met het echte image en de echte compose:
  - de container start met precies deze afscherming;
  - geen netwerk: `socket.create_connection(("1.1.1.1", 53))` faalt;
  - `/` alleen lezen: `touch /x` faalt;
  - `/data` is leeg;
  - `/run/media` is van `media:platform` met modus 2750, de socket 0660 met groep `platform`. De
    app verbindt, maar kan in de map niets maken (V2);
  - `id -u` in de worker is 10002;
  - een probe en een frame van een fixture, met de fd vanuit de platform-container;
  - schrijven op een doorgegeven fd faalt, en opnieuw openen via `/proc/self/fd/3` om te
    schrijven ook (V1);
  - de worker gestopt: de app meldt `down` binnen 30 s.

**Wijzigt**
- `render.py`: de tak video vraagt `frame` aan de worker. De JPEG die terugkomt, gaat als gewone
  afbeelding door de bestaande renderer. Het eigen ffmpeg-deel gaat weg.
- `render_meta.py`: de tak video vraagt `meta`.
- `previews.py`: is de worker weg, dan zet een preview van een video zichzelf opnieuw klaar
  (`jobs.Defer(300)`), zonder een poging te tellen.
- `Dockerfile`: de drie regels van Keuze 47 (de gebruiker `media`, de map `/run/media` met 2750,
  en `chmod 0711` van ffmpeg en ffprobe).

**Tests**
- `test_media_worker_guard.py`:
  - een job boven de geheugengrens wordt gestopt;
  - de verbinding dicht: ffmpeg is binnen 3 s weg (ook kleinkinderen, `killpg`);
  - een volle soort geeft `busy` met die soort; `caps` op de controleverbinding werkt dan nog;
  - met `PLATFORM_MEDIA_MAX_PROCS=3` krijgt een vierde proces `busy`, ook als er één nog stopt;
  - 10 keer zoeken in 1 s: per sessie nooit meer dan 2 ffmpeg tegelijk;
  - een job boven `RLIMIT_DATA` stopt met een fout, niet de worker;
  - de nice-waarde en de `RLIMIT`'s kloppen (`/proc/<pid>/stat`, `/proc/<pid>/limits`);
  - de omgeving van ffmpeg is kaal.
- `test_media_no_ffmpeg_outside.py`: de AST van alle modules buiten `app/media/worker/` bevat geen
  aanroep met `ffmpeg` of `ffprobe`.
- `test_previews_video_worker.py`: een preview van een video via de worker. Met de worker weg: een
  nieuwe taak, `attempts` blijft gelijk.
- `test_media_local.py`: de lokale worker herstart na `kill -9` (op pid); de app stopt hem bij het
  afsluiten.
- `test_media_health.py`: `ok`, `down` en `ffmpeg_missing` (een worker met een lege `PATH`). Met 6
  verbindingen open blijft de gezondheid `ok`.

**Klaar als** (per omgeving, want het image in de sandbox krijgt een leeg ffmpeg-pakket,
`test_install_update.sh:53–55`)
- In CI, in de job `media-image` (daar werkt apt): de worker draait met precies deze afscherming
  en maakt een preview en een probe. Daarna een transcode via curl: `POST /play` met een eigen
  profiel zonder H.264-direct, dan `GET` segment 0. Tijdens die transcode toont `docker top` van
  de app geen ffmpeg, en die van `media` er één.
- In de sandbox: met de worker weg (of een leeg ffmpeg) werkt direct play, en toont `/beheer`
  de melding `media_ffmpeg_missing`.

---

### M2 — Bibliotheek, scanner, namen en import

**Doel.** Mappen worden bibliotheken. Wat erin staat, verschijnt vanzelf, met nette titels, ook
zonder internet. Een bestaande verzameling komt binnen via de import.

**Maakt**
- `app/media/names.py` met `tests/media/names.tsv` (≥ 500) en `names-open.tsv` (Keuze 7).
- `app/media/classify.py`: extensies, extra's, samples, externe ondertitels, lokale art (Keuze 5,
  9).
- `app/media/scanner.py`:
  - `touched()` (M0 zet de haak) vult de set;
  - de lus van 30 s;
  - de taak `media.scan` in stukken van 500;
  - `media.reconcile` bij de start en elke 6 uur; die zet alleen scans klaar (rij `media_scan`).
- `app/media/library.py`: maken, wijzigen, weghalen, de controle op nesting, en wie wat ziet.
- `app/media/items.py`: `apply_metadata(node_id, fields, source)` met `locked` (ook voor M8), en het
  groeperen in series en delen (Keuze 8).
- `app/media/importer.py`: de import van Keuze 2, als job `media.import` in de rij `import`, en de
  functie achter `app.cli media-import`.
- `app/media/worker/ops/nfo.py`.
- `app/routers/media_library.py`:
  ```
  GET    /api/v1/media/libraries                    → [{id, name, kind, root_id, mine, item_count, scan_state, scanned_at, online}]
  POST   /api/v1/media/libraries                    {root_id, kind, name?, online?}
                                                    → 201 | 403 media_not_owner | 409 media_library_nested
                                                      | 422 media_library_not_folder | 422 media_library_limit
  PATCH  /api/v1/media/libraries/{id}               {name?, online?, language?} → 200
  DELETE /api/v1/media/libraries/{id}               → 204 (de bestanden blijven)
  POST   /api/v1/media/libraries/{id}/scan          → 202
  GET    /api/v1/media/home                         → {libraries[], continue[], next_up[], recent[]}
  GET    /api/v1/media/libraries/{id}/items         ?type=movie|show|video&sort=title|added|year&cursor=&limit=60
                                                    → {items: [Card], next}
  GET    /api/v1/media/items/{node_id}              → Item
  GET    /api/v1/media/shows/{show_id}              → {show, seasons: [{season, episodes: [Card]}]}
  ```
  - `Card` = `{node_id, title, year, kind, poster, progress, watched}`.
  - `Item` = Card plus `overview`, `backdrop`, `duration_ms`, `streams` (kort: codec, taal, titel),
    `subtitles`, `chapters`, `match_state`, `can_edit`, `parts` en `analysis` (de staat per stap).
  - Plaatjes zijn ondertekende adressen van `previews.info` (lokale art) of van `art/` (providers).
  - Alles met de toegang van Keuze 4. Een item van een bibliotheek die je niet ziet: 404.

**Web**
- `/media`: de start. Bibliotheken, Verder kijken en Volgende aflevering (M7), en Nieuw.
- `/media/bibliotheek/{id}`: een raster met sorteren. Virtualisatie zoals de fototijdlijn van fase 3.
- `/media/item/{node}`: de pagina van een film of aflevering, met een knop Afspelen en "Verder op
  12:34".
- `/media/serie/{id}`: seizoenen en afleveringen.
- "Als bibliotheek gebruiken" in het menu van een map (de haak van M0), met de keuze voor het soort.

**Tests**
- `test_media_names.py`: de testset 100 %; de fuzz (10.000 namen, vaste seed, < 0,5 s), met de
  eigenschap "zonder afleveringstoken nooit een aflevering"; absolute nummering geeft `season`
  leeg (Keuze 7 en 8).
- `test_media_scanner.py`:
  - nieuw, hernoemd, verplaatst binnen en naar buiten;
  - prullenbak en terugzetten (`present`, voortgang blijft);
  - een nieuwe versie (andere blob: opnieuw analyse);
  - `locked` blijft;
  - 2.000 bestanden in stukken;
  - een externe `.nl.forced.srt` hoort bij de goede video;
  - een "crash" (de set weggooien) en daarna de ronde bij de start: niets mist.
- `test_media_library_api.py`: nesting omhoog en omlaag; geen map; niet de eigenaar; zichtbaar via
  een deling van de map, niet via een deling van een submap; een gast; weghalen laat de bestanden
  staan.
- `test_media_nfo.py`: velden, alleen een IMDb-link, 2 MB (geweigerd), kapotte XML, `DOCTYPE` en
  `ENTITY` (geweigerd), fuzz.
- `test_media_import.py`: kopiëren; verplaatsen binnen één bestandssysteem (`adopt`); `EXDEV` geeft
  kopiëren en daarna de bron weg; ontdubbelen; quotum vol halverwege; een symlink en een fifo
  overgeslagen; `../` buiten `/data/import` geweigerd; een bronmap die een symlink naar `/data`
  is, geeft `media_import_path`; een bestand dat tijdens het tellen groeit, of jonger is dan
  60 s, komt in "overgeslagen" ("in gebruik"); een harde link wordt bij verplaatsen
  overgeslagen; een bestand van een andere uid dat niet te lezen is, komt in "overgeslagen";
  nog een keer draaien doet niets.
- e2e `test_media_m2.py`: een map met fixtures uploaden en er een bibliotheek van maken; de items
  verschijnen (tik 0,5 s in de test), met de goede titels; een import via `/beheer`; alles met het
  toetsenbord.

**Klaar als**
- De testset is 100 %.
- Een item verschijnt ≤ 35 s na de upload (met de tik van 30 s), ook met 100 analyses in de rij
  `media` (`test_jobs_lanes.py`: een nieuwe scan loopt binnen één tik).
- 1.000 bestanden scannen ≤ 60 s in de sandbox, zonder analyse.
- Prullenbak en terugzetten houden voortgang en koppeling.
- Een import van 100 fixtures, verplaatst, kost geen extra ruimte.

---

### M3 — Afspelen: profiel, beslissing, sessies en toegang

**Doel.** Van het profiel tot een sessie met een plan, met een controle bij elk verzoek. Direct play
werkt hiermee al helemaal.

**Maakt**
- `codecs.py` (Keuze 16), aangeroepen door de probe in de worker.
- `profile.py`: `ClientProfile`, de witte lijst van strings, de basisprofielen in `profiles/*.json`
  (Keuze 20), en `docs/fase-5/clientprofiel.md`.
- `decide.py` (Keuze 19).
- `sessions.py`: aanmaken met de gepinde `(sha, size)`, `guard` (Keuze 21), de vlag `closed`,
  grenzen, `replace`, `close_user`, `close_login`, `close_node`, `close_sha`, `list()`, en
  `create_audio` voor M9. Plus de iterator voor lange responsen (`/file`, en voor M9
  `audio.mp4`), die per stuk de vlag en de toegang nakijkt.
- In `play`: meteen na de probe de keyframes lezen (Keuze 17), binnen hetzelfde budget van 5 s.
- De lijst `subs` van de sessie, gefilterd op toegang (Keuze 29).
- `public.py`: de policy voor links (Keuze 34).
- `routers/media_play.py`, `routers/stream_file.py`, `routers/public_media.py`:
  ```
  GET  /api/v1/media/{node_id}/codecs        → {candidates: [string], item: [string]}
  POST /api/v1/media/{node_id}/play
       {profile, audio?, subtitle?, quality?: "auto"|"source"|1080|720|480, start_ms?, hdr?: "raw", replace?: sid}
       → 200 {sid, mode, reasons[], urls: {file|map|master}, mime, duration_ms, start_ms, offset,
              tracks: {audio: [{index, lang, title, codec, channels, default}],
                       subtitles: [{id, lang, label, forced, sdh, default, state}]},
              chapters[], credits_ms?, trick?, forced_available?, quality: {current, options[]},
              started_at, expires_at}
       → 404 not_found | 409 media_probe_pending {reason?: keyframes} | 409 media_unavailable
         | 415 media_unsupported {reason?: too_large} | 422 media_hdr {reason: too_large|dolby_vision|no_tonemap}
         | 503 media_busy {reason: cpu|disk|user|link|worker} | 503 media_worker_down
         | 503 media_ffmpeg_missing | 503 media_off
  GET|HEAD /api/v1/stream/{sid}/file         direct play: Range, If-Range, ETag (de route van send_file)
  DELETE   /api/v1/stream/{sid}              → 204
  POST /api/v1/public/media/{node_id}/play   sleutel van de link; zelfde body
                                             → 403 media_public_transcode | 429 rate_limited
  ```
  - `/file` houdt geen databaseverbinding vast (regel 8): de sessie staat in het geheugen.

**Tests**
- `test_media_codecs.py`: een tabel van bekende `avcC`, `hvcC`, `av1C`, `vpcC` en `dvcC` naar strings,
  Annex-B (`a.ts`, `a.m2ts`, H.264 en HEVC), de strings van alle fixtures, en fuzz.
- `test_media_profile.py`: de vorm, de grens van 64 en 8 KB, onbekende velden weg, een basisprofiel
  plus eigen metingen.
- `test_media_decide.py`: een tabel van ≥ 60 gevallen. De probes van alle fixtures, maal de
  profielen `chromium141` (gemeten), `chrome_ci`, `safari17`, `webos5` en `firefox` (aangenomen, uit
  [doc]), maal de keuzes (ander audiospoor, PGS, kwaliteit, HDR raw, publiek). Elk geval met de
  verwachte `mode` en `reasons`. Nieuw in revisie 2:
  - "hevc 2160p HDR, auto": 422 `media_hdr` met reden `too_large`;
  - een bron boven 8192×4320: 415 `media_unsupported`;
  - PGS geforceerd met een profiel dat direct kan: `direct`, met `forced_available`;
  - een publieke link met PGS geforceerd: `remux`, geen 403;
  - Hi10P (`h264_hi10p.mkv`): `chromium141` remux, `safari17` transcode;
  - `noaudio.mp4`: `audio: "none"` en een mime met alleen video;
  - een 4K-bron geeft een plan met `places: 2`;
  - een GOP van 10 s bij 80 Mbit/s: transcode met `gop_too_long`.
- `test_media_sessions.py`:
  - een onbekende sid op elke route: 410;
  - een sessie die de beheerder stopte: 403 met `reason: stopped`, ook na het sluiten (de
    grafsteen); na 30 minuten 410;
  - een gebruiker geblokkeerd, een family ingetrokken, het bestand in de prullenbak, een deling
    ingetrokken: 403 `media_access_gone` (met de nepklok binnen 15 s; `close_user` en
    `close_login` meteen);
  - een `/file`-stream die de client pauzeert, dan één login intrekken, dan weer lezen: de
    verbinding sluit binnen één stuk (4 MB of 5 s);
  - een nieuwe versie tijdens het spelen: het volgende verzoek geeft 410 met reden `replaced`;
    zoeken daarna opent nooit de nieuwe blob in de oude sessie;
  - `on_discard(sha)` sluit eerst de sessies, en gooit dan de afgeleide bestanden weg;
  - ondertitels: de node-id van een ondertitel van een ander geeft 404; een `x<i>` buiten de
    lijst geeft 404; een los bestand buiten de publieke link staat niet in de lijst;
  - 8 per gebruiker; 12 uur; 30 minuten stil; `replace`.
- `test_media_public.py`: direct en remux mogen; transcode 403; 2 per link; een verlopen link en een
  ander wachtwoord geven 403; een bestand buiten de link 404.
- `test_media_direct.py`: Range, `If-Range`, `HEAD`, en 20 gepauzeerde streams houden de pool vrij.

**Klaar als**
- Direct play van een 1080p-bestand werkt via de sessie, met zoeken, ook na 10 minuten.
- Elke route onder `/stream/` controleert de toegang. Intrekken werkt: meteen bij uitloggen en bij
  één login intrekken, ook midden in een `/file`-stream; ≤ 15 s bij een deling.
- Een mkv buiten een bibliotheek speelt als remux (de keyframes bij `play`).
- In geen enkel log staat een sid (`test_media_logs.py`).

---

### M4 — Stromen: producent, knipper, playlists, toelating en cache

**Doel.** Van een plan tot het laatste segment: remux en transcode, zoeken, de toelating, de
tegendruk en de cache.

**Maakt**
- `ladder.py`: de tredes, de kostentabel met de kosten van de bron (`d`, fps, HDR-factor) en de
  naad `Encoder` (Keuze 24, 26, 28).
- `admission.py`: de regel met de reserve, zware plannen als 2 plaatsen, de factoren `f` en
  `f_dec`, `B − 1,0` zolang DS start, per gebruiker en per link, en `snapshot()` voor `/beheer`
  en de tests. Een Grant reserveert ook de plaats in de worker (Keuze 12).
- `streams.py`: `open(session, plan)` en `close(session)` voor M3; de toegang tot segmenten.
- `producer.py`: één producent per sessie.
  - Start via `client.call(STREAM, …)` en leest de pipe (`connect_read_pipe`).
  - Knipt met `fmp4.py`: boxgroottes begrensd (init ≤ 1 MB, `moof` ≤ 4 MB, `mdat` ≤ 64 MB).
  - Groepeert fragmenten tot segmenten volgens `grid.py`, op de pts van het eerste sample: `tfdt`
    plus de getekende offset (`trun` v1), ook bij transcode.
  - Schrijft `<n>.m4s.tmp` en hernoemt atomair. Wekt wachtende verzoeken.
  - Leest niet verder bij 60 s voorsprong in mediatijd (tegendruk), en niet verder dan 10 s
    zolang een andere sessie nog geen segment 0 heeft (startvoorrang, Keuze 26).
  - Vergelijkt de `stsd` van elke nieuwe init (Keuze 22).
  - Herstart bij een sprong (Keuze 25), en één keer na een fout of "vast" (Keuze 46).
- `grid.py`: het rooster van 2 s en het rooster uit keyframes (Keuze 22, 23). (spike) Het rooster
  van 2 s hangt aan `origin` (het eerste videobeeld), N komt uit `last_frame_ms`, en een fragment
  voorbij N hoort bij het laatste segment (`SegmentGrid` in `app/media/types.py`). Een herstart
  op segment k gebruikt `-ss (origin + 2k)`. Voor MPEG-TS kiest M4 hoe een herstart toch het
  hele segment levert (Keuze 22).
- `hls.py`: `map.json`, `master.m3u8`, `v.m3u8`.
- `cache.py`: de mappen, het venster in bytes per sessie, de housekeeping `media.sweep` (elke 5
  minuten), en leegmaken bij de start (Keuze 31).
- `worker/ops/stream.py` en `worker/ops/keyframes.py`, en `worker/containers.py` (de lezers voor
  Cues en `stss`, begrensd). De index komt gecomprimeerd terug, hooguit 48 KB, en anders
  uitgedund (Keuze 12). M4 registreert de stap `keyframes` bij `analysis`, en levert
  `keyframes.read_now(sha)` voor `play` (Keuze 17).
- `routers/stream.py`:
  ```
  GET  /api/v1/stream/{sid}/map.json          Cache-Control: no-store
  GET  /api/v1/stream/{sid}/master.m3u8       application/vnd.apple.mpegurl, no-store
  GET  /api/v1/stream/{sid}/v.m3u8
  GET  /api/v1/stream/{sid}/init.mp4?g={gen}  Cache-Control: private, max-age=3600
  GET  /api/v1/stream/{sid}/{n}.m4s           X-Init: {gen}; private, max-age=3600
                                              → 503 media_timeout (Retry-After: 2) | 502 media_transcode_failed
  ```

**Playlists** (`hls.py`). Bij transcode geen `EXT-X-INDEPENDENT-SEGMENTS` voor `webos-5` [doc]; voor
de rest wel, want elk segment begint met een IDR. Bij remux nooit: de bron kan open GOP's hebben
(spike: HEVC met open GOP geeft CRA-keyframes).
```
#EXTM3U
#EXT-X-VERSION:7
#EXT-X-INDEPENDENT-SEGMENTS
#EXT-X-STREAM-INF:BANDWIDTH=4026000,AVERAGE-BANDWIDTH=2600000,CODECS="avc1.64001f,mp4a.40.2",RESOLUTION=1280x720,FRAME-RATE=23.976,VIDEO-RANGE=SDR
v.m3u8
```
```
#EXTM3U
#EXT-X-VERSION:7
#EXT-X-TARGETDURATION:2
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-MAP:URI="init.mp4?g=1"
#EXTINF:2.021,
0.m4s
#EXTINF:2.000,
1.m4s
…
#EXTINF:1.512,
3599.m4s
#EXT-X-ENDLIST
```
- (spike) `EXTINF` van segment 0 is `origin + 2`: hier 2,021 (video vanaf 0, S = −0,021).
  Begint de video laat (`origin + 2` ≥ 2,5), dan is `TARGETDURATION` de afgeronde duur van
  segment 0; alle andere segmenten blijven ≤ 2 na afronden. De HLS-lint eist dat (§3.6).
- `BANDWIDTH` = (maxrate plus audio) × 1,1. `AVERAGE-BANDWIDTH` = een schatting uit de trede.
- Bij remux: de echte piek uit de index (de grootste som van samplegroottes in een venster van
  6 s), en `EXTINF` met 3 decimalen.
- `CODECS` bevat video én audio, of alleen video als de bron geen audio heeft (Keuze 19). De
  lint eist audio alleen als de probe audio heeft. `FRAME-RATE` met drie decimalen. `VIDEO-RANGE` is `SDR`, `PQ`
  of `HLG`. Alleen de trede van de sessie staat erin: een native speler kiest nooit zelf een trede
  waar geen budget voor is.

**Tests**
- `test_media_grid.py`: het rooster van 2 s; `N` voor duren rond de grenzen (spike: met `origin`,
  ook bij een late videostart, en `last_frame_ms`; een fragment voorbij N); de remuxregel met lange
  GOP's, de laatste keyframe, en B-frames (pts tegen `tfdt`); een extra keyframe midden in een
  segment blijft in dat segment.
- `test_media_fmp4.py`: boxen in stukken van 1 byte, te groot, afgekapt, `size` 0 en 1, `tfdt`
  versie 0 en 1, `trun` versie 0 en 1 met negatieve offsets, de `stsd`-vergelijking, en fuzz.
- `test_media_restart.py` (echte ffmpeg, hooguit 1 tegelijk): een run vanaf 0 en een herstart op
  segment 5 geven dezelfde videotijden per segment; `tfdt` begint op mediatijd + 1 (J4); de pts
  van het eerste sample (`tfdt` plus de offset) is de brontijd + 1 s, binnen één frame, voor
  H.264 met B-frames (transcode) en voor HEVC (remux) (V4); `fps240.mp4` geeft precies één IDR
  per 2 s en 60 fps (V5); het verschil in audio is ≤ 100 ms. (spike) De herstart met
  `-ss (origin + 2k)`, ook voor een bron waarvan de video laat begint; `interlaced.ts` met de
  aanpak die M4 voor MPEG-TS kiest.
- `test_media_producer.py` (nep-worker):
  - tegendruk bij 60 s voorsprong (de nep meldt dat schrijven blokkeert);
  - herstart bij een sprong;
  - één nieuwe poging na een crash, daarna 502;
  - "vast" na 30 s zonder fragment;
  - geen segment in 20 s: 503;
  - idle na 120 s: de producent dicht, de sessie blijft;
  - een andere `stsd`: generatie 2 en de kop `X-Init`.
- `test_media_admission.py`: de reserve (1080 + 720sf + 720sf met de proefcijfers); max 2; een trede
  lager; 2 per gebruiker; 1 per link; het plafond uit env; de factor `f`; schijf vol. Nieuw:
  - "eerste kijker, VP9 HDR 720, `B` = 3,0": 720 veryfast;
  - een 4K-bron: 1080 superfast met 2 plaatsen, en daarna nog één kijker;
  - DS start: het budget is `B − 1,0`, en een nieuwe kijker krijgt een lagere trede;
  - de worker zegt `busy`: 503 `media_busy` met reden `worker` en `Retry-After: 2`;
  - een goedgekeurd plan krijgt nooit `busy` van de worker (de Grant reserveert).
- `test_media_cache.py`: het venster in bytes bij een 4K-remux; opruimen boven `cache_gb`
  ongeacht de leeftijd, maar nooit binnen 60 s achter de speler; de schijfcheck ook bij remux.
- `test_media_keyframes_wire.py`: een index van 50.000 keyframes past in één antwoord (uitgedund
  waar nodig).
- `test_media_hls.py`: golden files voor de playlists, de lint van §3.6, en `map.json` gelijk aan
  `v.m3u8`.
- `test_media_containers.py`: Cues en `stss` op alle fixtures tegen ffprobe; fuzz.
- `test_media_contract.py`: dezelfde scenario's als `media_contract.py` van M0, nu tegen de echte
  routes.

**Klaar als**
- Met de virtuele speler van §4.6 (zonder browser, marker `media_perf`) in de sandbox, met de
  worker onder `taskset -c 0-2` (regel 14):
  - van `play` tot segment 0 bij transcode 1080 naar 1080, alleen: ≤ 2,5 s;
  - hetzelfde terwijl er al 2 transcodes lopen: ≤ 5 s;
  - na een sprong naar 70 %: ≤ 2 s;
  - remux: ≤ 0,5 s.
- De belastingtest (§4.6) met 3 sessies, in alle fasen: 0 keer haperen na het eerste segment.

---

### M5 — Ondertitels, sprites en hoofdstukken

**Doel.** Ondertitels in elke taal en elke vorm, sprites op de zoekbalk, en hoofdstukken met de
aftiteling.

**Maakt**
- `subconv.py`: de omzetter van SRT en ASS naar VTT (Keuze 29). Pure functies; draait in de worker.
- `subtitles.py`: externe sporen koppelen, de controle van elke VTT, de staat per spoor, en de stap
  `subs` (achtergrond) en het uitpakken op verzoek.
- `trick.py`: sprites opslaan, `trick.vtt` (relatieve adressen), het serveren via de sessie, en de
  stap `sprites`.
- `chapters.py`: hoofdstukken uit de probe en `credits_ms(probe)`.
- `worker/ops/subs.py` en `worker/ops/sprites.py`.
- `routers/media_subs.py`:
  ```
  GET  /api/v1/stream/{sid}/subs/{id}.vtt     text/vtt; 202 + Retry-After: 5 zolang hij gemaakt wordt
                                              id = e<index> | x<i> (plek in de lijst van de sessie)
  GET  /api/v1/stream/{sid}/trick.vtt         text/vtt; private, max-age=3600
  GET  /api/v1/stream/{sid}/trick/{i}.jpg     image/jpeg; private, max-age=3600
  ```

**Tests**
- `test_media_subconv.py`: `\an8` wordt `line:0`; `<i>`, `<b>`, `<u>`; kleur als klasse; tekeningen
  weg; BOM, UTF-8 en CP1252 met "é"; overlappende cues; een tabel van golden files; fuzz.
- `test_media_subtitles.py`: tijden min `start_time`; de grenzen (5 MB, 20.000 cues, 2.000 tekens);
  geforceerd (alleen tekst gaat vanzelf aan); een los bestand bij de goede video; een losse
  `.sup` wordt niet gekoppeld; 202 zolang hij gemaakt wordt; de pipes voor de sporen zijn van de
  app.
- `test_media_trick.py`: aantal tegels, `trick.vtt` klopt met de tegels, elk adres in `trick.vtt`
  geeft 200 via de sessie, en na intrekken 403.
- `test_media_chapters.py`: titels, de aftiteling in het Nederlands en Engels, geen hoofdstukken.
- e2e `test_media_m5.py`: SRT en de ASS-cue met `\an8` (boven in beeld) als `<track>`; PGS inbranden
  (de pixels op de plek van de ondertitel veranderen, gemeten in een canvas); sprites bij hover.
  (spike) Ook inbranden na een sprong naar een plek waar een ondertitel al loopt (S9: `-ss 4`
  met een ondertitel van 2,0 tot 5,0 s), met de tweede fd van Keuze 29.

**Klaar als.** Elke tekstondertitel van de fixtures staat goed in beeld, op de goede tijd (±1 frame).
Het inbranden van PGS is bewezen, of geschoven met de reden (§5). (spike) S9 bewees het technisch,
voor PGS en VobSub; schuiven kan alleen nog om de planning.

---

### M6 — De speler (web)

**Doel.** Een eigen speler die direct, MSE en native HLS kan, met kwaliteit, audiospoor,
ondertitels, PiP en toetsenbord. Binnen het budget, zonder bibliotheek (B19).

**Maakt.** `web/src/player/**` (Keuze 32, 33), het gebied `player` (nl en en), en de checklist
`docs/fase-5/toetsenbord-m6.md`.

**Wijzigt.** Niets buiten de eigen bestanden. M0 zette de haak in `MediaView.tsx`.

**Tests**
- vitest:
  - `keys.test.ts`: elke toets, en niets als de focus in een veld staat; de combinaties van de
    scopes global, viewer, player en `/kijken` zijn uniek of bewust overschreven (Keuze 33);
  - `mse.test.ts` (pure delen): het segment voor tijd t, wat er weg mag, `gen` wisselen, de
    beslissing over een gat (spike: bij `waiting` na 250 ms, als het volgende stuk binnen 0,5 s
    begint; S4);
  - `session.test.ts`: vernieuwen bij 410 op de juiste positie, stoppen bij 403, geen lus (hooguit
    3 keer per minuut); 30 s netwerkfouten en 503 zonder code: de speler blijft proberen
    (1, 2, 4, 8, 10, 10 s) en loopt daarna door;
  - `caps.test.ts`: de vorm van het profiel en de grens van 64;
  - `tracks.test.ts`: de regel voor geforceerd, en labels via `Intl.DisplayNames`.
- e2e `test_media_m6.py` (Chromium 141 hier, Chrome for Testing 156 in CI):
  1. direct: `vp9_opus.webm`. Het eerste beeld via `requestVideoFrameCallback`;
  2. remux: `vp9_opus.mkv`. Zoeken naar 50 % en weer lopen;
  3. transcode: `hevc10_hdr10.mkv`. In Chromium 141 naar VP9 en Opus, en de gekozen trede is 720
     veryfast (`admission.snapshot()`). In CI naar H.264 als
     `isTypeSupported("video/mp4; codecs=\"avc1.640028\"")` ja zegt. Anders overgeslagen met een
     reden, behalve met `MEDIA_REQUIRE_H264=1`: dan faalt de test (§4.8);
  4. een ander audiospoor (`multi_audio.mkv`): de positie blijft (±1 s);
  5. kwaliteit 720 naar 480: een nieuwe sessie, en de positie blijft;
  6. de kaart "Volgende aflevering" met de aftelling; Esc annuleert;
  7. PiP: `requestPictureInPicture` wordt aangeroepen (geen beeldcontrole headless);
  8. alleen met het toetsenbord, volgens de checklist;
  9. de app herstart midden in het afspelen: de speler loopt binnen 10 s door. En met de
     harnasoptie `outage_s=30` (de app stoppen op pid, 30 s wachten, starten): de speler loopt
     ≤ 10 s na "ready" weer;
  10. geen fouten in de console, geen CSP-meldingen.
- `budget.mjs`: `player` ≤ 35 KB, `mediaRoute` ≤ 45 KB, de eerste lading ≤ 100 KB.

**Klaar als**
- Alle e2e groen in Chromium 141 en in CI.
- Gemeten in e2e (`media_perf`, 10 keer, de mediaan en de slechtste): het eerste beeld bij direct
  ≤ 2 s, bij transcode ≤ 5 s, en zoeken en weer lopen ≤ 3 s.
- Het budget klopt. De checklist voor het toetsenbord is af.

---

### M7 — Voortgang

**Doel.** Verder kijken waar je was, op elk apparaat, en weten wat je al zag.

**Maakt**
- `app/media/progress.py`: de regels van Keuze 36, het geheugen met gebundeld schrijven, en
  `home_rows()` voor `/media/home`.
- `app/routers/media_progress.py`:
  ```
  PUT  /api/v1/media/progress/{node_id}  {position_ms, duration_ms, event, origin, seq, started_at, audio?, subtitle?} → 204
                                         → 429 rate_limited (een tick binnen 5 s) | 404 not_found
  POST /api/v1/media/progress/batch      {node_ids[≤1.000] | show_id + season?, watched: bool} → 200 {updated}
  ```
- Het event `playback.progress`, alleen naar de gebruiker zelf.
- Web: `player/progress.ts` (ticks, pauze, stop, `keepalive`, `origin`, `seq`, `started_at`);
  `media/rows/ContinueRow.tsx` en `NextUpRow.tsx`; `media/progressSync.ts` (luistert naar SSE en
  werkt de lijsten bij, negeert de eigen `origin`).

**Tests**
- De regels: 90 %; de aftiteling; 3 minuten van het eind; korte items; `play_count` één keer per
  sessie; een vervangen bestand.
- Gebundeld schrijven: 20 ticks geven hooguit 2 schrijfacties; pauze schrijft meteen; een "crash"
  verliest hooguit 10 s.
- De grens op ticks. Een event gaat naar dezelfde gebruiker, niet naar een ander. `batch` met
  `show_id` en `season`. Iets wat je niet meer mag lezen: 404.
- De volgorde (Keuze 36): een late `keepalive`-tik na gezien zet gezien niet terug; een tik met
  een oudere `seq` van dezelfde `origin` telt niet; een tik van een sessie van vóór `watched_at`
  telt niet; een nieuwe sessie die verder komt dan 60 s haalt gezien weg.
- e2e `test_media_m7.py`: twee contexts van dezelfde gebruiker. A speelt tot 30 s en pauzeert. Bij
  B staat binnen 2 s "Verder kijken 0:30". B opent het item en begint op 0:30.

**Klaar als.** Synchroniseren ≤ 2 s (p95 van 20 keer, in e2e). Verder kijken en Volgende aflevering
kloppen in de e2e.

---

### M8 — Metadata online

**Doel.** Posters en samenvattingen erbij als Jonas dat wil. Niets breekt als het internet weg is.
De sleutel lekt nooit.

**Maakt**
- `providers/http.py`: de enige uitgaande client (Keuze 39).
- `providers/tmdb.py`, `musicbrainz.py` en `caa.py` (Keuze 40, 41).
- `providers/match.py` (Keuze 42).
- `providers/secrets.py`: AES-GCM, de sleutel via HKDF uit het app-geheim.
- `providers/art.py`: ophalen, controleren, de renderer, `art/<sha>/<maat>.webp`, ondertekende
  adressen, de tijdelijke map voor kandidaten, en het dagelijkse opruimen.
- `lookup.py`: de taken `media.lookup` en `media.art`, met `Defer` bij 429 of geen netwerk.
- `routers/media_match.py`:
  ```
  GET    /api/v1/media/items/{node_id}/candidates?q=&year=  → [{provider, id, title, year, original_title, overview, poster}]
  POST   /api/v1/media/items/{node_id}/match   {provider, id} → Item | 409 media_provider_off | 503 media_provider_unavailable | 503 media_provider_rate
  DELETE /api/v1/media/items/{node_id}/match   → Item (alleen lokaal)
  PATCH  /api/v1/media/items/{node_id}         {title?, sort_title?, year?, overview?, season?, episode?, unlock?} → Item
  POST   /api/v1/media/shows/{show_id}/match   {provider, id}
  GET    /api/v1/media/art/{sha}/{maat}.webp?exp=&sig=
  ```
- `app.cli media-provider-check --opnemen`: met een echte sleutel haalt dit van elk eindpunt één
  antwoord en bewaart alleen de vorm (sleutels en soorten, geen waarden) in
  `docs/fase-5/provider-shapes.json`. Voor Jonas, met de hand (§4.4).
- `tests/media/fake_providers/` (§4.4).
- Web: `MatchDialog` (zoeken met posters, kiezen) en `EditDialog` (velden, en wat vastzit). De
  lijst "Nakijken" op de startpagina van een bibliotheek.

**Tests**
- `test_media_http.py`: een privé-adres na DNS geweigerd; een nep-resolver die de eerste keer een
  publiek adres geeft en de tweede keer `127.0.0.1`: de client verbindt met het eerste adres en
  zoekt niet opnieuw op; TLS controleert de naam via `sni_hostname`; een omleiding naar een
  vreemde host geweigerd; de omleiding van CAA gevolgd, met een nieuwe controle van het adres;
  `Retry-After` met het plafond; de stroomonderbreker; een plaatje van 50 MB afgekapt; de
  logregel zonder query.
- Contracttests tegen de nep-server: zoeken, details, seizoen, find; 429 met backoff; 500; traag;
  kapotte JSON; 1 verzoek per seconde naar MusicBrainz (gemeten); de User-Agent.
- `test_media_secrets.py`: de sleutel staat niet in een antwoord, niet in een log (caplog en de
  access-log), niet in een foutmelding van httpx, en niet op `/beheer`. Een foute sleutel wordt niet
  bewaard.
- Offline: de provider is onbereikbaar. Items blijven `local`; de melding pas na 24 uur (nepklok).
- `test_media_match.py`: de score, scene-namen, een id uit de naam, `review`.
- `test_media_art.py`: alleen de toegestane types; groter dan 10 MB geweigerd; WebP in drie maten via
  de renderer; opruimen zonder verwijzing.

**Klaar als.** Met de nep-server: een bibliotheek van 20 films met scene-namen krijgt er ≥ 18 vanzelf
goed, en de rest staat onder "Nakijken". Handmatig koppelen werkt. De sleutel verlaat de server
alleen in de header naar TMDB.

---

### M9 — Muziek en gapless

**Doel.** Albums, artiesten en afspeellijsten, en nummers die naadloos op elkaar aansluiten.

**Maakt**
- `music.py`: albums groeperen, compilaties, sorteren en de lijst van artiesten (Keuze 37).
- `playlists.py`.
- `worker/ops/audio_info.py` (Keuze 18): eigen lezers, begrensd. Leest alleen de kop en de laatste
  pagina van Ogg. M9 registreert de stap `audio_info`.
- `worker/ops/remux_audio.py` (Keuze 38). (spike) `-c:a copy` voor FLAC en AAC, `-c:a flac` voor
  Opus, ALAC en WAV (S8).
- `routers/media_music.py`:
  ```
  GET    /api/v1/media/libraries/{id}/albums?sort=title|year|added&cursor=   → {items: [AlbumCard], next}
  GET    /api/v1/media/albums/{id}                    → {album, tracks: [Track]}
  GET    /api/v1/media/libraries/{id}/artists         → [{key, name, album_count}]
  GET    /api/v1/media/artists/{key}?library=         → {name, albums: [AlbumCard]}
  GET    /api/v1/media/playlists                      → [{id, name, item_count, duration_ms}]
  POST   /api/v1/media/playlists                      {name} → 201
  GET    /api/v1/media/playlists/{id}                 → {playlist, items: [{id, node_id, track}]}
  PATCH  /api/v1/media/playlists/{id}                 {name}
  DELETE /api/v1/media/playlists/{id}                 → 204
  POST   /api/v1/media/playlists/{id}/items           {node_ids[], position?} → {items} | 409 media_playlist_full
  PUT    /api/v1/media/playlists/{id}/order           {item_ids[]} → 204
  DELETE /api/v1/media/playlists/{id}/items/{item_id} → 204
  ```
  Een nummer dat je niet meer mag lezen, staat niet in het antwoord (gefilterd met
  `access.resolve`).
- `routers/stream_audio.py`: `GET /api/v1/stream/{sid}/audio.mp4`, via `sessions.guard` en de
  iterator van M3 die per stuk de toegang nakijkt (Keuze 21).
- Web `music/**`: albums, album, artiesten, artiest, afspeellijsten en afspeellijst; de muziekbalk;
  `queue.ts` en `gapless.ts`.

**Tests**
- Groeperen: compilatie, geen albumartiest, meerdere discs, dezelfde albumnaam bij twee artiesten.
- `audio_info` op fixtures: FLAC 882.000 samples, MP3 met LAME 882.000, Opus met pre-skip, AAC met en
  zonder `iTunSMPB`; fuzz. (spike) MP3: delay = `enc_delay`, zonder +529; Opus: `samples` =
  laatste granule − `pre_skip`, en delay 0 na FLAC (S8).
- Afspeellijsten: alleen van jezelf, 5.000, de volgorde, en een verwijderd bestand (CASCADE).
- e2e `test_media_m9.py`, gapless: een album van 3 FLAC- en 3 Opus-nummers uit één doorlopende sinus.
  De uitvoer gaat via een `MediaElementAudioSourceNode` naar een AudioWorklet. Bij elke overgang mag
  de fase niet springen (fout ≤ 1 sample). MP3 en AAC: het resultaat wordt vastgelegd, niet geëist.
  (spike) In S8 was MP3 met delay = `enc_delay` exact in Chromium 141; de e2e legt het vast.
  Werkt een AudioWorklet headless niet, dan de terugval: `buffered` is één bereik, en de grenzen van
  `appendWindow` kloppen op het sample [niet getest].
- De muziekbalk blijft spelen bij navigeren.

**Klaar als.** Gapless bewezen voor FLAC en Opus in Chromium 141. Albums, artiesten en
afspeellijsten werken met het toetsenbord.

---

### M10 — Beheer en uitrol

**Doel.** Jonas ziet wat media doet en stelt het bij. Bijwerken blijft één commando en breekt niets.

**Maakt**
- `media/admin.py` (`MediaConfig`), `media/monitor.py` (meldingen), `routers/admin_media.py`
  (Keuze 45).
- `media/status.py`: `app.cli media-status [--wait N] [--sessions]`.
- `media/loadtest.py`: de belastingtest in de app (§4.6), met `POST` en `GET
  /api/v1/admin/media/loadtest`, een knop op `/beheer`, en `app.cli media-loadtest`. De bronnen
  maakt de worker met `synth` (`worker/ops/synth.py`); de sessies gaan via
  `SessionSource = LoadtestFile` (beide bevroren in M0).
- `usage.py`: het deel `media`.
- Web: `admin/MediaTab.tsx`, met de import (bronmap, doelmap, eigenaar, kopiëren of verplaatsen).
- `install.sh` (in dezelfde stijl als office):
  ```bash
  # De eerste regels van main, vóór er een .env gelezen wordt (zoals OFFICE)
  want_media() {
    case "${MEDIA_SANDBOX-}" in
      "" | aan | uit) printf '%s' "${MEDIA_SANDBOX-}" ;;
      *) echo "MEDIA_SANDBOX moet aan of uit zijn (nu: $MEDIA_SANDBOX)." >&2; return 1 ;;
    esac
  }
  media_supported() { [ -f "$SRC/infra/single/compose.media.yaml" ]; }
  # Na het maken of lezen van .env; een bestaande waarde blijft, een gevraagde vervangt hem
  merge_media_env() {
    add_key MEDIA_SANDBOX aan
    add_key MAX_UPLOAD_BYTES 53687091200
    [ -z "$1" ] || set_key MEDIA_SANDBOX "$1"
  }
  # Zet MEDIA_ON (1 of 0) en MEDIA_REASON. Breekt nooit af.
  decide_media() {
    MEDIA_ON=0 MEDIA_REASON=""
    if ! media_supported; then
      MEDIA_REASON="deze versie van het platform heeft nog geen media"
    elif [ "${MEDIA_SANDBOX:-aan}" = uit ]; then
      MEDIA_REASON="MEDIA_SANDBOX=uit: ffmpeg draait zonder afscherming in de app"
    else
      MEDIA_ON=1
    fi
  }
  # De mappen voor media en de import, van de gebruiker van de app
  media_dirs() { install -d -m 750 -o 10001 -g 10001 "$DATA_ROOT/media" "$DATA_ROOT/import"; }
  # compose_files krijgt een tweede argument: $1 = office, $2 = media
  #   if [ "$2" = 1 ]; then
  #     compose+=(-f "$dir/compose.media.yaml")
  #     if limits_supported; then compose+=(-f "$dir/compose.media-limits.yaml"); fi
  #   fi
  # Na de app: de worker. Lukt het niet, dan draait de app zonder (alleen direct play) en zegt
  # het script hoe het verder kan. Nooit exit 1.
  media_start() {
    [ "$MEDIA_ON" = 1 ] || return 0
    if ! "${compose[@]}" up -d --no-deps media; then
      echo "Let op: de mediaworker kon niet starten (de melding van Docker staat hierboven)."
      echo "Afspelen werkt alleen direct. Nooduitgang: MEDIA_SANDBOX=uit (zonder afscherming)."
      return 0
    fi
    "${compose[@]}" exec -T platform python -m app.cli media-status --wait 20 </dev/null \
      || echo "Let op: media meldt een probleem (zie hierboven); het platform werkt verder."
  }
  ```
  - In `main`: `want_media` naast `want_office`; `merge_media_env` in `instellingen`;
    `media_dirs`; `decide_media` na `decide_office`; `compose_files "$OFFICE_ON" "$MEDIA_ON"`.
  - Vóór het vervangen van de app: `media-status --sessions`, alleen een waarschuwing.
  - De app start als eerste: `up -d --build --remove-orphans platform`. Daarna `media_start`, dan
    `office_start`. Pas daarna gaat het oude app-image weg (`old_app`), want de oude
    media-container draaide er tot `media_start` nog op.
  - Bij `MEDIA_SANDBOX=uit` staat `compose.media.yaml` niet in de lijst. De app krijgt dan geen
    `PLATFORM_MEDIA_WORKER` (de lokale worker), en `--remove-orphans` haalt de container weg.
  - `slot` meldt "Media: aan (afgeschermd)" of de reden waarom niet.
- `.env.example`: `MEDIA_SANDBOX` en `MAX_UPLOAD_BYTES`, met uitleg.
- `infra/single/tests/test_install_media.sh`:
  - een `MEDIA_SANDBOX` uit de omgeving wint;
  - een foute waarde stopt vóór er iets verandert;
  - `decide_media` stopt nooit (`set -e`);
  - `docker compose config -q` voor alle 8 combinaties (office aan/uit × media aan/uit × grenzen
    ja/nee);
  - na twee updates is er één image `platform:local`, en de oude id bestaat niet meer;
  - `compose.media.yaml` zet `stop_grace_period: 30s` voor `platform` en `user: media` voor de
    worker.
- `docs/runbooks/media.md`: de status lezen; de nooduitgang; terugrollen (de volgorde van
  Keuze 47); de cache opruimen; de TMDB-sleutel; de import (de map, `chown -R 110001:110001` op
  de Proxmox-host, de controle met `ls -ln` in de LXC, kopiëren of verplaatsen); de
  belastingtest op de LXC (§4.6); "later: GPU" (wat Jonas op de host zou moeten doen, en dat het
  nooit een eis is).

**Tests**
- `test_admin_media.py`: alleen beheerders; de configuratie heeft grenzen (`cpu_budget` 0,5–16;
  `max_transcodes` ≤ het plafond uit env); Stop sluit de sessie; de sleutel komt nooit terug.
- `test_media_monitor.py`: elke melding aan en weer uit.
- `test_media_status.py`: de uitvoer en de exitcode (0, ook bij een probleem met `--wait`).
- `test_media_loadtest.py` (nep-worker): het rapport, de weigering als er echte transcodes lopen, de
  stop bij een vierde ffmpeg, en de fasen van §4.6.
- `test_media_synth.py`: alleen bronnen uit de vaste lijst; een onbekende naam of parameter wordt
  geweigerd; de uitvoer gaat naar de fd van de app; alleen een beheerder met `MEDIA_LOADTEST=1`.
- e2e `test_media_m10.py`: het tabblad met het toetsenbord (Alt+8); een sessie zien en stoppen (de
  speler meldt het netjes); een import starten.
- **De installatietest** (zoals `test_install_update.sh` van fase 4, in de sandbox met dockerd).
  Het image in de sandbox krijgt een leeg ffmpeg-pakket (`test_install_update.sh:53–55`). Wat
  ffmpeg nodig heeft, bewijst daarom de CI-job `media-image` (§4.8). Per omgeving:

  | Stap | Sandbox | CI (`media-image`) |
  |---|---|---|
  | 1. 0.6.0 installeren (commit `263abd7`), een film uploaden | ja | ja |
  | 2. bijwerken naar deze versie: de app en de worker draaien | ja; direct play werkt, `/beheer` toont `media_ffmpeg_missing` | ja; plus een transcode via curl, en `docker top` (app: geen ffmpeg, media: één) |
  | 3. `MEDIA_SANDBOX=uit` en opnieuw: de container is weg, de lokale worker draait | ja | ja |
  | 4. terug naar 0.6.0, met precies de commando's van Keuze 47 | ja; schemaversie 0007 en de app 0.6.0 na de start | — |

**Klaar als.** Bijwerken en terugrollen zijn bewezen in de installatietest, per omgeving zoals in
de tabel. `install.sh` stopt in geen enkel geval door media. `/beheer` toont alles van Keuze 45.

---

## 3. Fundament (M0, eerst en alleen)

M0 raakt alle hotspots en legt de contracten vast. Werkers:

- **M0-api** begint met de spike (§3.7, dag 1–2). De eerste commit zet de CI-job `media-spike`
  in `ci.yml` (op `pull_request`), zodat de spike in het echte image draait vóór er iets
  bevriest. Daarna het schema, de contracten, de kern van de worker, de reparaties F1–F4, de
  rijen en de haken;
- **M0-web** tegelijk: routes, haken, types, codes en budget;
- **M0-e2e** daarna: het harnas, de fixtures, de nep-worker, de nep-stream, de kern van de
  nep-providers, de HLS-lint en de CI-jobs.

Eén PR op de fase-5-branch, getagd `fase5-basis`. Daarna is de app zonder worker, of met
`MediaConfig.enabled = false`, precies 0.6.0 plus F1–F4.

*Noot bij de bouw van M0:* de schakelaar is `PLATFORM_MEDIA_ENABLED` (standaard uit). Zonder die
vlag start er geen worker en geen rij van media, en geeft elk eindpunt van media 503 `media_off`
(`test_media_off.py`). Wat M0 bouwde en waar het afwijkt, staat in [m0.md](m0.md).

### 3.1 Migratie 0008 (`0008_media.py`, down_revision `0007`)

Alleen nieuwe tabellen. Geen batch, geen backfill. Met een downgrade. Alle tabellen beginnen met
`media_`, behalve `playback_state` (de naam uit het stappenplan). `album` bestaat al voor foto's.

```
media_library
  id            UUID PRIMARY KEY
  owner_id      UUID NOT NULL REFERENCES user(id) ON DELETE CASCADE
  root_id       UUID NOT NULL UNIQUE REFERENCES node(id) ON DELETE CASCADE
  kind          VARCHAR(8)   NOT NULL                 -- movies | shows | music | videos
  name          VARCHAR(255) NOT NULL
  language      VARCHAR(16)  NOT NULL DEFAULT 'nl-NL'
  online        BOOLEAN NOT NULL DEFAULT 0            -- online opzoeken (Keuze 39)
  scan_state    VARCHAR(8)   NOT NULL DEFAULT 'idle'  -- idle | queued | running | failed
  scanned_at    DATETIME NULL
  item_count    INTEGER NOT NULL DEFAULT 0
  created_at    DATETIME NOT NULL
INDEX ix_media_library_owner (owner_id)

media_probe                                           -- per inhoud (Keuze 6, 15)
  blob_sha        VARCHAR(64) PRIMARY KEY REFERENCES blob(sha256) ON DELETE CASCADE
  version         INTEGER NOT NULL                    -- PROBE_VERSION
  state           VARCHAR(8)  NOT NULL                -- ready | failed
  container       VARCHAR(32) NULL                    -- mp4 | matroska | webm | avi | mpegts | mp3 | flac | ogg | …
  duration_ms     INTEGER NULL
  bit_rate        INTEGER NULL
  start_ms        INTEGER NOT NULL DEFAULT 0          -- start_time van de bron (S, Keuze 22)
  streams         JSON NOT NULL DEFAULT '[]'          -- Keuze 15, met codec_string (Keuze 16)
  chapters        JSON NOT NULL DEFAULT '[]'
  tags            JSON NOT NULL DEFAULT '{}'          -- muziektags (Keuze 10)
  keyframes       BLOB NULL                           -- zlib(varint-delta's in ms), ≤ 48 KB (Keuze 17)
  keyframes_state VARCHAR(8) NOT NULL DEFAULT 'none'  -- none | ready | sparse | failed
  max_gop_ms      INTEGER NULL
  subs_state      VARCHAR(8) NOT NULL DEFAULT 'none'  -- none | pending | ready | failed | skip
  trick_state     VARCHAR(8) NOT NULL DEFAULT 'none'  -- idem
  trick           JSON NULL                           -- {interval, w, h, cols, rows, count} (Keuze 30)
  audio_state     VARCHAR(8) NOT NULL DEFAULT 'none'  -- idem
  audio           JSON NULL                           -- {rate, samples, delay, padding, source} (Keuze 18)
  truncated       JSON NOT NULL DEFAULT '{}'          -- wat de grens van 64 wegliet: {streams, chapters, attachments}
  error           VARCHAR(200) NULL
  probed_at       DATETIME NOT NULL

media_show
  id            UUID PRIMARY KEY
  library_id    UUID NOT NULL REFERENCES media_library(id) ON DELETE CASCADE
  folder_id     UUID NULL REFERENCES node(id) ON DELETE SET NULL    -- de map van de serie
  title         VARCHAR(255) NOT NULL
  sort_title    VARCHAR(255) NOT NULL
  year          INTEGER NULL
  overview      TEXT NULL
  ids           JSON NOT NULL DEFAULT '{}'            -- {tmdb, imdb, tvdb}
  art           JSON NOT NULL DEFAULT '{}'
  locked        JSON NOT NULL DEFAULT '[]'
  match_state   VARCHAR(8) NOT NULL DEFAULT 'local'   -- local | matched | review | manual
  candidates    JSON NOT NULL DEFAULT '[]'            -- de 5 beste bij review (Keuze 42)
UNIQUE (library_id, folder_id)

media_album
  id            UUID PRIMARY KEY
  library_id    UUID NOT NULL REFERENCES media_library(id) ON DELETE CASCADE
  key           VARCHAR(255) NOT NULL                 -- genormaliseerd albumartiest|titel (Keuze 37)
  title         VARCHAR(255) NOT NULL
  sort_title    VARCHAR(255) NOT NULL
  album_artist  VARCHAR(255) NOT NULL
  year          INTEGER NULL
  compilation   BOOLEAN NOT NULL DEFAULT 0
  mbid          VARCHAR(36) NULL
  art           JSON NOT NULL DEFAULT '{}'
  match_state   VARCHAR(8) NOT NULL DEFAULT 'local'
  track_count   INTEGER NOT NULL DEFAULT 0
  duration_ms   INTEGER NOT NULL DEFAULT 0
UNIQUE (library_id, key)

media_item                                            -- per bestand in een bibliotheek
  node_id       UUID PRIMARY KEY REFERENCES node(id) ON DELETE CASCADE
  library_id    UUID NOT NULL REFERENCES media_library(id) ON DELETE CASCADE
  kind          VARCHAR(8) NOT NULL                   -- movie | episode | track | video | extra
  extra         VARCHAR(12) NULL                      -- trailer | featurette | deleted | behind | sample | other
  parent_id     UUID NULL REFERENCES media_item(node_id) ON DELETE SET NULL   -- de film van een extra
  present       BOOLEAN NOT NULL DEFAULT 1            -- 0 = in de prullenbak (Keuze 5)
  title         VARCHAR(255) NOT NULL
  sort_title    VARCHAR(255) NOT NULL
  year          INTEGER NULL
  show_id       UUID NULL REFERENCES media_show(id) ON DELETE SET NULL
  season        INTEGER NULL                          -- NULL bij absolute nummering (Keuze 8)
  episode       INTEGER NULL
  episode_end   INTEGER NULL
  absolute      INTEGER NULL                          -- absoluut nummer (Keuze 7)
  aired         DATE NULL
  album_id      UUID NULL REFERENCES media_album(id) ON DELETE SET NULL
  disc          INTEGER NULL
  track         INTEGER NULL
  artist        VARCHAR(255) NULL
  part          INTEGER NULL
  edition       VARCHAR(64) NULL
  overview      TEXT NULL
  ids           JSON NOT NULL DEFAULT '{}'
  art           JSON NOT NULL DEFAULT '{}'            -- {poster: {node} | {art}, backdrop: …}
  subs          JSON NOT NULL DEFAULT '[]'            -- externe: [{node, lang, forced, sdh, default}]
  source        VARCHAR(8) NOT NULL DEFAULT 'name'    -- name | nfo | tags | provider
  confidence    REAL NOT NULL DEFAULT 0               -- van de parser (Keuze 7)
  names_version INTEGER NOT NULL DEFAULT 0            -- NAMES_VERSION bij het laatste ontleden
  locked        JSON NOT NULL DEFAULT '[]'
  match_state   VARCHAR(8) NOT NULL DEFAULT 'local'
  candidates    JSON NOT NULL DEFAULT '[]'
  added_at      DATETIME NOT NULL
  updated_at    DATETIME NOT NULL
INDEX ix_media_item_lib   (library_id, present, kind, sort_title)
INDEX ix_media_item_added (library_id, present, added_at)
INDEX ix_media_item_show  (show_id, season, episode)
INDEX ix_media_item_album (album_id, disc, track)
INDEX ix_media_item_parent (parent_id)

playback_state
  user_id       UUID NOT NULL REFERENCES user(id) ON DELETE CASCADE
  profile       VARCHAR(36) NOT NULL DEFAULT ''       -- in fase 5 altijd '' (Vraag 6)
  node_id       UUID NOT NULL REFERENCES node(id) ON DELETE CASCADE
  position_ms   INTEGER NOT NULL DEFAULT 0
  duration_ms   INTEGER NOT NULL DEFAULT 0
  watched       BOOLEAN NOT NULL DEFAULT 0
  play_count    INTEGER NOT NULL DEFAULT 0
  audio         INTEGER NULL
  subtitle      VARCHAR(48) NULL
  blob_sha      VARCHAR(64) NULL                      -- geen FK: alleen om een vervangen bestand te zien
  origin        VARCHAR(36) NULL                      -- het tabblad of apparaat van de laatste stand
  seq           INTEGER NOT NULL DEFAULT 0            -- de laatste seq van die origin (Keuze 36)
  play_started_at DATETIME NULL                       -- started_at van de sessie van de laatste stand
  watched_at    DATETIME NULL                         -- de laatste overgang naar gezien of end
  updated_at    DATETIME NOT NULL
PRIMARY KEY (user_id, profile, node_id)
INDEX ix_playback_recent (user_id, profile, watched, updated_at)

media_playlist
  id            UUID PRIMARY KEY
  owner_id      UUID NOT NULL REFERENCES user(id) ON DELETE CASCADE
  name          VARCHAR(255) NOT NULL
  created_at    DATETIME NOT NULL
  updated_at    DATETIME NOT NULL
INDEX ix_media_playlist_owner (owner_id)

media_playlist_item
  id            UUID PRIMARY KEY
  playlist_id   UUID NOT NULL REFERENCES media_playlist(id) ON DELETE CASCADE
  node_id       UUID NOT NULL REFERENCES node(id) ON DELETE CASCADE
  position      INTEGER NOT NULL
  added_at      DATETIME NOT NULL
INDEX ix_media_playlist_item (playlist_id, position)
```

- **De identiteit** (Keuze 6):
  - de inhoud per sha (`media_probe`): twee kopieën delen één probe, één keyframe-index, één set
    ondertitels en sprites;
  - het item en de voortgang per node: een kopie in een andere map is een eigen item.
- Een bestand definitief weg: item, voortgang en afspeellijstregels gaan mee (CASCADE). Een blob
  weg: de probe gaat mee, en `on_discard` ruimt de bestanden in `media/` op.
- `present` en geen join op de prullenbak: zo blijft een lijst één index-scan. Een map hoger in de
  prullenbak zou anders per lijst een recursieve query vragen. De scanner houdt `present` bij.
- Waarden in het Engels, zoals de rest van het schema (`queued`, `read`). De UI vertaalt ze.
- Geen tabel voor apparaten, voorkeuren, de cache of "vuile" nodes (Keuze 50).
- De downgrade haalt de tabellen weg in omgekeerde volgorde. Daarna is de database gelijk aan
  0007, en start 0.6.0 weer (`app.cli db-downgrade 0007`).
- `models.py` krijgt de klassen, met commentaar over de staten en de keuzes.

### 3.2 Instellingen en geheimen

`config.py` (alles met voorvoegsel `PLATFORM_`):
```python
# Media (fase 5)
media_worker: str = ""                 # PLATFORM_MEDIA_WORKER: de socket van de worker-container; leeg = lokale worker (Keuze 13)
media_max_transcodes: int = 3          # plafond; /beheer kan alleen lager (Keuze 26)
media_job_memory_mb: int = 1200        # per ffmpeg; daarboven stopt de worker hem (Keuze 12)
media_job_workers: int = 1             # workers van de rij media (Keuze 14)
media_max_procs: int = 10              # PLATFORM_MEDIA_MAX_PROCS: ffmpeg en ffprobe samen in de worker; harnas 3 (Keuze 12)
media_loadtest: bool = False           # PLATFORM_MEDIA_LOADTEST: Op.SYNTH en LoadtestFile toestaan (§4.6)
media_scan_tick: float = 30            # de lus van de scanner (tests: 0.5)
media_access_cache: float = 15         # seconden dat een toegangscontrole meegaat (Keuze 21)
media_segment_wait: float = 20         # seconden wachten op een segment (Keuze 25)
media_bench_fixed: bool = False        # tests: de kostentabel van de proef, geen meting
media_tmdb_url: str = "https://api.themoviedb.org"
media_tmdb_image_url: str = "https://image.tmdb.org"
media_musicbrainz_url: str = "https://musicbrainz.org"
media_caa_url: str = "https://coverartarchive.org"
media_provider_private_ok: bool = False   # alleen tests: de nep-providers op 127.0.0.1 (Keuze 39)
```
- *Noot bij de bouw van M0:* daarboven staat `media_enabled: bool = False`
  (`PLATFORM_MEDIA_ENABLED`): media staat alleen aan met die vlag ([m0.md](m0.md), afwijking 1).
  `media_worker` houdt de betekenis hierboven.
- `max_upload_bytes` bestaat al. Compose zet hem uit `.env`:
  `PLATFORM_MAX_UPLOAD_BYTES: ${MAX_UPLOAD_BYTES:-53687091200}` (M10).
- In `.env` komen alleen `MEDIA_SANDBOX` en `MAX_UPLOAD_BYTES` (M10). Geen geheim.
- De rest staat in `Setting "media"` (`MediaConfig`, Keuze 45) en kan op `/beheer`. In het
  geheugen, zoals `SecurityPolicy` en `OfficeConfig`.
- Geheimen staan alleen in `Setting "media_secrets"`, versleuteld (Keuze 40). Nooit in `.env`,
  nooit in het repo, nooit in een log.

### 3.3 Python-contracten (frozen)

```python
# app/media/wire.py: het protocol met de worker (Keuze 12)
PROTOCOL = 1
MAX_MESSAGE = 64 * 1024
class Op(StrEnum): CAPS; BENCH; PROBE; KEYFRAMES; AUDIO_INFO; NFO; FRAME; META; SUBS; SPRITES; STREAM; REMUX_AUDIO; SYNTH
class WorkClass(StrEnum): TRANSCODE; REMUX; BACKGROUND; SHORT; CONTROL   # de plaatsen van Keuze 12
async def send(sock, message: dict, fds: Sequence[int] = ()) -> None    # lengte + JSON, fd's als SCM_RIGHTS
async def receive(sock) -> tuple[dict, list[int]]                       # weigert > MAX_MESSAGE en te veel fd's

# app/media/worker/args.py: de argumenten per opdracht (pydantic; de client gebruikt dezelfde klassen)
class StreamArgs(BaseModel):
    mode: Literal["remux", "transcode"]
    demuxer: Demuxer                      # uit de witte lijst (render.container plus mp3, flac, aac)
    start_s: float                        # origin + 2k (transcode; spike, was 2k) of b_k − 0,5 (remux); 0 vanaf het begin
    source_start_s: float                 # S, de start_time van de bron
    video_index: int
    audio_index: int | None               # None: de bron heeft geen audio (geen -map en geen -c:a)
    video: Literal["copy"] | VideoEncode  # codec (libx264 | libvpx-vp9), width, height, preset (veryfast | superfast),
                                          #   level, maxrate_k, fps_max (60), tonemap (pq | hlg | None), deinterlace, burn_index
                                          #   (spike) burn_index komt uit een tweede fd van dezelfde blob, vanaf
                                          #   burn_start_s = max(0, start_s − 30) (Keuze 29)
    audio: Literal["copy", "none"] | AudioEncode  # codec (aac | libopus), channels (2), bitrate_k
    grid: Literal["2s"] | None            # None bij remux: dan groepeert de app (Keuze 23)
    hvc1: bool = False                    # -tag:v hvc1 bij HEVC
    background: bool = False              # nice 15 + ionice
# en ProbeArgs, KeyframesArgs, AudioInfoArgs, NfoArgs, FrameArgs, MetaArgs, SubsArgs, SpritesArgs,
# RemuxAudioArgs, BenchArgs: elk alleen getallen, vaste woorden en indexen. Nooit een pad of vrije tekst.
class SynthArgs(BaseModel):               # alleen voor de belastingtest (§4.6)
    source: Literal["testsrc2", "cellauto"]
    seed: int                             # 0..9; cellauto: random_seed, testsrc2: de ruis
    duration_s: int                       # 30..600
    height: Literal[1080, 720]

# app/media/client.py: de enige weg naar de worker
class WorkerDown(Exception): ...          # geen verbinding, of een andere PROTOCOL
class WorkerBusy(Exception): work_class: WorkClass   # de app maakt er 503 media_busy {reason: worker} van
class WorkerFailed(Exception): code: str; detail: str
class Job:
    async def events(self) -> AsyncIterator[dict]
    async def result(self) -> dict
    async def cancel(self) -> None        # de verbinding dicht = killpg in de worker
async def call(op: Op, args: BaseModel, *, source: Path | None = None, outputs: int = 0) -> tuple[Job, list[int]]
    # opent source zelf (O_RDONLY | O_NOFOLLOW | O_CLOEXEC), maakt `outputs` pipes, stuurt de
    # schrijfkanten mee, sluit de eigen kopieën, en geeft de leeskanten terug (D7)
    # (spike) per opdracht een eigen open(), bij inbranden twee (elk een eigen positie); een fd
    # gaat nooit naar twee opdrachten (S3, Keuze 12)
async def caps() -> WorkerCaps
async def probe(source: Path) -> Probe

# app/media/state.py: op app.state.media (één proces)
@dataclass
class MediaState:
    worker: Literal["starting", "ok", "down", "ffmpeg_missing"]
    mode: Literal["container", "local"]
    since: datetime
    caps: WorkerCaps | None
    bench_factor: float                   # 1,0 tot er gemeten is (Keuze 26)
    sessions: "SessionTable"              # M3
    admission: "Admission"                # M4

# app/media/types.py
class Stream(BaseModel): ...              # de velden van Keuze 15
class Probe(BaseModel): ...               # de kolommen van media_probe, zonder de bytes van de index
class ClientProfile(BaseModel): ...       # Keuze 20: v 1, witte lijst, ≤ 64 strings, ≤ 8 KB, onbekend weg
class Choice(BaseModel):
    audio: int | None = None; subtitle: str | None = None
    quality: Literal["auto", "source", 1080, 720, 480] = "auto"
    start_ms: int = 0; hdr: Literal["tonemap", "raw"] = "tonemap"
class Policy(BaseModel): public: bool; transcode: bool; max_height: int
class Rung(BaseModel): height: int; preset: Literal["veryfast", "superfast"]; codec: Literal["h264", "vp9"]
class Plan(BaseModel):
    mode: Literal["direct", "remux", "transcode", "audio"]
    reasons: list[str]                    # de vaste codes van Keuze 19
    video: Literal["copy"] | Rung | None
    audio: Literal["copy"] | AudioEncode | None
    burn: int | None; tonemap: Literal["pq", "hlg"] | None
    forced_available: str | None          # "e<i>": een geforceerd beeldspoor dat niet vanzelf aangaat (Keuze 29)
    cost: float; places: Literal[1, 2]    # Keuze 26

# app/media/decide.py (M0: signatuur; M3: de regels)
def decide(probe: Probe, profile: ClientProfile, choice: Choice, policy: Policy, budget: BudgetView) -> Plan

# app/media/admission.py (M0-stub; M4)
def view(user_id: UUID, link_id: UUID | None) -> BudgetView          # wat er nu past, voor decide
def reserve(plan: Plan, user_id: UUID, link_id: UUID | None) -> Grant # MediaBusy(reason, retry_after)
def release(grant: Grant) -> None
def snapshot() -> AdmissionView                                       # voor /beheer

# app/media/sessions.py (M0-stub; M3)
SessionSource = Node | LoadtestFile       # LoadtestFile alleen met media_loadtest en een beheerder (§4.6)
async def create(user, source: SessionSource, probe, plan, grant, *, family: UUID | None,
                 link_id=None, replace: str | None = None) -> Session
    # pint (sha, size) van de bron; family is de login die de sessie maakte (voor close_login)
async def create_audio(user, node, probe, *, family: UUID | None) -> Session   # voor M9
async def guard(request: Request, sid: str) -> Session
    # 410 media_session_gone: onbekend of verlopen; {reason: replaced} als de node niet meer naar de gepinde sha wijst
    # 403 media_access_gone {reason}: de toegang is weg, of de app sloot de sessie zelf (grafsteen)
def guarded_body(session: Session, chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]
    # voor /file en audio.mp4: per stuk (≤ 4 MB of 5 s) session.closed en de toegang (cache 15 s)
def close_user(user_id: UUID, keep_family: UUID | None = None) -> int
def close_login(family: UUID) -> int                                  # vanuit revoke_family, naast office_tunnel
def close_node(node_id: UUID) -> int                                  # vanuit files.notify: nieuwe versie, prullenbak, weg
def close_sha(sha: str) -> int                                        # vanuit on_discard, vóór het weggooien
def stop(session_id: UUID, reason: Literal["stopped", "revoked"]) -> bool   # /beheer; laat een grafsteen achter
def list() -> list[SessionView]

# app/media/streams.py (M0-stub geeft 503 media_off; M4)
async def open(session: Session, plan: Plan) -> StreamInfo           # urls, mime, offset, segmenten
async def close(session: Session) -> None

# app/media/analysis.py (M0: het register en de stap probe; de rest per pakket)
def step(name: str, order: int, applies: Callable[[Probe, str | None], bool]) -> Callable
async def enqueue(session, sha: str, *, library_id: UUID | None) -> None   # job media.analyse:<sha>
# probe (M0), keyframes (M4), subs en sprites (M5), audio_info (M9)

# app/media/hooks.py (geen imports van pakketten: zij melden zich aan)
def touched(node_ids: Iterable[UUID], type: str) -> None             # vanuit files.notify*; geen I/O;
                                                                     # roept ook sessions.close_node aan (Keuze 5)
async def on_discard(sha: str) -> None                               # vanuit previews.discard
def register_discard(fn: Callable[[str], Awaitable[None]]) -> None

# app/media/items.py (M0-stub; M2)
async def apply_metadata(session, node_id: UUID, fields: dict, source: str) -> None   # houdt locked aan

# app/media/progress.py (M0-stub geeft lege rijen; M7)
async def home_rows(session, user_id: UUID, library_ids: list[UUID]) -> dict[str, list[Card]]

# app/media/chapters.py (M0-stub geeft None; M5)
def credits_ms(probe: Probe) -> int | None

# app/media/providers/base.py (M0)
class MetadataProvider(Protocol):
    name: str
    async def search(self, kind: str, title: str, year: int | None, lang: str) -> list[Candidate]
    async def details(self, kind: str, id: str, lang: str) -> Details
    async def season(self, show_id: str, n: int, lang: str) -> list[EpisodeDetails]
    async def images(self, kind: str, id: str) -> list[ImageRef]
    async def check(self) -> None                                    # ProviderAuth | ProviderUnavailable
class ProviderAuth(Exception): ...
class ProviderUnavailable(Exception): ...
class ProviderRate(Exception): retry_after: float

# app/jobs.py (M0)
def register(type: str, run: Run, on_dead: OnDead | None = None, max_attempts: int = MAX_ATTEMPTS,
             *, lane: str = "default", timeout: float | None = None) -> None
class Defer(Exception):                  # terug in de rij na `seconds`; de poging telt niet
    def __init__(self, seconds: float) -> None
```

*Noot bij de bouw van M0:* de contracten hierboven staan er zo, met een paar additieve velden en
functies (onder meer `Stream.last_frame_ms`, `client.call(inputs=, fds=)`, `MediaState.enabled`,
`VideoEncode.burn_start_s`, `wire.Code.not_built`, `sessions.close_all`, `runtime.until_stopped`
en `jobs.active_lanes`); de lijst staat in [m0.md](m0.md), afwijking 4. `types.py` heeft ook
`SegmentGrid` (het rooster van Keuze 22, met `origin_ms ≥ 0` en `start_of(0) = 0`) en de modellen
van de eindpunten van §2. `VideoEncode.level` kent 3.0 tot en met 5.1, zoals de tabel van
Keuze 24.

**De commando's van de worker.** Frozen na S1 en S2 (§3.7). `{…}` vult de worker uit gevalideerde
argumenten. Dit zijn de commando's van ontwerp A met de twee fouten van J4 eruit.

(spike) S1 en S2 draaiden op 7.1.5, in het echte image: de commando's blijven in de kern gelijk.
Wat de spike veranderde, staat hieronder met "(spike)": het begin van een herstart
(`-ss (origin + 2k)`), de positie van de fd, het level van 480, inbranden met twee invoeren, VP9
met HDR, geen `-readrate`, Opus via `flac`, en de referentie van `bench`. Dezelfde lijst staat in
[spike.md](spike.md) ("De commando's van de worker na de spike").

Elke invoer:
```
-nostdin -hide_banner -loglevel error -progress pipe:2 -nostats -max_alloc 536870912
-protocol_whitelist fd,pipe -format_whitelist {lijst} -max_pixels 35389440 -f {demuxer} -fd 3 -i fd:
```
- `-max_alloc` (512 MB per blok) en `-max_pixels` (8192×4320) begrenzen wat een kwade bron vraagt.
  4K decodeert er gewoon mee (V6). Daarbovenop zet de worker `RLIMIT_DATA` (Keuze 12).
- (spike) `{lijst}` is `mov,matroska,avi,wav,flv,ogg,asf,mpegts,mpeg,mp3,flac,aac`. `lavfi` en
  `hevc` komen er nooit in. `lavfi` is in 7.1 een invoerapparaat: het staat onder
  `ffmpeg -devices`, niet onder `-demuxers` (S1). Alleen `bench` en `synth` gebruiken het,
  zonder invoer-fd. `hevc` (rauw) alleen in de meting van `f_dec`, op een pipe van de worker.
- (spike) **De positie van de fd.** Een fd uit `SCM_RIGHTS` deelt zijn positie met de app en met
  elk kind. Na ffprobe stond hij op 65536, en ffmpeg las `fd:` vanaf daar (rc 183, S3). De worker
  doet daarom `os.lseek(fd, 0, SEEK_SET)` op elke invoer-fd vóór elke exec, start nooit twee
  kinderen tegelijk op dezelfde fd, en zet de bron met `dup2` op fd 3 (en 4) alleen in het kind.
  De app opent de blob per opdracht opnieuw (Keuze 12).
- (spike) `-ss` staat vóór `-i` en telt in mediatijd (vanaf S).

Transcode, vanaf 0 of na een sprong naar segment k (720p, SDR, audio naar AAC):
```
nice -n 5 ffmpeg {invoer, met -ss (origin + 2k) vóór -i als k > 0 (spike)} -copyts
  -map 0:{V} [-map 0:{A}] -sn -dn -map_metadata -1 -map_chapters -1
  -vf "scale=w={W}:h={H}:flags=bicubic,format=yuv420p" -fpsmax 60
  -c:v libx264 -preset {veryfast|superfast} -crf 23 -maxrate {R}k -bufsize {2R}k
  -profile:v high -level:v {L} -threads 4 -sc_threshold 0 -g 100000
  -force_key_frames "expr:gte(t,n_forced*2)" -forced-idr 1
  [-c:a aac -ac 2 -b:a 160k]
  -output_ts_offset {1−S} -avoid_negative_ts disabled
  -f mp4 -movflags +frag_keyframe+empty_moov+default_base_moof+skip_trailer+frag_discont+negative_cts_offsets
  -fflags +bitexact pipe:1
```
- `[…]` valt weg als de bron geen audio heeft (Keuze 19).
- `-g 100000`: alleen `-force_key_frames` maakt IDR's. Zonder maakt x264 elke 250 beelden er
  zelf één, en bij meer dan 125 fps valt die midden in een segment (V5).
- `-fpsmax 60`: zo klopt het level van de tabel, ook bij een bron van 240 fps (V5).
- `+negative_cts_offsets`: `trun` versie 1, en het eerste beeld valt op `tfdt`. Zonder komt het
  beeld 104 ms na het geluid (V4).
- `t` telt vanaf het begin van de uitvoer (J4). Een herstart gebeurt alleen op 2k. Zo geeft
  dezelfde uitdrukking hetzelfde rooster, vanaf 0 en na elke sprong.
  (spike) Preciezer: `t` telt vanaf het eerste videobeeld van de uitvoer. Het rooster ligt dus op
  mediatijd `origin + 2k`, met `origin` = `start_time` van het videospoor − S (uit de probe, in
  hele ms naar beneden). Een herstart begint met `-ss (origin + 2k)`; met `-ss 2k` schuift het
  rooster `origin` op (spike.md, "Extra"). N komt uit het laatste videobeeld (Keuze 22).
- (spike) Bij MPEG-TS begint de uitvoer na `-ss` op de volgende keyframe van de bron (S2:
  1,02 s te laat): een herstart mist tot één GOP. M4 kiest de aanpak (Keuze 22).
- `+frag_discont` is nodig: zonder begint `tfdt` op 0, ook met `-copyts` (J4). Met de vlag is
  `tfdt` = mediatijd + 1 s.
- `-forced-idr 1`: elk geforceerd keyframe is een IDR. Dan begint elk segment echt opnieuw, en mag
  `EXT-X-INDEPENDENT-SEGMENTS` (S2 controleert NAL-type 5; spike: zo gemeten).
- `-threads 4` is een bovengrens per proces. De kosten meet S10. (spike) S10: 0,744 cores tegen
  0,743 zonder; geen kosten.
- (spike) `{L}`: 1080 4.0 (4.2 boven 30 fps), 720 3.1 (3.2 boven 30 fps), 480 3.0 (3.1 boven
  25 fps). 3.0 is voor 480 te laag vanaf 30 fps (Keuze 24).
- HDR: `-vf` wordt de keten van Keuze 27. Interlaced: `bwdif=…` ervoor. (spike) S1: `zscale` en
  `tonemap` zijn er in 7.1.5, en de keten draait.
- Inbranden:
  `-filter_complex "[0:{V}]{filters}[v];[0:{N}]scale={W}:{H}[s];[v][s]overlay=eof_action=pass,format=yuv420p[out]" -map "[out]"`.
  (spike) Zo mist na een herstart een ondertitel die vóór de keyframe begon (S9). Het wordt twee
  invoeren van dezelfde blob, elk een eigen fd:
  ```
  nice -n 5 ffmpeg {invoer A: -ss (origin + 2k), -fd 3} {invoer B: -ss max(0, origin + 2k − 30), -fd 4} -copyts
    -filter_complex "[0:{V}]{filters}[v];[1:{N}]scale={W}:{H}[s];[v][s]overlay=eof_action=pass,format=yuv420p[out]"
    -map "[out]" [-map 0:{A}] -sn -dn -map_metadata -1 -map_chapters -1 -fpsmax 60
    {de video, de audio en de staart van de transcode}
  ```
  Eerst het beeld schalen, de ondertitel naar W×H, dan `overlay` (S9; Keuze 24). De CPU blijft
  gelijk aan één invoer.
- Zonder H.264 bij de client: `-c:v libvpx-vp9 -deadline realtime -cpu-used 8 -row-mt 1 -b:v
  {R}k -maxrate {R}k -crf 32 -g 100000` en `-c:a libopus -b:a 128k -ac 2 -ar 48000`. VP9 kent
  alleen echte keyframes; `-forced-idr` valt dan weg. Gemeten liggen de keyframes van VP9 ook
  zonder `-g` op 0, 2, 4 en 6 s (V5); `-g 100000` staat er voor de zekerheid. (spike) VP9 met HDR
  hooguit 720: op 1080 haalt het geen realtime op 3 cores (S11).
- (spike) Geen `-readrate`: de tegendruk is genoeg (S6).

Remux (video kopiëren, audio kopiëren of omzetten):
```
ffmpeg {invoer, met -ss (b_k − 0,5) vóór -i als k > 0} -copyts
  -map 0:{V} [-map 0:{A}] -sn -dn -map_metadata -1 -map_chapters -1
  -c:v copy [-tag:v hvc1] [-c:a {copy | aac -ac 2 -b:a 160k | libopus -b:a 128k -ac 2 -ar 48000}]
  -output_ts_offset {1−S} -avoid_negative_ts disabled
  -f mp4 -movflags +frag_keyframe+empty_moov+default_base_moof+skip_trailer+frag_discont+negative_cts_offsets
  -fflags +bitexact pipe:1
```
- Elke GOP is een fragment. De app groepeert op de pts van het eerste sample, met de getekende
  offset uit `trun` v1 (Keuze 23), en gooit na een herstart weg wat vóór `b_k` ligt.
- Zonder `+negative_cts_offsets` komt het eerste beeld 83 ms na het geluid (V4).
- (spike) Geen `-readrate`: met tegendruk was het eerste segment er na 0,10 s, met `-readrate 1`
  pas na 7,46 s (S6). Bij MPEG-TS begint de uitvoer na `-ss` op de volgende keyframe (S2).

Ondertitels (alle tekstsporen in één run, Keuze 29):
```
nice -n 15 ionice -c 3 ffmpeg {invoer} -copyts
  -map 0:{i} -c:s copy -f ass pipe:4    (ASS, SSA)
  -map 0:{j} -c:s copy -f webvtt pipe:5 (WebVTT)
  -map 0:{k} -c:s srt -f srt pipe:6     (de rest)
```
- Alle pipes maakt de app (Keuze 11). Per spoor geeft hij twee mee: een pipe van ffmpeg naar de
  worker (de schrijfkant gaat naar ffmpeg als `pipe:4…`, de leeskant blijft bij de worker), en een
  pipe voor de VTT terug naar de app. De worker zet elk spoor om met `subconv` en schrijft de VTT
  naar die tweede pipe. Zo zijn alle pipes van 10001, niet van de uid van ffmpeg.
- Een los bestand (`.srt`, `.vtt`, `.ass`, `.ssa`): geen ffmpeg. De worker leest de fd (hooguit
  5 MB) en zet om.

Sprites (Keuze 30):
```
nice -n 15 ionice -c 3 ffmpeg -skip_frame nokey {invoer} -map 0:{V} -an -sn -dn
  -vf "fps=1/10,scale=160:-2,tile=10x10" -c:v mjpeg -q:v 5 -f image2pipe pipe:1
```

Eén beeld (previews van video's, een ingebedde hoes):
```
nice -n 15 ffmpeg {invoer, met -ss T vóór -i} -map 0:{V} -frames:v 1 -vf "scale={W}:-2"
  -c:v mjpeg -q:v 3 -f image2pipe pipe:1
```

Een nummer voor gapless (Keuze 38):
```
ffmpeg {invoer} -map 0:a:0 -vn -sn -dn -map_metadata -1 -c:a {copy | flac}
  -f mp4 -movflags +frag_keyframe+empty_moov+default_base_moof -frag_duration 10000000
  -use_editlist 0 pipe:1
```
(spike) `copy` voor FLAC en AAC; `flac` voor Opus (S8: Opus in fMP4 is niet gapless), ALAC en
WAV. MP3 gaat rauw als `audio/mpeg`.

De meting (Keuze 26), zonder invoer:
```
ffmpeg -f lavfi -i "testsrc2=size=1920x1080:rate=24,noise=alls=20:allf=t" -t 10
  -vf "scale=-2:720" -c:v libx264 -preset veryfast -threads 4 -f null -
```
De worker meet de CPU-tijd van het proces (`getrusage` van het kind). Cores voor 1,0× = CPU-tijd /
10 s. (spike) `f` = cores voor 1,0× / de cores van dezelfde bench in de sandbox; die referentie
meet M1. Niet 0,74: dat is de tabelprijs van een bestand, en de bench kost ±2 × zoveel (S7).

En het decoderen van HEVC10 (voor `f_dec`), twee processen met een pipe die de worker maakt:
```
ffmpeg -f lavfi -i "testsrc2=size=1920x1080:rate=24" -t 4 -c:v libx265 -preset ultrafast
  -pix_fmt yuv420p10le -f hevc pipe:1
ffmpeg -f hevc -i pipe:0 -f null -
```
Alleen de CPU-tijd van het tweede proces telt. `f_dec` = (CPU-tijd / 4 s) / 0,31. (spike) Ook
voor `f_dec` meet M1 de referentie van deze bench in de sandbox (S7: 0,82–0,86 op de runner).

Een bron voor de belastingtest (`synth`, §4.6), naar een fd die de app opent in
`media/loadtest/`:
```
nice -n 15 ffmpeg -f lavfi -i "{testsrc2=size=1920x1080:rate=24,noise=alls=20:allf=t |
  cellauto=s=1920x1080:rate=24:rule=110:random_seed={seed},noise=alls=20:allf=t}"
  -f lavfi -i "sine=frequency=440:sample_rate=48000" -t {duur}
  -c:v libx264 -preset ultrafast -b:v 6M -maxrate 6M -bufsize 12M -threads 3
  -c:a aac -b:a 160k -f matroska -fd 4 fd:
```
Gemeten: 50–75 s per bron van 300 s, en de mkv op de fd krijgt Cues (V8). `mandelbrot` en `life`
vallen weg: te traag.

### 3.4 API-contracten, gebeurtenissen en codes (frozen)

De eindpunten staan bij de pakketten (§2). M0 zet ze als routes met de juiste vorm, en de modellen
in `app/media/types.py` en `web/src/media/types.ts`. Een stub geeft 503 `media_off` tot het pakket
hem vult. `fake_stream.py` geeft precies dezelfde vorm (§3.6).

**Codes**, allemaal in `web/src/i18n/errors/media.ts` (nl en en; `test_error_catalog.py`;
*gebouwd:* alleen Engels, zoals elke catalogus in het repo, want het Nederlands komt van de
server, [m0.md](m0.md) afwijking 13):

| Code | Status | Extra | Wanneer |
|---|---|---|---|
| `media_off` | 503 | | media staat uit, of een stub van M0 |
| `media_worker_down` | 503 | | de worker is weg, of heeft een andere `PROTOCOL` (Keuze 13, 47) |
| `media_ffmpeg_missing` | 503 | | de worker draait, maar ffmpeg ontbreekt of `caps` faalt (Keuze 46) |
| `media_probe_pending` | 409 | `Retry-After`, `reason` (`probe`, `keyframes`) | de probe loopt nog na 5 s (Keuze 15); of een publieke link zonder keyframe-index (Keuze 17) |
| `media_unavailable` | 409 | `reason` | de probe mislukte |
| `media_unsupported` | 415 | `reasons` (ook `too_large`) | geen enkele weg naar deze client; of een bron boven 8192×4320 |
| `media_hdr` | 422 | `can_raw`, `reason` (`too_large`, `dolby_vision`, `no_tonemap`) | HDR kan niet omgezet worden (Keuze 27) |
| `media_busy` | 503 | `reason` (`cpu`, `disk`, `user`, `link`, `worker`), `retry_after` | de toelating zegt nee (Keuze 26), of de worker heeft geen plaats (Keuze 12); (gebouwd) ook onder `/stream/` zodra de app stopt, met `worker` en `Retry-After: 2` (F3) |
| `media_session_gone` | 410 | `reason?` (`replaced`) | de sid is onbekend of verlopen, of het bestand is vervangen; de speler vraagt stil een nieuwe |
| `media_access_gone` | 403 | `reason` (`revoked`, `stopped`, `blocked`) | de toegang is weg, of de app sloot de sessie; de speler stopt |
| `media_timeout` | 503 | `Retry-After` | een segment niet binnen 20 s |
| `media_transcode_failed` | 502 | | ffmpeg faalde twee keer op hetzelfde segment |
| `media_public_transcode` | 403 | | een publieke link, en transcode staat uit |
| `media_not_owner` | 403 | | een bibliotheek op een map van een ander |
| `media_library_nested` | 409 | `library_id` | een bibliotheek boven of onder een andere |
| `media_library_not_folder` | 422 | | geen map, of in de prullenbak |
| `media_library_limit` | 422 | `max` | meer dan 20 |
| `media_import_path` | 422 | | een pad buiten `/data/import`, ook via een symlink of een ander bestandssysteem (Keuze 2) |
| `media_import_busy` | 409 | | er loopt al een import |
| `media_bench_busy` | 409 | | er draait al een meting, een belastingtest of een echte transcode |
| `media_provider_off` | 409 | | geen sleutel, of online staat uit |
| `media_provider_auth` | 422 | | de sleutel werkt niet (bij opslaan of later) |
| `media_provider_unavailable` | 503 | | de provider is onbereikbaar of de stroomonderbreker is dicht |
| `media_provider_rate` | 503 | `retry_after` | de provider remt af |
| `media_playlist_full` | 409 | `max` | meer dan 5.000 nummers |

Bestaande codes blijven: `not_found`, `no_room` (quotum vol, ook bij de import), `rate_limited`,
`guest`, `read_only`.

**Meldingen** op `/beheer` (`NOTICES`): `media_worker_down`, `media_ffmpeg_missing`,
`media_cache_full`, `media_sandbox_off`, `media_provider_auth`, `media_provider_failing`.

**Gebeurtenissen** (SSE, via `events.publish`, na de commit):

| Type | Naar | Data |
|---|---|---|
| `playback.progress` | de gebruiker zelf | `{node_id, position_ms, duration_ms, watched, origin, updated_at}` |
| `media.library` | wie de bibliotheek ziet | `{library_id, scan_state, item_count}` |
| `media.item` | wie de bibliotheek ziet | `{node_id, library_id}` |
| `media.import` | beheerders | `{job, state, done, total, skipped, bytes}` |

`affectsFolder` negeert ze. `node.changed` komt al van `files.notify`.

**Koppen:** `X-Init: <gen>` op elk segment (Keuze 22), `Retry-After` bij 409, 503 en 202. Onder
`/stream/` ook `Cross-Origin-Resource-Policy: same-origin` en de `Cache-Control` van Keuze 35.

### 3.5 Hotspots die M0 zelf aanpast

- **F1. Geen databaseverbinding tijdens een stream** (N1). `files.download`, `files.content` en
  `public.download` doen `await session.close()` vlak voor de `FileResponse`. Nu blijft de
  verbinding vast zolang de stream loopt: 16 gepauzeerde streams gaven na 30 s een 500 [gemeten].
  - `test_download_pool.py`: 20 gepauzeerde streams, en `GET /api/v1/files/root` blijft ≤ 200 ms.
  - `/api/v1/stream/{sid}/file` volgt dezelfde regel (regel 8).
- **F2. `HEAD` op de downloadroutes** (N2). `api_route(…, methods=["GET", "HEAD"])`. De
  `FileResponse` van Starlette stuurt bij HEAD alleen de koppen [doc]. `test_download_head.py`.
  *Gebouwd* als `@router.get` met `@router.head(include_in_schema=False)`, zodat de
  operation-id's uniek blijven ([m0.md](m0.md), afwijking 11).
- (spike) S12 mat F1 en F2 vóór de reparatie: 16 gepauzeerde streams gaven na 30,01 s een 500 op
  `GET /files/root` (3 van 3; met 14 streams 200 in 0,02 s), en `HEAD` gaf 404 op alle drie de
  routes. Beide zijn nodig zoals hierboven.
- **F3. Netjes stoppen binnen de grace-periode** (N6). Nu stopt `serve.py` eerst https en pas
  daarna http, en http sluit de lifespan af (`serve.py:337–363`). Zonder office geeft Docker 10 s.
  Een lange stream op https gebruikt die 10 s helemaal, en dan stopt Docker de app met SIGKILL
  vóór de lifespan sluit: voortgang die nog niet geschreven is, gaat verloren, en jobs gaan niet
  terug in de rij. Daarom:
  - bij het begin van het stoppen sluit de app eerst de mediasessies, en geven wachtende
    segmentverzoeken 503 met `Retry-After: 2`;
  - https en http stoppen tegelijk, met één deadline van 10 s voor alles samen
    (`timeout_graceful_shutdown=10` op beide);
  - `compose.media.yaml` zet `stop_grace_period: 30s` voor `platform` (zoals
    `compose.office.yaml` al doet);
  - test: stoppen met één gepauzeerde stream; de logregel van de lifespan verschijnt binnen de
    grace-periode. De speler vernieuwt zelf (Keuze 32).
  - *Noot bij de bouw van M0:* `runtime.begin_stop(app)` loopt als eerste (ook vanuit het
    signaal); daarna geeft `/api/v1/stream/` 503 `media_busy {reason: worker}` met
    `Retry-After: 2`, en een wachten in `runtime.until_stopped` wordt afgebroken. De interne
    listener van office krijgt ook 10 s, en de lifespan van http wacht eerst op https.
    `compose.media.yaml` (met `stop_grace_period: 30s`) komt van M1 ([m0.md](m0.md),
    afwijking 12; `test_serve_stop.py`).
- **F4. `media-src 'self' blob:`** in de CSP (N5). Zonder `blob:` speelt geen enkele MSE-video:
  `video.src` is dan een object-URL. De CSP staat op drie plekken, en alle drie gaan mee:
  - `WEB_CSP` in `main.py`;
  - `web/nginx.conf`: dezelfde CSP staat daar 5 keer (het web-image van de opzet met twee
    containers);
  - `WEB_CSP` in de CI-job `images`, die beide images op dezelfde CSP controleert.

  `api/tests/test_csp_nginx.py` leest de 5 koppen uit `nginx.conf` en eist dat ze gelijk zijn aan
  `main.WEB_CSP`.
- **`jobs.py`** (Keuze 14):
  - `register` krijgt `lane` en `timeout`. `timeout` vervangt `settings.job_timeout` voor die
    soort;
  - een `Runner` per rij: `default` (`job_workers`, zoals nu), `media_scan` (1), `media`
    (`media_job_workers`), `media_net` (1) en `import` (1);
  - `Runner(settings, lane=…, housekeeping=False)` voor elke rij behalve `default`. Nu start
    `Runner.start` altijd `_housekeeping()`; met vijf Runners liep die vijf keer;
  - `claim()` pakt alleen soorten uit zijn eigen rij. De rij `default` pakt alles wat niet in een
    andere rij staat. Een onbekende soort faalt dus zoals nu;
  - `Defer(seconds)`: de taak gaat terug naar `queued` met `run_after = nu + seconds` en
    `attempts − 1`;
  - `test_jobs_lanes.py`: een lange media-taak houdt een preview niet op; met 100 analyses in de
    rij `media` loopt een nieuwe scan binnen één tik; elke housekeeping loopt één keer per
    periode, met alle Runners aan; `Defer` telt niet; de timeout per soort; de bestaande soorten
    veranderen niet.
  - *Noot bij de bouw van M0:* de rijen `media_scan`, `media`, `media_net` en `import` starten
    alleen met media aan (`jobs.active_lanes`); met media uit loopt alleen `default`, zoals in
    0.6.0 ([m0.md](m0.md), afwijking 9).
  - *Noot (M0, review C1):* een taak van media die blijft staan terwijl media uit staat, wacht
    bewust. `jobs.oldest_queued(session, settings)` telt alleen de soorten van de rijen die
    draaien, dus `/admin/status` geeft daarvoor geen `jobs_waiting` en geen `degraded`. Het aantal
    `queued` telt hem wel (hij staat er echt). Zonder `settings` telt `oldest_queued` alles, zoals
    eerst (`/beheer`, Taken).
- **De haken** (één regel elk):
  - `files.notify` en `notify_many` roepen `media.hooks.touched(nodes, type)` aan (synchroon, geen
    I/O). Bij een nieuwe versie, de prullenbak en weghalen sluit die ook de sessies op die node
    (Keuze 5, 21);
  - `auth.py` roept `media.sessions.close_user` aan naast `office_tunnel.close_user`, en in
    `revoke_family` `media.sessions.close_login(family)` naast `office_tunnel.close_login`, na
    dezelfde commit;
  - `previews.discard` roept `media.hooks.on_discard(sha)` aan, na `convert.on_discard`. Die sluit
    eerst de sessies op die sha (`close_sha`);
  - `usage.py` en `routers/admin.py`: een lege haak voor het deel `media` en de meldingen (M10
    vult ze).
  - *Noot bij de bouw van M0:* `files.notify` geeft nieuwe inhoud (een nieuwe of teruggezette
    versie) aan de haak door als `node.version` (de SSE blijft `node.changed`); `node.deleted` en
    `node.version` sluiten de sessies. `usage.media_bytes` geeft 0, zonder nieuw veld in
    `StorageParts`. De web-app vertaalt de meldingen op code (`admin.notice.media_*`)
    ([m0.md](m0.md), afwijking 8 en 14).
- **`main.py`**:
  - de routers (stubs);
  - in de lifespan: `media.runtime.bind(app)`. Dat start de lokale worker of de gezondheidslus,
    de lus van de scanner, maakt `media/sessions/` leeg, en registreert de housekeeping
    (`media.sweep`);
  - `_hide` krijgt het patroon `(/api/v1/stream/+(?:[a-z0-9-]{1,63}\.)?)[^/?\s"]+` → `\1***`
    (Keuze 35, de noot over review S5), naast
    `SECRET_PARAMS` en `LINK_PATH`;
  - F4.
- **`cli.py`**: `media-import`, `media-parse`, `media-status`, `media-provider-check` en
  `media-loadtest`, elk als dunne aanroep van een functie in het pakket (stub tot dan).
- **`config.py`** en **`models.py`**: §3.1 en §3.2.
- **`pyproject.toml`** en **`package.json`**: ongewijzigd. (Gebouwd: `pyproject.toml` krijgt
  alleen de markers `media`, `media_perf` en `provider_real` en `addopts` van pytest, geen
  afhankelijkheid; [m0.md](m0.md), afwijking 16.)
- **Web**:
  - `routes.tsx`: lazy routes `/media`, `/media/bibliotheek/:id`, `/media/item/:node`,
    `/media/serie/:id`, `/kijken/:node`, `/muziek`, `/muziek/album/:id`, `/muziek/artiest/:key`,
    `/muziek/lijst/:id`, met lege pagina's die de pakketten vullen;
  - `App.tsx`: Media en Muziek in het menu, en een plek voor de muziekbalk;
  - de haken: `browser/actions.ts` ("Als bibliotheek gebruiken"), `viewer/MediaView.tsx` (laadt de
    speler voor video; tot M6 de `<video>` van nu), `Beheer.tsx` (tabblad Media, Alt+8);
  - `media/{types,api,index}.ts`: de typen en aanroepen van §2;
  - `i18n/types.ts` (gebieden `media`, `player`, `music`) en `i18n/errors/media.ts`;
  - `budget.mjs`: `mediaRoute` 45, `player` 35, `music` 40 (KB gzip);
  - `pwa/routing.ts`: alleen een test dat `/api/v1/stream/` nooit in de cache komt.

### 3.6 Het harnas, de fixtures en de nep-servers

- **Fixtures: gemaakt in de test, niets binair in het repo.** `api/tests/media/fixtures.py`:
  `make(name) -> Path`. Elk recept is een ffmpeg-commando met lavfi (`testsrc2` en `sine`). Het
  resultaat staat in `.pytest_cache/media/`, op de hash van het recept. Samen ≤ 60 MB, en in CI
  ≤ 30 s om te maken [geschat; de kleinere set in de proef kostte 5,6 s].

  | Fixture | Waarvoor |
  |---|---|
  | `h264_aac.mp4` (720p, 10 s, `moov` vooraan) en `h264_aac_moov_end.mp4` | direct play in CI; remux |
  | `vp9_opus.webm`, `vp9_opus.mkv` (twee Opus-sporen), `av1_opus.mp4` | direct, remux, de speler in Chromium 141 |
  | `hevc10_hdr10.mkv` (x265 Main10, PQ, mastering-gegevens, AC-3 5.1, AAC nl, SRT nl, ASS met `\an8`, 3 hoofdstukken) | transcode, tone-mapping, ondertitels |
  | `h264_ac3_51.mkv` | video kopiëren, audio omzetten |
  | `xvid_mp3.avi` | transcode (oud) |
  | `interlaced.ts` (`-flags +ilme+ildct -top 1`, `start_time` 1,4 s) | bwdif, de verschuiving `S`; (spike) zoeken in MPEG-TS zonder index (S2) |
  | `fps23976.mkv` (H.264 op 24000/1001) | het rooster na een herstart (S2) |
  | `multi_audio.mkv` (nl en en, een geforceerd ondertitelspoor) | audiospoor, geforceerd |
  | `subs_text.mkv` (SRT en ASS), losse `nl.srt` (CP1252), `en.ass`, `.nl.forced.srt`, een SRT in UTF-8 met BOM | ondertitels |
  | `pgs.mkv` (via de eigen `.sup`-schrijver) en `vobsub.mkv` (de eigen `.sup` met `-c:s dvdsub`: beeld naar beeld; uit een SRT kan het niet, V8). (spike) De `.sup` muxen met `-copyts` (anders 2 s te vroeg); VobSub met `-fix_sub_duration` (anders gaat een ondertitel nooit weg; S9) | inbranden |
  | `fps240.mp4` (640×360, 240 fps, 6 s) | één IDR per 2 s en `-fpsmax 60` (V5) |
  | (spike) `late_video.mkv` (video vanaf 0,5 s met `-itsoffset 0.5`, audio vanaf 0; in `fixtures.py` sinds het samenvoegen van M0, en `test_media_probe.py` ziet `origin` 521 ms) | het rooster aan `origin`, het lange eerste segment, de herstart met `-ss (origin + 2k)` (Keuze 22) |
  | `noaudio.mp4` (H.264 zonder audio) | een plan zonder audio, een mime met alleen video, de lint |
  | `h264_hi10p.mkv` (x264 High 10) | Hi10P in decide (Keuze 19) |
  | `a.ts` en `a.m2ts` (H.264 en HEVC, Annex-B) | de codec-strings (Keuze 16) |
  | `hevc2160_hdr10.mkv` (2 s, 3840×2160, PQ) | de weigering `too_large` en de kosten van 4K (Keuze 26, 27) |
  | `long_gop.mkv` (keyframe per 25 s) en `irregular_gop.mkv` | remux geweigerd; de remuxregel |
  | `no_cues.mkv` (naar een pipe gemuxt, dus zonder Cues) | de terugval voor keyframes |
  | `chapters.mkv` (uit `ch.ffmeta`, met "End Credits") | hoofdstukken, de aftiteling |
  | `album/` (3 FLAC, 3 MP3 met LAME, 3 Opus, 3 AAC met en zonder `iTunSMPB`; één doorlopende sinus; tags en een hoes) | gapless, muziektags |
  | `broken.mp4` (afgekapt), `random.mkv` (willekeurige bytes), `empty.mkv` (0 bytes) | nette fouten, fuzz |
  | `loadtest/` (3 × 300 s 1080p H.264, 6 Mbit/s: `testsrc2` en twee keer `cellauto` met een andere seed, elk met ruis) | alleen `media_perf` en de belastingtest; na elkaar gemaakt, ±675 MB, samen ±3 minuten (V8) |

- **`api/tests/media/sup.py`:** een eigen schrijver voor PGS (`.sup`). Hij schrijft de segmenten
  PCS, WDS, PDS, ODS en END met een RLE-bitmap van een blok tekst. ffmpeg heeft geen PGS-encoder,
  maar wel een `sup`-demuxer en kan kopiëren naar mkv [doc; S9]. (spike) S9: werkt, voor PGS en
  VobSub, met `-copyts` bij het muxen.
- **`api/tests/media/fake_worker.py`:** spreekt het protocol van §3.3, over een echte Unix-socket.
  - `probe` geeft opgenomen antwoorden van de echte worker voor elke fixture
    (`tests/media/recorded/probe/*.json`). `python -m tests.media.record` neemt ze opnieuw op als
    `PROBE_VERSION` stijgt.
  - `stream` schrijft vooraf gemaakte fMP4 (één keer per testsessie met echte ffmpeg: 20 s 360p,
    VP9 + Opus en H.264 + AAC, op het rooster van 2 s) naar de echte pipe, in het tempo dat de test
    kiest (`speed`). De tegendruk is dus echt.
  - Fouten op bestelling: `crash_after` (N fragmenten), `hang`, `busy`, `slow_start`, `garbage`,
    `new_stsd` (een andere init na een herstart).
  - Voor de randgevallen van M3, M4 en M10 zonder CPU.
- **`api/tests/media/worker_contract.py`:** scenario's die tegen de nep én tegen de echte (lokale)
  worker draaien: de vorm van de probe; een opdracht van begin tot eind; een stroom met fragmenten
  op volgorde en op het rooster; de verbinding dicht is stoppen (geen proces over); `busy` met de
  soort als een soort vol is; `caps` werkt dan nog; een andere `PROTOCOL`; een te grote
  boodschap; een onbekende opdracht; een verkeerd aantal fd's. Zo blijft de nep eerlijk.
- **`api/tests/media/fake_stream.py`:** een ASGI-app met `play` en `/stream/…` uit segmenten die
  het harnas bij de start maakt met echte ffmpeg. Precies het contract van §3.4. Zo kan M6 bouwen
  voordat M3 en M4 klaar zijn. `media_contract.py` beschrijft de scenario's: M0 draait ze tegen de
  nep, M4 tegen het echte (`test_media_contract.py`).
- *Noot bij de bouw van M0 (M0-e2e):* de nep-worker, `record.py`, `worker_contract.py`, de
  nep-stroom en `media_contract.py` zijn er zo, met deze keuzes ([m0.md](m0.md), afwijkingen
  21–27):
  - de vooraf gemaakte uitvoer van `stream` zijn fixtures: `stream_src.mkv` (20 s H.264 + AAC) en
    `stream_h264.mp4` en `stream_vp9.mp4`, gemaakt met de transcode van §3.3 op 360p, plus een
    `_alt` op 480×270 voor `new_stsd`. Een eigen kleine lezer, `tests/media/boxes.py`, knipt ze;
  - de nep zoekt de bron van `probe` op zijn inhoud (sha256) tussen de fixtures. De opnames zijn
    van ffmpeg 6.1.1 (de sandbox), niet van 7.1.5;
  - de tijden hebben één referentie: mediatijd. Ook bij remux begint segment 0 op 0 en niet op het
    eerste keyframe. Zo is de som van de `EXTINF`'s tot segment n precies het begin `b_n`;
  - `urls` in het antwoord van `play` zijn relatief aan `/api/v1/stream/{sid}/`, net als `init` in
    `map.json`;
  - het contract heeft een haak `pace(speed)`. Een sprong is alleen een herstart als de producent
    er nog niet is; M4 zet daarvoor `fake_worker.speed`;
  - bij MPEG-TS eist het contract na een sprong alleen dat het segment in zijn eigen vak van het
    rooster valt en met een keyframe begint (Keuze 22).
- **`api/tests/media/fake_providers/`:** de nep-server voor TMDB, het plaatjeshost, MusicBrainz en
  de Cover Art Archive (§4.4). M0 maakt de kern (server, fouten op bestelling, logboek, laden van
  data); M8 vult hem. (M0) Eén Starlette-app, gerouteerd op de `Host` of een padvoorvoegsel; in
  de API-tests via `httpx.ASGITransport`, in de e2e als proces (`process.py`) over http op
  127.0.0.1 (dus met `PLATFORM_MEDIA_PROVIDER_PRIVATE_OK=true`).
- **`api/tests/media/hls_lint.py`:** een eigen lint voor onze playlists, naar RFC 8216 en de regels
  van Apple en LG [doc]:
  - `#EXTM3U` eerst, LF, UTF-8 zonder BOM;
  - `EXT-X-VERSION` 7 (fMP4 met `EXT-X-MAP`);
  - elke `EXTINF`, afgerond, ≤ `TARGETDURATION`; `EXT-X-ENDLIST` bij VOD;
  - `CODECS` in elke `EXT-X-STREAM-INF`, met video én audio, of alleen video als de probe geen
    audio heeft; `BANDWIDTH` ≥ de piek;
  - geen `EXT-X-INDEPENDENT-SEGMENTS` met het basisprofiel `webos-5`, en nooit bij remux;
  - alleen relatieve URI's; geen query behalve `g=`.
  - (spike) bij transcode mag alleen het eerste segment langer zijn dan 2 s (`origin + 2`), en
    is `TARGETDURATION` 2 of die duur afgerond; elk ander segment is afgerond ≤ 2
    (`extinf_grid`). Verder eist hij wat §2 M4 zegt: `EXTINF` met 3 decimalen, bij remux
    `TARGETDURATION` = het langste segment naar boven afgerond, één variant met `RESOLUTION`,
    `FRAME-RATE` en `VIDEO-RANGE`, en `EXT-X-INDEPENDENT-SEGMENTS` bij transcode (behalve
    `webos-5`).

  Met eigen tests (goede en kapotte playlists). `test_media_hls.py` (M4) gebruikt hem.
- **`e2e/media_harness.py`** en **`e2e/conftest.py`**:
  - fixture `media`: start de app met de lokale worker (in de CI-job `media-image` met de
    worker-container), en de nep-providers als proces. Zet `PLATFORM_MEDIA_SCAN_TICK=0.5`, de URL's
    van de providers, `PLATFORM_MEDIA_PROVIDER_PRIVATE_OK=true`,
    `PLATFORM_MEDIA_MAX_TRANSCODES=3` en `PLATFORM_MEDIA_MAX_PROCS=3`. Voor `media_perf` draait de
    lokale worker onder `taskset -c 0-2` (regel 14);
  - een optie `outage_s`: de app stoppen op pid, zoveel seconden wachten, en weer starten (R06);
  - uploadt fixtures via de API;
  - Chromium met `--autoplay-policy=no-user-gesture-required`; voor `media_perf` 40 Mbit/s en
    20 ms via CDP (`Network.emulateNetworkConditions`);
  - fixture `ffmpeg_count`: een thread kijkt tijdens de test elke 0,5 s naar de ffmpeg-processen
    (de kinderen van de worker, via `/proc`), en de test faalt bij een vierde. Na de test:
    verwacht 0;
  - stopt alles op pid, nooit op naam;
  - hulpjes: `first_frame_ms(page)` via `requestVideoFrameCallback`, `wait_playing(page, t)`,
    `codec_supported(page, s)`. Een test met H.264 of AAC slaat over als de browser ze niet kan, met
    de reden in het verslag. Met `MEDIA_REQUIRE_H264=1` faalt hij dan in plaats van over te
    slaan;
  - `e2e/pytest.ini`: markers `media`, `media_perf` en `provider_real` (`--strict-markers` blijft).
- *Noot bij de bouw van M0 (M0-e2e, het harnas in `e2e/`):* gebouwd zoals hierboven, met deze
  keuzes ([m0.md](m0.md), afwijkingen 28–35; uitleg in [e2e.md](e2e.md)):
  - caps zijn zichtbaar via `app.cli media-status` (de stub van M0 vraagt ze over de socket);
    `/api/v1/admin/media` blijft tot M10 503 `media_off`;
  - zonder scanner (M2) zet het harnas `media.analyse` in de rij van de app, en leest het
    `media_probe`;
  - `ffmpeg_count` telt ffmpeg en ffprobe in de hele boom van de app, want de previews draaien
    tot M1 nog in de app;
  - de worker-container in de e2e komt met M1 (`E2E_MEDIA_WORKER`); `media-image` draait in M0 de
    kern van de worker in het image en S3 van de spike.
- **CI** (§4.8): de nieuwe jobs `media-spike` (op `pull_request`, vanaf de eerste commit van M0),
  `api-media`, `e2e-media`, `e2e-media-perf` (een rapport) en `media-image`.

### 3.7 De spike (M0-api, dag 1–2)

ffmpeg 7.1.5 staat in het image, niet in de sandbox (apt is dicht, N22). Daarom draait het deel
"in het image" in CI. De rest draait in de sandbox, met ffmpeg 6.1.1, Docker 29 en Chromium 141.
Alles komt in `docs/fase-5/spike.md`. Daarna ligt het vast.

**De CI-job `media-spike` staat er vanaf de eerste commit van M0-api.** Revisie 1 had een job met
`workflow_dispatch`, pas in M0-e2e. Maar `workflow_dispatch` start alleen een workflow die op de
standaardbranch staat, en `main` heeft alleen een README. Zo'n job kan dus niet starten, en M0 zou
contracten bevriezen die op 7.1.5 nooit draaiden. Nu:

- `media-spike` is een gewone job in `ci.yml`, op `pull_request` (zoals de rest);
- een eerste stap kijkt met `git diff --name-only` of `api/tests/media/spike/**` of het
  Dockerfile veranderde. Zo niet, dan stopt de job groen. Geen nieuwe action;
- hij bouwt `platform:ci` zoals de job `images`, draait de spike-scripts in dat image (onder
  `--cpus 3`, regel 14), en uploadt `spike.json` als artefact;
- de poort van §3.8 eist dat artefact van de laatste commit van M0. Na M0 gaat de job weg; de
  selftest in `media-image` neemt het over.

| Stap | Waar | Wat | Beslist |
|---|---|---|---|
| S1 | image | ffmpeg 7.1.5: versie, encoders (`libx264`, `aac`, `libopus`, `libvpx-vp9`, `mjpeg`), filters (`zscale`, `tonemap`, `bwdif`, `overlay`, `tile`), protocollen `fd` en `pipe`, `-format_whitelist`; `nice` en `ionice` | of Keuze 27 tone-mapping heeft; de lijst van de selftest |
| S2 | image | D1–D6 en J4 op 7.1.5: fd-invoer met zoeken, fMP4 naar een pipe, `+frag_discont` geeft `tfdt` = mediatijd + 1, `gte(t,n_forced*2)` met `-forced-idr 1` en `-g 100000` vanaf 0 en na `-ss 2k` (ook `fps23976.mkv`, `interlaced.ts` en `fps240.mp4`: precies één IDR per 2 s, en 60 fps), elk fragment begint met NAL-type 5, tegendruk. En de tijden (V4): met `+negative_cts_offsets` is `tfdt` plus de offset van het eerste sample gelijk aan de brontijd + 1 s, binnen één frame, voor H.264 met B-frames (transcode) en HEVC (remux) | de commando's van §3.3 |
| S3 | image | fd's tussen containers met het echte image en de echte compose (J1 en V2 deden het al in de sandbox, met een ander image): de worker als `media` (10002); het benoemde volume (`media:platform`, 2750) en de socket (0660, groep `platform`); `network_mode: none`, `read_only`, tmpfs op `/data`, Docker 29; de zelftest van Keuze 11 (opnieuw openen via `/proc` faalt, `/proc/<worker>/fd` dicht, ffmpeg 0711). De spike bouwt een afgeleid image met de drie regels van Keuze 47 (M1 zet ze in het Dockerfile) | `compose.media.yaml`; de terugval |
| S4 | sandbox | MSE in Chromium 141: gemuxte VP9+Opus-segmenten uit onze knipper, één SourceBuffer, een nieuwe init (gelijke en andere `stsd`), over 54 ms springen, `timestampOffset = −1`, `<track>` met MSE. Plus beeld en geluid gelijk: `requestVideoFrameCallback` geeft `mediaTime`, en die moet binnen één frame de tijd zijn die `testsrc2` in het beeld schrijft, terwijl het geluid speelt | de details van `mse.ts` |
| S5 | image | welke boxen verschillen tussen de inits van twee runs: alleen `mvhd`, `tkhd`, `mdhd`, of ook `stsd` en `elst` (N10) | de vergelijking van Keuze 22 |
| S6 | sandbox | de CPU van het knippen in Python: 3 × 6 Mbit/s, en remux op schijfsnelheid | of remux `-readrate` nodig heeft naast de tegendruk |
| S7 | image | `bench` drie keer, onder `--cpus 3`: spreiding < 10 %, voor `f` en `f_dec` | of de factoren bruikbaar zijn |
| S8 | sandbox | gapless: FLAC en Opus via `remux_audio` met `-use_editlist 0` en `appendWindow`, op het sample? MP3 als `audio/mpeg`: de precieze `delay` | `gapless.ts`; wat we over MP3 beloven |
| S9 | sandbox | PGS: de eigen `.sup` naar mkv, ffmpeg decodeert hem, en `overlay` brandt zichtbare pixels in. VobSub: dezelfde `.sup` met `-c:s dvdsub` (V8 deed dat al op 6.1.1) | of inbranden in fase 5 blijft |
| S10 | image | `RLIMIT_FSIZE` 0, `RLIMIT_DATA` 1.200 MB, `-max_alloc` en `-max_pixels` met x264 1080 veryfast en `-threads 4`: draait ffmpeg nog (V6 deed dat op 6.1.1 met 720)? Wat kost `-threads 4` tegen de standaard op 1080 veryfast? | Keuze 12 |
| S11 | sandbox | VP9 realtime, onder `taskset -c 0-2`: cores voor 1,0× op elke trede, SDR en HDR, en een bron van 30 fps. V3 mat 720 al (0,57–0,59, gelijk aan x264). Ook het decoderen van VP9 en AV1 voor `d_codec` | de kostentabel voor VP9, de HDR-factor, `d_codec`, de fps-regel |
| S12 | sandbox | F1 en F2: 16 gepauzeerde streams vóór en na; `HEAD` | de reparatie |
| S13 | CI | Chrome for Testing 156 in de job `e2e-media-perf`: de browserversie, en wat `isTypeSupported` zegt voor `avc1.640028` en `mp4a.40.2` (in het artefact) | of de e2e-job `MEDIA_REQUIRE_H264=1` zet (§4.8) |

(spike) **De uitkomst** (28 oktober 2026; [spike.md](spike.md)): elke stap is gedraaid behalve
S13, die later in `e2e-media-perf` draait. Geen enkele stap heeft een terugval hieronder nodig.
S1–S12 in het kort: S1 alles er, `lavfi` onder `-devices`; S2 60 van 64 zoals verwacht, N uit het
laatste videobeeld, TS zoekt niet precies; S3 17 van 17, de fd deelt zijn positie; S4 MSE werkt,
ook met een andere `stsd`, maar een springer is nodig; S5 alleen de `stsd`; S6 0,007 core, geen
`-readrate`; S7 spreiding 0,7 % en 1,9 %, de referentie van `bench` meet M1; S8 FLAC exact, Opus
via FLAC, MP3 met `enc_delay`; S9 inbranden werkt, met een tweede fd; S10 alle grenzen aan;
S11 VP9 = 1,0 × x264, `d_codec` VP9 0,18 en AV1 0,25, VP9 met HDR hooguit 720; S12 F1 en F2 nodig.

**Als een stap faalt:**
- **S2** (`tfdt` of het rooster gedraagt zich anders op 7.1.5): de knipper zet `tfdt` zelf. Hij
  leest de `moof` toch al, en een vaste verschuiving per run is een paar regels. Valt een keyframe
  na een herstart één frame anders (23,976 fps), dan is dat onschuldig: MSE overschrijft een frame,
  en de speler springt over een gat van één frame.
- **S2** (de tijden met `+negative_cts_offsets`): de knipper telt de offset van het eerste
  sample bij `tfdt` op en schrijft dat in de `tfdt`; dan geen v1 nodig.
- **S3** (fd's tussen containers), in deze volgorde:
  1. een bindkoppeling `${DATA_ROOT}/media/run:/run/media` in plaats van het benoemde volume
     (`install.sh` maakt de map met eigenaar 10002, de groep van `platform` uit het image, en
     modus 2750). De afscherming blijft gelijk;
  2. de worker krijgt de blobs alleen lezen (`${DATA_ROOT}/blobs:/data/blobs:ro`), en de app
     geeft een pad in plaats van een fd. De worker ziet dan alle films, maar nog steeds geen
     database, geheimen of netwerk. Dat staat dan in `spike.md` en bij de risico's;
  3. de nooduitgang `MEDIA_SANDBOX=uit`.
- **S4** (een nieuwe init werkt niet in MSE): bij een andere `stsd` een nieuwe sessie op die plek,
  net als bij native HLS.
- **S5 en Safari:** Safari is hier niet te testen. Of native HLS `trun` v1 goed speelt, wordt een
  apparaattest in fase 9 (§5.1).
- **S9:** inbranden schuift (§5).
- **S10:** zonder `RLIMIT_FSIZE`; de rest blijft. Breekt `RLIMIT_DATA` x264 op 1080, dan gaat de
  grens voor live naar 2.000 MB (de container heeft 3 GB).
- **S13:** zegt CfT 156 nee tegen H.264 of AAC, dan blijft `MEDIA_REQUIRE_H264` uit in CI.
  `spike.md` zegt dat, en H.264 wordt bewezen op de LXC (§4.7, Ma).

(spike) Geen van deze terugvallen is nodig: S2, S3, S4, S9 en S10 slaagden; S5 gaat over Safari
(fase 9); S13 komt later.

### 3.8 Klaar als (M0)

- Alle bestaande tests groen; ruff, tsc, build en budget schoon. De eerste lading blijft ≤ 100 KB.
- `test_migration_0008.py`: een database van 0007 met data → upgrade → tabellen en indexen er →
  downgrade → gelijk aan 0007 → upgrade. Na `db-downgrade 0007` start 0.6.0.
- Met media uit, of zonder worker, slagen alle tests van fase 3 en 4 onveranderd.
- De lokale worker beantwoordt `caps` en `probe` op alle fixtures (`test_media_worker_core.py`,
  `test_media_probe.py`). `test_media_wire.py` dekt het protocol en de weigeringen.
- F1–F4 zijn groen, elk met een test die eerst faalde. F4 ook in `web/nginx.conf` en de job
  `images` (`test_csp_nginx.py`).
- De rijen en `Defer` werken (`test_jobs_lanes.py`).
- `test_media_routes_guarded.py`: elke route onder `/api/v1/stream/`, ook een stub, gaat langs
  `sessions.guard`.
- `test_media_logs.py` met de nep-stream: geen sid in een logregel.
- `fake_worker` slaagt voor `worker_contract.py`, en de lokale worker voor het deel dat M0 heeft
  (`caps`, `probe`, stoppen).
- `fake_stream` slaagt voor `media_contract.py`. `hls_lint` heeft zijn eigen tests.
- De fixtures worden in CI gemaakt in ≤ 30 s.
- De job `media-image` is groen, met de selftest op 7.1 (ook de afscherming van Keuze 11).
- De spike is gedaan. Het artefact `spike.json` van `media-spike` hoort bij de laatste commit van
  M0. `docs/fase-5/spike.md` heeft een uitkomst per stap, en de commando's van §3.3 liggen vast.
  (spike) `spike.md` is er, met S13 als open stap voor `e2e-media-perf`; §3.3 en de Keuzes zijn
  bijgewerkt (§6, "Uitslag van de spike (M0)").
- De jobs `api-media` en `e2e-media` bestaan, met `timeout-minutes: 20` (§4.8).
- De keuzes staan als B39 en verder in `docs/fase-0/beslissingen.md` (voorstel), en
  `docs/fase-5/README.md` heeft een skelet.
- Tag `fase5-basis`.

*Stand na M0-e2e* ([m0.md](m0.md), "Open"): M0-e2e bouwde de nep-worker, `worker_contract.py`
(tegen de nep, en tegen de lokale worker voor `caps`, `probe` en stoppen), `record.py` met
`recorded/`, de nep-stroom, `media_contract.py`, het deel van `test_media_logs.py` met de
nep-stroom, het harnas in `e2e/` (`media_harness.py`, de fixtures `media` en `ffmpeg_count`, de
markers in `e2e/pytest.ini`, de rooktest `test_media_m0.py`) en de jobs `api-media`,
`e2e-media` (beide `timeout-minutes: 20`), `e2e-media-perf` en `media-image`. Die jobs draaiden
nog niet: hun eerste uitslag komt na de push. `media-image` heeft in M0 de kern van de worker op
7.1 en S3; de selftest op 7.1 (ook de afscherming van Keuze 11) komt met M1 (`--selftest`). De
fixtures zijn hier koud in 30,0 s gemaakt op 3 cores (26 s op 4); `api-media` zet de tijd in CI
in het log. Het artefact `spike.json` is van de run op `f713169`, niet van de laatste commit van
M0. De tag zet de lead. De rest van de lijst hierboven is groen.

### 3.9 Elk verzoekpad, met bewijs en controle

| # | Van → naar | Pad | Bewijs | Wie controleert wat |
|---|---|---|---|---|
| 1 | browser → app | `/api/v1/media/…` (bibliotheken, items, `codecs`, voortgang, koppelen, muziek) | access-token (Bearer) | `CurrentUser` (2FA); `access.resolve`; schrijfrecht voor koppelen en corrigeren |
| 2 | browser → app | `POST /api/v1/media/{node}/play` | Bearer | leesrecht van nu, media aan, de toelating, 8 sessies per gebruiker |
| 3 | browser → app | `POST /api/v1/public/media/{node}/play` | sleutel van de link (`CurrentLink`) | de link leeft, het bestand zit erin, lezen mag, de vingerafdruk van het wachtwoord; 2 per link, 30 per minuut per IP |
| 4 | browser → app | `/api/v1/stream/{sid}/…` (`file`, `map.json`, playlists, `init.mp4`, `{n}.m4s`, `subs/…`, `trick.vtt`, `trick/{i}.jpg`, `audio.mp4`) | de sid in het pad (24 willekeurige bytes) | `sessions.guard`: bestaat, niet verlopen, geen grafsteen, de node wijst nog naar de gepinde sha, de toegang van nu (cache ≤ 15 s); `/file` en `audio.mp4` kijken dat per stuk opnieuw na |
| 5 | browser → app | `/api/v1/media/art/…` | ondertekend adres (12 uur) | handtekening en tijd, zoals previews |
| 6 | browser → app | `/api/v1/admin/media/…` | Bearer, beheerder | zoals de rest van `/beheer` |
| 7 | app → worker | Unix-socket `/run/media/worker.sock` | geen: alleen de groep `platform` (de app) kan verbinden (socket 0660, map 2750 van `media`); geen andere container ziet het volume | de worker: `PROTOCOL`, de grootte, het aantal fd's, de argumenten (pydantic), de plaatsen per soort |
| 8 | app → TMDB, MusicBrainz, CAA | https via `providers/http.py` | Bearer voor TMDB (uit `media_secrets`); niets voor MB en CAA | vaste hosts, de controle na DNS, grenzen, de stroomonderbreker (Keuze 39) |

Vanaf internet zijn alleen #1–#6 bereikbaar, via 80 of 443. #7 verlaat de LXC niet, en de worker
heeft geen netwerk. #8 gaat alleen naar buiten, en alleen als Jonas het aanzet.

---

## 4. Tests en meten

### 4.1 De lagen

| Laag | Waartegen | Waar | Poort |
|---|---|---|---|
| Unit | pure functies: namen, groeperen, NFO, codec-strings, EBML, MP4-boxen, het rooster, de knipper, VTT, gapless, de beslissing, de toelating, playlists, de score van koppelen, voortgang; web: toetsen, segmentrekensom, vernieuwen | pytest, vitest | hard, in elke CI-run |
| Worker | de lokale worker met echte ffmpeg op de fixtures, hooguit 3 processen | pytest (job `api-media`, ffmpeg 6.1) | hard |
| API | elk eindpunt; randgevallen met de nep-worker en de nep-providers | pytest | hard |
| Contract | `worker_contract.py` (nep en echt), `media_contract.py` (`fake_stream` en M4), `provider_contract.py` (nep altijd, echt alleen met de hand) | pytest | hard (behalve `provider_real`) |
| Image | de selftest op ffmpeg 7.1.5; de afscherming van de container; fd's tussen containers | CI-job `media-image` (Docker) | hard |
| e2e | de echte speler in de browser met gemaakte media | Playwright: Chromium 141 hier, Chrome for Testing 156 in CI (marker `media`, job `e2e-media`) | hard |
| Prestaties | de tijden van "klaar als" en de belasting | marker `media_perf` en de belastingtest; met de hand, in de sandbox en op de LXC | rapport, met grenzen |

### 4.2 Wat overal geldt

De tests per pakket staan in §2. Daarnaast, voor iedereen:

- **Tabellen boven losse gevallen.** De beslissing, de namen, de codec-strings, de toelating en de
  score van koppelen worden getest met een tabel in een bestand. Een nieuw geval is een nieuwe
  regel.
- **Golden files** voor wat een andere speler of ffmpeg leest: de playlists, `map.json`, VTT en de
  argv per opdracht. Een verschil is een bewuste wijziging met een nieuwe golden file.
- **Eigen fuzz met een vaste seed** voor elke lezer van onvertrouwde bytes: EBML, MP4-boxen (ook de
  knipper in de app), de codec-configuraties, NFO, SRT en ASS, de LAME-header, de Ogg-pagina. Nooit
  een hang, nooit een ongevangen fout, altijd binnen de tijd (regel 10).
- **Een nepklok** voor alles met 15 s, 30 minuten, 12 uur, 6 uur en 24 uur.
- **Geen geheimen in logs:** `test_media_logs.py` en een fixture in `test_media_secrets.py` vangen
  alle logregels (app en `uvicorn.access`) en zoeken naar elke sid en de TMDB-sleutel.
- **0 ffmpeg na elke test** (de fixture `ffmpeg_count`). Nooit meer dan 3 tegelijk: de worker
  dwingt dat af met `PLATFORM_MEDIA_MAX_PROCS=3`, en de fixture kijkt elke 0,5 s.
- **Elke test een tijdsgrens:** het harnas geeft elke mediatest een eigen deadline (90 s, en
  300 s voor `media_perf`). Dan stopt het de app en de worker op pid, en faalt de test met een
  dump van de draden (`faulthandler.dump_traceback`). Geen nieuwe dependency.
- **De vaste tests van M0 en M1:** `test_media_routes_guarded.py`, `test_media_no_ffmpeg_outside.py`,
  `test_media_logs.py`, `test_error_catalog.py` (bestaat) en `test_migration_0008.py`.
- **De volgorde van bewijzen:** eerst de pure functies, dan de API met de nep-worker, dan met echte
  ffmpeg, dan e2e. Een fout in e2e krijgt eerst een test op het laagste niveau dat hem kan vangen.

### 4.3 e2e met gemaakte media (marker `media`)

Chromium 141 van Playwright speelt VP9, AV1, Opus, FLAC en MP3, en geen H.264, AAC, HEVC of AC-3
(N13). De tests gebruiken dat bewust: HEVC en AC-3 dwingen een transcode af. Een toets met H.264
draait alleen als `codec_supported` ja zegt (in CI met Chrome for Testing waarschijnlijk wel
[niet getest]), en slaat anders over met een reden. Zegt S13 dat CfT 156 H.264 en AAC kan, dan
zet de job `e2e-media` `MEDIA_REQUIRE_H264=1`, en faalt zo'n toets in plaats van over te slaan.
Zo blijft CI niet stil groen terwijl de route van bijna alle echte bestanden nooit getest is.
Nooit `playwright install`: de browser die er is.

| # | Scenario | Pakket |
|---|---|---|
| 1 | een map met films, een serie en een album wordt een bibliotheek; titels uit de namen, posters uit `poster.jpg`, seizoenen | M2 |
| 2 | direct: `vp9_opus.webm`; het eerste beeld en zoeken | M3, M6 |
| 3 | remux: `vp9_opus.mkv` (reden `container`); een ander audiospoor (nieuwe sessie, de positie blijft ±1 s); zoeken voorbij de cache start een nieuwe producent | M3, M4, M6 |
| 4 | transcode: `hevc10_hdr10.mkv` naar VP9 720p met tone-mapping (redenen `video_codec`, `hdr`); SRT aan; de ASS-cue met `\an8` staat boven in beeld | M3, M4, M5, M6 |
| 5 | inbranden: `pgs.mkv` en `vobsub.mkv` (uit de eigen `.sup`, V8); de pixels op de plek van de ondertitel veranderen (canvas). Een geforceerd PGS-spoor gaat niet vanzelf aan; de speler biedt het inbranden aan | M5 |
| 6 | voortgang: twee contexts van één gebruiker; A pauzeert op 0:30; bij B staat binnen 2 s "Verder kijken 0:30", en B begint daar | M7 |
| 7 | de kaart "Volgende aflevering" met de aftelling; Esc annuleert; `cd1` gaat vanzelf naar `cd2` | M6, M7 |
| 8 | gapless: het album via MSE; de fase van de sinus springt niet (FLAC, Opus) | M9 |
| 9 | alles met alleen het toetsenbord: bibliotheek, item, speler, menu's, muziek, `/beheer` | M2, M6, M9, M10 |
| 10 | publieke link: direct en remux spelen, ook een mkv buiten een bibliotheek (remux, de keyframes bij `play`); transcode geeft 403 `media_public_transcode`; de link ingetrokken: de speler stopt binnen 15 s plus de buffer, ook bij direct play (`/file` breekt af) | M3 |
| 11 | een deling ingetrokken terwijl iemand kijkt: 403 binnen 15 s; uitloggen op een ander apparaat en één login intrekken: meteen, ook midden in een `/file`-stream | M3 |
| 12 | de lokale worker gedood (op pid) tijdens een transcode: hij herstart, de speler loopt binnen 10 s door; geen ffmpeg over | M1, M4 |
| 13 | de worker zonder ffmpeg (`PATH` leeg, een optie van het harnas): de melding op `/beheer`; direct werkt; een HEVC-film geeft de fout met "Downloaden" | M1, M10 |
| 14 | `/beheer` → Media: de status; een sessie stoppen: de speler zegt het en vraagt geen nieuwe sessie (403 `stopped`) | M10 |
| 15 | muziek: een album spelen; de balk blijft spelen bij navigeren; een afspeellijst maken en ordenen | M9 |
| 16 | de import vanuit `/data/import` via `/beheer`, met kopiëren en met verplaatsen | M2, M10 |
| 17 | metadata met de nep-TMDB: de sleutel invullen, posters, een verkeerde koppeling goed zetten | M8 |
| 18 | de app herstart midden in een film: de speler loopt binnen 10 s door, op de goede positie. Met `outage_s=30` (zoals een update): de speler loopt ≤ 10 s na "ready" weer | M3, M6 |
| 21 | een nieuwe versie van het bestand terwijl iemand kijkt: 410 met reden `replaced`, de speler opent de nieuwe versie | M3 |
| 19 | kwaliteit 720 naar 480 met de hand; en na 2 haperingen (de verbinding geknepen via CDP) zelf één trede lager | M6 |
| 20 | geen fouten in de console en geen CSP-meldingen, in elk scenario | iedereen |

Elk pakket heeft zijn eigen `e2e/test_media_<pkg>.py` (§2). `docs/fase-5/e2e.md` (M0) legt uit hoe
ze draaien.

### 4.4 De nep-providers, en hoe we weten dat ze kloppen

`api/tests/media/fake_providers/` is één Starlette-app (Starlette zit er al in), gebouwd naar de
documentatie [doc]. Opnemen kan hier niet: de proxy blokkeert de hosts (N23).

| Host | Eindpunten | Gedrag |
|---|---|---|
| TMDB API | `/3/configuration`, `/3/search/movie`, `/3/search/tv`, `/3/movie/{id}`, `/3/tv/{id}`, `/3/tv/{id}/season/{n}`, `/3/find/{id}` | eist `Authorization: Bearer` (anders 401 met `status_code: 7`); `language`; `append_to_response`; meer dan 20 per seconde: 429 met `Retry-After: 1`; met `nl-NL` een lege samenvatting voor een deel van de titels, zodat de terugval naar `en-US` getest wordt |
| TMDB beelden | `/t/p/{w300,w500,w780,w1280,original}/{pad}` | echte JPEG's, bij de start gemaakt met Pillow (zit er al in), in de gevraagde maat |
| MusicBrainz | `/ws/2/release/{mbid}`, `/ws/2/release/?query=…` (`fmt=json`) | zonder User-Agent in de vorm `Naam/versie ( contact )`: 403; meer dan 1 per seconde: 503 [doc] |
| Cover Art Archive | `/release/{mbid}/front-500` | 307 naar een tweede hostnaam (de nep luistert ook als "archive"), die het plaatje geeft |

- **Fouten op bestelling:** `POST /__fault {path, kind, times}` met `kind` = `500`, `429`, `slow`
  (10 s), `garbage` (geen JSON), `huge` (een plaatje van 50 MB), `wrongtype` (`text/html` als
  plaatje), `redirect_evil` (een 302 naar een vreemde host) of `drop` (de verbinding dicht).
- **Logboek:** `GET /__log` (methode, pad, koppen zonder sleutel, tijd), om de User-Agent en het
  tempo te controleren.
- **Data:** `fake_providers/data/*.json`, met de hand geschreven: ±20 films (ook met scene-namen),
  3 series met seizoenen, 5 albums. Verzonnen titels en id's (vanaf 900000), en een paar werken uit
  het publieke domein. Bovenaan elk bestand: "nepdata, niet echt".
- **Twee standen:** in het proces (`httpx.ASGITransport`, voor API-tests) en als proces (uvicorn op
  een vrije poort, voor e2e).

**Wat het bewijst en wat niet.** Het bewijst dat onze clients de gedocumenteerde vorm lezen, de
grenzen houden en netjes falen. Het bewijst niet dat de echte dienst precies zo antwoordt. Daarvoor:

- `provider_contract.py` draait dezelfde scenario's tegen de nep (altijd) en tegen de echte
  (`-m provider_real`, met een sleutel in de omgeving, alleen met de hand, nooit in CI);
- `app.cli media-provider-check --opnemen` (M8) haalt met een echte sleutel van elk eindpunt één
  antwoord, en bewaart alleen de vorm (sleutels en soorten, geen waarden) in
  `docs/fase-5/provider-shapes.json`. Een test vergelijkt de antwoorden van de nep met die vorm:
  elk veld dat wij lezen, moet in beide zitten. Zonder het bestand slaat die test over, met de
  reden. Jonas draait het één keer.

### 4.5 Metingen (in `docs/fase-5/metingen.md` en `metingen.json`)

Elke meting 5 keer (de tijden in e2e 10 keer), met de mediaan en de slechtste. In de sandbox (4 vCPU,
geen GPU, net als de LXC) en met hetzelfde script op de LXC. Nooit meer dan 3 ffmpeg tegelijk in de
sandbox, en samen onder 8 GB. In de sandbox draait de worker altijd met de grens van de LXC: de
lokale worker onder `taskset -c 0-2`, of de container met `--cpus 3` (regel 14). Revisie 1 rekende
met metingen op 4 cores, en dan valt de eerste start te mooi uit (V7).

| # | Meting | Hoe | Grens | Verwacht |
|---|---|---|---|---|
| Ma | Direct play, eerste beeld | e2e `media_perf`: 1080p, 20 Mbit/s, 2 min (VP9 hier, H.264 in CI); 40 Mbit/s en 20 ms via CDP; van klik tot het eerste `requestVideoFrameCallback` | ≤ 2 s | 38–81 ms lokaal in de browser (N15), plus API en schijf |
| Mb | Transcode, eerste beeld | e2e: `hevc10_hdr10.mkv` naar 720; zonder browser: van `POST /play` tot segment 0, H.264 1080 naar 1080 en naar 720, alleen en terwijl er 2 andere transcodes lopen | e2e ≤ 5 s; zonder browser alleen ≤ 2,5 s (mediaan) en ≤ 3,0 s (slechtste); naast 2 andere ≤ 5 s | op 3 cores: 1,9–2,6 s alleen; 3,3–4,0 s als ze alle drie tegelijk starten (V7) |
| Mc | Zoeken | `currentTime` naar 70 % (nog niet gemaakt) tot hij voorbij het doel loopt: direct, remux, transcode | ≤ 1,5 s / 1,0 s / 3,0 s | ±1,3 s plus toevoegen (D4) |
| Md | 3 transcodes | de belastingtest (§4.6) met de standaard van Vraag 3 (1080 veryfast + 720 superfast + 720 superfast), 300 s, met alle fasen; en nog eens terwijl OnlyOffice elke 30 s een docx van 50 pagina's omzet; en een run met een herstart van DS midden in de test | per fase van §4.6 | 3 × 720 veryfast 2,3–2,4× (J2), 1,67× (testbron); drie tegelijk op 3 cores 1,67–1,81×, 0,28 s haperen zonder startvoorrang (V7) |
| Me | 2 × 1080 | `max_transcodes` 2: 1080 veryfast + 1080 superfast, 300 s | 0 keer haperen | 2 × 1080 veryfast 1,37× (testbron) |
| Mf | HDR | 1 × 1080 HDR alleen; 1 × 720 HDR naast 2 × 720 superfast | 0 keer haperen | 1,35× / niet gemeten |
| Mg | De API onder belasting | p95 van `GET /api/v1/files/root` en `/api/v1/media/home` tijdens Md | ≤ 200 ms | — |
| Mh | Geheugen | de hoogste RSS van de worker tijdens Md en Mf | ≤ 3 GB | 0,75–1,3 GB |
| Mi | Voortgang synchroniseren | PUT in A tot het event in B, 20 keer | p95 ≤ 2 s | < 0,1 s (de hub zit in het geheugen) |
| Mj | Kosten van het knippen | de CPU van de app per stroom (S6) | ≤ 0,05 core | niet gemeten; (spike) het prototype in S6: ≈ 0,007 core voor 3 × 6 Mbit/s, 1,2 ms CPU per MB bij remux |
| Mk | De factor `f` | voorspelde snelheid tegen gemeten, per trede | binnen 15 % | — |
| Ml | Scannen | een bibliotheek van 1.000 bestanden, zonder analyse | ≤ 60 s | niet gemeten |
| Mm | Tegendruk | de CPU van een producent die 30 segmenten voor ligt | ≈ 0 | 0,14 s CPU in 4 s (D6) |
| Mn | Gapless | de fasefout op de overgangen (FLAC, Opus) | ≤ 1 sample | op het sample (N20) |
| Mo | Namen | 10.000 namen ontleden | ≤ 0,5 s | — |
| Mp | Grootte | de chunks van Keuze 33 | binnen de grenzen | kern van de speler 3,1 KB (proef) |

Wat de sandbox niet kan meten, en waar het dan gebeurt:
- H.264 in de browser: in CI (Chrome for Testing, als het daar speelt) en op de LXC;
- Safari, iPhone en webOS: fase 9, of eerder als er een toestel is;
- de CPU van Jonas: `bench` en de belastingtest op de LXC.

### 4.6 De belastingtest (in de app)

`app/media/loadtest.py` (M10). Te starten met de knop op `/beheer`, met
`POST /api/v1/admin/media/loadtest {streams: 3, seconds: 300, codec: "h264"|"vp9", sources?: [node_id]}`,
of met `docker compose exec platform python -m app.cli media-loadtest --sessies 3 --duur 300 [--json uit.json]`.
Hij draait in de app, dus met de echte beslissing, toelating, sessies, controle, producenten,
knipper en cache.

1. **Bronnen.** Standaard maakt de worker drie verschillende bronnen met de opdracht `synth`
   (Keuze 12): 300 s 1080p H.264, 6 Mbit/s, uit `testsrc2` en twee keer `cellauto` met een andere
   seed, elk met ruis. Na elkaar, in `media/loadtest/` (±675 MB, hergebruikt), en samen ±3 minuten
   (V8). De app opent de uitvoer-fd; de worker heeft geen schrijfbare map nodig. Revisie 1 had
   `mandelbrot` (±7 minuten per bron) en geen opdracht om bronnen te maken. Met `sources` kan het
   met echte films van de beheerder.
2. **Sessies.** Ze zijn van de beheerder die de test start. Bij de eigen bronnen is de bron een
   `LoadtestFile` (`SessionSource`, §3.3), alleen met `PLATFORM_MEDIA_LOADTEST=1`. De controle per
   verzoek kijkt dan of die beheerder nog actief is en de vlag nog aan staat.
3. **Profiel.** H.264 via MSE (of VP9 met `codec: "vp9"`) en `max.bitrate_kbps: 4000`. De bron
   heeft 6 Mbit/s, dus de beslissing kiest zelf transcode (reden `bitrate`), en de toelating kiest
   de trede.
4. **Spelers.** Per sessie een virtuele speler in Python (geen browser). Hij praat via http met de
   app op 127.0.0.1, dus de routes en de controle doen mee.
   - Hij begint te spelen zodra **segment 0** er is (niet 0 en 1: dat verborg het haperen, J3).
   - Daarna vraagt hij de segmenten op volgorde, met hooguit 30 s vooruit, en speelt in echte tijd.
   - De speeltijd loopt alleen als het segment dat nodig is, helemaal binnen is. Anders telt het
     als haperen (aantal en duur).
5. **Verloop,** in fasen:
   - **a. Gespreid:** de sessies starten op 0, 10 en 20 s. Elke sessie springt één keer, op 60 s,
     naar een plek voorbij de cache. Zo doet een herstart van een producent mee.
   - **b. Tegelijk:** drie sessies op t = 0, zoals drie kijkers die samen op spelen drukken.
   - **c. Herstart:** de app stoppen op pid, en weer starten. Alle drie de spelers vragen tegelijk
     een nieuwe sessie (410), zoals na een update. Deze fase draait alleen met de spelers buiten de
     app: in de sandbox via het harnas, en op de LXC met `app.cli media-loadtest --extern` terwijl
     Jonas `dc restart platform` doet (het runbook zegt hoe).
   - **d. DS herstart** (alleen met office aan): midden in fase a een herstart van DS.
6. **Meet:** de start per sessie; haperen (aantal en totaal); de snelheid van elke run; de trede die
   de toelating koos; de CPU van de worker (uit de cgroup, anders `/proc`); de RSS per proces; de
   cache in MB per minuut; de p95 van `GET /api/v1/files/root` en `/api/v1/media/home` elke seconde.
7. **Slaagt als,** per fase:
   - a: elke start ≤ 5 s, 0 keer haperen na de start, elke run gemiddeld ≥ 1,1× (marge boven de
     1,0× die het rooster van 2 s nodig heeft), en de API ≤ 200 ms (p95);
   - b: elke start ≤ 5 s (gemeten zonder startvoorrang 3,3–4,0 s, V7), en 0 keer haperen na de
     start;
   - c: alle drie lopen ≤ 10 s na "ready" weer, op de goede positie, en haperen daarna niet;
   - d: de lopende streams haperen niet; een nieuwe kijker krijgt zolang een lagere trede
     (`B − 1,0`, Keuze 26).
8. **Veilig:**
   - hij weigert (409 `media_bench_busy`) als er echte transcodes lopen;
   - zolang hij loopt, wachten de analyse en de previews van video's (`Defer`);
   - de worker draait met `PLATFORM_MEDIA_MAX_PROCS=3` en, in de sandbox, onder `taskset -c 0-2`;
     de test kijkt elke 0,5 s naar de ffmpeg-processen en stopt bij een vierde;
   - na afloop gaan zijn sessies en hun cache weg.
9. **In CI:** `test_media_loadtest.py` met de nep-worker (het rapport, de weigering, de stop bij een
   vierde), en een rooktest met echte ffmpeg in de job `media-image` (`--sessies 1 --duur 20`,
   360p). Een gedeelde runner is te wisselvallig voor de echte meting.
10. **Op de LXC:** na de installatie draait Jonas eerst `bench` en dan de belastingtest, met de knop
    of het commando. De uitkomst staat op `/beheer` en gaat in `metingen.md`. Het budget volgt
    eruit.

### 4.7 Hoe elke "klaar als" bewezen wordt

| Klaar als (stappenplan) | Wat we beloven | Bewijs |
|---|---|---|
| Een 1080p-film start binnen 2 s (direct play) | het eerste beeld ≤ 2 s | Ma: e2e `media_perf` (10 keer, VP9 in de sandbox, H.264 in CI) en de checklist op de LXC |
| … en binnen 5 s (transcode) | het eerste beeld ≤ 5 s, ook als er al 2 transcodes lopen, en ook als er drie tegelijk starten | Mb: e2e en zonder browser (alleen ≤ 2,5 s; naast 2 andere ≤ 5 s), op 3 cores; de start in de belastingtest ≤ 5 s, in fase a en b |
| Seek werkt | weer spelen ≤ 3 s, ook in een stuk dat nog niet gemaakt is; direct ≤ 1,5 s, remux ≤ 1 s | Mc; e2e 2, 3 en 4; `test_media_restart.py` (na een herstart hetzelfde rooster en `tfdt` = mediatijd + 1) |
| 3 gelijktijdige transcodes zonder haperen | standaard 1080 + 720 + 720 (Vraag 3), 0 keer haperen voor een speler die alleen hele segmenten speelt, de API ≤ 200 ms, ook naast OnlyOffice en na een herstart | Md, Mg en Mh met de belastingtest (alle fasen), in de sandbox en op de LXC; J3 laat zien waarom het rooster overal 2 s is |
| Voortgang synchroniseert tussen apparaten | ≤ 2 s (p95 van 20) | Mi; e2e 6 |
| De rest van het stappenplan (bibliotheken, metadata, ondertitels, muziek, gapless) | — | de e2e van §4.3, Ml, Mn en de tests per pakket |

"Zonder haperen" is hier strenger dan in een browser. Die buffert 30 s; de virtuele speler wacht
op elk segment dat hij nodig heeft.

### 4.8 CI

De workflow start op `push` naar `main` en op `pull_request`. `workflow_dispatch` werkt alleen
voor een workflow op de standaardbranch, en `main` heeft alleen een README. Elke nieuwe job is dus
een gewone job op `pull_request`. Geen nieuwe action.

- `api`: alle tests behalve `media`, `media_perf` en `provider_real`. Zoals nu.
- **`api-media`** (nieuw, naast de rest): `-m media` met de lokale worker en echte ffmpeg (6.1 van
  Ubuntu staat er al), onder `taskset -c 0-2`. `timeout-minutes: 20`.
- `web`: `tsc -b`, vitest, `check:i18n`, build, `budget`.
- `e2e`: zoals nu, zonder media:
  `-m "not perf and not media and not media_perf and not office and not office_real and not provider_real"`.
- **`e2e-media`** (nieuw, naast de rest): `-m media`, met Chrome for Testing 156 zoals `e2e`.
  `timeout-minutes: 20`. `MEDIA_REQUIRE_H264=1` zodra S13 zegt dat CfT H.264 en AAC kan.
- **`e2e-media-perf`** (nieuw, alleen een rapport, zoals `perf`): `-m media_perf` voor Ma en Mb in
  CfT. Het artefact noemt de browserversie en wat `isTypeSupported` zegt voor `avc1.640028` en
  `mp4a.40.2` (S13).
- `infra`: `docker compose config -q` voor alle 8 combinaties (office aan of uit × media aan of uit ×
  grenzen ja of nee); `shellcheck`; `test_install_env.sh` en `test_install_media.sh`.
- `images`: zoals nu; `WEB_CSP` krijgt `media-src 'self' blob:` (F4), voor het platform-image en
  het web-image (`nginx.conf`).
- **`media-image`** (nieuw): het image bouwen (de cache van `images`), `--selftest` op ffmpeg 7.1,
  `test_media_container.sh` (M1), de rooktest van de belastingtest, en het bewijs dat alleen een
  echt image kan geven (R12): installeren vanaf de commit van 0.6.0, bijwerken naar deze versie,
  een transcode via curl (`POST /play` met een eigen profiel, dan `GET` segment 0), en tijdens die
  transcode `docker top` (de app: geen ffmpeg; `media`: één).
- **`media-spike`** (nieuw, alleen tijdens M0): de scripts van de spike in het image, als
  `pull_request`-job vanaf de eerste commit van M0 (§3.7); de uitkomst als artefact.
- **De duur:** de fixtures zijn in ≤ 30 s gemaakt, dus geen cache (dat zou een nieuwe action
  zijn). Elke mediatest heeft een eigen deadline in het harnas (§4.2). Met de twee nieuwe jobs naast
  de rest groeit de wandtijd van CI niet met de mediatests.
- *Noot bij de bouw van M0 (M0-e2e):* de jobs staan zo in `ci.yml` ([e2e.md](e2e.md), "De jobs
  in CI"; [m0.md](m0.md), afwijkingen 32–33). `api-media` faalt als een test overslaat omdat
  ffmpeg ontbreekt (de scenario's met `stream` mogen overslaan tot M4), en geeft een
  waarschuwing als de fixtures langer dan 30 s duren. `e2e-media` zet `MEDIA_REQUIRE_H264` op
  `"0"`. `e2e-media-perf` zet de uitkomst van S13 in de samenvatting van de run. `media-image`
  draait in M0 de kern van de worker in het image (`api/tests/media/image_kern.py`) en S3 van de
  spike, allebei hard; M1 en M10 vullen de rest. `media-spike` is ongewijzigd.

---

## 5. Volgorde

1. **M0 Fundament, alleen** (±6 dagen).
   - Dag 1: de eerste commit zet de job `media-spike` in `ci.yml` (op `pull_request`).
   - Dag 1–2: de spike (§3.7). De stappen in het image via die job, de rest in de sandbox.
   - Tegelijk: M0-api (schema, contracten, de kern van de worker, F1–F4, de rijen, de haken) en
     M0-web (routes, haken, types, codes, budget).
   - Daarna M0-e2e: het harnas, de fixtures, de nep-worker met opnames, `fake_stream`, de kern van
     de nep-providers, `hls_lint`, en de jobs `api-media`, `e2e-media`, `e2e-media-perf` en
     `media-image`.
   - Poort: §3.8. Tag `fase5-basis`.
2. **De parallelle golf** (±9 dagen; M4 is het langst): M1 tot en met M10, elk in een eigen
   worktree vanaf `fase5-basis`. Elk levert één PR, met tests, `docs/fase-5/<pkg>.md` en (waar het
   web raakt) een toetsenbord-checklist. M6 bouwt eerst tegen `fake_stream`, M3 en M4 tegen de
   nep-worker, M8 tegen de nep-providers.
3. **De volgorde van samenvoegen: M1 → M3 → M4 → M5 → M2 → M6 → M7 → M9 → M8 → M10.**
   - **M1 eerst:** hij verandert waar ffmpeg draait, ook voor de previews. Iedereen rebaset op de
     echte afscherming.
   - **M3:** de beslissing, de sessies en de toegang. Direct play werkt dan al.
   - **M4:** de stromen. De e2e van M6 schakelt dan van `fake_stream` naar het echte.
   - **M5:** ondertitels en sprites hebben sessies en stromen nodig.
   - **M2:** de bibliotheken. Afspelen heeft ze niet nodig (elke video speelt), maar de analyse van
     M2 gebruikt de stappen van M4 en M5.
   - **M6:** de speler. Zijn e2e draait nu tegen M3–M5, met de bibliotheken van M2.
   - **M7:** de voortgang heeft de speler en de startpagina nodig.
   - **M9:** muziek gebruikt `create_audio`, de stap `audio_info` en de bibliotheken.
   - **M8:** koppelen werkt op de items van M2.
   - **M10 als laatste:** de installatietest en `/beheer` hebben alles nodig. Loopt de rest uit, dan
     mag M10 eerder: zijn haken lezen stubs.
   - Vóór elke merge rebasen. Daarna de volledige set: pytest (met de contracttests en
     `test_error_catalog`), ruff, `tsc -b`, vitest, build, budget, `check:i18n`, de e2e van fase 3
     en 4, `-m media`, en de job `media-image`.
4. **Integratie** (±3 dagen):
   - de metingen van §4.5 en de belastingtest in de sandbox → `docs/fase-5/metingen.md` en
     `metingen.json`;
   - de installatietest en het terugrollen (M10) → `docs/fase-5/integratie.md`;
   - de tag `v0.6.0` op `263abd7`, voor het runbook (Keuze 47);
   - het budget en de standaarden van `MediaConfig` op de metingen gezet;
   - `docs/fase-5/README.md`: wat er is, de grenzen, en wat geschrapt is (Keuze 50);
   - de vinkjes van fase 5 in het stappenplan, met de reden bij guessit, inotify, TVDB, hls.js,
     `device` en "3 transcodes"; B39 en verder definitief na de antwoorden van Jonas;
   - versie 0.7.0 in `pyproject.toml`, `app/__init__.py` en `package.json`.
5. **Op de LXC** (Jonas, ±30 minuten, volgens het runbook): `media-status`, `bench`, de
   belastingtest met de knop, en een film op telefoon en laptop. De uitkomst gaat in
   `metingen.md`.

**Eerlijk over de tijd.** M0 ±6 dagen, de golf ±9, de integratie ±3: samen ±18 werkdagen. Met ±4
dagen marge voor wat de spike en de LXC laten zien: **±22 werkdagen**. Revisie 2 kost ±2 dagen
meer: de eigen gebruiker van de worker, de plaatsen per soort, de kosten van de bron, het
intrekken per stuk, de keyframes bij `play`, `synth` en de nieuwe CI-jobs. Het stappenplan geeft 5
weken (25 dagen). Dat past, krap. De marge is nodig: S2 (de commando's op 7.1) of S3 (fd's tussen
containers) kan anders uitvallen.

**Loopt het uit, dan schuift dit als eerste naar later, in deze volgorde:**
1. PGS en VobSub inbranden (S9). Tekstondertitels blijven.
2. Sprites. De zoekbalk toont dan alleen de tijd en de hoofdstukken.
3. MusicBrainz en de Cover Art Archive. Lokale tags en hoezen blijven.
4. De dialogen voor handmatig koppelen. De API blijft.
5. HDR op 1080p als keuze. 720 blijft.
6. Gapless voor MP3 en AAC. FLAC en Opus blijven.

**Wat nooit schuift:**
- de afscherming van ffmpeg (M1);
- de controle bij elk verzoek (M3);
- direct, remux en transcode, met de toelating en de tegendruk;
- het rooster van 2 s bij transcode;
- het synchroniseren van voortgang;
- de belastingtest;
- het pad voor bijwerken en terugrollen;
- de import met kopiëren.

### 5.1 Risico's

| Risico | Kans | Impact | Wat we doen |
|---|---|---|---|
| Een lek in ffmpeg via een kwaadaardig bestand | laag tot middel | hoog | Keuze 11 en 12: geen netwerk, geen data, alleen lezen, geen rechten, een eigen uid (10002), `protocol_whitelist fd,pipe`, `format_whitelist`, `-max_alloc`, `-max_pixels` en `RLIMIT_DATA`, een argv uit vaste lijsten; ffmpeg uit Debian met beveiligingsupdates via het image |
| Een gekraakte ffmpeg schrijft in een blob, of leest of stoort een andere job | laag | middel | Keuze 11 (V1, V2): de worker is uid 10002, dus een blob van 10001 gaat via `/proc/self/fd` niet open om te schrijven; ffmpeg is 0711 en de worker zet `PR_SET_DUMPABLE 0`, dus `/proc/<pid>/fd` van een buur is dicht; de app maakt elke pipe; de zelftest in het image (S3) controleert het. Wat overblijft: een job kan een andere job van dezelfde uid een signaal sturen (hinder, geen lek); per job een uid is te zwaar; staat in het runbook |
| fd's tussen containers werken niet op de LXC, of het benoemde volume krijgt de verkeerde eigenaar | middel | middel | J1 en V2 werkten in de sandbox, ook in Docker (de map 2750 `media:platform`, de socket 0660); S3 met het echte image; de terugval in drie stappen (§3.7), de eerste met een bindkoppeling die `install.sh` maakt; `install.sh` stopt nooit. (spike) S3 slaagde in CI (17 van 17); op de LXC bewijst de zelftest het. De fd deelt zijn positie: `lseek` vóór elke exec (Keuze 12) |
| ffmpeg 7.1.5 gedraagt zich anders dan 6.1.1 (`tfdt`, `frag_discont`, `force_key_frames`, `fd:`) | middel | middel | S1 en S2 in het image; de selftest is een harde poort in CI; de argv als golden files; de terugval "de knipper zet `tfdt`". (spike) S1 en S2 draaiden: `tfdt`, `frag_discont`, `force_key_frames` en `fd:` zoals verwacht; anders waren N (nu uit het laatste videobeeld), zoeken in MPEG-TS, en `lavfi` onder `-devices` |
| De CPU van Jonas is trager dan de sandbox | middel | middel | `bench` en de factor `f`; de toelating valt een trede terug; de belastingtest op de LXC; Vraag 2 en 3 |
| OnlyOffice en drie transcodes tegelijk: haperen | middel | middel | het budget 3,0; `B − 1,0` zolang DS start of fonts maakt (Keuze 26); de reserve in de toelating; de tegendruk in seconden; `cpus: 3.0` op de worker; Md meet het samen met OnlyOffice, ook met een herstart van DS (fase d van de belastingtest) |
| Het rooster van 2 s geeft meer verzoeken (één per 2 s per kijker) en een playlist van ±70 KB | hoog | laag | de sessie staat in het geheugen; geen database per segment; Mj meet de app; gzip op de playlist. (spike) Het knippen kostte in S6 ≈ 0,007 core voor drie streams |
| Een keyframe valt na een herstart één frame anders (23,976 fps) | middel | laag | S2 met `fps23976.mkv`; MSE overschrijft een frame, de speler springt over een gat van één frame. (spike) S2: na een herstart hetzelfde rooster bij elke bron waarvan de video binnen één frame na S begint, ook `fps23976.mkv`; een late videostart vraagt `-ss (origin + 2k)` (Keuze 22) |
| Een gat in de audio na een herstart (32–54 ms, D4) | hoog | laag | de speler springt over gaten tot 0,5 s; S4. (spike) Chromium springt niet zelf: 54 ms gaf ≥ 10 s stilstand. De springer na 250 ms is dus nodig, en werkt (260 ms; Keuze 32) |
| De init verandert na een herstart, en native HLS raakt in de war | laag tot middel | middel | de vergelijking van de `stsd` (S5); bij native HLS een nieuwe sessie op die plek (410); `/beheer` telt het. (spike) S5: de `stsd` is na elke herstart gelijk; een andere `stsd` speelt in Chromium in dezelfde SourceBuffer (S4) |
| Safari, iOS, webOS en Firefox zijn niet getest | hoog | middel | het profiel komt uit de browser zelf; basisprofielen met vlaggen per toestel; de playlists volgen RFC 8216 en de regels van Apple en LG, met een eigen lint; echte toestellen in fase 9 |
| De codecs van Chrome for Testing 156 in CI zijn anders dan verwacht | middel | laag | S13 legt in `e2e-media-perf` vast wat CfT 156 zegt over `avc1` en `mp4a`; bij ja zet de e2e-job `MEDIA_REQUIRE_H264=1`, en dan faalt een H.264-test in plaats van over te slaan; bij nee bewijst de LXC H.264 (Ma); VP9, AV1 en Opus dekken de speler. (spike) S13 is nog niet gedraaid |
| Tone-mapping ontbreekt in het image (geen libzimg) | laag | middel | S1; dan 422 `media_hdr` met "zonder kleurcorrectie" (Keuze 27). (spike) S1: `zscale` en `tonemap` zijn er in 7.1.5, en de keten draait; de selftest houdt het zo |
| Lange streams houden de databaseverbindingen vast | was hoog | hoog | F1; regel 8; `test_download_pool.py` |
| Een update midden in een film | middel | laag | F3: https en http stoppen samen binnen 10 s, de mediasessies eerst, `stop_grace_period: 30s`; de speler probeert het opnieuw zolang er buffer is plus 120 s en kijkt naar `/api/v1/health` (Keuze 32); bij 410 een nieuwe sessie op de plek; e2e met `outage_s=30`; `media-status --sessions` waarschuwt vooraf |
| Een gestopte sessie start vanzelf opnieuw | middel | middel | een grafsteen: een sessie die de app sloot, geeft 30 minuten 403 met de reden, niet 410 (§3.3) |
| Terug naar 0.6.0 | laag | hoog | de volgorde van fase 4 (Keuze 47): de commit controleren, `platform` en `media` stoppen, `db-downgrade 0007`, dan het script van nu met `PLATFORM_SRC_REF` op `263abd7`; de tag `v0.6.0` bij de integratie; stap 4 van de installatietest draait precies die commando's en controleert schema 0007 en app 0.6.0 |
| De segmentcache loopt de pool vol | laag | middel | `cache_gb` 20; per sessie een venster in bytes (`cache_gb` gedeeld door de live sessies); boven `cache_gb` gaan de oudste segmenten achter de speler weg, hoe jong ook; geen nieuwe transcode of remux onder 10 GB vrij; remux met een te lange GOP gaat naar transcode (`gop_too_long`); de melding (Keuze 31) |
| De analyse op de achtergrond houdt de rij bezet, of leest de schijf vol | middel | middel | eigen rijen (Keuze 14): `media_scan` voor scan en reconcile, `media` voor de analyse, `media_net` voor opzoeken en plaatjes; housekeeping alleen in `default`; `Defer` als er 2 transcodes lopen of de worker vol zit; `nice` en `ionice`; Cues en `stss` eerst |
| Een import van honderden GB duurt lang, of vraagt twee keer de ruimte | middel | middel | een eigen rij `import`; per bestand hervatbaar; verplaatsen binnen één dataset kost geen ruimte; Vraag 1 |
| Ondertitels van een groot bestand op verzoek duren minuten | hoog | laag | voor bibliotheken alvast op de achtergrond; anders "wordt voorbereid" en 202 |
| De TMDB-sleutel lekt | laag | middel | versleuteld, nooit terug, nooit gelogd; `test_media_secrets.py`; de sleutel kan alleen lezen |
| De voorwaarden van TMDB (bronvermelding, logo, niet-commercieel) | laag | laag | de zin en een link; het logo alleen als het mag; Vraag 4 |
| MusicBrainz blokkeert ons (te snel, geen User-Agent) | laag | laag | 1 per seconde voor het hele proces; de User-Agent met de URL van het repo; standaard uit |
| SSRF via een omleiding of een DNS-truc van een provider | laag | middel | alleen vaste hosts; omleidingen alleen naar de lijst; de controle zit in het verbinden: één keer opzoeken, het adres controleren, en met dát adres verbinden (SNI en Host op de naam), bij elke omleiding opnieuw (Keuze 39); een test met een nep-resolver die de tweede keer `127.0.0.1` geeft |
| De nep-providers wijken af van de echte | middel | middel | gebouwd naar de documentatie; `provider-shapes.json` met de echte vormen; `provider_real` met de hand; alles achter één adapter |
| De parser kiest een verkeerde titel | middel | laag | de testset van ≥ 500; NFO en id's in de naam gaan voor; "Weinig zeker" op `/beheer`; corrigeren met `locked` |
| Gapless klopt niet voor MP3 of AAC zonder `iTunSMPB` | middel | laag | eerlijk in de README; FLAC en Opus zijn bewezen (S8, e2e 8). (spike) S8: MP3 is exact in Chromium 141 met delay = `enc_delay`; Opus alleen via FLAC (de cache is dan ±9 × zo groot) |
| Gapless meten werkt headless niet (AudioWorklet achter MSE) | middel | laag | de terugval op `buffered` en de grenzen van `appendWindow`, met een eerlijke melding |
| De PGS-fixture via de eigen `.sup`-schrijver werkt niet | middel | laag | S9; VobSub komt uit dezelfde `.sup` met `-c:s dvdsub` (V8 deed dat op 6.1.1); anders schuift inbranden (§5). (spike) S9: werkt voor PGS en VobSub, met `-copyts` bij het muxen en `-fix_sub_duration` voor VobSub |
| ManagedMediaSource op de iPhone werkt anders, of iemand mist AirPlay | middel | laag | native HLS als terugval; AirPlay valt alleen weg bij MMS |
| `RLIMIT_FSIZE`, `RLIMIT_DATA` of `-threads 4` hindert ffmpeg | middel | laag | S10; V6 mat `RLIMIT_DATA` 1.200 MB met 4K HEVC naar 720 (RSS 520 MB); breekt het x264 op 1080, dan 2.000 MB voor live (de container heeft 3 GB); zonder `RLIMIT_FSIZE` blijven de bewaking van de RSS en de grens van de container. (spike) S10: alle grenzen werken op 1080 veryfast, `RLIMIT_FSIZE` 0 gaat aan, en `-threads 4` kost niets |
| Testen raken de grenzen van de sandbox (3 ffmpeg, 8 GB, 3 GB scratch) | laag | middel | `PLATFORM_MEDIA_MAX_PROCS=3` in het harnas en de belastingtest (de worker dwingt het af, stervende processen tellen mee); `ffmpeg_count` kijkt elke 0,5 s en faalt bij een vierde; fixtures ±60 MB; de bronnen van de belastingtest uit `synth` (±675 MB, ±3 minuten) |
| Eén proces: sessies, toelating en de SSE-hub in het geheugen | — | — | goed voor standalone; het cluster vraagt later routering op de naam van de node en een broker (Keuze 48) |
| Safari speelt `trun` versie 1 (negatieve offsets) niet goed via native HLS | laag tot middel | middel | hier niet te testen; een apparaattest in fase 9; de terugval is een `elst` in de init die de vertraging terugzet, of de knipper telt de offset bij `tfdt` op [niet getest] (Keuze 22, §3.7). (spike) Ook in Chromium is `trun` v1 met B-frames nog niet getest (geen H.264 in Playwright); dat komt met S13 |
| De kosten van 4K, VP9 en AV1 als bron kloppen niet | middel | middel | `d_codec` voor VP9 en AV1 is geschat; S11 meet het; een plan boven 1,8 × `f` telt dubbel; HDR boven 1920×1088 wordt geweigerd; de belastingtest op de LXC. (spike) S11 mat VP9 0,150 en AV1 0,222 (nu 0,18 en 0,25); VP9 met HDR hooguit 720 |
| Mb haalt 2,5 s niet op 3 cores | middel | laag | V7 mat 2,59 s voor 1080 alleen; daarom ≤ 2,5 s als mediaan en ≤ 3,0 s als slechtste; naast 2 andere ≤ 5 s; de startvoorrang; de toelating kiest anders een lagere trede |
| De import kan in de unprivileged LXC niet lezen of verplaatsen | hoog | laag | het commando voor de Proxmox-host `chown -R 110001:110001 /tank/platform/import` (README, Vraag 1); de import zegt vooraf wat niet te lezen of te verplaatsen is en stelt kopiëren voor; een test met een bestand van een andere uid |
| De planning loopt uit (±22 dagen) | middel | laag | de lijst hierboven van wat als eerste schuift; de kern schuift nooit |
| (spike) Een herstart van een MPEG-TS-bron mist tot één GOP: zonder index begint de uitvoer na `-ss` op de volgende keyframe (S2) | middel | laag | M4 kiest de aanpak (eerder beginnen en weggooien wat vóór de grens ligt, of TS altijd vanaf een keyframe uit de index); `interlaced.ts` in `test_media_restart.py` (Keuze 22) |
| (spike) Bij inbranden mist na een herstart een ondertitel die langer dan 30 s staat | laag | laag | de tweede fd begint 30 s eerder (S9); netter is de begintijd uit de stap `subs` (M5) |
| (spike) De referentie van `bench` klopt niet: 0,74 is de prijs van een bestand, de bench kost ±2 × zoveel (S7) | hoog zolang niet gemeten | middel | M1 meet de referentie van `bench` en `f_dec` in de sandbox; tot er gemeten is, is `f` 1,0 (Keuze 26); Mk controleert de voorspelling |

---

## 6. Verwerkte en afgewezen kritiek

Alle 45 punten heb ik nagelopen: in de code van `263abd7`, in de notities van fase 5 en, waar het
kon, met een meting in de sandbox (V1–V8 bovenaan, de commando's in
[haalbaarheid.md](haalbaarheid.md)). Geen enkel punt bleek onjuist. Waar ik een voorgestelde fix
anders deed of een deel niet overnam, staat dat onder "Afgewezen kritiek", met de reden.

### Verwerkt

**Veiligheid**

| Kritiek | Fix |
|---|---|
| SEC-1. De worker draaide als uid 10001, de eigenaar van elke blob. Een gekraakte ffmpeg kon een blob via `/proc/self/fd/3` openen om te schrijven, en de pipes van een andere job via `/proc/<pid>/fd`. J1 testte alleen de fd zelf. | De worker draait als `media` (uid 10002); blobs blijven 0600 van 10001. ffmpeg en ffprobe zijn 0711 in het image, en de worker zet zichzelf op niet-dumpbaar (`PR_SET_DUMPABLE 0`). De app maakt elke pipe, ook die voor de ondertitels. `/run/media` is 2750 `media:platform`, de socket 0660. Nagemeten in V1 en V2, ook in Docker. De zelftest in het image (S3, M1) controleert het. Wat overblijft, staat in §5.1. Keuze 11, M1, §3.7. |
| SEC-2. Intrekken gold alleen bij het begin van een verzoek: `/file` en `audio.mp4` liepen door na het sluiten van de sessie. Eén login intrekken (`revoke_family`) sloot de sessies niet. | `guarded_body`: `/file` en `audio.mp4` kijken per 4 MB of 5 s of de sessie nog leeft, en doen daar de controle van nu (cache 15 s). `media.sessions.close_login(family)` staat naast `office_tunnel.close_login` in `revoke_family`. Test: een gepauzeerde `/file`, intrekken, verder lezen; de verbinding sluit binnen één stuk. Keuze 21, §3.3, §3.5, M3. |
| SEC-3. Een sessie pinde de inhoud niet. Een nieuwe versie midden in het kijken gaf een mengsel van twee bestanden, en `on_discard` haalde ondertitels en sprites weg die de sessie nog gebruikte. `close_node` riep niemand aan. | De sessie onthoudt (sha, grootte). Elk openen gebruikt de gepinde sha en controleert dat de node er nog naar wijst; anders 410 `media_session_gone` met reden `replaced`. `files.notify` roept `close_node` aan bij vervangen, prullenbak en verwijderen, ook buiten een bibliotheek. `on_discard(sha)` sluit eerst de sessies op die sha. Tests voor vervangen tijdens het spelen, en zoeken daarna. Keuze 6, 21, §3.3, M3. |
| SEC-4. De import controleerde alleen `../`. Een symlink als wortel kwam elders in `/data` uit. Verplaatsen hashte een bestand dat nog geschreven werd, en een open fd kon de blob daarna nog veranderen. | Een dirfd met `O_DIRECTORY` en `O_NOFOLLOW`, en elke stap met `O_NOFOLLOW`; `realpath` onder `/data/import` en dezelfde `st_dev`. Per bestand `fstat` vóór en na het tellen (grootte, `mtime_ns`, `ctime_ns`); jonger dan 60 s overslaan; `st_nlink == 1` voor verplaatsen; na de rename `st_ino` controleren. Anders "in gebruik". Tests: een symlink als wortel, een groeiend bestand, een hardlink. Keuze 2, M2. |
| SEC-5. `trick.vtt` had relatieve adressen, maar elk plaatje had een eigen handtekening nodig, en die viel weg. Een getekend adres van 12 uur was niet in te trekken. | De sprites komen via de sessie: `/stream/{sid}/trick.vtt` en `/stream/{sid}/trick/{i}.jpg`, langs `sessions.guard`. De relatieve adressen werken zo, en intrekken geldt. Een test haalt elk adres uit `trick.vtt` op en eist 200, en na intrekken 403. Keuze 30, 34, M5. Over art: zie "Afgewezen kritiek". |
| SEC-6. `x<node_id>` nam een node-id uit het adres, zonder te controleren of die bij het item hoorde of te lezen was. | `x<i>` is een plek in de lijst `subs` die de sessie bij `play` pint. Die lijst is gefilterd met `access.resolve`, en bij een publieke link blijft alleen wat in de link ligt (`inside`). Anders 404. Tests: een ondertitel van een ander geeft 404, een los bestand buiten de link ook. Keuze 29, M3, M5. |
| SEC-7. Het geheugen werd alleen elke 2 s gemeten. Een bron met enorme afmetingen kon tussendoor GB's pakken. | `RLIMIT_DATA` vóór exec: 1.200 MB voor live en sprites, 512 MB voor korte jobs. `-max_alloc 536870912` en `-max_pixels 35389440` op elke argv. Groter dan 8192×4320 geeft 415 `media_unsupported`. De RSS blijft als telemetrie. V6: 1.200 MB werkt met 4K HEVC naar 720, 400 MB stopt ffmpeg na 0,4 s. S10 doet het op 1080 in het image. Keuze 12, §3.3, §3.7. |
| SEC-8. De controle op privé-adressen stond los van het verbinden. httpx zoekt de naam zelf op, en kan dan een ander adres krijgen. | Eén keer opzoeken, het adres controleren, en met dát adres verbinden, met de extensie `sni_hostname` van httpcore en een `Host`-kop. Bij elke omleiding opnieuw. Test met een nep-resolver die de tweede keer `127.0.0.1` geeft. Keuze 39, M8. |

**Media**

| Kritiek | Fix |
|---|---|
| MED-1. De kosten waren per trede, gemeten met een 1080p-bron. Decoderen van 4K of HEVC10 telde niet mee. "4K HDR live nooit" stond nergens als regel. | `kosten = (tabel × fps/24 + d(bron) − d(ref) + extra) × f`, met `d_codec` uit V3 (H.264 0,18, HEVC 0,31). `bench` meet ook het decoderen van HEVC10 (`f_dec`). Een plan boven 1,8 × `f` telt als 2 plaatsen. Een regel in decide: HDR groter dan 1920×1088 geeft 422 `media_hdr` met reden `too_large`. Nagerekend tegen de metingen: binnen 0,1 core. Tests in `test_media_decide.py` en `test_media_admission.py`. Keuze 26, 27. |
| MED-2. Zonder edit list komt het eerste beeld een B-frame te laat (+83 ms bij remux, +104 ms bij transcode). J4 en S2 keken alleen naar `tfdt`. | `+negative_cts_offsets` in `-movflags` voor transcode en remux, dus `trun` versie 1 met de eerste offset 0 (V4, op de bestanden van de critici). De knipper leest getekende offsets. S2 eist `tfdt` plus de offset van het eerste sample = de brontijd + 1 s, binnen één frame. S4 meet het in Chromium met `requestVideoFrameCallback`. Keuze 22, 23, §3.3, §3.7. Over Safari: zie "Afgewezen kritiek". |
| MED-3. x264 hield `keyint=250`. Boven 125 fps kwamen er extra IDR's in een segment. En niets begrensde de fps, dus het level klopte niet. | `-g 100000` en `-fpsmax 60` in de argv van transcode (ook bij VP9, al is het daar volgens V5 niet nodig). De knipper deelt fragmenten in op de pts van het eerste sample, niet "één fragment is één segment". V5: met `-g 100000` vallen de keyframes precies om de 2 s, `-fpsmax 60` geeft 60 fps. `fps240.mp4` in S2. Keuze 22, 24, §3.3. |
| MED-4. Geforceerde ondertitels gingen vanzelf aan, ook als beeld (PGS). Zo werd direct play een transcode, en een publieke link gaf 403. | Alleen tekst gaat vanzelf aan. Bij een beeldspoor houdt decide de modus en geeft `forced_available`. De speler biedt "Geforceerde ondertitel inbranden (kost omzetten)" aan. Tests: PGS geforceerd met een profiel dat direct kan, geeft `direct`; een publieke link geeft `remux`, geen 403. Keuze 19, 29, M3. |
| MED-5. De keyframe-index (tot 4 MB) paste niet in een bericht van 64 KB. | De worker geeft de index al in de opgeslagen vorm (zlib, varint-delta's, base64), hooguit 48 KB. Daarboven dunt hij uit tot één keyframe per seconde; dat is genoeg voor de regel van 6 s. Test met 50.000 keyframes (`test_media_keyframes_wire.py`). Keuze 12, 17, M4. Over `sparse`: zie "Afgewezen kritiek". |
| MED-6. Bij de voortgang won de laatste PUT. Een late PUT kon gezien en positie 0 overschrijven. | Elke PUT krijgt `seq` (oplopend per `origin`) en `started_at` van `play`. Een PUT die niet nieuwer is, of van een sessie die begon vóór de laatste keer gezien, telt niet. Gezien gaat alleen weg door "Niet gezien", of door een nieuwe sessie die verder komt dan 60 s. `playback_state` krijgt `seq`, `play_started_at` en `watched_at`. Tests voor beide volgordes in `test_media_progress.py`. Keuze 36, §3.1, M7. |
| MED-7. Codec-strings kwamen uit `avcC` en `hvcC`, maar MPEG-TS heeft Annex-B. | Annex-B herkennen (`00 00 01`) en profiel en level uit de eerste SPS halen (bij HEVC ook de VPS). Fixtures `a.ts` en `a.m2ts` in `test_media_codecs.py`. Keuze 16, M3. |
| MED-8. Keuze 7 liet het seizoen leeg bij absolute nummering, Keuze 8 gaf seizoen 1. | Eén regel: geen seizoen, wel `absolute`. De serie toont die afleveringen in één groep "Afleveringen", op volgorde van het nummer, en verzint nooit seizoen 1. Beide in `names.tsv` en in de test van het groeperen. Keuze 7, 8, M2. |
| MED-9. De poort van 100 % lag op een eigen set, en de fuzz testte alleen of de parser niet crasht. `1x02` botst met resolutie en codec. | Resolutie, codec, audio en bron eerst weghalen. `NxNN` alleen met N ≤ 99, zonder cijfer ervoor of erna. Die namen in `names.tsv`. Een fuzz-eigenschap: een naam zonder afleveringstoken is nooit een aflevering. Keuze 7, M2. |
| MED-10. Een vast verbod op Hi10P dwong ±1,4 cores transcode af, ook op clients die het direct kunnen. | `avc1.6E00xx` telt als elke andere string: alleen als hij in het profiel staat en `decodingInfo` hem steunt. Uit in de basisprofielen van webOS 5, Safari en Firefox. Tests met `h264_hi10p.mkv`: `chromium141` remux, `safari17` transcode. Keuze 19, M3. |
| MED-11. De gezondheid gebruikte een gewone verbinding. Onder volle last gaf de worker `busy`, en dat kon als "down" tellen. | `caps` gaat over een eigen controleverbinding die niet meetelt; de worker antwoordt uit de cache en start geen proces. `busy` betekent in `health.py` "ok, maar druk". Een test in `test_media_health.py` met alle plaatsen bezet. Keuze 12, M1. |
| MED-12. Elke zoekactie buiten het venster startte meteen een nieuwe producent, terwijl de oude nog stopte. Snel scrubben in 3 sessies brak de grens van 3 ffmpeg en van 3 GB. | Eén producent per sessie: de nieuwe start pas na `waitpid` van de oude (SIGKILL na 2 s). Zoekverzoeken binnen 300 ms worden samengevoegd. Een stoppend proces telt mee in de plaatsen van de worker. Test: 10 keer zoeken in 1 s, nooit meer dan 2 ffmpeg per sessie. Keuze 12, 25, M1. |
| MED-13. Een video zonder audio liep vast: altijd `-map 0:{A}` en AAC, en de lint eiste audio. | `A` is optioneel. Het plan krijgt `audio: "none"`, de argv geen `-map` en geen `-c:a`, de mime alleen video, en de lint aanvaardt CODECS met alleen video als de probe geen audio heeft. De fixture `noaudio.mp4` in de tests van decide, de stroom en de lint. Keuze 19, 24, §3.3, M3, M4. |
| MED-14. Losse `.sup`, `.sub` en `.idx` werden gekoppeld, maar konden nergens spelen. | We koppelen ze in fase 5 niet; ze staan in `names-open.tsv`. De speler toont nooit een spoor dat decide niet kan spelen. Ingebedde PGS en VobSub werken wel. Test: een losse `.sup` wordt niet gekoppeld. Keuze 5, 29, M5. |

**Haalbaarheid**

| Kritiek | Fix |
|---|---|
| R01. De stappen van de spike in het image liepen via `workflow_dispatch`, en kwamen pas na M0-api. Zo bevroor M0 contracten die op ffmpeg 7.1.5 nooit gedraaid hadden. | De job `media-spike` staat in de eerste commit van M0 (dag 1), als gewone job op `pull_request`, met een filter via `git diff --name-only` (geen nieuwe action). `workflow_dispatch` kan hier niet: dat werkt alleen op de standaardbranch, en `main` heeft alleen een README. De job bouwt het image, draait de stappen en bewaart `spike.json`. De poort §3.8 eist dat artefact van de laatste commit. §3.7, §4.8, §5. |
| R02. VP9 telde als 2× x264, terwijl het even duur is. De HDR-fixture paste dan niet. HDR-kosten ontbraken voor 1080 superfast, 720 superfast en 480. | VP9 = 1,0× x264 (V3: 0,57–0,59 tegen 0,60 cores). Waar HDR niet gemeten is, de factor 1,65 op de SDR-kosten. S11 draait in de sandbox, onder `taskset -c 0-2`. Tests: "eerste kijker, VP9 HDR 720, B = 3,0 → 720 veryfast" in `test_media_admission.py`, en in e2e #4 de trede uit `admission.snapshot()`. Keuze 24, 26, §3.7. |
| R03. Remux had een keyframe-index nodig die alleen de analyse in een bibliotheek maakte. Elke mkv daarbuiten werd een transcode, en achter een publieke link gaf hij 403. | `play` draait de stap `keyframes` meteen na de probe, in het budget van 5 s, met de eigen lezers (Cues, `stss`), voor elke video. Het resultaat gaat in `media_probe`. Alleen de terugval met ffprobe is achtergrondwerk; zonder index wordt het nu transcode, en achter een publieke link 409 `media_probe_pending` met reden `keyframes`. "Of snel te lezen" is weg uit regel 6. Een e2e: een mkv buiten een bibliotheek speelt via een publieke link als remux. Keuze 17, 19. |
| R04. Scan, reconcile, opzoeken, plaatjes en analyse deelden één rij. Een nieuwe upload wachtte dan achter honderden analyses. | Eigen rijen: `media_scan` (scan, reconcile), `media` (analyse), `media_net` (opzoeken, plaatjes) en `import`. Test in `test_jobs_lanes.py`: met 100 analyses in de rij loopt een nieuwe scan binnen één tik. Keuze 14, §3.5. |
| R05. Terug naar 0.6.0 herhaalde de fout van fase 4: het schema ging terug terwijl de app draaide, en het script zonder versie haalde `main` op (0.7.0). Er was geen tag. | De volgorde van fase 4: `git cat-file -e '263abd7…^{commit}'`, `dc stop platform media`, `dc run --rm --no-deps platform python -m app.cli db-downgrade 0007`, dan `curl …/install.sh \| PLATFORM_SRC_REF=263abd7… bash`. De tag `v0.6.0` bij de integratie. Stap 4 van de installatietest draait precies dit, en controleert schema 0007 en app 0.6.0. Keuze 47, M10, §5. |
| R06. De speler gaf het na 7 s op, maar een update duurt langer. De e2e herstartte de app te snel om dat te zien. | Bij een netwerkfout, 502, of 503 zonder code: opnieuw na 1, 2, 4 en 8 s, dan elke 10 s, zolang er buffer is plus 120 s. Ondertussen kijkt de speler naar `/api/v1/health`. Na 410 een nieuwe sessie op de plek. Harnasoptie `outage_s=30`: de speler loopt ≤ 10 s na "ready" weer. Een geval in `session.test.ts` met 30 s fouten. Keuze 32, M6, §3.6. |
| R07. De grens van Mb lag onder de verwachting, en de metingen draaiden zonder de grens van 3 cores. | Mb in twee eisen: alleen ≤ 2,5 s (mediaan) en ≤ 3,0 s (slechtste); naast 2 andere ≤ 5 s. Alle metingen, `bench` en de belastingtest onder `taskset -c 0-2` of `--cpus 3` (regel 14). V7 mat 2,59 s voor 1080 alleen op 3 cores. §4.5, §4.6. |
| R08. De belastingtest startte nooit drie sessies tegelijk. Een nieuwkomer vocht met producenten die hun buffer vulden. | Fase b (drie op t = 0) en fase c (de app herstarten, alle drie komen samen terug), elk met eigen eisen. Startvoorrang: zolang een sessie geen segment 0 heeft, lezen de andere hooguit 10 s vooruit. V7 mat zonder die rem 3,3–4,0 s en 0,28 s haperen. Keuze 26, §4.6. |
| R09. H.264 in een browser werd nergens bewezen. Een overgeslagen test hield CI groen, en `media_perf` liep in CI niet. | De job `e2e-media-perf` (een rapport) met `-m media_perf`. Het artefact noemt de browserversie en wat `isTypeSupported` zegt voor `avc1` en `mp4a` (S13). Zegt CfT 156 ja, dan zet `e2e-media` `MEDIA_REQUIRE_H264=1`, en faalt een H.264-test in plaats van over te slaan. §3.7, §4.3, §4.8. |
| R10. De belastingtest paste niet in de bevroren contracten: geen opdracht om bronnen te maken, geen schrijfbare map, sessies alleen op een node. `mandelbrot` kostte ±7 minuten per bron. | M0 legt `Op.SYNTH` vast: alleen `testsrc2` en `cellauto` met een seed, vaste parameters, uitvoer naar een fd die de app opent in `media/loadtest/`. `SessionSource = Node \| LoadtestFile` in `sessions.create`, alleen met `PLATFORM_MEDIA_LOADTEST=1`. V8: drie bronnen in ±3 minuten. Keuze 12, 21, §3.3, §4.6. |
| R11. De worker telde alle live verbindingen samen, de toelating alleen transcodes. Een goedgekeurde play kon `busy` krijgen, zonder HTTP-code. Zoeken bij 3 streams faalde kort. | Plaatsen per soort: transcode ≤ `max_transcodes`, remux en audio 6, achtergrond 1, kort 2, en `caps` over de controleverbinding. De Grant van de toelating reserveert de plaats in de worker. `busy` wordt 503 `media_busy` met reden `worker` en `Retry-After: 2`; op de achtergrond `Defer(30)`. Zoeken wacht op `waitpid` (MED-12). Tests in `test_media_admission.py` en in het contract van de worker. Keuze 12, 26. |
| R12. Wat alleen een werkend image bewijst, kon in de sandbox niet. "Geen ffmpeg in de app-container" paste bij geen enkele job. | Per omgeving staat wat bewezen wordt. In de sandbox: direct play, `ffmpeg_missing` op `/beheer`, terugrollen. In CI, in de job `media-image`: installeren vanaf 0.6.0, bijwerken, een transcode via curl (`POST /play`, dan segment 0), en `docker top`: geen ffmpeg in de app, één in media. Het klaar-als van M1 is die curl-test. M1, M10, §4.8. |
| R13. Het recept voor `vobsub.mkv` kon niet: ffmpeg zet tekst niet om naar beeld. | VobSub komt uit de eigen `.sup`, met `-c:s dvdsub` (beeld naar beeld). V8: `srt` naar `dvdsub` faalt (rc 234), `.sup` naar `dvdsub` werkt (1280×720, pakketten op 0, 10, 17 en 30 s). S9 doet het op 7.1.5. §3.6, §3.7. |
| R14. "Nooit meer dan 3 ffmpeg" werd in de e2e niet afgedwongen, en de worker stond er meer toe. | `PLATFORM_MEDIA_MAX_PROCS`: standaard 10, in het harnas en de belastingtest 3. De worker dwingt het af vóór elke start, en een stoppend proces telt mee. Op de achtergrond `Defer`, live `media_busy`. `ffmpeg_count` kijkt elke 0,5 s (in een thread) en faalt bij een vierde. Keuze 12, §3.2, §3.6, §4.2. |
| R15. Elke Runner draaide zijn eigen housekeeping, dus drie keer tegelijk. | `Runner(settings, lane=…, housekeeping=False)` voor elke rij behalve `default`. Test in `test_jobs_lanes.py`: elke housekeeping één keer per periode. Keuze 14, §3.5. |
| R16. `serve.py` stopte eerst https en dan http. Een lange stream gebruikte de 10 s van Docker, en dan kwam SIGKILL vóór de lifespan sloot. | https en http stoppen samen, met één deadline van 10 s. Bij het begin van het stoppen gaan de mediasessies en de wachtende segmentverzoeken dicht (503 met `Retry-After`). `stop_grace_period: 30s` voor `platform` in `compose.media.yaml`. Test: stoppen met een gepauzeerde stream; de logregel van de lifespan komt binnen de grace-periode. F3, §3.5, M1. |
| R17. F4 paste alleen `WEB_CSP` aan. Het web-image heeft de CSP 5 keer in `nginx.conf`, en de job `images` controleert beide. | `web/nginx.conf` (alle 5 koppen) en de verwachte CSP in de job `images` horen bij M0 (F4). `test_csp_nginx.py` vergelijkt de CSP in `nginx.conf` met `main.WEB_CSP`. §2.0, §3.5, §3.8. |
| R18. `install.sh` haalde het oude app-image weg terwijl de oude media-container het nog gebruikte. Elke update liet zo een image achter. | `old_app` gaat pas weg na `media_start` en na office. `test_install_media.sh`: na twee updates één image `platform:local`, en de oude id bestaat niet meer. Keuze 47, M10. |
| R19. Shift+N voor "volgende aflevering" botste met de Shift+N van de app. In de viewer botsten Home, End, F en I. | "Volgende aflevering" is N, alleen op `/kijken`. In de viewer krijgt de speler alleen Spatie, K, J, L, M, C, A, P en 0–9; Home, End, F en I blijven van de viewer. `keys.test.ts` controleert dat combinaties over de scopes uniek zijn, of bewust overschreven. Keuze 33, M6. |
| R20. Cache en opruimen waren op transcode berekend. Een 4K-remux kwam ver boven `cache_gb`, de schijfcheck gold alleen voor transcode, en de tegendruk telde in segmenten. | Een venster in bytes per sessie (`cache_gb` gedeeld door de live sessies). Boven `cache_gb` gaan de oudste segmenten achter de speler weg, hoe jong ook. De schijfcheck ook voor remux. De tegendruk in seconden. Remux met bitrate × GOP boven 64 MB wordt transcode (`gop_too_long`). Keuze 23, 26, 31, M4. |
| R21. B = 3,0 rekende op één vrije core, maar office mag zelf 2 cores. Na een herstart maakt DS zijn fonts opnieuw, precies als alle streams terugkomen. Md testte geen herstart van DS. | Zolang de office-monitor zegt dat DS start of fonts maakt, is het budget `B − 1,0`. Lopende streams lopen door; nieuwe plannen krijgen een lagere trede. Fase d van de belastingtest en een run in Md: een herstart van DS midden in de test. Keuze 26, §4.5, §4.6. |
| R22. De duur van CI was niet begroot. De mediatests kwamen in de bestaande jobs, en `e2e` had geen `timeout-minutes`. | Twee eigen jobs naast de rest: `api-media` (`-m media`) en `e2e-media`, elk met `timeout-minutes: 20`. Een eigen deadline per test in het harnas. De verwachte duur staat in §4.8. |
| R23. `chown -R 10001:10001` werkt niet in een unprivileged LXC: bestanden van de host zijn daar 65534, en root in de LXC kan ze niet chownen. | Het commando voor de Proxmox-host: `chown -R 110001:110001 /tank/platform/import` (10001 plus de offset 100000). Dat verzwakt niets. De import zegt vooraf wat niet te lezen of te verplaatsen is, en stelt dan kopiëren voor. Test in `test_media_import.py`: een bestand van een andere uid komt in "overgeslagen". Keuze 2, Vraag 1, M10. |

### Uitslag van de spike (M0)

Geen kritiek, maar een meting. De spike (S1 tot S12: in CI op het echte image en in de sandbox;
S13 draait later in `e2e-media-perf`) legde de commando's van de worker vast. Geen enkele stap
had een terugval van §3.7 nodig: `compose.media.yaml` blijft zoals Keuze 11, de tone-mapping van
Keuze 27 is er, en inbranden blijft in fase 5. §3.3 en de Keuzes zijn bijgewerkt naar
[spike.md](spike.md), en elke plek die veranderde, staat erin met "(spike)". Revisie 2 blijft
verder de tekst. Wat er anders is dan hierboven:

| Uitkomst | Bijgewerkt in |
|---|---|
| Een fd uit `SCM_RIGHTS` deelt zijn positie (S3). Dus `lseek` vóór elke exec, één kind per fd, de blob per opdracht opnieuw open, `recvmsg` met `MSG_CMSG_CLOEXEC`, `dup2` alleen in het kind, en de app sluit de pipe zelf | Keuze 12, §3.3, M1 |
| `lavfi` staat in 7.1 onder `-devices` (S1), en komt nooit in de witte lijst van een invoer-fd | Keuze 12, §3.3, M1 (de zelftest) |
| Het rooster hangt aan het eerste videobeeld (`origin`): segment 0 loopt tot `origin + 2`, een herstart begint met `-ss (origin + 2k)`, en N komt uit het laatste videobeeld (S2, nagemeten in spike.md) | Keuze 22, 25, §3.3, M4, §3.6 (de lint) |
| MPEG-TS zoekt niet precies (S2); M4 kiest de aanpak | Keuze 22, 23, 25, §5.1 |
| Alleen de `stsd` telt, en er is geen `elst` (S5); een andere `stsd` speelt in dezelfde SourceBuffer (S4) | Keuze 22, 32 |
| Chromium springt niet zelf over een gat (S4): de springer gaat na 250 ms | Keuze 25, 32, M6 |
| Geen `-readrate` (S6) | Keuze 23, 26, §3.3 |
| `bench` is bruikbaar, maar 0,74 is geen referentie voor de bench (S7); M1 meet hem | Keuze 26, §3.3, §5.1 |
| Opus via `remux_audio -c:a flac`, MP3 met delay = `enc_delay` (S8) | Keuze 18, 38, §3.3, M9 |
| Inbranden met de ondertitels uit een tweede fd, 30 s eerder; eerst schalen, dan `overlay` (S9) | Keuze 24, 29, §3.3, §3.6, M5 |
| `RLIMIT_FSIZE` 0 gaat aan, en `-threads 4` kost niets (S10) | Keuze 12, §3.3 |
| `d_codec` VP9 0,18 en AV1 0,25; VP9 met HDR hooguit 720 (S11); level 3.1 voor 480 boven 25 fps; HEVC met open GOP geeft CRA-keyframes | Keuze 23, 24, 26, 27, §3.3 |
| F1 en F2 zijn nodig zoals beschreven (S12) | §3.5 |
| De regel "geen ffmpeg buiten de worker" gaat over `app/` | §2.0, regel 4 |

De keuzes van Jonas (B39–B48) staan nog open; de spike ging uit van de standaard. Wat de spike
niet kon testen (Safari en Firefox, `trun` v1 met B-frames in een browser, een ondertitel die
langer dan 30 s staat, de grootte van de cache voor Opus, HDR met VP9 op 7.1.5), staat bij de
open punten van `spike.md`.

### Afgewezen kritiek

| Kritiek | Waarom niet |
|---|---|
| SEC-5, het tweede deel: "ook art via de sessie, of per verzoek ondertekend". | Art (posters, fanart, hoezen) hoort niet bij een sessie: de startpagina en de bibliotheek tonen het terwijl niemand speelt. Het blijft een ondertekend adres op de sha, 12 uur geldig, zoals de previews van fase 3 (Keuze 34). Een poster zegt minder dan de previews die fase 3 al zo serveert. Alleen de sprites gaan via de sessie, want die horen bij één film. `trick.vtt` per verzoek opnieuw maken met ondertekende adressen is niet nodig: via de sessie werken de relatieve adressen zoals ze er staan. |
| MED-1: "HDR boven 1920×1080 **bij ≤ 30 fps** weigeren". | De regel kijkt alleen naar de grootte. Een 4K HDR-bron van 60 fps is nog duurder, en zou met "≤ 30 fps" juist doorkomen. De grens is 1920×1088, omdat een 1080p-bron soms als 1088 hoog gecodeerd is. |
| MED-2: "controleer in S5 dat Safari `trun` versie 1 speelt". | Safari is in de sandbox en in CI niet te testen. Het wordt een apparaattest in fase 9, en het staat bij de risico's (§5.1). De terugval ligt klaar: een `elst` in de init, of de knipper telt de offset bij `tfdt` op [niet getest]. |
| MED-5: "zet een uitgedunde index op `keyframes_state = sparse`". | `sparse` betekent bij ons al iets anders: Cues met gaten boven 20 s, en dus de terugval nodig (Keuze 17). Een uitgedunde index heeft één keyframe per seconde en is genoeg voor remux. Hij blijft `ready`. Het andere voorstel, grote antwoorden over een eigen fd, is met de grens van 48 KB niet nodig. |
| R04, de andere optie: "een kolom `priority` op Job". | Dat is een migratie van een bestaande tabel (uit 0004), en `claim()` verandert dan voor alle taken. Eigen rijen lossen het op zonder dat. |
| R07: "zonder browser alleen ≤ 2,5 s", als harde grens op elke run. | V7 mat op 3 cores 2,59 s voor 1080 alleen. Een harde grens op elke run faalt dan op ruis. Daarom ≤ 2,5 s als mediaan en ≤ 3,0 s als slechtste run. De belofte naar de gebruiker (≤ 5 s naast 2 andere) blijft gelijk. |
| R08: "de herstart als fase van de belastingtest in de app". | De belastingtest draait in de app. Stopt de app, dan stopt ook de test. Fase c draait daarom alleen met spelers buiten de app: in de sandbox via het harnas, op de LXC met `--extern`. |
| R09: "zet `MEDIA_REQUIRE_H264=1` in CI" zonder voorwaarde. | Of Chrome for Testing 156 H.264 en AAC kan, weten we nog niet. Kan hij het niet, dan faalt CI op elke H.264-test, zonder dat de code fout is. Eerst legt S13 het vast; bij ja gaat de vlag aan. Bij nee wordt H.264 bewezen op de LXC (Ma). |
| R10, de andere optie: "de bronnen als gewone nodes in een verborgen beheermap". | Een verborgen map in de boom moet uit het zoeken, het quotum, de prullenbak, de scanner en de lijsten blijven. Dat raakt veel code van fase 3. Een `LoadtestFile` met een eigen controle is kleiner. |
| R11: "één vrije plaats extra in de worker voor een herstart". | Niet nodig: de nieuwe producent start pas na `waitpid` van de oude (MED-12), en de Grant van de toelating reserveert de plaats al. Een vrije plaats extra zou de grens van 3 ffmpeg in het harnas juist breken. |
| R14: "`PLATFORM_MEDIA_MAX_PROCS` standaard 6". | Met 3 transcodes, 1 achtergrondjob, 2 korte jobs en een proces dat nog stopt, zijn 6 plaatsen te weinig. Dan krijgt een gewone play `busy`. De standaard is 10; in het harnas en de belastingtest 3. |
| R21, de eerste optie: "trek het CPU-gebruik van de office-container (uit zijn cgroup) van B af". | Docker geeft elke container een eigen cgroup-namespace. De app ziet de cgroup van office niet, en die openzetten verzwakt de afscherming. De staat van DS uit `office.health` zegt genoeg: `B − 1,0` zolang DS start of fonts maakt. |
| R22: "cache de fixtures met actions/cache" en "`faulthandler_timeout`". | De fixtures maken kost ±30 s. Een cache is een nieuwe action, en dat weegt niet op tegen 30 s. `faulthandler_timeout` schrijft alleen een stacktrace en stopt de test niet; een eigen deadline in het harnas doet dat wel. |
