> Werkdocument: het bouwontwerp van fase 5 (media en streaming), revisie 1.
> Nog niet besloten. De vragen aan Jonas staan onder "Vragen voor Jonas". Tot hij antwoordt,
> bouwen we op de standaard die daar staat.

# Fase 5 (media en streaming): bouwontwerp, revisie 1

Basis: branch `claude/stappenplan-uitwerken-lu4uv3` @ `263abd7` (v0.6.0, migraties 0001–0007).

Revisie 1 voegt twee ontwerpen samen. Ontwerp A ("eenvoudig, veilig en eerlijk over de
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

  Dit was in de sandbox, niet in een LXC [niet getest in de LXC].
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
  - HDR naar SDR kost 2,3 cores op 1080p en 1,21 op 720p.

  De toelating geeft daarom 1080p aan de eerste kijker en een lagere trede aan de tweede en derde,
  zodat de derde altijd nog past (Keuze 26, Vraag 3).
- **4K HDR live omzetten past niet** (3,2 cores voor 1,0×). HDR naar SDR live tot 1080p, en
  standaard op 720p (Keuze 27).
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
- **Tijd.** Het stappenplan geeft 5 weken. Dit ontwerp: ±20 werkdagen, met marge (§5).

### 0.2 De invalshoek

| Principe | Wat het hier betekent |
|---|---|
| Weinig bewegende delen | Eén extra container (de worker), uit hetzelfde image. Geen Redis, geen wachtrij-server, geen tweede database. Sessies in het geheugen van de app. Segmenten in een gewone map, per sessie. |
| Direct play eerst | Een bestand dat de client kan spelen, gaat als bytes met Range: 0 cores. Remux kost bijna niets. Transcode alleen als het niet anders kan. |
| Eén segmentmodel | ffmpeg schrijft fMP4 naar een pipe. De app knipt. Bij transcode is elk segment precies 2 s; bij remux volgen de segmenten de echte keyframes. |
| ffmpeg apart | ffmpeg draait alleen in de worker: geen netwerk, geen data, alleen een open bestand en een pipe terug (Keuze 11, 12). |
| Het profiel komt uit de client | De browser test zelf wat hij kan, met de echte codec-strings van het item. Geen lijst van browsers (Keuze 20). |
| Toegang per verzoek | Elk verzoek onder `/api/v1/stream/` controleert de sessie en, met een cache van hooguit 15 s, het recht van nu (Keuze 21). |
| Nep voor alles buiten ons | Een nep-worker, een nep-stream, en nep-servers voor TMDB, MusicBrainz en de Cover Art Archive. Geen test praat met een echte dienst (§3.6). |
| Update kan niets breken | Media is een extra laag. Zonder worker werkt alles van fase 4. `install.sh` stopt nooit door media. Terug naar 0.6.0 kan (Keuze 47). |
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
 │ decide, sessies, toegang, toelating, knipper,       │  fd    │ geen netwerk, alleen lezen,        │
 │ playlists, cache in /data/media                     │ ─────▶ │ geen /data, geen geheimen          │
 │ opent elke blob zelf (O_RDONLY)                     │ ◀───── │ ffmpeg leest fd:, schrijft pipe:1  │
 └─────────────────────────────────────────────────────┘  pipe  └────────────────────────────────────┘
                  Unix-socket in het benoemde volume media-run (/run/media)
```

- De app opent het bestand (alleen lezen) en geeft de worker de file descriptor over een
  Unix-socket. ffmpeg ziet geen pad, geen `/data`, geen database en geen netwerk (J1).
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
   - Ontdubbelen: staat de inhoud er al, dan komt er alleen een node bij.
   - Quotum: telt mee voor de eigenaar, net als een upload. Past het niet, dan stopt de import met
     `no_room` en een lijst van wat wel binnenkwam.
   - Hervatbaar en idempotent: een map die al bestaat, wordt hergebruikt. Dezelfde naam met
     dezelfde sha wordt overgeslagen. Een andere sha wordt een nieuwe versie, zoals bij uploaden.
   - Veilig: de import volgt nooit een symlink en slaat speciale bestanden over
     (`os.scandir`, `follow_symlinks=False`). Een pad buiten `/data/import` geeft
     `media_import_path`. Een bestand dat niet leesbaar is, komt in de lijst "overgeslagen".
   - Rechten op de LXC: `chown -R 10001:10001 /tank/platform/import`. De README zegt het.
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
     haakregel: `media.hooks.touched(nodes)`. Die doet niets zolang er geen bibliotheek bestaat
     (een vlag in het geheugen). Anders zet hij de node-id's in een set in het geheugen (hooguit
     50.000; daarboven: een volledige ronde voor die eigenaar). Geen I/O.
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
     - ondertitels: `srt vtt ass ssa sup sub idx`;
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
     `convert.on_discard` van office. Die gooit de ondertitels, sprites en audio-cache van die sha
     weg.
7. **Een eigen bestandsnaamparser** (`app/media/names.py`, pure functies, geen I/O).
   - `parse(parts: list[str], kind) -> Parsed`. `parts` is het pad vanaf de map van de
     bibliotheek. `Parsed` heeft: `title`, `year`, `season`, `episode`, `episode_end`, `date`,
     `part`, `edition`, `extra`, `ids` (`imdb`, `tmdb`, `tvdb`), de map van de serie, en een
     zekerheid tussen 0 en 1.
   - Werkwijze: tokens maken (punten, underscores en streepjes worden spaties, behalve in een
     datum of id), ruis wegknippen vanaf het eerste ruistoken (resolutie, bron, codec, audio,
     groep, `PROPER`, `REPACK`), het jaar zoeken (het laatste jaar tussen 1900 en volgend jaar dat
     niet de hele titel is), en de map erboven gebruiken als de naam weinig zegt.
   - Wat hij kent:
     - Jellyfin: `Naam (2010)/Naam (2010) [imdbid-tt0133093] - 1080p.mkv`, `{edition-…}`;
     - Plex: `{tmdb-603}`, `{imdb-tt…}`, `{edition-Director's Cut}`;
     - Kodi en scene: `Naam.2010.1080p.BluRay.x264-GROEP.mkv`;
     - delen: `cd1`, `part2`, `pt.2`, `disc 1`;
     - series: `S01E02`, `s1e2`, `1x02`, `S01E02-E03`, `S01E02E03`, `S01.E02`, `Season 1
       Episode 2`, `Seizoen 1 Aflevering 2`, `102` alleen in een seizoensmap;
     - `Season 00` of een map `Specials` is seizoen 0;
     - datums voor dagelijkse shows: `Show 2024-03-01`, `2024.03.01`;
     - absolute nummering (`[Groep] Titel - 012 [1080p]`): seizoen leeg, sorteren op nummer;
     - lastige titels: `2001 A Space Odyssey (1968)`, `1917 (2019)`, `Blade Runner 2049 (2017)`,
       `Mr. Robot`, Unicode en punten in de titel.
   - Muziek: tags gaan voor. Het pad is de terugval: `Artiest/Album (Jaar)/01 - Titel.flac`, met
     submappen `Disc 2` en `CD2`.
   - **De testset:** `api/tests/media/names.tsv`, minstens 500 regels, zelf geschreven. Groepen
     (zoals B ze opsomde): films, scene-tokens, ids, edities en delen, extra's en samples,
     SxxEyy-varianten, meerdere afleveringen, specials, datums, absolute nummering, Nederlands,
     jaartallen in de titel, Unicode, leestekens, muziek en rommel (`video.mkv`, `1.mp4`,
     `DSC_0001.MOV`).
   - De poort: **100 %**. Een vorm die we bewust niet aankunnen, staat in `names-open.tsv` met een
     reden. Elke gevonden fout wordt eerst een nieuwe regel.
   - Eigen fuzz (geen hypothesis): 10.000 namen uit een vaste seed, uit dezelfde bouwstenen. Nooit
     een uitzondering, het jaar altijd binnen bereik, nooit een lege titel als er letters zijn.
     10.000 namen in minder dan 0,5 s.
   - `python -m app.cli media-parse <pad>…` toont wat de parser ziet. Op `/beheer` staat per
     bibliotheek een lijst "Weinig zeker" (zekerheid < 0,5).
8. **Groeperen.**
   - Afleveringen hangen aan een serie (`media_show`), via de map van de serie, anders via de
     genormaliseerde titel. Een aflevering zonder seizoen krijgt seizoen 1.
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
    - De afscherming (compose in M1):
      - `network_mode: none`;
      - `read_only: true`;
      - `tmpfs` op `/tmp` (64 MB) en op `/data` (1 MB, leeg). Die laatste overschaduwt `VOLUME
        /data` van het image, zodat er geen anoniem volume ontstaat;
      - `cap_drop: [ALL]`, `no-new-privileges`, `pids_limit: 256`, `core: 0`, `nofile: 1024`;
      - geen datavolume. Alleen het benoemde volume `media-run` op `/run/media`, voor de socket.
        Het Dockerfile maakt `/run/media` met eigenaar 10001 en modus 0700; een leeg benoemd volume
        neemt dat over [gemeten J1; niet getest in de LXC].
    - Grenzen alleen als de cgroups ze aankunnen (dezelfde `limits_supported` als office):
      `mem_limit: 3g`, `memswap_limit: 3g`, `cpus: 3.0`. Zonder die grenzen bewaakt de worker
      zelf het geheugen per job (Keuze 12).
    - Ook de previews van video's en `render_meta` gaan via de worker. Een test bewaakt dat buiten
      `app/media/worker/` niets ffmpeg of ffprobe start (`test_media_no_ffmpeg_outside.py`).
    - **Wat overblijft:** de jobs van één moment delen de worker (hooguit 3 live en 1 op de
      achtergrond). Ze draaien als dezelfde gebruiker. Een gekraakte ffmpeg kan via
      `/proc/<pid>/fd` een bestand lezen dat een andere job op dat moment open heeft, en dat in
      zijn eigen segmenten terugsturen. Het gaat om een film die iemand anders op dat moment kijkt:
      niet de database, niet de geheimen. Per job een eigen container of uid is zwaarder dan het
      waard is. Dit staat bij de risico's.
    - **Afgewezen: een worker over http op een intern netwerk** (ontwerp B). Fase 4 (M7) liet zien
      dat een container op een `internal: true`-netwerk de host bereikt via de gateway, en de app
      luistert intern op 8081. Ook een alleen-lezen koppeling van alle blobs valt weg: een lek
      leest dan elke film. Die koppeling blijft alleen de terugval als S3 op de LXC faalt (§3.7).
12. **Het protocol: getypte opdrachten, file descriptors over een Unix-socket.**
    - Eén verbinding per opdracht. De eerste boodschap draagt de fd's als `SCM_RIGHTS`.
    - Boodschappen: 4 bytes lengte (big endian), dan JSON, hooguit 64 KB.
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
        `-format_whitelist` met dezelfde lijst.
    - **De opdrachten:**

      | op | fd's | doet |
      |---|---|---|
      | `caps` | — | versie van ffmpeg, encoders, filters (`zscale`, `tonemap`, `bwdif`, `overlay`), hwaccels, `/dev/dri`, `PROTOCOL` |
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

    - De worker start ffmpeg met:
      - een eigen procesgroep en een kale omgeving (`PATH`, `LC_ALL=C`, `HOME=/tmp`);
      - grenzen via `resource` vóór `exec`: `RLIMIT_CORE` 0, `RLIMIT_NOFILE` 256, en
        `RLIMIT_FSIZE` 0 als de spike laat zien dat ffmpeg dat verdraagt (hij schrijft geen
        bestanden; S10). `RLIMIT_AS` kan niet: ffmpeg reserveert veel virtueel geheugen voor
        threads (N8);
      - `-threads 4` als bovengrens per proces [kosten niet gemeten; S10];
      - `nice -n 5` live, `nice -n 15` en `ionice -c 3` op de achtergrond. `ionice` helpt alleen
        met de BFQ-scheduler; op ZFS weinig [doc].
    - **Geheugen per job:** de worker leest elke 2 s `VmRSS` en stopt een job boven
      `job_memory_mb` (1.200). Gemeten: 140–568 MB per proces. De grens van de container is het
      vangnet.
    - Hooguit 6 verbindingen tegelijk (3 live, 1 achtergrond, 2 korte). Daarboven `{"ev": "error",
      "code": "busy"}`.
    - Uitvoer altijd via pipes die de app maakt (D7). De app leest ze met asyncio.
13. **Zonder sandbox, en als de worker weg is.**
    - `PLATFORM_MEDIA_WORKER` leeg: de app start zelf een lokale worker. Dat is een kindproces
      `python -I -m app.media.worker --socket <data_dir>/media/run/worker.sock`, met een kale
      omgeving, een eigen procesgroep en herstart met backoff. Hetzelfde protocol en dezelfde
      code; alleen de afscherming ontbreekt.
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
      - `media`: 1 worker (`PLATFORM_MEDIA_JOB_WORKERS`);
      - `import`: 1 worker, zodat een import van uren de rest niet ophoudt.
    - `claim()` pakt alleen soorten uit zijn eigen rij.
    - Een nieuwe uitzondering `jobs.Defer(seconds)`: terug in de rij met `run_after`, en de poging
      telt niet. Voor: de worker is druk of weg, een provider geeft 429, er is geen netwerk.
    - Soorten:

      | soort | rij | timeout | wanneer |
      |---|---|---|---|
      | `media.scan` | media | 300 s | na een wijziging, per 500 nodes (Keuze 5) |
      | `media.reconcile` | media | 1.800 s | bij de start en elke 6 uur |
      | `media.analyse` | media | 3.600 s | per nieuwe sha in een bibliotheek; stappen hieronder |
      | `media.lookup` | media | 300 s | als online opzoeken aan staat (Keuze 39) |
      | `media.art` | media | 120 s | een plaatje van een provider ophalen en omzetten |
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
      verzoek hooguit 5 s. Daarna 409 `media_probe_pending` met `Retry-After: 2`.
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
    - AV1: `av01.<profiel>.<level><tier>.<bitdiepte>` uit de `av1C`;
    - VP9: `vp09.<profiel>.<level>.<bitdiepte>` uit de `vpcC`, anders uit ffprobe;
    - Dolby Vision: `dvh1.<profiel>.<level>` uit de `dvcC`/`dvvC`;
    - audio: `mp4a.40.2`, `mp4a.40.5`, `opus`, `flac`, `ac-3`, `ec-3`, `mp3`.

    De module draait in de worker (de extradata zijn bytes van een gebruiker). Tests: een tabel van
    bekende extradata naar strings, plus de strings van alle fixtures.
17. **De keyframe-index.**
    - In `media_probe.keyframes`: tijden in ms als varint-delta's, met zlib. Een film van 2 uur met
      een keyframe per 2 s is ±4 KB. Plus `max_gop_ms`.
    - Eerst de eigen lezers in de worker (`worker/containers.py`): Matroska-Cues via de
      `SeekHead` (een kleine EBML-lezer, met een grens op diepte en grootte) en MP4
      `stss`/`stts`/`ctts`/`elst`. Een `moov` achteraan kost twee keer lezen, nooit het hele
      bestand. Hooguit 4 MB index.
    - De terugval `ffprobe -select_streams v:0 -show_entries packet=pts_time,flags` leest het hele
      bestand: minuten op een harde schijf. Alleen op de achtergrond.
    - `keyframes_state`: `none`, `ready`, `sparse` (Cues met gaten > 20 s: terugval nodig),
      `failed`.
    - Geen index bij het afspelen: transcode in plaats van remux (reden `keyframes_missing`).
      Dat kost CPU, maar is eerlijk en werkt.
18. **Gapless-gegevens.** Per nummer het precieze aantal geldige samples, en hoeveel er aan het
    begin en eind af moeten. Eigen lezers in de worker (`audio_info`), begrensd:
    - MP3: de LAME- of Lavc-header in het Xing/Info-frame (`enc_delay`, `enc_padding`), plus de
      vaste vertraging van de decoder (529 samples). mutagen leest de Lavc-tag niet (N20);
    - AAC in MP4: `iTunSMPB` (voorloop, opvulling, lengte); anders de `elst` en de duur;
    - Opus: `pre_skip` uit de `OpusHead`, en de laatste granulepositie (alleen de laatste
      Ogg-pagina);
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
      spoor met `default`, anders het eerste. `S` = de gekozen ondertitel, of geen.
    - **Regels, in volgorde:**
      1. **Geen video:** het muziekpad (Keuze 38).
      2. `video_ok` als alles klopt:
         - de codec-string van `V` is `file` (direct) of `mse` (remux) in het profiel, en
           `smooth` als `decodingInfo` het wist;
         - H.264 Hi10P nooit;
         - `V.hdr` is `none`, of de client kan precies die HDR en heeft een HDR-scherm;
           Dolby Vision profiel 5 alleen met `dv`;
         - niet interlaced;
         - de kwaliteit is `auto` of `source`, of de gekozen trede is ≥ de bron;
         - de bitrate past onder `max.bitrate_kbps` van het profiel (0 = geen grens).
      3. `audio_ok` als de codec van `A` in het profiel staat.
      4. `subs_ok` als `S` leeg of tekst is (`subrip`, `ass`, `ssa`, `webvtt`, `mov_text`). Een
         beeldondertitel (`hdmv_pgs_subtitle`, `dvd_subtitle`, `dvb_subtitle`) moet ingebrand
         worden, dus transcode.
      5. **Direct** als alles klopt:
         - container mp4/mov/m4v of webm, en `canPlayType` van mime plus codecs is niet leeg;
         - `video_ok`, `audio_ok` en `subs_ok`;
         - `A` is het eerste audiospoor, of het profiel heeft `track_switch` (alleen Safari heeft
           `audioTracks`).

         Een `moov` achteraan mag: met Range kost dat bijna niets (N15).
      6. **Remux** als alles klopt:
         - `V` speelt via MSE of MMS (of via native HLS);
         - de codec past in fMP4: H.264, HEVC (als `hvc1`), AV1 of VP9;
         - de keyframe-index is er (of snel te lezen: Cues of `stss`), en `max_gop_ms` ≤ 20.000;
         - `subs_ok`.

         Audio: kopiëren als `audio_ok`, anders omzetten (Keuze 23).
      7. **Transcode** als de policy het toestaat en de toelating een trede geeft (Keuze 26).
         Anders 503 `media_busy` (ingelogd) of 403 `media_public_transcode` (publieke link).
    - `Plan` = `{mode, reasons[], video: "copy" | {codec, rung, preset}, audio: "copy" | {codec,
      channels, bitrate}, burn, tonemap, cost}`.
    - `reasons` zijn vaste codes: `container`, `video_codec`, `audio_codec`, `audio_track`,
      `subtitle_burn`, `hdr`, `dolby_vision`, `interlaced`, `quality`, `bitrate`, `not_smooth`,
      `keyframes_missing`, `gop_too_long`, `budget`, `no_worker`.
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
    - `sid = <node_name>.<base64url(24 willekeurige bytes)>`. De naam van de node is voor later
      (Keuze 48).
    - Alle verzoeken van de speler gaan naar `/api/v1/stream/{sid}/…` zonder header: een `<video>`
      en native HLS kunnen geen Bearer sturen. Geen cookies, dus geen gedoe met SameSite en CSRF.
    - Direct play gaat óók via de sessie: `GET` en `HEAD /api/v1/stream/{sid}/file`, met Range,
      `If-Range` en de sha als ETag. Zo vervalt het downloadtoken van 10 minuten voor video (N4).
    - **Elk verzoek** gaat langs `sessions.guard`:
      1. de sid opzoeken (geheugen). Onbekend of verlopen: 410 `media_session_gone`. De speler
         vraagt dan stil een nieuwe sessie op zijn positie;
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
    - **Intrekken werkt meteen.** Waar nu `office.tunnel.close_user` wordt aangeroepen (een
      ingetrokken login, overal uitloggen, blokkeren), komt `media.sessions.close_user` erbij. Een
      ingetrokken deling werkt binnen 15 s plus wat de speler al gebufferd had (hooguit 30 s).
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
        -sc_threshold 0`;
      - elk fragment is één segment: segment k loopt van 2k tot 2k+2 s mediatijd. Het aantal is
        vooraf bekend: `N = ceil(D/2)`. Het laatste is korter;
      - `TARGETDURATION` is 2. `EXTINF` is 2,000; de echte grens valt op het eerste frame na 2k,
        dus hooguit één frame later. De tijden in `tfdt` zijn de waarheid voor de speler;
      - herstarten gebeurt alleen op een grens (2k). Dan geeft dezelfde uitdrukking hetzelfde
        rooster, want `t` telt vanaf het begin van de uitvoer (J4).
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
    - `map.json` voor de MSE-speler:
      `{"gen": 1, "mime": "video/mp4; codecs=\"avc1.64001f,mp4a.40.2\"", "offset": -1.0,
      "init": "init.mp4?g=1", "segments": {"grid_ms": 2000, "count": 3600}}`. Bij remux staat er een
      lijst `[[begin_ms, duur_ms], …]` uit de index.
    - `master.m3u8` en `v.m3u8` voor native HLS: de app schrijft ze zelf (§2 M4). Een test eist dat
      `map.json` en `v.m3u8` dezelfde segmenten beschrijven.
23. **Remux van echte keyframes.**
    - De regel van Jellyfin: segment n loopt van de eerste keyframe ≥ n·6 s tot de eerste keyframe
      ≥ (n+1)·6 s. Een lange GOP geeft een langer segment. `TARGETDURATION` is het langste, naar
      boven afgerond. De grenzen staan vooraf vast; de playlist klopt dus precies.
    - Remux haalt tientallen keren realtime (het eerste segment na 0,15 s, video kopiëren plus
      audio omzetten 21×, feasibility §3.2). Daarom mag het hier 6 s zijn.
    - ffmpeg kopieert; elke GOP is een fragment (D5). De app voegt fragmenten samen tot de grens
      van het volgende segment. Welk fragment bij welk segment hoort, beslist de pts van zijn
      eerste sample: `tfdt` plus de compositie-offset uit de `trun`. Niet `tfdt` alleen: met
      B-frames is dat de decodeertijd.
    - Een herstart op segment k: `-ss (b_k − 0,5)` vóór de invoer, want met kopiëren begint ffmpeg
      op de keyframe ervoor (N11). De app gooit fragmenten met een pts vóór `b_k` weg.
    - HEVC krijgt `-tag:v hvc1` (Apple wil `hvc1`, nooit `hev1` [doc]).
    - Audio: kopiëren als de client de codec kan en hij in mp4 past (AAC, Opus, FLAC; AC-3 en
      E-AC-3 alleen als het profiel ze kan). Anders AAC-LC stereo 160 kbit/s (`-c:a aac -ac 2
      -b:a 160k`), of Opus 128 kbit/s als AAC niet kan. MP3 in MP4 via MSE kan niet in Chromium
      (N13), dus MP3 wordt AAC. Dat kost 0,05 cores [gemeten].
    - mkv nooit direct, altijd remux: het kost bijna niets.
24. **Transcode: één trede per sessie.**

    | trede | grootte | maxrate / bufsize | H.264-level | string | cores voor 1,0× [gemeten] |
    |---|---|---|---|---|---|
    | 1080 veryfast | ≤ 1920×1080 | 6M / 12M | 4.0 (4.2 boven 30 fps) | `avc1.640028` | 1,42 (HDR 2,30) |
    | 1080 superfast | idem | idem | idem | idem | 0,95 |
    | 720 veryfast | ≤ 1280×720 | 3500k / 7000k | 3.1 (3.2 boven 30 fps) | `avc1.64001f` | 0,74 (HDR 1,21) |
    | 720 superfast | idem | idem | idem | idem | 0,62 |
    | 480 veryfast | ≤ 854×480 | 1500k / 3000k | 3.0 | `avc1.64001e` | 0,56 |

    - Nooit vergroten. Een trede boven de bron valt weg. Een bron onder 480 houdt zijn eigen hoogte
      (even).
    - `auto` is de kleinste van: de bron, de schermhoogte (afgerond op een trede) en 1080. Bij HDR
      is `auto` 720 (Keuze 27). Welke preset, beslist de toelating (Keuze 26).
    - Video: `-c:v libx264 -preset {veryfast|superfast} -crf 23 -maxrate R -bufsize 2R
      -profile:v high -level:v L -pix_fmt yuv420p`, met het rooster van Keuze 22. Schalen naar de
      echte beeldverhouding, met een even breedte.
    - Audio: kopiëren als de client de codec kan en hij in mp4 past, anders AAC-LC stereo
      160 kbit/s.
    - **Een client zonder H.264 of AAC** (zoals de Chromium van Playwright) krijgt VP9 met Opus:
      `-c:v libvpx-vp9 -deadline realtime -cpu-used 8 -row-mt 1` en `-c:a libopus -b:a 128k -ac 2
      -ar 48000`. Dat is voor tests en zeldzame clients. De kosten meet de spike (S11). Tot dan
      rekent de toelating met 2× x264. AV1 coderen bouwen we niet.
    - Interlaced: eerst `bwdif=mode=send_frame:parity=auto:deint=interlaced`.
    - Een beeldondertitel inbranden: `overlay` vóór het schalen (Keuze 29).
25. **Zoeken: herstarten op een segmentgrens.**
    - De speler vraagt segment n.
    - Staat het in de cache van de sessie: meteen.
    - Loopt de producent en ligt n hooguit 3 segmenten (6 s) voor op wat er is: wachten.
    - Anders: de producent stoppen (de verbinding dicht) en een nieuwe starten op de grens van n
      (`-ss 2n` bij transcode, `-ss (b_n − 0,5)` bij remux). Wat al in de cache staat, blijft
      geldig: hetzelfde rooster en, als de `stsd` gelijk is, dezelfde init.
    - Wachten duurt hooguit 20 s. Daarna 503 `media_timeout` met `Retry-After: 2`. Na 3 keer achter
      elkaar gaat de sessie dicht.
    - Het verschil in audio na een herstart (32–54 ms, D4) vangt de speler op door over het gat te
      springen (Keuze 32).
26. **Toelating en het CPU-budget.**
    - **De kostentabel:** de cores voor 1,0× per trede, SDR of HDR, uit Keuze 24.
      - Remux kost 0,02. Alleen audio omzetten 0,05.
      - Inbranden +0,15, deinterlacen +0,2, VP9 2× x264 [geschat; S11].
    - **Zelf meten.** De opdracht `bench` draait 10 s `testsrc2` met ruis, 1080p naar 720p
      veryfast. Dat geeft de factor `f` = (cores voor 1,0× hier) / 0,74 (de sandbox).
      - Alle kosten × `f`.
      - Bij de eerste start en met de knop "Opnieuw meten" op `/beheer`, alleen als er niets
        draait (anders 409 `media_bench_busy`).
      - Opgeslagen in `Setting "media"`. De spike meet de spreiding (S7).
    - **Het budget** `B` = de cpu-grens van de worker, en anders het aantal cores − 1. Op de LXC is
      dat 3,0: één core blijft over voor de app en OnlyOffice. `/beheer` kan het lager zetten.
    - **De regel bij `play`.** Een nieuwe transcode krijgt de eerste trede uit
      `[1080 veryfast, 1080 superfast, 720 veryfast, 720 superfast, 480 veryfast]`, begrensd door
      de bron, het scherm, de keuze van de gebruiker en 720 bij een publieke link, waarvoor geldt:

      `in gebruik + kosten(trede) + reserve ≤ 0,9 × B`, met
      `reserve = (max_transcodes − lopend − 1) × kosten(480 veryfast)`.

      Zo kan de laatste toegestane kijker altijd nog starten (het idee van ontwerp B). Met de
      cijfers van de proef, `B` = 3,0 en `max_transcodes` = 3:
      - kijker 1: 1080 veryfast (1,42 + 2 × 0,56 = 2,54 ≤ 2,7);
      - kijker 2: 1080 veryfast past niet, 720 veryfast net niet (2,72); 720 superfast (2,60);
      - kijker 3: 720 superfast (2,66).

      Met `max_transcodes` = 2 (het andere antwoord op Vraag 3): 1080 veryfast en 1080 superfast
      (2,37).
    - Een gekozen trede die niet past: een lagere, met de reden `budget`. De speler zegt dan
      "lagere kwaliteit: de server is druk". Past ook 480 niet: 503 `media_busy` met
      `reason: cpu` en `retry_after`.
    - Grenzen: `max_transcodes` 3 (`PLATFORM_MEDIA_MAX_TRANSCODES` is het plafond; `/beheer` kan
      alleen lager), 2 per gebruiker (`reason: user`), 1 per publieke link. Minder dan 10 GB vrij:
      `reason: disk` (Keuze 31).
    - **Afremmen door tegendruk** (D6): de app leest de pipe niet verder als de producent 60 s
      (30 segmenten) voor ligt op het laatste verzoek van de speler. ffmpeg blokkeert dan en kost
      niets.
    - **Pauze:** 120 s geen verzoek. Dan gaat de producent dicht (dat maakt 250–570 MB vrij) en
      blijft de sessie. Verder spelen start hem op het volgende segment.
    - **Vast:** 30 s geen nieuw fragment terwijl de app leest. Dan stopt de app de producent en
      start hem één keer opnieuw op hetzelfde segment (Keuze 46).
27. **HDR naar SDR, en wat als het niet kan.**
    - De worker kijkt bij het starten (`caps`) of `zscale` en `tonemap` er zijn. Debian bouwt
      ffmpeg met libzimg [doc, pakketgegevens]; de spike controleert het in het image (S1).
    - De keten, op de doelgrootte, met de bron expliciet:
      `zscale=w=W:h=H:tin=smpte2084:min=bt2020nc:pin=bt2020:rin=tv:t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p`,
      plus `-color_primaries bt709 -color_trc bt709 -colorspace bt709`. Bij HLG
      `tin=arib-std-b67`. Dolby Vision profiel 7 en 8 gebruiken hun basislaag.
    - `auto` bij HDR is 720 (1,21 cores). 1080 alleen als iemand hem kiest en het budget het
      toelaat (2,30 cores). 4K HDR live nooit (3,2 cores voor 1,0×, N17).
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
      allemaal in één run uit, in hun eigen vorm: ASS en SSA met `-c:s copy`, de rest als SRT
      (`-c:s srt`). Altijd met `-copyts`. Dan maakt een **eigen omzetter** er WebVTT van
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
      `e<index>` voor een ingebed spoor en `x<node_id>` voor een los bestand.
    - De speler maakt er `<track kind="subtitles" srclang label>` van. Dat werkt met MSE, en hoort te
      werken met native HLS [niet getest in Safari].
    - **Geforceerd:** heeft de gebruiker geen ondertitel gekozen, en is er een geforceerd spoor in de
      taal van het audiospoor, dan staat dat aan.
    - **Beeldondertitels** (PGS, VobSub, DVB) kunnen niet naar tekst zonder OCR. Ze worden alleen
      ingebrand: transcode met `[0:s:N]scale=W:H[s];[v][s]overlay=eof_action=pass`. De speler zegt
      dat bij de keuze ("wordt ingebrand, kost omzetten"). Dit schuift als eerste als het uitloopt
      (§5).
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
      - Ze komen via een ondertekend adres, net als previews:
        `GET /api/v1/media/trick/{sha}/{i}.jpg?exp=…&sig=…` (12 uur geldig).
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
    - Elke 5 minuten (housekeeping `media.sweep`):
      - sessies 30 minuten stil: dicht;
      - boven `cache_gb`: eerst de oudste stille sessies dicht, dan segmenten die meer dan 15
        minuten achter de speler liggen;
      - minder dan 10 GB vrij op `data_dir`: geen nieuwe transcode (503 `media_busy` met
        `reason: disk`) en de melding `media_cache_full`.
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
      - een nieuwe init als `X-Init` verandert;
      - **springt over gaten** tot 0,5 s in `buffered` (een kleine "gap jumper": `currentTime` loopt
        1 s niet terwijl er verderop gebufferd is);
      - **na 2 haperingen in 60 s** bij transcode: één trede lager (een nieuwe sessie met
        `replace`). Geen automatische ABR.
    - **Foutgedrag per code:**
      - `410 media_session_gone`: stil een nieuwe sessie op dezelfde plek, hooguit 3 keer per
        minuut;
      - `403 media_access_gone`: stoppen, en zeggen waarom;
      - `503 media_busy` of `media_timeout`: na `Retry-After` opnieuw, met een melding;
      - een netwerkfout: 3 keer opnieuw, na 1, 2 en 4 s.
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
      | Shift+N | volgende aflevering |
      | I | info |
      | ? | de lijst van toetsen |
      | Esc | volledig scherm uit, de kaart annuleren, of terug |

      - De toetsen werken niet als de focus in een menu of invoerveld staat.
      - In de viewer van fase 3 blijven ← en → naar het vorige en volgende bestand gaan. Daar
        zoeken J en L.
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
    - **Sprites en art** gaan via ondertekende adressen, zoals previews. Die zijn gebonden aan de
      inhoud (sha), niet aan een gebruiker. Wie een sha kent, kent de inhoud al. Dit is het model
      van fase 3.
35. **Geen lekken.**
    - De sid komt nooit in een log: `_hide` in `main.py` krijgt het patroon
      `(/api/v1/stream/[^/.\s"]+\.)[^/?\s"]+` → `\1***`. Een test speelt een sessie af en zoekt de
      sid in alle logregels (`test_media_logs.py`).
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
      `{position_ms, duration_ms, event: "tick"|"pause"|"seek"|"stop"|"end", origin, audio?,
      subtitle?}`. `origin` is een willekeurig id per tabblad of apparaat.
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
        +frag_keyframe+empty_moov+default_base_moof -frag_duration 10000000 -use_editlist 0`;
      - in de cache `audio/<sha>.mp4`, en geserveerd terwijl het groeit. Remuxen kost minder dan
        1 s per nummer;
      - ALAC en WAV worden FLAC in fMP4 (verliesvrij);
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
        `mode = "sequence"` [doc: MSE byte stream formats]; S8 legt de precieze `delay` vast;
      - het volgende nummer alvast laden als er 20 s over is. Alleen het huidige en het volgende
        staan in de buffer.
    - Een ander nummer kiezen of ver zoeken: een nieuwe MediaSource.
    - Terugval zonder MSE of MMS, of als MSE de codec niet kent: `<audio>` per nummer, met het
      volgende alvast geladen. Dan is er een gat van tientallen ms, en de speler zegt het.
    - **Eerlijk:**
      - precies voor FLAC en Opus, en voor AAC met `iTunSMPB`;
      - AAC uit ffmpeg zonder `iTunSMPB` houdt een gat van 15,6 ms (688 samples), want het aantal
        echte samples staat nergens [gemeten];
      - MP3 via MSE met de LAME-header: [niet getest] (S8). De test is de waarheid, niet deze tekst.

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
      - na het opzoeken van de naam nooit een privé-, loopback- of link-local-adres (een eigen
        controle met `ipaddress`). Alleen de tests zetten `PLATFORM_MEDIA_PROVIDER_PRIVATE_OK`;
      - 429 en 503: `Retry-After` volgen, met een plafond van 60 s;
      - een stroomonderbreker per provider: na 5 fouten op rij 5 minuten dicht, dan één proef;
      - de log toont alleen host, pad zonder query, status en tijd. Nooit een sleutel.
    - Alle verzoeken naar buiten draaien als jobs in de rij `media` (`media.lookup`, `media.art`),
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
      - de worker: modus (`container` of `lokaal`), staat, sinds wanneer;
      - ffmpeg: versie, encoders, filters, wat ontbreekt, hwaccel, GPU;
      - de benchmark: factor en datum;
      - het budget: gebruikt en totaal;
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
    - Het Dockerfile krijgt één regel: `RUN install -d -o 10001 -g 10001 -m 0700 /run/media`.
    - `install.sh` volgt het patroon van office (§2 M10):
      - `MEDIA_SANDBOX` (`aan` of `uit`) uit de omgeving wint van `.env`;
      - `decide_media` breekt nooit af;
      - `compose_files` neemt de media-bestanden mee;
      - de mappen: `install -d -m 750 -o 10001 -g 10001 "$DATA_ROOT"/{media,import}`;
      - eerst de app (`up -d --build --remove-orphans platform` en `wait_health`), dan `media`
        (`up -d --no-deps media`, hetzelfde image met `pull_policy: never`), dan office. Zo zit
        media nooit in de weg van de app.
    - Na de start: `app.cli media-status --wait 20`. Dat print bijvoorbeeld "Media: aan
      (afgeschermd, ffmpeg 7.1.5, GPU: geen)", of een waarschuwing. Het stopt het script nooit.
    - Vóór het vervangen: `app.cli media-status --sessions` waarschuwt als er iemand kijkt. Die
      streams haperen 10–30 s en lopen dan zelf door (Keuze 21).
    - **Terug naar 0.6.0:**
      1. `app.cli db-downgrade 0007` met de nieuwe versie;
      2. het script van 0.6.0. Dat kent geen media, dus `--remove-orphans` haalt de container weg.

      Het volume `media-run` en de mappen `media/` en `import/` blijven staan en doen geen kwaad.
      Getest in `test_migration_0008.py` en de installatietest (§2 M10).
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

### Vragen voor Jonas

1. **Staan er al films, series of muziek op `/tank`, en waar?** Standaard: **we bouwen de
   import, en hij kopieert** (Keuze 2). De bron blijft dan staan. Daarvoor is tijdelijk twee keer
   de ruimte nodig. Staat de bron onder `/tank/platform/import` (hetzelfde dataset), dan kan ook
   "verplaatsen": geen extra ruimte. Staan de bestanden op een ander dataset en moeten ze daar
   blijven, zonder kopie? Dan is dat de map rechtstreeks van fase 7 (Keuze 3), en schuift dat deel
   naar voren.
2. **Welke CPU en is er een GPU (B5)?** Standaard: **alleen de CPU**. Het platform meet zichzelf
   (`bench`). Graag de uitvoer van `lscpu` en `ls -l /dev/dri` op de Proxmox-host. Een iGPU kan
   later (±2–3 dagen). Dan moet jij `/dev/dri` aan de LXC geven. Dat is nooit een eis.
3. **"3 gelijktijdige transcodes": drie op gemengde kwaliteit, of hooguit twee op 1080p?**
   Standaard: **drie**. De eerste kijker krijgt 1080p, de tweede en derde 720p superfast, zodat de
   derde altijd nog past (Keuze 26). Direct play en remux tellen niet mee, en dat is de normale
   weg. HDR telt zwaarder: één HDR-film op 1080p is ongeveer twee gewone. Het andere antwoord:
   hooguit twee, allebei 1080p, en de derde krijgt "probeer het straks". De belastingtest op de LXC
   (§4.6) laat zien wat jouw CPU echt haalt.
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
| **M0** | Fundament en spike (§3) | L | **eerst, alleen.** Werkers: M0-api, M0-web, daarna M0-e2e. De spike draait op dag 1–2 als CI-job in het echte image |
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
| `app/models.py` (de tabellen van §3.1), `migrations/versions/0008_media.py`, `app/config.py`, `app/serve.py` (F3), `app/main.py` (routers, lifespan, CSP, `_hide`), `app/jobs.py` (rijen, `timeout`, `Defer`), `app/cli.py` (de commando's als dunne aanroep), `app/routers/files.py` (F1, F2, de haak in `notify`), `app/routers/public.py` (F1, F2), `app/routers/auth.py` (de haak `close_user`), `app/previews.py` (alleen de haak `on_discard`), `app/usage.py` en `app/routers/admin.py` (alleen de haken), `app/media/{__init__,wire,client,state,runtime,types,errors,hooks,analysis}.py`, `app/media/worker/{__main__,server,run,args}.py`, `app/media/worker/ops/{caps,probe}.py`, `app/media/providers/base.py`, de stubs (§3.3), `tests/media/{fixtures,sup,fake_worker,worker_contract,record,fake_stream,media_contract,hls_lint}.py`, `tests/media/recorded/`, `tests/media/fake_providers/` (de kern), tests `test_migration_0008.py`, `test_media_wire.py`, `test_media_worker_core.py`, `test_media_probe.py`, `test_fake_worker.py`, `test_download_pool.py`, `test_download_head.py`, `test_jobs_lanes.py`, `test_media_routes_guarded.py`, `test_media_logs.py` | M0 (frozen) |
| `app/media/worker/ops/{frame,meta,bench}.py`, `app/media/worker/{guard,selftest}.py`, `app/media/local.py`, `app/media/health.py`, `app/render.py` (de tak video), `app/render_meta.py` (de tak video), `app/previews.py` (alleen de tak video), tests `test_media_worker_guard.py`, `test_media_no_ffmpeg_outside.py`, `test_previews_video_worker.py`, `test_media_local.py`, `test_media_health.py` | M1 |
| `app/media/{names,classify,scanner,library,items,importer}.py`, `app/media/worker/ops/nfo.py`, `app/routers/media_library.py`, `tests/media/{names,names-open}.tsv`, tests `test_media_names.py`, `test_media_classify.py`, `test_media_scanner.py`, `test_media_library_api.py`, `test_media_nfo.py`, `test_media_import.py` | M2 |
| `app/media/{decide,profile,codecs,sessions,public}.py`, `app/media/profiles/*.json`, `app/routers/{media_play,stream_file,public_media}.py`, tests `test_media_decide.py`, `test_media_profile.py`, `test_media_codecs.py`, `test_media_sessions.py`, `test_media_public.py`, `test_media_direct.py` | M3 |
| `app/media/{ladder,admission,producer,fmp4,grid,hls,cache,streams}.py`, `app/media/worker/ops/{stream,keyframes}.py`, `app/media/worker/containers.py`, `app/routers/stream.py`, tests `test_media_ladder.py`, `test_media_admission.py`, `test_media_producer.py`, `test_media_fmp4.py`, `test_media_grid.py`, `test_media_restart.py`, `test_media_hls.py`, `test_media_cache.py`, `test_media_containers.py`, `test_media_contract.py` | M4 |
| `app/media/{subconv,subtitles,trick,chapters}.py`, `app/media/worker/ops/{subs,sprites}.py`, `app/routers/media_subs.py`, tests `test_media_subconv.py`, `test_media_subtitles.py`, `test_media_trick.py`, `test_media_chapters.py` | M5 |
| `app/media/progress.py`, `app/routers/media_progress.py`, tests `test_media_progress.py`, `test_media_progress_api.py` | M7 |
| `app/media/providers/{http,tmdb,musicbrainz,caa,match,secrets,art}.py`, `app/media/lookup.py`, `app/routers/media_match.py`, `tests/media/fake_providers/**` behalve de kern, tests `test_media_http.py`, `test_media_providers_contract.py`, `test_media_match.py`, `test_media_secrets.py`, `test_media_art.py`, `test_media_lookup.py` | M8 |
| `app/media/{music,playlists}.py`, `app/media/worker/ops/{audio_info,remux_audio}.py`, `app/routers/{media_music,stream_audio}.py`, tests `test_media_music.py`, `test_media_audio_info.py`, `test_media_playlists.py`, `test_media_stream_audio.py` | M9 |
| `app/media/{admin,monitor,status,loadtest}.py`, `app/routers/admin_media.py`, `app/usage.py` (het deel `media`), tests `test_admin_media.py`, `test_media_monitor.py`, `test_media_status.py`, `test_media_loadtest.py` | M10 |

M0 zet elke module van een ander pakket als stub met de signaturen van §3.3, zodat alles compileert
en de app zonder media precies 0.6.0 is, plus de reparaties F1–F4.

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
| `e2e/media_harness.py`, `e2e/conftest.py` (fixtures `media`, `fake_providers`, `ffmpeg_count`), `e2e/pytest.ini` (markers `media`, `media_perf`, `provider_real`), `docs/fase-5/{spike,e2e}.md`, `.github/workflows/ci.yml` (de jobs `media-image` en `media-spike`, en de markers in `e2e`) | M0 (frozen) |
| `Dockerfile` (de regel `/run/media`), `infra/single/compose.media.yaml`, `infra/single/compose.media-limits.yaml`, `infra/single/tests/test_media_container.sh` | M1 |
| `docs/fase-5/clientprofiel.md` | M3 |
| `docs/fase-5/provider-shapes.json` | M8 |
| `infra/single/install.sh`, `infra/single/compose.yaml` (alleen `PLATFORM_MAX_UPLOAD_BYTES`), `infra/single/.env.example`, `infra/single/README.md`, `infra/single/tests/test_install_media.sh`, `docs/runbooks/media.md`, `.github/workflows/ci.yml` (alleen de job `infra`) | M10 |
| `e2e/test_media_<pkg>*.py`, `docs/fase-5/<pkg>.md`, `docs/fase-5/toetsenbord-<pkg>.md` | elk pakket zijn eigen |

**Regels** (dezelfde als in fase 4, plus):

1. Alleen eigen bestanden wijzigen; de rest alleen importeren.
2. Geen migraties en geen `models.py` buiten M0. Een schemawens gaat naar de hotfix-lane.
3. **Geen nieuwe pakketten** in `pyproject.toml` of `package.json`, ook geen dev-deps.
4. **Geen ffmpeg of ffprobe buiten `app/media/worker/`.** `test_media_no_ffmpeg_outside.py`
   controleert het (M1). Vanaf M1 geldt het ook voor `render.py`.
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
13. Nooit meer dan 3 ffmpeg tegelijk in een test of meting, en stoppen alleen op pid. Een fixture
    telt na elke e2e-test de ffmpeg-processen: 0.

**Afhankelijkheden** (alleen via de contracten en stubs van M0):

| Wie | Gebruikt | Gemaakt door |
|---|---|---|
| iedereen | `client.call`, `wire`, `state.MediaState`, `types.*`, `analysis.step`, `hooks` | M0 |
| M3, M4, M5, M9 | de lokale worker in tests | M0 (de kern); M1 (bewaking en supervisie) |
| M3 | `streams.open(session, plan)`, `streams.close(session)` | M4 (M0-stub geeft 503 `media_off`) |
| M3 | `codecs.strings(...)` in de probe | M3; tot dan geeft de M0-stub alleen wat ffprobe weet |
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
  - de bewaking per job: `VmRSS` elke 2 s, stoppen boven `job_memory_mb`;
  - `nice`, `ionice`, de `RLIMIT`'s van Keuze 12, de procesgroep, de kale omgeving;
  - hooguit 6 verbindingen.
- `app/media/worker/ops/frame.py`, `meta.py` en `bench.py` (Keuze 12, 26).
- `app/media/worker/selftest.py`: `python -I -m app.media.worker --selftest`. Dat controleert:
  - versie 7.1.x;
  - de encoders `libx264`, `aac`, `libopus`, `libvpx-vp9`, `mjpeg`;
  - de filters `zscale`, `tonemap`, `bwdif`, `overlay`, `scale`, `fps`, `tile`;
  - de protocollen `fd` en `pipe`;
  - `nice` en `ionice` in het image;
  - een transcode van 4 s `testsrc2` naar fMP4 op een pipe: twee fragmenten, `tfdt` op 1,000 en
    3,000 (`+frag_discont`, J4).

  Exit 1 bij een fout. CI gebruikt het als harde poort.
- `app/media/local.py`: de lokale worker als kindproces (Keuze 13): starten, herstarten met backoff
  (1, 2, 4 … 60 s), stoppen met de app.
- `app/media/health.py`: elke 30 s verbinden en `caps`. Dat schrijft `MediaState.worker` (`ok`,
  `down`, `ffmpeg_missing`) en zet de caps in de cache.
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

    media:
      image: platform:local          # hetzelfde image als de app, één keer gebouwd
      pull_policy: never
      restart: unless-stopped
      command: ["python", "-I", "-m", "app.media.worker", "--socket", "/run/media/worker.sock"]
      network_mode: none
      read_only: true
      tmpfs:
        - /tmp:size=64m,mode=1777
        - /data:size=1m,mode=0700,uid=10001,gid=10001   # overschaduwt VOLUME /data: geen anoniem volume
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
  - `/run/media` is van 10001 met modus 0700;
  - een probe en een frame van een fixture, met de fd vanuit de platform-container;
  - schrijven op een doorgegeven fd faalt;
  - de worker gestopt: de app meldt `down` binnen 30 s.

**Wijzigt**
- `render.py`: de tak video vraagt `frame` aan de worker. De JPEG die terugkomt, gaat als gewone
  afbeelding door de bestaande renderer. Het eigen ffmpeg-deel gaat weg.
- `render_meta.py`: de tak video vraagt `meta`.
- `previews.py`: is de worker weg, dan zet een preview van een video zichzelf opnieuw klaar
  (`jobs.Defer(300)`), zonder een poging te tellen.
- `Dockerfile`: `RUN install -d -o 10001 -g 10001 -m 0700 /run/media`.

**Tests**
- `test_media_worker_guard.py`:
  - een job boven de geheugengrens wordt gestopt;
  - de verbinding dicht: ffmpeg is binnen 3 s weg (ook kleinkinderen, `killpg`);
  - de zevende verbinding krijgt `busy`;
  - de nice-waarde en de `RLIMIT`'s kloppen (`/proc/<pid>/stat`, `/proc/<pid>/limits`);
  - de omgeving van ffmpeg is kaal.
- `test_media_no_ffmpeg_outside.py`: de AST van alle modules buiten `app/media/worker/` bevat geen
  aanroep met `ffmpeg` of `ffprobe`.
- `test_previews_video_worker.py`: een preview van een video via de worker. Met de worker weg: een
  nieuwe taak, `attempts` blijft gelijk.
- `test_media_local.py`: de lokale worker herstart na `kill -9` (op pid); de app stopt hem bij het
  afsluiten.
- `test_media_health.py`: `ok`, `down` en `ffmpeg_missing` (een worker met een lege `PATH`).

**Klaar als**
- In CI draait de worker met precies deze afscherming en maakt hij een preview en een probe.
- Tijdens afspelen staat er geen ffmpeg in de proceslijst van de app-container (e2e met
  `docker top`).
- Met de worker weg werkt direct play, en toont `/beheer` de melding.

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
  - `media.reconcile` bij de start en elke 6 uur.
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
- `test_media_names.py`: de testset 100 %; de fuzz (10.000 namen, vaste seed, < 0,5 s).
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
  overgeslagen; `../` buiten `/data/import` geweigerd; nog een keer draaien doet niets.
- e2e `test_media_m2.py`: een map met fixtures uploaden en er een bibliotheek van maken; de items
  verschijnen (tik 0,5 s in de test), met de goede titels; een import via `/beheer`; alles met het
  toetsenbord.

**Klaar als**
- De testset is 100 %.
- Een item verschijnt ≤ 35 s na de upload (met de tik van 30 s).
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
- `sessions.py`: aanmaken, `guard` (Keuze 21), grenzen, `replace`, `close_user`, `close_node`,
  `list()`, en `create_audio` voor M9.
- `public.py`: de policy voor links (Keuze 34).
- `routers/media_play.py`, `routers/stream_file.py`, `routers/public_media.py`:
  ```
  GET  /api/v1/media/{node_id}/codecs        → {candidates: [string], item: [string]}
  POST /api/v1/media/{node_id}/play
       {profile, audio?, subtitle?, quality?: "auto"|"source"|1080|720|480, start_ms?, hdr?: "raw", replace?: sid}
       → 200 {sid, mode, reasons[], urls: {file|map|master}, mime, duration_ms, start_ms, offset,
              tracks: {audio: [{index, lang, title, codec, channels, default}],
                       subtitles: [{id, lang, label, forced, sdh, default, state}]},
              chapters[], credits_ms?, trick?, quality: {current, options[]}, expires_at}
       → 404 not_found | 409 media_probe_pending | 409 media_unavailable | 415 media_unsupported
         | 422 media_hdr | 503 media_busy {reason: cpu|disk|user|link} | 503 media_worker_down
         | 503 media_ffmpeg_missing | 503 media_off
  GET|HEAD /api/v1/stream/{sid}/file         direct play: Range, If-Range, ETag (de route van send_file)
  DELETE   /api/v1/stream/{sid}              → 204
  POST /api/v1/public/media/{node_id}/play   sleutel van de link; zelfde body
                                             → 403 media_public_transcode | 429 rate_limited
  ```
  - `/file` houdt geen databaseverbinding vast (regel 8): de sessie staat in het geheugen.

**Tests**
- `test_media_codecs.py`: een tabel van bekende `avcC`, `hvcC`, `av1C`, `vpcC` en `dvcC` naar strings,
  de strings van alle fixtures, en fuzz.
- `test_media_profile.py`: de vorm, de grens van 64 en 8 KB, onbekende velden weg, een basisprofiel
  plus eigen metingen.
- `test_media_decide.py`: een tabel van ≥ 60 gevallen. De probes van alle fixtures, maal de
  profielen `chromium141` (gemeten), `chrome_ci`, `safari17`, `webos5` en `firefox` (aangenomen, uit
  [doc]), maal de keuzes (ander audiospoor, PGS, kwaliteit, HDR raw, publiek). Elk geval met de
  verwachte `mode` en `reasons`.
- `test_media_sessions.py`:
  - een onbekende sid op elke route: 410;
  - een sessie die de beheerder stopte: 403 met `reason: stopped`, ook na het sluiten (de
    grafsteen); na 30 minuten 410;
  - een gebruiker geblokkeerd, een family ingetrokken, het bestand in de prullenbak, een deling
    ingetrokken: 403 `media_access_gone` (met de nepklok binnen 15 s; `close_user` meteen);
  - 8 per gebruiker; 12 uur; 30 minuten stil; `replace`.
- `test_media_public.py`: direct en remux mogen; transcode 403; 2 per link; een verlopen link en een
  ander wachtwoord geven 403; een bestand buiten de link 404.
- `test_media_direct.py`: Range, `If-Range`, `HEAD`, en 20 gepauzeerde streams houden de pool vrij.

**Klaar als**
- Direct play van een 1080p-bestand werkt via de sessie, met zoeken, ook na 10 minuten.
- Elke route onder `/stream/` controleert de toegang. Intrekken werkt (meteen bij login, ≤ 15 s bij
  een deling).
- In geen enkel log staat een sid (`test_media_logs.py`).

---

### M4 — Stromen: producent, knipper, playlists, toelating en cache

**Doel.** Van een plan tot het laatste segment: remux en transcode, zoeken, de toelating, de
tegendruk en de cache.

**Maakt**
- `ladder.py`: de tredes, de kostentabel en de naad `Encoder` (Keuze 24, 26, 28).
- `admission.py`: de regel met de reserve, de factor `f`, per gebruiker en per link, en
  `snapshot()` voor `/beheer`.
- `streams.py`: `open(session, plan)` en `close(session)` voor M3; de toegang tot segmenten.
- `producer.py`: één producent per sessie.
  - Start via `client.call(STREAM, …)` en leest de pipe (`connect_read_pipe`).
  - Knipt met `fmp4.py`: boxgroottes begrensd (init ≤ 1 MB, `moof` ≤ 4 MB, `mdat` ≤ 64 MB).
  - Groepeert fragmenten tot segmenten volgens `grid.py`, op de pts van het eerste sample.
  - Schrijft `<n>.m4s.tmp` en hernoemt atomair. Wekt wachtende verzoeken.
  - Leest niet verder bij 60 s voorsprong (tegendruk).
  - Vergelijkt de `stsd` van elke nieuwe init (Keuze 22).
  - Herstart bij een sprong (Keuze 25), en één keer na een fout of "vast" (Keuze 46).
- `grid.py`: het rooster van 2 s en het rooster uit keyframes (Keuze 22, 23).
- `hls.py`: `map.json`, `master.m3u8`, `v.m3u8`.
- `cache.py`: de mappen, de housekeeping `media.sweep` (elke 5 minuten), en leegmaken bij de start.
- `worker/ops/stream.py` en `worker/ops/keyframes.py`, en `worker/containers.py` (de lezers voor
  Cues en `stss`, begrensd). M4 registreert de stap `keyframes` bij `analysis`.
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
de rest wel, want elk segment begint met een IDR. Bij remux nooit: de bron kan open GOP's hebben.
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
#EXTINF:2.000,
0.m4s
#EXTINF:2.000,
1.m4s
…
#EXTINF:1.512,
3599.m4s
#EXT-X-ENDLIST
```
- `BANDWIDTH` = (maxrate plus audio) × 1,1. `AVERAGE-BANDWIDTH` = een schatting uit de trede.
- Bij remux: de echte piek uit de index (de grootste som van samplegroottes in een venster van
  6 s), en `EXTINF` met 3 decimalen.
- `CODECS` bevat altijd video én audio. `FRAME-RATE` met drie decimalen. `VIDEO-RANGE` is `SDR`, `PQ`
  of `HLG`. Alleen de trede van de sessie staat erin: een native speler kiest nooit zelf een trede
  waar geen budget voor is.

**Tests**
- `test_media_grid.py`: het rooster van 2 s; `N` voor duren rond de grenzen; de remuxregel met lange
  GOP's, de laatste keyframe, en B-frames (pts tegen `tfdt`).
- `test_media_fmp4.py`: boxen in stukken van 1 byte, te groot, afgekapt, `size` 0 en 1, `tfdt`
  versie 0 en 1, de `stsd`-vergelijking, en fuzz.
- `test_media_restart.py` (echte ffmpeg, hooguit 1 tegelijk): een run vanaf 0 en een herstart op
  segment 5 geven dezelfde videotijden per segment; `tfdt` begint op mediatijd + 1 (J4); het
  verschil in audio is ≤ 100 ms.
- `test_media_producer.py` (nep-worker):
  - tegendruk bij 60 s voorsprong (de nep meldt dat schrijven blokkeert);
  - herstart bij een sprong;
  - één nieuwe poging na een crash, daarna 502;
  - "vast" na 30 s zonder fragment;
  - geen segment in 20 s: 503;
  - idle na 120 s: de producent dicht, de sessie blijft;
  - een andere `stsd`: generatie 2 en de kop `X-Init`.
- `test_media_admission.py`: de reserve (1080 + 720sf + 720sf met de proefcijfers); max 2; een trede
  lager; 2 per gebruiker; 1 per link; het plafond uit env; de factor `f`; schijf vol.
- `test_media_hls.py`: golden files voor de playlists, de lint van §3.6, en `map.json` gelijk aan
  `v.m3u8`.
- `test_media_containers.py`: Cues en `stss` op alle fixtures tegen ffprobe; fuzz.
- `test_media_contract.py`: dezelfde scenario's als `media_contract.py` van M0, nu tegen de echte
  routes.

**Klaar als**
- Met de virtuele speler van §4.6 (zonder browser, marker `media_perf`) in de sandbox:
  - van `play` tot segment 0 bij transcode 1080 naar 1080: ≤ 2,5 s;
  - na een sprong naar 70 %: ≤ 2 s;
  - remux: ≤ 0,5 s.
- De belastingtest (§4.6) met 3 sessies: 0 keer haperen.

---

### M5 — Ondertitels, sprites en hoofdstukken

**Doel.** Ondertitels in elke taal en elke vorm, sprites op de zoekbalk, en hoofdstukken met de
aftiteling.

**Maakt**
- `subconv.py`: de omzetter van SRT en ASS naar VTT (Keuze 29). Pure functies; draait in de worker.
- `subtitles.py`: externe sporen koppelen, de controle van elke VTT, de staat per spoor, en de stap
  `subs` (achtergrond) en het uitpakken op verzoek.
- `trick.py`: sprites opslaan, `trick.vtt`, de ondertekende adressen, en de stap `sprites`.
- `chapters.py`: hoofdstukken uit de probe en `credits_ms(probe)`.
- `worker/ops/subs.py` en `worker/ops/sprites.py`.
- `routers/media_subs.py`:
  ```
  GET  /api/v1/stream/{sid}/subs/{id}.vtt     text/vtt; 202 + Retry-After: 5 zolang hij gemaakt wordt
  GET  /api/v1/media/trick/{sha}/trick.vtt?exp=&sig=
  GET  /api/v1/media/trick/{sha}/{i}.jpg?exp=&sig=
  ```

**Tests**
- `test_media_subconv.py`: `\an8` wordt `line:0`; `<i>`, `<b>`, `<u>`; kleur als klasse; tekeningen
  weg; BOM, UTF-8 en CP1252 met "é"; overlappende cues; een tabel van golden files; fuzz.
- `test_media_subtitles.py`: tijden min `start_time`; de grenzen (5 MB, 20.000 cues, 2.000 tekens);
  geforceerd; een los bestand bij de goede video; 202 zolang hij gemaakt wordt.
- `test_media_trick.py`: aantal tegels, `trick.vtt` klopt met de tegels, het adres verloopt.
- `test_media_chapters.py`: titels, de aftiteling in het Nederlands en Engels, geen hoofdstukken.
- e2e `test_media_m5.py`: SRT en de ASS-cue met `\an8` (boven in beeld) als `<track>`; PGS inbranden
  (de pixels op de plek van de ondertitel veranderen, gemeten in een canvas); sprites bij hover.

**Klaar als.** Elke tekstondertitel van de fixtures staat goed in beeld, op de goede tijd (±1 frame).
Het inbranden van PGS is bewezen, of geschoven met de reden (§5).

---

### M6 — De speler (web)

**Doel.** Een eigen speler die direct, MSE en native HLS kan, met kwaliteit, audiospoor,
ondertitels, PiP en toetsenbord. Binnen het budget, zonder bibliotheek (B19).

**Maakt.** `web/src/player/**` (Keuze 32, 33), het gebied `player` (nl en en), en de checklist
`docs/fase-5/toetsenbord-m6.md`.

**Wijzigt.** Niets buiten de eigen bestanden. M0 zette de haak in `MediaView.tsx`.

**Tests**
- vitest:
  - `keys.test.ts`: elke toets, en niets als de focus in een veld staat;
  - `mse.test.ts` (pure delen): het segment voor tijd t, wat er weg mag, `gen` wisselen, de
    beslissing over een gat;
  - `session.test.ts`: vernieuwen bij 410 op de juiste positie, stoppen bij 403, geen lus (hooguit
    3 keer per minuut);
  - `caps.test.ts`: de vorm van het profiel en de grens van 64;
  - `tracks.test.ts`: de regel voor geforceerd, en labels via `Intl.DisplayNames`.
- e2e `test_media_m6.py` (Chromium 141 hier, Chrome for Testing 156 in CI):
  1. direct: `vp9_opus.webm`. Het eerste beeld via `requestVideoFrameCallback`;
  2. remux: `vp9_opus.mkv`. Zoeken naar 50 % en weer lopen;
  3. transcode: `hevc10_hdr10.mkv`. In Chromium 141 naar VP9 en Opus; in CI naar H.264 als
     `isTypeSupported("video/mp4; codecs=\"avc1.640028\"")` ja zegt, anders overgeslagen met een
     reden;
  4. een ander audiospoor (`multi_audio.mkv`): de positie blijft (±1 s);
  5. kwaliteit 720 naar 480: een nieuwe sessie, en de positie blijft;
  6. de kaart "Volgende aflevering" met de aftelling; Esc annuleert;
  7. PiP: `requestPictureInPicture` wordt aangeroepen (geen beeldcontrole headless);
  8. alleen met het toetsenbord, volgens de checklist;
  9. de app herstart midden in het afspelen: de speler loopt binnen 10 s door;
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
  PUT  /api/v1/media/progress/{node_id}  {position_ms, duration_ms, event, origin, audio?, subtitle?} → 204
                                         → 429 rate_limited (een tick binnen 5 s) | 404 not_found
  POST /api/v1/media/progress/batch      {node_ids[≤1.000] | show_id + season?, watched: bool} → 200 {updated}
  ```
- Het event `playback.progress`, alleen naar de gebruiker zelf.
- Web: `player/progress.ts` (ticks, pauze, stop, `keepalive`, `origin`);
  `media/rows/ContinueRow.tsx` en `NextUpRow.tsx`; `media/progressSync.ts` (luistert naar SSE en
  werkt de lijsten bij, negeert de eigen `origin`).

**Tests**
- De regels: 90 %; de aftiteling; 3 minuten van het eind; korte items; `play_count` één keer per
  sessie; een vervangen bestand.
- Gebundeld schrijven: 20 ticks geven hooguit 2 schrijfacties; pauze schrijft meteen; een "crash"
  verliest hooguit 10 s.
- De grens op ticks. Een event gaat naar dezelfde gebruiker, niet naar een ander. `batch` met
  `show_id` en `season`. Iets wat je niet meer mag lezen: 404.
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
- `test_media_http.py`: een privé-adres na DNS geweigerd; een omleiding naar een vreemde host
  geweigerd; de omleiding van CAA gevolgd; `Retry-After` met het plafond; de stroomonderbreker; een
  plaatje van 50 MB afgekapt; de logregel zonder query.
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
- `worker/ops/remux_audio.py` (Keuze 38).
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
- `routers/stream_audio.py`: `GET /api/v1/stream/{sid}/audio.mp4`, via `sessions.guard`.
- Web `music/**`: albums, album, artiesten, artiest, afspeellijsten en afspeellijst; de muziekbalk;
  `queue.ts` en `gapless.ts`.

**Tests**
- Groeperen: compilatie, geen albumartiest, meerdere discs, dezelfde albumnaam bij twee artiesten.
- `audio_info` op fixtures: FLAC 882.000 samples, MP3 met LAME 882.000, Opus met pre-skip, AAC met en
  zonder `iTunSMPB`; fuzz.
- Afspeellijsten: alleen van jezelf, 5.000, de volgorde, en een verwijderd bestand (CASCADE).
- e2e `test_media_m9.py`, gapless: een album van 3 FLAC- en 3 Opus-nummers uit één doorlopende sinus.
  De uitvoer gaat via een `MediaElementAudioSourceNode` naar een AudioWorklet. Bij elke overgang mag
  de fase niet springen (fout ≤ 1 sample). MP3 en AAC: het resultaat wordt vastgelegd, niet geëist.
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
  /api/v1/admin/media/loadtest`, een knop op `/beheer`, en `app.cli media-loadtest`.
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
    `office_start`.
  - Bij `MEDIA_SANDBOX=uit` staat `compose.media.yaml` niet in de lijst. De app krijgt dan geen
    `PLATFORM_MEDIA_WORKER` (de lokale worker), en `--remove-orphans` haalt de container weg.
  - `slot` meldt "Media: aan (afgeschermd)" of de reden waarom niet.
- `.env.example`: `MEDIA_SANDBOX` en `MAX_UPLOAD_BYTES`, met uitleg.
- `infra/single/tests/test_install_media.sh`:
  - een `MEDIA_SANDBOX` uit de omgeving wint;
  - een foute waarde stopt vóór er iets verandert;
  - `decide_media` stopt nooit (`set -e`);
  - `docker compose config -q` voor alle 8 combinaties (office aan/uit × media aan/uit × grenzen
    ja/nee).
- `docs/runbooks/media.md`: de status lezen; de nooduitgang; terugrollen; de cache opruimen; de
  TMDB-sleutel; de import (de map, `chown`, kopiëren of verplaatsen); de belastingtest op de LXC
  (§4.6); "later: GPU" (wat Jonas op de host zou moeten doen, en dat het nooit een eis is).

**Tests**
- `test_admin_media.py`: alleen beheerders; de configuratie heeft grenzen (`cpu_budget` 0,5–16;
  `max_transcodes` ≤ het plafond uit env); Stop sluit de sessie; de sleutel komt nooit terug.
- `test_media_monitor.py`: elke melding aan en weer uit.
- `test_media_status.py`: de uitvoer en de exitcode (0, ook bij een probleem met `--wait`).
- `test_media_loadtest.py` (nep-worker): het rapport, de weigering als er echte transcodes lopen, en
  de stop bij een vierde ffmpeg.
- e2e `test_media_m10.py`: het tabblad met het toetsenbord (Alt+8); een sessie zien en stoppen (de
  speler meldt het netjes); een import starten.
- **De installatietest** (zoals `test_install_update.sh` van fase 4, in de sandbox met dockerd):
  1. 0.6.0 installeren, een film uploaden;
  2. bijwerken naar deze versie: de app en de worker draaien, afspelen werkt;
  3. `MEDIA_SANDBOX=uit` en opnieuw: de container is weg, de lokale worker draait;
  4. terug met `db-downgrade 0007` en het script van 0.6.0: de app start, de bestanden zijn er.

**Klaar als.** Bijwerken en terugrollen zijn bewezen in de installatietest. `install.sh` stopt in
geen enkel geval door media. `/beheer` toont alles van Keuze 45.

---

## 3. Fundament (M0, eerst en alleen)

M0 raakt alle hotspots en legt de contracten vast. Werkers:

- **M0-api** begint met de spike (§3.7, dag 1–2), daarna het schema, de contracten, de kern van de
  worker, de reparaties F1–F4, de rijen en de haken;
- **M0-web** tegelijk: routes, haken, types, codes en budget;
- **M0-e2e** daarna: het harnas, de fixtures, de nep-worker, de nep-stream, de kern van de
  nep-providers, de HLS-lint en de CI-jobs.

Eén PR op de fase-5-branch, getagd `fase5-basis`. Daarna is de app zonder worker, of met
`MediaConfig.enabled = false`, precies 0.6.0 plus F1–F4.

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
  keyframes       BLOB NULL                           -- zlib(varint-delta's in ms) (Keuze 17)
  keyframes_state VARCHAR(8) NOT NULL DEFAULT 'none'  -- none | ready | sparse | failed
  max_gop_ms      INTEGER NULL
  subs_state      VARCHAR(8) NOT NULL DEFAULT 'none'  -- none | pending | ready | failed | skip
  trick_state     VARCHAR(8) NOT NULL DEFAULT 'none'  -- idem
  trick           JSON NULL                           -- {interval, w, h, cols, rows, count} (Keuze 30)
  audio_state     VARCHAR(8) NOT NULL DEFAULT 'none'  -- idem
  audio           JSON NULL                           -- {rate, samples, delay, padding, source} (Keuze 18)
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
  season        INTEGER NULL
  episode       INTEGER NULL
  episode_end   INTEGER NULL
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
class Op(StrEnum): CAPS; BENCH; PROBE; KEYFRAMES; AUDIO_INFO; NFO; FRAME; META; SUBS; SPRITES; STREAM; REMUX_AUDIO
async def send(sock, message: dict, fds: Sequence[int] = ()) -> None    # lengte + JSON, fd's als SCM_RIGHTS
async def receive(sock) -> tuple[dict, list[int]]                       # weigert > MAX_MESSAGE en te veel fd's

# app/media/worker/args.py: de argumenten per opdracht (pydantic; de client gebruikt dezelfde klassen)
class StreamArgs(BaseModel):
    mode: Literal["remux", "transcode"]
    demuxer: Demuxer                      # uit de witte lijst (render.container plus mp3, flac, aac)
    start_s: float                        # 2k (transcode) of b_k − 0,5 (remux); 0 vanaf het begin
    source_start_s: float                 # S, de start_time van de bron
    video_index: int
    audio_index: int | None
    video: Literal["copy"] | VideoEncode  # codec (libx264 | libvpx-vp9), width, height, preset (veryfast | superfast),
                                          #   level, maxrate_k, tonemap (pq | hlg | None), deinterlace, burn_index
    audio: Literal["copy"] | AudioEncode  # codec (aac | libopus), channels (2), bitrate_k
    grid: Literal["2s"] | None            # None bij remux: dan groepeert de app (Keuze 23)
    hvc1: bool = False                    # -tag:v hvc1 bij HEVC
    background: bool = False              # nice 15 + ionice
# en ProbeArgs, KeyframesArgs, AudioInfoArgs, NfoArgs, FrameArgs, MetaArgs, SubsArgs, SpritesArgs,
# RemuxAudioArgs, BenchArgs: elk alleen getallen, vaste woorden en indexen. Nooit een pad of vrije tekst.

# app/media/client.py: de enige weg naar de worker
class WorkerDown(Exception): ...          # geen verbinding, of een andere PROTOCOL
class WorkerBusy(Exception): ...
class WorkerFailed(Exception): code: str; detail: str
class Job:
    async def events(self) -> AsyncIterator[dict]
    async def result(self) -> dict
    async def cancel(self) -> None        # de verbinding dicht = killpg in de worker
async def call(op: Op, args: BaseModel, *, source: Path | None = None, outputs: int = 0) -> tuple[Job, list[int]]
    # opent source zelf (O_RDONLY | O_NOFOLLOW | O_CLOEXEC), maakt `outputs` pipes, stuurt de
    # schrijfkanten mee, sluit de eigen kopieën, en geeft de leeskanten terug (D7)
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
    burn: int | None; tonemap: Literal["pq", "hlg"] | None; cost: float

# app/media/decide.py (M0: signatuur; M3: de regels)
def decide(probe: Probe, profile: ClientProfile, choice: Choice, policy: Policy, budget: BudgetView) -> Plan

# app/media/admission.py (M0-stub; M4)
def view(user_id: UUID, link_id: UUID | None) -> BudgetView          # wat er nu past, voor decide
def reserve(plan: Plan, user_id: UUID, link_id: UUID | None) -> Grant # MediaBusy(reason, retry_after)
def release(grant: Grant) -> None
def snapshot() -> AdmissionView                                       # voor /beheer

# app/media/sessions.py (M0-stub; M3)
async def create(user, node, probe, plan, grant, *, link_id=None, replace: str | None = None) -> Session
async def create_audio(user, node, probe) -> Session                  # voor M9
async def guard(request: Request, sid: str) -> Session
    # 410 media_session_gone: onbekend of verlopen
    # 403 media_access_gone {reason}: de toegang is weg, of de app sloot de sessie zelf (grafsteen)
def close_user(user_id: UUID, keep_family: UUID | None = None) -> int
def close_node(node_id: UUID) -> int
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
def touched(node_ids: Iterable[UUID]) -> None                        # vanuit files.notify*; geen I/O
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

**De commando's van de worker.** Frozen na S1 en S2 (§3.7). `{…}` vult de worker uit gevalideerde
argumenten. Dit zijn de commando's van ontwerp A met de twee fouten van J4 eruit.

Elke invoer:
```
-nostdin -hide_banner -loglevel error -progress pipe:2 -nostats
-protocol_whitelist fd,pipe -format_whitelist {lijst} -f {demuxer} -fd 3 -i fd:
```

Transcode, vanaf 0 of na een sprong naar segment k (720p, SDR, audio naar AAC):
```
nice -n 5 ffmpeg {invoer, met -ss 2k vóór -i als k > 0} -copyts
  -map 0:{V} -map 0:{A} -sn -dn -map_metadata -1 -map_chapters -1
  -vf "scale=w={W}:h={H}:flags=bicubic,format=yuv420p"
  -c:v libx264 -preset {veryfast|superfast} -crf 23 -maxrate {R}k -bufsize {2R}k
  -profile:v high -level:v {L} -threads 4 -sc_threshold 0
  -force_key_frames "expr:gte(t,n_forced*2)" -forced-idr 1
  -c:a aac -ac 2 -b:a 160k
  -output_ts_offset {1−S} -avoid_negative_ts disabled
  -f mp4 -movflags +frag_keyframe+empty_moov+default_base_moof+skip_trailer+frag_discont
  -fflags +bitexact pipe:1
```
- `t` telt vanaf het begin van de uitvoer (J4). Een herstart gebeurt alleen op 2k. Zo geeft
  dezelfde uitdrukking hetzelfde rooster, vanaf 0 en na elke sprong.
- `+frag_discont` is nodig: zonder begint `tfdt` op 0, ook met `-copyts` (J4). Met de vlag is
  `tfdt` = mediatijd + 1 s.
- `-forced-idr 1`: elk geforceerd keyframe is een IDR. Dan begint elk segment echt opnieuw, en mag
  `EXT-X-INDEPENDENT-SEGMENTS` (S2 controleert NAL-type 5).
- `-threads 4` is een bovengrens per proces. De kosten meet S10.
- HDR: `-vf` wordt de keten van Keuze 27. Interlaced: `bwdif=…` ervoor.
- Inbranden:
  `-filter_complex "[0:{V}]{filters}[v];[0:{N}]scale={W}:{H}[s];[v][s]overlay=eof_action=pass,format=yuv420p[out]" -map "[out]"`.
- Zonder H.264 bij de client: `-c:v libvpx-vp9 -deadline realtime -cpu-used 8 -row-mt 1 -b:v
  {R}k -maxrate {R}k -crf 32` en `-c:a libopus -b:a 128k -ac 2 -ar 48000`. VP9 kent alleen echte
  keyframes; `-forced-idr` valt dan weg.

Remux (video kopiëren, audio kopiëren of omzetten):
```
ffmpeg {invoer, met -ss (b_k − 0,5) vóór -i als k > 0} -copyts
  -map 0:{V} -map 0:{A} -sn -dn -map_metadata -1 -map_chapters -1
  -c:v copy [-tag:v hvc1] -c:a {copy | aac -ac 2 -b:a 160k | libopus -b:a 128k -ac 2 -ar 48000}
  -output_ts_offset {1−S} -avoid_negative_ts disabled
  -f mp4 -movflags +frag_keyframe+empty_moov+default_base_moof+skip_trailer+frag_discont
  -fflags +bitexact pipe:1
```
- Elke GOP is een fragment. De app groepeert op de pts van het eerste sample (Keuze 23) en gooit
  na een herstart weg wat vóór `b_k` ligt.

Ondertitels (alle tekstsporen in één run, Keuze 29):
```
nice -n 15 ionice -c 3 ffmpeg {invoer} -copyts
  -map 0:{i} -c:s copy -f ass pipe:4    (ASS, SSA)
  -map 0:{j} -c:s copy -f webvtt pipe:5 (WebVTT)
  -map 0:{k} -c:s srt -f srt pipe:6     (de rest)
```
- De pipes zijn van de worker zelf. De worker zet elk spoor om met `subconv` en schrijft de VTT naar
  de uitvoer-fd's van de app.
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

De meting (Keuze 26), zonder invoer:
```
ffmpeg -f lavfi -i "testsrc2=size=1920x1080:rate=24,noise=alls=20:allf=t" -t 10
  -vf "scale=-2:720" -c:v libx264 -preset veryfast -threads 4 -f null -
```
De worker meet de CPU-tijd van het proces (`getrusage` van het kind). Cores voor 1,0× = CPU-tijd /
10 s.

### 3.4 API-contracten, gebeurtenissen en codes (frozen)

De eindpunten staan bij de pakketten (§2). M0 zet ze als routes met de juiste vorm, en de modellen
in `app/media/types.py` en `web/src/media/types.ts`. Een stub geeft 503 `media_off` tot het pakket
hem vult. `fake_stream.py` geeft precies dezelfde vorm (§3.6).

**Codes**, allemaal in `web/src/i18n/errors/media.ts` (nl en en; `test_error_catalog.py`):

| Code | Status | Extra | Wanneer |
|---|---|---|---|
| `media_off` | 503 | | media staat uit, of een stub van M0 |
| `media_worker_down` | 503 | | de worker is weg, of heeft een andere `PROTOCOL` (Keuze 13, 47) |
| `media_ffmpeg_missing` | 503 | | de worker draait, maar ffmpeg ontbreekt of `caps` faalt (Keuze 46) |
| `media_probe_pending` | 409 | `Retry-After` | de probe loopt nog na 5 s (Keuze 15) |
| `media_unavailable` | 409 | `reason` | de probe mislukte |
| `media_unsupported` | 415 | `reasons` | geen enkele weg naar deze client |
| `media_hdr` | 422 | `can_raw`, `reason` | HDR kan niet omgezet worden (Keuze 27) |
| `media_busy` | 503 | `reason` (`cpu`, `disk`, `user`, `link`), `retry_after` | de toelating zegt nee (Keuze 26) |
| `media_session_gone` | 410 | | de sid is onbekend of verlopen; de speler vraagt stil een nieuwe |
| `media_access_gone` | 403 | `reason` (`revoked`, `stopped`, `blocked`) | de toegang is weg, of de app sloot de sessie; de speler stopt |
| `media_timeout` | 503 | `Retry-After` | een segment niet binnen 20 s |
| `media_transcode_failed` | 502 | | ffmpeg faalde twee keer op hetzelfde segment |
| `media_public_transcode` | 403 | | een publieke link, en transcode staat uit |
| `media_not_owner` | 403 | | een bibliotheek op een map van een ander |
| `media_library_nested` | 409 | `library_id` | een bibliotheek boven of onder een andere |
| `media_library_not_folder` | 422 | | geen map, of in de prullenbak |
| `media_library_limit` | 422 | `max` | meer dan 20 |
| `media_import_path` | 422 | | een pad buiten `/data/import` |
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
- **F3. `timeout_graceful_shutdown=10`** in `serve.py` (N6). Bij stoppen krijgt een lange stream
  10 s, dan gaat hij dicht. De speler vernieuwt zelf (Keuze 21).
- **F4. `media-src 'self' blob:`** in `WEB_CSP` (N5). Zonder `blob:` speelt geen enkele MSE-video:
  `video.src` is dan een object-URL. De CI-job `images` controleert de CSP; die regel verandert
  mee.
- **`jobs.py`** (Keuze 14):
  - `register` krijgt `lane` en `timeout`. `timeout` vervangt `settings.job_timeout` voor die
    soort;
  - een `Runner` per rij: `default` (`job_workers`, zoals nu), `media` (`media_job_workers`) en
    `import` (1);
  - `claim()` pakt alleen soorten uit zijn eigen rij. De rij `default` pakt alles wat niet in een
    andere rij staat. Een onbekende soort faalt dus zoals nu;
  - `Defer(seconds)`: de taak gaat terug naar `queued` met `run_after = nu + seconds` en
    `attempts − 1`;
  - `test_jobs_lanes.py`: een lange media-taak houdt een preview niet op; `Defer` telt niet; de
    timeout per soort; de bestaande soorten veranderen niet.
- **De haken** (één regel elk):
  - `files.notify` en `notify_many` roepen `media.hooks.touched(...)` aan (synchroon, geen I/O);
  - `auth.py` roept `media.sessions.close_user` aan naast `office.tunnel.close_user`, na dezelfde
    commit;
  - `previews.discard` roept `media.hooks.on_discard(sha)` aan, na `convert.on_discard`;
  - `usage.py` en `routers/admin.py`: een lege haak voor het deel `media` en de meldingen (M10
    vult ze).
- **`main.py`**:
  - de routers (stubs);
  - in de lifespan: `media.runtime.bind(app)`. Dat start de lokale worker of de gezondheidslus,
    de lus van de scanner, maakt `media/sessions/` leeg, en registreert de housekeeping
    (`media.sweep`);
  - `_hide` krijgt het patroon `(/api/v1/stream/[^/.\s"]+\.)[^/?\s"]+` → `\1***`, naast
    `SECRET_PARAMS` en `LINK_PATH`;
  - F4.
- **`cli.py`**: `media-import`, `media-parse`, `media-status`, `media-provider-check` en
  `media-loadtest`, elk als dunne aanroep van een functie in het pakket (stub tot dan).
- **`config.py`** en **`models.py`**: §3.1 en §3.2.
- **`pyproject.toml`** en **`package.json`**: ongewijzigd.
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
  | `interlaced.ts` (`-flags +ilme+ildct -top 1`, `start_time` 1,4 s) | bwdif, de verschuiving `S` |
  | `fps23976.mkv` (H.264 op 24000/1001) | het rooster na een herstart (S2) |
  | `multi_audio.mkv` (nl en en, een geforceerd ondertitelspoor) | audiospoor, geforceerd |
  | `subs_text.mkv` (SRT en ASS), losse `nl.srt` (CP1252), `en.ass`, `.nl.forced.srt`, een SRT in UTF-8 met BOM | ondertitels |
  | `pgs.mkv` (via de eigen `.sup`-schrijver) en `vobsub.mkv` (`-c:s dvdsub` uit een SRT) | inbranden |
  | `long_gop.mkv` (keyframe per 25 s) en `irregular_gop.mkv` | remux geweigerd; de remuxregel |
  | `no_cues.mkv` (naar een pipe gemuxt, dus zonder Cues) | de terugval voor keyframes |
  | `chapters.mkv` (uit `ch.ffmeta`, met "End Credits") | hoofdstukken, de aftiteling |
  | `album/` (3 FLAC, 3 MP3 met LAME, 3 Opus, 3 AAC met en zonder `iTunSMPB`; één doorlopende sinus; tags en een hoes) | gapless, muziektags |
  | `broken.mp4` (afgekapt), `random.mkv` (willekeurige bytes), `empty.mkv` (0 bytes) | nette fouten, fuzz |
  | `loadtest/` (3 × 300 s 1080p H.264, 6 Mbit/s: `testsrc2`, `mandelbrot` en `life`, elk met ruis) | alleen `media_perf` en de belastingtest; na elkaar gemaakt, ±675 MB |

- **`api/tests/media/sup.py`:** een eigen schrijver voor PGS (`.sup`). Hij schrijft de segmenten
  PCS, WDS, PDS, ODS en END met een RLE-bitmap van een blok tekst. ffmpeg heeft geen PGS-encoder,
  maar wel een `sup`-demuxer en kan kopiëren naar mkv [doc; S9].
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
  op volgorde en op het rooster; de verbinding dicht is stoppen (geen proces over); `busy` bij de
  zevende verbinding; een andere `PROTOCOL`; een te grote boodschap; een onbekende opdracht; een
  verkeerd aantal fd's. Zo blijft de nep eerlijk.
- **`api/tests/media/fake_stream.py`:** een ASGI-app met `play` en `/stream/…` uit segmenten die
  het harnas bij de start maakt met echte ffmpeg. Precies het contract van §3.4. Zo kan M6 bouwen
  voordat M3 en M4 klaar zijn. `media_contract.py` beschrijft de scenario's: M0 draait ze tegen de
  nep, M4 tegen het echte (`test_media_contract.py`).
- **`api/tests/media/fake_providers/`:** de nep-server voor TMDB, het plaatjeshost, MusicBrainz en
  de Cover Art Archive (§4.4). M0 maakt de kern (server, fouten op bestelling, logboek, laden van
  data); M8 vult hem.
- **`api/tests/media/hls_lint.py`:** een eigen lint voor onze playlists, naar RFC 8216 en de regels
  van Apple en LG [doc]:
  - `#EXTM3U` eerst, LF, UTF-8 zonder BOM;
  - `EXT-X-VERSION` 7 (fMP4 met `EXT-X-MAP`);
  - elke `EXTINF`, afgerond, ≤ `TARGETDURATION`; `EXT-X-ENDLIST` bij VOD;
  - `CODECS` in elke `EXT-X-STREAM-INF`, met video én audio; `BANDWIDTH` ≥ de piek;
  - geen `EXT-X-INDEPENDENT-SEGMENTS` met het basisprofiel `webos-5`, en nooit bij remux;
  - alleen relatieve URI's; geen query behalve `g=`.

  Met eigen tests (goede en kapotte playlists). `test_media_hls.py` (M4) gebruikt hem.
- **`e2e/media_harness.py`** en **`e2e/conftest.py`**:
  - fixture `media`: start de app met de lokale worker (in de CI-job `media-image` met de
    worker-container), en de nep-providers als proces. Zet `PLATFORM_MEDIA_SCAN_TICK=0.5`, de URL's
    van de providers, `PLATFORM_MEDIA_PROVIDER_PRIVATE_OK=true` en
    `PLATFORM_MEDIA_MAX_TRANSCODES=3`;
  - uploadt fixtures via de API;
  - Chromium met `--autoplay-policy=no-user-gesture-required`; voor `media_perf` 40 Mbit/s en
    20 ms via CDP (`Network.emulateNetworkConditions`);
  - fixture `ffmpeg_count`: telt na elke test de ffmpeg-processen (de kinderen van de worker, via
    `/proc`). Verwacht: 0;
  - stopt alles op pid, nooit op naam;
  - hulpjes: `first_frame_ms(page)` via `requestVideoFrameCallback`, `wait_playing(page, t)`,
    `codec_supported(page, s)`. Een test met H.264 of AAC slaat over als de browser ze niet kan, met
    de reden in het verslag;
  - `e2e/pytest.ini`: markers `media`, `media_perf` en `provider_real` (`--strict-markers` blijft).
- **CI** (§4.8): de nieuwe jobs `media-image` (elke push) en `media-spike` (met de hand).

### 3.7 De spike (M0-api, dag 1–2)

ffmpeg 7.1.5 staat in het image, niet in de sandbox (apt is dicht, N22). Daarom draait het deel
"in het image" als CI-job `media-spike` (`workflow_dispatch`). De uitkomst is een artefact. De rest
draait in de sandbox, met ffmpeg 6.1.1, Docker 29 en Chromium 141. Alles komt in
`docs/fase-5/spike.md`. Daarna ligt het vast.

| Stap | Waar | Wat | Beslist |
|---|---|---|---|
| S1 | image | ffmpeg 7.1.5: versie, encoders (`libx264`, `aac`, `libopus`, `libvpx-vp9`, `mjpeg`), filters (`zscale`, `tonemap`, `bwdif`, `overlay`, `tile`), protocollen `fd` en `pipe`, `-format_whitelist`; `nice` en `ionice` | of Keuze 27 tone-mapping heeft; de lijst van de selftest |
| S2 | image | D1–D6 en J4 op 7.1.5: fd-invoer met zoeken, fMP4 naar een pipe, `+frag_discont` geeft `tfdt` = mediatijd + 1, `gte(t,n_forced*2)` met `-forced-idr 1` vanaf 0 en na `-ss 2k` (ook `fps23976.mkv` en `interlaced.ts`), elk fragment begint met NAL-type 5, tegendruk | de commando's van §3.3 |
| S3 | image | fd's tussen containers met het echte image en de echte compose (J1 deed het al in de sandbox, met een ander image): het benoemde volume (10001, 0700), `network_mode: none`, `read_only`, tmpfs op `/data`, Docker 29. De spike bouwt een afgeleid image met de regel `/run/media` (M1 zet hem in het Dockerfile) | `compose.media.yaml`; de terugval |
| S4 | sandbox | MSE in Chromium 141: gemuxte VP9+Opus-segmenten uit onze knipper, één SourceBuffer, een nieuwe init (gelijke en andere `stsd`), over 54 ms springen, `timestampOffset = −1`, `<track>` met MSE | de details van `mse.ts` |
| S5 | image | welke boxen verschillen tussen de inits van twee runs: alleen `mvhd`, `tkhd`, `mdhd`, of ook `stsd` en `elst` (N10) | de vergelijking van Keuze 22 |
| S6 | sandbox | de CPU van het knippen in Python: 3 × 6 Mbit/s, en remux op schijfsnelheid | of remux `-readrate` nodig heeft naast de tegendruk |
| S7 | image | `bench` drie keer: spreiding < 10 % | of de factor `f` bruikbaar is |
| S8 | sandbox | gapless: FLAC en Opus via `remux_audio` met `-use_editlist 0` en `appendWindow`, op het sample? MP3 als `audio/mpeg`: de precieze `delay` | `gapless.ts`; wat we over MP3 beloven |
| S9 | sandbox | PGS: de eigen `.sup` naar mkv, ffmpeg decodeert hem, en `overlay` brandt zichtbare pixels in | of inbranden in fase 5 blijft |
| S10 | image | `RLIMIT_FSIZE` 0 en `-threads 4` met x264: draait ffmpeg nog? Wat kost `-threads 4` tegen de standaard op 1080 veryfast? | Keuze 12 |
| S11 | image | VP9 realtime op 720p (`-cpu-used 8 -row-mt 1`): cores voor 1,0× | de kostentabel voor VP9 |
| S12 | sandbox | F1 en F2: 16 gepauzeerde streams vóór en na; `HEAD` | de reparatie |

**Als een stap faalt:**
- **S2** (`tfdt` of het rooster gedraagt zich anders op 7.1.5): de knipper zet `tfdt` zelf. Hij
  leest de `moof` toch al, en een vaste verschuiving per run is een paar regels. Valt een keyframe
  na een herstart één frame anders (23,976 fps), dan is dat onschuldig: MSE overschrijft een frame,
  en de speler springt over een gat van één frame.
- **S3** (fd's tussen containers), in deze volgorde:
  1. een bindkoppeling `${DATA_ROOT}/media/run:/run/media` in plaats van het benoemde volume
     (`install.sh` maakt de map met eigenaar 10001). De afscherming blijft gelijk;
  2. de worker krijgt de blobs alleen lezen (`${DATA_ROOT}/blobs:/data/blobs:ro`), en de app
     geeft een pad in plaats van een fd. De worker ziet dan alle films, maar nog steeds geen
     database, geheimen of netwerk. Dat staat dan in `spike.md` en bij de risico's;
  3. de nooduitgang `MEDIA_SANDBOX=uit`.
- **S4** (een nieuwe init werkt niet in MSE): bij een andere `stsd` een nieuwe sessie op die plek,
  net als bij native HLS.
- **S9:** inbranden schuift (§5).
- **S10:** zonder `RLIMIT_FSIZE`; de rest blijft.

### 3.8 Klaar als (M0)

- Alle bestaande tests groen; ruff, tsc, build en budget schoon. De eerste lading blijft ≤ 100 KB.
- `test_migration_0008.py`: een database van 0007 met data → upgrade → tabellen en indexen er →
  downgrade → gelijk aan 0007 → upgrade. Na `db-downgrade 0007` start 0.6.0.
- Met media uit, of zonder worker, slagen alle tests van fase 3 en 4 onveranderd.
- De lokale worker beantwoordt `caps` en `probe` op alle fixtures (`test_media_worker_core.py`,
  `test_media_probe.py`). `test_media_wire.py` dekt het protocol en de weigeringen.
- F1–F4 zijn groen, elk met een test die eerst faalde.
- De rijen en `Defer` werken (`test_jobs_lanes.py`).
- `test_media_routes_guarded.py`: elke route onder `/api/v1/stream/`, ook een stub, gaat langs
  `sessions.guard`.
- `test_media_logs.py` met de nep-stream: geen sid in een logregel.
- `fake_worker` slaagt voor `worker_contract.py`, en de lokale worker voor het deel dat M0 heeft
  (`caps`, `probe`, stoppen).
- `fake_stream` slaagt voor `media_contract.py`. `hls_lint` heeft zijn eigen tests.
- De fixtures worden in CI gemaakt in ≤ 30 s.
- De job `media-image` is groen, met de selftest op 7.1.
- De spike is gedaan. `docs/fase-5/spike.md` heeft een uitkomst per stap, en de commando's van §3.3
  liggen vast.
- De keuzes staan als B39 en verder in `docs/fase-0/beslissingen.md` (voorstel), en
  `docs/fase-5/README.md` heeft een skelet.
- Tag `fase5-basis`.

### 3.9 Elk verzoekpad, met bewijs en controle

| # | Van → naar | Pad | Bewijs | Wie controleert wat |
|---|---|---|---|---|
| 1 | browser → app | `/api/v1/media/…` (bibliotheken, items, `codecs`, voortgang, koppelen, muziek) | access-token (Bearer) | `CurrentUser` (2FA); `access.resolve`; schrijfrecht voor koppelen en corrigeren |
| 2 | browser → app | `POST /api/v1/media/{node}/play` | Bearer | leesrecht van nu, media aan, de toelating, 8 sessies per gebruiker |
| 3 | browser → app | `POST /api/v1/public/media/{node}/play` | sleutel van de link (`CurrentLink`) | de link leeft, het bestand zit erin, lezen mag, de vingerafdruk van het wachtwoord; 2 per link, 30 per minuut per IP |
| 4 | browser → app | `/api/v1/stream/{sid}/…` (`file`, `map.json`, playlists, `init.mp4`, `{n}.m4s`, `subs/…`, `audio.mp4`) | de sid in het pad (24 willekeurige bytes) | `sessions.guard`: bestaat, niet verlopen, geen grafsteen, de toegang van nu (cache ≤ 15 s) |
| 5 | browser → app | `/api/v1/media/trick/…`, `/api/v1/media/art/…` | ondertekend adres (12 uur) | handtekening en tijd, zoals previews |
| 6 | browser → app | `/api/v1/admin/media/…` | Bearer, beheerder | zoals de rest van `/beheer` |
| 7 | app → worker | Unix-socket `/run/media/worker.sock` | geen: alleen uid 10001 in de app en de worker ziet het volume (0700) | de worker: `PROTOCOL`, de grootte, het aantal fd's, de argumenten (pydantic) |
| 8 | app → TMDB, MusicBrainz, CAA | https via `providers/http.py` | Bearer voor TMDB (uit `media_secrets`); niets voor MB en CAA | vaste hosts, de controle na DNS, grenzen, de stroomonderbreker (Keuze 39) |

Vanaf internet zijn alleen #1–#6 bereikbaar, via 80 of 443. #7 verlaat de LXC niet, en de worker
heeft geen netwerk. #8 gaat alleen naar buiten, en alleen als Jonas het aanzet.

---

## 4. Tests en meten

### 4.1 De lagen

| Laag | Waartegen | Waar | Poort |
|---|---|---|---|
| Unit | pure functies: namen, groeperen, NFO, codec-strings, EBML, MP4-boxen, het rooster, de knipper, VTT, gapless, de beslissing, de toelating, playlists, de score van koppelen, voortgang; web: toetsen, segmentrekensom, vernieuwen | pytest, vitest | hard, in elke CI-run |
| Worker | de lokale worker met echte ffmpeg op de fixtures, hooguit 3 processen | pytest (job `api`, ffmpeg 6.1) | hard |
| API | elk eindpunt; randgevallen met de nep-worker en de nep-providers | pytest | hard |
| Contract | `worker_contract.py` (nep en echt), `media_contract.py` (`fake_stream` en M4), `provider_contract.py` (nep altijd, echt alleen met de hand) | pytest | hard (behalve `provider_real`) |
| Image | de selftest op ffmpeg 7.1.5; de afscherming van de container; fd's tussen containers | CI-job `media-image` (Docker) | hard |
| e2e | de echte speler in de browser met gemaakte media | Playwright: Chromium 141 hier, Chrome for Testing 156 in CI (marker `media`) | hard |
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
- **0 ffmpeg na elke test** (de fixture `ffmpeg_count`). Nooit meer dan 3 tegelijk.
- **De vaste tests van M0 en M1:** `test_media_routes_guarded.py`, `test_media_no_ffmpeg_outside.py`,
  `test_media_logs.py`, `test_error_catalog.py` (bestaat) en `test_migration_0008.py`.
- **De volgorde van bewijzen:** eerst de pure functies, dan de API met de nep-worker, dan met echte
  ffmpeg, dan e2e. Een fout in e2e krijgt eerst een test op het laagste niveau dat hem kan vangen.

### 4.3 e2e met gemaakte media (marker `media`)

Chromium 141 van Playwright speelt VP9, AV1, Opus, FLAC en MP3, en geen H.264, AAC, HEVC of AC-3
(N13). De tests gebruiken dat bewust: HEVC en AC-3 dwingen een transcode af. Een toets met H.264
draait alleen als `codec_supported` ja zegt (in CI met Chrome for Testing waarschijnlijk wel
[niet getest]), en slaat anders over met een reden. Nooit `playwright install`: de browser die er
is.

| # | Scenario | Pakket |
|---|---|---|
| 1 | een map met films, een serie en een album wordt een bibliotheek; titels uit de namen, posters uit `poster.jpg`, seizoenen | M2 |
| 2 | direct: `vp9_opus.webm`; het eerste beeld en zoeken | M3, M6 |
| 3 | remux: `vp9_opus.mkv` (reden `container`); een ander audiospoor (nieuwe sessie, de positie blijft ±1 s); zoeken voorbij de cache start een nieuwe producent | M3, M4, M6 |
| 4 | transcode: `hevc10_hdr10.mkv` naar VP9 720p met tone-mapping (redenen `video_codec`, `hdr`); SRT aan; de ASS-cue met `\an8` staat boven in beeld | M3, M4, M5, M6 |
| 5 | inbranden: `pgs.mkv` en `vobsub.mkv`; de pixels op de plek van de ondertitel veranderen (canvas) | M5 |
| 6 | voortgang: twee contexts van één gebruiker; A pauzeert op 0:30; bij B staat binnen 2 s "Verder kijken 0:30", en B begint daar | M7 |
| 7 | de kaart "Volgende aflevering" met de aftelling; Esc annuleert; `cd1` gaat vanzelf naar `cd2` | M6, M7 |
| 8 | gapless: het album via MSE; de fase van de sinus springt niet (FLAC, Opus) | M9 |
| 9 | alles met alleen het toetsenbord: bibliotheek, item, speler, menu's, muziek, `/beheer` | M2, M6, M9, M10 |
| 10 | publieke link: direct en remux spelen; transcode geeft 403 `media_public_transcode`; de link ingetrokken: de speler stopt binnen 15 s plus de buffer | M3 |
| 11 | een deling ingetrokken terwijl iemand kijkt: 403 binnen 15 s; uitloggen op een ander apparaat: meteen | M3 |
| 12 | de lokale worker gedood (op pid) tijdens een transcode: hij herstart, de speler loopt binnen 10 s door; geen ffmpeg over | M1, M4 |
| 13 | de worker zonder ffmpeg (`PATH` leeg, een optie van het harnas): de melding op `/beheer`; direct werkt; een HEVC-film geeft de fout met "Downloaden" | M1, M10 |
| 14 | `/beheer` → Media: de status; een sessie stoppen: de speler zegt het en vraagt geen nieuwe sessie (403 `stopped`) | M10 |
| 15 | muziek: een album spelen; de balk blijft spelen bij navigeren; een afspeellijst maken en ordenen | M9 |
| 16 | de import vanuit `/data/import` via `/beheer`, met kopiëren en met verplaatsen | M2, M10 |
| 17 | metadata met de nep-TMDB: de sleutel invullen, posters, een verkeerde koppeling goed zetten | M8 |
| 18 | de app herstart midden in een film: de speler loopt binnen 10 s door, op de goede positie | M3, M6 |
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
sandbox, en samen onder 8 GB.

| # | Meting | Hoe | Grens | Verwacht |
|---|---|---|---|---|
| Ma | Direct play, eerste beeld | e2e `media_perf`: 1080p, 20 Mbit/s, 2 min (VP9 hier, H.264 in CI); 40 Mbit/s en 20 ms via CDP; van klik tot het eerste `requestVideoFrameCallback` | ≤ 2 s | 38–81 ms lokaal in de browser (N15), plus API en schijf |
| Mb | Transcode, eerste beeld | e2e: `hevc10_hdr10.mkv` naar 720; zonder browser: van `POST /play` tot segment 0, H.264 1080 naar 1080 en naar 720, ook terwijl er 2 andere transcodes lopen | ≤ 5 s (e2e); ≤ 2,5 s (zonder browser) | eerste fragment 0,8–1,9 s alleen; 2,7–3,0 s naast 2 × 1080 (J2) |
| Mc | Zoeken | `currentTime` naar 70 % (nog niet gemaakt) tot hij voorbij het doel loopt: direct, remux, transcode | ≤ 1,5 s / 1,0 s / 3,0 s | ±1,3 s plus toevoegen (D4) |
| Md | 3 transcodes | de belastingtest (§4.6) met de standaard van Vraag 3 (1080 veryfast + 720 superfast + 720 superfast), 300 s; en nog eens terwijl OnlyOffice elke 30 s een docx van 50 pagina's omzet | 0 keer haperen; elke run ≥ 1,1× | 3 × 720 veryfast 2,3–2,4× (J2), 1,67× (testbron) |
| Me | 2 × 1080 | `max_transcodes` 2: 1080 veryfast + 1080 superfast, 300 s | 0 keer haperen | 2 × 1080 veryfast 1,37× (testbron) |
| Mf | HDR | 1 × 1080 HDR alleen; 1 × 720 HDR naast 2 × 720 superfast | 0 keer haperen | 1,35× / niet gemeten |
| Mg | De API onder belasting | p95 van `GET /api/v1/files/root` en `/api/v1/media/home` tijdens Md | ≤ 200 ms | — |
| Mh | Geheugen | de hoogste RSS van de worker tijdens Md en Mf | ≤ 3 GB | 0,75–1,3 GB |
| Mi | Voortgang synchroniseren | PUT in A tot het event in B, 20 keer | p95 ≤ 2 s | < 0,1 s (de hub zit in het geheugen) |
| Mj | Kosten van het knippen | de CPU van de app per stroom (S6) | ≤ 0,05 core | niet gemeten |
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

1. **Bronnen.** Standaard maakt de worker drie verschillende bronnen: 300 s 1080p H.264, 6 Mbit/s
   (`testsrc2`, `mandelbrot` en `life`, elk met ruis), na elkaar, in `media/loadtest/` (±675 MB,
   hergebruikt). Met `sources` kan het met echte films van de beheerder.
2. **Sessies.** Ze zijn van de beheerder die de test start. Bij de eigen bronnen wijzen ze naar een
   bestand buiten de boom; de controle per verzoek kijkt dan of die beheerder nog actief is.
3. **Profiel.** H.264 via MSE (of VP9 met `codec: "vp9"`) en `max.bitrate_kbps: 4000`. De bron
   heeft 6 Mbit/s, dus de beslissing kiest zelf transcode (reden `bitrate`), en de toelating kiest
   de trede.
4. **Spelers.** Per sessie een virtuele speler in Python (geen browser). Hij praat via http met de
   app op 127.0.0.1, dus de routes en de controle doen mee.
   - Hij begint te spelen zodra **segment 0** er is (niet 0 en 1: dat verborg het haperen, J3).
   - Daarna vraagt hij de segmenten op volgorde, met hooguit 30 s vooruit, en speelt in echte tijd.
   - De speeltijd loopt alleen als het segment dat nodig is, helemaal binnen is. Anders telt het
     als haperen (aantal en duur).
5. **Verloop.** De sessies starten op 0, 10 en 20 s. Elke sessie springt één keer, op 60 s, naar
   een plek voorbij de cache. Zo doet een herstart mee.
6. **Meet:** de start per sessie; haperen (aantal en totaal); de snelheid van elke run; de trede die
   de toelating koos; de CPU van de worker (uit de cgroup, anders `/proc`); de RSS per proces; de
   cache in MB per minuut; de p95 van `GET /api/v1/files/root` en `/api/v1/media/home` elke seconde.
7. **Slaagt als:** elke start ≤ 5 s, 0 keer haperen na de start, elke run gemiddeld ≥ 1,1× (marge
   boven de 1,0× die het rooster van 2 s nodig heeft), en de API ≤ 200 ms (p95).
8. **Veilig:**
   - hij weigert (409 `media_bench_busy`) als er echte transcodes lopen;
   - zolang hij loopt, wachten de analyse en de previews van video's (`Defer`);
   - hij telt elke seconde de ffmpeg-processen en stopt bij een vierde;
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
| … en binnen 5 s (transcode) | het eerste beeld ≤ 5 s, ook als er al 2 transcodes lopen | Mb: e2e en zonder browser (≤ 2,5 s, de grens van M4); de start in de belastingtest ≤ 5 s |
| Seek werkt | weer spelen ≤ 3 s, ook in een stuk dat nog niet gemaakt is; direct ≤ 1,5 s, remux ≤ 1 s | Mc; e2e 2, 3 en 4; `test_media_restart.py` (na een herstart hetzelfde rooster en `tfdt` = mediatijd + 1) |
| 3 gelijktijdige transcodes zonder haperen | standaard 1080 + 720 + 720 (Vraag 3), 0 keer haperen voor een speler die alleen hele segmenten speelt, de API ≤ 200 ms, ook naast OnlyOffice | Md, Mg en Mh met de belastingtest, in de sandbox en op de LXC; J3 laat zien waarom het rooster overal 2 s is |
| Voortgang synchroniseert tussen apparaten | ≤ 2 s (p95 van 20) | Mi; e2e 6 |
| De rest van het stappenplan (bibliotheken, metadata, ondertitels, muziek, gapless) | — | de e2e van §4.3, Ml, Mn en de tests per pakket |

"Zonder haperen" is hier strenger dan in een browser. Die buffert 30 s; de virtuele speler wacht
op elk segment dat hij nodig heeft.

### 4.8 CI

- `api`: alle tests behalve `media_perf` en `provider_real`. ffmpeg 6.1 van Ubuntu staat er al. De
  lokale worker.
- `web`: `tsc -b`, vitest, `check:i18n`, build, `budget`.
- `e2e`: zoals nu, met de media-tests erbij:
  `-m "not perf and not media_perf and not office and not office_real and not provider_real"`.
- `infra`: `docker compose config -q` voor alle 8 combinaties (office aan of uit × media aan of uit ×
  grenzen ja of nee); `shellcheck`; `test_install_env.sh` en `test_install_media.sh`.
- `images`: zoals nu; de verwachte CSP krijgt `media-src 'self' blob:` (F4).
- **`media-image`** (nieuw, elke push): het image bouwen (de cache van `images`), `--selftest` op
  ffmpeg 7.1, `test_media_container.sh` (M1), en de rooktest van de belastingtest.
- **`media-spike`** (nieuw, met de hand): de scripts van de spike in het image; de uitkomst als
  artefact.

---

## 5. Volgorde

1. **M0 Fundament, alleen** (±5 dagen).
   - Dag 1–2: de spike (§3.7). De stappen in het image als CI-job `media-spike`, de rest in de
     sandbox.
   - Tegelijk: M0-api (schema, contracten, de kern van de worker, F1–F4, de rijen, de haken) en
     M0-web (routes, haken, types, codes, budget).
   - Daarna M0-e2e: het harnas, de fixtures, de nep-worker met opnames, `fake_stream`, de kern van
     de nep-providers, `hls_lint`, en de jobs `media-image` en `media-spike`.
   - Poort: §3.8. Tag `fase5-basis`.
2. **De parallelle golf** (±8 dagen; M4 is het langst): M1 tot en met M10, elk in een eigen
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
   - het budget en de standaarden van `MediaConfig` op de metingen gezet;
   - `docs/fase-5/README.md`: wat er is, de grenzen, en wat geschrapt is (Keuze 50);
   - de vinkjes van fase 5 in het stappenplan, met de reden bij guessit, inotify, TVDB, hls.js,
     `device` en "3 transcodes"; B39 en verder definitief na de antwoorden van Jonas;
   - versie 0.7.0 in `pyproject.toml`, `app/__init__.py` en `package.json`.
5. **Op de LXC** (Jonas, ±30 minuten, volgens het runbook): `media-status`, `bench`, de
   belastingtest met de knop, en een film op telefoon en laptop. De uitkomst gaat in
   `metingen.md`.

**Eerlijk over de tijd.** M0 ±5 dagen, de golf ±8, de integratie ±3: samen ±16 werkdagen. Met ±4
dagen marge voor wat de spike en de LXC laten zien: **±20 werkdagen**. Het stappenplan geeft 5 weken
(25 dagen). Dat past. De marge is nodig: S2 (de commando's op 7.1) of S3 (fd's tussen containers)
kan anders uitvallen.

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
| Een lek in ffmpeg via een kwaadaardig bestand | laag tot middel | hoog | Keuze 11 en 12: geen netwerk, geen data, alleen lezen, geen rechten, `protocol_whitelist fd,pipe`, `format_whitelist`, een argv uit vaste lijsten; ffmpeg uit Debian met beveiligingsupdates via het image |
| Een gekraakte ffmpeg leest via `/proc/<pid>/fd` een film van een andere job van dat moment | laag | middel | Keuze 11, "wat overblijft": geen database, geheimen of netwerk; hooguit 6 jobs tegelijk; per job een uid of container is te zwaar; staat in het runbook |
| fd's tussen containers werken niet op de LXC, of het benoemde volume krijgt de verkeerde eigenaar | middel | middel | J1 werkte in de sandbox; S3 met het echte image; de terugval in drie stappen (§3.7); `install.sh` stopt nooit |
| ffmpeg 7.1.5 gedraagt zich anders dan 6.1.1 (`tfdt`, `frag_discont`, `force_key_frames`, `fd:`) | middel | middel | S1 en S2 in het image; de selftest is een harde poort in CI; de argv als golden files; de terugval "de knipper zet `tfdt`" |
| De CPU van Jonas is trager dan de sandbox | middel | middel | `bench` en de factor `f`; de toelating valt een trede terug; de belastingtest op de LXC; Vraag 2 en 3 |
| OnlyOffice en drie transcodes tegelijk: haperen | middel | middel | het budget 3,0 laat één core over; de reserve in de toelating; de tegendruk; `cpus: 3.0` op de worker; Md meet het samen met OnlyOffice |
| Het rooster van 2 s geeft meer verzoeken (één per 2 s per kijker) en een playlist van ±70 KB | hoog | laag | de sessie staat in het geheugen; geen database per segment; Mj meet de app; gzip op de playlist |
| Een keyframe valt na een herstart één frame anders (23,976 fps) | middel | laag | S2 met `fps23976.mkv`; MSE overschrijft een frame, de speler springt over een gat van één frame |
| Een gat in de audio na een herstart (32–54 ms, D4) | hoog | laag | de speler springt over gaten tot 0,5 s; S4 |
| De init verandert na een herstart, en native HLS raakt in de war | laag tot middel | middel | de vergelijking van de `stsd` (S5); bij native HLS een nieuwe sessie op die plek (410); `/beheer` telt het |
| Safari, iOS, webOS en Firefox zijn niet getest | hoog | middel | het profiel komt uit de browser zelf; basisprofielen met vlaggen per toestel; de playlists volgen RFC 8216 en de regels van Apple en LG, met een eigen lint; echte toestellen in fase 9 |
| De codecs van Chrome for Testing 156 in CI zijn anders dan verwacht | middel | laag | tests met H.264 slaan over met een reden; VP9, AV1 en Opus dekken de speler |
| Tone-mapping ontbreekt in het image (geen libzimg) | laag | middel | S1; dan 422 `media_hdr` met "zonder kleurcorrectie" (Keuze 27) |
| Lange streams houden de databaseverbindingen vast | was hoog | hoog | F1; regel 8; `test_download_pool.py` |
| Een update midden in een film | middel | laag | F3; de speler vraagt bij 410 een nieuwe sessie; `media-status --sessions` waarschuwt vooraf |
| Een gestopte sessie start vanzelf opnieuw | middel | middel | een grafsteen: een sessie die de app sloot, geeft 30 minuten 403 met de reden, niet 410 (§3.3) |
| Terug naar 0.6.0 | laag | hoog | `db-downgrade 0007`; het oude script haalt de container weg; getest in de installatietest |
| De segmentcache loopt de pool vol | laag | middel | `cache_gb` 20; opruimen elke 5 minuten; geen nieuwe transcode onder 10 GB vrij; de melding |
| De analyse op de achtergrond houdt de rij bezet, of leest de schijf vol | middel | middel | een eigen rij `media` met 1 worker; `Defer` als er 2 transcodes lopen; `nice` en `ionice`; Cues en `stss` eerst |
| Een import van honderden GB duurt lang, of vraagt twee keer de ruimte | middel | middel | een eigen rij `import`; per bestand hervatbaar; verplaatsen binnen één dataset kost geen ruimte; Vraag 1 |
| Ondertitels van een groot bestand op verzoek duren minuten | hoog | laag | voor bibliotheken alvast op de achtergrond; anders "wordt voorbereid" en 202 |
| De TMDB-sleutel lekt | laag | middel | versleuteld, nooit terug, nooit gelogd; `test_media_secrets.py`; de sleutel kan alleen lezen |
| De voorwaarden van TMDB (bronvermelding, logo, niet-commercieel) | laag | laag | de zin en een link; het logo alleen als het mag; Vraag 4 |
| MusicBrainz blokkeert ons (te snel, geen User-Agent) | laag | laag | 1 per seconde voor het hele proces; de User-Agent met de URL van het repo; standaard uit |
| SSRF via een omleiding of een DNS-truc van een provider | laag | middel | alleen vaste hosts; omleidingen alleen naar de lijst; geen privé-adres na DNS (Keuze 39) |
| De nep-providers wijken af van de echte | middel | middel | gebouwd naar de documentatie; `provider-shapes.json` met de echte vormen; `provider_real` met de hand; alles achter één adapter |
| De parser kiest een verkeerde titel | middel | laag | de testset van ≥ 500; NFO en id's in de naam gaan voor; "Weinig zeker" op `/beheer`; corrigeren met `locked` |
| Gapless klopt niet voor MP3 of AAC zonder `iTunSMPB` | middel | laag | eerlijk in de README; FLAC en Opus zijn bewezen (S8, e2e 8) |
| Gapless meten werkt headless niet (AudioWorklet achter MSE) | middel | laag | de terugval op `buffered` en de grenzen van `appendWindow`, met een eerlijke melding |
| De PGS-fixture via de eigen `.sup`-schrijver werkt niet | middel | laag | S9; anders schuift inbranden (§5) |
| ManagedMediaSource op de iPhone werkt anders, of iemand mist AirPlay | middel | laag | native HLS als terugval; AirPlay valt alleen weg bij MMS |
| `RLIMIT_FSIZE` of `-threads 4` hindert ffmpeg | middel | laag | S10; zonder die grens zijn de bewaking van de RSS en de grens van de container genoeg |
| Testen raken de grenzen van de sandbox (3 ffmpeg, 8 GB, 3 GB scratch) | laag | middel | `MAX_TRANSCODES=3` in het harnas; `ffmpeg_count` na elke test; de belastingtest stopt bij een vierde; fixtures ±60 MB, bronnen ±675 MB |
| Eén proces: sessies, toelating en de SSE-hub in het geheugen | — | — | goed voor standalone; het cluster vraagt later routering op de naam van de node en een broker (Keuze 48) |
| De planning loopt uit (±20 dagen) | middel | laag | de lijst hierboven van wat als eerste schuift; de kern schuift nooit |

---

## 6. Verwerkte en afgewezen kritiek

Revisie 2 vult deze sectie.
