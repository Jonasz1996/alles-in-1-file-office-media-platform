# Toetsenbord — O4 (nieuw document)

O4 vult het menu Nieuw van O0 met Document, Werkblad en Presentatie. Het voegt geen sneltoets
toe: de knop Nieuw, de sneltoets O en het submenu Nieuw in het menu van de map zijn van O0
(`toetsenbord-o0.md`), de editorpagina is van O2 (`toetsenbord-o2.md`). Het menu is er alleen
als office aan staat, de editor nu bereikbaar is (`status.editing`) en je in de map mag
schrijven; anders is de werkbalk precies die van fase 3, met de knop "Nieuwe map". Ctrl+N,
Ctrl+T en Ctrl+W gebruikt de app nooit.

## Bestanden (scope `browser`)

| Toets | Wat | Wanneer |
|---|---|---|
| O | het menu Nieuw openen, met de focus op Map | het menu is er (zie boven) en de focus staat niet in een invoerveld |
| Enter, Spatie of ↓ | op de knop Nieuw: het menu openen, met de focus op Map | idem; de knop is met Tab te bereiken in de werkbalk |
| ↑ | op de knop Nieuw: het menu openen, met de focus op Presentatie | idem |
| ↑ ↓, Home, End | in het menu: naar Map, Document, Werkblad of Presentatie | |
| Enter | op Document, Werkblad of Presentatie: het lege bestand maken en openen in de editor (`/bewerken/<id>`) | |
| Enter | op Map: een nieuwe map, zoals N in fase 3 | |
| Escape | het menu sluiten zonder iets te maken; de focus gaat terug naar de knop Nieuw | |
| N | nieuwe map (zoals in fase 3) | ongewijzigd |

- **Het menu van de map** (de knop "Meer acties" in de kop van de lijst, met Tab te bereiken, of
  in de rasterweergave in de werkbalk) heeft het submenu Nieuw met dezelfde items:
  - → op Nieuw opent het submenu, met de focus op Map;
  - ← sluit het weer;
  - verder dezelfde toetsen als hierboven.
- **Na Enter op een soort:**
  - het nieuwe bestand staat in de lijst, met de naam in jouw taal ("Nieuw document.docx", of
    "Nieuw document (1).docx" als die naam er al is);
  - een schermlezer hoort "Nieuw document.docx gemaakt";
  - de editorpagina opent. Daar krijgt de iframe de focus zodra het document klaar is
    (O2), met de titel "Editor: Nieuw document.docx".
- **Terug:** Alt+Shift+Q zet de focus op Sluiten; Enter gaat terug naar de map.
- **Lukt het niet** (quotum vol, de map is weg, alleen leesrecht), dan komt er een foutmelding
  in het meldingengebied, die ook wordt voorgelezen. Je blijft in de map.
- **Twee keer snel Enter** maakt één bestand.

## Gecontroleerd in tests

- `web/src/office/newItems.test.ts`:
  - wanneer de items er zijn;
  - de labels in het Nederlands en het Engels;
  - het maken, de melding voor de schermlezer en het openen;
  - de foutmelding;
  - twee keer snel kiezen.
- `e2e/test_office_o4.py` (nep-DS, alleen het toetsenbord):
  - **Nederlands:** O opent het menu met de focus op Map; ↓ naar Document; Enter maakt
    "Nieuw document.docx" en opent de editor, met de focus in de iframe. Alt+Shift+Q en Enter
    gaan terug naar de map.
  - **Engels:** Tab naar de knop New, ↑ opent het menu op Presentation, ↑ naar Spreadsheet,
    Enter maakt "New spreadsheet.xlsx". Escape sluit het menu en zet de focus terug op de knop.
  - **Het menu van de map:** Tab naar Meer acties, Enter, → opent het submenu Nieuw, End naar
    Presentatie, Enter maakt "Nieuwe presentatie (1).pptx".
  - **Alleen leesrecht:** geen knop Nieuw, en O doet niets.
- `e2e/test_office_o4_real.py` (echte DS): O, ↓ tot de soort en Enter openen de drie soorten
  in de echte editor.

## Nagelopen

- [x] Zonder office, met office uit of met de editor onbereikbaar: geen menu Nieuw, de knop
      "Nieuwe map" van fase 3; O doet niets (`newItems.test.ts`, en de haak van O0 in
      `Files.tsx`).
- [x] Elke tekst via `t()`: de labels en "{name} gemaakt" staan in het bevroren blok `office`
      van `browser.*`, in het Nederlands en het Engels (`check:i18n --strict`: 0 meldingen). O4
      voegt geen tekst toe.
- [x] Geen nieuwe sneltoets; O en N botsen met niets in scope `browser`. Geen Ctrl+N, Ctrl+T of
      Ctrl+W.
- [x] Met alleen het toetsenbord in de echte app (e2e met de nep-DS, in het Nederlands en het
      Engels): het menu Nieuw via O, via de knop en via het menu van de map, de editor open, en
      terug met Alt+Shift+Q en Enter.
- [x] Het open punt van `toetsenbord-o0.md` ("O (het menu Nieuw) met alleen het toetsenbord")
      is hiermee gedaan.
