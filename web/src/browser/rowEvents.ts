// De gebeurtenissen van een lijst of raster, op het element eromheen (delegatie): zo heeft een
// rij geen eigen functies en blijft hij gelijk tussen twee renders (memo), ook bij 10.000 rijen.
// Elke rij of cel heeft data-index; daarbinnen opent [data-open] (de naam), [data-check] (het
// vinkje) selecteert erbij of eraf en [data-menu] (⋯) opent het menu.

import {
  type DragEvent,
  type FocusEvent,
  type HTMLAttributes,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
  type RefObject,
  useEffect,
  useRef,
} from "react";
import { keyToMove, moveIndex } from "../ui/keys";
import { useLongPress } from "../ui/Menu";

export interface DragCallbacks {
  onDragStart(i: number, e: DragEvent<HTMLElement>): void;
  /** i null: niet boven een rij (de lege ruimte) */
  onDragOver(i: number | null, e: DragEvent<HTMLElement>): void;
  onDrop(i: number | null, e: DragEvent<HTMLElement>): void;
  onDragLeave(): void;
  onDragEnd(): void;
}

export interface RowCallbacks {
  count: number;
  /** 1 in een lijst */
  columns: number;
  rowHeight: number;
  focusIndex: number;
  /** Een klik op een rij (niet op de naam): selecteren */
  onSelect(i: number, mods: { shift: boolean; toggle: boolean }): void;
  onToggle(i: number): void;
  onOpen(i: number): void;
  onMenu(i: number, at: { x: number; y: number } | HTMLElement): void;
  /** Met het toetsenbord naar i (Shift: een reeks, Ctrl/Cmd: alleen de focus) */
  onMove(i: number, mods: { shift: boolean; toggle: boolean }): void;
  /** Een rij kreeg de focus (klik, Tab) */
  onFocusRow(i: number): void;
  scrollToIndex(i: number): void;
  drag?: DragCallbacks;
}

function indexOf(target: EventTarget | null): number | null {
  const el = target instanceof Element ? target.closest<HTMLElement>("[data-index]") : null;
  if (!el) return null;
  const i = Number(el.dataset.index);
  return Number.isInteger(i) ? i : null;
}

/** De rij of cel i in de lijst (als hij er staat). */
export function rowElement(container: HTMLElement | null, i: number): HTMLElement | null {
  return container?.querySelector<HTMLElement>(`[data-index="${i}"]`) ?? null;
}

/** Zet de focus op rij i zodra die er staat (na het scrollen: één of twee frames later). */
export function focusRow(container: RefObject<HTMLElement | null>, i: number, tries = 4): void {
  requestAnimationFrame(() => {
    const el = rowElement(container.current, i);
    if (el) el.focus({ preventScroll: true });
    else if (tries > 0) focusRow(container, i, tries - 1);
  });
}

export function useRowEvents(
  container: RefObject<HTMLElement | null>,
  cb: RowCallbacks,
): HTMLAttributes<HTMLDivElement> {
  const latest = useRef(cb);
  latest.current = cb;
  const pressIndex = useRef<number | null>(null);
  const lastLongPress = useRef(0);
  const press = useLongPress((at) => {
    const i = pressIndex.current;
    if (i === null) return;
    lastLongPress.current = Date.now();
    latest.current.onMenu(i, at);
  });

  return {
    onClick(e: MouseEvent<HTMLDivElement>) {
      const i = indexOf(e.target);
      if (i === null) return;
      const target = e.target as Element;
      const c = latest.current;
      if (target.closest("[data-menu]")) {
        e.preventDefault();
        c.onMenu(i, target.closest<HTMLElement>("[data-menu]") as HTMLElement);
        return;
      }
      if (target.closest("[data-open]") && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        c.onOpen(i);
        return;
      }
      if (target.closest("[data-check]")) {
        c.onToggle(i);
        return;
      }
      if (target.closest("a")) return; // een tag: die navigeert zelf
      c.onSelect(i, { shift: e.shiftKey, toggle: e.ctrlKey || e.metaKey });
    },
    onDoubleClick(e: MouseEvent<HTMLDivElement>) {
      const i = indexOf(e.target);
      const target = e.target as Element;
      if (i === null || target.closest("[data-check], [data-menu], a")) return;
      latest.current.onOpen(i);
    },
    onContextMenu(e: MouseEvent<HTMLDivElement>) {
      // Een lange druk gaf al een menu (Android stuurt daarna ook nog contextmenu)
      if (Date.now() - lastLongPress.current < 800) {
        e.preventDefault();
        return;
      }
      const i = indexOf(e.target);
      if (i === null) return;
      e.preventDefault();
      latest.current.onMenu(i, { x: e.clientX, y: e.clientY });
    },
    onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
      const c = latest.current;
      const move = keyToMove(e);
      if (!move || e.altKey) return;
      if ((e.target as Element).closest("input, textarea, select")) return;
      if (c.columns === 1 && (move === "left" || move === "right")) return;
      const pageRows = Math.max(1, Math.floor(window.innerHeight / c.rowHeight) - 1);
      const next = moveIndex(c.focusIndex, move, c.count, c.columns, pageRows);
      if (next < 0) return;
      e.preventDefault();
      if (next === c.focusIndex) return;
      c.onMove(next, { shift: e.shiftKey, toggle: e.ctrlKey || e.metaKey });
      c.scrollToIndex(next);
      focusRow(container, next);
    },
    onFocus(e: FocusEvent<HTMLDivElement>) {
      const i = indexOf(e.target);
      if (i !== null && i !== latest.current.focusIndex) latest.current.onFocusRow(i);
    },
    onPointerDown(e: PointerEvent<HTMLDivElement>) {
      pressIndex.current = indexOf(e.target);
      if (pressIndex.current !== null) press.onPointerDown(e);
    },
    onPointerMove: press.onPointerMove,
    onPointerUp: press.onPointerUp,
    onPointerCancel: press.onPointerCancel,
    onClickCapture: press.onClickCapture,
    onDragStart(e: DragEvent<HTMLDivElement>) {
      const i = indexOf(e.target);
      if (i === null || !latest.current.drag) return;
      latest.current.drag.onDragStart(i, e);
    },
    onDragOver(e: DragEvent<HTMLDivElement>) {
      latest.current.drag?.onDragOver(indexOf(e.target), e);
    },
    onDrop(e: DragEvent<HTMLDivElement>) {
      latest.current.drag?.onDrop(indexOf(e.target), e);
    },
    onDragLeave(e: DragEvent<HTMLDivElement>) {
      const to = e.relatedTarget;
      if (!(to instanceof Node) || !e.currentTarget.contains(to)) latest.current.drag?.onDragLeave();
    },
    onDragEnd() {
      latest.current.drag?.onDragEnd();
    },
  };
}

/** Zo lang na de laatste scrollstap telt de lijst nog als "aan het scrollen". */
const SCROLL_IDLE_MS = 150;

/**
 * Tijdens het scrollen krijgt de lijst de klasse "is-scrolling" (geen pointer-events): dan
 * schuift de inhoud onder de muis door zonder hover-stijlen, hit-tests en muisgebeurtenissen per
 * frame. 150 ms na de laatste stap gaat hij er weer af.
 */
export function useScrollingClass(ref: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const done = () => {
      timer = null;
      ref.current?.classList.remove("is-scrolling");
    };
    const onScroll = () => {
      if (timer === null) ref.current?.classList.add("is-scrolling");
      else clearTimeout(timer);
      timer = setTimeout(done, SCROLL_IDLE_MS);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (timer !== null) clearTimeout(timer);
    };
  }, [ref]);
}
