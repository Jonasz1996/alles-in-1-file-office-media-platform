# Fase 11 in het ClusterForge-cluster

Stand: 23 november 2026. Dit is de lijst om mediabeheer (fase 11) in het cluster aan te zetten.
**Nog niet uitvoeren.** Het cluster wordt pas bijgewerkt als fase 6 als 0.8.2 af is (tests op
PostgreSQL groen). Fase 11 komt daarna mee in dezelfde update.

Nooit `infra/single/install.sh` op een clusternode draaien: dat start de oude single opnieuw met
zijn oude SQLite-database. Bijwerken gaat alleen met de opgeslagen ClusterForge-commando's
"Platform bijwerken" en "Platform terug" (zie `docs/runbooks/cluster.md` op de clusterbranch).

## 1. De code: één branch

Het cluster bouwt de branch die bij Specificatie → "Branch of tag van het platform" staat.
Fase 11 staat nu op `claude/project-thread-cp46r0`, fase 6 (cluster) op de clusterbranch. Voor
de update:

1. Fase 11 samenvoegen met de branch van 0.8.2.
2. Migraties in één lijn: gedaan voor 0014 (23-11, `0016_mediabeheer` volgt op
   `0014_zoekindex_postgres`). Komt 0015 (Nextcloud) erbij, dan gaat `down_revision` daarnaar.
3. Versie ophogen (0.9.0: mediabeheer) en de CI groen, ook de tests op PostgreSQL.

Migratie 0016 maakt tien nieuwe tabellen (arr_wanted, arr_grab, arr_subtitle,
media_sub_offset, arr_request, arr_upscale, media_facts, media_profile, media_intro,
arr_cleanup). Ze draait vanzelf bij de start van de app, ook op PostgreSQL. Ze raakt geen
bestaande tabel, dus "Platform terug" naar 0.8.2 werkt na een downgrade naar 0014.

## 2. Eerst: een dump van de database

Kaart Database → Databases en gebruikers → Dumps → **Dump nu** bij `platform`. Wacht tot die als
goed staat. Pas dan "Platform bijwerken" op het hele cluster.

## 3. Wat er in de ClusterForge-template bij moet

De template staat in het repo ClusterForge (`internal/templates/builtin/media-platform`), niet
hier. Wat erbij moet:

1. **Een instelling ARR** (aan/uit, standaard uit) in `/etc/clusterforge/platform.env`.
2. **compose()** in `platform-ctl.sh.tmpl`: met `ARR=aan` ook `infra/single/compose.arr.yaml`
   uit de checkout meenemen (zoals nu al de analysebestanden met ANALYSE=aan).
3. **Starten, alleen op de VIP-node** (zelfde regel als de app): na `up` van het platform eerst
   `docker compose … exec -T platform python -m app.cli arr-koppel`, daarna
   `docker compose … up -d --no-deps qbittorrent prowlarr`. Bij failover gaan ze mee met het VIP,
   net als de app. Op de andere nodes draaien ze niet.
4. **Downloads op een lokale schijf, niet op SeaweedFS.** Torrents schrijven veel kleine stukken
   door elkaar; op de FUSE-mount van SeaweedFS is dat traag en harde links zijn er onzeker.
   Daarom in het cluster:
   - `/downloads` van qBittorrent op een lokale map van de node, bv. `/var/lib/cf-platform/torrents`;
   - die map ook in de app op `/data/import/torrents` (de importmap waar de app downloads leest);
   - de app zet een klare download dan met een kopie klaar voor de import in plaats van met een
     harde link (dat gebeurt vanzelf als een harde link niet kan).
   Gevolg: bij failover is een lopende download op de oude node weg; qBittorrent op de nieuwe
   node begint opnieuw. Wat al geïmporteerd is, staat veilig in SeaweedFS.
5. **De config van qBittorrent en Prowlarr** (`${DATA_ROOT}/arr/…`) mag wel op SeaweedFS: dat
   zijn kleine bestanden, en zo kent Prowlarr na failover zijn torrentsites nog.
6. **Poorten** op de VIP-node: 6881 tcp en udp (torrent; op de router naar het VIP doorsturen
   mag, hoeft niet) en 9696 (Prowlarr, alleen in het eigen netwerk). Via het keepalived-VIP
   blijft het adres na failover hetzelfde: `http://<VIP>:9696`.

## 4. Na de update in /beheer → Media

Allemaal standaard uit; aanzetten wanneer je wil:

- **Mediabeheer:** qBittorrent en Prowlarr zijn gekoppeld door arr-koppel. Voeg torrentsites toe
  in Prowlarr (`http://<VIP>:9696`) en kies de bibliotheken voor films en series.
- **Ondertitels:** de sleutel van OpenSubtitles invullen.
- **Verzoeken:** wie vanzelf goedkeuring krijgt (standaard niemand: Jonas keurt goed).
- **AI-upscaling:** zie hieronder.
- **Kinderslot, statistieken, opruimregels:** werken meteen; opruimregels staan uit tot je ze
  aanzet.
- **Intro overslaan, collecties, kijkwijzer:** lopen vanzelf in de achtergrond (een seizoen per
  10 minuten, collecties via TMDB als de sleutel er is).

## 5. De upscaler op jbogaertmedia2

De upscaler is geen deel van het cluster-platform. Hij draait in een eigen container in de LXC
op jbogaertmedia2 (192.168.10.147, RTX 3060 Ti) en haalt werk bij de app via HTTPS, met een token
uit /beheer → Media → AI-upscaling. In het cluster wijst `PLATFORM_URL` van de upscaler naar het
VIP (of de domeinnaam), niet naar één node: zo werkt hij na failover verder. De stappen voor de
host, de driver en de container staan in `infra/upscaler/README.md`.

## 6. Controle na de update

Op de VIP-node:

```sh
curl -s -m 3 http://127.0.0.1/api/v1/info; echo          # versie 0.9.0
docker ps --format '{{.Names}}: {{.Status}}' | grep -E 'platform|qbittorrent|prowlarr'
```

Op de andere nodes mogen qbittorrent en prowlarr **niet** draaien. In /beheer → Media moet
"Mediabeheer" qBittorrent en Prowlarr als verbonden tonen.

## 7. Films en series op 2 kopieën, de rest op 3

Jonas (23-11-2026): per plek, niet per soort bestand. Wat in een bibliotheek voor films of series
staat (vroeger Plex) mag op 2 kopieën; al de rest (vroeger Nextcloud, ook de video's daarin)
blijft op 3.

**Vervangen (24-11-2026) door replicatie per bibliotheek:** de beheerder kiest nu per bibliotheek
het aantal kopieën ("Standaard", 1, 2 of 3; bv. Films x2, Series x3), op /beheer → Media →
Bibliotheken. De inhoud gaat naar `blobs-r1/`, `blobs-r2/` of `blobs-r3/` in plaats van naar
`blobs-media/`; de regels in SeaweedFS zijn per map (collecties `r1`, `r2`, `r3` in plaats van
`media`), en `platform-ctl.sh replicatie` moet die drie collecties overslaan. De stappen in het
cluster (de mount zonder `-replication`, `fs.configure` per map, `PLATFORM_BLOB_SPLIT` en
`PLATFORM_BLOB_DEFAULT_COPIES`, nakijken en terugzetten) staan in
[docs/runbooks/replicatie.md](../runbooks/replicatie.md). Films x2 zoals hierboven: Films en
Series daar op 2 zetten.

Wat nog in `blobs-media/` staat (wie de versie van 23-11 met `PLATFORM_BLOB_SPLIT=true` draaide),
blijft leesbaar en verhuist naar zijn juiste plek; de regel `fs.configure -locationPrefix=/blobs-media/`
mag daarna weg (zie het runbook).

## 8. Terug

"Platform terug" zet de vorige versie terug. Omdat 0016 tabellen toevoegt, eerst de downgrade
van de database (het commando doet `alembic downgrade <vorige head>` voor het de oude versie
start) of de dump van stap 2 terugzetten. Gegevens van mediabeheer (gevolgde titels, verzoeken,
profielen) zijn dan weg; de media-bestanden zelf niet.

## 9. Lidarr en de YouTube-module (fixlijst 26-11-2026, punten 7 en 8)

Ook dit staat in de template van ClusterForge, niet hier. Alles alleen op de VIP-node, net als de
app en qBittorrent.

**Lidarr** (zit in `compose.arr.yaml`, dus mee met `ARR=aan`):

1. Starten: Lidarr start mee met mediabeheer zodra `compose.arr.yaml` een dienst `lidarr` heeft
   (ClusterForge PR #166; in de single: `up -d --no-deps qbittorrent prowlarr lidarr`).
   `arr-koppel` maakt `/data/arr/lidarr` en `/data/import/lidarr` (eigenaar 10001) en zet de
   API-sleutel van Lidarr; de map `/data/arr` zelf (eigenaar 10001) maakt de template al (fixlijst
   punt 6).
2. Volumes van Lidarr in het cluster:
   - `/config` → `${DATA_ROOT}/arr/lidarr` (op SeaweedFS mag: klein, en zo kent Lidarr na failover
     zijn artiesten nog);
   - `/downloads` → dezelfde **lokale** map als qBittorrent (bv. `/var/lib/cf-platform/torrents`),
     anders vindt Lidarr de klare download niet;
   - `/music` → `${DATA_ROOT}/import/lidarr` (SeaweedFS). Een harde link van de lokale schijf naar
     SeaweedFS kan niet; Lidarr kopieert dan vanzelf, en de app ook (staging).
3. Poort 8686 op de VIP-node (alleen in het eigen netwerk): `http://<VIP>:8686`.
4. Na de update: eerste bezoek aan Lidarr kiest een login; volg daar artiesten of albums. De app
   koppelt Lidarr zelf aan Prowlarr en qBittorrent (categorie `lidarr`) en zet de hoofdmap
   `/music`. In /beheer → Media → "Lidarr" kies je de muziekbibliotheek (anders de oudste).
   Een klaar album gaat binnen een minuut naar die bibliotheek en wordt meteen geïndexeerd;
   Lidarr volgt dat album daarna niet meer (anders downloadt hij het opnieuw). Een torrent van
   Lidarr gaat na 3 dagen seeden weg.

**YouTube-module** (`compose.youtube.yaml`):

De templatekant staat in ClusterForge PR #166 (branch `claude/project-thread-gyheez`):

1. **Instelling YOUTUBE** (aan/uit, standaard aan) in `/etc/clusterforge/platform.env`.
   `YOUTUBE_KEY` leidt de template zelf af: sha256("youtube " + mediasleutel), op alle nodes
   gelijk.
2. **compose()**: met `YOUTUBE=aan` ook `infra/single/compose.youtube.yaml` meenemen (image
   `cf-platform:local`, restart "no": platform-ctl.sh start de helper mee met de app, alleen op de
   VIP-node).
3. **Mappen:** platform-ctl.sh maakt `import/youtube` (en `arr/lidarr`, `import/lidarr`, eigenaar
   10001) vóór de start; een aparte `exec mkdir` of `up -d youtube` zoals bij de single is in het
   cluster niet nodig. De helper heeft internet nodig (yt-dlp), geen poort naar buiten.
4. De CSP van de web-app laat nu de speler van `youtube-nocookie.com` en de miniaturen van
   `i.ytimg.com` toe. Zet Nginx Proxy Manager een eigen Content-Security-Policy, dan moet die
   hetzelfde toelaten (standaard zet NPM er geen).
