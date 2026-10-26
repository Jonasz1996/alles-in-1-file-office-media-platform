# Fase 4 — Office

Status: **in aanbouw** (bouwontwerp revisie 2, 26 oktober 2026). Van O0 staan de API-kant
(migratie 0007, de contracten, de kern van proxy en tunnel, de invoercontrole, de gezondheid
en de nep-DS) en de web-kant (de haken, de route `/bewerken/<id>` als stub, het tabblad
Office) erin. De spike, de e2e met de nep-DS en de job `office` in CI volgen. Zonder
office-instellingen werkt de app precies als in fase 3.

## Doel

Documenten bekijken, maken en samen bewerken, in de browser, via dezelfde poorten 80 en 443 als
de rest van het platform. Uit het stappenplan:

- docx, xlsx en pptx bewerken, ook met meerdere mensen tegelijk;
- opslaan maakt een nieuwe versie;
- een voorbeeld met opmaak (PDF-pagina's) zonder de editor te openen, en downloaden als PDF;
- een nieuw document, werkblad of presentatie maken;
- de verbindingen tellen en bewaken, en Collabora als terugval klaarzetten.

Klaar als: twee gebruikers bewerken tegelijk één docx; opslaan maakt een nieuwe versie;
office-preview werkt zonder de editor te openen.

## De editor: nog open bij Jonas

De keuze ligt op een open beslissingskaart (Vraag 1 in het ontwerp).

- **Standaard: OnlyOffice Docs Community 10.0.0.1**, vastgepind op digest. Het ontwerp gaat
  hiervan uit. Het draait als **optionele tweede container** naast de app. Zonder die container
  werkt alles zoals in fase 3; alleen bewerken kan dan niet, en voorbeelden komen uit de eigen
  lezer (B21).
- **Collabora** blijft de terugval. Sessies, opslaan, versies, toegang en voorbeelden zitten in
  een kern die OnlyOffice niet kent. Alleen een kleine adapter spreekt het protocol van
  OnlyOffice. Omschakelen kost dan ±1 week.
- **Zelf bouwen** valt buiten deze fase: samen bewerken in docx, xlsx en pptx is een product op
  zich.

Twee dingen uit het stappenplan gelden niet meer voor OnlyOffice 10.0: de limiet van 20
verbindingen (er is geen limiet meer; we tellen zelf en hebben een eigen grens), en een eigen
Postgres, Redis en RabbitMQ (Community draait als één proces).

## Wat er komt

- **O0 — Fundament en spike:** migratie 0007, contracten, de kern van proxy en tunnel, de
  invoercontrole, de gezondheid van de editor, een nep-editor voor de tests, en de echte editor
  in CI vanaf de eerste dag.
- **O1 — Toegang:** een eigen proxy en WebSocket-tunnel op `/office/`. De app beslist bij elk
  verzoek naar de editor wie het is en of die nu nog mag bewerken.
- **O2 — Bewerken:** sessies, opslaan als nieuwe versie, tussentijds opslaan, herstel van
  niet-opgeslagen werk, en de editorpagina `/bewerken/<id>`.
- **O3 — Voorbeelden en PDF:** de conversie van de editor voor pagina's, thumbnails en
  "Downloaden als PDF", met de eigen lezer als terugval.
- **O4 — Nieuw document:** eigen lege docx, xlsx en pptx.
- **O5 — Beheer:** het tabblad Office op `/beheer`, met meldingen.
- **O6 — Uitrol:** office aan met hetzelfde ene `install.sh`, uit met `OFFICE=uit`, bijwerken en
  terugrollen zonder dat het platform breekt.

Planning: ±11 werkdagen, één dag meer dan de twee weken van het stappenplan. Het ontwerp zegt wat
er als eerste schuift als het uitloopt.

## Vragen voor Jonas

1. Welke editor? Standaard OnlyOffice.
2. Office meteen aan bij installeren en bijwerken? Standaard ja (±1 GB downloaden, ±4 GB schijf).
3. Het restrisico van de editor op hetzelfde adres als de app accepteren? Standaard ja, met de
   beperkingen uit het ontwerp en later een eigen hostnaam (fase 8).
4. Hoeveel versies maakt de editor? Standaard één per bewerksessie, plus één per 30 minuten, en
   hooguit 10 per sessie.
5. De Collabora-adapter nu bouwen? Standaard nee, alleen klaarzetten.

## Documenten

- [bouwontwerp.md](bouwontwerp.md): het ontwerp (revisie 2, leidend); revisie 1 in
  [bouwontwerp-r1.md](bouwontwerp-r1.md). De keuzes staan als voorstel in B31–B38 in
  [beslissingen](../fase-0/beslissingen.md).
