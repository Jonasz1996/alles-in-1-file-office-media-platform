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
  // Op /account: de weg naar deze pagina, voor iedereen die is ingelogd
  account: {
    title: "Over dit platform",
    link: "Bronnen en licenties",
  },
} as const;
