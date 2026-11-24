"""Fase 11: mediabeheer (wat Sonarr, Radarr, Prowlarr en Bazarr doen) in de eigen app.

Stap 1 zijn de bouwstenen zonder database:

- `release`: een releasenaam lezen (resolutie, bron, codec, HDR, audio, taal, groep, PROPER);
- `score`: een kwaliteitsprofiel en de keuze van de beste release;
- `torznab`: zoeken bij een torrentbron via de open standaard Torznab;
- `qbit`: qBittorrent aansturen via zijn web-API v2 (de downloader, keuze van Jonas 15-11-2026).

Alleen torrents, geen usenet (keuze van Jonas 16-11-2026).
"""
