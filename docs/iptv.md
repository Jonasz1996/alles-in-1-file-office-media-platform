# Live tv (IPTV)

Live tv uit je eigen IPTV-abonnement, in het platform zelf: een kanaallijst (m3u), een gids
(XMLTV) en kijken in de browser, op de tv (webOS-app, via `/live`) en in de Android-app. Zonder
extra container of programma: de app haalt de stroom, de mediaworker (ffmpeg, die er al is)
zet hem om.

## Instellen (beheerder)

**Beheer → Media → Live tv**:

1. **Kanaallijst (m3u):** het adres van je m3u (bij Xtream Codes iets als
   `http://aanbieder:8080/get.php?username=…&password=…&type=m3u_plus&output=ts`), of een
   m3u-bestand. Daar staan je logins in: het platform bewaart hem versleuteld (met het app-geheim)
   en toont hem nergens, ook niet op deze pagina. Leeg laten bij opslaan houdt de huidige lijst.
2. **Gids (XMLTV):** `https://epgiptv.jbogaert.be/guide.xml` (de stack `epg_iptv` op
   media-cluster). Lukt dat adres vanuit het platform niet (geen hairpin-NAT naar je eigen
   domein), neem dan het adres in het LAN: `http://192.168.10.145:3675/guide.xml`. De gids
   koppelt aan de kanalen via `tvg-id` in de m3u = `channel id` in de gids. Leeg: de gids die de
   m3u zelf noemt (`url-tvg`), als die er is.
3. **Kanalen tegelijk:** hoeveel verbindingen je aanbieder toelaat (vaak 1 of 2). Eén kanaal is
   één verbinding, hoeveel mensen er ook naar kijken.
4. **Beeld:** *Kopiëren als het kan* (standaard: H.264 gaat ongewijzigd door, weinig CPU; ander
   beeld zoals MPEG-2 of HEVC wordt vanzelf omgezet) of *Altijd omzetten* (720p H.264, meer CPU).
5. **Opslaan en bijwerken.** Het platform leest de m3u meteen (lukt dat niet, dan bewaart het
   niets) en haalt daarna lijst en gids op. Dat gebeurt vanzelf elke 6 uur; **Nu bijwerken** doet
   het meteen.

Daaronder: wie nu kijkt (per kanaal: kijkers, gekopieerd of omgezet), en **Kanalen tonen** om
kanalen te verbergen (bijv. radio of dubbele kanalen). Een verborgen kanaal speelt niet en staat
niet in de lijst. **Live tv wissen** haalt lijst, gids, favorieten en instellingen weg.

## Kijken

- **Web:** Media → **Live tv** (`/live`). Links de speler met nu en straks, rechts de kanalen
  (zoeken, groep, alleen favorieten, ★ voor een favoriet) of de **Gids** (6 uur, Eerder/Later/Nu;
  een programma aanklikken geeft de beschrijving en *Kijken*).
- **Tv (webOS-app):** de tv-stand opent de webpagina's; Media → Live tv. Kanaal+ en Kanaal- op
  de afstandsbediening wisselen van kanaal, de pijltjes en OK doen de rest.
- **Android-app:** Media → **Live tv**; een kanaal kiezen speelt het af.
- Gasten (gedeelde links) zien geen live tv.

## Hoe het werkt

- `api/app/iptv/`: `m3u.py` en `xmltv.py` lezen lijst en gids, `refresh.py` is de taak
  `iptv.refresh` (rij media_net), `upstream.py` leest de stroom van de aanbieder (MPEG-TS, of HLS
  met TS-stukken), `tuner.py` deelt één stroom per kanaal met alle kijkers, `fmp4.py` knipt de
  uitvoer in init en fragmenten, `logos.py` haalt de logo's via een ondertekend adres.
- De mediaworker krijgt een nieuwe opdracht `live` (`media/worker/ops/live.py`): de app schrijft
  de bytes van de aanbieder in een pipe, de worker krijgt alleen de leeskant (geen adres, geen
  logins, geen netwerk). Uit: fMP4 met een fragment per keyframe; geluid altijd AAC.
- De browser leest `GET /api/v1/iptv/channels/{id}/live` met fetch en zet het in MediaSource
  (`web/src/live/engine.ts`); de codecs staan in de kop `X-Codecs`. Android speelt dezelfde stroom
  met ExoPlayer.
- Zijn alle tuners bezet, dan stopt eerst een kanaal waar niemand meer naar kijkt; anders zegt de
  speler welke kanalen lopen (503 `iptv_busy`). Twintig seconden zonder kijker: de tuner stopt.
- Tabellen (migratie 0022): `iptv_channel` (het stroomadres versleuteld), `iptv_programme`,
  `iptv_favorite`. De instellingen staan in `setting` (`iptv_settings`).

## In het cluster

Geen nieuwe container en geen nieuwe poort: het ClusterForge-template hoeft niet te veranderen.
De tuners staan in het geheugen van de node met het VIP (daar komt het verkeer binnen). Na een
overname naar een andere node verbinden de spelers opnieuw. Het platform moet naar buiten kunnen
(de aanbieder en de gids).

## Grenzen (nu)

- Geen opnemen, geen terugkijken of pauzeren voorbij de buffer van de browser.
- HLS met versleutelde stukken (`#EXT-X-KEY`) of fMP4-stukken leest het platform niet; Xtream
  Codes geeft gewone TS en werkt wel.
- Ondertitels (teletekst, DVB) gaan niet mee.
- iPhone: alleen in Safari vanaf iOS 17.1 (ManagedMediaSource).
