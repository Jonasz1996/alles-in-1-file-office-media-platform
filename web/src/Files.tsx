import { type DragEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, type FileNode, formatBytes, formatDate, type Listing } from "./api";
import { navigate } from "./router";

interface UploadItem {
  key: string;
  name: string;
  progress: number;
  error?: string;
}

export function Files({ folderId }: { folderId: string | null }) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setListing(folderId ? await api.children(folderId) : await api.root());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [folderId]);

  useEffect(() => {
    load();
  }, [load]);

  const current = listing?.folder.id;

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn();
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function uploadFiles(files: FileList | File[]) {
    if (!current) return;
    for (const file of Array.from(files)) {
      const key = `${file.name}-${crypto.randomUUID()}`;
      setUploads((u) => [...u, { key, name: file.name, progress: 0 }]);
      try {
        await api.upload(current, file, (p) =>
          setUploads((u) => u.map((x) => (x.key === key ? { ...x, progress: p } : x))),
        );
        setUploads((u) => u.filter((x) => x.key !== key));
      } catch (e) {
        setUploads((u) => u.map((x) => (x.key === key ? { ...x, error: (e as Error).message } : x)));
      }
      await load();
    }
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
              {u.name}{" "}
              {u.error ? (
                <span role="alert">{u.error}</span>
              ) : (
                <progress max={1} value={u.progress} />
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
