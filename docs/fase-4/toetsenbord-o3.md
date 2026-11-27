# Toetsenbord — O3 (voorbeelden en PDF)

O3 voegt geen sneltoetsen toe. Alles wat het maakt, is een gewone knop of een menu-item dat met
Tab (of de pijltjes in een menu) te bereiken is en met Enter of Spatie werkt. Zonder
office-instellingen is er niets van, en gedraagt de viewer zich precies als in fase 3. Ctrl+N,
Ctrl+T en Ctrl+W gebruikt de app nooit.

## Bestanden (scope `browser`)

| Waar | Wat | Wanneer |
|---|---|---|
| contextmenu (Shift+F10 of de menutoets) en het menu ⋯ | Downloaden als PDF, in de groep omzetten vlak voor de prullenbak | één bestand dat de editor omzet (docx, xlsx, pptx, odt, ods, odp, doc, xls, ppt, rtf en de m-varianten), met leesrecht, office aan. In de prullenbak uitgeschakeld met de reden; Enter op het item zegt die reden als melding, zoals bij elke uitgeschakelde actie |

- Duurt het langer dan anderhalve seconde (de server zet de PDF eerst om, hooguit een minuut),
  dan verschijnt de melding "De PDF wordt gemaakt…". Die wordt ook voorgelezen (de vaste
  live-regio) en verdwijnt vanzelf zodra de download begint of een fout komt.
- Een fout (te vaak gevraagd, onbereikbaar, kon niet worden omgezet) komt als foutmelding in het
  meldingengebied; de focus blijft in de lijst.

## De viewer (scope `viewer`)

| Waar | Wat | Wanneer |
|---|---|---|
| werkbalk, na Downloaden | Downloaden als PDF | dezelfde regels als in Bestanden; alleen ingelogd, nooit bij een eerdere versie. Kan het even niet, dan zegt de viewer de reden in zijn foutregel |
| boven de eenvoudige weergave | de knop "Voorbeeld met opmaak maken" | `pdf_state` is `ask` (een upload via een uploadlink, een eerdere versie of een groot bestand) en je bent ingelogd; nooit via een publieke link |

- Een omgezet document toont de echte pagina's met dezelfde toetsen als een PDF (PageDown,
  PageUp, + en −, 0 voor de paginabreedte, T voor de inhoudsopgave als die er is). Vooraan in de werkbalk van de pagina's staat het
  label "Voorbeeld"; de uitleg ("Gemaakt door de editor…") staat er ook als tekst voor
  schermlezers, niet alleen als `title`.
- Zolang de PDF gemaakt wordt, staat de eenvoudige weergave er met de melding "Voorbeeld met
  opmaak wordt gemaakt…" (`role="status"`, dus voorgelezen). De viewer kijkt zelf opnieuw (eerst
  na 2 s, dan steeds minder vaak, hooguit ruim tien minuten) en wisselt dan naar de pagina's.
  Er is niets te bedienen; Sluiten en weer openen kijkt meteen opnieuw.
- De knop "Voorbeeld met opmaak maken" zit met Tab bereikbaar in de inhoud van de viewer. Na
  Enter is hij bezig (`aria-busy`), dan komt de melding "wordt gemaakt…". Een fout staat eronder
  als `role="alert"`.

## Gecontroleerd in tests

- `web/src/office/pdf.test.ts`: wanneer de actie er is (ook met leesrecht, ook als de editor
  even weg is, niet voor een map, een album of een eerdere versie, uitgeschakeld in de
  prullenbak), de naam van de PDF, en de melding bij een trage conversie (met nep-klok).
- `web/src/viewer/converted.test.ts`: wat de viewer toont (pagina's, wachten, de knop alleen
  ingelogd) en hoe vaak hij opnieuw kijkt.
- `e2e/test_office_o3.py` (nep-DS): het voorbeeld in de viewer zonder de editor te openen, de
  knop "Voorbeeld met opmaak maken" met alleen het toetsenbord (Tab, Enter) bij een upload via een
  uploadlink, en de publieke link zonder Bewerken.

## Nagelopen

- [x] Zonder office: geen PDF-actie, geen label, geen knop; de viewer van fase 3
      (`api/tests/test_viewer.py` en de e2e van fase 3 ongewijzigd groen).
- [x] Elke tekst via `t()` in het gebied `viewer` (blok `converted`, buiten het bevroren blok
      `office`), in het Nederlands en het Engels (`check:i18n --strict`).
- [x] Geen nieuwe sneltoets: niets botst met de bestaande toetsen van `browser` en `viewer`.
- [x] Met alleen het toetsenbord in de echte app (e2e met de nep-DS): Tab naar "Voorbeeld met
      opmaak maken", Enter, de pagina's verschijnen; de werkbalk van de pagina's werkt zoals bij
      een PDF.
