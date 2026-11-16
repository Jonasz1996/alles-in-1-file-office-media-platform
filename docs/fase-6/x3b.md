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
[I2 (0.8.1): de install zet de analyse nu standaard aan; wie `ANALYSE=uit` vroeg, houdt hem uit.
Zoeken op beeld staat per gebruiker standaard uit (Keuze 85); Vraag 11 blijft open. Met de analyse
uit zegt 0.8.1 op `/zoeken` en `/beheer` dat het uitstaat, en staat op `/account` de link naar
`/over`; de rest is zoals hierboven. Zie "Met de analyse uit" en
[integratie-2.md](integratie-2.md).]

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

[I2 (0.8.1): met de analyse uit zeggen de stand beeld op `/zoeken` en het blok op `/beheer` nu dat
het op deze server uitstaat (`ANALYSE=uit` in `/opt/platform/.env`) en hoe de beheerder het
aanzet, in plaats van de zin van 0.8.0 (`bea29ca`). Op `/account` staat "Over dit platform" met de
link naar `/over` (`6890c2f`). `e2e/test_uit080.py` eist precies die verschillen en verder de
pagina's van 0.8.0 (`ca8af68`). Het vinkje in Account → Foto's en zijn uitleg bleven gelijk.]

## De go/no-go van X3

Keuze 73 en K2-12 noemen drie delen; §5 vraagt deel 1 en BZ1. Wat er nu van bewezen is:

| Deel | Stand |
|---|---|
| 1. De kleine vorm rekent als een eigen numpy-versie | gehaald (X3a, opnieuw gemeten in de nacontrole van golf 5) |
| 2. Twee keer omzetten geeft dezelfde sha256 (BZ2) | gehaald in `beeld-model` op `26c49ca` (x3a.md, CI op 26c49ca) |
| 3. De kwaliteitspoort (BZ4) | **niet gehaald, niet streng gedaan**: op `26c49ca` niet gerekend (HTTP 429 van Commons). [Nacontrole golf 6: op `669f03c` wel gerekend, op de set uit het voorstel (`voorstel-beeld.tsv`, 120 foto's) en met de omzetter van `VERSIE` 3: twee poorten rood, "slechtste vraag 0/5" (poort ≥ 1) en "meer zoals deze 0,475" (poort ≥ 0,7), in fp32 ook; nagelezen in het log van run 43850620942; x3a.md, "CI op 669f03c".] Streng wordt BZ4 pas in een run van `beeld-model` met de pins in `models.lock` én `api/tests/analyse/sets/beeld.tsv`. De pins van `VERSIE` 3 staan er sinds G6-2 (`c921c66`), ~~`beeld.tsv` niet: die zet de lead, uit het voorstel van een run~~. **Of BZ4 groen is, is het oordeel van CI na de push**, niet van deze tak. [Na golf 6, 2026-11-16: deel 3 is beslist in Keuze 85, door Jonas ("Toch uitbrengen"): zoeken op beeld komt in 0.8.1, per gebruiker standaard uit. Twee poorten werden ondergrenzen: 'bergen' en 'boot' mogen 0/5 geven (en moeten in `beeld_vragen.tsv` blijven), elke andere vraag ≥ 1/5; "meer zoals deze" ≥ 0,40, met 0,7 als doel alleen in de regel. Met die grenzen was BZ4 groen in `beeld-model` op `502f4c3` (run 43870814179), op de set uit het voorstel: nl precisie@5 0,692, slechtste vraag buiten 'bergen' en 'boot' 2/5, meer zoals deze 0,475. Die set staat sinds `d1c29e0` als `beeld.tsv` op deze tak. Streng, met de pins en `beeld.tsv`, blijft het het oordeel van CI na de push.] [I2: streng groen in `beeld-model` op `dc5dfe5` (run 43888472609), ook de stap "Uitslag", en weer op `e535332` (run 43892193604); zie "CI op dc5dfe5". Deel 3 is gehaald, met de ondergrenzen van Keuze 85.] |

Volgens x3a.md ("De go/no-go na CI") gaat X3b door met BZ4 streng als poort van X3b: een rode
BZ4 met de pins en de set houdt X3b tegen. Dat is hier niet anders gemaakt. Niets is hier beslist
of als beslist gemarkeerd. [Na golf 6, 2026-11-16: Jonas besliste deel 3 in Keuze 85
([bouwontwerp.md](bouwontwerp.md), Keuze 85; x3a.md, "Keuze 85"), niet deze tak. BZ4 blijft de
poort van X3b, nu met de ondergrenzen van Keuze 85: een rode BZ4 met de pins en `beeld.tsv` houdt
X3b nog steeds tegen. Keuze 85 beantwoordt Vraag 11 (standaard aan of uit) niet; die blijft open,
net als B49–B70, Vraag 4 en Vraag 8–14.] [I2: met de pins en `beeld.tsv` was BZ4 groen op
`dc5dfe5` en op `e535332`; BZ4 houdt X3b niet tegen.]

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
niet gemeten. [Nacontrole golf 6: dit zijn de getallen van `VERSIE` 1. De pins van nu zijn die van
`VERSIE` 3; in `beeld-model` op `669f03c` 32,4 ms per foto (1.852 per minuut) en een vraag p95
20,4 ms (log van run 43850620942), op een Xeon 6975P-C met VNNI; x3a.md, "Open na 669f03c", zegt
wat een tragere runner kan geven.] [I2: op `dc5dfe5` 34,7 ms per foto, op `e535332` 33,8 ms en
een vraag p95 22,2 ms, beide op een Xeon 6975P-C met VNNI; op `ffac665`, zonder VNNI, 82,8 ms
(x3a.md). BZ6 als geheel is M12 in [metingen.md](metingen.md). In de sandbox van I2 haalde het
pad met een tekstmodel van ware grootte (willekeurige gewichten) de eigen grens van 100 ms bij
100.000 foto's niet: p95 105,7 ms warm en 113,0 ms na opwarmen; het tekstmodel kost daar ±40 ms
per vraag op één core (de toelichting van `test_beeld_perf.py`, `e8c4a02`). De optelsom hierboven
was dus te laag. De poort van het ontwerp voor het geheel is 250 ms.]

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
15. **`MIN_TEKST` en `MIN_FOTO` blijven 0** (I2, `eed4c00`). Het ontwerp zegt dat de app onder een
    vaste grens niets toont, en dat BZ4 die grenzen vaststelt. BZ4 gaf geen grens die goede van
    slechte treffers scheidt: de kans van een goede treffer heeft p5 0,0, en de cosinus van "Meer
    zoals deze" overlapt tussen dezelfde en een andere soort (x3a.md, "CI op 3a9b07d" en "CI op
    669f03c"). Dus beide 0: elke treffer telt, en "onder de grens toont de app niets" gebeurt
    nooit. `tests/beeld/test_beeld_drempels.py` houdt beide op 0 en laat zien dat een grens boven
    0 wel treffers weghaalt. De opmerking "tot dan" in `app/beeld/search.py` is weg.

**Bestanden van anderen die X3b raakte** (alleen hotspots of tests die een exacte lijst
hebben):

- `app/analyse/worker/child.py`, `server.py` (X2a): de handlers en BUILT, hotspots sinds
  `913c318`;
- `app/analyse/worker/caps.py` (X2a): welke regels van `models.lock` meetellen (de bronnen van
  een omzetting niet, een model in een map pas als die map er is) en het blok `beeld` uit
  `meta.json` (`58263d6`; de review van golf 6, G6-7);
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

1. ~~**BZ4 streng**: `beeld.tsv` uit het voorstel van een run van `beeld-model`, door de lead; dan
   rekent CI de poort. Rood houdt X3b tegen (de go/no-go hierboven).~~ [Na golf 6, 2026-11-16:
   `beeld.tsv` staat er sinds `d1c29e0`: de 120 foto's uit het voorstel van de run op `502f4c3`
   (run 43870814179), waarop BZ4 met de ondergrenzen van Keuze 85 groen was. Open is alleen nog
   het oordeel van CI na de push; zie "Na de nacontrole (golf 6 afronden)".] [I2: streng groen
   op `dc5dfe5` (run 43888472609), ook de stap "Uitslag", en weer op `e535332`; zie "CI op
   dc5dfe5".]
2. ~~**De pins van de uitkomst** in `models.lock` (`beeld.int8.onnx`, `tekst.int8.onnx`,
   `meta.json`) komen uit de run op `26c49ca`. Op de branch staat sinds `eb3f763` een andere
   int8-omzetting (|q| ≤ 64), dus na de samenvoeging kloppen die drie niet meer; de strenge run
   zegt het, en de lead vervangt ze dan uit het voorstel. Geen sha256 is hier verzonnen.~~
   **Gedaan in de review van golf 6 (G6-2, `c921c66`):** de drie regels van VERSIE 3 (per
   token) uit de run op `669f03c`; zie De review. [I2: BZ2 gaf op `dc5dfe5` en `e535332` precies
   die pins.]
3. **BZ5** (het geheugen met een groepsronde van 60.000, de cache vol en een herlaadactie) heeft
   de groepsronde van X2b nodig en het echte model: niet gemeten. Na de samenvoeging met X2b, in
   CI of op de LXC. [I2: `test_geheugen_samen.py` draait nu ook met modellen van de echte vorm
   (`ANALYSE_GEHEUGEN_MODELLEN` en `ANALYSE_GEHEUGEN_LOCK`, `4c70dbc`): de echte YuNet en SFace en
   SigLIP 2 op ware grootte met willekeurige gewichten (`siglip_vol.py`, `965a36d`). De poort is
   dan de som van VmHWM ≤ 2,5 GB en elk kind onder zijn RLIMIT_DATA. Dat is M11 in
   [metingen.md](metingen.md): gehaald op `e41b724`, pid 1 37 MiB, `werk` 619 MiB (RLIMIT_DATA
   1.024), `zoeken` 1.178 MiB (RLIMIT_DATA 1.536), de som 1.835 van 2.384 MiB. De review zag dat
   `zoeken` met de echte modellen +779 MiB kost, niet de geschatte 440; de nepvorm rekent nu 780 MB
   bij, en de echte vorm houdt ook de grens bij herladen (C2, `50213fb`). `memory.peak` van de cgroup
   blijft voor de LXC.]
4. **BZ6 als geheel** met het echte model (het pad, `werk` vol, tijdens de backfill van de
   index) en **BZ9** (`beeld-bench` op de LXC van Jonas): niet gedaan. Het runbook zegt hoe.
   [I2: `test_beeld_perf.py` draait nu ook met modellen van de echte vorm (`BEELD_PERF_MODELLEN`
   en `BEELD_PERF_LOCK`, `e8c4a02`). Dat is M12 in [metingen.md](metingen.md): gehaald op
   `e41b724`, het geheel p95 90,2 ms warm bij 55.000, 97,5 ms warm bij 100.000 en 106,9 ms bij de
   eerste 20 vragen na het opwarmen, tegen 250 ms; zonder het tekstmodel 58,7, 65,9 en 73,3 ms,
   tegen 100 ms. ~~In de sandbox haalde de echte vorm de eigen grens van 100 ms bij 100.000 niet
   (zie Metingen). En de eerste beeldvraag na de start van de worker valt terug op gewoon zoeken:
   het laden van de tokenizer en het tekstmodel duurde 1,65 s, de klok is 800 ms.~~ Beide zijn na
   de review anders: de echte vorm legde het hele tekstmodel onder de 100 ms, en meet nu beide
   getallen van het ontwerp (C1, `8934ee3`); en het opwarmen laadt het tekstmodel mee, zodat de
   eerste vraag daarna niet terugvalt (U1, `4aa42e1`). Onder belasting, met `werk` vol, is het
   M13: p95 191,8 ms. BZ9 blijft voor de LXC.]
5. **BZ10** (`beeld-kwaliteit --personen` op de LXC): niet gedaan. Boven toeval geldt de regel
   van Keuze 82: dan geeft "Meer zoals deze" niets op een foto met een gevonden gezicht, en gelden
   voor beeld de regels van gezichten. Die regel is niet gebouwd; dat is een beslissing na de
   meting. [I2: niet gedaan; blijft voor de LXC, na bevestigde personen.]
6. **`test_beeld_perf.py`** eens rood in negen runs (de eerste vraag na een nieuw bestand bij
   100.000: p95 114,8 ms), in de eerste volle `tests_analyse`, nog vóór `bd10d6c` en `4d2644d`.
   Daarna groen, met p95 tot 90,2 ms: weinig ruimte onder de 100 ms. In CI in de gaten houden; de
   grens blijft 100 ms. [I2: groen in `api-analyse` op `e535332` (330 goed, 16 niet gekozen), en
   op `e41b724` in de volle `tests_analyse` (331 goed, 16 niet gekozen). De eerste vraag die niet
   meetelt, laadde het bestand onder de klok van 800 ms en kostte op één core 698–892 ms; sinds
   `e41b724` warmt de test het bestand eerst op, zoals na het bouwen. De grens bleef 100 ms.]
7. ~~**`infra/single/README.md`** zegt bij de analyse "vóór 0.8.1 doet de app er nog niets mee:
   alleen de status". Met X2b en X3b op de branch klopt dat niet meer voor wie de analyse nu al
   aanzet; die zin is van de analyse als geheel (X2), dus voor de samenvoeging of I2.~~ [I2: de
   zin is weg; het deel heet nu "Gezichten en zoeken op beeld: de container analyse" en zegt wat
   0.8.1 doet (`e4c9f42`).]
8. Niet gedaan en niet van X3b: B49–B70, Vraag 4 en Vraag 8–14 blijven open (Vraag 11 is "uit",
   Vraag 14 is 250 ms, zoals in het ontwerp).
9. [I2] **Zoeken op beeld naast de gezichtenscan.** In M13 was de poort van stap 2 191,8 ms, met
   de scan wachtend op de index (Keuze 65). In stap 3, een rapport, liep de scan van 5.000 foto's
   op dezelfde core als `zoeken`, en was het p95 290,4 ms (9 van 103 boven 250 ms). Een vraag
   voor Jonas, standaard (a) zo laten ([integratie-2.md](integratie-2.md#vragen-voor-jonas)).

## Klaar als

De regels van X3 in het ontwerp. "In CI" is na de push; "op de LXC" is na de installatie bij
Jonas.

[I2: alles wat hieronder "in CI" heet, was groen op `e535332` (run 43892193604, alle 19 jobs);
`beeld-model` met de pins en `beeld.tsv` al op `dc5dfe5`. ~~Nog open: BZ5 en BZ6 als geheel met de
echte vorm (M11 en M12 in [metingen.md](metingen.md)), en BZ9 en BZ10 op de LXC.~~ BZ5 en BZ6 als
geheel met de echte vorm zijn gehaald op `e41b724` (M11 en M12 in [metingen.md](metingen.md)), en
§4.5 stap 2 met de analyse ook (M13). Nog open: BZ9 en BZ10 op de LXC.]

| Regel | Bewijs | Uitkomst |
|---|---|---|
| De contracten van Keuze 80 ingevuld; geen route van X3 geeft nog `501`; een vraag zonder `mode` als vóór X3; geen test zwakker | `test_beeld_search.py` (9), `test_beeld_access.py` (4), `test_contracts.py` (26), `test_beeld_off.py::test_met_de_analyse_uit_niets_van_beeld` | met de analyse aan geen `501`; met de analyse uit bewust `501` (Afwijking 1). Groen in stap 1 (zie Gedraaid); in CI |
| 0012 heen en terug, de triggers gelijk aan `schema.py`, BZ8 groen | 0012 en de triggers: A0 (`test_migration_0012.py`, `test_triggers_beeld.py`); BZ8: `test_beeld_gen.py::test_bz8_…` | BZ8 groen hier (1,011 en 0,993); in CI |
| `test_tokenizer.py` en BZ3 groen | X3a (`test_tokenizer.py`, 95); `beeld-model` | van X3a, hier niet geraakt; BZ3 met de nieuwe lijst: in CI |
| De omzetter gelijk aan numpy, twee keer dezelfde sha256 | X3a (`test_beeld_omzetten.py`) | gehaald (x3a.md) |
| De go/no-go na X3a | x3a.md, "De go/no-go na CI" | deel 1 en 2 gehaald, ~~deel 3 (BZ4) niet; niet beslist~~ [Na golf 6, 2026-11-16: deel 3 beslist in Keuze 85, door Jonas ("Toch uitbrengen"); BZ4 met de ondergrenzen groen op `502f4c3` (run 43870814179), op de set uit het voorstel; streng in CI na de push] |
| De echte omzetting (BZ2) in `beeld-model`, de sha256's in `models.lock` | `beeld-model` op `26c49ca` en `669f03c`; `0a90be0`, `c921c66`; `test_lock_siglip.py` (9) | gedaan; de pins van de uitkomst zijn sinds G6-2 die van `VERSIE` 3 uit de run op `669f03c`, in de nacontrole regel voor regel tegen het log van die run gelezen; streng (dezelfde sha256 na de push) in CI. [Na golf 6: op `502f4c3` (run 43870814179) gaf BZ2 weer twee keer dezelfde sha256, en het voorstel noemde precies de vijf pins van de uitkomst die sinds `c921c66` in `models.lock` staan; de omzetter en wat hij gebruikt zijn tussen `502f4c3` en deze kop gelijk (`git diff`)] |
| De opdrachten in de container met nepmodellen; `rank` gelijk aan brute force | `tests_analyse/test_beeld_ops.py` (13), `test_beeld_cache.py` (10; sinds `c1b2939` ook het herladen) | groen in de volle `tests_analyse` van stap 1 en van de nacontrole; in CI (`api-analyse`, nu 329 en 16) [I2: 330 en 16 sinds `e535332`, groen; 331 en 16 sinds `4aa42e1` (de review, U1), groen op `e41b724`] |
| Zoeken: alleen eigen, levende foto's; elk filter; de terugval; geen vector en geen score; schone logs; `429`; een nieuwe vraag stopt de oude | `test_beeld_search.py`, `test_beeld_access.py`, `test_beeld_never_out.py`, `test_search_logs.py`, `test_beeld_events.py::test_beeld_fallback` | groen in stap 1 (zie Gedraaid); in CI |
| Uit en wissen: 0 rijen, geen bestand, `forget`; `blob_embed` niet in de reservekopie; de beheerder kan uitzetten | `test_beeld_off.py`, `test_beeld_events.py` (`off`, `on_again`, `wipe`, `admin_off`), `test_backup_gezichten.py` (A0); `e2e/test_zoek_beeld.py` (schakelaar, beheerder) | API: groen in stap 1 en in de volle suite op `4924b30` (Gedraaid); e2e: groen op `4e286b9`. **Tot `9228eb6` niet helemaal**: de lijsten van 60 s in de app bleven staan, en na uit en weer aan of na wissen gaf een vraag nog foto's uit de gewiste vectoren (De nacontrole) |
| De kwaliteitspoort (BZ4) groen | `test_beeld_kwaliteit.py` in `beeld-model` | **open**: in CI na de push, met `beeld.tsv` (Open 1). [Na golf 6, 2026-11-16: met de ondergrenzen van Keuze 85 groen op `502f4c3` (run 43870814179), op de set uit het voorstel; die set is sinds `d1c29e0` `beeld.tsv`. Streng, met de pins en `beeld.tsv`, blijft het oordeel van CI na de push] [I2: streng groen op `dc5dfe5` en `e535332`] |
| De tijden: p95 ≤ 100 ms zonder tekstmodel; ≤ 250 ms warm met het echte model, ook bij 100.000 en de eerste vraag; ≥ 500 foto's per minuut | `test_beeld_perf.py`; `test_beeld_echt_perf.py` in `beeld-model` | ≤ 100 ms: groen, eens rood in 9 (Metingen, Open 6); ≥ 500 per minuut: 1.508 op `26c49ca` (`VERSIE` 1), 1.852 op `669f03c` (`VERSIE` 3, de pins van nu); een vraag met het echte model p95 20,4 ms op `669f03c`; ≤ 250 ms voor het geheel met het echte model: in CI. [Na golf 6: op `502f4c3` 1.671 per minuut (35,9 ms per foto) en een vraag p95 23,4 ms, op een Xeon 6975P-C met VNNI en AMX] [I2: op `e535332` 33,8 ms per foto en een vraag p95 22,2 ms; het geheel met de echte vorm is M12 (Open 4): gehaald, p95 90,2 / 97,5 / 106,9 ms tegen 250, en zonder tekstmodel 58,7 / 65,9 / 73,3 ms tegen 100] |
| Het geheugen (BZ5) | `tests_analyse/test_geheugen_samen.py` (De samenvoeging) | met de nepmodellen gehaald (De samenvoeging; opnieuw in de nacontrole); met de echte modellen en `memory.peak` van de cgroup **open**: in CI of op de LXC (Open 3) [I2: met de echte vorm is het M11 (Open 3): gehaald, de som 1.835 van 2.384 MiB; onder belasting 1,51 GB (M13); `memory.peak` op de LXC] |
| BZ10 gemeten | `beeld-kwaliteit --personen` | **op de LXC**, niet gedaan (Open 5) |
| `test_install_beeld.sh` groen in `infra`; `BEELD=uit` werkt; een mislukking is niet fataal | `test_install_beeld.sh` | 120 controles groen hier (stap 1). **Van `c921c66` tot `a453869` rood** (1 van 120: de ruimte rekende nog met de uitkomst van `VERSIE` 1); sinds `a453869` weer 120 groen (De nacontrole); in CI |
| De web: vitest en e2e met alleen het toetsenbord groen; de eerste lading +0 B; het raster ≤ 15 kB; `toetsenbord-x3.md` | `beeld.test.ts` (45), `e2e/test_zoek_beeld.py` (8), `npm run budget` | groen op `4e286b9` (Gedraaid); +0 B ruw, 10.883 B en 2.558 B (Metingen); de vinkjes in `toetsenbord-x3.md` |
| Het blok op `/beheer`, het runbook, de README en het document | `BeeldBlock.tsx`, `searchTab.test.ts`, `beeld.test.ts`; dit document | gedaan |
| `e2e/slim.sh` groen, alle bestaande jobs groen (ook `zoek-perf` met 200 ms) | `slim.sh`, de e2e-set van CI hier; de rest in CI | `slim.sh` en de e2e-set groen op `4e286b9` (Gedraaid); `zoek-perf` en de andere jobs in CI |

### Bewijs BZ1–BZ10

| # | Waar | Uitkomst |
|---|---|---|
| BZ1 | `beeld-model` op `26c49ca` | huggingface.co, commons.wikimedia.org en upload.wikimedia.org bereikbaar; images-assets.nasa.gov 403 (x3a.md) |
| BZ2 | idem, en op `669f03c` | twee omzettingen gelijk, maxrss 354 MiB (grens 1.536); op `669f03c` (`VERSIE` 3) weer gelijk, maxrss 352 MiB; die pins in `models.lock` sinds G6-2 (`c921c66`). [Na golf 6: op `502f4c3` (run 43870814179) weer gelijk (8,9 en 8,5 s, maxrss 354 MiB; met `--fp32` 16,5 s, 1.608 MiB), en precies die vijf pins] [I2: op `dc5dfe5` en `e535332` weer precies die pins] |
| BZ3 | idem | 0 verschillen op 2.000 zinnen; de lijst had 8 rijen met U+E000/U+E001 (hersteld in `e92b1c1`); in CI |
| BZ4 | `beeld-model` | **open**: op `669f03c` met de set uit het voorstel twee poorten rood (slechtste vraag 0/5, meer zoals deze 0,475; fp32 ook); streng in CI, met `beeld.tsv`. [Na golf 6, 2026-11-16: sinds Keuze 85 zijn die twee poorten ondergrenzen. Op `502f4c3` (run 43870814179) was BZ4 daarmee groen, op de set uit het voorstel: nl precisie@5 0,692 (fp32 0,692), slechtste vraag buiten de bekend zwakke 2/5 ('bergen' 0/5, 'boot' 0/5), combinaties 6/6 (plekken 4, 1, 2, 3, 1, 6), int8 zakt 0,000, meer zoals deze 0,475 (fp32 0,463), kleuren 3/3; pytest `bz4` 6 en `bz4r` 5 goed. `beeld.tsv` is die set (`d1c29e0`); streng in CI na de push] [I2: streng groen op `dc5dfe5` (run 43888472609), op de 120 foto's van `beeld.tsv`, met dezelfde getallen; ook de stap "Uitslag"] |
| BZ5 | `tests_analyse/test_geheugen_samen.py` | met de nepmodellen gehaald (De samenvoeging); met de echte modellen en de cgroup **open**: in CI of op de LXC [I2: met de echte vorm M11 (Open 3): gehaald, 1.835 van 2.384 MiB; de cgroup op de LXC] |
| BZ6 | `beeld-model` (het model), `test_beeld_perf.py` (het pad) | het model: op `26c49ca` (`VERSIE` 1) 1.508 foto's per minuut, een vraag p95 35,5 ms; op `669f03c` (`VERSIE` 3) 1.852 per minuut, p95 20,4 ms; het pad zonder tekstmodel ≤ 100 ms hier; het geheel: in CI of op de LXC. [Na golf 6: op `502f4c3` 1.671 per minuut (35,9 ms per foto), een vraag p95 23,4 ms, op een Xeon 6975P-C met VNNI en AMX] [I2: op `dc5dfe5` 34,7 ms per foto; op `e535332` 33,8 ms en een vraag p95 22,2 ms; het geheel is M12 (Open 4): gehaald, p95 ≤ 106,9 ms tegen 250] |
| BZ7 | `beeld-model` op `26c49ca` | 0,135 s, ~~0,0 MB erbij~~ [I2: die 0,0 MB was een fout in de meting, niet het echte geheugen: `ru_maxrss` erft bij exec de piek van de ouder, dus "erbij" kwam op 0 uit. Sinds `d15499b` meet BZ7 de VmHWM van het kind zelf; `test_tokenizer.py` is rood met de oude meting. In de sandbox met een `tokenizer.bin` op ware grootte: 29.028.352 B erbij (VmHWM 45,7 MB), mediaan 0,129 s. In CI op de commit van 0.8.1: ⟦I2: de regel BZ7 uit beeld-model op de commit van 0.8.1⟧] |
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

- **Alleen in CI, na de push:** `api` (met `tests/uit080`), `api-analyse` (263 en 11, met BZ5; sinds G6-3 329 en 16),
  `e2e` (met `test_uit080.py`), `analyse-image` en `beeld-model` (BZ4 streng, Open 1 en 2).
  [Na golf 6, 2026-11-16: Open 2 is gedaan in `c921c66` (G6-2), Open 1 op de tak in `d1c29e0`
  (`beeld.tsv`); BZ4 heeft sinds Keuze 85 twee ondergrenzen. Streng blijft het voor CI na de
  push.]
- **`e2e/test_uit080.py` hangt aan de vorm van `aria_snapshot`.** De opname is gemaakt met
  Playwright 1.65.2; de job `e2e` haalt Playwright met `uv run --with playwright`, zonder versie.
  Verandert een nieuwe Playwright de vorm van de boom, dan wordt de test rood op elke pagina. Dan
  opnieuw opnemen met de web van 0.8.0 (`3fca440`; de test zegt hoe), niet de vergelijking
  losser maken. **Sinds de review van golf 6 (G6-6)** haalt elke stap van de job `e2e`
  `playwright==1.65.2`; een nieuwere Playwright komt er pas met een nieuwe opname.
- **BZ5 met de echte modellen** en `memory.peak` van de cgroup: CI (`analyse-image`) of de LXC.
- **`test_install_search.sh --stappen` en `test_install_update.sh`** zijn op deze kop niet
  gedraaid: de schijf van de sandbox heeft 6 GB vrij voor Docker en de tests vragen er 8 en 12
  (de vier images die blijven, en de build-cache van andere sessies). X3b veranderde
  `install.sh` (`BEELD`); `test_install_analyse.sh` en `test_install_beeld.sh` gaan terug naar
  0.8.0 en weer vooruit, en waren hier groen (128 en 120 controles). De job `infra` draait
  `--stappen` na de push; `test_install_update.sh` staat niet in CI (x2a.md, Open 8).
- ~~**Andere metingen van het geheugen met `ru_maxrss`** nemen ook de piek van pytest mee, en zijn
  dan te ruim of zeggen niets: `_MEET_RSS` in `tests/beeld/test_tokenizer.py` en
  `tests_analyse/test_beeld_tokenizer_echt.py` (`na - voor` binnen pytest),
  `test_beeld_omzetten.py` (`wait4` van een kind) en `test_beeld_echt_perf.py` (`RUSAGE_SELF`
  van pytest). Niet veranderd: ze waren groen en zijn van X3a en X3b; dezelfde aanpak als
  `c2be5fc` (`VmHWM` in het kind) past ook daar.~~ [I2: `test_beeld_omzetten.py` sinds
  `e535332` (`bouw.maxrss_mib()` meldt VmHWM); `_MEET_RSS` en BZ7 meten in het kind met
  `tests/beeld/piek.py`, en het rapport van BZ6 zegt wat het meet (`d15499b`). Elk met een test
  die met de oude meting rood is; de grenzen bleven.]

## De review (golf 6)

De review van de samengevoegde kop gaf twee punten die moesten (G6-1, G6-2), drie die zouden
moeten (G6-3, G6-4, G6-5) en twee kleine (G6-6, G6-7). G6-1 is van X2b (x2b.md, De review); de
rest staat hier. Elk punt eerst zelf nagegaan; een test die zonder de herstelling rood is, de
herstelling, en de test groen.

| Punt | Wat er gedaan is | Test |
|---|---|---|
| **G6-2** (moet) De pins van `beeld.int8.onnx`, `tekst.int8.onnx` en `meta.json` in `models.lock` waren die van VERSIE 1 van `bouw.py` (de run op `26c49ca`); na de samenvoeging staat de omzetter op VERSIE 3 (per token), dus `beeld-model` rood en `install.sh` met `BEELD=aan` gooit elke uitkomst weg | Klopt (`meta.json` noemt `"omzetter"`, de int8-bytes veranderen mee). De drie regels komen nu uit het voorstel van BZ2 in de CI-run op `669f03c` (run 43850620942, job `beeld-model`, drie omzettingen met dezelfde regels; x3a.md, "CI op 669f03c"); de omzetter en wat hij gebruikt zijn tussen `669f03c` en deze kop gelijk. Geen sha256 of grootte verzonnen. Een proef in het kladblok liet de worker (`parse_meta`, `preprocess`, beide grafen, `unit_rows`) de uitkomst van VERSIE 3 van het kleine model lezen en draaien (`c921c66`). `beeld.tsv` blijft van de lead (Open 1). [Na golf 6: de lead zette hem in `d1c29e0`, uit het voorstel van de run op `502f4c3`] | `test_lock_siglip.py`: de nieuwe exacte PINS, en `test_de_uitkomst_in_models_lock_is_van_de_versie_van_de_omzetter` legt `PINS_OMZETTER` (3) naast `VERSIE` van `bouw.py`: met de versie van de oude regels (1) rood. Streng in CI: `beeld-model` na de push |
| **G6-3** (zou) `origin` samenvoegen; `ANALYSE_TESTS` en `ANALYSE_DESELECTED` opnieuw tellen; drie conflicten | Een gewone merge van `origin/claude/stappenplan-uitwerken-lu4uv3` (`ffac665`), niets herschreven (`e004d24`). `ci.yml`: de telling opnieuw op de samengevoegde boom, niet van een van beide kanten genomen; het commentaar noemt de bijdragen van beide. `test_beeld_echt_hulp.py`: de imports van beide. `docs/runbooks/analyse.md`: de alinea over VERSIE 3, daarna de delen van X3b | `pytest --collect-only tests_analyse -m "not beeld_echt"` in de venv van de analyse: 329 van 345, 16 niet gekozen = `ANALYSE_TESTS` 329 en `ANALYSE_DESELECTED` 16; de hele `tests_analyse` daarna: 329 groen, 16 niet gekozen (206 s). De job `api-analyse` eist die telling exact (CI na de push) |
| **G6-4** (zou) Geen test voor Keuze 69 bij het zoeken (`origin_ok("n")` in `_select`): na beeld uit van de uploader kwam diens foto nog uit het bestand en de cache van de eigenaar | Klopt: zonder `text(beeld_rows.origin_ok("n"))` was `tests/beeld` groen. `test_beeld_other_uploader` zet Bram na zijn foto weer op uit, zonder jobs: bij Eva is zijn foto meteen weg, terwijl haar bestand en de cache van 60 s hem nog hebben (geen nieuwe rank, het bestand byte voor byte gelijk); en ook nadat de jobs het bestand opnieuw maakten (`2566acb`) | Met die mutatie rood op de nieuwe bewering (`test_beeld_events.py:128`), zonder groen; `test_beeld_events.py` 16 groen |
| **G6-5** (zou) Het filter op het begin van de sha (§3.9) zou geen test hebben die zonder het filter rood is; `test_beeld_replace` zou het bestand opnieuw maken vóór de zoekvraag | Niet zo: tussen de nieuwe inhoud (PUT) en de zoekvraag draait in `test_beeld_replace` geen job, dus komt die vraag uit de warme cache met het oude bestand. Met `rows = list(rows)` in plaats van het filter is die test rood (`["<node>", "<oranje>"]` in plaats van `["<oranje>"]`), en hij is de enige van `tests/beeld` (180 groen, 1 rood). Om het zichtbaar te maken zegt de test het nu ook: geen nieuwe rank en het bestand gelijk (`2566acb`) | Met de mutatie rood op `test_beeld_events.py:230`; zonder groen |
| **G6-6** (klein) De job `e2e` haalde Playwright zonder versie, terwijl `test_uit080.py` vergelijkt met een opname van 1.65.2 | Elke stap van de job (Chromium installeren, de e2e, de marker `office`) haalt `playwright==1.65.2`, zodat de browser bij de Playwright van de tests past (`7abdbfa`). "Open na de samenvoeging" zegt het | `tests/uit080/test_uit080_ci.py` leest de versie uit de docstring van `test_uit080.py` en eist die bij elke `--with` van de job: zonder de wijziging rood. Lokaal `test_uit080.py` met `--with playwright==1.65.2` (zie onder); de job zelf in CI |
| **G6-7** (klein) `app/analyse/worker/caps.py` (X2a) ontbrak bij de bestanden van anderen | Toegevoegd in die lijst (`58263d6`: welke regels van `models.lock` meetellen, en het blok `beeld`) | Alleen tekst |

Ook x3a.md, "Open na 669f03c": de pins van VERSIE 3 staan nu in `models.lock` (G6-2).

**Gedraaid voor de review**, achter het slot van de zware runs en op drie cores: de tests die de
herstellingen raken (`test_beeld_events.py` 16 groen; `test_compose_analyse.py`,
`test_lock_siglip.py` en `test_models_lock.py` 44 groen; de tests die `ci.yml` lezen, met
`test_uit080_ci.py`, 20 groen), `e2e/test_uit080.py` met `uv run --with playwright==1.65.2` (1
groen in 164 s; lokaal met de Chromium van de sandbox, in CI die van 1.65.2), ruff, yamllint,
`bash -n` en shellcheck schoon. De mutaties: zonder `origin_ok` in `_select` (heel `tests/beeld`: alleen
`test_beeld_other_uploader` rood, 180 groen) en met `rows = list(rows)` in plaats van het filter
van §3.9 (alleen `test_beeld_replace` rood, 180 groen), telkens hersteld en `search.py` weer gelijk
aan de kop. De rest draait de nacontrole.

## De nacontrole (golf 6)

Op de kop van de review (`b14009e`), op 16 november 2026, in een losse worktree (`git worktree add
--detach`). Wat X2b raakt (G6-1 en de getallen van gezichten), staat in "De nacontrole (golf 6)"
in [x2b.md](x2b.md); hier de rest, en alles wat er gedraaid is. Niets gepusht, geen tag; de versie
blijft 0.8.0.

### De reparaties weggehaald

Per commit één regel van de herstelling tegelijk terug naar wat er vóór stond (een script in het
kladblok: de regel vervangen, de test van dat punt draaien, `git checkout` van het bestand, de
test weer draaien en de worktree schoon).

| Commit (punt) | Wat weggehaald | Rood | Oordeel |
|---|---|---|---|
| `c921c66` (G6-2) | de regel `beeld.int8.onnx` in `models.lock` weer die van `VERSIE` 1 (`e2a9f0f3…`, 102.672.680) | 1: `test_lock_siglip.py::test_de_pins_in_models_lock` (8 groen) | gezien |
| | `tekst.int8.onnx` weer die van `VERSIE` 1 | 1, dezelfde test | gezien |
| | `meta.json` weer die van `VERSIE` 1 | 1, dezelfde test | gezien |
| | `VERSIE = 4` in `bouw.py`: de omzetter verandert, de pins niet | 1: `test_de_uitkomst_in_models_lock_is_van_de_versie_van_de_omzetter` (`['4'] == ['3']`) | gezien |
| | niets weggehaald: `test_install_beeld.sh` naast de nieuwe pins | al rood op de kop: 1 van 120 | **niet af**: hersteld in `a453869` (hieronder) |
| `e004d24` (G6-3) | de telling: de `grep` van de stap in `api-analyse`, letterlijk uit `ci.yml`, op de laatste regel van de volle `tests_analyse` van de nacontrole | geen regel om terug te zetten; op `329 passed, 16 deselected, 2 warnings in 206.85s`: met 329 en 16 goed; met 263 en 11 (vóór G6-3), 282 en 16 (`ffac665`), 330 en 16, en 329 en 15 fout | gezien |
| `2566acb` (G6-4) | `_select` zonder `text(beeld_rows.origin_ok("n"))` | 1: `test_beeld_other_uploader` | gezien |
| `2566acb` (G6-5) | `rows = list(rows)` in plaats van het filter van §3.9 | 1: `test_beeld_replace` | gezien |
| `7abdbfa` (G6-6) | Chromium installeren zonder `==1.65.2` | 1: `tests/uit080/test_uit080_ci.py` (de eerste `--with`) | gezien |
| | de e2e zonder versie | 1 (de tweede) | gezien |
| | de marker `office` zonder versie | 1 (de derde) | gezien |
| `a05bbd5`, `b14009e` (G6-7, De review) | alleen tekst | — | nagelezen: `caps.py` kreeg in `58263d6` de regels van `models.lock` en het blok `beeld`; de regelnummers 128 en 230 van de review wijzen naar de beweringen van G6-4 en G6-5 |

**De pins van G6-2, nagelezen.** In het log van run 43850620942 (job `beeld-model` op `669f03c`)
noemt het voorstel van BZ2 voor `beeld.int8.onnx`, `tekst.int8.onnx`, `tokenizer.bin` en
`meta.json` precies de sha256 en grootte die nu in `models.lock` staan; twee omzettingen gaven
daar dezelfde sha256 voor alle vijf bestanden (8,5 en 8,0 s, maxrss 352 MiB). Tussen `669f03c` en
de kop zijn `app/analyse/worker/omzetten/`, `app/analyse/onnxw.py`, de Dockerfiles, `uv.lock` en
`pyproject.toml` niet veranderd (`git diff`), dus de pins horen bij de omzetter van de kop.

### Wat de nacontrole vond: uit en wissen vergaten de lijsten van 60 s (`9228eb6`)

Bij het nalopen van de rij "Uit en wissen" in Klaar als: `app/beeld/cache.py` heeft
`forget(user_id=None)` ("alles van deze gebruiker weg (uit, wissen)"), maar niets riep het aan. De
sleutel van een gerangschikte lijst is (gebruiker, vraag of foto, `gen`), en `gen` van
`beeld_owner` gaat alleen omhoog bij de triggers van `node` (`TRIGGERS_0012`), niet bij het wissen
van vectoren. Na uit en weer aan, of na "Beeldindex wissen", had het volgende bestand dus
dezelfde `gen` als het oude, en een vraag binnen 60 s kwam uit de oude lijst: foto's gevonden uit
vectoren die al gewist waren. Een proef met de nep-worker: na het wissen gaf "rood" nog de rode
foto, terwijl het bestand 0 rijen had en de voortgang 0,0 was. Uit wist dus niet alles wat alleen
voor jou bewaard is (K1-08, Keuze 82), en wissen wiste niet alles.

Nu roept `off_job` na `forget` naar de container ook `bcache.forget(user_id)` aan, en `wipe_job`
`bcache.forget()`. De jobs draaien in het proces van de app (`jobs.start_runners` in de
lifespan), dus dat is dezelfde cache als die van `GET /search?mode=image`. "Zet uit voor iedereen"
wist niets (de vectoren blijven tot wissen, `test_beeld_admin_off`) en zoeken geeft dan `409
beeld_off`; daar hoeft de cache niet weg.

`test_beeld_on_again` en `test_beeld_wipe` zoeken nu eerst, zodat de lijst in de cache staat, en
eisen na uit en aan, of na wissen, met dezelfde `gen` en 0 rijen in het bestand: geen treffer;
daarna de vectoren opnieuw en weer gevonden. Zonder de regel in `off_job` is `test_beeld_on_again`
rood (twee foto's in plaats van geen), zonder die in `wipe_job` `test_beeld_wipe` (één); met beide
16 groen. Geen contract, geen migratie, geen test losser.

### Wat de nacontrole nog vond: G6-2 liet `test_install_beeld.sh` op `VERSIE` 1 (`a453869`)

In de keten hieronder was `test_install_beeld.sh` op `9228eb6` rood: 1 van 120 controles, "de
echte lock: de ruimte is de bron plus twee keer de uitkomst (±2,3 GB)". Die controle eist voor
`BEELD_NODIG` (de vrije ruimte die `install.sh` met `BEELD=aan` vraagt) exact de bronnen plus twee
keer de uitkomst, en had de som van de uitkomst van `VERSIE` 1 (399.407.013 B). G6-2 zette
`beeld.int8.onnx`, `tekst.int8.onnx` en `meta.json` op die van `VERSIE` 3 (de vijf bestanden van
de uitkomst samen 399.284.072 B) en draaide `test_lock_siglip.py`, niet deze test. Na de push was
de job `infra` dus rood geworden. `install.sh` zelf rekende goed: het leest de groottes uit
`models.lock`. Alleen het getal in de test was oud.

Nu dezelfde controle, nog steeds exact, met de vijf groottes uit `models.lock` uitgeschreven
(`1541884183 + 2 * (102609072 + 284248004 + 12414446 + 2376 + 10174)`). Met de drie regels van
`VERSIE` 1 terug in `models.lock` (in de kladworktree) is die controle rood (1 van 120); met de
regels van nu 120 van 120 groen.

### Klaar als, nagelopen

Elke test die Klaar als, "Met de analyse uit", "Bewijs BZ1–BZ10" en "De gebeurtenissen" noemen,
bestaat, met de genoemde naam. Wat niet meer klopte, is in de tekst zelf verbeterd:

- de go/no-go (deel 3) en de rij BZ4 zeiden alleen "op `26c49ca` niet gerekend": op `669f03c` is
  BZ4 wel gerekend, op de set uit het voorstel, met twee poorten rood (slechtste vraag 0/5, meer
  zoals deze 0,475, in fp32 ook). Streng is het nog niet: ~~`beeld.tsv` staat er niet~~ [na golf 6:
  `beeld.tsv` staat er sinds `d1c29e0`, en de twee poorten zijn sinds Keuze 85 ondergrenzen;
  streng is het oordeel van CI na de push];
- de rij BZ2 en "De echte omzetting" zeiden dat de pins na de samenvoeging opnieuw moesten: dat
  is G6-2, nagelezen hierboven;
- BZ5 stond op "open": met de nepmodellen gemeten bij de samenvoeging (en opnieuw hieronder); met
  de echte modellen en de cgroup blijft het open;
- de tijden van het echte model waren die van `VERSIE` 1 (`26c49ca`); die van de pins van nu
  (`669f03c`) staan erbij;
- `test_beeld_cache.py` telt 10 tests (9 bij X3b), `api-analyse` 329 en 16;
- de rij "Uit en wissen" zegt dat de lijsten van 60 s tot `9228eb6` bleven staan;
- de rij `test_install_beeld.sh` zei "120 controles groen": van `c921c66` tot `a453869` was er
  één rood (hierboven).

De getallen in Metingen zijn van de runs die daar staan; de optelsom onder Metingen staat er als
optelsom, niet als meting.

### Gedraaid

Eenmaal, op `9228eb6` in de worktree van de nacontrole, in twee ketens naast elkaar; elke zware
stap onder het slot van het kladblok, één tegelijk, met `taskset -c 0-2`. `a453869` veranderde
daarna alleen `test_install_beeld.sh`; wat dat raakt, is op `a453869` opnieuw gedraaid (de laatste
twee rijen).

| Wat | Hoe | Uitkomst |
|---|---|---|
| ruff | `ruff check .` en `ruff format --check .` in `api/` | schoon; 793 bestanden al opgemaakt |
| de volle api-suite | `pytest -q -p no:randomly` | 6.844 groen, 2 overgeslagen, 241 niet gekozen, in 3.081 s |
| `tests_analyse` | `.venv-analyse`, `-m "not beeld_echt"`, `-rs`, `ANALYSE_MODELLEN` met de echte YuNet en SFace | 329 groen, 16 niet gekozen, niets overgeslagen, in 206,9 s; de `grep` van `api-analyse` op die regel: goed |
| als nobody | `/tmp/ci-nobody/run.sh` met `tests/analyse`, `tests/beeld`, `tests/people/faces`, `tests/uit080`, de tests van zoeken die de analyse raken, `test_compose_analyse.py`, `test_jobs_lanes.py`, `test_cli_db.py`, `test_db_pragmas.py` en `test_conftest_volgorde.py` | 912 groen, 1 niet gekozen, in 492 s |
| de web | `npm run typecheck`, `npm test`, `npm run build`, `npm run check:i18n -- --strict`, `npm run budget` | 123 bestanden met 1.343 tests groen; 0 meldingen in 293 bestanden; binnen het budget: eerste lading JS 98.030 B (100.000), CSS 3.403 B, faces 11.837 B (12.000), beeld 10.904 B (15.000), likeRow 2.556 B (3.000) |
| `e2e/slim.sh` | `SLIM_CPUS=0-2` | groen in 1.204 s: zoek en triggers in python:3.12-slim met SQLite 3.46.1, 1.309 groen (909 s); de web was bij; e2e met de marker `zoek`, 111 groen (295 s) |
| de e2e-set van CI | `uv run --with playwright==1.65.2 python -m pytest ../e2e` met de marker-uitdrukking van de job | 249 groen, 136 niet gekozen, in 1.140 s |
| e2e gezichten en beeld | `-m "" -rs` op `test_zoek_gezichten.py`, `test_zoek_gezichten_echt.py` en `test_zoek_beeld.py` | 10 groen, 1 overgeslagen: `test_zoek_gezichten_echt.py` (geen image; `E2E_ANALYSE_IMAGE` zet de job `analyse-image`), in 93,5 s |
| de installatietests | `test_install_env.sh`, `_media`, `_search`, `_analyse`, `_beeld` | env (137 controles), media (127), search (209) en analyse (128) groen; beeld 1 rood van 120 (hierboven) |
| groeperen, 60.000 open en 6.000 bevestigd (poort ≤ 60 s, ≤ 400 MB) | `tests_analyse/test_group_perf.py`, `-s` | 26,2 s, maxrss 256 MB, zuiverheid 1,0000, recall 0,986 |
| zoeken op beeld zonder tekstmodel (poort p95 ≤ 100 ms) | `tests_analyse/test_beeld_perf.py`, `-s` | warm bij 55.000: p95 60,2 ms; warm bij 100.000: p95 63,7 ms; de eerste vraag na het opwarmen bij 100.000: p95 60,5 ms |
| BZ5 met de nepmodellen (poort 2,5 GB) | `tests_analyse/test_geheugen_samen.py`, `-s` | pid 1 36 MB, `werk` 277 MB, `zoeken` 506 MB; som van de maxrss 819 MB, hoogste som tegelijk 792 MB; met wat de echte modellen meer kosten 1.648 MB: gehaald, als bij De samenvoeging |
| de routes van gezichten, en de tijd per foto met de echte modellen | `test_faces_perf.py`, `test_models_real.py` | in x2b.md (De nacontrole, Gedraaid) |
| `test_install_beeld.sh` op `a453869` | als root, onder het slot | 120 van 120 groen |
| yamllint, shellcheck, `bash -n`, compose, de links op `a453869` | `yamllint -s .`; shellcheck met de lijst van de job `infra`; `docker compose config -q` zoals de job `infra` (`.env.example`, met eenmalige geheimen, met office en de grenzen, met de analyse met en zonder `--profile omzetten`); `links.py` uit het kladblok | schoon; de links ook na deze tekst |

### Open na de nacontrole

- **Alleen in CI, na de push:** `api` (met `tests/uit080`), `api-analyse` (exact 329 en 16),
  `zoek-perf`, `e2e` (met `test_uit080.py` en Playwright 1.65.2), `infra` (met
  `test_install_beeld.sh` en `--stappen`), `analyse-image` (G6-1: het script in de container;
  `test_zoek_gezichten_echt.py`) en `beeld-model` (BZ2: dezelfde sha256 als de pins; BZ4 met
  `beeld.tsv`, Open 1).
- ~~**BZ4** is niet beslist: op `669f03c` twee poorten rood (Bewijs BZ1–BZ10).~~ [Na golf 6,
  2026-11-16: Jonas besliste in Keuze 85 ("Toch uitbrengen"); de twee poorten zijn ondergrenzen,
  en op `502f4c3` (run 43870814179) was BZ4 daarmee groen. Zie "Na de nacontrole (golf 6
  afronden)".]
- **BZ5 met de echte modellen** en `memory.peak` van de cgroup: CI (`analyse-image`) of de LXC.
- **`test_install_search.sh --stappen` en `test_install_update.sh`** zijn ook nu niet gedraaid:
  de schijf van de sandbox had 3,7 GB vrij, de tests vragen er 8 en 12. De job `infra` draait
  `--stappen` na de push; `test_install_update.sh` staat niet in CI (x2a.md, Open 8).
- ~~**`origin` is verder dan deze kop:** `b65eeb1` (e2e m6), `d0d25d8` (x3a.md) en `502f4c3` (BZ4,
  van de lead) staan op `origin/claude/stappenplan-uitwerken-lu4uv3` en niet hier. Ze raken
  `docs/fase-5/m6.md`, `e2e/test_media_m6.py`, `docs/fase-6/x3a.md`, `docs/fase-6/bouwontwerp.md`
  en `tests_analyse/test_beeld_kwaliteit.py`; in dat laatste blijven dezelfde tests met de marker
  `beeld_echt`, dus de telling van `api-analyse` verandert er niet door. Samenvoegen vóór de push
  is aan de lead.~~ [Na golf 6: samengevoegd in `cb6ab8c`; de telling bleef 329 en 16. Zie
  hieronder.]

## Na de nacontrole (golf 6 afronden)

Op 16 november 2026, na de nacontrole (`9a64365`), door de lead. Drie commits; niets herschreven,
niets gepusht, de versie blijft 0.8.0.

| Commit | Wat |
|---|---|
| `cb6ab8c` | Een gewone merge van `origin/claude/stappenplan-uitwerken-lu4uv3` (`502f4c3`, met `d0d25d8` en `b65eeb1`). Brengt Keuze 85 in [bouwontwerp.md](bouwontwerp.md), de ondergrenzen in `tests_analyse/test_beeld_kwaliteit.py` (`BEKEND_ZWAK` en `MEER_ONDER`), in x3a.md "CI op ffac665" en "Keuze 85", en de reparatie van e2e m6 (`e2e/test_media_m6.py`, `docs/fase-5/m6.md`). Alle tests van `test_beeld_kwaliteit.py` hebben de marker `beeld_echt`; de telling van `api-analyse` blijft 329 en 16 (`--collect-only` op deze kop: 329 van 345, 16 niet gekozen) |
| `d1c29e0` | `api/tests/analyse/sets/beeld.tsv`: het voorstel uit het log van `beeld-model` op `502f4c3` (run 43870814179), 120 foto's, CC0 of publiek domein, thumbnails van 1280 px met url, bytes en sha256. Nagekeken met `lees_set`, tegen `beeld_niet.tsv`, en heen en terug door `set_tsv` byte voor byte gelijk. `LATER` in `test_beeld_ci.py` is nu leeg: elk pad van de poort van `beeld-model` moet bestaan. In `test_beeld_sets.py` alleen de docstring |
| `5e24f80` | De uitleg bij de schakelaar in Account → Foto's (`switch.hint` in `beeld.nl.ts` en `beeld.en.ts`; die staat er alleen met de analyse aan) zegt wat je mag verwachten (Keuze 85): één woord geeft meestal een paar goede foto's bovenaan; sommige woorden vinden weinig, zoek dan op 'berg' in plaats van 'bergen' of op 'bootje' in plaats van 'boot'; bij "Meer zoals deze" gaat ongeveer de helft over hetzelfde onderwerp. `beeld.test.ts` en `HINT` in `e2e/test_zoek_beeld.py` zijn mee veranderd. De tekst van de terugval (`photos_people.settings.beeldHint`) bleef gelijk, zodat de pagina met de analyse uit dezelfde blijft als in 0.8.0 (`e2e/test_uit080.py`, `e2e/uit080_web.json`). [Na de review: de zin over "Meer zoals deze" noemt de eerste foto's (`f78b415`) en de Engelse uitleg noemt geen enkelvoud meer (`82bfc26`); zie "De review van golf 6 afronden"] |

**CI op `502f4c3`** (run 43870814179, job `beeld-model`, een Xeon 6975P-C met VNNI en AMX; de
cijfers staan in x3a.md, "CI op 502f4c3"). BZ2 gaf twee keer dezelfde sha256 en precies de vijf
pins van de uitkomst die sinds `c921c66` in `models.lock` staan. BZ4 was groen met de
ondergrenzen van Keuze 85, op de set uit het voorstel: nl precisie@5 0,692 (fp32 0,692),
slechtste vraag buiten 'bergen' en 'boot' 2/5 ('bergen' 0/5, 'boot' 0/5), combinaties 6/6, int8
zakt 0,000, meer zoals deze 0,475 (fp32 0,463), kleuren 3/3. BZ6: 35,9 ms per foto (1.671 per
minuut), een vraag p95 23,4 ms. pytest: `bz3` 4, `bz4` 6, `bz4r` 5 en `bz6` 1 goed. De job werd
alleen rood op de laatste stap, "Uitslag": op `origin` had `models.lock` de regels van SigLIP niet,
en `beeld.tsv` was een voorstel. Beide staan nu op deze tak (`c921c66`, `d1c29e0`). Of die stap na
de push groen is, zegt CI.

Wat dit document daarna zegt: de go/no-go (deel 3 en de alinea eronder), Open 1, de rijen van
Klaar als (de go/no-go, BZ2, BZ4 en de tijden), Bewijs BZ1–BZ10 (BZ2, BZ4, BZ6), "Open na de
samenvoeging", G6-2 in De review, "Klaar als, nagelopen" en "Open na de nacontrole" hebben een
noot [Na golf 6, …] of een doorgehaalde zin. Keuze 85 beantwoordt Vraag 11 (standaard aan of uit)
niet; B49–B70, Vraag 4 en Vraag 8–14 blijven open.

### De review van golf 6 afronden

De review van `9a64365..ae1ec48` vond niets dat blokkeert, één punt dat zou moeten (R7-1) en twee
kleine (R7-2, R7-3). Elk punt eerst zelf nagegaan.

| Punt | Wat er gedaan is | Test |
|---|---|---|
| **R7-1** (zou) De Engelse uitleg zei "then try another word, for example the singular"; dat is niet gemeten | Klopt. Gemeten zijn alleen Nederlandse vragen: 'bergen' werd 'berg' (3/5), maar 'boot' werd 'bootje' (3 tot 4/5), en dat is geen enkelvoud. De Engelse vragen van BZ4 haalden op `502f4c3` allemaal 3 of meer van 5 (beach 3, dog 4, cat 5, snow 4, bicycle 4, sunset 5). De zin is nu "then try a different word for the same thing" (`82bfc26`) | `beeld.test.ts`, "de uitleg in het Engels zegt alleen wat gemeten is (Keuze 85)": de hele Engelse uitleg; met de oude zin rood |
| **R7-2** (klein) "Ongeveer de helft van wat 'Meer zoals deze' laat zien" gaat verder dan de meting | Klopt. BZ4 meet "Meer zoals deze" op de top 4 (0,475 op `502f4c3`), terwijl de link naar `/zoeken?mode=image&like=` per 48 foto's laadt. De zin gaat nu over de eerste foto's: "Bij 'Meer zoals deze' gaat ongeveer de helft van de eerste foto's over hetzelfde onderwerp." en "Of the first few photos 'More like this' shows, about half are about the same subject." (`f78b415`). De terugval `photos_people.settings.beeldHint` bleef gelijk | `beeld.test.ts` (de teksten van Keuze 81 en de Engelse uitleg) en `HINT` in `e2e/test_zoek_beeld.py`; met de oude teksten zijn beide tests van `beeld.test.ts` rood |
| **R7-3** (klein) De rij "schakelaar" in de teksten van Keuze 81 had de zinnen van Keuze 85 niet, terwijl de test met die naam ze wel eist | Klopt. [bouwontwerp.md](bouwontwerp.md) heeft in die rij een noot met de hele tekst erachter, en Keuze 85 "In de app" een noot over R7-2. De naam van de test bleef | Alleen tekst |

**Gedraaid na de review**, achter het slot van de zware runs en op drie cores: in `web`
`npm run typecheck`, `npm test` (123 bestanden, 1.344 tests groen), `npm run build`,
`npm run check:i18n -- --strict` (0 meldingen) en `npm run budget` (binnen het budget; likeRow
2.692 van 3.000 B, beeld 11,0 van 15,0 KB); `e2e/test_zoek_beeld.py` en `e2e/test_uit080.py` met
`uv run --with playwright==1.65.2` (9 groen in 197 s, met de Chromium van de sandbox); ruff schoon
op `e2e/test_zoek_beeld.py`. In CI na de push: de jobs `web` en `e2e`.

### CI op dc5dfe5 (golf 6 gepusht)

Run 43888472609. **`beeld-model` is groen, ook de stap "Uitslag"** (`models.lock en beeld.tsv
waren er al; geen voorstel`): BZ2 twee keer dezelfde sha256 en precies de pins van
`models.lock`, BZ4 op de 120 foto's van `beeld.tsv` met de ondergrenzen van Keuze 85 (nl 0,692,
fp32 0,692; slechtste vraag buiten 'bergen' en 'boot' 2/5; combinaties 6/6; int8 zakt 0,000;
meer zoals deze 0,475; kleuren 3/3), BZ6 34,7 ms per foto op een Xeon 6975P-C met VNNI. Ook
groen: `web`, `api-media`, `https`, `ocr`, `media-image`, `infra`, `office`, `e2e-media` en de
twee rapporten. [I2: ook groen waren `api`, `zoek-perf`, `e2e` en de drie jobs "Images bouwen";
die ontbraken in dit lijstje. Alleen `analyse-image` en `api-analyse` waren rood.] Rood waren drie tests die alleen in CI draaien; geen ervan was een fout in de
app:

| Job | Wat | Oorzaak | Herstel |
|---|---|---|---|
| `analyse-image` | `als root op de host: kinderen: ['werk', 'zoeken']` | Sinds X3b draait embed_text in het kind zoeken, en de proef van de container vraagt embed_text. De controle in `test_analyse_container.sh` eiste nog precies één kind | De controle eist nu precies werk en zoeken, elk met de grenzen van `CHILDREN` in `wire.py` (werk nice 19 en RLIMIT_DATA 1 GiB, zoeken nice 0 en 1,5 GiB), allebei uit python-tekst, met onnxruntime, zonder de sleutel in hun omgeving. Nagegaan op gemaakte invoer: met alleen werk, twee keer zoeken, een onbekend kind, of zoeken met de grenzen van werk, de sleutel, zonder onnxruntime of dumpbaar is de controle rood |
| `api-analyse` | `test_maxrss_en_tijd_gemeten`: 411 MiB, poort 400 | ru_maxrss van wait4: Linux geeft bij (v)fork en exec de piek van de ouder door, hier die van pytest (dezelfde fout als in `test_group_perf.py`, gerepareerd in golf 6). De omzetting van het kleine model zelf is ±35 MiB | `bouw.maxrss_mib()` meldt VmHWM (de piek sinds exec), en de test leest die regel "klaar", zoals BZ2. De output van de omzetter verandert niet. Nieuwe test `test_de_maxrss_van_de_omzetter_telt_de_piek_van_pytest_niet_mee` (met de oude meting rood: 350 MiB); `ANALYSE_TESTS` 330 |
| `api-analyse` | `test_zoeken_laadt_nooit_pillow`: PermissionError op `/proc/<pid>/maps` | Het kind zoeken is niet dumpbaar; dan leest alleen root zijn maps, en `api-analyse` draait als gewone gebruiker | Het kind zegt het zelf: de test draait de worker met de proef (`proef.py`, die nu ook `onnxruntime` in `sys.modules` meldt), doet een echte rank met een vraag en vraagt daarna aan hetzelfde kind of PIL en onnxruntime geladen zijn. Als nobody groen; de oude test als nobody rood |

Gedraaid na het herstel: `tests_analyse` met `-m "not beeld_echt"` als root en als nobody (330
groen, 16 niet gekozen), de zeven API-tests die `ci.yml` lezen (246 groen), `bash -n` en
shellcheck op `test_analyse_container.sh`, ruff.

### Open na golf 6

- ~~**Alleen in CI, na de push:** `beeld-model` streng: BZ2 met dezelfde sha256 als de pins, BZ4
  op de foto's van `beeld.tsv` (geen voorstel meer uit Commons) met de ondergrenzen van Keuze 85,
  en de stap "Uitslag (rood zolang models.lock of beeld.tsv een voorstel is)", de enige rode stap
  op `502f4c3`. `api-analyse` exact 329 en 16. De rest zoals in "Open na de nacontrole": `api`
  (met `tests/uit080` en de tests van `d1c29e0`), `web` (`beeld.test.ts` van `5e24f80`),
  `zoek-perf`, `e2e` (met `test_uit080.py`, `test_zoek_beeld.py` met de nieuwe uitleg, en
  Playwright 1.65.2), `e2e-media` (de reparatie van m6 uit `b65eeb1`), `infra` (met
  `test_install_beeld.sh` en `--stappen`) en `analyse-image` (G6-1, `test_zoek_gezichten_echt.py`).~~
  [I2: `beeld-model` streng groen op `dc5dfe5`; alle 19 jobs groen op `e535332`, `api-analyse`
  330 en 16. Zie "CI op e535332" hieronder.]
- **BZ5 met de echte modellen** en `memory.peak` van de cgroup: CI (`analyse-image`) of de LXC.
  [I2: met de echte vorm M11 (Open 3).]
- **`test_install_search.sh --stappen` en `test_install_update.sh`**: niet gedraaid (de schijf);
  `test_install_update.sh` staat niet in CI (x2a.md, Open 8). [I2: `--stappen` draait in de job
  `infra` en was groen op `e535332`. `test_install_update.sh` is ook in I2 niet gedraaid
  ([integratie-2.md](integratie-2.md), "Niet gedaan in I2, met de reden").]
- **BZ9 en BZ10** op de LXC (Open 4 en 5).

### CI op e535332 (I2)

Run 43892193604: alle 19 jobs groen. `api-analyse` precies 330 goed en 16 niet gekozen (de
nieuwe test van het herstel telt mee), en `analyse-image` groen. `beeld-model` gaf dezelfde pins, BZ4
weer groen met de ondergrenzen van Keuze 85, BZ6 33,8 ms per foto en een vraag p95 22,2 ms, op een
Xeon 6975P-C met VNNI.

### Wat open blijft na I2

- **BZ5 en BZ6 als geheel met de echte vorm**: M11 en M12 in [metingen.md](metingen.md) (Open 3
  en 4). Met de echte gewichten zegt pas de LXC het.
- **De eerste beeldvraag na de start van de worker** valt terug op gewoon zoeken: het laden van de
  tokenizer en het tekstmodel duurde in de sandbox 1,65 s, de klok is 800 ms (Open 4). De vraag
  daarna is wel zoeken op beeld. Niet veranderd in I2.
- **BZ9 en BZ10** op de LXC (Open 4 en 5).
- **`memory.peak` van de cgroup** op de LXC.
- Niet van X3b en niet beslist: B49–B70, Vraag 4 en Vraag 8–14 blijven open (Open 8).
