import { useCallback, useEffect, useRef, useState } from "react";
import { api, type FileNode, type FileVersion, formatBytes, formatDate } from "./api";
import { Dialog } from "./Dialog";

/** Eerdere inhoud van een bestand: downloaden, terugzetten, verwijderen, of een nieuwe
 * versie uploaden (dan blijft de huidige hier bewaard). */
export function Versions({ node, onClose, onChanged }: { node: FileNode; onClose: () => void; onChanged: () => void }) {
  const [current, setCurrent] = useState(node);
  const [items, setItems] = useState<FileVersion[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setItems(await api.versions(node.id));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [node.id]);

  useEffect(() => {
    load();
  }, [load]);

  async function act(fn: () => Promise<FileNode | void>) {
    setBusy(true);
    try {
      const changed = await fn();
      if (changed) setCurrent(changed);
      setError(null);
      await load();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function download(version: FileVersion) {
    try {
      const a = document.createElement("a");
      a.href = await api.versionDownloadUrl(node.id, version.id);
      a.download = current.name;
      a.click();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <Dialog title={`Versies van "${current.name}"`} onClose={onClose}>
      <p className="muted">
        Nu: {formatBytes(current.size)}, gewijzigd {formatDate(current.modified_at)}. De 20 meest recente eerdere
        versies blijven bewaard; ze tellen mee voor je quotum.
      </p>
      <div className="toolbar">
        <button type="button" disabled={busy || !node.parent_id} onClick={() => input.current?.click()}>
          Nieuwe versie uploaden
        </button>
        <input
          ref={input}
          type="file"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            if (file && node.parent_id) act(() => api.replaceContent(node.parent_id!, current.name, file));
          }}
        />
        {busy && <span className="muted">Bezig…</span>}
      </div>
      {error && <p role="alert">{error}</p>}
      {items?.length === 0 && (
        <p className="muted">Er zijn nog geen eerdere versies. Upload je een nieuwe versie, dan blijft de huidige hier bewaard.</p>
      )}
      {items && items.length > 0 && (
        <ul className="version-list" aria-label="Eerdere versies">
          {items.map((v) => (
            <li key={v.id}>
              <div>
                <strong>{formatDate(v.created_at)}</strong> · {formatBytes(v.size)}
                <div className="muted">
                  vervangen {formatDate(v.replaced_at)} door {v.replaced_by?.display_name ?? "onbekend"}
                </div>
              </div>
              <div className="version-actions">
                <button type="button" onClick={() => download(v)}>
                  Downloaden
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    if (window.confirm("Deze versie terugzetten? Wat er nu in staat, blijft bewaard als versie."))
                      act(() => api.restoreVersion(node.id, v.id));
                  }}
                >
                  Terugzetten
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    if (window.confirm("Deze versie definitief verwijderen?"))
                      act(() => api.deleteVersion(node.id, v.id));
                  }}
                >
                  Verwijderen
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <div className="dialog-actions">
        <button type="button" onClick={onClose}>
          Sluiten
        </button>
      </div>
    </Dialog>
  );
}
