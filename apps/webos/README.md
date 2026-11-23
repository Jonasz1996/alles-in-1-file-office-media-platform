# apps/webos: de app voor de LG webOS-tv

Fase 11, stap 9. Een kleine webOS-web-app die de **tv-stand van het platform** opent
(`<platform>/tv`). Alles wat je op de tv ziet, komt van het platform zelf: de rijen van media,
de speler met upscaling in de speler en ondertitels, en bediening met de afstandsbediening
(pijltjes, OK, Terug). Zo krijgt de tv elke update van het platform meteen, zonder de app
opnieuw te installeren. Het is geen Jellyfin- of Plex-app; die werken niet met het platform.

## Bestanden

- `appinfo.json`: de gegevens van de app (id `org.platform.tv`).
- `index.html`: kiest en onthoudt het adres van het platform en opent daarna `/tv`.
- `config.js`: het adres vooraf. De downloadpagina van het platform (`/apps`) maakt het `.ipk`
  zelf en vult hier het adres van dat platform in.
- `icon.png`, `largeIcon.png`: de iconen.

## Installeren op de tv

1. Maak een (gratis) account op <https://webostv.developer.lge.com> en log met dat account in
   op de tv in de app **Developer Mode** (uit de LG Content Store). Zet Dev Mode Status aan; de
   tv herstart. Developer Mode blijft 50 uur aan; in de app verleng je dat.
2. Download het `.ipk` op het platform: **Account → Apps** (of `/apps`), knop *LG webOS-tv*.
3. Installeer het op de tv, met één van deze twee:
   - **webOS Dev Manager** (een programma met vensters voor Windows, macOS en Linux): tv
     toevoegen met het IP-adres en de Passphrase uit de Developer Mode-app, daarna
     *Install* en het `.ipk` kiezen.
   - **ares-cli** (`npm install -g @webos-tools/cli`):
     ```sh
     ares-setup-device            # tv toevoegen: IP, poort 9922, gebruiker prisoner
     ares-novacom --device tv --getkey   # de Passphrase uit de Developer Mode-app
     ares-install --device tv platform-webos.ipk
     ```
4. Start **Platform** op de tv. De eerste keer: log in zoals in de browser.

Ander adres kiezen: houd bij het starten van de app de **rode knop** ingedrukt.

## Zelf een .ipk maken (zonder het platform)

```sh
ares-package apps/webos      # geeft org.platform.tv_1.0.0_all.ipk
```
