# Nextcloud-clients, WebDAV en TrueNAS

Stand: 17 november 2026. Eerste stuk van fase 7 ("Sync, delen en integraties").

## Wat werkt

- **De officiële Nextcloud-apps** (desktop voor Windows/Mac/Linux, Android, iOS) koppelen met
  het serveradres van het platform. De app opent zelf een inlogpagina (inlogflow v2); daar log
  je in met e-mail, wachtwoord en 2FA-code. De app krijgt een eigen app-wachtwoord.
- **WebDAV** voor rclone, TrueNAS Cloud Sync en andere clients:
  `https://<server>/remote.php/dav/files/<e-mail>/` (of `/remote.php/webdav/`), met een
  app-wachtwoord uit Account → Apps en synchronisatie.
- Lezen, uploaden (ook grote bestanden in stukken, Nextcloud chunking v2), mappen maken,
  hernoemen, verplaatsen, kopiëren en verwijderen. Verwijderen gaat naar de prullenbak;
  vervangen houdt de vorige inhoud als versie. De wijzigtijd van de client blijft bewaard
  (`X-OC-Mtime`).
- De ETag van een map verandert als er ergens onder iets verandert, zodat de desktop-app
  wijzigingen op de server snel vindt.

## Wat (nog) niet

- Wat met je gedeeld is, staat niet in de WebDAV-boom (alleen je eigen bestanden).
- Geen WebDAV-LOCK: Finder en Windows Verkenner koppelen alleen-lezen. Gebruik de Nextcloud-app.
- Geen delen, activiteit of meldingen via de Nextcloud-app; dat doe je in de web-app.
- De map-ETags staan in het geheugen: na een herstart of failover vergelijkt de desktop-app
  één keer alles opnieuw (bestanden met dezelfde inhoud downloadt hij niet opnieuw).

## Inloggen en veiligheid

- Het gewone wachtwoord werkt niet voor WebDAV: dat zou 2FA omzeilen. Alleen app-wachtwoorden
  (72 willekeurige tekens; alleen de SHA-256 staat in de database).
- Mislukte pogingen: hooguit 30 per IP per 5 minuten, daarna 429.
- Intrekken: Account → Apps en synchronisatie, of uitloggen in de app.

## TrueNAS (NFS of SMB)

Gekozen aanpak: **TrueNAS haalt een kopie op via WebDAV** (Cloud Sync-taak, richting PULL).
Zo is er geen NFS- of SMB-mount in de unprivileged LXC nodig, niets te veranderen aan
ClusterForge, en werkt het gelijk op de single en in het cluster. De kopie staat op TrueNAS
als gewone mappen in een dataset, die TrueNAS zelf via NFS of SMB deelt en met snapshots
beschermt.

1. In het platform: Account → Apps en synchronisatie → naam `TrueNAS` → App-wachtwoord maken.
2. In TrueNAS: Credentials → Backup Credentials → Cloud Credentials → Add.
   Provider **WebDAV**, URL `https://<server>/remote.php/dav/files/<e-mail>/`,
   Vendor **Nextcloud**, gebruikersnaam = je e-mail, wachtwoord = het app-wachtwoord.
3. Data Protection → Cloud Sync Tasks → Add: Direction **PULL**, Transfer Mode **SYNC**
   (of COPY als verwijderen op het platform niet mag doorwerken), credential van stap 2,
   doelmap = een dataset (bijv. `/mnt/tank/platform-kopie/<naam>`), schema bijv. elk uur.
4. Eén taak per gebruiker. Zet daarna Periodic Snapshot Tasks aan op die dataset: zo heb je
   ook oudere standen.

Andere richting (een share op TrueNAS die je in het platform opent, of TrueNAS als
hoofdopslag) is nog niet gebouwd; zie het stappenplan, fase 7 "Externe opslag koppelen".
