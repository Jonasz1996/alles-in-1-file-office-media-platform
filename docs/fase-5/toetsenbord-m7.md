# Toetsenbord — M7 (Verder kijken en Volgende aflevering)

De rijen Verder kijken en Volgende aflevering op `/media` (`web/src/media/rows/`) zijn helemaal
met het toetsenbord te bedienen. De teksten staan in het gebied `media` (`media.progress.*`, NL en
EN). De e2e-tests `test_verder_kijken_met_het_toetsenbord` en
`test_volgende_aflevering_en_gezien_markeren` (`e2e/test_media_m7.py`) lopen deze lijst af; alleen
het binnenkomen in een rij gaat daar met `focus()` (één tabstop), zoals in de e2e van M2.

Geen enkele toets van de rijen gebruikt Ctrl, Alt of ⌘ (met Ctrl of ⌘ erbij doen ← en → niets),
dus **Ctrl+N, Ctrl+T en Ctrl+W** en de andere sneltoetsen van de browser en de app blijven werken.

## Een rij (Verder kijken, Volgende aflevering)

| Toets | Wat |
|---|---|
| Tab / Shift+Tab | de rij in en uit: één tabstop per rij (de kaart die het laatst de focus had) |
| ← / → | de vorige / volgende kaart |
| Home / End | de eerste / laatste kaart |
| Enter | Verder kijken: verder op de stand (`/kijken/{id}?t=…`); Volgende aflevering: afspelen (of verder, als hij al begonnen is); een kaart zonder bestand: de pagina |
| Shift+F10, de menutoets | het menu van de kaart (ook met rechtsklik of lang indrukken) |

Een schermlezer hoort per kaart de titel en de stand ("Heat, verder op 1:40") of de aflevering
("Breaking Bad, S01E02"). Een stand van een ander tabblad of apparaat werkt de kaart bij zonder
de focus te verplaatsen. Verdwijnt de kaart met de focus (gezien, hier of elders), dan gaat de
focus naar de kaart die nu op die plek staat (na Gezien markeren in Volgende aflevering: de
volgende aflevering), niet naar het begin van de pagina.

## Het menu van een kaart

Het menu heet naar de rij ("Verder kijken" of "Volgende aflevering"). De pijltjes gaan langs de
items, Enter kiest, Esc sluit het menu en zet de focus terug op de kaart.

| Item | Wat | Wanneer |
|---|---|---|
| Verder kijken vanaf 1:40 | `/kijken/{id}?t=100` | als er een stand is |
| Afspelen | `/kijken/{id}` van voren af | Volgende aflevering, of zonder stand |
| Naar het item | de pagina van het item | altijd |
| Naar de serie | de pagina van de serie | bij een aflevering |
| Gezien markeren | `POST /media/progress/batch`; een melding "… staat op gezien."; de kaart gaat uit de rij | altijd |

## Wat niet van M7 is

- De toetsen van de speler zelf (pauzeren, springen, 0 … 9): [toetsenbord-m6.md](toetsenbord-m6.md).
  Een stand van elders laat een **gepauzeerde** speler springen; dat vraagt geen toets.
- "Verder op 0:30" op de pagina van het item en de rij Net toegevoegd: [toetsenbord-m2.md](toetsenbord-m2.md).
