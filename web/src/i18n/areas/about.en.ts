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
  },
  account: {
    title: "About this platform",
    link: "Sources and licences",
  },
};

export default en;
