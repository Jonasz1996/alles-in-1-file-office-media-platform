import type { Translation } from "../types";
import type nl from "./beeld.nl";

const en: Translation<typeof nl> = {
  title: "Search by image",
  notReady: "Search by image comes later in phase 6.",
  later: "Search by image is coming in a future version.",
  admin: {
    seen: "Looked at",
    seenValue: "{seen} of {photos} photos",
    queries: "Queries in the last hour",
    failed: "Failed",
  },
};

export default en;
