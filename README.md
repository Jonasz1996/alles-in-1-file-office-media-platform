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

## Bijwerken naar 0.6.0 (office)

Versie 0.6.0 is fase 4: Word-, Excel- en PowerPoint-bestanden bewerken in de browser, ook met
meer mensen tegelijk, met OnlyOffice als tweede container naast het platform. Zolang deze versie
nog niet op `main` staat, werk je bij vanaf de branch, in de Console van de container (als root):

```bash
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/claude/stappenplan-uitwerken-lu4uv3/infra/single/install.sh | PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 bash
```

Het adres kiest het script, `PLATFORM_BRANCH` de code die het ophaalt; laat geen van beide weg.
Staat 0.6.0 op `main`, dan is het weer het gewone commando uit
[infra/single/README.md](infra/single/README.md), zonder `PLATFORM_BRANCH`.

- **Office staat na het bijwerken aan.** Het script zet `OFFICE=aan` en twee nieuwe geheimen in
  `/opt/platform/.env`, haalt het image van OnlyOffice op (±1 GB downloaden, ±3,1 GB op schijf;
  het wil 8 GB vrij) en start het naast het platform, zonder eigen poort.
- **Liever zonder office:** zet `OFFICE=uit` ervoor (`… | PLATFORM_BRANCH=… OFFICE=uit bash`).
  Het platform werkt dan precies als 0.5.0; weer aan met `OFFICE=aan`.
- **Wat er voor jou verandert:** na 2 à 3 minuten (OnlyOffice bouwt eerst zijn fonts; `/beheer`
  zegt zolang "Start op…") staat er **Bewerken** bij docx, xlsx en pptx (ook met E), **Nieuw**
  → Document, Werkblad of Presentatie (ook met O), een PDF-voorbeeld met opmaak voor
  kantoordocumenten (ook doc, xls, ppt en rtf), **Downloaden als PDF**, en het tabblad
  **Office** op `/beheer` (Alt+7). Opslaan in de editor maakt een nieuwe versie van het bestand.
  Bestaande documenten tot 20 MB krijgen op de achtergrond hun PDF-voorbeeld; OnlyOffice is dan
  een tijd druk. De database gaat vanzelf naar het nieuwe schema; inloggen en 2FA blijven gelijk.
- **Vooraf:** één controle op de container, in het runbook
  ([docs/runbooks/office.md](docs/runbooks/office.md), "Vóór het bijwerken naar 0.6.0").
  Terug naar 0.5.0 kan, maar liever office uit; de stappen staan in het runbook ("De app
  terugrollen").

Wat er precies is en wat nog niet kan: [docs/fase-4/README.md](docs/fase-4/README.md).

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

Tests: `cd api && uv run pytest` en `cd web && npm test`; de e2e-tests met Playwright staan in
[docs/fase-3/e2e.md](docs/fase-3/e2e.md), die van office (met een nep- en een echte OnlyOffice) in
[docs/fase-4/e2e.md](docs/fase-4/e2e.md). De HTTPS-test tegen Pebble (de
test-CA van Let's Encrypt) heeft Go nodig: `cd api && eval "$(tests/pebble/start.sh)" && uv run pytest tests/test_acme_pebble.py`.

## Voortgang

| Fase | Onderwerp | Status |
|------|-----------|--------|
| 0 | Kaders: beslissingen, monorepo, CI-skelet | klaar op de inventaris na, zie [docs/fase-0](docs/fase-0/beslissingen.md) |
| 1 | Infrastructuur (3 nodes, failover) | deel 1 klaar om uit te rollen, zie [infra/README.md](infra/README.md) |
| 2 | Core backend | deel 1 t/m 3 klaar: standalone app met login, 2FA, sessies, bestanden, hervatbare uploads, versies, zoeken, tags, previews, live updates, delen met personen, groepen en links, HTTPS en beheer, zie [docs/fase-2](docs/fase-2/README.md) |
| 3 | Web-app | klaar (versie 0.5.0): bestandsbrowser voor grote mappen, mapuploads, viewers, fototijdlijn en albums, beheer, account, delen, prullenbak en versies, Nederlands en Engels, PWA, zie [docs/fase-3](docs/fase-3/README.md) |
| 4 | Office | klaar (versie 0.6.0): docx, xlsx en pptx samen bewerken in de browser met OnlyOffice als tweede container (standaard aan, `OFFICE=uit` zet het uit), opslaan als nieuwe versie, PDF-voorbeelden en Downloaden als PDF, Nieuw document en het tabblad Office op `/beheer`, zie [docs/fase-4](docs/fase-4/README.md) |
| 5 | Media en streaming | |
| 6 | Zoeken en foto's | |
| 7 | Sync, delen en integraties | |
| 8 | Hardening en productie | |
| 9 | Apps (LG TV, Windows, Android) | |
