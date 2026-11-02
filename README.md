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

## Bijwerken naar 0.7.0 (media)

Versie 0.7.0 is fase 5: films, series, eigen video's en muziek in het platform, met
bibliotheken, posters, "verder kijken", ondertitels, een speler voor telefoon, laptop en tv, en
een muziekbalk die doorspeelt terwijl je verder klikt. Wat de browser niet zelf kan afspelen,
zet het platform tijdens het kijken om, met ffmpeg in een eigen afgeschermde container. Zolang
deze versie nog niet op `main` staat, werk je bij vanaf de branch, in de Console van de container
(als root):

```bash
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/claude/stappenplan-uitwerken-lu4uv3/infra/single/install.sh | PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 bash
```

Het adres kiest het script, `PLATFORM_BRANCH` de code die het ophaalt; laat geen van beide weg.
De commando's in de eindtekst van het script hebben dan ook dat adres en `PLATFORM_BRANCH`; die
op `/beheer` → Office noemen `main`, dus zet daar zolang hetzelfde in.
Staat 0.7.0 op `main`, dan is het weer het gewone commando uit
[infra/single/README.md](infra/single/README.md), zonder `PLATFORM_BRANCH`.

- **Media staat na het bijwerken aan**, met de mediaworker in zijn eigen container
  (`platform-media-1`: zonder netwerk, zonder je bestanden, en hooguit 3 cores en 3 GB als de
  kernel die grenzen kan zetten). Het is hetzelfde image als het platform: er wordt niets extra
  opgehaald. Het script zet `MEDIA_SANDBOX=aan`, `MAX_UPLOAD_BYTES` (50 GB) en een eigen sleutel
  voor de worker in `/opt/platform/.env`, en de eindtekst zegt `Media: aan (afgeschermd).` of
  waarom niet. Lukt iets met media niet, dan stopt het script niet en werkt de rest gewoon door.
- **Media raakt niets aan tot je het zelf gebruikt:** geen bibliotheek, geen import, geen
  sleutel van TMDB, en MusicBrainz staat uit. Je bestanden en mappen blijven zoals ze waren.
- **Wat er voor jou verandert:** in het menu staan **Media** en **Muziek**; een map wordt een
  bibliotheek met **Als bibliotheek gebruiken** (films, series, muziek of video's), een bestaande
  verzameling komt binnen met de import op `/beheer` → **Media** (Alt+8), en video's spelen in de
  viewer en op `/kijken` met de nieuwe speler. Het tabblad Media toont de worker, wie er kijkt,
  de cache, de online metadata, de import, de instellingen en de belastingtest. De database gaat
  vanzelf naar het nieuwe schema; inloggen, 2FA en office blijven gelijk.
- **Liever zonder media:** `/beheer` → Media → Instellingen → **Media aan** uitvinken; de rest
  werkt dan gewoon door. Terug naar de code van 0.6.0 kan ook, maar liever niet; de stappen
  staan in het runbook ([docs/runbooks/media.md](docs/runbooks/media.md), "Terug naar 0.6.0").
- **Daarna op de LXC (±30 minuten):** de status, de meting, de belastingtest met de knop, en een
  film op telefoon en laptop. De lijst staat in [docs/fase-5/README.md](docs/fase-5/README.md)
  ("Op de LXC"), de commando's in het runbook.
- **Kom je nog van 0.5.0,** dan komt office (0.6.0) er in dezelfde run bij: `OFFICE=aan`, twee
  geheimen in `.env`, het image van OnlyOffice (±1 GB downloaden, ±3,1 GB op schijf; het script
  wil 8 GB vrij). Liever zonder office: `… | PLATFORM_BRANCH=… OFFICE=uit bash`. Eerst één
  controle op de container: [docs/runbooks/office.md](docs/runbooks/office.md), "Vóór het
  bijwerken naar 0.6.0"; wat office is: [docs/fase-4/README.md](docs/fase-4/README.md).

Wat er precies is, de grenzen en wat nog niet kan: [docs/fase-5/README.md](docs/fase-5/README.md).

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
[docs/fase-4/e2e.md](docs/fase-4/e2e.md), die van media (met echte ffmpeg; `-m media`) in
[docs/fase-5/e2e.md](docs/fase-5/e2e.md). De HTTPS-test tegen Pebble (de
test-CA van Let's Encrypt) heeft Go nodig: `cd api && eval "$(tests/pebble/start.sh)" && uv run pytest tests/test_acme_pebble.py`.

## Voortgang

| Fase | Onderwerp | Status |
|------|-----------|--------|
| 0 | Kaders: beslissingen, monorepo, CI-skelet | klaar op de inventaris na, zie [docs/fase-0](docs/fase-0/beslissingen.md) |
| 1 | Infrastructuur (3 nodes, failover) | deel 1 klaar om uit te rollen, zie [infra/README.md](infra/README.md) |
| 2 | Core backend | deel 1 t/m 3 klaar: standalone app met login, 2FA, sessies, bestanden, hervatbare uploads, versies, zoeken, tags, previews, live updates, delen met personen, groepen en links, HTTPS en beheer, zie [docs/fase-2](docs/fase-2/README.md) |
| 3 | Web-app | klaar (versie 0.5.0): bestandsbrowser voor grote mappen, mapuploads, viewers, fototijdlijn en albums, beheer, account, delen, prullenbak en versies, Nederlands en Engels, PWA, zie [docs/fase-3](docs/fase-3/README.md) |
| 4 | Office | klaar (versie 0.6.0): docx, xlsx en pptx samen bewerken in de browser met OnlyOffice als tweede container (standaard aan, `OFFICE=uit` zet het uit), opslaan als nieuwe versie, PDF-voorbeelden en Downloaden als PDF, Nieuw document en het tabblad Office op `/beheer`, zie [docs/fase-4](docs/fase-4/README.md) |
| 5 | Media en streaming | klaar in de sandbox (versie 0.7.0); het laatste bewijs is de meting op de LXC: bibliotheken met scanner en import, direct play, remux en omzetten in een afgeschermde worker (alleen de CPU), ondertitels, sprites en hoofdstukken, een eigen speler, kijkvoortgang tussen apparaten, muziek met gapless, metadata van TMDB en MusicBrainz (uit tot je een sleutel geeft) en het tabblad Media op `/beheer`, zie [docs/fase-5](docs/fase-5/README.md) |
| 6 | Zoeken en foto's | |
| 7 | Sync, delen en integraties | |
| 8 | Hardening en productie | |
| 9 | Apps (LG TV, Windows, Android) | |
