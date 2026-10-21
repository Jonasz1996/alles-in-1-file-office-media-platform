import { useCallback, useEffect, useRef, useState } from "react";
import { api, type FileNode, type Listing } from "./api";
import { isGuest, useCurrentUser } from "./currentUser";
import { Dialog } from "./Dialog";
import "./files.css";
import { isShared } from "./sharing";

/**
 * Kies een map om iets naartoe te kopiëren of te verplaatsen. Begint in startId (de map die
 * open staat). exclude: een map die je verplaatst of kopieert; daar kun je niet in, want
 * iets kan niet in zichzelf. check: waarom een map niet als doel kan (bijv. alleen kijken),
 * of null als het mag. Behalve je eigen bestanden staan ook de mappen die met je gedeeld
 * zijn erbij ("Gedeeld met mij"); gasten hebben alleen die.
 */
export function FolderPicker({
  title,
  action,
  startId,
  exclude,
  check,
  onPick,
  onClose,
}: {
  title: string;
  action: string;
  startId: string | null;
  exclude?: string;
  check?: (folder: FileNode) => string | null;
  onPick: (folderId: string) => void;
  onClose: () => void;
}) {
  const guest = isGuest(useCurrentUser());
  const [listing, setListing] = useState<Listing | null>(null);
  // De lijst met wat met je gedeeld is, in plaats van een map
  const [shared, setShared] = useState<FileNode[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loads = useRef(0);

  const open = useCallback(async (id: string | null) => {
    const seq = ++loads.current;
    try {
      const result = id ? await api.children(id) : await api.root();
      if (seq !== loads.current) return; // intussen een andere map gekozen
      setListing(result);
      setShared(null);
      setError(null);
    } catch (e) {
      if (seq === loads.current) setError((e as Error).message);
    }
  }, []);

  const openShared = useCallback(async () => {
    const seq = ++loads.current;
    try {
      const rows = await api.sharedWithMe();
      if (seq !== loads.current) return;
      setShared(rows.map((r) => r.node).filter((n) => n.kind === "dir"));
      setListing(null);
      setError(null);
    } catch (e) {
      if (seq === loads.current) setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    // Een gast heeft geen eigen hoofdmap
    if (!startId && guest) openShared();
    else open(startId);
  }, [open, openShared, startId, guest]);

  const folders = listing?.children.filter((c) => c.kind === "dir" && c.id !== exclude) ?? [];
  const inShared = listing ? isShared(listing.path[0]) : shared !== null;
  const reason = listing ? (check?.(listing.folder) ?? null) : null;

  return (
    <Dialog title={title} onClose={onClose}>
      <div className="toolbar" style={{ marginTop: 0 }}>
        {!guest && (
          <button type="button" aria-pressed={!inShared} onClick={() => open(null)}>
            Mijn bestanden
          </button>
        )}
        <button type="button" aria-pressed={inShared} onClick={openShared}>
          Gedeeld met mij
        </button>
      </div>
      {listing && (
        <nav className="crumbs" aria-label="Pad">
          {inShared && (
            <span>
              <button type="button" className="link" onClick={openShared}>
                Gedeeld met mij
              </button>
            </span>
          )}
          {listing.path.map((p, i) => (
            <span key={p.id}>
              {(i > 0 || inShared) && " / "}
              <button type="button" className="link" onClick={() => open(p.id)}>
                {i === 0 && !inShared ? "Mijn bestanden" : p.name}
              </button>
            </span>
          ))}
        </nav>
      )}
      {error && <p role="alert">{error}</p>}
      {shared && shared.length === 0 && <p className="muted">Er zijn geen mappen met je gedeeld.</p>}
      {shared && shared.length > 0 && (
        <ul className="picker-list">
          {shared.map((f) => (
            <li key={f.id}>
              <button type="button" onClick={() => open(f.id)}>
                📁 {f.name}
                {f.owner && <span className="muted"> · van {f.owner.display_name}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
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
      {reason && <p className="muted">{reason}</p>}
      <div className="dialog-actions">
        <button type="button" onClick={onClose}>
          Annuleren
        </button>
        <button
          type="button"
          className="primary"
          disabled={!listing || listing.folder.id === exclude || reason !== null}
          onClick={() => listing && onPick(listing.folder.id)}
        >
          {action}
        </button>
      </div>
    </Dialog>
  );
}
