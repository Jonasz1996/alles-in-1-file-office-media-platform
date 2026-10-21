// Virtualisatie: van een lange lijst (10.000 rijen) staan alleen de zichtbare rijen (plus wat
// marge) in de DOM. Scrollen gaat standaard op het venster. De rij met de focus (keepIndex)
// blijft altijd gemount, ook buiten beeld, zodat de focus en schermlezers hem niet kwijtraken.

import {
  type CSSProperties,
  Fragment,
  type JSX,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { type KeyHandler, keyToMove, moveIndex } from "./keys";

export interface Range {
  start: number;
  /** Exclusief */
  end: number;
}

/** Welke rijen (vaste hoogte rowH) zichtbaar zijn bij deze scrollpositie, plus overscan. */
export function fixedRange(scrollTop: number, viewport: number, rowH: number, count: number, overscan: number): Range {
  if (count <= 0 || rowH <= 0) return { start: 0, end: 0 };
  const first = Math.floor(Math.max(0, scrollTop) / rowH);
  const last = Math.ceil((Math.max(0, scrollTop) + Math.max(0, viewport)) / rowH);
  return { start: Math.max(0, Math.min(count, first - overscan)), end: Math.min(count, Math.max(0, last + overscan)) };
}

/** Eerste i met offsets[i + 1] > y (binair zoeken). */
function rowAt(offsets: readonly number[], y: number): number {
  let lo = 0;
  let hi = offsets.length - 2;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (offsets[mid + 1] > y) hi = mid;
    else lo = mid + 1;
  }
  return lo;
}

/** Hetzelfde voor rijen van verschillende hoogte. offsets: prefixsommen, lengte n + 1 (offsets[0] = 0). */
export function variableRange(offsets: readonly number[], scrollTop: number, viewport: number, overscan: number): Range {
  const count = offsets.length - 1;
  if (count <= 0) return { start: 0, end: 0 };
  const top = Math.max(0, scrollTop);
  const bottom = top + Math.max(0, viewport);
  if (top >= offsets[count]) return { start: Math.max(0, count - overscan), end: count };
  const first = rowAt(offsets, top);
  let last = rowAt(offsets, Math.max(top, bottom - 0.001));
  if (offsets[last] < bottom) last += 1;
  return { start: Math.max(0, first - overscan), end: Math.min(count, last + overscan) };
}

/** keepIndex buiten [start, end): die rij moet extra gerenderd worden; anders null. */
export function extraIndex(range: Range, keepIndex: number | null | undefined, count: number): number | null {
  if (keepIndex === null || keepIndex === undefined || keepIndex < 0 || keepIndex >= count) return null;
  return keepIndex < range.start || keepIndex >= range.end ? keepIndex : null;
}

/** Prefixsommen van de rijhoogtes. */
export function prefixOffsets(count: number, height: (i: number) => number): number[] {
  const offsets = new Array<number>(count + 1);
  offsets[0] = 0;
  for (let i = 0; i < count; i++) offsets[i + 1] = offsets[i] + height(i);
  return offsets;
}

/** Waar je heen moet scrollen om rij i (top..bottom) te tonen; null = staat er al (auto). */
export function scrollTarget(
  top: number,
  bottom: number,
  viewTop: number,
  viewport: number,
  align: "auto" | "start" | "center",
): number | null {
  if (align === "start") return top;
  if (align === "center") return top - (viewport - (bottom - top)) / 2;
  if (top < viewTop) return top;
  if (bottom > viewTop + viewport) return bottom - viewport;
  return null;
}

/**
 * Waar de lijst begint binnen de inhoud van een eigen scroller (in px vanaf de bovenkant van zijn
 * scrollbare inhoud). Uit de schermposities, niet uit offsetTop: offsetTop is relatief aan de
 * dichtstbijzijnde gepositioneerde ouder, en dat is de scroller zelf vaak al (een <dialog>
 * altijd). clientTop: de bovenrand van de scroller.
 */
export function listOffsetIn(listTop: number, scrollerTop: number, clientTop: number, scrollTop: number): number {
  return listTop - scrollerTop - clientTop + scrollTop;
}

function offsetInScroller(list: HTMLElement | null, el: HTMLElement): number {
  if (!list || list === el) return 0;
  return listOffsetIn(list.getBoundingClientRect().top, el.getBoundingClientRect().top, el.clientTop, el.scrollTop);
}

export interface VirtualRowsOptions {
  count: number;
  rowHeight: number | ((i: number) => number);
  overscan?: number;
  /** Waar gescrold wordt: het venster (standaard) of een eigen element */
  scroller?: "window" | RefObject<HTMLElement | null>;
  /** Het element van de lijst zelf (voor de afstand tot de bovenkant van de scroller) */
  listRef: RefObject<HTMLElement | null>;
  keepIndex?: number | null;
}

export interface VirtualRows {
  start: number;
  end: number;
  totalHeight: number;
  offsetOf(i: number): number;
  scrollToIndex(i: number, align?: "auto" | "start" | "center"): void;
  /** keepIndex buiten [start, end): die rij wordt óók gerenderd, absoluut gepositioneerd */
  extra: number | null;
}

/** De zichtbare rijen bij de huidige scrollpositie; herberekent bij scrollen en resizen (per frame). */
export function useVirtualRows(o: VirtualRowsOptions): VirtualRows {
  const { count, rowHeight, overscan = 6, scroller = "window", listRef, keepIndex = null } = o;
  const fixed = typeof rowHeight === "number";
  const offsets = useMemo(
    () => (typeof rowHeight === "number" ? null : prefixOffsets(count, rowHeight)),
    [count, rowHeight],
  );
  const totalHeight = typeof rowHeight === "number" ? count * rowHeight : (offsets?.[count] ?? 0);

  const offsetOf = useCallback(
    (i: number) => (typeof rowHeight === "number" ? i * rowHeight : (offsets?.[Math.max(0, Math.min(i, count))] ?? 0)),
    [rowHeight, offsets, count],
  );

  /** Scrollpositie binnen de lijst en de hoogte van het zichtbare deel. */
  const measure = useCallback((): { top: number; viewport: number } => {
    const list = listRef.current;
    if (scroller === "window") {
      const listTop = list ? list.getBoundingClientRect().top + window.scrollY : 0;
      return { top: window.scrollY - listTop, viewport: window.innerHeight };
    }
    const el = scroller.current;
    if (!el) return { top: 0, viewport: 0 };
    return { top: el.scrollTop - offsetInScroller(list, el), viewport: el.clientHeight };
  }, [listRef, scroller]);

  const compute = useCallback((): Range => {
    const { top, viewport } = measure();
    if (fixed) return fixedRange(top, viewport, rowHeight as number, count, overscan);
    return variableRange(offsets ?? [0], top, viewport, overscan);
  }, [measure, fixed, rowHeight, count, overscan, offsets]);

  const [range, setRange] = useState<Range>(() => ({ start: 0, end: Math.min(count, 30) }));
  const frame = useRef(0);

  const update = useCallback(() => {
    const next = compute();
    setRange((old) => (old.start === next.start && old.end === next.end ? old : next));
  }, [compute]);

  useLayoutEffect(update, [update]);

  useEffect(() => {
    const target: Window | HTMLElement | null = scroller === "window" ? window : scroller.current;
    if (!target) return;
    const onScroll = () => {
      if (frame.current) return;
      frame.current = requestAnimationFrame(() => {
        frame.current = 0;
        update();
      });
    };
    target.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    // Een eigen scroller kan van hoogte veranderen zonder dat het venster dat doet (een paneel
    // dat opent, een dialoog die groeit)
    const observer =
      target !== window && typeof ResizeObserver === "function" ? new ResizeObserver(onScroll) : null;
    if (observer) observer.observe(target as HTMLElement);
    return () => {
      target.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      observer?.disconnect();
      if (frame.current) cancelAnimationFrame(frame.current);
      frame.current = 0;
    };
  }, [scroller, update]);

  const scrollToIndex = useCallback(
    (i: number, align: "auto" | "start" | "center" = "auto") => {
      if (i < 0 || i >= count) return;
      const top = offsetOf(i);
      const bottom = offsetOf(i + 1);
      const { top: viewTop, viewport } = measure();
      const y = scrollTarget(top, bottom, viewTop, viewport, align);
      if (y === null) return;
      if (scroller === "window") {
        const list = listRef.current;
        const listTop = list ? list.getBoundingClientRect().top + window.scrollY : 0;
        window.scrollTo({ top: Math.max(0, listTop + y) });
      } else if (scroller.current) {
        const el = scroller.current;
        el.scrollTop = Math.max(0, offsetInScroller(listRef.current, el) + y);
      }
      update();
    },
    [count, offsetOf, measure, scroller, listRef, update],
  );

  // Een kortere lijst (gefilterd): het bereik mag niet voorbij het eind wijzen
  const start = Math.min(range.start, count);
  const end = Math.min(range.end, count);
  return {
    start,
    end,
    totalHeight,
    offsetOf,
    scrollToIndex,
    extra: extraIndex({ start, end }, keepIndex, count),
  };
}

export interface VirtualListProps<T> {
  count: number;
  get(i: number): T | undefined;
  rowHeight: number;
  getKey(item: T | undefined, i: number): string;
  /** style: de positie van de rij (absoluut); geef hem door aan het rij-element */
  renderRow(item: T | undefined, i: number, style: CSSProperties): ReactNode;
  header?: ReactNode;
  overscan?: number;
  scroller?: "window" | RefObject<HTMLElement | null>;
  role?: "grid" | "listbox" | "list";
  ariaLabel: string;
  onRangeChange?(s: number, e: number): void;
  keepIndex?: number | null;
}

/** Een gevirtualiseerde lijst met vaste rijhoogte; de rijen staan absoluut in een vlak van de
 * volledige hoogte (aria-rowcount = count). */
export function VirtualList<T>(p: VirtualListProps<T>): JSX.Element {
  const listRef = useRef<HTMLDivElement>(null);
  const rows = useVirtualRows({
    count: p.count,
    rowHeight: p.rowHeight,
    overscan: p.overscan,
    scroller: p.scroller,
    listRef,
    keepIndex: p.keepIndex,
  });
  const { onRangeChange } = p;
  useEffect(() => {
    onRangeChange?.(rows.start, rows.end);
  }, [rows.start, rows.end, onRangeChange]);

  const items: ReactNode[] = [];
  const place = (i: number): CSSProperties => ({
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    height: p.rowHeight,
    transform: `translateY(${rows.offsetOf(i)}px)`,
  });
  for (let i = rows.start; i < rows.end; i++) {
    const item = p.get(i);
    items.push(<Fragment key={p.getKey(item, i)}>{p.renderRow(item, i, place(i))}</Fragment>);
  }
  if (rows.extra !== null) {
    const item = p.get(rows.extra);
    items.push(<Fragment key={p.getKey(item, rows.extra)}>{p.renderRow(item, rows.extra, place(rows.extra))}</Fragment>);
  }

  return (
    <div
      className="virtual-list"
      role={p.role}
      aria-label={p.ariaLabel}
      aria-rowcount={p.role === "grid" ? p.count : undefined}
    >
      {p.header}
      <div ref={listRef} className="virtual-list-body" style={{ height: rows.totalHeight }}>
        {items}
      </div>
    </div>
  );
}

/**
 * Eén tabstop voor een lijst of raster: de pijltjes, Home/End en PageUp/PageDown verplaatsen de
 * focus. Na scrollToIndex staat de rij er pas in het volgende frame; dan krijgt hij de focus.
 */
export function useRovingFocus(o: {
  count: number;
  index: number;
  setIndex(i: number): void;
  columns?: number;
  scrollToIndex(i: number): void;
  getElement(i: number): HTMLElement | null;
}): KeyHandler {
  const latest = useRef(o);
  useEffect(() => {
    latest.current = o;
  });
  return useCallback((e: KeyboardEvent<HTMLElement>) => {
    const { count, index, setIndex, columns = 1, scrollToIndex, getElement } = latest.current;
    const move = keyToMove(e);
    if (!move || e.ctrlKey || e.metaKey) return;
    if (columns === 1 && (move === "left" || move === "right")) return; // in een lijst niets
    const rowH = getElement(index)?.offsetHeight || 44;
    const pageRows = Math.max(1, Math.floor((typeof window === "undefined" ? 600 : window.innerHeight) / rowH) - 1);
    const next = moveIndex(index, move, count, columns, pageRows);
    if (next < 0 || next === index) return;
    e.preventDefault();
    setIndex(next);
    scrollToIndex(next);
    requestAnimationFrame(() => getElement(next)?.focus({ preventScroll: true }));
  }, []);
}

/** Hoeveel kolommen er passen (cellen van minstens minCell px) en hoe breed een cel wordt. */
export function columnsFor(width: number, minCell: number, gap = 0): { columns: number; cellWidth: number } {
  if (width <= 0 || minCell <= 0) return { columns: 1, cellWidth: Math.max(0, width) };
  const columns = Math.max(1, Math.floor((width + gap) / (minCell + gap)));
  return { columns, cellWidth: (width - gap * (columns - 1)) / columns };
}

/** Kolommen voor een raster; volgt de breedte van het element (ResizeObserver). */
export function useColumns(
  ref: RefObject<HTMLElement | null>,
  minCell: number,
  gap = 0,
): { columns: number; cellWidth: number } {
  const [size, setSize] = useState(() => ({ columns: 1, cellWidth: minCell }));
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const apply = () => {
      const next = columnsFor(el.clientWidth, minCell, gap);
      setSize((old) => (old.columns === next.columns && old.cellWidth === next.cellWidth ? old : next));
    };
    apply();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", apply);
      return () => window.removeEventListener("resize", apply);
    }
    const observer = new ResizeObserver(apply);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, minCell, gap]);
  return size;
}
