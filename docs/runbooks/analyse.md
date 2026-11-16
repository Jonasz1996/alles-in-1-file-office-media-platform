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
precies wat 0.8.0 deed: geen download, geen container, geen nieuwe regel in `.env`. Aanzetten
vóór 0.8.1 is alleen om te proberen: elke gebruiker ziet dan onder Account de schakelaar voor
gezichten. Wat gezichten doen en bewaren: [gezichten.md](gezichten.md).

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
  opdrachten: cancel, caps, faces, group
```

De eerste regel is de stand: `ok`, `off`, `down (<reden>)` of `models_missing (<modellen>)`.
Daarna, als de worker antwoordt: per model of het klopt (de versie is het begin van de sha256),
de grenzen die de container zelf ziet, en de opdrachten die de worker kent. Tot X3 klaar is, zijn
dat er vier; de opdrachten van beeld zeggen `not_built`. De exitcode is 0 bij `ok`
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
  container (de rootdisk) heeft ze wel. Wat er in die map staat, hoe je hem wist en hoe je hem
  ook uit een snapshot van de container houdt: [gezichten.md](gezichten.md).

## Terug naar 0.8.0

Zoals in `docs/runbooks/zoeken.md`, "Terug naar 0.8.0 van `59e0250`", ook met de analyse aan.
Het script van 0.8.0 kent de analyse niet: het start de app zonder `compose.analyse.yaml`, en
`--remove-orphans` haalt de container weg. `.env` (met `ANALYSE=aan` en de sleutel), de modellen
en de sleutels blijven. Weer vooruit: het script van de branch, zonder `PLATFORM_SRC_REF`; de
modellen kloppen nog, dus het haalt niets opnieuw, en de container start weer.

## Zoeken op beeld (X3)

Golf 5 (X3a) bouwt de tokenizer, de omzetter van SigLIP 2 en de CI-job `beeld-model`. Golf 6
(X3b) bouwt de rest: het model op de LXC, de opdrachten in de container, het zoeken in de app, de
web, het blok op `/beheer` en `beeld-status`, `beeld-bench` en `beeld-kwaliteit`. Wat er gebouwd
is en wat nog open staat: [docs/fase-6/x3a.md](../fase-6/x3a.md) en
[docs/fase-6/x3b.md](../fase-6/x3b.md).

Tot 0.8.1 staat ook zoeken op beeld uit: met de analyse uit kent de app het niet (elke route van
beeld geeft `501`, zoals in 0.8.0), en de web toont wat 0.8.0 toonde.

### Het model op de LXC (`BEELD`, X3b)

Alleen met de analyse aan (`ANALYSE=aan`): dan zet `install.sh` één keer `BEELD=aan` in `.env`,
en haalt het na de start van de app het model op en zet het om (Keuze 84). Zonder `ANALYSE`, met
`ANALYSE=uit` of met `BEELD=uit` doet het niets: geen download, geen omzetting, geen map, geen
regel.

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

Uit: `curl … | BEELD=uit bash`. Dan haalt het niets meer op; een model dat er al staat, blijft
staan (gooi `MODELLEN_ROOT/siglip2-b32-256` zelf weg als je de ruimte terug wilt). Aan:
`curl … | BEELD=aan bash`. Elke gebruiker zet zoeken op beeld daarna zelf aan bij Instellingen.

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
de robotregels staan 25 Mbps toe), en elke foto als thumbnail van 1280 px breed, niet als origineel
(op de originelen gaf upload.wikimedia.org een 429 met Retry-After 600; x3a.md, "CI op 9ebf298").
Is het origineel niet breder dan 1280, dan is het origineel zelf de thumbnail. De API van Commons
(commons.wikimedia.org) krijgt hooguit één vraag per 8 s, en ook een nieuwe poging na een 429
telt: zonder contact in de User-Agent staat Wikimedia volgens wie de grenzen citeert (september
2026) 10 vragen per minuut per IP toe (x3a.md, "CI op 3148c2f"). Een voorstel doet ±90 tot 200
vragen aan de API en duurt daardoor ±12 tot 27 minuten; met `beeld.tsv` vraagt `set` de API
niets. Upload krijgt hooguit één vraag per seconde.
Na een 429 of 503 vraagt de job nooit eerder opnieuw dan Retry-After zegt (seconden, ook met
decimalen, of een HTTP-datum). Zegt die meer dan 600 s, is hij niet te lezen, of past het wachten
niet meer in 900 s per stap (ook de eigen 2 tot 32 s na een 5xx tellen), dan stopt hij meteen,
met een fout die dat zegt. Draait hij niet, dan zegt het blok `=== kort ===` aan het eind van het
log "beeld-model: draaien nee" met de reden.

Het blok `=== kort ===` staat aan het eind van het log, met per regel:

- `BZ1 <host>: bereikbaar (HTTP …)` of `niet bereikbaar (…)`, voor huggingface.co,
  commons.wikimedia.org, upload.wikimedia.org en images-assets.nasa.gov;
- `BZ2 …`: de commit; de tijd en maxrss van de twee omzettingen zoals de dienst (grens 1,5 GB),
  of die twee dezelfde sha256 geven en of ze kloppen met `models.lock`; en van de derde omzetting
  met `--fp32` (alleen voor BZ4) de tijd en maxrss zonder grens, en of hij dezelfde vijf bestanden
  geeft;
- `BZ4 set: 120 foto's kloppen met beeld.tsv (… uit de cache, … van Commons, … als origineel;
  … MB in … s; API: … vragen, … keer 429; upload: … vragen, … keer 429; contact: ja)`, of het
  voorstel (`… (… als origineel; … MB in … s; API: …)`): wat er van Commons kwam, hoeveel daarvan
  als origineel in plaats van als thumbnail, hoe lang dat duurde, en het verkeer met Commons in
  die stap: per dienst (de API en upload) de vragen, elke poging meegeteld, hoe vaak het antwoord
  429 was (en `… keer 5xx` als dat er waren), na hoeveel seconden de eerste 429 kwam (`eerste 429
  na … s`), en of de User-Agent een contact had (nooit welk). Een dienst zonder vragen staat er
  als `upload: 0 vragen`;
- `FOUT <stap>: … HTTP 429 (…)` of `HTTP 5xx (…)`: de host gaf ook na zes pogingen nee, of vroeg
  langer te wachten dan mag; tussen haakjes de laatste Retry-After, waarom de job meteen stopte
  (als dat zo was) en de reden uit zijn antwoord: de titel, of als die alleen "Wikimedia Error"
  zegt (de foutpagina van Wikimedia) de eerste alinea, anders de eerste regel tekst; alleen
  afdrukbare ASCII en hooguit 160 tekens. Herhaalt het antwoord de User-Agent met
  `BEELD_CONTACT`, dan staat daar `[contact]`. Bij `set` staat erachter hoe ver hij kwam, wat hij
  ophaalde en het verkeer met Commons, bijvoorbeeld `(bij foto 37 van 120, een thumbnail van
  1280 px, 12,3 MB opgehaald; API: 111 vragen, 0 keer 429; upload: 37 vragen, 0 keer 429;
  contact: ja)` of `(…, een origineel, …)`. Zegt de API `… keer 429` bij weinig vragen en een
  `eerste 429 na` van een paar seconden, dan was het budget van het IP van de runner al op voordat
  de job begon (ander verkeer op dat IP);
- `BZ3`, `BZ4`, `BZ6`, `BZ7`: de uitkomsten van de tests, met `goed` of `FOUT`;
- `UITSLAG …`: waarom de job rood is, als er een voorstel ligt;
- per pytest-stap de laatste regel.

Het hele verslag staat bij de run als artifact `beeld-model` (`kort.txt`, `bz3.json`, `bz4.json`,
`bz6.json`, `bz7.json`, de voorstellen en de logs van de omzetter, en `uit1/meta.json`). Alleen in
het log, niet in het blok: aan het begin van `set` de regel `BZ4 set: de API van Commons hooguit
één vraag per 1 s, upload één per 1 s; contact: ja` (in de CI, met de link naar het repository als
contact; zonder contact `… per 8 s, …; contact: nee`).

### Een contact in de User-Agent (`BEELD_CONTACT`, in de CI aan)

Zonder contact is de User-Agent van de tool algemeen: `beeld-model-meting/1.0 (CI van een
zelf-gehost fotoplatform)`, zonder persoon, e-mail of repository. Wikimedia telt zo'n client als
niet geïdentificeerd: 10 vragen per minuut aan de API, per IP (zo geciteerd in september 2026;
x3a.md, "CI op 3148c2f"). Dan vraagt `set` de API hooguit één keer per 8 s. Met contactgegevens
in de User-Agent, zoals de richtlijn van Wikimedia voor de User-Agent vraagt, is de grens 200 per
minuut; dan vraagt de tool de API hooguit één keer per seconde.

`tools/beeld/beeld_model.py` stuurt dat contact mee als de omgevingsvariabele `BEELD_CONTACT`
gezet is, en alleen naar Commons (de API en upload; Hugging Face en andere hosts nooit).

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
   sha256, bytes, de URL van de thumbnail van 1280 px breed op upload.wikimedia.org
   (`…/thumb/…/1280px-…`; van een origineel dat niet breder is: het origineel zelf) en de
   bestandspagina van het origineel op Commons. Staat er toch een herkenbaar persoon op een foto,
   vervang die regel door een andere foto van Commons met dezelfde sleutel-soort, categorie en
   licentie. De URL is die van zijn thumbnail van 1280 breed: `thumburl` uit
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

**Klopt een foto van `beeld.tsv` later niet meer** ("FOUT set: kat-2.jpg: sha256 …, verwacht …
(bij foto … van 120, een thumbnail van 1280 px, …)"), dan heeft Wikimedia die thumbnail
waarschijnlijk opnieuw gemaakt, met andere bytes. Zolang de cache in CI de eerste goede kopie
heeft, merkt de job het niet; pas als die cache weg is. Haal de thumbnail (de URL in die regel)
één keer op, kijk of het dezelfde foto is, en zet de nieuwe sha256 en grootte in die regel van
`beeld.tsv`, in een eigen commit. Is het een andere foto, vervang de regel zoals in stap 4.

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
  `analyse-kwaliteit`~~ [golf 6: gebouwd door X2b, zie [gezichten.md](gezichten.md); uit tot 0.8.1];
- zoeken op beeld voor gebruikers: het is gebouwd (X3a en X3b, hierboven), maar staat tot 0.8.1
  uit, net als de rest van de analyse. Nog niet gedaan: BZ9 en BZ10 op de LXC van Jonas
  (`beeld-bench` en `beeld-kwaliteit --personen`, hierboven), en de strenge run van `beeld-model`
  met `beeld.tsv` (BZ4); zie [docs/fase-6/x3b.md](../fase-6/x3b.md), Open.
