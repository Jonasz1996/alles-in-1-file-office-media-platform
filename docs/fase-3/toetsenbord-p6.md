# Toetsenbord — P6 (beheer, account, delen, prullenbak, versies)

Alle sneltoetsen staan in een eigen scope (`useScope`), zodat ze alleen werken op de pagina of
in de dialoog waar ze bij horen. Een dialoog of menu erboven houdt ze tegen. `?` toont ze ook
in de app. *Mod* is Ctrl, op een Mac ⌘.

## /beheer (scope `admin`)

| Toets | Wat | Waar |
|---|---|---|
| Alt+1 … Alt+6 | naar Overzicht, Gebruikers, Opslag, Taken, Groepen, Beveiliging | alle tabbladen |
| N | nieuwe gebruiker (dialoog) | Gebruikers |
| / | focus in het zoekveld | Gebruikers |
| Escape | zoekveld leegmaken (in het veld) | Gebruikers |
| Shift+R | alle mislukte taken opnieuw (na een vraag) | Taken |
| N | focus in het veld voor een nieuwe groep; Enter maakt hem | Groepen |

In de dialogen (gebruiker wijzigen, wachtwoord instellen, leden kiezen) werkt Tab door de
velden en knoppen; Escape sluit. Vragen (blokkeren, verwijderen, 2FA uitzetten) hebben de focus
eerst op Annuleren. Verwijderen vraagt om het e-mailadres; Enter bevestigt pas als het klopt.

## /account

Geen eigen sneltoetsen: alles is een gewoon formulier (Tab, Enter, Spatie). De taal wordt pas
gewisseld met de knop "Taal wijzigen" (een pijltje in de keuzelijst herlaadt niet meteen).
Uitloggen van een apparaat en "Overal anders uitloggen" vragen eerst om bevestiging.

## /prullenbak (scope `trash`)

| Toets | Wat |
|---|---|
| ↑ ↓, Home, End, PageUp, PageDown | de focus door de lijst (één tabstop: de rij met de focus) |
| Shift+↑ ↓ | de selectie uitbreiden vanaf het anker |
| Ctrl/⌘+↑ ↓ | alleen de focus verplaatsen; Spatie (de)selecteert dan |
| Spatie | het item met de focus (de)selecteren |
| Mod+A | alles selecteren |
| Escape | de selectie opheffen |
| Enter | terugzetten (de selectie, of anders het item met de focus) |
| Shift+Delete of Shift+Mod+Backspace | definitief verwijderen (na een vraag) |
| Shift+L | de prullenbak legen (na een vraag) |

## Versies… (dialoog, scope `versions`)

| Toets | Wat |
|---|---|
| U | nieuwe versie uploaden (de bestandskiezer) |
| Tab | naar Annuleren tijdens het uploaden, en naar Bekijken, Downloaden, Terugzetten, Verwijderen |

De focus begint op "Nieuwe versie uploaden" (als je mag schrijven). Bekijken opent de viewer
bovenop de dialoog; Sluiten of Escape brengt je terug.

## Delen… (dialoog, scope `share`)

| Toets | Wat |
|---|---|
| Alt+P | focus in "Zoek een persoon of groep" (ook vanuit een ander veld) |
| Alt+L | het formulier voor een nieuwe link openen (focus op Soort) |
| ↓ ↑ (in het zoekveld) | door de gevonden personen en groepen (aria-activedescendant: de focus blijft in het veld) |
| Enter (in het zoekveld) | delen met de actieve keuze (of de eerste) |
| Escape (in het zoekveld) | de lijst sluiten; nog een keer: het veld leegmaken; daarna sluit Escape de dialoog |

## Gedeeld met mij en Gedeeld door mij

De tabbladen "Gedeeld met mij" en "Gedeeld door mij" zijn links (Tab, Enter). Een map of album
is een link naar zijn eigen adres; een bestand opent met Enter in de viewer. "Delen…" in Gedeeld
door mij opent de deeldialoog.

## Checklist (alleen toetsenbord)

- [ ] /beheer: Alt+1 … Alt+6 wisselen van tabblad; het actieve tabblad heeft aria-current.
- [ ] Gebruikers: N, velden invullen, Enter voegt toe; / zoekt; Tab naar Wijzigen, Enter.
- [ ] Gebruiker blokkeren en verwijderen met alleen Tab, Enter en typen.
- [ ] Taken: Shift+R, Tab naar "Alles opnieuw", Enter.
- [ ] Groepen: N, naam typen, Enter.
- [ ] Account: taal kiezen met de pijltjes, Tab naar "Taal wijzigen", Enter.
- [ ] Prullenbak: Tab naar de lijst, ↓ en Shift+↓, Enter zet terug; Mod+A en Shift+Mod+Backspace verwijderen; Shift+L leegt.
- [ ] Versies: U, bestand kiezen, Tab naar "Upload annuleren"; Bekijken en Terugzetten met Tab en Enter.
- [ ] Delen: Alt+P, typen, ↓, Enter; Alt+L voor een nieuwe link.

De e2e-tests `e2e/test_admin.py`, `e2e/test_trash_versions.py` en `e2e/test_sharing_ui.py`
lopen de meeste van deze toetsen echt na.
