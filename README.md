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

## Bijwerken naar 0.8.0 (zoeken en foto's)

Versie 0.8.0 is de kern van fase 6. Je zoekt nu ook in wat er ín je documenten staat. Dat werkt
ook bij scans: die leest het platform met OCR (tekstherkenning: het haalt de letters uit een
plaatje).

Je foto's met een GPS-plek krijgen een land, en waar het kan een plaats. Er is een kaart, een
pagina voor dubbele bestanden (`/dubbel`) en er zijn filters op `/fotos`. Personen wijs je zelf
aan op een foto. Herinneringen laten foto's zien van "Vandaag, N jaar geleden". Een slim album
vult zich zelf, met een regel die je kiest. Deel je een foto waar de plek in het bestand staat,
dan zegt het deelvenster dat wie hem downloadt die plek meekrijgt.

Gezichten vanzelf herkennen zit er nog niet in; dat staat gepland voor 0.8.1. Zoeken op wat er op
een foto staat ("strand", "hond") ook nog niet. Dat staat ook gepland voor 0.8.1, maar kan naar
een eigen stap (fase 6b) schuiven.

Bij 95 van de 100 zoekopdrachten over 100.000 bestanden kwam het antwoord in de meting binnen
126,7 ms;
de grens is 200 ms.

Versie 0.8.0 staat nog niet op `main`, maar op de branch `claude/stappenplan-uitwerken-lu4uv3`.
Zolang dat zo is, werk je bij vanaf de branch, in de Console van de container (als root):

```bash
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/claude/stappenplan-uitwerken-lu4uv3/infra/single/install.sh | PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 bash
```

Het adres kiest het script, `PLATFORM_BRANCH` de code die het ophaalt; laat geen van beide weg.
De commando's in de eindtekst van het script hebben dan ook dat adres en `PLATFORM_BRANCH`; die
op `/beheer` → Office noemen `main`, dus zet daar zolang hetzelfde in.
Staat 0.8.0 op `main`, dan is het weer het gewone commando uit
[infra/single/README.md](infra/single/README.md), zonder `PLATFORM_BRANCH`.

Het script maakt eerst een reservekopie van de database (in `/tank/platform/backup`, zonder
`/tank` in `/opt/platform/data/platform/backup`; mislukt die, dan stopt het vóór er iets
verandert), haalt de plaatsnamen op (`PLAATSEN=aan`, ±16 MB; mislukt dat, dan gaat het gewoon
door) en zegt aan het eind waar de kopie staat. Zoeken op naam werkt meteen. De inhoud van
documenten, de plekken van foto's en hun hashes (een soort vingerafdruk van het beeld, om dubbele
foto's te vinden) komen op de achtergrond, te volgen op `/beheer` → **Zoeken en foto's** (Alt+9).
Wat er gebeurt en de weg terug naar 0.7.1: [infra/single/README.md](infra/single/README.md)
("Bijwerken naar 0.8.0 (zoeken en foto's)"); de stand lezen, de zoekindex herbouwen, zoeken meten
en terug: [docs/runbooks/zoeken.md](docs/runbooks/zoeken.md).

Daarna loop je op je eigen server (de container) één keer de lijst "Op de LXC" na, in
[docs/fase-6/README.md](docs/fase-6/README.md#op-de-lxc).

### Zoeken werkt anders dan in 0.7.1

- Een naam wordt in woorden geknipt: `foto-0001` zoekt "foto" en "0001", en vindt dus ook
  `foto-00001`.
- De beste treffer staat bovenaan; zonder zoekwoord komen de nieuwste eerst.
- Het soort **Overig** neemt nu ook archieven mee.
- Een pagina heeft 30 treffers; **Meer laden** haalt de volgende 30. Een script dat nog de vraag
  van 0.7.x stuurt (meer dan 50 tegelijk, of het soort `archive`), krijgt nu een foutmelding.
- Het knippen in woorden en de nieuwe volgorde werken via de zoekindex. Tijdens
  **Index herbouwen** (op `/beheer`) zoekt het platform even zoals in 0.7.1: alleen in de naam,
  met eerst de mappen en dan de rest op naam.

### Bekende grenzen van 0.8.0

Alles staat in [docs/fase-6/README.md](docs/fase-6/README.md); de belangrijkste:

- **Gezichten en zoeken op beeld** zitten er nog niet in (zie hierboven).
- **Een pagina `/over` is er nog niet.** De bronnen van de plaatsnamen en de landen staan onder de
  kaart op `/fotos/kaart`, en hieronder bij Bronnen.
- **"Niet gelijk" op `/dubbel` kan niet ongedaan:** die groep komt niet meer terug.
- **Herinneringen per map verbergen** kan in de web-app nog niet; per dag en per persoon wel.
- **Land en Plaats in de filters op `/fotos`** kennen hooguit 500 plaatsen (die met de meeste
  foto's eerst); wie meer plaatsen heeft, mist de kleinste plaatsen en soms een land. **Camera**
  is een tekstveld.
- **Een kader om een persoon** blijft op dezelfde plek staan als je de foto door een nieuwe
  versie vervangt.
- **OCR:** gescande PDF's leest het vanzelf, foto's alleen met **Tekst herkennen** of als je onder
  Account → Foto's "Tekst in foto's herkennen" aanzet. Een volle liggende scan kost ±160 minuten
  per 1.000 pagina's (rechte scans ±60). Upload je een foto die er al was (dezelfde inhoud), dan
  leest het die pas na de volgende start. Wie een PDF alleen mag lezen, ziet niet of OCR klaar is;
  de tekst vindt hij wel. OCR werkt alleen met media aan (de standaard).
- **De kaart van de hele wereld met een filter op soort, map, camera, persoon of tag** kan tot een
  halve seconde duren; met alleen datum, plaats of land blijft hij onder 150 ms.
- **Meer dan 64 gedeelde mappen, of een filter op foto's met alleen een datum,** kan een half
  of leeg antwoord geven; de pagina zegt dat dan.
- **Een upload wacht hooguit 1 seconde op het werk op de achtergrond.** Dat lukt op een schijf
  waar het vastleggen van een schrijfactie (`fsync`) tot ±0,2 s extra kost; bij ±0,3 s niet meer.
- **Zoeken op PostgreSQL** gaat zonder zoekindex, alleen op naam zoals in 0.7.0 (langzamer), en is
  niet getest; de wekelijkse controle van de zoekindex draait alleen op SQLite.

## Eerder: 0.7.1 en 0.7.0 (media, delen en foto's)

Draai je nog 0.7.0 of ouder, dan komt dit met het commando van 0.8.0 hierboven in dezelfde run
erbij. Versie 0.7.1 is 0.7.0 met twee verbeteringen:

- **"Kan bewerken" werkt nu bij delen.** Je kiest eerst wie, dan de rechten ("Kan bekijken" of
  "Kan bewerken"), en pas **Delen** deelt. Eerder deelde kiezen meteen met "Kan bekijken", en
  deed "Kan bewerken" daarna niets meer. Sluiten met iemand op de lijst vraagt eerst.
- **Elke foto een thumbnail**, ook HEIC, RAW, TIFF, PSD en JPEG 2000; lukt het niet (een kapot
  bestand), dan telt `/beheer` hem als "zonder preview". Eerst de thumbnails, daarna de grote
  versies; wat je op Foto's, in een map of in de viewer bekijkt, gaat voor. Op `/beheer` → Taken
  staat een statusregel voor foto's en video's, met **Alles nalopen**. Hoe het werkt:
  [docs/fotos-indexeren.md](docs/fotos-indexeren.md).

De database krijgt twee indexen (migratie 0009; die loopt vanzelf bij het starten). Terug naar
0.7.0 kan; de stappen staan in [docs/fotos-indexeren.md](docs/fotos-indexeren.md) ("Database:
migratie 0009").

Versie 0.7.0 is fase 5: films, series, eigen video's en muziek in het platform, met
bibliotheken, posters, "verder kijken", ondertitels, een speler voor telefoon, laptop en tv, en
een muziekbalk die doorspeelt terwijl je verder klikt. Wat de browser niet zelf kan afspelen,
zet het platform tijdens het kijken om, met ffmpeg in een eigen afgeschermde container. Kom je
van 0.6.0 of ouder, dan geldt ook dit:

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
[docs/fase-5/e2e.md](docs/fase-5/e2e.md), die van zoeken en foto's (de markers `zoek` en `ocr`,
de snelle set `e2e/slim.sh` en de meetscripts `e2e/perf/zoek_*.py`) in
[docs/fase-6/e2e.md](docs/fase-6/e2e.md). De HTTPS-test tegen Pebble (de
test-CA van Let's Encrypt) heeft Go nodig: `cd api && eval "$(tests/pebble/start.sh)" && uv run pytest tests/test_acme_pebble.py`.

## Bronnen

- **Plaatsnamen**: [GeoNames](https://www.geonames.org/) (`cities1000`, als `rg_cities1000.csv`
  uit reverse_geocoder 1.5.1), onder [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- **Landen en de wereldkaart**: [Natural Earth](https://www.naturalearthdata.com/) (de landen
  1:10m admin-0 v5.1.2, de kaart 1:110m), publiek domein.

De bronnen staan niet in de repo: de app bouwt er bij de start `/data/geo/places.sqlite` van (of
`app.cli geo-load`). De kaart op `/fotos/kaart` noemt ze onder de kaart; een pagina `/over` met
deze vermelding komt nog (zie [docs/fase-6/z3.md](docs/fase-6/z3.md), Open).

## Voortgang

| Fase | Onderwerp | Status |
|------|-----------|--------|
| 0 | Kaders: beslissingen, monorepo, CI-skelet | klaar op de inventaris na, zie [docs/fase-0](docs/fase-0/beslissingen.md) |
| 1 | Infrastructuur (3 nodes, failover) | deel 1 klaar om uit te rollen, zie [infra/README.md](infra/README.md) |
| 2 | Core backend | deel 1 t/m 3 klaar: standalone app met login, 2FA, sessies, bestanden, hervatbare uploads, versies, zoeken, tags, previews, live updates, delen met personen, groepen en links, HTTPS en beheer, zie [docs/fase-2](docs/fase-2/README.md) |
| 3 | Web-app | klaar (versie 0.5.0): bestandsbrowser voor grote mappen, mapuploads, viewers, fototijdlijn en albums, beheer, account, delen, prullenbak en versies, Nederlands en Engels, PWA, zie [docs/fase-3](docs/fase-3/README.md) |
| 4 | Office | klaar (versie 0.6.0): docx, xlsx en pptx samen bewerken in de browser met OnlyOffice als tweede container (standaard aan, `OFFICE=uit` zet het uit), opslaan als nieuwe versie, PDF-voorbeelden en Downloaden als PDF, Nieuw document en het tabblad Office op `/beheer`, zie [docs/fase-4](docs/fase-4/README.md) |
| 5 | Media en streaming | klaar in de sandbox (versie 0.7.0); het laatste bewijs is de meting op de LXC: bibliotheken met scanner en import, direct play, remux en omzetten in een afgeschermde worker (alleen de CPU), ondertitels, sprites en hoofdstukken, een eigen speler, kijkvoortgang tussen apparaten, muziek met gapless, metadata van TMDB en MusicBrainz (uit tot je een sleutel geeft) en het tabblad Media op `/beheer`, zie [docs/fase-5](docs/fase-5/README.md) |
| 6 | Zoeken en foto's | kern gebouwd (versie 0.8.0): zoeken in de inhoud van documenten en scans (OCR), plaatsnamen en een kaart zonder kaartdienst van buiten, duplicaten, personen met de hand, herinneringen, slimme albums en filters, en het tabblad Zoeken en foto's op `/beheer`; gezichten vanzelf en zoeken op beeld volgen, zie [docs/fase-6](docs/fase-6/README.md) |
| 7 | Sync, delen en integraties | |
| 8 | Hardening en productie | |
| 9 | Apps (LG TV, Windows, Android) | |
