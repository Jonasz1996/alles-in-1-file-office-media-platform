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
import { crossApi, isNotBuilt, MAX_BATCH_IDS } from "../crossApi";
import { errorText, t } from "../i18n";
import { Button } from "../ui/Button";
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
    if (viewIndex < 0) return null;
    const s = sectionOfItem(layout, viewIndex);
    let a = s;
    let b = s;
    while (a > 0 && store.isLoaded(a - 1)) a--;
    while (b < layout.counts.length - 1 && store.isLoaded(b + 1)) b++;
    const items: PhotoItem[] = [];
    for (let x = a; x <= b; x++) items.push(...(store.section(x) ?? []));
    const viewerItems: readonly ViewerItem[] = items;
    return { first: layout.firstItem[a], a, b, items: viewerItems };
    // store.version: er is een sectie bij geladen
  }, [viewIndex, layout, store, store.version]);

  const lastViewed = useRef(-1);
  const follow = useCallback(
    (global: number) => {
      lastViewed.current = global;
      setFocus(global);
      control.current?.scrollToItem(global, "center");
    },
    [control],
  );

  const viewer =
    block && viewIndex >= 0 ? (
      <Suspense fallback={null}>
        <Viewer
          items={block.items}
          index={viewIndex - block.first}
          onIndexChange={(i) => {
            const it = block.items[i];
            if (!it) return;
            setViewing(it.id);
            follow(block.first + i);
          }}
          onClose={() => {
            const at = viewIndex;
            setViewing(null);
            follow(at);
            control.current?.focusItem(at);
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

  const openFocused = useCallback(() => {
    const it = store.item(latest.current.focus);
    if (it) setViewing(it.id);
  }, [store, setViewing]);

  const toggleFocused = useCallback(() => {
    const i = latest.current.focus;
    const it = store.item(i);
    if (it) setSelection((s) => toggled(s, i, it.id));
  }, [store]);

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
    selectAll,
    viewer,
    viewing: viewIndex >= 0,
  };
}

/** Een link maken voor een zip en die downloaden. */
export async function downloadZip(ids: string[], name: string): Promise<void> {
  if (ids.length === 0) return;
  try {
    const { url } = await crossApi.archiveLink(ids, name);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${name}.zip`;
    a.click();
  } catch (e) {
    if (isNotBuilt(e)) toast(t("photos.zipNotYet"));
    else toast(errorText(e), { kind: "error" });
  }
}

/** De balk met de selectie, onderaan in beeld. */
export function SelectionBar({
  count,
  onClear,
  children,
}: {
  count: number;
  onClear(): void;
  children: ReactNode;
}): JSX.Element | null {
  if (count === 0) return null;
  return (
    <div className="photo-selection" role="toolbar" aria-label={t("photos.select.toolbar")}>
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
