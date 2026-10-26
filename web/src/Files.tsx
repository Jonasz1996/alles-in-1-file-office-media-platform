// Een map (/ of /map/:id): van jezelf, of een map die met je gedeeld is. Ook met 10.000 items
// soepel: de inhoud komt in pagina's (browser/folderStore.ts), de lijst en het raster tonen alleen
// wat in beeld is (browser/FileList.tsx, FileGrid.tsx), en live wijzigingen werken de lijst bij
// zonder alles opnieuw te laden. Selecteren met muis, aanraking en toetsenbord; acties uit één
// register (browser/actions.ts) in de selectiebalk, het contextmenu en als sneltoets; slepen
// naar een map in de lijst of in het pad; bestanden van buiten de browser loslaten = uploaden.

import {
  type DragEvent,
  type JSX,
  memo,
  Suspense,
  useCallback,
  useDeferredValue,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { api, type FileNode } from "./api";
import {
  type ActionEnv,
  actionLabel,
  actionMenuItems,
  actionsVersion,
  availability,
  type BrowserAction,
  findAction,
  subscribeActions,
} from "./browser/actions";
import { actionShortcuts } from "./browser/actionKeys";
import { clipboard } from "./browser/clipboard";
import { FileGrid } from "./browser/FileGrid";
import { FileList } from "./browser/FileList";
import { FILTER_INPUT_ID, FilterBox } from "./browser/FilterBox";
import { filterIds } from "./browser/filter";
import { focusRow, rowElement } from "./browser/rowEvents";
import { isServerSort, nextSort, SERVER_SORT, type Sort, type SortKey, sortIds } from "./browser/sort";
import { canUndo, undoLast } from "./browser/undo";
import { useBrowserUi } from "./browser/useBrowserUi";
import { useDrag } from "./browser/useDrag";
import { loadView, saveView, useFolderStore, type View } from "./browser/useFolder";
import { isGuest, useCurrentUser } from "./currentUser";
import { errorText, t } from "./i18n";
import { Link } from "./links";
import { markReady } from "./perf";
import { type RouteProps, useNavSection, useRestoreScroll } from "./router";
import { SEARCH_INPUT_ID, SearchBox } from "./browser/SearchBox";
import { ShareDialog } from "./ShareDialog";
import { canWrite, folderActions, isShared, permissionOf } from "./sharing";
import { Button, IconButton } from "./ui/Button";
import { prompt } from "./ui/dialogs";
import { useFocusBack } from "./ui/focusBack";
import { Icon } from "./ui/Icon";
import { useScope, useShortcuts, type Shortcut } from "./ui/keys";
import { Menu, MenuButton } from "./ui/Menu";
import type { MenuItem } from "./ui/menu";
import { PageTitle } from "./ui/PageTitle";
import { clickSelect, keySelect, SelectionStore, useSelection } from "./ui/selection";
import { announce, toast } from "./ui/toast";
import { hasFiles, UploadButtons, uploadDropped } from "./upload/index";
import { useViewerParam, Viewer } from "./viewer";
import "./files.css";
import "./share.css";
import "./browser/browser.css";

/** Acties die in de selectiebalk een eigen knop hebben (de rest staat in het menu ⋯). */
const BAR_ACTIONS = ["download", "share", "move", "copyTo", "trash"];
/** Niets geselecteerd: altijd dezelfde lege lijst (de selectiebalk volgt met useDeferredValue en
 * hoeft dan niet bij elke nieuwe pagina van de map opnieuw) */
const NO_NODES: FileNode[] = [];

export default function Files({ params }: RouteProps): JSX.Element {
  // De sneltoets-scope van de bestandsbrowser (P0): P3 (U, Shift+U) en P2 registreren hun
  // toetsen in "browser"; P2 houdt deze regel
  useScope("browser");
  const folderId = params.id ?? null;
  // Een andere map: alles opnieuw (selectie, filter, focus, store)
  return <FolderView key={folderId ?? ""} folderId={folderId} />;
}

interface OpenMenu {
  items: readonly MenuItem[];
  at: { x: number; y: number } | HTMLElement;
  label: string;
  restore: Element | null;
}

function useActionsVersion(): number {
  return useSyncExternalStore(subscribeActions, actionsVersion, actionsVersion);
}

function FolderView({ folderId }: { folderId: string | null }): JSX.Element {
  const user = useCurrentUser();
  const me = user?.id ?? null;
  const guest = isGuest(user);
  const { store, snap } = useFolderStore(folderId);
  const folder = snap.folder;
  const shared = isShared(folder);
  const can = folderActions(folder);
  // Een map van iemand anders hoort bij "Gedeeld met mij" (gasten hebben alleen dat)
  useNavSection(guest || shared ? "shared" : null);
  useActionsVersion();

  const [view, setViewState] = useState<View>(loadView);
  const [sort, setSort] = useState<Sort>(SERVER_SORT);
  const [filter, setFilter] = useState("");
  const [selection] = useState(() => new SelectionStore());
  const sel = useSelection(selection);
  const clip = useSyncExternalStore(clipboard.subscribe, clipboard.getSnapshot, clipboard.getSnapshot);
  const [viewing, setViewing] = useViewerParam();
  const [sharingFolder, setSharingFolder] = useState(false);
  const [dropId, setDropId] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [menu, setMenu] = useState<OpenMenu | null>(null);
  const [focus, setFocus] = useState<{ index: number; id: string | null }>({ index: -1, id: null });
  const listRef = useRef<HTMLDivElement>(null);
  const scrollTo = useRef<((i: number) => void) | null>(null);

  const setView = useCallback((v: View) => {
    setViewState(v);
    saveView(v);
  }, []);

  // --- wat er getoond wordt: de volgorde (server of eigen sortering) en het filter ---
  const words = filter.trim();
  const custom = !isServerSort(sort) && snap.complete;
  const reorder = custom || words !== "";
  const viewIds = useMemo(() => {
    let ids = snap.ids;
    if (custom) ids = sortIds(ids, snap.nodes, sort);
    if (words) ids = filterIds(ids, snap.nodes, words);
    return ids;
    // version: de nodes zelf veranderden (de Map wordt in place bijgewerkt)
  }, [snap.ids, reorder ? snap.version : 0, custom, sort, words]);
  // Zonder filter of eigen sortering staan wat nog niet geladen is er al (lege rijen)
  const count = reorder ? viewIds.length : Math.max(snap.total, viewIds.length);
  const indexOf = useMemo(() => {
    const m = new Map<string, number>();
    viewIds.forEach((id, i) => m.set(id, i));
    return m;
  }, [viewIds]);
  const get = useCallback(
    (i: number): FileNode | undefined => {
      const id = viewIds[i];
      return id === undefined ? undefined : snap.nodes.get(id);
    },
    [viewIds, snap.version],
  );

  const focusIndex =
    focus.id !== null && indexOf.has(focus.id)
      ? (indexOf.get(focus.id) as number)
      : Math.min(focus.index, count - 1);
  const focused = focusIndex >= 0 ? get(focusIndex) : undefined;

  // Wat er niet meer is (weggegooid, verplaatst), is ook niet meer geselecteerd
  useEffect(() => {
    const s = selection.getSnapshot();
    if (s.selected.size === 0 && s.anchor === null && s.focus === null) return;
    selection.retain(new Set(snap.ids));
  }, [snap.ids, selection]);

  const selected = useMemo(() => {
    const out: FileNode[] = [];
    for (const id of sel.selected) {
      const n = snap.nodes.get(id);
      if (n) out.push(n);
    }
    return out.length ? out : NO_NODES;
  }, [sel.selected, snap.version]);
  /** Waar een sneltoets op werkt: de selectie, of anders de rij met de focus. */
  const target = selected.length ? selected : focused ? [focused] : [];

  const env: ActionEnv = { folder, clip, me, guest };
  const { ui, dialogs, transfer } = useBrowserUi({
    folder,
    me,
    openFile: (n) => setViewing(n.id),
    onUpsert: (nodes) => store.upsert(nodes),
    onRemove: (ids) => store.remove(ids),
    onRefresh: (ids) => store.refresh(ids),
  });

  function runAction(action: BrowserAction, nodes: readonly FileNode[]): void {
    const a = availability(action, nodes, env);
    if (!a.visible) return;
    if (!a.enabled) {
      if (a.reason) toast(a.reason);
      return;
    }
    void action.run(nodes, { ...env, ui });
  }

  /** restore: waar de focus na Escape heen gaat (anders wat hem nu heeft). */
  function openMenu(nodes: readonly FileNode[], at: OpenMenu["at"], restore?: Element | null): void {
    const items = actionMenuItems(nodes, env, (action) => runAction(action, nodes));
    if (!items.length) return;
    const label =
      nodes.length === 1 ? t("browser.row.actions", { name: nodes[0].name }) : t("browser.selection.menu");
    setMenu({ items, at, label, restore: restore ?? document.activeElement });
  }

  // --- selecteren en de focus ---
  function selectOnly(node: FileNode): void {
    selection.set(clickSelect(selection.getSnapshot(), viewIds, node.id, { shift: false, toggle: false }));
  }

  const rowCallbacks = {
    count,
    focusIndex,
    onSelect(i: number, mods: { shift: boolean; toggle: boolean }) {
      const node = get(i);
      if (!node) return;
      setFocus({ index: i, id: node.id });
      selection.set(clickSelect(selection.getSnapshot(), viewIds, node.id, mods));
    },
    onToggle(i: number) {
      const node = get(i);
      if (!node) return;
      setFocus({ index: i, id: node.id });
      selection.toggle(node.id);
    },
    onOpen(i: number) {
      const node = get(i);
      if (node) ui.open(node);
    },
    onMenu(i: number, at: OpenMenu["at"]) {
      const node = get(i);
      if (!node) return;
      setFocus({ index: i, id: node.id });
      const s = selection.getSnapshot();
      if (!s.selected.has(node.id)) {
        selectOnly(node);
        openMenu([node], at);
      } else openMenu(selected.length ? selected : [node], at);
    },
    onMove(i: number, mods: { shift: boolean; toggle: boolean }) {
      const node = get(i);
      const from = get(focusIndex)?.id;
      setFocus({ index: i, id: node?.id ?? null });
      if (node) selection.set(keySelect(selection.getSnapshot(), viewIds, node.id, mods, from));
    },
    onFocusRow(i: number) {
      setFocus({ index: i, id: get(i)?.id ?? null });
    },
  };

  // --- slepen ---
  const drag = useDrag({
    get,
    pick: (i) => {
      const node = get(i);
      if (!node) return [];
      // Een rij buiten de selectie sleept alleen zichzelf, en de selectie blijft zoals hij is:
      // een selectiebalk die tijdens het slepen verschijnt, schuift de lijst onder de muis weg
      // (en tijdens dragstart laat Chromium de sleep dan zelfs meteen afbreken)
      if (!selection.has(node.id)) return [node];
      return selected;
    },
    folderId: folder?.id ?? folderId,
    me,
    setDropId,
    transfer: (nodes, to, mode) => void transfer(nodes, to, mode),
  });

  function onPageDragOver(e: DragEvent<HTMLElement>) {
    drag.edge(e);
    if (!hasFiles(e.dataTransfer)) return;
    e.preventDefault(); // anders opent de browser het bestand
    e.dataTransfer.dropEffect = can.upload ? "copy" : "none";
    setDragging(can.upload);
  }

  function onPageDrop(e: DragEvent<HTMLElement>) {
    setDragging(false);
    if (!hasFiles(e.dataTransfer)) return;
    e.preventDefault();
    // Synchroon, vóór elke await: daarna is dataTransfer leeg
    if (can.upload && folder) uploadDropped(e.dataTransfer, folder.id);
    else if (folder) toast(t("browser.readOnly"), { kind: "error" });
  }

  // --- nieuwe map ---
  async function newFolder(): Promise<void> {
    if (!folder || !can.mkdir) return;
    const name = (
      await prompt({
        title: t("browser.newFolder.title"),
        label: t("browser.newFolder.label"),
        confirm: t("browser.newFolder.confirm"),
        validate: (v) => (v.trim() ? null : t("common.dialog.required")),
      })
    )?.trim();
    if (!name) return;
    try {
      const made = await api.mkdir(folder.id, name);
      store.upsert([made]);
      announce(t("browser.newFolder.created", { name: made.name }));
      setFocus({ index: -1, id: made.id });
      selection.set(clickSelect(selection.getSnapshot(), [made.id], made.id, { shift: false, toggle: false }));
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    }
  }

  // De rij met de focus gaat weg (naar de prullenbak, verplaatst, ook door iemand anders): de
  // focus valt dan naar <body>, waar de pijltjes en sneltoetsen van de lijst niet meer werken.
  // Stond de focus het laatst in de lijst, dan gaat hij naar de rij die nu op die plek staat.
  const listHadFocus = useRef(false);
  useEffect(() => {
    const onFocusIn = (e: FocusEvent) => {
      listHadFocus.current = !!listRef.current?.contains(e.target as Node);
    };
    document.addEventListener("focusin", onFocusIn);
    return () => document.removeEventListener("focusin", onFocusIn);
  }, []);
  useLayoutEffect(() => {
    const active = document.activeElement;
    if (!listHadFocus.current || count === 0 || (active && active !== document.body)) return;
    focusRow(listRef, Math.max(0, Math.min(focusIndex, count - 1)));
  }, [indexOf]);

  // Een nieuwe map (of ander doel van de focus) in beeld en met de focus, zodra hij in de lijst staat
  const wantFocus = useRef<string | null>(null);
  useEffect(() => {
    if (focus.index === -1 && focus.id !== null) wantFocus.current = focus.id;
  }, [focus]);
  useEffect(() => {
    const id = wantFocus.current;
    if (id === null || !indexOf.has(id)) return;
    wantFocus.current = null;
    const i = indexOf.get(id) as number;
    setFocus({ index: i, id });
    scrollTo.current?.(i);
    focusRow(listRef, i);
  }, [indexOf]);

  // --- sorteren ---
  const sortBlocked = snap.complete ? null : t("browser.sort.waiting");
  function changeSort(key: SortKey) {
    if (sortBlocked) return;
    setSort((s) => nextSort(s, key));
  }

  // --- sneltoetsen (scope browser) ---
  const inList = () => !!listRef.current?.contains(document.activeElement);
  const shortcuts: Shortcut[] = actionShortcuts(target, env, runAction, inList);
  shortcuts.push(
    {
      combo: " ",
      label: t("browser.keys.toggle"),
      when: () => inList() && !!focused,
      run: () => focused && selection.toggle(focused.id),
    },
    {
      combo: "mod+a",
      label: t("browser.keys.selectAll"),
      when: () => viewIds.length > 0,
      run: () => {
        selection.selectAll(viewIds);
        announce(t("browser.selected", { n: viewIds.length }));
      },
    },
    {
      combo: "escape",
      label: t("browser.keys.clear"),
      when: () => sel.selected.size > 0,
      run: () => selection.clear(),
    },
    {
      combo: ["shift+f10", "contextmenu"],
      label: t("browser.keys.menu"),
      when: () => target.length > 0,
      run: () => {
        // De rij met de focus, ook als die er pas in het volgende frame echt op staat (End en
        // meteen Shift+F10): Escape zet hem daar terug
        const el = rowElement(listRef.current, focusIndex);
        openMenu(target, el ?? listRef.current ?? document.body, el);
      },
    },
    {
      combo: "mod+z",
      label: t("browser.undo"),
      when: canUndo,
      run: undoLast,
    },
    {
      combo: "g",
      label: t("browser.keys.view"),
      run: () => setView(view === "list" ? "grid" : "list"),
    },
    {
      combo: "n",
      label: t("browser.keys.newFolder"),
      when: () => can.mkdir,
      run: () => void newFolder(),
    },
    {
      combo: "mod+f",
      label: t("browser.keys.filter"),
      // Staat de focus al in het filter, dan is een tweede Ctrl/Cmd+F weer van de browser
      run: () => focusInput(FILTER_INPUT_ID),
    },
    {
      combo: "/",
      label: t("browser.keys.search"),
      run: () => focusInput(SEARCH_INPUT_ID),
    },
  );
  useShortcuts(shortcuts, { scope: "browser" });

  // --- klaar en scrollpositie ---
  const ready = snap.status === "ready";
  useEffect(() => {
    if (!ready) return;
    // Na de eerste rijen op het scherm (de meting van de eerste weergave)
    const frame = requestAnimationFrame(() => markReady("folder"));
    return () => cancelAnimationFrame(frame);
  }, [ready]);
  useRestoreScroll(ready);

  // --- de viewer: de bestanden in de volgorde van de lijst ---
  const open = viewing !== null;
  const viewerItems = useMemo(() => {
    if (!open) return [];
    const out: FileNode[] = [];
    for (const id of viewIds) {
      const n = snap.nodes.get(id);
      if (n && n.kind === "file") out.push(n);
    }
    return out;
  }, [open, viewIds, snap.version]);
  const viewIndex = viewing ? viewerItems.findIndex((n) => n.id === viewing) : -1;

  const cut = useMemo(
    () => new Set(clip?.mode === "cut" ? clip.nodes.map((n) => n.id) : []),
    [clip],
  );
  const isCut = useCallback((id: string) => cut.has(id), [cut]);

  // --- de kop: pad, titel en wat je hier mag ---
  const title = folder ? (folder.parent_id === null && !shared ? t("browser.title") : folder.name) : t("browser.title");
  const crumbs = useMemo(() => crumbList(snap.path), [snap.path]);

  const folderItems = (): MenuItem[] => {
    const items: MenuItem[] = [];
    if (can.mkdir)
      items.push({ id: "new-folder", label: t("browser.newFolder.button"), icon: "new-folder", shortcut: "n", onSelect: () => void newFolder() });
    const paste = findAction("paste");
    if (paste) {
      const a = availability(paste, [], env);
      if (a.visible)
        items.push({
          id: "paste",
          label: actionLabel(paste, []),
          shortcut: "mod+v",
          disabled: !a.enabled,
          reason: a.reason,
          onSelect: () => runAction(paste, []),
        });
    }
    items.push({
      id: "select-all",
      label: t("browser.selection.all"),
      shortcut: "mod+a",
      disabled: viewIds.length === 0,
      onSelect: () => selection.selectAll(viewIds),
    });
    if (folder && !guest)
      items.push({
        id: "download-folder",
        label: t("browser.folder.download"),
        icon: "download",
        separatorBefore: true,
        onSelect: () => ui.download([folder]),
      });
    if (can.share)
      items.push({ id: "share-folder", label: t("browser.folder.share"), icon: "share", onSelect: () => setSharingFolder(true) });
    return items;
  };

  const sortItems = (): MenuItem[] => {
    const keys: SortKey[] = ["name", "size", "modified"];
    const out: MenuItem[] = [];
    for (const key of keys) {
      for (const dir of ["asc", "desc"] as const) {
        out.push({
          id: `${key}-${dir}`,
          label: t(`browser.sort.${dir}`, { key: t(`browser.sort.${key}`) }),
          checked: sort.key === key && sort.dir === dir,
          radio: true,
          disabled: !!sortBlocked && !(key === "name" && dir === "asc"),
          reason: sortBlocked ?? undefined,
          onSelect: () => setSort({ key, dir }),
        });
      }
    }
    return out;
  };

  const folderMenu = (
    <MenuButton icon="more" label={t("browser.selection.folderMenu")} items={folderItems} />
  );

  // De selectiebalk met lage voorrang (useDeferredValue): een toets in de lijst (PageDown
  // verplaatst ook de selectie) zet eerst de focus en de rijen, de balk volgt een frame later.
  // De knoppen werken op wat nu geselecteerd is (latest), net als de sneltoetsen.
  const barNodes = useDeferredValue(selected);
  const barEnv = useMemo((): ActionEnv => ({ folder, clip, me, guest }), [folder, clip, me, guest]);
  const latest = useRef({ selected, runAction, openMenu, focusIndex: focus.index, count });
  latest.current = { selected, runAction, openMenu, focusIndex: focus.index, count };
  const barActions = useMemo(
    () => ({
      onRun: (action: BrowserAction) => latest.current.runAction(action, latest.current.selected),
      onMenu: (el: HTMLElement) => latest.current.openMenu(latest.current.selected, el),
      onClear: () => selection.clear(),
      onGone: () => {
        const { focusIndex: i, count: n } = latest.current;
        focusRow(listRef, Math.max(0, Math.min(i, n - 1)));
      },
    }),
    [selection],
  );

  const listProps = {
    ...rowCallbacks,
    get,
    version: snap.version,
    selected: sel.selected,
    isCut,
    dropId,
    ariaLabel: t("browser.list", { name: title }),
    scrollRef: scrollTo,
    drag: drag.rows,
  };

  return (
    <main
      className={`wide browser${dragging ? " dropping" : ""}`}
      onDragOver={onPageDragOver}
      onDragLeave={(e) => {
        if (!(e.relatedTarget instanceof Node) || !e.currentTarget.contains(e.relatedTarget)) setDragging(false);
      }}
      onDrop={onPageDrop}
    >
      <nav className="crumbs" aria-label={t("browser.path")}>
        {crumbs.map((c, i) => {
          const node = c.node;
          const targetProps = node && node.id !== folder?.id ? drag.target(node) : null;
          return (
            <span key={c.href}>
              {i > 0 && <span aria-hidden="true"> / </span>}
              <Link
                to={c.href}
                className={node && node.id === dropId ? "drop-target" : undefined}
                aria-current={i === crumbs.length - 1 ? "page" : undefined}
                {...targetProps}
              >
                {c.label}
              </Link>
            </span>
          );
        })}
      </nav>
      <PageTitle>{title}</PageTitle>
      {folder && shared && (
        <p className="muted folder-note">
          {folder.owner && <>{t("browser.sharedBy", { name: folder.owner.display_name })} · </>}
          {canWrite(permissionOf(folder)) ? t("browser.canEdit") : t("browser.readOnly")}
        </p>
      )}

      {snap.status === "gone" && (
        <div className="browser-gone">
          <p role="alert">{shared ? t("browser.goneShared") : t("browser.gone")}</p>
          <p>
            <Link to="/gedeeld">{t("browser.toShared")}</Link>
            {!guest && (
              <>
                {" · "}
                <Link to="/">{t("browser.toMine")}</Link>
              </>
            )}
          </p>
        </div>
      )}
      {snap.status === "error" && (
        <div className="browser-error">
          <p role="alert">{t("browser.loadError", { error: errorText(snap.error) })}</p>
          <Button onClick={() => store.start()}>{t("browser.retry")}</Button>
        </div>
      )}

      {snap.status !== "gone" && (
        <div className="browser-toolbar" role="toolbar" aria-label={t("browser.toolbar")}>
          {folder && <UploadButtons folderId={folder.id} canUpload={can.upload} />}
          {can.mkdir && (
            <Button icon="new-folder" onClick={() => void newFolder()}>
              {t("browser.newFolder.button")}
            </Button>
          )}
          <span className="view-switch" role="group" aria-label={t("browser.view.label")}>
            <IconButton icon="list" label={t("browser.view.list")} pressed={view === "list"} onClick={() => setView("list")} />
            <IconButton icon="grid" label={t("browser.view.grid")} pressed={view === "grid"} onClick={() => setView("grid")} />
          </span>
          <MenuButton icon="sort-asc" label={t("browser.sort.label")} items={sortItems} />
          {view === "grid" && folderMenu}
          <FilterBox
            value={filter}
            onChange={setFilter}
            shown={viewIds.length}
            total={snap.ids.length}
            partial={!snap.complete}
          />
          <SearchBox />
        </div>
      )}

      {barNodes.length > 0 && <SelectionBar nodes={barNodes} env={barEnv} {...barActions} />}

      {snap.status === "loading" && <p className="muted">{t("browser.loading")}</p>}
      {ready && snap.total === 0 && snap.ids.length === 0 && (
        <div className="browser-empty">
          <p className="muted">{t("browser.empty")}</p>
          {can.upload && <p className="muted">{t("browser.emptyHint")}</p>}
        </div>
      )}
      {ready && words !== "" && viewIds.length === 0 && snap.ids.length > 0 && (
        <p className="muted">{t("browser.filter.none")}</p>
      )}

      {ready && count > 0 && (
        <div ref={listRef} className="browser-items">
          {view === "list" ? (
            <FileList
              {...listProps}
              sort={custom ? sort : SERVER_SORT}
              sortBlocked={sortBlocked}
              onSort={changeSort}
              headerMenu={folderMenu}
            />
          ) : (
            <FileGrid {...listProps} />
          )}
        </div>
      )}
      {/* Onder de lijst (browser.css: onderaan in beeld zolang hij laadt), niet erboven: een regel
          die verdwijnt als alles er is, verschuift de rijen dan niet */}
      {ready && !snap.complete && (
        <p className="muted browser-progress" aria-live="polite">
          {t("browser.loadingMore", { loaded: snap.ids.length, total: snap.total })}
        </p>
      )}

      {menu && (
        <Menu
          items={menu.items}
          anchor={menu.at}
          label={menu.label}
          onClose={(reason) => {
            const restore = menu.restore;
            setMenu(null);
            if (reason !== "outside" && restore instanceof HTMLElement && restore.isConnected) restore.focus();
          }}
        />
      )}

      {dialogs}
      {sharingFolder && folder && (
        <ShareDialog node={folder} onClose={() => setSharingFolder(false)} onChanged={() => store.refresh([folder.id])} />
      )}

      {viewIndex >= 0 && (
        <Suspense fallback={null}>
          <Viewer
            items={viewerItems}
            index={viewIndex}
            onIndexChange={(i) => setViewing(viewerItems[i].id)}
            onClose={() => setViewing(null)}
          />
        </Suspense>
      )}
    </main>
  );
}

/** De balk met de acties voor wat geselecteerd is (een memo: alleen opnieuw als de selectie of de
 * omgeving verandert). */
const SelectionBar = memo(function SelectionBar({
  nodes,
  env,
  onRun,
  onMenu,
  onClear,
  onGone,
}: {
  nodes: readonly FileNode[];
  env: ActionEnv;
  onRun(action: BrowserAction): void;
  onMenu(el: HTMLElement): void;
  onClear(): void;
  /** De balk verdwijnt (niets meer geselecteerd) terwijl de focus erin stond */
  onGone(): void;
}): JSX.Element {
  const more = useRef<HTMLButtonElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  useFocusBack(bar, onGone);
  const buttons: JSX.Element[] = [];
  for (const id of BAR_ACTIONS) {
    const action = findAction(id);
    if (!action) continue;
    const a = availability(action, nodes, env);
    if (!a.visible) continue;
    buttons.push(
      <Button
        key={id}
        icon={action.icon}
        variant={action.danger ? "danger" : "default"}
        aria-disabled={a.enabled ? undefined : true}
        title={a.reason}
        onClick={() => onRun(action)}
      >
        {actionLabel(action, nodes)}
      </Button>,
    );
  }
  return (
    <div ref={bar} className="selection-bar" role="toolbar" aria-label={t("browser.selection.label")}>
      <span className="selection-count" aria-live="polite">
        {t("browser.selected", { n: nodes.length })}
      </span>
      {buttons}
      <button
        ref={more}
        type="button"
        className="icon-button"
        aria-haspopup="menu"
        aria-label={t("browser.selection.menu")}
        title={t("browser.selection.menu")}
        onClick={() => more.current && onMenu(more.current)}
      >
        <Icon name="more" size={20} />
      </button>
      <IconButton icon="close" label={t("browser.selection.clear")} onClick={onClear} />
    </div>
  );
});

function focusInput(id: string): void {
  const el = document.getElementById(id);
  if (el instanceof HTMLInputElement) {
    el.focus();
    el.select();
  }
}

/** Het pad boven de map, met vertaalde namen; node: de map zelf (een doel om op los te laten). */
function crumbList(path: readonly FileNode[]): { label: string; href: string; node: FileNode | null }[] {
  if (path.length === 0) return [];
  if (isShared(path[0])) {
    return [
      { label: t("browser.sharedWithMe"), href: "/gedeeld", node: null },
      ...path.map((p) => ({ label: p.name, href: `/map/${p.id}`, node: p })),
    ];
  }
  return path.map((p, i) =>
    i === 0 ? { label: t("browser.myFiles"), href: "/", node: p } : { label: p.name, href: `/map/${p.id}`, node: p },
  );
}
