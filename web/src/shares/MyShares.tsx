// /gedeeld/door-mij: alles wat je zelf deelt, met personen, groepen en als link, met waar het
// staat, de rechten en de vervaldatum. Delen… opent de deeldialoog van dat item.

import { type JSX, useCallback, useEffect, useMemo, useState } from "react";
import { api, type FileNode, type Share } from "../api";
import { isShareEvent, useReloadOn } from "../events";
import { errorText, t } from "../i18n";
import { Link, nodeHref } from "../links";
import type { RouteProps } from "../router";
import { ShareDialog } from "../ShareDialog";
import { expiryLabel, linkSummary, PERMISSION_LABELS } from "../sharing";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { Icon } from "../ui/Icon";
import { PageTitle } from "../ui/PageTitle";
import { SharedTabs } from "./SharedTabs";
import "../share.css";

export interface MyShare {
  share: Share;
  node: FileNode;
  /** De mappen boven het item, zonder de hoofdmap; [] = in de hoofdmap */
  path: { id: string; name: string }[];
}

/** Met wie: de naam, "Groep …" of de link (met zijn naam). */
export function shareWith(s: Share): string {
  if (s.type === "link") return s.link?.label ? t("sharing.mine.linkNamed", { label: s.link.label }) : t("sharing.mine.link");
  if (s.type === "group") return t("sharing.mine.group", { name: s.group?.name ?? "" });
  return s.user?.display_name ?? t("sharing.people.unknown");
}

/** Waar het staat: "Mijn bestanden / Foto's". */
export function sharePlace(path: MyShare["path"]): string {
  return [t("sharing.crumbs.mine"), ...path.map((p) => p.name)].join(" / ");
}

export default function MyShares(_: RouteProps): JSX.Element {
  const [rows, setRows] = useState<MyShare[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [managing, setManaging] = useState<FileNode | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await api.request<MyShare[]>("GET", "/api/v1/shares"));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const ids = useMemo(() => new Set(rows?.map((r) => r.node.id)), [rows]);
  useReloadOn(
    (e) =>
      e.type === "connected" ||
      isShareEvent(e) ||
      (e.type.startsWith("node.") && (ids.has(String(e.data.id ?? "")) || Array.isArray(e.data.ids))),
    () => void load(),
  );

  return (
    <main className="wide">
      <PageTitle>{t("sharing.myShares")}</PageTitle>
      <SharedTabs current="byMe" />
      {error && <p role="alert">{error}</p>}
      {rows?.length === 0 && (
        <EmptyState icon="share" title={t("sharing.mine.none")}>
          {t("sharing.mine.noneText")}
        </EmptyState>
      )}
      {rows && rows.length > 0 && (
        <>
          <p className="muted">{t("sharing.mine.count", { n: rows.length })}</p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("sharing.mine.name")}</th>
                  <th>{t("sharing.mine.with")}</th>
                  <th>{t("sharing.mine.rights")}</th>
                  <th className="wide-only">{t("sharing.mine.expires")}</th>
                  <th className="wide-only">{t("sharing.mine.where")}</th>
                  <th>
                    <span className="sr-only">{t("sharing.mine.manage")}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ share, node, path }) => (
                  <tr key={share.id}>
                    <td className="wrap">
                      <Link to={nodeHref(node)} className="link">
                        <Icon name={node.kind === "dir" ? "folder" : node.kind === "album" ? "album" : "file"} size={16} />{" "}
                        {node.name}
                      </Link>
                    </td>
                    <td>
                      {share.type === "link" && <Icon name="link" size={16} />} {shareWith(share)}
                    </td>
                    <td>
                      {share.type === "link"
                        ? linkSummary({ ...share, expires_at: null })
                        : PERMISSION_LABELS[share.permission === "write" ? "write" : "read"]}
                    </td>
                    <td className="wide-only">{expiryLabel(share.expires_at) ?? t("sharing.mine.never")}</td>
                    <td className="wide-only wrap">{sharePlace(path)}</td>
                    <td className="actions">
                      <Button
                        aria-label={t("sharing.mine.manageLabel", { name: node.name })}
                        onClick={() => setManaging(node)}
                      >
                        {t("sharing.mine.manage")}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {managing && (
        <ShareDialog node={managing} onClose={() => setManaging(null)} onChanged={() => void load()} />
      )}
    </main>
  );
}
