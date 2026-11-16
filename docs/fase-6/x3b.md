# X3b: zoeken op wat er op een foto staat

X3b is het deel van X3 in golf 6, naast X2b ([bouwontwerp.md](bouwontwerp.md), "X3 — Zoeken op
wat er op een foto staat"; Keuzes 69, 75–84; §3.11; K1-08, K1-16, K2-07, K2-13, K2-23). X3a
(golf 5) bouwde de tokenizer, de omzetter en de CI-job `beeld-model` ([x3a.md](x3a.md)). X3b
bouwt de rest:

- de opdrachten `embed_image`, `embed_text`, `rank` en `forget` in de kinderen `werk` en
  `zoeken` van de container analyse, met de cache van vectoren;
- in de app `app/beeld/*` en `app/beeld_rows.py`: de stap `embed`, het bestand per eigenaar, de
  jobs, de grenzen, het zoekpad met de terugval, `/photos/beeld`, aan en uit per gebruiker en
  voor iedereen, wissen, en `app.cli beeld-status`, `beeld-bench` en `beeld-kwaliteit`;
- in `install.sh` de stap `BEELD` (het model ophalen, omzetten in `analyse-omzetten`,
  nakijken, opruimen) met `test_install_beeld.sh`, en de pins van SigLIP 2 in `models.lock`;
- de web van Keuze 81 en 82: het raster op `/zoeken`, "Meer zoals deze", de schakelaar in
  Account → Foto's en het blok op `/beheer`, met `beeld.test.ts` en `e2e/test_zoek_beeld.py`;
- dit document, [toetsenbord-x3.md](toetsenbord-x3.md), het deel beeld van
  [docs/runbooks/analyse.md](../runbooks/analyse.md) en van `infra/single/README.md`.

Gebouwd op de tak `wip/golf6-x3b`, vanaf `625e946`, in de worktree `wt-g6-x3b`, naast X2b. Er is
niet gepusht. De versie blijft 0.8.0.

**Tot 0.8.1 onzichtbaar.** Met de analyse uit (geen container, `PLATFORM_ANALYSE_ENABLED` niet
waar, geen `ANALYSE`-regel) meldt de app niets van beeld aan: geen stap `embed`, geen job in de
rij `analyse`, geen bestand onder `DATA_ROOT`, geen nieuwe regel in `.env`. Elke route van beeld
geeft `501 not_built` zoals in 0.8.0, een vraag zonder `mode` geeft wat hij in 0.8.0 gaf, en de
web toont wat 0.8.0 toonde. Zie "Met de analyse uit" hieronder voor de tests die dat bewijzen.

## Gebouwd

### Stap 1: de container, de app, install.sh en CI

| Commit | Wat |
|---|---|
| `58263d6` | `app/analyse/worker/ops/beeld.py`: `embed_image` in `werk` (previews als fd, 768 × f16, lengte 1), `embed_text`, `rank` en `forget` in `zoeken` (zonder Pillow); de cache van vectoren per (eigenaar, `gen`, sha256), hooguit 400 MB, de eigenaar die het langst niet zocht eruit; `caps.beeld` uit `meta.json`. Vier regels in `child.py` en `server.py` (BUILT) |
| `89b4989` | `app/beeld/*`: de stap `embed`, het bestand per eigenaar (`vecfile.py`), de jobs `beeld.vec`, `beeld.on`, `beeld.off`, `beeld.wipe`, de grenzen (1 tegelijk, 10 wachtenden, daarboven `429 search_busy`), de cache van 60 s, de klok van 800 ms met terugval, de SQL met `json_each` en de tweede controle; `GET /photos/beeld`, `GET /search?mode=image` met `q` of `like`, `DELETE /admin/search/beeld`; de instellingen `PLATFORM_BEELD_*`. Alles alleen met de analyse aan |
| `67a2892` | de gebeurtenissen van beeld (de tabel van het ontwerp) met `test_beeld_events.py` (16) en de nep-worker `tests/beeld/nep.py` |
| `a94e9cf` | `test_beeld_search.py`, `test_beeld_access.py`, `test_beeld_off.py`, `test_beeld_never_out.py` |
| `0c79f01` | `test_embed_step.py`, `test_vecfile.py`, `test_beeld_gen.py` |
| `c30d8c2` | `geheimwoord7` via `mode=image` nergens in de logs (`test_search_logs.py`) |
| `f37b1ba` | `beeld-status`, `beeld-bench` en `beeld-kwaliteit --personen` (BZ9, BZ10) in `app/beeld/cli.py`; uit `ANALYSE_STUBS` |
| `a96060f` | `tests_analyse/test_beeld_perf.py`: p95 ≤ 100 ms via HTTP bij 55.000 en 100.000 foto's op één core, met een nep-tekstmodel |
| `0a90be0` | `install.sh` `BEELD` (Keuze 84) met `test_install_beeld.sh` in job `infra`; de tien regels van SigLIP 2 in `models.lock` |
| `70ba0f0` | `test_search_logs.py` los van de volgorde; de nieuwe exacte lijst van caps |
| `bd10d6c` | de voortgang van beeld telt alleen als er iets open staat (EXISTS op `ix_analysis_todo`): 0,7–1,3 ms per vraag in plaats van 4,7–9,4 ms |
| `4d2644d` | `test_beeld_perf.py` met de heap in `gc.freeze`, zoals `serve.freeze_heap` in de echte app; grens ongewijzigd |
| `866595c` | de rijen van beeld (`write_embed`, de vragen van Keuze 69) in `app/beeld_rows.py`, buiten de wachter van `own_live`, zoals `photo_rows.py` |
| `9aa699d` | `disable()` (alleen voor tests) haalt ook de soorten jobs van beeld weg; een exacte test |
| `ff5dd6d` | `tests/beeld` in `test_conftest_volgorde.py` |
| `5792c08` | BZ8 in blokken van 25 om de beurt (ABBA), met de heap in `gc.freeze`; grens 1,10 ongewijzigd |

### Stap 2: de web, e2e en de documenten

| Commit | Wat |
|---|---|
| `e5bce37` | `admin/BeeldBlock.tsx` (Keuze 82): de stand, "Zet uit voor iedereen" / "Zet aan voor iedereen", "Opnieuw berekenen", "Beeldindex wissen" met bevestiging, model, container, bekeken, mislukt per code, het afgelopen uur, ruimte, `beeld-bench`, `beeld-kwaliteit`, de vaste zin; zonder stand precies de zin van 0.8.0. Het gebied `beeld` in nl en en; `errors/beeld.ts` getoetst aan de echte codes |
| `2463ed0` | `search/beeld/status.ts` (de stand van `GET /photos/beeld`; 501 is `off`, tien minuten bewaard), `beeld.ts` (de vraag, de URL, de pijlen, de meldingen), `BeeldResults.tsx` (het raster), `beeld.css`; `ImageHint.tsx` leest de stand via een dynamische import |
| `6552879` | `BeeldSwitch.tsx` (de schakelaar met de uitleg) en `viewer/LikeRow.tsx` ("Meer zoals deze") |
| `f3ff04a` | `beeld.test.ts` (44 tests) |
| `4c662e7` | na "Meer foto's laden" de focus op de eerste nieuwe foto (`BeeldResults.tsx`), zoals "Meer laden" van Z4; zonder dat viel de focus weg als de knop verdween |
| `519a414` | dit document, `toetsenbord-x3.md`, het runbook en de README |
| `2ae5e44` | `e2e/test_zoek_beeld.py` (7 tests) |
| `4924b30` | `app/search/admin.py` (Z7): de motor, "namen klaar" en de herbouw in `GET /admin/search` uit één lezing van `search_state`, met `test_motor_namen_en_herbouw_uit_een_lezing`. Gevonden doordat `test_zoek_beheer.py` in `slim.sh` rood werd (Afwijking 14) |
| `4e286b9` | de focus in het raster meteen, niet een frame later (`BeeldResults.tsx`), met de achtste e2e-test `test_esc_vlak_na_de_viewer_blijft_in_het_veld` en een test in `beeld.test.ts` (45). Gevonden doordat `test_zoek_beeld.py` in `slim.sh` rood werd (zie Gedraaid) |
| de laatste | dit document na de eindruns (Gedraaid, Klaar als) en de vinkjes in `toetsenbord-x3.md` |

## Met de analyse uit: zoals 0.8.0

| Wat | Bewijs |
|---|---|
| geen stap `embed`, geen job van beeld, geen bestand onder `DATA_ROOT`, elke route van beeld `501`, `GET /search` zonder `mode` als in 0.8.0 | `tests/beeld/test_beeld_off.py::test_met_de_analyse_uit_niets_van_beeld`, `::test_de_soorten_alleen_zolang_beeld_aangemeld_is` |
| `beeld-status`, `beeld-bench`, `beeld-kwaliteit` doen niets (één regel, code 1, geen bestand) | `tests/beeld/test_beeld_cli.py::test_met_de_analyse_uit_niets` |
| geen download, geen omzetting, geen map en geen regel zonder `ANALYSE=aan` of met `BEELD=uit` | `test_install_beeld.sh` |
| de stand beeld op `/zoeken`: niets zolang de stand er niet is, dan de zin van 0.8.0, ook in de beschrijving van het veld; Account → Foto's met het vinkje van 0.8.0; geen "Meer zoals deze"; `/beheer` met de zin van 0.8.0 en geen knop | `beeld.test.ts`, "met de analyse uit: precies wat 0.8.0 toonde" (5); `lazySlots.test.ts`; `infoRows.test.ts` |
| hetzelfde in de browser, met de echte server met de analyse uit: geen raster, geen melding, geen vraag aan `/search`, geen uitleg van de schakelaar, geen rij in de viewer, `/beheer` zonder knop | `e2e/test_zoek_beeld.py::test_met_de_analyse_uit_blijft_de_web_zoals_in_0_8_0` |

Eén verschil met 0.8.0 is er wel, zonder dat de gebruiker het ziet: elke pagina die een plek van
beeld toont, vraagt één keer `GET /photos/beeld` en krijgt `501` (tien minuten bewaard). Die route
bestond in 0.8.0 al als stub met hetzelfde antwoord; de server doet er geen werk voor. Zie
Afwijkingen.

## De go/no-go van X3

Keuze 73 en K2-12 noemen drie delen; §5 vraagt deel 1 en BZ1. Wat er nu van bewezen is:

| Deel | Stand |
|---|---|
| 1. De kleine vorm rekent als een eigen numpy-versie | gehaald (X3a, opnieuw gemeten in de nacontrole van golf 5) |
| 2. Twee keer omzetten geeft dezelfde sha256 (BZ2) | gehaald in `beeld-model` op `26c49ca` (x3a.md, CI op 26c49ca) |
| 3. De kwaliteitspoort (BZ4) | **niet gehaald, niet gedaan**: op `26c49ca` niet gerekend (HTTP 429 van Commons). Streng wordt BZ4 pas in een run van `beeld-model` met de pins in `models.lock` én `api/tests/analyse/sets/beeld.tsv`. De pins staan er (`0a90be0`), `beeld.tsv` niet: die zet de lead, uit het voorstel van een run. **Of BZ4 groen is, is het oordeel van CI na de push**, niet van deze tak |

Volgens x3a.md ("De go/no-go na CI") gaat X3b door met BZ4 streng als poort van X3b: een rode
BZ4 met de pins en de set houdt X3b tegen. Dat is hier niet anders gemaakt. Niets is hier beslist
of als beslist gemarkeerd.

## Metingen

Hier gemeten, in de sandbox, onder `taskset -c 0-2`, met de nep-worker of nepmodellen; met het
echte model is hier niets gemeten (huggingface.co is vanuit de sandbox dicht).

| Wat | Hoe | Uitkomst |
|---|---|---|
| p95 van een beeldvraag via HTTP, alles behalve het tekstmodel (Keuze 78, K2-13) | `test_beeld_perf.py`, 20 vragen, één core voor het kind, grens 100 ms | warm 55.000: 58,5 en 54,5 ms; warm 100.000: 74,0 en 78,3 ms; eerste vraag na een nieuw (opgewarmd) bestand bij 100.000: 93,7 en 81,6 ms (vóór `bd10d6c` en `4d2644d`). Daarna 74,1 / 90,2 / 81,1 / 89,1 ms. Eén rode run op 9: de eerste volle `tests_analyse`, nog vóór `bd10d6c` en `4d2644d`, met p95 114,8 ms (max 184,9) voor de eerste vraag na opwarmen bij 100.000; grens niet veranderd |
| de fasen van die eerste vraag bij 100.000 | eigen tijdmeting in de test | het kind 25–43 ms, SQL 9–15 ms, treffers 3–6 ms, voortgang ±1 ms, samen 51–77 ms |
| waar de spreiding vandaan komt | `perf`-tellers en losse runs | de matmul in het kind: 25 → 45–76 ms in losse runs onder last op de machine (100.000 × 768 f32 is 307 MB, meer dan de L3 van 260 MB; 55.000 past). Niet de GC, niet de runqueue, niet THP |
| de GC in het testproces | `gc.callbacks` | ±298.000 objecten; een gen-2 100–170 ms (eens 107,9 ms midden in "sql"); met `gc.freeze` 2–6 ms. De echte app heeft `serve.freeze_heap` |
| de voortgang (`indexing`) per beeldvraag | vóór en na `bd10d6c` | 4,7–9,4 ms → 0,7–1,3 ms |
| BZ8: een import van 500 foto's met en zonder de triggers van beeld | `test_bz8_…`, blokken van 25, ABBA, som per kant | 1,011 en 0,993 (grens 1,10); met een heap van 3,3 miljoen objecten 1,035. Ter controle dat de test iets ziet: een `WHEN` die 4 ms duurt geeft 1,206, 8 ms 1,442 (rood) |
| `test_install_beeld.sh` | als root | 120 controles groen, ±6,7 s; `test_install_analyse.sh` 128, media 127, search 209, env groen |
| de eerste lading van de web (gz) | `node scripts/budget.mjs --json`, vóór (`5792c08`, eigen build) en na; daarna de drie bestanden ruw en met de hashnamen gelijkgemaakt vergeleken | 98.039 → 98.040 B (op `4e286b9`). `index`, `shell` en `vendor` zijn ruw even groot (255, 90.787 en 218.792 B) en zonder de hashnamen byte voor byte gelijk: de 1 B is een andere hashnaam in een bestandsnaam. Grens: +0 B |
| het raster (gebied beeld: `BeeldResults`, `BeeldSwitch`, `status`, de teksten) | idem | 10.883 B (grens 15.000) |
| "Meer zoals deze" (`LikeRow`, `status`, de teksten) | idem | 2.558 B (grens 3.000) |
| `SearchPage` met wat het laadt | idem | 34.337 → 34.580 B (+243; `ImageHint` vraagt de stand via een dynamische import, Afwijking 3) |

Met het echte model (uit `beeld-model` op `26c49ca`, x3a.md): `embed_image` 39,8 ms per foto op
één core, 1.508 per minuut (poort ≥ 500); een vraag (tokenizer en tekstmodel) p95 35,5 ms;
`tokenizer.bin` laden 0,135 s. Samen met de 51–77 ms van het pad zonder tekstmodel is een warme
vraag bij 100.000 foto's naar verwachting ±90–115 ms, onder de 250 ms; dat is een optelsom, geen
meting. BZ6 als geheel (het pad met het echte model, `werk` vol, tijdens de backfill) en BZ9 zijn
niet gemeten.

## Afwijkingen

**Van het ontwerp, en vormen die het ontwerp open liet.**

1. **Met de analyse uit geven de routes van X3 nog `501`.** De Klaar-als-regel zegt "geen route
   van X3 geeft nog `501`"; de FACTS van golf 6 zeggen dat golf 6 onzichtbaar blijft tot 0.8.1 en
   dat nieuwe routes een duidelijke uit-code mogen geven. Met de analyse aan geeft geen route van
   X3 nog `501`.
2. **Eén `GET /photos/beeld` per pagina** met een plek van beeld, ook met de analyse uit (dan
   `501`, tien minuten bewaard in `status.ts`). Zonder die vraag weet de web niet of de server
   zoeken op beeld kent. De server doet er niets voor; de route bestond in 0.8.0 al met dat
   antwoord.
3. **`ImageHint.tsx` (van Z4) aangeraakt**: de uitleg bij het veld vraagt de stand via een
   dynamische import van `status.ts`. Een statische import zette 31 B (ruw) in de eerste lading;
   zo blijft die gelijk. Zolang de stand er niet is, wijst `aria-describedby` naar een id die er
   nog niet is (de verborgen zin van 0.8.0 komt pas als de stand `off` is); een schermlezer
   leest dan alleen de rest.
4. **`like` zonder `type`**: "Meer zoals deze" stuurt `type=image` niet mee, zodat de terugval
   (gewoon zoeken) niet al je foto's geeft. `type=image` gaat alleen mee met `q` of een filter.
   `q` en `like` samen: `q` gaat voor (de API geeft anders 400).
5. **De datum in het raster** is `modified_at` van de node (de API geeft bij een treffer geen
   opnamedatum).
6. **"Misschien meer"** staat er als er een filter is, de API geen volgende pagina meldt en er
   minder dan 48 foto's terugkwamen; het ontwerp noemde de melding, niet wanneer.
7. **Een pagina is 48 foto's** (PAGE 48 in `beeld.ts`), tot de grens van 1.000 kandidaten.
8. **Het afgelopen uur** op `/beheer` noemt ook "keer zonder model" (de API telt `no_model`); het
   voorbeeld in Keuze 82 noemde alleen te traag en zonder container.
9. **Het model: "niet nagerekend in CI"** kan de web tonen, maar de API geeft het niet: de pins
   staan in `models.lock` en worden in CI nagerekend, dus `model` is `ok` of `missing`.
10. **De schakelaar per gebruiker heeft geen bevestiging**; uit wist meteen wat alleen voor jou
    bewaard is (Keuze 82). De uitleg onder het vinkje zegt dat.
11. **De tekst bij een foto zonder vector** ("Deze foto is nog niet bekeken. Probeer het straks
    opnieuw.") is een eigen keuze; het ontwerp noemde de code `beeld_no_vector`, niet de zin.
12. **Het document heet `x3b.md`**, niet `x3.md` zoals de tabel Eigendom zegt; zo staat het naast
    `x3a.md`.
13. **Na "Meer foto's laden"** gaat de focus naar de eerste nieuwe foto, zoals "Meer laden" van Z4
    (toetsenbord-z4.md); het ontwerp zei niet waar de focus dan staat. Zo valt hij niet weg als de
    knop na de laatste pagina verdwijnt.
14. **Een fout in `app/search/admin.py` (Z7) hersteld** (`4924b30`). De eerste `slim.sh` van
    stap 2 werd rood op `test_zoek_beheer.py::test_de_tab_met_alleen_het_toetsenbord`: na het
    einde van de herbouw gaf `GET /admin/search` motor `like` zonder herbouw. De oorzaak: `state()`
    las de cursor van `search_state` twee keer, voor de motor (`engine_for`) en daarna voor de
    herbouw (`rebuild.progress`, op een eigen verbinding). Viel het laatste stuk daartussen, dan
    klopte het antwoord niet met zichzelf. Nu één lezing; de motor zoals `engine_for`. Geen
    contract veranderd (de vorm van `AdminSearch` is gelijk), geen test losser. De nieuwe test
    laat het laatste stuk midden in de GET vallen en is zonder de wijziging rood met
    `("like", False, None)`. Niet van X3b, maar het hield de controles van deze stap tegen.

**Bestanden van anderen die X3b raakte** (alleen hotspots of tests die een exacte lijst
hebben):

- `app/analyse/worker/child.py`, `server.py` (X2a): de handlers en BUILT, hotspots sinds
  `913c318`;
- `app/cli.py`: `beeld_cli` aangemeld, de drie stubs weg; `tests/test_cli_db.py`: de nieuwe exacte
  lijst van stubs;
- `app/config.py`: `PLATFORM_BEELD_*`; `app/routers/search.py`: `mode=image` naar
  `app/beeld/search.py`;
- `tests/analyse/fake_worker.py`, `test_fake_analyse_worker.py`, `test_analyse_monitor.py`,
  `test_models_lock.py`, `test_lock_siglip.py`, `test_beeld_tools.py`; `tests_analyse/echt.py`,
  `test_analyse_contract_real.py`, `test_analyse_kinderen.py`, `test_worker_sandbox.py`: de nieuwe
  opdrachten en de nieuwe regels van `models.lock` (nieuwe exacte lijsten, niets losser);
- `tests/search/test_search_logs.py` (Z1), `tests/test_conftest_volgorde.py`;
- `app/search/admin.py` en `tests/search/test_admin_search.py` (Z7): de stand in één lezing
  (Afwijking 14);
- `infra/single/install.sh` (gedeeld), `.env.example` (een regel als commentaar),
  `tests/test_analyse_container.sh` (de nieuwe lijst opdrachten; `embed_text` zonder SigLIP is nu
  `model_missing` in plaats van `not_built`), `.github/workflows/ci.yml` (`ANALYSE_TESTS` 235,
  de stap van `test_install_beeld.sh` in `infra`);
- de web: `search/ImageHint.tsx` (Z4, hierboven), `admin/searchTab.test.ts` (Z7; de nieuwe exacte
  HTML van het blok met een stand), `lazySlots.test.ts` en `viewer/infoRows.test.ts` (Z0b; de
  plekken zijn nu gevuld, met de analyse uit nog steeds zoals 0.8.0);
- de documenten: het deel beeld van `docs/runbooks/analyse.md` en `infra/single/README.md`.

Geen contract van A0 of Z0b is veranderd (`app/search/types.py`, `web/src/search/types.ts`,
`web/src/api.ts` zijn niet geraakt). Geen migratie, geen trigger, geen nieuwe afhankelijkheid
(`uv.lock` en `web/package-lock.json` zijn gelijk), geen nieuwe GitHub-action.

## Open

1. **BZ4 streng**: `beeld.tsv` uit het voorstel van een run van `beeld-model`, door de lead; dan
   rekent CI de poort. Rood houdt X3b tegen (de go/no-go hierboven).
2. **De pins van de uitkomst** in `models.lock` (`beeld.int8.onnx`, `tekst.int8.onnx`,
   `meta.json`) komen uit de run op `26c49ca`. Op de branch staat sinds `eb3f763` een andere
   int8-omzetting (|q| ≤ 64), dus na de samenvoeging kloppen die drie niet meer; de strenge run
   zegt het, en de lead vervangt ze dan uit het voorstel. Geen sha256 is hier verzonnen.
3. **BZ5** (het geheugen met een groepsronde van 60.000, de cache vol en een herlaadactie) heeft
   de groepsronde van X2b nodig en het echte model: niet gemeten. Na de samenvoeging met X2b, in
   CI of op de LXC.
4. **BZ6 als geheel** met het echte model (het pad, `werk` vol, tijdens de backfill van de
   index) en **BZ9** (`beeld-bench` op de LXC van Jonas): niet gedaan. Het runbook zegt hoe.
5. **BZ10** (`beeld-kwaliteit --personen` op de LXC): niet gedaan. Boven toeval geldt de regel
   van Keuze 82: dan geeft "Meer zoals deze" niets op een foto met een gevonden gezicht, en gelden
   voor beeld de regels van gezichten. Die regel is niet gebouwd; dat is een beslissing na de
   meting.
6. **`test_beeld_perf.py`** eens rood in negen runs (de eerste vraag na een nieuw bestand bij
   100.000: p95 114,8 ms), in de eerste volle `tests_analyse`, nog vóór `bd10d6c` en `4d2644d`.
   Daarna groen, met p95 tot 90,2 ms: weinig ruimte onder de 100 ms. In CI in de gaten houden; de
   grens blijft 100 ms.
7. **`infra/single/README.md`** zegt bij de analyse "vóór 0.8.1 doet de app er nog niets mee:
   alleen de status". Met X2b en X3b op de branch klopt dat niet meer voor wie de analyse nu al
   aanzet; die zin is van de analyse als geheel (X2), dus voor de samenvoeging of I2.
8. Niet gedaan en niet van X3b: B49–B70, Vraag 4 en Vraag 8–14 blijven open (Vraag 11 is "uit",
   Vraag 14 is 250 ms, zoals in het ontwerp).

## Klaar als

De regels van X3 in het ontwerp. "In CI" is na de push; "op de LXC" is na de installatie bij
Jonas.

| Regel | Bewijs | Uitkomst |
|---|---|---|
| De contracten van Keuze 80 ingevuld; geen route van X3 geeft nog `501`; een vraag zonder `mode` als vóór X3; geen test zwakker | `test_beeld_search.py` (9), `test_beeld_access.py` (4), `test_contracts.py` (26), `test_beeld_off.py::test_met_de_analyse_uit_niets_van_beeld` | met de analyse aan geen `501`; met de analyse uit bewust `501` (Afwijking 1). Groen in stap 1 (zie Gedraaid); in CI |
| 0012 heen en terug, de triggers gelijk aan `schema.py`, BZ8 groen | 0012 en de triggers: A0 (`test_migration_0012.py`, `test_triggers_beeld.py`); BZ8: `test_beeld_gen.py::test_bz8_…` | BZ8 groen hier (1,011 en 0,993); in CI |
| `test_tokenizer.py` en BZ3 groen | X3a (`test_tokenizer.py`, 95); `beeld-model` | van X3a, hier niet geraakt; BZ3 met de nieuwe lijst: in CI |
| De omzetter gelijk aan numpy, twee keer dezelfde sha256 | X3a (`test_beeld_omzetten.py`) | gehaald (x3a.md) |
| De go/no-go na X3a | x3a.md, "De go/no-go na CI" | deel 1 en 2 gehaald, deel 3 (BZ4) niet; niet beslist |
| De echte omzetting (BZ2) in `beeld-model`, de sha256's in `models.lock` | `beeld-model` op `26c49ca`; `0a90be0` | gedaan; de pins van de uitkomst moeten na de samenvoeging opnieuw (Open 2) |
| De opdrachten in de container met nepmodellen; `rank` gelijk aan brute force | `tests_analyse/test_beeld_ops.py` (13), `test_beeld_cache.py` (9) | groen in de volle `tests_analyse` van stap 1; in CI (`api-analyse`, 235) |
| Zoeken: alleen eigen, levende foto's; elk filter; de terugval; geen vector en geen score; schone logs; `429`; een nieuwe vraag stopt de oude | `test_beeld_search.py`, `test_beeld_access.py`, `test_beeld_never_out.py`, `test_search_logs.py`, `test_beeld_events.py::test_beeld_fallback` | groen in stap 1 (zie Gedraaid); in CI |
| Uit en wissen: 0 rijen, geen bestand, `forget`; `blob_embed` niet in de reservekopie; de beheerder kan uitzetten | `test_beeld_off.py`, `test_beeld_events.py` (`off`, `wipe`, `admin_off`), `test_backup_gezichten.py` (A0); `e2e/test_zoek_beeld.py` (schakelaar, beheerder) | API: groen in stap 1 en in de volle suite op `4924b30` (Gedraaid); e2e: groen op `4e286b9` |
| De kwaliteitspoort (BZ4) groen | `test_beeld_kwaliteit.py` in `beeld-model` | **open**: in CI na de push, met `beeld.tsv` (Open 1) |
| De tijden: p95 ≤ 100 ms zonder tekstmodel; ≤ 250 ms warm met het echte model, ook bij 100.000 en de eerste vraag; ≥ 500 foto's per minuut | `test_beeld_perf.py`; `test_beeld_echt_perf.py` in `beeld-model` | ≤ 100 ms: groen, eens rood in 9 (Metingen, Open 6); ≥ 500 per minuut: 1.508 op `26c49ca`; ≤ 250 ms met het echte model: in CI |
| Het geheugen (BZ5) | — | **open** (Open 3) |
| BZ10 gemeten | `beeld-kwaliteit --personen` | **op de LXC**, niet gedaan (Open 5) |
| `test_install_beeld.sh` groen in `infra`; `BEELD=uit` werkt; een mislukking is niet fataal | `test_install_beeld.sh` | 120 controles groen hier (stap 1); in CI |
| De web: vitest en e2e met alleen het toetsenbord groen; de eerste lading +0 B; het raster ≤ 15 kB; `toetsenbord-x3.md` | `beeld.test.ts` (45), `e2e/test_zoek_beeld.py` (8), `npm run budget` | groen op `4e286b9` (Gedraaid); +0 B ruw, 10.883 B en 2.558 B (Metingen); de vinkjes in `toetsenbord-x3.md` |
| Het blok op `/beheer`, het runbook, de README en het document | `BeeldBlock.tsx`, `searchTab.test.ts`, `beeld.test.ts`; dit document | gedaan |
| `e2e/slim.sh` groen, alle bestaande jobs groen (ook `zoek-perf` met 200 ms) | `slim.sh`, de e2e-set van CI hier; de rest in CI | `slim.sh` en de e2e-set groen op `4e286b9` (Gedraaid); `zoek-perf` en de andere jobs in CI |

### Bewijs BZ1–BZ10

| # | Waar | Uitkomst |
|---|---|---|
| BZ1 | `beeld-model` op `26c49ca` | huggingface.co, commons.wikimedia.org en upload.wikimedia.org bereikbaar; images-assets.nasa.gov 403 (x3a.md) |
| BZ2 | idem | twee omzettingen gelijk, maxrss 354 MiB (grens 1.536); de pins in `models.lock` (Open 2) |
| BZ3 | idem | 0 verschillen op 2.000 zinnen; de lijst had 8 rijen met U+E000/U+E001 (hersteld in `e92b1c1`); in CI |
| BZ4 | `beeld-model` | **open**: in CI, met `beeld.tsv` |
| BZ5 | — | **open**: na X2b, in CI of op de LXC |
| BZ6 | `beeld-model` (het model), `test_beeld_perf.py` (het pad) | het model: 1.508 foto's per minuut, een vraag p95 35,5 ms; het pad zonder tekstmodel ≤ 100 ms hier; het geheel: in CI of op de LXC |
| BZ7 | `beeld-model` op `26c49ca` | 0,135 s, 0,0 MB erbij |
| BZ8 | `test_beeld_gen.py::test_bz8_…` (de kosten); `test_triggers_beeld.py` van A0 (de werking, ook op 3.46.1 in de stap "triggers" van `zoek-perf`) | 1,011 en 0,993 (grens 1,10) hier; in CI |
| BZ9 | `beeld-bench` | **op de LXC**, niet gedaan |
| BZ10 | `beeld-kwaliteit --personen` | **op de LXC**, niet gedaan |

### De gebeurtenissen

Elke rij van de tabel "Elke gebeurtenis" heeft zijn test in `tests/beeld/test_beeld_events.py`,
met de nep-worker: `test_beeld_upload_then_found`, `test_beeld_other_uploader`,
`test_beeld_copy_same_owner`, `test_beeld_copy_other_owner`, `test_beeld_owner_change`,
`test_beeld_replace`, `test_beeld_trash`, `test_beeld_purge`, `test_beeld_user_delete`,
`test_beeld_off`, `test_beeld_on_again`, `test_beeld_admin_off`, `test_beeld_wipe`,
`test_beeld_version`, `test_beeld_fallback` en `test_public_never_search` (16). Groen in stap 1
(zie Gedraaid); in CI.

## Gedraaid

Alles op deze machine, naast X2b. De zware runs (de volle api-suite, `tests_analyse`, `slim.sh`,
de e2e) onder het gedeelde slot en `taskset -c 0-2`.

**Stap 1** (de container, de app, `install.sh`):

| Wat | Op | Uitkomst |
|---|---|---|
| de volle api-suite, eenmaal | `4d2644d` | 4 rood, 6.541 groen, 2 overgeslagen, 240 niet gekozen (46:46). De vier: `test_own_live.py::test_elke_fotofunctie_gebruikt_own_live` (hersteld in `866595c`), `test_jobs_lanes.py::test_de_bestaande_soorten_veranderen_niet` (`9aa699d`), `test_conftest_volgorde.py::test_elke_submap_met_een_conftest_staat_in_de_lijst` (`ff5dd6d`) en `test_beeld_gen.py::test_bz8_…` (1,101 in de volle suite, boven 1,10; de meting in blokken om de beurt, `5792c08`). Daarna alleen gericht: groen |
| `test_search_logs.py` en de tests van de container, gericht | vóór `70ba0f0` | 2 rood in `test_search_logs.py` (de test hing aan de volgorde van de logregels) en de oude exacte lijst van opdrachten in `test_worker_sandbox.py`; beide in `70ba0f0` |
| `tests_analyse` met `ANALYSE=aan` | vóór `70ba0f0`; daarna `5792c08` | de eerste keer 2 rood en 233 groen: `test_worker_sandbox.py` (hierboven) en `test_beeld_perf.py` (Open 6); op `5792c08` drie keer 235 groen |
| `test_install_beeld.sh`, `test_install_analyse.sh`, `_media`, `_search`, `_env` | `0a90be0` en later | 120, 128, 127 en 209 controles groen; env groen |

**Stap 2** (de web, e2e, de documenten):

| Wat | Op | Uitkomst |
|---|---|---|
| de web: `typecheck`, `npm test`, `build`, `check:i18n --strict`, `budget` (build en budget direct na elkaar) | `4e286b9` | groen: 121 bestanden en 1.311 tests, 0 meldingen in 290 bestanden, binnen het budget (Metingen) |
| `e2e/test_zoek_beeld.py`, de eerste keer (toen 7) | vóór `2ae5e44` | 2 rood door fouten in de test zelf (de volgorde van `q` en `mode` in de URL; een antwoord dat te laat gelezen werd); daarna 7 groen in 29 s |
| eindketen 1: `slim.sh`, daarna de e2e-set van CI | `2ae5e44` | `slim.sh` stap 1: 1.307 groen (817 s); stap 3: 1 rood, 107 groen, `test_zoek_beheer.py::test_de_tab_met_alleen_het_toetsenbord` (Afwijking 14, hersteld in `4924b30`). De e2e-set na 65 groen gestopt (SIGINT, opruimen netjes): de code veranderde eronder |
| de volle api-suite, eenmaal (de api veranderde in `4924b30`) | `4924b30` | 6.532 groen, 2 overgeslagen, 240 niet gekozen, 1 rood en 16 fouten (46:05). Alle 17 waren `OSError: AF_UNIX path too long` in `tests/ocr/test_ocr_worker.py` en `tests/analyse/test_analyse_monitor.py::test_down_als_niemand_luistert`: ik gaf `--basetemp` een lange map in mijn scratch, en de sockets van die tests werden langer dan de 108 tekens van Linux. Die tests opnieuw met de gewone basetemp: 18 groen. Na `4924b30` is geen code van de api meer veranderd |
| eindketen 2: `slim.sh` | `4924b30` | stap 1: 1.308 groen (833 s); stap 3: 1 rood, 107 groen: `test_zoek_beeld.py::test_zoeken_op_beeld_met_alleen_het_toetsenbord`. Na Esc in de viewer en meteen nog een Esc stond de focus niet in het veld. De oorzaak: `moveTo` in `BeeldResults.tsx` zette de focus in een `requestAnimationFrame`; op een drukke machine kwam dat frame na de tweede Esc en zette de focus terug op de foto. Hersteld in `4e286b9` (de focus meteen; elke cel staat al in de pagina). De nieuwe test `test_esc_vlak_na_de_viewer_blijft_in_het_veld` laat elk frame een halve seconde later komen: rood op de oude build, groen op de nieuwe. De keten daarna gestopt (de web moest veranderen) |
| eindketen 3: `slim.sh` | `4e286b9` | stap 1: 1.308 groen (820 s); stap 3: 109 groen (222 s) |
| eindketen 3: de e2e-set van CI (`-m "not perf and not media and not media_perf and not office and not office_real and not provider_real and not ocr"`) | `4e286b9` | 246 groen, 135 niet gekozen (892 s) |
| eindketen 3: `e2e/test_zoek_beeld.py` | `4e286b9` | 8 groen (35 s) |

## De samenvoeging (golf 6)

Samengevoegd op 16 november 2026 op `claude/stappenplan-uitwerken-lu4uv3` (vanaf `45bb926`):
eerst `wip/golf6-x3b` (`aa21e37`), dan `wip/golf6-x2b` (`dcafcea`), beide met `--no-ff`
(bouwontwerp §2.0 en G5-C4: X3b eerst, de tweede houdt beide regels). Niets gepusht, geen tag; de
versie blijft 0.8.0. Wat X2b raakt, staat in "De samenvoeging (golf 6)" in [x2b.md](x2b.md).

**De conflicten**, alle met beide kanten:

- `aa21e37`: alleen `ANALYSE_TESTS` in `ci.yml` (de lead zette er sinds `625e946` 5 bij, X3b 24);
- `dcafcea`: `server.BUILT` en `child.handlers()` (`group` naast `embed_image` in `werk`;
  `embed_text`, `rank` en `forget` in `zoeken`), met de nieuwe exacte lijsten in
  `test_analyse_kinderen.py`, `test_worker_sandbox.py`, `test_analyse_monitor.py` en
  `test_analyse_contract_real.py`; `tests_analyse/echt.py` kent `group` en `rank` als lange
  opdrachten, en met beide gebouwd staat het scenario `zoeken_naast_werk` in de lijst van de
  echte worker; `ANALYSE_STUBS` in `cli.py` is leeg; het runbook van de analyse met "Wat er nog
  niet is" van beide.

**Wat de samenvoeging erbij deed:**

| Commit | Wat |
|---|---|
| `dcd6bc1` | met de analyse uit geven de routes van gezichten `501 not_built("X2")` zoals in 0.8.0 (X2b gaf `409 faces_unavailable`); `api/tests/uit080/`: een wandeling langs elke route van 0.8.0 |
| `8cc9027` | `tests/people/faces/test_faces_naast_beeld.py`: gezichten en beeld om de beurt in de rij `analyse` en het kind `werk` |
| `a98f5c4` | `e2e/test_uit080.py`: met de analyse uit elke pagina zoals met de web van 0.8.0 |
| `c1b2939` | BZ5 met de nepmodellen (`tests_analyse/test_geheugen_samen.py`), en `Cache.get` laat het oude bestand los vóór het laden |
| `210f665` | de voorbewerking van de worker is die van `beeld_echt.voorbewerk` (x3a.md, Open 4) |
| `df8d56b` | `ANALYSE_TESTS` 262; `infra/single/README.md` noemt beide schakelaars (Open 7); het runbook zegt Account → Foto's |
| `6a98579` | de wandeling van 0.8.0 draait in een nieuw proces: een eerdere lifespan liet `app.state.started_at` staan (Gedraaid hieronder) |
| `c2be5fc` | de poort van groeperen meet `VmHWM` van het kind, niet `ru_maxrss` met de piek van pytest erin; `ANALYSE_TESTS` 263 (Gedraaid hieronder) |
| `b74e1ae` | `test_search_rebuild.py`: de twee tests met een grens van 1 s bevriezen de heap van pytest (`frozen_heap`; Gedraaid hieronder) |

**Gezichten en beeld samen.** Allebei werken in de rij `analyse`, en `faces`, `group` en
`embed_image` gaan naar hetzelfde kind `werk`. `test_faces_naast_beeld.py` draait één nep-worker
met de nep van gezichten en die van beeld, één eigenaar met allebei aan, en pakt de jobs één voor
één zoals de Runner (`jobs.claim`, de vroegste `run_after` eerst):

- `test_om_de_beurt_en_niemand_verhongert`: zolang allebei werk hebben, wisselen een batch van
  gezichten en een batch van beeld elkaar af (in de rij en in de opdrachten aan `werk`); na elke
  `faces` meteen `group` (de suggest van die batch); de `full` aan het eind wacht hooguit op de
  ene batch van beeld die al klaarstond, en houdt beeld niet tegen; alles klaar, geen fout;
- `test_werk_bezig_wijkt_zonder_fout`: met `werk` bezig wijken `faces.scan`, `faces.group` en
  de pomp van beeld met `Defer(DEFER_BUSY)`: terug in de rij na 30 s, zonder poging, zonder
  fout, de foto's weer op `wait` zonder poging; daarna komt alles klaar.

Er hoefde niets aan de code te veranderen: `jobs.claim` neemt de oudste `run_after`, en zowel
`faces.scan` als de pomp zet zijn volgende batch als een nieuwe job achteraan (`jobs.chain`, met
`run_after` nu).

**`/beheer` en Account.** `SearchTab.tsx` toont de blokken Gezichten en Zoeken op beeld onder
elkaar, `PhotoSettingsBlock.tsx` de twee schakelaars; dat zijn de plekken van Z0b, en ze botsten
niet. Bewijs: `searchTab.test.ts`, `facesBlock.test.ts`, `beeld.test.ts` en de e2e
`test_zoek_gezichten.py` en `test_zoek_beeld.py` op de samengevoegde boom (Gedraaid hieronder).

**Met de analyse uit, de hele boom.** Twee wandelingen, elk tegen een opname op de code van 0.8.0
(`3fca440`):

- `api/tests/uit080/test_wandeling.py`: elke route van 0.8.0 (291 met hun methoden, behalve de
  stroom `/events`) als niemand, Eva, de gast Gijs en de beheerder Jonas, tegen
  `antwoorden_080.json`. Elk antwoord gelijk; dezelfde jobs (geen in de rij `analyse`), dezelfde
  Setting, dezelfde bestanden onder de datamap, dezelfde rijen in de tabellen van 0.8.0, en in die
  van 0011 en 0012 alleen wat de triggers van A0 bijhouden (`beeld_owner` 1, `node_origin` 2).
  Vóór `dcd6bc1` vond hij precies twintig antwoorden anders: de routes van gezichten.
- `e2e/test_uit080.py`: voor dezelfde vier elke pagina van de web (elke tab van `/beheer`,
  `/zoeken` ook in de stand beeld, de viewer met het infopaneel), de toegankelijkheidsboom, de
  titel, het pad en de gevraagde routes, tegen `e2e/uit080_web.json` (114 pagina's, opgenomen met
  de web van 0.8.0). Bomen en titels gelijk; de routes op precies `ERBIJ` na: `GET
  /photos/beeld` (`501`, de stub van 0.8.0) en `GET /me/photo-settings` (`200`), allebei routes
  van 0.8.0 met hun antwoord van 0.8.0, die niets tonen. De opname hangt aan de vorm van
  `aria_snapshot` van Playwright 1.65.2 (Open na de samenvoeging).

**BZ5, het geheugen met alles tegelijk** (Open 3). `tests_analyse/test_geheugen_samen.py` start
de echte worker (pid 1, `werk` en `zoeken`, met `RLIMIT_DATA` en de wachter per kind) met de
nepmodellen, en doet tegelijk: in `werk` een groepsronde (`full`) van 60.000 open plus 6.000
bevestigde gezichten; in `zoeken` de cache vol (Eva 100.000 foto's en Bram 36.000: 418,9 van de
419,4 MB) en, midden in die ronde, een nieuw bestand van Eva met 100.000 foto's dat `zoeken`
herlaadt en doorzoekt. Gemeten: `VmHWM` van elk proces, en elke 20 ms de som van `VmRSS`.

| | vóór `c1b2939` | na `c1b2939` (twee runs) |
|---|---|---|
| pid 1 | 36 MB | 36 MB |
| `werk` (grens `RLIMIT_DATA` 1 GiB) | 277 MB | 277 MB |
| `zoeken` (grens 1,5 GiB) | 801 MB | 506–507 MB |
| som van de maxrss | 1.114 MB | 819–820 MB |
| de hoogste som tegelijk | 982 MB | 792–793 MB |
| met wat de echte modellen meer kosten (§3.11: `werk` +224 +165 MB, `zoeken` +340 +100 MB) | 1.943 MB | 1.648–1.649 MB |

De poort: maxrss ≤ 2,5 GB voor de container, elk kind met de echte modellen erbij onder zijn
`RLIMIT_DATA`, `zoeken` hooguit de cache plus 200 MB, geen kind vervangen, geen opdracht mislukt.
Gehaald, ±33 s.

De meting vond een fout: `Cache.get` hield de oude matrix van Eva vast via de lokale `found` tot
na het laden van het nieuwe bestand (100.000 × 768 × 4 B = 307 MB dubbel). Nu laat `get` hem los
vóór het laden, zoals de opmerking erboven al zei;
`test_beeld_cache.py::test_herladen_laat_het_oude_bestand_los_voor_het_laden` toetst het met een
`weakref` (zonder de wijziging rood: `[True] == [False]`).

Wat alleen CI of de LXC meet: het echte SigLIP 2 (tekst en beeld) en YuNet en SFace samen in één
container, en de piek van de cgroup (`memory.peak` onder `mem_limit: 3g`). De optelsom hierboven
gebruikt de getallen van §3.11 (B6), geen meting met de echte modellen.

**Open, per punt:**

- Open 3 (BZ5): gemeten met de nepmodellen, onder de poort; met de echte modellen en de cgroup
  blijft het voor CI of de LXC.
- Open 7 (`infra/single/README.md`): gedaan; de zin "doet de app er nog niets mee" was al weg
  (X2b), en de README noemt nu beide schakelaars (`df8d56b`).
- x3a.md Open 4 (de voorbewerking): `test_beeld_echt_hulp.py::test_de_worker_voorbewerkt_zoals_voorbewerk`
  zet `ops/beeld.preprocess` naast `beeld_echt.voorbewerk` met `meta.json` in de vorm van
  `bouw.py`, op een beeld dat niet vierkant is: dezelfde float32, byte voor byte (`210f665`).
- De telling van `api-analyse`: 263 en 11 (`--collect-only`: 274, waarvan 11 `beeld_echt`).
  Erbij sinds de som van beide (258, die op deze boom 259 was door `zoeken_naast_werk`): BZ5, het
  herladen, de voorbewerking en de meting van de poort van groeperen (`c2be5fc`, hieronder).
- Open 1, 2, 4, 5, 6 en 8 blijven staan. Geen contract van A0 of Z0b veranderd, geen migratie,
  geen pakket, geen nieuwe action; de pins van SigLIP zijn niet aangeraakt.

### Gedraaid op de samengevoegde kop

Elke controle één keer, achter het slot van de zware runs en op drie cores (`taskset -c 0-2`).
Eerst op `3c3ab61`; wat daar rood was, staat onder de tabel, met de oorzaak, de herstelling en
de herhaling van alleen die tests.

| Controle | Kop | Uitkomst |
|---|---|---|
| `ruff check` en `ruff format --check` | `3c3ab61` | schoon (790 bestanden) |
| yamllint (`ci.yml` en de hele repo) en shellcheck | `3c3ab61` | schoon |
| `uv.lock` en `web/package-lock.json` | `3c3ab61` | gelijk aan `45bb926` |
| `docker compose config -q` | `3c3ab61` | vier varianten: zonder analyse, office met grenzen, analyse met grenzen, profiel omzetten |
| web: typecheck, vitest, i18n strikt, build, budget | `3c3ab61` | 1343 tests groen; i18n 0 vondsten in 293 bestanden; binnen het budget |
| `api` vol | `3c3ab61` | 6746 groen, 2 rood (hieronder), 2 overgeslagen, 241 niet gekozen (3098 s) |
| `tests_analyse` (de venv van de analyse) | `3c3ab61` | 261 groen, 1 rood (hieronder), 11 niet gekozen |
| `tests_analyse` | `c2be5fc` | 263 groen, 11 niet gekozen (202 s) |
| `/tmp/ci-nobody/run.sh` | `3c3ab61` | 785 groen, 1 rood (de wandeling, hieronder) |
| `SLIM_CPUS=0-2 e2e/slim.sh` | `3c3ab61` | groen in 1215 s (stap 1: 1309 groen; de e2e van zoeken: 111 groen) |
| e2e, de set van CI (met `test_uit080.py`) | `3c3ab61` | 249 groen, 136 niet gekozen (1141 s) |
| e2e `test_zoek_gezichten.py` en `test_zoek_beeld.py` | `3c3ab61` | 10 groen, 1 overgeslagen (`test_zoek_gezichten_echt` vraagt `E2E_ANALYSE_IMAGE`) |
| `infra/single/tests/test_install_{env,media,search,analyse,beeld}.sh` | `c2be5fc` | alle controles geslaagd (media 127, search 209, analyse 128, beeld 120) |
| `test_install_search.sh --stappen` en `test_install_update.sh` | `c2be5fc` | niet gedraaid: de test stopt zelf, 6 GB vrij voor Docker en hij vraagt er 8 en 12 (Open hieronder) |

**Rood op `3c3ab61`, en waarom.**

- `tests_analyse/test_group_perf.py::test_60000_gezichten_binnen_60_s_en_400_mb`: maxrss 419 MB,
  grens 400. Niet het groeperen: het kind zelf had een piek van 255,7 MB (`VmHWM`), op deze kop
  en op X2b. De test las `ru_maxrss` van het kind, en Linux geeft bij fork en exec de piek van
  de ouder door: dat was de piek van pytest (BZ5 en de andere tests draaien ervoor in hetzelfde
  proces). Nu leest het kind `VmHWM`, zijn eigen piek sinds exec, en
  `test_de_meting_telt_de_piek_van_pytest_niet_mee` toetst dat met 256 MB in pytest: `VmHWM`
  onder 64 MB, `ru_maxrss` erboven (`c2be5fc`). De grens blijft 400 MB; gemeten 24,6 s en
  255,7 MB.
- `tests/uit080/test_wandeling.py` in de volle suite en als nobody: `GET /admin/status` anders dan
  in 0.8.0 (de melding `jobs_stopped`, `started_at` en `uptime_s`). Niet de code van golf 6:
  `main.lifespan` zet `app.state.started_at` en ruimt het niet op, en een test die eerder in
  hetzelfde proces de lifespan draait (`tests/test_rate_limits.py`) laat het staan; dan zegt
  `system_info` dat de jobs stil staan. In een vers proces is het antwoord gelijk. De wandeling
  draait nu in een nieuw proces (`wandel.in_een_nieuw_proces`, `6a98579`), met dezelfde
  beweringen. Met de tests van de lifespan ervoor: 63 groen; als nobody 44 groen.
- `tests/search/test_search_rebuild.py::test_een_upload_wacht_hooguit_1_s_en_de_index_klopt`:
  een upload wachtte 1145 ms (grens 1000; 7 stukken, het langste 33 ms, dus niet de
  schrijfpoort). Op die plek in de suite volgt de gc van pytest 1,25–1,39 miljoen objecten (deze
  test alleen: 244.000; de nieuwe tests van golf 6 brachten er ±234.000 bij), en een volle ronde
  daarover kost 0,6–1,5 s (68 rondes gemeten met een spion op `gc.callbacks`, 12 boven 1 s). Valt
  zo'n ronde in een upload, dan wacht die de ronde plus ±60 ms. Gemeten met een plugin in het
  kladblok die in een upload een volle ronde afdwingt, op de bestanden 1–114 van de suite en dan
  deze test: zonder bevroren heap 768–840 ms per ronde (de upload 823–909 ms), met 150.000 oude
  lijsten in een kring als afval 933 ms (de upload 990 ms, de test rood met 1089 ms); met de
  fixture 3–6 ms (de uploads 30–54 ms), met hetzelfde afval 62 ms (101 ms). Beide tests met een
  grens van 1 s hebben nu de fixture `frozen_heap` (`gc.freeze`, zoals `serve.freeze_heap` in de
  app en `frozen_heap` in `test_media_loadtest.py` en `tests/beeld/test_beeld_gen.py`;
  `b74e1ae`). De grens en de beweringen blijven gelijk; alleen 9 groen. De rode run had geen
  meting van de gc of van `fsync`, dus een trage `fsync` (z1.md, F4) is voor die ene run niet
  uitgesloten: een meting van `fdatasync` op 4 KiB, elke 200 ms van 08:26 tot 10:26, zag een
  mediaan van 0,5–0,8 ms per minuut en drie keer meer dan 500 ms (753, 529 en 1191 ms, rond
  de installatietests met Docker, 09:36–09:48). Daarvoor blijft de grens van z1.md: 1 s houdt
  tot ±200 ms extra per `fsync`.

### Open na de samenvoeging

- **Alleen in CI, na de push:** `api` (met `tests/uit080`), `api-analyse` (263 en 11, met BZ5),
  `e2e` (met `test_uit080.py`), `analyse-image` en `beeld-model` (BZ4 streng, Open 1 en 2).
- **`e2e/test_uit080.py` hangt aan de vorm van `aria_snapshot`.** De opname is gemaakt met
  Playwright 1.65.2; de job `e2e` haalt Playwright met `uv run --with playwright`, zonder versie.
  Verandert een nieuwe Playwright de vorm van de boom, dan wordt de test rood op elke pagina. Dan
  opnieuw opnemen met de web van 0.8.0 (`3fca440`; de test zegt hoe), niet de vergelijking
  losser maken.
- **BZ5 met de echte modellen** en `memory.peak` van de cgroup: CI (`analyse-image`) of de LXC.
- **`test_install_search.sh --stappen` en `test_install_update.sh`** zijn op deze kop niet
  gedraaid: de schijf van de sandbox heeft 6 GB vrij voor Docker en de tests vragen er 8 en 12
  (de vier images die blijven, en de build-cache van andere sessies). X3b veranderde
  `install.sh` (`BEELD`); `test_install_analyse.sh` en `test_install_beeld.sh` gaan terug naar
  0.8.0 en weer vooruit, en waren hier groen (128 en 120 controles). De job `infra` draait
  `--stappen` na de push; `test_install_update.sh` staat niet in CI (x2a.md, Open 8).
- **Andere metingen van het geheugen met `ru_maxrss`** nemen ook de piek van pytest mee, en zijn
  dan te ruim of zeggen niets: `_MEET_RSS` in `tests/beeld/test_tokenizer.py` en
  `tests_analyse/test_beeld_tokenizer_echt.py` (`na - voor` binnen pytest),
  `test_beeld_omzetten.py` (`wait4` van een kind) en `test_beeld_echt_perf.py` (`RUSAGE_SELF`
  van pytest). Niet veranderd: ze waren groen en zijn van X3a en X3b; dezelfde aanpak als
  `c2be5fc` (`VmHWM` in het kind) past ook daar.
