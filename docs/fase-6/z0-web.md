# Z0-web: de lege plekken en de contracten van fase 6 in het web

Dit is het webdeel van Z0 (bouwontwerp §2.0, §3.4 en de rijen voor `web/` in §3.5; Keuze 45 en
48). Z0-web zet alleen de plekken waar Z4, Z5, Z6, Z7 en X1 hun werk in hangen, en de typen en
aanroepen van de bevroren eindpunten van §3.4. Tot elk pakket zijn routes vult, geeft de server
daar `501` (de stubs van Z0-api); de web roept die eindpunten nog nergens aan. Wat een gebruiker
ziet: een zoekveld in de balk bovenaan (Mod+K, Enter opent de zoekpagina van 0.7.0), vier lege
pagina's en een leeg tabblad op `/beheer`. De rest van de app is die van 0.7.1.

Gebouwd op `b1e1ad6` (0.7.1), tak `wip/z0-web`.

## Wat er is

| Bestand | Wat Z0 zette | Van wie daarna |
|---|---|---|
| `src/search/types.ts` | de typen van §3.3 en §3.4 voor zoeken: `Filters` en `SearchParams` (de namen van de querystring, ook voor `/photos`, de kaart en een slimme regel), `SearchPage`, `Hit`, `Groups`, `Suggestions`, `PageWords`, `Analysis`, de grenzen (`MAX_QUERY` 200, `MAX_OFFSET` 1.000, `MAX_LIMIT` 50, `MAX_SUGGEST` 5), de foutcodes, de codes van de analyse en de gebeurtenissen `search.ready` en `search.progress` | Z0 (frozen) |
| `src/search/wire.ts` | `wireQuery(params)` (de querystring: lege velden weg, een lijst als herhaalde sleutel, `from`/`to`/`in`) en `seg(id)` (een stuk van een pad, gecodeerd). Ook gebruikt door de kaart, personen en beheer | Z0 (frozen) |
| `src/search/api.ts` | `search`, `suggest`, `pageWords`, `analysis`, `requestOcr` | Z0 (frozen) |
| `src/search/appBar.ts`, `src/search/AppBarSearch.tsx` | het zoekveld in de balk: `SEARCH_KEY` (`mod+k`), `searchTarget`, `ariaKeys`, `searchShortcut`; een gewoon veld, Enter naar `/zoeken?q=` | Z4 (vult de suggesties in) |
| `src/photos/map/types.ts`, `api.ts` | de plek, de kaart (`cells`, `items`), de plaatsen, de controle bij delen, de bronvermelding; `bboxParam` | Z0 (frozen) |
| `src/photos/map/MapPage.tsx` | een lege pagina `/fotos/kaart` | Z5 |
| `src/dupes/types.ts`, `api.ts` | `duplicates(kind, cursor)`, `ignoreDuplicates(key)` | Z0 (frozen) |
| `src/dupes/DupesPage.tsx` | een lege pagina `/dubbel` | Z5 |
| `src/photos/people/types.ts`, `api.ts` | personen, de personen op een foto, herinneringen (en verbergen), de instellingen voor foto's, slimme albums (regel, bevestigen, weghalen, verversen) | Z0 (frozen) |
| `src/photos/people/PeoplePage.tsx`, `PersonPage.tsx` | lege pagina's `/fotos/personen` en `/fotos/personen/:id` | Z6 |
| `src/admin/searchApi.ts` | de aanroepen van `/admin/search` (stand, pauze en OCR, herbouwen, mislukte, opnieuw) met een voorstel voor de vorm van de stand (Keuze 51) | Z0; Z7 legt de vorm vast |
| `src/admin/SearchTab.tsx` | een leeg tabblad | Z7 |
| `src/viewer/infoRows.ts` | `InfoRowProps` en `showPhotoRows` (wanneer de plekken laden) | Z0 (frozen) |
| `src/viewer/LocationRow.tsx` | een lege plek in het infopaneel (geeft `null`) | Z5 |
| `src/viewer/PeopleRow.tsx` | een lege plek in het infopaneel (geeft `null`) | Z6 |
| `src/shares/LocationWarning.tsx` | een lege plek in het deeldialoog (geeft `null`) | Z5 |
| `src/ui/LazySlot.tsx` | een lazy plek die stil leeg blijft als het laden mislukt | Z0 (frozen) |
| `src/i18n/areas/{search,photos_map,dupes,photos_people}.{nl,en}.ts` | de gebieden, met alleen de titel en "komt later" | Z4, Z5, Z5, Z6 |
| `src/i18n/errors/{search,photos_map,people}.ts` | de catalogi, met de codes van §3.4 en hun Engelse tekst | Z1/Z4, Z3/Z5, Z6 |
| `src/routes.tsx` | vier lazy routes (zie onder) | Z0 (frozen) |
| `src/App.tsx` | `<AppBarSearch />` in de balk, tussen het menu en de accountknop | Z4 |
| `src/Beheer.tsx` | het negende tabblad "Zoeken en foto's" (Alt+9), altijd | Z0 (frozen) |
| `src/viewer/InfoPanel.tsx`, `src/viewer/Viewer.tsx` | de twee plekken, en `signedIn` als prop | Z0 (frozen) |
| `src/shares/ShareDialogPanel.tsx` | de plek voor de waarschuwing, bij één node en bij meer | Z0 (frozen) |
| `scripts/budget.mjs` | de regels `search`, `mapRoute`, `mapData`, `dupes`, `people` en de controle op de eerste lading | Z0 (frozen) |
| `vite.config.ts` | de offline-woordenboeken: ook catalogi met `_` in de naam (`photos_map.ts`) | Z0 (frozen) |

De routes:

| Route | Chunk | Gebied | Kop | Sectie | Rollen |
|---|---|---|---|---|---|
| `/fotos/kaart` | `photos/map/MapPage` | `photos_map` | "Kaart" | Foto's | beheerder, gebruiker |
| `/fotos/personen` | `photos/people/PeoplePage` | `photos_people` | "Personen" | Foto's | beheerder, gebruiker |
| `/fotos/personen/:id` | `photos/people/PersonPage` | `photos_people` | "Persoon" | Foto's | beheerder, gebruiker |
| `/dubbel` | `dupes/DupesPage` | `dupes` | "Dubbele bestanden" | Bestanden | beheerder, gebruiker |

`/zoeken` blijft `Search.tsx` van 0.7.0 met `searchQuery.ts`; die werken door tot Z4 ze vervangt.

## Waar elk pakket inhaakt

- **Z4 (zoeken)**: `AppBarSearch.tsx` krijgt de combobox (lazy na de eerste toets, Keuze 46);
  houd `SEARCH_KEY`, `searchShortcut` en `searchTarget` (de vitest in `search/appBar.test.ts`
  toetst ze tegen de echte registry). De pagina: vervang de route `/zoeken` in `routes.tsx` door
  een pagina onder `src/search/` met de gebieden `["search"]` (plus wat hij nog nodig heeft), en
  zet die pagina in `ZOEK_PAGES` van `budget.mjs` in plaats van `src/Search.tsx` (twee regels
  in hotspots van Z0; zeg het in `z4.md`). De aanroepen staan in `search/api.ts`
  (`search(params)`, `suggest(q)`); de querystring maakt `wireQuery`. De tekstlaag in de
  viewer leest `pageWords(id, n, token)`. Alles wat het zoekveld uit `search/` laadt, telt mee
  in de eerste lading: alleen `AppBarSearch.tsx`, `appBar.ts` en `types.ts` mogen daar
  (`ZOEK_INITIAL`); de combobox moet lazy.
- **Z5 (kaart en duplicaten)**: de default export van `MapPage.tsx` en `DupesPage.tsx` is de
  pagina (`RouteProps`). `LocationRow.tsx` krijgt `InfoRowProps` (`item`, `detail`, `kind`) en
  laadt alleen voor een foto of video, ingelogd, de huidige versie (`showPhotoRows`). Wie de
  eigenaar is, weet de plek niet: bij de foto van een ander geeft `fileLocation(id)` `404` en
  `filePeople(id)` een lege lijst, en dan toont de plek niets. `LocationWarning.tsx`
  krijgt `{nodes}`: bij één node `[node]`, bij meer de nodes die gedeeld kunnen worden
  (`plan.ok`); `nodes` is elke render een nieuwe lijst, dus sleutel een effect op de ids.
  `shareCheck(nodeId)` geeft `{with_location, unknown}`. De wereld van de kaart moet als
  `dist/kaart/<naam>.json` gebouwd worden (`MAP_DATA` in `budget.mjs`, ≤ 60 kB gzip); tot dan
  staat de regel `mapData` op "nog niet gebouwd, Z5".
- **Z6 (personen, herinneringen, slimme albums)**: de default export van `PeoplePage.tsx` en
  `PersonPage.tsx` (`params.id`). `PeopleRow.tsx` met dezelfde `InfoRowProps` als `LocationRow`.
  De aanroepen staan in `photos/people/api.ts`; `PEOPLE_CHANGED` is de naam van de gebeurtenis
  (de web luistert er nog niet naar).
- **Z7 (beheer)**: de default export van `admin/SearchTab.tsx`. De vorm van `GET /admin/search`
  in `admin/searchApi.ts` is een voorstel uit Keuze 51; Z7 legt hem vast samen met
  `app/search/admin.py` en past `AdminSearch` aan.
- **X1 (OCR)**: `requestOcr(id)` in `search/api.ts`; de codes `ocr_*` horen in de catalogus
  `photos.ts` (§3.4) en staan daar nog niet; X1 voegt ze toe. `ocr_photos` zit al in
  `PhotoSettings`.

Elk pakket vult zijn eigen gebied (`search`, `photos_map`, `dupes`, `photos_people`) en zijn
eigen catalogus; geen twee pakketten schrijven in hetzelfde bestand. De teksten van het tabblad
staan in `admin.search.*` (Z7 vult aan na `tab` en `notReady`); die van het zoekveld in
`shell.search.*` (de schil heeft die altijd).

## Grootte (gzip niveau 9, `npm run budget`)

| Regel | Grens | Nu (Z0) | Was op `b1e1ad6` |
|---|---|---|---|
| eerste lading JS | 100.000 B | 97.302 B | 96.421 B |
| eerste lading CSS | 12.000 B | 3.403 B | 3.380 B |
| route Bestanden | 45.000 B | 44.225 B | 44.213 B |
| `search` (`/zoeken`, nu `Search.tsx` met `browser`, `sharing` en `search`) | 45.000 B | 27.566 B | — |
| `mapRoute` (`/fotos/kaart`) | 30.000 B | 314 B | — |
| `mapData` (de wereld) | 60.000 B | nog niet gebouwd (Z5) | — |
| `dupes` (`/dubbel`) | 45.000 B | 321 B | — |
| `people` (`/fotos/personen`) | 45.000 B | 333 B | — |
| `people` (`/fotos/personen/:id`) | 45.000 B | 334 B | — |
| grootste lazy chunk | 50.000 B | 13.271 B | 13.271 B |
| `sw.js` | 8.000 B | 3.241 B | 3.017 B |

De eerste lading groeide met 881 B. Het zoekveld zelf is 360 B (gemeten door het uit de balk te
halen: 96.942 B), binnen de 0,8 kB van Keuze 45. De rest zijn de vier routes, de ingangen voor de
nieuwe gebieden en catalogi in de lijst van woordenboeken, en de teksten van het veld in `shell`.
Er blijft 2.698 B over voor de schil; de suggesties van Z4 laden lazy.

Elke regel telt wat er bij het openen bovenop de eerste lading binnenkomt: de chunks met hun
statische imports en het Nederlandse woordenboek, zoals de regels van fase 5. Daarnaast weigert
`budget.mjs`:

- een module uit `src/search/`, `src/dupes/`, `src/photos/map/` of `src/photos/people/` in de
  eerste lading, behalve `AppBarSearch.tsx`, `appBar.ts` en `types.ts`;
- `LocationRow`, `PeopleRow` of `LocationWarning` in de eerste lading of in de route Bestanden.

De drie nieuwe catalogi staan in de precache van de service worker (offline te vertalen, zoals de
andere); de precache ging van 29 naar 32 bestanden.

## Afwijkingen van het ontwerp

1. **`Hit.path` is een lijst `{id, name}`**, niet `str` zoals in §3.3. §3.4 zegt dat `Hit` "blijft
   `{node, path}` zoals in 0.7.0", en 0.7.0 geeft een lijst; de zoekpagina leest die lijst. Z0-api
   koos hetzelfde (`PathPart` in `app/search/types.py`).
2. **De plek voor de waarschuwing zit in `shares/ShareDialogPanel.tsx`**, niet in
   `ShareDialog.tsx`. In 0.7.1 is `ShareDialog.tsx` een kleine schil die het paneel lazy laadt;
   het paneel is het dialoog. De plek laadt bij het openen (`import()` in een `LazySlot`), dus
   niet met Bestanden mee.
3. **De catalogi zijn niet leeg**: ze hebben de codes van §3.4 met hun Engelse tekst. Een lege
   catalogus zou `test_error_catalog.py` niets laten toetsen, en de codes liggen vast. De
   pakketten mogen de teksten verbeteren. De codes `ocr_*` staan nog niet in `photos.ts` (X1).
4. **Het tabblad "Zoeken en foto's" staat er altijd**, als laatste (Alt+9). Zoeken kan niet uit.
   De verwachtingen in `beheer.test.ts` kregen daarom `search` achteraan (nog steeds exact), en
   een test dat het tabblad altijd het laatste is.
5. **De toets van de titels in `router.test.ts` is strenger gemaakt.** De oude toets eiste
   `^[a-z]+\.`; de nieuwe gebieden hebben een `_` (Keuze 48: `photos_map`, `photos_people`). De
   nieuwe toets eist dat het gebied van de titel een gebied van de route is (of `common` of
   `shell`), en laat `_` toe. Geen route viel daardoor uit.
6. **`OFFLINE_I18N` in `vite.config.ts` laat `_` toe in de naam van een catalogus.** Anders kwam
   `errors/photos_map.ts` niet in de precache.
7. **De grens voor `search`, `dupes` en `people` is 45 kB**: het ontwerp noemt de regels, maar
   alleen de grenzen van `mapRoute` en `mapData`. 45 kB is die van een route van Bestanden of
   media. Tot Z4 meet `search` de pagina van 0.7.0, met het gebied `search` erbij.
8. **De nieuwe routes zijn niet voor gasten.** Ze gaan over eigen bestanden (`own_live`); een
   gast heeft er geen en krijgt "Geen toegang". `/dubbel` valt onder Bestanden in het menu
   (`aria-current`), de andere drie onder Foto's.
9. **De plekken in het infopaneel laden alleen voor een foto of video, ingelogd, de huidige
   versie** (`showPhotoRows`). Via een publieke link, bij een eerdere versie en bij een ander
   soort bestand laden ze niet; de API geeft daar toch niets. Bij de foto van een ander laden ze
   wel (de viewer kent de eigenaar niet); de API geeft dan `404` of een lege lijst.
10. **Esc in het zoekveld** maakt een veld met tekst leeg, en geeft bij een leeg veld de focus
    terug aan waar hij stond vóór Mod+K. Het ontwerp zegt alleen "Esc sluit" voor de combobox
    (Z4).
11. **Keuze 45 zegt "Op Bestanden blijft `/` het naamfilter (J9)".** In de code van 0.7.1 is `/`
    het zoekveld van Bestanden (`#zoekveld`) en Mod+F het naamfilter. Z0 verandert geen van
    beide (zie [toetsenbord-z0.md](toetsenbord-z0.md)).
12. **Vormen die het ontwerp niet uitschrijft**, gekozen door Z0-web en naast het werk van Z0-api
    gelegd waar dat er al was (`app/search/types.py` in `wip/z0-api`, nog niet gecommit):
    `Groups` (`{people, places}` met `{id: string, name, n}`, zoals `GroupItem` van Z0-api),
    `NameRef` (`{id, name, kind}`), `PersonRef`, `PlaceRef`, `AlbumRef`, `IndexProgress`
    (`{done}`), `StepState` en de vorm van `Analysis`, `PersonPhotos` (`{items, cursor}`),
    `MemoryHides` (`{hides}`), `AlbumRuleSaved` (`{needs_confirm?}`), `AdminSearch`,
    `FailedItem` en `FailedPage`.

## Buiten de eigen bestanden

- `src/i18n/types.ts` en `src/i18n/i18n.test.ts`: de vier nieuwe gebieden.
- `src/i18n/areas/shell.{nl,en}.ts`: `shell.search.*` (het veld staat in de schil).
- `src/i18n/areas/admin.{nl,en}.ts`: `admin.search.tab` en `admin.search.notReady`.
- `src/base.css`: de breedte van het veld (`min(14rem, 45vw)`).
- `src/pwa/routing.test.ts`: de nieuwe pagina's zijn de schil; de nieuwe API-paden komen nooit in
  de cache.

## Tests

vitest (alleen de pure delen, zonder DOM):

- `search/appBar.test.ts`: `searchTarget` (leeg, spaties, tekens die gecodeerd moeten worden),
  heen en terug met `readSearch` van 0.7.0, `MAX_QUERY`, `formatCombo` en `ariaKeys` op Mac en
  elders, en Mod+K tegen de echte `ShortcutRegistry`: Ctrl+K en ⌘K werken, ook in een veld; K,
  Alt+K, Ctrl+Shift+K, Ctrl+Alt+K en Win+K niet; onder een dialoog, een menu of de viewer met een
  dialoog niet; onder de scope `browser` wel; de K van de viewer blijft van de viewer; de toets
  staat in de hulp.
- `search/api.test.ts`, `photos/map/api.test.ts`, `dupes/api.test.ts`,
  `photos/people/api.test.ts`, `admin/searchApi.test.ts`: elke functie roept de juiste methode,
  het juiste pad en de juiste body aan; `wireQuery` en `seg`; de codes van elke catalogus zijn
  precies die van de typen, zonder `{…}` in de tekst.
- `viewer/infoRows.test.ts`: wanneer de plekken laden.
- `router.test.ts`, `beheer.test.ts`, `pwa/routing.test.ts`: de routes, het tabblad, de cache.

Geen e2e: de e2e van het zoekveld is van Z4 (`e2e/test_zoek_z4.py`), die van de kaart van Z5,
enzovoort. In de echte app nagelopen met een tijdelijke e2e die niet gecommit is (zie de lijst
"Nagelopen" in [toetsenbord-z0.md](toetsenbord-z0.md)): Mod+K, Enter, Esc, de plekken in de viewer
en in het deeldialoog, de vier pagina's, Alt+9, een gast, 360 px breed, en de bewaker van de e2e
zonder melding.

## Open

1. **Gelijk met Z0-api bij het samenvoegen.** Leg `search/types.ts`, `photos/map/types.ts`,
   `dupes/types.ts`, `photos/people/types.ts` en `admin/searchApi.ts` naast de modellen en
   routers van Z0-api (de OpenAPI van de app): elk pad en elke methode, en de vormen uit punt 12
   van de afwijkingen.
2. **De foutcode van de stubs.** Welke code geven de stubs van Z0-api bij `501`? Staat die niet
   in een catalogus, dan faalt `test_error_catalog.py` bij het samenvoegen; dan hoort hij in de
   catalogus van zijn pakket of in een algemene.
3. **De codes `ocr_*`.** Gebruikt Z0-api ze al in `app/`, dan moeten ze bij het samenvoegen in
   `i18n/errors/photos.ts` (§3.4 geeft die aan X1).
4. **Twee zoekvelden op Bestanden.** Het veld bovenaan en het veld boven de lijst (`#zoekveld`,
   `/`) doen nu hetzelfde. Z4 beslist of het veld van Bestanden blijft (en wat `/` dan doet).
5. **`dist/kaart/*.json`** is een aanname voor de plek van de wereld (Keuze 31 noemt
   `world-110m.<hash>.json`). Kiest Z5 een andere plek, dan past hij `MAP_DATA` aan. De service
   worker cachet `/kaart/` nu niet.
6. **De gebeurtenissen** (`search.ready`, `search.progress`, `people.changed`) zijn alleen namen
   en vormen; geen code luistert er nog naar.
7. **`e2e/slim.sh`** (§3.5) is niet van Z0-web; hij staat er niet in deze tak.
8. **De plekken bij de foto van een ander.** De viewer weet niet wie de eigenaar is, dus Plek en
   Personen laden ook bij een gedeelde foto en vragen dan de API (die `404` of `[]` geeft). Wil
   Z5 of Z6 dat verzoek sparen, dan is een teken van de eigenaar in `ViewerItem` nodig (een
   kleine wijziging in de viewer, niet in Z0).
9. **Besluiten B49–B60** blijven "voorstel"; Z0-web nam er geen.
