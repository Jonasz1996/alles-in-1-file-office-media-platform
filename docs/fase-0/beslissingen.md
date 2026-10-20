# Fase 0 — beslissingen

Status per beslissing: **voorstel** (overgenomen uit het stappenplan, nog te bevestigen),
**besloten** of **open**. Fase 1 start pas als elke regel *besloten* is.
Pas na een keuze de status aan en schrijf in één zin waarom.

| # | Vraag | Voorstel | Status |
|---|-------|----------|--------|
| B1 | Orkestratie | Docker Compose per node in een unprivileged LXC-container, uitgerold met Ansible. Geen Kubernetes: dat kan in LXC alleen privileged met AppArmor uit, en Jonas kent Compose. Failover komt uit de datalaag zelf (Patroni, SeaweedFS) en keepalived. | besloten (Jonas, 2026-10-20) |
| B2 | Objectopslag | SeaweedFS met replicatie 001 (≈ 1,0 TB netto); Garage RF3 als alternatief | voorstel |
| B3 | Office-engine | OnlyOffice Document Server (community, max. 20 verbindingen); Collabora als terugval | voorstel |
| B4 | Toegang van buiten | Geen WireGuard of VPN: alles publiek via de webinterface over HTTPS. Router stuurt alleen 80/443 door naar de VIP; SSH en de interne diensten (database, opslag) blijven alleen in het LAN. Daarom komen TLS, security-headers en rate limiting al in fase 1, en 2FA vanaf de eerste login in fase 2. | besloten (Jonas, 2026-10-20) |
| B5 | GPU voor transcoding | Intel iGPU (QSV/VAAPI) of een Intel Arc-kaart in één node; volgt uit de hardware-inventaris | open |
| B6 | Offsite-backupdoel | Hetzner Storage Box of Backblaze B2 (append-only, restic) | open |
| B7 | Aantal ontwikkelaars | Eén; app-sporen lopen dan na elkaar (LG TV, Windows, Android) | voorstel |
| B8 | Frontend | React 19 + TypeScript + Vite, hergebruikt door webOS en Tauri | besloten (skelet staat in `/web`) |
| B9 | Rol van cluster-forge | Vervalt (geen Kubernetes) | besloten |
| B10 | Projectnaam en domein | nog te kiezen; werknaam "platform" | open |
| B11 | Edge | Geen Traefik in de eerste versie: de app serveert zelf web-app en API op één poort en zet zelf de security-headers; login heeft eigen rate limiting. HTTPS voor toegang van buiten bouwen we in de app zelf (Let's Encrypt). Voor het cluster bekijken we het opnieuw; de Ansible-rol heeft Traefik nog. | besloten voor één node (Jonas, 2026-10-20) |
| B12 | Database | PostgreSQL 17 met Patroni en etcd (3 nodes), synchronous_mode_strict: RPO 0. Geen HAProxy of PgBouncer: de API verbindt met alle 3 nodes en kiest zelf de primary (target_session_attrs). Backups met pgBackRest volgen in fase 1 deel 2. | standaard gekozen |
| B13 | Virtualisatie | Alleen LXC-containers op Proxmox, geen VM's; unprivileged, met nesting en keyctl voor Docker. De 700 GB is een mount point op de ZFS-opslag van de host. | besloten (Jonas, 2026-10-20) |
| B14 | Bestaande tools | Zo weinig mogelijk kant-en-klare tools; wat het platform specifiek nodig heeft bouwen we zelf. Hergebruikt worden alleen onderdelen die we niet kunnen winnen: PostgreSQL + Patroni + etcd, SeaweedFS, Traefik, keepalived, en later OnlyOffice en ffmpeg. Beheer en monitoring zitten in de eigen web-app (`/beheer`); geen Portainer, Grafana of Prometheus. | besloten (Jonas, 2026-10-20) |
| B15 | Standalone app | Het platform werkt als één container zonder andere diensten: SQLite en bestanden (content-addressed, met ontdubbeling) in `/data`. PostgreSQL en SeaweedFS komen pas met het cluster; de code is daar al op voorbereid (`PLATFORM_DATABASE_URL`, `PLATFORM_MODE=cluster`). | besloten (Jonas, 2026-10-20) |

## Vastgelegd in deze fase

- Fase 1 is na B1, B13 en B14 herschreven van k3s naar Docker Compose; de k3s-versie staat nog in de git-geschiedenis.
- Monorepo-structuur: `/api`, `/web`, `/infra`, `/apps/windows`, `/apps/android`, `/apps/webos`.
- Backend: Python 3.12 + FastAPI, beheerd met `uv`.
- CI: GitHub Actions met lint, tests en het bouwen van de API- en web-image.
- Containers draaien als niet-root; de API heeft `/health` (liveness) en `/ready` (readiness)
  voor de health-checks van Traefik en Compose.
