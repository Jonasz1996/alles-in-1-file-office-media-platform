# M0-web: de haken en contracten van media in het web

Dit is het webdeel van M0 (bouwontwerp §2.0, de eerste rij van de tabel Web; §3.4 en §3.5).
M0-web zet alleen de plekken waar de pakketten M2–M10 hun werk in hangen. Zolang de server zegt
dat media uit staat, is er niets van te zien en is de app precies die van fase 4 (0.6.0).

## Wat er is

| Bestand | Wat M0 zette | Van wie daarna |
|---|---|---|
| `src/media/types.ts` | de typen van §2 en §3.4: de 25 foutcodes met hun status, de meldingen, de vier gebeurtenissen, `ClientProfile` v1, `PlayIn`/`PlayOut`, `map.json`, `X-Init`, voortgang, metadata, muziek en beheer | M0 (frozen) |
| `src/media/api.ts` | één functie per eindpunt van §2 (M2, M3, M7, M8, M9, M10), plus `adminMediaStatus` | M0 (frozen) |
| `src/media/index.ts` | de kleine ingang voor de schil: de status van media, de adressen van de pagina's, `streamUrl`, de regels van de haken en `musicBar` | M0 (frozen) |
| `src/media/Gate.tsx` | `MediaGate` (eerst een antwoord van de server; uit is "niet gevonden") en `NotReady` (de lege pagina) | M0 (frozen) |
| `src/routes.tsx` | negen lazy routes: `/media`, `/media/bibliotheek/:id`, `/media/item/:node`, `/media/serie/:id`, `/kijken/:node`, `/muziek`, `/muziek/album/:id`, `/muziek/artiest/:key`, `/muziek/lijst/:id` | M0 (frozen) |
| `src/App.tsx` | Media en Muziek in het menu, de live stroom (`media.library`), vergeten bij uitloggen, en de plek van de muziekbalk | M0 (frozen) |
| `src/browser/actions.ts` | de actie "Als bibliotheek gebruiken" | M0 (frozen) |
| `src/viewer/MediaView.tsx` | de keuze tussen de speler van M6 en de `<video>` van fase 3 | M0 (frozen) |
| `src/Beheer.tsx` | het achtste tabblad Media (Alt+8) | M0 (frozen) |
| `scripts/budget.mjs` | de regels `mediaRoute`, `player` en `music` | M0 (frozen) |
| `src/pwa/routing.test.ts` | de test dat `/api/v1/stream/` nooit in de cache komt | M0 (frozen) |
| `src/i18n/types.ts` | de gebieden `media`, `player` en `music` | M0 (frozen) |
| het blok `media` in `browser`, `viewer` en `admin` (nl en en) | de teksten van de haken | M0; in `admin` vult M10 aan na `tab` en `notReady` |
| `src/media/{LibraryHome,Library,ItemPage,ShowPage,NewLibraryDialog}.tsx`, `src/i18n/areas/media.*` | een lege versie | M2 |
| `src/player/{WatchPage,ViewerPlayer}.tsx`, `src/i18n/areas/player.*` | een lege versie | M6 |
| `src/music/{MusicHome,AlbumPage,ArtistPage,PlaylistPage,MusicBar}.tsx`, `src/i18n/areas/music.*` | een lege versie | M9 |
| `src/admin/MediaTab.tsx` | een lege versie | M10 |

## Wanneer staat media aan?

Het ontwerp heeft geen eigen statuspunt voor media. De status is daarom het antwoord op
`GET /api/v1/media/libraries` (M2):

- **200**: media staat aan, met de bibliotheken die jij ziet;
- **503 `media_off`** (MediaConfig `enabled` false, of de stub van M0): uit;
- **404** (een oudere server) of **403**: ook uit;
- een andere fout (geen verbinding, 502): onbekend, dus ook geen haken.

Een antwoord (ook "uit") geldt 60 s (`STATUS_TTL`), een fout zonder antwoord 15 s
(`STATUS_RETRY`). Twee keer tegelijk vragen is één verzoek. `media.library` houdt de telling en de
scanstaat bij zonder verzoek; een onbekende bibliotheek (net gemaakt of net gedeeld) vraagt de
lijst opnieuw. Bij uitloggen vergeet de app de status en verbergt hij de muziekbalk
(`resetMedia`): de bibliotheken waren van de vorige gebruiker.

De schil vraagt de status één keer na het inloggen (de balk bovenaan). In M0 geven de stubs
`503 media_off`: geen menu-items, geen actie, de `<video>` van fase 3, en geen tabblad.

## Waar elk pakket inhaakt

- **Pagina's** (M2, M6, M9): de default export van elk bestand in de tabel hierboven is de pagina
  van zijn route (`RouteProps`). Zet de inhoud in `<MediaGate params={params}>{() => …}</MediaGate>`:
  zonder media zegt die "niet gevonden", precies zoals fase 3 bij een adres dat niet bestaat. De
  adressen maak je met `libraryPath`, `itemPath`, `showPath`, `watchPath(node, t?)` (`?t=` is de
  start in seconden), `albumPath`, `artistPath` en `playlistPath` uit `media/index.ts`.
- **De dialoog voor een bibliotheek** (M2): `openNewLibraryDialog(folder: FileNode): void` in
  `media/NewLibraryDialog.tsx`. De actie laadt dat bestand en het gebied `media` pas bij het kiezen.
  Na het maken roept M2 `mediaStatus.load()` aan (of wacht op `media.library`).
- **De speler in de viewer** (M6): de default export van `player/ViewerPlayer.tsx` krijgt
  `ViewerPlayerProps` (`viewer/MediaView.tsx`): `item`, `detail`, `source` (altijd ingelogd, nooit
  een eerdere versie) en `fallback()`, de `<video>` van fase 3 met zijn sneltoetsen. Kan de speler
  het niet, dan geeft hij `fallback()` terug. Laadt het chunk niet, dan toont de viewer zelf de
  fallback. In de viewer krijgt de speler alleen Spatie, K, J, L, M, C, A, P en 0–9 (Keuze 33).
- **De muziekbalk** (M9): de default export van `music/MusicBar.tsx`. App.tsx laadt hem (met het
  gebied `music`) pas als `musicBar.visible()` waar is, en zet hem buiten de pagina, zodat hij
  blijft staan bij navigeren. M9 zet hem aan met `musicBar.show()` en weg met `musicBar.hide()`.
- **Het tabblad Media** (M10): de default export van `admin/MediaTab.tsx`, een eigen chunk.
  Beheer toont het tabblad zodra `GET /api/v1/admin/media` antwoordt (`adminMediaStatus`). M10
  moet dat eindpunt dus ook laten antwoorden als `MediaConfig.enabled` false is: anders kan een
  beheerder media niet meer aanzetten. Alleen de stub (503 `media_off`) en een niet-beheerder
  verbergen het.
- **Aanroepen**: altijd via `media/api.ts` (nooit zelf een pad bouwen). `closeSession` en `getMap`
  gaan zonder Authorization: de sid in het pad is de sleutel (§3.4). `streamUrl(sid, rest)` maakt
  een adres onder `/api/v1/stream/{sid}/`.

## Grootte (gzip, `npm run budget`)

| Regel | Grens | Nu (M0) |
|---|---|---|
| eerste lading | 100 KB | 95,6 KB (was 94,1 KB op `f713169`) |
| route Bestanden | 45 KB | 42,6 KB (was 42,4) |
| elke mediapagina (`mediaRoute`) | 45 KB | 0,8–0,9 KB |
| speler (`player`, alles uit `src/player/`) | 35 KB | 0,8 KB |
| muziek (`music`, alles uit `src/music/`) | 40 KB | 1,7 KB |

Elke regel telt wat er bij het openen bovenop de eerste lading binnenkomt: de chunks met hun
statische imports en het Nederlandse woordenboek. Een module uit `src/media/`, `src/player/` of
`src/music/` in de eerste lading is een fout, behalve `src/media/index.ts`. Daarom staat
`useMediaGate` in `Gate.tsx` en niet in `index.ts`.

De eerste lading groeide met 1,5 KB, niet met de ±0,5 KB van Keuze 33: naast de routes staan de
status (een eigen `StatusStore`, want die van office is alleen voor `OfficeStatus`), de regels van
de haken en de muziekbalk in de schil. Er blijft 4,4 KB over.

## Keuzes waar het ontwerp twee lezingen toeliet (de veiligste gekozen)

1. **De status is de lijst van bibliotheken** (zie boven). Er is geen statuspunt in §2.
2. **Een publieke link houdt de `<video>` van fase 3.** De server zegt daar niet of media aan staat
   (de lijst van bibliotheken vraagt een login), en een mislukte `POST /public/media/{node}/play`
   zou een werkende video breken. `publicPlay` staat al in `api.ts`. Wil M3 of M6 de speler ook op
   een publieke link, dan is dat een kleine wijziging in de hotfix-lane: een signaal van media op
   `GET /public/{token}`.
3. **De speler alleen voor de huidige versie**: `play` kent geen versie. Een eerdere versie houdt
   de `<video>`.
4. **De keuze valt één keer per bestand.** De viewer wisselt nooit midden in het afspelen van de
   `<video>` naar de speler als de status later binnenkomt.
5. **Gasten** zien Media en Muziek alleen als er minstens één bibliotheek met hen gedeeld is
   (een gast heeft geen eigen mappen). De routes zelf zijn er voor elke rol.
6. **"Als bibliotheek gebruiken"** staat bij één map van jezelf (geen gast, geen gedeelde map), in
   de groep delen na "Toevoegen aan album", zonder sneltoets. Is de map al een bibliotheek, dan
   staat hij uitgeschakeld met de naam. Een bibliotheek boven of onder een andere weigert de
   server (`media_library_nested`); de browser kent de voorouders niet.
7. **Alt+8 is altijd Media**, ook zonder het tabblad Office; Alt+7 en Alt+8 vangt de app alleen als
   hun tabblad er is.
8. **Geen eigen chunkgroepen in `vite.config.ts`.** Een groep `player` of `music` neemt ook de
   afhankelijkheden mee (`includeDependenciesRecursively`, standaard aan): in een proef kwam
   `media/Gate.tsx` in het chunk van de speler, en laadde elke mediapagina de speler. De regels in
   `budget.mjs` meten daarom de som van de chunks, hoe Rolldown ze ook verdeelt. Verder vraagt §3.5
   niets van de configuratie: `/api/v1/stream/` loopt in `npm run dev` al via de proxy van `/api`,
   en `i18n/errors/media.ts` valt al onder de offline-woordenboeken.

## Buiten de eigen bestanden

- `router.ts`: `NavSection` krijgt `media` en `music` (voor `aria-current` in het menu).
- `shell.{nl,en}.ts`: `nav.media` en `nav.music`. Het menu staat in de schil, dus de teksten ook.
- `i18n/areas/{media,player,music}.*`, de pagina's, de dialoog, de speler, de balk en het tabblad:
  een lege versie in de bestanden van M2, M6, M9 en M10, zodat alles importeert. Die pakketten
  vervangen ze.
- `media/index.test.ts` en `media/api.test.ts`: de tests van de eigen bestanden van M0, onder het
  patroon `media/*.test.ts` van M2. Ze testen alleen `index.ts` en `api.ts`.
- `i18n/errors/media.ts` maakt M0-api. `media/api.test.ts` legt de codes van `types.ts` ernaast
  zodra hij er is (tot dan slaat die test over). Let op: vitest bewaart zijn transformaties in
  `node_modules/.vitest-cache`, ook het resultaat van `import.meta.glob`. Komt de catalogus erbij
  zonder dat de test verandert, gooi dan die map weg, anders blijft de test lokaal overslaan. In CI
  (een schone `npm ci`) speelt dat niet.

## Naast M0-api gelegd (stand van `1c020e5` in de worktree van M0-api)

- **De catalogus** `i18n/errors/media.ts` heeft precies de 25 codes van `MEDIA_ERROR_CODES`; de
  test in `media/api.test.ts` slaagt ermee.
- **De stubs** geven 503 `media_off` op `GET /api/v1/media/libraries` en `GET /api/v1/admin/media`:
  in M0 staat media dus uit, zoals bedoeld.
- **Gelijkgetrokken met `app/media/types.py`:**
  - het rooster in `map.json` (`GridSegments`): `{grid_ms, origin_ms, count, last_frame_ms}` in
    mediatijd (spike punten 10 en 12), met `segmentStart`, `segmentOf` en `segmentCount` in
    `media/api.ts`, die rekenen zoals `start_of` en `segment_of`;
  - `Rung` (`{height, preset, codec}`) en `SessionView` (de velden van `sessions.list()`, plus
    wat M10 erbij zet voor Keuze 45).
- **Nog verschillend, voor het samenvoegen:** `Card`. §2 (M2) zegt
  `{node_id, title, year, kind, poster, progress, watched}`, en zo staat hij in `types.ts`.
  `types.py` heeft `{node_id, library_id, title, kind, poster, position_ms, duration_ms, watched,
  updated_at}`: zonder `year` en met de voortgang plat. Eén van de twee moet om, in de hotfix-lane
  en aan beide kanten tegelijk. Voorstel: `types.py` volgt §2 (`year`, en `progress:
  {position_ms, duration_ms} | null`), en `library_id` komt er in allebei bij.
- De andere vormen die §2 niet helemaal vastlegt (`CardProgress`, `TrickInfo`,
  `SubtitleBrief.kind`, `ImportView`, `MediaAdmin`) staan niet in `types.py`: die maken de
  routers van M2, M5 en M10. Wat die anders noemen, gaat in dezelfde commit naar `types.ts`.
