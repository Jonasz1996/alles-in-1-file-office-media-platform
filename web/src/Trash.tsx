import { useCallback, useEffect, useState } from "react";
import { api, type FileNode, formatBytes, formatDate } from "./api";
import type { RouteProps } from "./router";

export default function Trash(_: RouteProps) {
  const [items, setItems] = useState<FileNode[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(await api.listTrash());
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn();
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <main className="wide">
      <h1>Prullenbak</h1>
      {error && <p role="alert">{error}</p>}
      {items.length === 0 && <p className="muted">De prullenbak is leeg.</p>}
      {items.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Naam</th>
                <th>Grootte</th>
                <th>Verwijderd</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((n) => (
                <tr key={n.id}>
                  <td>
                    {n.kind === "dir" ? "📁 " : "📄 "}
                    {n.name}
                  </td>
                  <td>{n.kind === "file" ? formatBytes(n.size) : ""}</td>
                  <td>{n.deleted_at && formatDate(n.deleted_at)}</td>
                  <td className="actions">
                    <button type="button" onClick={() => act(() => api.restore(n.id))}>
                      Terugzetten
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        if (window.confirm(`"${n.name}" definitief verwijderen?`)) act(() => api.purge(n.id));
                      }}
                    >
                      Definitief verwijderen
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
