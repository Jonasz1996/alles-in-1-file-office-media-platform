// /gedeeld: wat anderen met je gedeeld hebben, rechtstreeks of via een groep. Een map of album
// opent op zijn eigen adres (nodeHref), een bestand in de viewer. Live: een nieuwe of
// ingetrokken deling, of de eigenaar hernoemt of verwijdert iets.

import { type JSX, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type FileNode, type SharedWithMe as Row } from "./api";
import { isGuest, useCurrentUser } from "./currentUser";
import { affectsSharedWithMe, useReloadOn } from "./events";
import { errorText, fmt, t } from "./i18n";
import { Link, nodeHref } from "./links";
import { NodeActions } from "./NodeActions";
import type { RouteProps } from "./router";
import { SearchBox } from "./Search";
import { SharedTabs } from "./shares/SharedTabs";
import { PERMISSION_LABELS } from "./sharing";
import { TagChips } from "./Tags";
import { Thumb, usePreviews } from "./Thumb";
import { Button } from "./ui/Button";
import { EmptyState } from "./ui/EmptyState";
import { PageTitle } from "./ui/PageTitle";
import { useViewerParam, Viewer } from "./viewer";
import "./share.css";

export default function SharedWithMe(_: RouteProps): JSX.Element {
  const guest = isGuest(useCurrentUser());
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewing, setViewing] = useViewerParam();
  const loads = useRef(0);
  const nodes = useMemo(() => rows?.map((r) => r.node), [rows]);
  const files = useMemo(() => nodes?.filter((n) => n.kind === "file") ?? [], [nodes]);
  const viewIndex = viewing ? files.findIndex((n) => n.id === viewing) : -1;
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
      if (seq === loads.current) setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useReloadOn((event) => affectsSharedWithMe(event, ids), () => void load());

  async function download(node: FileNode) {
    try {
      const a = document.createElement("a");
      a.href = await api.downloadUrl(node.id);
      a.download = node.name;
      a.click();
    } catch (e) {
      setError(errorText(e));
    }
  }

  return (
    <main className="wide">
      <PageTitle>{t("sharing.sharedWithMe")}</PageTitle>
      {!guest && <SharedTabs current="withMe" />}
      <div className="toolbar">
        <SearchBox />
      </div>
      {guest && <p className="muted">{t("sharing.withMe.guestNote")}</p>}
      {error && <p role="alert">{error}</p>}
      {rows?.length === 0 && <EmptyState icon="share" title={t("sharing.withMe.none")} />}
      {rows && rows.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("sharing.withMe.name")}</th>
                <th>{t("sharing.withMe.from")}</th>
                <th>{t("sharing.withMe.rights")}</th>
                <th className="wide-only">{t("sharing.withMe.via")}</th>
                <th className="wide-only">{t("sharing.withMe.sharedOn")}</th>
                <th>
                  <span className="sr-only">{t("sharing.withMe.download")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const n = r.node;
                return (
                  <tr key={r.share_id}>
                    <td>
                      {n.kind === "file" ? (
                        <button type="button" className="link" onClick={() => setViewing(n.id)}>
                          <Thumb node={n} preview={previews[n.id]} />
                          {n.name}
                        </button>
                      ) : (
                        <Link to={nodeHref(n)} className="link">
                          <Thumb node={n} preview={previews[n.id]} />
                          {n.name}
                        </Link>
                      )}
                      <TagChips tags={n.tags} />
                    </td>
                    <td>{r.owner.display_name}</td>
                    <td>{PERMISSION_LABELS[r.permission]}</td>
                    <td className="wide-only">
                      {r.via === "group" && r.group
                        ? t("sharing.withMe.group", { name: r.group.name })
                        : t("sharing.withMe.personal")}
                    </td>
                    <td className="wide-only">{fmt.dateTime(r.created_at)}</td>
                    <td className="actions">
                      {n.kind === "file" && (
                        <Button
                          aria-label={t("sharing.withMe.downloadLabel", { name: n.name })}
                          onClick={() => void download(n)}
                        >
                          {t("sharing.withMe.download")}
                        </Button>
                      )}
                      {n.kind !== "album" && (
                        <NodeActions node={n} folderId={null} onChanged={load} onError={setError} />
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {viewIndex >= 0 && (
        <Suspense fallback={null}>
          <Viewer
            items={files}
            index={viewIndex}
            onIndexChange={(i) => setViewing(files[i].id)}
            onClose={() => setViewing(null)}
          />
        </Suspense>
      )}
    </main>
  );
}
