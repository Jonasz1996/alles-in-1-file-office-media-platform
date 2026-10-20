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
beheerdersaccount aan. Doe dat voordat je de poort in je router openzet, anders kan iemand
anders je voor zijn.

Een andere poort kiezen kan bij de eerste installatie met `PORT=8080 bash install.sh`, of
later in `/opt/platform/.env` (daarna het script opnieuw draaien).

Er is nog geen HTTPS. Gebruik het voorlopig alleen in je LAN; HTTPS met Let's Encrypt komt
in de app zelf.

## Wat er draait

| Onderdeel | Waar |
|-----------|------|
| Platform (web-app en API) | poort 80 van de container, in Docker poort 8080 |
| Database (SQLite) | `/tank/platform/platform.db` |
| Bestanden | `/tank/platform/blobs`, op inhoud opgeslagen en ontdubbeld |
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

## Back-up

Alles staat in `/tank/platform`. Een ZFS-snapshot van het mount point op de Proxmox-host is
een geldige back-up: de database overleeft een snapshot zoals een stroomstoring.
Offsite-backups volgen in fase 1 deel 2.
