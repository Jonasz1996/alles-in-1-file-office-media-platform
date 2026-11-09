import type { Translation } from "../types";
import type nl from "./photos_map.nl";

const en: Translation<typeof nl> = {
  title: "Map",
  map: {
    label: "Map of your photos",
    role: "map",
    help: "Arrow keys move the map, + and − zoom in and out, Home shows all your photos. Tab goes along the groups of photos; Enter zooms in, or opens the photos when you cannot zoom further.",
    zoomIn: "Zoom in",
    zoomOut: "Zoom out",
    home: "Show all your photos",
    loading: "Loading the map…",
    noWorld: "The country borders could not load. Your photos are still shown.",
    tooMany: "Not all photos in this area fit on the map. Zoom in to see the rest.",
    empty: "No photos with a location yet",
    emptyHint:
      "A photo appears on the map when its file contains a location and Location under Account → Photos is set to Me.",
    photos: { one: "{n} photo", other: "{n} photos" },
    photosAt: { one: "{place}, {n} photo", other: "{place}, {n} photos" },
    status: { one: "{n} place with photos in view", other: "{n} places with photos in view" },
    nothingHere: "No photos here. Zoom out or press Home.",
  },
  places: {
    title: "Places",
    hint: "Choose a place: the map then shows only its photos.",
    count: { one: "{n} photo", other: "{n} photos" },
    all: "All places",
    only: "Only {place}",
    none: "No places yet. A place appears when a photo with a location lies near one.",
  },
  attribution: {
    places: "Place names: {source}, {license}.",
    countries: "Countries: {source} ({license}).",
    publicDomain: "public domain",
    borders: "Borders are accurate to about a kilometre; the enclaves of Baarle-Hertog are shown as the Netherlands.",
  },
  location: {
    title: "Location",
    coords: "{lat}, {lon}",
    north: "{v}° N",
    south: "{v}° S",
    east: "{v}° E",
    west: "{v}° W",
    altitude: "{m} m altitude",
    onMap: "On the map",
    onlyYou: "Only you can see the location.",
  },
};

export default en;
