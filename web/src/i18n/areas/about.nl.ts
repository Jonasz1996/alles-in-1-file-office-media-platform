// De pagina /over (fase 6, I2; Keuze 29 en K1-13): waar de gegevens van de kaart en de modellen
// van de analyse vandaan komen, met hun licentie. Vaste tekst: de pagina vraagt de server niets en
// toont niets van een gebruiker. De namen, versies, licenties en adressen zelf staan in
// about/sources.ts (in elke taal hetzelfde); hier staan de zinnen eromheen.
export default {
  title: "Over dit platform",
  intro: "Dit platform gebruikt gegevens en modellen van anderen. Hier staat waar ze vandaan komen en onder welke licentie.",
  map: {
    title: "Kaart en plaatsen",
    // Dezelfde bronnen als GET /geo/attribution en de README (Bronnen)
    places: "Plaatsnamen: {source} (als {file} uit {from}), {license}.",
    countries: "Landen: {source} {version} ({license}).",
    world: "De wereldkaart: {source} ({license}).",
    publicDomain: "publiek domein",
  },
  analyse: {
    title: "Gezichten en zoeken op beeld",
    intro:
      "Deze modellen draaien alleen als de analyse op deze server aanstaat (ANALYSE=aan). Ze rekenen op deze server; er gaat niets naar buiten.",
    yunet: "Gezichten vinden: {name} van {from}, {license}, {notice}.",
    sface: "Gezichten herkennen: {name} van {from}, {license}.",
    siglip: "Zoeken op beeld: {name} van {from}, {license}.",
    changed: "Het platform zet SigLIP 2 op deze server zelf om naar int8: wat er draait, is een gewijzigde vorm van het model.",
    texts:
      "De volledige licentieteksten staan naast de modellen (standaard in {models}) en in de broncode, in {folder}.",
  },
  packages: {
    title: "Pakketten van de analyse",
    intro:
      "De analyse rekent met onnxruntime. Dat pakket en de pakketten die ermee meekomen, zitten in het image van het platform:",
    item: "{name} {version}: {license}",
  },
  // /apps (fase 11, stap 9): de downloadpagina voor de client-apps
  apps: {
    title: "Apps",
    link: "Apps voor je tv en telefoon",
    intro: "Apps om het platform te gebruiken op je tv en andere toestellen. Je logt erin in met hetzelfde account.",
    download: "Downloaden",
    version: "Versie {version}",
    refresh: "Nieuwste apps ophalen",
    refreshHint: "Het platform haalt de apps die GitHub bouwt ook vanzelf op, elke 6 uur.",
    refreshed: "De nieuwste apps staan op het platform.",
    later: "Komt later.",
    webos: {
      name: "LG webOS-tv",
      text: "Kijk films, series en muziek van het platform op je LG-tv, met de afstandsbediening. Ondertitels en het scherper maken in de speler werken ook op de tv. Het .ipk kent het adres van dit platform al.",
      step1: "Maak een gratis account op webostv.developer.lge.com, installeer op de tv de app Developer Mode uit de LG Content Store, log daarin in en zet Dev Mode Status aan.",
      step2: "Download het .ipk hierboven op je computer.",
      step3: "Installeer het met webOS Dev Manager of met ares-install (zie apps/webos/README.md), met het IP-adres en de Passphrase uit de Developer Mode-app.",
      step4: "Start Platform op de tv en log in. Een ander adres kiezen: houd bij het starten de rode knop ingedrukt.",
    },
    android: {
      name: "Android",
      text: "Media kijken zoals in Plex of Jellyfin, en je bestanden synchroniseren zoals met de Nextcloud-app.",
    },
    windows: {
      name: "Windows",
      text: "Media en het synchroniseren van je bestanden op je Windows-computer.",
    },
    linux: {
      name: "Linux",
      text: "Media en het synchroniseren van je bestanden op je Linux-computer.",
    },
    linuxarm: {
      name: "Linux-telefoon (Droidian) en Linux op arm64",
      text: "Dezelfde app als voor Linux, voor toestellen met een arm64-chip: telefoons met Droidian of postmarketOS en bv. een Raspberry Pi.",
      step1: "Installeer het .deb op de telefoon: sudo apt install ./platform-linux-arm64.deb",
      step2: "Open Platform-instellingen, vul het adres van het platform in en log in. Kies eventueel een map om te synchroniseren.",
      step3: "Op een telefoon is er geen systeemvak: het synchroniseren loopt zolang de app open is. De instellingen open je met de snelkoppeling Platform-instellingen.",
    },
    macos: {
      name: "macOS",
      text: "Media en het synchroniseren van je bestanden op je Mac (Intel en Apple-chip).",
      step1: "Open de .dmg en sleep Platform naar Apps.",
      step2: "De app is niet bij Apple geregistreerd (dat kost geld). De eerste keer: open Platform, klik Gereed, en ga naar Systeeminstellingen → Privacy en beveiliging → Open toch.",
      step3: "Meldt macOS dat de app beschadigd is: voer in Terminal xattr -cr /Applications/Platform.app uit en open hem opnieuw.",
    },
    ios: {
      name: "iPhone en iPad",
      text: "Geen app uit de App Store (een eigen app daar kost elk jaar geld), maar het platform zelf als app op je beginscherm: zonder adresbalk, met een eigen icoon.",
      step1: "Open het adres van dit platform in Safari.",
      step2: "Tik op de deelknop (vierkant met pijl) en kies Zet op beginscherm.",
      step3: "Open Platform vanaf het beginscherm en log in. Bestanden en foto's opladen gaat met de uploadknop; synchroniseren op de achtergrond kan op iOS niet.",
    },
  },
  // Op /account: de weg naar deze pagina, voor iedereen die is ingelogd
  account: {
    title: "Over dit platform",
    link: "Bronnen en licenties",
  },
} as const;
