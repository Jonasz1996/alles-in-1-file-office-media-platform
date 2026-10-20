import { type DragEvent, type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, type FileNode, formatBytes, formatDate, type Listing } from "./api";
import { isGuest, useCurrentUser } from "./currentUser";
import { useFolderEvents } from "./events";
import { NodeActions } from "./NodeActions";
import { PreviewPanel } from "./PreviewPanel";
import { navigate } from "./router";
import { SearchBox } from "./Search";
import { ShareDialog } from "./ShareDialog";
import { crumbs, folderActions, isShared, nodeActions, PERMISSION_LABELS, permissionOf } from "./sharing";
import { TagChips } from "./Tags";
import { Thumb, usePreviews } from "./Thumb";
import { uploadQueue, useUploads } from "./uploadQueue";
import "./share.css";

/** Een link binnen de app, zonder de pagina te herladen. */
export function Link({ to, children }: { to: string; children: ReactNode }) {
  return (
    <a
      href={to}
      onClick={(e) => {
        e.preventDefault();
        navigate(to);
      }}
    >
      {children}
    </a>
  );
}

/**
 * Een map: van jezelf, of een map die met je gedeeld is. onShared: of de map die nu open
 * staat van iemand anders is (voor de navigatie bovenaan).
 */
export function Files({ folderId, onShared }: { folderId: string | null; onShared?: (shared: boolean) => void }) {
  const guest = isGuest(useCurrentUser());
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState<string | null>(null);
  // De map is weg of je hebt er geen toegang (meer) toe; de server zegt bewust niet welke
  const [gone, setGone] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);
  const uploads = useUploads();
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const loads = useRef(0);
  /** De laatst getoonde map, als die met je gedeeld was */
  const sharedFolder = useRef<string | null>(null);
  const [shown, setShown] = useState<FileNode | null>(null);
  const previews = usePreviews(listing?.children);

  const load = useCallback(async () => {
    const seq = ++loads.current;
    try {
      const result = folderId ? await api.children(folderId) : await api.root();
      if (seq !== loads.current) return; // intussen een nieuwere lijst gevraagd (andere map)
      sharedFolder.current = isShared(result.folder) ? result.folder.id : null;
      setListing(result);
      setError(null);
      setGone(null);
    } catch (e) {
      if (seq !== loads.current) return;
      if (e instanceof ApiError && e.status === 404 && folderId) {
        setListing(null);
        setGone(
          sharedFolder.current === folderId
            ? "Je hebt geen toegang meer tot deze map"
            : "Deze map bestaat niet (meer), of je hebt er geen toegang toe",
        );
      } else setError((e as Error).message);
    }
  }, [folderId]);

  useEffect(() => {
    load();
  }, [load]);

  const current = listing?.folder.id;
  const shared = isShared(listing?.folder);
  const can = folderActions(listing?.folder);

  useEffect(() => {
    if (listing) onShared?.(isShared(listing.folder));
  }, [listing, onShared]);

  // Wat klaar is met wachten (upload, actie) gebruikt de load en map van nu, niet die van
  // toen het begon: de gebruiker kan intussen naar een andere map zijn gegaan.
  const latest = useRef({ load, current });
  useEffect(() => {
    latest.current = { load, current };
  });

  useEffect(
    () =>
      uploadQueue.onFinished((parent) => {
        if (parent === latest.current.current) latest.current.load();
      }),
    [],
  );
  // Live: wijzigingen van elders; in een gedeelde map ook als de deling verandert (andere
  // rechten, of ingetrokken)
  useFolderEvents(current, () => latest.current.load(), 250, shared);

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn();
      await latest.current.load();
    } catch (e) {
      setError((e as Error).message); // ook "Je mag hier alleen kijken" en dergelijke
    }
  }

  function uploadFiles(files: FileList) {
    if (!current) return;
    uploadQueue.add(current, Array.from(files)).catch((e: Error) => setError(e.message));
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    if (!e.dataTransfer.files.length) return;
    if (can.upload) uploadFiles(e.dataTransfer.files);
    else if (listing) setError("Je mag hier alleen kijken");
  }

  function open(node: FileNode) {
    if (node.kind === "dir") navigate(`/map/${node.id}`);
    else setShown(node);
  }

  async function download(node: FileNode) {
    const a = document.createElement("a");
    a.href = await api.downloadUrl(node.id);
    a.download = node.name;
    a.click();
  }

  return (
    <main
      className={`wide${dragging ? " dropping" : ""}`}
      onDragOver={(e) => {
        e.preventDefault(); // anders opent de browser het bestand
        if (!can.upload) e.dataTransfer.dropEffect = "none";
        setDragging(can.upload);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      <nav className="crumbs" aria-label="Pad">
        {listing &&
          crumbs(listing.path).map((c, i) => (
            <span key={c.href}>
              {i > 0 && " / "}
              <Link to={c.href}>{c.label}</Link>
            </span>
          ))}
      </nav>
      {listing && shared && (
        <p className="muted folder-note">
          {listing.folder.owner && <>Gedeeld door {listing.folder.owner.display_name} · </>}
          {permissionOf(listing.folder) === "write" ? PERMISSION_LABELS.write : "Je mag hier alleen kijken"}
        </p>
      )}

      {gone && (
        <>
          <p role="alert">{gone}</p>
          <p>
            <Link to="/gedeeld">Naar Gedeeld met mij</Link>
            {!guest && (
              <>
                {" of "}
                <Link to="/">Mijn bestanden</Link>
              </>
            )}
          </p>
        </>
      )}

      <div className="toolbar">
        {can.upload && (
          <button type="button" onClick={() => input.current?.click()}>
            Uploaden
          </button>
        )}
        {can.mkdir && (
          <button
            type="button"
            onClick={() => {
              const name = window.prompt("Naam van de nieuwe map");
              if (name && current) act(() => api.mkdir(current, name));
            }}
          >
            Nieuwe map
          </button>
        )}
        {can.share && (
          <button type="button" onClick={() => setSharing(true)}>
            Delen…
          </button>
        )}
        <input
          ref={input}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files) uploadFiles(e.target.files);
            e.target.value = "";
          }}
        />
        {can.upload && <span className="muted">of sleep bestanden hierheen</span>}
        <SearchBox />
      </div>

      {error && <p role="alert">{error}</p>}

      {uploads.length > 0 && (
        <ul className="uploads">
          {uploads.map((u) => (
            <li key={u.key}>
              <span className="upload-name">{u.file.name}</span>
              {u.state === "failed" ? (
                <>
                  <span role="alert">{u.error}</span>
                  <button type="button" onClick={() => uploadQueue.run(u.key)}>
                    Opnieuw proberen
                  </button>
                  {/* Weggooien, niet alleen sluiten: anders houdt de server de ruimte tot 24 uur vast */}
                  <button type="button" onClick={() => uploadQueue.discard(u.key)}>
                    Weggooien
                  </button>
                </>
              ) : u.state === "discarding" ? (
                <span className="muted">Weggooien…</span>
              ) : (
                <>
                  <progress max={1} value={u.progress} />
                  <span className="muted">{Math.floor(u.progress * 100)} %</span>
                  {u.state === "running" && (
                    <button type="button" onClick={() => uploadQueue.cancel(u.key)}>
                      Annuleren
                    </button>
                  )}
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      {listing && listing.children.length === 0 && <p className="muted">Deze map is leeg.</p>}
      {listing && listing.children.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Naam</th>
                <th>Grootte</th>
                <th>Gewijzigd</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {listing.children.map((n) => {
                const a = nodeActions(n);
                return (
                  <tr key={n.id}>
                    <td>
                      <button type="button" className="link" onClick={() => open(n)}>
                        <Thumb node={n} preview={previews[n.id]} />
                        {n.name}
                      </button>
                      {n.shared && (
                        <span className="shared-mark" title="Je hebt dit gedeeld, met iemand of via een link">
                          gedeeld
                        </span>
                      )}
                      {isShared(n) && n.owner && <span className="owner-note">van {n.owner.display_name}</span>}
                      <TagChips tags={n.tags} />
                    </td>
                    <td>{n.kind === "file" ? formatBytes(n.size) : ""}</td>
                    <td>{formatDate(n.modified_at)}</td>
                    <td className="actions">
                      {a.download && (
                        <button type="button" onClick={() => download(n)}>
                          Download
                        </button>
                      )}
                      {a.rename && (
                        <button
                          type="button"
                          onClick={() => {
                            const name = window.prompt("Nieuwe naam", n.name);
                            if (name && name !== n.name) act(() => api.change(n.id, { name }));
                          }}
                        >
                          Hernoemen
                        </button>
                      )}
                      {a.trash && (
                        <button type="button" onClick={() => act(() => api.trash(n.id))}>
                          Verwijderen
                        </button>
                      )}
                      <NodeActions
                        node={n}
                        folderId={current ?? null}
                        onChanged={() => latest.current.load()}
                        onError={setError}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {sharing && listing && (
        <ShareDialog node={listing.folder} onClose={() => setSharing(false)} onChanged={() => latest.current.load()} />
      )}

      {shown && (
        <PreviewPanel
          node={shown}
          preview={previews[shown.id]}
          onClose={() => setShown(null)}
          onDownload={() => download(shown).catch((e: Error) => setError(e.message))}
        />
      )}
    </main>
  );
}
