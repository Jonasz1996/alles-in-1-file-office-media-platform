# Runbook: replicatie per bibliotheek

Jonas (24-11-2026): "Replicatie per map/bibliotheek instelbaar maken (bv. Films x2, Series x3),
naast de huidige cluster-brede 3 kopieën". Dit vervangt de vaste regel van fase 11 (films en
series op 2, `blobs-media/`, [docs/fase-11/clusterforge.md](../fase-11/clusterforge.md) §7).

Dit runbook gaat alleen over het cluster (SeaweedFS). Op de single heeft het geen zin: daar is er
één schijf, en blijft alles in `blobs/`.

## Wat het doet

Op /beheer → Media → Bibliotheken kiest de beheerder per bibliotheek **Kopieën in het cluster**:
"Standaard (N)", 1, 2 of 3. Standaard is wat het cluster zelf geeft
(`PLATFORM_BLOB_DEFAULT_COPIES`, standaard 3). De keuze staat in de database
(`media_library.copies`, migratie 0019) en is ook te zetten via de API (`PATCH
/api/v1/admin/media/libraries/{id}` met `{"copies": 2}`, of `null` voor de standaard).

De app bewaart elk bestand op zijn inhoud (`blobs/ab/cd/<sha256>`), niet op zijn plek in de boom.
SeaweedFS kan per map een andere replicatie geven. Daarom zet de app inhoud met een ander aantal
dan de standaard in een eigen map naast `blobs/`:

| Map | Kopieën | Regel in SeaweedFS |
|---|---|---|
| `blobs/` | de standaard (master en filer, nu `002` = 3) | geen (de standaard) |
| `blobs-r1/` | 1 | collectie `r1`, replicatie `000` |
| `blobs-r2/` | 2 | collectie `r2`, replicatie `001` |
| `blobs-r3/` | 3 | collectie `r3`, replicatie `002` |

Het doel van een inhoud is **het hoogste aantal over al haar verwijzingen** (bestanden, oudere
versies, ook wat in de prullenbak ligt):

- een bestand in een bibliotheek telt met de kopieën van die bibliotheek ("Standaard" telt als de
  standaard; ligt een bibliotheek in een andere, dan de hoogste);
- een bestand buiten elke bibliotheek telt als de standaard.

Dus: een film in Films (x2) staat op 2 kopieën. Staat dezelfde film ook in iemands eigen map, dan
blijft hij op 3. Een aflevering in Films (x2) en in Series (x3): 3. Elke soort bibliotheek telt
(films, series, muziek, video's): de keuze is per bibliotheek.

`api/app/blobsplit.py` verhuist de inhoud: elk uur, bij de start, en een minuut na een wijziging op
/beheer. Een verhuizing is een nieuwe kopie in de juiste map (een nieuwe write, zodat SeaweedFS de
regel van die map toepast; hernoemen zou de oude replicatie houden), daarna onder het slot van de
opslag nog eens nakijken, de kopie op haar plek zetten en de oude in `<map>/.weg/`. Daar blijft ze
een dag (een lopende stream leest verder) en dan gaat ze weg. Nieuwe uploads en downloads komen
altijd eerst in `blobs/` en verhuizen bij de volgende ronde. Een eerste ronde na het zetten van
Films op 2 kopieert dus alle films één keer: dat duurt even en vraagt tijdelijk ruimte voor een
extra kopie per film.

Wat nog in `blobs-media/` van fase 11 staat (alleen als `PLATFORM_BLOB_SPLIT` daar al aan stond),
blijft leesbaar en verhuist naar zijn juiste plek. Een bibliotheek zonder keuze staat na de update
op "Standaard": wil je de films op 2 kopieën houden zonder dat ze eerst terug naar `blobs/` gaan,
start deze versie dan eerst met `PLATFORM_BLOB_SPLIT=false`, zet Films en Series op /beheer op 2,
en zet het daarna weer aan (stap 4 hieronder). Daarna mag de regel van fase 11 weg:
`printf 'fs.configure -locationPrefix=/blobs-media/ -delete -apply\n' | weed shell -master=127.0.0.1:9333`
(pas als `blobs-media/` buiten `.weg/` leeg is).

Zonder `PLATFORM_BLOB_SPLIT=true` verhuist er niets. De keuze op /beheer wordt wel bewaard, en
/beheer zegt dan dat de instelling pas in het cluster werkt. Lezen kan altijd: de app kijkt in
`blobs/`, dan in `blobs-r1/` tot `blobs-r3/` die er zijn, dan in `blobs-media/`.

## Het cluster instellen

Wat er in het cluster staat (template `media-platform` 1.0.0, gezien 23-11): de master heeft
`-defaultReplication=002`, de filer `-defaultReplicaPlacement=002`, en de mount
(`cf-platform-mount.service`, op elke node) geeft zelf `-replication=002` mee. Die laatste wint van
elke regel per map, dus die moet weg. De standaard `002` van master en filer blijft. `weed shell`
hieronder draait op een node: `weed shell -master=127.0.0.1:9333`.

**1. De template (repo ClusterForge, `internal/templates/builtin/media-platform/1.0.0`):**

- `files/cf-platform-mount.service.tmpl`: ` -replication={{ .params.replication }}` weglaten uit
  `ExecStart`, voor alle nodes.
- `files/platform-cluster.yaml.tmpl`, bij `environment` van het platform:
  `PLATFORM_BLOB_SPLIT: ${PLATFORM_BLOB_SPLIT:-false}` en
  `PLATFORM_BLOB_DEFAULT_COPIES: ${PLATFORM_BLOB_DEFAULT_COPIES:-3}`; in `files/platform.env.tmpl`
  de regels `PLATFORM_BLOB_SPLIT=true` en `PLATFORM_BLOB_DEFAULT_COPIES=3` (of parameters,
  standaard uit en 3).
- `files/platform-ctl.sh.tmpl`, functie `replicatie`: die zet nu ALLE volumes op de replicatie van
  ClusterForge (`volume.configure.replication … -collectionPattern *`), en `--check` meldt elk
  volume dat afwijkt. De collecties `r1`, `r2` en `r3` moet hij overslaan: in `volumes_placements`
  alleen volumes zonder collectie meetellen, en `volume.configure.replication` alleen op de
  standaardcollectie laten werken. Anders zet hij de films terug op 3 kopieën en blijft de
  controle rood. (Stond `media` van fase 11 daar al, vervang het dan door `r1`, `r2` en `r3`.)

**2. De template uitrollen en de mount herstarten**, per node, de VIP-node als laatste:

- op elke andere node: `systemctl restart cf-platform-mount`;
- op de VIP-node: `platform-ctl.sh down`, `systemctl restart cf-platform-mount`,
  `platform-ctl.sh up`.

Nakijken: `ps aux | grep "weed mount"` toont geen `-replication` meer.

**3. De regels per map**, één keer (ze gelden voor het hele cluster), op één node:

```bash
printf 'fs.configure -locationPrefix=/blobs-r1/ -collection=r1 -replication=000 -apply\n' | weed shell -master=127.0.0.1:9333
printf 'fs.configure -locationPrefix=/blobs-r2/ -collection=r2 -replication=001 -apply\n' | weed shell -master=127.0.0.1:9333
printf 'fs.configure -locationPrefix=/blobs-r3/ -collection=r3 -replication=002 -apply\n' | weed shell -master=127.0.0.1:9333
```

De mount gebruikt `-filer.path=/`, dus `/srv/platform-data/blobs-r2` is in de filer `/blobs-r2/`.
`000` is één kopie (gaat de node weg, dan is de inhoud weg: alleen voor wat je opnieuw kunt halen),
`001` twee kopieën op verschillende nodes (met 3 nodes overleeft dat het verlies van één node),
`002` drie. `blobs-r3/` is alleen nodig als de standaard lager is dan 3. Nakijken:
`printf 'fs.configure\n' | weed shell -master=127.0.0.1:9333` toont de drie regels.

**4. Het platform**: `PLATFORM_BLOB_SPLIT=true` en `PLATFORM_BLOB_DEFAULT_COPIES` (het aantal van
master en filer: `002` is 3) staan in `/etc/clusterforge/platform.env` (stap 1); het platform
herstarten (`platform-ctl.sh down` en `up` op de VIP-node). In de log staat
`blobsplit: aan, standaard 3 kopieën`.

**5. De bibliotheken**: op /beheer → Media → Bibliotheken per bibliotheek de kopieën kiezen (bv.
Films 2, Series Standaard) en per rij op Opslaan drukken. Een minuut later begint de ronde.

## Nakijken

- De log van de app na een ronde:
  `blobsplit: {'naar_blobs': 0, 'naar_r1': 0, 'naar_r2': 812, 'naar_r3': 0, 'weg': 0, 'afgebroken': 0}`
  (hoeveel inhoud naar welke map verhuisde, hoeveel oude kopieën uit `.weg/` weg zijn, en hoeveel
  kopieën die bij een herstart halverwege bleven steken uit `.tmp/` zijn opgeruimd). Een regel
  `blobsplit: … niet naar blobs-r2: …` zegt welke inhoud niet lukte; de volgende ronde probeert
  opnieuw. `… klopt niet (SHA-256)` betekent dat de bron zelf stuk is: die blijft waar ze staat.
  `blobsplit: … in blobs-r2 is stuk; opnieuw kopiëren`: een kopie die al op haar plek stond maar
  niet heel was (na een stroomstoring); de goede kopie blijft staan tot er een hele naast ligt.
- De volumes: `printf 'volume.list\n' | weed shell -master=127.0.0.1:9333 | grep -c 'collection:"r2"'`
  geeft volumes, met `replica_placement:1`; die van `r1` hebben er geen (`0`), de volumes zonder
  collectie `2`.
- De mappen: `ls /srv/platform-data` toont `blobs-r2` (en de andere die in gebruik zijn).
- `platform-ctl.sh replicatie --check` blijft groen (stap 1, de functie `replicatie`).

## Terugzetten

**Eén bibliotheek terug naar de standaard:** op /beheer "Standaard (N)" kiezen. Een minuut later
gaat de inhoud terug naar `blobs/` (een nieuwe write: weer 3 kopieën).

**Helemaal uit:**

1. Elke bibliotheek op "Standaard" zetten, een ronde laten lopen, en nakijken dat er niets meer
   staat: `find /srv/platform-data/blobs-r? -type f -not -path '*/.weg/*' -not -path '*/.tmp/*' | wc -l`
   geeft `0`.
2. Een dag wachten (dan ruimt de app `.weg/` zelf op), of na een dag de mappen `.weg` zelf weggooien.
3. `PLATFORM_BLOB_SPLIT=false` in `platform.env`, het platform herstarten.
4. De regels weghalen:
   `printf 'fs.configure -locationPrefix=/blobs-r1/ -delete -apply\n' | weed shell -master=127.0.0.1:9333`
   (en zo voor `/blobs-r2/` en `/blobs-r3/`). De mount mag weer `-replication=002` krijgen.

Zet je `PLATFORM_BLOB_SPLIT` uit zonder stap 1, dan blijft wat in `blobs-r1/` tot `blobs-r3/` staat
daar leesbaar (op het aantal kopieën van die map), maar het verhuist niet meer.

**Terug naar een oudere versie** (`db-downgrade 0018` of lager): een oudere versie kijkt niet in
`blobs-r1/` tot `blobs-r3/`. Doe dus eerst stap 1 hierboven. `db-downgrade` zegt dat ook. De
keuzes per bibliotheek gaan met de downgrade weg.

**De standaard veranderen** (bv. master en filer naar `001`): `PLATFORM_BLOB_DEFAULT_COPIES` moet
altijd gelijk zijn aan wat het cluster voor `blobs/` geeft. Met een andere standaard verhuist de
inhoud van bibliotheken op "Standaard" niet (die blijft in `blobs/`), maar een bibliotheek op 3
gaat dan naar `blobs-r3/`. Wat al in `blobs/` staat, houdt de oude replicatie tot het opnieuw
geschreven wordt (`fs.configure` en de standaard gelden voor nieuwe writes).
