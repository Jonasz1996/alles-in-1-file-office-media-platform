import { type DragEvent, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, type FileNode, formatBytes, formatDate, type Listing } from "./api";
import { navigate } from "./router";
import { uploadFile } from "./upload";

interface UploadItem {
  key: string;
  file: File;
  parent: string;
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

  const controllers = useRef(new Map<string, AbortController>());

  async function runUpload(item: UploadItem) {
    const controller = new AbortController();
    controllers.current.set(item.key, controller);
    const update = (change: Partial<UploadItem>) =>
      setUploads((u) => u.map((x) => (x.key === item.key ? { ...x, ...change } : x)));
    update({ error: undefined });
    try {
      await uploadFile(api, item.parent, item.file, (progress) => update({ progress }), {
        signal: controller.signal,
      });
      setUploads((u) => u.filter((x) => x.key !== item.key));
    } catch (e) {
      if (e instanceof ApiError && e.code === "aborted") setUploads((u) => u.filter((x) => x.key !== item.key));
      else update({ error: (e as Error).message });
    } finally {
      controllers.current.delete(item.key);
    }
    await load();
  }

  async function uploadFiles(files: FileList | File[]) {
    if (!current) return;
    const items = Array.from(files).map((file) => ({
      key: `${file.name}-${crypto.randomUUID()}`,
      file,
      parent: current,
      progress: 0,
    }));
    setUploads((u) => [...u, ...items]);
    for (const item of items) await runUpload(item);
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
              {u.error ? (
                <>
                  <span role="alert">{u.error}</span>
                  <button type="button" onClick={() => runUpload(u)}>
                    Opnieuw proberen
                  </button>
                  <button type="button" onClick={() => setUploads((x) => x.filter((y) => y.key !== u.key))}>
                    Sluiten
                  </button>
                </>
              ) : (
                <>
                  <progress max={1} value={u.progress} />
                  <span className="muted">{Math.floor(u.progress * 100)} %</span>
                  {controllers.current.has(u.key) && (
                    <button type="button" onClick={() => controllers.current.get(u.key)?.abort()}>
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
