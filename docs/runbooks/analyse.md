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
bewaart maar één keer. Elke foto komt als thumbnail van 1280 px breed, niet als origineel (op de
originelen gaf upload.wikimedia.org een 429 met Retry-After 600; x3a.md, "CI op 9ebf298"). De
thumbnails staan sinds september 2026 op thumb.wikimedia.org (volgens bronnen uit de tweede hand;
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
- `BZ4 set: beeld_niet.tsv: … foto's op de lijst, als kandidaat overgeslagen: …`: alleen als die
  lijst er is; hoeveel foto's erop staan en hoeveel daarvan het voorstel tegenkwam en oversloeg;
- `FOUT set: beeld_niet.tsv regel …: …`: de lijst klopt niet (de kop, een bron die geen
  bestandspagina van Commons is, een reden die niet bestaat, een lege of te lange `gezien`, een
  dubbele bron; ook `FOUT set: beeld_niet.tsv: geen UTF-8`); `set` vraagt dan niets aan Commons.
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
    zoals deze, cos, en de grootte en tijd van de grafen. Na 8 minuten `overgeslagen: tijd`;
    `Q_MAX 127` zonder VNNI `overgeslagen: geen VNNI`;
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
staan. Alleen ids en namen uit de vaste lijsten van de tool, nooit tekst van Commons. Daarmee
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

**Klopt een foto van `beeld.tsv` later niet meer** ("FOUT set: kat-2.jpg: sha256 …, verwacht …
(bij foto … van 120, een thumbnail van 1280 px, …)"), dan heeft Wikimedia die thumbnail
waarschijnlijk opnieuw gemaakt, met andere bytes. Zolang de cache in CI de eerste goede kopie
heeft, merkt de job het niet; pas als die cache weg is. Haal de thumbnail (de URL in die regel)
één keer op, kijk of het dezelfde foto is, en zet de nieuwe sha256 en grootte in die regel van
`beeld.tsv`, in een eigen commit. Is het een andere foto, vervang de regel met de hand zoals aan
het eind van stap 4. Zo ook als een vaste foto later toch fout blijkt: zet je zijn `bron` in
`beeld_niet.tsv`, dan stopt `set` met `FOUT set: beeld.tsv: regel … (<sleutel>) staat in
beeld_niet.tsv regel … (<reden>); …` tot die regel van `beeld.tsv` vervangen is.

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
