# apps/desktop: de app voor Windows en Linux

Fase 11, stap 11 (gebouwd vanaf 23-11-2026). Tauri 2: een klein Rust-programma met het venster
van het systeem (WebView2 op Windows, WebKitGTK op Linux). Dezelfde functies als de Android-app:

- **Het platform in een eigen venster:** bestanden, foto's, media met de speler (upscaling in de
  speler, ondertitels). Het venster toont de web-app van het platform zelf, dus elke update van
  het platform is er meteen.
- **Synchroniseren op de achtergrond** (`src-tauri/src/sync.rs`): één map van deze computer in
  twee richtingen gelijk met een map op het platform (zelfde naam), zoals de Nextcloud-client.
  Elke 15 minuten (instelbaar) en een paar seconden na een wijziging in de map. Conflicten
  worden een kopie ("naam (conflict …)"), weggooien gaat naar de prullenbak van het platform.
- **Systeemvak:** het venster sluiten laat de app doorlopen; het menu daar heeft Platform openen,
  Instellingen, Nu synchroniseren en Afsluiten.

De instellingen (adres, het refresh-token, de map) staan in `%APPDATA%\org.platform.desktop`
(Windows) of `~/.config/org.platform.desktop` (Linux), alleen leesbaar voor de gebruiker.

## Bouwen

GitHub bouwt bij elke push naar `apps/desktop` (workflow **Desktop-app**):

- `platform-windows.exe`: installatieprogramma voor Windows 10 en 11 (per gebruiker, zonder
  beheerdersrechten). Niet ondertekend: Windows SmartScreen vraagt de eerste keer "Toch
  uitvoeren".
- `platform-linux.deb`: voor Debian 12+, Ubuntu 22.04+ (`sudo apt install ./platform-linux.deb`).

Zelf bouwen: Rust, Node 22 en op Linux `libwebkit2gtk-4.1-dev libayatana-appindicator3-dev
librsvg2-dev`, dan `npx @tauri-apps/cli@2 build` in deze map.

## Op de downloadpagina

Vanzelf: de workflow zet elke build in de release `clients` van het repo, en het platform haalt
de nieuwste daar zelf op (bij de start en elke 6 uur; `app/client_fetch.py`) naar de map `apps`
van zijn data. De pagina `/apps` geeft ze dan van de eigen server. Meteen ophalen: een beheerder
doet `POST /api/v1/apps/refresh`.
