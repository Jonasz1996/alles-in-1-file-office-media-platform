# Fase 0 — beslissingen

Status per beslissing: **voorstel** (overgenomen uit het stappenplan, nog te bevestigen),
**besloten** of **open**. Fase 1 start pas als elke regel *besloten* is.
Pas na een keuze de status aan en schrijf in één zin waarom.

| # | Vraag | Voorstel | Status |
|---|-------|----------|--------|
| B1 | Orkestratie: k3s + cluster-forge of Docker Compose per node? | k3s op 3 VM's met Argo CD (app-of-apps) direct, zonder cluster-forge. cluster-forge blijkt de AMD Enterprise AI-stack uit te rollen (GPU/ML, Gitea, OpenBao) en past niet bij dit platform; het gebruikt zelf hetzelfde Argo CD-patroon. | standaard gekozen (fase 1) |
| B2 | Objectopslag | SeaweedFS met replicatie 001 (≈ 1,0 TB netto); Garage RF3 als alternatief | voorstel |
| B3 | Office-engine | OnlyOffice Document Server (community, max. 20 verbindingen); Collabora als terugval | voorstel |
| B4 | Toegang van buiten | Geen WireGuard of VPN: alles publiek via de webinterface over HTTPS. Router stuurt alleen 80/443 door naar de VIP; SSH en de Kubernetes-API blijven alleen intern. Daarom komen TLS, security-headers en rate limiting al in fase 1, en 2FA vanaf de eerste login in fase 2. | besloten (Jonas, 2026-10-20) |
| B5 | GPU voor transcoding | Intel iGPU (QSV/VAAPI) of een Intel Arc-kaart in één node; volgt uit de hardware-inventaris | open |
| B6 | Offsite-backupdoel | Hetzner Storage Box of Backblaze B2 (append-only, restic) | open |
| B7 | Aantal ontwikkelaars | Eén; app-sporen lopen dan na elkaar (LG TV, Windows, Android) | voorstel |
| B8 | Frontend | React 19 + TypeScript + Vite, hergebruikt door webOS en Tauri | besloten (skelet staat in `/web`) |
| B9 | Rol van cluster-forge | Alleen platformtools; app-deploys via Argo CD | voorstel |
| B10 | Projectnaam en domein | nog te kiezen; werknaam "platform" | open |
| B11 | Edge-ingress | Traefik (meegeleverd met k3s) in plaats van ingress-nginx: het ingress-nginx-project is in maart 2026 met pensioen gegaan en krijgt geen updates meer. nginx blijft binnen het platform (de web-container) voor X-Accel-Redirect en statische bestanden. | standaard gekozen (fase 1) |
| B12 | Database-backups | CloudNativePG met barman naar de `backups`-bucket in SeaweedFS (in plaats van pgBackRest, dat CloudNativePG niet ondersteunt) | standaard gekozen, volgt in fase 1 deel 2 |

## Vastgelegd in deze fase

- Monorepo-structuur: `/api`, `/web`, `/infra`, `/apps/windows`, `/apps/android`, `/apps/webos`.
- Backend: Python 3.12 + FastAPI, beheerd met `uv`.
- CI: GitHub Actions met lint, tests en het bouwen van de API- en web-image.
- Containers draaien als niet-root; de API heeft `/health` (liveness) en `/ready` (readiness)
  voor de health-checks van k3s of Compose.
