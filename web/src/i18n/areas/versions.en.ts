import type { Translation } from "../types";
import type nl from "./versions.nl";

const en: Translation<typeof nl> = {
  title: "Versions of {name}",
  now: "Now: {size}, changed {date}.",
  keepOwn: "The 20 most recent earlier versions are kept; they count towards your quota.",
  keepOwner: "The 20 most recent earlier versions are kept; they count towards the owner's quota.",
  upload: "Upload new version",
  uploading: "Uploading: {percent} %",
  progress: "Upload progress",
  cancelUpload: "Cancel upload",
  cancelled: "Upload cancelled.",
  uploaded: "New version saved; the previous one is listed below.",
  none: "There are no earlier versions yet.",
  noneHint: "If you upload a new version, the current one is kept here.",
  list: "Earlier versions",
  replaced: "replaced {date} by {name}",
  unknown: "unknown",
  versionLabel: "Version of {date}",
  view: "View",
  viewLabel: "View version of {date}",
  download: "Download",
  restore: "Restore",
  restoreTitle: "Restore this version?",
  restoreText: "What is in it now is kept as a version.",
  restored: "Version restored.",
  remove: "Delete",
  removeTitle: "Permanently delete this version?",
  removeText: "This cannot be undone.",
  removed: "Version deleted.",
  keys: {
    upload: "Upload new version",
  },
};

export default en;
