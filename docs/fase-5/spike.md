# Spike M0: ffmpeg 7.1.5 in het image, en de sandbox

Datum: 28 oktober 2026. Volgens §3.7 van `docs/fase-5/bouwontwerp.md` (revisie 2). De stappen
"in het image" (S1, S2, S3, S5, S7, S10) draaiden in de CI-job `media-spike` op het echte image.
De stappen "in de sandbox" (S4, S6, S8, S9, S11, S12) draaiden met ffmpeg 6.1.1 en Chromium 141.
S13 draait later, in de CI-job `e2e-media-perf`. M0-los schreef dit verslag uit de uitkomsten, en
mat één punt na (het rooster bij een late videostart, zie "Extra").

**Kort.** Geen enkele stap heeft een terugval van §3.7 nodig. `compose.media.yaml` blijft zoals
Keuze 11, de tone-mapping van Keuze 27 is er, inbranden blijft in fase 5, en de commando's van
§3.3 blijven in de kern gelijk. Wel wijkt een aantal dingen af van wat het ontwerp aanneemt. De
belangrijkste:

1. **Een fd uit `SCM_RIGHTS` deelt zijn positie** met de app en met elk kind. Na ffprobe staat
   hij op 65536, en ffmpeg leest `fd:` vanaf daar (rc 183). De worker zet de positie vóór elke
   exec op 0, start nooit twee kinderen tegelijk op één fd, en de app opent de blob per opdracht
   opnieuw (S3).
2. **`lavfi` staat in ffmpeg 7.1 onder `-devices`, niet onder `-demuxers`.** `caps` en de
   zelftest zoeken het daar. `lavfi` komt nooit in de witte lijst van een invoer-fd (S1).
3. **Het aantal segmenten is niet `ceil(D/2)`**: in 0 van de 10 runs klopte dat. N komt uit de
   tijd van het laatste videobeeld; een fragment voorbij N hoort bij het laatste segment (S2).
4. **Het rooster van 2 s hangt aan het eerste videobeeld**, niet aan mediatijd 0. Een herstart
   op segment k gebruikt `-ss (origin + 2k)`, met `origin` = start van het videospoor − S
   (nagemeten door M0-los; S2 en S6 zagen het als "brontijd 2k").
5. **MPEG-TS zoekt niet precies**: na `-ss` begint de uitvoer op de volgende keyframe van de
   bron (1,02 s te laat). Een herstart mist tot één GOP (S2). M4 kiest de aanpak.
6. **Chromium springt niet zelf over een gat van 54 ms** (≥ 10 s stilstand). `mse.ts` springt na
   250 ms (S4).
7. **Opus in fMP4 is niet gapless** (+631 en +619 samples per overgang). Opus gaat via
   `remux_audio -c:a flac`, met delay 0. MP3 is exact met delay = `enc_delay`, zonder +529 (S8).
8. **Inbranden na een herstart** mist een ondertitel die vóór de keyframe begon. De ondertitels
   komen uit een tweede fd van dezelfde blob, die 30 s eerder begint (S9).
9. **`d_codec`** voor VP9 is 0,18 en voor AV1 0,25 (8 bit). **VP9 met HDR** haalt op 1080 geen
   realtime: hooguit 720 (S11).
10. **0,74 is geen referentie voor de bench.** Het is de tabelprijs van een echte bron; de bench
    (testsrc2 met ruis) kost ±2 keer zoveel. M1 meet de referentie van de bench zelf (S7).

---

## Opzet

**Het image (CI).** De job `media-spike` in `.github/workflows/ci.yml` bouwt `platform:ci` zoals
de job `images`, en een afgeleid image met de drie regels van Keuze 47 voor S3. Dan:

```sh
python3 -I api/tests/media/spike/ci.py --uit "$RUNNER_TEMP/spike/spike.json" \
  --image platform:ci --worker-image platform-spike:ci
```

Elke container draait onder `--cpus 3` (cgroup v2, `cpu.max` 300000 100000; S3 las dat in de
worker). ffmpeg is **7.1.5-0+deb13u1** (Debian 13).

- Run 40720340185 op commit `3d43cbb`: het artefact `spike` (`spike.json`, artefact-id
  12831155552). De cijfers hieronder komen daaruit, tenzij er "f713169" staat.
- Run 40723309787 (job 122939771057) op commit `f713169`: dezelfde stappen, met de afwijkingen kort
  aan het eind van het log. Dezelfde uitkomsten; de cijfers van S7 en S10 staan erbij.

**Het image lokaal.** `taskset -c 0-2 python3 -I api/tests/media/spike/spike.py --serieel` op de
host, met ffmpeg 6.1.1-3ubuntu5: 69 s, rc 0, geen crash. S3 met een stand-in (`/bin/sleep` als
ffmpeg): `ci.py --stappen S3 --standin` met Docker 29.9.1 en Compose 5.6.0, 15 van 15 controles
goed. Geen containers of volumes achtergelaten.

**De sandbox.** ffmpeg 6.1.1 (Ubuntu), Python 3.13, Chromium 141.0.7390.37 (Playwright,
headless, `--no-proxy-server --autoplay-policy=no-user-gesture-required`), 4 cores (Xeon 2,1 GHz).

- Metingen onder `taskset -c 0-2` (regel 14). CPU-tijd is die van het kind (`wait4`).
- Nooit meer dan 2 ffmpeg tegelijk; in de praktijk één.
- Elke meting 3 keer; het getal is de mediaan, tenzij er "per run" staat.
- De bron ging steeds via een fd (`-f <demuxer> -fd N -i fd:`) en de uitvoer naar `pipe:1`, met
  de argv van §3.3.
- Processen gestopt op pid.

De scripts van het image staan in `api/tests/media/spike/`. De prototypes van de sandbox
(`fmp4cut.py`, `ffcmd.py`, `s4_*.py`, `s8_*.py`, `supwrite.py`, `s9_*.py`, `s11_vp9.py`,
`probe_*.py`) staan niet in het repo; zie "Bestanden".

## Overzicht

| Stap | Waar | Uitkomst | Besluit | Terugval van §3.7 |
|---|---|---|---|---|
| S1 | CI | alles uit §3.3 is er; `lavfi` alleen onder `-devices` | Keuze 27 zoals ontworpen; de lijst van de zelftest; `lavfi` in `-devices` | niet nodig |
| S2 | CI | 60 van 64 zoals verwacht; N ≠ `ceil(D/2)`; TS zoekt naar de volgende keyframe | de commando's blijven; N uit het laatste videobeeld; TS: M4 kiest | niet nodig |
| S3 | CI | 17 van 17 controles goed; de fd deelt zijn positie | `compose.media.yaml` zoals Keuze 11; `lseek` vóór elke exec | niet nodig |
| S4 | sandbox | MSE werkt, ook met een andere `stsd`; Chromium springt niet over 54 ms | één SourceBuffer; een springer na 250 ms | niet nodig |
| S5 | CI | alleen `stsd` telt; geen `elst` in de init | de vergelijking van Keuze 22 is de `stsd` per spoor | — |
| S6 | sandbox | 0,007 core voor 3 × 6 Mbit/s; tegendruk zet ffmpeg stil | geen `-readrate` | — |
| S7 | CI | spreiding f 0,7 %, f_dec 1,9 % | bruikbaar, één run per meting; M1 meet de referentie van de bench | — |
| S8 | sandbox | FLAC exact; Opus in fMP4 niet; MP3 exact met `enc_delay` | Opus via `-c:a flac`; MP3 zonder +529 | niet nodig |
| S9 | sandbox | PGS en VobSub branden in; na een herstart mist een ondertitel | inbranden blijft; ondertitels uit een tweede fd, 30 s eerder | niet nodig |
| S10 | CI | alle grenzen werken; `-threads 4` kost niets extra | `RLIMIT_FSIZE` 0 aan; 1.200 MB blijft; `-threads 4` blijft | niet nodig |
| S11 | sandbox | VP9 0,82–1,05 × x264; VP9 HDR 1080 0,99× | VP9 = 1,0 × x264; `d_codec` VP9 0,18, AV1 0,25; VP9 + HDR hooguit 720 | — |
| S12 | sandbox | 16 gepauzeerde streams: 500 na 30 s; `HEAD` 404 | F1 en F2 zoals §3.5 | — |
| S13 | CI, later | nog niet gedraaid | `MEDIA_REQUIRE_H264` blijft uit tot `e2e-media-perf` het meet | — |

## S1: ffmpeg 7.1.5 in het image

**Wat er draaide.** `stap1.py` in `platform:ci`: `ffmpeg -version`, `-encoders`, `-filters`,
`-protocols`, `-demuxers`, `-muxers`, de opties van §3.3 (`ffmpeg -h full`), een echte keten
PQ en HLG naar SDR (zscale + tonemap), `bwdif`, `nice` en `ionice`, en een weigering door
`-format_whitelist` en `-protocol_whitelist`.

**Gemeten.**
- Versie `7.1.5-0+deb13u1`.
- De encoders `libx264`, `aac`, `libopus`, `libvpx-vp9`, `mjpeg`, `libx265` en `flac`; de filters
  `zscale`, `tonemap`, `bwdif`, `overlay` en `tile`; de protocollen `fd` en `pipe`: allemaal er.
- Tone-mapping PQ en HLG draait echt. `nice` en `ionice` werken.
- De weigering door de witte lijsten werkt.
- **Anders:** van de verwachte demuxers `mov, matroska, avi, wav, flv, ogg, asf, mpegts, mpeg,
  mp3, flac, aac, lavfi, hevc` ontbreekt `lavfi` in `ffmpeg -demuxers`. `lavfi` is een
  invoerapparaat van libavdevice. Het werkt wel (de bronnen en `bench` in dezelfde job zijn met
  `-f lavfi` gemaakt), maar 7.1 noemt het alleen onder `ffmpeg -devices`.
- Lokaal op 6.1.1: alleen de versie anders.

**Besluit.** Keuze 27 zoals ontworpen (zscale + tonemap). De zelftest eist de encoders, filters en
protocollen hierboven, en een weigering door `-format_whitelist` en `-protocol_whitelist`. `caps`
en de zelftest zoeken `lavfi` in `ffmpeg -devices`, de andere demuxers in `-demuxers`. `lavfi`
staat nooit in de witte lijst van een invoer-fd: alleen `synth` en `bench` gebruiken het, zonder
invoer-fd. De lijst werkt ook op 6.1.1 (de jobs `api-media` draaien Ubuntu's 6.1).

**Terugval:** niet nodig.

## S2: fd-invoer, het rooster en de tijden

**Wat er draaide.** `stap2.py`: D1–D6 en J4 op 7.1.5 met de fixtures van de spike (`bronnen.py`),
de transcode en de remux van §3.3 (`commandos.py`), fMP4 naar een pipe, en een eigen lezer van de
boxen (`boxen.py`). Vanaf 0 en na `-ss 2k`, ook met `fps23976.mkv`, `interlaced.ts` en
`fps240.mp4`. Plus V4: `tfdt` plus de offset van het eerste sample tegen de brontijd.

**Gemeten.** 60 van de 64 controles zoals verwacht:
- het rooster van 2 s, precies één IDR per fragment, en elk fragment begint met NAL-type 5;
- `fps240.mp4` wordt 60 fps;
- V4: `tfdt` + cto = brontijd − S + 1 s, binnen één frame, `trun` versie 1 (H.264 met B-frames
  bij transcode, HEVC bij remux);
- remux: één fragment per GOP;
- tegendruk (D6): ffmpeg staat stil als de app niet leest;
- zoeken via de fd in mkv (met Cues) en in mp4;
- het eerste fragment kwam na 0,07 tot 0,64 s.

Anders:

| Controle | Verwacht | Gemeten |
|---|---|---|
| N = `ceil(D/2)` (Keuze 22) | het aantal fragmenten | 0 van 10 runs gelijk; bv. `h264_bframes` vanaf 0: 6 in plaats van 7 (D 12,021 s, het laatste fragment 48 beelden) |
| `interlaced.ts` vanaf 6 s | fragment j begint op 7 + 2j s | 3 fragmenten, eerste `tfdt` 8,0213 s: elk 1,0213 s te laat |
| `interlaced.ts`, herstart (D4) | dezelfde videotijden als vanaf 0 | 0 gelijk, verschil −1,0 s; audio na de sprong 26,7 / 986,7 / 1008,0 ms |
| D1, zoeken via de fd in `interlaced.ts` | begint op het doel | 1,0213 s na het doel; geen Cues, S 1,458667, keyframes (mediatijd) 0,021, 1,421, 2,821, 4,221, … |

**Besluit.**
- De commando's van §3.3 blijven zoals ze er staan.
- N komt uit de tijd van het laatste videobeeld, niet uit `ceil(D/2)` met de duur van het
  formaat. Het laatste segment sluit bij het eind van de producent. Een fragment voorbij N
  (`-fpsmax` vult het eind aan) hoort bij het laatste segment. M4 bouwt het; het contract is
  daarop gemaakt (Keuze 22, `SegmentGrid`).
- MPEG-TS heeft geen index. Na `-ss` begint de uitvoer pas op de volgende keyframe van de bron,
  dus een herstart mist tot één GOP. Het contract gaat voor TS niet uit van exact zoeken; M4
  kiest de aanpak.

**Terugval:** niet nodig (`tfdt` en het rooster gedragen zich zoals verwacht).

## S3: fd's tussen containers

**Wat er draaide.** `s3_app.py` (als `platform`, 10001) en `s3_worker.py` (als `media`, 10002)
in `platform-spike:ci`, met `s3.compose.yaml` zoals Keuze 11: het benoemde volume `media-run` op
`/run/media`, `network_mode: none`, `read_only`, tmpfs op `/data` en `/tmp`, `cap_drop: ALL`,
`no-new-privileges`, `pids_limit` 256, 3 cores en 3 GB.

**Gemeten.** Alle 17 controles zoals verwacht:
- de worker start met de afscherming, als uid 10002 `media`;
- `docker inspect`: `NetworkMode` none, `ReadonlyRootfs`, `CapDrop` ALL,
  `no-new-privileges:true`, `PidsLimit` 256, tmpfs `/data: size=1m,mode=0700,uid=10002,gid=10002`
  en `/tmp: size=64m,mode=1777`, 3 cores, 3 GB;
- in de worker: alleen `lo` (verbinden: `ENETUNREACH`), `/` geeft `EROFS`, `/data` is leeg,
  `CapEff` 0, `NoNewPrivs` 1, Seccomp 2, `cpu.max` 300000 100000;
- het volume is 2750 `media:platform`, de socket 0660 `platform`;
- de app verbindt, maar kan niets maken in `/run/media` (`EACCES`);
- de fd komt over `SCM_RIGHTS` aan; de hash van een stuk midden in de blob klopt;
- schrijven op de fd faalt (`EBADF`); opnieuw openen via `/proc/self/fd/N` faalt, en de blob
  bleef gelijk;
- niet dumpbaar: een kind kan `/proc/<worker>/fd` niet openen; ook met dumpbaar 1 opent een kind
  de blob niet (0600 van de app);
- ffmpeg en ffprobe zijn 0711 van root; de worker kan ze niet lezen;
- een buur ziet `/proc/<ffmpeg>/fd` niet, wel `status` (VmRSS);
- de pipe is van de app (10001);
- ffprobe leest de fd in de worker (`matroska,webm`, `start_time` −0,021, h264 en aac);
- de remux in de worker leest de fd en schrijft fMP4 naar de pipe van de app (0,077 s CPU).

**Bevinding.** Een fd uit `SCM_RIGHTS` deelt zijn positie (de file offset) met de app en met elk
kind. Na ffprobe stond hij op 65536. ffmpeg las `fd:` vanaf daar en stopte met rc 183 ("EBML
header parsing failed"). Na `lseek(fd, 0)` rc 0. Lokaal (6.1.1, als root) precies zo. Uit de
proeven kwamen nog drie regels:
- `socket.recv_fds` zet geen CLOEXEC: ontvang met `recvmsg` en `MSG_CMSG_CLOEXEC`;
- `dup2` van de bron naar fd 3 alleen in het kind, nooit in de asyncio-ouder: daar was fd 3 de
  epoll van de event loop (`EBADF`, S6);
- een gepauzeerde asyncio-transport krijgt na een kill geen EOF: stopt de app een producent, dan
  sluit de app de pipe zelf (S6).

**Besluit.** `compose.media.yaml` zoals Keuze 11, en de zelftest zoals Keuze 11. De worker zet de
positie vóór elke exec op 0 (`os.lseek(fd, 0, SEEK_SET)`), start nooit twee kinderen tegelijk op
dezelfde fd, en de app opent de blob per opdracht opnieuw (M0: `wire.py` en de worker).

**Terugval:** niet nodig (geen bindkoppeling, geen pad in plaats van een fd).

## S4: MSE in Chromium 141

**Wat er draaide.**

```
python3 server.py 18931                    # statisch, met Range
python3 s4_make.py                         # bron, drie runs door de knipper, plan.json
cd api && uv run --with playwright python <spike>/s4_run.py 18931    # 8 proeven × 3 runs
python3 s4_av.py                           # beeld tegen geluid, per piep
```

De bron: `testsrc2` 1280×720, 24 fps, 24 s, met het beeldnummer als 12 blokken bovenin, en een
piep van 1 kHz op elke k + 0,5 s; H.264 + AAC, S = −0,021. De transcode van §3.3 met VP9 (de
Chromium van Playwright heeft geen H.264), door de knipper (rooster 2 s): run A vanaf 0, run B
vanaf `-ss 10`, run C vanaf `-ss 16` op 480p (een andere `stsd`), en een gat van 54 ms. Eén
MediaSource, één SourceBuffer, `timestampOffset = −1`. MIME `video/mp4;
codecs="vp09.00.31.08,opus"`.

**Gemeten (3 runs).**
- `grid`: speelt tot het eind, eerste `playing` na 240–270 ms, 0 verloren beelden.
- Beeld: `mediaTime` tegen het beeldnummer hooguit 0,01 ms verschil.
- Beeld tegen geluid: 15–45 ms, even veel als direct afspelen van dezelfde bytes (23–29 ms). Dat
  is de vaste vertraging van de headless Chromium.
- `init_same` (de init van B is byte voor byte die van A): geen stilstand.
- `init_diff` (init C, 480p, `vp09.00.30.08`): op 16,0 s wordt het beeld 854×480, zonder
  stilstand of fout, in dezelfde SourceBuffer.
- Een natuurlijke herstart gaf overlap (39,8 ms), geen gat. Het gat van 54 ms is gemaakt.
- `gap_nojump`: `waiting` op 10,027 s en dan **≥ 10 s stilstand** (3 van 3). Chromium springt
  niet zelf.
- `gap_jump250`: de springer zet `currentTime` op het begin van het volgende stuk na 250 ms:
  stilstand 260 ms (3 van 3). Na 1000 ms werkt ook, maar is een seconde stilstand.
- `<track>` met VTT: de cues gaan 1–8 ms na hun tijd af.

**Besluit voor `mse.ts`.** Eén SourceBuffer met gemuxte segmenten, `timestampOffset = −1` vóór de
eerste append. Een nieuwe init alleen als de generatie wisselt; een andere `stsd` werkt in
dezelfde SourceBuffer. **Een springer is nodig:** bij `waiting`, als het begin van het volgende
gebufferde stuk binnen 0,5 s ligt, na 250 ms daarheen. `<track>` werkt naast MSE.

**Terugval:** niet nodig (bij een andere `stsd` geen nieuwe sessie).

**Niet te testen hier:** `trun` versie 1 met negatieve offsets bij B-frames in de browser. VP9
heeft geen B-frames, en deze Chromium heeft geen H.264.

## S5: de verschillen tussen twee inits

**Wat er draaide.** `stap5.py`: twee runs van dezelfde transcode (vanaf 0 en na een herstart),
de boxen van de init vergeleken, plus een controle met 640×360.

**Gemeten.** De `stsd` per spoor is gelijk na elke herstart. Er staat geen `elst` in de init. De
controle met 640×360 heeft een andere `stsd`, en de vergelijking ziet dat.

**Besluit.** De vergelijking van Keuze 22 is de `stsd` per spoor. `mvhd`, `tkhd` en `mdhd` tellen
niet; `elst` is er niet.

## S6: de knipper in Python

**Wat er draaide.**

```
python3 mk_live6.py      # de transcode van §3.3, 1080 veryfast, 61 s, 5,0 Mbit/s
python3 s6_cut.py idle|live3|remux|remux4k|remux_bp|remux_rr|bp_first|rr_first   # elk 3 keer
```

`fmp4cut.py` (alleen de standaardbibliotheek) leest de init en elke `moof`+`mdat`, deelt
fragmenten in op het rooster of op keyframe-grenzen, en schrijft `.<k>.m4s.tmp` en dan
`os.replace`. `live3`: drie feeders op 6,19 Mbit/s, één knipper. De remux van §3.3 zonder rem op
41,6 MB (H.264 1080p) en 119,5 MB (HEVC 4K).

**Gemeten (mediaan van 3).**

| Scenario | Uitkomst |
|---|---|
| `live3` (3 × 6,19 Mbit/s, 49,7 s) | knipper 1,10 s CPU, **≈ 0,007 core netto**; 90 segmenten; vertraging van de event loop p99 1,7 ms |
| `remux` (41,6 MB) | 0,25 s; knipper **1,2 ms CPU per MB**; ffmpeg 0,27 s CPU |
| `remux4k` (119,5 MB) | 0,30 s; 1,17 ms per MB; p99 3,6 ms |
| `remux_bp` (tegendruk 60 s) | na 0,3 s 60 s voor; daarna staat ffmpeg stil in `write` |
| eerste segment met tegendruk | **0,10 s** |
| eerste segment met `-readrate 1` | **7,46 s** |

**Besluit.** Geen `-readrate`: de tegendruk is genoeg, en `-readrate 1` levert het eerste
remux-segment pas na zijn hele lengte.

## S7: bench drie keer onder 3 cores

**Wat er draaide.** `stap7.py`: drie keer de meting van §3.3 (10 s `testsrc2` met ruis, 1080 naar
720 veryfast) en het decoderen van 4 s HEVC10, onder `--cpus 3`.

**Gemeten.**

| | f | f_dec |
|---|---|---|
| CI, `3d43cbb` | 2,107 / 2,099 / 2,093 (spreiding 0,7 %) | 0,839 / 0,842 / 0,855 (1,9 %) |
| CI, `f713169` | 1,697 / 1,701 / 1,719 | 0,848 / 0,829 / 0,819 |
| lokaal, 6.1.1, 3 cores | 1,78 / 1,80 / 1,89 (5,9 %) | 0,98 / 0,93 / 0,89 (10,4 %, met een Chrome ernaast) |

f deelt door 0,74 (`REF_BENCH`), f_dec door 0,31. De bench kostte op de runner dus ±1,55 cores
(1,559 / 1,553 / 1,549), lokaal 1,3–1,5.

**Besluit.** De factoren zijn bruikbaar; één run per meting is genoeg. Maar 0,74 is in de tabel
van Keuze 26 de prijs van 720 veryfast van een **echte** bron, niet van deze bench (`testsrc2`
met ruis): f ≈ 2 waar het 1 hoort te zijn. M1 meet de referentie van de bench zelf, in de
sandbox. M0 legt alleen het contract vast (`bench` geeft cores voor 1,0×).

## S8: gapless

**Wat er draaide.**

```
python3 s8_make.py         # signaal, aanloop + 3 nummers per codec, remux_audio, audio_info
cd api && uv run --with playwright --with numpy python <spike>/s8_run.py 18931 flac opus mp3
cd api && uv run --with playwright --with numpy python <spike>/s8_run.py 18931 opus=0 mp3=576
python3 s8_alt.py          # Opus → FLAC (remux_audio -c:a flac) en Opus → WebM
python3 s8_cost.py         # de prijs van Opus → FLAC
```

Ruis met een vaste seed, zodat elk venster van 2048 samples zijn eigen verschuiving geeft. Per
codec een aanloop van 1 s en drie nummers met een lengte die geen veelvoud van een frame is.
`remux_audio` letterlijk uit §3.3. `s8.html` doet wat `music/gapless.ts` gaat doen (één
SourceBuffer, `timestampOffset`, `appendWindowStart` en `appendWindowEnd`).

**Gemeten (3 runs, elke run gelijk).** Sprong in samples bij aanloop→1 / 1→2 / 2→3:

| Variant | Sprong | Oordeel |
|---|---|---|
| FLAC, delay 0 | 0 / 0 / 0 | **exact** |
| Opus fMP4 `-c:a copy`, delay 312 (`pre_skip`) | −312 / +631 / +619 | niet gapless |
| Opus fMP4 `-c:a copy`, delay 0 | 0 / +631 / +619 | niet gapless |
| **Opus → FLAC** (`-c:a flac`), delay 0 | 0 / 0 / 0 | **exact** |
| Opus → WebM `-c:a copy`, delay 0 | 0 / −48 / −48 | bijna (1 ms overlap) |
| MP3 `audio/mpeg`, delay 1105 (576 + 529) | −529 / 0 / 0 | 529 te veel weg |
| **MP3 `audio/mpeg`, delay 576** (`enc_delay`) | 0 / 0 / 0 | **exact** |

Chromium haalt de `pre_skip` zelf weg, en speelt de opvulling van het laatste Opus-frame (631
samples) ondanks `appendWindowEnd`. De prijs van Opus → FLAC voor een nummer van 4 min: 1,80 s CPU
tegen 0,41 s, en 23,5 MB tegen 2,65 MB (8,9 ×).

**Besluit.** FLAC (en ALAC en WAV via FLAC) zoals ontworpen, delay 0. **Opus via `remux_audio`
met `-c:a flac`**, delay 0, `samples` = laatste granule − `pre_skip`. **MP3: delay =
`enc_delay`**, zonder de 529 van de decoder; exact in Chromium 141, niet getest in Firefox en
Safari. "Remuxen kost minder dan 1 s per nummer" geldt niet meer voor Opus (1,8 s); met 20 s
vooruit laden is dat geen probleem.

**Terugval:** `<audio>` per nummer is niet nodig.

## S9: beeldondertitels inbranden

**Wat er draaide.**

```
python3 supwrite.py m/s9/t.sup 1920 1080 "2:5:HALLO WERELD" "9:13:ONDERTITEL 2" "16:19:EINDE."
python3 s9_pgs.py          # .sup → mkv → ffprobe → inbranden (0, -ss 10, -ss 4); VobSub idem
python3 s9_restart.py      # de herstart met de ondertitels uit een tweede fd
```

Een eigen PGS-schrijver (`supwrite.py`, later `tests/media/sup.py`), drie ondertitels in een mkv
met 25 s van een H.264-bron van 1080p. Inbranden met de transcode van §3.3 (720 veryfast):
`[0:0]scale=…,format=yuv420p[v];[0:2]scale=1280:720[s];[v][s]overlay=eof_action=pass,format=yuv420p[out]`.
De controle: per tijdstip het deel van de witte pixels van de bitmap dat in de uitvoer echt wit
is.

**Gemeten.**
- Vanaf 0 (PGS): op de 5 tijden in een ondertitel 100 % wit, tegen 42–60 % in de bron; erbuiten
  gelijk aan de bron. 17,2 s CPU voor 25 s (0,69 core), wand 7,8 s. VobSub: byte voor byte
  dezelfde uitvoer.
- Herstart `-ss 10`: goed (het zoeken landt op de keyframe van 7,917 s, het pakket van 9,0 komt
  daarna).
- **Herstart `-ss 4`: de ondertitel van 2,0 s ontbreekt op 4,5 s** (PGS en VobSub). Het zoeken
  landt op de keyframe van 3,333 s; het pakket van 2,0 s komt daarvoor.
- **De reparatie:** de ondertitels uit een tweede invoer van dezelfde blob, op een eigen fd, die
  30 s eerder begint:

  ```
  ffmpeg {invoer -ss T, fd A} {invoer -ss max(0, T − 30), fd B} -copyts
    -filter_complex "[0:0]{vf}[v];[1:2]scale=1280:720[s];[v][s]overlay=eof_action=pass,format=yuv420p[out]"
    -map [out] -map 0:1 ...
  ```

  Met `-ss 4` staat de ondertitel er op 4,5 s, is hij weg op 6,0 s, en staat de volgende er op
  10,5 s (PGS en VobSub). Met `-ss 10` ook goed. De CPU blijft gelijk.
- Twee fouten in het maken van de fixture: zonder `-copyts` kwam de `.sup` 2 s te vroeg; zonder
  `-fix_sub_duration` schrijft `dvdsub` geen einde, en blijft elke VobSub-ondertitel staan.

**Besluit.** Inbranden blijft in fase 5. De app opent de blob bij inbranden twee keer (twee fd's,
elk een eigen positie); de worker leest de ondertitels uit de tweede, vanaf `max(0, T − 30)`.
Keuze 24 volgt §3.3: eerst het beeld schalen, de ondertitel naar W×H schalen, en dan `overlay`
(getest, en goedkoper dan `overlay` vóór het schalen). De kosten: +0,1 core bij 720 (0,69 tegen
0,585); de +0,15 van Keuze 26 blijft. Voor de fixtures (M5): een `.sup` muxen met `-copyts`,
VobSub maken met `-fix_sub_duration`.

**Terugval:** "inbranden schuift" is niet nodig.

## S10: de grenzen vóór exec

**Wat er draaide.** `stap10.py`: de transcode naar 1080 veryfast (8 s) onder `RLIMIT_CORE` 0,
`RLIMIT_NOFILE` 256, `RLIMIT_FSIZE` 0 en `RLIMIT_DATA` 1.258.291.200 (1.200 MB), met
`-max_alloc` en `-max_pixels`, met en zonder `-threads 4`. Plus twee controles die moeten bijten.

**Gemeten.**

| | `3d43cbb` | `f713169` |
|---|---|---|
| `-threads 4`, alle grenzen | rc 0, 4 fragmenten, 0,744 cores voor 1,0×, RSS 391,5 MB | rc 0, 0,614 cores, RSS 399,6 MB |
| standaard (zonder `-threads`) | rc 0, 0,743 cores, RSS 428,3 MB | rc 0, 0,613 cores, RSS 437,4 MB |
| verhouding CPU | 1,001 | 1,002 |
| controle: `RLIMIT_FSIZE` 0 en een bestand schrijven | signaal 25 (SIGXFSZ), 0 bytes | bijt (zoals verwacht) |
| controle: `RLIMIT_DATA` 64 MB | rc 245 (de decoder opent niet) | bijt (zoals verwacht) |

Lokaal (6.1.1): rc 0, RSS 335 MB, `-threads 4` 1,06 × (eerdere runs 1,05–1,22: de meting ruist).

**Besluit.** `RLIMIT_FSIZE` 0 gaat aan. `RLIMIT_DATA` 1.200 MB voor live blijft. `-threads 4`
blijft de bovengrens per proces (Keuze 12).

**Terugval:** niet nodig.

## S11: VP9 realtime en decoderen

**Wat er draaide.**

```
python3 s11_vp9.py sdr      # H.264 1080p24, 5,5 Mbit/s, 30 s → 1080/720/480
python3 s11_vp9.py hdr      # HEVC10 PQ 1080p24, 20 s, met tone-mapping
python3 s11_vp9.py hdrsdr   # dezelfde HEVC10-bron zonder tone-mapping
python3 s11_vp9.py fps      # dezelfde 480 beelden op 24 en op 30 fps → 720
python3 s11_vp9.py dec      # alleen decoderen, 1080p24, 20 s: H.264, VP9, AV1, HEVC10
```

De argv van §3.3, onder `taskset -c 0-2`, nooit met een browser of een andere ffmpeg ernaast.
Cores voor 1,0× = CPU-tijd van ffmpeg / mediaduur.

**Gemeten (cores voor 1,0×, mediaan van 3; tussen haakjes × realtime).**

| Trede | SDR VP9 | SDR x264 | HDR VP9 | HDR x264 | tabel Keuze 24 (SDR / HDR) |
|---|---|---|---|---|---|
| 1080 | 0,752 (2,8×) | 0,888 (3,2×) | 2,234 (**0,99×**) | 2,183 (1,17×) | 1,42 / 2,30 |
| 720 | 0,535 (3,8×) | 0,585 (4,3×) | 1,174 (1,9×) | 1,184 (2,1×) | 0,74 / 1,21 |
| 480 | 0,429 (4,6×) | 0,428 (5,2×) | 0,746 (2,9×) | 0,754 (3,2×) | 0,56 / 0,92 |

- VP9 tegen x264: 0,82–1,05 per trede.
- 30 fps tegen 24 fps (720): × 1,20–1,23 (de regel × fps/24 geeft × 1,25).
- Decoderen (`d_codec`): H.264 0,170, HEVC10 0,315, **VP9 0,150**, **AV1 0,222** (dav1d).
- De tabel van Keuze 24 is gemeten met de ruisbron van `bench`; met echte inhoud kost x264
  0,89 / 0,59 / 0,43. De tabel is dus een bovengrens.

**Besluit.** De kostentabel rekent VP9 als 1,0 × x264. De HDR-kolom blijft, ook voor VP9. De
fps-regel blijft. `d_codec`: VP9 0,18 en AV1 0,25 (8 bit); 10 bit blijft 0,31. **VP9 met HDR
haalt op 1080 geen realtime op 3 cores**: een client zonder H.264 met een HDR-bron krijgt hooguit
720. Op 7.1.5 kan dat anders liggen; het image meet het niet.

## S12: F1 en F2 vóór de reparatie

**Wat er draaide.** Op `3d43cbb` (`api/app` gelijk aan `7c8c4d9`), elke probe met een echte
`python -m app.serve` en een eigen datamap:

```
cd api && uv run python <fase5>/probe_pool.py 16      # 3 keer, en één keer met 14 als controle
cd api && uv run python <fase5>/probe_download.py
cd api && uv run python <spike>/probe_head.py         # 3 keer
```

**Gemeten.**
- F1: 16 gepauzeerde streams van `/api/v1/download?token=…`, dan `GET /files/root`: **500 na
  30,01 s** (3 van 3). Met 14 streams: 200 in 0,02 s. De grens is de standaardpool van
  SQLAlchemy (5 + 10, wachttijd 30 s).
- F2: `HEAD` geeft **404** op alle drie de routes (de downloadlink, `/files/{id}/content` en
  `/public/download/{id}`); `GET` met `Range: bytes=0-0` geeft 206.

**Besluit.** F1 en F2 van §3.5 zijn nodig, zoals beschreven. De meting "na" hoort bij M0-api
(`test_download_pool.py` met 20 streams).

## S13: Chrome for Testing in CI

Nog niet gedraaid: S13 draait in de CI-job `e2e-media-perf` (Chrome for Testing 156), die er
later komt. Die job legt in zijn artefact de browserversie vast, en wat `isTypeSupported` zegt
voor `avc1.640028` en `mp4a.40.2`. Tot dan blijft `MEDIA_REQUIRE_H264` uit in CI. Zegt CfT 156 nee
tegen H.264 of AAC, dan blijft het uit, en wordt H.264 bewezen op de LXC (§4.7, Ma). Dat komt dan
hier bij.

## Extra: het rooster bij een late videostart (M0-los)

S6 zag de keyframes "op brontijd 2k" bij een bron met S = −0,021 en video vanaf 0. Dat is ook
"het eerste videobeeld + 2k". Om te weten welke van de twee de regel is, en dus waar een
herstart moet beginnen, mat M0-los het na met ffmpeg 6.1.1 en de transcode van §3.3 (320×240,
met `-copyts -output_ts_offset (1 − S)`):

```
ffmpeg -itsoffset 0.5 -f lavfi -i testsrc2=size=320x240:rate=24 \
  -f lavfi -i sine=frequency=440:sample_rate=48000 -map 0:v -map 1:a -t 10 \
  -c:v libx264 -preset ultrafast -g 12 -c:a aac src.mkv          # video 0,500, audio −0,021
ffprobe -select_streams v:0 -show_entries packet=dts_time,flags uit.mp4   # de keyframes
```

| Bron | Run | Keyframes (mediatijd) |
|---|---|---|
| video op 0,500, S = −0,021 (origin 0,521) | vanaf 0 | 0,521 / 2,521 / 4,521 / … |
| idem | `-ss 4` | 4,021 / 6,021 / 8,021: **naast het rooster** |
| idem | `-ss 4.5` | 4,521 / 6,521 / 8,521: op het rooster |
| video op 0, S = −0,021 (origin 0,021) | vanaf 0 | 0,021 / 2,021 / 4,021 / … |
| idem | `-ss 4` | 4,021 / 6,021 / 8,021: op het rooster |
| MPEG-TS, video 2,817, S = 2,779 (origin 0,038) | vanaf 0 | 0,05 / 2,05 / 4,05 / … (niet 2k − S) |

**Uitkomst.** `t` in `-force_key_frames` telt vanaf het eerste videobeeld van de uitvoer (zoals
J4 zei). Het rooster ligt dus op mediatijd `origin + 2k`, met `origin` = `start_time` van het
videospoor − S (altijd ≥ 0). Een herstart op segment k moet beginnen met `-ss (origin + 2k)`:
`-ss` vóór de invoer telt in mediatijd. Met `-ss 2k` schuift het rooster `origin` op, en dat valt
alleen weg als de video op S begint (meestal 0,021 s ernaast: dat is minder dan één frame).

**Besluit.** De ene referentie voor `EXTINF` en `map.json` (spike punt 12) is mediatijd, met dit
rooster: segment 0 van 0 tot `origin + 2`, segment k van `origin + 2k`, het laatste tot het eind
van het laatste videobeeld. Zie Keuze 22. `origin` komt uit de probe.

## De commando's van de worker na de spike

Dit is §3.3 na de spike. `{…}` vult de worker uit gevalideerde argumenten. Wat de spike
veranderde, staat erbij.

**Bij elk kind** (Keuze 12): een eigen procesgroep en een kale omgeving; `RLIMIT_CORE` 0,
`RLIMIT_NOFILE` 256, `RLIMIT_FSIZE` 0 (S10), `RLIMIT_DATA` 1.200 MB voor live en sprites en 512 MB
voor de korte opdrachten; `lseek(fd, 0)` op elke invoer-fd vóór de exec, en nooit twee kinderen
tegelijk op dezelfde fd (S3); de bron met `dup2` op fd 3 (en 4) alleen in het kind (S6).

Elke invoer:
```
-nostdin -hide_banner -loglevel error -progress pipe:2 -nostats -max_alloc 536870912
-protocol_whitelist fd,pipe
-format_whitelist mov,matroska,avi,wav,flv,ogg,asf,mpegts,mpeg,mp3,flac,aac
-max_pixels 35389440 -f {demuxer} [-ss {T}] -fd 3 -i fd:
```
`lavfi` en `hevc` staan nooit in deze lijst (S1).

Transcode, vanaf 0 of na een sprong naar segment k (720p, SDR, audio naar AAC):
```
nice -n 5 ffmpeg {invoer, met -ss (origin + 2k) als k > 0} -copyts
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
- `-ss (origin + 2k)`: was `-ss 2k` (Extra).
- `{L}`: 1080 4.0 (4.2 boven 30 fps), 720 3.1 (3.2 boven 30 fps), **480 3.0 (3.1 boven 25 fps)**:
  3.0 is te laag vanaf 30 fps (spike punt 16).
- HDR: `-vf` wordt de keten van Keuze 27, met `-color_primaries bt709 -color_trc bt709
  -colorspace bt709` (S1: zscale en tonemap zijn er). Interlaced: `bwdif=mode=send_frame:parity=auto:deint=interlaced` ervoor.
- Zonder H.264 bij de client: het blok van `-c:v libx264` tot en met `-level:v` wordt `-c:v
  libvpx-vp9 -deadline realtime -cpu-used 8 -row-mt 1 -b:v {R}k -maxrate {R}k -crf 32`, en
  `-forced-idr 1` valt weg; audio `-c:a libopus -b:a 128k -ac 2 -ar 48000`. VP9 met HDR hooguit
  720 (S11).
- Geen `-readrate`: de tegendruk is genoeg (S6).

Inbranden (twee invoeren van dezelfde blob, elk een eigen fd; S9):
```
nice -n 5 ffmpeg {invoer A: -ss (origin + 2k), -fd 3} {invoer B: -ss max(0, origin + 2k − 30), -fd 4} -copyts
  -filter_complex "[0:{V}]{filters}[v];[1:{N}]scale={W}:{H}[s];[v][s]overlay=eof_action=pass,format=yuv420p[out]"
  -map "[out]" [-map 0:{A}] -sn -dn -map_metadata -1 -map_chapters -1 -fpsmax 60
  {de video, de audio en de staart van de transcode}
```

Remux (video kopiëren, audio kopiëren of omzetten):
```
ffmpeg {invoer, met -ss (b_k − 0,5) als k > 0} -copyts
  -map 0:{V} [-map 0:{A}] -sn -dn -map_metadata -1 -map_chapters -1
  -c:v copy [-tag:v hvc1] [-c:a {copy | aac -ac 2 -b:a 160k | libopus -b:a 128k -ac 2 -ar 48000}]
  -output_ts_offset {1−S} -avoid_negative_ts disabled
  -f mp4 -movflags +frag_keyframe+empty_moov+default_base_moof+skip_trailer+frag_discont+negative_cts_offsets
  -fflags +bitexact pipe:1
```
Geen `-readrate` (S6). Bij MPEG-TS begint de uitvoer na `-ss` op de volgende keyframe (S2).

Ondertitels (tekst), sprites en één beeld: ongewijzigd (§3.3).

Een nummer voor gapless (Keuze 38):
```
ffmpeg {invoer} -map 0:a:0 -vn -sn -dn -map_metadata -1 -c:a {copy | flac}
  -f mp4 -movflags +frag_keyframe+empty_moov+default_base_moof -frag_duration 10000000
  -use_editlist 0 pipe:1
```
`copy` voor FLAC en AAC; **`flac` voor Opus** (S8), ALAC en WAV. MP3 gaat rauw als `audio/mpeg`.

De meting (Keuze 26), zonder invoer:
```
ffmpeg -f lavfi -i "testsrc2=size=1920x1080:rate=24,noise=alls=20:allf=t" -t 10
  -vf "scale=-2:720" -c:v libx264 -preset veryfast -threads 4 -f null -
```
`f` = cores voor 1,0× / de cores van dezelfde bench in de sandbox (M1 meet die; niet 0,74, S7).

Het decoderen van HEVC10 (`f_dec`) en de bron voor de belastingtest (`synth`): ongewijzigd.

**De zelftest** (`media-image`) eist: de encoders `libx264`, `aac`, `libopus`, `libvpx-vp9`,
`mjpeg`, `libx265`, `flac`; de filters `zscale`, `tonemap`, `bwdif`, `overlay`, `tile`; de
protocollen `fd` en `pipe`; de demuxers van de witte lijst in `ffmpeg -demuxers` en `lavfi` in
`ffmpeg -devices`; een weigering door `-format_whitelist` en `-protocol_whitelist`; en de
afscherming van Keuze 11.

## Afwijkingen van het ontwerp

1. **De positie van de fd (Keuze 12):** gedeeld met de app en elk kind. `lseek(fd, 0)` vóór elke
   exec, één kind per fd, de blob per opdracht opnieuw open. `recvmsg` met `MSG_CMSG_CLOEXEC`;
   `dup2` alleen in het kind; de app sluit de pipe zelf als hij een producent stopt.
2. **`lavfi` (Keuze 12, de zelftest):** onder `-devices`, niet onder `-demuxers`.
3. **N (Keuze 22):** uit het laatste videobeeld, niet `ceil(D/2)`; een fragment voorbij N hoort
   bij het laatste segment.
4. **Het rooster (Keuze 22, 25):** aan het eerste videobeeld (`origin`); herstarten met
   `-ss (origin + 2k)`; `EXTINF` en `map.json` in mediatijd met dat rooster.
5. **MPEG-TS (Keuze 22, 23, 25):** geen exact zoeken; een herstart mist tot één GOP. M4 kiest.
6. **De `stsd` (Keuze 22):** alleen die telt; `elst` is er niet (S5).
7. **De springer (Keuze 32):** Chromium springt niet zelf; na 250 ms, gaten tot 0,5 s.
8. **Opus (Keuze 18, 38):** via `-c:a flac`, delay 0. Remuxen kost dan 1,8 s, en de cache is
   ±9 × zo groot voor Opus.
9. **MP3 (Keuze 18, 38):** delay = `enc_delay`, zonder +529.
10. **Inbranden (Keuze 24, 29):** de ondertitels uit een tweede fd, 30 s eerder; eerst schalen,
    dan `overlay` (Keuze 24 zei het omgekeerd).
11. **Fixtures (§3.6):** een `.sup` muxen met `-copyts`; VobSub met `-fix_sub_duration`.
12. **`d_codec` (Keuze 26):** VP9 0,18 en AV1 0,25 (8 bit).
13. **VP9 met HDR (Keuze 24, 27):** hooguit 720.
14. **De referentie van `bench` (Keuze 26):** niet 0,74; M1 meet hem.
15. **`RLIMIT_FSIZE` 0 (Keuze 12):** gaat aan.
16. **Level 480 (Keuze 24):** 3.1 boven 25 fps. HEVC met open GOP geeft CRA-keyframes (Keuze 23).
17. **Geen `-readrate` (Keuze 23, 26):** de tegendruk is genoeg.
18. **"Geen ffmpeg buiten `app/media/worker/`" (regel 4):** gaat over `app/`. De spike-scripts
    onder `api/tests/` roepen ffmpeg zelf aan.

## Open punten

- **`trun` versie 1 met B-frames in een browser** is niet getest (VP9 heeft geen B-frames, de
  Chromium van Playwright geen H.264). Dat komt met S13 en `e2e-media-perf`.
- **Safari en Firefox** zijn niet getest: MP3-delay, Opus en FLAC in fMP4, de springer, `trun` v1
  via native HLS (een apparaattest in fase 9).
- **MPEG-TS zoeken:** M4 kiest de aanpak (bijvoorbeeld eerder beginnen en weggooien wat vóór de
  grens ligt, of TS altijd vanaf een keyframe uit de index).
- **De terugblik van 30 s** voor ondertitels: een ondertitel die langer dan 30 s staat, mist na
  een herstart nog steeds. Netter is de begintijd uit de stap `subs`.
- **De opvulling van Opus in fMP4** in Chromium is gemeten, niet verklaard. Wordt de cache voor
  Opus te groot: WebM (1 ms overlap), of zelf het laatste frame knippen. Dat is een nieuw contract.
- **HDR met VP9 op 7.1.5**, en de snelheid van de keten met `zscale` en `tonemap` op 7.1.5, zijn
  alleen op 6.1.1 gemeten.
- **De referentie van `bench`** en van `f_dec` meet M1 in de sandbox.
- **Het rooster bij een late videostart** is op 6.1.1 nagemeten, niet op 7.1.5. Het gedrag van
  `-force_key_frames` met `t` vanaf het eerste beeld is in beide gelijk volgens J4 en S2; S2 op
  7.1.5 zag het rooster na een herstart gelijk bij elke bron waarvan de video binnen één frame
  na S begint (alleen `interlaced.ts` week af, door het zoeken in TS).
- De keuzes van Jonas (B39–B48) staan nog open. De spike ging uit van de standaard.

## Bestanden

In het repo:
- `api/tests/media/spike/`: de stappen in het image (`spike.py`, `ci.py`, `stap1.py`, `stap2.py`,
  `stap5.py`, `stap7.py`, `stap10.py`, `s3_app.py`, `s3_worker.py`, `s3.compose.yaml`, het
  `Dockerfile` voor S3, en de gedeelde modules `commandos.py`, `boxen.py`, `bronnen.py`,
  `stroom.py`, `berichten.py` en `gedeeld.py`).
- `.github/workflows/ci.yml`: de job `media-spike`.
- Het artefact `spike` van run 40720340185 (`spike.json`), en het log van job 122939771057.

Niet in het repo (de scratch van M0-spike-sandbox; met de scripts opnieuw te maken): de knipper
`fmp4cut.py`, `ffcmd.py`, `proc.py`, `feeder.py`, `mk_live6.py`, `s6_cut.py`, `server.py`,
`s4_make.py`, `s4.html`, `s4_run.py`, `s4_av.py`, `s8_make.py`, `s8.html`, `s8_run.py`,
`s8_alt.py`, `s8_cost.py`, `supwrite.py`, `s9_pgs.py`, `s9_restart.py`, `s11_vp9.py`,
`probe_head.py`, `probe_pool.py` en `probe_download.py`, met hun uitkomsten (`out/*.json`). Grote
media zijn weggegooid.
