import type { Translation } from "../types";
import type nl from "./browser.nl";

const en: Translation<typeof nl> = {
  title: "Files",
  searchTitle: "Search",
  selected: { one: "{n} selected", other: "{n} selected" },
};

export default en;
