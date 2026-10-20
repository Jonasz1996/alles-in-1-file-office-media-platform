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
  IP-adres in je LAN blijft http werken. Fouten krijgen een uitleg (DNS, poort 80, een andere
  server op het adres, limieten). Na een fout wacht de app steeds langer (10 minuten,
  verdubbelend tot 6 uur) en houdt zich aan de wachttijd die Let's Encrypt opgeeft; "Nu
  opnieuw proberen" kan hooguit eens per minuut en niet terwijl er al een poging loopt.
  Gaat de https-poort niet open (bezet, of `HTTPS_PORT` gelijk aan `PORT`), dan blijft http
  gewoon werken, toont `/beheer` waarom, en start https vanzelf zodra de poort vrij is.
  In CI vraagt een test echt een certificaat aan bij Pebble, de test-CA van Let's Encrypt.
- **Zelf bestand tegen het internet**, want er zit geen proxy voor: een verbinding die binnen
  10 seconden geen complete headers stuurt (ook de tls-handshake en tussen keep-alive-verzoeken)
  gaat dicht; een body die stilvalt telt na 60 seconden (uploads) of 90 seconden (de rest) als
  verbroken; het proces zet zijn limiet voor open bestanden op het maximum (compose: 65536).
  Niet afgedekt: limieten per IP-adres voor het aantal verbindingen; zet bij zware blootstelling
  een firewall voor de container.
- **2FA (TOTP)**: zelf gebouwd volgens RFC 6238, werkt met elke authenticator-app.
  - **Verplicht** (beslissing B4): wie nog geen 2FA heeft, kan na het inloggen alleen 2FA
    aanzetten. Ook het eerste beheerdersaccount. Uit te zetten op `/beheer` onder Beveiliging
    (niet aan te raden zodra het platform van buiten bereikbaar is).
  - Instellen op de accountpagina met een QR-code en je wachtwoord; 10 eenmalige herstelcodes.
    Elke code werkt één keer, ook bij gelijktijdige verzoeken.
  - Tegen raden: de eerste 5 foute codes op rij mogen; daarna is de 2FA van die gebruiker
    1 minuut geblokkeerd, en na elke volgende fout dubbel zo lang, tot 24 uur. Ook een goede
    code werkt dan niet. De teller staat in de database en telt alleen fouten; een goede code
    zet hem op nul. Elke fout komt in het log (gebruiker en IP-adres).
  - Keerzijde: wie je wachtwoord of een sessie heeft, kan je 2FA zo blijven blokkeren. Een
    beheerder heft dat op met "Uitzetten" bij de gebruiker op `/beheer`.
  - 2FA aan- of uitzetten, of door een beheerder laten uitzetten, beëindigt alle andere
    sessies van die gebruiker.
  - Telefoon en herstelcodes kwijt en je bent de enige beheerder:
    `docker exec platform python -m app.cli reset-2fa <e-mailadres>` zet 2FA uit en
    beëindigt alle sessies; log daarna in met je wachtwoord en zet 2FA opnieuw aan.
- **Hervatbare uploads (tus 1.0.0)**, zelf gebouwd: stukken van 16 MB; valt de verbinding weg,
  dan gaat de upload verder waar hij was, ook na herladen van de pagina (kies hetzelfde
  bestand opnieuw). Uploads lopen door als je naar een andere pagina gaat. Lopende uploads
  reserveren hun ruimte in het quotum; "Weggooien" bij een mislukte upload geeft die meteen
  vrij, anders wordt een onafgemaakte upload na 24 uur opgeruimd. Bestaat de naam al, dan
  zegt de server dat meteen bij het begin. Een gemist laatste antwoord levert geen dubbel
  bestand op, en een upload waarvan alle bytes binnen zijn wordt ook na een herstart nog
  afgemaakt. Werkt ook via gewoon http in het LAN.
- **Opslag**: ontdubbelen en opruimen gebeuren nooit tegelijk, zodat definitief verwijderen
  nooit de inhoud weggooit van een bestand dat net met dezelfde inhoud binnenkwam.

## Nog te doen

- Wachtwoord wijzigen en sessies per apparaat bekijken en intrekken
- Thumbnails en previews, met een eigen job-queue
- Zoeken op naam, pad en type
- Delen met gebruikers en publieke links
- WebSocket-events en versies van bestanden
