# apps/android: de Android-app

Fase 11, stap 10 (gebouwd vanaf 23-11-2026). Kotlin, Jetpack Compose, Media3/ExoPlayer en
WorkManager. Eén app voor media (zoals Plex of Jellyfin) en je bestanden (zoals de Nextcloud-app).

## Wat ze kan (versie 0.1)

- **Inloggen:** adres van het platform, e-mail, wachtwoord en de code van 2FA. De tokens staan
  versleuteld (Android Keystore); het refresh-token wordt bij elk gebruik vervangen.
- **Media:** de start met rijen (verder kijken, volgende aflevering, net toegevoegd, per
  bibliotheek), bibliotheken, series met seizoenen. Afspelen met ExoPlayer: direct of HLS
  (remux/transcode) van het platform, met het profiel `android-exo`. Voortgang elke 10 s en bij
  pauze/stop/einde (verder kijken op een ander toestel). Knop "Intro overslaan".
- **Bestanden:** mappen openen, een bestand downloaden (naar Downloads), bestanden uploaden.
- **Synchroniseren** (op de achtergrond, elk uur, standaard alleen via wifi):
  - camera-upload: nieuwe foto's en video's naar "Camera-uploads" op het platform;
  - één map van het toestel in twee richtingen gelijk met een map op het platform (zelfde naam),
    met conflictkopieën en weggooien naar de prullenbak (zie `sync/FolderSync.kt`).

Nog niet: muziek, profielen kiezen, ondertitels kiezen, offline downloads van films,
Chromecast. Die komen in volgende delen.

## Bouwen

Er is geen Android Studio nodig: GitHub bouwt de APK bij elke push naar `apps/android`
(workflow **Android-app**). Download hem onder Actions → de laatste run → Artifacts →
`platform-android`.

- `platform-android-debug.apk`: altijd; installeerbaar (sta "onbekende bronnen" toe).
- `platform-android.apk`: alleen als de sleutel als geheim in het repo staat
  (`ANDROID_KEYSTORE_B64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`).
  Een vaste sleutel is nodig om later updates over de oude app heen te installeren.

Zelf bouwen kan ook: `./gradlew assembleDebug` (JDK 17 en de Android SDK nodig).

## Op de downloadpagina zetten

Leg de APK als `platform-android.apk` in de map `apps` van de data van het platform
(standalone: `/opt/platform/data/platform/apps/`, eigenaar 10001). De pagina `/apps` biedt
hem dan aan. Een debug-APK mag daar ook staan, hernoemd naar `platform-android.apk`.
