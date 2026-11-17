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
zoeken op beeld) in het cluster. Dit runbook beschrijft wat er nu is.

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
   "Images zonder container" en **Docker** → **Images** → **Ongebruikte weg** halen elk image
   weg dat geen container gebruikt. Dat is `cf-platform:vorige` (dan kan Platform terug niet
   meer) en op de nodes zonder VIP zelfs `cf-platform:local` (dan moet die node eerst bouwen
   voordat het platform er kan starten).

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
2. Alleen in dat tweede geval, en alleen als je accepteert wat er verloren gaat (zie
   hieronder): open Platform terug, zet bovenaan `MET_DATABASE=ja`, **Bewaren** en draai het
   opnieuw op het hele cluster. Zet het daarna meteen terug op `nee` en **Bewaren**.
3. Aan het eind drukt Platform terug de commit van het vorige image af. Zet die in ClusterForge
   bij **Versie** en **Toepassen**. Anders bouwt de volgende Platform bijwerken de nieuwe versie
   weer.

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
- **Images opruimen** op een node van het platform (zie "Vooraf").

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
| welke versie elke node heeft, en op welke nodes de VIP-node wacht (gedeeld, geen geheimen) | `/srv/platform-data/.cf-platform-bijwerken/node-<naam>`, `wacht-bijwerken`, `wacht-terug` | Platform bijwerken en Platform terug |
