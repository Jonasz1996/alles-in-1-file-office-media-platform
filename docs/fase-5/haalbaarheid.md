# Fase 5: haalbaarheid (metingen bij revisie 2)

> Bij het bouwontwerp, revisie 2 ([bouwontwerp.md](bouwontwerp.md)). Gemeten op 28 oktober 2026 in
> de sandbox, om de kritiek op revisie 1 na te lopen. Er staan geen mediabestanden in het repo.
> De bronnen en de scripts staan in scratch (`fase5/`), niet in het repo.

Revisie 1 rekende op metingen uit eerdere proeven (D1–D7, N1–N23, J1–J4). De critici vonden 45
punten. Waar een punt met een meting te controleren was, heb ik dat gedaan. Dat zijn V1 tot en
met V8 hieronder. Per meting: wat de vraag was, het commando, en wat eruit kwam.

## De sandbox

| | |
|---|---|
| CPU | Intel(R) Xeon(R) Processor @ 2.10GHz (familie 6, model 207, stepping 2), 4 vCPU, 1 thread per core, onder KVM; AVX-512 en AMX |
| Geheugen | 15 GiB |
| ffmpeg | 6.1.1-3ubuntu5. Het image krijgt later 7.1.5 uit Debian 13; de spike (§3.7) meet dat opnieuw |
| Docker | 29.9.1 |
| GPU | geen |

Regels tijdens het meten: nooit meer dan 3 ffmpeg tegelijk, samen onder 8 GB, en een proces
alleen stoppen op zijn pid.

Voor de metingen op "3 cores" draait alles onder `taskset -c 0-2`. Dat is de grens die de worker
op de LXC krijgt (`cpus: 3.0`).

## De bronnen

| Bestand | Wat |
|---|---|
| `media/source.mkv` | H.264 High, 1920×1080, 24 fps, B-frames, 60 s, 5,93 Mbit/s; audio AC-3 en AAC; ondertitels SRT en ASS. Een echte opname uit de eerdere proef |
| `crit-sec/m/hevc1080.mkv` | HEVC Main 10, 1920×1080, 24 fps, PQ (HDR10), 20 s, 4,3 Mbit/s, met AAC. Van de critici |
| `crit-sec/m/hevc2160.mkv` | HEVC Main 10, 3840×2160, 24 fps, PQ (HDR10), 10 s, 15,9 Mbit/s, zonder audio. Van de critici |
| `crit-sec/m/s.sup` | een eigen PGS-bestand (1280×720) uit `supwrite.py` van de critici |

## De hulpscripts

Alle scripts staan in scratch. Ze gebruiken alleen Python en de standaardbibliotheek.

- `redteam/scripts/enc.py <mediaduur> <argv…>`: start ffmpeg, leest `pipe:1`, en telt de boxen
  bovenaan. Geeft de tijd tot de eerste volledige `mdat`, de wandtijd, de CPU-tijd van het kind
  (user plus sys) en de hoogste RSS. **Cores voor 1,0×** is de CPU-tijd gedeeld door de
  mediaduur.
- `redteam/scripts/conc2.py <mix.json> <vertragingen>`: start elk commando uit `mix.json` na zijn
  vertraging, en noteert de tijd van elke `mdat`. Haperen rekent hij als een speler die begint bij
  het eerste fragment en elke 2 s een fragment van 2 s nodig heeft.
- `crit-sec/scripts/trun.py <bestand>`: print voor de eerste twee `moof` van spoor 1 de `tfdt`,
  de versie van `trun` en de eerste offsets van de compositie.
- `crit-sec/scripts/rlimdata.py <bron> <MB>`: de transcode naar 720p via fd 3, met
  `RLIMIT_DATA` gezet vlak vóór exec.
- `crit-sec/scripts/reopen.sh` en `reopen_uid2.sh`: zie V1.
- `verify/nodump_proc.py` en `verify/nodump_self.py`: zie V2.

`$S` hieronder is het pad van de scratchmap `fase5/`.

## V1. Een blob opnieuw openen via `/proc` (SEC-1)

**Vraag.** Kan een proces met alleen een fd om te lezen, de blob via `/proc/self/fd/3` openen om te
schrijven? En maakt een andere uid verschil?

**Opzet.** Een bestand van 10001, modus 0600. In een nieuwe mount-namespace wordt de map alleen
lezen. Dan wordt het proces uid 10001 (of 10002), zonder caps en met `no_new_privs`. Het houdt
alleen fd 3, die vooraf `O_RDONLY` geopend is.

```sh
cd $S; mkdir -p verify/blobs; D=$PWD/verify/blobs
printf 'ORIGINAL-BLOB-CONTENT' > $D/blob1; chown 10001:10001 $D/blob1; chmod 600 $D/blob1; chmod 755 $D
unshare -m sh -c "exec 3<$D/blob1; sh crit-sec/scripts/reopen.sh $D"; cat $D/blob1
printf 'ORIGINAL-BLOB-CONTENT' > $D/blob1; chown 10001:10001 $D/blob1; chmod 600 $D/blob1
unshare -m sh -c "exec 3<$D/blob1; sh crit-sec/scripts/reopen_uid2.sh $D"; cat $D/blob1
```

`reopen.sh` doet `mount --bind "$D" "$D" && mount -o remount,bind,ro "$D"`, en dan
`setpriv --reuid=10001 --regid=10001 --clear-groups --inh-caps=-all --bounding-set=-all
--no-new-privs python3 -I -c …`. Daarin: eerst het pad openen met `O_WRONLY`, dan
`os.open('/proc/self/fd/3', os.O_WRONLY)` en `TAMPERED` schrijven. `reopen_uid2.sh` is gelijk,
met `--reuid=10002 --regid=10002`, en leest eerst 8 bytes via fd 3.

**Uitkomst.**

| Als | Uitvoer | De blob daarna |
|---|---|---|
| uid 10001 | `direct path open for write: Permission denied`; `reopen via /proc/self/fd/3 O_WRONLY: wrote 8 bytes` | `TAMPERED-BLOB-CONTENT` |
| uid 10002 | `read via passed fd: b'ORIGINAL'`; `reopen via /proc/self/fd/3 O_WRONLY: Permission denied` | `ORIGINAL-BLOB-CONTENT` |

**Gevolg.** De worker draait als eigen gebruiker `media` (uid 10002). Een doorgegeven fd leest
gewoon, maar opnieuw openen om te schrijven kan niet meer (Keuze 11).

## V2. Wat een buurproces ziet (SEC-1)

**Vraag.** Kan een job onder dezelfde uid de fd's van een andere job, of van de worker, openen?

**Kind van een programma met modus 0711.** Een kopie van `sleep` met modus 0711, gestart als uid
10002. Een tweede proces van uid 10002 probeert zijn `/proc`-bestanden te lezen.

```sh
cd $S; timeout 20 python3 -I verify/nodump_proc.py; rm -f /usr/local/bin/sleep_ro
```

`nodump_proc.py` kopieert `/bin/sleep` naar `/usr/local/bin/sleep_ro` (modus 0711), wordt uid
10002, start `sleep_ro 3`, en leest dan `/proc/<pid>/{status,stat,limits,io,fd}`.

| `/proc/<pid>/…` | Uitkomst |
|---|---|
| `status` | OK (`VmRSS` en `Uid` leesbaar) |
| `stat` | OK |
| `limits` | OK |
| `io` | `PermissionError` |
| `fd` | `PermissionError` |

Een programma dat je niet mag lezen, maakt het proces niet dumpbaar. Het meten van `VmRSS` werkt
nog.

**De worker zelf.** `nodump_self.py` wordt uid 10002, maakt een pipe, en laat een kind
`/proc/<worker>/fd/<w>` openen om te schrijven: eerst met `prctl(PR_SET_DUMPABLE, 1)`, dan met 0.

```sh
cd $S/verify; timeout 20 python3 -I nodump_self.py
```

| De worker | Het kind |
|---|---|
| dumpbaar (1) | `fd OPEN-OK` |
| niet dumpbaar (0) | `fd PermissionError` |

**In Docker.** De map en de socket tussen de app en de worker.

```dockerfile
FROM python:3.12-slim
RUN useradd --system --uid 10001 platform && useradd --system --uid 10002 --no-create-home media \
    && install -d -o media -g platform -m 2750 /run/media
```

```sh
docker build -q -t sockt:1 .; docker volume create sockv
docker run -d --name sockw --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --user media -v sockv:/run/media -v $PWD:/t:ro sockt:1 python -I /t/worker.py
docker run --rm --network none --user platform -v sockv:/run/media -v $PWD:/t:ro sockt:1 python -I /t/app.py
docker logs sockw; docker rm -f sockw; docker volume rm sockv; docker rmi sockt:1
```

`worker.py` zet `umask 077`, maakt `/run/media/worker.sock`, zet die op 0660 en wacht. `app.py`
verbindt, stuurt `hallo`, en probeert een bestand in `/run/media` te maken.

Uitvoer:

```
worker uid 10002 sock 0o660 10002 999 dir 0o2750 10002 999
app uid 10001 999 connected
app write in dir: Permission denied
```

In `python:3.12-slim` (Debian 13.7) geeft `useradd --system` de groep `platform` gid 999, en
`media` gid 998. Het Dockerfile noemt de groep daarom bij naam.

**Gevolg.** ffmpeg en ffprobe krijgen modus 0711 in het image, de worker zet zichzelf op niet
dumpbaar, en de app maakt elke pipe (Keuze 11, 47).

## V3. Wat decoderen en coderen kost (MED-1, R02)

**Vraag.** Hoeveel kost de bron zelf? En is VP9 echt twee keer zo duur als x264?

```sh
cd $S; E=redteam/scripts/enc.py; S1=media/source.mkv
MOV="-f mp4 -movflags +frag_keyframe+empty_moov+default_base_moof+skip_trailer+frag_discont pipe:1"
TM="zscale=w=1280:h=720:tin=smpte2084:min=bt2020nc:pin=bt2020:rin=tv:t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"

# H.264 1080p → 720, x264 en VP9 (twee keer)
python3 $E 20 ffmpeg -nostdin -loglevel error -t 20 -i $S1 -map 0:v:0 -an -vf scale=-2:720,format=yuv420p \
  -c:v libx264 -preset veryfast -crf 23 -maxrate 3500k -bufsize 7000k -threads 4 -sc_threshold 0 \
  -force_key_frames "expr:gte(t,n_forced*2)" $MOV
python3 $E 20 ffmpeg -nostdin -loglevel error -t 20 -i $S1 -map 0:v:0 -an -vf scale=-2:720,format=yuv420p \
  -c:v libvpx-vp9 -deadline realtime -cpu-used 8 -row-mt 1 -b:v 3500k -maxrate 3500k -crf 32 -threads 4 -g 48 $MOV

# HDR → 720 met tone-mapping, en alleen decoderen
python3 $E 10 ffmpeg -nostdin -loglevel error -i crit-sec/m/hevc2160.mkv -map 0:v:0 -an -vf "$TM" \
  -c:v libx264 -preset veryfast -crf 23 -maxrate 3500k -bufsize 7000k -threads 4 -sc_threshold 0 \
  -force_key_frames "expr:gte(t,n_forced*2)" $MOV
python3 $E 10 ffmpeg -nostdin -loglevel error -i crit-sec/m/hevc2160.mkv -map 0:v:0 -an -f null -
python3 $E 20 ffmpeg -nostdin -loglevel error -i crit-sec/m/hevc1080.mkv -map 0:v:0 -an -vf "$TM" \
  -c:v libx264 -preset veryfast -crf 23 -maxrate 3500k -bufsize 7000k -threads 4 -sc_threshold 0 \
  -force_key_frames "expr:gte(t,n_forced*2)" $MOV
python3 $E 20 ffmpeg -nostdin -loglevel error -i crit-sec/m/hevc1080.mkv -map 0:v:0 -an -vf "$TM" \
  -c:v libvpx-vp9 -deadline realtime -cpu-used 8 -row-mt 1 -b:v 3500k -maxrate 3500k -crf 32 -threads 4 -g 48 $MOV
python3 $E 20 ffmpeg -nostdin -loglevel error -i crit-sec/m/hevc1080.mkv -map 0:v:0 -an -f null -

# H.264 1080p24 alleen decoderen, zonder en met -threads 4
python3 $E 60 ffmpeg -nostdin -loglevel error -i $S1 -map 0:v:0 -an -f null -
python3 $E 60 ffmpeg -nostdin -loglevel error -threads 4 -i $S1 -map 0:v:0 -an -f null -
```

**Uitkomst** (4 vCPU, zonder `taskset`):

| Bron → uitvoer | Cores voor 1,0× | Verder |
|---|---|---|
| H.264 1080p24, alleen decoderen | 0,18 (0,19 met `-threads 4`) | |
| HEVC 10-bit 1080p24, alleen decoderen | 0,31 | |
| HEVC 10-bit 2160p24, alleen decoderen | 1,31 | 4,2× zoveel als 1080p |
| H.264 1080p → 720, x264 veryfast | 0,60 en 0,60 | eerste fragment na 1,16–1,23 s |
| H.264 1080p → 720, VP9 realtime | 0,59 en 0,57 | |
| HEVC10 HDR 1080p → 720 met tone-mapping, x264 veryfast | 1,35 | |
| HEVC10 HDR 1080p → 720 met tone-mapping, VP9 realtime | 1,27 | |
| HEVC10 HDR 2160p → 720 met tone-mapping, x264 veryfast | 2,28 | eerste fragment na 3,37 s; RSS 562 MB |

**Gevolg.**
- Decoderen schaalt met het aantal pixels. De kosten van een plan tellen de bron daarom apart mee
  (Keuze 26). Nagerekend: 4K HEVC → 720 geeft 1,80 tegen 1,89 gemeten door de critici.
- VP9 realtime kost hetzelfde als x264 veryfast. De toelating rekent VP9 als 1,0× x264.
- HDR naar 720 kostte hier 1,35. De eerdere proef (N17) mat 1,21 met een andere bron, en een
  criticus 1,13–1,15. De inhoud telt dus mee. Daarom meet het platform zichzelf (`bench`), en
  draait er een belastingtest op de LXC.
- Een HDR-bron groter dan 1920×1088 weigert decide (Keuze 27): 2,28 cores voor één kijker op 720
  is te veel naast anderen.

## V4. Tijden in de fragmenten (MED-2)

**Vraag.** Klopt de tijd van het eerste beeld met de movflags van revisie 1?

De critici maakten vier bestanden met het commando van §3.3 (revisie 1), via fd 3: twee keer een
transcode naar 720p x264 van een bron met B-frames, en twee keer een remux (`-c:v copy`). Steeds
zonder en met `+negative_cts_offsets` in `-movflags`. Ik las ze opnieuw uit:

```sh
cd $S/crit-sec
for f in m/tc0.mp4 m/tcneg0.mp4 m/rmx.mp4 "m/rmx+negative_cts_offsets.mp4"; do
  echo "== $f"; timeout 30 python3 scripts/trun.py "$f"; done
```

| Run | `tfdt` | `trun` | Eerste offsets |
|---|---|---|---|
| transcode, revisie 1 | 12800 (tijdschaal 12288) | v0 | 1024, 1536, 512, 1536, 512 |
| transcode, met `+negative_cts_offsets` | 12800 | v1 | 0, 512, −512, 512, −512 |
| remux, revisie 1 | 16336 (tijdschaal 16000) | v0 | 1328, 2000, 672, … |
| remux, met `+negative_cts_offsets` | 16336 | v1 | 0, 672, −656, … |

**Gevolg.** Zonder de vlag komt het eerste beeld een B-frame te laat: 1024/12288 = 83 ms na
`tfdt` (bij de transcode, samen met de rest +104 ms tegen het geluid, gemeten door de critici),
en 1328/16000 = 83 ms bij de remux. Met de vlag is de eerste offset 0. De knipper leest dus
getekende offsets (`trun` v1), en de spike controleert het op 7.1.5 (S2) en in Chromium (S4).
Safari is hier niet te testen.

## V5. Keyframes bij 240 fps (MED-3)

**Vraag.** Maakt x264 extra IDR's bij een hoge fps, en helpen `-g 100000` en `-fpsmax 60`?

```sh
cd $S/verify
ffmpeg -h full 2>/dev/null | grep -E "^\s*-fpsmax|max_alloc|max_pixels"
ffmpeg -nostdin -loglevel error -y -f lavfi -i testsrc2=size=640x360:rate=240 -t 6 -c:v libx264 -preset ultrafast fps240.mp4
for G in "" "-g 100000"; do
  ffmpeg -nostdin -loglevel error -y -i fps240.mp4 -c:v libx264 -preset veryfast -sc_threshold 0 $G \
    -force_key_frames "expr:gte(t,n_forced*2)" -forced-idr 1 -an -f mp4 \
    -movflags +frag_keyframe+empty_moov+default_base_moof+skip_trailer+frag_discont out240.mp4
  echo "[$G]"; ffprobe -v error -select_streams v:0 -show_entries packet=pts_time,flags -of csv=p=0 out240.mp4 \
    | awk -F, '$2 ~ /K/ {printf "%s ", $1} END {print ""}'
done
ffmpeg -nostdin -loglevel error -y -i fps240.mp4 -c:v libx264 -preset veryfast -fpsmax 60 -g 100000 \
  -force_key_frames "expr:gte(t,n_forced*2)" -forced-idr 1 -an -f mp4 out60.mp4
ffprobe -v error -show_entries stream=r_frame_rate,avg_frame_rate,nb_frames -of compact out60.mp4

# VP9, zonder en met -g 100000
for G in "" "-g 100000"; do
  ffmpeg -nostdin -loglevel error -y -f lavfi -i testsrc2=size=640x360:rate=24 -t 7 -c:v libvpx-vp9 \
    -deadline realtime -cpu-used 8 -row-mt 1 -b:v 1M $G -force_key_frames "expr:gte(t,n_forced*2)" -f mp4 \
    -movflags +frag_keyframe+empty_moov+default_base_moof+skip_trailer+frag_discont vp9k.mp4
  echo "vp9 [$G]"; ffprobe -v error -select_streams v:0 -show_entries packet=pts_time,flags -of csv=p=0 vp9k.mp4 \
    | awk -F, '$2 ~ /K/ {printf "%s ", $1} END {print ""}'
done
```

**Uitkomst.**
- `ffmpeg -h full` kent `-fpsmax`, `-max_alloc` en `-max_pixels` (tot `INT_MAX`).
- x264 zonder `-g`: keyframes op 0,008333, 1,05, 2,008333, 3,05, 4,008333 en 5,05 s. Elke 250
  beelden komt er een extra IDR bij.
- x264 met `-g 100000`: alleen op 0,008333, 2,008333 en 4,008333 s.
- Met `-fpsmax 60`: `r_frame_rate=60/1`, 362 beelden.
- VP9: keyframes op 0, 2, 4 en 6 s, met en zonder `-g 100000`.

**Gevolg.** `-g 100000` en `-fpsmax 60` staan in de argv van transcode (Keuze 24). Bij VP9 is
`-g 100000` niet nodig, maar het staat er voor de zekerheid ook.

## V6. Een harde geheugengrens (SEC-7)

**Vraag.** Laat `RLIMIT_DATA` ffmpeg met `-threads 4` gewoon draaien? En werken `-max_alloc` en
`-max_pixels` met 4K?

```sh
cd $S
for mb in 1200 400; do timeout 120 python3 -I crit-sec/scripts/rlimdata.py crit-sec/m/hevc2160.mkv $mb; done
ffmpeg -nostdin -loglevel error -max_alloc 536870912 -max_pixels 35389440 -i crit-sec/m/hevc2160.mkv -t 2 -f null - \
  && echo "max_alloc+max_pixels 4K: ok"
```

`rlimdata.py` draait de transcode van 4K HEVC naar 720 x264 veryfast met `-threads 4`, via fd 3
met `-protocol_whitelist fd,pipe -format_whitelist matroska`, naar `/dev/null`. `RLIMIT_DATA`
staat in `preexec_fn`.

**Uitkomst.**

| `RLIMIT_DATA` | Uitkomst |
|---|---|
| 1200 MB | rc 0, 5,3 s, hoogste RSS 520 MB |
| 400 MB | rc 187 na 0,4 s (ffmpeg krijgt geen geheugen) |

`-max_alloc 536870912 -max_pixels 35389440` decodeert 4K gewoon.

**Gevolg.** 1.200 MB voor live en sprites, 512 MB voor korte jobs (Keuze 12). Op 1080 met
`-threads 4` en op 7.1.5 meet de spike het opnieuw (S10). Breekt het daar, dan 2.000 MB.

## V7. Het eerste fragment op 3 cores (R07, R08)

**Vraag.** Hoe snel komt het eerste fragment als de worker 3 cores heeft? En als er drie tegelijk
starten?

`redteam/mix.json` heeft drie commando's in de vorm van §3.3, met de echte bron `source.mkv`:
1080 veryfast (vanaf 0 s), 720 superfast (vanaf 10 s) en 720 superfast (vanaf 18 s), elk 40 s,
met AAC 160k, `-threads 4`, `-force_key_frames "expr:gte(t,n_forced*2)" -forced-idr 1`, en fMP4
naar `pipe:1`.

```sh
cd $S
python3 -c 'import json; json.dump(json.load(open("redteam/mix.json"))[:1], open("verify/single_cmd.json", "w"))'
taskset -c 0-2 python3 redteam/scripts/conc2.py verify/single_cmd.json 0
taskset -c 0-2 python3 redteam/scripts/conc2.py redteam/mix.json 0,0,0
taskset -c 0-2 python3 redteam/scripts/conc2.py redteam/mix.json 0,10,20
```

**Uitkomst.**

| Run | Eerste fragment | Snelheid | Haperen |
|---|---|---|---|
| 1080 veryfast alleen | 2,59 s | 2,76× | 0 |
| alle drie op t = 0 | 4,04 / 4,00 / 3,28 s | 1,67× / 1,81× / 1,70× | 0,28 s (1080) |
| gestart op 0, 10 en 20 s | 1,86 / 3,25 / 1,25 s | — | 0 |

**Gevolg.**
- Alleen haalt 1080 de 2,5 s net niet (2,59 s, en 1,86 s in de gespreide run). Mb eist daarom
  ≤ 2,5 s als mediaan en ≤ 3,0 s als slechtste; naast 2 andere ≤ 5 s (§4.5).
- Drie tegelijk blijven onder de 5 s, maar 1080 hapert even. Daarom de startvoorrang: zolang een
  sessie geen segment 0 heeft, lezen de andere producenten hooguit 10 s vooruit (Keuze 26).
- De belastingtest krijgt een fase "drie tegelijk" en een fase "herstart" (§4.6).

## V8. Fixtures en bronnen maken (R10, R13)

**Vraag.** Kan de fixture `vobsub.mkv` uit een SRT? En hoe snel maakt de worker een bron voor de
belastingtest?

```sh
cd $S
ffmpeg -nostdin -loglevel error -y -i redteam/nl.srt -c:s dvdsub verify/vs.mkv; echo "srt->dvdsub rc=$?"
ffmpeg -nostdin -loglevel error -y -i crit-sec/m/s.sup -c:s dvdsub verify/vs2.mkv; echo "sup->dvdsub rc=$?"
ffprobe -v error -show_entries stream=codec_name,width,height -of compact verify/vs2.mkv
ffprobe -v error -show_entries packet=pts_time -of csv=p=0 verify/vs2.mkv

cd $S/verify
for src in "testsrc2=size=1920x1080:rate=24,noise=alls=20:allf=t" \
           "cellauto=s=1920x1080:rate=24:rule=110:random_seed=7,noise=alls=20:allf=t" \
           "life=s=1920x1080:rate=24:mold=10:r=24:ratio=0.1:death_color=#C83232:life_color=#00ff00,noise=alls=20:allf=t"; do
  s=$(date +%s.%N); ffmpeg -nostdin -loglevel error -f lavfi -i "$src" -frames:v 240 -f null -
  e=$(date +%s.%N); echo "$src: $(echo "$e - $s" | bc) s"; done
for src in "testsrc2=size=1920x1080:rate=24,noise=alls=20:allf=t" \
           "cellauto=s=1920x1080:rate=24:rule=110:random_seed=7,noise=alls=20:allf=t"; do
  s=$(date +%s.%N); ffmpeg -nostdin -loglevel error -y -f lavfi -i "$src" -frames:v 480 -c:v libx264 \
    -preset ultrafast -b:v 6M -maxrate 6M -bufsize 12M -threads 3 u.mp4
  e=$(date +%s.%N); echo "$src ultrafast: $(echo "$e - $s" | bc) s"; done
s=$(date +%s.%N); ffmpeg -nostdin -loglevel error -y -f lavfi \
  -i "cellauto=s=1920x1080:rate=24:rule=110:random_seed=7,noise=alls=20:allf=t" -frames:v 240 -c:v libx264 \
  -preset veryfast -b:v 6M -maxrate 6M -bufsize 12M -threads 3 ca.mp4; e=$(date +%s.%N); echo "$(echo "$e - $s" | bc) s"

# matroska naar een geërfde fd van een gewoon bestand
timeout 60 ffmpeg -nostdin -loglevel error -y -f lavfi -i "testsrc2=size=640x360:rate=24" \
  -f lavfi -i "sine=frequency=440:sample_rate=48000" -t 6 -c:v libx264 -preset ultrafast -c:a aac -b:a 160k \
  -f matroska -fd 4 fd: 4>synth.mkv; echo rc=$?
python3 -c "print('Cues:', b'\x1c\x53\xbb\x6b' in open('synth.mkv','rb').read())"
```

**Uitkomst.**
- SRT naar `dvdsub`: rc 234, "Subtitle encoding currently only possible from text to text or
  bitmap to bitmap".
- De eigen `.sup` naar `dvdsub`: rc 0, `dvd_subtitle` 1280×720, pakketten op 0, 10, 17 en 30 s.
- 240 beelden van 1080p24 alleen maken: `testsrc2` met ruis 1,05 s, `cellauto` 1,95 s, `life`
  8,88 s.
- 480 beelden (20 s media) met x264 ultrafast op 6 Mbit/s: `testsrc2` 3,29 s, `cellauto` 4,96 s.
  Met veryfast kost `cellauto` 6,0 s voor 240 beelden.
- Matroska naar fd 4: rc 0, en het bestand heeft Cues. De fd is dus zoekbaar.

**Gevolg.**
- VobSub komt uit de eigen `.sup` met `-c:s dvdsub` (§3.6). S9 doet het op 7.1.5.
- Een bron van 300 s voor de belastingtest kost 50–75 s met x264 ultrafast. Drie bronnen kosten
  samen ±3 minuten. `Op.SYNTH` gebruikt alleen `testsrc2` en `cellauto` met een seed (§3.3).
  `mandelbrot` (±7 minuten per bron) en `life` vallen af.
- De worker schrijft een bron naar een fd die de app opent. Hij heeft geen schrijfbare map nodig.

## Wat de sandbox niet kan zeggen

- **ffmpeg 7.1.5 in het image.** Alles hierboven is 6.1.1. De job `media-spike` meet het in CI
  vanaf de eerste commit van M0 (§3.7).
- **H.264 in de browser.** De Chromium van Playwright (141) speelt geen H.264. Chrome for Testing
  156 in CI misschien wel; S13 legt het vast.
- **Safari en native HLS** met `trun` versie 1: een apparaattest in fase 9.
- **De CPU van Jonas.** `bench` en de belastingtest op de LXC (§4.6).
- **De rechten in de unprivileged LXC.** Zonder id-map is dat hier niet te testen. De README
  noemt het commando voor de Proxmox-host (Vraag 1).
