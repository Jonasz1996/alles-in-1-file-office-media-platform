# Runbook: de analyse (gezichten en zoeken op beeld)

De analyse is het deel van het platform dat met modellen naar foto's kijkt: gezichten vinden
(fase 6, X2) en zoeken op wat er op een foto staat (X3). Het rekenen gebeurt in een eigen
container, `platform-analyse-1`: zonder netwerk, alleen-lezen, als een eigen gebruiker (uid
10003), zonder capabilities. De app geeft hem per foto alleen de preview (als bestand dat al open
is, nooit een pad), en krijgt kaders en getallen terug. Wat de analyse is en hoe je hem aan- en
uitzet, staat in `infra/single/README.md` (Gezichten en zoeken op beeld: de container analyse).
Dit runbook gaat over
wat het script doet, de stand, de afscherming, uitzetten, de modellen, de sleutels en terug naar
0.8.0; het deel over zoeken op beeld (X3) staat onder "Zoeken op beeld (X3)".

**Sinds 0.8.1 staat de analyse standaard aan.** Zonder `ANALYSE` in `/opt/platform/.env` zet het
script `ANALYSE=aan` erbij, haalt het de modellen op en start het de container. Voor elke
gebruiker staan gezichten en zoeken op beeld daarna nog uit, tot hij ze zelf aanzet bij Account →
Foto's. Liever zonder: `ANALYSE=uit` (hieronder, "Uitzetten en weer aan"); dan doet het script wat
0.8.0 deed. Wat gezichten doen en bewaren: [gezichten.md](gezichten.md).

Alles hieronder gebeurt in de Console van de container (de LXC), als root. `dc` is de functie uit
de README (Handige commando's): `docker compose` met precies de bestanden van het
installatiescript; staat de container analyse er, dan ook `compose.analyse.yaml` en de grenzen.
Het installatiescript is steeds de nieuwste versie:

```bash
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/claude/stappenplan-uitwerken-lu4uv3/infra/single/install.sh | PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 bash
```

Hieronder heet dat kortweg "het script"; een instelling zet je ervoor, na `PLATFORM_BRANCH`,
zoals `curl … | PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 ANALYSE=uit bash`. Zolang 0.8.1
niet op `main` staat, hoort `PLATFORM_BRANCH` er altijd bij, zoals in `infra/single/README.md`.

## Wat het script bij elke run met de analyse doet

1. Het leest `ANALYSE` uit de omgeving vóór `/opt/platform/.env`. Alleen `aan` of `uit`; iets
   anders stopt het script meteen, vóór er iets verandert.
2. Staat er geen `ANALYSE` in `.env` en vraag je er niet om, dan geldt de standaard: sinds 0.8.1
   `aan`. (Tot en met 0.8.0 was dat uit, en hield het hier op: geen stap "Analyse", geen regel in
   de eindtekst.)
3. Bij `aan` komen in `.env` (rechten 600), als ze er nog niet zijn: `ANALYSE=aan`,
   `ANALYSE_WORKER_KEY` (64 tekens, één keer gemaakt en nooit vervangen, ook niet na uit en weer
   aan), `MODELLEN_ROOT=/opt/platform/modellen`, `SLEUTELS_ROOT=/opt/platform/sleutels/gezichten`
   en `BEELD=aan`. Een bestaande waarde blijft altijd staan. Met `ANALYSE=uit` komt of verandert
   alleen die regel; hij blijft daarna staan, ook als een volgende run geen `ANALYSE` meegeeft.
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
   - is er minder dan 105 MB vrij in `DATA_ROOT`, dan zegt het `Let op: weinig ruimte in
     <DATA_ROOT> voor de gezichten (105 MB aangeraden; ±90 MB per 100.000 gezichten). De analyse
     gaat door.` Het is een waarschuwing, geen stop;
   - de licenties (`yunet-MIT.txt`, `sface-Apache-2.0.txt` en, voor zoeken op beeld,
     `siglip2-Apache-2.0.txt`) komen naast de modellen.
6. **De start**, na de app en media: `up -d --no-deps analyse`, met `--force-recreate` als er
   een model veranderde (de worker leest de modellen bij de start). Daarna
   `app.cli analyse-status --wait 20`. Lukt de start niet, of is de stand niet `ok`, dan alleen
   `Let op: …`: het platform werkt verder.
7. **De eindtekst** zegt `Gezichten (de container analyse): aan; elke gebruiker zet het zelf aan
   (Account → Foto's).`, of waarom niet, en hoe het verder kan (opnieuw proberen, uit, aan).

## De stand lezen

```bash
dc exec -T platform python -m app.cli analyse-status
```

Als alles goed is, met het model van zoeken op beeld (de grenzen alleen als de kernel ze kan
zetten):

```
Analyse: ok
  model face_detection_yunet_2026may.onnx (versie ebafce4e3c11): klopt
  model face_recognition_sface_2021dec.onnx (versie 0ba9fbfa01b5): klopt
  model siglip2-b32-256/beeld.int8.onnx (versie ddc76fc08a1c): klopt
  model siglip2-b32-256/tekst.int8.onnx (versie 258337dbc659): klopt
  model siglip2-b32-256/tokenizer.bin (versie 1516dde1739c): klopt
  model siglip2-b32-256/meta.json (versie dfb3d153f941): klopt
  model siglip2-b32-256/siglip2-Apache-2.0.txt (versie 0d542e0c8804): klopt
  grenzen: 1 cpus, 3072 MB geheugen
  opdrachten: cancel, caps, embed_image, embed_text, faces, forget, group, rank
```

De eerste regel is de stand: `ok`, `off`, `down (<reden>)` of `models_missing (<modellen>)`.
Daarna, als de worker antwoordt: per model of het klopt (de versie is het begin van de sha256,
in de volgorde van `models.lock`), de grenzen die de container zelf ziet, en de opdrachten die
de worker kent. Dat zijn er acht: `caps` en `cancel`, `faces` en `group` voor gezichten, en
`embed_image`, `embed_text`, `rank` en `forget` voor zoeken op beeld. De vijf regels onder
`siglip2-b32-256/` staan er pas als die map er is, dus na de omzetting (met `BEELD=uit` nooit);
de bronnen van de omzetting nooit. De exitcode is 0 bij `ok` en `off`, anders 1 (met `--wait N`
altijd 0).

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

**Het geheugen op de LXC.** De container mag hooguit 3 GB (`mem_limit: 3g`). Na een paar dagen
werk op de achtergrond (de eerste ronde van gezichten en beeld) zie je zo hoeveel hij op zijn
hoogst gebruikte, en of de kernel er een proces van stopte [niet getest in de sandbox: daar
draait de container zonder die grens]:

```bash
docker inspect --format '{{.State.OOMKilled}}' platform-analyse-1
dc exec -T analyse /usr/bin/env -i /bin/cat /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory.events
```

`OOMKilled` hoort `false` te zijn. `memory.peak` is de piek in bytes sinds de start van de
container; onder 2,5 GB is de poort van het ontwerp gehaald (BZ5). In `memory.events` hoort
`oom_kill 0` te staan. Zie je iets anders, zeg het dan tegen de ontwikkelaar, met die drie
getallen.

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
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/claude/stappenplan-uitwerken-lu4uv3/infra/single/install.sh | PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 ANALYSE=uit bash
```

Geen download en geen container meer: de app start zonder `compose.analyse.yaml`, en
`--remove-orphans` haalt de container weg. De eindtekst zegt waar de modellen staan; ze blijven
daar tot je de map zelf weggooit:

```bash
rm -rf /opt/platform/modellen
```

(alleen met de analyse uit; met aan haalt het volgende script ze opnieuw). Weer aan: hetzelfde
commando met `ANALYSE=aan`. De sleutel in `.env` blijft dezelfde. `ANALYSE=uit` blijft in `.env`
staan: ook een volgende run zonder `ANALYSE` laat de analyse uit. Het script zegt dan
`Analyse: uit (ANALYSE=uit in /opt/platform/.env)`.

**Uit nadat hij aan stond.** De app doet dan weer wat 0.8.0 deed. De routes van gezichten en van
zoeken op beeld geven `501` (zoals de lege routes van 0.8.0), de schakelaars bij Account → Foto's
verdwijnen, en in het antwoord voor `/beheer` zijn de delen voor gezichten en beeld leeg (`null`).
De web zegt op `/beheer` dat gezichten en zoeken op beeld niet draaien ("de analyse staat uit, of
is niet helemaal geïnstalleerd"), en wijst naar de regel **Gezichten (de container analyse)** in de
eindtekst van het installatiescript: die zegt waarom (`uit (ANALYSE=uit)`, of `uit, want …`). Een
gewone gebruiker ziet alleen dat zoeken op beeld uitstaat op deze server en dat de beheerder het
kan aanzetten, zonder pad of variabele. Dezelfde lege blokken zie je ook met `ANALYSE=aan` als de
analyse toch niet draait: op PostgreSQL, als het script een map weigerde, of zonder de map met
sleutels van gezichten. Dan helpt `ANALYSE=aan` niet; de regel in de eindtekst zegt wat wel. Uitzetten wist niets: de gezichten, de vectoren van beeld en de sleutels
blijven staan, en zijn er weer zodra de analyse weer aan staat. Wil je ze echt kwijt, wis ze dan
eerst met de analyse aan (per gebruiker "uit" bij Account → Foto's, of op `/beheer`
"Gezichtsherkenning toestaan" uit en "Beeldindex wissen") en zet de analyse daarna pas uit.

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
  container (de rootdisk) heeft ze wel. Wat er in die map staat, hoe je hem wist en hoe je hem
  ook uit een snapshot van de container houdt: [gezichten.md](gezichten.md).

## Terug naar 0.8.0

Zoals in `docs/runbooks/zoeken.md`, "Terug naar 0.8.0 van `59e0250`", ook met de analyse aan.
Het script van 0.8.0 kent de analyse niet: het start de app zonder `compose.analyse.yaml`, en
`--remove-orphans` haalt de container weg. `.env` (met `ANALYSE=aan`, `BEELD=aan` en de sleutel),
de modellen en de sleutels blijven. Weer vooruit: het script van de branch, zonder
`PLATFORM_SRC_REF`; de modellen kloppen nog, dus het haalt niets opnieuw (ook de 1,55 GB van
zoeken op beeld niet), en de container start weer. `db-downgrade 0010` haalde de beeldindex en de
gezichten weg: zoeken op beeld staat daarna voor iedereen uit, en wie gezichten aan had, krijgt een
nieuwe scan (zie [gezichten.md](gezichten.md), "Terug naar 0.8.0").

## Zoeken op beeld (X3)

Golf 5 (X3a) bouwde de tokenizer, de omzetter van SigLIP 2 en de CI-job `beeld-model`. Golf 6
(X3b) bouwde de rest: het model op de LXC, de opdrachten in de container, het zoeken in de app, de
web, het blok op `/beheer` en `beeld-status`, `beeld-bench` en `beeld-kwaliteit`. Wat er gebouwd
is en wat nog open staat: [docs/fase-6/x3a.md](../fase-6/x3a.md) en
[docs/fase-6/x3b.md](../fase-6/x3b.md).

Zoeken op beeld zit in 0.8.1 (Keuze 85), en staat met de analyse aan; per gebruiker staat het uit
tot hij het zelf aanzet. Met de analyse uit kent de app het niet (elke route van beeld geeft
`501`, zoals in 0.8.0), en de web toont wat 0.8.0 toonde.

### Het model op de LXC (`BEELD`, X3b)

Alleen met de analyse aan (sinds 0.8.1 de standaard): dan zet `install.sh` één keer `BEELD=aan`
in `.env`, en haalt het na de start van de app het model op en zet het om (Keuze 84). Dat gebeurt
één keer: staat het model er al goed, dan haalt een volgende run niets. Met `ANALYSE=uit` of met
`BEELD=uit` doet het niets: geen download, geen omzetting, geen map, geen regel.

1. Eerst de ruimte (`df`): in `MODELLEN_ROOT` de bron en twee keer de uitkomst plus 100 MB
   (±2,44 GB), in `DATA_ROOT` 1 GB voor de vectoren en de cache. Te weinig: `Let op: te weinig
   ruimte in …`.
2. De vijf bronbestanden (±1,55 GB, `huggingface.co` op een vaste commit) naar een eigen map
   `MODELLEN_ROOT/.beeld.XXXXXX/bron` van root, elk met `--max-filesize` en dan de grootte en
   sha256 van `models.lock`.
3. De dienst `analyse-omzetten` (`docker compose --profile omzetten run --rm --no-deps -T
   analyse-omzetten`): uid 10004, zonder netwerk, zonder sleutel, zonder `analyse-run` en zonder
   `/models`; de bron alleen-lezen, de uitvoer in een lege map van 10004. Een paar minuten.
4. Root kopieert de vijf bestanden van de uitkomst (nooit een symlink of iets anders dan een
   gewoon bestand) en kijkt elke kopie na op grootte en sha256. Pas dan gaat de nieuwe map in één
   keer op de plek van `MODELLEN_ROOT/siglip2-b32-256` (0755, de bestanden 0644, van root), en
   start de container analyse opnieuw (`--force-recreate`).
5. De bron en de tijdelijke map gaan weg, ook als iets mislukte.

Een mislukking is nooit fataal: `Zoeken op beeld: het model is niet geladen. De volgende run
probeert het opnieuw.`, en de rest (ook gezichten) werkt gewoon; de eindtekst zegt hoe je het
opnieuw probeert. Staat het model er al goed, dan zegt het script `Zoeken op beeld: het model
staat er al (…)` en haalt het niets. Een symlink of iets anders dan een map op de plek van
`siglip2-b32-256`, of een symlink op de plek van een bestand erin, raakt het niet aan.

Uit: `curl … | BEELD=uit bash`. Dat komt in `.env` en blijft: ook een volgende run zonder `BEELD`
haalt niets meer op. Een model dat er al staat, blijft staan (gooi `MODELLEN_ROOT/siglip2-b32-256`
zelf weg als je de ruimte terug wilt). Aan: `curl … | BEELD=aan bash`. Elke gebruiker zet zoeken op
beeld daarna zelf aan bij Account → Foto's.

### De job `beeld-model`

De job haalt het echte model (`google/siglip2-base-patch32-256`, ±1,55 GB) en de 120 foto's van de
kwaliteitsset, zet het model twee keer om en draait de poorten van Keuze 83. Hij draait alleen:

- bij een push of PR die `app/analyse/worker/omzetten/`, `app/analyse/tokenizer.py`,
  `app/analyse/onnxw.py` of `app/analyse/models.lock` verandert (`git diff --name-only`), of wat
  de job zelf meet: `tools/beeld/`, de tests met marker `beeld_echt` en hun lijsten in
  `api/tests/analyse/sets/` (`beeld_tokens.tsv`, `beeld_vragen.tsv`, `beeld.tsv` en
  `beeld_niet.tsv`; de paden staan in `ci.yml`, in de stap "Moet beeld-model draaien?");
- of als de tekst van de PR een regel `beeld-model: ja` heeft.

Er is geen knop om hem met de hand te starten. Het model komt nooit uit een cache: elke run haalt
de bron van huggingface.co. De 120 foto's wel, zodra `beeld.tsv` er is: een cache op de sha256
van `beeld.tsv`, omdat de robotregels van Wikimedia zo weinig mogelijk downloads vragen. `set`
kijkt elke foto uit de cache na (grootte en sha256) en haalt alleen wat ontbreekt of niet klopt.
De cache wordt bewaard direct na een goede `set`, ook als de job daarna rood eindigt; nooit een
halve set, want een sleutel bewaart maar één keer. Elke foto komt als thumbnail van 1280 px
breed, niet als origineel (op de originelen gaf upload.wikimedia.org een 429 met Retry-After
600; x3a.md, "CI op 9ebf298"). De thumbnails staan sinds september 2026 op thumb.wikimedia.org
(volgens bronnen uit de tweede hand;
de oude URL's op upload.wikimedia.org werken nog; x3a.md, "CI op 45bb926"); `beeld.tsv` heeft de
URL zoals de API hem gaf. Is het origineel niet breder dan 1280, dan is het origineel zelf de
thumbnail, en dat staat altijd op upload.wikimedia.org. Van thumb.wikimedia.org en van
upload.wikimedia.org haalt de job elk gemiddeld hooguit 2,5 MB/s (20 Mbps; de robotregels staan
25 Mbps toe). Elke foto
vraagt hij als JPEG (`Accept: image/jpeg`: thumb.wikimedia.org geeft op dezelfde URL ook WebP), en
hij kijkt de eerste bytes na; is het geen JPEG, dan stopt `set` met `FOUT set: <sleutel>.jpg: WebP
in plaats van JPEG` of `…: geen JPEG`, en die foto staat niet in `set/`. Een omleiding van een
vraag aan Commons volgt de job alleen naar https op commons.wikimedia.org, thumb.wikimedia.org of
upload.wikimedia.org; een andere is een fout (`HTTP 302 naar <host>, niet gevolgd (…)`), en
daarheen gaat geen vraag. De API van Commons
(commons.wikimedia.org) krijgt hooguit één vraag per 8 s, en ook een nieuwe poging na een 429
telt: zonder contact in de User-Agent staat Wikimedia volgens wie de grenzen citeert (september
2026) 10 vragen per minuut per IP toe (x3a.md, "CI op 3148c2f"). Een voorstel doet ±90 tot 200
vragen aan de API en duurt daardoor ±12 tot 27 minuten; is de eerste keus van een zoekopdracht op
(zie stap 4), dan leest hij nog pagina's tot het uiterste van vroeger, 3 vragen per pagina, en
hooguit 750 vragen samen (x3a.md, "De set strenger"). Met `beeld.tsv` vraagt `set` de API niets.
Thumb en upload krijgen elk hooguit één vraag per seconde.
Na een 429 of 503 vraagt de job nooit eerder opnieuw dan Retry-After zegt (seconden, ook met
decimalen, of een HTTP-datum). Zegt die meer dan 600 s, is hij niet te lezen, of past het wachten
niet meer in 900 s per stap (ook de eigen 2 tot 32 s na een 5xx tellen), dan stopt hij meteen,
met een fout die dat zegt. Draait hij niet, dan zegt het blok `=== kort ===` aan het eind van het
log "beeld-model: draaien nee" met de reden.

Het blok `=== kort ===` staat aan het eind van het log, met per regel:

- `BZ1 <host>: bereikbaar (HTTP …)` of `niet bereikbaar (…)`, voor huggingface.co,
  commons.wikimedia.org, thumb.wikimedia.org, upload.wikimedia.org en images-assets.nasa.gov.
  Leidt de host om, dan staat erbij waarheen en wat daar het antwoord was (bijvoorbeeld `HTTP 301
  naar commons.wikimedia.org, daar HTTP 200`), of dat de job hem niet volgde (`HTTP 302 naar
  <host>, niet gevolgd (…)`);
- `BZ2 …`: de commit; de tijd en maxrss van de twee omzettingen zoals de dienst (grens 1,5 GB),
  of die twee dezelfde sha256 geven en of ze kloppen met `models.lock`; en van de derde omzetting
  met `--fp32` (alleen voor BZ4) de tijd en maxrss zonder grens, en of hij dezelfde vijf bestanden
  geeft;
- `BZ4 set: 120 foto's kloppen met beeld.tsv (… uit de cache, … van Commons, … als origineel;
  … MB in … s; API: … vragen, … keer 429; thumb: … vragen, … keer 429; upload: … vragen, … keer
  429; contact: ja)`, of het voorstel (`… (… als origineel; … MB in … s; API: …)`): wat er van
  Commons kwam, hoeveel daarvan als origineel in plaats van als thumbnail, hoe lang dat duurde, en
  het verkeer met Commons in die stap: per dienst, altijd in de volgorde API (de zoekvragen aan
  commons.wikimedia.org), thumb (thumb.wikimedia.org: de thumbnails) en upload
  (upload.wikimedia.org: de originelen, en thumbnails in de oude vorm), de vragen, elke poging en
  elke gevolgde omleiding naar die dienst meegeteld, hoe vaak het antwoord 429 was (en `… keer
  5xx` als dat er waren), na hoeveel seconden de eerste 429 kwam (`eerste 429 na … s`), en of de
  User-Agent een contact had (nooit welk). Een dienst zonder vragen staat er als `upload: 0
  vragen`, één vraag als `upload: 1 vraag`;
- `BZ4 set: Commons: maar … bruikbare foto's voor <categorie> (… kandidaten bekeken; afgevallen:
  …)`, `geen bruikbare foto voor <a>+<b> (…)` of `… voor <onderwerp> (anders; … bekeken;
  afgevallen: …)`, en bij een tekort aan combinaties `BZ4 reserve <a>+<b>: … bruikbare foto's
  (eerste pagina per licentie; afgevallen: …)`: een tekort, met waarom de kandidaten afvielen.
  Hooguit de vier vaakste redenen met hun aantal, de meeste eerst (bij een gelijk aantal op de
  tekst), en de rest als `… om een andere reden`. De redenen: `geen image/jpeg`, `origineel niet
  op upload`, `geen bestandspagina`, `te klein`, `te groot`, `mens erop`, `licentie`, `geen
  thumbnail van 1280 px`, `thumbnail op een andere host (<host>)`, `te langgerekt`, `onleesbare
  gegevens`, `al gekozen`, `uitgesloten (beeld_niet.tsv)` (de bron staat in
  `api/tests/analyse/sets/beeld_niet.tsv`; stap 4) en `beeldt een categorie af`. De host staat
  er alleen als het een gewone hostnaam is van hooguit 64 tekens, anders `een onbekende host`;
  nooit een URL. Zeggen
  bijna alle kandidaten `thumbnail op een andere host`, dan geeft de API de thumbnails op een
  host die de job niet kent: kijk in x3a.md ("CI op 45bb926") en pas `THUMB` in
  `tools/beeld/beeld_model.py` aan, alleen naar een host van Wikimedia (`*.wikimedia.org`):
  `THUMB` krijgt ook het contact (`BEELD_CONTACT`) en is een toegestaan doel van omleidingen. Een
  andere host vraagt eerst toestemming van Jonas;
- `BZ4 set: … tweede keus (<zoekopdracht> …, …); de eerste keus was op; het bewijs per foto staat
  in het log`: alleen als een voorstel foto's van de tweede keus nam (stap 4), met per
  zoekopdracht hoeveel;
- `BZ4 set: de eerste keus gaf … tekorten, de volgorde van Commons …: dit voorstel volgt de
  volgorde van Commons, met … tweede keus (…); het bewijs per foto staat in het log, na deze
  regel`: de eerste keus gaf een tekort (een eerste keus van de ene zoekopdracht kan een volgende
  een foto afnemen), en opnieuw kiezen in de volgorde van Commons, zoals vóór de eerste keus maar
  met `beeld_niet.tsv`, gaf er minder. Het voorstel volgt dan die keuze; zonder tweede keus valt
  `, met … tweede keus (…)` weg. Bij evenveel tekorten blijft de eerste keus (x3a.md, "De
  review");
- `BZ4 set: beeld_niet.tsv: … foto's op de lijst, als kandidaat overgeslagen: …`: alleen als die
  lijst er is; hoeveel foto's erop staan en hoeveel daarvan het voorstel tegenkwam en oversloeg;
- `FOUT set: beeld_niet.tsv regel …: …`: de lijst klopt niet (de kop, een bron die geen
  bestandspagina van Commons is, een reden die niet bestaat, een lege of te lange `gezien`, een
  dubbele bron; ook `FOUT set: beeld_niet.tsv: geen UTF-8`, en `FOUT set: beeld_niet.tsv: <pad>
  is er niet (--niet)` als een lijst die met `--niet` is opgegeven niet bestaat; zonder `--niet`
  mag de standaardlijst ontbreken); `set` vraagt dan niets aan Commons.
  `FOUT set: beeld.tsv: regel … (<sleutel>) staat in beeld_niet.tsv regel …
  (<reden>); …`: een vaste foto staat op de lijst; `set` haalt dan niets op;
- `FOUT <stap>: … HTTP 429 (…)` of `HTTP 5xx (…)`: de host gaf ook na zes pogingen nee, of vroeg
  langer te wachten dan mag; tussen haakjes de laatste Retry-After, waarom de job meteen stopte
  (als dat zo was) en de reden uit zijn antwoord: de titel, of als die alleen "Wikimedia Error"
  zegt (de foutpagina van Wikimedia) de eerste alinea, anders de eerste regel tekst; alleen
  afdrukbare ASCII en hooguit 160 tekens. Herhaalt het antwoord de User-Agent met
  `BEELD_CONTACT`, dan staat daar `[contact]`. Bij `set` staat erachter hoe ver hij kwam, wat hij
  ophaalde en het verkeer met Commons, bijvoorbeeld `(bij foto 37 van 120, een thumbnail van
  1280 px, 12,3 MB opgehaald; API: 111 vragen, 0 keer 429; thumb: 36 vragen, 0 keer 429; upload:
  1 vraag, 0 keer 429; contact: ja)` of `(…, een origineel, …)`. Zegt de API `… keer 429` bij
  weinig vragen en een `eerste 429 na` van een paar seconden, dan was het budget van het IP van
  de runner al op voordat de job begon (ander verkeer op dat IP);
- `BZ3`, `BZ4`, `BZ6`, `BZ7`: de uitkomsten van de tests, met `goed` of `FOUT`;
- `BZ4 … (rapport)`: regels zonder poort. Ze veranderen nooit een uitkomst, en mislukt er een, dan
  zegt hij `… (rapport): mislukt (<soort>)`. Waar int8 het verlies maakt (x3a.md, "CI op
  3a9b07d", met hoe je ze leest), uit de stap BZ4, in `bz4.json` onder `int8_rapport`:
  - `BZ4 torens (rapport)`: precisie@5 met de tekst in int8 en de beelden in fp32, en omgekeerd;
  - `BZ4 fp32 (rapport)`: engels, meer zoals deze, MIN_FOTO, MIN_TEKST en de plekken van de
    combinaties, nu in fp32;
  - `BZ4 cos int8 tegen fp32 (rapport)`, `BZ4 één per keer (rapport)` (elke vraag en foto alleen,
    zoals de dienst) en `BZ4 buren uit dezelfde batch (rapport)` (tegen toeval);
  - `BZ4 nl per vraag (rapport, int8/fp32/tekst-fp32/beeld-fp32) 1/3: …` (drie regels) en `BZ4 en
    per vraag (rapport, int8/fp32): …`;
  - sinds CI op 011bf2d, elk in een eigen try (een fout is `BZ4 <deel> (rapport): mislukt
    (<soort>)`, de andere delen gaan door): `BZ4 meer zoals deze per categorie (rapport,
    int8/fp32) 1/n: strand …/… (fout int8: stad …, boot …, anders-brug …; fp32: …) | …`
    (drie categorieën per regel, dus bij de twaalf categorieën van de set vier regels; per
    categorie het deel van de top 4 in int8 en fp32, en waar de foute buren het vaakst vandaan
    komen: een categorie, of bij een afleider de naam zoals `anders-vogel`; een combinatie telt
    bij allebei), `BZ4 zwakke vragen (rapport) 1/n: bergen
    (nl; int8 …/5, fp32 …/5): int8 <vijf sleutels>; fp32 <vijf sleutels>` (elke vraag in nl of
    en met hooguit 1 goede foto in de top 5 in int8 of fp32, de zwakste eerst, met de sleutels
    van de top 5; hooguit 24 regels en dan `nog …, niet uitgeschreven`; zonder zwakke vraag één
    regel `geen`) en `BZ4 meer zoals deze top 1 (rapport): int8 … (…/…), fp32 …` (hoe vaak de
    naaste buur een categorie deelt). In `bz4.json`: `meer_zoals_per_categorie`,
    `zwakke_vragen` (`aantal` en hooguit 24 `vragen`) en `meer_zoals_top1`;

  en uit de eigen stap `BZ4 rapport int8 per laag en varianten (geen poort)`, in `bz4r.json` (die
  stap mag falen, heeft hooguit 15 minuten en de uitslag leest hem niet):
  - `BZ4 cpu (rapport): <model>; vnni ja|nee`;
  - `BZ4 gewicht int8 <toren> (rapport)`: de fout van de gewichten per Linear, met |q| ≤ 64 en
    met 127;
  - `BZ4 invoer <toren> (rapport; …)`: de drie Linears met de grootste fout per tensor, met max/rms,
    het token en het kanaal van de max, en de fout per tensor in de batch, per tensor alleen, per
    token en per token na glad;
  - `BZ4 afwijking per laag <toren> (rapport)`: cos tussen int8 en fp32 na elke laag;
  - `BZ4 variant <naam> (rapport)`: nl (en hoeveel die zakt tegen fp32), engels, combinaties, meer
    zoals deze, cos, en de grootte en tijd van de grafen. `fp32`, `zoals nu` (wat de job omzet:
    sinds `VERSIE` 3 per token) en `per tensor (v2)` (de vorm van `VERSIE` 2), en bovenop per
    token: `fc2 fp32`, `mlp fp32`, `aandacht fp32`, `kop fp32`, `laag 0-3 fp32`, `laag 8-11 fp32`,
    `per token + glad` en `embedding fp32` (sinds CI op 011bf2d; `per token`, `per token + kop
    fp32` en `Q_MAX 127` zijn weg). Elke variant meet één plek: `fc2 fp32`, `mlp fp32` en
    `aandacht fp32` alleen in de lagen van de encoder, de koppen alleen in `kop fp32`. Na 8
    minuten `overgeslagen: tijd`; een variant die faalt `mislukt (<soort>)`, en de volgende gaat
    door;
- `UITSLAG …`: waarom de job rood is, als er een voorstel ligt;
- per pytest-stap de laatste regel (`bz3`, `bz4`, `bz4r`, `bz6`).

Het hele verslag staat bij de run als artifact `beeld-model` (`kort.txt`, `bz3.json`, `bz4.json`,
`bz4r.json`, `bz6.json`, `bz7.json`, de uitvoer van pytest (`bz3.txt`, `bz4.txt`, `bz4r.txt`,
`bz6.txt`), de voorstellen en de logs van de omzetter, en `uit1/meta.json`). Alleen in
het log, niet in het blok: aan het begin van `set` de regel `BZ4 set: de API van Commons hooguit
één vraag per 1 s, thumb één per 1 s, upload één per 1 s; contact: ja` (in de CI, met de link naar
het repository als contact; zonder contact `… per 8 s, …; contact: nee`). Bij een voorstel ook,
per zoekopdracht, `BZ4 set: <zoekopdracht>: … van … gekozen, … tweede keus (… kandidaten bekeken
op … pagina's; geen eerste keus: … kunstwerk, … collectie)`: hoeveel bruikbare kandidaten er
tweede keus waren en waarom (`kunstwerk`, `collectie`, `persoon` of `sjablonen onvolledig`, zoals
bij een tekort), en per gekozen foto `BZ4 set: bewijs <sleutel>: eerste keus; kunstwerk nee,
collectie nee, persoon nee, eigen werk ja, prominent ja, P180 3`: eerste of tweede keus, en waar
elk teken uit blijkt (een eigenschap als `P6243` of `P195`, een waarde als `P31 Q3305213` of
`P180 Q8441`, of een sjabloon als `Artwork` of `Personality rights`). `eigen werk` is P7482 = eigen
werk; `prominent` zegt of de categorie van de zoekopdracht op P180 de rang preferred heeft (bij
een combinatie `1 van 2` als één van de twee); `P180 …` is hoeveel dingen er volgens Commons op
staan. Gaf de eerste keus een tekort, dan kiest `set` opnieuw in de volgorde van Commons, met
per zoekopdracht `BZ4 set: volgorde van Commons, <zoekopdracht>: … van … gekozen, … tweede keus
(…)`, en daarna `BZ4 set: de volgorde van Commons gaf …: het voorstel houdt de eerste keus`, of
(als die keuze minder tekorten gaf) de regel uit het blok hierboven en het bewijs per foto van
die keuze. Alleen ids en namen uit de vaste lijsten van de tool, nooit tekst van Commons. Daarmee
kijk je stap 4 sneller na.

### Een contact in de User-Agent (`BEELD_CONTACT`, in de CI aan)

Zonder contact is de User-Agent van de tool algemeen: `beeld-model-meting/1.0 (CI van een
zelf-gehost fotoplatform)`, zonder persoon, e-mail of repository. Wikimedia telt zo'n client als
niet geïdentificeerd: 10 vragen per minuut aan de API, per IP (zo geciteerd in september 2026;
x3a.md, "CI op 3148c2f"). Dan vraagt `set` de API hooguit één keer per 8 s. Met contactgegevens
in de User-Agent, zoals de richtlijn van Wikimedia voor de User-Agent vraagt, is de grens 200 per
minuut; dan vraagt de tool de API hooguit één keer per seconde.

`tools/beeld/beeld_model.py` stuurt dat contact mee als de omgevingsvariabele `BEELD_CONTACT`
gezet is, en alleen naar Commons (de API, thumb.wikimedia.org en upload.wikimedia.org; Hugging Face
en andere hosts nooit). Ook na een omleiding: de User-Agent komt per stap opnieuw, en een omleiding
van een vraag aan Commons naar een andere host volgt de tool niet.

**Zo staat het nu.** Jonas vond op 2026-11-15 goed dat de CI de link naar zijn openbare
repository als contact naar Wikimedia stuurt, en alleen dat (geen e-mail, geen naam). Daarom
zet `.github/workflows/ci.yml` in de job `beeld-model` op de twee stappen die Commons vragen,
`BZ1 bereik per host` en `BZ4 de 120 foto's (beeld.tsv, of een voorstel uit Commons)`, elk in een
eigen `env:`:

```yaml
- name: BZ1 bereik per host
  if: steps.poort.outputs.draaien == 'ja'
  env:
    BEELD_CONTACT: ${{ github.server_url }}/${{ github.repository }}
  run: python3 -I ../tools/beeld/beeld_model.py bereik --werk "$WERK"
```

Niet op de job: dan staat het bovenaan elke stap van de job in het log, en kijkt elke stap van
`beeld_model.py` het na, zodat een fout erin ook `bron`, `vergelijk` en `uitslag` tegenhoudt.
`test_alleen_bz1_en_bz4_set_geven_wikimedia_de_link_naar_het_repository` in
`api/tests/analyse/test_beeld_ci.py` eist precies dit: die waarde, op die twee stappen, nergens
anders (niet op een job, niet uit een secret, niet in `run` of `with`).

Regels voor de waarde: een https://-URL of een e-mailadres, alleen afdrukbare ASCII, zonder `;`,
`(` of `)`, hooguit 200 tekens. Iets anders geeft bij de start van de stap een fout (`FOUT set:
BEELD_CONTACT: …`, zonder de waarde), en dan gaat er niets weg. Iets anders dan de link naar het
repository (bijvoorbeeld een e-mailadres) vraagt eerst opnieuw toestemming van Jonas, en hoort
dan in een secret, niet in `ci.yml`: `BEELD_CONTACT: ${{ secrets.BEELD_CONTACT }}`; de test
hierboven gaat dan bewust mee om.

De tool schrijft het contact nooit in het log of in het verslag, alleen `contact: ja` of
`contact: nee`; herhaalt een foutpagina de User-Agent, dan staat daar `[contact]`. GitHub zelf
toont de waarden onder `env:` wel bovenaan die twee stappen in het log; voor de link naar een
openbaar repository is dat geen probleem. Uitzetten: de twee regels weer weghalen (en de test
mee aanpassen); zonder `BEELD_CONTACT`, of met een lege, gaat er geen contact mee en vraagt `set`
de API weer hooguit één keer per 8 s.

### Na de eerste run: de regels vastzetten

Zolang `app/analyse/models.lock` geen regels van SigLIP heeft, of
`api/tests/analyse/sets/beeld.tsv` ontbreekt, stelt de job ze voor en is hij rood met
"models.lock mist de regels van SigLIP" en "beeld.tsv ontbreekt". Dat is de bedoeling. De job zet
zelf nooit iets in het repo; een mens doet dat, zo: (Sinds golf 6 staan beide er: de regels van
`VERSIE` 3 in `c921c66`, `beeld.tsv` uit de run op `502f4c3` in `d1c29e0`. Deze stappen gelden
weer na een nieuwe omzetter of een nieuwe set.)

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
   sha256, bytes, de URL van de thumbnail van 1280 px breed zoals de API hem gaf (op
   thumb.wikimedia.org, of in de oude vorm op upload.wikimedia.org: `…/thumb/…/1280px-…`; van een
   origineel dat niet breder is: het origineel zelf, op upload.wikimedia.org) en de
   bestandspagina van het origineel op Commons (`bron`). Open de bestandspagina's; het log zegt per
   foto of hij eerste of tweede keus was en waar dat uit blijkt (`BZ4 set: bewijs …`, hierboven).
   Klopt een foto niet:
   1. zet zijn `bron` met een reden in `api/tests/analyse/sets/beeld_niet.tsv`: `persoon` (er
      staat een herkenbaar mens op), `kunstwerk` (een schilderij, prent, tekening, beeld of
      museumvoorwerp, terwijl de categorie het onderwerp is), `niet-zichtbaar` (de categorie, bij
      `anders-*` het onderwerp, is niet te zien) of `toont-categorie` (een `anders-*` die toch een
      van de twaalf categorieën laat zien), en in `gezien` de sleutel en de commit, zoals `stad-5
      op 3a9b07d`;
   2. commit en push. De job draait weer als `beeld_niet.tsv` verandert (x3a.md, "De set
      strenger"), en anders met `beeld-model: ja` in de tekst van de PR;
   3. het volgende voorstel slaat die foto's over en neemt de volgende in dezelfde volgorde, met
      alle controles van de tool. Kijk het weer door, tot er geen foto meer fout is; dan stap 5.
      Eén ronde is één run van de job (op 3a9b07d ±5 minuten, waarvan ±3 voor `set`).

   Zet een foto alleen om een van die vier redenen op de lijst, die je aan de foto en zijn pagina
   ziet; nooit om een score van het model of een uitkomst van BZ4 (`bz4.json` heeft geen score per
   foto, en dat blijft zo). Een ontbrekend of verkeerd gegeven op Commons zelf rechtzetten (depicts
   toevoegen, als prominent markeren) mag ook; het volgende voorstel leest het. Een vaste foto van
   een combinatie (`<a>+<b>.jpg`) hoort niet op de lijst zonder overleg: daar zijn maar weinig
   kandidaten voor, en een tekort maakt `set` rood. Welke combinaties nog nagekeken moeten worden,
   staat in x3a.md ("De set strenger"). Vallen er foto's weg, dan schuiven de sleutels erna op
   (`strand-3` kan `strand-2` worden); dat is geen probleem.

   Alleen als het niet anders kan (een combinatie zonder andere kandidaat): vervang de regel met
   de hand door een andere foto van Commons met dezelfde sleutel-soort, categorie en licentie.
   Dat gaat buiten de controles van de tool om (de licentie uit de gegevens, de grootte, P180, de
   eerste keus), dus kijk die zelf na. De URL is die van zijn thumbnail van 1280 breed:
   `thumburl` uit
   `https://commons.wikimedia.org/w/api.php?action=query&prop=imageinfo&iiprop=url|size|mime&iiurlwidth=1280&titles=File:<naam>`
   (een liggende foto moet dan ten minste 320 px hoog zijn). sha256 en bytes zijn die van precies
   dat bestand: haal het één keer op en reken ze uit. Een thumbnail van een andere breedte weigert
   de controle. Weglaten kan niet: de controle van de set eist die aantallen (`beeld_model.py`,
   `lees_set`; `test_beeld_sets.py`).
5. Zet in één commit:
   - de tien regels onder de regels van de gezichten in `app/analyse/models.lock`, met een
     opmerking erboven (de commit, Apache-2.0, de licentie in `licenties/`);
   - `voorstel-beeld.tsv` als `api/tests/analyse/sets/beeld.tsv`;
   - de nieuwe exacte lijst in `api/tests/analyse/test_models_lock.py`
     (`test_de_twee_modellen_van_gezichten` kent nu alleen de twee regels van de gezichten).
6. Push. De job draait dan weer (de lock is veranderd), nu streng: een andere sha256 of grootte
   is rood. Groen is BZ2 en BZ4 gehaald: dan kan de go/no-go van X3 (Keuze 73) beslist worden.
   [Gedaan: de regels van `VERSIE` 3 en `beeld.tsv` staan er sinds `dc5dfe5`, en de job was daar
   helemaal groen, ook de stap "Uitslag" (x3b.md, "CI op dc5dfe5"). Jonas koos op 16 november
   2026 "Toch uitbrengen": zoeken op beeld zit in 0.8.1 (Keuze 85).]

Neem de regels van `beeld.int8.onnx`, `tekst.int8.onnx` en `meta.json` uit een run op een commit
met `VERSIE` 3 (na `011bf2d`). Die van de runs ervoor (beeld `96c602da`, tekst `afc9dd55`, meta
`cc84a88b`, `VERSIE` 2) gelden niet meer: per token geeft andere int8-bytes en een andere
`meta.json`. De bronnen, `tokenizer.bin` en de licentie zijn niet veranderd.

**Klopt een foto van `beeld.tsv` later niet meer** ("FOUT set: kat-2.jpg: sha256 …, verwacht …
(bij foto … van 120, een thumbnail van 1280 px, …)"), dan heeft Wikimedia die thumbnail
waarschijnlijk opnieuw gemaakt, met andere bytes. Zolang de cache in CI de eerste goede kopie
heeft, merkt de job het niet; pas als die cache weg is. Haal de thumbnail (de URL in die regel)
één keer op, kijk of het dezelfde foto is, en zet de nieuwe sha256 en grootte in die regel van
`beeld.tsv`, in een eigen commit. Is het een andere foto, vervang de regel met de hand zoals aan
het eind van stap 4. Zo ook als een vaste foto later toch fout blijkt: zet je zijn `bron` in
`beeld_niet.tsv`, dan stopt `set` met `FOUT set: beeld.tsv: regel … (<sleutel>) staat in
beeld_niet.tsv regel … (<reden>); …` tot die regel van `beeld.tsv` vervangen is.

Let op bij stap 5 (voor een volgende `VERSIE`): de regels horen samen met de stap `BEELD` van
`install.sh` in `models.lock`. Zonder die stap maakt niemand de omzetting, en zegt `/beheer` dat
er modellen ontbreken (`models_missing`; gezichten werken wel). `beeld_model.py uitslag` zegt het
ook (x3a.md, Open 1). Voor `VERSIE` 3 is dat zo gebeurd: de regels en X3b kwamen samen op de
branch (`dc5dfe5`).

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

De int8-grafen hebben sinds `VERSIE` 3 (x3a.md, "CI op 011bf2d") per token: elke Linear van beide
torens, ook de MAP-kop, is `MatMulInteger` met de invoer in uint8 per rij (max |x| / 127, nulpunt
128) en het gewicht in int8 per kolom (|q| ≤ 64); de embedding van de tekst blijft int8 per rij.
`meta.json` zegt `"omzetter": 3` en beschrijft dat onder `int8`. De vorm van `VERSIE` 2 (per
tensor, `DynamicQuantizeMatMul`) is alleen nog een optie in de code (`per_token=False`), voor het
rapport; de opdrachtregel kent hem niet.

### De stand van zoeken op beeld (`beeld-status`)

```bash
dc exec -T platform python -m app.cli beeld-status
```

Zo ziet dat eruit als alles goed is (de getallen en tijden zijn een voorbeeld, geen meting):

```
analyse: ok
model: ok
beheerder: aan
foto's met een vector: 4210 van 4213
mislukt (no_preview): 3
ruimte: database 6467584 B, cache 6471680 B, modellen 399569024 B
bench (2026-11-20T10:12:03+00:00): p95 61 ms, 1490 foto's per minuut
kwaliteit (2026-11-20T10:20:41+00:00): ok
```

- `analyse`: de stand van de container, zoals `analyse-status` (`ok`, `down (…)`,
  `models_missing`).
- `model`: `ok` als de container de opdrachten van beeld kent en het model klopt met
  `models.lock`; anders `missing` (het script haalt het bij de volgende run; zie "Het model op de
  LXC").
- `beheerder`: of zoeken op beeld voor iedereen aan staat (de knop op `/beheer`).
- `foto's met een vector`: hoeveel foto's van gebruikers met zoeken op beeld aan al bekeken zijn.
  Mislukt per code daaronder; `no_preview` is een foto zonder preview.
- `ruimte`: de vectoren in de database, de bestanden per eigenaar in de cache, en de modellen.
- `bench` en `kwaliteit` alleen als `beeld-bench` of `beeld-kwaliteit` al gedraaid heeft.

Alleen getallen en woorden van de app; nooit een vector, een vraag of een bestandsnaam. Met de
analyse uit zegt het één regel op stderr (`Zoeken op beeld staat uit: de analyse staat uit
(ANALYSE).`) en is de code 1.

### Het blok op `/beheer`

`/beheer` → "Zoeken en foto's" → "Zoeken op beeld" toont hetzelfde als `beeld-status`, plus het
afgelopen uur: hoeveel vragen, de p95, en hoe vaak de app terugviel op gewoon zoeken (te traag,
geen container, geen model). Drie knoppen:

- **Zet uit voor iedereen** / **Zet aan voor iedereen**: uit geeft iedereen meteen de melding
  "Zoeken op beeld staat uit voor dit platform."; de vectoren blijven staan, zodat aan weer meteen
  werkt.
- **Opnieuw berekenen**: alle vectoren opnieuw, als job op de achtergrond (na een nieuw model doet
  de app dat zelf).
- **Beeldindex wissen**, met een bevestiging: alle vectoren, de bestanden per eigenaar en het
  geheugen van de container gaan weg. Wie zoeken op beeld aan heeft, krijgt daarna de melding
  dat zijn foto's nog bekeken worden, en de app begint opnieuw. Dat kost ongeveer twee core-uur per
  100.000 foto's.

Elke gebruiker zet zoeken op beeld zelf aan bij Account → Foto's (standaard uit, Vraag 11). Uit
wist wat alleen voor die gebruiker bewaard is, ook in het geheugen van de container. De vectoren
gaan niet mee in de reservekopie van het platform; een snapshot die je zelf van `/data` maakt
(ZFS, restic), heeft ze wel, tot je die weggooit.

### BZ9: `beeld-bench` op de LXC

Hoe snel is zoeken op beeld op deze CPU? Na de installatie, met de analyse en `BEELD` aan en het
model geladen (`beeld-status` zegt `model: ok`):

```bash
dc exec -T platform python -m app.cli beeld-bench
```

Het rekent op de container zelf, met eigen getallen: hoeveel foto's per minuut `embed_image`
haalt (64 nep-previews in batches van 16), en de p95 van 40 vragen met vaste woorden over een
bestand van 100.000 willekeurige vectoren in een tijdelijke map (die daarna weg is, ook uit de
container). Een paar minuten. `--fotos`, `--vragen` en `--embed` veranderen de aantallen.

```
beeld-bench: p95 <ms> ms over 40 vragen en 100000 foto's; <aantal> foto's per minuut
```

De uitkomst staat daarna op `/beheer` (regel `beeld-bench`) en in `beeld-status`; het bestand is
`/data/cache/beeld-bench.json`. De poort (Keuze 77, 78, Vraag 14): p95 ≤ 250 ms en ≥ 500 foto's
per minuut. Haalt de CPU de 250 ms niet, dan vangt de klok van 800 ms het op: de gebruiker krijgt
dan de treffers van gewoon zoeken met de melding dat het te lang duurde, en `/beheer` telt dat
onder "te traag". Zeg het tegen de ontwikkelaar, met de regel van `beeld-bench`.

### BZ10: `beeld-kwaliteit --personen` op de LXC

Vindt "Meer zoals deze" op een portret dezelfde persoon terug? Alleen met gezichten (X2) en
personen die je zelf bevestigd hebt:

```bash
dc exec -T platform python -m app.cli beeld-kwaliteit --personen --eigenaar <e-mailadres>
```

Zonder `--eigenaar` neemt het de eerste gebruiker. Voor elke foto van een bevestigde persoon met
minstens twee foto's vraagt het "Meer zoals deze", en telt het hoe vaak een andere foto van
dezelfde persoon in de top 5 staat, tegen wat je bij toeval zou verwachten:

```
beeld-kwaliteit --personen: <personen> personen, <vragen> vragen; dezelfde persoon in de top 5: <deel>, bij toeval <deel> (boven toeval)
```

(of `niet boven toeval`).

Alleen getallen: geen naam, geen foto en geen vector, en niets verlaat de LXC. De uitkomst staat
daarna op `/beheer` (regel `beeld-kwaliteit`: "in orde" als het boven toeval is) en in
`/data/cache/beeld-kwaliteit.json`. Zeg de regel (alleen de getallen) tegen de ontwikkelaar; hij
komt in `docs/fase-6/x3b.md`. Boven toeval geldt de regel van Keuze 82: dan geeft "Meer zoals
deze" niets meer op een foto met een gevonden gezicht, en gelden voor beeld dezelfde regels als
voor gezichten. Die regel is nog niet gebouwd; het is een beslissing na deze meting (x3b.md,
Open). Tot dan blijft zoeken op beeld per gebruiker standaard uit (Vraag 11).

Zonder `--personen` zegt het commando dat de kwaliteitspoort met de vaste set (Keuze 83, BZ4) in
de CI-job `beeld-model` draait, met code 2. Geen bevestigde persoon met twee foto's: één regel op
stderr, code 1.

## Wat er nog niet is

- ~~het vinden van gezichten in de app, de voorstellen, `/beheer` en `app.cli analyse-bench` en
  `analyse-kwaliteit`~~ [golf 6: gebouwd door X2b, zie [gezichten.md](gezichten.md); in 0.8.1
  standaard aan, per gebruiker uit];
- ~~zoeken op beeld voor gebruikers~~ [gebouwd door X3a en X3b (hierboven); in 0.8.1 standaard
  aan, per gebruiker uit (Keuze 85). De strenge run van `beeld-model` met `beeld.tsv` (BZ4) was
  groen op `dc5dfe5`];
- metingen die alleen op de LXC van Jonas kunnen: BZ9 en BZ10 (`beeld-bench` en `beeld-kwaliteit
  --personen`, hierboven), `analyse-bench` en `analyse-kwaliteit` ([gezichten.md](gezichten.md)),
  en het geheugen van de container onder de echte grens (hierboven, "Het geheugen op de LXC").
  Wat er verder open is: [docs/fase-6/x3b.md](../fase-6/x3b.md), Open, en
  [docs/fase-6/integratie-2.md](../fase-6/integratie-2.md).
