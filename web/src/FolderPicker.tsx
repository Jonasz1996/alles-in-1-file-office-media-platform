import { type KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, type FileNode } from "./api";
import { browserApi, type Page } from "./browser/api";
import { isGuest, useCurrentUser } from "./currentUser";
import { errorText, t } from "./i18n";
import { canWrite, isShared, permissionOf } from "./sharing";
import { Button } from "./ui/Button";
import { Dialog } from "./ui/Dialog";
import { prompt } from "./ui/dialogs";
import { Icon } from "./ui/Icon";
import { typeahead } from "./ui/menu";
import "./files.css";

/** Zoveel mappen per keer in de kiezer. */
const PAGE = 500;
/** Zo lang (ms) telt getypte tekst als één zoekterm. */
const TYPEAHEAD_MS = 700;

/**
 * Kies een map om iets naartoe te kopiëren of te verplaatsen. Begint in startId (de map die
 * open staat). exclude: de mappen die je verplaatst of kopieert; daar kun je niet in, want iets
 * kan niet in zichzelf. check: waarom een map niet als doel kan (bijv. alleen kijken, of een
 * andere eigenaar bij verplaatsen), of null als het mag; over de hele selectie. Behalve je
 * eigen bestanden staan ook de mappen die met je gedeeld zijn erbij ("Gedeeld met mij"); gasten
 * hebben alleen die. Alleen mappen (?kind=dir); "Nieuwe map" maakt er een in de map die open
 * staat; typen springt naar een map met die naam.
 */
export function FolderPicker({
  title,
  action,
  startId,
  exclude,
  check,
  onPick,
  onClose,
}: {
  title: string;
  action: string;
  startId: string | null;
  exclude?: string | ReadonlySet<string>;
  check?: (folder: FileNode) => string | null;
  onPick: (folderId: string, folder: FileNode) => void;
  onClose: () => void;
}) {
  const guest = isGuest(useCurrentUser());
  const [page, setPage] = useState<Page | null>(null);
  const [folders, setFolders] = useState<FileNode[]>([]);
  // De lijst met wat met je gedeeld is, in plaats van een map
  const [shared, setShared] = useState<FileNode[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loads = useRef(0);
  const list = useRef<HTMLUListElement>(null);
  const confirm = useRef<HTMLButtonElement>(null);
  // Een map (of Gedeeld met mij) is net geladen: zie het effect hieronder
  const landed = useRef(false);
  const first = useRef(true);
  const typed = useRef({ text: "", at: 0 });
  const excluded = typeof exclude === "string" ? new Set([exclude]) : (exclude ?? new Set<string>());

  const open = useCallback(async (id: string | null) => {
    const seq = ++loads.current;
    try {
      const result = await browserApi.page(id, { kind: "dir", limit: PAGE });
      if (seq !== loads.current) return; // intussen een andere map gekozen
      landed.current = true;
      setPage(result);
      setFolders(result.children);
      setShared(null);
      setError(null);
    } catch (e) {
      if (seq === loads.current) setError(errorText(e));
    }
  }, []);

  const more = useCallback(async () => {
    if (!page?.next) return;
    const seq = loads.current;
    try {
      const result = await browserApi.page(page.folder.id, { kind: "dir", limit: PAGE, after: page.next });
      if (seq !== loads.current) return;
      setPage(result);
      setFolders((old) => [...old, ...result.children]);
    } catch (e) {
      setError(errorText(e));
    }
  }, [page]);

  const openShared = useCallback(async () => {
    const seq = ++loads.current;
    try {
      const rows = await api.sharedWithMe();
      if (seq !== loads.current) return;
      landed.current = true;
      setShared(rows.map((r) => r.node).filter((n) => n.kind === "dir"));
      setPage(null);
      setFolders([]);
      setError(null);
    } catch (e) {
      if (seq === loads.current) setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    // Een gast heeft geen eigen hoofdmap
    if (!startId && guest) openShared();
    else open(startId);
  }, [open, openShared, startId, guest]);

  // Na het laden de focus op de eerste map: de eerste keer (data-autofocus van Dialog komt te
  // vroeg, de mappen zijn er dan nog niet), en als de focus weg is omdat de knop van de map die
  // je opende verdween (anders valt hij naar <body> en werken de pijltjes niet meer). Zonder
  // mappen dan de knop om hier te kiezen, of anders Mijn bestanden / Gedeeld met mij.
  const sources = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!landed.current) return;
    landed.current = false;
    const initial = first.current;
    first.current = false;
    const folder = list.current?.querySelector<HTMLButtonElement>("button");
    const active = document.activeElement;
    const lost = !active || active === document.body || !active.isConnected;
    if (initial) {
      folder?.focus();
      return;
    }
    if (!lost) return;
    const target = folder ?? (confirm.current?.disabled ? null : confirm.current) ?? sources.current?.querySelector("button");
    target?.focus();
  });

  const visible = (shared ?? folders).filter((f) => !excluded.has(f.id));
  const inShared = page ? isShared(page.path[0]) : shared !== null;
  const reason = page ? (check?.(page.folder) ?? null) : null;
  const canMkdir = page !== null && canWrite(permissionOf(page.folder));

  async function newFolder() {
    if (!page) return;
    const name = await prompt({
      title: t("browser.newFolder.title"),
      label: t("browser.newFolder.label"),
      confirm: t("browser.newFolder.confirm"),
      validate: (v) => (v.trim() ? null : t("common.dialog.required")),
    });
    if (!name?.trim()) return;
    try {
      const made = await api.mkdir(page.folder.id, name.trim());
      await open(made.id);
    } catch (e) {
      setError(errorText(e));
    }
  }

  /** Pijltjes tussen de mappen, en typen springt naar een map met die naam. */
  function onKeyDown(e: KeyboardEvent<HTMLUListElement>) {
    const buttons = [...(list.current?.querySelectorAll<HTMLButtonElement>("button") ?? [])];
    const cur = buttons.indexOf(document.activeElement as HTMLButtonElement);
    let next = -1;
    if (e.key === "ArrowDown") next = Math.min(buttons.length - 1, cur + 1);
    else if (e.key === "ArrowUp") next = Math.max(0, cur - 1);
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = buttons.length - 1;
    else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && e.key !== " ") {
      const now = Date.now();
      typed.current = { text: now - typed.current.at > TYPEAHEAD_MS ? e.key : typed.current.text + e.key, at: now };
      next = typeahead(
        visible.map((f) => ({ label: f.name })),
        cur,
        typed.current.text,
      );
    } else return;
    if (next < 0) return;
    e.preventDefault();
    buttons[next]?.focus();
  }

  return (
    <Dialog
      title={title}
      onClose={onClose}
      actions={
        <>
          {canMkdir && (
            <Button icon="new-folder" onClick={newFolder}>
              {t("browser.picker.newFolder")}
            </Button>
          )}
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button
            ref={confirm}
            variant="primary"
            disabled={!page || excluded.has(page.folder.id) || reason !== null}
            title={reason ?? undefined}
            onClick={() => page && onPick(page.folder.id, page.folder)}
          >
            {action}
          </Button>
        </>
      }
    >
      <div ref={sources} className="picker-sources">
        {!guest && (
          <Button aria-pressed={!inShared} onClick={() => open(null)}>
            {t("browser.picker.mine")}
          </Button>
        )}
        <Button aria-pressed={inShared} onClick={openShared}>
          {t("browser.picker.shared")}
        </Button>
      </div>
      {page && (
        <nav className="crumbs picker-crumbs" aria-label={t("browser.path")}>
          {inShared && (
            <span>
              <Button variant="link" onClick={openShared}>
                {t("browser.picker.shared")}
              </Button>
            </span>
          )}
          {page.path.map((p, i) => (
            <span key={p.id}>
              {(i > 0 || inShared) && " / "}
              <Button variant="link" onClick={() => open(p.id)}>
                {i === 0 && !inShared ? t("browser.picker.mine") : p.name}
              </Button>
            </span>
          ))}
        </nav>
      )}
      {error && <p role="alert">{error}</p>}
      {shared && visible.length === 0 && <p className="muted">{t("browser.picker.noShared")}</p>}
      {page && visible.length === 0 && <p className="muted">{t("browser.picker.noFolders")}</p>}
      {visible.length > 0 && (
        <ul ref={list} className="picker-list" aria-label={t("browser.picker.folders")} onKeyDown={onKeyDown}>
          {visible.map((f) => (
            <li key={f.id}>
              <button type="button" onClick={() => open(f.id)}>
                <Icon name="folder" size={20} />
                <span className="picker-name">{f.name}</span>
                {shared && f.owner && (
                  <span className="muted"> · {t("browser.picker.owner", { name: f.owner.display_name })}</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
      {page?.next && (
        <Button variant="link" onClick={more}>
          {t("browser.picker.more")}
        </Button>
      )}
      {reason && <p className="muted">{reason}</p>}
    </Dialog>
  );
}
