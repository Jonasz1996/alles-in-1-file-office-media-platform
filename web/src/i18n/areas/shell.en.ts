import type { Translation } from "../types";
import type nl from "./shell.nl";

const en: Translation<typeof nl> = {
  brand: "Platform",
  skip: "Skip to content",
  pageTitle: "{title} · Platform",
  navigated: "{title}",
  nav: {
    label: "Main menu",
    files: "Files",
    photos: "Photos",
    shared: "Shared with me",
    trash: "Trash",
    admin: "Admin",
  },
  account: {
    menu: "Account menu for {name}",
    account: "Account",
    theme: "Theme",
    language: "Language",
    shortcuts: "Keyboard shortcuts",
    logout: "Log out",
  },
  theme: {
    label: "Theme",
    light: "Light",
    dark: "Dark",
    system: "System",
  },
  language: {
    label: "Language",
    nl: "Nederlands",
    en: "English",
    apply: "Change language",
  },
  shortcuts: {
    title: "Keyboard shortcuts",
    open: "Show keyboard shortcuts",
    empty: "There are no shortcuts here.",
    scopes: {
      global: "Everywhere",
      dialog: "In a dialog",
      menu: "In a menu",
      browser: "Files",
      viewer: "Viewer",
      photos: "Photos",
      trash: "Trash",
      page: "This page",
      login: "Sign in",
      albums: "Albums",
      album: "Album",
      photo: "Photo",
      share: "Sharing",
      versions: "Versions",
      uploadConflict: "Duplicate files",
      admin: "Administration",
    },
  },
  notFound: {
    title: "Page not found",
    text: "This page does not exist (any more).",
    home: "Go to the start",
  },
  noAccess: {
    title: "No access",
    text: "You do not have access to this page.",
  },
  offline: {
    title: "No connection",
    text: "The server cannot be reached. As soon as you are back online, we will try again automatically.",
  },
  recoveryCodes: "Recovery codes",
  recoveryCodesKeep: "Keep these codes somewhere safe. Each code works once.",
  recoveryCodesDone: "I have saved them",
  chunkError: {
    title: "This page could not load",
    reload: "Reload page",
  },
};

export default en;
