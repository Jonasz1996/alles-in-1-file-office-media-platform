# Runbook: media

Media is video en muziek in het platform: bibliotheken, de speler, de muziekbalk en de import.
Het omzetten (ffmpeg) gebeurt in een eigen container, de **mediaworker** `platform-media-1`:
zonder netwerk, zonder je bestanden, als een eigen gebruiker (uid 10002). De app geeft hem elk
bestand dat hij nodig heeft, en hij geeft het resultaat terug. Wat media is en hoe je het aan- en
uitzet, staat in `infra/single/README.md` (Media). Dit runbook gaat over de status, de nooduitgang,
terugrollen, de cache, de sleutel van TMDB, de import, de belastingtest en later een GPU.

Alles hieronder gebeurt in de Console van de container (de LXC), als root, tenzij er "op de
Proxmox-host" staat. `dc` is de functie uit de README (Handige commando's): `docker compose` met
precies de bestanden van het installatiescript, ook die van media. Het installatiescript is
steeds de nieuwste versie:

```bash
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/main/infra/single/install.sh | bash
```

Hieronder heet dat kortweg "het script"; een instelling zet je ervoor, zoals
`curl … | MEDIA_SANDBOX=uit bash`. Zolang deze versie nog niet op `main` staat, gebruik je het
adres van de branch en `PLATFORM_BRANCH`, zoals in `infra/single/README.md`.

## Wat het script bij elke run met media doet

1. Het leest `MEDIA_SANDBOX` uit de omgeving vóór `/opt/platform/.env` (alleen `aan` of `uit`;
   iets anders stopt het script meteen, vóór er iets verandert).
2. Ontbrekende sleutels komen in `.env`: `MEDIA_SANDBOX=aan`, `MAX_UPLOAD_BYTES` (50 GB, de
   grootste upload) en `MEDIA_WORKER_KEY` (64 tekens, één keer gemaakt, nooit vervangen). De app
   en de worker bewijzen met die sleutel aan elkaar dat ze de echte zijn. Een bestaande waarde
   blijft altijd staan; `.env` houdt rechten 600.
3. Het maakt de mappen `/tank/platform/media` (de cache) en `/tank/platform/import` (de import),
   van uid 10001.
4. Kijkt er iemand, dan zegt het dat vóór het vervangen ("Let op: er kijkt nu 1 persoon …"). Het
   wacht niet: die stroom staat tot ±60 s stil, en de speler gaat daarna zelf verder.
5. Het vervangt de app en wacht tot `/health` antwoordt.
6. Dan start het de worker (hetzelfde image als de app) en vraagt het de status
   (`app.cli media-status --wait 20`). Daarna office, en pas dan gaat het oude image van de app
   weg.
7. De eindtekst zegt `Media: aan (afgeschermd).`, of waarom niet.

Media stopt het script nooit. Start de worker niet, dan staat de melding van Docker erboven en
werkt de rest door; video's spelen dan alleen als de browser het bestand zelf kan. Draai het
script later opnieuw.

## De status lezen

```bash
dc ps
dc exec -T platform python -m app.cli media-status
dc exec -T platform python -m app.cli media-status --sessions
```

- `dc ps` toont `platform-media-1` als `healthy` (de healthcheck vraagt de worker elke 30 s of
  hij leeft).
- `media-status` geeft één regel, zoals `Media: aan (afgeschermd, ffmpeg 7.1.5)`. Andere regels:
  - `Media: aan (lokaal, zonder afscherming, …)`: de nooduitgang staat aan (hieronder);
  - `Media: aan, maar de worker is niet bereikbaar (…)`: zie "De worker is weg";
  - `ffmpeg ontbreekt` of `Let op: de worker mist …`: het image is niet goed gebouwd; draai het
    script opnieuw;
  - `afscherming niet gecontroleerd`: de zelftest van de worker kon niet kijken; zie de logs.
  - `GPU: … (niet in gebruik)`: de worker ziet een GPU; die wordt nog niet gebruikt (hieronder).
- `--sessions` zegt ook wie er nu kijkt: `Kijkers: niemand`, of per persoon de titel en hoe
  (`direct`, `remux`, `transcode 720p`, `audio`).

Op `/beheer` → **Media** (Alt+8) staat hetzelfde, en meer: de worker en zijn zelftest, ffmpeg,
de meting (bench), het budget, wie er kijkt (met **Stop** per sessie), waarom er de laatste 7
dagen werd omgezet, de achtergrond (analyses, met **Mislukte opnieuw**), de bibliotheken, de cache,
de online metadata, de import, de instellingen en de belastingtest.

**Meldingen.** Bovenaan `/beheer` kan staan:

| Melding | Wat het betekent | Wat je doet |
|---|---|---|
| De mediaworker is niet bereikbaar | video's die omgezet moeten worden, spelen niet; direct play en muziek die de browser zelf kan, werken | "De worker is weg" hieronder |
| ffmpeg ontbreekt of werkt niet | de worker leeft, maar kan niets omzetten | het script opnieuw draaien (bouwt het image opnieuw) |
| De cache van media is vol | omzetten gaat langzamer; minder dan de marge vrij op de schijf | "De cache opruimen" hieronder |
| De mediaworker draait zonder afscherming | de nooduitgang staat aan | zodra het kan, weer `MEDIA_SANDBOX=aan` |
| De sleutel van de online metadata werkt niet meer | TMDB weigert de sleutel | een nieuwe sleutel (hieronder) |
| Online metadata opzoeken mislukt al een tijd | TMDB of MusicBrainz is (even) onbereikbaar | niets; de app probeert het later zelf opnieuw |

**De worker is weg.**

```bash
dc ps media
dc logs --tail 100 media
docker inspect --format '{{.State.OOMKilled}}' platform-media-1
```

- Draait de container niet, of start hij steeds opnieuw: draai het script opnieuw.
- `OOMKilled` is `true`: de worker had meer dan 3 GB nodig (de grens van
  `compose.media-limits.yaml`). Zet op `/beheer` → Media → Instellingen **Omzettingen tegelijk**
  lager, of geef de LXC meer geheugen.
- Zegt het log iets over de sleutel (`MEDIA_WORKER_KEY`): verander die nooit met de hand; draai
  het script opnieuw.

**Nooit `dc exec media …` zonder `env -i`.** Een `exec` in de container van de worker krijgt de
omgeving van de container, en daar staat de sleutel in. Een programma dat zo start, is
leesbaar voor elk ander proces van dezelfde gebruiker, dus ook voor een ffmpeg die een vreemd
bestand omzet. Moet je toch iets in die container zien, start het dan zonder omgeving:

```bash
dc exec media env -i PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin ffmpeg -hide_banner -version
```

Nooit `dc exec media sh` of `bash`. Meestal is het ook niet nodig: `dc logs media`,
`media-status` en `/beheer` → Media zeggen genoeg.

## De nooduitgang: media zonder afscherming

Start de worker in zijn container niet, en moet media toch werken, dan kan ffmpeg in de app zelf
draaien, zonder de eigen container (de "lokale worker"):

```bash
curl -fsSL …/install.sh | MEDIA_SANDBOX=uit bash
```

Het script zet `MEDIA_SANDBOX=uit` in `.env`, haalt de container `platform-media-1` weg en start
de app opnieuw; de app start de worker dan zelf. De eindtekst zegt `Media: aan, zonder
afscherming (…)`, en `/beheer` meldt het zolang het zo staat. Alles werkt, maar een kwaadaardig
videobestand dat ffmpeg kraakt, staat dan in de app, met al je bestanden. Gebruik het alleen
tijdelijk.

Weer afgeschermd (dit is ook de standaard):

```bash
curl -fsSL …/install.sh | MEDIA_SANDBOX=aan bash
```

Media helemaal uit (niets van media draait, afspelen geeft een nette melding, je bestanden en
bibliotheken blijven): `/beheer` → Media → Instellingen → **Media aan** uitvinken.

## Terug naar 0.6.0

**Liefst niet de code terug.** Is media het probleem, zet het dan uit op `/beheer` (hierboven):
de app blijft de nieuwe versie en werkt verder zonder media.

**Moet de code echt terug** naar 0.6.0 (fase 4, commit
`263abd74d914a9811901e7570ed410866364f4b5`; na de integratie ook de tag `v0.6.0`), dan moet eerst
de database terug naar revisie 0007, want 0.6.0 start niet op een nieuwer schema. De volgorde is
die van office (`docs/runbooks/office.md`, "De app terugrollen"). Office blijft gewoon aan:
0.6.0 kent office.

1. Kijk eerst of de oude versie er is, vóór je iets stopt:
   ```bash
   git -C /opt/platform/src fetch -q origin
   git -C /opt/platform/src cat-file -e '263abd74d914a9811901e7570ed410866364f4b5^{commit}' && echo "goed: 0.6.0 is er"
   ```
   Zegt dit niet "goed", stop dan hier. Met een versie die niet bestaat, faalt stap 4 pas als de
   app al gestopt is en het schema terug is.
2. Stop de app en de worker, zodat niets meer naar de tabellen van media schrijft:
   ```bash
   dc stop platform media
   ```
3. Zet het schema terug, met het image van nu (het commando vraagt om "ja"):
   ```bash
   dc run --rm --no-deps platform python -m app.cli db-downgrade 0007
   ```
4. Installeer 0.6.0, met het script van nu (via curl) en de commit uit stap 1:
   ```bash
   curl -fsSL …/install.sh | PLATFORM_SRC_REF=263abd74d914a9811901e7570ed410866364f4b5 bash
   ```
   Zonder `PLATFORM_SRC_REF` haalt het script `main`, en die zet het schema bij de start weer
   vooruit. Het script ziet zelf dat 0.6.0 geen media heeft, en haalt de worker weg. De
   eindtekst zegt `Media: uit, want deze versie van het platform heeft nog geen media.`

Daarna zijn de bibliotheken, de voortgang ("verder kijken"), de afspeellijsten en de metadata weg
(die stonden in de nieuwe tabellen). Je bestanden, hun versies en de accounts blijven, ook de
films en muziek zelf: dat zijn gewone bestanden. De sleutel van TMDB blijft versleuteld in de
database staan; 0.6.0 gebruikt hem niet. De mappen
`/tank/platform/media` en `/tank/platform/import` en het Docker-volume `platform_media-run`
blijven staan en doen geen kwaad; de cache in `media` mag weg.

**Weer vooruit:** draai het script zonder `PLATFORM_SRC_REF`. De app zet het schema bij zijn start
zelf weer op de nieuwe revisie; de bibliotheken maak je opnieuw, en de scanner vindt alles terug.

Zo nagelopen in de installatietest (`infra/single/tests/test_install_media.sh --stappen`, stap 4).

## De cache opruimen

De cache staat in `/tank/platform/media` en is standaard hooguit 20 GB (`/beheer` → Media →
Instellingen → **Cache (GB)**). De app ruimt hem zelf op: de oudste stukken eerst, en bij een
start van de app alle stukken van sessies.

Met de hand: `/beheer` → Media → **Cache** → **Opruimen** bij een soort (lopende sessies,
ondertitels, voorbeeldbeelden, muziek). Wat nu speelt, blijft werken; de rest maakt de app
opnieuw als het nodig is. Hoezen en posters ruim je daar niet op: die komen niet vanzelf terug.

Is de schijf zo vol dat de app niet meer start, haal dan alleen de stukken van sessies weg (die
gaan bij de start toch weg):

```bash
dc stop platform
rm -rf /tank/platform/media/sessions
```

en draai daarna het script opnieuw. Ruim daarna eerst op wat de schijf vulde.

**Een lang nummer speelt niet.** De muziekbalk pakt een nummer dat de browser niet zelf kan om in
de cache. Eén nummer mag daar hooguit `cache_gb / 8` innemen (2,5 GB bij 20 GB): een mix of
luisterboek in Opus tot ±2,5 uur, in WAV of ALAC tot ±2,8 uur. Langer geeft "kan niet afspelen"
(415 `media_unsupported`, reden `too_large`). Wil je dat toch, zet de cache groter.

## De sleutel van TMDB

Zonder sleutel (de standaard) haalt het platform titels, jaren en afleveringen alleen uit de
namen van je bestanden en uit `.nfo`-bestanden ernaast. Met een sleutel van
[TMDB](https://www.themoviedb.org/) zoekt het ook posters, beschrijvingen en de juiste film erbij.
Muziek gebruikt MusicBrainz en de Cover Art Archive; die hebben geen sleutel nodig (uit te zetten
op `/beheer` → Media → Instellingen).

1. Maak een account op themoviedb.org, en vraag bij Settings → API een sleutel aan (gratis, voor
   eigen gebruik). Neem het **API Read Access Token**: de lange, die met `eyJ` begint. De korte
   **API Key** (32 tekens) werkt hier niet; het platform weigert hem meteen.
2. `/beheer` → Media → **Online metadata** → **De sleutel van TMDB**: plak hem en kies **Sleutel
   bewaren**. Het platform probeert hem meteen, en zegt het als hij niet werkt.
3. **Testen** probeert hem later nog eens; **Sleutel weghalen** haalt hem weg (wat er al staat,
   blijft).

De sleutel staat versleuteld in de database, niet in `.env`, en komt nooit meer in beeld, ook
niet op `/beheer`. Meldt `/beheer` dat hij niet meer werkt: maak op themoviedb.org een nieuwe en
bewaar die.

## Films, series en muziek importeren

Heb je al een verzameling (op een USB-schijf, een NAS of de Proxmox-host), dan zet je die in één
keer in het platform met de import. Elk bestand komt erin alsof je het uploadde: bij een eigenaar,
met zijn quotum, ontdubbeld, met versies en prullenbak.

**1. De map.** In de LXC is het `/tank/platform/import`; in de app heet die map `/data/import`.
Op de Proxmox-host is het dezelfde map onder het pad van het mount point van `/tank`. Dat vind je
op de host met:

```bash
pct config <id-van-de-container> | grep mp=/tank
```

Staat er een ZFS-volume (`mp0: tank:subvol-101-disk-0,mp=/tank`), dan geeft
`zfs get -H -o value mountpoint tank/subvol-101-disk-0` het pad op de host; de map is dan
`<dat pad>/platform/import`. Is `/tank` een gewone map van de host (`mp0: /tank,mp=/tank`), dan is
het op de host ook `/tank/platform/import`. Hieronder heet die map op de host `IMPORT`.

**2. De bestanden erin**, op de Proxmox-host, per verzameling een eigen map. Bijvoorbeeld van een
USB-schijf:

```bash
rsync -a --info=progress2 /mnt/usb/Films/ "$IMPORT/Films/"
```

**3. De rechten**, op de Proxmox-host. De container is unprivileged: uid 10001 in de app is op de
host uid 110001 (10001 plus 100000). Root in de LXC kan bestanden van de host niet van eigenaar
veranderen (die zijn daar van `nobody`, 65534), dus dit moet op de host:

```bash
chown -R 110001:110001 "$IMPORT"
```

**4. De controle**, in de LXC:

```bash
ls -ln /tank/platform/import /tank/platform/import/Films | head
```

De derde en vierde kolom moeten `10001 10001` zijn. Staat er `65534`, dan was de `chown` niet op
de host of op een ander pad. De import zegt het ook vooraf: "N bestanden kan de app niet lezen"
of "niet verplaatsen".

**5. Importeren:** `/beheer` → Media → **Importeren**. Kies de **bronmap** (wat er in de import
staat, met het aantal bestanden en de grootte), de **eigenaar**, de **doelmap** (een map van
jezelf, of de hoofdmap van een ander) en **hoe**:

- **Kopiëren** (de standaard): de bron blijft staan. Lezen is genoeg. Het kost tijdelijk dubbele
  ruimte; haal de bron zelf weg als de import klaar is (`rm -r /tank/platform/import/Films`).
- **Verplaatsen:** elk bestand gaat uit de importmap zodra het binnen is. Het kost geen extra
  ruimte, want de importmap en de bestanden van het platform staan op dezelfde schijf. Daarvoor
  moet de app in de importmap mogen schrijven (stap 3 regelt dat).

Kies **Importeren**. De voortgang staat er live onder, met wat er overgeslagen werd en waarom
(niet te lezen, of "in gebruik": een bestand dat nog geschreven werd; een volgende import pakt het
op). Er loopt hooguit één import tegelijk. Stop je de app midden in een import, dan verlies je
niets: start hem daarna gewoon opnieuw; wat er al is, slaat hij over.

Hetzelfde kan vanaf de commandoregel, ook naar een submap van een ander:

```bash
dc exec -T platform python -m app.cli media-import /data/import/Films --naar /Films --eigenaar jij@voorbeeld.nl
dc exec -T platform python -m app.cli media-import /data/import/Muziek --naar /Muziek --eigenaar jij@voorbeeld.nl --verplaats
```

**6. Een bibliotheek.** De import zet bestanden in een map; video en muziek zie je pas onder
Media als die map een bibliotheek is. De eigenaar van de map kiest op Media **Nieuwe
bibliotheek**, of opent de map in Bestanden en kiest **Als bibliotheek gebruiken** (films, series,
muziek of video's). De scanner doet de rest; met een sleutel van TMDB komen er ook posters bij.

## De belastingtest op de LXC

De belastingtest laat zien of de LXC drie kijkers tegelijk aankan die allemaal omgezet moeten
worden (§4.6 van het bouwontwerp). Hij maakt zelf zijn bronnen (300 s 1080p, met ffmpeg), speelt
ze af met virtuele spelers en meet het starten, het haperen en de API. Doe hem als er niemand
kijkt; hij duurt 10 tot 20 minuten. Hij weigert als er echte omzettingen of een meting lopen, en
stopt zelf bij een vierde ffmpeg.

1. **Eerst de meting:** `/beheer` → Media → **Meting** → **Meten** (±1 minuut). Zo weet het
   platform wat deze CPU kan.
2. **De test aanzetten** (hij staat standaard uit). Maak één keer een klein bestand en start de
   app en de worker ermee (allebei: de app start de test, de worker maakt de bronnen, en zonder
   de regel bij `media` weigert hij dat):
   ```bash
   cat >/root/belastingtest.yaml <<'EOF'
   services:
     platform:
       environment:
         PLATFORM_MEDIA_LOADTEST: "1"
     media:
       environment:
         PLATFORM_MEDIA_LOADTEST: "1"
   EOF
   dc -f /root/belastingtest.yaml up -d media platform
   ```
   Wacht tot `dc ps platform media` voor allebei weer `healthy` zegt.

   Staat `MEDIA_SANDBOX=uit` in `.env` (de nooduitgang: er is dan geen container `media`), laat
   dan de laatste drie regels van het bestand weg (`media:` en wat eronder staat) en start alleen
   de app: `dc -f /root/belastingtest.yaml up -d platform`. De lokale worker krijgt de
   belastingtest van de app mee. Met het blok `media` erin weigert `dc` dan ("service "media"
   has neither an image nor a build context").
3. **De test:** `/beheer` → Media → **Belastingtest** → **Belastingtest starten** (3 sessies, 300
   seconden). Of vanaf de commandoregel, met de uitkomst in een bestand:
   ```bash
   dc exec -T platform python -m app.cli media-loadtest --sessies 3 --duur 300 | tee /root/belastingtest.json
   ```
   Fase a (gespreid, met een sprong) en b (alle drie tegelijk) slagen als elke start binnen 5 s
   is, er niets hapert, en de API (p95) binnen 200 ms antwoordt.
4. **Fase c, de app herstart** (de spelers moeten dan buiten de app draaien). Start ze in een
   eigen container:
   ```bash
   dc -f /root/belastingtest.yaml run --rm --no-deps -T platform python -m app.cli media-loadtest --extern http://platform:8080 --duur 300 | tee /root/belastingtest-c.json
   ```
   en herstart in een tweede console, ±60 s later, alleen de app:
   ```bash
   docker restart platform-platform-1
   ```
   Fase c slaagt als elke speler binnen 10 s na de herstart weer speelt en daarna niet hapert.
5. **Fase d, office herstart** (alleen met office aan): start de test van stap 3 opnieuw, en
   herstart ±30 s later `docker restart platform-office-1`. Op `/beheer` → Media → Budget staat
   dan "Tijdelijk een core minder: de document server start"; de test moet toch slagen.
6. **Weer uit:** draai het script opnieuw (zonder het bestand van stap 2). Het bestand mag blijven
   staan voor een volgende keer.

Zet de uitkomst (de JSON-bestanden, of de regels op `/beheer`) in `docs/fase-5/metingen.md`.
Slaagt een fase niet, zet dan op `/beheer` → Media → Instellingen **Omzettingen tegelijk** of
**Cores voor omzetten** lager, en meet opnieuw.

## Later: een GPU

Een GPU (bijvoorbeeld de iGPU van een Intel-processor) kan omzetten veel goedkoper maken, en HDR en
drie keer 1080p waarschijnlijk wel aan (niet getest). Het platform gebruikt er nog geen: alles werkt
met alleen de CPU, en een GPU wordt nooit een eis. Zonder GPU zegt `media-status` er niets over
(er komt geen regel bij), en `/beheer` → Media → ffmpeg zegt bij GPU "geen (nooit nodig)". Ziet
de worker er een, dan zegt `media-status` `GPU: /dev/dri (vaapi, …) (niet in gebruik)` en
`/beheer` bij GPU `/dev/dri`.

Wil je dat later, dan is dit wat er op de host en in de LXC nodig is (nu nog niet doen; de
versie die het gebruikt, zegt het precies):

- op de Proxmox-host: `ls -l /dev/dri` toont `renderD128`; in de configuratie van de container
  (`/etc/pve/lxc/<id>.conf`) komt `dev0: /dev/dri/renderD128,gid=<gid van render in de LXC>`;
- in de LXC: de groep `render`, en `ls -l /dev/dri/renderD128` werkt;
- in compose: het apparaat en de groep voor alleen de worker (`devices` en `group_add`), en een
  driver in het image.

Dat geeft de worker één apparaat erbij, en verder niets: geen extra rechten voor de LXC en geen
privileged container.
