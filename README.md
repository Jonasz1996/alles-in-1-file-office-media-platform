# alles-in-1-file-office-media-platform

Eén self-hosted platform voor bestanden, office-bewerking en mediastreaming
(de rol van Nextcloud, OnlyOffice en Plex samen), op 3 nodes.
Het volledige plan staat in [docs/stappenplan.md](docs/stappenplan.md).

## Structuur

| Map | Inhoud |
|-----|--------|
| `api/` | Kern-API in Python 3.12 + FastAPI |
| `web/` | Web-app in React + TypeScript + Vite |
| `infra/` | Infrastructuur voor de 3 nodes: LXC, Docker Compose en Ansible (fase 1) |
| `apps/windows`, `apps/android`, `apps/webos` | Apps (fase 9) |
| `docs/` | Stappenplan en beslissingen per fase |

## Eerste versie installeren

Op één Proxmox-container, vanuit de console: zie [infra/single/README.md](infra/single/README.md).

## Lokaal draaien

Vereist: [uv](https://docs.astral.sh/uv/), Node 22 en Docker.

```bash
# API op http://localhost:8000 (docs op /api/docs), data in api/data
cd api && uv sync && uv run uvicorn app.main:app --reload --port 8000

# Web-app met hot reload op http://localhost:5173, praat via een proxy met de API
cd web && npm install && npm run dev

# Of alles als één container, zoals in productie, op http://localhost:8080
docker compose up --build
```

Bij het eerste bezoek maak je het beheerdersaccount aan en zet je 2FA aan (verplicht).

Tests: `cd api && uv run pytest` en `cd web && npm test`. De HTTPS-test tegen Pebble (de
test-CA van Let's Encrypt) heeft Go nodig: `cd api && eval "$(tests/pebble/start.sh)" && uv run pytest tests/test_acme_pebble.py`.

## Voortgang

| Fase | Onderwerp | Status |
|------|-----------|--------|
| 0 | Kaders: beslissingen, monorepo, CI-skelet | klaar op de inventaris na, zie [docs/fase-0](docs/fase-0/beslissingen.md) |
| 1 | Infrastructuur (3 nodes, failover) | deel 1 klaar om uit te rollen, zie [infra/README.md](infra/README.md) |
| 2 | Core backend | deel 1 t/m 3 klaar: standalone app met login, 2FA, sessies, bestanden, hervatbare uploads, versies, zoeken, tags, previews, live updates, delen met personen, groepen en links, HTTPS en beheer, zie [docs/fase-2](docs/fase-2/README.md) |
| 3 | Web-app | basis staat (inloggen, bestandsbrowser, beheer) |
| 4 | Office | |
| 5 | Media en streaming | |
| 6 | Zoeken en foto's | |
| 7 | Sync, delen en integraties | |
| 8 | Hardening en productie | |
| 9 | Apps (LG TV, Windows, Android) | |
