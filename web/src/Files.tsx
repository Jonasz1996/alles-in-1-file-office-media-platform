import { type DragEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, type FileNode, formatBytes, formatDate, type Listing } from "./api";
import { navigate } from "./router";
import { uploadQueue, useUploads } from "./uploadQueue";

export function Files({ folderId }: { folderId: string | null }) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const uploads = useUploads();
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const loads = useRef(0);

  const load = useCallback(async () => {
    const seq = ++loads.current;
    try {
      const result = folderId ? await api.children(folderId) : await api.root();
      if (seq !== loads.current) return; // intussen een nieuwere lijst gevraagd (andere map)
      setListing(result);
      setError(null);
    } catch (e) {
      if (seq === loads.current) setError((e as Error).message);
    }
  }, [folderId]);

  useEffect(() => {
    load();
  }, [load]);

  const current = listing?.folder.id;

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

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn();
      await latest.current.load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function uploadFiles(files: FileList) {
    if (!current) return;
    uploadQueue.add(current, Array.from(files)).catch((e: Error) => setError(e.message));
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
  }

  async function open(node: FileNode) {
    if (node.kind === "dir") navigate(`/map/${node.id}`);
    else window.open(await api.downloadUrl(node.id, true), "_blank", "noopener");
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
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      <nav className="crumbs" aria-label="Pad">
        {listing?.path.map((p, i) => (
          <span key={p.id}>
            {i > 0 && " / "}
            <a
              href={i === 0 ? "/" : `/map/${p.id}`}
              onClick={(e) => {
                e.preventDefault();
                navigate(i === 0 ? "/" : `/map/${p.id}`);
              }}
            >
              {i === 0 ? "Mijn bestanden" : p.name}
            </a>
          </span>
        ))}
      </nav>

      <div className="toolbar">
        <button type="button" onClick={() => input.current?.click()}>
          Uploaden
        </button>
        <button
          type="button"
          onClick={() => {
            const name = window.prompt("Naam van de nieuwe map");
            if (name && current) act(() => api.mkdir(current, name));
          }}
        >
          Nieuwe map
        </button>
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
        <span className="muted">of sleep bestanden hierheen</span>
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
              {listing.children.map((n) => (
                <tr key={n.id}>
                  <td>
                    <button type="button" className="link" onClick={() => open(n)}>
                      {n.kind === "dir" ? "📁 " : "📄 "}
                      {n.name}
                    </button>
                  </td>
                  <td>{n.kind === "file" ? formatBytes(n.size) : ""}</td>
                  <td>{formatDate(n.modified_at)}</td>
                  <td className="actions">
                    {n.kind === "file" && (
                      <button type="button" onClick={() => download(n)}>
                        Download
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => {
                        const name = window.prompt("Nieuwe naam", n.name);
                        if (name && name !== n.name) act(() => api.change(n.id, { name }));
                      }}
                    >
                      Hernoemen
                    </button>
                    <button type="button" onClick={() => act(() => api.trash(n.id))}>
                      Verwijderen
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
