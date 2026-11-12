# Z0b-web: de contracten van gezichten (X2) en zoeken op beeld (X3) in het web

Dit is het webdeel van Z0b (bouwontwerp Keuze 55; §3.3, §3.4, de rijen voor `web/` in §3.5;
Keuze 66, 67, 71, 78–82). Z0b legt alleen de contracten van X2 en X3 vast, met een standaard
voor elk nieuw veld, en zet de lege plekken neer waar X2 en X3 hun werk in hangen. Het bouwt
geen functie. Tot X2 en X3 er zijn, geeft de server op hun routes `501 not_built("X2")` of
`not_built("X3")` (de stubs van Z0b-api); de web roept die routes nog nergens aan. Wat een
gebruiker ziet: op `/beheer/zoeken` onder het lege deel van Z7 twee blokken, "Gezichten" en
"Zoeken op beeld", met alleen "komt later in fase 6". De derde plek in het infopaneel toont
niets. De rest van de app is die van `3e6c1c2`.

Gebouwd op `3e6c1c2`, tak `wip/z0b-web`.

## Wat er is

| Bestand | Wat Z0b zette | Van wie daarna |
|---|---|---|
| `src/search/types.ts` | `SEARCH_MODES` (`text`, `image`) en `SearchParams.mode?`, `.like?`; `MatchKind` + `image`; `SearchPage.mode` en `.fallback` (`BEELD_FALLBACKS`: `slow`, `down`, `no_model`, of `null`); `BEELD_STATES` en `BeeldStatus` (`{state, done}`); `ANALYSE_STATES` en `AnalyseState` (de container analyse, gedeeld door X2 en X3); `ANALYSIS_CODES` + `no_preview`, `image_bad`; `BEELD_ERROR_CODES`; `SearchProgressData.what` + `faces`, `beeld` | Z0b (frozen); X3 |
| `src/search/api.ts` | `beeldStatus()` (`GET /photos/beeld`); `search()` neemt `mode: "image"` met `q` of `like` | X3 |
| `src/photos/faces/types.ts` (nieuw) | de vormen van de API van X2: `MAX_FACES` 200, `FACES_PAGE` 60, `CROP_SIZES` 96 en 192, `FaceSuggest`, `FaceOut`, `FacesStatus` (met `FacesPhotos`, `FacesCount`), `PersonSuggestions`, `SuggestionsByPerson`, `FacePage`, `FaceGroup`, `FaceGroups`, `FacesConfirm`, `FacesReject`, `FacesQueued`, `FACES_ERROR_CODES` | Z0b; A0 legt vast met `app/people/faces_types.py` |
| `src/photos/faces/api.ts` (nieuw) | `facesStatus`, `faceSuggestions`, `personFaceSuggestions`, `faceGroups`, `faceGroup`, `confirmFaces`, `rejectFaces`, `hideFaces`, `resetFaces`, `wipeUnnamedFaces`, `fileFaces`, `rescanFaces`, en ~~`faceCropUrl(id, size)` (een adres voor een `<img>`, geen aanroep)~~ `faceCrop(id, size)` (een fetch met de inlog, geeft een blob; review Z0b V2/F2, zie [z0b.md](z0b.md)) | X2 |
| `src/photos/people/types.ts` | `OnOff` (`aan`, `uit`); `PhotoSettings` + `faces`, `faces_forbidden`, `faces_available`, `beeld`; `PhotoSettingsPatch` als eigen type (+ `faces?`, `beeld?`); `AlbumRule` laat ook `mode` en `like` weg | Z6; X2 en X3 lezen het |
| `src/photos/people/api.ts` | `updatePhotoSettings(patch: PhotoSettingsPatch)` | Z6 |
| `src/admin/searchApi.ts` | `REBUILD_WHAT` + `faces`, `beeld`; `CodeCount`, `AnalyseModel`, `AdminFaces` (Keuze 67), `AdminBeeld` (Keuze 82); `AdminSearch.faces` en `.beeld` (elk `null` zolang het pakket er niet is); `AdminSearchPatch.faces_allowed?` en `.beeld?`; `wipeBeeld()` (`DELETE /admin/search/beeld`, 202) | Z7; X2 en X3 leggen hun blok vast |
| `src/admin/FacesBlock.tsx` (nieuw) | het blok "Gezichten": een kop en "komt later" | X2 |
| `src/admin/BeeldBlock.tsx` (nieuw) | het blok "Zoeken op beeld": een kop en "komt later" | X3 |
| `src/admin/SearchTab.tsx` | de twee blokken onder het deel van Z7, in dezelfde chunk | Z7 (houdt de blokken onderaan) |
| `src/Beheer.tsx` | `SEARCH_TAB_AREAS` (`photos_faces`, `beeld`): de tab laadt met `lazyWithAreas` ook de gebieden van de blokken | Z0b (frozen) |
| `src/viewer/LikeRow.tsx` (nieuw) | de plek "Meer zoals deze" in het infopaneel (geeft `null`) | X3 |
| `src/viewer/infoRows.ts` | `showLikeRow`: alleen bij een foto, ingelogd, de huidige versie | Z0b (frozen) |
| `src/viewer/InfoPanel.tsx` | de derde lazy plek, naast Plek en Personen, met het gebied `beeld` | Z0b (frozen) |
| `src/i18n/areas/{photos_faces,beeld}.{nl,en}.ts` | de gebieden, met alleen de titel en "komt later" | X2, X3 |
| `src/i18n/errors/faces.ts`, `beeld.ts` | de catalogi, met de codes van X2 en X3 en hun Engelse tekst | X2, X3 |
| `src/i18n/types.ts` | de gebieden `photos_faces` en `beeld` | Z0b (frozen) |
| `scripts/budget.mjs` | de grenzen `faces` (12 kB), `beeld` (15 kB) en `likeRow` (3 kB); `ZOEK_GROUPS`; `src/photos/faces/` in `ZOEK_MODULE`; `LikeRow` in `ZOEK_SLOTS` | Z0b (frozen) |

De nieuwe routes in `api.ts`-vorm (methode, pad, body) toetst `photos/faces/api.test.ts` één voor
één, net als `search/api.test.ts` en `admin/searchApi.test.ts` die van X3 en het beheer:

| Aanroep | Methode en pad | Pakket |
|---|---|---|
| `facesStatus()` | `GET /people/faces/status` | X2 |
| `faceSuggestions(cursor)` | `GET /people/faces/suggestions?cursor=` | X2 |
| `personFaceSuggestions(id, cursor, limit)` | `GET /people/faces/suggestions?person=&cursor=&limit=` | X2 |
| `faceGroups(cursor)` | `GET /people/faces/groups?cursor=` | X2 |
| `faceGroup(key, cursor)` | `GET /people/faces/groups/{key}?cursor=` | X2 |
| `confirmFaces(body)` | `POST /people/faces/confirm` `{faces, person_id}` of `{faces, name}` | X2 |
| `rejectFaces(body)` | `POST /people/faces/reject` `{faces, person_id}` | X2 |
| `hideFaces(faces)` | `POST /people/faces/hide` `{faces}` | X2 |
| `resetFaces(faces)` | `POST /people/faces/reset` `{faces}` | X2 |
| `wipeUnnamedFaces()` | `POST /people/faces/wipe-unnamed` | X2 |
| `fileFaces(id)` | `GET /files/{id}/faces` | X2 |
| `rescanFaces(id)` | `POST /files/{id}/faces/rescan` | X2 |
| ~~`faceCropUrl(id, size)`~~ `faceCrop(id, size)` | `GET /people/faces/{id}/crop?size=96` of `192`, met de inlog in de header (review Z0b V2/F2) | X2 |
| `updatePhotoSettings({faces, beeld})` | `PATCH /me/photo-settings` | X2, X3 |
| `search({mode: "image", q})`, `search({mode: "image", like})` | `GET /search?mode=image&q=` of `&like=` | X3 |
| `beeldStatus()` | `GET /photos/beeld` | X3 |
| `patchAdminSearch({faces_allowed, beeld})` | `PATCH /admin/search` | X2, X3 |
| `rebuildSearch("faces")`, `rebuildSearch("beeld")` | `POST /admin/search/rebuild` | X2, X3 |
| `wipeBeeld()` | `DELETE /admin/search/beeld` | X3 |

## Waar X2 en X3 inhaken

- **X2 (gezichten)**: de modules onder `src/photos/faces/` (lazy; het budget telt ze als groep
  `faces`, zie onder). De schakelaar per gebruiker gaat via `updatePhotoSettings({faces})`;
  `PhotoSettings.faces_forbidden` en `.faces_available` zeggen of hij kan. Het blok op `/beheer`
  is `admin/FacesBlock.tsx`; de stand komt uit `AdminSearch.faces`, de schakelaar
  "Gezichtsherkenning toestaan" is `patchAdminSearch({faces_allowed})`. De teksten horen in het
  gebied `photos_faces`, de foutteksten in `errors/faces.ts`. De gebieden van de tab laadt
  `Beheer.tsx` al (`SEARCH_TAB_AREAS`). Houd de modules in een eigen lazy chunk: de regel
  `faces` telt elke chunk met een module uit `src/photos/faces/`, dus een statische import vanuit
  `PeoplePage.tsx` zou de hele pagina van Z6 in `faces` laten tellen (en `faces` in `people`).
- **X3 (zoeken op beeld)**: `viewer/LikeRow.tsx` krijgt `InfoRowProps` (`item`, `detail`, `kind`)
  en laadt alleen bij een foto, ingelogd, de huidige versie (`showLikeRow`); bij de foto van een
  ander of zonder vector geeft de API `404` of `409 beeld_no_vector`, en X3 kiest wat de rij dan
  toont. Het raster van `/zoeken` in de stand beeld hoort onder `src/search/beeld/` (groep
  `beeld`). `beeldStatus()` geeft de stand voor wie vraagt; `SearchPage.fallback` zegt waarom
  zoeken op beeld terugviel op gewoon zoeken. Het blok op `/beheer` is `admin/BeeldBlock.tsx`
  (stand uit `AdminSearch.beeld`, aan en uit met `patchAdminSearch({beeld})`, wissen met
  `wipeBeeld()`). Teksten in `beeld`, foutteksten in `errors/beeld.ts`.

## Grootte (gzip niveau 9, `npm run budget`)

| Regel | Grens | Nu (Z0b) | Was op `3e6c1c2` |
|---|---|---|---|
| eerste lading JS | 100.000 B | 97.407 B | 97.304 B |
| eerste lading CSS | 12.000 B | 3.403 B | 3.403 B |
| route Bestanden | 45.000 B | 44.266 B | 44.249 B |
| `search` (`/zoeken`) | 45.000 B | 27.599 B | 27.589 B |
| `faces` (X2) | 12.000 B | nog niet gebouwd | — |
| `beeld` (X3, het raster) | 15.000 B | nog niet gebouwd | — |
| `likeRow` (X3, met het gebied `beeld`) | 3.000 B | 169 B | — |
| grootste lazy chunk | 50.000 B | 13.271 B | 13.271 B |
| `sw.js` | 8.000 B | 3.323 B | 3.248 B |

De eerste lading groeide met 103 B: de ingangen voor de vier nieuwe woordenboeken en de twee
catalogi in de lijst van de schil. Er blijft 2.593 B over. De route Bestanden groeide met 17 B
(de derde plek in het infopaneel en `showLikeRow`). `sw.js` groeide met 75 B: de catalogi
`faces.ts` en `beeld.ts` staan in de precache (offline te vertalen, zoals de andere); die heeft
nu 34 bestanden. De lege pagina's van Z0 (kaart, dubbel, personen) bleven op 1 à 2 B gelijk.

Daarnaast weigert `budget.mjs` nu ook:

- een module uit `src/photos/faces/` in de eerste lading (`ZOEK_MODULE`);
- `LikeRow` in de eerste lading of in de route Bestanden (`ZOEK_SLOTS`).

De regels voor fase 6 toetst `scripts/budget.test.mjs` met exacte lijsten: de grenzen, de
pagina's, de groepen en hun gebied, de lazy plekken (tegen de echte bestanden in `src/viewer` en
`src/shares`), en wat in de eerste lading mag.

## Afwijkingen van het ontwerp

1. **`faces` is een groep, geen pagina in `ZOEK_PAGES`.** Het ontwerp zegt "`ZOEK_MODULE` en
   `ZOEK_PAGES` krijgen de chunk van gezichten". X2 heeft geen eigen route: zijn schermen staan
   op `/fotos/personen` (van Z6), in de foto-instellingen en op `/beheer`. `ZOEK_GROUPS` meet
   daarom alle chunks met een module uit `src/photos/faces/`, met hun statische imports en het
   gebied `photos_faces`, tegen 12 kB; zo ook `beeld` (`src/search/beeld/`, 15 kB) en `likeRow`
   (`viewer/LikeRow.tsx`, 3 kB, Keuze 81). Zolang er geen chunk is, staat de regel op "nog niet
   gebouwd, X2" of "X3", zoals `mapData` van Z5.
2. **De blokken staan in `admin/SearchTab.tsx`, het bestand van Z7**, en niet in `Beheer.tsx`:
   het tabblad is één lazy chunk, en zo komen de blokken niet in de chunk van `/beheer` zelf.
   Hun teksten staan in de eigen gebieden `photos_faces` en `beeld` (niet in `admin.*`), en
   `Beheer.tsx` laadt die met de tab (`SEARCH_TAB_AREAS`). Z7 houdt de twee regels in
   `SearchTab.tsx`.
3. **`failed_by_code` is een lijst `{code, n}`** (`CodeCount[]`) in `AdminFaces` en
   `AdminBeeld`, geen object per code. De lezer van `test_web_contracts.py` kent geen `Record`,
   en een lijst houdt de volgorde van de API. Z0b-api koos hetzelfde (`list[CodeCount]`).
4. **De vormen van `AdminFaces` en `AdminBeeld` zijn een voorstel.** Keuze 67 en 82 noemen wat
   er in de blokken staat, niet de velden. Z0b-web schreef ze uit (aantallen, nooit per gebruiker
   of per gezicht); Z0b-api heeft dezelfde modellen in `app/search/types.py`. X2 en X3 leggen ze
   vast.
5. **De vormen van de routes van X2 zijn een voorstel** uit de tabel van de API van X2:
   `photos/faces/types.ts` naast `app/people/faces_types.py` van Z0b-api (dezelfde velden). A0 is
   de eigenaar van dat bestand (§2.0) en legt ze vast.
6. **`FacesConfirm` is een union** (`{faces} & ({person_id} | {name})`); de API heeft één model
   met twee optionele velden en een controle (`FacesConfirmIn`). De lezer van de contracttest
   kent die vorm niet; de vitest toetst de twee bodies.
7. **"Meer zoals deze" alleen bij een foto** (`showLikeRow`), niet bij een video: zoeken op beeld
   kent geen video's (Keuze 71). Bij de foto van een ander laadt de plek wel (de viewer kent de
   eigenaar niet), net als Plek en Personen.
8. **`PhotoSettingsPatch` is een eigen type** in `photos/people/types.ts` (was een
   `Partial<…>` in de aanroep), zodat de contracttest hem tegen `prefs.PhotoSettingsPatch` kan
   leggen.
9. **`AlbumRule` laat ook `mode` en `like` weg**: een slimme regel zoekt niet op beeld (Z0b-api:
   `mode` en `like` staan in `SearchParams`, niet in `SearchRule`, dus `extra=forbid` geeft
   `422`).

## Buiten de eigen bestanden

- `src/i18n/i18n.test.ts`: `photos_faces` en `beeld` in de exacte lijst `AREAS`.
- `src/pwa/routing.test.ts`: de API-paden van X2 en X3 (ook een uitsnede) komen nooit in de cache.
- `src/viewer/infoRows.test.ts`, `src/search/api.test.ts`, `src/photos/people/api.test.ts`,
  `src/admin/searchApi.test.ts`: de exacte lijsten van aanroepen kregen de nieuwe erbij.

Geen bestand in `api/`, `e2e/` of `infra/`; geen nieuwe afhankelijkheid.

## Tests

vitest (zonder DOM):

- `photos/faces/api.test.ts` (nieuw): elke aanroep van X2 met methode, pad en body, het adres
  van een uitsnede (zonder aanroep), `MAX_FACES` en `FACES_PAGE`, en de catalogus `faces.ts` is
  precies `FACES_ERROR_CODES`.
- `search/api.test.ts`: zoeken op beeld met `q` en met `like`, `beeldStatus`, en de catalogus
  `beeld.ts` is precies `BEELD_ERROR_CODES`.
- `admin/searchApi.test.ts`: `faces_allowed` en `beeld` in de PATCH, herbouwen voor alle zeven,
  `wipeBeeld`.
- `photos/people/api.test.ts`: de PATCH met `faces` en `beeld`.
- `admin/searchTab.test.ts` (nieuw): `SEARCH_TAB_AREAS` exact, en de tab (met
  `renderToStaticMarkup`) eindigt in nl en en met precies de twee blokken, met alleen een kop en
  "komt later".
- `viewer/infoRows.test.ts`: wanneer "Meer zoals deze" laadt, en `LikeRow` toont niets.
- `scripts/budget.test.mjs` (nieuw): de regels van het budget voor fase 6, exact.
- `i18n/i18n.test.ts`, `pwa/routing.test.ts`: zie hierboven.

Geen e2e: er is niets te bedienen. De e2e van de blokken en de rij is van X2 en X3.

## Gedraaid

Op `c802d97` (de code van Z0b-web; daarna kwam alleen dit document erbij), met de build van die
boom:

| Wat | Uitkomst |
|---|---|
| `npm run typecheck` | groen |
| `npm test` | groen: 92 bestanden, 993 tests (Z0: 89 bestanden, 975 tests) |
| `npm run build` en `npm run budget` | groen, zie de tabel hierboven |
| `npm run check:i18n -- --strict` | 0 meldingen in 242 bestanden |
| `tests/test_error_catalog.py` (api) | groen |
| `tests/search/test_web_contracts.py` (api) | rood op deze tak alleen, zoals verwacht: de API van `3e6c1c2` kent de nieuwe velden niet (11 tests). Zie Open 1 |
| de e2e-set van CI (`-m "not perf and not media and not media_perf and not office and not office_real and not provider_real and not ocr"`), met de bewaker van de browser | groen: 146 geslaagd, 129 niet gekozen, in 13:31 (onder `taskset -c 0-2`) |

## Open

1. ~~**De contracttest bij het samenvoegen met Z0b-api.** `api/tests/search/test_web_contracts.py`
   is van de API-kant en wijzigt Z0b-web niet. Op deze tak alleen faalt hij (de web heeft de
   velden, de API van `3e6c1c2` niet); op `wip/z0b-api` alleen ook (andersom). Nagelopen op een
   kopie met de app van `wip/z0b-api` (nog niet gecommit, stand van 4 november) en `web/src`
   van deze tak: 2 van de 69 tests rood, en met de aanpassingen hieronder alle 87 groen
   (samen met `test_error_catalog.py`). Bij het samenvoegen in die test:
   - `"photos/faces/types.ts"` in `BESTANDEN`, en `from app.people import faces_types as X2`;
   - in `PAREN`: `BeeldStatus` (`S.BeeldStatus`); `CodeCount`, `AnalyseModel`, `AdminFaces`,
     `AdminBeeld` (uit `S`, bestand `admin/searchApi.ts`); `PhotoSettingsPatch`
     (`prefs.PhotoSettingsPatch`); en uit `photos/faces/types.ts`: `FaceSuggest`, `FaceOut`,
     `FacesPhotos`, `FacesCount`, `FacesStatus`, `PersonSuggestions`, `SuggestionsByPerson`,
     `FacePage`, `FaceGroup`, `FaceGroups`, `FacesReject` (`X2.FacesRejectIn`), `FacesQueued`;
   - in `RICHTING`: `PhotoSettingsPatch` en `FacesReject` zijn `"vraag"`;
   - in `test_de_filters_zijn_de_regel_van_een_slim_album` de nieuwe exacte waarden:
     `'Omit<SearchParams, "offset" | "limit" | "after" | "mode" | "like">'` en
     `{"offset", "limit", "after", "mode", "like"}`.
   Zonder de paren van `AdminFaces` en `AdminBeeld` faalt `AdminSearch` met "de lezer kent het
   type 'AdminFaces' niet".~~ Gedaan: Z0b-api zette precies deze regels al in de test (`d96de96`);
   na de samenvoeging is hij groen zonder verdere aanpassing ([z0b.md](z0b.md)).
2. ~~**De migratie.** 0010 is gepusht en blijft zoals hij is; het leegmaken van het kader van
   `photo_person` in `node_bu_text` (Keuze 25a) gaat naar 0011 (A0). De zin in het bouwontwerp
   ("Z0b kan het in 0010 repareren") en de aantekening in `z0b.md` zijn van Z0b-api of de
   samenvoeging, niet van de web.~~ Gedaan: de zin staat in het bouwontwerp (bij Keuze 55 en in
   §3.1a), de aantekening in [z0b.md](z0b.md) (afwijking 1).
3. ~~**De docstring van `api/app/prefs.py`** (Vraag 3 waar Vraag 2 bedoeld is) is van Z0b-api;
   in `wip/z0b-api` staat hij al goed.~~ Gedaan in Z0b-api (`d96de96`).
4. **De vormen van X2 en X3** (`photos/faces/types.ts`, `AdminFaces`, `AdminBeeld`,
   `BeeldStatus`) zijn een voorstel tot A0, X2 en X3 ze vastleggen; past een pakket een vorm
   aan, dan in de web en in de API tegelijk (de contracttest houdt ze gelijk zodra Open 1 gedaan
   is).
5. **`FacesConfirm`** staat niet in de contracttest (zie Afwijking 6). X2 kan er een eigen toets
   voor schrijven, of de API in twee modellen splitsen.
6. **De standaarden.** `faces` en `beeld` staan per gebruiker standaard op `uit` (het antwoord
   van Jonas; voor beeld Vraag 11). De API geeft ze; de web zet niets. Alleen de eigenaar ziet zijn gezichten; een ander die de foto
   mag zien krijgt `[]` van `fileFaces`.
