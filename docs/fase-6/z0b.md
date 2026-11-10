# Z0b: de contracten van gezichten en zoeken op beeld

Z0b is de kleine stap na Z0 ([bouwontwerp.md](bouwontwerp.md), Keuze 55; §3.3, §3.4 en §3.5). Hij
bouwt geen functie. Hij zet de contracten van X2 (gezichten automatisch) en X3 (zoeken op wat er
op een foto staat) bij de code van Z0, zodat golf 1–4 kunnen bouwen zonder dat X2 en X3 later aan
hun bestanden en hun exacte lijsten komen. Alles is erbij gezet, met een standaard: een oude
client krijgt hetzelfde antwoord als ervoor. De routes van X2 en X3 geven `501 not_built("X2")`
of `not_built("X3")`, net als de stubs van Z0. Zoeken werkt nog zoals na Z0: op naam, met LIKE
(`LikeFallback`).

Z0b is in twee delen gebouwd, elk op een eigen tak vanaf `3e6c1c2` (0.7.1 plus Z0). Op 4 november
2026 zijn ze samengevoegd op `claude/stappenplan-uitwerken-lu4uv3`. Dit verslag zegt wat er samen
staat, wat de samenvoeging veranderde, en waar het bewijs voor elke regel van Keuze 55 is. De
verslagen per deel blijven de bron voor de details.

Z0b bouwt op wat Jonas koos: gezichten en zoeken op beeld zitten in fase 6 en staan per gebruiker
standaard uit. Vraag 4 (wie ziet de plek) is niet beantwoord, dus de standaard geldt: alleen de
eigenaar. Vraag 8–14 op hun standaard. Van B49–B70 zijn alleen B53 en B57 besloten.

## De delen

| Deel | Tak | Commits | Verslag |
|---|---|---|---|
| Z0b-api: de vormen, de instelling en de rij `analyse`, de haak na de grote preview, de stubs | `wip/z0b-api` (`39adbcc`) | 4 | [z0b-api.md](z0b-api.md) |
| Z0b-web: de vormen en aanroepen, de lege plekken, de gebieden, de catalogi, het budget | `wip/z0b-web` (`f0cc1ee`) | 2 | [z0b-web.md](z0b-web.md) |

### Wat er nu staat

In de API (de lijst met standaarden staat in [z0b-api.md](z0b-api.md), "De vormen"):

- **`app/search/types.py`**: `mode` (standaard `text`) en `like` in de vraag van `/search`, niet
  in de regel van een slim album; `mode` en `fallback` (standaard `null`) in `SearchPage`; `image`
  als `Match`; `no_preview` en `image_bad` als code van de analyse; `faces` en `beeld` bij
  herbouwen en in `search.progress`; de stand van de container (`AnalyseState`); de blokken
  `AdminSearch.faces` en `.beeld` (standaard `null`) en hun schakelaars in de PATCH; `BeeldStatus`.
- **`app/people/faces_types.py`** (nieuw): de vormen van de API van X2, met `MAX_FACES` 200,
  `FACES_PAGE` 60 en de uitsneden 96 en 192.
- **`app/prefs.py`**: `faces` en `beeld` per gebruiker, standaard `uit`; de docstring noemt nu
  Vraag 2. Na de review ook de regel voor aanzetten (`check_switch`) en de aanmelding van X2 en
  X3 (`register_switch`); zie De review, V3 en F4.
- **`app/analysis.py`**: `Step.EMBED`. **`app/jobs.py`**: de rij `analyse`, alleen met
  `PLATFORM_ANALYSE_ENABLED` (standaard uit, de enige nieuwe instelling in `app/config.py`).
- **`app/previews.py`**: `register_large(fn)`, na de ronde `preview.large`. Niemand meldt zich nog
  aan. Na de review stopt een haak na `LARGE_HOOK_S` (5 s); zie De review, V4.
- **De routes**: twaalf van X2 in `routers/people_faces.py` (vóór `routers/people.py` gemount),
  twee van X3 in `routers/beeld.py`, en `GET /search` met `mode=image` of `like`: allemaal `501`
  na de inlog. De query string onder `/search`, `/photos` en `/people` ging al niet in de
  toegangslog; dat staat nu ook voor zoeken op beeld in een test.

In de web (de tabellen staan in [z0b-web.md](z0b-web.md), "Wat er is"):

- de vormen en aanroepen van X3 in `src/search/`, die van X2 in `src/photos/faces/` (nieuw), de
  foto-instellingen in `src/photos/people/`, de blokken in `src/admin/searchApi.ts`;
- de lege plekken: de blokken "Gezichten" en "Zoeken op beeld" op `/beheer/zoeken` (een kop en
  "komt later in fase 6"), en `LikeRow` ("Meer zoals deze") in het infopaneel, die niets toont;
- de gebieden `photos_faces` en `beeld`, de catalogi `faces.ts` en `beeld.ts`;
- de regels `faces` (12 kB), `beeld` (15 kB) en `likeRow` (3 kB) in `scripts/budget.mjs`.

Niet in Z0b, en niet aangeraakt: de migraties 0001–0010, `TRIGGERS` en `TRIGGERS_NO_FTS` in
`app/search/schema.py` (na de review staan ze in een deel per migratie, met precies dezelfde
triggers; zie De review, F5), `pyproject.toml`, `uv.lock`, de Dockerfile en `package.json`. Geen tabel
van gezichten of beeld (dat zijn 0011 en 0012, A0), geen numpy of onnxruntime, geen model.

## De samenvoeging

In deze volgorde, met `git merge --no-ff`:

1. `wip/z0b-api` (`db3c1e1`): geen conflict.
2. `wip/z0b-web` (`2c4e52e`): geen conflict. De twee delen raken geen bestand samen.

Daarna, omdat de delen elkaar nu zien:

- **De contracttest was meteen groen.** `api/tests/search/test_web_contracts.py` legde Z0b-api al
  naast de web van Z0b-web (de regels uit Open 1 van [z0b-web.md](z0b-web.md)); op de tak van
  Z0b-api alleen ontbrak `web/src/photos/faces/types.ts`. Na de samenvoeging: 88 groen, samen met
  `test_contracts.py`, `test_api_stubs.py` en `test_error_catalog.py` 126.
- **Een vast getal in de query string kwam nooit bij de route** (`8b979ca`). Elke aanroep uit de
  vitest van de web (`search`, `photos/people`, `photos/map`, `dupes`, `admin/searchApi` en
  `photos/faces`, 77 in totaal) is met echte id's tegen de API gedaan. 74 kwamen bij hun stub
  (`501 not_built`), zoeken en de suggesties gaven 200, en de woorden van een pagina 401 met het
  nep-token van de vitest (zoals bedoeld). Twee kwamen er niet: `/people?hidden=0` en `?hidden=1`
  gaven 422. Hetzelfde gold voor het adres van een uitsnede van X2 (`faceCropUrl`:
  `/people/faces/{id}/crop?size=96`), ~~dat geen aanroep is maar een `<img>`~~ (na de review een
  fetch met de inlog, `faceCrop`; zie De review, V2). De oorzaak: een query
  string is tekst, en `Literal[0, 1]` of `Literal[96, 192]` neemt alleen een int. Nu zet een
  ~~`BeforeValidator(int)`~~ `QueryInt` (na de review: alleen gewone cijfers; zie De review, V1)
  de tekst eerst om; 2 of 128 geeft nog 422, en de OpenAPI blijft een enum
  van getallen. `hidden` is van Z0 (de stub van Z6), maar het is dezelfde fout op dezelfde plek,
  dus hij ging mee. Test: `test_een_vast_getal_in_de_query_string_komt_bij_de_stub`
  (`tests/search/test_api_stubs.py`) loopt elke stub van fase 6 langs met zo'n keuze in de query
  (uit de OpenAPI), stuurt elke waarde als tekst en eist 501, en eist dat het precies `hidden` en
  `size` zijn. Zonder de fix faalt hij op `hidden`; met alleen de fix van `hidden` op `size`.
- **De vitest zag de nieuwe woordenboeken niet in deze checkout** (`56e6c43`). Op de tak van
  Z0b-web was `src/admin/searchTab.test.ts` groen, hier faalde hij: "geen woordenboek voor
  `photos_faces`" (en `beeld`). Niet de code, maar de module-cache van vitest 5
  (`fsModuleCache`, standaard aan, in `node_modules/.vitest-cache`): die bewaart een getransformeerd
  bestand onder een sleutel van zijn bron, en slaat een bestand met een glob alleen over als de
  bron letterlijk `import.meta.glob(` bevat. `src/i18n/index.ts` schreef de glob met een
  typeargument ertussen, dus kwam hij toch in de cache, met de lijst woordenboeken van 3 november;
  de bron veranderde niet, de sleutel dus ook niet. In CI is de cache leeg. Nu staat het type met
  `as` achter de glob (ook in `i18n.test.ts` en `player/playback.ts`, waar hetzelfde kon), en
  `src/globCache.test.ts` eist dat elke glob er zo staat en dat de bekende aanroepen er zijn. Hij
  faalt op de oude bron met precies die drie bestanden; daarna is de tab groen zonder de cache te
  wissen, en vitest schrijft voor die bestanden niets meer in de cache.
- **De verslagen per deel** kregen een notitie waar ze iets over de andere tak zeiden dat nu niet
  meer klopt (doorgestreept, met wat er nu is): Open 2 van [z0b-api.md](z0b-api.md), Open 1–3 van
  [z0b-web.md](z0b-web.md), en in [z0.md](z0.md) de regel van Z0b onder "Klaar als" en Open 1.
- **Het bouwontwerp**: bij Keuze 55 ("Z0b zet beide recht in 0010") en in §3.1a ("Z0b kan het in
  0010 repareren") staat nu dat 0010 gepusht is en 0011 (A0) het doet. De README noemt Z0b in de
  regel met de stand.

## Klaar als (Keuze 55)

| Klaar als | Bewijs |
|---|---|
| de velden en waarden van Keuze 66 en 80, elk met een standaard | `tests/search/test_contracts.py`: `test_de_nieuwe_waarden_van_z0b` (de woorden van `Match`, `SearchMode`, `BeeldFallback`, `AnalysisCode`, `RebuildWhat`, `AnalyseState`; `AnalysisOut` zonder veld voor `embed`, Keuze 79). Aan beide kanten gelijk: `tests/search/test_web_contracts.py` (de paren van `BeeldStatus`, `CodeCount`, `AnalyseModel`, `AdminFaces`, `AdminBeeld`, `PhotoSettingsPatch` en de twaalf vormen van X2; zes nieuwe proeven in `test_de_vergelijking_ziet_een_ander_type`; `test_gezichten_bevestigen_en_de_grenzen`). Groen. |
| een oude client krijgt hetzelfde antwoord | `test_zoeken_zonder_mode_geeft_hetzelfde_antwoord`: `/search?q=x` en `/search?q=x&mode=text` geven byte voor byte hetzelfde, met precies de sleutels van Z0 plus `mode` en `fallback`. `test_een_oude_vraag_en_een_oud_antwoord_blijven_gelijk`: een oude `AdminSearch`, `PhotoSettings` en `SearchPage` geven de oude data plus de nieuwe standaarden. `test_een_regel_met_mode_geeft_422`: een slim album zoekt nooit op beeld. Groen. |
| `Step.EMBED` | `test_contracts.py` eist de stappen precies `text, hash, ocr, embed`. Groen. |
| de rij `analyse` in `LANES`, alleen actief met de container (Keuze 59) | `tests/test_jobs_lanes.py`: `test_de_rij_analyse_alleen_met_de_container` en de exacte lijst van workers per rij (`test_workers_per_rij`); `tests/test_media_off.py`: met media aan draaien alle rijen behalve `ocr` en `analyse`. Groen. |
| de sleutels `faces` en `beeld` in `PHOTO_PREFS`, standaard `uit` | `test_contracts.py` eist de vijf sleutels met hun standaard, en de velden van `PhotoSettings` en `PhotoSettingsPatch` precies. Groen. |
| de routes van X2 en X3 als stub (`501 not_built("X2")` of `"X3"`) | `tests/search/test_api_stubs.py`: de 14 routes in `ENDPOINTS` met pad, body, antwoordmodel en status (`test_elk_eindpunt_van_paragraaf_3_4_bestaat_met_zijn_vorm`), 501 met het pakket (`test_elke_stub_geeft_501_not_built`), 401 en 403 (`test_zonder_login_geen_toegang_en_beheer_alleen_voor_beheerders`), `test_zoeken_op_beeld_is_van_x3`, `test_gezichten_bevestigen_met_id_of_naam_niet_allebei`, en sinds de samenvoeging `test_een_vast_getal_in_de_query_string_komt_bij_de_stub`. Groen. |
| de vraag van zoeken op beeld nooit in de log | `tests/search/test_log_hiding.py`: zes regels voor X2 en X3 in de tabel, `test_zoeken_op_beeld_staat_niet_in_de_toegangslog` (de regel wordt precies `"GET /api/v1/search?*** HTTP/1.1" 501`) en `test_zoeken_op_beeld_logt_de_vraag_niet`. Groen. |
| `own_live` ziet ook gezichten en beeld | `tests/search/test_own_live.py`: `routers/people_faces.py`, `routers/beeld.py` en `beeld` onder de wachter, de tabellen van 0011 en 0012 erbij, en `test_de_wachter_kijkt_naar_gezichten_en_beeld`. Groen. |
| `previews.register_large(fn)` (Keuze 65) | `tests/test_previews.py`: `test_na_de_grote_preview_is_de_volgende_aan_de_beurt`, `test_een_fout_na_de_grote_preview_gaat_door`, `test_zonder_aanmelding_niets_na_de_grote_preview`; `tests/search/test_hotspots.py`: `test_niemand_meldt_zich_aan_na_de_grote_preview`. Groen. |
| de lege plekken in de web (`LikeRow`, de blokken op `/beheer`) | `src/viewer/infoRows.test.ts` (wanneer "Meer zoals deze" laadt, en dat `LikeRow` niets toont), `src/admin/searchTab.test.ts` (de tab eindigt in nl en en met precies de twee blokken). Groen. |
| de gebieden `photos_faces` en `beeld`, de catalogi `faces.ts` en `beeld.ts` | `src/i18n/i18n.test.ts` (de exacte lijst `AREAS`), `npm run check:i18n -- --strict`; `src/photos/faces/api.test.ts` en `src/search/api.test.ts` (de catalogus is precies de lijst van codes); `tests/test_error_catalog.py`. Groen. |
| de regels in `budget.mjs` | `scripts/budget.test.mjs` (de grenzen, de groepen, de lazy plekken en de eerste lading, exact) en `npm run budget` na de build. Groen. |
| de tests met een exacte lijst eisen de nieuwe lijst; geen test wordt zwakker | Bij de samenvoeging is elke verwijderde regel in de tests nagelopen (`git diff 3e6c1c2 -- api/tests web/src`): elke exacte lijst kreeg de nieuwe exacte lijst (de rijen en workers, de stappen, de sleutels en velden van de foto-instellingen, `ENDPOINTS`, de namen in de OpenAPI, de paren van de contracttest, `AREAS`, de aanroepen in de vitest, de regels van het budget). Geen gelijkheid werd een deelverzameling, geen test is weg. Eén eis veranderde met opzet: `PhotoSettingsPatch` neemt nu `faces` (de API van X2 zet het via `PATCH /me/photo-settings`); daar staat nu een test die de velden precies eist. |
| de docstring van `app/prefs.py` noemt de goede vraag | Vraag 2 (`d96de96`). |
| `node_bu_text` maakt het kader van `photo_person` leeg bij nieuwe inhoud (Keuze 25a) | **Niet in Z0b**: 0010 is gepusht. Dat doet 0011 (A0); zie Afwijkingen 1. |

## Gedraaid op de samengevoegde tak

Op `56e6c43` (de API-suite op `8b979ca`; daarna veranderde alleen de web), alles onder
`taskset -c 0-2`:

| Check | Uitkomst |
|---|---|
| `uv run ruff check .` en `uv run ruff format --check .` | groen (497 bestanden) |
| `uv run pytest -q` (de hele API-suite) | 4337 groen, 2 overgeslagen, 193 niet gekozen (29 min). `test_fuzz_10000_namen_met_vaste_seed` was deze keer ook groen. |
| `uv run pytest -q -m media` | 178 groen (2 min) |
| `tests/search` als nobody (`/tmp/ci-nobody/run.sh`, zoals CI) | 387 groen, 9 niet gekozen |
| `npm run typecheck`, `npm test`, `npm run check:i18n -- --strict`, `npm run build`, `npm run budget` | groen: 995 tests in 93 bestanden, 0 meldingen van i18n, binnen het budget (eerste lading JS 97,4 van 100 kB; `faces` 0,0 en `beeld` 0,0 kB, nog niet gebouwd; `likeRow` 0,2 van 3 kB) |
| `SLIM_CPUS=0-2 e2e/slim.sh` | groen in 143 s: 387 tests op SQLite 3.46.1 in `python:3.12-slim`, de web-app was bij, 9 e2e met marker `zoek` |
| de e2e van CI (`-m "not perf and not media and not media_perf and not office and not office_real and not provider_real and not ocr"`) | 146 groen, 129 niet gekozen (13 min) |

Eén run faalde, en niet door Z0b: de eerste `e2e/slim.sh`, die naast de API-suite op dezelfde
cores liep. Stap 1 gaf `test_spike_deadline.py::test_rondes_a_b_en_c` rood; zie
Open 11. Alleen gedraaid was slim.sh groen.

## Afwijkingen

Van de samenvoeging:

1. **De fix van `node_bu_text` (Keuze 25a) gaat naar 0011 (A0), niet naar 0010.** Keuze 55 zegt
   dat Z0b het kader van `photo_person` bij nieuwe inhoud leeg laat maken in 0010. Maar 0010 is
   gepusht en draait misschien al op de server van Jonas; een gepushte migratie verandert niet.
   Daarom blijven ook `TRIGGERS` en `TRIGGERS_NO_FTS` in `app/search/schema.py` gelijk aan 0010
   (`test_schema_triggers.py` eist dat). In het bouwontwerp staat dat bij Keuze 55 en in §3.1a. Het
   lijstje "in 0010" onder "Wat Z0b toevoegt" (§3.3) en de rij van `schema.py` en `0010_zoeken.py`
   in "Hotspots van Z0b en A0" (§3.5) lees je als 0011, A0.
2. **`/people?hidden=` is van Z0, maar ging mee in de fix van de query string** (`8b979ca`):
   dezelfde fout als bij de uitsnede van X2, en de test loopt alle stubs van fase 6 langs.
3. **Drie bestanden van de web buiten Z0b veranderden** (`56e6c43`): `i18n/index.ts`,
   `i18n/i18n.test.ts` en `player/playback.ts` schrijven de glob zonder typeargument, en
   `src/globCache.test.ts` is nieuw. Alleen de schrijfwijze van het type; wat ze laden is gelijk.

Van de delen: [z0b-api.md](z0b-api.md) (12) en [z0b-web.md](z0b-web.md) (9), elk onder
"Afwijkingen". De belangrijkste:

- de velden van `AdminFaces` en `AdminBeeld` en de vormen van de routes van X2 zijn een voorstel
  (Keuze 67 en 82 noemen wat er te zien is, niet de vorm); A0, X2 en X3 leggen ze vast, in de web
  en de API tegelijk;
- `failed_by_code` staat per blok, als lijst van `{code, n}`, niet als één veld op `AdminSearch`;
- `app/people/faces_types.py` is volgens §2.0 van A0; Z0b zette het neer zodat de stubs en de web
  een vorm hebben;
- meer dan 200 gezichten in één verzoek geeft de 422 van de validatie, nog niet de code
  `faces_too_many`;
- `like` zonder `mode=image` geeft ook 501 `X3`;
- de tests van de haak na de grote preview staan in `tests/test_previews.py`, niet in
  `tests/search/test_hotspots.py`, omdat de renderer in de stap van `e2e/slim.sh` geen Pillow
  heeft;
- `faces` in het budget is een groep (`ZOEK_GROUPS`), geen pagina; X2 heeft geen eigen route;
- de blokken staan in `admin/SearchTab.tsx` (het bestand van Z7), niet in `Beheer.tsx`.

## De review

Na de samenvoeging lazen twee reviews Z0b: een met de lens "veilig" (V1–V4) en een met de lens
"contract" (F1–F5). V2 en F2 zijn hetzelfde punt, net als V3 en F1. Elk punt is eerst nagegaan;
geen bleek onjuist. Elk is gerepareerd met een test die zonder de reparatie faalt (nagegaan door
de test op de oude code te draaien, en voor de regel van V3 door elke regel apart weg te halen).
Geen punt vroeg een keuze van Jonas: van B49–B70 zijn nog steeds alleen B53 en B57 besloten, en
Vraag 4 staat op de standaard (alleen de eigenaar). Geen migratie, geen tabel, geen nieuwe
afhankelijkheid; `TRIGGERS` en `TRIGGERS_NO_FTS` zijn nog precies wat 0010 maakte.

| Punt | Wat er gedaan is | Test |
|---|---|---|
| V1 (should) | `d9d686c`. De `BeforeValidator(int)` uit de samenvoeging gaf bij `?hidden=abc` een 422 met de tekst van `int()` en de waarde erin, en `int()` nam ook `" 96"`, `"+1"`, `"9_6"` en `"٩٦"`. Nu zet `QueryInt` (`app/routers/stubs.py`) alleen gewone cijfers om (ASCII, hooguit 9); de rest blijft tekst en valt op de `Literal`: 422 "Ongeldige invoer", code `invalid_input`, als params alleen het veld. Voor `hidden` en `size`. | `test_een_vast_getal_in_de_query_string_komt_bij_de_stub` (`tests/search/test_api_stubs.py`) stuurt nu ook acht foute waarden (`abc`, 5000 cijfers, `9_6`, `٩٦`, `" 96"`, `+1`, `-0`, `1e1`): 422 met fouttype `literal_error`, geen `input`, en bij `abc` en de 5000 cijfers de waarde en `int()` niet in het antwoord. |
| V2 en F2 (should) | `d94e459`. De uitsnede wil de inlog als Bearer-header, zoals elke route van X2 (`CurrentUser` is alleen `HTTPBearer`); een `<img>` stuurt die niet mee, dus `faceCropUrl` gaf altijd 401, en een token in de query zou in de log en de geschiedenis komen. Nu haalt de web hem met `faceCrop(id, size)`: `api.rawRequest` met de header, en een `Blob` waar X2 een object-URL van maakt. De route bleef gelijk; de docstring, het bouwontwerp (de rij van de uitsnede) en [z0b-web.md](z0b-web.md) zeggen het nu. | `test_een_uitsnede_alleen_met_de_inlog_in_de_header` (zonder header, ook met `?token=` of `sig=`: 401 `not_logged_in`; met de header 501 `X2`); in `src/photos/faces/api.test.ts` de aanroepen van `faceCrop` via `rawRequest`, het type `image/webp`, en geen export met "url" in de naam. Zonder de reparatie faalt de vitest; de API-test bewaakt de route, die niet veranderde, en is ook op de oude code groen (zie De nacontrole). |
| V3 en F1 (should, blocking) | `8775064`. `PhotoSettingsPatch` nam `faces: "aan"` en `beeld: "aan"`, en niets weigerde dat zonder X2, X3 of de analyse (Keuze 59). Nu staat de regel in `app/prefs.py`, en Z6 roept hem aan vóór hij schrijft: `check_switch()` weigert "aan" met 409 `faces_unavailable` of 409 `beeld_not_installed` zolang X2 of X3 zich niet aanmeldt, en altijd als `PLATFORM_ANALYSE_ENABLED` uit staat, ook met een aangemeld pakket; met 403 `faces_forbidden` (Keuze 64) of 409 `beeld_off` (Keuze 82) als de beheerder het verbood; met 403 `guest` voor een gast. "uit" mag altijd. De route is nog de stub van Z6 (501); de docstring van `routers/photo_prefs.py` en Z6 in het bouwontwerp zeggen wat Z6 aanroept. | `tests/search/test_contracts.py`: `test_gezichten_en_beeld_aanzetten_kan_niet_zonder_x2_en_x3`, `test_x2_en_x3_melden_hun_schakelaar_aan`. Elke regel apart weggehaald (de analyse, de gast, "uit", het verbod) gaf rood. |
| V4 (nit) | `231d209`. Een haak van `register_large` had geen tijdgrens, en de taak houdt een plek in de rij `preview` bezet. Nu stopt `_after_large` een haak na `LARGE_HOOK_S` (5 s), met `previews.large te traag` in het log, en loopt de volgende. De docstring zegt dat een haak alleen werk in de rij zet en niet op de container of een render wacht. | `test_een_trage_haak_na_de_grote_preview_houdt_de_rij_niet_op` (`tests/test_previews.py`): een haak die nooit klaar is, daarna een gewone; zonder de grens loopt de test in zijn eigen limiet van 10 s. |
| F3 (should) | `46eac9b`. Er was geen plek voor X2 en X3 in de bestanden van Z4 en Z6. Nu laadt `PeoplePage` `PeopleFaces`, `PersonPage` `PersonFaces` (met `personId`) en het infopaneel `FacesRow` (alleen bij een foto, ingelogd, niet via een publieke link en niet bij een eerdere versie: `showFacesRow`), elk lazy met het gebied `photos_faces` in een `LazySlot`. Voor pagina's die er nog niet zijn staan de bestanden klaar: `FacesSwitch` en `BeeldSwitch` (Z6, Account → Foto's) en `BeeldResults` (Z4, `/zoeken` in de stand beeld). Elk toont niets. Het budget is gelijk gebleven (eerste lading en Bestanden); `faces` werd 0,3 kB. | `src/lazySlots.test.ts` (de regels in de bron van `PeoplePage`, `PersonPage` en `InfoPanel`, en alle zes plekken tonen niets); `src/viewer/infoRows.test.ts` (wanneer de rij gezichten laadt). |
| F4 (should) | `8775064`. Er was geen haak voor het deel van X2 en X3 in `/admin/search` en in de foto-instellingen, dus moesten X2 en X3 de bestanden van Z6 en Z7 openen. Nu melden ze zich aan: `prefs.register_switch()` (de stand en wat er gebeurt na een verandering; `photo_settings()` en `switched()` gebruiken die) en `search.hooks.register_admin()` (het blok, de schakelaar en herbouwen). Zonder aanmelding geeft `admin_block` `None`, en `admin_switch` en `admin_rebuild` 501 `X2` of `X3`. | `test_x2_en_x3_melden_hun_schakelaar_aan`, `test_de_foto_instellingen_met_de_stand_van_x2`, `test_x2_en_x3_melden_hun_deel_van_beheer_aan` (`tests/search/test_contracts.py`). |
| F5 (nit) | `c0e3c4c`. De plek voor de triggers van 0011 en 0012 stond er niet, en de test van de kopie in 0010 vergeleek met heel `TRIGGERS`. Nu zijn `TRIGGERS_0010` en `TRIGGERS_NO_FTS_0010` het bevroren deel van 0010, en `TRIGGERS` en `TRIGGERS_NO_FTS` alles na head (nu gelijk aan 0010). De kopie in 0010 wordt met het deel van 0010 vergeleken, de database met `TRIGGERS`, allebei exact; de migratie zelf is niet veranderd. | `test_de_plek_voor_de_triggers_van_0011_en_0012` (`tests/search/test_schema_triggers.py`): rood zonder de splitsing, en ook als `TRIGGERS` hetzelfde dict is als `TRIGGERS_0010`. |

Afwijkingen van de review:

1. **De vormen van de haken zijn een voorstel van Z0b**: `Switch`, `SwitchState` en `AdminPart`,
   en welke fout bij welke weigering hoort. X2, X3, Z6 en Z7 leggen ze vast; een vorm die
   verandert, verandert met zijn test in `test_contracts.py`.
2. **`beeld: "aan"` terwijl de beheerder beeld uit heeft, geeft 409 `beeld_off`**, de code van
   Keuze 82 voor "staat uit voor dit platform". Er is geen code `beeld_forbidden`, en de catalogus
   `beeld.ts` heeft er geen; bij gezichten is het 403 `faces_forbidden` (Keuze 64).
3. **V2 is opgelost in de web, niet in de route.** Een uitsnede blijft een route met de inlog als
   header; geen token of handtekening in de query, dus ook geen nieuw adres dat in de log kan.

## Gedraaid na de review

Op `f91bc3a` (daarna veranderde alleen dit verslag), alles onder `taskset -c 0-2`, één zware run
tegelijk:

| Check | Uitkomst |
|---|---|
| `uv run ruff check .` en `uv run ruff format --check .` | groen (497 bestanden) |
| `uv run pytest -q` (de hele API-suite) | 4344 groen, 2 overgeslagen, 193 niet gekozen (29 min). `test_fuzz_10000_namen_met_vaste_seed` was ook nu groen. |
| `uv run pytest -q -m media` | 178 groen (2 min) |
| `tests/search` als nobody (`/tmp/ci-nobody/run.sh`, zoals CI) | 393 groen, 9 niet gekozen |
| `npm run typecheck`, `npm test`, `npm run build`, `npm run check:i18n -- --strict`, `npm run budget` | groen: 999 tests in 94 bestanden, 0 meldingen van i18n, binnen het budget (eerste lading JS 97,4 van 100 kB en Bestanden 44,3 van 45 kB, allebei gelijk aan vóór de review; `faces` 0,3 van 12 kB, `beeld` 0,0 van 15 kB, `likeRow` 0,2 van 3 kB) |
| `SLIM_CPUS=0-2 e2e/slim.sh`, alleen gedraaid | groen in 147 s: 393 tests op SQLite 3.46.1 in `python:3.12-slim`, 9 e2e met marker `zoek` |
| de e2e van CI (`-m "not perf and not media and not media_perf and not office and not office_real and not provider_real and not ocr"`) | 146 groen, 129 niet gekozen (13 min) |

## De nacontrole

Daarna is elke commit van de review nagelopen, in een eigen worktree vanaf `31e8299`: alleen de
reparatie teruggedraaid, de test gedraaid (rood), de reparatie terug (groen). Waar een commit uit
meer regels bestaat, is ook elke regel apart weggehaald.

| Punt | Teruggedraaid of weggehaald | Uitkomst |
|---|---|---|
| V1 (`d9d686c`) | `QueryInt` weer `BeforeValidator(int)`; apart: zonder `isascii()`, zonder de grens van 9 cijfers | Elk rood. Met `int()` is de melding bij `?hidden=abc` "invalid literal for int() with base 10: 'abc'"; zonder `isascii()` komt `٩٦` bij de stub (501); zonder de grens staat de melding van `int()` over 5000 cijfers in het antwoord. |
| V3, F1 en F4 (`8775064`) | `app/prefs.py` en `app/search/hooks.py` van vóór de commit; daarna 14 regels apart | Van vóór de commit: rood (er is geen `forget_switches`). Rood met elk van: de analyse niet meegeteld, geen gastregel, "uit" niet overgeslagen, geen verbod (faces, beeld), "niet beschikbaar" niet geweigerd (faces, beeld), `switched` ook zonder verandering, `photo_settings` zonder de stand van X2, `admin_switch` en `admin_rebuild` zonder 501, `reset` zonder de delen van beheer. **Groen bleven** `register_switch` en `register_admin` zonder hun controle op de sleutel; zie hieronder. |
| V2 en F2 (`d94e459`) | `web/src/photos/faces/api.ts` met `faceCropUrl` | Rood in `src/photos/faces/api.test.ts` (`faceCrop is not a function`). `test_een_uitsnede_alleen_met_de_inlog_in_de_header` is ook op de oude code groen: de route veranderde niet (alleen de docstring). Hij bewaakt dat de route geen token of `sig` in de query neemt; de vitest is de test van de reparatie. |
| F3 (`46eac9b`) | `PeoplePage.tsx`, `PersonPage.tsx` en `InfoPanel.tsx` elk apart van vóór de commit; `infoRows.ts` zonder `showFacesRow` | Elk rood, in `src/lazySlots.test.ts` of `src/viewer/infoRows.test.ts`. |
| V4 (`231d209`) | `previews.py` van vóór de commit; apart: de grens weg (de constante blijft), de regel in het log weg, één grens om de hele lus | Elk rood. Zonder de grens loopt de test in zijn `fail_after(10)` (na 14 s); zonder de regel mist "previews.large te traag"; met één grens om de lus komt de volgende haak niet meer aan de beurt. |
| F5 (`c0e3c4c`) | `schema.py` van vóór de commit; apart: `TRIGGERS = TRIGGERS_0010`, en alleen `TRIGGERS_NO_FTS` gedeeld | Rood. Zonder de splitsing 4 tests (de kopie, de plek, 0009 → 0010 met en zonder FTS5); met één gedeeld dict `test_de_plek_voor_de_triggers_van_0011_en_0012`. |

Wat de nacontrole veranderde, in `f74236d` (1–3) en in dit verslag (4–6):

1. **De controle op de sleutel heeft een test.** `test_alleen_faces_en_beeld_melden_zich_aan`
   (`tests/search/test_contracts.py`): `register_switch` met een instelling van Z6 (`location`,
   `memories`, `ocr_photos`) of iets anders dan `faces` en `beeld`, en `register_admin` met een
   deel van Z7 (`names`, `text`, `geo`, `hash`, `ocr`), geven `ValueError` en melden niets aan.
   Rood met elk van de twee mutaties die groen bleven. Sinds golf 4 is `ocr` het deel van X1
   (Z7 zette het in `ADMIN_PARTS`); de test weigert nu `Ocr` in plaats van `ocr`.
2. **Niemand meldt zich aan.** `test_niemand_meldt_een_schakelaar_of_een_deel_van_beheer_aan`
   (sinds de samenvoeging van golf 4 `test_geen_schakelaar_en_alleen_ocr_als_deel_van_beheer`,
   met de stand `0 ['ocr']`: X1 meldt bij het laden het deel `ocr` aan)
   (`tests/search/test_hotspots.py`), zoals die van `register_large`: de hele app geladen in een
   eigen proces, en dan geen schakelaar in `app.prefs` en geen deel in `search.hooks`. Daarop rust
   dat een oude client hetzelfde antwoord krijgt: zonder aanmelding weigert `check_switch` "aan",
   en geven `admin_switch` en `admin_rebuild` 501. Rood als `prefs` of `hooks` bij het laden iets
   aanmeldt.
3. **Wat `available` betekent.** De docstring van `SwitchState` zei "de container analyse is er,
   met het model". Keuze 59 weigert aanzetten alleen in de stand `off` (`faces_unavailable` is "de
   stand is off"; voor X2 ook zonder `PLATFORM_FACES_KEYS`); in `down` en `models_missing` wacht
   het werk in de rij. Nu zeggen de docstring en het commentaar bij
   `PhotoSettings.faces_available` dat. Het gedrag is gelijk: zonder aanmelding en zonder
   `PLATFORM_ANALYSE_ENABLED` is `available` False.
4. **De rij van V2** zegt nu dat de API-test ook op de oude code groen is, en dat de vitest de
   test van de reparatie is.
5. **Open 5 en 8.** `check_switch()` kent de huidige waarde niet (Open 5, voor Z6; **gedaan
   door Z6** in `6f77e8a`: PATCH geeft `check_switch()` alleen de velden die echt veranderen). Wat
   `available` betekent, en wie de test van "niemand meldt zich aan" aanpast (Open 8, voor X2 en
   X3).
6. **Open 11 had de verkeerde oorzaak.** `test_rondes_a_b_en_c` faalde in de eerste volle
   API-suite op `f74236d`, zonder andere last. Nagemeten komt de interrupt in stap 2 niet te laat
   maar te vroeg: vóór de vraag in de thread van aiosqlite begint, en dan negeert SQLite hem. Open
   11 zegt nu de oorzaak, de getallen en wat Z1 ermee moet in `deadline()`. De test staat
   ongewijzigd.

**Klaar als.** Elke regel noemt tests die bestaan (op naam nagezocht) en groen zijn in de runs
hieronder. De getallen kloppen: 14 routes van X2 en X3 in `ENDPOINTS` (12 en 2), zes regels voor
X2 en X3 in de tabel van `test_log_hiding.py`, twaalf vormen van X2 en zes nieuwe proeven in
`test_web_contracts.py`. Geen regel zegt meer dan de tests eisen. Sinds de samenvoeging is geen
regel uit een test weggehaald behalve waar de reparatie hem verving (de kopie van 0010 tegen
`TRIGGERS_0010`, nog steeds exact; de test van `faceCropUrl` werd die van `faceCrop`).

### Gedraaid na de nacontrole

Op `f74236d` (daarna veranderde alleen dit verslag), alles onder `taskset -c 0-2`, één zware run
tegelijk:

| Check | Uitkomst |
|---|---|
| `uv run ruff check .` en `uv run ruff format --check .` | groen (497 bestanden) |
| `uv run pytest -q` (de hele API-suite), eerste keer | 4345 groen, 1 rood, 2 overgeslagen, 193 niet gekozen (31 min). Rood was `tests/search/test_spike_deadline.py::test_rondes_a_b_en_c` in stap 2: "A" waar "B" moest komen (Open 11). `test_fuzz_10000_namen_met_vaste_seed` was groen. |
| `uv run pytest -q`, tweede keer | 4346 groen, 2 overgeslagen, 193 niet gekozen (32 min); `test_rondes_a_b_en_c` en `test_fuzz_10000_namen_met_vaste_seed` waren ook groen. |
| `uv run pytest -q -m media` | 178 groen (2 min) |
| `tests/search` als nobody (`/tmp/ci-nobody/run.sh`, zoals CI) | 395 groen, 9 niet gekozen |
| `npm run typecheck`, `npm test`, `npm run check:i18n -- --strict`, `npm run build`, `npm run budget` | groen: 999 tests in 94 bestanden, 0 meldingen van i18n, binnen het budget, gelijk aan na de review (eerste lading JS 97,4 van 100 kB, Bestanden 44,3 van 45 kB, `faces` 0,3 van 12 kB, `beeld` 0,0 van 15 kB, `likeRow` 0,2 van 3 kB) |
| `SLIM_CPUS=0-2 e2e/slim.sh`, alleen gedraaid | groen in 155 s: 395 tests op SQLite 3.46.1 in `python:3.12-slim`, 9 e2e met marker `zoek` |
| de e2e van CI (`-m "not perf and not media and not media_perf and not office and not office_real and not provider_real and not ocr"`) | 146 groen, 129 niet gekozen (13 min) |

De twee nieuwe tests van `f74236d` staan in de tellingen: de API-suite en `tests/search` hebben er
elk twee meer dan na de review (4346 en 395).

## Open

Voor de volgende pakketten:

1. **A0**: de migraties 0011 en 0012 (de tabellen van gezichten en beeld, de kolom `beeld` op
   `photo_pref`) en de fix van `node_bu_text` (Afwijking 1). De wachter van `own_live` kent de
   tabellen al (`face`, `face_person`, `face_scan`, `blob_embed`) en hun modellen onder een
   aangenomen naam (`Face`, `FacePerson`, `FaceScan`, `BlobEmbed`): A0 gebruikt die namen, of zet
   de lijst in `tests/search/test_own_live.py` gelijk. De rest van §3.11 (de container `analyse`,
   `compose.analyse.yaml`, de modellen en hun instellingen) is ook van A0. Na de review (F5): de
   triggers van 0011 en 0012 komen als `TRIGGERS_0011` en `TRIGGERS_0012` (en hun vorm zonder
   FTS5) naast `TRIGGERS_0010` in `app/search/schema.py`, elk gelijk aan de kopie in zijn
   migratie, en erbij in `TRIGGERS`; beide vergelijkingen in `test_schema_triggers.py` blijven
   exact. Met 0012 leest `photo_prefs()` de kolom `beeld` (nu geeft hij de standaard).
2. **X2**: zet de 422 van meer dan 200 gezichten om naar `faces_too_many`, of het ontwerp laat
   die code vallen. `FacesConfirm` is een union in de web en één model met een controle in de API;
   de contracttest vergelijkt alleen de velden, de vitest de twee bodies.
3. **A0, X2 en X3**: de vormen van Z0b zijn een voorstel tot ze vastliggen. Een vorm die verandert,
   verandert in de web en de API tegelijk; `test_web_contracts.py` houdt ze gelijk.
4. **De aanroepen van de web tegen de API** zijn bij deze samenvoeging één keer met de hand
   nagelopen (zie De samenvoeging), niet in een vaste test. De vaste test dekt alleen de soort fout
   die het vond (een vast getal in de query). Wie een route vult, draait zijn aanroepen uit de
   vitest het best ook een keer tegen de echte route.
5. ~~**Z6** (na de review, V3, F1, F3 en F4): GET `/me/photo-settings` met `prefs.photo_settings()`;
   PATCH met eerst `prefs.check_switch()`, dan schrijven, dan `prefs.switched()` in dezelfde
   transactie, met de test "PATCH `{faces: "aan"}` zonder X2 geeft 409 `faces_unavailable`".
   `check_switch()` kent de huidige waarde niet: `faces: "aan"` terwijl het al aan staat, geeft in
   de stand off ook 409. `PhotoSettingsPatch` is "alleen wat verandert"; Z6 stuurt dus alleen wat
   verandert, of geeft `check_switch()` alleen de velden die echt veranderen (nacontrole).~~
   **De API is gedaan door Z6** (`6f77e8a`): GET en PATCH `/me/photo-settings` in
   `routers/photo_prefs.py`; PATCH neemt alleen wat anders wordt, roept dan `check_switch()`
   aan, schrijft en roept `switched()` aan; een weigering schrijft niets
   (`tests/photos/test_photo_settings.py`, ook "al aan" in de stand off: geen 409). ~~De web
   (hieronder) is stap 2 van Z6.~~ **De web is ook gedaan door Z6** (`6297f02`): in het blok
   Foto's onder Account (`photos/people/PhotoSettingsBlock.tsx`) laadt hij
   `photos/faces/FacesSwitch.tsx` (gebied `photos_faces`) en `search/beeld/BeeldSwitch.tsx`
   (gebied `beeld`) lazy. Wie `PeoplePage` of `PersonPage`
   herschrijft, houdt de plek van X2 (`src/lazySlots.test.ts` eist hem in de bron).
6. ~~**Z7** (F4): GET `/admin/search` vult `faces` en `beeld` met `search.hooks.admin_block()`;
   PATCH stuurt `faces_allowed` en `beeld` naar `admin_switch()` (`ADMIN_SWITCHES`); rebuild met
   `what` `faces` of `beeld` gaat naar `admin_rebuild()`. Zonder aanmelding 501 `X2` of `X3`.~~
   Gedaan door Z7 (`d292ba6`), samengevoegd in golf 4, ook voor `ocr` van X1: X1 meldt
   `admin_block`, `admin_switch` en `admin_rebuild` aan met `register_admin("ocr", …)`. Bij de
   samenvoeging kwam er een commit na de haken bij (`e359bde`): de schakelaar en de herbouw
   werken in de transactie van de aanroeper. Zie [z7.md](z7.md) en [x1.md](x1.md).
7. ~~**Z4** (F3): in de stand beeld (`?mode=image`) laadt `/zoeken` `search/beeld/BeeldResults.tsx`
   lazy, met het gebied `beeld`, in plaats van de lijst.~~ Gedaan door Z4 (`f2590f7`,
   `search/SearchPage.tsx` met een `LazySlot`; `search/page.test.ts`, "de stand beeld op /zoeken").
   Zie [z4.md](z4.md).
8. **X2 en X3** (F3, F4, V4): melden zich aan met `prefs.register_switch()` en
   `search.hooks.register_admin()`, en vullen de bestanden van hun plekken. Een haak van
   `previews.register_large()` zet alleen werk in de rij; na 5 s stopt hij. `SwitchState.available`
   is False in de stand off (Keuze 59) en voor X2 zonder `PLATFORM_FACES_KEYS`, niet in `down` of
   `models_missing`: dan wacht het werk in de rij (nacontrole). Wie zich aanmeldt, past
   ~~`test_niemand_meldt_een_schakelaar_of_een_deel_van_beheer_aan` aan (die eist nu 0 en 0)~~
   `test_geen_schakelaar_en_alleen_ocr_als_deel_van_beheer` aan (die eist sinds golf 4
   `0 ['ocr']`: X1 deed dat met `register_admin`).

Bekend en niet van Z0b:

9. `tests/test_media_names.py::test_fuzz_10000_namen_met_vaste_seed` meet tijd en faalt alleen op
   deze VM (Open 14 van [z0.md](z0.md)). Hij staat ongewijzigd.
10. `tests/test_media_worker_guard.py::test_verbinding_dicht_stopt_ffmpeg_en_kleinkinderen_binnen_3_s`
    faalde één keer in een volle run van Z0b-api: de nep-ffmpeg doet `sleep 60 &`, en de test leest
    de naam van het kind voordat de kopie van sh `exec sleep` gedaan heeft (Open 6 van
    [z0b-api.md](z0b-api.md)). Van media, niet van Z0b; hij staat ongewijzigd. In de volle run
    van deze samenvoeging was hij groen. Dit is I1 (S3): de test wacht tot het kind `sleep` heet.
11. ~~`tests/search/test_spike_deadline.py::test_rondes_a_b_en_c` (S1, van Z0-spike) faalde één keer
    in stap 1 van `e2e/slim.sh`, die naast de volle API-suite op dezelfde cores 0–2 liep, en in de
    nacontrole één keer in de volle API-suite zonder andere last: in stap 2 van de test gaf ronde A
    voor `"de"` een antwoord ("A") waar "B" moest komen. De grens is daar een derde van één meting
    van ronde A: A duurt ±4 ms, dus de grens is meestal 1 ms. Nagemeten met precies die stap van de
    test (dezelfde helpers en fixtures, in `python:3.12-slim`, 12 × 30 keer): naast de volle suite
    2 van de 360 keer "A" (A duurde toen 6 en 8 ms), zonder die suite 0 van de 360.~~
    Opgelost door Z1 (`f0bbb64`, zie hieronder).

    **De oorzaak (nacontrole).** De interrupt komt dan niet te laat, maar te vroeg. SQLite negeert
    `interrupt()` als er op dat moment geen statement loopt, ook voor het statement dat daarna
    begint (nagegaan op 3.53.1: een vraag van 300 ms na zo'n interrupt liep helemaal). Stap 2 is
    1.802 keer herhaald, na dezelfde bestanden van `tests/search` als in de volle suite, met de
    tijd van de interrupt en van het begin van de vraag in de thread van aiosqlite (de
    trace-callback van `sqlite3`; alleen in een scratch-worktree): 12 keer "A", 11 van de 1.202
    met de hele suite verzameld en 1 van de 600 met alleen die bestanden. De vraag begint meestal
    0,4 ms na het zetten van de timer (met de hele suite verzameld p99 1,0 ms, max 1,7 ms), dus
    soms na de grens van 1 ms. In de 5 gevallen waar het begin gemeten is, ging de
    interrupt in 4 af 0,26–0,43 ms vóór de vraag begon, en liep A helemaal (8–17 ms). In de 5e was
    de meting van A zelf te hoog (30,6 ms, dus een grens van 10 ms) en was A al in 6,8 ms klaar.
    In de 7 zonder het begin ging de interrupt af na 1,1–4,1 ms, terwijl A 6–14 ms duurde en niet
    stopte: hetzelfde beeld.

    De test, `spike.py` en de code van zoeken zijn door Z0b niet veranderd; de test staat
    ongewijzigd. Voor Z1, die `deadline()` bouwt en deze tests houdt: hetzelfde gat zit in de vorm
    van `deadline()` (één `interrupt()` uit `call_later`). Begint de vraag in de thread van
    aiosqlite pas na de grens, dan stopt hij niet te laat maar helemaal niet. `deadline()` moet dat
    opvangen, bijvoorbeeld door de interrupt te herhalen tot de vraag klaar is, of met een progress
    handler die de tijd bijhoudt. Stap 2 van de test heeft ±1 ms marge; draai slim.sh niet naast
    een andere zware run (en een CI-runner met buren kan hetzelfde zien).

    **Opgelost door Z1** (`f0bbb64`): `deadline()` in `api/app/search/deadline.py` herhaalt de
    interrupt elke `HERHAAL_MS` tot het blok klaar is, en `test_spike_deadline.py` gebruikt nu
    die `deadline()` in plaats van de eigen grens van de spike.
12. **De module-cache van vitest** (`node_modules/.vitest-cache`) kan bij een ander bestand met een
    glob nog steeds oude lijsten bewaren als iemand er een typeargument in zet;
    `src/globCache.test.ts` vangt dat in de bron. Een checkout die al een oude kopie in de cache
    heeft, raakt die kwijt zodra de bron van het bestand verandert, of met `npx vitest --clearCache`.
