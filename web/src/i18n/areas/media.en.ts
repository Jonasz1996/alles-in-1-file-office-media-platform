import type { Translation } from "../types";
import type nl from "./media.nl";

const en: Translation<typeof nl> = {
  title: "Media",
  library: "Library",
  item: "Movie or episode",
  show: "Show",
  notReady: "This page comes later in phase 5.",
  newLibrary: {
    notReady: "Creating a library comes later in phase 5.",
  },
};

export default en;
