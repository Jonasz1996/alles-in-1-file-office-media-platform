import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Listing } from "./api";
import { Dialog } from "./Dialog";

/**
 * Kies een map om iets naartoe te kopiëren of te verplaatsen. Begint in startId (de map die
 * open staat). exclude: een map die je verplaatst of kopieert; daar kun je niet in, want
 * iets kan niet in zichzelf.
 */
export function FolderPicker({
  title,
  action,
  startId,
  exclude,
  onPick,
  onClose,
}: {
  title: string;
  action: string;
  startId: string | null;
  exclude?: string;
  onPick: (folderId: string) => void;
  onClose: () => void;
}) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loads = useRef(0);

  const open = useCallback(async (id: string | null) => {
    const seq = ++loads.current;
    try {
      const result = id ? await api.children(id) : await api.root();
      if (seq !== loads.current) return; // intussen een andere map gekozen
      setListing(result);
      setError(null);
    } catch (e) {
      if (seq === loads.current) setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    open(startId);
  }, [open, startId]);

  const folders = listing?.children.filter((c) => c.kind === "dir" && c.id !== exclude) ?? [];

  return (
    <Dialog title={title} onClose={onClose}>
      <nav className="crumbs" aria-label="Pad">
        {listing?.path.map((p, i) => (
          <span key={p.id}>
            {i > 0 && " / "}
            <button type="button" className="link" onClick={() => open(p.id)}>
              {i === 0 ? "Mijn bestanden" : p.name}
            </button>
          </span>
        ))}
      </nav>
      {error && <p role="alert">{error}</p>}
      {listing && folders.length === 0 && <p className="muted">Hier zijn geen submappen.</p>}
      {folders.length > 0 && (
        <ul className="picker-list">
          {folders.map((f) => (
            <li key={f.id}>
              <button type="button" onClick={() => open(f.id)}>
                📁 {f.name}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="dialog-actions">
        <button type="button" onClick={onClose}>
          Annuleren
        </button>
        <button
          type="button"
          className="primary"
          disabled={!listing || listing.folder.id === exclude}
          onClick={() => listing && onPick(listing.folder.id)}
        >
          {action}
        </button>
      </div>
    </Dialog>
  );
}
