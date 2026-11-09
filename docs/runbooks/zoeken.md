# Runbook: zoeken en foto's

Fase 6 (0.8.0) zoekt in de inhoud van documenten, zet plaatsnamen en landen bij foto's, vindt
duplicaten en kent personen. Zoeken op naam zit in de database zelf (triggers) en werkt meteen.
De rest komt op de achtergrond: de inhoud van documenten, de plek, camera en hash van foto's.
Wat fase 6 is en hoe je bijwerkt, staat in `infra/single/README.md` ("Bijwerken naar fase 6").
Dit runbook gaat over wat het script doet, de reservekopie, de plaatsnamen, de stand lezen, de
index herbouwen, zoeken meten en terug naar 0.7.1.

Alles hieronder gebeurt in de Console van de container (de LXC), als root. `dc` is de functie uit
de README (Handige commando's): `docker compose` met precies de bestanden van het
installatiescript. `/tank/platform` is `DATA_ROOT` uit `/opt/platform/.env`; zonder `/tank` is
dat `/opt/platform/data/platform`. In de app heet die map `/data`.

Het installatiescript staat, zolang fase 6 niet op `main` staat, op de branch:

```bash
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/claude/stappenplan-uitwerken-lu4uv3/infra/single/install.sh | PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 bash
```

Hieronder heet dat kortweg "het script"; een instelling zet je vóór `bash`, zoals
`… | PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 PLAATSEN=uit bash`.

## Wat het script bij elke run voor zoeken doet

1. Het leest `PLAATSEN` uit de omgeving vóór `/opt/platform/.env`. Alleen `aan` of `uit`; iets
   anders stopt het script meteen, vóór er iets verandert. Staat er in `.env` iets anders dan
   `aan` of `uit`, dan stopt het ook, met de vraag het goed te zetten.
2. Ontbreekt `PLAATSEN` in `.env`, dan komt `PLAATSEN=aan` erbij. Een bestaande waarde blijft,
   tenzij je er zelf een meegeeft.
3. **Plaatsnamen.** Bij `aan` haalt het de twee bronnen op naar `/tank/platform/geo`, als ze er nog
   niet (goed) staan. Elk bestand komt eerst in `<naam>.part`, en pas op zijn plek als het
   precies zoveel bytes heeft en de sha256 klopt (rechten 640, van uid 10001). Lukt het niet, dan
   zegt het script `Plaatsnamen: niet geladen` en gaat het door: dit stopt het script nooit.
4. Office laat open documenten opslaan, zoals altijd.
5. **De reservekopie** van de database, terwijl de oude app nog draait (hieronder). Lukt die niet,
   dan stopt het script hier, vóór er iets verandert aan de app.
6. Het vervangt de app en wacht tot `/health` antwoordt. Bij de eerste start van 0.8.0 draait
   migratie 0010 (seconden). Bij de start bouwt de app `geo/places.sqlite` uit de bronnen
   (±1 minuut). Werd de app in deze run niet opnieuw gestart (hetzelfde image, dezelfde
   instellingen) maar zijn de bronnen nieuw, dan bouwt het script hem in de draaiende app
   (`app.cli geo-load`).
7. Dan media en office, zoals altijd.
8. De eindtekst zegt `Zoeken op naam werkt meteen. De inhoud wordt nu op de achtergrond gelezen;
   volg het op /beheer → Zoeken en foto's.`, de stand van de plaatsnamen (met het commando voor
   aan, uit of opnieuw proberen) en waar de reservekopie van deze run staat.

## De reservekopie

Vóór elke update maakt het script een kopie van de database:
`/tank/platform/backup/platform-<schemaversie>-<tijd>.db`, bijvoorbeeld
`platform-0009-20261109T201502.123Z.db` (de tijd in UTC). De schemaversie is de alembic-head van
de database op dat moment: `0009` voor 0.7.1, `0010` voor 0.8.0.

- **Hoe.** Met `sqlite3.Connection.backup`, terwijl de app draait: de kopie klopt, ook als er
  net iemand iets opslaat. Het script draait daarvoor `api/app/backup.py` van de nieuwe code, met
  het image dat er nu is en alleen de standaardbibliotheek van Python. Zo werkt het ook met het
  image van 0.7.1, waar `app.cli db-backup` nog niet in zit.
- **Zonder gezichten en zonder beeld.** De kopie gaat eerst naar een `.part`. Daarin gaan de rijen
  van `face_person`, `face`, `face_scan`, `face_owner` en `blob_embed` weg (voor zover die tabellen
  er zijn), met `secure_delete`, zodat ook de vrije pagina's leeg zijn. Pas dan krijgt hij zijn
  echte naam. Na het terugzetten rekent de app ze opnieuw. Verder staat de hele database erin,
  met accounts, delingen en de geheimen van 2FA: daarom rechten 600 (de map 700), van de gebruiker
  van de app, en nooit in een log. Bewaar een kopie die je ergens anders neerzet, net zo afgeschermd
  als de `.env`.
- **Welke blijven.** Per schemaversie de nieuwste. Een tweede run op 0.8.0 vervangt dus de vorige
  kopie van `0010`, maar de laatste kopie van `0009` (van vóór de migratie) blijft altijd staan.
  Bestanden in de map die niet zo heten, raakt het script niet aan. Ruimte: reken op twee keer de
  grootte van `platform.db`.
- **Mislukt.** Dan is er geen kopie (ook geen `.part`), staat de melding boven de regel "De
  reservekopie van de database lukte niet", en stopt het script. De app draait zoals hij draaide.
  Bijvoorbeeld een volle schijf (`df -h /tank`), of iets anders op de plek van de map `backup`.
  Los het op en draai het script opnieuw.
- **Eerste installatie** (nog geen database): geen kopie.

Een kopie voor de hand, met de app van 0.8.0 (dezelfde tekst, dezelfde regels):

```bash
dc exec -T platform python -m app.cli db-backup
ls -l /tank/platform/backup
```

Dit is een vangnet voor de update, geen back-up: de kopie staat op dezelfde schijf. De back-up
blijft de ZFS-snapshot van `/tank/platform` (README, "Back-up").

**Een kopie terugzetten.** Alleen als de database zelf kapot is, of `db-downgrade` (hieronder)
niet lukt. Alles wat na de kopie veranderde, staat er niet in: nieuwe mappen, uploads, delingen
en wijzigingen sinds die run zijn dan uit de database weg (de bestanden van nieuwe uploads blijven
als losse blobs op schijf). De database van nu bewaar je ernaast; ook de `-wal` en `-shm` gaan
mee, anders leest SQLite ze bij de kopie in:

```bash
dc stop platform media
cd /tank/platform
ls -l backup/
mkdir -m 700 database-voor-terugzetten
for f in platform.db platform.db-wal platform.db-shm; do [ ! -e "$f" ] || mv "$f" database-voor-terugzetten/; done
install -m 600 -o 10001 -g 10001 backup/platform-0009-<tijd>.db platform.db
```

Kies bij `install` de kopie die je wilt (de tijd staat in de naam). Daarna het script van de versie
die bij de kopie hoort. Bij een kopie van `0009` is dat het script van 0.7.1: de eerste drie regels
en de laatste van "Terug naar 0.7.1" (hieronder); `dc stop` en de downgrade sla je over, want de app
staat al stil en de kopie staat al op 0009. Bij een kopie van `0010` is het het gewone script.
Gezichten en beeld rekent de app daarna opnieuw; de index van zoeken staat gewoon in de kopie.

## Plaatsnamen

Het land komt uit de grenzen van Natural Earth 10m (publiek domein), de plaats uit GeoNames
cities1000 (CC BY 4.0, de vermelding staat op `/over`): de dichtstbijzijnde plaats binnen 30 km,
in hetzelfde land. Alles gebeurt in de container, zonder iets online op te zoeken. De bronnen
(±16 MB) staan in `/tank/platform/geo`, met de tabel die de app ervan maakt (`places.sqlite`).

- **Aan of uit.** `PLAATSEN=aan` (standaard) of `PLAATSEN=uit` in `/opt/platform/.env`. Veranderen
  doe je met het script: `… | PLATFORM_BRANCH=… PLAATSEN=uit bash`, en weer aan met `PLAATSEN=aan`.
  Bij `uit` haalt het script niets op en laadt de app geen plaatsnamen; de rest van zoeken en
  foto's werkt gewoon, en de kaart heeft dan alleen de plekken uit GPS.
- **Niet geladen.** Lukte het ophalen niet (geen internet, GitHub of PyPI niet bereikbaar, een
  sha256 die niet klopt), dan werkt het platform zonder plaatsnamen. Draai het script later
  opnieuw: het haalt alleen op wat er nog niet goed staat. Wat er misging, staat in de uitvoer
  van het script (`Kon <bestand> niet ophalen, of de sha256 klopt niet (<adres>).`).
- **Met de hand laden**, bijvoorbeeld als de eindtekst zei `Let op: plaatsnamen laden lukte niet`:

  ```bash
  dc exec -T platform python -m app.cli geo-load
  dc exec -T platform python -m app.cli geo-load --force
  ```

  Zonder `--force` doet het niets als `places.sqlite` er al goed staat; met `--force` bouwt het
  hem opnieuw. Exitcode 1 als een bron ontbreekt of niet klopt.
- **De plekken opnieuw.** Na nieuwe bronnen krijgen foto's hun plaats bij de volgende keer dat
  ze gelezen worden. Alles opnieuw: `/beheer` → Zoeken en foto's → **Herbouwen**, de plekken
  (hieronder).

## De stand lezen

Op `/beheer` → **Zoeken en foto's** staat:

- de motor: `fts` (de index van SQLite) of `like` (de terugval, langzamer, zonder rangorde);
- de index: namen (altijd klaar), tekst `n` van `m`, wat mislukte (met de sha en een code, nooit
  een naam), en een herbouw die loopt, met zijn voortgang;
- of tekst in de zandbak van de mediaworker gelezen wordt of erbuiten;
- per uur: hoe vaak een zoekvraag tegen zijn tijdsgrens aanliep, en hoe vaak zoeken te druk was;
- OCR (als die er is), de plaatsnamen (geladen, de bron, de datum) en de laatste meting
  (hieronder);
- de knoppen: **Herbouwen**, **Mislukte opnieuw** en **Achtergrondwerk pauzeren**.

Hoe lang de achtergrond duurt (geschat voor één core; de meting hieronder zegt wat jouw CPU
haalt):

| Wat | Voor 100.000 bestanden (20.000 documenten, 55.000 foto's) |
|---|---|
| tekst uit documenten | ±10–15 minuten |
| de index bijwerken | ±30 seconden |
| GPS, camera en hash van foto's | ±2,5–5 minuten |
| land en plaats | ±1 minuut |

Het achtergrondwerk wijkt voor media en office: lopen er 2 of meer omzettingen van video, of start
OnlyOffice, dan wacht het even. Zoeken op naam wacht nooit.

Vanaf de console:

```bash
dc exec -T platform python -m app.cli search-check
dc exec -T platform python -m app.cli search-check --volledig
```

`search-check` vergelijkt 1.000 willekeurige bestanden in de index met de bron (`--volledig`: alle)
en noemt elk verschil; exitcode 1 bij een verschil. Loopt er een herbouw, dan zegt het dat en wacht
je tot hij klaar is.

## De index herbouwen

Nodig na een melding van `search-check`, of als zoeken iets niet vindt dat er wel is. Herbouwen gaat
in stukken terwijl de app draait; zoeken werkt ondertussen gewoon (met wat er al is). Op `/beheer`
→ Zoeken en foto's → **Herbouwen** kies je wat:

- **Namen** of **Tekst**: de index opnieuw uit wat de database al heeft. Documenten worden niet
  opnieuw gelezen. Loopt er al een, dan zegt `/beheer` dat (`Er loopt al een herbouw`);
- **Plekken**: plek, camera en datum van alle foto's opnieuw lezen (na nieuwe bronnen);
- **Hashes**: de hashes voor duplicaten opnieuw; tot een foto zijn nieuwe heeft, blijft de oude.

Of vanaf de console, dezelfde herbouw van de index:

```bash
dc exec -T platform python -m app.cli search-rebuild
```

Het zegt per stuk hoe ver het is (`tot num … van …`), en aan het eind hoe lang het langste stuk
duurde. Zijn de triggers van zoeken zelf stuk (een melding van `search-check` die na een herbouw
terugkomt), dan zet `search-triggers` ze terug en herbouwt het daarna:

```bash
dc exec -T platform python -m app.cli search-triggers
```

**Mislukte opnieuw** zet alles wat mislukte weer in de rij (een document of foto die niet te
lezen was). Wat weer mislukt, komt weer in de lijst; vaak is het bestand zelf
kapot of versleuteld. **Achtergrondwerk pauzeren** zet het lezen stil (ook een herbouw, tussen twee
stukken); zoeken blijft werken. Vergeet niet het weer aan te zetten: de pauze blijft staan, ook na
een herstart.

## Zoeken meten

De meting maakt een boom van 100.000 bestanden in een tijdelijke map naast de app, meet zoeken voor
"eigenaar" (één persoon) en "alles" (30 personen), en ruimt alles weer op. Er verandert niets aan
de app of je bestanden. Het duurt ±2 minuten en heeft even ±1,1 GB vrij nodig in `/data/cache`
(`/tank/platform/cache`). Doe het op een rustig moment: het vraagt veel van de CPU.

```bash
dc exec -T platform mkdir -p /data/cache
dc exec -T platform python -I - --los --json /data/cache/zoekbench.json < /opt/platform/src/tools/zoekbench/bench.py
```

De uitvoer is een tabel per bereik: p50, p95 en max in ms, en hoe vaak een snellere ronde nodig
was. De poort van het ontwerp is een p95 onder 200 ms. `/beheer` → Zoeken en foto's toont de p95
van "eigenaar" en "alles" uit `zoekbench.json`, met de tijd van de meting. Exitcode 3 betekent dat
SQLite geen FTS5 kent; dan zoekt de app met LIKE.

## Terug naar 0.7.1

Alleen als 0.8.0 niet goed werkt. Liever niet: wat in fase 6 bijkwam, is daarna weg. Het zijn deze
commando's, in deze volgorde, met de volledige commit van 0.7.1 (`80d5bb8`, de laatste vóór
fase 6):

```bash
git -C /opt/platform/src fetch -q origin
git -C /opt/platform/src cat-file -e '80d5bb8ce9cd937534f06a5bc5e4f54ec8509ad1^{commit}' && echo "goed: 0.7.1 is er"
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/80d5bb8ce9cd937534f06a5bc5e4f54ec8509ad1/infra/single/install.sh -o /root/platform-install-0.7.1.sh && echo "goed: het script is er"
dc stop platform media
dc run --rm --no-deps platform python -m app.cli db-downgrade 0009
PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 PLATFORM_SRC_REF=80d5bb8ce9cd937534f06a5bc5e4f54ec8509ad1 bash < /root/platform-install-0.7.1.sh
```

Per stap:

1. **Kijken of 0.7.1 er is, en het script in een bestand, vóór je iets stopt.** Zeggen de tweede
   en de derde regel niet allebei "goed", stop dan: de rest pas als de commit en het script er
   zijn. Het script komt van de commit van 0.7.1 zelf, niet van de branch: zo draait het oude
   script met het oude image. In een bestand, omdat `curl … | bash` met een adres dat er niet is
   één regel van curl geeft en verder niets doet; dan stond de app al stil met het schema terug.
2. **Stoppen** (`dc stop platform media`), zodat niets meer naar de nieuwe tabellen schrijft.
3. **Het schema terug** met het image van nu, want alleen dat kent 0010. `db-downgrade` vraagt
   eerst, als die er zijn, naar album-items die uit een regel van een slim album komen en niet
   gedeeld worden: in 0.7.1 deelt een album elk item, dus die items zouden ineens gedeeld zijn.
   Enter of "ja" haalt ze eerst uit het album (de standaard). Dan zegt het wat er weggaat:

   > Weg zijn daarna: de zoekindex, de plekken van foto's, de hashes voor duplicaten, de personen
   > die je met de hand benoemde en de regels van slimme albums. Bestanden, delingen en albums
   > blijven, en zoeken op naam werkt gewoon.

   en vraagt om "ja". Alles anders dan "ja" doet niets. Aan het eind:
   `De database staat nu op revisie 0009.` Zegt het iets anders, stop dan, en zet de reservekopie
   terug (hierboven).
4. **0.7.1 installeren** met het script van 0.7.1. Het bouwt het image van 0.7.1 en start het.
   De reservekopieën en `/tank/platform/geo` blijven staan; 0.7.1 gebruikt ze niet.

Wat blijft: je bestanden en hun versies, mappen, delingen, albums (zonder regels), accounts en 2FA,
office en media. Wat weg is: de index (0.7.1 zoekt op naam zoals vroeger), plaatsnamen, plekken op
de kaart, duplicaten, personen en de regels van slimme albums.

**Weer vooruit.** Het script van de branch, zonder `PLATFORM_SRC_REF`:

```bash
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/claude/stappenplan-uitwerken-lu4uv3/infra/single/install.sh | PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 bash
```

Het maakt eerst een nieuwe kopie van `0009` (die vervangt de vorige), migreert weer naar 0010, en
de achtergrond vult de index, de plekken en de hashes opnieuw (zie de tabel bij "De stand lezen").
Personen die je met de hand benoemde, en de regels van slimme albums, komen niet terug.

De CI-job `infra` doet deze weg elke push echt (`infra/single/tests/test_install_search.sh
--stappen`, stap 6 en 7): met het script van `80d5bb8`, `db-downgrade 0009` en weer vooruit.
