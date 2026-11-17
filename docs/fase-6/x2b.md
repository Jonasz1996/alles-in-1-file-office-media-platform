# X2b: gezichten in de app (golf 6)

X2b is het tweede deel van X2, "Gezichten automatisch" ([bouwontwerp.md](bouwontwerp.md), X2;
Keuze 60–69; de rij X2b in §2.0). Op de container van X2a ([x2a.md](x2a.md)) bouwt het de keten
in de app (plannen, zoeken, versleuteld opslaan, groeperen, de keuzes, wissen), de opdracht
`group` in de worker, de API, de web, het blok op `/beheer`, `app.cli analyse-bench` en
`analyse-kwaliteit`, de tests van laag 1 en 2 en de e2e, en de documenten. Hoe je het met alleen
het toetsenbord gebruikt: [toetsenbord-x2.md](toetsenbord-x2.md); wat je op de server doet: het
runbook [gezichten.md](../runbooks/gezichten.md); wat het voor Jonas betekent: de
[README](README.md#gezichten-in-detail-vanaf-081).

**Onzichtbaar tot 0.8.1.** De versie blijft 0.8.0, er is geen nieuwe migratie (0001–0012 en
`TRIGGERS_0010/0011/0012` staan vast), en geen pakket erbij (`uv.lock` en
`web/package-lock.json` zijn niet veranderd). Zonder de analyse (`ANALYSE` niet op `aan`, dus
`PLATFORM_ANALYSE_ENABLED` uit, zoals 0.8.0) ziet een gebruiker of beheerder niets nieuws: geen
schakelaar, geen regel, geen raster, geen rij in de viewer, op `/beheer` het blok van 0.8.0;
geen job in de rij `analyse`, geen bestand onder `DATA_ROOT`, geen regel in `.env`, en elke
route van 0.8.0 antwoordt zoals in 0.8.0. De routes van gezichten geven dan na de inlog `501
not_built("X2")`, zoals de stubs van 0.8.0 (sinds de samenvoeging, `dcd6bc1`; `409
faces_unavailable` alleen met de analyse aan zonder de map met sleutels). Bewezen met `test_faces_off.py` (6), `faces.test.ts` ("uit is onzichtbaar") en
`e2e/test_zoek_gezichten.py::test_zonder_analyse_ziet_niemand_iets_nieuws`. Tests en CI zetten
het aan met `ANALYSE=aan` (of `PLATFORM_ANALYSE_ENABLED` en een map met sleutels). [I2: in 0.8.1
staat de analyse standaard aan (P1), en elke gebruiker zet gezichten zelf aan. Met de analyse uit
toont 0.8.1 bewust iets meer dan 0.8.0: de zinnen dat gezichten en zoeken op beeld op deze server
uitstaan (ook in het blok op `/beheer`), en de link naar `/over` (P2); `test_uit080.py` legt dat
precies vast.]

Gebouwd op de tak `wip/golf6-x2b`, vanaf `625e946` (golf 5 na de nacontrole), in de worktree
`/home/user/wt-g6-x2b`, naast X3b (`/home/user/wt-g6-x3b`). Niet gepusht.

## Gebouwd

### Stap 1: de keten, de worker, de API en `/beheer` (`f8f6ddd` … `1cbda87`)

- **De opdracht `group`** (`api/app/analyse/worker/ops/group.py`, in het kind `werk`): per
  eigenaar de bevestigde en de open gezichten uit een verzegelde memfd (`wire.GROUP_HEAD`), K = 10
  wederzijdse buren, randen boven `T_LINK` (0,50), union-find met de sterkste rand eerst, groepen
  vanaf `MIN_GROUP` (3); voorstellen naar de beste persoon boven `T_SUGGEST` (0,45) met
  `MARGIN` (0,05) boven de tweede; afwijzingen tellen niet mee. De uitkomst is n ×
  `wire.GROUP_OUT` in de memfd `uit`; een invoer die liegt is `bad_request`. Een regel in
  `server.BUILT` en `child.handlers()` (de hotspots van G5-C4). De referentie in puur Python:
  `api/tests/analyse/groep_ref.py`; de nep-worker kan `group` ook (`faces_nep.py`).
- **De app** (`api/app/people/faces/`):
  - `state.py`: draait gezichten (`PLATFORM_ANALYSE_ENABLED` op SQLite én een map met sleutels),
    het verbod van de beheerder (Setting `search`, `faces_allowed`), de keuze van de eigenaar
    (`photo_pref.faces`), en `people.changed` hooguit één keer per 10 s per eigenaar.
  - `crypto.py`: AES-256-GCM per vector (540 B: nonce, 512 B, tag), de id van het gezicht als
    AAD, een sleutel per eigenaar (`<uuid hex>.key`, 0600) in `PLATFORM_FACES_KEYS`; "uit" haalt
    eerst de sleutel weg (unlink en fsync van de map).
  - `scan.py`: de jobs `faces.plan` (rij `default`, in stukken van 500, scopes owner, sha, sweep,
    version; Keuze 69 met `not_own_upload`; pas na de backfill van de index) en `faces.scan` (rij
    `analyse`, batches van 16, om de beurt per eigenaar, hooguit 4 per eigenaar, nieuwste eerst,
    de viewer vooraan; `no_preview` wacht, `unreadable`/`image_bad`/`too_big`/`bad_output` zonder
    nieuwe poging, `timeout` drie keer, `Defer` zonder poging).
  - `group.py`: lezen, ontsleutelen, de memfd schrijven en de uitkomst terugschrijven in stukken
    van 2.000 (`face_rows`); `maybe_full` (hooguit één keer per uur, als de rij van de eigenaar
    leeg is), `suggest` na `VERDICT_WAIT` (30 s) na de laatste keuze, en sinds `7050cb6`
    `full_due` in de ronde van elk uur.
  - `verdicts.py` en `links.py`: ja (ook met een nieuwe naam via Z6), nee, niet tonen, ongedaan
    maken (≤ 10 minuten, ≤ 200 gezichten), "nee, dit is Bram" in één stap, `sync_links`
    (`photo_person` met `source = 'auto'`), een kader met de hand (IoU ≥ 0,5) maakt een gezicht ja,
    een persoon zonder kader met één gezicht is een voorstel met `suggest_why = 'tag'`, en de
    drie haken van Z6 (`person_removed`, `persons_merged`, `person_deleted`).
  - `read.py`: de stand, de voorstellen, de groepen en de gezichten van een foto, uit de indexen
    van 0011, met `own_live`; nooit een vector of score (`sure` zegt alleen of het voorstel ruim
    boven de drempel zit).
  - `wipe.py`: de job `faces.wipe` (scopes `all`, `unnamed`, `checkpoint`) met een
    `wal_checkpoint(TRUNCATE)` op een eigen verbinding, opnieuw zolang een lezer het tegenhoudt.
  - `version.py` (`FACES_VERSION` 1, `LOCK_SHA` uit `models.lock`), `drempels.py` (voorlopig),
    `meten.py` (`analyse-bench` en `analyse-kwaliteit`), `register.py` (de jobs, de schakelaar,
    `/beheer`, de haken van previews, kopie, start en Z6, en de ronde van elk uur).
  - `api/app/face_rows.py`: de schrijvers per eigenaar die ook rijen in de prullenbak raken, en
    de tellingen voor `/beheer` (zoals `photo_rows.py`, buiten de wachter van `test_own_live.py`).
- **De API** (`api/app/routers/people_faces.py`, vóór `routers/people.py`): `GET
  /people/faces/status`, `suggestions`, `groups`, `groups/{key}`, `POST confirm`, `reject`,
  `hide`, `reset`, `wipe-unnamed`, `GET /people/faces/{id}/crop` (WebP, `no-store`, hooguit 20
  per seconde per gebruiker), `GET /files/{id}/faces` en `POST /files/{id}/faces/rescan` (20 per
  uur). Aan en uit via `PATCH /me/photo-settings` (de schakelaar van Z6, `prefs.register_switch`).
  De vormen zijn die van Z0b (`app/people/faces_types.py`, niet veranderd).
- **`/beheer`**: het deel `faces` van `GET /admin/search` (de stand van de monitor met modellen,
  versies en grenzen uit `app.analyse.state.view`, gebruikers met gezichten aan, de rij,
  mislukt per code, totaal, snelheid, wissen, kwaliteit) en `faces_allowed` in `PATCH`;
  "Mislukte opnieuw" via `POST /admin/search/rebuild {what: faces}`.
- **`app.cli`**: `analyse-bench` en `analyse-kwaliteit` (niet meer in `ANALYSE_STUBS`);
  `analyse-status` noemt `group`.

### Stap 2: de web, de e2e, CI en de documenten (`0b21ce7` … deze documentatie)

- **De web** (`web/src/photos/faces/`, lazy, in de plekken die Z0b neerzette):
  - `logic.ts`: wat los van React getoetst wordt: of gezichten aan staan, de regel met de stand,
    wanneer die opnieuw vraagt, welke toets wat doet in het raster, welke gezichten naar `confirm`
    gaan, de stukken van 200, het volgende gezicht zonder keuze, en ongedaan maken (8 s).
  - `FacesSwitch.tsx` (Account → Foto's): de schakelaar met de dialogen van Keuze 66.
  - `PeopleFaces.tsx` (`/fotos/personen`): de regel met de stand (`role="status"`, elke 10 s
    zolang er werk is), "Is dit …?", "Wie is dit?", "Niet benoemde gezichten wissen".
  - `FaceGrid.tsx` en `Crop.tsx`: het raster (`role="grid"`, roving tabindex, J/N/V of Y/N/H,
    Spatie, Enter naar de viewer, Ctrl+Z, Esc), met het formulier "Wie is dit?" (het veld Naam van
    Z6) en "Meer laden".
  - `PersonFaces.tsx` (de pagina van een persoon): "Misschien ook Anna".
  - `FacesRow.tsx` (het infopaneel van de viewer): de rij Gezichten met de kaders, de namen, "?",
    het formulier met Ja, Nee en Niet tonen, en "Opnieuw zoeken".
  - `faces.css`, het i18n-gebied `photos_faces` (nl en en), en `faces.test.ts` (25 tests).
- **`/beheer`**: `web/src/admin/FacesBlock.tsx` (Keuze 67; zonder stand het blok van 0.8.0) met
  `facesBlock.test.ts` (7 tests).
- **De e2e**:
  - `e2e/test_zoek_gezichten.py` (marker `zoek`): `test_gezichten_met_alleen_het_toetsenbord`
    (ook `analyse`; een eigen app met de nep-worker van `api/tests/people/faces/schijven.py` in een
    draad) en `test_zonder_analyse_ziet_niemand_iets_nieuws` (de server van de sessie, zoals 0.8.0).
  - `e2e/test_zoek_gezichten_echt.py` (marker `analyse_echt`): de echte compose met het echte
    image en de echte modellen, zonder browser; draait in de job `analyse-image`.
- **CI** (`.github/workflows/ci.yml`): `ANALYSE_TESTS` 229; in `zoek-perf` de stap "§4.4
  gezichten bij 60.000 gezichten"; in `e2e` sluit `-m` ook `analyse_echt` uit; in
  `analyse-image` de stap "De echte scan in de e2e" (precies `1 passed`).
- **De documenten**: dit bestand, [toetsenbord-x2.md](toetsenbord-x2.md), het runbook
  [gezichten.md](../runbooks/gezichten.md), het deel "Gezichten in detail (vanaf 0.8.1)" in de
  [README](README.md), de verwijzingen in [analyse.md](../runbooks/analyse.md) en
  `infra/single/README.md`, [e2e.md](e2e.md), en notities bij B57, B61 en B64 in
  [beslissingen.md](../fase-0/beslissingen.md).

### Wat stap 2 in de keten vond en herstelde

Elk met een test die zonder de reparatie rood is (zo nagegaan: de reparatie terugdraaien, de
test draaien, terugzetten).

- **`0b21ce7`**: `/beheer` liet de modellen en grenzen weg die de monitor wel kende; nu komen ze
  uit `app.analyse.state.view`, zoals bij X2a (`test_faces_meten.py::test_beheer_toont_de_modellen_en_grenzen_van_de_container`).
- **`23a1812`**: een kader met de hand dat een gezicht ja maakte, zette `verdict_at` maar geen
  `suggest`: "Is dit Anna?" kwam pas bij de volgende keuze. Nu zoals elke keuze
  (`test_faces_verdicts.py::test_een_kader_met_de_hand_maakt_het_gezicht_ja`; de e2e wacht erop).
- **`e8f4a79`**: bij het openen van het raster stond de focus nog op de knop van de kaart, die
  dan verdwijnt; nu op het eerste gezicht (de e2e).
- **`03655e1`**: na het laatste voorstel viel de focus op de body; nu op de eerste kaart die er
  nog is, anders "Niet benoemde gezichten wissen", anders de regel (de e2e:
  `.people-faces :focus`).
- **`b978b08`**: de pagina laadt na elke keuze opnieuw; na de laatste keuze haalde die lading het
  open raster weg, met Ctrl+Z. Nu blijft het staan tot Esc (de e2e, `15e39fd`).
- **`7050cb6`**: een `full` liep alleen als de rij van de eigenaar leeg was, hooguit één keer per
  uur. Wat binnen dat uur na een `full` binnenkwam, wachtte op een volgende upload of keuze, ook
  als die nooit kwam. Nu groepeert de ronde van elk uur (`register.hourly`) iedere eigenaar met
  gezichten van na zijn laatste `full` (`face_rows.new_since_full`, `group.full_due`)
  (`test_faces_group_app.py::test_de_ronde_van_elk_uur_groepeert_wat_binnen_het_uur_kwam`).

Alleen een test erbij, zonder reparatie: `10c1c7d` (`faces.plan` wacht op de backfill van de
index, Keuze 65; zonder de wachter rood) en `26c17ce` (de e2e doet ook het vinkje op `/beheer`).

## Vormen

Wat het ontwerp open liet, en hoe het nu is.

1. **Uit is `409 faces_unavailable`.** Draaien gezichten niet (`state.running`: de analyse uit,
   of geen `PLATFORM_FACES_KEYS`), dan geeft elke nieuwe route na de inlog `409
   faces_unavailable` (eerst `401` zonder inlog, `403 guest` voor een gast). In 0.8.0 waren het
   stubs met `501`; de web van 0.8.0 vraagt ze niet. Met gezichten uit voor de eigenaar: `409
   faces_off` (behalve de stand en `wipe-unnamed`). Sinds de samenvoeging (`dcd6bc1`) is het met
   de analyse uit weer `501 not_built("X2")`, zoals 0.8.0; `409 faces_unavailable` blijft voor
   de analyse aan zonder `PLATFORM_FACES_KEYS` (De samenvoeging).
2. **De schakelaar bestaat alleen als gezichten draaien.** `GET /me/photo-settings` geeft
   `faces_available` (van Z0b); zonder de analyse staat onder Account het vinkje van Z6 met "Nog
   niet beschikbaar op deze server." (0.8.0), met de analyse de schakelaar van X2 met zijn
   dialoog. `PATCH faces=aan` zonder de analyse geeft wat 0.8.0 gaf.
3. **Het blok op `/beheer` meldt zich pas bij de start aan, en alleen als gezichten draaien.**
   Anders is `faces` in `GET /admin/search` `null` en toont de web het blok van 0.8.0 ("Gezichten
   herkennen komt later in fase 6.", zonder knop of schakelaar) [I2: sinds 0.8.1 met de zin dat
   gezichten niet draaien op deze server, en waar de reden staat; `bea29ca`, `d538cbb`]. De regel "Analyse: off" uit het
   ontwerp verschijnt daardoor nooit: met de analyse uit is er geen nieuw blok.
4. **De sleutel van een groep** is `"<run>:<cluster>"`: `face_owner.run` gaat bij elke `full` één
   omhoog. Een oude sleutel geeft `409 faces_gone`.
5. **Schrijven per eigenaar staat in `app/face_rows.py`**, zoals `photo_rows.py`: "uit", "niet
   benoemde wissen", een nieuwe versie en samenvoegen raken ook rijen van foto's in de
   prullenbak (een vector in de prullenbak is ook een vector). Wat leest om te tonen of te
   groeperen, staat onder `app/people/` en gebruikt `own_live`.
6. **De plannen zonder `sqlite_stat1`**: de tellingen gaan langs `ix_face_new`,
   `ix_face_open`, `ix_face_cluster`, `ix_fp_person` en `ix_face_scan_todo`; waar SQLite anders
   een slechtere index koos, staat een unaire `+` voor de kolom (`test_faces_plans.py`).
7. **De uitsnede maakt de app zelf** (Pillow in een draad, uit de preview van 1024), niet de
   worker; er komt niets op schijf. De grenzen (20 uitsneden per seconde, 20 keer "opnieuw
   zoeken" per uur, per gebruiker) staan in het geheugen van het proces.
8. **`analyse-bench` en `analyse-kwaliteit` bewaren hun uitkomst in de Setting** (`faces.bench`,
   `faces.kwaliteit`), niet in een bestand; `/beheer` leest ze daar. De bench meet de tijd per
   foto op gemaakte foto's (YuNet vindt er geen gezicht); de tijd per extra gezicht meet hij niet
   (Afwijkingen 5). `analyse-kwaliteit`: exit 3 als er gemeten is maar een poort niet gehaald,
   exit 1 als er niets te meten was.
9. **De web vraagt `GET /me/photo-settings` hooguit één keer per minuut per gebruiker**, gedeeld
   door alle plekken (`facesSettings`); na een `PATCH` geldt de nieuwe stand meteen. Zonder de
   analyse of met gezichten uit vraagt geen plek een route van gezichten.
10. **De regel op `/fotos/personen` vraagt elke 10 s opnieuw** zolang er werk is (wachtend of
    wissen) en de pagina te zien is, en verder bij `people.changed`.
11. **Lazy tot het nodig is**: de viewer (vanuit het raster), het veld Naam van Z6
    (`PersonCombo`) en de aanroepen van `../people/api` laadt het chunk van gezichten pas als ze
    nodig zijn; zo blijft het chunk onder 12 kB.
12. **De focus**: bij het openen van het raster op het eerste gezicht (zijn er geen, op Sluiten);
    na J, N of V naar het volgende gezicht zonder keuze, anders het vorige; na Esc op de knop van
    de kaart, of (is die weg) de eerste kaart, "Niet benoemde gezichten wissen", de regel. Het
    raster blijft open na de laatste keuze, ook als de pagina opnieuw laadt.
13. **De ronde van elk uur** doet `faces.plan` met scope `sweep` en `group.full_due` (`7050cb6`):
    een `full` voor wie gezichten heeft van na zijn laatste `full`, nog steeds hooguit één keer per
    uur en alleen met een lege rij.
14. **Ongedaan maken in de web** kan 8 s (zo lang staat de melding); de API staat het 10
    minuten toe.

## Metingen

Alle lokaal, in de sandbox (4 cpu's; zware runs op 3 cores met `taskset -c 0-2`, één tegelijk
onder het slot).

| Wat | Hoe | Uitkomst |
|---|---|---|
| groeperen, 60.000 synthetische gezichten (poort ≤ 60 s, ≤ 400 MB) | `tests_analyse/test_group_perf.py`, één core, één draad, nieuw proces | 23,5 s, maxrss 254 MB, recall 0,986 (zuiverheid ≥ 0,99 gehaald) |
| `group` door de echte worker (pid 1 en het kind `werk`) | `EchteDriver.lang` in `tests_analyse/echt.py` | 20.000 gezichten 1,54 s; 30.000 3,51 s (maxrss 167 MB); 40.000 6,10 s (206 MB) |
| routes bij 60.000 gezichten (poort p95 ≤ 100 ms, de stand ≤ 50 ms) | `tests/people/faces/test_faces_perf.py` (marker `zoek_perf`), N = 100.000, SQLite 3.53.1 | voorstellen 46,5 ms, groepen 35,7 ms, een foto 10,0 ms, de stand 31,7 ms (vóór `d0950f1` was de stand 50,5 ms: rood) |
| het chunk van gezichten (begroting 12.000 B gzip) | `npm run budget`, rij "faces (X2)" | 11.831 B (de eerste versie was 15,3 kB; zie Afwijkingen 9) |
| de initiële JS (0 B erbij) | `npm run budget`, rij "eerste lading JS", tegen een build van `625e946` in een eigen worktree | 98.039 → 98.050 B gzip; de ruwe bytes van de shell gelijk (90.787 B), en na het gelijk maken van de hashes in de namen ook de inhoud; de 11 B zijn gzip van andere hashnamen |
| `e2e/test_zoek_gezichten.py` | onder het slot, `taskset -c 0-2`, met `-m ""` | 2 passed in 63,7 s (de lange test 49,2 s, die zonder de analyse 7,7 s) |
| de echte scan (`test_zoek_gezichten_echt.py`) | tegen een stand-in van het image (de Dockerfile zonder de web-stap en apt) met de echte modellen | 1 passed in 7,94 s (de eerste run faalde op de namen van de modellen in de test; zie Gedraaid) |
| de tijd per foto met de echte modellen | `test_models_real.py` van X2a (laag 3) en `analyse-bench` | ongewijzigd t.o.v. X2a; `analyse-bench` op de LXC: het runbook zegt hoe. [Nacontrole golf 6: "ongewijzigd" was geen meting. Op `9228eb6` gemeten: 28,1 ms per foto zonder gezicht, 29,4 ms per gezicht, 1.043 foto's per minuut per core met één gezicht (poort ≥ 250), maxrss van `werk` 224 MB; X2a had 1.070 als root op `e609341` (De nacontrole, Gedraaid)] |

## Afwijkingen

1. **Het document heet `x2b.md`**, niet `docs/fase-6/x2.md` (§2.0 en "Klaar als"): X2 is in twee
   delen gebouwd, zoals [x2a.md](x2a.md).
2. **De markers van de e2e.** Het ontwerp zegt `zoek` voor `test_zoek_gezichten.py` en `analyse`
   voor `test_zoek_gezichten_echt.py`. Gebouwd: het bestand `zoek`, de test met de nep-worker
   ook `analyse` (die draait in de job `e2e`), en de echte test `analyse_echt` (de marker die A0
   daarvoor neerzette; de job `e2e` sluit hem uit, `analyse-image` draait hem en eist `1
   passed`). Met `analyse` zou de echte test in de job `e2e` vallen, zonder image.
3. **Laag 4 is niet gebouwd** (`test_faces_quality.py`, `fotos.lock`, `maak_set.py`): Vraag 8
   staat op nee. De kwaliteit meet `app.cli analyse-kwaliteit` op de LXC, tegen de eigen ja en nee;
   tot dan staat in de README en op `/beheer` "niet gemeten", en zijn 0,50 en 0,45 voorlopig.
4. **"Wat schuift" 3 en 4 zijn niet gebouwd**: voorstellen om personen samen te voegen, en een
   uitsnede van een gezicht als omslag van een persoon. 1 (de groepen "Wie is dit?") en 2 (de knop
   "Niet benoemde gezichten wissen") zijn er wel.
5. **De tijd per extra gezicht** meet `analyse-bench` niet: de opdrachten van A0 hebben geen
   SFace los, en op gemaakte foto's vindt YuNet niets (Vormen 8).
6. **"Gezichtsherkenning is op deze server niet geïnstalleerd."** staat nergens: zonder de
   analyse moet de web gelijk zijn aan 0.8.0, dus is er geen plek die het zegt (Vormen 2 en 3).
   De tekst staat wel in de foutcatalogus (`faces_unavailable`) voor wie de route toch vraagt.
7. **De zin bij persoon wissen in het dialoog van Z6** ("De gezichten die je als Anna bevestigde,
   worden gewist …") is niet gebouwd: dat dialoog staat in `PersonPage` van Z6, buiten de plekken
   die Z0b neerzette (Open 2). Wat de zin zegt, gebeurt wel (`person_bd_face`,
   `test_faces_verdicts.py::test_de_haken_van_z6`).
8. **De rij "Analyse" bij `off`** verschijnt niet (Vormen 3); `facesBlock.test.ts` toetst de
   tekst wel, voor de stand die de monitor zou geven.
9. **Het chunk van gezichten was eerst 15,3 kB** (de viewer, `sharing.ts`, `PersonCombo` en de
   API van personen gingen statisch mee). Niet de begroting opgerekt, maar herschreven: de
   rechten van een foto zonder `sharing.ts`, de viewer via `lazyWithAreas`, `PersonCombo` en
   `../people/api` dynamisch (`logic.ts`). 11.831 B.
10. **Aanrakingen van bestanden van anderen** (§2.0 en §3.5):
    - stap 1: `api/app/cli.py` (de stubs eruit, de twee commando's) en `tests/test_cli_db.py` (de
      nieuwe exacte lijst); `tests/search/test_hotspots.py` (de rij van analyse telt nu 1 soort);
      `tests/analyse/test_analyse_monitor.py` (de opdrachten: `cancel, caps, faces, group`);
      `tests_analyse/echt.py`, `test_analyse_contract_real.py`, `test_analyse_kinderen.py` en
      `test_worker_sandbox.py` (`group` is gebouwd); `tests/test_jobs_lanes.py` (de exacte rij en
      grens van `faces.plan`, `faces.scan`, `faces.group` en `faces.wipe`, zoals X1 voor `ocr`);
      `tests/search/test_api_stubs.py` (de routes van gezichten zijn gebouwd);
      `app/analyse/worker/server.py` en `child.py` (één regel `group` elk, G5-C4);
    - stap 2: `web/src/admin/searchTab.test.ts` (het blok met een stand),
      `.github/workflows/ci.yml` (zie Gebouwd), `docs/fase-6/e2e.md`, `docs/fase-6/README.md`,
      `docs/runbooks/analyse.md`, `infra/single/README.md` en `docs/fase-0/beslissingen.md`
      (notities, niets besloten).
    - De contracten van A0 en Z0b (`wire.py`, `Op`, `wire.CHILD`, `faces_types.py`,
      `search/types.py`, de typen en `api.ts` van de web) zijn niet veranderd.

## Open

1. ~~**CI na de push**: `api` (de volle suite), `api-analyse` (229), `zoek-perf` (de stap
   gezichten), `e2e` (met `analyse`, zonder `analyse_echt`) en `analyse-image` (de echte scan)
   hebben nog niet gedraaid. De echte test draaide alleen tegen een stand-in.~~ [I2: alle 19 jobs
   groen op `e535332` (run 43892193604), met `api-analyse` 330 en 16 en de echte scan in
   `analyse-image`.]
2. ~~**De zin in het dialoog van Z6 bij persoon wissen** (Afwijkingen 7): een aanraking van
   `PersonPage` buiten de plekken, voor wie Z6 bijhoudt of voor I2.~~ [I2: gedaan in P2 (`1a00a0f`
   en `93c303c`): de zin over de bevestigde gezichten staat er als gezichten aanstaan, en het
   dialoog vraagt één ding tegelijk.]
3. ~~**`/over`**: de naamsvermelding van YuNet en SFace (MIT, Apache-2.0) staat in de README en naast
   de modellen, nog niet op `/over`; gepland voor 0.8.1 (I2).~~ [I2: gedaan in P2 (`6890c2f`):
   `/over` noemt YuNet, SFace en SigLIP 2 met hun licentie, voor iedereen.]
4. **De kwaliteit is niet gemeten** (Afwijkingen 3): `analyse-kwaliteit` op de LXC, tegen de
   eigen ja en nee van Jonas. Verandert een drempel, dan gaat `FACES_VERSION` omhoog.
5. **§4.5 met X2 aan** (de belastingtest met `analyse` vol): van de integratie. [I2: gemeten als
   M13 ([metingen.md](metingen.md#gezichten-en-beeld-081)); gehaald op `e41b724`, alle 13 poorten:
   de container `analyse` zonder OOM-kill (de som van VmHWM 1,51 GB), zoeken op beeld p95 191,8 ms,
   de poorten van 0.8.0 alle 10. De gezichten zelf zijn daar niet geoefend: de testfoto's hebben
   geen gezichten, dus SFace en het groeperen draaiden niet onder belasting.]
6. **`analyse-bench` op de LXC**: niet gedraaid; het runbook zegt hoe (`gezichten.md`).
7. ~~**`df` op `DATA_ROOT` voor de vectoren** (K2-19; x2a.md, Open 3): `install.sh` kijkt er niet
   naar voor gezichten (±0,8 kB per gezicht; `/beheer` toont de ruimte). X3b zet die controle
   voor beeld neer; gezichten kunnen er bij I2 een regel bij krijgen.~~ [I2: gedaan in P1
   (`e0a271e`): `gezichten_ruimte` waarschuwt onder 105 MB vrij op `DATA_ROOT`, nooit fataal.]
8. **Niet in een e2e** (toetsenbord-x2.md): V, Spatie om te deselecteren, Home en End, "Meer
   laden", "Niet benoemde gezichten wissen", "Misschien ook Anna", "?" met Ja, Nee en Niet tonen
   in de viewer, "Opnieuw zoeken" en "Mislukte opnieuw". De toetsen toetst `faces.test.ts`, de API
   erachter `tests/people/faces/`.
9. **Samenvoegen met X3b**: allebei veranderen `ci.yml` (dezelfde regel `ANALYSE_TESTS` rond
   regel 128: de som van beide), `docs/runbooks/analyse.md` ("Wat er nog niet is": naast elkaar)
   en `infra/single/README.md`; en `server.BUILT`/`child.handlers()` (G5-C4: X3b eerst, dan X2b).
10. **`node_modules` is gedeeld** (een symlink naar de hoofdrepo, ook voor X3b): het budget leest
    `.tmp/platform-chunks.json` van de laatste build; draai `npm run budget` direct na de eigen
    build.

## Klaar als

De regels van X2 (en de tests per bestand) die bij X2b horen. Die van X2a staan in
[x2a.md](x2a.md), Klaar als.

| Regel uit het ontwerp | Bewijs | Uitkomst |
|---|---|---|
| Bouwt: `face`, `face_person`, `face_scan`, `face_owner` gevuld door de app | `test_faces_scan.py` (19), `test_faces_group_app.py` (8), `test_faces_verdicts.py` (12) | groen |
| Bouwt: groeperen per eigenaar in `werk`, met `GROUP_MAX` 60.000 | `tests_analyse/test_worker_group.py` (14), `test_group_perf.py` (2; sinds `c2be5fc` ook `test_de_meting_telt_de_piek_van_pytest_niet_mee`) | groen |
| Bouwt: bevestigen, afwijzen en samenvoegen met Z6 als bron | `test_faces_verdicts.py` (12, met `test_de_haken_van_z6`) | groen |
| Bouwt: de privacyregels, de versleuteling, Keuze 69 | zie Privacy hieronder | groen |
| Bouwt: de rij `analyse` pas na de backfill | `test_faces_scan.py::test_eerst_de_backfill_van_de_index` (`10c1c7d`; zonder de wachter in `plan_job` rood); `tests/test_jobs_lanes.py` (de rij van elke job) | groen |
| Bouwt: de API, de web en `/beheer` | `test_faces_api.py` (11), `faces.test.ts` (25), `facesBlock.test.ts` (7), de e2e | groen |
| Bouwt: `analyse-kwaliteit` en `analyse-bench` | `test_faces_meten.py` (4) | groen |
| Tests: `test_faces_crypto.py` | 5 | groen |
| Tests: `test_faces_wipe_latency.py` (≤ 100 ms extra) | 1 | groen |
| Tests: `test_faces_scan.py` | 19 (met de parameters; sinds `10c1c7d` ook de backfill) | groen |
| Tests: `test_faces_group_app.py` | 8 (sinds `7050cb6` ook de ronde van elk uur) | groen |
| Tests: `test_faces_verdicts.py` | 12 | groen |
| Tests: `test_faces_off.py` | 6 (ook: zonder de analyse elke route van gezichten `501 not_built("X2")` zoals 0.8.0 (`dcd6bc1`), met de analyse aan zonder sleutels `409 faces_unavailable`, `/me/photo-settings` en `/beheer` zoals 0.8.0, geen job of bestand) | groen |
| Tests: `test_faces_wipe_bytes.py` (0 merktekens) | 5 | groen |
| Tests: `test_faces_no_vector.py` | 1 (alle routes uit OpenAPI, caplog, gebeurtenissen) | groen; als nobody alleen met `-u SSL_CERT_FILE` (de variabele van de sandbox wijst naar een map van root; CI heeft hem niet) |
| Tests: `test_faces_privacy.py` (ook Keuze 69) | 5 | groen |
| Tests: `test_faces_copy.py` | 3 | groen |
| Tests: `test_faces_version.py` | 2 | groen |
| Tests: `test_faces_lifecycle.py` | 5 | groen |
| Tests: `test_faces_backup.py` | 3 | groen |
| Tests: `test_faces_api.py` | 11 | groen |
| Tests: `test_own_live.py`, `test_api_stubs.py` (uitgebreid) | de routes van gezichten gebouwd; `face_rows.py` buiten de wachter | groen |
| Tests: `test_worker_group.py` (laag 2) | 14 | groen |
| Tests: `test_group_perf.py` (poort) | 23,5 s, 254 MB, recall 0,986 | gehaald |
| Tests: `test_faces_quality.py` (laag 4) | niet gebouwd (Vraag 8 nee; Afwijkingen 3) | — |
| Tests: `web/src/photos/faces/faces.test.ts` | 25 (toetsen, alleen gezien en geselecteerd naar `confirm`, nl en en, de stand) | groen |
| Tests: `e2e/test_zoek_gezichten.py` | 2 tests | groen (los, in `slim.sh` en in de CI-set; zie Gedraaid) |
| Tests: `e2e/test_zoek_gezichten_echt.py` | 1 test | stand-in groen; `analyse-image` in CI na de push [I2: groen op `e535332`, run 43892193604] |
| Tests: `test_faces_perf.py` (`zoek-perf`) | p95 46,5 / 35,7 / 10,0 / 31,7 ms | gehaald lokaal; in CI na de push [I2: `zoek-perf` groen op `dc5dfe5` en `e535332`] |
| Klaar als: `/beheer` toont de juiste stand bij ok, down, models_missing en off | `facesBlock.test.ts::de regel Analyse` (ok, off, elke reden van down, models_missing met de namen); `test_faces_meten.py`; de e2e: "werkt" met de nep-worker, en zonder de analyse het blok van 0.8.0 (Vormen 3) | groen |
| Klaar als: de lagen 1 en 2 groen (scan, groeperen, keuzes, levensloop, kopieën, versie) | `tests/people/faces/` (96, en 1 perf; 94 bij X2b, sinds de samenvoeging ook `test_faces_naast_beeld.py` (2)), `test_worker_group.py` (14) | groen |
| Klaar als: privacy (`off`, `wipe_bytes`, `no_vector`, `privacy`, `crypto`, `backup`, `test_db_pragmas.py`) | in de volle suite, en `tests/people/faces` als nobody | groen (zie Gedraaid) |
| Klaar als: poort 60.000 gezichten ≤ 60 s en ≤ 400 MB | `test_group_perf.py` | 23,5 s, 254 MB |
| Klaar als: de kwaliteit op echte gezichten, de drempels in `drempels.py`; tot dan "niet gemeten" in de README en op `/beheer` | `drempels.py` (0,50, 0,45, voorlopig); README; `facesBlock.test.ts` en de e2e ("niet gemeten") | het deel tot de meting: groen; de meting zelf open (Open 4) |
| Klaar als: `e2e/test_zoek_gezichten.py` groen en `toetsenbord-x2.md` af | de e2e (zie Gedraaid); [toetsenbord-x2.md](toetsenbord-x2.md), met wat niet in een e2e staat | groen; af |
| Klaar als: `e2e/test_zoek_gezichten_echt.py` groen in `analyse-image` | de stap in `analyse-image` | in CI na de push [I2: groen op `e535332`, run 43892193604] |
| Klaar als: `test_faces_perf.py` p95 ≤ 100 ms in `zoek-perf` | de stap in `zoek-perf` | lokaal gehaald; in CI na de push [I2: groen op `dc5dfe5` en `e535332`] |
| Klaar als: de initiële JS niet gegroeid, het chunk binnen zijn begroting | `npm run budget` | 11.831 ≤ 12.000 B; de initiële JS gelijk in ruwe bytes |
| Klaar als: §4.5 met X2 aan | — | integratie (Open 5) [I2: M13; gehaald op `e41b724`, maar zonder gezichten op de testfoto's] |
| Klaar als: `analyse-bench` op de LXC, of het runbook zegt hoe | [gezichten.md](../runbooks/gezichten.md), "Hoe snel is het op deze CPU" | het runbook zegt hoe |
| Klaar als: de documenten (README, runbook, x2.md, B57 en B61 en verder) | README "Gezichten in detail", `gezichten.md` (de map met sleutels niet in snapshots), dit bestand, notities bij B57, B61 en B64 | er |
| Klaar als: `e2e/slim.sh` groen | `SLIM_CPUS=0-2 e2e/slim.sh` op `61b9d9b` | groen in 1106 s |
| Levensloop: upload door een ander of via een link | `test_faces_privacy.py::test_keuze_69_een_upload_van_een_ander` | groen |
| Levensloop: upload (gezichten aan), `wait` dan `done` | `test_faces_scan.py::test_zonder_1024_wacht_en_daarna_klaar` | groen |
| Levensloop: kopie door dezelfde eigenaar, door een ander | `test_faces_copy.py` (3) | groen |
| Levensloop: andere eigenaar, nieuwe inhoud, prullenbak en terug, definitief wissen | `test_faces_lifecycle.py` (5), `test_faces_wipe_bytes.py` | groen |
| Levensloop: uit of verbod | `test_faces_off.py`, `test_faces_privacy.py::test_een_verbod_wist_iedereen`, de e2e (verbieden op `/beheer`) | groen |
| Levensloop: niet tonen, ongedaan maken, persoon wissen | `test_faces_wipe_bytes.py`, `test_faces_verdicts.py` | groen |
| Levensloop: nieuwe versie | `test_faces_version.py::test_een_nieuwe_versie` | groen |
| Levensloop: reservekopie | `test_faces_backup.py` | groen |

## Gedraaid

Aan het eind van stap 2, op `61b9d9b` (de web op `b978b08`: daarna veranderde er niets onder
`web/`). De zware runs elk één keer, onder het slot en met `taskset -c 0-2`.

| Wat | Uitkomst |
|---|---|
| `uv run ruff check .` en `ruff format --check` | groen (758 bestanden) |
| `uv.lock`, `web/package-lock.json`, `pyproject.toml`, `web/package.json` | gelijk aan `625e946` |
| yamllint (relaxed) op `ci.yml` | alleen regels die te lang zijn, zoals ervoor |
| web: `typecheck`, `vitest`, `build`, `check:i18n --strict` | groen; 122 bestanden, 1297 tests; 0 meldingen |
| web: `npm run budget` (direct na de eigen build) | faces 11.831 / 12.000 B, eerste lading JS 98.050 / 100.000 B |
| `e2e/slim.sh` (python:3.12-slim, SQLite 3.46.1) | groen in 1106 s: zoeken en triggers 1307 passed (845 s), e2e marker `zoek` 103 passed (261 s) |
| de e2e-set van CI (`-m "not perf and … and not analyse_echt"`) | 240 passed, 136 deselected in 926 s |
| `e2e/test_zoek_gezichten.py` en `test_zoek_gezichten_echt.py` met `-m ""` | 2 passed, 1 skipped (de echte: geen `E2E_ANALYSE_IMAGE`; die draait in `analyse-image`) in 63,7 s |
| de volle api-suite (`-p no:randomly`) | 6561 passed, 2 skipped (zoals in stap 1), 241 deselected in 2707 s |
| `tests_analyse` (`.venv-analyse`) | 229 passed, 11 deselected in 73,8 s |
| als nobody (`/tmp/ci-nobody`, met `-u SSL_CERT_FILE`): `tests/people/faces`, `test_own_live.py`, `test_api_stubs.py`, `tests/analyse` | 449 passed, 1 deselected in 113 s |

De echte scan tegen de stand-in (bij `7e8f516`): de eerste run faalde in de test zelf, die de
korte namen van de modellen verwachtte (`sface`, `yunet`); `/admin/search` geeft de bestandsnamen
van `models.lock` (`face_detection_yunet_2026may.onnx` …). De test leest die namen nu uit
`models.lock` (`modellen_van_gezichten`); daarna 1 passed in 7,94 s. De stand-in is daarna
gewist; het image zelf bouwt alleen CI.

## De samenvoeging (golf 6)

Samengevoegd op 16 november 2026 op `claude/stappenplan-uitwerken-lu4uv3`: eerst X3b
(`aa21e37`), dan X2b (`dcafcea`), beide met `--no-ff`. De conflicten, wat de samenvoeging erbij
deed, BZ5 en de twee wandelingen met de analyse uit staan in "De samenvoeging (golf 6)" in
[x3b.md](x3b.md); hier wat X2b raakt. Niets gepusht; de versie blijft 0.8.0.

**Afwijking van Vormen 1: met de analyse uit `501`, niet `409`** (`dcd6bc1`). De FEITEN van golf
6 zeggen dat met de analyse uit elke route van 0.8.0 antwoordt zoals in 0.8.0. De routes van
gezichten bestonden in 0.8.0 als stubs van Z0b met `501 not_built("X2")`; X2b gaf er `409
faces_unavailable`. De wandeling langs elke route (`api/tests/uit080/test_wandeling.py`, als
niemand, Eva, de gast Gijs en de beheerder Jonas, tegen een opname op `3fca440`) vond precies die
twintig antwoorden anders. Nu, in `_running` van `routers/people_faces.py`:

- de analyse uit (`analyse_on` niet waar): `501 not_built("X2")`, na de inlog, ook voor een gast,
  zoals 0.8.0;
- de analyse aan maar geen map `PLATFORM_FACES_KEYS`: `409 faces_unavailable` (zoals Vormen 1);
- gezichten draaien: `403 guest` voor een gast, `409 faces_off` als de eigenaar ze uit heeft.

`test_faces_off.py`, `test_api_stubs.py` en `e2e/test_zoek_gezichten.py` kregen het nieuwe
exacte antwoord. `PATCH /me/photo-settings` met `faces: "aan"` geeft met de analyse uit nog
steeds `409 faces_unavailable`: dat was in 0.8.0 ook zo (Z0b, `check_switch`). Wat hierboven
"De nieuwe routes geven dan `409 faces_unavailable`" en de rij van `test_faces_off.py` in Klaar
als zeggen, geldt dus alleen met de analyse aan zonder sleutels.

**Gezichten naast beeld.** In de rij `analyse` en het kind `werk` wisselen gezichten en beeld
elkaar af, en met `werk` bezig wijken `faces.scan` en `faces.group` met `Defer(DEFER_BUSY)`
zonder poging of fout: `tests/people/faces/test_faces_naast_beeld.py` (`8cc9027`; x3b.md).
De groepsronde van 60.000 plus 6.000 in `werk` naast een volle cache en een herlaadactie in
`zoeken` (BZ5): `werk` 277 MB maxrss met de nepmodellen, met de modellen van gezichten en het
beeldmodel erbij (§3.11) 666 MB, onder de 1 GiB van `RLIMIT_DATA`
(`tests_analyse/test_geheugen_samen.py`; x3b.md).

**De poort van groeperen meet de piek van het kind** (`c2be5fc`). Op de samengevoegde kop was
`test_group_perf.py` rood: maxrss 419 MB, grens 400 MB. Het kind zelf had een piek van 255,7 MB
(`VmHWM`). De test las `ru_maxrss`, en Linux geeft bij fork en exec de piek van de ouder door:
die van pytest, dat op de samengevoegde kop eerst ook BZ5 en de andere nieuwe tests draait. Op
X2b alleen was de piek van pytest kleiner dan die van het kind, en zag de meting hierboven
(254 MB) dus wel het kind. Nu leest het kind `VmHWM`, zijn eigen piek sinds exec;
`test_de_meting_telt_de_piek_van_pytest_niet_mee` toetst dat met 256 MB in pytest. De grens
blijft 400 MB; op de kop 24,6 s en 255,7 MB.

**Open, per punt:**

- Open 9 (samenvoegen met X3b): gedaan. `ANALYSE_TESTS` is 263 en `ANALYSE_DESELECTED` 11
  (`df8d56b` en `c2be5fc`, nageteld met `--collect-only`); het runbook van de analyse heeft
  "Wat er nog niet is" van beide; `infra/single/README.md` noemt beide schakelaars;
  `server.BUILT` en `child.handlers()` hebben de regels van beide (`dcafcea`), met de nieuwe
  exacte lijsten.
- x2a.md Open 3 (wat X2b moest bouwen): gebouwd, op `df` na (Open 7 hieronder).
- Open 1 (CI), 2 (de zin bij persoon wissen, I2), 3 (`/over`, I2), 4 (de kwaliteit op de LXC),
  5 (§4.5 met X2 aan, I2), 6 (`analyse-bench` op de LXC), 7 (`df` voor de vectoren van
  gezichten, I2), 8 en 10 blijven staan.

### Open na de samenvoeging

- **Alleen in CI, na de push:** `api` (met `tests/uit080` en `test_faces_naast_beeld.py`),
  `api-analyse` (263 en 11), `zoek-perf`, `e2e` (met `test_uit080.py`) en `analyse-image` (de
  echte scan, en BZ5 met de echte modellen: zie x3b.md).

## De review (golf 6)

De review van de samengevoegde kop gaf zeven punten (G6-1 … G6-7). Dit is het punt van X2b; de
andere staan in x3b.md, De review (de pins van SigLIP, de samenvoeging met `origin` en de nieuwe
telling van `api-analyse`, twee tests van beeld, Playwright in de job `e2e`, een regel in de lijst
van bestanden).

| Punt | Wat er gedaan is | Test |
|---|---|---|
| **G6-1** (moet) `infra/single/tests/test_analyse_container.sh` (job `analyse-image`) vroeg bij `analyse-status` en bij `caps.built` de lijst opdrachten zonder `group`; `server.BUILT` heeft `group` sinds X2b, dus de job werd rood | Klopt. Beide lijsten in het script noemen nu `group`: `analyse-status` met `cancel caps embed_image embed_text faces forget group rank`, en de proef in de container met `sorted(caps.built)` gelijk aan die acht (`8d0809c`) | `tests/test_compose_analyse.py::test_de_containertest_kent_precies_de_opdrachten_van_built` legt beide lijsten in het script vast op `caps.built(server.BUILT)`: zonder de wijziging rood, nu groen; `bash -n` en shellcheck schoon. Het script zelf draait alleen in de job `analyse-image` (CI na de push) |

## De nacontrole (golf 6)

Op de kop van de review (`b14009e`), op 16 november 2026, in een losse worktree. Hoe er gewerkt
is, wat X3b raakt (en de herstellingen `9228eb6`: uit en wissen van beeld vergaten de lijsten van
60 s in de app, en `a453869`: `test_install_beeld.sh` rekende nog met de uitkomst van `VERSIE` 1)
en de hele tabel van wat er gedraaid is, staan in "De nacontrole (golf 6)" in
[x3b.md](x3b.md); hier wat X2b raakt. Niets gepusht, geen tag; de versie blijft 0.8.0.

### G6-1 weggehaald

| Commit (punt) | Wat weggehaald | Rood | Oordeel |
|---|---|---|---|
| `8d0809c` (G6-1) | `analyse-status` in `test_analyse_container.sh` weer zonder `group` | 1: `tests/test_compose_analyse.py::test_de_containertest_kent_precies_de_opdrachten_van_built` | gezien |
| | de proef met `sorted(caps.built)` in de container weer zonder `group` | 1, dezelfde test | gezien |

Na elke mutatie `git checkout` van het script, en dezelfde test weer groen. Dat het script in de
container echt `group` ziet, zegt alleen `analyse-image` (CI na de push).

### Klaar als, nagelopen

Elke test die Klaar als en "Wat stap 2 in de keten vond" noemen, bestaat, met de genoemde naam.
Wat niet meer klopte, is in de tekst zelf verbeterd:

- de alinea bovenaan, Vormen 1 en de rij van `test_faces_off.py` zeiden dat de routes van
  gezichten zonder de analyse `409 faces_unavailable` geven. Sinds `dcd6bc1` is dat `501
  not_built("X2")` zoals 0.8.0, en 409 alleen met de analyse aan zonder de map met sleutels
  (`test_faces_off.py::test_elke_route_van_gezichten_antwoordt_als_in_080` en
  `::test_analyse_aan_zonder_sleutelmap_is_ook_uit`);
- `tests/people/faces/` telt 96 tests en 1 met `zoek_perf` (94 bij X2b): `test_faces_naast_beeld.py`
  (2) kwam erbij bij de samenvoeging;
- `test_group_perf.py` heeft sinds `c2be5fc` 2 tests;
- `api-analyse` telt sinds G6-3 329 en 16 (x3b.md, De review). De 229 onder Gebouwd en Open 1 is
  die van X2b alleen, en blijft daar staan als geschiedenis.

"Ongewijzigd t.o.v. X2a" in Metingen (de tijd per foto met de echte modellen) was geen meting:
`ops/faces.py` is sinds `625e946` niet veranderd (`git log`), maar met de echte modellen was bij
X2b niets gemeten. De nacontrole heeft het gemeten (hieronder); de rij in Metingen zegt het erbij.

### Gedraaid

Op `9228eb6`, in de keten van x3b.md (De nacontrole, Gedraaid), onder het slot met `taskset -c
0-2`; daar staan ook de volle api-suite (6.844 groen), `tests_analyse` (329 groen, 16 niet
gekozen), de run als nobody (912 groen, met `tests/people/faces`), de web, `slim.sh` en de
e2e-set. Wat gezichten raakt:

| Wat | Hoe | Uitkomst |
|---|---|---|
| de routes bij 60.000 gezichten (poort p95 ≤ 100 ms, de stand ≤ 50 ms) | `tests/people/faces/test_faces_perf.py`, marker `zoek_perf`, `-s`; N = 100.000, SQLite 3.53.1 | voorstellen p95 39,4 ms, groepen 38,6 ms, een foto 13,0 ms, de stand 33,8 ms: gehaald (de boom met 60.000 gezichten klaar in 73,7 s) |
| groeperen, 60.000 open en 6.000 bevestigd (poort ≤ 60 s, ≤ 400 MB) | `tests_analyse/test_group_perf.py` (2), `-s` | 26,2 s, maxrss 256 MB, zuiverheid 1,0000, recall 0,986 |
| de tijd per foto met de echte modellen (poort ≥ 250 foto's per minuut per core met één gezicht) | `tests_analyse/test_models_real.py::test_de_tijd_per_foto_en_de_maxrss_van_het_kind`, `-s`, `ANALYSE_MODELLEN` met de YuNet en SFace van `models.lock` | 28,1 ms per foto zonder gezicht, 29,4 ms per gezicht, 1.043 foto's per minuut per core; maxrss van `werk` 224 MB |
| e2e | `test_zoek_gezichten.py` en `test_zoek_gezichten_echt.py` met `-m "" -rs` (samen met `test_zoek_beeld.py`) | `test_zoek_gezichten.py` groen (10 groen met beeld); `test_zoek_gezichten_echt.py` overgeslagen: geen image, alleen in `analyse-image` |
| het chunk van gezichten (begroting 12.000 B gzip) | `npm run budget` | 11.837 B |
| `test_install_analyse.sh` | als root | 128 controles groen |
| `test_compose_analyse.py` (G6-1) | in de volle api-suite en als nobody | groen |

### Klaar als, na de nacontrole

Elke rij van Klaar als noemt een test die bestaat en hier groen was, zegt dat het bewijs in CI
na de push komt (`api-analyse`, `zoek-perf`, `analyse-image`) of op de LXC (`analyse-bench`), of
staat open met zijn nummer: de kwaliteit op echte gezichten (Open 4), §4.5 met X2 aan (Open 5);
`test_faces_quality.py` is niet gebouwd (Afwijkingen 3). Wat niet klopte, is hierboven
verbeterd.

### Open na de nacontrole

- ~~**Alleen in CI, na de push:** `analyse-image` (G6-1: `test_analyse_container.sh` met `group` in
  de container; `test_zoek_gezichten_echt.py` met het echte image), `api-analyse` (exact 329 en
  16), `zoek-perf` (ook `test_faces_perf.py`) en de rest uit x3b.md, "Open na de nacontrole".~~
  [I2: groen op `e535332` (run 43892193604). Op `dc5dfe5` waren drie tests rood die alleen in CI
  draaien (de kinderen in `test_analyse_container.sh`, de maxrss van de omzetter en `/proc` als
  gewone gebruiker); hersteld in `e535332`, met één test erbij: `api-analyse` telt nu 330 en 16
  ([x3b.md](x3b.md), "CI op dc5dfe5"). Sinds `4aa42e1` (review I2, U1) is dat 331 en 16.]
- **Op de LXC:** `analyse-bench` met de echte modellen (het runbook zegt hoe).
- **`test_install_update.sh`** is niet gedraaid (de schijf; x3b.md) en staat niet in CI (x2a.md,
  Open 8).
