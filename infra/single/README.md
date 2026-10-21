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

Het script installeert Docker, bouwt het platform en start het op poort 80. Open daarna
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

Bijwerken: draai het installatiescript opnieuw. Had je de eerste versie met Traefik,
PostgreSQL en SeaweedFS al geïnstalleerd, dan ruimt het script die containers op; de oude
mappen `/tank/postgres`, `/tank/seaweedfs` en `/tank/traefik` mag je daarna weghalen.

Handige commando's:

```bash
cd /opt/platform/src/infra/single
docker compose --env-file /opt/platform/.env ps
docker compose --env-file /opt/platform/.env logs -f platform
```

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
een geldige back-up: de database overleeft een snapshot zoals een stroomstoring.
Offsite-backups volgen in fase 1 deel 2.
