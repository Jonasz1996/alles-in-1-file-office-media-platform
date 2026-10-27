# Runbook: office

Office is OnlyOffice Docs (de "DS") in de container `platform-office-1`, naast het platform. Wat
het is en hoe je het aan- en uitzet, staat in `infra/single/README.md` (Office). Dit runbook gaat
over bijwerken, terugrollen, niet-opgeslagen werk, de belasting en de controle van de LXC.

Alles hieronder gebeurt in de Console van de container (de LXC), als root. `dc` is de functie
uit de README (Handige commando's): `docker compose` met precies de bestanden van het
installatiescript. Het installatiescript is steeds de nieuwste versie:

```bash
curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/main/infra/single/install.sh | bash
```

Hieronder heet dat kortweg "het script"; een instelling zet je ervoor, zoals
`curl … | OFFICE=uit bash`.

## Wat het script bij elke run met office doet

1. Het leest `OFFICE` uit de omgeving vóór `/opt/platform/.env` (alleen `aan` of `uit`).
2. Ontbrekende sleutels komen in `.env` (`OFFICE=aan`, `OFFICE_JWT_SECRET`,
   `OFFICE_LINK_SECRET`); een bestaande waarde blijft altijd staan.
3. Het controleert de opslagdriver van Docker (`overlay2` of `overlayfs`) en of er 8 GB vrij is.
4. Het image: een pin in `.env` (`OFFICE_IMAGE`) gaat voor. Anders de digest die het script
   kent, eerst van Docker Hub, dan van `mirror.gcr.io`; daarna zet het de naam van Docker Hub er
   lokaal op. Lukt niets: een draaiende DS blijft op zijn versie, en anders start het platform
   zonder editor.
5. Het laat de open documenten tussentijds opslaan (`app.cli office-checkpoint`).
6. Het vervangt het platform en wacht tot `/health` antwoordt.
7. Pas dan werkt het DS bij, alleen als het image of de instellingen veranderden.
8. Oude images van DS die geen container meer gebruikt, gaan weg, behalve het vorige (om terug
   te rollen) en de pin.

## DS bijwerken

Een nieuwe DS komt met een nieuwe versie van het platform: het script kent de digest. Draai het
script; meer hoeft niet. Het vorige image blijft staan.

Voor wie het platform ontwikkelt: een nieuwe DS betekent een nieuwe digest op vier plaatsen,
in één commit:

- `OFFICE_TAG` en `OFFICE_DIGEST` in `infra/single/install.sh`;
- het standaardimage in `infra/single/compose.office.yaml` (en het voorbeeld in `.env.example`);
- `OFFICE_TAG` en de digest in `e2e/office_ds.sh` (de job `office` in CI).

Die commit moet door de job `office` (de echte DS, marker `office_real`). Kijk bij een nieuwe
versie ook of `infra/single/office/local-production-linux.json` nog klopt met `default.json` van
het nieuwe image: dat bestand vervangt hele lijsten (zoals `inputLimits`), dus een soort die
DS erbij krijgt, valt anders buiten de grenzen. `e2e/test_office_o6_real.py` controleert de
grenzen.

## DS terugrollen

Zet het image dat je wilt houden als **pin** in `/opt/platform/.env`, op digest:

```bash
docker images --digests onlyoffice/documentserver
echo 'OFFICE_IMAGE=onlyoffice/documentserver:<tag>@sha256:<digest>' >> /opt/platform/.env
```

en draai het script. Het gebruikt de pin zoals hij is, haalt dan geen ander image en gooit de pin
nooit weg. Het vorige image staat er na een update nog, dus terug kan zonder internet. Terug naar
de gewone versie: haal de regel `OFFICE_IMAGE=` weer uit `.env` en draai het script.

Let op: `/beheer` meldt een DS ouder dan 10.0 of een andere versie dan de vastgepinde (melding
`office_version`). Een oudere DS kan andere grenzen hebben; houd een pin daarom kort.

## De app terugrollen

**Liefst niet de code, maar office uit:**

```bash
curl -fsSL …/install.sh | OFFICE=uit bash
```

De app blijft dan de nieuwe versie en werkt zoals in fase 3; DS is weg, zijn volumes blijven.
Weer aan met `OFFICE=aan`.

**Moet de code echt terug** naar een versie zonder office (fase 3, bijvoorbeeld v0.5.0), dan moet
eerst de database terug naar revisie 0006, want een oudere app start niet op een nieuwer schema:

1. Laat alles opslaan:
   ```bash
   dc exec -T platform python -m app.cli office-checkpoint --wait 30
   ```
2. Wacht tot `/beheer` → Office → **Niet opgeslagen** leeg is (zie hieronder).
3. Stop de app en zet het schema terug (het commando vraagt om "ja"):
   ```bash
   dc stop platform
   dc run --rm --no-deps platform python -m app.cli db-downgrade 0006
   ```
4. Installeer de oude versie, met office uit:
   ```bash
   curl -fsSL …/install.sh | PLATFORM_SRC_REF=<oude commit of tag> OFFICE=uit bash
   ```
   Gebruik hiervoor het script van nu (via curl), niet het oude: alleen het nieuwe kent
   `PLATFORM_SRC_REF`, en het ziet zelf dat de oude versie geen office heeft.

Daarna zijn de sessies van office en de staat van de PDF's weg (die stonden in de nieuwe
kolommen en tabellen). De PDF's in `/tank/platform/office/` mogen weg. De volumes van DS blijven
staan tot je ze zelf weghaalt (hieronder).

## Niet-opgeslagen werk

Kan DS een document niet aan het platform teruggeven (de app was even weg, bijvoorbeeld bij een
update of een herstart van de LXC), dan bewaart DS het als "forgotten file" in het volume
`platform_office-lib`. Het platform haalt zulke documenten zelf op: bij zijn start, zodra DS
weer gezond is, en elke 10 minuten. Is het bestand intussen niet veranderd, dan komt het werk in
het bestand; anders als herstelkopie bij de eigenaar.

Wat ook dan niet lukt (quotum vol, document te groot, eigenaar weg), staat op `/beheer` → Office
onder **Niet opgeslagen**, met de naam en de eigenaar. **Terugzetten** maakt de herstelkopie
alsnog (als er nu ruimte is); **Verwijderen** gooit het werk weg. Heeft de eigenaar het
bestand intussen definitief verwijderd (prullenbak geleegd), dan staat er "het bestand bestaat
niet meer" en is er geen Terugzetten: de eigenaar wilde het weg, en het herstel ruimt het in zijn
volgende ronde zelf op. Een document dat het platform niet kent (een andere database), staat er
als "onbekend". Een document dat wel bij het platform
hoort, blijft daar staan tot het opgeslagen is of tot je het verwijdert, ook na 30 dagen: het
platform ruimt een oude sessie pas op als DS het werk niet meer heeft.

Haal de volumes van DS pas weg als die lijst leeg is:

```bash
docker volume rm platform_office-lib platform_office-data platform_office-logs platform_office-fonts
```

(alleen met office uit; daarna is de volgende start van DS weer een eerste start, met fonts).

## De belasting meten

Op de LXC, terwijl mensen werken:

```bash
docker stats --no-stream platform-office-1 platform-platform-1
uptime
free -m
```

en op `/beheer` → Office: het aantal verbindingen, mensen en documenten, en de piek sinds de
start van de app. Elke open editor-tab is één verbinding.

- DS mag hooguit 2 GB geheugen en 2 cores (als de kernel die grenzen kan zetten;
  `docker info --format '{{.MemoryLimit}} {{.CPUCfsQuota}}'` geeft dan `true true`). Blijft DS
  vlak onder 2 GB, of staat hij lang op 200 % cpu, dan is het te veel.
- De grens `max_editors` (standaard 20, op `/beheer` 1 tot 500) geldt per document: zitten er al
  zoveel mensen in één document, dan kan de volgende het niet openen (`office_too_many`); het
  PDF-voorbeeld werkt dan gewoon. Zet hem lager als de meting dat zegt.
- Na de eerste start van een nieuwe DS is hij ±5 minuten druk met fonts. Meet daarna.
- Werd DS gestopt door een geheugentekort: `docker inspect --format '{{.State.OOMKilled}}'
  platform-office-1` geeft dan `true`.

## Controle van de LXC

Eén keer na de eerste installatie met office, en na elke wijziging aan de LXC:

1. **Docker:**
   ```bash
   docker info --format '{{.Driver}} {{.MemoryLimit}} {{.CPUCfsQuota}}'
   ```
   De driver is `overlay2` of `overlayfs`. Staan de grenzen op `false`, dan draait DS zonder
   grens op geheugen en cpu (het script laat `compose.office-limits.yaml` dan weg).
2. **Het image** staat onder de naam van Docker Hub, op digest (zo vindt elke compose het):
   ```bash
   docker image inspect onlyoffice/documentserver@sha256:ca17889f10b11141cc6441836aebdd2835cf3e1b49681b2fd6e155c94b7850b3 --format '{{.RepoTags}}'
   ```
3. **Het interne netwerk:** `docker network inspect platform_office --format '{{.Internal}}'`
   geeft `true`, en `docker port platform-office-1` geeft niets.
4. **Wat er op de LXC luistert.** DS kan de LXC zelf bereiken via het gateway-adres van zijn
   netwerk; daarom mag daar niets anders luisteren dan de poorten van het platform en SSH:
   ```bash
   ss -Hltn | awk '{print $4}' | sort -u
   ```
   Verwacht: `PORT` en `HTTPS_PORT` uit `.env` (standaard 80 en 443) en, als je SSH gebruikt,
   22. Een adres op `127.0.0.1` of `[::1]` (bijvoorbeeld een mailserver of DNS-cache van het
   template) is alleen van de LXC zelf en mag. Iets anders op `0.0.0.0`, `*` of `[::]`: zet het
   uit, of zorg dat het een eigen login vraagt. SSH alleen met een sleutel.
5. **DS start:** `dc ps office` toont de container als `healthy` binnen een paar minuten, en
   `/beheer` → Office zegt dat office werkt.
6. **Twee mensen tegelijk:** open met twee accounts hetzelfde Word-bestand, typ allebei iets,
   sluit allebei. In de versies van het bestand staat daarna een nieuwe versie met beide teksten.
