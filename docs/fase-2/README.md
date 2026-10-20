# Fase 2 — Core backend

De app werkt standalone: één container met API en web-app, zonder Traefik, PostgreSQL of
SeaweedFS (beslissingen B11, B15 en B16). Installeren op één node: zie
[infra/single/README.md](../../infra/single/README.md).

## Klaar (deel 1)

- Eén image (`Dockerfile` in de root): de API serveert ook de gebouwde web-app, op poort 8080.
- Database via SQLAlchemy: SQLite in `/data` standalone, PostgreSQL in het cluster.
  Alembic-migraties draaien vanzelf bij het starten.
- Auth: eerste bezoek maakt het beheerdersaccount, argon2id-wachtwoorden, korte access-tokens
  en roterende refresh-tokens per apparaat (hergebruik trekt de hele reeks in), rollen
  admin/user/guest, rate limiting op login.
- Bestanden: mappen, upload met voortgang, download met Range en ETag/304, hernoemen,
  verplaatsen, prullenbak met terugzetten, quotum per gebruiker.
- Opslag op inhoud (SHA-256): hetzelfde bestand twee keer uploaden kost één keer schijfruimte.
- Beveiliging zonder proxy: security-headers en CSP uit de app zelf; geüploade HTML en SVG
  draaien in een sandbox.
- Beheer (`/beheer`): status van database en opslag, schijfgebruik, gebruikers aanmaken.

## Klaar (deel 2)

- **HTTPS zonder proxy**: een eigen ACME-client (RFC 8555) vraagt het certificaat aan bij
  Let's Encrypt met de HTTP-01-challenge, die de app zelf beantwoordt. Domein instellen op
  `/beheer`; het certificaat wordt vernieuwd als er nog een derde van de looptijd over is en
  zonder herstart geladen. Daarna gaat http voor dat domein naar https, met HSTS. Via het
  IP-adres in je LAN blijft http werken. Fouten krijgen een uitleg (DNS, poort 80, limieten).
  In CI vraagt een test echt een certificaat aan bij Pebble, de test-CA van Let's Encrypt.
- **2FA (TOTP)**: zelf gebouwd volgens RFC 6238, werkt met elke authenticator-app. Instellen
  op de accountpagina met een QR-code; 10 eenmalige herstelcodes; elke code werkt één keer;
  hooguit 10 pogingen per 5 minuten per gebruiker. Een beheerder kan 2FA van iemand anders
  uitzetten.
- **Hervatbare uploads (tus 1.0.0)**, zelf gebouwd: stukken van 16 MB; valt de verbinding weg,
  dan gaat de upload verder waar hij was, ook na herladen van de pagina (kies hetzelfde
  bestand opnieuw). Lopende uploads tellen mee voor het quotum; onafgemaakte uploads worden
  na 24 uur opgeruimd. Een gemist laatste antwoord levert geen dubbel bestand op.

## Nog te doen

- Wachtwoord wijzigen en sessies per apparaat bekijken en intrekken
- Thumbnails en previews, met een eigen job-queue
- Zoeken op naam, pad en type
- Delen met gebruikers en publieke links
- WebSocket-events en versies van bestanden
