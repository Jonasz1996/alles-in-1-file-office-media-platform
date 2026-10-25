# Fase 3 — Toetsenbord van de viewer (P4)

Alle sneltoetsen van de viewer staan in de scope `viewer` (`useShortcuts(…, { scope: "viewer" })`).
De viewer zet bij het openen eerst `dialog` en daarbovenop `viewer` op de stapel (zoals
`ui/Dialog`), dus alles van de pagina eronder ligt stil zolang hij open is. `?` toont deze
lijst in de app (de hulp toont wat op dat moment werkt).

Toetsen zonder Ctrl/Cmd werken niet als de focus in een invoerveld of keuzelijst staat (het
paginavak van een PDF, de keuze van de tekencodering) en nemen een pijltje of spatie niet af
van een element dat die zelf gebruikt (een speler met bediening, een knop): dat regelt
`ui/keys` (`consumesKey`).

## Overal in de viewer

| Toets | Wat |
|---|---|
| Esc | Viewer sluiten (terug naar de map; één keer) |
| ← / → | Vorig / volgend bestand (bij een afbeelding alleen als hij passend staat) |
| Shift+← / Shift+→ | Vorig / volgend bestand, ook als de afbeelding is ingezoomd |
| Home / End | Eerste / laatste bestand (bij een afbeelding alleen als hij passend staat) |
| F | Volledig scherm aan of uit |
| I | Paneel met gegevens tonen of verbergen |
| D | Downloaden |
| ? | De sneltoetsen tonen |
| Tab / Shift+Tab | Door de knoppen van de werkbalk (Vorige, Volgende, Downloaden, Gegevens, Openen in nieuw tabblad, Volledig scherm, Sluiten) en van de weergave; Enter of spatie drukt de knop |

Aan het begin of eind van de lijst vraagt ← of → de pagina om meer (`onNeedMore`).

## Afbeeldingen

| Toets | Wat |
|---|---|
| + (of =) | Inzoomen (×1,25) |
| − | Uitzoomen |
| 0 | Passend |
| 1 | Ware grootte (100 %) |
| R | Een kwartslag draaien (daarna weer passend) |
| ← → ↑ ↓ (ingezoomd) | Verschuiven, 10 % van het venster per keer |

## PDF

| Toets | Wat |
|---|---|
| PgDn / PgUp | Volgende / vorige pagina |
| + / − | In- en uitzoomen (50–400 %) |
| 0 | Paginabreedte |
| T | Inhoudsopgave tonen of verbergen (als het document er een heeft) |
| Paginavak + Enter | Naar die pagina |

De knoppen "Paginabreedte" en "Hele pagina" en de items van de inhoudsopgave zijn gewone
knoppen (Tab, Enter).

## Video en audio

| Toets | Wat |
|---|---|
| Spatie / K | Afspelen of pauzeren |
| J / L | 10 seconden terug / vooruit |
| M | Geluid aan of uit |

Staat de focus op de speler zelf (met bediening), dan gebruikt de speler spatie en de
pijltjes; de viewer neemt ze dan niet over.

## Tekst

| Toets | Wat |
|---|---|
| Tab | Naar de knoppen "Met opmaak" / "Als tekst" / "Als tabel", de keuzelijst Tekencodering en de inhoud; in de inhoud scrollen de pijltjes en PgUp/PgDn (de inhoud is een focusbaar gebied) |
| "Meer laden" (Enter) | De volgende 512 KB |

## Kantoordocumenten

Een rekenblad met meer bladen heeft tabbladen (`ui/Tabs`): ← en → wisselen van blad als de
tabs de focus hebben.

## Getest

`e2e/test_p4_viewer.py`: ← → Home End door de hele map, + − 0 1 R, pannen met de pijltjes en
Shift+← → ingezoomd, I, D, ?, Esc en Terug, PgDn/PgUp en T bij een PDF plus het paginavak, K M
J L bij een video, Tab en Enter op "Volgende", de tekencodering met de keuzelijst.
