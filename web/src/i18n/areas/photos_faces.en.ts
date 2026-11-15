import type { Translation } from "../types";
import type nl from "./photos_faces.nl";

const en: Translation<typeof nl> = {
  title: "Faces",
  notReady: "Face recognition comes later in phase 6.",
  admin: {
    faces: "Faces",
    named: "Of which named",
    usersOn: "Users with faces on",
    waiting: "Photos in the queue",
    failed: "Failed",
  },
};

export default en;
