# Toetsenbord — M2 (bibliotheken en de pagina's onder /media)

M2 vult de pagina's `/media`, `/media/bibliotheek/{id}`, `/media/item/{node}` en
`/media/serie/{id}`, en de dialoog "Als bibliotheek gebruiken" achter de haak van M0. Er komt
geen nieuwe sneltoets bij: alles is een link, een knop, een keuzelijst of een raster met één
tabstop. Ctrl+N, Ctrl+T en Ctrl+W gebruikt de app nooit. *Mod* is Ctrl, op een Mac ⌘.

## Bestanden: "Als bibliotheek gebruiken"

| Toets | Wat |
|---|---|
| Shift+F10 of de menutoets | het contextmenu bij de map met de focus; daarin "Als bibliotheek gebruiken" (↑ ↓, Enter) |
| — | ook in het menu ⋯ van de map |

De dialoog (`ui/Dialog`, scope `dialog`):

- bij het openen staat de focus op het gekozen soort (een voorstel uit de naam van de map:
  "Series" → Series, "Muziek" → Muziek, "Video's" → Video's, anders Films);
- ↑ ↓ (en ← →) kiezen een ander soort, zoals bij elke groep keuzerondjes;
- Tab naar het veld Naam (leeg: de naam van de map), dan Bibliotheek maken; Enter in het veld
  verstuurt;
- Escape of Annuleren sluit; de focus staat daarna weer op de rij van de map;
- een fout van de server (bijvoorbeeld "ligt binnen een andere bibliotheek") staat in de dialoog
  als `role="alert"`, en de dialoog blijft open;
- na het maken: een melding "Bibliotheek … gemaakt. De scan loopt." met de knop Openen (die
  bereik je met Tab, zoals elke melding met een actie).

## /media

- De kop krijgt na het navigeren de focus (zoals elke pagina).
- Nieuwe bibliotheek (een knop in de kop; niet voor gasten) opent de mappenkiezer van Bestanden
  (`FolderPicker`): de focus staat op de eerste map, ↑ ↓ Home End en typen kiezen een map, Enter
  opent haar, en zonder mappen erin staat de focus op "Deze map gebruiken". Een map van een
  ander of met al een bibliotheek kan niet (de knop is uit, de reden staat eronder). Daarna
  dezelfde dialoog als hierboven; na het maken of Escape staat de focus weer op Nieuwe
  bibliotheek (de knop staat altijd op dezelfde plek, ook als de lege staat verdwijnt).
- Verder kijken, Volgende aflevering en Net toegevoegd zijn elk een rij met één tabstop: ← en →
  verplaatsen de focus naar de vorige of volgende kaart, Home en End naar de eerste en de
  laatste; Enter opent de kaart. De rij scrolt mee.
- De bibliotheken zijn gewone links (Tab, Enter).
- Zonder bibliotheken: de uitleg en de link Naar Bestanden.

## /media/bibliotheek/{id}

| Toets | Wat |
|---|---|
| Tab | Media, Opnieuw scannen (alleen de eigenaar), Sorteren op, Tonen (als de bibliotheek meer soorten heeft), dan het raster |
| ← → ↑ ↓ | in het raster: de vorige of volgende kaart, een rij omhoog of omlaag |
| Home, End | de eerste of de laatste kaart die geladen is |
| PageUp, PageDown | een scherm omhoog of omlaag |
| Enter | de kaart openen (een film of video: de pagina van het item; een serie: de serie) |

- Het raster is per rij gevirtualiseerd (zoals de fototijdlijn); de kaart met de focus blijft
  altijd gerenderd, ook als hij buiten beeld scrolt. Met ↓ naar het eind laden de volgende
  pagina's vanzelf.
- Sorteren en Tonen zijn gewone keuzelijsten; een andere keuze vervangt het adres (`?sort=`,
  `?type=`), zodat Terug naar de vorige pagina gaat en niet naar de vorige sortering.
- De staat van de scan ("Bezig met scannen") staat in een `role="status"`.

## /media/item/{node}

- Tab: Bibliotheek, Naar de serie (bij een aflevering), Afspelen, Verder op 12:34 (als je half
  bent), de delen, de hoofdstukken (elk een link die op die tijd begint).
- Enter op Afspelen of Verder op opent `/kijken/{node}` (met `?t=`); de toetsen daar zijn van M6.
- "Kan niet afspelen: …" staat in een `role="status"`.

## /media/serie/{id}

- Tab: Bibliotheek, Afspelen S01E01 of Verder met S01E03, dan elke aflevering per seizoen (een
  link per aflevering, met de code en de titel).

## Nagelopen

- [x] Geen nieuwe sneltoets; geen botsing met de scopes `global`, `browser` of `dialog`.
- [x] Elke actie is te bereiken met Tab en te doen met Enter of Spatie: de e2e
      `test_media_m2.py` doet het hele rondje (map → dialoog → /media → bibliotheek → item →
      Afspelen) alleen met het toetsenbord, en ook Nieuwe bibliotheek (kiezer → map → dialoog →
      de focus terug op de knop).
- [x] Het raster heeft één tabstop; de pijltjes, Home, End, PageUp en PageDown
      (`ui/virtual.useRovingFocus`), getoetst in de e2e.
- [x] De dialoog volgt `ui/Dialog`: de focus erin, Escape sluit, de focus terug.
- [x] Elke tekst via `t()`, in het Nederlands en het Engels (`check:i18n --strict`).
