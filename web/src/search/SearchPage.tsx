// De pagina /zoeken (fase 6, Z4; Keuze 9, 43, 47, 78 en 81), in een eigen lazy chunk. Vervangt
// Search.tsx van 0.7.0. De vraag staat in de URL (query.ts): q, de filters als chips, en de stand
// "Namen en tekst" of "Wat er op de foto staat" (mode=image).
//
// Stand tekst: de treffers staan in dezelfde lijst als een map (FileList), met icoon, naam en een
// kolom met het pad en de snippet (gemarkeerde woorden als <mark>, via tekst). Pijlen en Enter
// zoals in Bestanden; Enter op een bestand opent de viewer met ?bestand= naast ?q= (de markering
// in de tekstlaag is van de viewer). "Meer laden" haalt de volgende pagina (offset, of after na
// ronde B van Keuze 9). De meldingen staan in één role="status": de index is nog bezig, de twee
// teksten van Keuze 9, alleen op naam, niets gevonden. Bovenaan eigen personen en plaatsen die zo
// heten (groups, Keuze 38): een keuze zet het filter person of place. Zonder foto's in de
// treffers: "Geen foto met deze naam?" (Keuze 81).
//
// Stand beeld: het veld met zijn eigen plaatshouder en uitleg (ImageHint), en het raster van X3
// (lazy, met het gebied beeld; tot X3 er is, één zin dat zoeken op beeld later komt, die ImageHint
// ook in de beschrijving van het veld zet). De pagina vraagt dan zelf niets aan /search:
// het raster leest q en like uit de URL. Kom je er zonder vraag (de knop "Zoeken op beeld" op
// /fotos), dan staat de focus in het veld (Keuze 81).

import {
  type FormEvent,
  type JSX,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { api, type FileNode, type Tag } from "../api";
import { actionShortcuts } from "../browser/actionKeys";
import { type ActionEnv, actionMenuItems, availability, type BrowserAction } from "../browser/actions";
import { clipboard } from "../browser/clipboard";
import { FileList } from "../browser/FileList";
import { canUndo, undoLast } from "../browser/undo";
import { useBrowserUi } from "../browser/useBrowserUi";
import { isGuest, useCurrentUser } from "../currentUser";
import { errorText, lazyWithAreas, t } from "../i18n";
import { Link } from "../links";
import { navigate, type RouteProps, useLocation } from "../router";
import { isShared } from "../sharing";
import { Button } from "../ui/Button";
import { useScope, useShortcuts } from "../ui/keys";
import { LazySlot } from "../ui/LazySlot";
import { Menu } from "../ui/Menu";
import type { MenuItem } from "../ui/menu";
import { PageTitle } from "../ui/PageTitle";
import { clickSelect, keySelect, SelectionStore, useSelection } from "../ui/selection";
import { toast } from "../ui/toast";
import { useViewerParam, Viewer } from "../viewer";
import { places as listPlaces } from "../photos/map/api";
import { people as listPeople } from "../photos/people/api";
import { search } from "./api";
import { FilterBar, type PersonName } from "./FilterBar";
import { ImageHint, imageDescribedBy } from "./ImageHint";
import { appendHits, folderHref, hitPath, isPhoto, parentHref, pathText } from "./hits";
import { Notices, notices, Snippet } from "./Notices";
import { canSearch, firstParams, nextParams, queryHref, readQuery, withMode, withPerson } from "./query";
import { type Groups, type Hit, MAX_QUERY, type PlaceRef, type SearchPage as Page } from "./types";
import "../files.css";
import "../share.css";
import "../browser/browser.css";
import "./search.css";

// Het raster van zoeken op beeld (X3, Keuze 81): een eigen chunk met het gebied beeld
const BeeldResults = lazyWithAreas(() => import("./beeld/BeeldResults"), ["beeld"]);

/** De treffers van één vraag, over alle geladen pagina's. */
interface Results {
  /** De vraag (queryHref) waar ze bij horen */
  key: string;
  hits: Hit[];
  /** De laatste pagina: more, offset, after en indexing */
  last: Page;
  partial: boolean;
  namesOnly: boolean;
  /** Van de eerste pagina */
  groups: Groups | null;
}

export default function SearchPage(_: RouteProps): JSX.Element {
  useScope("browser");
  const location = useLocation();
  // ?bestand= (de viewer) hoort niet bij de vraag: key verandert alleen als de vraag verandert
  const key = queryHref(readQuery(location.search));
  const query = useMemo(() => readQuery(key.slice(key.indexOf("?") + 1)), [key]);
  const image = query.mode === "image";
  const user = useCurrentUser();
  const me = user?.id ?? null;
  const guest = isGuest(user);
  const [text, setText] = useState(query.q ?? "");
  const [results, setResults] = useState<Results | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [tags, setTags] = useState<Tag[]>([]);
  const [people, setPeople] = useState<PersonName[]>([]);
  const [placeNames, setPlaceNames] = useState<Pick<PlaceRef, "id" | "name" | "admin1">[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selection] = useState(() => new SelectionStore());
  const sel = useSelection(selection);
  const [focusIndex, setFocusIndex] = useState(-1);
  const [menu, setMenu] = useState<{ items: readonly MenuItem[]; at: { x: number; y: number } | HTMLElement; restore: Element | null } | null>(null);
  const [viewing, setViewing] = useViewerParam();
  const clip = useSyncExternalStore(clipboard.subscribe, clipboard.getSnapshot, clipboard.getSnapshot);
  const searches = useRef(0);
  const listRef = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const scrollTo = useRef<((i: number) => void) | null>(null);
  // Na "Meer laden": de focus naar de eerste nieuwe treffer
  const focusNew = useRef<number | null>(null);

  const run = useCallback(() => {
    const seq = ++searches.current;
    setError(null);
    if (image || !canSearch(query)) {
      setResults(null);
      return;
    }
    search(firstParams(query)).then(
      (page) => {
        if (seq !== searches.current) return; // intussen een nieuwere vraag
        setResults({
          key,
          hits: page.results,
          last: page,
          partial: page.partial,
          namesOnly: page.names_only,
          groups: page.groups,
        });
      },
      (e: unknown) => {
        if (seq === searches.current) setError(errorText(e));
      },
    );
  }, [query, key, image]);

  useEffect(() => {
    setText(query.q ?? "");
    selection.clear();
    setFocusIndex(-1);
    setLoadingMore(false);
    run();
  }, [run, query, selection]);

  useEffect(() => {
    api.tags().then(setTags, () => {});
  }, []);

  // Je eigen personen (Z6): hun naam op de chips en in het paneel. Een gast heeft er geen
  useEffect(() => {
    if (guest) return;
    listPeople().then((r) => setPeople(r.people), () => {});
  }, [guest]);

  // De naam van de plaats op de chip (Z3): GET /photos/places met het filter place geeft alleen die
  // plaats, van je eigen foto's. Zonder antwoord (locatie uit, geen eigen foto daar, een gast)
  // blijft er "Plaats" staan
  const place = query.place;
  useEffect(() => {
    if (guest || place === undefined) return;
    let live = true;
    listPlaces({ place }).then(
      (r) => {
        if (live) setPlaceNames(r.places);
      },
      () => {},
    );
    return () => {
      live = false;
    };
  }, [guest, place]);

  // Keuze 81: de knop "Zoeken op beeld" (/fotos) opent de stand beeld zonder vraag, met de focus
  // in het veld. Na de kop: de schil geeft na een navigatie de kop de focus (PageTitle), en dat
  // kan in hetzelfde commit na dit effect gebeuren; daarom een frame later, en alleen als de
  // focus dan nog op de kop of nergens staat. Alleen bij het openen van de pagina
  useEffect(() => {
    if (!image || query.q) return;
    const id = requestAnimationFrame(() => {
      const active = document.activeElement;
      const free = !active || active === document.body || active.id === "inhoud" || active.hasAttribute("data-page-title");
      if (free) field.current?.focus();
    });
    return () => cancelAnimationFrame(id);
  }, []);

  // Alleen de treffers van deze vraag (een antwoord van de vorige telt niet meer)
  const current = results?.key === key ? results : null;
  const hits: readonly Hit[] = current?.hits ?? [];
  const nodes = useMemo(() => hits.map((h) => h.node), [hits]);
  const ids = useMemo(() => nodes.map((n) => n.id), [nodes]);
  const hitOf = useMemo(() => new Map(hits.map((h) => [h.node.id, h])), [hits]);
  const get = useCallback((i: number) => nodes[i], [nodes]);
  const selected = useMemo(() => nodes.filter((n) => sel.selected.has(n.id)), [nodes, sel.selected]);
  const focused = focusIndex >= 0 ? nodes[focusIndex] : undefined;
  const target = selected.length ? selected : focused ? [focused] : [];

  const env: ActionEnv = { folder: null, clip, me, guest };
  const { ui, dialogs } = useBrowserUi({
    folder: null,
    me,
    // Kopiëren of verplaatsen begint in de map van het eerste resultaat
    startId: null,
    openFile: (n) => setViewing(n.id),
    // Wat veranderde, kan nu wel of niet meer bij de vraag passen: opnieuw zoeken
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

  function submit(e: FormEvent): void {
    e.preventDefault();
    // Dezelfde filters, andere woorden; dezelfde vraag nog eens: opnieuw zoeken
    const href = queryHref({ ...query, q: text.trim() || undefined });
    if (href === key) run();
    else navigate(href);
  }

  const next = current ? nextParams(query, current.last) : null;
  function loadMore(): void {
    if (!current || !next) return;
    const seq = searches.current;
    const from = current.hits.length;
    setLoadingMore(true);
    search(next).then(
      (page) => {
        if (seq !== searches.current) return;
        setLoadingMore(false);
        focusNew.current = from;
        setResults((r) =>
          r && r.key === current.key
            ? {
                ...r,
                hits: appendHits(r.hits, page.results),
                last: page,
                partial: r.partial || page.partial,
                namesOnly: r.namesOnly || page.names_only,
              }
            : r,
        );
      },
      (e: unknown) => {
        if (seq !== searches.current) return;
        setLoadingMore(false);
        setError(errorText(e));
      },
    );
  }

  // De eerste nieuwe treffer krijgt de focus (de knop is dan misschien weg)
  useEffect(() => {
    const i = focusNew.current;
    if (i === null) return;
    focusNew.current = null;
    if (i >= hits.length) return;
    setFocusIndex(i);
    scrollTo.current?.(i);
    requestAnimationFrame(() => listRef.current?.querySelector<HTMLElement>(`[data-index="${i}"]`)?.focus());
  }, [hits]);

  const files = useMemo(() => nodes.filter((n) => n.kind === "file"), [nodes]);
  const viewIndex = viewing ? files.findIndex((n) => n.id === viewing) : -1;

  const pathOf = useCallback(
    (node: FileNode) => {
      const hit = hitOf.get(node.id);
      if (!hit) return null;
      const href = parentHref(hit);
      return (
        <>
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
          {hit.snippet && hit.snippet.length > 0 && <Snippet parts={hit.snippet} />}
        </>
      );
    },
    [hitOf],
  );

  const list = current
    ? notices({ count: hits.length, partial: current.partial, namesOnly: current.namesOnly, indexing: current.last.indexing })
    : [];
  const groups = current?.groups;
  const places = (groups?.places ?? []).filter((p) => /^\d+$/.test(p.id));

  return (
    <main className="wide browser search-page">
      <PageTitle>{t("search.title")}</PageTitle>
      <form className="search-form" role="search" onSubmit={submit}>
        <input
          ref={field}
          type="search"
          aria-label={t("search.field.label")}
          aria-describedby={image ? imageDescribedBy() : undefined}
          placeholder={image ? t("search.field.placeholderImage") : t("search.field.placeholder")}
          maxLength={MAX_QUERY}
          enterKeyHint="search"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <Button type="submit" variant="primary">
          {t("search.field.submit")}
        </Button>
      </form>
      {image && <ImageHint />}
      {/* De stand (Keuze 81): ← en → kiezen, zoals bij elke groep keuzerondjes. Een gast heeft
          geen eigen foto's, dus geen zoeken op beeld */}
      {!guest && (
        <div role="radiogroup" aria-label={t("search.mode.label")} className="search-mode">
          {(["text", "image"] as const).map((mode) => (
            <label key={mode}>
              <input
                type="radio"
                name="zoekstand"
                checked={(mode === "image") === image}
                onChange={() => navigate(queryHref(withMode(query, mode)))}
              />
              {t(mode === "image" ? "search.mode.image" : "search.mode.text")}
            </label>
          ))}
        </div>
      )}
      <FilterBar query={query} tags={tags} people={people} places={placeNames} />

      {error && <p role="alert">{error}</p>}
      {image ? (
        <LazySlot>
          <BeeldResults />
        </LazySlot>
      ) : (
        <>
          {!canSearch(query) && <p className="muted">{t("search.hint")}</p>}
          <Notices list={list} />
          {groups && (groups.people.length > 0 || places.length > 0) && (
            <div className="search-groups">
              {groups.people.length > 0 && (
                <section aria-labelledby="zoek-personen">
                  <h2 id="zoek-personen">{t("search.groups.people")}</h2>
                  <ul>
                    {groups.people.map((p) => (
                      <li key={p.id}>
                        <Link to={queryHref(withPerson({}, p.id))}>{t("search.groups.count", { name: p.name, n: p.n })}</Link>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {places.length > 0 && (
                <section aria-labelledby="zoek-plaatsen">
                  <h2 id="zoek-plaatsen">{t("search.groups.places")}</h2>
                  <ul>
                    {places.map((p) => (
                      <li key={p.id}>
                        <Link to={queryHref({ place: Number(p.id) })}>{t("search.groups.count", { name: p.name, n: p.n })}</Link>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>
          )}
          {hits.length > 0 && (
            <div ref={listRef} className="browser-items">
              <FileList
                count={nodes.length}
                get={get}
                version={hits.length}
                selected={sel.selected}
                focusIndex={focusIndex}
                ariaLabel={t("search.results")}
                pathOf={pathOf}
                scrollRef={scrollTo}
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
                  selection.set(keySelect(selection.getSnapshot(), ids, ids[i], mods, ids[focusIndex]));
                }}
                onFocusRow={setFocusIndex}
              />
            </div>
          )}
          {current?.last.more &&
            (next ? (
              <p>
                <Button onClick={loadMore} busy={loadingMore}>
                  {loadingMore ? t("search.loadingMore") : t("search.more")}
                </Button>
              </p>
            ) : (
              <p className="muted">{t("search.tooMany")}</p>
            ))}
          {current && !guest && query.q && !hits.some(isPhoto) && (
            <p className="search-beeld-tip">
              {t("search.noPhoto")} <Link to={queryHref(withMode(query, "image"))}>{t("search.noPhotoAction")}</Link>
            </p>
          )}
        </>
      )}

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
