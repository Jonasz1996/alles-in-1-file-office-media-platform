# Runbook: het platform in het cluster bijwerken en terugzetten

Dit runbook hoort bij de template **media-platform 1.0.0** van ClusterForge zoals die nu is.
Het platform draait daar als de dienst `cf-platform`, alleen op de node met het VIP van het
platform. Elke node heeft een eigen image `cf-platform:local`, gebouwd uit de commit die in
ClusterForge bij **Versie** staat.

ClusterForge bouwt en herstart bij een nieuwe Versie zelf niets. De hulptekst bij Versie zegt
van wel, maar dat klopt niet: Toepassen zet alleen de nieuwe versie in twee scripts op de
nodes. Daarom zijn er twee eigen commando's:

- **Platform bijwerken** ([infra/cluster/platform-bijwerken.sh](../../infra/cluster/platform-bijwerken.sh))
- **Platform terug** ([infra/cluster/platform-terug.sh](../../infra/cluster/platform-terug.sh))

Ze zijn getest op een nagebootst cluster van drie nodes
([infra/cluster/tests/test_cluster_scripts.sh](../../infra/cluster/tests/test_cluster_scripts.sh),
in CI in de job `infra`). Op echte nodes met keepalived, systemd en Docker zijn ze nog niet
gedraaid.

In 0.8.2 komen er twee dingen bij: een weg terug die de database met het nieuwe image
terugzet (`db-downgrade`), zodat er geen gegevens verloren gaan, en de analyse (gezichten en
zoeken op beeld) in het cluster. De stappen van 0.8.1 naar 0.8.2 en terug staan in
[Van 0.8.1 naar 0.8.2](#van-081-naar-082). De sleutels van gezichten:
[De sleutels van gezichten](#de-sleutels-van-gezichten).

Het aantal kopieën per bibliotheek (bv. Films x2, Series x3) en wat daarvoor in SeaweedFS en de
template moet: [replicatie.md](replicatie.md).

ClusterForge zelf open je op http://192.168.113.154:8080 (het VIP van ClusterForge, ook
`clusterforge.jbogaert.be`). `platform-ctl.sh` hieronder is
`/usr/local/lib/clusterforge/platform-ctl.sh` op een node.

## Eenmalig: de twee commando's in ClusterForge

Doe dit twee keer, één keer per script:

1. **Commando's** (onder Uitrollen en onderhoud) → **Nieuw commando**.
2. **Naam**: `Platform bijwerken` (de tweede keer `Platform terug`).
3. **Als gebruiker**: `root`.
4. **Limiet (min)**: `60` (3600 s per node). Een `docker build` van het platform duurt lang.
5. Plak in het scriptveld de hele inhoud van `infra/cluster/platform-bijwerken.sh` (de tweede
   keer `platform-terug.sh`), van de commit waar je naartoe gaat. Neem de eerste regel
   `#!/bin/sh` mee.
6. **Bewaren**.

Komt er later een nieuwe versie van een script: open het commando, plak de nieuwe tekst en
**Bewaren**. ClusterForge bewaart de oude als revisie.

Zo draai je een commando, elke keer:

1. Open het commando → **Uitvoeren…** → **Heel cluster** → kies het cluster.
2. Laat **"Verder op de volgende nodes als het op een node mislukt"** uit. Dat vinkje wordt
   niet bewaard: kijk er elke keer naar.
3. **Uitvoeren op … nodes** (alle nodes van het cluster). Bij een prodcluster vraagt
   ClusterForge eerst de naam.
4. Volg de uitvoer via de link naar de taak.

ClusterForge draait het commando node voor node: eerst de nodes zonder VIP, de nodes met een VIP
(ook dat van de database) als laatste. Weigert of faalt het op een node, dan stopt de run daar.
De volgende nodes worden dan niet geraakt.

Hebben het VIP van het platform en het VIP van de database elk een andere node, dan komt de
node met het VIP van de database soms ná die van het platform. Daarom meldt elke node in
`/srv/platform-data/.cf-platform-bijwerken` welke versie hij heeft. De node met het VIP van het
platform raakt de database pas als elke andere node zover is. Zie
[Twee runs](#twee-runs-nog-niet-en-nog-niet-klaar).

De scripts lezen het VIP en de versie op de node zelf, en de database uit
`/etc/clusterforge/platform.env`. Er staat geen wachtwoord in het commando en ze drukken er
geen af. Zet er ook zelf geen in.

## Vooraf, elke keer (alleen kijken)

1. **`pg_dump`, `pg_restore` en `psql` op elke node**, in de hoofdversie van de PostgreSQL-server
   of nieuwer. De template installeert ze niet. Kijken kan met een los commando op het hele
   cluster (bijvoorbeeld een derde opgeslagen commando `Platform vooraf`, als root):

   ```sh
   #!/bin/sh
   for t in pg_dump pg_restore psql; do command -v "$t" || echo "$t ontbreekt"; done
   pg_dump --version 2>/dev/null
   df -h /var/lib/docker
   exit 0
   ```

   Ontbreekt er een, werk dan niet bij. Dan moet `postgresql-client` in de template (een nieuw
   pakket: eerst overleggen). Elke node, want het VIP kan verhuizen.
2. **Vrije ruimte**: minstens 5 GiB vrij op `/var/lib/docker` van elke node. Het nieuwe image
   komt naast het vorige te staan.
3. **Extra vangnet**: **Databases** → de database van het platform → **Dumps** → **Dump nu**.
   Platform bijwerken maakt zelf ook een dump; deze staat daar los van in ClusterForge.
4. **Nooit images opruimen op een node van het platform.** **Docker** → **Opruimen** →
   "Images zonder container", **Docker** → **Images** → **Ongebruikte weg**, bij **Updates**
   "ongebruikte images opruimen" en bij het opruimen van een schijf "Ongebruikte Docker-images
   en bouwcache" halen elk image weg dat geen container gebruikt. Dat is `cf-platform:vorige`
   (dan kan Platform terug niet meer) en op de nodes zonder VIP zelfs `cf-platform:local` (dan
   moet die node eerst bouwen voordat het platform er kan starten).

## Bijwerken

1. **Clusters** → het cluster → **Specificatie** → **Wijzigen…** → **Versie**: de volledige
   commit-hash van de nieuwe versie (40 tekens). Een branchnaam kan ook, maar dan bouwt elke
   node wat er op dat moment op de branch staat. → **Toepassen**. Er wordt nog niets gebouwd
   of herstart.

   Zie je bij Toepassen de melding "Een eerdere revisie staat nog niet op alle nodes. Toepassen
   past daarom op elke node alle stappen van de template opnieuw toe.", ga dan niet door. Dan
   bouwt en herstart ClusterForge zelf, met hoogstens 10 minuten per stap, zonder
   `cf-platform:vorige` en zonder dump voor Platform terug. Zorg eerst dat de huidige revisie op
   alle nodes staat.
2. **Commando's** → **Platform bijwerken** → uitvoeren op het hele cluster (zie boven).
3. Controle: open het platform in de browser en log in. `https://<domein>/api/v1/info` toont
   de nieuwe versie.

Wat Platform bijwerken op elke node doet:

- de controles uit de tabel [Als een commando weigert](#als-een-commando-weigert);
- het drukt de versies af: de Versie uit ClusterForge, het image van nu, wat er draait;
- het bouwt alleen als code of image niet bij de Versie horen. Het image van daarvoor blijft
  bewaard als `cf-platform:vorige`;
- een node zonder VIP is dan klaar;
- de node met het VIP van het platform kijkt eerst of elke andere node de Versie al heeft.
  Zo niet, dan doet hij niets (zie [Twee runs](#twee-runs-nog-niet-en-nog-niet-klaar));
- de node met het VIP maakt eerst een dump van de database, en pas daarna bouwt hij. Dat geldt
  ook als `cf-platform` daar op dat moment niet draait: keepalived start het daarna met het
  nieuwe image, en de app migreert de database bij de start;
- daarna herstart de node met het VIP het platform, als er iets veranderd is (met nog een dump,
  vlak voor de herstart), en wacht tot de app antwoordt (hoogstens 15 minuten). Het platform is
  tijdens de herstart even weg.

De dumps komen in `/var/backups/cf-platform` (daar blijven de 5 nieuwste). Bij een nieuw image
blijft de laatste van die dumps daarnaast bewaard als
`/var/lib/cf-platform/bijwerken/voor-update.dump`, met in `voor-update.info` van welke naar
welke versie. Platform terug gebruikt die. Een herstart zonder nieuw image (bijvoorbeeld na een
andere instelling in ClusterForge) laat `voor-update.dump` staan.

Nog een keer draaien is veilig: staat alles al op de Versie, dan doet het niets.

Mislukt het bouwen, dan is het platform niet geraakt en houdt de node het oude image. Los de
oorzaak op (vaak: de Versie bestaat niet, geen netwerk, schijf vol) en draai opnieuw. Antwoordt
de app na de herstart niet: zie hieronder.

## Van 0.8.1 naar 0.8.2

In twee stappen, zodat elke stap apart terug kan: eerst het platform, dan de template.

**Eerst dit, over de analyse.** ClusterForge `main` zet `PLATFORM_ANALYSE_POSTGRES=true` al sinds
`26879dd` (PR #162 van ClusterForge), in `/etc/clusterforge/platform-analyse.yaml`. Dat geldt
zodra de parameter **Gezichten en zoeken op beeld** aan staat, en dat is de standaard. De sync
van de sleutels met wissen (achter `sleutels-klaar`) komt pas met PR #163. Start 0.8.2 met de
analyse aan en de oude sync, dan blijft na "uit" de sleutel van een gebruiker op de andere nodes
staan: zijn gezichten zijn dan niet echt weg. Daarom staat de parameter uit van stap 2 tot
stap 3. Met een ClusterForge van vóór `26879dd` is dat niet nodig, maar ook niet verkeerd.

ClusterForge herstart het platform niet als alleen een parameter verandert (alleen de gewijzigde
bestanden gaan naar de nodes). Daarvoor is `platform-ctl.sh restart-if-changed`: draai het als
los commando op het hele cluster. Het doet alleen iets op de node waar het platform draait, en
alleen als er iets veranderde: eerst een dump, dan de herstart.

1. **Vooraf** zoals hierboven. Gaat de analyse aan: 12 GB RAM of meer per node (16 is beter).
2. **Het platform**: **Wijzigen…** → Versie = de commit van 0.8.2 en **Gezichten en zoeken op
   beeld** uit → **Toepassen**. Dan **Platform bijwerken** (zie [Bijwerken](#bijwerken)). Dat
   herstart het platform met 0.8.2 en de analyse uit. De app migreert 0012 → 0013 → 0014. 0013
   duurt seconden; 0014 vult de zoekindex, bij een grote bibliotheek enkele minuten. Controle:
   de browser, `platform-ctl.sh status`, en `/beheer` → Zoeken toont de index van PostgreSQL.
   Bij Gezichten zegt `/beheer` "Analyse: uit (ANALYSE=uit)". Zo hoort het. Er gaat niets
   verloren: 0.8.1 draaide de analyse op PostgreSQL nog niet.
   Staat PR #163 al op het cluster, dan mag de parameter aan blijven. Kijken kan met een los
   commando: `grep -c sleutels-klaar /usr/local/lib/clusterforge/platform-ctl.sh` geeft dan op
   elke node meer dan 0.
3. **De template**: ClusterForge bijwerken naar de versie met PR #163 (Jonas voegt hem samen).
   Neem eerst een dump (**Dump nu**, zoals in "Vooraf"): het **Toepassen** hierna herstart het
   platform op de node met het VIP zonder dump (de unit van de mount verandert). Dan het cluster
   → **Toepassen**, met de parameter nog uit. Dat brengt de sync van de sleutels met wissen
   achter `sleutels-klaar`, de grenzen van de containers en `BindsTo=keepalived`. Daarna
   **Wijzigen…** → **Gezichten en zoeken op beeld** aan → **Toepassen**, en
   `platform-ctl.sh restart-if-changed` (zie boven). Nu staat de analyse aan, met
   `PLATFORM_ANALYSE_POSTGRES=true` en de sync die wist.
4. **Controle**: op de node met het VIP `platform-ctl.sh compose exec -T platform python -m
   app.cli analyse-status` (eerste regel `Analyse: ok`, met de grenzen). Een gebruiker zet
   gezichten aan (Account → Foto's). De app maakt zijn sleutel bij het eerste gezicht dat de scan
   vindt; een minuut later staat die op elke node en is de sync `platform-sleutels` groen (zie
   [De sleutels van gezichten](#de-sleutels-van-gezichten)).
5. **Eén keer de [failover-handtest](#de-failover-handtest).**

Terug, van de laatste stap naar de eerste:

- **Alleen de template slecht** (stap 3): eerst **Wijzigen…** → **Gezichten en zoeken op beeld**
  uit → **Toepassen**, en `platform-ctl.sh restart-if-changed`: het platform herstart met de
  analyse uit. Pas daarna ClusterForge terug naar de vorige versie en het cluster →
  **Toepassen**. In die volgorde: ClusterForge `main` zet `PLATFORM_ANALYSE_POSTGRES` zelf
  (`26879dd`), met de sync die niet wist. Er gaat niets verloren: de gezichten, de vectoren en
  de sleutels blijven.
- **0.8.2 zelf slecht**: **Platform terug** met `MET_DATABASE=downgrade` (eerste keus; zie
  [Terugzetten](#terugzetten)). Het image van 0.8.2 zet de database eerst terug naar 0012; wat
  sinds de update in de database kwam, blijft. Weg zijn de zoekindex (0014) en de plaatsen van
  foto's (0013). Daarna Versie = de commit van 0.8.1 en **Toepassen**. Staat de template van
  stap 3 er al, zet die dan ook terug: 0.8.1 kent `sleutels-check` niet, dus de sync van de
  sleutels meldt elke minuut een fout (hij kopieert en wist dan niets: veilig, maar luid).
- **Lukt de downgrade niet**: Platform terug met `MET_DATABASE=ja`. Dat zet
  `voor-update.dump` terug: wat sinds de update in de database kwam, is dan weg
  (`voor-terug.dump` bewaart die stand).
- **Laatste vangnet**: de dump uit "Vooraf" (zie
  [Laatste vangnet](#laatste-vangnet-de-dump-van-clusterforge)).

## De sleutels van gezichten

Vanaf de template van 0.8.2. Elke eigenaar met gezichten aan heeft een sleutel: één bestand in
`/var/lib/cf-platform/sleutels/gezichten` op de node zelf. Niet op de gedeelde data en niet in
de database. Alleen de node met het VIP schrijft er (daar draait de app).

- **De sync `platform-sleutels`** kopieert die map elke minuut van de node met het VIP naar de
  andere nodes, met wissen: een sleutel die op de node met het VIP weg is ("uit"), gaat ook
  elders weg.
- **Alleen achter `platform-ctl.sh sleutels-klaar`.** Dat draait eerst, op de node die stuurt.
  Het slaagt alleen als die node het VIP heeft, `cf-platform` er draait, de map een echte map
  is van uid 10001 (geen symlink), en `python -m app.cli sleutels-check` 0 geeft. Anders
  kopieert en wist de sync niets. Op een node zonder het VIP van het platform (zoals die met
  alleen het VIP van de database) en vlak na een failover telt ClusterForge de run als
  overgeslagen, niet als mislukt.
- **Nooit een nieuwe sleutel als er nog versleutelde gezichten zijn.** Mist een sleutel, dan
  wacht de scan van die eigenaar en zegt `/beheer` "sleutel ontbreekt".
- **Nakijken** op de node met het VIP: `platform-ctl.sh compose exec -T platform python -m
  app.cli sleutels-check`. Exitcode 0: elke eigenaar met versleutelde gezichten heeft een
  sleutel die ze opent. 1: er is iets mis; de uitvoer zegt wat (alleen aantallen). Op elke node
  hetzelfde aantal sleutels: een los commando op het hele cluster met
  `ls /var/lib/cf-platform/sleutels/gezichten | wc -l`.
- **Een sleutel die alleen op een andere node staat** (een failover vlak na het eerste gezicht
  van een eigenaar, vóór de sync): `sleutels-check` geeft 1 en de sync meldt een fout, zonder iets
  te wissen. Kopieer het bestand `<id>.key` van die node naar dezelfde map op de node met het
  VIP (rechten 0600, eigenaar 10001). Daarna is de sync weer groen.
- **De dumps.** De dump van het platform (`platform-ctl.sh db-backup`, ook die van Platform
  bijwerken) maakt de tabellen van gezichten leeg (`app.cli backup-wipe-tables`). De dumps van
  de database-component van ClusterForge kunnen dat niet: daar staan de vectoren in, versleuteld.
  De sleutels zitten in geen enkele dump.

Wat "uit" en wissen op PostgreSQL wel en niet weghalen: [gezichten.md](gezichten.md#in-het-cluster).
De analyse in het cluster: [analyse.md](analyse.md#in-het-cluster-postgresql-vanaf-082).

## De failover-handtest

Eén keer na de update naar 0.8.2, en na een grote wijziging aan de template. Nog niet gedraaid
op echte nodes. De failover van de database zelf: [failover-test.md](failover-test.md).

1. **Vooraf**: een gebruiker heeft gezichten aan en de scan liep (`/fotos/personen` toont
   gezichten). De sync `platform-sleutels` is groen, en elke node heeft evenveel sleutels (zie
   boven). Noteer welke node het VIP heeft: `platform-ctl.sh status` als los commando op het
   hele cluster; die node zegt `(actief)`.
2. **Het VIP verplaatsen**: op die node `systemctl stop keepalived`. Met `BindsTo=keepalived`
   stopt `cf-platform` daar mee; een andere node neemt het VIP over en start het platform. Heeft
   die node ook het VIP van de database, dan verhuist dat mee: doe de test op een rustig moment.
3. **Controle**, na een paar minuten:
   - er draait één platform: `systemctl is-active cf-platform` als los commando op het hele
     cluster geeft één keer `active`;
   - het platform opent in de browser, en zoeken vindt een woord uit de inhoud van een
     document;
   - `/fotos/personen` toont de gezichten, en `/beheer` zegt nergens "sleutel ontbreekt";
   - `sleutels-check` op de nieuwe node met het VIP geeft 0, en de sync is na een minuut groen.
4. **Terug**: `systemctl start keepalived` op de eerste node. Het VIP blijft op de nieuwe node
   (`nopreempt`); er draait nog steeds één platform.

## Terugzetten

Na een Platform bijwerken dat slecht uitpakte. Platform terug maakt op elke node het vorige
image weer `cf-platform:local`. Het nieuwe heet daarna `cf-platform:teruggezet`. De node met
het VIP herstart het platform.

1. **Commando's** → **Platform terug** → uitvoeren op het hele cluster. Laat bovenaan
   `MET_DATABASE=nee` staan (zo staat het in het script).
   - Kent het vorige image de database nog (de nieuwe versie had geen nieuwe migratie), dan
     gaat alleen het image terug. Er gaat niets verloren.
   - Kent het vorige image de database niet, dan stopt het op de eerste node met "NIETS GEDAAN:
     het vorige image start niet op deze database" en zegt het wat er zou gebeuren. Er is dan
     op geen enkele node iets veranderd.
2. Alleen in dat tweede geval: open Platform terug, zet bovenaan `MET_DATABASE=downgrade`
   (vanaf 0.8.2, eerst proberen) of `MET_DATABASE=ja` (zie hieronder), **Bewaren** en draai het
   opnieuw op het hele cluster. Zet het daarna meteen terug op `nee` en **Bewaren**.
3. Aan het eind drukt Platform terug de commit van het vorige image af. Zet die in ClusterForge
   bij **Versie** en **Toepassen**. Anders bouwt de volgende Platform bijwerken de nieuwe versie
   weer.

Wat `MET_DATABASE=downgrade` doet (vanaf 0.8.2): het **nieuwe** image zet het schema terug met
`python -m app.cli db-downgrade <revisie van het vorige image> --ja`, in een eigen container
(`docker run --rm --network host --env-file /etc/clusterforge/platform.env`). Er gaan geen rijen
verloren: wat na de update geschreven is, blijft. Alleen wat de nieuwere revisies toevoegden,
gaat weg. Van 0.8.2 naar 0.8.1 zijn dat twee dingen: de zoekindex van 0014 (0.8.1 zoekt op
PostgreSQL weer met LIKE, alleen op naam) en de plaatsen van foto's van 0013 (het land en de plek
op de kaart blijven). `db-downgrade` noemt ze allebei. Met een nieuwe update naar 0.8.2 komen ze
terug: de app bouwt de index opnieuw en zoekt de plaatsen weer op. Verder gaat het zoals `ja`: eerst wachten tot elke andere node het vorige image heeft,
markering opzij, stoppen, een dump als `voor-terug.dump`, dan de downgrade, de revisie lezen,
het vorige image terug en starten. Mislukt de downgrade, dan is de database ongewijzigd (één
transactie), komt de markering terug en draait het nieuwe image weer; de uitvoer noemt dan
`MET_DATABASE=ja` als volgende stap. Details in `/var/lib/cf-platform/bijwerken/terug.log`.

Wat `MET_DATABASE=ja` doet op de node met het VIP:

1. Het controleert dat `voor-update.dump` precies op de revisie van het vorige image staat, en
   dat elke andere node het vorige image al heeft. Zo niet, dan doet het niets (zie
   [Twee runs](#twee-runs-nog-niet-en-nog-niet-klaar)).
2. Het zet de markering `/srv/platform-data/.cf-platform-active` opzij, zodat geen enkele node
   het platform start, en stopt `cf-platform`.
3. Het maakt eerst een dump van de database zoals hij nu is:
   `/var/lib/cf-platform/bijwerken/voor-terug.dump`.
4. Het zet `voor-update.dump` terug in dezelfde database, in één transactie: alles of niets.
5. Het controleert de revisie, zet het vorige image terug, zet de markering terug en start.

Wat je met `MET_DATABASE=ja` kwijtraakt: alles wat sinds Platform bijwerken in de database is
gekomen. Bestanden die sindsdien zijn geüpload staan nog in `/srv/platform-data`, maar de app
kent ze niet meer. Wat sindsdien is gewist, mist. De stand van vlak voor het terugzetten
blijft in `voor-terug.dump` op die node.

Mislukt het terugzetten van de database, dan leest Platform terug daarna de revisie van de
database:

- **Nog de revisie van vóór het terugzetten**: de database is niet veranderd (één transactie).
  De markering komt terug en het nieuwe image draait weer. Hetzelfde bij **Annuleren** in
  ClusterForge of de tijdslimiet voordat de database terug was.
- **Al de revisie van het vorige image**, of **niet te lezen**: de markering blijft opzij en het
  platform blijft uit. Het nieuwe image zou de database anders weer vooruit migreren, en wat
  sinds de update in de database kwam, is dan toch weg. Dat geldt ook als het daarna misgaat,
  bijvoorbeeld bij het wisselen van het image of bij Annuleren. De uitvoer zegt welke van de
  twee het is. Draai dan **Platform terug nog een keer** (met `MET_DATABASE=ja` als de uitvoer
  dat zegt). Het ziet de markering opzij, kijkt naar de database en maakt het af. Platform
  bijwerken weigert zolang de markering opzij staat.

De oorzaak staat in de uitvoer, de details in `/var/lib/cf-platform/bijwerken/terug.log`. Dat
bestand kan gegevens uit de database bevatten: deel het niet zomaar.

Faalt Platform terug op de node met het VIP nadat de andere nodes al terug zijn, dan zegt de
uitvoer dat. Het platform draait dan nog de nieuwe versie. Draai Platform bijwerken om de nodes
weer gelijk te zetten: het herkent een teruggezet image en bouwt de Versie opnieuw. Hetzelfde
als een node weigert waarop de node met het VIP wachtte (zie hieronder).

## Twee runs: NOG NIET en NOG NIET KLAAR

Dit gebeurt alleen als een andere node dan die met het VIP van het platform een VIP heeft (meestal
dat van de database). ClusterForge draait die node dan ná de node met het VIP van het platform.
Platform bijwerken, of Platform terug met `MET_DATABASE=ja`, gaat dan zo:

1. De node met het VIP van het platform zegt `NOG NIET: ...` met de nodes waarop hij wacht. Hij
   verandert niets: niet bouwen, geen dump, geen herstart, de database niet terug. Voor
   ClusterForge is hij geslaagd, dus de run gaat door.
2. De laatste node waarop hij wachtte, bouwt (of zet het image terug) en stopt dan met
   `NOG NIET KLAAR: ... draai Platform bijwerken nog een keer` (of `Platform terug`). Voor
   ClusterForge is de run dan mislukt.
3. Draai hetzelfde commando nog een keer op het hele cluster. Nu heeft elke andere node de
   Versie (of het vorige image), en de node met het VIP van het platform maakt het af.

Zegt de node met het VIP van het platform `NOG NIET` en komt er in dezelfde run geen
`NOG NIET KLAAR`, dan meldde een node iets anders dan verwacht. De regels `<node> meldde: ...`
in de uitvoer zeggen wat. Meestal staat de nieuwe Versie nog niet op elke node (zie de melding
bij Toepassen onder [Bijwerken](#bijwerken)). Er is dan niets veranderd.

Weigert een node in de eerste run, dan is de database nog niet geraakt. Los de oorzaak op en
draai opnieuw. Bij Platform terug kunnen andere nodes dan al het vorige image hebben; de melding
zegt dat je Platform bijwerken draait om ze weer gelijk te zetten.

### Laatste vangnet: de dump van ClusterForge

Niet van begin tot eind getest. **Databases** → **Dumps** → de dump uit "Vooraf" →
**Terugzetten**. ClusterForge zet die altijd in een nieuwe database `<naam>_herstel_<datum>`.
Zet daarna in de Specificatie **Database van het platform** op die naam, **Toepassen**, en
draai Platform terug. De oude database blijft staan.

## Als een commando weigert

Een weigering of fout begint met `GEWEIGERD/FOUT:`. De nodes erna draaien dan niet. Een
weigering in de controles (de eerste zeven regels hieronder) verandert niets op die node.
Weigert Platform terug pas op de node met het VIP, dan kunnen de andere nodes al terug zijn;
de melding zegt dan hoe je ze weer gelijk zet.

| Melding (kort) | Wat het betekent | Wat je doet |
|---|---|---|
| `... is niet gemount` | `/srv/platform-data` is niet gemount op die node | eerst de mount oplossen (`journalctl -u cf-platform-mount`) |
| `cf-platform draait op deze node zonder het VIP` | het platform draait waar het niet hoort | `systemctl stop cf-platform` op die node, dan opnieuw |
| `het platform draait op 2 nodes tegelijk` | twee platformen op dezelfde data en database | stop het op de node zonder VIP, dan opnieuw |
| `pg_dump ontbreekt` | geen back-up mogelijk, dus geen herstart | `postgresql-client` nodig (zie "Vooraf") |
| `loopt al op deze node` | een ander commando is nog bezig | wachten tot dat klaar is |
| `.cf-platform-active.terug bestaat` (Platform bijwerken) | een eerdere Platform terug is niet afgemaakt; het platform is uit | draai Platform terug nog een keer; dat maakt het af |
| `cf-platform:vorige ontbreekt` | geen vorig image (weggehaald, of nooit bijgewerkt) | had de nieuwe versie geen migratie: de vorige commit als Versie en Platform bijwerken; anders het laatste vangnet |
| `geen back-up van vóór de update` | `voor-update.dump` staat niet op deze node | zie [Bekende grenzen](#bekende-grenzen) |
| `bouwen mislukt` | `docker build` faalde; het platform is niet geraakt | oorzaak in de uitvoer, oplossen, opnieuw |
| `database terugzetten mislukt`, `afgebroken`, met `markering teruggezet` | Platform terug met `MET_DATABASE=ja` stopte voordat de database terug was; de database is ongewijzigd en het nieuwe image draait weer | oorzaak in de uitvoer en `terug.log`, dan Platform bijwerken of opnieuw Platform terug |
| `db-downgrade mislukt` | Platform terug met `MET_DATABASE=downgrade`: het nieuwe image kon het schema niet terugzetten; de database is ongewijzigd en het nieuwe image draait weer | de laatste regels in `terug.log`, dan Platform terug met `MET_DATABASE=ja` |
| `MET_DATABASE=…: kies nee, ja of downgrade` | een tikfout bovenaan het commando | de waarde verbeteren, **Bewaren** |
| `De database is al teruggezet` of `is niet te lezen` | Platform terug stopte toen de database (misschien) al terug was; het platform is uit | Platform terug nog een keer (zie [Terugzetten](#terugzetten)) |
| `de app antwoordt niet` | de nieuwe versie start niet | Platform terug |
| `... schrijven mislukt` (`.cf-platform-bijwerken`) | de node kan zijn melding niet in `/srv/platform-data` zetten | de mount nakijken, dan opnieuw |

`NOG NIET` en `NOG NIET KLAAR` zijn geen fouten: zie
[Twee runs](#twee-runs-nog-niet-en-nog-niet-klaar).

## Wat je nooit doet

- **`install.sh` op een clusternode** (`curl … install.sh | bash` of `sudo bash install.sh`).
  Dat zet er een platform voor één node naast, met een eigen database en compose, los van het
  cluster. `install.sh` is alleen voor [infra/single](../../infra/single/README.md).
- **Alleen de Specificatie** (Versie wijzigen en Toepassen) en denken dat het platform is
  bijgewerkt. Er wordt niets gebouwd of herstart. En als ClusterForge toch alles opnieuw
  toepast (de melding bij "Bijwerken"), dan zonder `cf-platform:vorige` en zonder dump voor
  Platform terug.
- **Updates met "Docker Compose (pull; bij een nieuw image down en up -d)"** voor het platform.
  Het image `cf-platform:local` staat in geen registry. Een `up -d` buiten `cf-platform` om
  start het platform ook op een node zonder VIP: twee platformen op dezelfde data en database.
- **Images opruimen** op een node van het platform (zie "Vooraf"): het wist
  `cf-platform:vorige`.
- **0.8.2 met Gezichten en zoeken op beeld aan, zonder PR #163.** ClusterForge `main` zet
  `PLATFORM_ANALYSE_POSTGRES` dan al (sinds `26879dd`), maar de sync van de sleutels wist niet:
  na "uit" blijft een sleutel op de andere nodes staan. Zet de variabele ook niet zelf aan.

## Bekende grenzen

- **`voor-update.dump` staat alleen op de node die bij het bijwerken het VIP had.** Is het VIP
  sindsdien verhuisd, dan weigert Platform terug met `MET_DATABASE=ja`. Kopieer dan dat bestand
  naar `/var/lib/cf-platform/bijwerken/` op de node die nu het VIP heeft, of zet bovenaan
  `DUMP=` op een andere dump van vóór de update (gemaakt met `pg_dump -Fc`). Het script
  controleert de revisie van die dump en weigert een verkeerde.
- **Platform terug op de node met het VIP leest de database met `psql`**, ook met
  `MET_DATABASE=nee`. Zonder `psql` weigert het daar.
- **Updates en Onderhoud** verplaatsen het VIP door keepalived te stoppen, maar dat stopt
  `cf-platform` niet. Het platform kan dan even op twee nodes draaien. Niet getest op echte
  nodes. Beide commando's weigeren in dat geval. De geplande template-wijziging voor 0.8.2
  (`BindsTo=keepalived`) moet dit oplossen.
- **De controle op twee platformen** vraagt de andere nodes via HTTP op poort 80. Zit daar een
  firewall tussen, dan ziet de controle alleen de eigen node.
- Met `MET_DATABASE=ja` gaan de nodes zonder VIP eerst terug. Valt de node met het VIP uit
  voordat hij aan de beurt is, dan start een andere node het oude image op de nieuwe database.
  Dat start niet; het VIP springt door. Draai dan Platform bijwerken.
- Een image van vóór de eerste Platform bijwerken heeft nog geen bekende commit; het script
  neemt dan de commit uit `/var/lib/clusterforge/platform-image.rev`.
- **De zoekindex van 0.8.2 vraagt een database met een UTF-8-ctype**, zoals ClusterForge hem
  maakt (`C.UTF-8`). Met `LC_CTYPE` `C` kent PostgreSQL alleen letters uit ASCII: een woord met
  een accent, Grieks of Cyrillisch is dan niet te vinden. De app zegt dat bij de start in het
  log ("de database heeft LC_CTYPE C"). Kijken kan met
  `SELECT datctype FROM pg_database WHERE datname = current_database();`.
- **Een frase in een lange tekst op PostgreSQL.** Een tsvector kent hooguit plek 16.383, en per
  woord hooguit 256 plekken. Na ongeveer 16.000 woorden, of na de 256e keer van een woord, vindt
  een frase ("…") daar soms niet wat SQLite vindt, of juist iets wat er niet zo staat. Losse
  woorden vindt de index tot de grens van 1 MB. Een frase loopt wel nooit over twee kolommen
  (naam, titel, tekst, OCR, meta), net als op SQLite.
- **Een database die een eerdere 0014 draaide** (een testcluster op de branch vóór de review
  van PR #6) houdt de oude functies en mist de kolom `capped`: een migratie draait niet twee
  keer. Zet hem eerst terug met `python -m app.cli db-downgrade 0013 --ja` (met het image van nu)
  en start dan opnieuw: de app draait 0014 dan opnieuw en bouwt de index.

## Wat er op een node staat

| Wat | Waar | Van wie |
|---|---|---|
| het image dat draait of bij failover start | `cf-platform:local` | ClusterForge en Platform bijwerken |
| het image van vóór de laatste update | `cf-platform:vorige` | Platform bijwerken |
| het image waar Platform terug vandaan kwam | `cf-platform:teruggezet` | Platform terug |
| welke commit bij welk image hoort | `/var/lib/cf-platform/bijwerken/gebouwd`, `vorige.rev` | Platform bijwerken |
| de dump van vóór de update (alleen de VIP-node) | `/var/lib/cf-platform/bijwerken/voor-update.dump` en `voor-update.info` | Platform bijwerken |
| de dump van vóór het terugzetten | `/var/lib/cf-platform/bijwerken/voor-terug.dump` | Platform terug |
| de 5 nieuwste dumps | `/var/backups/cf-platform` | `platform-ctl.sh` van de template |
| de sleutels van gezichten, per eigenaar (geheim, nooit in een dump) | `/var/lib/cf-platform/sleutels/gezichten` | de app op de node met het VIP; de sync `platform-sleutels` op de andere |
| welke versie elke node heeft, en op welke nodes de VIP-node wacht (gedeeld, geen geheimen) | `/srv/platform-data/.cf-platform-bijwerken/node-<naam>`, `wacht-bijwerken`, `wacht-terug` | Platform bijwerken en Platform terug |
