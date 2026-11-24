# apps/desktop: de app voor Windows, Linux, Linux-telefoons en macOS

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
- `platform-linux-arm64.deb` (stap 12): dezelfde app voor arm64, gebouwd op een arm-runner van
  GitHub. Voor telefoons met Droidian of postmarketOS (Phosh, Plasma Mobile) en bv. een
  Raspberry Pi. Op een telefoon (herkend aan `XDG_CURRENT_DESKTOP`, of met `PLATFORM_MOBIEL=1`)
  is er geen systeemvak: sluiten stopt de app, en de snelkoppeling **Platform-instellingen**
  (`platform-desktop --instellingen`) opent de instellingen. Het venster past vanaf 320 px
  breed; de web-app van het platform is zelf al geschikt voor een telefoon.
- `platform-macos.dmg` (stap 12): voor macOS 11+, Intel en Apple-chip in één app. Alleen
  ad-hoc ondertekend: Apple-registratie (Developer Program) kost geld. De eerste keer opent
  macOS hem pas na Systeeminstellingen → Privacy en beveiliging → Open toch; zegt macOS dat de
  app beschadigd is, dan `xattr -cr /Applications/Platform.app`.

iPhone en iPad krijgen geen eigen app: een app in de App Store vraagt een betaald account
(Apple Developer Program, 99 dollar per jaar), en met een gratis Apple ID werkt een eigen app
maar 7 dagen en moet je hem met een Mac en Xcode opnieuw installeren. In de plaats: het platform
zelf is een web-app; in Safari → Deel → Zet op beginscherm staat het als app op het beginscherm.

Zelf bouwen: Rust, Node 22 en op Linux `libwebkit2gtk-4.1-dev libayatana-appindicator3-dev
librsvg2-dev`, dan `npx @tauri-apps/cli@2 build` in deze map.

## Op de downloadpagina

Vanzelf: de workflow zet elke build in de release `clients` van het repo, en het platform haalt
de nieuwste daar zelf op (bij de start en elke 6 uur; `app/client_fetch.py`) naar de map `apps`
van zijn data. De pagina `/apps` geeft ze dan van de eigen server. Meteen ophalen: een beheerder
doet `POST /api/v1/apps/refresh`.
