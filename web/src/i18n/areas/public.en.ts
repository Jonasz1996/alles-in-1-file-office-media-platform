import type { Translation } from "../types";
import type nl from "./public.nl";

const en: Translation<typeof nl> = {
  title: "Shared link",
  pageTitle: "{name} · shared",
  brand: "Platform",
  gone: "This link does not exist (anymore)",
  expired: "This link has expired",
  askNew: "Ask whoever sent the link for a new one.",
  failed: "That did not work",
  sharedBy: "Shared by {owner}",
  linkExpiry: " · the link {expiry}",
  password: {
    title: "Password needed",
    intro: "This link needs a password. You get it from whoever sent the link.",
    label: "Password",
    open: "Open",
  },
  folder: {
    crumbs: "Path",
    backTo: "Back to {name}",
    empty: "This folder is empty.",
    name: "Name",
    size: "Size",
    modified: "Modified",
    actions: "Actions",
    download: "Download",
    downloadName: "Download {name}",
  },
  file: {
    view: "View",
    download: "Download",
    pages: { one: "1 page", other: "{n} pages" },
    pixels: "{w} × {h}",
    modified: "modified {date}",
  },
  upload: {
    intro: "Here you can leave files for {owner}. You cannot see what is already in the folder.",
    drop: "Drag files here, or",
    choose: "Choose files",
    resume: "If the connection drops, choose the same file again: it continues where it left off.",
    retry: "Try again",
    discard: "Discard",
    discarding: "Discarding…",
    cancel: "Cancel",
    percent: "{n} %",
    arrived: "Your files have arrived",
    arrivedSoFar: "Arrived so far",
  },
};

export default en;
