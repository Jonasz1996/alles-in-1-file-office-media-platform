// Wat de tijdlijn en een album delen: de focus, selecteren (klik, Ctrl/Cmd, Shift, Spatie,
// Mod+A, Escape), de viewer met ←/→ door de foto's, downloaden als zip en de balk met de
// selectie. Sneltoetsen in de scope van de pagina ("photos" of "album").

import {
  type JSX,
  type ReactNode,
  type RefObject,
  Suspense,
  startTransition,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { crossApi, MAX_BATCH_IDS } from "../crossApi";
import { errorText, t } from "../i18n";
import { Button } from "../ui/Button";
import { useFocusBack } from "../ui/focusBack";
import { useShortcuts } from "../ui/keys";
import { toast } from "../ui/toast";
import { useViewerParam, Viewer, type ViewerItem } from "../viewer";
import type { IdRange, PhotoItem } from "./api";
import type { GridControl } from "./Grid";
import { NO_SELECTION, type PhotoSelection, type Placed, type RangeSource, rangeIds, toggled, withRange } from "./range";
import type { SectionStore } from "./store";
import { type Layout, sectionOfItem } from "./timeline";

/**
 * De versie van de store, als state: nieuwe items (een maand die binnenkomt) tekent React als
 * transition, in stukjes, zodat een heel scherm nieuwe cellen geen lange taak wordt.
 */
export function useStoreVersion(store: SectionStore): number {
  const [version, setVersion] = useState(store.version);
  useEffect(() => {
    setVersion(store.version);
    return store.subscribe(() => startTransition(() => setVersion(store.version)));
  }, [store]);
  return version;
}

export interface PhotoBrowserOptions {
  store: SectionStore;
  layout: Layout;
  /** De ids van a tot en met b bij de server (voor een reeks over niet-geladen secties) */
  fetchIds(a: Placed, b: Placed): Promise<IdRange>;
  control: RefObject<GridControl | null>;
  scope: string;
}

/** Of de focus in het raster staat (dan zijn Enter en Spatie van het raster). */
function inGrid(): boolean {
  const a = document.activeElement;
  return !!a && !!a.closest?.(".photo-grid");
}

export function usePhotoBrowser(o: PhotoBrowserOptions) {
  const { store, layout, fetchIds, control, scope } = o;
  const [selection, setSelection] = useState<PhotoSelection>(NO_SELECTION);
  const [focus, setFocus] = useState(0);
  const [viewing, setViewing] = useViewerParam();
  const seq = useRef(0);
  const latest = useRef({ selection, focus, layout });
  latest.current = { selection, focus, layout };

  const source = useMemo<RangeSource>(
    () => ({
      item: (i) => store.item(i),
      load: async (i) => {
        const s = sectionOfItem(latest.current.layout, i);
        if (s < 0) return undefined;
        await store.load(s);
        return store.item(i);
      },
      fetchIds,
    }),
    [store, fetchIds],
  );

  const selectRange = useCallback(
    async (a: number, b: number, keep: boolean) => {
      const n = ++seq.current;
      try {
        const r = await rangeIds(source, a, b);
        if (n !== seq.current) return;
        setSelection((s) => withRange({ ids: s.ids, anchor: s.anchor ?? a }, r.ids, keep));
        if (r.truncated) toast(t("photos.select.truncated", { max: MAX_BATCH_IDS }));
      } catch (e) {
        if (n === seq.current) toast(errorText(e), { kind: "error" });
      }
    },
    [source],
  );

  const onCellClick = useCallback(
    (i: number, m: { shift: boolean; toggle: boolean; check: boolean }) => {
      const { selection: sel } = latest.current;
      const it = store.item(i);
      if (m.shift) {
        const anchor = sel.anchor ?? i;
        setSelection((s) => ({ ids: s.ids, anchor }));
        void selectRange(anchor, i, m.toggle);
        return;
      }
      if (!it) return;
      if (m.toggle || m.check || sel.ids.size > 0) {
        setSelection((s) => toggled(s, i, it.id));
        return;
      }
      setViewing(it.id);
    },
    [store, selectRange, setViewing],
  );

  const onShiftMove = useCallback(
    (from: number, to: number, keep: boolean) => {
      const anchor = latest.current.selection.anchor ?? from;
      setSelection((s) => ({ ids: s.ids, anchor }));
      void selectRange(anchor, to, keep);
    },
    [selectRange],
  );

  const clear = useCallback(() => {
    seq.current++;
    setSelection(NO_SELECTION);
  }, []);

  /** De focus terug op de cel met de focus in het raster (na de selectiebalk) */
  const focusGrid = useCallback(() => control.current?.focusItem(latest.current.focus), [control]);

  const selectAll = useCallback(() => {
    const total = latest.current.layout.total;
    if (total > 0) {
      setSelection((s) => ({ ids: s.ids, anchor: 0 }));
      void selectRange(0, total - 1, false);
    }
  }, [selectRange]);

  // --- de viewer: de geladen secties rond de foto die open is ---
  const viewIndex = viewing ? store.indexOf(viewing) : -1;
  const block = useMemo(() => {
    if (viewIndex < 0) {
      // Zijn maand (of het album) kreeg een ander aantal en wordt opnieuw geladen: de viewer
      // blijft met de oude items open (geen nieuwe start van een video, de zoom blijft), en gaat
      // pas dicht als het nieuwe er is zonder deze foto
      const held = viewing ? store.held(viewing) : null;
      if (!held) return null;
      const s = held.section;
      const items: readonly ViewerItem[] = held.items;
      return { first: layout.firstItem[s] ?? 0, a: s, b: s, items, index: held.items.findIndex((it) => it.id === viewing) };
    }
    const s = sectionOfItem(layout, viewIndex);
    let a = s;
    let b = s;
    while (a > 0 && store.isLoaded(a - 1)) a--;
    while (b < layout.counts.length - 1 && store.isLoaded(b + 1)) b++;
    const items: PhotoItem[] = [];
    for (let x = a; x <= b; x++) items.push(...(store.section(x) ?? []));
    const viewerItems: readonly ViewerItem[] = items;
    return { first: layout.firstItem[a], a, b, items: viewerItems, index: viewIndex - layout.firstItem[a] };
    // store.version: er is een sectie bij geladen
  }, [viewIndex, viewing, layout, store, store.version]);
  const showing = block !== null && block.index >= 0;

  const lastViewed = useRef(-1);
  const follow = useCallback(
    (global: number) => {
      lastViewed.current = global;
      setFocus(global);
      control.current?.scrollToItem(global, "center");
    },
    [control],
  );

  // Na het sluiten de focus op de cel van de laatst bekeken foto. Pas als de viewer weg is: de
  // viewer (P4) zet bij het verdwijnen zelf de focus terug op wat hij bij het openen vond, en
  // zolang de modale dialoog open is, kan niets daarbuiten de focus krijgen.
  const refocus = useRef<number | null>(null);
  useEffect(() => {
    if (showing || refocus.current === null) return;
    const at = refocus.current;
    refocus.current = null;
    control.current?.focusItem(at);
  }, [showing, control]);

  const viewer =
    block && showing ? (
      <Suspense fallback={null}>
        <Viewer
          items={block.items}
          index={block.index}
          onIndexChange={(i) => {
            const it = block.items[i];
            if (!it) return;
            setViewing(it.id);
            follow(block.first + i);
          }}
          onClose={() => {
            const at = block.first + block.index;
            refocus.current = at;
            setViewing(null);
            follow(at);
          }}
          onNeedMore={(direction) => {
            const s = direction === 1 ? block.b + 1 : block.a - 1;
            if (s < 0 || s >= layout.counts.length) return;
            store
              .load(s)
              .then((items) => {
                const it = direction === 1 ? items[0] : items[items.length - 1];
                if (!it) return;
                setViewing(it.id);
                follow(store.indexOf(it.id));
              })
              .catch((e: unknown) => toast(errorText(e), { kind: "error" }));
          }}
        />
      </Suspense>
    ) : null;

  // Enter of Spatie op de cel met de focus. Laadt die cel nog (een plaatshouder: de tijdlijn
  // tekent de cellen al voordat zijn maand er is), dan zodra de maand er is, als de focus dan
  // nog op die cel in het raster staat. Anders ging de toets verloren.
  const withFocused = useCallback(
    (run: (i: number, it: PhotoItem) => void) => {
      const i = latest.current.focus;
      const it = store.item(i);
      if (it) {
        run(i, it);
        return;
      }
      const s = sectionOfItem(latest.current.layout, i);
      if (s < 0) return;
      store
        .load(s)
        .then(() => {
          const loaded = store.item(i);
          if (loaded && latest.current.focus === i && inGrid()) run(i, loaded);
        })
        .catch(() => {
          /* de store meldt het al (onError) */
        });
    },
    [store],
  );

  const openFocused = useCallback(() => withFocused((_, it) => setViewing(it.id)), [withFocused, setViewing]);

  const toggleFocused = useCallback(
    () => withFocused((i, it) => setSelection((s) => toggled(s, i, it.id))),
    [withFocused],
  );

  useShortcuts(
    [
      { combo: "enter", label: t("photos.keys.open"), run: openFocused, when: inGrid },
      { combo: " ", label: t("photos.keys.toggle"), run: toggleFocused, when: inGrid },
      { combo: "mod+a", label: t("photos.keys.selectAll"), run: selectAll },
      {
        combo: "escape",
        label: t("photos.keys.clear"),
        run: clear,
        when: () => latest.current.selection.ids.size > 0,
      },
    ],
    { scope },
  );

  return {
    selection,
    setSelection,
    focus,
    setFocus,
    onCellClick,
    onShiftMove,
    clear,
    focusGrid,
    selectAll,
    viewer,
    viewing: showing,
  };
}

/** Een link maken voor een zip (P2: archive/link) en die downloaden. */
export async function downloadZip(ids: string[], name: string): Promise<void> {
  if (ids.length === 0) return;
  try {
    const { url } = await crossApi.archiveLink(ids, name);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${name}.zip`;
    a.click();
  } catch (e) {
    toast(errorText(e), { kind: "error" });
  }
}

interface SelectionBarProps {
  count: number;
  onClear(): void;
  /** De balk verdwijnt (niets meer geselecteerd) terwijl de focus erin stond: usePhotoBrowser().focusGrid */
  onGone?(): void;
  children: ReactNode;
}

/** De balk met de selectie, onderaan in beeld. */
export function SelectionBar(p: SelectionBarProps): JSX.Element | null {
  if (p.count === 0) return null;
  return <Bar {...p} />;
}

function Bar({ count, onClear, onGone, children }: SelectionBarProps): JSX.Element {
  const bar = useRef<HTMLDivElement>(null);
  useFocusBack(bar, onGone);
  return (
    <div ref={bar} className="photo-selection" role="toolbar" aria-label={t("photos.select.toolbar")}>
      <span className="photo-selection-count" aria-live="polite">
        {t("photos.select.count", { n: count })}
      </span>
      {children}
      <Button variant="ghost" icon="close" onClick={onClear}>
        {t("photos.select.clear")}
      </Button>
    </div>
  );
}
