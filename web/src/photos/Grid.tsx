// Het raster van foto's (tijdlijn en album): gevirtualiseerd over het venster, met kopregels per
// maand, één tabstop (roving focus over de cellen) en de pijltjes, Home/End en PageUp/PageDown.
// Rijen hebben role=row met aria-rowindex; de kop van een maand bevat een columnheader. Een cel
// bestaat ook als de foto nog niet geladen is (een plaatshouder), zodat de focus overal heen kan.

import {
  type JSX,
  type KeyboardEvent,
  memo,
  type MouseEvent,
  type MutableRefObject,
  type RefObject,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { fmt, t } from "../i18n";
import { Icon } from "../ui/Icon";
import { keyToMove } from "../ui/keys";
import { type PhotoItem, wallLabel } from "./api";
import type { PhotoSelection } from "./range";
import { useWindowRows } from "./rows";
import { type Layout, moveInLayout, rowInfo, rowOfItem, sectionsInRows } from "./timeline";

/** Een cel is minstens zo breed (px), met deze ruimte ertussen. */
export const MIN_CELL = 160;
export const GAP = 4;

/** Wat de pagina met het raster kan doen (scrollen, focus zetten, de positie lezen). */
export interface GridControl {
  scrollToItem(i: number, align?: "auto" | "start" | "center"): void;
  focusItem(i: number): void;
  /** De bovenkant van het raster in het document (px) */
  listTop(): number;
}

export interface GridProps {
  layout: Layout;
  rowHeight: number;
  headerHeight: number;
  /** De geladen items van sectie s (een nieuwe array als die sectie veranderde), of undefined */
  section(s: number): readonly PhotoItem[] | undefined;
  sectionLabel?(s: number): string;
  selection: PhotoSelection;
  focus: number;
  setFocus(i: number): void;
  label: string;
  /** De secties die nu (bijna) in beeld zijn: die moeten geladen worden */
  onVisible(sections: number[]): void;
  /** Klik op cel i (shift: een reeks, toggle: Ctrl/Cmd); check: op het vinkje */
  onCellClick(i: number, m: { shift: boolean; toggle: boolean; check: boolean }): void;
  /** Shift met een pijltje: de reeks van from tot to */
  onShiftMove(from: number, to: number, keep: boolean): void;
  control: MutableRefObject<GridControl | null>;
}

/** Zoveel thumbnails per frame (elk start een verzoek en een <img>). */
const IMAGES_PER_FRAME = 6;
/** Sneller dan dit (px/ms) scrollen: dan nog geen thumbnails laden. */
export const FAST_SCROLL = 1.5;
/** Zo lang (ms) na het laatste snelle scrollen komen de thumbnails. */
const SETTLE_MS = 250;
/** Zo lang (ms) na het laatste scrollen reageert het raster weer op de muis. */
const POINTER_MS = 150;

/**
 * De wachtrij van thumbnails. Een cel die er een wil tonen, vraagt een plek; de wachtrij geeft
 * er een paar per frame, zodat een heel scherm nieuwe cellen geen lange taak wordt. Tijdens snel
 * scrollen (een fling, de scrubber, End) geeft hij niets: de cellen die alleen voorbijkomen,
 * laden dan geen afbeelding die toch niemand ziet. Dat staat buiten React: omschakelen laat
 * geen enkele cel opnieuw tekenen.
 */
const imageQueue = new Set<() => void>();
let imageFrame = 0;
let scrollingFast = false;

function runImages(): void {
  imageFrame = 0;
  if (scrollingFast) return; // na het snelle scrollen gaat hij verder
  let n = 0;
  for (const grant of imageQueue) {
    imageQueue.delete(grant);
    grant();
    if (++n >= IMAGES_PER_FRAME) break;
  }
  scheduleImages();
}

function scheduleImages(): void {
  if (!imageFrame && !scrollingFast && imageQueue.size > 0) imageFrame = requestAnimationFrame(runImages);
}

/** Vraagt een plek voor een thumbnail; grant komt in een volgend frame. Geeft de functie om
 * de vraag in te trekken (de cel verdwijnt eerder). */
export function requestImage(grant: () => void): () => void {
  imageQueue.add(grant);
  scheduleImages();
  return () => {
    imageQueue.delete(grant);
  };
}

/**
 * Volgt het scrollen van het venster: snel scrollen houdt de wachtrij van thumbnails tegen, en
 * tijdens elk scrollen krijgt het raster data-scrolling (meteen, buiten React om): dan geen
 * hover en geen muisgebeurtenissen, die de browser anders bij elk frame opnieuw uitrekent.
 */
export function useScrollWatch(grid: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    let lastY = window.scrollY;
    let lastT = performance.now();
    let settle: ReturnType<typeof setTimeout> | null = null;
    let pointer: ReturnType<typeof setTimeout> | null = null;
    const onScroll = () => {
      const now = performance.now();
      const y = window.scrollY;
      const v = Math.abs(y - lastY) / Math.max(1, now - lastT);
      lastY = y;
      lastT = now;
      const el = grid.current;
      if (el && el.dataset.scrolling === undefined) el.dataset.scrolling = "";
      if (pointer !== null) clearTimeout(pointer);
      pointer = setTimeout(() => {
        pointer = null;
        if (grid.current) delete grid.current.dataset.scrolling;
      }, POINTER_MS);
      if (v > FAST_SCROLL) scrollingFast = true;
      if (scrollingFast) {
        if (settle !== null) clearTimeout(settle);
        settle = setTimeout(() => {
          settle = null;
          scrollingFast = false;
          scheduleImages();
        }, SETTLE_MS);
      }
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (settle !== null) clearTimeout(settle);
      if (pointer !== null) clearTimeout(pointer);
      scrollingFast = false;
      scheduleImages();
    };
  }, [grid]);
}

export function PhotoGrid(p: GridProps): JSX.Element {
  const gridRef = useRef<HTMLDivElement>(null);
  useScrollWatch(gridRef);
  const listRef = useRef<HTMLDivElement>(null);
  const { layout, rowHeight, headerHeight } = p;
  const keepRow = p.focus >= 0 && p.focus < layout.total ? rowOfItem(layout, p.focus) : null;
  const rows = useWindowRows({ offsets: layout.offsets, overscan: 3, listRef, keepIndex: keepRow });

  const element = useCallback(
    (i: number) => listRef.current?.querySelector<HTMLElement>(`[data-index="${i}"]`) ?? null,
    [],
  );

  const control = useMemo<GridControl>(
    () => ({
      scrollToItem(i, align = "auto") {
        const r = rowOfItem(layout, i);
        if (r >= 0) rows.scrollToIndex(r, align);
      },
      focusItem(i) {
        // Staat de cel er al, dan meteen; anders na de volgende render (de rij komt er dan bij)
        const now = element(i);
        if (now) now.focus({ preventScroll: true });
        else requestAnimationFrame(() => element(i)?.focus({ preventScroll: true }));
      },
      listTop() {
        const list = listRef.current;
        return list ? list.getBoundingClientRect().top + window.scrollY : 0;
      },
    }),
    [layout, rows.scrollToIndex, element],
  );
  p.control.current = control;

  // Laden wat in beeld komt; alleen melden als het iets anders is dan de vorige keer
  const { onVisible } = p;
  // (een nieuwe indeling telt altijd: dan kan er iets verouderd zijn)
  const lastVisible = useRef<{ layout: Layout | null; key: string }>({ layout: null, key: "" });
  useEffect(() => {
    const sections = sectionsInRows(layout, rows.start, rows.end);
    const key = sections.join(",");
    if (key === lastVisible.current.key && layout === lastVisible.current.layout) return;
    lastVisible.current = { layout, key };
    onVisible(sections);
  }, [layout, rows.start, rows.end, onVisible]);

  const latest = useRef(p);
  useEffect(() => {
    latest.current = p;
  });

  const onKeyDown = useCallback(
    (e: KeyboardEvent<HTMLElement>) => {
      const { focus, setFocus, onShiftMove } = latest.current;
      const move = keyToMove(e);
      if (!move) return;
      const pageRows = Math.max(1, Math.floor(window.innerHeight / rowHeight) - 1);
      const next = moveInLayout(layout, focus, move, pageRows);
      if (next < 0) return;
      e.preventDefault();
      if (e.shiftKey) onShiftMove(focus < 0 ? next : focus, next, e.ctrlKey || e.metaKey);
      if (next === focus) return;
      setFocus(next);
      control.scrollToItem(next);
      control.focusItem(next);
    },
    [layout, rowHeight, control],
  );

  const onClick = useCallback((e: MouseEvent<HTMLElement>) => {
    const target = e.target as HTMLElement;
    const cell = target.closest<HTMLElement>("[data-index]");
    if (!cell) return;
    const i = Number(cell.dataset.index);
    latest.current.setFocus(i);
    latest.current.onCellClick(i, {
      shift: e.shiftKey,
      toggle: e.ctrlKey || e.metaKey,
      check: !!target.closest(".photo-check"),
    });
  }, []);

  // Krijgt een cel de focus op een andere manier (Tab, een schermlezer, de viewer die sluit),
  // dan is dat de nieuwe plek van de tabstop
  const onFocus = useCallback((e: { target: EventTarget }) => {
    const cell = (e.target as HTMLElement).closest?.<HTMLElement>("[data-index]");
    if (!cell) return;
    const i = Number(cell.dataset.index);
    if (i !== latest.current.focus) latest.current.setFocus(i);
  }, []);

  const out: JSX.Element[] = [];
  const add = (r: number) => {
    const info = rowInfo(layout, r);
    const top = rows.offsetOf(r);
    if (info.kind === "header") {
      out.push(
        <HeaderRow
          key={`h${info.section}`}
          r={r}
          top={top}
          height={headerHeight}
          label={p.sectionLabel?.(info.section) ?? ""}
          columns={layout.columns}
        />,
      );
      return;
    }
    const focusHere = p.focus >= info.first && p.focus < info.first + info.count ? p.focus : -1;
    out.push(
      <PhotoRow
        key={`r${info.first}`}
        r={r}
        top={top}
        height={rowHeight}
        first={info.first}
        count={info.count}
        columns={layout.columns}
        items={p.section(info.section)}
        start={info.first - layout.firstItem[info.section]}
        selection={p.selection}
        focus={focusHere}
        tabbable={focusHere >= 0 || (p.focus < 0 && info.first === 0)}
      />,
    );
  };
  for (let r = rows.start; r < rows.end; r++) add(r);
  if (rows.extra !== null) add(rows.extra);

  return (
    <div
      ref={gridRef}
      className="photo-grid"
      role="grid"
      aria-label={p.label}
      aria-rowcount={layout.rowCount}
      aria-colcount={layout.columns}
      aria-multiselectable="true"
      data-selecting={p.selection.ids.size > 0 || undefined}
      onKeyDown={onKeyDown}
      onClick={onClick}
      onFocus={onFocus}
    >
      <div ref={listRef} className="photo-grid-body" style={{ height: rows.totalHeight }}>
        {out}
      </div>
    </div>
  );
}

const HeaderRow = memo(function HeaderRow(p: {
  r: number;
  top: number;
  height: number;
  label: string;
  columns: number;
}): JSX.Element {
  return (
    <div
      role="row"
      aria-rowindex={p.r + 1}
      className="photo-month"
      style={{ transform: `translateY(${p.top}px)`, height: p.height }}
    >
      <div role="columnheader" aria-colspan={p.columns} className="photo-month-label">
        {p.label}
      </div>
    </div>
  );
});

const PhotoRow = memo(function PhotoRow(p: {
  r: number;
  top: number;
  height: number;
  first: number;
  count: number;
  columns: number;
  items: readonly PhotoItem[] | undefined;
  /** Waar de rij begint binnen zijn sectie */
  start: number;
  selection: PhotoSelection;
  focus: number;
  tabbable: boolean;
}): JSX.Element {
  const cells: JSX.Element[] = [];
  for (let k = 0; k < p.count; k++) {
    const i = p.first + k;
    const it = p.items?.[p.start + k];
    cells.push(
      <PhotoCell
        key={i}
        i={i}
        it={it}
        selected={!!it && p.selection.ids.has(it.id)}
        tab={p.focus === i || (p.focus < 0 && p.tabbable && k === 0)}
      />,
    );
  }
  return (
    <div
      role="row"
      aria-rowindex={p.r + 1}
      className="photo-row"
      style={{
        transform: `translateY(${p.top}px)`,
        height: p.height,
        gridTemplateColumns: `repeat(${p.columns}, minmax(0, 1fr))`,
      }}
    >
      {cells}
    </div>
  );
});

/** Eén cel; tekent alleen opnieuw als zijn eigen foto, selectie of tabstop verandert. */
const PhotoCell = memo(function PhotoCell({
  i,
  it,
  selected,
  tab,
}: {
  i: number;
  it: PhotoItem | undefined;
  selected: boolean;
  tab: boolean;
}): JSX.Element {
  // Eenmaal getoond, blijft de thumbnail staan (ook als er daarna weer snel gescrold wordt).
  // Hij komt via de wachtrij: een paar per frame, zodat een heel scherm geen lange taak wordt
  const [ready, setReady] = useState(false);
  const want = !!it?.thumb && !ready;
  useEffect(() => (want ? requestImage(() => setReady(true)) : undefined), [want]);
  const src = ready ? (it?.thumb ?? null) : null;
  return (
    <div
      role="gridcell"
      data-index={i}
      tabIndex={tab ? 0 : -1}
      aria-selected={selected}
      aria-label={it ? cellLabel(it) : t("common.loading")}
      className="photo-cell"
    >
      {src ? (
        <img src={src} alt="" decoding="async" draggable={false} />
      ) : (
        <span className="photo-placeholder" aria-hidden="true">
          {it && !it.thumb && <Icon name={it.media === "video" ? "video" : "image"} size={24} />}
        </span>
      )}
      {it?.media === "video" && (
        <span className="photo-badge" aria-hidden="true">
          {it.duration !== null && fmt.duration(it.duration)}
        </span>
      )}
      {it && <span className="photo-check" aria-hidden="true" />}
    </div>
  );
});

/** De naam van een cel (per item één keer opgemaakt: datums opmaken kost tijd bij scrollen). */
const labels = new WeakMap<PhotoItem, string>();

function cellLabel(it: PhotoItem): string {
  let label = labels.get(it);
  if (label === undefined) {
    const date = wallLabel(it.taken_at);
    label =
      it.media === "video"
        ? t("photos.cell.video", { name: it.name, date })
        : t("photos.cell.photo", { name: it.name, date });
    labels.set(it, label);
  }
  return label;
}
