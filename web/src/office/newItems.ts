// De items "Document", "Werkblad" en "Presentatie" voor het menu Nieuw en het contextmenu van de
// map (Keuze 24 en 33). De teksten staan in het blok office van browser.* (O0). Van O4.
//
// Er zijn items als de server zegt dat er nu bewerkt kan worden (status.editing): zonder office,
// met office uitgezet of met de editor onbereikbaar geen enkel, en dan blijft de knop "Nieuwe
// map" in Bestanden precies zoals in fase 3 (Files.tsx toont het menu Nieuw pas als hier iets
// staat). Files.tsx vraagt de items alleen voor een map waar je mag schrijven.
//
// Een item maakt het lege document (POST /office/new, zonder naam: de server kiest hem in de taal
// van de gebruiker), zet het in de lijst, meldt het aan een schermlezer en opent meteen de editor
// (/bewerken/<id>, de pagina van O2). Terug of Sluiten in de editor gaat dan terug naar de map. Een
// fout (quotum vol, de map is weg, alleen leesrecht) wordt een melding; dan blijf je in de map.

import type { FileNode } from "../api";
import { errorText, t } from "../i18n";
import type { MenuItem } from "../ui/menu";
import { announce, toast } from "../ui/toast";
import { openEditor } from "./actions";
import { newDocument } from "./api";
import type { NewDocumentIn, OfficeKind, OfficeStatus } from "./types";

export interface NewItemsContext {
  /** De map die open staat; Files.tsx vraagt het alleen als je daar mag schrijven */
  folder: FileNode;
  status: OfficeStatus | null;
  /** Het nieuwe bestand staat er: in de lijst zetten */
  added(node: FileNode): void;
}

/** Wat een item doet; los te vervangen in de tests. */
export interface NewItemsDeps {
  create(body: NewDocumentIn): Promise<FileNode>;
  open(node: FileNode): void;
}

const DEFAULT_DEPS: NewItemsDeps = { create: newDocument, open: openEditor };

/** De soorten in de volgorde van het menu, na Map (zoals browser.office.new van O0). */
export const NEW_KINDS: readonly OfficeKind[] = ["docx", "xlsx", "pptx"];

/** Mappen waar nu een document gemaakt wordt: een tweede keer kiezen maakt er geen tweede. */
const busy = new Set<string>();

/** De items na "Map" in het menu Nieuw (en in het contextmenu van de map). */
export function newItems(ctx: NewItemsContext, deps: NewItemsDeps = DEFAULT_DEPS): MenuItem[] {
  const status = ctx.status;
  if (!status?.editing) return [];
  return NEW_KINDS.filter((kind) => status.new_kinds.includes(kind)).map((kind) => ({
    id: `new-${kind}`,
    label: t(`browser.office.new.${kind}`),
    icon: "document",
    onSelect: () => void createDocument(ctx, kind, deps),
  }));
}

/** Maakt het lege document en opent het in de editor. Geeft het nieuwe bestand, of null (een
 * fout, die als melding komt, of er werd in deze map al een gemaakt). */
export async function createDocument(
  ctx: Pick<NewItemsContext, "folder" | "added">,
  kind: OfficeKind,
  deps: NewItemsDeps = DEFAULT_DEPS,
): Promise<FileNode | null> {
  const folderId = ctx.folder.id;
  if (busy.has(folderId)) return null;
  busy.add(folderId);
  try {
    const node = await deps.create({ parent_id: folderId, kind });
    ctx.added(node);
    announce(t("browser.office.new.created", { name: node.name }));
    deps.open(node);
    return node;
  } catch (e) {
    toast(errorText(e), { kind: "error" });
    return null;
  } finally {
    busy.delete(folderId);
  }
}
