# Toetsenbord — P5 (Foto's en albums)

Alle acties van de tijdlijn, de albums en een album achter een link werken zonder muis. De
sneltoetsen staan met `useShortcuts` in de scope van de pagina en staan in de hulp (`?`). Mod is
Ctrl, op een Mac Cmd. Eén-lettertoetsen werken niet terwijl je in een veld typt.

## Het raster (tijdlijn `/fotos`, album `/album/:id`, album via een link)

Het raster heeft één tabstop (roving focus): Tab gaat erin naar de cel met de focus, nog een
keer Tab gaat eruit. De pijltjes en Home/End handelt het raster zelf af (geen scope nodig).

| Toets | Wat |
|---|---|
| ← / → | Vorige / volgende foto (ook over de grens van een maand) |
| ↑ / ↓ | Dezelfde kolom in de rij erboven / eronder; maandkoppen worden overgeslagen; een kortere rij → de laatste foto |
| PageUp / PageDown | Een scherm vol rijen omhoog / omlaag |
| Home / End | De nieuwste / oudste foto (End scrolt naar de laatste rij) |
| Shift + pijltje, PageUp/Down, Home/End | Een reeks selecteren vanaf het anker, ook over maanden die nog niet geladen zijn |
| Shift + Mod + pijltje | De reeks erbij, de rest blijft geselecteerd |
| Spatie | De foto met de focus selecteren of deselecteren (scope `photos`/`album`) |
| Enter | De foto openen in de viewer (scope `photos`/`album`/`public-album`) |
| Mod+A | Alles selecteren (hooguit 10.000; daarboven alleen het begin, met een melding) |
| Escape | De selectie wissen |

In de viewer (P4) gaan ← / → naar de vorige en volgende foto van de
tijdlijn; aan het eind van een geladen maand laadt de volgende maand en gaat hij verder.
Sluiten zet de focus terug op de cel van de laatst bekeken foto.

## Tijdlijn `/fotos` (scope `photos`)

| Toets | Wat |
|---|---|
| A | De selectie toevoegen aan een album (dialoog: kies een album of "Nieuw album" met een naam; Enter voegt toe) |
| D | De selectie downloaden als zip (P2, archive/link) |
| Delete of Mod+Backspace | De selectie naar de prullenbak; de melding heeft "Ongedaan maken" |

### De scrubber

De balk rechts is een schuif (`role="slider"`, "Naar maand"); hij staat na het raster in de
tabvolgorde. De waarde is de maand bovenaan in beeld (na een sprong met het toetsenbord de
maand waar je heen sprong, zolang die in beeld staat: onderaan kan de pagina niet verder).

| Toets | Wat |
|---|---|
| ↑ / ↓ | Een maand nieuwer / ouder |
| PageUp / PageDown | Naar het begin van het jaar ervoor (nieuwer) / erna (ouder) |
| Home / End | De nieuwste / oudste maand |

Met de muis: klikken of slepen springt naar die plek; het label toont de maand.

## Albums `/albums` (scope `albums`)

| Toets | Wat |
|---|---|
| N | Nieuw album (vraagt een naam, opent het album) |
| Tab / Enter | De albumkaarten zijn links |

## Eén album `/album/:id` (scope `album`)

| Toets | Wat | Wie |
|---|---|---|
| D | Downloaden als zip: de selectie, of zonder selectie het hele album (≤ 10.000; daarboven uitgeschakeld met de reden) | iedereen die het ziet |
| S | Delen… (de deeldialoog; alleen om te bekijken) | eigenaar |
| F2 | Hernoemen | eigenaar |
| C | De geselecteerde (of de foto met de focus) als omslag | eigenaar |
| Delete of Mod+Backspace | De selectie uit het album halen (de foto's zelf blijven) | eigenaar |
| Shift+Delete | Het album verwijderen (met bevestiging) | eigenaar |

De knoppen bovenaan (Downloaden, Delen…, Hernoemen, Omschrijving, Album verwijderen) en in de
selectiebalk zijn ook met Tab te bereiken.

## Album via een link `/s/<token>` (scopes `public-album`, `public-view`)

| Toets | Wat |
|---|---|
| Pijltjes, Home/End, PageUp/Down | Door het raster, zoals hierboven |
| Enter | De foto groot bekijken (een dialoog) |
| ← / → in de dialoog | Vorige / volgende foto |
| Escape | De dialoog sluiten; de focus gaat terug naar de cel |

In de dialoog staat de focus eerst op "Downloaden".

## In de e2e-test

`e2e/test_p5_photos.py` doet het meeste hiervan met het toetsenbord: Tab-stop, → , Spatie,
Shift+→, Escape, A (toevoegen aan album), Enter en ←/→ in de viewer, End/Home op de scrubber,
S (deeldialoog) en de zip (Downloaden) op een album, Enter en ←/→ op de publieke albumpagina, en Delete met
Ongedaan maken op de tijdlijn. `e2e/perf/scroll.py` (tijdlijn) controleert ArrowDown, PageDown
en End op 50.000 foto's.
