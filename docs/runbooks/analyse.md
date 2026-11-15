# Runbook: de analyse (gezichten en zoeken op beeld)

De analyse is het deel van het platform dat met modellen naar foto's kijkt: gezichten vinden
(fase 6, X2) en later zoeken op wat er op een foto staat (X3). Het rekenen gebeurt in een eigen
container, `platform-analyse-1`: zonder netwerk, alleen-lezen, als een eigen gebruiker (uid
10003), zonder capabilities. De app geeft hem per foto alleen de preview (als bestand dat al open
is, nooit een pad), en krijgt kaders en getallen terug. Wat de analyse is en hoe je hem aan- en
uitzet, staat in `infra/single/README.md` (Gezichten: de container analyse). Dit runbook gaat over
wat het script doet, de stand, de afscherming, uitzetten, de modellen, de sleutels en terug naar
0.8.0; het deel over zoeken op beeld (X3) staat onder "Zoeken op beeld (X3)".

**Tot 0.8.1 staat de analyse uit.** Zonder `ANALYSE` in `/opt/platform/.env` doet het script
precies wat 0.8.0 deed: geen download, geen container, geen nieuwe regel in `.env`. De app doet
vóór 0.8.1 nog niets met de container behalve zijn stand lezen; aanzetten is dus alleen om te
proberen.

Alles hieronder gebeurt in de Console van de container (de LXC), als root. `dc` is de functie uit
de README (Handige commando's): `docker compose` met precies de bestanden van het
installatiescript; staat de container analyse er, dan ook `compose.analyse.yaml` en de grenzen.
Het installatiescript is steeds de nieuwste versie:

```bash
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/main/infra/single/install.sh | bash
```

Hieronder heet dat kortweg "het script"; een instelling zet je ervoor, zoals
`curl … | ANALYSE=aan bash`. Zolang deze versie nog niet op `main` staat, gebruik je het adres
van de branch en `PLATFORM_BRANCH`, zoals in `infra/single/README.md`.

## Wat het script bij elke run met de analyse doet

1. Het leest `ANALYSE` uit de omgeving vóór `/opt/platform/.env`. Alleen `aan` of `uit`; iets
   anders stopt het script meteen, vóór er iets verandert.
2. Staat er geen `ANALYSE` in `.env` en vraag je er niet om, dan houdt het hier op: geen stap
   "Analyse", geen regel in de eindtekst (tot 0.8.1).
3. Bij `aan` komen in `.env` (rechten 600), als ze er nog niet zijn: `ANALYSE=aan`,
   `ANALYSE_WORKER_KEY` (64 tekens, één keer gemaakt en nooit vervangen, ook niet na uit en weer
   aan), `MODELLEN_ROOT=/opt/platform/modellen` en `SLEUTELS_ROOT=/opt/platform/sleutels/gezichten`.
   Een bestaande waarde blijft altijd staan. Met `ANALYSE=uit` verandert alleen die regel.
4. **De mappen.** `MODELLEN_ROOT` wordt een map van root (0755), `SLEUTELS_ROOT` een map van de
   app (uid 10001, 0700). Het script weigert, met uitleg, als een pad niet absoluut is, `.` of
   `..` bevat, onder `DATA_ROOT` ligt (of `DATA_ROOT` eronder), als de een in de ander ligt, of
   als een deel van het pad een symlink is, geen map is, niet van root is of door anderen te
   beschrijven is. Dan draait het platform deze keer zonder analyse; er is niets aangemaakt.
5. **De modellen**, volgens `api/app/analyse/models.lock` van de versie die klaarstaat:
   - een model dat er al is met de goede grootte en sha256, wordt overgeslagen;
   - anders: eerst kijkt het of er ruimte is (wat er nodig is plus 100 MB), dan
     `curl --retry 3 --max-filesize <bytes>` naar een tijdelijke map van root in `MODELLEN_ROOT`,
     dan de grootte en de sha256, en pas dan zet het het bestand neer (0644, van root, met een
     rename: een symlink op die plek volgt het niet, het weigert hem);
   - lukt het niet, dan is dat **niet fataal**: `Let op: <naam> kon niet geladen worden;
     gezichten wachten tot de volgende installatie.` De container start toch, en zegt
     `models_missing`;
   - een `*.onnx` die niet meer in `models.lock` staat, gaat weg (`Oud model weg: …`);
   - de licenties (`yunet-MIT.txt`, `sface-Apache-2.0.txt` en, voor zoeken op beeld,
     `siglip2-Apache-2.0.txt`) komen naast de modellen.
6. **De start**, na de app en media: `up -d --no-deps analyse`, met `--force-recreate` als er
   een model veranderde (de worker leest de modellen bij de start). Daarna
   `app.cli analyse-status --wait 20`. Lukt de start niet, of is de stand niet `ok`, dan alleen
   `Let op: …`: het platform werkt verder.
7. **De eindtekst** zegt `Gezichten (de container analyse): aan.`, of waarom niet, en hoe het
   verder kan (opnieuw proberen, uit, aan).

## De stand lezen

```bash
dc exec -T platform python -m app.cli analyse-status
```

Als alles goed is (de grenzen alleen als de kernel ze kan zetten):

```
Analyse: ok
  model face_detection_yunet_2026may.onnx (versie ebafce4e3c11): klopt
  model face_recognition_sface_2021dec.onnx (versie 0ba9fbfa01b5): klopt
  grenzen: 1 cpus, 3072 MB geheugen
  opdrachten: cancel, caps, faces
```

De eerste regel is de stand: `ok`, `off`, `down (<reden>)` of `models_missing (<modellen>)`.
Daarna, als de worker antwoordt: per model of het klopt (de versie is het begin van de sha256),
de grenzen die de container zelf ziet, en de opdrachten die de worker kent. Tot X2b en X3 klaar
zijn, zijn dat er drie; de andere (groeperen, beeld) zeggen `not_built`. De exitcode is 0 bij `ok`
en `off`, anders 1 (met `--wait N` altijd 0).

| De eerste regel | Wat het betekent | Wat je doet |
|---|---|---|
| `Analyse: off (de analyse staat uit (PLATFORM_ANALYSE_ENABLED))` | de app is gestart zonder `compose.analyse.yaml` | aanzetten met het script en `ANALYSE=aan` |
| `Analyse: down (de socket van de analyse is er niet (draait de container analyse?))` | de container draait niet, of start nog | `dc ps analyse` en `dc logs --tail 50 analyse`; daarna het script opnieuw |
| `Analyse: down (PLATFORM_ANALYSE_WORKER_KEY ontbreekt of is korter dan 32 tekens)` | de regel `ANALYSE_WORKER_KEY` in `.env` is weg of te kort | het script opnieuw: het maakt een nieuwe |
| `Analyse: down (de analyse bewijst zich niet: …)` | de app en de container hebben een andere sleutel, of iets anders luistert op de socket | het script opnieuw (het start beide met dezelfde `.env`) |
| `Analyse: down (de afscherming van de container analyse klopt niet (de zelftest faalde))` | de zelftest van de worker vond iets open (netwerk, schrijfbaar, capabilities, …); hij neemt dan niets aan | zie "De afscherming nakijken"; niet "oplossen" door de afscherming weg te halen |
| `Analyse: down (de analyse antwoordt niet binnen 5 s)` of `… weigert de verbinding` | de worker hangt of herstart | `dc logs --tail 50 analyse`; `dc restart analyse` |
| `Analyse: models_missing (een model ontbreekt of klopt niet: …)` | een model is er niet, of de bytes kloppen niet met `models.lock` | het script opnieuw; het haalt wat ontbreekt |

`docker inspect --format '{{.State.Health.Status}}' platform-analyse-1` zegt `healthy` zodra de
worker op zijn socket antwoordt; een worker die door een gefaalde zelftest alles weigert, wordt
`unhealthy`.

## De afscherming nakijken

De worker kijkt bij elke start zelf of hij afgeschermd is (de zelftest): uid 10003, geen
capabilities, `no-new-privileges`, geen netwerk behalve `lo`, `/` en `/models` niet te
beschrijven, `/data` leeg, geen map `/keys`, een bestand van de app (`/srv/zelftest-10001`) niet
te openen, de kopie van python voor de kinderen en de map van de socket. Faalt er iets, dan
luistert hij wel, maar weigert hij elke opdracht, en staat de reden in het log:

```bash
dc logs analyse | grep -F 'de afscherming faalt'
```

De zelftest los draaien, met de uitkomst als JSON (`ok`, `problems`, en per controle wat hij
zag). Altijd met `env -i`: een `dc exec` zonder krijgt de omgeving van de container, met de
sleutel erin.

```bash
boot=$(docker inspect --format '{{index .Config.Cmd 3}}' platform-analyse-1)
dc exec -T analyse /usr/bin/env -i PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin python -I -c "$boot" /srv --selftest --models /models
```

Zo los gedraaid kijkt hij niet naar de socket (die is van het proces dat al draait); de rest is
dezelfde controle. Wat hij weigert, verandert niets aan het platform: de app wacht met gezichten
tot de stand weer `ok` is.

**Nooit** `dc exec analyse` zonder `env -i`, en nooit de afscherming in `compose.analyse.yaml`
weghalen (`read_only`, `network_mode: none`, `cap_drop`, `user`): de modellen lezen beelden die
van iedereen kunnen komen.

## Uitzetten en weer aan

```bash
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/main/infra/single/install.sh | ANALYSE=uit bash
```

Geen download en geen container meer: de app start zonder `compose.analyse.yaml`, en
`--remove-orphans` haalt de container weg. De eindtekst zegt waar de modellen staan; ze blijven
daar tot je de map zelf weggooit:

```bash
rm -rf /opt/platform/modellen
```

(alleen met de analyse uit; met aan haalt het volgende script ze opnieuw). Weer aan: hetzelfde
commando met `ANALYSE=aan`. De sleutel in `.env` blijft dezelfde.

## De sleutels

- **`ANALYSE_WORKER_KEY`** in `.env`: de app en de worker bewijzen met deze sleutel aan elkaar
  dat ze de echte zijn. Hij gaat alleen via de omgeving naar de app en de container, nooit in een
  commando, een log of een foutmelding; de worker haalt hij uit zijn omgeving vóór hij iets
  anders doet, en zijn kinderen krijgen hem nooit. Hij moet anders zijn dan die van de
  mediaworker en dan het geheim van de app; anders zegt de stand `down` (`key_reused`).
- **`SLEUTELS_ROOT`** (`/opt/platform/sleutels/gezichten`, 0700, van de app): daar bewaart de app
  vanaf X2b per eigenaar de sleutel waarmee de vectoren van gezichten versleuteld zijn. Hij staat
  bewust buiten `/tank`: een snapshot van `/tank/platform` heeft de sleutels dan niet, en "uit"
  bij gezichten (de sleutel weg) werkt ook voor die snapshot. Een snapshot of back-up van de hele
  container (de rootdisk) heeft ze wel. Wat er in die map staat en hoe je hem wist, beschrijft
  X2b.

## Terug naar 0.8.0

Zoals in `docs/runbooks/zoeken.md`, "Terug naar 0.8.0 van `59e0250`", ook met de analyse aan.
Het script van 0.8.0 kent de analyse niet: het start de app zonder `compose.analyse.yaml`, en
`--remove-orphans` haalt de container weg. `.env` (met `ANALYSE=aan` en de sleutel), de modellen
en de sleutels blijven. Weer vooruit: het script van de branch, zonder `PLATFORM_SRC_REF`; de
modellen kloppen nog, dus het haalt niets opnieuw, en de container start weer.

## Zoeken op beeld (X3)

Golf 5 (X3a) bouwt de tokenizer, de omzetter van SigLIP 2 en de CI-job `beeld-model`. Op de LXC
verandert er nog niets: `install.sh` haalt SigLIP 2 niet op en zet niets om; dat komt in golf 6
(X3b, de functies voor `BEELD` in `install.sh`). Wat er gebouwd is en wat nog open staat:
[docs/fase-6/x3a.md](../fase-6/x3a.md).

### De job `beeld-model`

De job haalt het echte model (`google/siglip2-base-patch32-256`, ±1,55 GB) en de 120 foto's van de
kwaliteitsset, zet het model twee keer om en draait de poorten van Keuze 83. Hij draait alleen:

- bij een push of PR die `app/analyse/worker/omzetten/`, `app/analyse/tokenizer.py`,
  `app/analyse/onnxw.py` of `app/analyse/models.lock` verandert (`git diff --name-only`);
- of als de tekst van de PR een regel `beeld-model: ja` heeft.

Er is geen knop om hem met de hand te starten. Het model komt nooit uit een cache: elke run haalt
de bron van huggingface.co. De 120 foto's wel, zodra `beeld.tsv` er is: een cache op de sha256
van `beeld.tsv`, omdat de robotregels van Wikimedia zo weinig mogelijk downloads vragen. `set`
kijkt elke foto uit de cache na (grootte en sha256) en haalt alleen wat ontbreekt of niet klopt.
De cache wordt bewaard direct na een goede `set`, ook als de job daarna rood eindigt (zolang
`models.lock` de regels van SigLIP mist, is hij dat altijd); nooit een halve set, want een sleutel
bewaart maar één keer. Van upload.wikimedia.org haalt de job gemiddeld hooguit 2,5 MB/s (20 Mbps;
de robotregels staan 25 Mbps toe). Draait hij niet, dan zegt het blok `=== kort ===` aan het eind
van het log "beeld-model: draaien nee" met de reden.

Het blok `=== kort ===` staat aan het eind van het log, met per regel:

- `BZ1 <host>: bereikbaar (HTTP …)` of `niet bereikbaar (…)`, voor huggingface.co,
  commons.wikimedia.org, upload.wikimedia.org en images-assets.nasa.gov;
- `BZ2 …`: de commit; de tijd en maxrss van de twee omzettingen zoals de dienst (grens 1,5 GB),
  of die twee dezelfde sha256 geven en of ze kloppen met `models.lock`; en van de derde omzetting
  met `--fp32` (alleen voor BZ4) de tijd en maxrss zonder grens, en of hij dezelfde vijf bestanden
  geeft;
- `BZ4 set: 120 foto's kloppen met beeld.tsv (… uit de cache, … van Commons)`, of het voorstel;
- `FOUT <stap>: … HTTP 429 (…)` of `HTTP 5xx (…)`: de host gaf ook na zes pogingen nee; tussen
  haakjes de laatste Retry-After en de eerste regel van zijn antwoord;
- `BZ3`, `BZ4`, `BZ6`, `BZ7`: de uitkomsten van de tests, met `goed` of `FOUT`;
- `UITSLAG …`: waarom de job rood is, als er een voorstel ligt;
- per pytest-stap de laatste regel.

Het hele verslag staat bij de run als artifact `beeld-model` (`kort.txt`, `bz3.json`, `bz4.json`,
`bz6.json`, `bz7.json`, de voorstellen en de logs van de omzetter, en `uit1/meta.json`).

### Na de eerste run: de regels vastzetten

Zolang `app/analyse/models.lock` geen regels van SigLIP heeft, of
`api/tests/analyse/sets/beeld.tsv` ontbreekt, stelt de job ze voor en is hij rood met
"models.lock mist de regels van SigLIP" en "beeld.tsv ontbreekt". Dat is de bedoeling. De job zet
zelf nooit iets in het repo; een mens doet dat, zo:

1. Kijk in `=== kort ===` of BZ1 bij huggingface.co en Commons kon. Kon dat niet, dan gaat de rest
   naar de LXC (X3b: `beeld-kwaliteit`, `beeld-bench`, trust-on-first-use).
2. Kijk of BZ2 zegt "dezelfde sha256 voor alle 5 bestanden" (twee keer omzetten, en met `--fp32`)
   en of maxrss van de twee omzettingen zoals de dienst onder de 1,5 GB bleef. Zo niet: niets
   vastzetten, eerst de omzetter nakijken.
3. Haal het artifact `beeld-model` op. `voorstel-models.lock` heeft tien regels: vijf bronnen
   (`siglip2-b32-256/bron/<naam>` met de URL op één vaste commit) en vijf uitkomsten
   (`siglip2-b32-256/<naam>` met url `-`). Kijk na: de commit in de URL's is dezelfde als in het
   blok, `model.safetensors` is ±1,51 GB en `tokenizer.json` ±34,4 MB.
4. Kijk `voorstel-beeld.tsv` door: 120 foto's (12 categorieën × 5, de 6 vaste foto's van de
   combinaties, 60 andere), elk met sleutel, categorieën, licentie (CC0 of publiek domein),
   sha256, bytes, URL op upload.wikimedia.org en de bestandspagina op Commons. Staat er toch een
   herkenbaar persoon op een foto, vervang die regel door een andere foto van Commons met dezelfde
   sleutel-soort, categorie en licentie, met zijn eigen sha256 en grootte. Weglaten kan niet: de
   controle van de set eist die aantallen (`beeld_model.py`, `lees_set`; `test_beeld_sets.py`).
5. Zet in één commit:
   - de tien regels onder de regels van de gezichten in `app/analyse/models.lock`, met een
     opmerking erboven (de commit, Apache-2.0, de licentie in `licenties/`);
   - `voorstel-beeld.tsv` als `api/tests/analyse/sets/beeld.tsv`;
   - de nieuwe exacte lijst in `api/tests/analyse/test_models_lock.py`
     (`test_de_twee_modellen_van_gezichten` kent nu alleen de twee regels van de gezichten).
6. Push. De job draait dan weer (de lock is veranderd), nu streng: een andere sha256 of grootte
   is rood. Groen is BZ2 en BZ4 gehaald: dan kan de go/no-go van X3 (Keuze 73) beslist worden.

Let op bij stap 5: zet de regels pas samen met X3b (de stap `BEELD` van `install.sh`) in
`models.lock`. `install.sh` haalt de regels onder `siglip2-b32-256/` niet (de 1,51 GB van
`model.safetensors` is van die stap), maar de container meldt elke regel van `models.lock`: zonder
de stap `BEELD` maakt niemand de omzetting, en zegt `/beheer` dat er modellen ontbreken
(`models_missing`; gezichten werken wel). `beeld_model.py uitslag` zegt het ook (x3a.md, Open 1).

### De omzetter met de hand draaien (alleen om te testen)

In de repo, met de extra analyse (`UV_PROJECT_ENVIRONMENT=.venv-analyse uv sync --frozen --extra
analyse`), en een map met de vijf bronbestanden:

```bash
cd api
BOOT="import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('app.analyse.worker.omzetten.siglip',run_name='__main__',alter_sys=True)"
UV_PROJECT_ENVIRONMENT=.venv-analyse uv run --no-sync python -I -c "$BOOT" "$PWD" --bron /pad/naar/bron --uit /lege/map
```

Dat is dezelfde opdrachtregel als die van de dienst `analyse-omzetten` (`OMZET_BOOT`) en van de
job.

Op stdout komen de regels voor `models.lock`, op stderr de voortgang, de tijd en maxrss. Code 2
met één zin is een bron of map die niet klopt (de uitvoermap blijft leeg); code 1 is een fout in
de omzetter.

## Wat er nog niet is

- het vinden van gezichten in de app, de voorstellen, `/beheer` en `app.cli analyse-bench` en
  `analyse-kwaliteit` (X2b; `analyse-bench` zegt tot dan welk pakket hem bouwt);
- zoeken op beeld op de LXC: SigLIP 2 ophalen en omzetten met `install.sh` (de dienst
  `analyse-omzetten`), de opdrachten `embed_image`, `embed_text`, `rank` en `forget`, en
  `beeld-status` (X3b). De omzetter zelf en de job `beeld-model` zijn er (X3a, hierboven).
