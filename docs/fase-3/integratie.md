# Fase 3 — Integratie: het verhaal (§5.4 "Full e2e")

`e2e/test_integratie.py` loopt het platform door zoals iemand het in gebruik neemt: één server
vanaf de eerste start (eigen datamap, eigen kopie van `web/dist`), de productie-build op
`localhost`, `PLATFORM_DOWNLOAD_TOKEN_MINUTES=1` voor de hele run en 2FA verplicht. Het harnas
staat in `e2e/verhaal.py` (de mensen met hun 2FA-codes, de contexts die over de stappen heen
open blijven, de bewaking op CSP, i18n en fouten na elke stap).

```
cd api && uv run --with playwright pytest ../e2e/test_integratie.py          # alles, ±3 min
cd api && uv run --with playwright pytest ../e2e/test_integratie.py -m "not slow"
```

Stap 3 (10.000 in één map, ±40 s) is `slow`: CI's e2e-job (`-m "not perf"`) draait hem wel
mee, en niets bouwt erop voort. De stappen bouwen verder op elkaar voort; faalt er een, dan
falen de stappen die hem nodig hebben meteen met de naam van de ontbrekende stap. De
focustests die eerder `test_integratie.py` heetten, staan nu in `test_integratie_focus.py`.

## Wat elke stap dekt, en welke tests het verder uitdiepen

| Stap | Uit §5.4 | Verder in |
|---|---|---|
| 1 Eerste start | eerste account in het formulier, 2FA aan met een code; de beheerder maakt een tweede gebruiker en een gast, die ook eerst 2FA aanzetten | `test_login` (code, blokkade na vijf foute codes), `test_admin` |
| 2 Map uploaden | Shift+U met submappen, een map met de naam van een bestand (zichtbaar mislukt), een groot bestand gaat na herladen verder | `test_p3_upload` |
| 3 Tienduizend (`slow`) | scrollen in lijst en raster, selecteren, in bulk verplaatsen, kopiëren, prullenbak + Mod+Z, zip, contextmenu, alleen toetsenbord met Mod+Backspace | `test_browser`, `test_browser_keyboard`, `e2e/perf` |
| 4 Foto's | backfill van EXIF- en videodatum (23:30 UTC wordt 1 januari in Europe/Amsterdam), Camera/Belichting zonder locatie, album uit de tijdlijn, alleen-bekijken delen (geen Kopiëren, geen download als map), de gast ziet alleen dat, publieke link zonder GPS in enig antwoord, albumzip | `test_p5_photos`, `test_sharing_ui::test_gedeeld_album_zonder_kopieren`, `test_p4_viewer::test_geen_gps_in_enig_antwoord` |
| 5 Viewers | GIF (het origineel), HEIC, PDF van 50 pagina's, webm en mp3 spelen, mp4 de eerlijke melding, cp1252, md, csv, json, docx, xlsx, pptx, odt; achter elkaar met Shift+→ | `test_p4_viewer` |
| 6 Groep | groep Familie, drie bestanden in één keer delen (S, Alt+P), beide leden zien ze, Gedeeld door mij | `test_sharing_ui` (ook de grens van 100) |
| 7 Prullenbak | Mod+A, Delete; terug met Shift+↓ en Enter; Shift+L leegt; de blobs zijn van schijf, die van de teruggezette niet | `test_trash_versions` |
| 8 Versies | V, U met een trage upload (de voortgang loopt), Bekijken, Terugzetten | `test_trash_versions` |
| 9 PDF | de PDF van stap 5 staat ruim een minuut open: een nieuw `/document`, de laatste pagina (label 48) laadt met een nieuw token | `test_p4_viewer::test_pdf_pagina_s_na_het_verlopen_van_het_token` |
| 10 Beheer | blokkeren: sessie, refresh, inloggen en een al uitgegeven downloadlink stoppen; verwijderen: de taak loopt, de blobs zijn weg; quotumbalk, opslag per node, taken | `test_admin`, `test_login::test_geblokkeerd_account` |
| 11 Engels | 17 hoofdschermen, `?` en het accountmenu zonder Nederlandse tekst (ook niet in aria-labels); een API-fout in het Engels | `test_login::test_engels`, `test_admin::test_account_in_het_engels` |
| 12 PWA | de SW neemt het over op localhost, offline herladen geeft de schil met de balk, weer online komt de app zelf terug, een nieuwe build geeft de melding en Shift+N laadt hem | `test_pwa`, `test_login::test_geen_verbinding*` |
| 13 Toetsenbord | de checklists van `toetsenbord-p1` … `p5` die de stappen ervoor nog niet deden: inloggen met 2FA (Tab, Alt+Shift+P, Escape), N, F2, Mod+X en Mod+V, `?`, U met de vraag over een dubbel bestand, de viewer met Enter, D en Escape, selecteren in de tijdlijn | `test_browser_keyboard`, `test_integratie_focus`, `test_admin` (tabbladen, taken, groepen) |

De checklist van `toetsenbord-p6` zit in de stappen 4, 6, 7, 8 en 10 (Alt+P, Alt+L, de
prullenbak, Versies, `/` bij Gebruikers, Alt+3 en Alt+4); de rest staat in `test_admin`.

## Gevonden met het verhaal en verholpen

- Shift+pijl na verplaatsen of weggooien selecteerde één rij te weinig; de focus viel na
  Delete of Mod+Backspace naar `<body>`; in de prullenbak soms ook na Terugzetten (`03777dc`).
- In de mappenkiezer opende een spatie de map in plaats van mee te tellen in de naam
  (`d9a7a78`).
- Mod+Z na 10.000 naar de prullenbak duurde 75 s (drie queries per node); nu 8 s (`7123796`).
- Offline opstarten liet een onafgehandelde belofte achter (`browser/preload.ts`, `006b166`).

## Niet gedekt in de sandbox, en waarom

- **mp4/H.264 afspelen:** deze Chromium (141) heeft geen H.264 (`canPlayType` geeft `""`), dus
  alleen de eerlijke melding. Met H.264 kijkt de stap dat hij speelt.
- **HEIC wel, TIFF niet:** pillow-heif (al een afhankelijkheid van de API) schrijft hier een
  HEIC; de viewer toont de WebP. Een TIFF zit niet in het verhaal.
- **Opslag per node:** één container, dus één node; de balk meet alleen de lokale node (bekende
  grens).
- **Album via een publieke link:** geen zip en geen gegevenspaneel (bekende grens); de zip komt
  van de eigenaar.
- **PWA:** offline is de browser (`set_offline`) met de server even uit, niet het netwerk;
  installeren kan headless niet. De SW registreert alleen in een secure context (`localhost`
  of `127.0.0.1`; geen https in de sandbox). Het offline herladen gebeurt in een eigen context
  op `127.0.0.1`, zoals in `test_pwa`: op de pagina die het hele verhaal open stond (localhost)
  had `set_offline` in de Chromium van CI (156) geen effect.
- **2FA:** de codes komen uit het geheim (`app.totp`), zoals een authenticator-app ze maakt.
- **Herladen terwijl de refresh na weer online loopt:** wie dan herlaadt, breekt een refresh af
  die de server al heeft gedraaid, en houdt het oude cookie. Na de marge van `REFRESH_RACE` is
  dat hergebruik en eindigt de login: zo werkt de detectie van een gestolen token, bewust. Het
  verhaal wacht daarom tot de app zelf weer binnen is.
- **Niet gebouwd:** slepen naar de navigatiebalk, het systeemklembord, een live
  `preview.ready` in albums, een tekstlaag in de PDF.

## Over het harnas

- De bestandskiezer: `expect_file_chooser` zette het onderscheppen pas aan bij zijn eerste
  luisteraar en wachtte daar niet op. Bij een drukke pagina kwam de toets soms eerder in de
  renderer aan (gezien in het protocol), en dan kwam er nooit een `filechooser`: in het verhaal
  bij U (stap 13), in de hele suite bij `test_trash_versions`, ±1 op 6 in een herhaalde meting.
  Nu krijgt elke pagina in `Browsers.new_context` (conftest) meteen een vaste luisteraar
  (`intercept_file_chooser`), zodat het onderscheppen al aan staat: daarna 0 op 48.
- Elke login kost een code: een TOTP-stap die nog niet gebruikt is, anders een herstelcode
  (`Persoon.code()`).
