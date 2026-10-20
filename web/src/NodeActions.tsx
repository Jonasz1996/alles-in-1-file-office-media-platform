import { useState } from "react";
import { api, type FileNode } from "./api";
import { FolderPicker } from "./FolderPicker";
import { ShareDialog } from "./ShareDialog";
import { canWrite, nodeActions, permissionOf } from "./sharing";
import { TagEditor } from "./Tags";
import { Versions } from "./Versions";

type Action = "copy" | "move" | "versions" | "tags" | "share";

/** Waarom je hier niets heen kunt kopiëren of verplaatsen, of null als het mag. */
function writable(folder: FileNode): string | null {
  return canWrite(permissionOf(folder)) ? null : "Je mag hier alleen kijken";
}

/** Het menu "Meer…" bij een map of bestand: kopiëren, verplaatsen, versies, tags en delen. */
export function NodeActions({
  node,
  folderId,
  onChanged,
  onError,
  shareRoot = false,
}: {
  node: FileNode;
  /** De map die nu open staat; daar begint het kiezen van een doel. */
  folderId: string | null;
  onChanged: () => void;
  onError: (message: string) => void;
  /** Dit is de gedeelde map (of het gedeelde bestand) zelf: verplaatsen doet alleen de eigenaar. */
  shareRoot?: boolean;
}) {
  const [open, setOpen] = useState<Action | null>(null);
  const close = () => setOpen(null);
  const can = nodeActions(node, shareRoot);

  async function run(fn: () => Promise<unknown>) {
    close();
    try {
      await fn();
      onChanged();
    } catch (e) {
      onError((e as Error).message); // ook "Je mag hier alleen kijken" en dergelijke van de server
    }
  }

  // Een map kan niet in zichzelf; een bestand kan overal heen
  const exclude = node.kind === "dir" ? node.id : undefined;

  return (
    <>
      <select
        aria-label={`Meer acties voor ${node.name}`}
        value=""
        onChange={(e) => setOpen((e.target.value || null) as Action | null)}
      >
        <option value="">Meer…</option>
        {can.copy && <option value="copy">Kopiëren…</option>}
        {can.move && <option value="move">Verplaatsen…</option>}
        {can.versions && <option value="versions">Versies…</option>}
        {can.tags && <option value="tags">Tags…</option>}
        {can.share && <option value="share">Delen…</option>}
      </select>
      {open === "copy" && (
        <FolderPicker
          title={`"${node.name}" kopiëren naar`}
          action="Hierheen kopiëren"
          startId={folderId}
          exclude={exclude}
          check={writable}
          onPick={(target) => run(() => api.copy(node.id, target))}
          onClose={close}
        />
      )}
      {open === "move" && (
        <FolderPicker
          title={`"${node.name}" verplaatsen naar`}
          action="Hierheen verplaatsen"
          startId={folderId}
          exclude={exclude}
          check={(folder) =>
            writable(folder) ??
            (folder.owner && node.owner && folder.owner.id !== node.owner.id
              ? "Verplaatsen kan alleen binnen dezelfde eigenaar; kopieer het in plaats daarvan"
              : null)
          }
          onPick={(target) => run(() => api.change(node.id, { parent_id: target }))}
          onClose={close}
        />
      )}
      {open === "versions" && <Versions node={node} onClose={close} onChanged={onChanged} />}
      {open === "tags" && <TagEditor node={node} onClose={close} onSaved={onChanged} />}
      {open === "share" && <ShareDialog node={node} onClose={close} onChanged={onChanged} />}
    </>
  );
}
