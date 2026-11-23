# Fase 6: de integratie van 0.8.1 (I2)

De integratie die gezichten (X2) en zoeken op beeld (X3) tot versie 0.8.1 maakt. Ze begint waar
golf 6 eindigde, op `e535332`, en werkt in zeven pakketten naast elkaar, elk in een eigen
worktree. Daarna komen het samenvoegen, één review, de controles, de metingen M10–M13, de versie
0.8.1 en CI. De integratie van 0.8.0 (I1) staat in [integratie.md](integratie.md); wat Jonas
moet weten, in de [README](README.md).

Getallen, runs en hashes die er bij het schrijven nog niet waren, stonden als merkteken in de tekst
(tussen ⟦ en ⟧, met I2 ervoor). De integrator vulde ze in na de metingen, zoals bij I1 ("C2
tokens"). Wat pas na de push bestond, de volledige hash van 0.8.1 en de run van CI, vulde de commit na de
push in: 0.8.1 is `ae163ac69470343de7bc8d876db1a48e7da9331e`, met CI groen in run 43981579405.

## Waar het begon

- **`e535332`** op de branch `claude/stappenplan-uitwerken-lu4uv3`. In CI waren daar alle 19 jobs
  groen (run 43892193604): `api-analyse` precies 330 goed en 16 niet gekozen, `analyse-image`
  groen, en `beeld-model` met dezelfde pins als op `dc5dfe5` (BZ6 33,8 ms per foto, een vraag p95
  22,2 ms, op een Xeon 6975P-C met VNNI).
- **`dc5dfe5`** (golf 6, run 43888472609): `beeld-model` helemaal groen, ook de stap "Uitslag". BZ4
  op de 120 foto's van `beeld.tsv` met de ondergrenzen van Keuze 85: Nederlands 0,692, de
  slechtste vraag buiten 'bergen' en 'boot' 2 van 5, combinaties 6 van 6, int8 zakt 0,000, "Meer
  zoals deze" 0,475, kleuren 3 van 3; BZ6 34,7 ms per foto. Drie tests die alleen in CI draaien,
  gingen nog uit van de stand vóór golf 6; hersteld in `e535332` ([x3b.md](x3b.md), "CI op
  dc5dfe5").
- **Keuze 85**: Jonas koos op 16 november 2026 om 12:39 "Toch uitbrengen". Zoeken op beeld komt in
  0.8.1, met de twee poorten van BZ4 als ondergrens ([bouwontwerp.md](bouwontwerp.md)).

## De pakketten

| Pakket | Wat | Commits |
|---|---|---|
| P1 infra | `install.sh` zet de analyse standaard aan (`ANALYSE_STANDAARD=aan`); een gevraagde `ANALYSE=uit` of `BEELD=uit` blijft in `.env` staan; een waarschuwing bij weinig ruimte voor gezichten (nooit fataal); de eindtekst "elke gebruiker zet het zelf aan"; de installatietests omgezet, `--stappen` met `ANALYSE=uit`; de opmerkingen in `.env.example` en `compose.analyse.yaml`; de drie installatieadressen in de runbooks naar de branch, met een controle | `e0a271e`, `afcaf84`, `988b974`, `c334dd4` |
| P2 web | `/over` met de bronnen en licenties (A2, K1-13); de zinnen met de analyse uit zeggen dat het uitstaat en wat de beheerder kan doen; "Persoon wissen" noemt de bevestigde gezichten als gezichten aanstaan, en vraagt één ding tegelijk; `test_uit080` legt precies vast wat 0.8.1 met de analyse uit anders toont; `/over` met alleen het toetsenbord | `6890c2f`, `bea29ca`, `4013d27`, `1a00a0f`, `93c303c`, `ad45bdb`, `ca8af68`, `44262c7` |
| P3 beeld | de drempels van zoeken op beeld vast op 0 (BZ4), met een test; BZ7 meet het geheugen van het kind zelf (VmHWM); `siglip_vol.py` (SigLIP 2 op ware grootte met willekeurige gewichten); BZ5 en `test_beeld_perf` ook met modellen van de echte vorm; `beeld-model` draait ook als `tests/beeld/piek.py` verandert | `eed4c00`, `d15499b`, `965a36d`, `4c70dbc`, `e8c4a02`, `3c3de47` |
| P4 belasting | `zoek_samen --analyse` voor stap 2 van §4.5 met de analyse helemaal aan; foto's van 1024 × 768 met een voorbeeld van 1024; in CI `--kort --analyse nep` als rapport | `657c82a`, `7ecb163`, `ef1efbd`, `cb664f4`, `f8439c7`, `e23170e`, `d8402dc` |
| P5 tests | de namenfuzz bevriest de heap van pytest tijdens het meten en zegt bij rood meer; Office o2 wacht tot de statusregel van Bram weet dat Anna weg is | `388081c`, `919d7f8`, `c84ab57` |
| P6 docs | de teksten van 0.8.1: de README's, de runbooks, deze pagina, de metingen, de e2e, de notities in de golven, het ontwerp, het stappenplan en de beslissingen | `e4c9f42`, `ff0b534`, `7648449`, `0e06532`, `659061a`, `c99993e`, `34c78eb`, `5f013df`, `a2bd2e7`, `1fd6d73`, `f28aec3`, `d9a0b43`, `6904c96`, `78b0385`, `ff6f9e3`, `0b03a1b`, `0f7e17f`, `d7d3c5b`, `f835fec` |
| P7 omzetting | de eerste echte omzetting van SigLIP 2 in de dienst `analyse-omzetten`, als stap in CI ([x3a.md](x3a.md), Open 9) | `416fc0f`, `2bde8f4`, `1db4940` |

De volgorde van samenvoegen, langs de eerste ouder vanaf `e535332`: P3 en P7 samen (`2ac847e`), P1
(`f6151d9`), P5 (`665c156`), P2 (`377c303`), P4 (`9aa4b99`) en P6 (`0e37cd9`). Daarna de review
en de nacontrole (`38c52f4`), het opwarmen in `test_beeld_perf` (`e41b724`, de kop waarop alles
hieronder gedraaid en gemeten is), de opmerkingen in de web (`969f345`, C4), de versie
(`ba7cf9d`) en de teksten met de getallen van I2.

## Waar het afwijkt van het plan

1. **`ANALYSE=uit` en `BEELD=uit` blijven bewaard** (P1). Het plan zei alleen "standaard aan". Een
   gevraagde uit komt in `.env` en een volgende run zonder `ANALYSE` laat hem uit; zo zet een
   update nooit iets aan dat de beheerder bewust uit had.
2. **`/over` is er altijd** (P2), ook met de analyse uit en voor een gast, en Account linkt er
   altijd naar. Het plan wilde de link alleen waar de analyse zichtbaar is; de kritiek (CRITIC) wees
   erop dat de bronnen van de kaart (Keuze 29) er altijd moeten staan. `test_uit080` legt daarom
   precies vast wat er met de analyse uit bijkomt. De eerste lading JS groeide: in `6890c2f` van
   98.051 naar 98.141 B; op de samengevoegde kop (`e41b724`) 98.149 B van de 100.000 (golf 6:
   98.030 B).
3. **De download van 1,55 GB is eenmalig** (kritiek "wrong" 3). Het plan schreef dat elke update het
   model opnieuw haalt; `install.sh` doet niets als de uitkomst er al met de juiste sha256 staat, en
   probeert het alleen na een mislukte omzetting opnieuw. De docs zeggen "één keer".
4. **P7 kwam erbij.** Het plan had zes pakketten. De kritiek vond dat de eerste echte omzetting in
   de afgeschermde `analyse-omzetten` nooit gedraaid had, terwijl die met de nieuwe standaard bij
   elke installatie draait. P7 maakt daar een stap in CI van.
5. **Meer jobs groen op `dc5dfe5` dan [x3b.md](x3b.md) noemde.** Naast `beeld-model` waren daar ook
   API, Zoek-perf, e2e en de drie jobs "Images bouwen" groen; x3b.md heeft er een correctie bij.
6. **De nummers van de metingen.** Het plan noemt ze M1–M4; [metingen.md](metingen.md) heeft al
   M1–M9 van 0.8.0. Daarom heten ze hier M10–M13:

| Hier | In het plan | Wat |
|---|---|---|
| M10 | M1 | een bron van SigLIP 2 op ware grootte met willekeurige gewichten (`siglip_vol.py`), omgezet zoals `analyse-omzetten` dat doet |
| M11 | M2 | BZ5: het geheugen van de analyse samen (de som van VmHWM ≤ 2,5 GB), met de echte modellen van gezichten en de SigLIP van M10 |
| M12 | M3 | BZ6 als geheel: zoeken op beeld via HTTP met het tekstmodel op ware grootte, op één core, p95 ≤ 250 ms warm bij 55.000 en 100.000 foto's en bij de eerste vraag |
| M13 | M4 | §4.5 stap 2 met de analyse helemaal aan (`zoek_samen --analyse`), met OnlyOffice: alle poorten van stap 2, plus zoeken op beeld p95 ≤ 250 ms en de analyse samen ≤ 2,5 GB |

7. **Het commando van M13.** De regel voor M4 in het plan draaide `uv run --extra analyse` zonder
   `UV_PROJECT_ENVIRONMENT=.venv-analyse`; dat zet numpy en onnxruntime in `api/.venv`. Zo is het
   niet gedraaid: de app liep uit `api/.venv`, de worker van de analyse uit `api/.venv-analyse`, en
   na de meting heeft `api/.venv` nog steeds geen numpy en geen onnxruntime. Het commando staat in
   [metingen.md](metingen.md#gezichten-en-beeld-081) en in het runbook
   [zoeken.md](../runbooks/zoeken.md).

## De controles

Op de samengevoegde kop `e41b724` (16 november 2026), elk zwaar pakket alleen, met de getallen van
golf 6 ([x3b.md](x3b.md), De nacontrole) ernaast:

| Controle | Uitslag |
|---|---|
| ruff in `api` en `e2e` | `ruff check` en `ruff format --check`: schoon; 796 bestanden in `api` en 95 in `e2e` al opgemaakt |
| de hele suite van `api` | 6.853 goed, 2 overgeslagen, 241 niet gekozen, 0 fout, in 2.890 s (golf 6: 6.844 in 3.081 s) |
| `tests_analyse` met de echte modellen van gezichten | precies `331 passed, 16 deselected`, in 210 s, zoals `ci.yml` nu eist (`ANALYSE_TESTS` 331, `ANALYSE_DESELECTED` 16; 330 tot `4aa42e1`, U1 hieronder) |
| de tests als gewone gebruiker (nobody) | deel A, de lijst van golf 6 plus wat I2 raakte (`/tmp/ci-nobody/run.sh`): 1.147 goed, 2 niet gekozen, in 497 s (golf 6: 912 goed). Deel B, `test_beeld_ops.py`, `test_beeld_perf.py` en `test_geheugen_samen.py` met de `.venv-analyse` van nobody: 16 goed in 117 s; de eerste poging was rood, zie onder |
| de installatietests (env, media, search, analyse, beeld) | alle vijf groen: env 137 controles, media 129, search 209, analyse 158, beeld 144 (golf 6: media 127, analyse 128, beeld 120); de ansible-render met 3 nodes, `failed=0` |
| web: typecheck, vitest, build, i18n strikt, budget | groen: 125 bestanden met 1.360 tests (41 s), build 8 s, i18n strikt 0 meldingen; budget binnen: eerste lading JS 98.149 B (100.000), CSS 3.403 B (12.000), faces 11.964 B (12.000, 36 B over), beeld 11.180 B (15.000), likeRow 2.833 B (3.000), about 1.808 B (8.000), grootste van 179 lazy chunks 13.281 B (50.000) |
| `e2e/slim.sh` | groen in 1.107 s: stap 1 (zoek en triggers in python:3.12-slim) 1.309 goed, 5.787 niet gekozen, in 801 s; e2e met de marker `zoek` 146 goed, 275 niet gekozen, in 294 s (golf 6: 1.204 s, 111 e2e) |
| de e2e van CI, office, en `test_zoek_gezichten.py`, `test_zoek_gezichten_echt.py`, `test_zoek_beeld.py`, `test_uit080.py` | de set van CI 285 goed, 136 niet gekozen, in 1.135 s (golf 6: 249); office 30 goed in 134 s; de vier van I2 met de metingen erbij 115 goed en 1 overgeslagen in 271 s (gezichten 3, beeld 8, uit080 2, metingen 102): `test_zoek_gezichten_echt.py` slaat zich over zonder image (`E2E_ANALYSE_IMAGE` zet de job `analyse-image`) |
| yamllint, `docker compose config` en de links in de docs | yamllint 25 bestanden schoon, shellcheck 0.11.0 schoon, `docker compose config -q` schoon, de harding van DS `fail=0`; de relatieve links en ankers in de docs van I2 kloppen (na deze teksten opnieuw nagelopen) |
| CI op de kop van I2 | run 43981579405 op `ae163ac`: alle 19 jobs groen, met `api-analyse` precies `331 passed, 16 deselected` (165 s, Xeon 6975P-C met VNNI en AMX), `beeld-model` met BZ2, BZ4, BZ6 en BZ7 goed, `infra` met `--stappen` 79 goed en 0 fout, en `analyse-image` met de echte omzetting (X3) en `test_zoek_gezichten_echt.py` groen |

Wat niet gedraaid is, met de reden: `test_install_update.sh` (±17 min, fase 4) weigert zolang
`python:3.12-slim` er is en draait niet in CI; net als bij 0.8.0 "niet gedraaid". Verder alleen in
CI, omdat het een image of de echte modellen vraagt: `test_zoek_gezichten_echt.py` (het image van
`analyse-image`), de stap X3 met de echte bron in `analyse-omzetten` (de download van 1,55 GB) en
`beeld-model` met de echte SigLIP 2 (BZ4 en de echte BZ7).

Deel B als nobody was de eerste keer 14 van 16 rood ("de verbinding viel weg"). De oorzaak zat in
de run, niet in de code: die draaide onder `nice -n 5`, en dan zet pid 1 van de analyse het kind
`zoeken` terug op nice 0 (`os.setpriority` in `worker/child.py`). Een lagere nice vraagt
`CAP_SYS_NICE`, die nobody niet heeft; het kind start niet. Zonder die extra nice: 16 goed. In CI
en in de container start pid 1 op nice 0, dus daar speelt het niet; de vraag of `zoeken` zijn
nice beter relatief zet, staat bij [Vragen voor Jonas](#vragen-voor-jonas).

## De metingen M10–M13

De getallen staan in [metingen.md](metingen.md#gezichten-en-beeld-081), met hoe je ze opnieuw
meet. Kort:

- **M10:** `siglip_vol.py` maakt de bron in 11,9 s (`model.safetensors` 1.507.473.288 B,
  `tokenizer.json` met 256.000 stukken en 600.000 merges, seed 85). Omzetten in het proces: 13,0 s,
  VmHWM 338 MiB; in de afgeschermde `analyse-omzetten` (P7) 13,1 en 12,7 s, maxrss 339 MiB, piek
  van de cgroup 527–528 MiB met de page cache (grens 3.072 MiB), `/tmp` 0 van 64 MiB, en dezelfde
  sha256 voor alle vijf bestanden. Uitkomst: `beeld.int8.onnx` 102.609.072 B, `tekst.int8.onnx`
  284.248.004 B, `tokenizer.bin` 12.388.813 B (de echte 12.414.446 B). Niet opnieuw gedraaid in
  stap 2; de getallen komen uit P3 en P7.
- **M11 (BZ5):** pid 1 37 MiB, `werk` 619 MiB (RLIMIT_DATA 1.024), `zoeken` 1.178 MiB (RLIMIT_DATA
  1.536); de som 1.835 MiB tegen 2.384 MiB (2,5 GB), de hoogste som tegelijk 1.788 MiB. Gehaald.
- **M12 (BZ6 als geheel):** het geheel p95 90,2 ms warm bij 55.000, 97,5 ms warm bij 100.000 en
  106,9 ms bij de eerste 20 vragen na het opwarmen (max 150,8 ms), tegen 250 ms; zonder het
  tekstmodel 58,7, 65,9 en 73,3 ms, tegen 100 ms. Het tekstmodel kost ±34–42 ms per vraag op één
  core, en laden 1,78 s. Gehaald.
- **M13 (§4.5 stap 2 met de analyse):** geslaagd, alle 13 poorten van stap 2 gehaald, met 5.000
  vectoren van beeld (5.000 in het bestand) bij de start van het meetvenster. Media zonder haperen
  (elke start ≤ 2,46 s), zoeken p95 53,4 ms, `/files/root` 80,5 ms, `/health` 5,0 ms,
  `MemAvailable` minstens 11.268 MiB, OnlyOffice 8,3 s tegen 8,9 s (1,2 × 7,4 s) met 0 verloren
  woorden, zoeken op beeld p95 191,8 ms (106 vragen, geen terugval), de analyse samen 1,51 GB en
  geen OOM-kill. De eerste run brak af: de vrije ruimte op `/` zakte onder de 10 GiB van media
  (Keuze 31), en elke speler kreeg 503 `media_busy`; geen fout in de code. Run 2 begon op
  14,81 GiB.

De modellen in M10–M13 hebben de echte vorm maar willekeurige gewichten (`modellen-vol`: SigLIP 2
op ware grootte uit `siglip_vol.py`, met de echte YuNet en SFace). Ze zeggen dus iets over de tijd
en het geheugen, niets over de kwaliteit. De kwaliteit komt alleen uit BZ4 in CI (de
echte SigLIP 2) en, voor gezichten, uit `analyse-kwaliteit` op de LXC.

## De review

Na het samenvoegen van P1–P5 en P7 (op `9aa4b99`, vóór de teksten van P6) keek één review met
drie lenzen: veilig, uitrol en contract. Ze vond twaalf punten. De herstellingen staan op de branch
via `38c52f4`; een nacontrole draaide elke herstelcommit terug en zag zijn test rood worden.

| Punt | Wat er mis was | Hersteld | De test, rood zonder de herstelcommit |
|---|---|---|---|
| U1 (uitrol) | Het opwarmen van zoeken op beeld (`rank` met k = 0) laadde alleen het bestand, niet het tekstmodel. De eerste woordvraag laadde het dan onder de klok van 800 ms en viel terug; een afgebroken vraag stopte na 5 s met SIGKILL terwijl het model nog laadde, en op een tragere CPU zou het model nooit blijven staan. | `4aa42e1`: `rank` met k = 0 laadt na het bestand ook de tokenizer en het tekstmodel, onder de eigen deadline van `rank` (30 s). Zonder tekstmodel blijft het bestand geladen ("Meer zoals deze"). | `tests_analyse/test_beeld_ops.py::test_opwarmen_laadt_het_tekstmodel` (laden vertraagd tot 1,5 s; na het opwarmen antwoordt een woordvraag binnen 800 ms, met 3 treffers in hetzelfde kind `zoeken`): zonder `4aa42e1` TimeoutError. Daarom telt `api-analyse` sindsdien 331. |
| U3 (uitrol, klein) | Een bezet kind `zoeken` (`AnalyseBusy`) viel terug als "geen container". | `24b35cc`: bezet telt als te traag (`slow`); `down` blijft voor een container die weg is. | `tests/beeld/test_beeld_events.py::test_beeld_busy`: zonder de commit 'down'. |
| C1 (contract) | De echte vorm van `test_beeld_perf.py` legde het hele tekstmodel onder de grens van 100 ms: rood op een goed systeem, en geen van beide getallen van het ontwerp gemeten. | `8934ee3`: de echte vorm meet beide, zonder het tekstmodel p95 ≤ 100 ms en het geheel p95 ≤ 250 ms (BZ6). De nepvorm in `api-analyse` houdt 100 ms op het geheel. | Zonder de commit rood (warm 55.000 p95 103,3 > 100). 40 ms extra vóór de SQL maakt beide vormen rood. |
| C2 (contract) | `ECHT_ERBIJ` van `zoeken` in `test_geheugen_samen.py` schatte 440 MB voor de echte modellen; gemeten is het +779 MiB. En de echte vorm keek niet meer naar de grens bij herladen. | `50213fb`: 780 MB, en in de echte vorm ook `zoeken` ≤ cache + import + `ECHT_ERBIJ`. De nepvorm werd strenger: nog 401 MB ruimte onder 2,5 GB. | Met 440 MB rood (1.176,4 > 1.040,0). |
| V1 (veilig, klein) | `test_omzetter_echt.sh` liet een tijdelijke map van root staan als `OUT` niet te maken was: de trap kwam pas na `mktemp`. | `f30d43d`: het opruimen staat meteen na `mktemp`. | `api/tests/analyse/test_analyse_image.py` legt de volgorde vast (`docker_root`, `cleanup()`, `mktemp`, de trap, `mkdir -p "$OUT"`): zonder de commit rood. |
| V2, U2, C3 (alle drie) | De zinnen met de analyse uit toonden iedereen `/opt/platform/.env` en `ANALYSE=uit`, en noemden dat als de oorzaak, ook als de analyse om een andere reden niet draait (PostgreSQL, een geweigerde map, geen map met sleutels). | `d538cbb`: voor iedereen "Zoeken op beeld staat uit op deze server. De beheerder kan het aanzetten."; op `/beheer` dat de analyse uit staat of niet helemaal geïnstalleerd is, en dat de reden in de eindtekst van het installatiescript staat, bij "Gezichten (de container analyse)". | `web/src/lazySlots.test.ts`: geen `/opt/platform`, `.env` of `ANALYSE` in de zinnen voor iedereen; de zin op `/beheer` noemt de regel van de eindtekst en `ANALYSE=aan`. Zonder de commit 4 rood. |
| C4 (contract) | Opmerkingen en een testtitel in de web zeiden nog "komt later". | `d538cbb`, en de laatste in `969f345`. | Alleen opmerkingen en een titel. |
| U5 (uitrol, klein) | `test_omzetter_echt.sh` zei dat het de grenzen van `analyse-omzetten` controleerde, maar accepteerde elke `mem_limit` en `cpus`. | Eerst afgewezen; de nacontrole vond de reden niet goed en herstelde het in `7a479d8`: precies `mem_limit 3072MiB cpus 1` als `docker info` 'true true' zegt, anders `mem_limit geen cpus geen`. | Rood met een grens van 4g en zonder het bestand met de grenzen. Te bevestigen in CI (`analyse-image`). |
| U4 (uitrol, vraag) | `BEELD=uit` samen met `ANALYSE=uit` komt niet in `.env`; een latere `ANALYSE=aan` schrijft `BEELD=aan` en haalt 1,55 GB. | Niet veranderd: Vraag 4 hieronder. Jonas koos de standaard, zo laten (besloten: standaard, Jonas 2026-11-17). | — |
| C5 (contract, klein) | De wachttijd van 3 s en de bescherming tegen twee keer drukken bij "Persoon wissen" staan alleen als brontekst in een test. | Niet gedaan: er gaat niets mis, en een test met de DOM is werk voor later. | — |

Na de nacontrole kwam `e41b724`. De nepvorm van `test_beeld_perf.py` stelt eerst één vraag die niet
meetelt ('rood' bij 55.000 foto's), en die moest het bestand in `zoeken` laden binnen de klok van
800 ms. Op één core van de sandbox kostte dat 698–892 ms, al op `9aa4b99` en los van U1; in de
nacontrole was daardoor één van de 331 rood. `e41b724` warmt het bestand eerst op, zoals
`bjobs.warm` na het bouwen van `beeld.vec` (K2-13). De gemeten reeks en de grens van 100 ms bleven
gelijk.

Een gevolg van U1: na een herstart van de container betaalt de eerste `GET /photos/beeld` (of het
opwarmen na `beeld.vec`) één keer het laden van het tekstmodel, 1,6–1,8 s op één core van de
sandbox. Dat valt onder de deadline van `rank`, niet onder de klok van een zoekvraag.

## Bekende grenzen

Wat 0.8.1 niet goed doet of niet liet zien, zonder mooier te maken:

- **De metingen gebruiken willekeurige gewichten.** `modellen-vol` heeft SigLIP 2 op ware grootte,
  maar met willekeurige gewichten. M10–M13 zeggen iets over tijd en geheugen; de kwaliteit komt
  alleen uit BZ4 in CI (de echte SigLIP 2).
- **Gezichten deden niet echt mee in M13.** De testfoto's hebben geen gezichten: YuNet las ze
  allemaal, maar SFace en het groeperen kregen niets te doen. De ruimte per gezicht is ook niet
  gemeten (per gezicht AES-GCM over 128 × float32, 512 B klare tekst).
- **Na een herstart warmt zoeken op beeld één keer op**, ±1,7 s (U1 hierboven). Dat betaalt het
  opwarmen, niet de eerste zoekvraag.
- **De chunk van gezichten heeft 36 B ruimte.** 11.964 van 12.000 B op `e41b724`. Elke volgende
  regel in de schermen van gezichten gaat over het budget (een vraag hieronder; de grens blijft, besloten: standaard, Jonas 2026-11-17).
- **Office: de knop Sluiten schuift.** Gaat een andere bewerker weg, dan verliest de statusregel
  "met 1 ander" en schuift Sluiten in de kopbalk ±80 px. Wie precies dan klikt, kan naast de knop
  loslaten. De test in `e2e/test_office_o2.py` wacht er nu op (`919d7f8`); de app is niet
  veranderd.
- **Waarom de namenfuzz rood was in de hele suite, is niet bewezen.** `test_fuzz_10000_namen_met_vaste_seed`
  was in de volle api-suite een paar keer 0,514–0,561 s (grens 0,5 s). Een volle ronde van de gc
  is een vermoeden: `388081c` bevriest de heap tijdens het meten, en bij rood noemt de melding nu de
  drie tijden, de gc-rondes en de draden (`c84ab57`). De grens bleef 0,5 s. In de volle suite van
  I2 was hij groen.
- **`/over` vraagt een login.** De pagina met de bronnen en licenties is er voor iedereen die is
  ingelogd, ook een gast, maar niet voor een bezoeker zonder account.
- **BZ6 meet zoeken op beeld zonder de gezichtenscan ernaast.** In stap 2 van M13 wachtte de scan
  op de index (Keuze 65). In stap 3, een rapport, liep de scan van 5.000 foto's op dezelfde core,
  en daar was beeld p95 290,4 ms (9 van 103 vragen boven 250 ms). Een vraag hieronder; het blijft zo (besloten: standaard, Jonas 2026-11-17).
- **`test_install_update.sh` is niet gedraaid**, net als bij 0.8.0 (hierboven).

## Niet gedaan in I2, met de reden

| Wat | Waarom niet | Waar het staat |
|---|---|---|
| `test_install_update.sh` (±17 min, fase 4) | weigert zolang `python:3.12-slim` er is, en draait niet in CI; net als bij 0.8.0 niet gedraaid | [integratie.md](integratie.md), De controles |
| Een `--stappen` met echte docker en de analyse standaard aan (modellen die niet te halen zijn, de container met `models_missing`) | vandaag alleen met de nep van docker in de installatietests | P1 |
| `ROLLBACK_SHA` in `test_install_search.sh` van 0.7.1 naar de commit van 0.8.0 | hoort bij een volgende versie | [a0.md](a0.md) |
| Gereedschap voor de kleurvolgorde en voor "≥ 95% van de portretten" | er is geen set met echte gezichten: Vraag 8 is nee (besloten: standaard, Jonas 2026-11-17), dus de twee eisen blijven niet gemeten | [README](README.md), Vragen voor Jonas |
| Een GPS-waarde met 60,0 seconden | een herstel vraagt een nieuwe versie van photometa, en dan leest de update van Jonas elke foto opnieuw | [integratie.md](integratie.md), Niet gedaan; [metingen.md](metingen.md), Bevindingen 5 |
| De koude eerste weergave van ±8 s | geen meting draait `run_all.py` met een trace | [z5.md](z5.md), Open |
| Een e2e voor "Meer laden" op `/beheer`, en de gaten in de e2e met het toetsenbord van X2b | staan op de lijst voor de hand-test van Jonas | [z7.md](z7.md), Open; [toetsenbord-x2.md](toetsenbord-x2.md) |
| De regel van Keuze 82 (geen "Meer zoals deze" op een foto met een gevonden gezicht) | een beslissing na BZ10 op de LXC | runbook [analyse.md](../runbooks/analyse.md) |
| Een vaste zin rond de vraag voor 'bergen' en 'boot', en SmoothQuant | onderzoek; Keuze 85 laat de twee zwakke woorden toe | [x3a.md](x3a.md) |
| Kleine punten uit A0 en X3b (een nieuw mime-type verhoogt `gen` niet, de uv-cache in het image, een WAL-checkpoint per start, PostgreSQL voor 0011 en 0012 met de hand, `ImageHint` en de datum in het raster van beeld) | klein; geen pakket van I2 had ze | [a0.md](a0.md) en [x3b.md](x3b.md), Open |
| `test_analyse_kinderen.py` naar `analyse-image` | later | [x2a.md](x2a.md), Open |
| Opnieuw nagaan: de ids van Wikidata en de miniaturen voor `beeld.tsv` | `beeld.tsv` staat vast sinds golf 6 (`d1c29e0`) en BZ4 is groen op `dc5dfe5` | [x3a.md](x3a.md) |

## Vragen voor Jonas

**Besloten op 17 november 2026.** Jonas koos "Standaard vastzetten": elke vraag hieronder staat op
haar standaard, met de status **besloten: standaard, Jonas 2026-11-17** (kort ook in de
[README](README.md#vragen-voor-jonas)). 0.8.1 draaide al zo; er verandert niets.

1. **Waar staat 0.8.1?** Standaard: op de branch `claude/stappenplan-uitwerken-lu4uv3`, net als
   0.8.0. Anders: naar `main`.
2. **Een tag voor 0.8.1?** Standaard: geen tag; de docs noemen de volledige commit van 0.8.1 als de
   weg terug voor de volgende versie.
3. **De twee eisen voor gezichten zonder gereedschap** ("≥ 95% van de portretten" en
   "kleurvolgorde ≥ 0,05 hoger", [bouwontwerp.md](bouwontwerp.md)). Standaard: **niet gemeten,
   geen gereedschap**. Vraag 8 is nee (besloten: standaard, Jonas 2026-11-17): de twee
   eisen blijven bij besluit niet gemeten; de kwaliteit meet alleen Jonas, met `analyse-kwaliteit`
   op de LXC.
4. **`BEELD=uit` samen met `ANALYSE=uit` (U4).** Nu schrijft `install.sh` de regel `BEELD` alleen
   als hij al in `.env` staat of als de analyse aangaat. Wie beide uit vraagt en later
   `ANALYSE=aan` zet, krijgt zoeken op beeld erbij en de download van 1,55 GB. (a) `BEELD` ook
   schrijven met de analyse uit, zodat de keuze blijft (wat de review aanraadt); (b) de regel
   houden en in de eindtekst zeggen dat `BEELD` alleen telt met `ANALYSE=aan`. Standaard: zo
   laten; `install.sh` verandert niet (besloten: standaard, Jonas 2026-11-17).
5. **Het kind na een afgebroken vraag (`Kid._stop`).** De worker wacht 5 s (`KILL_GRACE_S`) voor hij
   een kind dat nog bezig is stopt. Een antwoord "afgebroken" zou `zoeken` eerder vrijmaken, maar
   raakt elke opdracht en beide kinderen. (a) zo laten (standaard); (b) een eigen pakket met tests
   voor afbreken in `werk` en `zoeken`.
6. **De paden van de job `beeld-model`.** Die start niet als alleen `test_beeld_perf.py`,
   `test_geheugen_samen.py` of `siglip_vol.py` verandert; de echte vorm draait met de hand, de
   getallen staan in de docs. (a) zo laten (standaard); (b) de paden toevoegen, zodat zo'n
   wijziging `beeld-model` start.
7. **Een pid 1 met een hogere nice.** `zoeken` zet zijn nice op 0. Start iemand pid 1 van de analyse
   met `nice` (zoals de eerste run als nobody hierboven), dan mag dat niet zonder `CAP_SYS_NICE` en
   start `zoeken` niet. (a) zo laten (standaard): in de container start pid 1 op nice 0; (b)
   `zoeken` zet zijn nice nooit lager dan die van pid 1, met een test.
8. **36 B ruimte in de chunk van gezichten.** (a) zo laten (standaard): de grens van 12.000 B blijft,
   en wie de schermen van gezichten uitbreidt, maakt eerst iets anders kleiner of vraagt een hogere
   grens; (b) de grens nu ophogen.
9. **BZ6 met de gezichtenscan ernaast.** (a) zo laten (standaard): BZ6 is de poort van stap 2, stap
   3 is een rapport; (b) een aparte meting of rapportregel voor zoeken op beeld terwijl de scan
   loopt; (c) de scan laten wijken voor een beeldvraag (een codewijziging, met een nieuwe test).

I2 besliste zelf niets. Op 17 november 2026 zette Jonas alles op de standaard: B49–B71 (B53 en
B57 waren al besloten), Vraag 4 en Vraag 8–14, en de negen vragen hierboven zijn
besloten: standaard, Jonas 2026-11-17.

## Alleen op de LXC

Wat de sandbox en CI niet kunnen laten zien; de lijst voor Jonas staat in de
[README](README.md#op-de-lxc):

- BZ9, `beeld-bench` op de CPU van Jonas: p95 ≤ 250 ms en ≥ 500 foto's per minuut;
- BZ10, `beeld-kwaliteit --personen`, na bevestigde personen;
- `analyse-bench` (de snelheid van gezichten) en `analyse-kwaliteit` (precisie, zuiverheid en
  vangst tegen zijn eigen ja en nee);
- `memory.peak` en `OOMKilled` van `platform-analyse-1` onder de echte cgroup, na een paar dagen;
- de map met sleutels op een eigen mount point zonder backup, als hij dat wil;
- de echte update van 0.8.0 naar 0.8.1 (de download, de omzetting, de start van de container) en
  zo nodig terug naar `59e0250`.

## Klaar als

| Punt | Waar bewezen | Uitslag |
|---|---|---|
| De analyse staat standaard aan; uit blijft uit | de installatietests van P1, `--stappen` in CI | `test_install_analyse.sh` (158 controles) en `test_install_beeld.sh` (144) groen op `e41b724`; `--stappen` in CI: 79 goed en 0 fout (run 43981579405 op `ae163ac`) |
| `/over` voor iedereen, met de bronnen van kaart en modellen | vitest, `test_zoek_gezichten.py`, `test_uit080.py` | vitest groen (125 bestanden, 1.360 tests, ook `about.test.ts`); `test_zoek_gezichten.py` 3 en `test_uit080.py` 2 groen. "Iedereen" is iedereen die is ingelogd, ook een gast; zonder login niet (Bekende grenzen) |
| Met de analyse uit doet 0.8.1 wat 0.8.0 deed, op de vastgelegde zinnen en `/over` na | `test_uit080.py` | 2 groen op `e41b724`, met de zinnen van `d538cbb` |
| BZ5, BZ6 en §4.5 stap 2 met modellen van de echte vorm | M11, M12, M13 | alle drie binnen hun poort: M11 1.835 van 2.384 MiB; M12 het geheel p95 ≤ 106,9 ms (250) en zonder tekstmodel ≤ 73,3 ms (100); M13 13 van 13 poorten, beeld p95 191,8 ms, de analyse samen 1,51 GB |
| De eerste echte omzetting in `analyse-omzetten` | de stap van P7 in CI | groen in run 43981579405 op `ae163ac`: de echte bron van SigLIP 2 in de dienst `analyse-omzetten` (alleen lezen, geen netwerk, `mem_limit 3072MiB`, `cpus 1`) omgezet in 9,6 s met maxrss 351 MiB; de vijf bestanden precies die van `models.lock` (grootte en sha256), en geen bron of tijdelijke map achtergebleven |
| Versie 0.8.1 op de zes regels | de commit van de versie | `ba7cf9d` (`api/app/__init__.py`, `api/pyproject.toml`, `api/uv.lock`, `web/package.json` en twee regels in `web/package-lock.json`); `uv lock --check` en `uv sync --frozen` goed |
| CI groen op de commit van 0.8.1 | GitHub Actions | run 43981579405 op `ae163ac`: alle 19 jobs groen |
| De volledige hash van 0.8.1 in de docs, als weg terug voor de volgende versie | de commit erna | `ae163ac69470343de7bc8d876db1a48e7da9331e`; de commit direct na `ae163ac` zet hem in de docs en verandert alleen docs |

## Na I2: PR #2 en de besluiten (17 november 2026)

- **PR #2 is samengevoegd** in `claude/stappenplan-uitwerken-lu4uv3` (fast-forward naar `aa6fa8b`).
  Daarmee kwamen `app.cli db-copy` (de SQLite van één node naar PostgreSQL, voor het cluster), de
  tests op PostgreSQL met `PLATFORM_TEST_DATABASE_URL`, en de CI-job `api-postgres` (toen nog niet
  blokkerend). De app van 0.8.1 voor één node verandert er niet door. Het vervolg op PostgreSQL
  staat in [i3/w1.md](i3/w1.md).
- **Alle keuzes van fase 6 zijn besloten.** Jonas koos "Standaard vastzetten" voor B49–B71, Vraag 4
  en Vraag 8–14, de vragen van I1 en de negen vragen hierboven:
  besloten: standaard, Jonas 2026-11-17. Zie [beslissingen.md](../fase-0/beslissingen.md).
