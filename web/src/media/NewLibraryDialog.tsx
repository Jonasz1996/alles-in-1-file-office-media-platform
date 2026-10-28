// De dialoog "Als bibliotheek gebruiken" (fase 5, van M2): het soort kiezen (films, series,
// muziek, gemengd) en POST /api/v1/media/libraries. De haak in het menu van een map
// (browser/actions.ts, M0) laadt dit bestand en het gebied "media", en roept
// openNewLibraryDialog aan. M0 zette een lege versie; M2 maakt de dialoog.

import type { FileNode } from "../api";
import { t } from "../i18n";
import { toast } from "../ui/toast";

/** Opent de dialoog voor deze map (een map van jezelf, zonder bibliotheek). */
export function openNewLibraryDialog(_folder: FileNode): void {
  toast(t("media.newLibrary.notReady"));
}
