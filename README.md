# alles-in-1-file-office-media-platform

Eén self-hosted platform voor bestanden, office-bewerking en mediastreaming
(de rol van Nextcloud, OnlyOffice en Plex samen), op 3 nodes.
Het volledige plan staat in [docs/stappenplan.md](docs/stappenplan.md).

## Structuur

| Map | Inhoud |
|-----|--------|
| `api/` | Kern-API in Python 3.12 + FastAPI |
| `web/` | Web-app in React + TypeScript + Vite |
| `infra/` | Infrastructuur voor de 3 nodes (fase 1) |
| `apps/windows`, `apps/android`, `apps/webos` | Apps (fase 9) |
| `docs/` | Stappenplan en beslissingen per fase |

## Lokaal draaien

Vereist: [uv](https://docs.astral.sh/uv/), Node 22 en Docker.

```bash
# API op http://localhost:8000 (docs op /api/docs)
cd api && uv sync && uv run uvicorn app.main:app --reload

# Web-app op http://localhost:5173, praat via een proxy met de API
cd web && npm install && npm run dev

# Of beide als containers (API op :8000, web op :8080)
docker compose up --build
```

Tests: `cd api && uv run pytest` en `cd web && npm test`.

## Voortgang

| Fase | Onderwerp | Status |
|------|-----------|--------|
| 0 | Kaders: beslissingen, monorepo, CI-skelet | bezig, zie [docs/fase-0](docs/fase-0/beslissingen.md) |
| 1 | Infrastructuur (3 nodes, failover) | |
| 2 | Core backend | |
| 3 | Web-app | |
| 4 | Office | |
| 5 | Media en streaming | |
| 6 | Zoeken en foto's | |
| 7 | Sync, delen en integraties | |
| 8 | Hardening en productie | |
| 9 | Apps (LG TV, Windows, Android) | |
