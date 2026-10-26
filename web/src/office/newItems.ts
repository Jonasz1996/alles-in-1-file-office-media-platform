// De items "Document", "Werkblad" en "Presentatie" voor het menu Nieuw en het contextmenu van de
// map (Keuze 24 en 32). De teksten staan in het blok office van browser.* (O0).
//
// Van O4. O0 zet dit als stub met de vaste vorm: nog geen items, dus blijft de knop "Nieuwe
// map" in Bestanden precies zoals in fase 3 (Files.tsx toont het menu Nieuw pas als hier iets
// staat). O4 vult het: alleen als status.editing, POST /office/new, dan naar /bewerken/<id>.

import type { FileNode } from "../api";
import type { MenuItem } from "../ui/menu";
import type { OfficeStatus } from "./types";

export interface NewItemsContext {
  /** De map die open staat; Files.tsx vraagt het alleen als je daar mag schrijven */
  folder: FileNode;
  status: OfficeStatus | null;
  /** Het nieuwe bestand staat er: in de lijst zetten */
  added(node: FileNode): void;
}

/** De items na "Map" in het menu Nieuw (en in het contextmenu van de map). */
export function newItems(_ctx: NewItemsContext): MenuItem[] {
  return [];
}
