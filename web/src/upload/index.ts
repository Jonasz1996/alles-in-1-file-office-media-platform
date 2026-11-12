// Het uploadcontract (signaturen frozen na P0; de uitvoering is van P3). Deze module en walk.ts
// worden statisch geïmporteerd: uploadDropped moet synchroon in de drop-handler lopen, vóór
// elke await, en mag dus niets lazy laden.

import type { ComponentType } from "react";
import { lazyWithAreas } from "../i18n";
import { startUpload } from "./start";
import { collect, type EntryLike } from "./walk";

export { UploadButtons } from "./UploadButtons";

/** Zitten er bestanden (van buiten de browser) in deze sleepactie? */
export function hasFiles(dt: DataTransfer | null): boolean {
  if (!dt) return false;
  return Array.from(dt.types ?? []).includes("Files");
}

/**
 * Bestanden en mappen die op een map zijn losgelaten uploaden. MOET synchroon in de
 * drop-handler worden aangeroepen, vóór elke await: daarna is dt leeg. Neemt hier meteen een
 * momentopname van dt.items (webkitGetAsEntry, voor mappen) en dt.files; het uitlezen van de
 * mappen gaat daarna asynchroon.
 */
export function uploadDropped(dt: DataTransfer, folderId: string): void {
  const items = Array.from(dt.items ?? []).filter((item) => item.kind === "file");
  const entries: (EntryLike | null)[] = items.map((item) =>
    typeof item.webkitGetAsEntry === "function" ? (item.webkitGetAsEntry() as EntryLike | null) : null,
  );
  const files = Array.from(dt.files ?? []);
  if (entries.length === 0 && files.length === 0) return;
  startUpload(folderId, collect(entries, files));
}

/** De uploadlade rechtsonder, met dubbele bestanden en onderbroken uploads. */
export const UploadTray: ComponentType = lazyWithAreas(() => import("./UploadTray"), ["upload"]);
