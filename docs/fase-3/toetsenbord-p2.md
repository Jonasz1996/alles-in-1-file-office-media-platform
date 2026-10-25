# Fase 3 — Toetsenbord in de bestandsbrowser (P2)

Alle sneltoetsen van Bestanden (`/`, `/map/<id>`) en Zoeken (`/zoeken`) staan in de scope
`browser` (`useShortcuts`, `ui/keys.ts`). Een dialoog of menu bovenop houdt ze tegen. `?` toont
wat er op dat moment werkt (alleen de acties die bij de selectie passen). Mod = Ctrl, op een
Mac Cmd. Nooit Ctrl+N/T/W of Ctrl+Shift+N: die zijn van de browser.

Een actie werkt op de selectie, of zonder selectie op de rij met de focus. Een actie die
zichtbaar is maar niet mag (bijv. alleen kijken), neemt de toets en meldt waarom niet.

## In de lijst of het raster

De lijst is een `grid` met één tabstop (roving focus). Tab gaat de lijst in en uit.

| Toets | Wat |
|---|---|
| ↑ / ↓ | vorige / volgende rij (selecteert die rij) |
| ← / → | in het raster: vorige / volgende cel |
| Home / End | eerste / laatste rij |
| PageUp / PageDown | een scherm omhoog / omlaag |
| Shift + pijl, Home, End, PageUp/Down | een reeks selecteren vanaf het anker |
| Mod + pijl | alleen de focus verplaatsen (de selectie blijft) |
| Spatie | de rij met de focus erbij of eraf selecteren |
| Enter | openen (map of viewer) |
| Shift+F10 of de menutoets | contextmenu bij de rij met de focus |

In het contextmenu en het menu ⋯: pijltjes, Home/End, typen springt naar een item, Enter of
Spatie kiest, Escape sluit (de focus gaat terug naar de rij).

## Acties (register, `browser/actions.ts`)

| Toets | Actie |
|---|---|
| Enter | Openen |
| D | Downloaden (één bestand) of Downloaden als zip |
| F2 | Naam wijzigen |
| Mod+X / Mod+C / Mod+V | Knippen / Kopiëren / Plakken (in de app, niet het systeemklembord) |
| M | Verplaatsen naar… |
| Shift+M | Kopiëren naar… |
| T | Tags… |
| S | Delen… (hooguit 100 items) |
| Shift+A | Toevoegen aan album… (eigen foto's en video's) |
| V | Versies… |
| Delete of Mod+Backspace | Naar prullenbak (met "Ongedaan maken" in de melding) |

## Op de pagina

| Toets | Wat |
|---|---|
| Mod+A | alles selecteren (wat geladen en gefilterd is) |
| Escape | selectie opheffen |
| G | lijst of raster |
| N | nieuwe map |
| Mod+F | filter in deze map; staat de focus al in het filter, dan is Mod+F weer van de browser |
| / | naar het zoekveld |
| U / Shift+U | uploaden / map uploaden (P3) |
| ? | sneltoetsen tonen (schil) |

## In dialogen

- **Verplaatsen/kopiëren naar** (mapkiezer): ↑/↓, Home/End tussen de mappen, typen springt naar
  een map met die naam, Enter opent een map; "Nieuwe map", "Hierheen …" en Annuleren met Tab.
- **Tags**: Tab door de vinkjes, Spatie zet aan of uit; Enter in het veld "Nieuwe tag" maakt hem.
- **Naam wijzigen / Nieuwe map**: het veld heeft de focus (bij een bestand alleen de naam, niet de
  extensie), Enter bevestigt, Escape annuleert.

## Getest

`e2e/test_browser_keyboard.py` doet dit rondje alleen met het toetsenbord: pijltjes, Shift+End,
Mod+A, Escape, Spatie, F2, N, Mod+X/Mod+V, Mod+C, Delete en Mod+Backspace met Ongedaan maken,
Shift+F10, G, Mod+F en `?` (de acties staan in de hulp).
