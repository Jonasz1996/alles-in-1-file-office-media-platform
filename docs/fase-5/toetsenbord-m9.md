# Toetsenbord — M9 (muziek)

Albums, artiesten, afspeellijsten en de muziekbalk zijn helemaal met het toetsenbord te bedienen.
Muziek heeft **geen eigen sneltoetsen**: alles gaat via Tab, de pijltjes binnen een lijst of
raster, Enter en Spatie op knoppen, en Shift+F10 of de menutoets voor het menu van een regel. Zo
botst niets met de app of de browser; **Ctrl+N, Ctrl+T en Ctrl+W gebruikt muziek nooit**. De
mediatoetsen van het toestel (spelen/pauzeren, volgende, vorige, stoppen) werken via de Media
Session van de browser (`music/player.ts`).

De e2e-tests `test_albums_artiesten_en_de_muziekbalk_met_het_toetsenbord` en
`test_afspeellijsten_met_het_toetsenbord` (`e2e/test_media_m9.py`) lopen deze lijst af. Alleen
het binnenkomen in een lijst of raster gaat daar met `focus()` (één tabstop), zoals in
`test_media_m2.py`.

## Een lijst of raster: één tabstop

Het raster van albums, de lijst van artiesten, de lijst van afspeellijsten en de lijst van
nummers hebben elk één tabstop (roving tabindex, `ui/keys` `useRovingFocus`). Tab gaat naar de
regel die het laatst de focus had; daarna gaat Tab naar het menu van die regel en dan uit de
lijst.

| Toets | Wat | Waar |
|---|---|---|
| ← → ↑ ↓ | naar het album ernaast, erboven of eronder (het aantal kolommen volgt de breedte) | raster van albums |
| ↑ ↓ | de vorige / volgende regel | lijsten |
| Home / End | de eerste / laatste | overal |
| Page Up / Page Down | een scherm verder (de lijst van nummers is gevirtualiseerd) | lijsten |
| Enter | het album, de artiest of de afspeellijst openen; op een nummer: spelen vanaf dat nummer (de rij wordt het album of de lijst) | overal |
| Shift+F10, menutoets | het menu van de regel (Meer voor …), de focus op het eerste item | lijsten |

Een toets met Alt erbij beweegt de focus niet: Alt+↑ en Alt+↓ zijn van de afspeellijst (onder).

## Een menu

| Toets | Wat |
|---|---|
| ↑ ↓, Home, End | door de items |
| letters | naar het item dat zo begint |
| Enter, Spatie | kiezen; op "Aan afspeellijst toevoegen" opent het submenu |
| → | het submenu openen |
| ← | het submenu dicht, terug naar het item |
| Esc | het menu dicht; de focus terug op de knop Meer van de regel |

**Op een album of nummer:** Aan de rij toevoegen; Aan afspeellijst toevoegen → elke afspeellijst,
of Nieuwe afspeellijst… (een dialoog met het veld Naam: Enter maakt hem, Esc annuleert).

**Op een afspeellijst (in de lijst en in de kop van de lijst):** Naam wijzigen (dialoog: Enter
bewaart, Esc annuleert); Verwijderen (bevestigen: de focus staat eerst op Annuleren, Tab naar
Verwijderen, Enter).

## De pagina's

| Pagina | Toetsen |
|---|---|
| `/muziek` | De tabs Albums, Artiesten, Afspeellijsten: ← → (rondom), Home, End; de tab kiezen is meteen (het adres krijgt `?tab=`). Tab naar de keuze van de bibliotheek (bij meer dan één) en Sorteren (een `<select>`: ↑ ↓), dan het raster of de lijst. Meer laden is een knop. Op Afspeellijsten eerst de knop Nieuwe afspeellijst. |
| Album | Na het openen staat de focus op de titel (de kop). Tab: de artiest (een link naar zijn pagina), Afspelen, Meer (het menu van het album), Muziek (terug), dan de lijst van nummers. |
| Artiest | Tab: Afspelen (alle albums na elkaar), Muziek (terug), dan het raster van zijn albums. |
| Afspeellijst | Tab: Afspelen, Meer (Naam wijzigen, Verwijderen), Afspeellijsten (terug), dan de lijst van nummers. |

### Een afspeellijst ordenen

Op een nummer in de lijst van een afspeellijst:

| Toets | Wat |
|---|---|
| Alt+↑ / Alt+↓ | het nummer een plek omhoog / omlaag; de focus gaat mee, en een schermlezer hoort "… staat nu op plek n" |
| Delete | het nummer uit de lijst halen (een melding); de focus naar het nummer dat op die plek komt |

Dezelfde acties staan in het menu van de regel (Omhoog, Omlaag, Uit de lijst halen), met de toets
erbij.

## De muziekbalk

De balk staat onderaan de schil zolang er muziek is, en blijft staan bij navigeren in de app (de
muziek speelt door). Hij staat in de tabvolgorde na de pagina.

| Element | Toetsen |
|---|---|
| De titel | een link naar het album (Enter) |
| Vorige | Enter/Spatie: naar het begin van het nummer, of binnen 3 s na het begin naar het vorige |
| Spelen / Pauzeren | Enter/Spatie |
| Volgende | Enter/Spatie (uit als er geen volgende is) |
| De tijdlijn (een schuif) | ← → per seconde, Page Up / Page Down per 10 %, Home / End |
| Geluid uit / aan | Enter/Spatie |
| Volume (een schuif) | ← → per 5 %, Home / End |
| De rij | Enter/Spatie opent het paneel De rij (de knop is dan ingedrukt) |
| Speler sluiten | stopt, leegt de rij en haalt de balk weg |

**Het paneel De rij** (onder de balk): Rij leegmaken, dan per nummer (vanaf het huidige, hooguit
100) een knop "… afspelen" en een knop "… uit de rij halen". **Esc** sluit het paneel en zet de
focus terug op de knop De rij.

Een fout van het laden staat in de balk met de knop Opnieuw proberen (Enter).

## Wat de e2e niet doet

- De mediatoetsen van een toestel (Media Session): headless Chromium heeft ze niet.
- Page Up / Page Down in een lijst met meer nummers dan op het scherm passen: het album in de
  e2e heeft er drie (de unit-tests van `ui/virtual` dekken het).
