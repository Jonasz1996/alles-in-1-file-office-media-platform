// Een upload beginnen vanuit de knoppen of een drop (statisch geïmporteerd, net als walk.ts).

import { errorText, t } from "../i18n";
import { toast } from "../ui/toast";
import { uploadQueue } from "../uploadQueue";
import { type Collected, fromInput } from "./walk";

/** Een batch in map folderId: mappen, bezette namen, één vraag, dan de uploads (addTree).
 * Fouten (geen recht, offline) als melding. */
export function startUpload(folderId: string, collected: Collected | Promise<Collected>): void {
  Promise.resolve(collected)
    .then((c) => {
      if (c.rejected.length) toast(t("upload.tray.rejected", { names: c.rejected.join(", ") }), { kind: "error" });
      if (c.files.length === 0 && c.dirs.length === 0) return;
      return uploadQueue.addTree(folderId, c);
    })
    .catch((e: unknown) => toast(errorText(e), { kind: "error" }));
}

/** Bestanden uit een gewone bestandskiezer of "Map uploaden" (webkitRelativePath). */
export function uploadFiles(folderId: string, files: readonly File[]): void {
  if (files.length) startUpload(folderId, fromInput(files));
}

