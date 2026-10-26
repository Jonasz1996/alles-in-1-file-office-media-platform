// Wanneer "Bewerken" er is (formaat, schrijfrecht, office aan), voor de haak in
// browser/actions.ts en de knop in de viewer (Keuze 22, 23 en 32).
//
// Van O2. O0 zet dit als stub met de vaste vorm: canEdit() geeft een HookState, openEditor()
// gaat naar de editorpagina (zolang O2 die niet heeft, zegt die "nog niet klaar"). O2 maakt de
// regels af; de haken blijven zo.

import { canWrite, permissionOf } from "../sharing";
import { navigate } from "../router";
import { editorPath } from "./index";
import type { HookState, OfficeStatus, OfficeTarget } from "./types";

const HIDDEN: HookState = { visible: false, reason: null };

/** De extensie van een naam, in kleine letters ("" zonder punt). */
function extension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

/** Is "Bewerken" er voor dit bestand, en kan het nu? Zonder status (office uit of onbekend):
 * nooit. */
export function canEdit(target: OfficeTarget, status: OfficeStatus | null): HookState {
  if (!status?.enabled) return HIDDEN;
  if (target.kind !== undefined && target.kind !== "file") return HIDDEN;
  if (target.version) return HIDDEN;
  if (!status.edit_formats.includes(extension(target.name))) return HIDDEN;
  if (!canWrite(permissionOf(target))) return HIDDEN;
  if (target.deleted_at) return { visible: true, reason: "trashed" };
  if (target.size > status.max_bytes) return { visible: true, reason: "too_large" };
  if (!status.editing) return { visible: true, reason: "office_down" };
  return { visible: true, reason: null };
}

/** Opent de editor voor dit bestand (een eigen pagina; Terug sluit hem weer). */
export function openEditor(target: Pick<OfficeTarget, "id">): void {
  navigate(editorPath(target.id));
}
