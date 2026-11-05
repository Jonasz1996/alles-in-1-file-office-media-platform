# Toetsenbord — O0 (de haken van office in het web)

O0 zet alleen de haken: de knoppen en sneltoetsen die er zijn zodra office ingesteld is. Wat ze
doen, maken O2 (de editor), O3 (de PDF), O4 (Nieuw document) en O5 (het tabblad) af. Zonder
office-instellingen (of als `GET /api/v1/office/status` niet antwoordt) is er niets van, en
gedragen alle toetsen zich precies als in fase 3: E, O en Alt+7 vangt de app dan niet. *Mod* is
Ctrl, op een Mac ⌘. Ctrl+N, Ctrl+T en Ctrl+W gebruikt de app nooit.

## Bestanden (scope `browser`)

| Toets | Wat | Wanneer |
|---|---|---|
| E | het bestand met de focus (of de ene selectie) openen in de editor (`/bewerken/<id>`) | één docx, xlsx of pptx met schrijfrecht; kan het even niet (te groot, in de prullenbak, editor onbereikbaar), dan staat Bewerken uitgeschakeld met de reden en toont E die reden als melding (zoals elke uitgeschakelde actie) |
| O | het menu Nieuw openen, focus op het eerste item | alleen als het menu er is: office ingesteld, je mag in de map schrijven en `office/newItems.ts` geeft items (in O0 nog geen, dus blijft de knop "Nieuwe map" van fase 3) |
| N | nieuwe map (zoals in fase 3) | ongewijzigd, ook met het menu Nieuw |

- Bewerken staat in het contextmenu en het menu ⋯ direct na Openen (groep openen), en
  Downloaden als PDF in de groep omzetten, vlak voor de prullenbak. Een uitgeschakeld item heeft
  zijn reden als tekst, zoals de andere acties.
- De menuknop Nieuw (als hij er is): Enter of Spatie opent het menu met de focus op het eerste
  item, ↓ ook, ↑ met de focus op het laatste. In het menu: ↑ ↓, Home, End, Enter; Escape sluit
  en zet de focus terug op de knop. `aria-haspopup="menu"`, `aria-expanded` en
  `aria-keyshortcuts="O"` staan op de knop.
- Het menu van de map (de menuknop ⋯ van de map, met Tab bereikbaar, of rechtsklikken op de lege
  lijst) heeft dan het submenu Nieuw met dezelfde items; → opent het, ← sluit het.

## De viewer (scope `viewer`)

| Toets | Wat | Wanneer |
|---|---|---|
| E | het bestand in de editor openen | dezelfde regels als in Bestanden; alleen ingelogd (nooit via een publieke link) en nooit bij een eerdere versie. Kan het even niet, dan zegt de viewer de reden in zijn foutregel |

- Bewerken staat in de werkbalk vóór Downloaden, Downloaden als PDF erna (in O0 nog nooit
  zichtbaar). Met Tab bereikbaar zoals de andere knoppen; een knop die even niet kan, heeft
  `aria-disabled` en de reden als `title`.
- E staat alleen in de hulp (`?`) als Bewerken er is.

## /bewerken/<id> (de editorpagina)

In O0 een stub: met office ingesteld de kop "Bewerken", de tekst dat de editor er nog niet is
en de knop Terug (Tab, Enter). Zonder office de pagina "niet gevonden" van de schil, zoals
elk onbekend adres in fase 3. De kopbalk, Sluiten, Nu opslaan en Alt+Shift+Q komen met O2.

## /beheer (scope `admin`)

| Toets | Wat | Wanneer |
|---|---|---|
| Alt+7 | naar het tabblad Office (`/beheer/office`) | alleen als office ingesteld is (ook uitgezet of onbereikbaar); anders vangt de app Alt+7 niet en is `/beheer/office` het overzicht |

Alt+1 … Alt+6 blijven Overzicht, Gebruikers, Opslag, Taken, Groepen en Beveiliging. De inhoud
van het tabblad is van O5; in O0 alleen de tekst dat het overzicht later komt.

## Nagelopen

- [x] Zonder office: geen Bewerken, geen PDF, de knop "Nieuwe map", geen zevende tabblad; E, O
      en Alt+7 doen niets (de bestaande e2e van fase 3 slagen ongewijzigd).
- [x] De sneltoetsen E (browser en viewer), O (browser) en Alt+7 (admin) botsen met niets in hun
      scope (`browser/actions.test.ts`: E alleen voor Bewerken; de test "elke actie met een
      sneltoets…" blijft groen).
- [x] Elke tekst van de haken via `t()`, in het Nederlands en het Engels (`check:i18n --strict`).
- [x] Met office ingesteld en met alleen het toetsenbord: E en Alt+7 in de echte app. Nagelopen
      bij het samenvoegen van O0 (de API en het web samen), met de nep-DS als proces en
      `PLATFORM_OFFICE_DEV_UNGATED=1`, in een tijdelijke rooktest (niet in het repo): de status
      zegt `ok`; Bewerken staat bij een docx en niet bij een txt; E in de lijst en E in de viewer
      openen `/bewerken/<id>`; Terug is met Tab en Enter te bereiken; het tabblad Office staat er
      en Alt+7 gaat erheen.
- [x] O (het menu Nieuw) met alleen het toetsenbord: sinds O4 geeft `office/newItems.ts` de items
      Document, Werkblad en Presentatie. Bewezen in de e2e met de nep-DS,
      `e2e/test_office_o4.py::test_nieuw_document_met_alleen_het_toetsenbord`: O opent het menu
      met de focus op Map, ↓ naar Document en Enter maakt "Nieuw document.docx" en opent de
      editor; in het Engels Tab naar de knop New, ↑ opent het menu onderaan, Enter maakt "New
      spreadsheet.xlsx", en Escape zet de focus terug op de knop. Het submenu Nieuw in het menu
      van de map (→, End, Enter) in `::test_nieuw_via_het_contextmenu_van_de_map`, dat ook
      bewijst dat O met alleen leesrecht niets doet. Met de echte DS: O, ↓ en Enter in
      `test_office_o4_real.py` (de checklist staat in [toetsenbord-o4.md](toetsenbord-o4.md)).
