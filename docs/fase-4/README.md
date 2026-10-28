# Fase 4 — Office

Status: **klaar** (versie 0.6.0, 28 oktober 2026).

Documenten bekijken, maken en samen bewerken in de browser, via dezelfde poorten 80 en 443 als
de rest van het platform. De editor is OnlyOffice Docs Community 10.0.0.1 (hierna DS), als
optionele tweede container naast de app. Zonder office-instellingen, of met office uit, werkt de
app precies als in fase 3.

## Doel

Uit het stappenplan:

- docx, xlsx en pptx bewerken, ook met meerdere mensen tegelijk;
- opslaan maakt een nieuwe versie;
- een voorbeeld met opmaak (PDF-pagina's) zonder de editor te openen, en downloaden als PDF;
- een nieuw document, werkblad of presentatie maken;
- de verbindingen tellen en bewaken, en Collabora als terugval klaarzetten.

Klaar als: twee gebruikers bewerken tegelijk één docx; opslaan maakt een nieuwe versie;
office-preview werkt zonder de editor te openen.

## De editor: OnlyOffice

Jonas koos op 26 oktober 2026 voor OnlyOffice (Vraag 1 in het ontwerp, B3 en B31).

- **OnlyOffice Docs Community 10.0.0.1**, vastgepind op digest, als **optionele tweede
  container**. Zonder die container werkt alles zoals in fase 3; alleen bewerken kan dan niet, en
  voorbeelden komen uit de eigen lezer (B21).
- **Collabora** blijft de terugval. Sessies, opslaan, versies, toegang en voorbeelden zitten in
  een kern die OnlyOffice niet kent; alleen een kleine adapter spreekt zijn protocol. Hoe
  omschakelen gaat (±1 week): [collabora.md](collabora.md).
- **Zelf bouwen** valt buiten deze fase: samen bewerken in docx, xlsx en pptx is een product op
  zich.

Twee dingen uit het stappenplan gelden niet voor OnlyOffice 10.0 Community: de limiet van 20
verbindingen (die bestaat sinds 9.4 niet meer; we tellen zelf, met een eigen grens
`max_editors` per document, standaard 20), en een eigen Postgres, Redis en RabbitMQ (Community
draait als één proces, met de bewerkstatus in het geheugen).

## Wat er is

- **O0 — Fundament en spike:** migratie 0007 (met `app.cli db-downgrade 0006` terug naar fase
  3), de bevroren contracten, de kern van proxy en tunnel, de invoercontrole (echte bytes tellen,
  grenzen per soort, ook tegen zip-bommen), de gezondheid van DS (ook "verkeerd ingesteld" en het
  wachten op zijn font-herstart), een nep-DS voor de tests, en de echte DS in CI vanaf de eerste
  dag. De uitslag van de spike: [spike.md](spike.md).
- **O1 — Toegang:** een eigen proxy en WebSocket-tunnel op `/office/`, zonder Traefik, nginx of
  extra poort. Elk pad naar DS heeft een klasse; al het andere is 404. Bij elk verzoek beslist de
  app met het poort-cookie wie het is en of die nu nog mag bewerken; een ingetrokken login is
  meteen uit de editor ([o1.md](o1.md)).
- **O2 — Bewerken:** de editorpagina `/bewerken/<id>` (Bewerken in de lijst en de viewer, of E),
  sessies met één key per bestand, opslaan als nieuwe versie (één per sessie, plus één per 30
  minuten, hooguit 10 per sessie), tussentijds opslaan elke 5 minuten, en herstel: wat niet op
  het origineel kan, wordt een herstelkopie, en werk dat in DS bleef hangen, zet de app vanzelf
  terug ([o2.md](o2.md), [toetsenbord-o2.md](toetsenbord-o2.md)).
- **O3 — Voorbeelden en PDF:** echte pagina's en thumbnails voor docx, xlsx, pptx, odt, ods,
  odp, doc, xls, ppt en rtf via de conversie van DS, en "Downloaden als PDF". De eigen lezer van
  fase 3 blijft de terugval. Uploads via een link en bestanden boven 20 MB alleen op verzoek
  ([o3.md](o3.md), [toetsenbord-o3.md](toetsenbord-o3.md)).
- **O4 — Nieuw document:** Nieuw → Document, Werkblad of Presentatie (ook met O), uit eigen lege
  bestanden in de taal van de gebruiker ([o4.md](o4.md), [toetsenbord-o4.md](toetsenbord-o4.md)).
- **O5 — Beheer:** het tabblad Office op `/beheer` (Alt+7): de gezondheid en versie van DS, de
  verbindingen, mensen en documenten van nu en de piek, de open documenten, Niet opgeslagen
  (Terugzetten en Verwijderen), de conversies (Mislukte opnieuw), Nu tussentijds opslaan, de
  instellingen (`max_editors`, de minuten, aan en uit) en de meldingen ([o5.md](o5.md),
  [toetsenbord-o5.md](toetsenbord-o5.md)).
- **O6 — Uitrol:** office met hetzelfde ene `install.sh`, standaard aan; DS op een intern
  Docker-netwerk zonder uitgang en zonder poort, afgeschermd (alle capabilities eraf op zeven na,
  `no-new-privileges`, 512 processen) en, als de kernel het kan, met 2 GB en 2 cores; de geheimen
  in `.env`; het image op digest met de mirror als terugval; bijwerken en terugrollen zonder dat
  het platform breekt ([o6.md](o6.md), runbook [office.md](../runbooks/office.md)).
- **Integratie (§5 stap 4):** de bevindingen van de review verholpen; het bewijs van "Klaar als"
  met de echte DS ([klaar.md](klaar.md)); de installatietest in de sandbox, van een installatie
  uit fase 3 naar deze versie, terug naar fase 3 zoals het runbook zegt, en weer vooruit
  ([integratie.md](integratie.md)); de metingen van §4.7 ([metingen.md](metingen.md)).

## Klaar als

| Criterium (stappenplan) | Bewijs |
|---|---|
| Twee gebruikers bewerken tegelijk één docx | met de echte DS: `e2e/test_office_o2_real.py::test_twee_gebruikers_een_docx` (Anna en Bram, elk met 2FA, dezelfde key, `info` noemt beide, beide woorden in de docx), met schermafdrukken; met de nep-DS bij elke PR ([klaar.md](klaar.md)) |
| Opslaan maakt een nieuwe versie | dezelfde test: precies één versie meer, byte voor byte de inhoud van vóór de sessie; ook bij `docker compose stop` met een open editor in de installatietest ([klaar.md](klaar.md), [integratie.md](integratie.md)) |
| Office-preview werkt zonder de editor te openen | met de echte DS: `test_office_o3_real.py::test_voorbeeld_zonder_editor_echt` (docx, xlsx, pptx, doc en rtf binnen 60 s, zonder verkeer naar `/office/`); met de nep-DS `test_office_o3.py` ([klaar.md](klaar.md)) |

De e2e met de echte DS: 22 van de 22, niets overgeslagen; met de nep-DS 30 van de 30; de
installatietest 149 controles goed, 0 mislukt. Wat de sandbox niet kan bewijzen (Jonas' LXC,
andere browsers, een echt certificaat), staat in [klaar.md](klaar.md).

## Office aan en uit

- **Op de server:** office staat na het bijwerken standaard aan (`OFFICE=aan` in
  `/opt/platform/.env`). Uit met het installatiescript en `OFFICE=uit`, weer aan met `OFFICE=aan`
  (het commando staat in [infra/single/README.md](../../infra/single/README.md), Office). Uit laat
  eerst alles opslaan en haalt dan de container van DS weg; de volumes blijven. Het platform werkt
  dan zoals in fase 3.
- **Op `/beheer` → Office:** het vinkje "Bewerken en omzetten naar PDF aan", zonder herstart.
  Uit stopt nieuwe sessies, Nieuw en nieuwe PDF's; wie al bewerkt, slaat gewoon op, en een PDF
  die er al is, blijft te downloaden.
- **Terug naar fase 3 (de code):** liever niet; liever office uit. Moet het toch, dan precies in
  de volgorde van het runbook, "De app terugrollen" (met `db-downgrade 0006`). Zo nagelopen in
  de installatietest.

## Vragen voor Jonas

1. Welke editor? **Besloten: OnlyOffice** (Jonas, 2026-10-26; B3 en B31).
2. Office meteen aan bij installeren en bijwerken? **Standaard ja**, en zo gebouwd (±1 GB
   downloaden, het image ±3,1 GB op schijf; B37). Jonas koos niet anders; kan nog veranderen.
3. Het restrisico van de editor op hetzelfde adres als de app accepteren? **Besloten: ja**
   (Jonas, 2026-10-26; B32), met de beperkingen uit het ontwerp en later een eigen hostnaam
   (fase 8).
4. Hoeveel versies maakt de editor? **Standaard** één per bewerksessie, plus één per 30 minuten,
   hooguit 10 per sessie, en zo gebouwd (B34). Jonas koos niet anders; kan nog veranderen
   (`/beheer` → Office heeft de minuten).
5. De Collabora-adapter nu bouwen? **Standaard nee**, alleen klaargezet
   ([collabora.md](collabora.md)). Jonas koos niet anders; kan nog veranderen.

## Documenten

- [bouwontwerp.md](bouwontwerp.md): het ontwerp (revisie 2, leidend, met korte noten waar de
  bouw afwijkt); revisie 1 in [bouwontwerp-r1.md](bouwontwerp-r1.md). De keuzes staan in B31–B38
  in [beslissingen](../fase-0/beslissingen.md).
- Per pakket: [o1](o1.md), [o2](o2.md), [o3](o3.md), [o4](o4.md), [o5](o5.md), [o6](o6.md), elk
  met zijn afwijkingen en open punten; [spike.md](spike.md) en [collabora.md](collabora.md).
- Sneltoetsen: [toetsenbord-o0](toetsenbord-o0.md), [o2](toetsenbord-o2.md),
  [o3](toetsenbord-o3.md), [o4](toetsenbord-o4.md), [o5](toetsenbord-o5.md).
- Testen en meten: [e2e.md](e2e.md) (de e2e met de nep-DS en de echte DS, en ontwikkelen met een
  echte DS), [klaar.md](klaar.md) (het bewijs van "Klaar als", met de schermafdrukken in
  [klaar/](klaar/)), [integratie.md](integratie.md) (de installatietest), [metingen.md](metingen.md)
  met [metingen.json](metingen.json).
- Voor de server: [infra/single/README.md](../../infra/single/README.md) (Office) en het runbook
  [office.md](../runbooks/office.md) (bijwerken, terugrollen, niet-opgeslagen werk, de belasting,
  de controle van de LXC).

Dezelfde stappen als in CI, vanuit de hoofdmap:

```sh
cd api && uv run ruff check . && uv run ruff format --check . && uv run pytest -q   # API
cd web && npm run typecheck && npm test && npm run build                            # web
cd web && npm run check:i18n -- --strict && npm run budget                          # i18n, budget
cd api && uv run --with playwright pytest ../e2e -m office                          # nep-DS
cd api && E2E_REAL_DS=… E2E_REAL_DS_SECRET_FILE=… \
  uv run --with playwright pytest ../e2e -m office_real -rs                         # echte DS
bash infra/single/tests/test_install_env.sh                                         # install.sh
```

Een echte DS starten en de installatietest: [e2e.md](e2e.md) en [integratie.md](integratie.md).

## Bekende grenzen

Elk in één regel; de uitleg staat achter de link.

Bewerken:

- Een docx met heel veel opmaak net onder de grens van 4 MB XML opent niet in de editor (de
  browser blijft rekenen); het PDF-voorbeeld werkt wel ([metingen.md](metingen.md#bevindingen),
  bevinding 4).
- De echte editor laat vlak na het openen soms de eerste toets vallen, op een volle machine soms
  de eerste woorden; de gebruiker ziet dat, het is geen stil verlies ([o2.md](o2.md), afwijking
  32; [metingen.md](metingen.md#bevindingen), bevinding 6).
- De pptx-editor van DS komt soms niet klaar (A22, een fout in DS zelf); de editorpagina laadt
  hem dan één keer opnieuw ([metingen.md](metingen.md#bevindingen), bevinding 3).
- Na een kill of OOM van DS is weg wat sinds het laatste tussentijds opslaan getypt is, plus de
  laatste ±2 s ervoor. De klok slaat pas op bij de eerste ronde (elke 30 s) na 5 minuten, dus
  dat is hooguit ±5,5 minuten; gemeten zijn kills 30, 150 en 285 s na het opslaan, het slechtste
  geval vlak voor het volgende opslaan niet. Een nette stop slaat alles op
  ([metingen.md](metingen.md#kill-van-ds-met-open-editors-ja)).
- Na elke start van DS kan er ±2 minuten niet bewerkt worden (de font-herstart), en `docker
  stop` met open editors duurt ±30 s ([metingen.md](metingen.md#stoppen-met-open-editors-ja),
  [o6.md](o6.md), afwijking 17).
- `max_editors` (standaard 20) geldt per document, niet voor alle documenten samen; gemeten is
  hooguit 20 tegelijk ([metingen.md](metingen.md#max_editors-keuze-27)).
- Warm openen duurt 2,3–2,5 s in plaats van 2 s; DS zelf doet er al 1,9–2,2 s over, en React
  wacht ±0,3 s per opening via een link ([metingen.md](metingen.md#warm-openen-nee), bevinding 2).
- Een definitief verwijderd bestand geeft de editorpagina geen eigen melding; de editor toont
  die van DS ([o2.md](o2.md), open punten).
- Prullenbak en meteen definitief verwijderen terwijl alleen een deelnemer bewerkt: of er een
  herstelkopie komt, hangt af van of de ronde (elke 30 s) ertussen viel ([o2.md](o2.md),
  afwijking 43).
- Na een herstart van de app kan een lege sessie tot 2 minuten later dicht gaan (de telling van
  de ronde staat in het geheugen); zolang geeft openen `office_busy` ([o2.md](o2.md), open
  punten).
- Een crash precies tussen het maken van een herstelkopie en het onthouden ervan kan een tweede
  kopie geven ([o2.md](o2.md), afwijking 39).
- Met DS weg toont de web-app het menu Nieuw niet, al kan de API het wel ([o4.md](o4.md), open
  problemen).
- 2FA verplicht maken of een deling intrekken sluit de editor binnen 15 tot 30 s, niet meteen
  zoals uitloggen ([o1.md](o1.md), open punten).

Voorbeelden en PDF:

- Een docx met alleen pagina-einden en meer dan ±75.000 pagina's laat x2t bij 2 GB stoppen (OOM);
  de invoercontrole laat er ±97.000 toe. DS blijft draaien, het document wordt `failed`
  ([metingen.md](metingen.md#xml-grens-bij-2-gb-nee-door-de-docx-met-heel-veel-paginas)).
- DS onthoudt een conversie per key: een mislukte conversie van een onveranderd bestand mislukt
  meteen weer, tot Mislukte opnieuw op `/beheer` ([o3.md](o3.md), afwijking 16;
  [metingen.md](metingen.md#xml-grens-bij-2-gb-nee-door-de-docx-met-heel-veel-paginas)).
- De viewer kijkt ±10 minuten of de PDF klaar is en stopt dan; daarna pas bij opnieuw openen
  ([o3.md](o3.md), open problemen).
- Sommige fouten heten in de viewer "(code 0)": de foutcode van DS heeft geen eigen kolom
  ([o3.md](o3.md), open problemen, schemawens).
- Kon de invoercontrole het bestand even niet bekijken, dan geeft openen in de editor die ene
  keer 422 `office_unsafe` in plaats van 503 ([o3.md](o3.md), open problemen).
- Een kleine conversie duurt via de app ±1,1 s, waarvan ±0,5 s wachten op de vaste vraag elke
  seconde ([metingen.md](metingen.md#conversie-klein--401-paginas-ja)).

Beheer:

- Eén mislukte `/healthcheck` zet DS tot 30 s op down; zolang opent niemand een editor
  ([metingen.md](metingen.md#bevindingen), bevinding 5).
- Niet opgeslagen toont ook even een document dat het herstel zo zelf opslaat (hooguit 10
  minuten) ([o5.md](o5.md), open problemen).
- Nu tussentijds opslaan wacht hooguit 10 s; met veel open documenten meldt hij "x van y"
  ([o5.md](o5.md), open problemen).
- Uit en binnen een minuut weer aan tijdens een inhaalronde: wat in die tijd binnenkwam, krijgt
  zijn PDF later ([o5.md](o5.md), open problemen).
- Direct na Opslaan op het tabblad kan de rest van de app nog tot een minuut de oude stand
  hebben, als er net een vraag onderweg was ([o5.md](o5.md), open problemen).
- Office uit op `/beheer` terwijl DS ons token weigert: dan is "Downloaden als PDF" weg, ook voor
  een bewaarde PDF ([o5.md](o5.md), afwijking 19).

Uitrol en DS:

- DS start via een eigen `entrypoint` die eerst een oud pid-bestand van supervisord weghaalt
  (anders kwam DS na een herstart van dezelfde container soms niet meer op). Bij een nieuwe DS
  moet het entrypoint van het image nog hetzelfde zijn; het
  [runbook](../runbooks/office.md#ds-bijwerken) zegt hoe je dat ziet, en
  [wat te doen als DS toch niet opkomt](../runbooks/office.md#ds-komt-na-een-herstart-niet-meer-op)
  ([o6.md](o6.md), afwijking 24; [metingen.md](metingen.md#bevindingen), bevinding 7).
- Stopte je DS zelf en is er bij een nieuw image te weinig ruimte, dan is office die run uit
  ([o6.md](o6.md), open problemen).
- Na een herstart van de machine wacht bewerken 2 à 3 minuten op de fonts van DS
  (`GENERATE_FONTS` blijft aan) ([o6.md](o6.md), open problemen).
- De versie van DS staat op meer plaatsen in de code; het runbook noemt ze allemaal voor wie DS
  bijwerkt ([o6.md](o6.md), open problemen).
- `/docbuilder` gaf 403; de oorzaak is niet zeker, dus het staat niet als bewijs in een test
  ([o6.md](o6.md), open problemen).

Techniek en grootte:

- Wie schrijfrecht heeft, kan met heel veel kleine frames de app bezighouden; er is geen grens
  op frames per seconde ([o1.md](o1.md), open punten).
- De poort zonder key leest alle sessies die niet dicht zijn; bij veel open sessies is een index
  op de leden beter ([o1.md](o1.md), open punten).
- Een callback van status 1 met iemand die eruit gezet is, wordt alleen gelogd; de ronde stopt
  de sessie pas bij de volgende ronde ([o1.md](o1.md), open punten).
- De route Bestanden groeit +2,94 KB (doel < 1 KB); de harde grens van 45 KB houdt
  ([metingen.md](metingen.md#budget-eerste-lading-en-editor-ja-bestanden-nee)).
- De cpu van de app bij koud openen is krap bij docx (0,94 core-seconde, één keer 1,12; doel 1)
  ([metingen.md](metingen.md#cpu-van-de-app-bij-koud-openen-ja-maar-krap-bij-docx)).
- Elke run van de installatietest laat ±0,85 GB build-cache achter ([integratie.md](integratie.md#de-build-cache)).

Nog na te gaan:

- Op Jonas' LXC: `docker info` (driver en grenzen), het image van Docker Hub op digest, het
  interne netwerk, `ss -Hltn`, de eerste start van DS en één sessie met twee gebruikers
  ([integratie.md](integratie.md#alleen-op-jonas-lxc-na-te-gaan); runbook, "Controle van de LXC").
- Andere browsers dan Chromium en Chrome, een echt certificaat via internet, en de
  schermafdrukken als artefact van een geslaagde CI-run ([klaar.md](klaar.md#wat-de-sandbox-niet-bewijst)).
