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

/**
 * Mag een uitgestelde focusRow de focus nog verzetten? Alleen als hij niet intussen ergens anders
 * heen ging: nog op dezelfde plek (from), in de lijst, of nergens (body, bijv. omdat de rij met de
 * focus wegscrolde). Een menu of dialoog die in de tussentijd openging, houdt de focus.
 */
export function mayMoveFocus(
  now: Element | null,
  from: Element | null,
  container: Pick<Node, "contains"> | null,
  body: Element | null,
): boolean {
  return now === null || now === from || now === body || !!container?.contains(now);
}

/** Het nummer van de laatste vraag om de focus (focusRow of focusRowNow), voor alle lijsten samen:
 * er is maar één focus. Een eerdere focusRow die nog op een frame wacht, doet dan niets meer.
 * Anders zet hij na een snelle tweede toets de focus terug op zijn rij, onFocus zet de focusindex
 * daar ook heen, en de volgende toets begint weer van daar (Shift+PageUp vier keer achter elkaar
 * selecteerde dan soms maar drie pagina's). */
let latestRequest = 0;

/** Zet de focus op rij i zodra die er staat (na het scrollen: één of twee frames later), tenzij er
 * intussen een nieuwere vraag om de focus kwam. */
export function focusRow(
  container: RefObject<HTMLElement | null>,
  i: number,
  tries = 4,
  from: Element | null = document.activeElement,
  request: number = ++latestRequest,
): void {
  requestAnimationFrame(() => {
    if (request !== latestRequest) return;
    if (!mayMoveFocus(document.activeElement, from, container.current, document.body)) return;
    const el = rowElement(container.current, i);
    if (el) el.focus({ preventScroll: true });
    else if (tries > 0) focusRow(container, i, tries - 1, from, request);
  });
}

/** Voor een toets in de lijst: staat rij i er al, dan meteen de focus erop (dan staat de focus
 * waar de focusindex staat, en een wachtende focusRow vervalt); anders zodra hij er staat. */
export function focusRowNow(container: RefObject<HTMLElement | null>, i: number): void {
  const el = rowElement(container.current, i);
  if (!el) return focusRow(container, i);
  latestRequest += 1;
  el.focus({ preventScroll: true });
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
      focusRowNow(container, next);
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

/** Zo lang na de laatste scrollstap telt de lijst nog als "aan het scrollen". Ruim: op een
 * trage processor komen scrollstappen soms 150 tot 200 ms na elkaar, en elke keer dat de klasse
 * eraf en er weer op gaat, krijgt de rij onder de muis een stel muisgebeurtenissen en een
 * hover-stijl (net als imageGate.ts). */
const SCROLL_IDLE_MS = 250;

/**
 * Tijdens het scrollen krijgt de lijst de klasse "is-scrolling": dan ligt er een leeg vlak over
 * de rijen (browser.css), en schuift de inhoud onder de muis door zonder hover-stijlen,
 * hit-tests en muisgebeurtenissen per rij en per frame. SCROLL_IDLE_MS na de laatste stap gaat
 * hij er weer af.
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
