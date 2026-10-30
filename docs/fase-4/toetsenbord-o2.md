# Toetsenbord — O2 (de editorpagina)

O2 maakt de editorpagina `/bewerken/<id>` af (Keuze 32). E in Bestanden en in de viewer (de
haken van O0) openen haar; wat daar gebeurt, staat hieronder. Toetsen in de iframe gaan naar de
editor van DS: daarom staat er een kopbalk boven de iframe die je altijd met het toetsenbord
bereikt. *Mod* is Ctrl, op een Mac ⌘. Ctrl+N, Ctrl+T en Ctrl+W gebruikt de app nooit, ook hier
niet.

## Openen (scopes `browser` en `viewer`, van O0)

| Toets | Wat | Wanneer |
|---|---|---|
| E | het bestand met de focus openen in de editor | zoals in `toetsenbord-o0.md`: één docx, xlsx of pptx met schrijfrecht |
| Enter | op het menu-item of de knop Bewerken: hetzelfde | |

## /bewerken/<id> (scope `editor`)

| Toets | Wat | Wanneer |
|---|---|---|
| Alt+Shift+Q | de focus uit de iframe naar Sluiten in de kopbalk | altijd: in de iframe (de pagina luistert in het venster van de iframe, dezelfde origin) en op de pagina zelf (de sneltoets van scope `editor`, ook vanuit een invoerveld) |
| Tab / Shift+Tab | door de kopbalk: Sluiten, Nu opslaan, de knoppen van de meldingen, dan de iframe | Sluiten is altijd de eerste; de `<h1>` en de statusregel zijn niet tabbaar |
| Enter of Spatie | op Sluiten: terug naar waar je vandaan kwam; zonder stap terug (een link die je kreeg) naar de map van het bestand, of naar Gedeeld als de map niet van jou is | altijd, ook terwijl de editor nog opent |
| Enter of Spatie | op Nu opslaan: tussentijds opslaan (status 6, een versie erbij) | alleen als de editor klaar is; tot dan is de knop uitgeschakeld. Tijdens het opslaan zegt de knop "Opslaan…" tot de nieuwe versie er is (hooguit 10 s) |
| Enter of Spatie | op Opnieuw laden (bij een melding): de pagina opnieuw laden | bij een melding die dat nodig heeft (de editor niet bereikbaar, kwam niet klaar, meldt een fout, of de verbinding is weg) |
| de sluitknop van DS | hetzelfde als Sluiten (`onRequestClose`) | in de iframe, met de toetsen van DS |
| Alt+← (Terug in de browser) | de editor sluiten | zoals elke pagina |

- De iframe heeft de titel "Editor: <naam>" (Engels "Editor: <name>") en krijgt de focus zodra
  het document klaar is (`onDocumentReady`).
- De statusregel (`role="status"`) zegt "Verbinden…", "Bewerken", "Bewerken met 2 anderen",
  "Opgeslagen om 10:42", "Verbinding weg" of "De editor is gesloten". Een schermlezer leest
  hem voor zonder de focus te verplaatsen.
- Meldingen staan onder de kopbalk: een melding die vanzelf overgaat (een nieuwe versie van het
  bestand, de editor is bezet) met `role="status"`, de rest met `role="alert"`.
- `?` (met de focus op de pagina, niet in de iframe) toont de hulp met Alt+Shift+Q erin, onder
  "Deze pagina": "Alt+Shift+Q: van de editor naar deze balk".

## Meldingen buiten de editor (overal)

`office.unsaved` (definitief) en `office.recovered` worden een melding rechtsonder die blijft
staan tot je hem sluit. Bij `office.recovered` staat er de knop Openen: met Tab bereikbaar,
Enter opent de kopie in haar map. Sluiten van de melding gaat zoals bij elke melding van de app.

## Nagelopen

- [x] Elke tekst van de editorpagina en de meldingen via `t()`, in het Nederlands en het Engels
      (`npm run check:i18n -- --strict`: 0 bevindingen).
- [x] Alt+Shift+Q is de enige sneltoets van O2; hij botst met niets in scope `editor` en DS
      gebruikt hem niet (spike stap 6). Geen Ctrl+N, Ctrl+T of Ctrl+W. `editor.test.ts` toetst
      `isLeaveKey`, ook de Mac-variant waarin Alt+Shift+Q "Œ" geeft.
- [x] Sluiten staat vóór Nu opslaan in de DOM, dus eerst in de tabvolgorde; Nu opslaan is
      uitgeschakeld tot het document klaar is (`editor.test.ts`, en de e2e met de nep-DS).
- [x] Met alleen het toetsenbord tegen de nep-DS
      (`e2e/test_office_o2.py::test_toetsenbord_openen_opslaan_en_sluiten`): Tab naar het
      bestand in Bestanden en E opent `/bewerken/<id>`; de iframe heeft de titel "Editor:
      verslag.docx" en krijgt de focus; Alt+Shift+Q in de iframe zet de focus op Sluiten; Tab
      naar Nu opslaan en Enter geeft een versie erbij en "Opgeslagen om …"; de focus blijft op
      Nu opslaan; Shift+Tab naar Sluiten; Alt+Shift+Q ook vanaf de pagina zelf; Enter op Sluiten
      gaat terug naar de map. Daarna alles nog een keer in het Engels (Close, Save now).
- [x] De meldingen in de pagina met de nep-DS: "Je hebt geen toegang meer" (`role="alert"`),
      "De verbinding met de editor is weg", en bij DS weg de fout met de knop Opnieuw laden
      (`test_deling_ingetrokken_…`, `test_app_weg_…`, `test_ds_weg_en_weer_terug`).
- [ ] Dezelfde toetsenronde met de echte DS: niet als vaste test. `test_office_o2_real.py`
      opent via de knop Bewerken en drukt Nu opslaan en Sluiten met de muis. Dat DS
      Alt+Shift+Q niet zelf gebruikt, is in de spike nagelopen (stap 6).
