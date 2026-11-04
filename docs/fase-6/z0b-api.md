# Z0b-api: de contracten van gezichten en zoeken op beeld, zoals ze gebouwd zijn

Dit is het deel Z0b-api van het bouwontwerp van fase 6 ([bouwontwerp.md](bouwontwerp.md), Keuze
55, §3.3 en §3.4). Z0b bouwt geen functie: het zet de contracten van X2 (gezichten automatisch)
en X3 (zoeken op beeld) bij de code van Z0, zodat X2, X3 en de web ertegen kunnen bouwen. Alles is
erbij gezet, met een standaard: een oude client krijgt hetzelfde antwoord als ervoor.

Het web-deel staat in `z0b-web.md` (wip/z0b-web). De tabellen van gezichten en beeld komen niet
in Z0b: die zijn van A0 (0011 en 0012, §3.1a). Er komt geen model, geen numpy of onnxruntime en
geen nieuwe afhankelijkheid.

Z0b bouwt op wat Jonas besliste: gezichten en zoeken op beeld zitten in fase 6 en staan per
gebruiker standaard uit; Vraag 4 is nog open, dus de standaard (alleen de eigenaar) geldt; Vraag
8–14 op de standaard. Van B49–B70 zijn alleen B53 en B57 beslist.

## Wat er is

### De vormen (§3.3)

In **`api/app/search/types.py`**, elk veld met een standaard:

- nieuwe waarden:
  - `Match` + `"image"`;
  - `AnalysisCode` + `"no_preview"`, `"image_bad"` (de codes van de stap `embed`);
  - `RebuildWhat` + `"faces"`, `"beeld"`;
  - `SearchProgressEvent.what` + `"faces"`, `"beeld"`.
- nieuwe types:
  - `SearchMode = Literal["text", "image"]`;
  - `BeeldFallback = Literal["slow", "down", "no_model"]`;
  - `AnalyseState = Literal["ok", "down", "models_missing", "off"]` (de container analyse,
    gedeeld door X2 en X3).
- nieuwe velden op bestaande vormen:
  - `SearchParams.mode: SearchMode = "text"`, `SearchParams.like: UUID | None = None` (niet in
    `SearchRule`, dus een regel van een slim album met `mode` geeft 422);
  - `SearchPage.mode: SearchMode = "text"`, `SearchPage.fallback: BeeldFallback | None = None`;
  - `AdminSearch.faces: AdminFaces | None = None`, `AdminSearch.beeld: AdminBeeld | None = None`;
  - `AdminSearchPatch.faces_allowed: bool | None = None`, `AdminSearchPatch.beeld: bool | None =
    None`.
- nieuwe vormen:
  - `CodeCount`, `AnalyseModel`, `AnalyseLimits`, `AnalyseQueue`, `AnalyseQuality`;
  - `FacesBench` en `AdminFaces` (het blok "Gezichten" op `/beheer`, Keuze 67);
  - `BeeldHour`, `BeeldSpace`, `BeeldBench` en `AdminBeeld` (het blok "Zoeken op beeld", Keuze
    82);
  - `BeeldStatus` (`GET /photos/beeld`): `state` (`ok`, `bezig`, `uit`, `beheer_uit`,
    `niet_geinstalleerd`, `weg`) en `done` (0..1 of null).
- `AnalysisOut` krijgt geen veld voor `embed`: of er een vector is, zegt de API niet (Keuze 79).

In **`api/app/people/faces_types.py`** (nieuw; gelijk aan `web/src/photos/faces/types.ts`): de
vormen uit de tabel van de API van X2.

- de grenzen `MAX_FACES = 200`, `FACES_PAGE = 60` en `CropSize = Literal[96, 192]`;
- `FaceSuggest`, `FaceOut` (een gezicht: id, node, kader 0..1, persoon of voorstel; nooit een
  vector of een score);
- `FacesPhotos`, `FacesCount`, `FacesStatus`;
- `PersonSuggestions` (hooguit 6 uitsneden), `SuggestionsByPerson`, `FacePage` (hooguit 60),
  `FaceGroup` (hooguit 12), `FaceGroups`;
- de bodies `FacesIn` (1 tot 200 gezichten, `extra="forbid"`), `FacesConfirmIn` (`person_id` of
  `name`, niet allebei) en `FacesRejectIn` (`person_id`);
- `FacesQueued`.

In **`api/app/prefs.py`**:

- `PHOTO_PREFS["beeld"] = "uit"` (naast `faces: "uit"`), en `OnOff = Literal["aan", "uit"]`;
- `PhotoSettings` + `faces: OnOff = "uit"`, `faces_forbidden: bool = False`,
  `faces_available: bool = False`, `beeld: OnOff = "uit"`;
- `PhotoSettingsPatch` + `faces: OnOff | None = None`, `beeld: OnOff | None = None`;
- `photo_prefs()` geeft vijf sleutels; `beeld` is altijd de standaard, want `photo_pref` heeft
  nog geen kolom `beeld` (0012, A0);
- de docstring zegt nu Vraag 2 (er stond Vraag 3).

In **`api/app/analysis.py`**: `Step.EMBED = "embed"`, met versie 1 in `VERSIONS`. De kolom
`analysis.step` is `String(8)`, dus `embed` past zonder migratie.

### De instelling en de rij

- **`api/app/config.py`**: `analyse_enabled: bool = False` (`PLATFORM_ANALYSE_ENABLED`, Keuze
  59). Alleen die; de rest van §3.11 is van A0.
- **`api/app/jobs.py`**: `LANES` eindigt nu op `"ocr", "analyse"`. `active_lanes` neemt
  `analyse` alleen mee als `analyse_enabled` aan staat, los van media. De rij heeft één worker
  (de standaard van `lane_workers`).

### De haak na de grote preview (Keuze 65)

**`api/app/previews.py`**: `register_large(fn)` en `forget_large()`. `fn` krijgt
`(maker, sha)` en komt aan de beurt nadat de ronde `preview.large` de 1024 gemaakt heeft, ná
`announce`. Een fout in `fn` komt in het log (`previews.large`) en de volgende gaat gewoon door;
de preview zelf is dan al klaar. Dezelfde `fn` twee keer aanmelden telt één keer. Niemand meldt
zich nog aan (X2 en X3 doen dat).

### De routes (§3.4)

Allemaal stubs: eerst de inlog (en voor wissen de rol), dan 501 `not_built` met het pakket erbij,
net zoals de stubs van Z0. Een body met een fout geeft 422, vóór de stub.

| Bestand | Route | Antwoord | Pakket |
|---|---|---|---|
| `routers/people_faces.py` | `GET /people/faces/status` | `FacesStatus` | X2 |
| | `GET /people/faces/suggestions?person=&cursor=&limit=` | `SuggestionsByPerson \| FacePage` | X2 |
| | `GET /people/faces/groups?cursor=` | `FaceGroups` | X2 |
| | `GET /people/faces/groups/{key}?cursor=` | `FacePage` | X2 |
| | `POST /people/faces/confirm` (`FacesConfirmIn`) | 204 | X2 |
| | `POST /people/faces/reject` (`FacesRejectIn`) | 204 | X2 |
| | `POST /people/faces/hide` (`FacesIn`) | 204 | X2 |
| | `POST /people/faces/reset` (`FacesIn`) | 204 | X2 |
| | `POST /people/faces/wipe-unnamed` | 202 `FacesQueued` | X2 |
| | `GET /people/faces/{face_id}/crop?size=96\|192` | WebP | X2 |
| | `GET /files/{id}/faces` | `list[FaceOut]` | X2 |
| | `POST /files/{id}/faces/rescan` | 202 | X2 |
| `routers/beeld.py` | `GET /photos/beeld` | `BeeldStatus` | X3 |
| | `DELETE /admin/search/beeld` (beheer) | 202 | X3 |
| `routers/search.py` | `GET /search?mode=image&q=` en `&like=` | 501 | X3 |

`people_faces.router` staat in `main.py` vóór `people.router`, zodat `/people/faces/...` nooit bij
`/people/{person_id}` uitkomt. `GET /search` zonder `mode` (of met `mode=text`) is niet
veranderd: hetzelfde antwoord, met `mode: "text"` en `fallback: null` erbij.

### De toegangslog

`QUERY_PATHS` in `main.py` haalde de hele query string onder `/search`, `/photos` en `/people`
al weg; dat geldt dus ook voor `mode=image&q=...`, `like=...`, `/photos/beeld` en
`/people/faces/...`. Nu staat dat in een test: de regel van uvicorn met
`/api/v1/search?mode=image&q=...` (ook met `like`) wordt precies
`"GET /api/v1/search?*** HTTP/1.1" 501`, en de 501 van zoeken op beeld zet de vraag niet in zijn
antwoord en niet in een andere logregel.

### De tests

Erbij of uitgebreid, allemaal in `api/tests/`:

- `search/test_contracts.py`:
  - een oude vraag en een oud antwoord blijven gelijk (de dump van een oude `AdminSearch`,
    `PhotoSettings` en `SearchPage` is de oude data plus de nieuwe standaarden);
  - `/search?q=x` en `/search?q=x&mode=text` geven byte voor byte hetzelfde, met precies de
    sleutels van Z0 plus `mode` en `fallback`;
  - een regel met `mode` geeft 422;
  - de nieuwe waarden;
  - de stappen zijn precies `text, hash, ocr, embed`, de sleutels van de foto-instellingen
    precies vijf.
- `search/test_api_stubs.py`: de 14 nieuwe routes in `ENDPOINTS` (401, 403, 501 met het
  pakket); `mode=image` en `like` geven 501 `X3`, een andere `mode` of een kapotte `like` 422;
  confirm met `person_id` én `name` (of geen van beide) geeft 422.
- `search/test_hotspots.py`: na de grote preview komt de haak één keer aan de beurt (en niet na
  de eerste ronde); een fout gaat door; zonder aanmelding gebeurt niets; na `import app.main` is
  er niemand aangemeld.
- `search/test_log_hiding.py`: zes nieuwe regels en twee tests voor zoeken op beeld.
- `search/test_own_live.py`: `routers/people_faces.py`, `routers/beeld.py` en de functies met
  `beeld` vallen onder de wachter; `people/faces_types.py` staat bij `NOT_A_QUERY`; de tabellen
  `face`, `face_person`, `face_scan` en `blob_embed` (en hun modellen) tellen mee, met een proef
  dat de wachter ze ziet.
- `search/test_web_contracts.py`: de vormen van gezichten en beeld tegen de web, de filters
  zonder `mode` en `like`, en de grenzen (`MAX_FACES`, `FACES_PAGE`, `CROP_SIZES`).
- `test_jobs_lanes.py`: de nieuwe exacte lijst van rijen en workers, en dat `analyse` alleen met
  `PLATFORM_ANALYSE_ENABLED` draait.
- `test_media_off.py`: zonder media draaien `ocr` en `analyse` niet.

## Afwijkingen van het ontwerp

1. **De fix van `node_bu_text` gaat naar 0011 (A0)**, niet naar 0010. 0010 is gepusht, en een
   gepushte migratie verandert niet; `app/search/schema.py` (`TRIGGERS`, `TRIGGERS_NO_FTS`) blijft
   dus ook gelijk, want `test_schema_triggers.py` eist dat het gelijk is aan 0010. In het
   bouwontwerp staat dat bij "Z0b kan het in 0010 repareren" (§3.1a). Keuze 55 (de regel "Z0b zet
   beide recht in 0010"), het lijstje "in 0010" van §3.3 en de tabel van eigenaars (`schema.py`,
   `0010_zoeken.py`: Z0b) zeggen nog 0010; dat lees je nu als 0011, A0.
2. **`failed_by_code` staat per blok** (`AdminFaces.failed_by_code` en
   `AdminBeeld.failed_by_code`, als `list[CodeCount]`), niet als één veld op `AdminSearch`. Zo
   weet je bij welk blok een code hoort, en een lijst van paren kan de vergelijking met de web
   lezen (een `Record` niet).
3. **De velden van `AdminFaces` en `AdminBeeld` zijn een voorstel**, gelijk aan
   `web/src/admin/searchApi.ts` van Z0b-web. Keuze 67 en 82 noemen wat er te zien is, niet de
   vorm. X2 en X3 mogen ze veranderen zolang de web meeverandert.
4. **`app/people/faces_types.py` is volgens §2.0 van A0**, maar Z0b zette het neer, zodat de
   stubs hun antwoordmodel hebben en de web ertegen kan bouwen. A0 legt het vast.
5. **`GET /people/faces/suggestions` heeft twee vormen**: zonder `person` per persoon
   (`SuggestionsByPerson`), met `person` de gezichten van die persoon (`FacePage`). Het
   antwoordmodel is dus `SuggestionsByPerson | FacePage`.
6. **`like` zonder `mode=image` geeft ook 501 `X3`.** "Meer zoals deze" is altijd zoeken op
   beeld; anders zou `like` stil genegeerd worden.
7. **`MAX_FACES` staat in het model**: meer dan 200 gezichten geeft de 422 van de validatie, niet
   een eigen code `faces_too_many`. X2 kan die code nog zetten (Open).
8. **`register_large(fn)` krijgt `(maker, sha)`** en komt alleen aan de beurt na de ronde
   `preview.large`. Een eerste ronde die alle maten al in één keer maakte, roept alleen de haken
   van `register_ready`. En `announce` (dus `preview.ready` en `register_ready`) draait ook na de
   grote ronde, al zei het ontwerp dat niet; dat was in Z0 al zo.
9. **De wachter van `own_live` kent de tabellen van 0011 en 0012 al** (`face`, `face_person`,
   `face_scan`, `blob_embed`) en hun modellen onder een aangenomen naam (`Face`, `FacePerson`,
   `FaceScan`, `BlobEmbed`). A0 moet die namen gebruiken, of de lijst in
   `tests/search/test_own_live.py` gelijk zetten.
10. **De statussen van de stubs**: 204 voor confirm, reject, hide en reset; 202 voor
    wipe-unnamed, rescan en `DELETE /admin/search/beeld`. De tabel van X2 noemt ze niet allemaal.
11. **`beeld` in `photo_prefs()` is altijd `"uit"`** tot 0012 de kolom maakt; `PATCH
    /me/photo-settings` neemt het al aan, maar die route is nog een stub.

## Open

1. **De migraties 0011 en 0012** (A0): de tabellen van gezichten en beeld, de kolom `beeld` op
   `photo_pref`, en de fix van `node_bu_text` (afwijking 1).
2. **`tests/search/test_web_contracts.py` is op wip/z0b-api alleen rood** (88 errors, het
   bestand `web/src/photos/faces/types.ts` ontbreekt) tot de samenvoeging met wip/z0b-web. Tegen
   de boom van Z0b-web zijn ze alle 88 groen.
3. **`faces_too_many`**: X2 zet de 422 van meer dan 200 gezichten om naar die code, of het
   ontwerp laat hem vallen (afwijking 7).
4. **De rest van §3.11** (de container analyse, `compose.analyse.yaml`, de modellen en hun
   instellingen) is van A0.

Bekend en niet van Z0b:

5. `tests/test_media_names.py::test_fuzz_10000_namen_met_vaste_seed` meet tijd en faalt alleen op
   deze VM; hij staat ongewijzigd.
