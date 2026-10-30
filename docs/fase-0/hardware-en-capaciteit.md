# Fase 0 — hardware, capaciteit en netwerk

## Hardware-inventaris

Draai op elke Proxmox-host:

```bash
sudo infra/scripts/hardware-inventaris.sh > infra/inventaris/$(hostname).txt
```

Vat de uitkomst hieronder samen (de ruwe `.txt`-bestanden blijven buiten git).

| | Node 1 | Node 2 | Node 3 |
|---|---|---|---|
| Proxmox-host | | | |
| CPU (model, cores/threads) | | | |
| RAM | | | |
| OS-disk | | | |
| Datadisk (700 GB): type (SSD/HDD) | | | |
| NIC-snelheid | | | |
| GPU / iGPU, QSV of VAAPI | | | |
| Rol | start als PostgreSQL-primary, VIP-voorkeur | standby | standby |

Per LXC-container als startpunt: 4 cores, 16 GB RAM, swap 0, rootfs 64 GB, mount point `/tank` van 700 GB (OnlyOffice en transcoding vragen het meest).

## Capaciteitsplan per node (700 GB mount point op ZFS)

| Deel | Grootte | Inhoud |
|------|---------|--------|
| SeaweedFS volume | ≈ 560 GB | bestanden, versies, thumbnails (2 kopieën cluster-breed) |
| PostgreSQL | 30 GB | metadata, gebruikers, bibliotheek, embeddings |
| Transcode-cache | 60 GB | HLS-segmenten, lokaal, regenereerbaar |
| Logs | 20 GB | Docker-logs (geroteerd) |
| Reserve | ≈ 10 GB | ZFS-snapshots en marge |

Netto bruikbaar voor bestanden en media: ≈ 3 × 560 GB / 2 ≈ **0,84 TB** bij replicatie 001.
Groeipad: een extra disk per node (SeaweedFS volume erbij) of een vierde node.
Alert bij disk > 80 %.

## Netwerk

| Item | Waarde |
|------|--------|
| Cluster-VLAN (ID, subnet) | |
| Vaste IP's node 1/2/3 | |
| VIP (keepalived) | |
| Interne DNS-zone | |
| Externe domeinnaam | |
| Toegang van buiten | publiek via HTTPS, router stuurt 80/443 door naar de VIP (zie B4) |
| Snelheid tussen nodes | 1 GbE werkt; 2,5 of 10 GbE aanbevolen |

## Niet-functionele eisen

| Eis | Doel | Toelichting |
|-----|------|-------------|
| RPO | 0 | synchrone DB-replicatie (ANY 1) en 2 synchrone objectkopieën |
| RTO | < 2 min | automatische failover met quorum |
| Gelijktijdige transcodes | 3 per cluster zonder haperen | meten in fase 5 |
| Max. uploadgrootte | 50 GB (voorstel) | TUS, chunks van 8–16 MB |
| Office | max. 20 gelijktijdige verbindingen | limiet community-editie |
| Snapshots | elk uur; bewaar 24 uur, 7 dagen, 4 weken | sanoid |
| Offsite-backup | nachtelijks, append-only | restic of kopia |
| Restore-oefening | elk kwartaal | DB point-in-time en één bucket |
