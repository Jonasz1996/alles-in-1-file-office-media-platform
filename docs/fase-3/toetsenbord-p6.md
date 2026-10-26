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
Na blokkeren staat de focus weer op dezelfde knop, die dan "Blokkade opheffen" heet.

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
| Tab | naar Bekijken, Downloaden, Terugzetten, Verwijderen |

De focus begint op "Nieuwe versie uploaden" (als je mag schrijven). Tijdens een upload staat
hij op "Upload annuleren" (Enter annuleert), daarna weer op "Nieuwe versie uploaden"; na
Terugzetten of Verwijderen op dezelfde knop (of, als die versie weg is, op "Nieuwe versie
uploaden"). Bekijken opent de viewer bovenop de dialoog; Sluiten of Escape brengt je terug op
Bekijken.

## Delen… (dialoog, scope `share`)

| Toets | Wat |
|---|---|
| Alt+P | focus in "Zoek een persoon of groep" (ook vanuit een ander veld) |
| Alt+L | het formulier voor een nieuwe link openen (focus op Soort) |
| ↓ ↑ (in het zoekveld) | door de gevonden personen en groepen (aria-activedescendant: de focus blijft in het veld) |
| Enter (in het zoekveld) | delen met de actieve keuze (of de eerste) |
| Escape (in het zoekveld) | de lijst sluiten; nog een keer: het veld leegmaken; daarna sluit Escape de dialoog |

Na Link maken of Annuleren in het formulier voor een nieuwe link staat de focus op "Nieuwe
link"; na Opslaan of Annuleren bij Wijzigen van een link op Wijzigen.

## Gedeeld met mij en Gedeeld door mij

De tabbladen "Gedeeld met mij" en "Gedeeld door mij" zijn links (Tab, Enter). Een map of album
is een link naar zijn eigen adres; een bestand opent met Enter in de viewer. "Delen…" in Gedeeld
door mij opent de deeldialoog.

## Checklist (alleen toetsenbord)

Elk punt loopt een e2e-test na met alleen het toetsenbord (geen klik), ook waar de focus daarna
staat: in een dialoog nooit op `<body>`.

- [x] /beheer: Alt+1 … Alt+6 wisselen van tabblad; het actieve tabblad heeft aria-current
  (`test_admin::test_tabbladen_met_het_toetsenbord`).
- [x] Gebruikers: N, velden invullen, Enter voegt toe; / zoekt; Tab naar Wijzigen, Enter.
- [x] Gebruiker blokkeren en verwijderen met alleen Tab, Enter en typen (beide
  `test_toetsenbord_p6::test_gebruiker_toevoegen_blokkeren_en_verwijderen`).
- [x] Taken: Shift+R, Tab naar "Alles opnieuw", Enter (`test_toetsenbord_p6::test_taken_alles_opnieuw`).
- [x] Groepen: N, naam typen, Enter (`test_admin::test_taken_en_groepen_met_het_toetsenbord`).
- [x] Account: taal kiezen met de pijltjes, Tab naar "Taal wijzigen", Enter
  (`test_toetsenbord_p6::test_account_taal_met_pijltjes_tab_en_enter`).
- [x] Prullenbak: Tab naar de lijst, ↓ en Shift+↓, Enter zet terug; Mod+A en
  Shift+Mod+Backspace verwijderen; Shift+L leegt; de vragen met Tab en Enter
  (`test_toetsenbord_p6::test_prullenbak_terugzetten_verwijderen_en_legen`).
- [x] Versies: V, U, bestand kiezen, Enter op "Upload annuleren"; Bekijken en Terugzetten met
  Tab en Enter (`test_toetsenbord_p6::test_versies_uploaden_annuleren_bekijken_en_terugzetten`).
- [x] Delen: S, Alt+P, typen, ↓, Enter; Alt+L, Tab naar "Link maken", Enter
  (`test_toetsenbord_p6::test_delen_met_alt_p_en_een_link_met_alt_l`).

`e2e/test_admin.py`, `e2e/test_trash_versions.py` en `e2e/test_sharing_ui.py` lopen daarnaast
de rest van P6 na, deels met de muis.
