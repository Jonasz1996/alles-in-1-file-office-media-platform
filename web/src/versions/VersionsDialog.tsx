// Eerdere inhoud van een bestand: bekijken (in de viewer), downloaden, terugzetten, verwijderen,
// of een nieuwe versie uploaden met voortgang en annuleren (dan blijft de huidige hier bewaard).
// Wie alleen mag kijken, kan alleen bekijken en downloaden; verwijderen kan alleen de eigenaar.
// U: een nieuwe versie uploaden.

import { type JSX, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { api, type FileNode, type FileVersion } from "../api";
import { eventIds, useReloadOn } from "../events";
import { errorText, fmt, t } from "../i18n";
import { nodeActions } from "../sharing";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { confirm } from "../ui/dialogs";
import { useScope, useShortcuts } from "../ui/keys";
import { toast } from "../ui/toast";
import type { VersionsProps } from "../Versions";
import { Viewer, type ViewerItem } from "../viewer";
import { percentOf, replaceWithProgress } from "./upload";

interface Upload {
  name: string;
  percent: number;
  abort: AbortController;
}

/** Een eerdere versie als item voor de viewer. */
export function versionItem(node: FileNode, v: FileVersion): ViewerItem {
  return {
    id: node.id,
    name: node.name,
    mime: node.mime,
    size: v.size,
    modified_at: v.created_at,
    version: { id: v.id, label: t("versions.versionLabel", { date: fmt.dateTime(v.created_at) }) },
  };
}

export default function VersionsDialog({ node, onClose, onChanged }: VersionsProps): JSX.Element {
  const [current, setCurrent] = useState(node);
  const can = nodeActions(node);
  const [items, setItems] = useState<FileVersion[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [upload, setUpload] = useState<Upload | null>(null);
  const [viewing, setViewing] = useState<number | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setItems(await api.versions(node.id));
    } catch (e) {
      setError(errorText(e));
    }
  }, [node.id]);

  useEffect(() => {
    void load();
  }, [load]);
  useReloadOn(
    (e) => e.type === "connected" || (e.type.startsWith("node.") && eventIds(e).includes(node.id)),
    () => void load(),
  );

  // Weg uit de dialoog = de upload stoppen
  const running = useRef<AbortController | null>(null);
  useEffect(() => () => running.current?.abort(), []);

  useScope("versions");
  useShortcuts(
    [
      {
        combo: "u",
        label: t("versions.keys.upload"),
        run: () => input.current?.click(),
        when: () => can.versionWrite && upload === null && !busy,
      },
    ],
    { scope: "versions" },
  );

  async function act(fn: () => Promise<FileNode | void>, done?: string): Promise<void> {
    setBusy(true);
    try {
      const changed = await fn();
      if (changed) setCurrent(changed);
      setError(null);
      if (done) toast(done, { kind: "ok" });
      await load();
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function send(file: File) {
    const abort = new AbortController();
    running.current = abort;
    setUpload({ name: file.name, percent: 0, abort });
    try {
      const changed = await replaceWithProgress(
        node.id,
        file,
        (loaded, total) => setUpload((u) => (u ? { ...u, percent: percentOf(loaded, total) } : u)),
        abort.signal,
      );
      setCurrent(changed);
      setError(null);
      toast(t("versions.uploaded"), { kind: "ok" });
      await load();
      onChanged();
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") toast(t("versions.cancelled"));
      else setError(errorText(e));
    } finally {
      running.current = null;
      setUpload(null);
    }
  }

  async function download(version: FileVersion) {
    try {
      const a = document.createElement("a");
      a.href = await api.versionDownloadUrl(node.id, version.id);
      a.download = current.name;
      a.click();
    } catch (e) {
      setError(errorText(e));
    }
  }

  async function restore(v: FileVersion) {
    const yes = await confirm({
      title: t("versions.restoreTitle"),
      message: t("versions.restoreText"),
      confirm: t("versions.restore"),
    });
    if (yes) await act(() => api.restoreVersion(node.id, v.id), t("versions.restored"));
  }

  async function remove(v: FileVersion) {
    const yes = await confirm({
      title: t("versions.removeTitle"),
      message: t("versions.removeText"),
      confirm: t("versions.remove"),
      danger: true,
    });
    if (yes) await act(() => api.deleteVersion(node.id, v.id), t("versions.removed"));
  }

  const viewItems = items ? items.map((v) => versionItem(current, v)) : [];

  return (
    <Dialog
      title={t("versions.title", { name: current.name })}
      size="l"
      onClose={onClose}
      actions={<Button onClick={onClose}>{t("common.close")}</Button>}
    >
      <p className="muted">
        {t("versions.now", { size: fmt.bytes(current.size), date: fmt.dateTime(current.modified_at) })}{" "}
        {can.versionDelete ? t("versions.keepOwn") : t("versions.keepOwner")}
      </p>
      {can.versionWrite && (
        <div className="toolbar">
          {upload === null ? (
            <Button
              icon="upload"
              data-autofocus=""
              disabled={busy}
              aria-keyshortcuts="u"
              onClick={() => input.current?.click()}
            >
              {t("versions.upload")}
            </Button>
          ) : (
            <div className="version-upload">
              <span>{upload.name}</span>
              <progress aria-label={t("versions.progress")} max={100} value={upload.percent} />
              <span className="muted" aria-live="polite">
                {t("versions.uploading", { percent: upload.percent })}
              </span>
              <Button onClick={() => upload.abort.abort()}>{t("versions.cancelUpload")}</Button>
            </div>
          )}
          <input
            ref={input}
            type="file"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) void send(file);
            }}
          />
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      {items?.length === 0 && (
        <p className="muted">
          {t("versions.none")}
          {can.versionWrite && <> {t("versions.noneHint")}</>}
        </p>
      )}
      {items && items.length > 0 && (
        <ul className="version-list" aria-label={t("versions.list")}>
          {items.map((v, i) => {
            const date = fmt.dateTime(v.created_at);
            return (
              <li key={v.id}>
                <div>
                  <strong>{date}</strong> · {fmt.bytes(v.size)}
                  <div className="muted">
                    {t("versions.replaced", {
                      date: fmt.dateTime(v.replaced_at),
                      name: v.replaced_by?.display_name ?? t("versions.unknown"),
                    })}
                  </div>
                </div>
                <div className="version-actions">
                  <Button aria-label={t("versions.viewLabel", { date })} onClick={() => setViewing(i)}>
                    {t("versions.view")}
                  </Button>
                  <Button onClick={() => void download(v)}>{t("versions.download")}</Button>
                  {can.versionWrite && (
                    <Button disabled={busy || upload !== null} onClick={() => void restore(v)}>
                      {t("versions.restore")}
                    </Button>
                  )}
                  {can.versionDelete && (
                    <Button disabled={busy} onClick={() => void remove(v)}>
                      {t("versions.remove")}
                    </Button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {viewing !== null && viewItems[viewing] && (
        <Suspense fallback={null}>
          <Viewer
            items={viewItems}
            index={viewing}
            onIndexChange={setViewing}
            onClose={() => setViewing(null)}
          />
        </Suspense>
      )}
    </Dialog>
  );
}
