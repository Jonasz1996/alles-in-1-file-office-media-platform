import type { Translation } from "../types";
import type nl from "./dupes.nl";

const en: Translation<typeof nl> = {
  title: "Duplicate files",
  intro:
    "Files that are exactly the same, and photos that look alike. Nothing is ever ticked in advance: you choose what can go. It goes to the trash, where you can get it back.",
  exactNote:
    "A file that is exactly the same takes no extra space: its content is stored once. Cleaning up only makes things tidier.",
  tabs: {
    label: "Kind",
    exact: "Exactly the same",
    near: "Look alike",
  },
  level: {
    exact: "Exactly the same",
    zeker: "Almost certainly the same",
    lijkt: "Looks alike",
  },
  group: {
    files: { one: "{n} file", other: "{n} files" },
  },
  item: {
    view: "View {name}",
    pick: "Choose",
    pickLabel: "Choose {name} for the trash",
    taken: "Taken",
    modified: "Modified",
    size: "Size",
    folder: "Folder",
  },
  myFiles: "My files",
  trash: {
    none: "Move to trash",
    some: { one: "Move {n} file to trash", other: "Move {n} files to trash" },
    hint: "First tick what can go.",
    done: { one: "{n} file moved to trash", other: "{n} files moved to trash" },
    undo: "Undo",
    allTitle: "Remove everything in this group?",
    allMessage: {
      one: "You ticked the only file in this group.",
      other: "You ticked all {n} files in this group. None will be left. They go to the trash, where you can get them back.",
    },
    allConfirm: { one: "Move to trash", other: "Move all {n} to trash" },
  },
  notSame: "Not the same",
  ignored: "This group will not come back.",
  more: "Load more",
  loading: "Loading the groups…",
  failed: "The groups could not load.",
  empty: {
    exact: "No files that are exactly the same",
    near: "No photos that look alike",
    nearHint: "A new photo only shows up here once it has been read. Check again later.",
  },
};

export default en;
