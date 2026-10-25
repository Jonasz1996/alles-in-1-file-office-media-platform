# Toetsenbord — P1 (schil, PWA, inloggen)

Alle sneltoetsen hieronder staan via `useShortcuts` in een scope, met een label (dus in de
sneltoetsenhulp `?` waar de schil die toont). Ze werken ook als de focus in een invoerveld staat
(`allowInInput`), want op het inlogscherm staat de focus bijna altijd in een veld.

## Inloggen (scope `login`)

| Toets | Wanneer | Wat |
|---|---|---|
| `Escape` | stap "Code invoeren" (2FA) | Terug naar e-mail en wachtwoord; de focus gaat naar het wachtwoordveld, het e-mailadres blijft staan |
| `Alt+Shift+P` | stap "Inloggen" | Wachtwoord tonen of weer verbergen (hetzelfde als het vinkje "Wachtwoord tonen") |
| `Enter` | in elk veld | Het formulier versturen (gewoon HTML-formuliergedrag) |

Zonder sneltoets, met `Tab` te bereiken: het vinkje "Wachtwoord tonen", de knop "Terug" (stap
2FA), de knop "Inloggen" of "Account aanmaken", en op het scherm "Geen verbinding met de
server" de knop "Opnieuw proberen".

## Nieuwe versie (PWA)

| Toets | Wanneer | Wat |
|---|---|---|
| `Shift+R` | de melding "Nieuwe versie beschikbaar" staat er | De nieuwe versie laden (zelfde als de knop "Vernieuwen" in de melding) |

De melding blijft staan tot je kiest (geen time-out). De knop "Vernieuwen" zit in het
meldingengebied ("Meldingen") en is met `Tab` te bereiken.

## Offline

De balk "Je bent offline" is een `role="status"`-melding zonder knoppen: er is niets te
bedienen. Komt de verbinding terug, dan verdwijnt hij vanzelf.

## Gecontroleerd in e2e

- `e2e/test_login.py::test_inloggen_met_code_en_terug`: `Alt+Shift+P` vanuit het
  wachtwoordveld, `Escape` vanuit het codeveld, focus na "Terug", `Enter` in het codeveld.
- `e2e/test_pwa.py::test_nieuwe_versie_en_vernieuwen`: eerst "Vernieuwen" met de muis, daarna
  een tweede nieuwe versie met `Shift+R`.

## Checklist voor de integratie (alleen toetsenbord)

1. Open `/` uitgelogd. `Tab` naar het e-mailadres, typ, `Tab` naar het wachtwoord, typ.
2. `Alt+Shift+P`: het wachtwoord is leesbaar; nog eens: verborgen.
3. `Enter`: bij een account met 2FA staat de focus in het codeveld.
4. `Escape`: terug op het eerste formulier, focus in het wachtwoordveld.
5. `Enter`, de code typen, `Enter`: ingelogd.
6. Een nieuwe build uitrollen terwijl de app open is: "Nieuwe versie beschikbaar" verschijnt;
   `Shift+R` laadt hem.
