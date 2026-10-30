import type { Translation } from "../types";
import type nl from "./music.nl";

const en: Translation<typeof nl> = {
  title: "Music",
  album: "Album",
  artist: "Artist",
  playlist: "Playlist",
  notReady: "This page comes later in phase 5.",
  bar: {
    label: "Music player",
  },
};

export default en;
