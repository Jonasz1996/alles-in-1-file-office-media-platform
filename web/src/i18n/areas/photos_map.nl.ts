// De kaart, de plaatsen, de plek in het infopaneel en de waarschuwing bij delen (fase 6, Z5). Het
// gebied is van Z5: /fotos/kaart met de lijst Plaatsen en de bronvermelding (Keuze 31–33), en de
// rij Plek in het infopaneel (Keuze 27).
export default {
  title: "Kaart",
  map: {
    label: "Kaart met je foto's",
    role: "kaart",
    help: "Pijltjestoetsen schuiven de kaart, + en − zoomen in en uit, Home zet al je foto's in beeld. Met Tab ga je langs de groepjes foto's; Enter zoomt in, of opent de foto's als je niet verder kunt.",
    zoomIn: "Inzoomen",
    zoomOut: "Uitzoomen",
    home: "Al je foto's in beeld",
    loading: "De kaart laden…",
    noWorld: "De landsgrenzen konden niet laden. De foto's staan er wel.",
    tooMany: "Niet alle foto's in dit stuk passen op de kaart. Zoom in om de rest te zien.",
    empty: "Nog geen foto's met een plek",
    emptyHint:
      "Een foto komt op de kaart als er een plek in het bestand staat en Locatie bij Account → Foto's op Mij staat.",
    photos: { one: "{n} foto", other: "{n} foto's" },
    photosAt: { one: "{place}, {n} foto", other: "{place}, {n} foto's" },
    status: { one: "{n} plek met foto's in beeld", other: "{n} plekken met foto's in beeld" },
    nothingHere: "Hier staan geen foto's. Zoom uit of druk op Home.",
  },
  places: {
    title: "Plaatsen",
    hint: "Kies een plaats: de kaart toont dan alleen de foto's daarvan.",
    count: { one: "{n} foto", other: "{n} foto's" },
    all: "Alle plaatsen",
    only: "Alleen {place}",
    none: "Nog geen plaatsen. Een plaats komt erbij als een foto met een plek bij een plaats ligt.",
  },
  attribution: {
    places: "Plaatsnamen: {source}, {license}.",
    countries: "Landen: {source} ({license}).",
    publicDomain: "publiek domein",
    borders:
      "Een grens is op ongeveer een kilometer nauwkeurig; de enclaves van Baarle-Hertog staan erin als Nederland.",
  },
  location: {
    title: "Plek",
    coords: "{lat}, {lon}",
    north: "{v}° N",
    south: "{v}° Z",
    east: "{v}° O",
    west: "{v}° W",
    altitude: "{m} m hoogte",
    onMap: "Op de kaart",
    onlyYou: "Alleen jij ziet de plek.",
  },
  // De waarschuwing in het deeldialoog (Keuze 30). "photos" bij een map, een album of een selectie;
  // "photo" en "video" bij één bestand. Onbekend is een video waarvan de plek niet gelezen kon
  // worden omdat media uit staat.
  share: {
    photos: {
      one: "{n} foto hierin heeft een locatie in het bestand. Wie hem downloadt, krijgt die mee.",
      other: "{n} foto's hierin hebben een locatie in het bestand. Wie ze downloadt, krijgt die mee.",
    },
    photo: "Deze foto heeft een locatie in het bestand. Wie hem downloadt, krijgt die mee.",
    video: "Deze video heeft een locatie in het bestand. Wie hem downloadt, krijgt die mee.",
    unknown: {
      one: "Van {n} video weten we het niet (media staat uit).",
      other: "Van {n} video's weten we het niet (media staat uit).",
    },
    unknownOnly: {
      one: "Van {n} video hierin weten we niet of hij een locatie heeft (media staat uit).",
      other: "Van {n} video's hierin weten we niet of ze een locatie hebben (media staat uit).",
    },
    unknownVideo: "Of deze video een locatie heeft, weten we niet (media staat uit).",
    failed: "Of hierin foto's met een locatie zitten, konden we niet nagaan.",
    failedOne: "Of dit bestand een locatie heeft, konden we niet nagaan.",
  },
} as const;
