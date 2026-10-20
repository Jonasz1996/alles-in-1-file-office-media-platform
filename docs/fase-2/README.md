# Fase 2 — Core backend

De app werkt standalone: één container met API en web-app, zonder Traefik, PostgreSQL of
SeaweedFS (beslissingen B11 en B15). Installeren op één node: zie
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

## Nog te doen

- HTTPS voor toegang van buiten, met Let's Encrypt in de app zelf
- TOTP-2FA (nodig voordat poort 80/443 open gaat, zie B4)
- Hervatbare upload (TUS) voor grote bestanden
- Thumbnails en previews, met een eigen job-queue
- Zoeken op naam, pad en type
- Delen met gebruikers en publieke links
- WebSocket-events en versies van bestanden
