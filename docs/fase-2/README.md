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

## Klaar (deel 3)

- **Account**: je wachtwoord wijzig je op de accountpagina met je huidige wachtwoord en, als
  2FA aan staat, een code of herstelcode. Foute pogingen tellen mee voor dezelfde blokkade als
  2FA, zodat ook wie alleen een sessie heeft je wachtwoord niet kan raden. Daarna ben je op al
  je andere apparaten uitgelogd. Onder "Apparaten en sessies" zie je waar je ingelogd bent
  (bijv. "Firefox op Windows", met IP-adres en wanneer laatst actief) en log je één apparaat of
  alle andere uit. Uitloggen werkt meteen, ook voor de live updates, niet pas na een kwartier.
  Een beheerder kan op `/beheer` met "Wachtwoord instellen" iemand die zijn wachtwoord kwijt is
  een nieuw geven; die is dan overal uitgelogd. Twee tabbladen die tegelijk openen blijven
  allebei ingelogd.
- **Soort bestand uit de inhoud**: het soort komt uit de eerste 8 KB, niet uit de naam (een
  `.txt` die eigenlijk een PNG is, wordt een afbeelding). Bij tekst helpt de extensie (md, csv,
  json, …).
- **Kopiëren** van bestanden en hele mappen (hooguit 10.000 per keer). De inhoud wordt niet
  nog eens opgeslagen, maar telt wel mee voor je quotum. Bestaat de naam al, dan wordt het
  "x (1)".
- **Versies**: een bestand vervangen (ook via een hervatbare upload) bewaart de vorige inhoud.
  Hooguit 20 per bestand; bekijken, downloaden, terugzetten en verwijderen via Meer… →
  Versies…. Versies tellen mee voor je quotum; definitief verwijderen ruimt ze op.
- **Zoeken** op naam, met filters op soort (mappen, afbeeldingen, video, audio, pdf,
  documenten, archieven) en tag. Hoofdletters en accenten tellen niet, de prullenbak doet niet
  mee, en elk resultaat toont zijn pad. 20.000 bestanden doorzoeken duurt minder dan een
  seconde. Zelf gebouwd op de database (geen Typesense, beslissing B17).
- **Tags** met een naam en kleur, persoonlijk: alleen jij ziet je tags, ook op wat met je
  gedeeld is. Ze staan als chips in de lijst; een klik zoekt erop.
- **Previews en thumbnails** voor afbeeldingen (ook HEIC en AVIF), pdf (eerste pagina), video
  (een beeld, met ffmpeg), audio (hoes, duur, titel, artiest), tekst (eerste 50 regels), zip en
  tar (de inhoudslijst) en epub (de hoes). Een klik op een bestand opent het in een paneel met
  een viewer. Ze worden gemaakt in een apart proces met een grens voor geheugen
  (`PLATFORM_PREVIEW_MEMORY_MB`, standaard 1536) en pixels (`PLATFORM_PREVIEW_MAX_PIXELS`,
  standaard 100 miljoen), zodat een kapot of kwaadaardig bestand de app niet plat legt. Eén
  preview per inhoud; bestaande bestanden krijgen hun preview vanzelf na de update.
- **Achtergrondtaken**: een eigen wachtrij in de database, met `PLATFORM_JOB_WORKERS` workers
  (standaard 2) en een tijdslimiet per taak (`PLATFORM_JOB_TIMEOUT`, standaard 300 s). Een
  mislukte taak gaat na 30 s opnieuw, daarna steeds dubbel zo lang, tot 3 pogingen; daarna
  staat hij onder `/beheer` → Achtergrondtaken, met "Opnieuw" en "Alles opnieuw". Wat bij een
  herstart bleef hangen, gaat terug in de rij.
- **Live updates**: verandert er iets (een upload, een preview die klaar is, een nieuwe
  deling), dan ziet elke open web-app het meteen, ook die van iemand met wie je deelt. Via
  Server-Sent Events in plaats van WebSockets (beslissing B17); het token gaat in een header,
  nooit in een adres.
- **Delen met personen en groepen**: Meer… → Delen…. Zoek iemand op naam of e-mailadres, of
  kies een groep, en geef "Kan bekijken" of "Kan bewerken". Wie iets gedeeld krijgt, ziet het
  onder **Gedeeld met mij**, met van wie het is.
  - Toegang wordt bij elk verzoek opnieuw berekend. Trek je een deling in, laat je hem
    verlopen, gooi je de map (of een map erboven) in de prullenbak, of haal je iemand uit een
    groep, dan kan die er meteen niet meer bij; ook een downloadlink die al uit was werkt dan
    niet meer.
  - Wie iets gedeeld kreeg, ziet niets van wat erboven ligt: niet in het pad, niet in
    zoekresultaten, previews of live updates. Wat niet gedeeld is, bestaat voor die persoon
    niet ("Niet gevonden").
  - Wat een ander in jouw gedeelde map zet, is van jou en telt mee in jouw quotum. Vervangt
    iemand een bestand, dan blijft de vorige versie bewaard, met wie hem verving. Wat een ander
    weggooit, komt in jouw prullenbak. De gedeelde map zelf kan alleen jij hernoemen,
    verplaatsen of weggooien; verplaatsen kan alleen binnen bestanden van dezelfde eigenaar
    (kopiëren kan wel).
  - **Groepen** maak je op `/beheer`; lid worden of eruit gaan werkt meteen.
  - **Gasten** (rol gast) hebben geen eigen bestanden en zien alleen wat met hen gedeeld is,
    ook om te bewerken. Zelf delen kunnen ze niet.
- **Publieke links** (`/s/…`), in hetzelfde venster: om te bekijken (een bestand of een map
  om door te bladeren en te downloaden) of, bij een map, om bestanden in te uploaden zonder
  iets van de inhoud te zien. Optioneel met een wachtwoord (minstens 6 tekens), een
  vervaldatum en een label. Wie de link opent, hoeft niet in te loggen. Uploaden via een link
  is hervatbaar, net als gewoon uploaden, en gaat op jouw quotum.
  - Na het openen krijgt de bezoeker een sleutel die hooguit 12 uur geldig is. Verander je het
    wachtwoord of haal je het weg, dan moet iedereen de link opnieuw openen.
  - Tegen raden: per adres hooguit 20 keer openen per minuut en 100 uploads per uur; na 10
    foute wachtwoorden in 10 minuten gaat de link 10 minuten dicht. Wie het goede wachtwoord
    geeft, telt niet mee, ook niet als veel mensen de link tegelijk openen.
  - Wachtwoorden, tokens en sleutels komen nooit in het log; foute pogingen wel, met de link en
    het adres.
- **Namen** met onzichtbare stuur- of richtingtekens (bijv. om `factuur-exe.pdf` te laten
  lijken op een pdf) worden geweigerd.

## Bekend

- **Ontdubbelen verraadt dat inhoud er al is.** Inhoud wordt over alle gebruikers heen maar
  één keer opgeslagen, en de preview hoort bij die inhoud. Upload je een bestand dat iemand
  anders op de server al heeft, dan is de preview meteen klaar; bij nieuwe inhoud staat hij
  eerst op "wordt gemaakt". Wie een bestand precies kan namaken, kan zo nagaan of iemand het
  al heeft (niet wie, en niets van de inhoud die hij nog niet had). Dat volgt uit één preview
  per inhoud; voor een platform voor één huishouden of team is dat nu zo gelaten.

## Nog te doen

- Virusscan van uploads als achtergrondtaak (het stappenplan noemt ClamAV; eerst kijken of
  dat past bij "zo weinig mogelijk bestaande tools")
- Loadtest: 50 uploads tegelijk en 200 verzoeken per seconde op een maplijst
- De OpenAPI-beschrijving gebruiken om de clients voor de apps te maken (TypeScript, Kotlin)
- De hele testsuite ook tegen PostgreSQL draaien (nu alleen SQLite; de queries zijn wel
  dialect-neutraal geschreven)
