import { useState } from "react";
import { api, type FileNode } from "./api";
import { FolderPicker } from "./FolderPicker";
import { TagEditor } from "./Tags";
import { Versions } from "./Versions";

type Action = "copy" | "move" | "versions" | "tags";

/** Het menu "Meer…" bij een map of bestand: kopiëren, verplaatsen, versies en tags. */
export function NodeActions({
  node,
  folderId,
  onChanged,
  onError,
}: {
  node: FileNode;
  /** De map die nu open staat; daar begint het kiezen van een doel. */
  folderId: string | null;
  onChanged: () => void;
  onError: (message: string) => void;
}) {
  const [open, setOpen] = useState<Action | null>(null);
  const close = () => setOpen(null);

  async function run(fn: () => Promise<unknown>) {
    close();
    try {
      await fn();
      onChanged();
    } catch (e) {
      onError((e as Error).message);
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
        <option value="copy">Kopiëren…</option>
        <option value="move">Verplaatsen…</option>
        {node.kind === "file" && <option value="versions">Versies…</option>}
        <option value="tags">Tags…</option>
      </select>
      {open === "copy" && (
        <FolderPicker
          title={`"${node.name}" kopiëren naar`}
          action="Hierheen kopiëren"
          startId={folderId}
          exclude={exclude}
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
          onPick={(target) => run(() => api.change(node.id, { parent_id: target }))}
          onClose={close}
        />
      )}
      {open === "versions" && <Versions node={node} onClose={close} onChanged={onChanged} />}
      {open === "tags" && <TagEditor node={node} onClose={close} onSaved={onChanged} />}
    </>
  );
}
