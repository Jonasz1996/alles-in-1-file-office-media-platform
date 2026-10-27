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
(OnlyOffice Docs, zie Office hieronder) als tweede container ernaast. Open daarna
`http://<ip-van-de-container>` in je browser: bij het eerste bezoek maak je het
beheerdersaccount aan en zet je meteen 2FA aan (verplicht; houd een authenticator-app zoals
Aegis of Google Authenticator bij de hand). Doe dat voordat je de poort in je router
openzet, anders kan iemand anders je voor zijn.

Een andere poort kiezen kan bij de eerste installatie met `PORT=8080 bash install.sh`, of
later in `/opt/platform/.env` (daarna het script opnieuw draaien).

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

Bijwerken: draai het installatiescript opnieuw. Het laat eerst de open documenten opslaan,
vervangt dan het platform en werkt pas daarna office bij. Had je de eerste versie met Traefik,
PostgreSQL en SeaweedFS al geïnstalleerd, dan ruimt het script die containers op; de oude
mappen `/tank/postgres`, `/tank/seaweedfs` en `/tank/traefik` mag je daarna weghalen.

Handige commando's. Plak eerst dit in de console (of zet het in `/root/.bashrc`); `dc` is dan
`docker compose` met precies de bestanden die het installatiescript gebruikt:

```bash
dc() {
  local d=/opt/platform/src/infra/single
  local f=(-f "$d/compose.yaml")
  if docker container inspect platform-office-1 >/dev/null 2>&1; then
    f+=(-f "$d/compose.office.yaml")
    if [ "$(docker info --format '{{.MemoryLimit}} {{.CPUCfsQuota}}')" = "true true" ]; then
      f+=(-f "$d/compose.office-limits.yaml")
    fi
  fi
  docker compose --project-directory "$d" --env-file /opt/platform/.env "${f[@]}" "$@"
}
```

```bash
dc ps
dc logs -f platform
dc logs --tail 100 office
```

Iets veranderen (aan, uit, bijwerken, opnieuw starten) doe je met het installatiescript, niet
met `docker compose up` of `down`. Vooral nooit `docker compose up -d` met alleen
`compose.yaml`: dan start het platform zonder de instellingen van office (geen editor meer) en
blijft DS los draaien. Draai daarna gewoon het installatiescript opnieuw; dat zet het recht.

## Office: documenten bewerken in de browser

Met office open je Word-, Excel- en PowerPoint-bestanden (en de ODF-varianten) in de browser,
ook met meer mensen tegelijk, en krijgen ze een PDF-voorbeeld. Het is OnlyOffice Docs (de
"DS") in een tweede container naast het platform. DS heeft geen eigen poort en geen uitgang
naar internet: alleen het platform praat ermee, en de browser komt er via het platform, dus
via dezelfde poorten 80 en 443.

**Wat het kost.** Het image is ±3,1 GB. Bij een update staan het oude en het nieuwe even naast
elkaar; daarom wil het script minstens 8 GB vrij voor Docker. DS krijgt hooguit 2 GB geheugen
en 2 cores, als de kernel van de container die grenzen kan zetten (anders zonder grens). In de
proef was de piek 894 MB. Na de eerste start van een nieuwe DS maakt hij ±5 minuten zijn
fonts; dan is hij even druk.

**Aan en uit.** Office staat standaard aan (`OFFICE=aan` in `/opt/platform/.env`). Uitzetten:

```bash
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/main/infra/single/install.sh | OFFICE=uit bash
```

Het script laat eerst de open documenten opslaan en haalt dan de container van DS weg. Het
platform werkt verder zoals in fase 3: geen knop Bewerken, voorbeelden zoals vroeger. De
volumes van DS blijven staan. Weer aanzetten: hetzelfde commando met `OFFICE=aan`.

Lukt het ophalen van het image niet (Docker Hub en `mirror.gcr.io` allebei onbereikbaar), dan
breekt er niets: draait er al een DS, dan blijft die op zijn versie; anders start het platform
zonder editor en zegt het script dat. Draai het later opnieuw.

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

**Office start op.** Na een start of update is DS na 5 à 15 seconden gezond; bij de eerste start
van een nieuw image duurt het door de fonts een paar minuten langer. Zolang kan bewerken nog
niet, maar het PDF-voorbeeld en de rest van het platform werken gewoon. In de eerste 5 minuten
geeft `/beheer` daar nog geen melding over.

**Office onbereikbaar.** Meldt `/beheer` dat office onbereikbaar is (na 2 minuten), kijk dan:

```bash
dc ps office
dc logs --tail 100 office
```

- Draait de container niet, of start hij steeds opnieuw: draai het installatiescript opnieuw.
  Niets van de documenten gaat verloren; open werk komt via "Niet-opgeslagen werk" terug.
- Meldt `/beheer` dat office verkeerd ingesteld is: het geheim van DS en dat van het platform
  verschillen. Beide komen uit `OFFICE_JWT_SECRET` in `/opt/platform/.env`; verander dat niet
  met de hand, en draai het installatiescript opnieuw.
- Te weinig geheugen: `docker inspect --format '{{.State.OOMKilled}}' platform-office-1` geeft
  dan `true`. Geef de container in Proxmox meer geheugen.

Meer, zoals terugrollen en het meten van de belasting, staat in `docs/runbooks/office.md`.

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
Offsite-backups volgen in fase 1 deel 2.
