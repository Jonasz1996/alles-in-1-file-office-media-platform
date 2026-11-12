import type { Translation } from "../types";
import type nl from "./pwa.nl";

const en: Translation<typeof nl> = {
  title: "App",
  offline: "You are offline",
  offlineText: "What you already opened still works; new changes arrive when you are back online.",
  updateReady: "New version available",
  reload: "Refresh",
  reloadShortcut: "Load the new version",
};

export default en;
