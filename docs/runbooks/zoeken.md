# Runbook: zoeken en foto's

Fase 6 (0.8.0) zoekt in de inhoud van documenten, zet plaatsnamen en landen bij foto's, vindt
duplicaten en kent personen. Zoeken op naam zit in de database zelf (triggers) en werkt meteen.
De rest komt op de achtergrond: de inhoud van documenten, de plek, camera en hash van foto's.
Wat fase 6 is en hoe je bijwerkt, staat in `infra/single/README.md` ("Bijwerken naar fase 6").
Dit runbook gaat over wat het script doet, de reservekopie, de plaatsnamen, de stand lezen, de
wekelijkse controle, de index herbouwen, zoeken meten, zoeken naast media en office meten, en
terug naar 0.7.1.

Alles hieronder gebeurt in de Console van de container (de LXC), als root. `dc` is de functie uit
de README (Handige commando's): `docker compose` met precies de bestanden van het
installatiescript. De data staat in `DATA_ROOT` uit `/opt/platform/.env`: `/tank/platform`, of
zonder mount point `/tank` `/opt/platform/data/platform`. In de app heet die map `/data`. De
commando's hieronder gebruiken `$DATA_ROOT`; zet die eerst in de Console (review golf 4, contract
F3):

```bash
DATA_ROOT=$(sed -n 's/^DATA_ROOT=//p' /opt/platform/.env) && echo "$DATA_ROOT"
```

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
3. **Plaatsnamen.** Bij `aan` haalt het de twee bronnen op naar `$DATA_ROOT/geo`, als ze er nog niet
   (goed) staan. Elk bestand komt eerst in `<naam>.part`, en pas op zijn plek als het
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
`$DATA_ROOT/backup/platform-<schemaversie>-<tijd>.db`, bijvoorbeeld
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
  grootte van `platform.db`. Die kopie van `0009` is van de dag van de update: wat daarna
  veranderde, staat er niet in. Wie 0.8.0 vers installeerde, heeft er geen. "Terug naar 0.7.1"
  (hieronder) maakt daarom eerst een verse kopie van `0010` (review golf 4, uitrol F5).
- **Mislukt.** Dan is er geen kopie (ook geen `.part`), staat de melding boven de regel "De
  reservekopie van de database lukte niet", en stopt het script. De app draait zoals hij draaide.
  Bijvoorbeeld een volle schijf (`df -h "$DATA_ROOT"`), of iets anders op de plek van de map
  `backup`. Los het op en draai het script opnieuw.
- **Eerste installatie** (nog geen database): geen kopie.

Een kopie voor de hand, met de app van 0.8.0 (dezelfde tekst, dezelfde regels):

```bash
dc exec -T platform python -m app.cli db-backup
ls -l "$DATA_ROOT/backup"
```

Dit is een vangnet voor de update, geen back-up: de kopie staat op dezelfde schijf. De back-up
blijft de ZFS-snapshot van `/tank/platform` (README, "Back-up"; zonder `/tank` is er geen).

**Een kopie terugzetten.** Alleen als de database zelf kapot is, of `db-downgrade` (hieronder)
niet lukt. Alles wat na de kopie veranderde, staat er niet in: nieuwe mappen, uploads, delingen
en wijzigingen sinds die run zijn dan uit de database weg (de bestanden van nieuwe uploads blijven
als losse blobs op schijf). De database van nu bewaar je ernaast; ook de `-wal` en `-shm` gaan
mee, anders leest SQLite ze bij de kopie in:

```bash
dc stop platform media
cd "$DATA_ROOT"
ls -l backup/
mkdir -m 700 database-voor-terugzetten
for f in platform.db platform.db-wal platform.db-shm; do [ ! -e "$f" ] || mv "$f" database-voor-terugzetten/; done
install -m 600 -o 10001 -g 10001 backup/platform-0009-<tijd>.db platform.db
```

Kies bij `install` de kopie die je wilt (de tijd staat in de naam). Daarna het script van de versie
die bij de kopie hoort. Bij een kopie van `0009` is dat het script van 0.7.1: de eerste drie regels
en de laatste van "Terug naar 0.7.1" (hieronder); `dc stop`, de kopie en de downgrade sla je over,
want de app staat al stil en de kopie staat al op 0009. Bij een kopie van `0010` is het het gewone
script.
Gezichten en beeld rekent de app daarna opnieuw; de index van zoeken staat gewoon in de kopie.

## Plaatsnamen

Het land komt uit de grenzen van Natural Earth 10m (publiek domein), de plaats uit GeoNames
cities1000 (CC BY 4.0, de vermelding staat op `/over`): de dichtstbijzijnde plaats binnen 30 km,
in hetzelfde land. Alles gebeurt in de container, zonder iets online op te zoeken. De bronnen
(±16 MB) staan in `$DATA_ROOT/geo`, met de tabel die de app ervan maakt (`places.sqlite`).

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
- **De plekken opnieuw.** Na elke nieuwe bouw van `places.sqlite` (door de app bij de start, of
  met `geo-load`) krijgen foto's met een GPS-plek maar nog zonder plaats en land die vanzelf: op
  de achtergrond, één keer per bouw. Zet je `PLAATSEN` van `uit` weer op `aan`, dan doet de
  eerstvolgende start dat ook. Dat vraagt wel dat de start met `uit` zijn notitie van de vorige
  bouw kon wissen. Staat in het log van die start `Plaatsnamen: Setting geo.places niet gewist`,
  dan krijgen de foto's die met `uit` gelezen zijn pas een plaats na **Plek, camera en datum
  opnieuw lezen**. Op zee blijft de plaats leeg. Een foto die al een plaats had,
  houdt die tot hij opnieuw gelezen wordt. Alles opnieuw: `/beheer` → Zoeken en foto's → **Plek,
  camera en datum opnieuw lezen** (hieronder).

## De stand lezen

Op `/beheer` → **Zoeken en foto's** (Alt+9; alleen voor een beheerder) staat, van boven naar
beneden, en elke 5 seconden ververst:

- **Zoekmachine**: `FTS5 (snel)` (de index van SQLite) of `LIKE (langzamer)` (de terugval, zonder
  rangorde: tijdens een herbouw, of als deze SQLite geen FTS5 kent). Daaronder **Tekst uit
  documenten lezen**: `in de zandbak van de mediaworker` of `buiten de zandbak (zonder
  mediaworker)`;
- **Achtergrondwerk**: `loopt` of `gepauzeerd`, met de knop **Achtergrondwerk pauzeren** (of
  **Achtergrondwerk hervatten**);
- **Index**: **Namen** `klaar` (of `worden opnieuw opgebouwd`), **Tekst uit bestanden** `n van m
  bestanden` met een balk, **Mislukt** `n inhouden` en **Controle (wekelijks)** (zie "De
  wekelijkse controle" hieronder); de knop **Index herbouwen**;
- **Herbouwen**, alleen als er een loopt: wat (`De index`, `Plek, camera en datum`,
  `Duplicaten`), hoe ver, sinds wanneer, met een balk;
- **Mislukte inhouden**: `Niets mislukt.`, of de knoppen **Mislukte opnieuw** en **Lijst van
  mislukte tonen**. De lijst noemt per inhoud de sha256, de stap, een code en wanneer; nooit een
  naam, want één inhoud kan bij meer bestanden horen;
- **Foto's: plek en duplicaten**: de knoppen **Duplicaten opnieuw berekenen** en **Plek, camera
  en datum opnieuw lezen**;
- **Het afgelopen uur**: hoe vaak een zoekvraag tegen zijn tijdsgrens aanliep (ronde B en C), en
  hoe vaak zoeken te druk was;
- **OCR** (geïnstalleerd, aan, pagina's in de rij), **Plaatsnamen** (geladen, de bron, de datum)
  en **Zoekbench** (de laatste meting, hieronder).

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

`search-check` vergelijkt 1.000 willekeurige bestanden in de index met de bron; met `--n` kies je
een ander aantal, en een `--n` boven het aantal bestanden vergelijkt ze allemaal. Het noemt de
eerste 50 verschillen (met het nummer van de node, geen naam) en telt de rest; exitcode 1 bij een
verschil. `--volledig` doet daarnaast de integrity-check van FTS5, die de hele index leest; het
aantal vergeleken bestanden blijft dat van `--n`. Loopt er een herbouw, dan zegt het dat en wacht
je tot hij klaar is.

## De wekelijkse controle

De app kijkt zelf of de index nog klopt met de bron. Eens per week vergelijkt hij 200 willekeurige
bestanden in de index met wat de database heeft, zoals `search-check` hierboven, maar met minder
bestanden. Hij leest alleen, in één momentopname, en houdt het schrijven van de app niet op: wie
iets uploadt of opslaat, merkt er niets van.

- **Wanneer.** Elk uur kijkt de app of het tijd is. Is er nog geen uitkomst, dan meteen: de eerste
  keer dus kort na de eerste start van 0.8.0. Daarna een week na de vorige uitkomst. Die tijd staat
  in de database, dus een herstart begint de week niet opnieuw.
- **Geen uitkomst: het volgende uur opnieuw.** Staat het achtergrondwerk op pauze (**Achtergrondwerk
  pauzeren**), loopt er een herbouw van de index, of zijn er nog geen bestanden, dan slaat hij zijn
  beurt over en probeert hij het een uur later. De vorige uitkomst blijft dan staan.
- **Alleen met FTS5.** Zoekt de app met LIKE omdat er geen index is (deze SQLite kent geen FTS5,
  `PLATFORM_SEARCH_INDEX=uit`, of PostgreSQL), dan valt er niets te vergelijken en loopt hij niet.
- **Waar je het ziet.** Op `/beheer` → Zoeken en foto's, bij Index, de regel **Controle
  (wekelijks)**: `nog niet gedaan`, `<tijd>: 200 bestanden vergeleken, geen verschil`, of in
  oranje, als waarschuwing, `<tijd>: 3 verschillen met de bron (200 vergeleken)`. Elk bestand met
  een verschil telt één keer. Met LIKE staat er `loopt alleen met FTS5`. In het log van de app
  staat alleen hoeveel bestanden hij vergeleek en hoeveel verschillen hij vond, nooit een naam of
  tekst.

**Bij een verschil.** Onder de oranje regel zegt `/beheer` wat je doet:

1. Herbouw de index: de knop **Index herbouwen** (zie "De index herbouwen" hieronder). Wil je eerst
   weten welke bestanden het zijn, draai dan `search-check --n <meer dan het aantal bestanden>`
   (hierboven). De wekelijkse controle bewaart alleen aantallen, en `search-check` trekt een eigen
   steekproef; met zo'n `--n` vergelijkt hij alle bestanden (dat leest alles en duurt bij veel
   bestanden even). Hij noemt de eerste 50 verschillen.
2. Wacht tot de herbouw klaar is. Na een verschil kijkt de app niet pas na een week, maar elk uur
   opnieuw. Binnen een uur na de herbouw staat er dus `geen verschil`, of komt het verschil terug.
3. Komt het verschil na de herbouw terug, dan zijn de triggers van zoeken zelf stuk. Zet ze terug
   met `search-triggers` (onder "De index herbouwen"); dat herbouwt de index daarna ook.
4. Komt het daarna nog terug, dan klopt er iets in de app zelf niet. Bewaar de uitvoer van
   `search-check` (alleen de nummers van de bestanden, geen namen) voor wie het uitzoekt.

Herstellen doet de controle zelf nooit: dat blijft met de hand.

## De index herbouwen

Nodig na een verschil in de wekelijkse controle of van `search-check`, of als zoeken iets niet vindt
dat er wel is. Herbouwen gaat in stukken terwijl de app draait. Tot de nieuwe index klaar is, zoekt
iedereen met LIKE: dat werkt, alleen langzamer en zonder rangorde. Op `/beheer` → Zoeken en foto's:

- **Index herbouwen** (bij Index): de namen en de tekst opnieuw in de index, uit wat de database
  al heeft. Documenten worden niet opnieuw gelezen. Eerst vraagt het `De index herbouwen?`;
  **Herbouwen** begint, **Annuleren** (of Esc) niet. Zolang hij loopt, is de knop weg en staat
  onder Index de voortgang. Bij 100.000 bestanden duurt het een paar minuten;
- **Plek, camera en datum opnieuw lezen** (bij Foto's): van alle foto's en video's opnieuw (na
  nieuwe bronnen);
- **Duplicaten opnieuw berekenen**: alle foto's een nieuwe pHash; tot een foto zijn nieuwe heeft,
  blijft de oude.

Ook die twee vragen eerst (**Starten** of **Annuleren**). Er loopt hooguit één herbouw van de index
tegelijk, en één van de foto's.

Of vanaf de console, dezelfde herbouw van de index:

```bash
dc exec -T platform python -m app.cli search-rebuild
```

Het zegt per stuk hoe ver het is (`tot num … van …`), en aan het eind hoe lang het langste stuk
duurde. Zijn de triggers van zoeken zelf stuk (een verschil van de wekelijkse controle of van
`search-check` dat na een herbouw terugkomt), dan zet `search-triggers` ze terug en herbouwt het
daarna:

```bash
dc exec -T platform python -m app.cli search-triggers
```

**Mislukte opnieuw** zet alles wat mislukte weer in de rij (een document of foto die niet te
lezen was). Wat weer mislukt, komt weer in de lijst; vaak is het bestand zelf
kapot of versleuteld. **Achtergrondwerk pauzeren** zet het lezen stil (de index en OCR, en ook een
herbouw van de index, tussen twee stukken); zoeken blijft werken. Vergeet niet op
**Achtergrondwerk hervatten** te drukken: de pauze blijft staan, ook na een herstart.

## Zoeken meten

De meting maakt een boom van 100.000 bestanden in een tijdelijke map onder `/data/cache`
(`$DATA_ROOT/cache`), meet zoeken voor "eigenaar" (één persoon) en "alles" (30 personen), en
ruimt die map weer op, ook bij een fout of Ctrl-C. Er verandert niets aan de app of je bestanden.
Het duurt een paar minuten en heeft even ±1,1 GB vrij nodig (plus 512 MB reserve; met minder
begint hij niet). Doe het op een rustig moment: het vraagt veel van de CPU.

```bash
dc exec -T platform python -m app.cli bench --los
```

De uitvoer is een tabel per bereik: p50, p95 en max in ms, en hoe vaak een snellere ronde nodig
was. De poort van het ontwerp is een p95 onder 200 ms. Het korte deel komt in
`/data/cache/zoekbench.json` (alleen tijden, aantallen en de versies van SQLite en Python), en
`/beheer` → Zoeken en foto's → **Zoekbench** toont de p95 van "eigenaar" en "alles", met de tijd
van de meting. Exitcode 1: de bench is er niet, er is te weinig ruimte, of hij faalde (de vorige
uitkomst blijft dan staan). Exitcode 3: deze SQLite kent geen FTS5; dan zoekt de app met LIKE.

## Zoeken naast media en office meten

`e2e/perf/zoek_samen.py` (bouwontwerp §4.5) meet of zoeken, de app en het achtergrondwerk goed
samengaan met de belastingtest van media en, als je wilt, bewerkers in OnlyOffice. Het draait niet
in de container van de app, maar in een checkout van de repository met `uv` en `ffmpeg` (de
sandbox, of een eigen machine), met eigen servers in een tijdelijke map. Je bestanden doen niet
mee.

Drie stappen, elk zolang de belastingtest loopt (`app.cli media-loadtest`):

1. **pauze**: een rij met documenten om te lezen, maar het achtergrondwerk op pauze. De nulmeting.
2. **achtergrond**: de pauze eraf zodra media loopt, en photometa leest alle foto's opnieuw (de
   herbouw hash). De index wijkt dan (Defer, standaard bij 2 omzettingen); de poort is dat hij
   binnen 90 s na de belastingtest weer leest.
3. **zonder_defer**: nieuwe documenten, en Defer uit (`PLATFORM_INDEX_DEFER_TRANSCODES=1000`). Een
   rapport: wat er gebeurt als alleen `nice` en de idle IO-klasse beschermen.

De last van §4.5: per stap 5.000 documenten, en één keer 5.000 foto's (640 × 480 met EXIF, 85%
met een plek, elk met zijn voorbeeld; in de map "Foto's") en 500 gescande PDF's voor OCR (in de map
"Scans"). De foto's staan er als al gelezen, zodat photometa pas bij de herbouw van stap 2 en 3 aan
het werk gaat.

Tijdens elke stap: 2 zoekvragen per seconde, `GET /files/root` en `/health` elke seconde,
`MemAvailable` van de machine, hoe ver text, ocr en photometa zijn, en of de app en de worker nog
dezelfde processen zijn. De poorten (stap 2): elke start van media ≤ 5 s en 0 keer haperen, zoeken
p95 ≤ 200 ms, `/files/root` p95 ≤ 200 ms, `/health` p95 ≤ 50 ms, `MemAvailable` ≥ 1,5 GiB, geen
container `OOMKilled` (DS, met een geheugengrens), de app en de worker leven nog, de index leest
weer binnen 90 s, en met OnlyOffice het opslaan ≤ 1,2 × de nulmeting en 0 verloren woorden. Elke
poort zegt `gehaald`, `niet gehaald` of `niet te meten`. Aan het eind staat een blok `=== kort ===`.

Nodig, naast `uv` en `ffmpeg`: een gebouwde web-app (`cd web && npm run build`), en voor de scans
`tesseract` met `nld`, `eng` en `osd` en het lettertype DejaVu Sans (`fonts-dejavu-core`). Met
`--kort` slaat het script de scans zonder die over en zegt dat (`niet in deze meting`); zonder
`--kort` stopt het meteen met de reden.

**Kort** (zoals de job `e2e-media-perf` in CI, als rapport: 60 s per fase, 1 sessie media,
300 documenten per stap, 300 foto's en, met `tesseract`, 20 scans; zonder OnlyOffice; ±5 minuten):

```bash
cd api
uv run python ../e2e/perf/zoek_samen.py --kort --json /tmp/zoek-samen.json
```

**Heel, met OnlyOffice** (300 s per fase, 3 sessies, 5.000 documenten per stap, 5.000 foto's,
500 scans, 5 bewerkers; ±40 minuten, plus het maken van de foto's en scans). Dit gebruikt de echte
DS van de office-metingen (`e2e/office_ds.sh`, zie `docs/fase-4/e2e.md`, "Met de echte DS") en
Chromium voor de bewerkers. DS moet een geheugengrens hebben, anders zegt `OOMKilled` niets en
stopt het script: `office_ds.sh` zet `--memory 2g` alleen als
`docker info --format '{{.MemoryLimit}} {{.CPUCfsQuota}}'` `true true` zegt (anders start DS met
de hand met `--memory 2g --memory-swap 2g`). Draai het op een rustige machine met ±8 GB vrij:

```bash
export OFFICE_DS_NAME=meet-ds OFFICE_DS_PORT=18150 OFFICE_DS_DIR=/tmp/meet-ds \
  OFFICE_DS_CONFIG=$PWD/infra/single/office/local-production-linux.json
bash e2e/office_ds.sh start && bash e2e/office_ds.sh wait
cd web && npm run build && cd ../api
E2E_REAL_DS=http://127.0.0.1:18150 E2E_REAL_DS_SECRET_FILE=/tmp/meet-ds/jwt.secret \
  uv run --with playwright python ../e2e/perf/zoek_samen.py --office --json /tmp/zoek-samen.json
cd .. && bash e2e/office_ds.sh stop
```

Kleiner kan met `--duur`, `--sessies` en `--bewerkers` (bijvoorbeeld `--sessies 2 --bewerkers 3`,
de maat voor 8 GB van §4.5). Met `--docs`, `--fotos` of `--scans` kleiner is het niet meer de last
van §4.5; dat staat dan onder `niet in deze meting`. Exitcode 1 als een poort van stap 2 niet
gehaald of niet te meten is (met `--kort` altijd 0). Zonder `--office` zijn de poorten van
OnlyOffice en van `OOMKilled` niet te meten, dus eindigt een hele meting zonder OnlyOffice altijd
op `niet geslaagd` (exitcode 1): de andere poorten staan er wel. Nog niet in deze meting (0.8.1):
de container `analyse` (gezichten en beeld) en de beeldvraag.

## Terug naar 0.7.1

Alleen als 0.8.0 niet goed werkt. Liever niet: wat in fase 6 bijkwam, is daarna weg. Het zijn deze
commando's, in deze volgorde, met de volledige commit van 0.7.1 (`80d5bb8`, de laatste vóór
fase 6):

```bash
git -C /opt/platform/src fetch -q origin
git -C /opt/platform/src cat-file -e '80d5bb8ce9cd937534f06a5bc5e4f54ec8509ad1^{commit}' && echo "goed: 0.7.1 is er"
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/80d5bb8ce9cd937534f06a5bc5e4f54ec8509ad1/infra/single/install.sh -o /root/platform-install-0.7.1.sh && echo "goed: het script is er"
dc stop platform media
dc run --rm --no-deps platform python -m app.cli db-backup
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
3. **Een verse kopie** van de database zoals hij nu is, op `0010`, met het image van nu
   (`db-backup`, dezelfde kopie als die van het script). Hij zegt
   `De reservekopie staat in /data/backup/platform-0010-<tijd>.db (… MB).` (in de Console is dat
   `$DATA_ROOT/backup/…`). Zegt hij iets anders, stop dan: zonder deze kopie geen downgrade. Hij
   vervangt de vorige kopie van `0010`; die van `0009` blijft.
4. **Het schema terug** met het image van nu, want alleen dat kent 0010. `db-downgrade` vraagt
   eerst, als die er zijn, naar album-items die uit een regel van een slim album komen en niet
   gedeeld worden: in 0.7.1 deelt een album elk item, dus die items zouden ineens gedeeld zijn.
   Enter of "ja" haalt ze eerst uit het album (de standaard). Dan zegt het wat er weggaat:

   > Weg zijn daarna: de zoekindex, de plekken van foto's, de hashes voor duplicaten, de personen
   > die je met de hand benoemde en de regels van slimme albums. Bestanden, delingen en albums
   > blijven, en zoeken op naam werkt gewoon.

   en vraagt om "ja". Alles anders dan "ja" doet niets. Aan het eind:
   `De database staat nu op revisie 0009.` Zegt het iets anders, stop dan, en zet de kopie van
   stap 3 terug (`backup/platform-0010-<tijd>.db`, zie "Een kopie terugzetten" hierboven). Blijf
   dan op 0.8.0: het gewone script van de branch ("Weer vooruit" hieronder), niet dat van 0.7.1.
   Zo ben je niets kwijt.
5. **0.7.1 installeren** met het script van 0.7.1. Het bouwt het image van 0.7.1 en start het.
   De reservekopieën en `$DATA_ROOT/geo` blijven staan; 0.7.1 gebruikt ze niet.

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
