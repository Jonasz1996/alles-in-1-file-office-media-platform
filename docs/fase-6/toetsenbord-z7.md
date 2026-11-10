# Toetsenbord — Z7 (de tab "Zoeken en foto's" op /beheer)

De tab "Zoeken en foto's" op `/beheer/zoeken` (`web/src/admin/SearchTab.tsx`, Keuze 51) is
helemaal met het toetsenbord te bedienen. Er komt geen nieuwe sneltoets bij: Alt+9 zette Z0 al
neer (zie [toetsenbord-z0.md](toetsenbord-z0.md)); verder is alles een gewone knop in de volgorde
van de pagina. De teksten staan in het gebied `admin` (`admin.search.*`, NL en EN;
`web/src/admin/searchTab.test.ts` toetst dat elke stand in beide talen een tekst heeft, zonder
sleutel of `{naam}` in beeld).

De e2e in `e2e/test_zoek_beheer.py` lopen deze lijst af met alleen Tab, Enter en Esc (nergens een
klik):

- `test_de_tab_met_alleen_het_toetsenbord`: Alt+9, pauzeren met Enter, "Index herbouwen" eerst
  met Esc en dan met "Herbouwen", "Lijst van mislukte tonen", "Mislukte opnieuw", hervatten, en de
  twee knoppen van de foto's met Esc;
- `test_een_gewone_gebruiker_ziet_geen_tab_en_krijgt_403`: voor een gewone gebruiker doet Alt+9
  niets en vraagt de pagina niets aan `/admin/search`.

Geen enkele toets van de tab gebruikt Ctrl of ⌘: Mod+K (het zoekveld) en de sneltoetsen van de
browser blijven werken.

## Naar de tab

| Toets | Wat |
|---|---|
| Alt+9 | naar `/beheer/zoeken`, overal op `/beheer`. Altijd: zoeken kan niet uit, dus de tab staat er ook zonder Office en Media. De link in de rij tabbladen krijgt `aria-current="page"` |
| Tab / Shift+Tab, dan Enter | de tabbladen van `/beheer` zijn links in een `nav` (`ui/Tabs` met `href`, zoals in fase 3): Tab gaat erdoor, Enter opent er een. Pijltjes doen hier niets |

Een gewone gebruiker heeft geen Beheer in het menu; `/beheer/zoeken` zegt "Geen toegang" en
Alt+9 doet daar niets.

## Op de tab

De tab bestaat uit delen met elk een kop (`h3`), van boven naar beneden: Zoekmachine,
Achtergrondwerk, Index, Herbouwen (alleen tijdens een herbouw), Mislukte inhouden, Foto's: plek en
duplicaten, Het afgelopen uur, OCR, Plaatsnamen en Zoekbench. Daaronder de blokken Gezichten en
Zoeken op beeld (van X2 en X3; nu "komt later", zonder knoppen).

| Toets | Wat |
|---|---|
| Tab / Shift+Tab | door de knoppen, van boven naar beneden: "Achtergrondwerk pauzeren" (of "hervatten"), "Index herbouwen", "Mislukte opnieuw" en "Lijst van mislukte tonen" (alleen als er iets mislukte), "Meer laden" (als de lijst langer is), "Duplicaten opnieuw berekenen", "Plek, camera en datum opnieuw lezen", "OCR aanzetten" of "uitzetten" en "Alles opnieuw herkennen" (alleen als de worker OCR heeft, `ocr.installed`; golf 4) |
| Enter of spatie op een knop | de knop. "Index herbouwen", de twee knoppen van de foto's, "OCR aanzetten" en "Alles opnieuw herkennen" vragen eerst een bevestiging; "OCR uitzetten" niet |
| In de bevestiging | de focus staat eerst op Annuleren (de veilige keuze); Tab gaat naar "Herbouwen", "Starten" of "Aanzetten" en Enter bevestigt dan. Esc (of Enter op Annuleren) sluit zonder iets te doen en zonder aanroep; de focus gaat terug naar de knop |
| Enter op "Achtergrondwerk pauzeren" | de index en OCR staan stil (`PATCH`, `paused: true`); de knop heet nu "Achtergrondwerk hervatten" en heeft `aria-pressed="true"`. Nog eens Enter hervat |
| Enter op "Lijst van mislukte tonen" | de tabel met de sha256, de stap, de code en het tijdstip (nooit een naam of pad). De knop heet dan "Lijst verbergen" en heeft `aria-expanded="true"`; Enter klapt de lijst weer dicht |

Na een knop die iets deed, blijft de focus op die knop. Gaat de knop weg, dan gaat de focus naar
de kop van zijn deel, niet naar het begin van de pagina:

- "Index herbouwen" is weg zolang de herbouw loopt: de focus staat op de kop "Index";
- "Mislukte opnieuw" is weg als er niets meer mislukt is: de focus staat op de kop "Mislukte
  inhouden";
- de twee knoppen van de foto's zijn weg zolang hun herbouw loopt: de focus staat op de kop
  "Foto's: plek en duplicaten".

Staat de focus al ergens anders (je tabde verder terwijl de aanroep liep), dan blijft hij daar.

## Wat een schermlezer hoort

- Een gelukte actie is een toast ("Het achtergrondwerk staat op pauze.", "Het herbouwen is
  gestart.", "De mislukte staan weer in de rij."); een fout staat onder het deel als melding
  (`role="alert"`). Lukt het verversen niet, dan staat dat als melding boven de stand die er al
  was.
- De voortgang van een herbouw is `role="status"` ("De index: 40 %, sinds …"), met een
  voortgangsbalk "Voortgang van het herbouwen". De tekst uit bestanden heeft een eigen balk
  ("Tekst uit bestanden gelezen").
- De tab ververst elke 5 seconden zolang het tabblad zichtbaar is. Dat verplaatst de focus niet en
  leest niets voor, behalve de regio's hierboven als hun tekst verandert.
- Badges ("FTS5 (snel)", "LIKE (langzamer)", "loopt", "gepauzeerd") zijn gewone tekst, geen kleur
  alleen.

## Wat niet in de e2e zit

- "Meer laden" in de lijst van mislukte: die knop komt pas bij meer dan 50 mislukte inhouden (één
  bladzijde van `GET /admin/search/failed`); de e2e heeft er één. De knop is dezelfde `Button` met
  dezelfde focusregel; de bladzijden zelf toetst `api/tests/search/test_admin_search.py`.
- ~~"OCR aanzetten" en "uitzetten": de knop staat er pas als X1 zich aanmeldt (`ocr.installed`).
  Dezelfde bevestiging en focusregel als hierboven; de e2e met het toetsenbord komt bij X1
  (`e2e/test_zoek_ocr.py`).~~ Gedaan bij de samenvoeging (golf 4):
  `test_ocr_op_beheer_pauzeren_uitzetten_en_opnieuw_herkennen` in `e2e/test_zoek_ocr.py` (marker
  `ocr`, de job `ocr` van CI) doet het blok OCR met alleen het toetsenbord; zie
  [toetsenbord-x1.md](toetsenbord-x1.md).
- De herbouw van de foto's echt starten: de e2e stopt bij Esc in de bevestiging. Of de focus dan
  op de knop of op de kop landt, hangt af van hoe snel de herbouw klaar is (de boom van de test
  heeft weinig foto's); de regel is dezelfde als bij "Index herbouwen", die de e2e wel doet.

## Lijst

- [x] Alt+9 opent de tab; de link heeft `aria-current="page"`; de kop is "Zoeken en foto's".
- [x] Tab bereikt elke knop; Enter en spatie doen de knop.
- [x] Pauzeren en hervatten met Enter; `aria-pressed` volgt; de focus blijft op de knop.
- [x] "Index herbouwen": de bevestiging met de focus op Annuleren; Esc doet niets en zet de focus
      terug op de knop; Tab en Enter op "Herbouwen" starten; de focus gaat naar de kop "Index".
- [x] De voortgang van de herbouw is `role="status"` met een voortgangsbalk.
- [x] "Lijst van mislukte tonen" met `aria-expanded`; de lijst toont alleen de sha256.
- [x] "Mislukte opnieuw": daarna de focus op de kop "Mislukte inhouden".
- [x] De twee knoppen van de foto's: de bevestiging met de focus op Annuleren; Esc doet niets.
- [x] Het blok OCR (golf 4, in `e2e/test_zoek_ocr.py`): "OCR uitzetten" zonder vraag, de focus
      blijft op de knop (`aria-pressed` volgt); "OCR aanzetten" en "Alles opnieuw herkennen" met
      de bevestiging, Esc doet niets en zet de focus terug op de knop.
- [x] Een gewone gebruiker: geen tab, Alt+9 doet niets, geen aanroep van `/admin/search`.
- [x] Nergens een fout in de console, een CSP-melding of een ontbrekende tekst (de bewaker van
      de e2e).
