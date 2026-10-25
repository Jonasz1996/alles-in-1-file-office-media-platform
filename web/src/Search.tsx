// Zoeken (/zoeken): op naam, soort of tag, in de eigen bestanden en wat met je gedeeld is. De
// resultaten staan in dezelfde lijst als een map (FileList), met een kolom voor de map waar ze in
// staan; een bestand opent in de viewer, een map opent zelf. Acties (contextmenu, ⋯ en
// sneltoetsen) komen uit hetzelfde register als in Bestanden.

import { type FormEvent, type JSX, Suspense, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { api, type Category, type FileNode, type SearchHit, type SearchResult, searchParams, type Tag } from "./api";
import { actionShortcuts } from "./browser/actionKeys";
import { type ActionEnv, actionMenuItems, availability, type BrowserAction } from "./browser/actions";
import { clipboard } from "./browser/clipboard";
import { FileList } from "./browser/FileList";
import { canUndo, undoLast } from "./browser/undo";
import { useBrowserUi } from "./browser/useBrowserUi";
import { isGuest, useCurrentUser } from "./currentUser";
import { errorText, t } from "./i18n";
import { navigate, type RouteProps, useLocation } from "./router";
import {
  CATEGORIES,
  categoryLabel,
  folderHref,
  hasFilter,
  hitPath,
  MAX_LIMIT,
  parentHref,
  pathText,
  readSearch,
  searchHref,
} from "./searchQuery";
import { isShared } from "./sharing";
import { Button } from "./ui/Button";
import { useScope, useShortcuts } from "./ui/keys";
import { Menu } from "./ui/Menu";
import type { MenuItem } from "./ui/menu";
import { PageTitle } from "./ui/PageTitle";
import { clickSelect, keySelect, SelectionStore, useSelection } from "./ui/selection";
import { toast } from "./ui/toast";
import { useViewerParam, Viewer } from "./viewer";
import "./files.css";
import "./share.css";
import "./browser/browser.css";

// Het zoekveld boven in Bestanden staat in een eigen module (klein, in het chunk van Bestanden)
export { SEARCH_INPUT_ID, SearchBox } from "./browser/SearchBox";

export default function Search(_: RouteProps): JSX.Element {
  useScope("browser");
  const location = useLocation();
  // ?bestand= (de viewer) hoort niet bij de zoekopdracht
  const key = searchParams(readSearch(location.search));
  const query = useMemo(() => readSearch(`?${key}`), [key]);
  const user = useCurrentUser();
  const me = user?.id ?? null;
  const [text, setText] = useState(query.q ?? "");
  const [result, setResult] = useState<SearchResult | null>(null);
  const [tags, setTags] = useState<Tag[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selection] = useState(() => new SelectionStore());
  const sel = useSelection(selection);
  const [focusIndex, setFocusIndex] = useState(-1);
  const [menu, setMenu] = useState<{ items: readonly MenuItem[]; at: { x: number; y: number } | HTMLElement; restore: Element | null } | null>(null);
  const [viewing, setViewing] = useViewerParam();
  const clip = useSyncExternalStore(clipboard.subscribe, clipboard.getSnapshot, clipboard.getSnapshot);
  const searches = useRef(0);
  const listRef = useRef<HTMLDivElement>(null);

  const run = useCallback(() => {
    const seq = ++searches.current;
    if (!hasFilter(query)) {
      setResult(null);
      return;
    }
    api.search(query).then(
      (r) => {
        if (seq !== searches.current) return; // intussen een nieuwere zoekopdracht
        setResult(r);
        setError(null);
      },
      (e: unknown) => {
        if (seq === searches.current) setError(errorText(e));
      },
    );
  }, [query]);

  useEffect(() => {
    setText(query.q ?? "");
    selection.clear();
    setFocusIndex(-1);
    run();
  }, [run, query, selection]);

  useEffect(() => {
    api.tags().then(setTags, () => {});
  }, []);

  const hits: readonly SearchHit[] = result?.results ?? [];
  const nodes = useMemo(() => hits.map((h) => h.node), [hits]);
  const ids = useMemo(() => nodes.map((n) => n.id), [nodes]);
  const hitOf = useMemo(() => new Map(hits.map((h) => [h.node.id, h])), [hits]);
  const get = useCallback((i: number) => nodes[i], [nodes]);
  const selected = useMemo(() => nodes.filter((n) => sel.selected.has(n.id)), [nodes, sel.selected]);
  const focused = focusIndex >= 0 ? nodes[focusIndex] : undefined;
  const target = selected.length ? selected : focused ? [focused] : [];

  const env: ActionEnv = { folder: null, clip, me, guest: isGuest(user) };
  const { ui, dialogs } = useBrowserUi({
    folder: null,
    me,
    // Kopiëren of verplaatsen begint in de map van het eerste resultaat
    startId: null,
    openFile: (n) => setViewing(n.id),
    // Wat veranderde, kan nu wel of niet meer bij de zoekopdracht passen: opnieuw zoeken
    onUpsert: run,
    onRemove: run,
    onRefresh: run,
  });

  function runAction(action: BrowserAction, list: readonly FileNode[]): void {
    const a = availability(action, list, env);
    if (!a.visible) return;
    if (!a.enabled) {
      if (a.reason) toast(a.reason);
      return;
    }
    void action.run(list, { ...env, ui });
  }

  function openMenu(list: readonly FileNode[], at: { x: number; y: number } | HTMLElement): void {
    const items = actionMenuItems(list, env, (action) => runAction(action, list));
    if (items.length) setMenu({ items, at, restore: document.activeElement });
  }

  const inList = () => !!listRef.current?.contains(document.activeElement);
  useShortcuts(
    [
      ...actionShortcuts(target, env, runAction, inList),
      {
        combo: " ",
        label: t("browser.keys.toggle"),
        when: () => inList() && !!focused,
        run: () => focused && selection.toggle(focused.id),
      },
      {
        combo: "mod+a",
        label: t("browser.keys.selectAll"),
        when: () => ids.length > 0,
        run: () => selection.selectAll(ids),
      },
      {
        combo: "escape",
        label: t("browser.keys.clear"),
        when: () => sel.selected.size > 0,
        run: () => selection.clear(),
      },
      {
        combo: "mod+z",
        label: t("browser.undo"),
        when: canUndo,
        run: undoLast,
      },
      {
        combo: ["shift+f10", "contextmenu"],
        label: t("browser.keys.menu"),
        when: () => target.length > 0,
        run: () => openMenu(target, listRef.current?.querySelector<HTMLElement>(`[data-index="${focusIndex}"]`) ?? document.body),
      },
    ],
    { scope: "browser" },
  );

  function go(change: Partial<typeof query>) {
    // Een nieuwe zoekopdracht begint weer bij de eerste resultaten
    navigate(searchHref({ ...query, limit: undefined, ...change }));
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    go({ q: text });
  }

  const limit = query.limit ?? 50;
  const files = useMemo(() => nodes.filter((n) => n.kind === "file"), [nodes]);
  const viewIndex = viewing ? files.findIndex((n) => n.id === viewing) : -1;

  const pathOf = useCallback(
    (node: FileNode) => {
      const hit = hitOf.get(node.id);
      if (!hit) return null;
      const href = parentHref(hit);
      return (
        <a
          href={href}
          tabIndex={-1}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            navigate(href);
          }}
        >
          {pathText(hitPath(hit), isShared(node))}
        </a>
      );
    },
    [hitOf],
  );

  return (
    <main className="wide browser">
      <PageTitle>{t("browser.searchTitle")}</PageTitle>
      <form className="search-form" role="search" onSubmit={submit}>
        <input
          type="search"
          aria-label={t("browser.search.words")}
          placeholder={t("browser.search.wordsPlaceholder")}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <select
          aria-label={t("browser.search.type")}
          value={query.type ?? ""}
          onChange={(e) => go({ q: text, type: e.target.value as Category | "" })}
        >
          <option value="">{t("browser.search.allTypes")}</option>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {categoryLabel(c)}
            </option>
          ))}
        </select>
        <select
          aria-label={t("browser.search.tag")}
          value={query.tag ?? ""}
          onChange={(e) => go({ q: text, tag: e.target.value })}
        >
          <option value="">{t("browser.search.allTags")}</option>
          {tags.map((tag) => (
            <option key={tag.id} value={tag.id}>
              {t("browser.search.tagCount", { name: tag.name, n: tag.count })}
            </option>
          ))}
        </select>
        <Button type="submit" variant="primary">
          {t("browser.search.submit")}
        </Button>
      </form>

      {error && <p role="alert">{error}</p>}
      {!hasFilter(query) && <p className="muted">{t("browser.search.hint")}</p>}
      {result && hits.length === 0 && <p className="muted">{t("browser.search.none")}</p>}
      {hits.length > 0 && (
        <div ref={listRef} className="browser-items">
          <FileList
            count={nodes.length}
            get={get}
            version={hits.length}
            selected={sel.selected}
            focusIndex={focusIndex}
            ariaLabel={t("browser.results")}
            pathOf={pathOf}
            onSelect={(i, mods) => {
              setFocusIndex(i);
              selection.set(clickSelect(selection.getSnapshot(), ids, ids[i], mods));
            }}
            onToggle={(i) => {
              setFocusIndex(i);
              selection.toggle(ids[i]);
            }}
            onOpen={(i) => {
              const hit = hits[i];
              if (hit.node.kind === "file") setViewing(hit.node.id);
              else navigate(folderHref(hit));
            }}
            onMenu={(i, at) => {
              setFocusIndex(i);
              const node = nodes[i];
              if (!selection.has(node.id)) {
                selection.set(clickSelect(selection.getSnapshot(), ids, node.id, { shift: false, toggle: false }));
                openMenu([node], at);
              } else openMenu(selected, at);
            }}
            onMove={(i, mods) => {
              setFocusIndex(i);
              selection.set(keySelect(selection.getSnapshot(), ids, ids[i], mods));
            }}
            onFocusRow={setFocusIndex}
          />
        </div>
      )}
      {result?.more &&
        (limit < MAX_LIMIT ? (
          <p>
            <Button onClick={() => navigate(searchHref({ ...query, limit: MAX_LIMIT }))}>
              {t("browser.search.more")}
            </Button>
          </p>
        ) : (
          <p className="muted">{t("browser.search.tooMany")}</p>
        ))}

      {menu && (
        <Menu
          items={menu.items}
          anchor={menu.at}
          label={t("browser.selection.menu")}
          onClose={(reason) => {
            const restore = menu.restore;
            setMenu(null);
            if (reason !== "outside" && restore instanceof HTMLElement && restore.isConnected) restore.focus();
          }}
        />
      )}
      {dialogs}
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
