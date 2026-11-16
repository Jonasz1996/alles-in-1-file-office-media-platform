# Runbook: gezichten

Gezichten vanzelf herkennen (fase 6, X2): het platform zoekt gezichten in de eigen foto's van
wie het aanzet, en stelt voor wie het is ("Is dit Eva?"). Niets wordt vanzelf bevestigd. Het
rekenen gebeurt in de container `analyse`; hoe die werkt, hoe je hem aan- en uitzet en wat elke
melding betekent, staat in [analyse.md](analyse.md). Dit runbook gaat over de gezichten zelf: wat
er bewaard wordt, de toestemming, de versleuteling, wissen, reservekopieën en snapshots (met de
map met sleutels), de stand, `analyse-bench` en `analyse-kwaliteit`, en de wet.

**Tot 0.8.1 staat dit uit.** Zonder `ANALYSE=aan` draait de container `analyse` niet, en dan is
er in de web niets van gezichten te zien: geen schakelaar, geen voorstellen, op `/beheer` het blok
van 0.8.0. De app maakt dan geen rijen, geen jobs en geen bestanden voor gezichten. Wie
`ANALYSE=aan` vóór 0.8.1 zet, probeert een versie die nog niet af is: de kwaliteit is niet
gemeten en de drempels zijn voorlopig (hieronder).

Alles hieronder gebeurt in de Console van de container (de LXC), als root, tenzij er "op de
Proxmox-host" staat. `dc` is de functie uit de README (Handige commando's).

## Wat er bewaard wordt

Alleen voor eigenaren die gezichten aan hebben, en alleen voor hun eigen foto's (Keuze 69: een
foto die een ander in jouw map zette, alleen als die ander het ook aan heeft; via een publieke
link nooit).

| Wat | Waar | Inhoud |
|---|---|---|
| een gevonden gezicht | `face` in `platform.db` | de foto, het kader (deel van de foto), de score van YuNet, en de vector: 128 getallen van SFace, **versleuteld** (540 bytes) |
| een keuze | `face_person` | ja, nee of niet tonen, met de persoon; ook de afwijzingen ("nee, niet Anna") |
| de stand per foto | `face_scan` | wacht, bezig, klaar, overgeslagen of mislukt (met een code), en met welke versie |
| de stand per eigenaar | `face_owner` | wie aan de beurt is, de laatste groepsronde, of er gewist wordt |
| de sleutel per eigenaar | `SLEUTELS_ROOT` (standaard `/opt/platform/sleutels/gezichten`), in de app op `/keys` | één bestand `<id>.key` per eigenaar: 32 willekeurige bytes, `0600`, van uid 10001 |
| een bevestigde persoon op een foto | `photo_person` (personen van Z6) | net als een persoon die je met de hand benoemde; dit blijft na "uit" |

- **Geen uitsneden op schijf.** De uitsnede van een gezicht maakt de app bij elke vraag uit de
  preview van de foto, alleen voor de eigenaar, met `Cache-Control: no-store`.
- **Nooit naar buiten**: geen vector, geen score van een vergelijking, geen sleutel, in geen
  enkel antwoord van de API, log, foutmelding of gebeurtenis. De beheerder ziet alleen aantallen.
- **Ruimte**: ±0,8 kB per gezicht in de database (`/beheer` toont het totaal).

## De toestemming

- Per gebruiker, standaard **uit**. Aanzetten doe je onder Account → Foto's → "Gezichten
  herkennen in mijn foto's". Eerst komt een dialoog met wat er gebeurt (Keuze 66): dat het
  biometrische data is, ook van mensen op je foto's die zelf niets kozen; dat de getallen
  versleuteld op deze server staan; dat de beheerder er technisch bij kan; wat er met kopieën
  gebeurt; en dat "uit" alles wist.
- De beheerder kan het voor iedereen verbieden: `/beheer` → Zoeken en foto's → Gezichten →
  "Gezichtsherkenning toestaan" uit. Dat wist de gezichten van iedereen, en wie het aan had,
  staat daarna op uit. Weer toestaan zet niemand aan.
- Een gast kan het niet aanzetten.

## De versleuteling

- Elke vector is versleuteld met AES-256-GCM, met een eigen sleutel per eigenaar; de id van het
  gezicht zit in de versleuteling, dus een vector die naar een andere rij gekopieerd wordt, gaat
  niet open. Alleen de app gebruikt de sleutel. De container `analyse` krijgt de vectoren
  ontsleuteld in een verzegeld stuk geheugen (memfd) en ziet nooit een sleutel.
- De sleutel wordt gemaakt bij de eerste vector van een eigenaar.
- Een rij die niet te ontsleutelen is (geen sleutel, een andere sleutel, kapot), telt als gewist;
  die foto krijgt een nieuwe scan.

## Wissen

| Wat | Wat er weg gaat | Wat blijft |
|---|---|---|
| "uit" door de gebruiker | eerst de sleutel (weg, en de map gesynchroniseerd), dan alle rijen in `face`, `face_person`, `face_scan` en `face_owner` van die eigenaar | personen en `photo_person` (wat je zelf benoemde of bevestigde) |
| "verbieden" door de beheerder | hetzelfde, voor iedereen | idem |
| "Niet benoemde gezichten wissen" (op `/fotos/personen`) | de gezichten zonder ja | de bevestigde gezichten |
| "niet tonen" | de vector van dat gezicht | het kader, zodat het niet terugkomt |
| een persoon wissen | de gezichten met ja op die persoon worden "niet tonen", zonder vector | — |
| een foto definitief wissen | zijn gezichten | — |

- De database staat op `secure_delete`: gewiste rijen worden overschreven, ook in de WAL.
- Heeft iemand op dat moment de database open om te lezen, dan zegt `/beheer` "wissen wacht op
  een lezer"; de job probeert het opnieuw tot het lukt. Een herstart halverwege maakt het af.

## Reservekopieën en snapshots

**Wat het platform zelf maakt** (`app.cli db-backup`, en de reservekopie van `install.sh` vóór
een update) bevat **geen** gezichten: de kopie gooit `face`, `face_person`, `face_scan` en
`face_owner` leeg, zonder journaal, voordat hij af is. Na het terugzetten van zo'n kopie zijn er
geen gezichten; wie het aan had, krijgt een nieuwe scan (±2,2–2,5 uur per 55.000 foto's), en
bevestigde personen komen terug als ja via hun kader.

**Wat je zelf maakt** (de ZFS-snapshots van `/tank`, de offsite-kopie) bevat de rijen van
gezichten tot die kopie weg is, met versleutelde vectoren. Na "uit" zijn ze daar niet meer te
lezen, **zolang de map met sleutels niet in die kopie zit**. `SLEUTELS_ROOT` staat daarom buiten
`/tank`: een snapshot van alleen de data heeft de sleutels niet.

### De map met sleutels niet in snapshots of backups

Op de LXC staat `/opt/platform` op de rootdisk. Een backup van de **hele** container (vzdump) en
een snapshot van de container in Proxmox nemen de rootdisk mee, dus ook de sleutels. In zo'n
kopie van vóór "uit" blijven oude vectoren leesbaar tot je die kopie weggooit. Zo houd je de
sleutels uit de backups [niet getest in de sandbox: daar is geen Proxmox]:

1. **In de container**: de app stoppen en de map met sleutels opzij zetten. `dc stop` houdt de
   app ook na een herstart van de container gestopt (`restart: unless-stopped`):

   ```bash
   dc stop platform
   mv /opt/platform/sleutels /opt/platform/sleutels.oud
   ```

2. **Op de Proxmox-host**: een eigen mount point op `/opt/platform/sleutels`, met **Backup uit**
   (vzdump slaat het dan over). In de web-interface: de container → Resources → Add → Mount
   Point; een opslag naar keuze (1 GB is ruim: per gebruiker 32 bytes), Path
   `/opt/platform/sleutels`, en het vinkje **Backup** uit. Of met de hand, met de container uit
   (`<id>` is je container, `local-zfs` je opslag):

   ```bash
   pct shutdown <id>
   pct set <id> -mp1 local-zfs:1,mp=/opt/platform/sleutels,backup=0
   pct start <id>
   ```

3. **In de container**: de sleutels terugzetten op het mount point, het script laten nakijken
   (van 10001, `0700`, geen symlink) en de app starten, en dan de oude map weggooien:

   ```bash
   cp -a /opt/platform/sleutels.oud/gezichten /opt/platform/sleutels/
   ls -ln /opt/platform/sleutels/gezichten   # elk bestand 0600 en van 10001
   curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/claude/stappenplan-uitwerken-lu4uv3/infra/single/install.sh | PLATFORM_BRANCH=claude/stappenplan-uitwerken-lu4uv3 bash
   rm -rf /opt/platform/sleutels.oud
   ```

   Backups van de container van vóór deze stap hebben de oude map nog; die blijven leesbaar tot
   je ze weggooit.

4. **Snapshots van de container in Proxmox** nemen elk mount point mee, ook met Backup uit. Maak
   je die (bijvoorbeeld vóór een update), gooi ze dan weg zodra je ze niet meer nodig hebt: tot
   dan blijven de gezichten van vóór "uit" daarin leesbaar.

Wie de server beheert, kan bij een lopende app de sleutels lezen. Dat staat ook in de tekst bij
het aanzetten.

## De stand lezen

- **Een gebruiker** ziet op `/fotos/personen` de regel "Gezichten zoeken: 120 van 5.400 foto's",
  "Klaar: … gezichten in … foto's", of "Wacht op de analyse" (de container is er niet).
- **De beheerder** ziet op `/beheer` → Zoeken en foto's → Gezichten: de stand van de analyse en de
  modellen (met "sha256 klopt"), de grenzen, het aantal gebruikers met gezichten aan, de rij
  `analyse` (wachtend, in het laatste uur, mislukt per code, waar hij op wacht), het totaal, de
  snelheid, of het wissen wacht, en de kwaliteit. Geen namen, geen gezichten.
- **De container**: `dc exec -T platform python -m app.cli analyse-status` (zie
  [analyse.md](analyse.md), De stand lezen).
- **Mislukt**: "Mislukte opnieuw" op `/beheer` zet de mislukte foto's terug in de rij. Een foto
  die niet te lezen is (`unreadable`) of een antwoord dat niet klopt (`bad_output`), probeert de
  rij niet vanzelf opnieuw; een time-out wel, drie keer.
- **Eén foto**: in de viewer, infopaneel, rij Gezichten → "Opnieuw zoeken" zet die foto vooraan.

Het zoeken wijkt: lopen er twee of meer video-omzettingen, start OnlyOffice, of staat `/beheer`
op pauze, dan wacht de rij (`/beheer` zegt waarop).

## Hoe snel is het op deze CPU: `analyse-bench`

```bash
dc exec -T platform python -m app.cli analyse-bench            # 200 gemaakte foto's
dc exec -T platform python -m app.cli analyse-bench --fotos 50 # sneller, minder precies
```

Stuurt gemaakte foto's van 1024 × 768 (ruis, een verloop, een landschap; zonder gezicht) door de
echte container, zoals de scan dat doet. Uitvoer: ms per foto, foto's per minuut, en hoe lang
wat er nu wacht nog duurt. De uitkomst komt ook op `/beheer` (Snelheid). De tijd per extra
gezicht meet hij niet: gemaakte foto's hebben geen gezicht (in de sandbox: ±25 ms per gezicht
extra, [x2a.md](../fase-6/x2a.md)). Exitcode 0 met een uitkomst, 1 als er niets gemeten is (de
analyse staat uit, is niet bereikbaar of bezig; de reden staat op stderr). Draai hem als de rij
leeg is of op pauze staat: hij wacht anders op de scan.

## Hoe goed is het: `analyse-kwaliteit`

**De kwaliteit is niet gemeten.** In de sandbox zijn er geen echte gezichten (Vraag 8 heeft de
standaard nee: geen foto's van NASA in CI), dus de drempels zijn voorlopig: **0,50** voor groepen en
**0,45** voor een voorstel (strenger dan 0,363 van OpenCV), in `app/people/faces/drempels.py`.
`/beheer` zegt "niet gemeten" tot dit commando gedraaid heeft.

```bash
dc exec -T platform python -m app.cli analyse-kwaliteit
```

Meet op de LXC, tegen je eigen ja en nee (niets verlaat de LXC; alleen getallen):

- **precisie** van de voorstellen: de gezichten met een ja in vijf delen, elk deel om de beurt
  als onbekend; een voorstel voor de goede persoon telt als goed. Poort: ≥ 95%.
- **zuiverheid** en **recall** van de groepen "Wie is dit?" over de gezichten met een ja. Poorten:
  ≥ 90% en ≥ 80%.
- met een nee: hoe vaak het model de afgewezen persoon opnieuw zou voorstellen.

Er zijn minstens 20 gezichten met een ja nodig (bij één eigenaar); anders zegt hij "te weinig om
te meten". Exitcode 0: de poorten gehaald; 3: gemeten, maar niet gehaald; 1: niets gemeten. De
uitkomst komt op `/beheer` (Kwaliteit). Haalt hij de poorten niet, dan horen de drempels anders;
dat is een wijziging in de code (met een hogere `FACES_VERSION`, en dan zoekt het platform alles
opnieuw), niet iets voor de LXC.

## Een nieuwe versie

Veranderen de modellen of de drempels, dan gaat `FACES_VERSION` omhoog. Na de update zoekt het
platform alle foto's opnieuw: de open gezichten worden vervangen, een ja komt terug via het kader,
en "niet tonen" blijft niet tonen. Afwijzingen beginnen opnieuw.

## Terug naar 0.8.0

Zoals [analyse.md](analyse.md), "Terug naar 0.8.0". `db-downgrade 0010` haalt de tabellen van
gezichten (0011) en beeld (0012) weg; de sleutels en de modellen blijven op hun plek. Weer vooruit
begint met lege tabellen: wie gezichten aan had, krijgt bij de volgende ronde (elk uur) een
nieuwe scan.

## De wet (AVG)

Gezichten zijn biometrische gegevens, ook van mensen op je foto's die zelf niets kozen. Voor
gebruik in je eigen gezin geldt waarschijnlijk de uitzondering voor huishoudelijk gebruik in de
AVG. Gebruik je het platform voor anderen (een vereniging, klanten), dan gaat het om bijzondere
persoonsgegevens (artikel 9): dan heb je uitdrukkelijke toestemming nodig van wie op de foto's
staat. Dit is niet door een jurist nagekeken.
