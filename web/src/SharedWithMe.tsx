import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type FileNode, formatDate, type SharedWithMe as Row } from "./api";
import { isGuest, useCurrentUser } from "./currentUser";
import { affectsSharedWithMe, useReloadOn } from "./events";
import { NodeActions } from "./NodeActions";
import { PreviewPanel } from "./PreviewPanel";
import { navigate } from "./router";
import { SearchBox } from "./Search";
import { PERMISSION_LABELS } from "./sharing";
import { TagChips } from "./Tags";
import { Thumb, usePreviews } from "./Thumb";
import "./share.css";

/** Wat anderen met je gedeeld hebben, rechtstreeks of via een groep (/gedeeld). */
export function SharedWithMe() {
  const guest = isGuest(useCurrentUser());
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState<FileNode | null>(null);
  const loads = useRef(0);
  const nodes = useMemo(() => rows?.map((r) => r.node), [rows]);
  const previews = usePreviews(nodes);
  const ids = useMemo(() => new Set(nodes?.map((n) => n.id)), [nodes]);

  const load = useCallback(async () => {
    const seq = ++loads.current;
    try {
      const result = await api.sharedWithMe();
      if (seq !== loads.current) return;
      setRows(result);
      setError(null);
    } catch (e) {
      if (seq === loads.current) setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Live: een nieuwe of ingetrokken deling, of de eigenaar hernoemt of verwijdert iets
  useReloadOn((event) => affectsSharedWithMe(event, ids), load);

  async function download(node: FileNode) {
    const a = document.createElement("a");
    a.href = await api.downloadUrl(node.id);
    a.download = node.name;
    a.click();
  }

  function open(node: FileNode) {
    if (node.kind === "dir") navigate(`/map/${node.id}`);
    else setShown(node);
  }

  return (
    <main className="wide">
      <h1>Gedeeld met mij</h1>
      <div className="toolbar">
        <SearchBox />
      </div>
      {guest && <p className="muted">Als gast heb je geen eigen bestanden; hier staat wat anderen met je delen.</p>}
      {error && <p role="alert">{error}</p>}
      {rows?.length === 0 && <p className="muted">Er is nog niets met je gedeeld.</p>}
      {rows && rows.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Naam</th>
                <th>Van</th>
                <th>Rechten</th>
                <th className="wide-only">Via</th>
                <th className="wide-only">Gedeeld op</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const n = r.node;
                return (
                  <tr key={r.share_id}>
                    <td>
                      <button type="button" className="link" onClick={() => open(n)}>
                        <Thumb node={n} preview={previews[n.id]} />
                        {n.name}
                      </button>
                      <TagChips tags={n.tags} />
                    </td>
                    <td>{r.owner.display_name}</td>
                    <td>{PERMISSION_LABELS[r.permission]}</td>
                    <td className="wide-only">
                      {r.via === "group" && r.group ? `Groep ${r.group.name}` : "Persoonlijk"}
                    </td>
                    <td className="wide-only">{formatDate(r.created_at)}</td>
                    <td className="actions">
                      {n.kind === "file" && (
                        <button type="button" onClick={() => download(n).catch((e: Error) => setError(e.message))}>
                          Download
                        </button>
                      )}
                      <NodeActions node={n} folderId={null} shareRoot onChanged={load} onError={setError} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
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
