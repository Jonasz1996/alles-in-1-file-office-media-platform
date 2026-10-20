# Eerste versie op één node

Zonder cluster, zonder SSH en zonder Ansible: alles gebeurt in de console van één
Debian- of Ubuntu-container. Het cluster van 3 nodes (`infra/ansible`) komt later; de data
gaat dan mee.

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

Het script vraagt een domeinnaam. Laat die leeg om eerst alleen in je LAN te testen
(`http://<ip-van-de-container>`). Met een domein vraagt Traefik zelf een certificaat aan bij
Let's Encrypt; daarvoor moet het domein naar je publieke IP wijzen en moet je router poort
80 en 443 naar de container doorsturen.

Aan het eind toont het script het adres en het **beheertoken** voor `/beheer`.

## Wat er draait

| Dienst | Poort | Bereikbaar |
|--------|-------|------------|
| Traefik | 80, 443 | van buiten |
| web-app | 8080 | via Traefik |
| API | 8000 | alleen lokaal, via Traefik |
| PostgreSQL 17 | 5432 | alleen lokaal |
| SeaweedFS (master, volume, filer, S3) | 9333, 8081, 8888, 8333 | alleen lokaal |

Instellingen en geheimen staan in `/opt/platform/.env`, data in `/tank`.
Bijwerken: draai het installatiescript opnieuw.

Handige commando's:

```bash
cd /opt/platform/src/infra/single
docker compose --env-file /opt/platform/.env ps
docker compose --env-file /opt/platform/.env logs -f api
```
