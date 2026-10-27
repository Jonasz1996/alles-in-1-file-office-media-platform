import type { Translation } from "../types";
import type nl from "./office.nl";

const en: Translation<typeof nl> = {
  title: "Edit",
  page: {
    close: "Close",
    saveNow: "Save now",
    frameTitle: "Editor: {name}",
    opening: "Opening the editor…",
    reload: "Reload",
    leaveHint: "Alt+Shift+Q: from the editor to this bar",
  },
  status: {
    connecting: "Connecting…",
    editing: "Editing",
    others: { one: "Editing with 1 other person", other: "Editing with {n} others" },
    saving: "Saving…",
    saved: "Saved at {time}",
    offline: "Connection lost",
    closed: "The editor is closed",
  },
  notice: {
    stale:
      "This file changed outside the editor. What the editor saves becomes the newest version; the other content stays under Versions.",
    busy: "This document is being saved; it opens again in a moment.",
    offline: "The connection to the editor is lost. What was saved last is in the file.",
    dropped: "You no longer have access; the editor was closed.",
    notReady: "The editor did not start. Reload the page.",
    error: "The editor reports an error (code {code}). Reload the page.",
    unsaved: {
      no_room: "Not saved in between: the owner's storage is full. Your work is still in the editor.",
      too_large: "Not saved in between: the document has become too large. Your work is still in the editor.",
      owner_gone: "Not saved: the owner of this document no longer exists.",
      error_status: "The editor could not store the document. Your work is still in the editor.",
    },
  },
  toast: {
    unsaved: {
      no_room: "{name} was not saved: there is no storage left. The administrator can restore it once there is room.",
      too_large: "{name} was not saved: the document is too large.",
      owner_gone: "{name} was not saved: the owner no longer exists.",
      error_status: "{name} was not saved: the editor reported an error. The administrator sees it under Admin.",
    },
    recovered: {
      status3: "Your work was kept as a recovered copy: {name}",
      no_writer: "Nobody could edit the original any more. Your work is in the recovered copy {name}",
      forgotten: "A document that had not been saved was restored: {name}",
    },
    open: "Open",
  },
};

export default en;
