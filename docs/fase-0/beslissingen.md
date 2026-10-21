# Fase 0 — beslissingen

Status per beslissing: **voorstel** (overgenomen uit het stappenplan, nog te bevestigen),
**besloten** of **open**. Fase 1 start pas als elke regel *besloten* is.
Pas na een keuze de status aan en schrijf in één zin waarom.

| # | Vraag | Voorstel | Status |
|---|-------|----------|--------|
| B1 | Orkestratie | Docker Compose per node in een unprivileged LXC-container, uitgerold met Ansible. Geen Kubernetes: dat kan in LXC alleen privileged met AppArmor uit, en Jonas kent Compose. Failover komt uit de datalaag zelf (Patroni, SeaweedFS) en keepalived. | besloten (Jonas, 2026-10-20) |
| B2 | Objectopslag | SeaweedFS met replicatie 001 (≈ 1,0 TB netto); Garage RF3 als alternatief | voorstel |
| B3 | Office-engine | OnlyOffice Document Server (community, max. 20 verbindingen); Collabora als terugval | voorstel |
| B4 | Toegang van buiten | Geen WireGuard of VPN: alles publiek via de webinterface over HTTPS. Router stuurt alleen 80/443 door naar de VIP; SSH en de interne diensten (database, opslag) blijven alleen in het LAN. Daarom komen TLS, security-headers en rate limiting al in fase 1, en 2FA vanaf de eerste login in fase 2: 2FA is standaard verplicht voor iedereen (uit te zetten op `/beheer`, niet aan te raden). | besloten (Jonas, 2026-10-20) |
| B5 | GPU voor transcoding | Intel iGPU (QSV/VAAPI) of een Intel Arc-kaart in één node; volgt uit de hardware-inventaris | open |
| B6 | Offsite-backupdoel | Hetzner Storage Box of Backblaze B2 (append-only, restic) | open |
| B7 | Aantal ontwikkelaars | Eén; app-sporen lopen dan na elkaar (LG TV, Windows, Android) | voorstel |
| B8 | Frontend | React 19 + TypeScript + Vite, hergebruikt door webOS en Tauri | besloten (skelet staat in `/web`) |
| B9 | Rol van cluster-forge | Vervalt (geen Kubernetes) | besloten |
| B10 | Projectnaam en domein | nog te kiezen; werknaam "platform" | open |
| B11 | Edge | Geen Traefik: de app serveert zelf web-app en API, zet zelf de security-headers en doet zelf HTTPS. Een eigen ACME-client vraagt het certificaat aan bij Let's Encrypt (HTTP-01) en vernieuwt het; het domein stel je in op `/beheer`. Login en 2FA hebben eigen rate limiting. Voor het cluster bekijken we het opnieuw; de Ansible-rol heeft Traefik nog. | besloten voor één node (Jonas, 2026-10-20) |
| B12 | Database | PostgreSQL 17 met Patroni en etcd (3 nodes), synchronous_mode_strict: RPO 0. Geen HAProxy of PgBouncer: de API verbindt met alle 3 nodes en kiest zelf de primary (target_session_attrs). Backups met pgBackRest volgen in fase 1 deel 2. | standaard gekozen |
| B13 | Virtualisatie | Alleen LXC-containers op Proxmox, geen VM's; unprivileged, met nesting en keyctl voor Docker. De 700 GB is een mount point op de ZFS-opslag van de host. | besloten (Jonas, 2026-10-20) |
| B14 | Bestaande tools | Zo weinig mogelijk kant-en-klare tools; wat het platform specifiek nodig heeft bouwen we zelf. Hergebruikt worden alleen onderdelen die we niet kunnen winnen: PostgreSQL + Patroni + etcd, SeaweedFS, Traefik, keepalived, en later OnlyOffice en ffmpeg. Beheer en monitoring zitten in de eigen web-app (`/beheer`); geen Portainer, Grafana of Prometheus. | besloten (Jonas, 2026-10-20) |
| B15 | Standalone app | Het platform werkt als één container zonder andere diensten: SQLite en bestanden (content-addressed, met ontdubbeling) in `/data`. PostgreSQL en SeaweedFS komen pas met het cluster; de code is daar al op voorbereid (`PLATFORM_DATABASE_URL`, `PLATFORM_MODE=cluster`). | besloten (Jonas, 2026-10-20) |
| B16 | 2FA en uploads | TOTP (RFC 6238) en het tus-protocol voor hervatbare uploads bouwen we zelf; geen Keycloak of tusd. Voor de QR-code gebruiken we de kleine bibliotheek segno (geen eigen QR-encoder), voor certificaten en sleutels `cryptography`. Tegen raden van codes: een teller per gebruiker in de database, na 5 fouten een blokkade die verdubbelt tot 24 uur. | standaard gekozen |
| B17 | Zoeken, live updates en previews | Zoeken zelf op de database (een recursieve query op de boom, namen zonder hoofdletters en accenten), geen Typesense. Live updates via Server-Sent Events in plaats van WebSockets: alleen de server stuurt iets, het werkt over gewoon HTTP met het token in een header, en het is minder code. Previews en de job-queue bouwen we zelf; voor het lezen van bestandsformaten gebruiken we kleine bibliotheken (Pillow met pillow-heif, pypdfium2, mutagen) en ffmpeg. | standaard gekozen |
| B18 | Delen | Delen met personen, groepen (beheerd op `/beheer`) en publieke links met wachtwoord, vervaldatum of alleen uploaden. Wat een ander in jouw gedeelde map zet, is van jou en telt in jouw quotum. Een link opent met een sleutel van hooguit 12 uur; het token van de link staat nooit in een API-adres. Gasten hebben geen eigen bestanden. | standaard gekozen |
| B19 | Bibliotheken in fase 3 | Geen PDF.js, hls.js, Workbox, TanStack of bibliotheken voor virtualisatie, i18n, UI of state: alles zelf (B14). Geen nieuwe afhankelijkheden in `web/package.json` of `api/pyproject.toml`, ook geen dev-afhankelijkheden; UI-logica in pure modules, getest met vitest. | standaard gekozen |
| B20 | PDF | De server rendert pagina's op aanvraag naar WebP met het al aanwezige pypdfium2, alleen met een download-token. Navigatie, zoom en inhoudsopgave; een tekstlaag komt in fase 6. "Openen in nieuw tabblad" blijft de uitweg naar de viewer van de browser. | standaard gekozen |
| B21 | Office-preview | Een eigen vereenvoudigde lezer (zipfile en ElementTree uit de standaardbibliotheek) voor docx, xlsx, pptx, odt, ods en odp: de ingebedde thumbnail plus tekst, tabellen en dia-teksten zonder opmaak. doc, xls, ppt en rtf krijgen in fase 3 geen preview. | standaard gekozen |
| B22 | EXIF en opnamedatum | Opnamedatum, camera, objectief en belichting uit Pillow en ffprobe; GPS en locatie lezen en bewaren we in fase 3 niet (dat hoort bij fase 6). De opnamedatum is een zwevende lokale tijd; een UTC-tijd wordt omgezet naar `PLATFORM_TIMEZONE` (standaard Europe/Amsterdam). Zonder EXIF-datum geldt de lastModified van de client, anders het uploadmoment. | standaard gekozen |
| B23 | Albums | Een album is een node met kind `album` buiten de boom (`parent_id` NULL), plus de tabellen `album` en `album_item`. Zo werkt delen (B18) zonder nieuwe deelcode; gedeelde albums zijn alleen te bekijken. Je voegt alleen eigen foto's en video's toe, er wordt niets gekopieerd; elk boom-endpoint weigert een album met 422 `album`. | standaard gekozen |
| B24 | Bulkacties en zip | Bulkacties via batch-endpoints: één verzoek, één transactie per 500 items, een resultaat per item en gebundelde events. Downloaden als zip (selectie, map of album) streamt met zipfile (`ZIP_STORED`, ZIP64), tot 10.000 top-ids per link. | standaard gekozen |
| B25 | Talen | Nederlands is de bron, Engels wordt volledig, met een eigen i18n zonder bibliotheek. De taal hoort bij het account (`user.locale`), met de keuze per apparaat als terugval; paden blijven Nederlands. Woordenboeken laden per gebied wanneer ze nodig zijn. | standaard gekozen |
| B26 | Foutcodes | Elke API-fout heeft een vaste code (plus params): `{"detail": {"message", "code", "params"}}`. Elke code staat in precies één catalogus in `web/src/i18n/errors/`; in het Nederlands toont de app de tekst van de server, in het Engels vertaalt hij op code. | standaard gekozen |
| B27 | Thema | Licht, donker of systeem, per apparaat. `/boot.js` zet het thema vóór de eerste paint. | standaard gekozen |
| B28 | PWA en offline | Alleen in een secure context: het https-domein (B11) en localhost. De service worker precachet alleen de schil, komt nooit aan `/api`, en een update gaat pas in na "Vernieuwen". | standaard gekozen |
| B29 | Playwright als testgereedschap | Playwright (Python) voor de e2e-tests, alleen in de sandbox en in een CI-job (`uv run --with playwright`); niet in de image, `pyproject.toml` of `package.json`. Bij nee blijven vitest op pure logica en handmatige metingen in Chrome DevTools over. | standaard gekozen, Jonas beslist |

## Vastgelegd in deze fase

- Fase 1 is na B1, B13 en B14 herschreven van k3s naar Docker Compose; de k3s-versie staat nog in de git-geschiedenis.
- Monorepo-structuur: `/api`, `/web`, `/infra`, `/apps/windows`, `/apps/android`, `/apps/webos`.
- Backend: Python 3.12 + FastAPI, beheerd met `uv`.
- CI: GitHub Actions met lint, tests en het bouwen van de API- en web-image.
- Containers draaien als niet-root; de API heeft `/health` (liveness) en `/ready` (readiness)
  voor de health-checks van Traefik en Compose.
