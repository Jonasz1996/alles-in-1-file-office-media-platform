import type { Translation } from "../types";
import type nl from "./trash.nl";

const en: Translation<typeof nl> = {
  title: "Trash",
  empty: "The trash is empty.",
  emptyText: "What you throw away stays here until you restore it or delete it permanently.",
  list: "Contents of the trash",
  total: {
    one: "{n} item, {size} in total",
    other: "{n} items, {size} in total",
  },
  selected: { one: "{n} selected", other: "{n} selected" },
  name: "Name",
  wasIn: "Was in",
  size: "Size",
  deleted: "Deleted",
  rootFolder: "My files",
  toRoot: "returns to My files",
  toRootHint: "The folder it was in no longer exists or is in the trash itself.",
  select: "Select {name}",
  restore: "Restore",
  restoreN: "Restore ({n})",
  purge: "Delete permanently",
  purgeN: "Delete permanently ({n})",
  emptyTrash: "Empty trash",
  purgeTitle: {
    one: "Permanently delete {n} item?",
    other: "Permanently delete {n} items?",
  },
  purgeOneTitle: "Permanently delete {name}?",
  purgeText: "This cannot be undone. Whatever is inside a folder goes too.",
  emptyTitle: "Empty the trash?",
  emptyConfirmText: "Everything in the trash ({count}, {size}) is deleted permanently. This cannot be undone.",
  restored: { one: "{n} item restored.", other: "{n} items restored." },
  purged: { one: "{n} item deleted permanently.", other: "{n} items deleted permanently." },
  emptied: { one: "The trash is empty ({n} item deleted).", other: "The trash is empty ({n} items deleted)." },
  failed: { one: "{n} item failed: {reason}", other: "{n} items failed: {reason}" },
  keys: {
    restore: "Restore selection",
    purge: "Delete selection permanently",
    selectAll: "Select all",
    clear: "Clear selection",
    toggle: "(De)select the focused item",
    emptyTrash: "Empty trash",
  },
};

export default en;
