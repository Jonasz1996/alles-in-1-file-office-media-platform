# Het clientprofiel, versie 1

Wat een speler meestuurt met `POST /api/v1/media/{node_id}/play` (en
`POST /api/v1/public/media/{node_id}/play`), en hoe de server het leest. Keuze 20 van het
[bouwontwerp](bouwontwerp.md) legt het vast; M3 bouwde het (`api/app/media/profile.py`,
`api/app/media/types.py`, de basisprofielen in `api/app/media/profiles/*.json`). Dit document is
voor de apps van fase 9 (Keuze 49): een nieuwe client voegt hooguit een basisprofiel toe, het
profiel zelf blijft gelijk.

Het profiel is geen beveiliging. Wie liegt, krijgt iets wat hij niet kan spelen; wat het kost,
begrenst de toelating (Keuze 26) toch.

## Het verloop

1. De speler vraagt `GET /api/v1/media/{node_id}/codecs` (met Bearer). Dat geeft
   `{"candidates": [...], "item": [...]}`: de vaste kandidaten en de echte strings van dit item
   (uit de probe; leeg als er nog geen probe is). Een publieke speler heeft geen login en kent
   alleen de vaste kandidaten hieronder.
2. De speler meet elke string (zie [Meten](#meten)) en stuurt het profiel mee met play.
3. De server neemt het basisprofiel dat de client noemt (`base`), legt wat de client mat
   eroverheen, en beslist (Keuze 19, `decide.py`). Wat de client zelf mat, gaat voor; alleen
   `deny` van het basisprofiel gaat boven een meting.

De vaste kandidaten (versie 1):

```
avc1.42E01E  avc1.64001f  avc1.640028  avc1.640033
hvc1.1.6.L120.90  hvc1.2.4.L150.90
vp09.00.40.08  vp09.02.40.10
av01.0.08M.08  av01.0.08M.10
mp4a.40.2  mp4a.40.5  ac-3  ec-3  opus  flac  mp3
```

## De vorm

```json
{
  "v": 1,
  "base": "web",
  "client": {"kind": "web", "name": "Chrome 141", "app": "0.7.0"},
  "engines": {"mse": true, "mms": false, "native_hls": false},
  "file": ["video/mp4", "video/webm", "audio/mpeg", "audio/flac", "audio/ogg"],
  "codecs": {
    "vp09.00.40.08": {"mse": true, "file": true, "smooth": true, "power": false},
    "avc1.640028":   {"mse": false, "file": false},
    "opus":          {"mse": true, "file": true}
  },
  "hdr": {"pq": false, "hlg": false, "dv": false, "screen": false},
  "max": {"height": 1440, "bitrate_kbps": 0},
  "audio": {"channels": 2, "track_switch": false}
}
```

| Veld | Type | Betekenis |
|---|---|---|
| `v` | `1` | De versie. Een andere waarde: 422 `invalid_input`. |
| `base` | `web`, `ios-safari`, `webos-5`, `android-exo`, `windows-webview2` | Het basisprofiel (hieronder). Standaard `web`. |
| `client.kind` | `web`, `tv`, `android`, `windows`, `other` | Soort client. |
| `client.name` | tekst, ≤ 64 | Naam en versie van de browser of het toestel. `deny_by_name` van het basisprofiel kijkt hiernaar (bijvoorbeeld `firefox`). |
| `client.app` | tekst, ≤ 32 | Versie van de app. Alleen informatie. |
| `engines.mse` | bool | `MediaSource` is er (remux en transcode in fMP4). |
| `engines.mms` | bool | `ManagedMediaSource` (Safari 17). Telt als MSE. |
| `engines.native_hls` | bool | De `<video>` speelt HLS zelf (`canPlayType("application/vnd.apple.mpegurl")`). |
| `file` | lijst van mime-types, ≤ 16 | Containers die de client als bestand speelt (direct play). Alleen `video/…` of `audio/…`. |
| `codecs` | object, string → meting, ≤ 64 strings | Per codec-string wat de client mat. Alleen strings op een bekend patroon (RFC 6381, zie [De strings](#de-strings)); andere sleutels gaan weg. |
| `codecs[s].mse` | bool | Via MSE of MMS in fMP4 (en voor VP9, AV1 en Opus ook in WebM). |
| `codecs[s].file` | bool | Als bestand in de `<video>` (`canPlayType` niet leeg). |
| `codecs[s].smooth` | bool of weggelaten | `decodingInfo().smooth`. `false` maakt direct en remux onmogelijk (reden `not_smooth`). Weggelaten: onbekend, telt niet tegen. |
| `codecs[s].power` | bool of weggelaten | `decodingInfo().powerEfficient`. Alleen informatie. |
| `hdr.pq`, `hdr.hlg`, `hdr.dv` | bool | De client speelt die overdracht af (`decodingInfo` met `transferFunction`). |
| `hdr.screen` | bool | Het scherm is HDR (`matchMedia("(dynamic-range: high)")`). |
| `max.height` | 144 tot 4320 | Hoogte van het scherm maal `devicePixelRatio`. `auto` kiest geen trede daarboven. |
| `max.bitrate_kbps` | 0 tot 1 000 000 | 0 is geen grens. Een bron daarboven speelt niet direct of via remux (reden `bitrate`). |
| `audio.channels` | 1 tot 8 | Kanalen die de client weergeeft. Versie 1 leest het nog niet: omzetten is altijd stereo. |
| `audio.track_switch` | bool | De client kan zelf van audiospoor wisselen (`audioTracks`). Zonder: een ander spoor dan het eerste kan niet direct. |

Grenzen: het hele profiel hooguit 8 KB (als JSON), hooguit 64 strings in `codecs`, en onbekende
velden gaan weg zonder fout. Een profiel boven 8 KB is 422 `invalid_input`.

## De strings

De server laat alleen deze vormen door (de rest gaat stil weg):

| Codec | Vorm | Voorbeeld |
|---|---|---|
| H.264 | `avc1.PPCCLL` (hex) | `avc1.64001f`, Hi10P `avc1.6e001e` |
| HEVC | `hvc1.…` of `hev1.…` | `hvc1.1.6.L120.90`, `hvc1.2.4.L153.B0` |
| Dolby Vision | `dvh1.PP.LL`, `dvhe.PP.LL` | `dvh1.05.06` |
| VP9 | `vp09.PP.LL.DD` (+ tot 5 velden) | `vp09.00.31.08`, `vp09.02.40.10` |
| AV1 | `av01.P.LLT.DD` (+ rest) | `av01.0.08M.08` |
| Audio | `mp4a.40.N`, `mp4a.6N`, `ac-3`, `ec-3`, `opus`, `flac`, `mp3`, `vorbis`, `alac` | `mp4a.40.2` |

De worker maakt dezelfde strings uit het bestand (Keuze 16, `codecs.py`), dus de strings van het
item in `GET …/codecs` passen precies op wat de client daarna meet.

**Families.** Heeft het profiel een string niet precies, dan kijkt de server naar een string van
dezelfde familie: dezelfde codec, hetzelfde profiel en dezelfde bitdiepte, met een tier en level
die minstens zo hoog zijn. `avc1.640033` uit een basisprofiel dekt zo ook `avc1.64001f`, en de
kandidaat `vp09.00.40.08` dekt `vp09.00.21.08`. H.264 Hi10P (`avc1.6e…`) is een eigen profiel en
telt alleen als hij er zelf in staat. Audio heeft geen familie: alleen precies.

## Meten

Wat de web-app doet (M6), en wat `e2e/test_media_m3.py` nadoet:

- `mse`: `MediaSource.isTypeSupported` (of `ManagedMediaSource.isTypeSupported`) met
  `video/mp4; codecs="<s>"`, voor VP9, AV1 en Opus ook `video/webm; codecs="<s>"`; audio met
  `audio/mp4` en `audio/webm`;
- `file`: `document.createElement("video").canPlayType(…)` met dezelfde types, niet leeg;
- `smooth` en `power`: `navigator.mediaCapabilities.decodingInfo({type: "media-source", video:
  {contentType, width, height, bitrate, framerate}})`. Voor het item met zijn echte maat en
  bitrate, voor de kandidaten op 1080p. HDR met `transferFunction` (`pq` of `hlg`) en
  `colorGamut: "rec2020"`. Lukt de vraag niet, dan blijven beide weg (onbekend);
- de uitkomst voor de kandidaten mag de web-app per browserversie bewaren in `localStorage`
  (alleen gemak, in try/catch); de strings van het item meet hij elke keer opnieuw.

## De basisprofielen

Data in `api/app/media/profiles/<naam>.json`, met dezelfde velden als het profiel plus:

| Veld | Betekenis |
|---|---|
| `_doc` | Uitleg voor mensen; de server leest het niet. |
| `deny` | Voorvoegsels van strings die nooit direct of via remux spelen, wat de client ook meet (Keuze 19, MED-10). |
| `deny_by_name` | Hetzelfde, maar alleen als `client.name` (kleine letters) het woord bevat. |
| `playlist.independent_segments` | `EXT-X-INDEPENDENT-SEGMENTS` in de playlist (M4). webOS 5 wil hem niet. |

| Naam | Voor wie | Wat het aanneemt |
|---|---|---|
| `web` | Een browser die alles zelf meet | Geen codecs; MSE; mp4, webm, mp3, flac, ogg en m4a als bestand; tot 1080p; Hi10P niet bij Firefox. |
| `ios-safari` | Safari op iOS, iPadOS en macOS | MMS en native HLS; H.264 tot level 5.1, HEVC (hvc1), AAC, AC-3, E-AC-3, FLAC; PQ en HLG; `audioTracks`; geen Hi10P. |
| `webos-5` | LG webOS 5 (2020) | MSE en native HLS; H.264, HEVC en VP9 tot level 5.1, AAC, AC-3, E-AC-3; tot 2160p; PQ en HLG; geen `EXT-X-INDEPENDENT-SEGMENTS`; geen Hi10P. |
| `android-exo` | De app van fase 9 met ExoPlayer | Geen MSE, wel HLS; H.264 tot level 4.0 en AAC, Opus, FLAC, MP3 als bestand; de app meet de rest met `MediaCodecList`; wisselt zelf van spoor. |
| `windows-webview2` | De app van fase 9 in WebView2 | MSE; H.264, VP9 (ook 10 bit) en AV1, AAC, Opus en FLAC. HEVC niet (alleen met de extensie van Windows). |

Een nieuw basisprofiel is een nieuw bestand plus zijn naam in `PROFILE_BASES` (`types.py`); het
profiel zelf blijft versie 1.

## Wat de server met het profiel doet

`decide` (Keuze 19) kiest met het samengevoegde profiel:

- **direct:** de container staat in `file` (mp4, mov, m4v als `video/mp4`, webm als
  `video/webm`), video en audio zijn er via `file`, niet `smooth: false`, en het gekozen
  audiospoor is het eerste of `track_switch` is waar;
- **remux:** de video via `mse` (of MMS, of native HLS), een codec die in fMP4 past, de
  keyframe-index is er; het geluid gaat mee als de client het kan, anders AAC (of Opus);
- **transcode:** anders, als de policy het toestaat en er plek is (M4).

De reasons van het plan zeggen waarom het niet beter kon (`container`, `video_codec`,
`audio_codec`, `not_smooth`, `quality`, …), zodat een client kan zien wat er ontbrak.

## Versies

- **1** (fase 5, M3): dit document.
