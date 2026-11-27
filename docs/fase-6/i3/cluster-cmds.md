# I3 cluster-cmds: de commando's Platform bijwerken en Platform terug (P7, eerste deel)

Branch `wip/i3-cluster-cmds`. Het eerste deel van P7 uit het plan voor 0.8.2: de twee opgeslagen
commando's voor ClusterForge, de test met nagebootste nodes, de stap in CI en het runbook. De
route `MET_DATABASE=downgrade` en `api/tests/test_pg_downgrade.py` komen later (golf 4).

## Wat er is

- `infra/cluster/platform-bijwerken.sh` en `infra/cluster/platform-terug.sh`: de scripts uit
  `cf-update.md` (Bijlage A en B), voor de template media-platform 1.0.0 zoals die nu is op de
  ClusterForge-branch `claude/project-thread-4hflqz` (2532166). Geen vaste branch: de versie komt
  uit `platform-ctl.sh` (REF), dus uit de parameter Versie (`platform_ref`). Wachtwoorden alleen
  via `PGPASSWORD` in de omgeving van `psql`/`pg_dump`/`pg_restore`, nooit in argumenten of
  uitvoer.
- `infra/cluster/tests/test_cluster_scripts.sh`: drie nagebootste nodes, elk een map met nep-
  `docker`, `systemctl`, `ip`, `curl`, `psql`, `pg_dump`, `pg_restore`, `mountpoint`, `id`,
  `hostname`, `df`, `sleep` en `git`, en een PATH met alleen die map. De scripts draaien
  ongewijzigd (alleen de paden bovenaan wijzen naar mappen van de test), met `dash`. De volgorde
  van ClusterForge (nodes zonder VIP eerst, elk VIP telt, ook dat van de database; stoppen bij de
  eerste fout) wordt nagedaan. Met
  `CF_TEMPLATE=<ClusterForge>/internal/templates/builtin/media-platform/1.0.0` kijkt de test ook
  of de afspraken met de template nog kloppen (paden, de regels `VIP=`/`REF=`, de subcommando's
  van `platform-ctl.sh`, de markering, `PLATFORM_DATABASE_URL`).
- CI, job `infra`: de scripts en de test in de regel van `shellcheck`, en een stap
  "Cluster-commando's controleren (test_cluster_scripts.sh)".
- `docs/runbooks/cluster.md`: instellen, vooraf, bijwerken, terugzetten, weigeringen, wat je
  nooit doet, bekende grenzen. Gelinkt uit `infra/README.md`.

## Wat de test afdekt

Elke weigering van beide scripts (geen root, bestanden of gereedschap weg, slot bezet, een
halve terugzet, mount weg, platform zonder VIP, twee platformen gezien vanaf een node zonder VIP
en vanaf de VIP-houder, `pg_dump` weg op de VIP-houder, bouwen mislukt, back-up mislukt,
`cf-platform:vorige` weg, database niet te lezen op de VIP-houder, geen `voor-update.dump`, een
dump op de verkeerde revisie), elk met de controle dat er niets veranderd is. Beide routes van
Platform terug (alleen het image; met de database).

Een terugzet met de database die halverwege faalt, op vijf manieren: `pg_restore` faalt, `psql`
faalt, de back-up vooraf faalt, `cf-platform` stopt niet, en Annuleren (SIGTERM aan de hele
procesgroep, zoals de agent van ClusterForge doet; daarvoor draait de test die run met
`setsid`). Telkens: de database ongewijzigd, de markering terug, het nieuwe image draait weer,
de hint, en Platform bijwerken zet daarna alles weer gelijk.

Idempotent: twee keer bijwerken en twee keer terug. Geen wachtwoord in de uitvoer, in de
argumenten van de nep-gereedschappen of in de bestanden die de commando's bewaren (`terug.log`,
`voor-update.info`, `vorige.rev`, de meldingen in `.cf-platform-bijwerken`).

Sinds de review ook: het VIP van de database op een andere node dan dat van het platform (beide
commando's, in twee runs), een stopgezet `cf-platform` op de VIP-houder, een herstart om alleen
`platform.env`, een fout ná de COMMIT van het terugzetten (vier soorten, elk met de run die het
afmaakt) en de rechten 0700 op de eigen map. Zie "De review".

Mutatietest (los, niet in de repo): 29 kleine fouten in de scripts, één per run (een controle
weg, een voorwaarde omgedraaid, de trap weg, `DROP OWNED` weg, het wachtwoord naar stderr,
enzovoort). Uitslag: zie "Gemeten".

## Afwijkingen van Bijlage A/B

1. `LOCK` en `BACKUPS` zijn variabelen bovenaan, zoals de andere paden (zelfde waarden), zodat de
   test elk pad naar een eigen map kan zetten.
2. Platform terug: de hint "Draai Platform bijwerken om ze weer gelijk te zetten" staat bij elke
   fout op de VIP-houder, niet alleen met `MET_DATABASE=ja`. Ook zonder database-terugzet hebben
   de nodes zonder VIP hun image dan al gewisseld.
3. Platform bijwerken: een mislukte herbouw na Platform terug haalt ook de tijdelijke tag
   `cf-platform:basis` weg, zoals een gewone mislukte bouw al deed.
4. Platform terug: INT, TERM of HUP tijdens het terugzetten van de database (Annuleren of de
   tijdslimiet) eindigt met "afgebroken" en de hint, niet met een kale `exit 1`. De trap op EXIT
   zet de markering terug en start het image dat er nu is, zoals in Bijlage B.

## Gemeten

- `bash infra/cluster/tests/test_cluster_scripts.sh`: 144 goed, 0 fout, ongeveer 36 s. Na de
  review: 199 goed, 0 fout, ongeveer 58 s.
- Met `CF_TEMPLATE=<ClusterForge>/internal/templates/builtin/media-platform/1.0.0` (de template
  op 2532166): 154 goed, 0 fout (10 controles op de template erbij). Na de review niet opnieuw
  gedraaid: de template staat niet in deze omgeving.
- `shellcheck` op beide scripts en de test: schoon. `sh -n` en `dash -n` op beide scripts: schoon.
- Mutatietest (vóór de review, niet opnieuw gedraaid): 29 van de 29 fouten gevangen. De eerste
  ronde miste er twee (het wachtwoord naar stderr, dat zo in `terug.log` belandde; Platform terug
  zonder de controle op root). Daarvoor kwamen de controle op de bestanden van de commando's en
  de test "niet als root" voor Platform terug erbij. Eén mutatie (Platform terug stopt
  `cf-platform` niet) laat de test na 12 fouten stoppen bij een voorbereidende stap (exit 2); ook
  dat telt als gevangen.
- `ruff check` en `ruff format --check` in `api/`: schoon (dit pakket raakt `api/` niet).
- De hele SQLite-suite (`uv run pytest -q -p no:cacheprovider` in `api/`, onder het slot):
  6855 passed, 5 skipped, 241 deselected, 0 failed, in 56 min. Na de review weer: 6855 passed,
  5 skipped, 241 deselected, 0 failed, in 56 min.
- `yamllint -s .` en `yaml.safe_load` op `ci.yml`: schoon. Linkcontrole (los script, er is er
  geen in de repo) op het runbook, `infra/README.md` en deze notitie: 0 fouten.

## De review

Vijf bevindingen, alle vijf nagespeeld en hersteld. Bij elke: eerst een test die faalt, dan de
fix. De getallen zijn de hele test (`test_cluster_scripts.sh`) vóór en na de fix.

- **F1: de node met het VIP van het platform komt niet altijd als laatste.** Klopt. ClusterForge
  zet elke node met een VIP achteraan, ook die met het VIP van de database (Patroni). Staat dat
  VIP op een andere node, dan draait die ná de node met het VIP van het platform. Die migreerde
  (bijwerken) of zette terug (terug) de database dus al voordat die node had gekeken of gebouwd.
  Nagespeeld met een VIP van de database in de test: n1 herstartte terwijl de build op n3 nog
  faalde, en bij terug zette n1 de database terug terwijl n3 weigerde. Fix: elke node meldt in
  `/srv/platform-data/.cf-platform-bijwerken/node-<naam>` welke Versie of welk vorig image hij
  heeft. De node met het VIP van het platform verandert pas iets als elke andere node zover is.
  Anders zegt hij `NOG NIET`, schrijft op op welke nodes hij wacht en stopt zonder iets te
  veranderen (exit 0, zodat de run doorgaat). De laatste node waarop hij wachtte stopt de run met
  `NOG NIET KLAAR`; de tweede run maakt het af. Bij terug geldt dit alleen met de database (zonder
  database raakt terug de database niet). Komt er geen `NOG NIET KLAAR`, dan meldde een node iets
  anders (bijvoorbeeld een andere Versie); de uitvoer zegt wat. 187 goed en 12 fout vóór, 199 goed
  na. Runbook: een nieuw stuk "Twee runs".
- **F2: een fout ná de COMMIT van het terugzetten start het nieuwe image op de teruggezette
  database.** Klopt. De trap zette bij elke afloop de markering terug, en de melding zei "de
  database is ongewijzigd" zodra de revisie niet die van het vorige image was, ook als hij niet te
  lezen was. Nagespeeld: na een mislukte lees-actie, een mislukte `docker tag` of Annuleren vlak na
  de COMMIT stond de database weer op 0014, met de rijen van vóór de update. Fix: het script houdt
  bij waar de database is en leest na het terugzetten de revisie (een paar keer). Alleen bij de
  oude revisie komt de markering terug. Bij de revisie van het vorige image of als hij niet te lezen
  is, blijft de markering opzij en het platform uit, met een melding welke van de twee. Platform
  terug nog een keer maakt het af; Platform bijwerken weigert zolang de markering opzij staat.
  161 goed en 20 fout vóór, 181 goed na.
- **F3: `voor-update.dump` werd ook bij een herstart zonder nieuw image vervangen.** Klopt. Na
  alleen een andere `platform.env` wees hij naar een dump op de nieuwe revisie, en Platform terug
  met de database weigerde dan. Fix: alleen een run die een nieuw image bouwde (en het oude als
  `cf-platform:vorige` bewaarde) zet `voor-update.dump`. De nep-`platform-ctl.sh` kent nu de
  vingerafdruk van de template (env en image).
- **F4: met de markering maar zonder draaiend `cf-platform` bouwde de VIP-houder zonder dump.**
  Klopt. Keepalived start daarna het nieuwe image en dat migreert de database, zonder back-up.
  Fix: de VIP-houder (na de overstap) maakt de dump vóór het bouwen, ook als `cf-platform` niet
  draait, en bouwt niet als die mislukt. F3 en F4 zitten in één commit: 147 goed en 9 fout vóór,
  156 goed na.
- **F5: `chmod 0700` op de eigen map had geen test.** Klopt; de dumps daarin zijn harde links naar
  bestanden met 0644. De test zet de map nu eerst op 0755 en kijkt na beide commando's of hij weer
  0700 is. Zonder de `chmod` in de scripts: 144 goed en 2 fout; met: 146 goed.

Afgewezen: geen.

## Open

- `pg_dump`, `pg_restore` en `psql` staan niet in de template (pakketten: keepalived, fuse3, git,
  curl, rsync). Zonder `pg_dump` herstart `platform-ctl.sh` niet; zonder `psql` weigert Platform
  terug op de VIP-houder. `postgresql-client` toevoegen is een nieuw apt-pakket: eerst Jonas (CF1).
- Niet getest op echte nodes: keepalived, systemd, Docker en een echte `docker build` in een LXC.
- De controle op twee platformen gebruikt HTTP op poort 80 tussen de nodes.
- `voor-update.dump` staat alleen op de node die bij het bijwerken het VIP had.
- Updates en Onderhoud van ClusterForge kunnen het platform even op twee nodes laten draaien
  (geen `BindsTo=keepalived` in de template van nu); de commando's weigeren dan.
- De hulptekst bij Versie in de template zegt dat Toepassen bouwt en herstart; dat doet het niet
  (alleen de regels `REF=`). Het runbook zegt dat; de tekst zelf is van ClusterForge.
- Hebben het VIP van het platform en dat van de database elk een andere node, dan zijn er twee
  runs nodig (F1 in "De review"). `NOG NIET` is voor ClusterForge een geslaagde node; pas
  `NOG NIET KLAAR` op een latere node maakt de run rood. Kan ClusterForge ooit de node met het VIP
  van het platform als laatste zetten, dan kan dit weg.
- De test heeft `setsid` nodig (util-linux) voor de run die Annuleren nadoet; zonder stopt hij
  met exit 2.
