# Fase 6: de integratie van 0.8.1 (I2)

De integratie die gezichten (X2) en zoeken op beeld (X3) tot versie 0.8.1 maakt. Ze begint waar
golf 6 eindigde, op `e535332`, en werkt in zeven pakketten naast elkaar, elk in een eigen
worktree. Daarna komen het samenvoegen, één review, de controles, de metingen M10–M13, de versie
0.8.1 en CI. De integratie van 0.8.0 (I1) staat in [integratie.md](integratie.md); wat Jonas
moet weten, in de [README](README.md).

Getallen, runs en hashes die er bij het schrijven nog niet waren, staan als ⟦I2: …⟧ in de tekst.
De integrator vult ze in na de metingen en na CI, zoals bij I1 ("C2 tokens").

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
| P6 docs | de teksten van 0.8.1: de README's, de runbooks, deze pagina, de metingen, de e2e, de notities in de golven, het ontwerp, het stappenplan en de beslissingen | ⟦I2: de commits van P6 zoals ze op de branch staan⟧ |
| P7 omzetting | de eerste echte omzetting van SigLIP 2 in de dienst `analyse-omzetten`, als stap in CI ([x3a.md](x3a.md), Open 9) | ⟦I2: de commits van P7 zoals ze op de branch staan⟧ |

De volgorde van samenvoegen: ⟦I2: de volgorde waarin de pakketten op de branch kwamen, en de commit na het samenvoegen⟧.

## Waar het afwijkt van het plan

1. **`ANALYSE=uit` en `BEELD=uit` blijven bewaard** (P1). Het plan zei alleen "standaard aan". Een
   gevraagde uit komt in `.env` en een volgende run zonder `ANALYSE` laat hem uit; zo zet een
   update nooit iets aan dat de beheerder bewust uit had.
2. **`/over` is er altijd** (P2), ook met de analyse uit en voor een gast, en Account linkt er
   altijd naar. Het plan wilde de link alleen waar de analyse zichtbaar is; de kritiek (CRITIC) wees
   erop dat de bronnen van de kaart (Keuze 29) er altijd moeten staan. `test_uit080` legt daarom
   precies vast wat er met de analyse uit bijkomt. De eerste lading JS groeide: in `6890c2f` van
   98.051 naar 98.141 B; op de samengevoegde kop ⟦I2: de grootte van de eerste lading JS op de samengevoegde kop, uit npm run budget⟧.
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

## De controles

Op de samengevoegde kop, elk zwaar pakket alleen:

| Controle | Uitslag |
|---|---|
| ruff in `api` en `e2e` | ⟦I2: uitslag van ruff check en ruff format --check⟧ |
| de hele suite van `api` | ⟦I2: aantal goed, overgeslagen en fout van de volle api-suite⟧ |
| `tests_analyse` met de echte modellen van gezichten | ⟦I2: precies '330 passed, 16 deselected' of het nieuwe getal uit ci.yml⟧ |
| de tests als gewone gebruiker (nobody) | ⟦I2: uitslag van de run als nobody⟧ |
| de installatietests (env, media, search, analyse, beeld) | ⟦I2: uitslag van de functiedelen van de vijf installatietests⟧ |
| web: typecheck, vitest, build, i18n strikt, budget | ⟦I2: uitslag van de web-controles en de eerste lading JS⟧ |
| `e2e/slim.sh` | ⟦I2: uitslag van slim.sh⟧ |
| de e2e van CI, office, en `test_zoek_gezichten.py`, `test_zoek_gezichten_echt.py`, `test_zoek_beeld.py`, `test_uit080.py` | ⟦I2: uitslag van de e2e-sets⟧ |
| yamllint, `docker compose config` en de links in de docs | ⟦I2: uitslag van yamllint, compose config en de linkcontrole⟧ |
| CI op de kop van I2 | ⟦I2: run-id, aantal jobs en wat groen was op de kop van I2⟧ |

Wat niet gedraaid is, met de reden: `test_install_update.sh` (±17 min, fase 4) weigert zolang
`python:3.12-slim` er is en draait niet in CI; net als bij 0.8.0 "niet gedraaid". ⟦I2: wat er verder niet gedraaid is, en waarom⟧

## De metingen M10–M13

De getallen staan in [metingen.md](metingen.md#gezichten-en-beeld-081), met hoe je ze opnieuw
meet. Kort:

- **M10:** ⟦I2: uitslag van M10 (de omzetting van siglip_vol, de grootte en de tijd)⟧.
- **M11 (BZ5):** ⟦I2: de som van VmHWM en per kind uit M11, tegen 2,5 GB en RLIMIT_DATA⟧.
- **M12 (BZ6 als geheel):** ⟦I2: p95 warm bij 55.000 en 100.000 en de eerste vraag uit M12, tegen 250 ms⟧.
- **M13 (§4.5 stap 2 met de analyse):** ⟦I2: de uitkomst van M13 per poort, met het aantal vectoren van beeld bij de start van het meetvenster⟧.

De modellen in M10–M12 hebben de echte vorm maar willekeurige gewichten. Ze zeggen dus iets over
de tijd en het geheugen, niets over de kwaliteit. De kwaliteit komt alleen uit BZ4 in CI (de
echte SigLIP 2) en, voor gezichten, uit `analyse-kwaliteit` op de LXC.

## De review

⟦I2: de review na het samenvoegen (veilig, uitrol, contract): wat ze vond, wat er hersteld is en met welke test⟧

## Standaarden die Jonas kan veranderen

Geen vraag aan Jonas: 0.8.1 gaat uit van deze standaarden, en hij kan ze veranderen
([README](README.md), "Vragen voor Jonas"):

- 0.8.1 blijft op de branch `claude/stappenplan-uitwerken-lu4uv3`, net als 0.8.0;
- er komt geen tag; de docs noemen de volledige commit van 0.8.1 als de weg terug voor de volgende
  versie;
- de twee eisen voor gezichten zonder gereedschap ("≥ 95% van de portretten" en "kleurvolgorde ≥
  0,05 hoger", [bouwontwerp.md](bouwontwerp.md)) staan op "niet gemeten, geen gereedschap"; Vraag
  8 blijft open.

B49–B70, Vraag 4 en Vraag 8–14 blijven open; B71 blijft een voorstel.

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
| De analyse staat standaard aan; uit blijft uit | de installatietests van P1, `--stappen` in CI | ⟦I2: uitslag van test_install_analyse.sh en test_install_beeld.sh en de stap --stappen in CI⟧ |
| `/over` voor iedereen, met de bronnen van kaart en modellen | vitest, `test_zoek_gezichten.py`, `test_uit080.py` | ⟦I2: uitslag van de vitest van about en de twee e2e⟧ |
| Met de analyse uit doet 0.8.1 wat 0.8.0 deed, op de vastgelegde zinnen en `/over` na | `test_uit080.py` | ⟦I2: uitslag van test_uit080.py⟧ |
| BZ5, BZ6 en §4.5 stap 2 met modellen van de echte vorm | M11, M12, M13 | ⟦I2: of M11, M12 en M13 binnen hun poort bleven⟧ |
| De eerste echte omzetting in `analyse-omzetten` | de stap van P7 in CI | ⟦I2: run en uitslag van de stap X3 in analyse-image⟧ |
| Versie 0.8.1 op de zes regels | de commit van de versie | ⟦I2: de commit met de versie 0.8.1⟧ |
| CI groen op de commit van 0.8.1 | GitHub Actions | ⟦I2: run-id en aantal groene jobs op de commit van 0.8.1⟧ |
| De volledige hash van 0.8.1 in de docs, als weg terug voor de volgende versie | de commit erna | ⟦I2: de volledige hash van 0.8.1 en de commit die hem in de docs zet⟧ |
