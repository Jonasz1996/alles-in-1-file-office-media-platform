# Fase 0 — beslissingen

Status per beslissing: **voorstel** (overgenomen uit het stappenplan, nog te bevestigen),
**besloten** of **open**. Fase 1 start pas als elke regel *besloten* is.
Pas na een keuze de status aan en schrijf in één zin waarom.

| # | Vraag | Voorstel | Status |
|---|-------|----------|--------|
| B1 | Orkestratie: k3s + cluster-forge of Docker Compose per node? | k3s op 3 VM's met cluster-forge; Compose + keepalived als terugval. cluster-forge eerst in een test-VM proberen. | voorstel |
| B2 | Objectopslag | SeaweedFS met replicatie 001 (≈ 1,0 TB netto); Garage RF3 als alternatief | voorstel |
| B3 | Office-engine | OnlyOffice Document Server (community, max. 20 verbindingen); Collabora als terugval | voorstel |
| B4 | Toegang van buiten | Alleen via WireGuard; publiek pas na fase 8 (WAF, 2FA, rate limiting) | voorstel |
| B5 | GPU voor transcoding | Intel iGPU (QSV/VAAPI) of een Intel Arc-kaart in één node; volgt uit de hardware-inventaris | open |
| B6 | Offsite-backupdoel | Hetzner Storage Box of Backblaze B2 (append-only, restic) | open |
| B7 | Aantal ontwikkelaars | Eén; app-sporen lopen dan na elkaar (LG TV, Windows, Android) | voorstel |
| B8 | Frontend | React 19 + TypeScript + Vite, hergebruikt door webOS en Tauri | besloten (skelet staat in `/web`) |
| B9 | Rol van cluster-forge | Alleen platformtools; app-deploys via Argo CD | voorstel |
| B10 | Projectnaam en domein | nog te kiezen; werknaam "platform" | open |

## Vastgelegd in deze fase

- Monorepo-structuur: `/api`, `/web`, `/infra`, `/apps/windows`, `/apps/android`, `/apps/webos`.
- Backend: Python 3.12 + FastAPI, beheerd met `uv`.
- CI: GitHub Actions met lint, tests en het bouwen van de API- en web-image.
- Containers draaien als niet-root; de API heeft `/health` (liveness) en `/ready` (readiness)
  voor de health-checks van k3s of Compose.
