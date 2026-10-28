# Fase 4 — Integratie: de installatietest

Bouwontwerp §4.6 punt 3 en §5 stap 4. De installatietest
(`infra/single/tests/test_install_update.sh`) neemt een installatie uit fase 3 (v0.5.0,
`3ccee0a`) en werkt die bij naar deze versie. Hij gebruikt de echte install.sh, de echte Docker,
de echte DS en de echte editor in een browser. Daarna gaat hij terug naar fase 3 zoals het
runbook het zegt, en weer vooruit. Hij draait alleen in de sandbox, niet in CI: hij bouwt het
platform vier keer en start DS een paar keer.

## Het commando

Uit de hoofdmap van de repo:

```sh
INSTALL_TEST_UV=pypi INSTALL_TEST_DIR=<lege map> INSTALL_TEST_LOGS=<map> \
  bash infra/single/tests/test_install_update.sh
```

- `INSTALL_TEST_UV=pypi`: ghcr.io weigert hier uv; de test neemt dan uv 0.11 uit het wiel van
  PyPI, onder dezelfde naam.
- Nodig: docker, git, python3, ss, curl, uv en de omgeving van `api/` met Playwright
  (`uv run --with playwright`, zoals de e2e) plus Chromium (in de sandbox die van
  `/opt/pw-browsers`). Er komt niets bij: geen nieuw pakket voor Python of npm.
- De test begint niet met minder dan 12 GB vrij voor Docker, of als er al een project
  `platform`, een image `platform:local` of iets op zijn poorten is.
- Aan het eind (ook bij een fout of Ctrl+C) ruimt hij op wat hij maakte: containers, volumes,
  netwerken, de images die er vooraf niet waren, de browser en de werkmap. De logs blijven in
  `INSTALL_TEST_LOGS`.

## De uitslag

| Run | Commit | Uitslag | Duur |
|---|---|---|---|
| 1 | `8782669` (de app; de test van `c036c50` stond nog niet in een commit) | 148 goed, 1 mislukt | 16 min 22 s |
| 2 | `c036c50` | **149 goed, 0 mislukt** | 13 min 53 s |
| 3 | `c036c50` | **149 goed, 0 mislukt** | 13 min 43 s |

Alle drie in de sandbox (zie "De sandbox"), met de build-cache van eerdere runs. Tussen
`8782669` en `c036c50` veranderden alleen de test, de twee hulpjes in `e2e/` en het runbook; de
app, install.sh en de compose-bestanden zijn dezelfde. Run 3 gaf elke regel een tijd mee (zie
"De tijd per stap").

Run 1 vond een fout in het hulpje, niet in het platform: `install_api.py login-zonder-code`
las het veld `code` bovenaan het antwoord, maar de API zet het in `detail` (`{"detail":
{"message": …, "code": "totp_required"}}`, `app/errors.py`). De fix in `install_api.py` kwam
nog tijdens run 1 (de test roept het hulpje elke keer opnieuw aan); daarom slaagde dezelfde
controle in stap R van run 1 wel. In run 2 en 3 slaagden alle controles.

Dit document en de wijziging in `o6.md` kwamen in de commit na `c036c50`. Die commit verandert
alleen documentatie.

## De tijd per stap

Uit run 3 (de tijd tussen de kop van een stap en de kop van de volgende):

| Stap | Tijd | Waar de tijd heen gaat |
|---|---|---|
| Voorbereiden en basisimages | 26 s | |
| A | 38 s | de build van fase 3 (uit de cache) |
| B | 2 min 13 s | DS gezond na 6 s, maar `ok` pas na 120 s (de font-herstart) |
| B2 | 1 s | |
| S | 2 min 48 s | de editor open en getypt na 12 s, de stop 31,8 s, daarna ±2 min tot DS weer `ok` is |
| K | 11 s | |
| C | 5 s | |
| H | 21 s | |
| G | 38 s | |
| G2 | 6 s | |
| G3 | 36 s | |
| D | 9 s | |
| E | 19 s | |
| F | 15 s | |
| R1 | 1 min 53 s | wachten op `ok`: F startte een nieuwe DS |
| R2 | 39 s | install.sh: tussentijds opslaan, DS stoppen, de app opnieuw |
| R3 | 3 s | |
| R | 6 s | |
| V | 2 min 10 s | weer wachten op `ok` na de start van DS |
| Opruimen | 6 s | |
| **Samen** | **13 min 43 s** | |

De tijden komen uit de uitvoer van run 3, met een tijd voor elke regel:
`… bash infra/single/tests/test_install_update.sh 2>&1 | while IFS= read -r l; do printf
'%(%H:%M:%S)T %s\n' -1 "$l"; done`. De test zelf schrijft geen tijden.

## Wat elke stap doet en wat hij bewees

Aantallen uit run 2 en 3 (gelijk).

### A — fase 3 installeren (10 controles)

install.sh van nu met `PLATFORM_SRC_REF=3ccee0a`. De app draait op zijn poort, `.env` heeft de
vijf regels van fase 3, er is geen DS, en de eindtekst zegt waarom office uit is. Het schema
staat op 0006. Via de API: een beheerder met 2FA aan (`install_api.py setup`; de TOTP-code wordt
in Python uitgerekend, RFC 6238 zoals `app/totp.py`). Inloggen zonder code geeft 401
`totp_required`. In de map Werk: `notitie.txt` met één versie (twee uploads, de tweede met
`conflict=replace`) en `verslag.docx` zonder versie.

### B — bijwerken naar deze versie (36 controles)

install.sh met `PLATFORM_SRC_REF=<deze commit>`; Docker Hub is in de runner geblokkeerd, dus komt
DS van de mirror. Bewezen:

- de beheerder logt in met wachtwoord en 2FA-code; setup is niet meer nodig;
- `.env`: de vijf regels van fase 3 blijven, `OFFICE=aan` en twee verschillende geheimen van 64
  hex-tekens erbij, rechten 600; de app en DS lezen dezelfde geheimen uit `.env`;
- DS draait op de tag van Docker Hub, die naar de digest van install.sh wijst (mirror en dan
  `docker tag`, M19), met de afscherming van het ontwerp; DS wordt gezond, de app praat met DS
  (`version` 10.0.0.169);
- **het interne netwerk**: `docker exec <DS> curl https://example.com` komt nergens. Omdat geen
  enkele container in de sandbox example.com kan opzoeken, is er een tegenproef: hetzelfde image
  op het gewone bridge-netwerk komt wel bij pypi.org (151.101.128.223). DS zelf komt er ook met
  dat adres erbij (`curl --resolve`, dus zonder DNS) niet. DS heeft geen poort op de host. De app
  bereikt DS wel (`version` hierboven);
- de migraties liepen (schema 0007, na 0006), en `notitie.txt` en zijn versie zijn er nog;
- **`GET /api/v1/office/status`**, elke seconde gevraagd door de beheerder met 2FA (vanaf de
  eerste start van DS): eerst `starting`, en zolang het niet `ok` is `editing: false`. Pas na het
  einde van de font-herstart van DS `ok` met `editing: true`. Het einde van de font-herstart
  komt uit het log van supervisord in DS: de eerste `success: converter` na `stopped:
  converter` (runbook "DS bijwerken").

  | Run | Toestanden | Font-herstart voorbij | Status `ok` |
  |---|---|---|---|
  | 1 | `starting` 118×, `ok` 1× | 115,1 s na de start van DS | 120,2 s |
  | 2 | `starting` 113×, `ok` 1× | 117,3 s | 119,7 s |
  | 3 | `starting` 118×, `ok` 1× | 119,4 s | 120,3 s |

  Geen `down` en geen fout tussendoor;
- een nieuw document via office (`POST /api/v1/office/new`): `Nieuw van office.docx`.

### B2 — een losse `docker compose up -d` (5 controles)

Zonder `OFFICE_IMAGE`, zoals iemand na een herstart met de hand doet: niets opgehaald, niets
opnieuw gemaakt.

### S — `docker compose stop` met de echte editor open (14 controles)

`e2e/install_editor.py` logt in Chromium in met wachtwoord en 2FA-code, opent `verslag.docx` in
de viewer en drukt op Bewerken. Als de editor klaar is, typt het "Voor de stop gestopt<willekeurig>"
zoals een gebruiker (`type_in_editor` uit het harnas) en wacht 3 s. Met de editor nog open stopt
de test alles met `docker compose stop` (precies de bestanden van install.sh). Bewezen:

- de stop duurde 31,6 s in run 2 (31,7 s in run 1, 31,8 s in run 3), binnen 90 s;
- DS stopte netjes (exitcode 0) en vóór de app (`depends_on`); de app werd niet gekild;
- daarna gaat de browser dicht (zonder fout), dan `docker compose start`: dezelfde containers;
- **het getypte woord staat in het bestand, als nieuwe versie**: één versie meer dan vóór de
  sessie, en die oudere versie is precies het bestand van vóór de sessie;
- DS komt weer op en de app zegt weer `ok`.

Compose stopt eerst DS en pas daarna de app (dat bewijst de test met de tijden waarop de
containers stopten). Volgens metingen.md (deel 2, "Stoppen met open editors") vangt DS de
SIGTERM, slaat het elk open document op binnen 2 s en stuurt het de callback naar de app, die dan
nog draait; dat verklaart ook de ±30 s van de stop. De test kijkt naar het bestand na de start,
niet naar het moment van opslaan. Wel stond de tekst er in run 3 al 2 s na `docker compose
start` in; zo snel antwoordt een DS die net start nog niet (metingen.md: ±4 s tot
`/healthcheck`).

### K — een fout geheim aan één kant (14 controles)

Zoals iemand die `OFFICE_JWT_SECRET` in `.env` met de hand verandert en alleen de app opnieuw
start. De test herstart wat install.sh herstart: `compose up -d --remove-orphans platform`. DS
blijft staan met het oude geheim. Bewezen: de app en DS hebben een ander geheim;
`/api/v1/office/status` zegt `misconfigured` met `editing: false`; `/api/v1/admin/office` zegt
`misconfigured`; `/beheer` meldt `office_misconfigured`. Daarna het oude `.env` terug (precies
dat van na B, rechten 600) en weer `up -d --remove-orphans platform`: hetzelfde geheim, weer
`ok`, geen melding meer.

### C, H, G, G2, G3, D, E, F — opnieuw, geblokkeerd, pin, uit en aan (31 controles)

Zoals voor de integratie (o6.md, "De installatietest in de sandbox"): C (nog een keer: `.env` en
DS onveranderd, 3), H (geblokkeerde pull terwijl DS draait: DS draait door, met de reden, 4), G,
G2 en G3 (een pin uit `RepoDigests` blijft staan, ook na een tweede run; zonder pin weer de
standaard, 3 + 2 + 1), D (`OFFICE=uit`: DS weg, volumes blijven, 6), E (weer aan maar nergens te
halen: het platform werkt zonder editor, 5) en F (weer aan: DS terug op dezelfde volumes,
afgeschermd en gezond, 7).

### R — terug naar v0.5.0 zoals het runbook (3 + 2 + 3 + 16 controles)

Precies "De app terugrollen" in `docs/runbooks/office.md`:

1. **R1**: office werkt (`ok`), er zijn geen open documenten en "Niet opgeslagen" is leeg
   (`/api/v1/admin/office`, wat `/beheer` → Office laat zien).
2. **R2**: `install.sh` met de versie van nu en `OFFICE=uit`. Het script laat alles tussentijds
   opslaan en stopt DS, terwijl de app nog draait. DS is weg, de app draait.
3. **R3**: `dc stop platform`, dan `dc run --rm --no-deps platform python -m app.cli
   db-downgrade 0006` met "ja". Het commando zegt "De database staat nu op revisie 0006."
4. **R**: `install.sh` met `PLATFORM_SRC_REF=3ccee0a OFFICE=uit`. Bewezen: de app van fase 3
   draait op schema 0006, zonder DS (de app kent DS niet); de volumes van DS en de geheimen in
   `.env` blijven; 2FA blijft verplicht (zonder code geen login) en **de beheerder logt in** met
   wachtwoord en code; `notitie.txt` met zijn versie (de eerste upload); `verslag.docx` met **de
   tekst uit de editor (S)** en de versie van vóór office (het bestand van vóór de sessie); **het
   nieuwe document van office** is er nog, met dezelfde inhoud.

### V — weer vooruit (15 controles)

`install.sh` met deze versie en `OFFICE=aan`. Bewezen: de migraties liepen weer (schema 0007);
`.env` is precies dat van na B (dezelfde geheimen); DS is terug op de standaard, op dezelfde
volumes, afgeschermd en gezond; de app praat met DS; de beheerder logt in; de status is `ok`; de
bestanden en hun versies zijn er, met de tekst uit de editor; en `verslag.docx` kan weer in de
editor (`can_edit`).

## Het runbook was fout

"De app terugrollen" zei eerst: alles laten opslaan (`office-checkpoint`), wachten tot "Niet
opgeslagen" leeg is, `dc stop platform` en `db-downgrade`, en dan de oude versie met
`OFFICE=uit`. Maar `dc stop platform` stopt alleen de app, niet DS (gemeten met compose v5.6.0:
`stop` van één dienst laat de dienst die ervan afhangt draaien). DS stopte dan pas bij de laatste
stap (`--remove-orphans`), toen de app al weg was: zijn laatste opslag vond geen app meer, en wat
er sinds de checkpoint nog open was, bleef in het volume van DS. Na het terugzetten van het
schema kent het platform dat niet meer.

Nu (commit `c036c50`): eerst kijken op `/beheer` (terwijl office nog aan staat, want die lijsten
komen van DS), dan office uit met de versie van nu (DS slaat op en stopt terwijl de app draait),
pas dan de app stoppen en het schema terugzetten, en dan de oude versie. Plus hoe je weer vooruit
gaat (`OFFICE=aan`). Zo lopen R en V het na.

## De sandbox

Docker 29.9.1 met de opslag van containerd (`overlayfs`, snapshotter
`io.containerd.snapshotter.v1`), `MemoryLimit` en `CPUCfsQuota` beide `true`, 4 cpu's, 15,7 GB
geheugen, 15 GB vrij voor Docker. Verschillen met Jonas' LXC, en hoe de test ermee omgaat
(o6.md, afwijking 15):

- dockerd geeft maar 20000 open bestanden, niet de 65536 van `compose.yaml`: de app krijgt in de
  test 20000 (een extra compose-bestand, alleen in de test);
- geen Debian-spiegel bereikbaar: lege pakketten voor ffmpeg en unattended-upgrades, en de runner
  is buildpack-deps:bookworm-scm; video's krijgen in de test geen preview;
- ghcr.io weigert uv (zie het commando) en Docker Hub geeft 429: basisimages van mirror.gcr.io;
- geen container kan example.com opzoeken: daarom de tegenproef met pypi.org in stap B;
- `/tank` is niet gekoppeld: de data staat in de werkmap;
- stap S doet één gebruiker in de browser, niet twee;
- **een herstart van dezelfde DS-container** (stap S) kan in zeldzame gevallen blijven hangen op
  een oud `/var/run/supervisord.pid` (metingen.md, deel 2, punt 7; daar ±1 op 6). In de drie runs
  gebeurde het niet. Gebeurt het, dan faalt "S: DS komt weer op" en laat de test het pid-bestand
  zien. Het is in deze stap niet opgelost.

## Alleen op Jonas' LXC na te gaan

Uit het runbook ("Controle van de LXC") en §4.7. De sandbox kan dit niet bewijzen, omdat het om
de machine zelf gaat:

1. **`docker info --format '{{.Driver}} {{.MemoryLimit}} {{.CPUCfsQuota}}'`**: de driver
   (`overlay2` of `overlayfs`; bij `overlay2` de klassieke opslag, die `test_image_store.sh` hier
   nabootst) en of de kernel grenzen kan zetten (anders laat install.sh
   `compose.office-limits.yaml` weg). Een LXC zonder cgroup-delegatie kan `false` geven.
2. **Het image**: `docker image inspect onlyoffice/documentserver:10.0.0.1` wijst naar de digest
   van het script. In de sandbox kwam het van de mirror; bij Jonas waarschijnlijk van Docker Hub.
3. **Het interne netwerk** (`docker network inspect platform_office` zegt `Internal: true`) en
   geen poort voor DS. In de sandbox bewezen (stap B); op de LXC nog één keer kijken.
4. **`ss -Hltn`**: wat er op de LXC luistert. DS kan de LXC bereiken via het gateway-adres van zijn
   netwerk (M7), dus daar mag niets anders open staan dan de poorten van het platform en SSH.
   Dat hangt af van het template van de LXC.
5. **De eerste start van DS**: `dc ps office` is `healthy` binnen een paar minuten en `/beheer` →
   Office zegt dat office werkt. Met de echte pull, schijf en cpu van de LXC (in de sandbox ±2
   minuten tot `ok`).
6. **Eén sessie met twee gebruikers**: twee accounts openen hetzelfde Word-bestand, typen allebei,
   sluiten allebei; daarna is er één nieuwe versie met beide teksten. Met de echte DS is dat
   bewezen in `e2e/test_office_o2_real.py::test_twee_gebruikers_een_docx` (het harnas, niet de
   installatie); in de installatietest typt maar één gebruiker.

## De build-cache

Elke run laat ±60 entries achter in de build-cache van BuildKit (±0,85 GB). Een volgende run
gebruikt ze niet (geen van die entries werd in een latere run nog gebruikt), waarschijnlijk omdat de test elke run zijn afgeleide basisimages (met de CA en
de lege pakketten) opnieuw maakt, met een nieuw ID. De test laat de cache bewust staan, want hij
deelt die met andere builds op dezelfde Docker (zie de kop van het script).

In deze stap is weggehaald wat de runs maakten, en niets anders:

1. Vóór de eerste run de lijst van ID's bewaard: 181 entries.
2. Na run 3: 361 entries, waarvan 180 nieuw. Alle 180 gemaakt binnen de tijd van run 1, 2 of 3
   (er draaide toen geen andere build), geen ervan gedeeld.
3. Eerst één entry weggehaald met `docker builder prune -f --all --filter id=<id>`, en gekeken
   dat alleen die weg was. Daarna de rest, één ID per keer (de filter `id` neemt één waarde), in
   acht rondes: een entry waar een andere op bouwt, gaat pas als die andere weg is.
4. Daarna precies de 181 entries van vooraf: 3,77 GB in plaats van 6,30 GB.

Na een volgende run kan het op dezelfde manier: de ID's van vóór de run bewaren
(`docker buildx du --verbose`), en daarna alleen weghalen wat nieuw is en in de tijd van de run
gemaakt is.
