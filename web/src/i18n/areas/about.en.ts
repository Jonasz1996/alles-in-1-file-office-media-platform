import type { Translation } from "../types";
import type nl from "./about.nl";

const en: Translation<typeof nl> = {
  title: "About this platform",
  intro: "This platform uses data and models made by others. This page says where they come from and under which licence.",
  map: {
    title: "Map and places",
    places: "Place names: {source} (as {file} from {from}), {license}.",
    countries: "Countries: {source} {version} ({license}).",
    world: "The world map: {source} ({license}).",
    publicDomain: "public domain",
  },
  analyse: {
    title: "Faces and search by image",
    intro:
      "These models only run when the analysis is on for this server (ANALYSE=aan). They run on this server; nothing goes outside.",
    yunet: "Finding faces: {name} from {from}, {license}, {notice}.",
    sface: "Recognising faces: {name} from {from}, {license}.",
    siglip: "Search by image: {name} from {from}, {license}.",
    changed: "The platform converts SigLIP 2 to int8 on this server itself: what runs is a modified form of the model.",
    texts: "The full licence texts are next to the models (by default in {models}) and in the source code, in {folder}.",
  },
  packages: {
    title: "Packages of the analysis",
    intro:
      "The analysis runs on onnxruntime. That package and the packages that come with it are part of the platform image:",
    item: "{name} {version}: {license}",
  },
  // /apps (phase 11, step 9): the download page for the client apps
  apps: {
    title: "Apps",
    link: "Apps for your TV and phone",
    intro: "Apps to use the platform on your TV and other devices. You sign in with the same account.",
    download: "Download",
    version: "Version {version}",
    refresh: "Fetch latest apps",
    refreshHint: "The platform also fetches the apps GitHub builds by itself, every 6 hours.",
    refreshed: "The latest apps are on the platform.",
    later: "Coming later.",
    webos: {
      name: "LG webOS TV",
      text: "Watch movies, shows and music from the platform on your LG TV, with the remote. Subtitles and sharpening in the player also work on the TV. The .ipk already knows the address of this platform.",
      step1: "Create a free account at webostv.developer.lge.com, install the Developer Mode app from the LG Content Store on the TV, sign in there and turn on Dev Mode Status.",
      step2: "Download the .ipk above to your computer.",
      step3: "Install it with webOS Dev Manager or with ares-install (see apps/webos/README.md), using the IP address and Passphrase from the Developer Mode app.",
      step4: "Start Platform on the TV and sign in. To choose another address: hold the red button while the app starts.",
    },
    android: {
      name: "Android",
      text: "Watch media like in Plex or Jellyfin, and sync your files like with the Nextcloud app.",
    },
    windows: {
      name: "Windows",
      text: "Media and file sync on your Windows computer.",
    },
    linux: {
      name: "Linux",
      text: "Media and file sync on your Linux computer.",
    },
    linuxarm: {
      name: "Linux phone (Droidian) and Linux on arm64",
      text: "The same app as for Linux, for devices with an arm64 chip: phones with Droidian or postmarketOS and e.g. a Raspberry Pi.",
      step1: "Install the .deb on the phone: sudo apt install ./platform-linux-arm64.deb",
      step2: "Open Platform settings, enter the platform address and sign in. Optionally choose a folder to sync.",
      step3: "A phone has no system tray: syncing runs while the app is open. Open the settings with the Platform settings shortcut.",
    },
    macos: {
      name: "macOS",
      text: "Media and file sync on your Mac (Intel and Apple silicon).",
      step1: "Open the .dmg and drag Platform to Applications.",
      step2: "The app is not registered with Apple (that costs money). The first time: open Platform, click Done, and go to System Settings → Privacy & Security → Open Anyway.",
      step3: "If macOS says the app is damaged: run xattr -cr /Applications/Platform.app in Terminal and open it again.",
    },
    ios: {
      name: "iPhone and iPad",
      text: "Not an App Store app (an own app there costs money every year), but the platform itself as an app on your home screen: no address bar, its own icon.",
      step1: "Open this platform's address in Safari.",
      step2: "Tap the share button (square with arrow) and choose Add to Home Screen.",
      step3: "Open Platform from the home screen and sign in. Upload files and photos with the upload button; background sync is not possible on iOS.",
    },
  },
  account: {
    title: "About this platform",
    link: "Sources and licences",
  },
};

export default en;
