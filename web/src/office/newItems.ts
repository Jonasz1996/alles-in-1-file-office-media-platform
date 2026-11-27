// De items "Document", "Werkblad", "Presentatie" en "Tekstbestand" voor het menu Nieuw en het
// contextmenu van de map (Keuze 24 en 33). De teksten staan in het blok office van browser.* (O0).
// Van O4; Tekstbestand kwam er in 0.8.3 bij.
//
// Document, Werkblad en Presentatie zijn er als de server zegt dat er nu bewerkt kan worden
// (status.editing): zonder office, met office uitgezet of met de editor onbereikbaar niet.
// Tekstbestand is er altijd: de server maakt een leeg .txt ook zonder office, en de viewer bewerkt
// tekst zelf (viewer/TextView.tsx). Daardoor is er in een map waar je mag schrijven altijd het menu
// Nieuw (Map en Tekstbestand, met office ook de rest); Files.tsx vraagt de items alleen daar.
//
// Een item maakt het lege bestand (POST /office/new, zonder naam: de server kiest hem in de taal
// van de gebruiker), zet het in de lijst, meldt het aan een schermlezer en opent het meteen: een
// kantoordocument in de editor (/bewerken/<id>, de pagina van O2; Terug of Sluiten gaat terug naar
// de map), een tekstbestand in de viewer, meteen in bewerkmodus (Files.tsx: editText). Een fout
// (quotum vol, de map is weg, alleen leesrecht) wordt een melding; dan blijf je in de map.

import type { FileNode } from "../api";
import { errorText, t } from "../i18n";
import { nodeHref } from "../links";
import { navigate } from "../router";
import type { MenuItem } from "../ui/menu";
import { announce, toast } from "../ui/toast";
import { openEditor } from "./actions";
import { newDocument } from "./api";
import type { NewDocumentIn, NewKind, OfficeKind, OfficeStatus } from "./types";

export interface NewItemsContext {
  /** De map die open staat; Files.tsx vraagt het alleen als je daar mag schrijven */
  folder: FileNode;
  status: OfficeStatus | null;
  /** Het nieuwe bestand staat er: in de lijst zetten */
  added(node: FileNode): void;
  /** Een nieuw tekstbestand in de viewer openen, meteen om te bewerken (Bestanden: ?bestand=<id>
   * en de prop editText van de viewer); zonder: naar de map met ?bestand= */
  openText?(node: FileNode): void;
}

/** Wat een item doet; los te vervangen in de tests. */
export interface NewItemsDeps {
  create(body: NewDocumentIn): Promise<FileNode>;
  /** Een kantoordocument openen (de editor) */
  open(node: FileNode): void;
}

const DEFAULT_DEPS: NewItemsDeps = { create: newDocument, open: openEditor };

/** De soorten van de editor in de volgorde van het menu, na Map (zoals browser.office.new van
 * O0). Tekstbestand komt daarna, en is er altijd. */
export const NEW_KINDS: readonly OfficeKind[] = ["docx", "xlsx", "pptx"];

/** Mappen waar nu een document gemaakt wordt: een tweede keer kiezen maakt er geen tweede. */
const busy = new Set<string>();

/** De items na "Map" in het menu Nieuw (en in het contextmenu van de map). */
export function newItems(ctx: NewItemsContext, deps: NewItemsDeps = DEFAULT_DEPS): MenuItem[] {
  const status = ctx.status;
  const office: OfficeKind[] = status?.editing ? NEW_KINDS.filter((kind) => status.new_kinds.includes(kind)) : [];
  const kinds: NewKind[] = [...office, "txt"];
  return kinds.map((kind) => ({
    id: `new-${kind}`,
    label: t(`browser.office.new.${kind}`),
    icon: "document",
    onSelect: () => void createDocument(ctx, kind, deps),
  }));
}

/** Een nieuw tekstbestand openen in de viewer (Bestanden: meteen om te bewerken). */
function openText(ctx: Pick<NewItemsContext, "openText">, node: FileNode): void {
  if (ctx.openText) ctx.openText(node);
  else navigate(nodeHref(node));
}

/** Maakt het lege bestand en opent het (de editor, of de viewer voor tekst). Geeft het nieuwe
 * bestand, of null (een fout, die als melding komt, of er werd in deze map al een gemaakt). */
export async function createDocument(
  ctx: Pick<NewItemsContext, "folder" | "added" | "openText">,
  kind: NewKind,
  deps: NewItemsDeps = DEFAULT_DEPS,
): Promise<FileNode | null> {
  const folderId = ctx.folder.id;
  if (busy.has(folderId)) return null;
  busy.add(folderId);
  try {
    const node = await deps.create({ parent_id: folderId, kind });
    ctx.added(node);
    announce(t("browser.office.new.created", { name: node.name }));
    if (kind === "txt") openText(ctx, node);
    else deps.open(node);
    return node;
  } catch (e) {
    toast(errorText(e), { kind: "error" });
    return null;
  } finally {
    busy.delete(folderId);
  }
}
