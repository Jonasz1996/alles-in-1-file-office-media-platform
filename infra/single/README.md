# Eerste versie op één node

Zonder cluster, zonder SSH en zonder Ansible: alles gebeurt in de console van één
Debian- of Ubuntu-container. Het platform draait daarin als één Docker-container
(API en web-app samen, database en bestanden in `/tank/platform`). Het cluster van 3 nodes
(`infra/ansible`) komt later; de data gaat dan mee.

## Container in Proxmox

- Debian 13-template, **Unprivileged** aan
- 4 cores, 16 GB RAM, swap 0, rootdisk 64 GB
- Mount point `/tank` van 700 GB op je ZFS-opslag (optioneel; zonder komt de data op de rootdisk)
- Options → Features: **nesting** en **keyctl** aan
- Vast IP

## Installeren

Open in Proxmox de **Console** van de container, log in als root en plak:

```bash
apt update && apt install -y curl
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/main/infra/single/install.sh | bash
```

Het script installeert Docker, bouwt het platform en start het op poort 80, met office
(OnlyOffice Docs, zie Office hieronder) als tweede container ernaast en de mediaworker (ffmpeg,
zie Media hieronder) als derde. Open daarna
`http://<ip-van-de-container>` in je browser: bij het eerste bezoek maak je het
beheerdersaccount aan en zet je meteen 2FA aan (verplicht; houd een authenticator-app zoals
Aegis of Google Authenticator bij de hand). Doe dat voordat je de poort in je router
openzet, anders kan iemand anders je voor zijn.

Een andere poort kiezen kan bij de eerste installatie met `curl … | PORT=8080 bash` (het
commando hierboven, met `PORT=8080` vóór `bash`), of later in `/opt/platform/.env` (daarna het
script opnieuw draaien).

## Bijwerken naar fase 6 (zoeken en foto's)

Fase 6 (0.8.0: zoeken in de inhoud, plaatsnamen, duplicaten, personen) staat, net als 0.7.1, op
de branch `claude/stappenplan-uitwerken-lu4uv3`. Je werkt bij met hetzelfde commando als hieronder
bij 0.7.1:

```bash
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/claude/stappenplan-uitwerken-lu4uv3/infra/single/install.sh | PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 bash
```

Wat er bij het bijwerken van 0.7.1 naar fase 6 gebeurt, in deze volgorde:

- `PLAATSEN=aan` komt in `/opt/platform/.env`, als hij er nog niet staat. Dan haalt het script
  twee bronnen op naar `/tank/platform/geo` (±16 MB: de landen van Natural Earth en de plaatsen
  van GeoNames, elk met een vaste sha256). De app maakt er bij de start `places.sqlite` van
  (±1 minuut). Lukt het ophalen niet, dan zegt het script `Plaatsnamen: niet geladen` en gaat het
  gewoon door; de volgende run probeert het opnieuw. Liever zonder plaatsnamen:
  `… | PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 PLAATSEN=uit bash`;
- het laat open documenten opslaan en maakt dan een **reservekopie** van de database, terwijl de
  oude app nog draait: `/tank/platform/backup/platform-0009-<tijd>.db` (rechten 600). Lukt die
  niet, dan stopt het script daar: er is dan nog niets veranderd, en de app draait zoals hij
  draaide;
- het bouwt het nieuwe platform en start het; de database krijgt bij de start migratie 0010
  (seconden). Zoeken op naam werkt meteen. De inhoud van documenten, de plekken en de hashes van
  foto's komen op de achtergrond (voor 20.000 documenten en 55.000 foto's ±15–20 minuten werk
  voor één core, geschat); de stand staat op `/beheer` → **Zoeken en foto's**;
- de eindtekst zegt dat, met de stand van de plaatsnamen en waar de reservekopie staat.

Daarna, op `/beheer` → **Zoeken en foto's** (Alt+9): de zoekmachine (`FTS5 (snel)`, of
`LIKE (langzamer)` tijdens een herbouw), hoeveel bestanden al gelezen zijn, wat mislukte, en de
knoppen **Index herbouwen**, **Mislukte opnieuw** en **Achtergrondwerk pauzeren**. Hoe snel zoeken
op jouw machine is, meet `dc exec -T platform python -m app.cli bench --los` (een paar minuten,
±1,1 GB tijdelijk in `/tank/platform/cache`); de uitkomst staat daarna op dezelfde tab, bij
**Zoekbench**.

Per schemaversie blijft de nieuwste kopie staan; de kopie van 0.7.1 (`platform-0009-…`) gaat dus
nooit weg door een volgende update. De kopie heeft geen gezichten en geen beeld (die rekent de app
na het terugzetten opnieuw), maar verder de hele database: bewaar hem net zo afgeschermd als de
`.env`. Hoe je hem terugzet, de index herbouwt en zoeken meet: `docs/runbooks/zoeken.md`.

**Terug naar 0.7.1.** Alleen als fase 6 niet goed werkt. Het zijn deze commando's, in deze
volgorde en met de volledige commit van 0.7.1 (`dc` uit Handige commando's hieronder; de uitleg
per stap staat in `docs/runbooks/zoeken.md`, "Terug naar 0.7.1"):

```bash
git -C /opt/platform/src fetch -q origin
git -C /opt/platform/src cat-file -e '80d5bb8ce9cd937534f06a5bc5e4f54ec8509ad1^{commit}' && echo "goed: 0.7.1 is er"
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/80d5bb8ce9cd937534f06a5bc5e4f54ec8509ad1/infra/single/install.sh -o /root/platform-install-0.7.1.sh && echo "goed: het script is er"
dc stop platform media
dc run --rm --no-deps platform python -m app.cli db-downgrade 0009
PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 PLATFORM_SRC_REF=80d5bb8ce9cd937534f06a5bc5e4f54ec8509ad1 bash < /root/platform-install-0.7.1.sh
```

Zeggen de tweede en de derde regel niet allebei "goed", stop dan: de rest pas als de commit en het
script er zijn. Het script komt van de commit van 0.7.1 zelf, niet van de branch: zo draait het
oude script met het oude image. `db-downgrade` noemt wat er weggaat (de zoekindex, de plekken van
foto's, de hashes voor duplicaten, de personen die je met de hand benoemde en de regels van slimme
albums) en vraagt om "ja"; bestanden, delingen en albums blijven. Weer vooruit: het commando van
de branch, zonder `PLATFORM_SRC_REF`:

```bash
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/claude/stappenplan-uitwerken-lu4uv3/infra/single/install.sh | PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 bash
```

## Bijwerken naar 0.7.1 (met media)

Versie 0.7.1 (fase 5, met de verbeteringen aan delen en foto's) staat nog niet op `main`, maar op
de branch
`claude/stappenplan-uitwerken-lu4uv3`. Tot hij op `main` staat, werk je zo bij (en geldt dit ook
voor elk ander commando hieronder met `curl … | bash`):

```bash
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/claude/stappenplan-uitwerken-lu4uv3/infra/single/install.sh | PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 bash
```

Het adres kiest het script en `PLATFORM_BRANCH` de code die het ophaalt; laat geen van beide
weg. Zonder `PLATFORM_BRANCH` haalt het script `main` op, en daar staat nog niets; draai het dan
gewoon opnieuw met `PLATFORM_BRANCH`. De eindtekst van het script noemt de commando's met het
adres en `PLATFORM_BRANCH` van de branch waarmee je het draaide; die kun je zo overnemen. Het
tabblad **Office** op `/beheer` noemt altijd de commando's van `main`: zet daar zolang hetzelfde
adres en `PLATFORM_BRANCH=…` in. Een instelling erbij gaat net zo:
`… | PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 OFFICE=uit bash`.

Wat er bij het bijwerken van 0.7.0 naar 0.7.1 gebeurt:

- het script laat open documenten opslaan, bouwt het nieuwe platform en start het; de database
  krijgt bij de start twee indexen (migratie 0009); `.env`, office en media blijven zoals ze waren;
- **delen**: kiezen deelt niet meer meteen. Je kiest eerst wie, dan de rechten ("Kan bekijken" of
  "Kan bewerken"), en pas **Delen** deelt; sluiten met iemand op de lijst vraagt eerst;
- **foto's**: elke foto krijgt een thumbnail, ook HEIC, RAW, TIFF, PSD en JPEG 2000 (of telt op
  `/beheer` als "zonder preview"); eerst de thumbnails, dan de grote versies, en wat iemand
  bekijkt gaat voor. `/beheer` → **Taken** heeft een statusregel **Foto's en video's** met
  **Alles nalopen** (ook `dc exec -T platform python -m app.cli previews-recheck`);
- terug naar 0.7.0: het schema eerst naar 0008, met de app gestopt (`docs/fotos-indexeren.md`,
  "Database: migratie 0009").

Wat er bij het bijwerken van 0.6.0 naar 0.7.0 gebeurt (van 0.6.0 komt 0.7.1 in dezelfde run):

- het script laat open documenten opslaan, bouwt het nieuwe platform en start het; de database
  gaat bij de start vanzelf naar het nieuwe schema;
- **media staat daarna aan**: `MEDIA_SANDBOX=aan`, `MAX_UPLOAD_BYTES` (50 GB) en
  `MEDIA_WORKER_KEY` komen in `/opt/platform/.env` (rechten 600), de mappen
  `/tank/platform/media` en `/tank/platform/import` komen erbij, en de mediaworker start als
  container `platform-media-1` na de app, uit hetzelfde image (er wordt niets extra
  opgehaald). De eindtekst zegt `Media: aan (afgeschermd).`, of waarom niet; lukt iets met media
  niet, dan stopt het script niet en werkt de rest door;
- **media doet niets met je bestanden** tot je het zelf gebruikt: geen bibliotheek, geen import,
  geen sleutel van TMDB, en MusicBrainz staat uit;
- daarna staan **Media** en **Muziek** in het menu, en `/beheer` heeft het tabblad **Media**
  (Alt+8). Een map wordt een bibliotheek met **Als bibliotheek gebruiken**; een bestaande
  verzameling komt binnen met de import (zie Media hieronder). Voorbeeldbeelden van video's maakt
  nu de mediaworker;
- office blijft zoals het was (aan of uit), net als inloggen, 2FA, je bestanden en hun versies.
  Liever zonder media: `/beheer` → Media → Instellingen → **Media aan** uitvinken. Terug naar
  0.6.0: zie Media hieronder ("Terug naar 0.6.0");
- daarna één keer op de LXC: de status, de meting, de belastingtest en een film op telefoon en
  laptop (de lijst in `docs/fase-5/README.md`, "Op de LXC"; de commando's in
  `docs/runbooks/media.md`).

## Van 0.5.0: office komt erbij (0.6.0)

Draai je nog 0.5.0 (fase 3), dan komt office in dezelfde run erbij, met het commando hierboven.

Kijk vóór het bijwerken één keer of Docker in de container office aankan (één commando, in
`docs/runbooks/office.md`, "Vóór het bijwerken naar 0.6.0").

Wat er bij het bijwerken van 0.5.0 naar 0.6.0 gebeurt:

- het script laat open werk opslaan (in 0.5.0 is er nog niets), bouwt het nieuwe platform en
  start het; de database gaat bij de start vanzelf naar het nieuwe schema;
- **office staat daarna aan**: `OFFICE=aan` en twee nieuwe geheimen komen in
  `/opt/platform/.env` (rechten 600), het image van OnlyOffice wordt opgehaald (±1 GB, eerst van
  Docker Hub, anders van `mirror.gcr.io`; het script wil 8 GB vrij) en start als tweede
  container. Liever niet: zet `OFFICE=uit` ervoor; het platform werkt dan precies als 0.5.0,
  en weer aan kan later met `OFFICE=aan`;
- na 2 à 3 minuten (de fonts van OnlyOffice; zolang zegt `/beheer` "Start op…") kun je
  bewerken: **Bewerken** bij docx, xlsx en pptx, **Nieuw** → Document, Werkblad of Presentatie,
  PDF-voorbeelden met opmaak, **Downloaden als PDF**, en het tabblad **Office** op `/beheer`;
- bestaande kantoordocumenten tot 20 MB krijgen op de achtergrond een PDF-voorbeeld, één voor
  één; OnlyOffice is dan een tijd druk (een groter document krijgt het als iemand erom vraagt);
- inloggen, 2FA, je bestanden en hun versies blijven gelijk. Terug naar 0.5.0: liever office uit;
  anders de stappen in het runbook ("De app terugrollen").

## Van buiten bereikbaar maken (HTTPS)

1. Laat je domein (bijv. `app.voorbeeld.nl`) met een A-record naar je publieke IP-adres
   wijzen en stuur in je router poort **80 en 443** door naar de container: publiek 80
   naar `PORT` en publiek 443 naar `HTTPS_PORT` van de container (standaard ook 80 en 443;
   zie `/opt/platform/.env`). Het installatiescript noemt aan het eind de juiste poorten.
2. Vul op `/beheer` bij **HTTPS** het domein in en klik op Opslaan.

Buiten moeten het echt poort 80 en 443 zijn, ook als je in de container andere poorten
gebruikt: Let's Encrypt controleert het domein altijd via poort 80, en het doorsturen van
`http://` naar `https://` gaat naar het domein zonder poortnummer, dus naar 443.

Het platform vraagt dan zelf een certificaat aan bij Let's Encrypt en vernieuwt het op tijd;
er hoeft niets herstart te worden. Lukt het niet, dan staat op `/beheer` waarom (meestal
DNS of de router) en probeert het platform het later vanzelf opnieuw. Gaat de https-poort
niet open (bijvoorbeeld omdat een ander programma hem gebruikt), dan blijft http gewoon
werken en staat ook dat op `/beheer`. Met **Testmodus** probeer je het eerst bij de
test-omgeving van Let's Encrypt; browsers vertrouwen dat certificaat niet, maar je loopt
dan niet tegen de limieten aan.

Zodra https werkt, gaat `http://<domein>` vanzelf naar `https://`. Via het IP-adres in je LAN
blijft http werken.

## Wat er draait

| Onderdeel | Waar |
|-----------|------|
| Platform (web-app en API) | poort `PORT` (http, standaard 80) en `HTTPS_PORT` (https, standaard 443) van de container, in Docker 8080 en 8443 |
| Database (SQLite) | `/tank/platform/platform.db` |
| Bestanden | `/tank/platform/blobs`, op inhoud opgeslagen en ontdubbeld |
| Uploads die nog lopen | `/tank/platform/uploads` |
| Certificaten | `/tank/platform/tls` |
| Instellingen | `/opt/platform/.env` |
| Office (OnlyOffice Docs) | de container `platform-office-1`, zonder eigen poort: alleen het platform praat ermee, over het interne netwerk `platform_office` |
| PDF-voorbeelden van documenten | `/tank/platform/office` |
| Gegevens van office | de Docker-volumes `platform_office-lib`, `platform_office-data`, `platform_office-logs` en `platform_office-fonts` (niet in `/tank`) |
| Instellingen van office | `/opt/platform/.env`: `OFFICE`, `OFFICE_JWT_SECRET`, `OFFICE_LINK_SECRET` en eventueel `OFFICE_IMAGE` (de pin) |
| Mediaworker (ffmpeg) | de container `platform-media-1`, zonder netwerk en zonder je bestanden: hij krijgt alleen wat de app hem geeft, over de socket in het Docker-volume `platform_media-run` |
| Cache van media | `/tank/platform/media` (standaard hooguit 20 GB) |
| Import | `/tank/platform/import`, in de app `/data/import` |
| Instellingen van media | `/opt/platform/.env`: `MEDIA_SANDBOX`, `MAX_UPLOAD_BYTES` en `MEDIA_WORKER_KEY`; de rest op `/beheer` → Media |
| Reservekopieën van de database | `/tank/platform/backup` (rechten 700): vóór elke update één, `platform-<schemaversie>-<tijd>.db` (rechten 600), zonder gezichten en beeld; per schemaversie blijft de nieuwste |
| Plaatsnamen en landen | `/tank/platform/geo`: de twee bronnen en `places.sqlite` |
| Instellingen van zoeken | `/opt/platform/.env`: `PLAATSEN`; de rest op `/beheer` → Zoeken en foto's |

Bijwerken: draai het installatiescript opnieuw (met de branch: zie hierboven). Het laat eerst de
open documenten opslaan, maakt een reservekopie van de database (vanaf fase 6), vervangt dan het
platform en werkt pas daarna de mediaworker en office bij. Had je de
eerste versie met Traefik, PostgreSQL en SeaweedFS al geïnstalleerd, dan ruimt het script die
containers op; de oude mappen `/tank/postgres`, `/tank/seaweedfs` en `/tank/traefik` mag je
daarna weghalen.

Handige commando's. Plak eerst dit in de console (of zet het in `/root/.bashrc`); `dc` is dan
`docker compose` met precies de bestanden die het installatiescript gebruikt:

```bash
dc() {
  local d=/opt/platform/src/infra/single
  local f=(-f "$d/compose.yaml")
  local limits
  limits=$(docker info --format '{{.MemoryLimit}} {{.CPUCfsQuota}}')
  if docker container inspect platform-office-1 >/dev/null 2>&1; then
    f+=(-f "$d/compose.office.yaml")
    if [ "$limits" = "true true" ]; then
      f+=(-f "$d/compose.office-limits.yaml")
    fi
  fi
  if docker container inspect platform-media-1 >/dev/null 2>&1; then
    f+=(-f "$d/compose.media.yaml")
    if [ "$limits" = "true true" ]; then
      f+=(-f "$d/compose.media-limits.yaml")
    fi
  elif [ -f "$d/compose.media-local.yaml" ] && grep -qx 'MEDIA_SANDBOX=uit' /opt/platform/.env; then
    f+=(-f "$d/compose.media-local.yaml")
  fi
  docker compose --project-directory "$d" --env-file /opt/platform/.env "${f[@]}" "$@"
}
```

```bash
dc ps
dc logs -f platform
dc logs --tail 100 office
dc logs --tail 100 media
dc exec -T platform python -m app.cli media-status --sessions
```

Iets veranderen (aan, uit, bijwerken, opnieuw starten) doe je met het installatiescript, niet
met `docker compose up` of `down`. Vooral nooit `docker compose up -d` met alleen
`compose.yaml`: dan start het platform zonder de instellingen van office en media (geen editor
en geen worker meer) en blijven DS en de worker los draaien. Draai daarna gewoon het
installatiescript opnieuw; dat zet het recht. Nooit `dc exec media` zonder `env -i` (zie
`docs/runbooks/media.md`).

## Office: documenten bewerken in de browser

Met office open je Word-, Excel- en PowerPoint-bestanden (en de ODF-varianten) in de browser,
ook met meer mensen tegelijk, en krijgen ze een PDF-voorbeeld. Het is OnlyOffice Docs (de
"DS") in een tweede container naast het platform. DS heeft geen eigen poort en geen uitgang
naar internet: alleen het platform praat ermee, en de browser komt er via het platform, dus
via dezelfde poorten 80 en 443.

**Wat het kost.** Het image is ±3,1 GB. Bij een update staan het oude en het nieuwe even naast
elkaar; daarom wil het script minstens 8 GB vrij voor Docker als het een image moet ophalen. DS krijgt hooguit 2 GB geheugen
en 2 cores, als de kernel van de container die grenzen kan zetten (anders zonder grens). In de
proef was de piek 894 MB. Na elke start maakt DS 2 à 5 minuten zijn fonts; dan is hij even
druk.

**Aan en uit.** Office staat standaard aan (`OFFICE=aan` in `/opt/platform/.env`). Uitzetten:

```bash
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/main/infra/single/install.sh | OFFICE=uit bash
```

Het script laat eerst de open documenten opslaan en haalt dan de container van DS weg. Het
platform werkt verder zoals in fase 3: geen knop Bewerken, voorbeelden zoals vroeger. De
volumes van DS blijven staan. Weer aanzetten: hetzelfde commando met `OFFICE=aan`.

Lukt het ophalen van het image niet (Docker Hub en `mirror.gcr.io` allebei onbereikbaar), of
start DS daarna niet, dan breekt er niets: draait er al een DS, dan blijft die op zijn versie;
anders start het platform zonder editor en zegt het script dat. Draai het later opnieuw.

**Waar de data staat.** De documenten zelf staan gewoon in het platform (`/tank/platform`):
wat je in de editor opslaat, komt in het bestand. De PDF-voorbeelden staan in
`/tank/platform/office`. DS bewaart zijn eigen spullen in vier Docker-volumes (zie de tabel
hierboven). Die staan niet in `/tank` en dus niet
in de ZFS-snapshot; er staat niets in dat het platform niet zelf ook heeft, behalve werk dat
nog niet opgeslagen is (hieronder).

**Niet-opgeslagen werk.** Kan DS een document niet aan het platform teruggeven (bijvoorbeeld
omdat het platform net bijgewerkt werd terwijl iemand aan het werk was), dan bewaart DS het in
het volume `platform_office-lib`. Het platform haalt zulke documenten vanzelf op: bij zijn
start, als DS weer gezond is, en verder elke 10 minuten. Is het bestand intussen niet
veranderd, dan komt het werk gewoon in het bestand; anders als herstelkopie bij de eigenaar.
Lukt ook dat niet (quotum vol, te groot), dan staat het op `/beheer` → Office onder **Niet
opgeslagen**, met **Terugzetten** en **Verwijderen**.

**Office start op.** Na elke start van DS (een update, office weer aan, een herstart van de
machine) antwoordt hij na 5 à 15 seconden, maar dan bouwt hij nog zijn fonts. Daarna herstart
hij zichzelf, 2 à 3 minuten na de start (op een drukke machine tot ±12 minuten), en wat dan
open is in de editor, is weg. Daarom wacht het platform op die herstart: zolang zegt `/beheer`
"Start op…", kan bewerken nog niet ("De editor is nu niet bereikbaar") en komen er geen nieuwe
PDF-voorbeelden. Een PDF-voorbeeld dat er al is en de rest van het platform werken gewoon. In
de eerste 5 minuten geeft `/beheer` geen melding als DS nog niet antwoordt. Ziet het platform
de herstart na 15 minuten nog niet, dan kan bewerken toch weer (met een waarschuwing in het
log van de app).

**Office onbereikbaar.** Meldt `/beheer` dat office onbereikbaar is (na 2 minuten), kijk dan:

```bash
dc ps office
dc logs --tail 100 office
```

- Draait de container niet, of start hij steeds opnieuw: draai het installatiescript opnieuw.
  Wat opgeslagen is, blijft. Wat in een open editor na het laatste tussentijds opslaan getypt
  was (hooguit ±5,5 minuten), is na een crash of geheugentekort van DS weg; werk dat DS nog had
  maar niet kwijt kon, staat op `/beheer` → Office onder "Niet opgeslagen".
- Meldt `/beheer` dat office verkeerd ingesteld is: het geheim van DS en dat van het platform
  verschillen. Beide komen uit `OFFICE_JWT_SECRET` in `/opt/platform/.env`; verander dat niet
  met de hand, en draai het installatiescript opnieuw.
- Te weinig geheugen: `docker inspect --format '{{.State.OOMKilled}}' platform-office-1` geeft
  dan `true`. Geef de container in Proxmox meer geheugen.

Meer, zoals terugrollen en het meten van de belasting, staat in `docs/runbooks/office.md`.

## Media: video en muziek

Met media worden mappen met films, series, video's of muziek een **bibliotheek**: met posters,
"verder kijken", ondertitels, een speler die op telefoon, laptop en tv werkt, en een muziekbalk
die doorspeelt terwijl je verder klikt. Kan een browser een bestand niet zelf afspelen, dan zet
het platform het tijdens het kijken om (met ffmpeg, op de CPU).

**De mediaworker.** ffmpeg leest vreemde bestanden, en daar zitten soms fouten in. Daarom draait
het in een eigen container, `platform-media-1`: zonder netwerk, zonder toegang tot je bestanden,
als een eigen gebruiker en met hooguit 3 GB geheugen en 3 cores (als de kernel die grenzen kan
zetten, zoals bij office). De app geeft hem per opdracht alleen het bestand dat nodig is. Het is
hetzelfde image als het platform; er wordt niets extra opgehaald.

**Aan en uit.** Media staat aan na het bijwerken, met de worker in zijn eigen container
(`MEDIA_SANDBOX=aan` in `/opt/platform/.env`). De eindtekst van het script zegt
`Media: aan (afgeschermd).`, of waarom niet. Uitzetten zonder iets weg te halen: `/beheer` →
**Media** → Instellingen → **Media aan**. Start de worker in zijn container niet, dan is er een
nooduitgang die ffmpeg in de app zelf laat draaien, zonder afscherming:

```bash
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/main/infra/single/install.sh | MEDIA_SANDBOX=uit bash
```

Alleen tijdelijk; weer terug met `MEDIA_SANDBOX=aan`. Lukt het starten van de worker niet, dan
stopt het script niet: het platform werkt verder, en video's spelen dan alleen als de browser het
bestand zelf kan.

**Uploads tot 50 GB.** Een film is groot: `MAX_UPLOAD_BYTES` in `.env` staat op 50 GB. Een hele
verzameling zet je er sneller in met de import (hieronder).

**Je verzameling importeren.** Zet de mappen op de Proxmox-host in `/tank/platform/import` en geef
ze daar aan het platform:

```bash
chown -R 110001:110001 <pad-op-de-host>/platform/import
```

(de container is unprivileged: uid 10001 in de app is 110001 op de host). Kijk in de LXC met
`ls -ln /tank/platform/import` of er `10001 10001` staat, en kies dan op `/beheer` → Media →
**Importeren** de map, de eigenaar en **Kopiëren** of **Verplaatsen**. Hoe je het pad op de host
vindt en de rest: `docs/runbooks/media.md`.

**Metadata.** Zonder meer haalt het platform titels, jaren en afleveringen uit de namen van je
bestanden en uit `.nfo`-bestanden. Met een (gratis) sleutel van TMDB komen er posters en
beschrijvingen bij; die zet je op `/beheer` → Media → Online metadata (versleuteld bewaard, nooit
in `.env`). Neem daar het API Read Access Token (de lange, die met `eyJ` begint). Versleuteld wil
zeggen: met een sleutel die uit het app-geheim in `/data` komt. Dat beschermt een losse kopie van
`platform.db`, niet een kopie van heel `/data` of een snapshot van `/tank/platform`: daar staan de
database en het geheim samen in. Bewaar die dus net zo afgeschermd als de `.env`.

**Terug naar 0.6.0.** Liever niet: zet media uit op `/beheer` (hierboven), dan werkt de rest
gewoon door. Moet de code echt terug, dan zijn het deze commando's, in deze volgorde en met de
volledige commit van 0.6.0 (`dc` uit Handige commando's hierboven; de uitleg per stap staat in
`docs/runbooks/media.md`, "Terug naar 0.6.0"):

```bash
git -C /opt/platform/src fetch -q origin
git -C /opt/platform/src cat-file -e '263abd74d914a9811901e7570ed410866364f4b5^{commit}' && echo "goed: 0.6.0 is er"
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/claude/stappenplan-uitwerken-lu4uv3/infra/single/install.sh -o /root/platform-install.sh && echo "goed: het script is er"
dc stop platform media
dc run --rm --no-deps platform python -m app.cli db-downgrade 0007
PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 PLATFORM_SRC_REF=263abd74d914a9811901e7570ed410866364f4b5 bash < /root/platform-install.sh
```

Zeggen de tweede en de derde regel niet allebei "goed", stop dan: de rest pas als de commit en het
script er zijn. Het script komt eerst in een bestand, vóór er iets stopt: `curl … | bash` met een
adres dat er niet is, geeft één regel van curl en doet verder niets, en dan stond de app al stil
met het schema terug. `db-downgrade` vraagt om "ja". Daarna zijn de bibliotheken, de voortgang en de
afspeellijsten weg; je bestanden, ook de films en muziek, blijven. Weer vooruit: het script zonder
`PLATFORM_SRC_REF`. Het adres is dat van de branch, zoals bij het bijwerken hierboven; staat 0.7.1
op `main`, dan mag `main` in het adres, zonder `PLATFORM_BRANCH`.

Meer, zoals de status lezen, de cache opruimen en de belastingtest op de LXC, staat in
`docs/runbooks/media.md`.

## 2FA kwijt

Ben je je telefoon én je herstelcodes kwijt, dan kan een andere beheerder je 2FA uitzetten
op `/beheer`. Ben je de enige beheerder, doe het dan in de console van de container:

```bash
cd /opt/platform/src/infra/single
docker compose --env-file /opt/platform/.env exec platform python -m app.cli reset-2fa jij@voorbeeld.nl
```

Daarna log je in met alleen je wachtwoord en zet je 2FA opnieuw aan. Hetzelfde helpt als je
2FA geblokkeerd is na te veel foute codes.

## Back-up

Alles staat in `/tank/platform`. Een ZFS-snapshot van het mount point op de Proxmox-host is
een geldige back-up: de database overleeft een snapshot zoals een stroomstoring. De volumes van
office horen er niet bij; wat daar nog niet opgeslagen is, zet het platform binnen enkele
minuten zelf in `/tank/platform` (zie "Niet-opgeslagen werk").
Vanaf fase 6 maakt het installatiescript vóór elke update ook een kopie van de database in
`/tank/platform/backup` (zie "Bijwerken naar fase 6"). Dat is een vangnet voor de update, geen
back-up: hij staat op dezelfde schijf.
Offsite-backups volgen in fase 1 deel 2.
