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
  account: {
    title: "About this platform",
    link: "Sources and licences",
  },
};

export default en;
