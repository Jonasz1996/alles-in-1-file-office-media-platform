// Menu's volgens WAI-ARIA: role=menu, de focus gaat met de pijltjes langs de items (roving),
// Home/End, zoeken op de eerste letters, → en ← voor een submenu, Escape sluit en zet de focus
// terug. Het menu komt in de top layer van een open dialoog als die er is, anders in body.

import {
  type JSX,
  type KeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { Icon, type IconName } from "./Icon";
import { formatCombo, pushScope } from "./keys";
import { itemRole, type MenuItem, nextIndex, placeMenu, placeSubmenu, typeahead } from "./menu";

export type { MenuItem } from "./menu";

export type MenuCloseReason = "select" | "escape" | "outside" | "tab";

export interface MenuProps {
  items: readonly MenuItem[];
  anchor: { x: number; y: number } | HTMLElement;
  label: string;
  onClose(reason: MenuCloseReason): void;
  /** Welk item eerst de focus krijgt (ArrowUp op de knop: het laatste) */
  initialFocus?: "first" | "last";
}

function boxOf(el: Element) {
  const r = el.getBoundingClientRect();
  return { x: r.left, y: r.top, width: r.width, height: r.height };
}

function viewport() {
  return { width: document.documentElement.clientWidth || window.innerWidth, height: window.innerHeight };
}

/** Waar het menu in komt: de open dialoog rond het anker (de top layer), anders body. */
function hostFor(anchor: MenuProps["anchor"]): HTMLElement {
  const from = anchor instanceof HTMLElement ? anchor : document.activeElement;
  return (from instanceof Element ? from.closest<HTMLElement>("dialog[open]") : null) ?? document.body;
}

/** Een menu (en zijn submenu's). De ouder toont het zolang het open is. */
export function Menu({ items, anchor, label, onClose, initialFocus = "first" }: MenuProps): JSX.Element {
  const root = useRef<HTMLDivElement>(null);
  const [host] = useState(() => hostFor(anchor));
  const latest = useRef(onClose);
  useEffect(() => {
    latest.current = onClose;
  });

  // Een klik of tik ergens anders sluit het menu
  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      if (!(e.target instanceof Node) || !root.current || root.current.contains(e.target)) return;
      // Op de knop zelf: die sluit het menu met zijn eigen klik
      if (anchor instanceof HTMLElement && anchor.contains(e.target)) return;
      latest.current("outside");
    };
    document.addEventListener("pointerdown", onDown, true);
    const popScope = pushScope("menu"); // sneltoetsen van de pagina even niet
    return () => {
      document.removeEventListener("pointerdown", onDown, true);
      popScope();
    };
  }, []);

  return createPortal(
    <div ref={root} className="menu-root">
      <MenuLevel
        items={items}
        label={label}
        place={(size) =>
          anchor instanceof HTMLElement ? placeMenu(boxOf(anchor), size, viewport()) : placeMenu(anchor, size, viewport())
        }
        initialFocus={initialFocus}
        onClose={onClose}
        nested={false}
      />
    </div>,
    host,
  );
}

function MenuLevel({
  items,
  label,
  place,
  initialFocus,
  onClose,
  nested,
}: {
  items: readonly MenuItem[];
  label: string;
  place(size: { width: number; height: number }): { x: number; y: number };
  initialFocus: "first" | "last";
  onClose(reason: MenuCloseReason): void;
  nested: boolean;
}) {
  const list = useRef<HTMLUListElement>(null);
  const refs = useRef<(HTMLLIElement | null)[]>([]);
  const [active, setActive] = useState(() => (initialFocus === "last" ? items.length - 1 : 0));
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const [submenu, setSubmenu] = useState<number | null>(null);
  const typed = useRef({ text: "", at: 0 });
  const id = useId();

  useLayoutEffect(() => {
    const el = list.current;
    if (!el) return;
    setPos(place({ width: el.offsetWidth, height: el.offsetHeight }));
    // alleen bij het openen: daarna blijft het menu staan
  }, []);

  useEffect(() => {
    if (!pos || submenu !== null) return;
    const el = refs.current[active];
    el?.focus({ preventScroll: true });
    // Een lang menu scrolt zelf (overflow); het menu is fixed, dus alleen het menu scrolt mee
    el?.scrollIntoView?.({ block: "nearest" });
  }, [active, pos, submenu]);

  function activate(i: number) {
    const item = items[i];
    if (!item) return;
    if (item.items?.length) {
      if (!item.disabled) setSubmenu(i);
      return;
    }
    if (item.disabled) return;
    onClose("select");
    item.onSelect?.();
  }

  function onKeyDown(e: KeyboardEvent<HTMLUListElement>) {
    e.stopPropagation(); // niet ook naar een menu erboven
    const key = e.key;
    if (key === "ArrowDown" || key === "ArrowUp") {
      e.preventDefault();
      setActive((cur) => nextIndex(items.length, cur, key === "ArrowDown" ? 1 : -1));
    } else if (key === "Home" || key === "PageUp") {
      e.preventDefault();
      setActive(0);
    } else if (key === "End" || key === "PageDown") {
      e.preventDefault();
      setActive(items.length - 1);
    } else if (key === "Enter" || key === " ") {
      e.preventDefault();
      activate(active);
    } else if (key === "ArrowRight") {
      e.preventDefault();
      if (items[active]?.items?.length && !items[active].disabled) setSubmenu(active);
    } else if (key === "ArrowLeft") {
      e.preventDefault();
      if (nested) onClose("escape");
    } else if (key === "Escape") {
      e.preventDefault();
      onClose("escape");
    } else if (key === "Tab") {
      e.preventDefault();
      onClose("tab");
    } else if (key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const now = Date.now();
      const text = now - typed.current.at > 500 ? key : typed.current.text + key;
      typed.current = { text, at: now };
      const hit = typeahead(items, active, text);
      if (hit >= 0) {
        e.preventDefault();
        setActive(hit);
      }
    }
  }

  return (
    <>
      <ul
        ref={list}
        role="menu"
        aria-label={label}
        className="menu"
        tabIndex={-1}
        style={pos ? { left: pos.x, top: pos.y } : { left: 0, top: 0, visibility: "hidden" }}
        onKeyDown={onKeyDown}
      >
        {items.map((item, i) => {
          const reasonId = item.disabled && item.reason ? `${id}-r${i}` : undefined;
          return [
            item.separatorBefore && i > 0 ? <li key={`${item.id}-sep`} role="separator" className="menu-separator" /> : null,
            <li
              key={item.id}
              ref={(el) => {
                refs.current[i] = el;
              }}
              role={itemRole(item)}
              aria-checked={item.checked}
              aria-disabled={item.disabled || undefined}
              aria-haspopup={item.items?.length ? "menu" : undefined}
              aria-expanded={item.items?.length ? submenu === i : undefined}
              aria-describedby={reasonId}
              title={item.disabled ? item.reason : undefined}
              tabIndex={-1}
              className={["menu-item", item.danger ? "danger" : null, item.disabled ? "disabled" : null]
                .filter(Boolean)
                .join(" ")}
              onClick={() => activate(i)}
              onPointerMove={() => {
                if (active !== i) setActive(i);
              }}
            >
              <span className="menu-check" aria-hidden="true">
                {item.checked ? <Icon name="check" size={16} /> : item.icon ? <Icon name={item.icon} size={16} /> : null}
              </span>
              <span className="menu-label">{item.label}</span>
              {item.shortcut && (
                <kbd className="menu-shortcut" aria-hidden="true">
                  {formatCombo(item.shortcut)}
                </kbd>
              )}
              {item.items?.length ? (
                <span className="menu-sub" aria-hidden="true">
                  <Icon name="chevron-right" size={16} />
                </span>
              ) : null}
              {reasonId && (
                <span id={reasonId} className="sr-only">
                  {item.reason}
                </span>
              )}
            </li>,
          ];
        })}
      </ul>
      {submenu !== null && items[submenu]?.items && (
        <MenuLevel
          key={items[submenu].id}
          items={items[submenu].items!}
          label={items[submenu].label}
          place={(size) => {
            const el = refs.current[submenu];
            return el ? placeSubmenu(boxOf(el), size, viewport()) : { x: 0, y: 0 };
          }}
          initialFocus="first"
          nested
          onClose={(reason) => {
            setSubmenu(null);
            if (reason === "escape") {
              const el = refs.current[submenu];
              el?.focus({ preventScroll: true });
              el?.scrollIntoView?.({ block: "nearest" });
            }
            else onClose(reason); // gekozen, Tab of ergens anders geklikt: alles dicht
          }}
        />
      )}
    </>
  );
}

/** Een knop die een menu opent (aria-haspopup). Escape of een keuze zet de focus terug op de knop. */
export function MenuButton({
  items,
  label,
  icon,
  children,
  className,
}: {
  items: readonly MenuItem[] | (() => readonly MenuItem[]);
  label: string;
  icon?: IconName;
  children?: ReactNode;
  className?: string;
}): JSX.Element {
  const button = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState<{ items: readonly MenuItem[]; focus: "first" | "last" } | null>(null);

  const show = (focus: "first" | "last") => setOpen({ items: typeof items === "function" ? items() : items, focus });

  return (
    <>
      <button
        ref={button}
        type="button"
        className={["menu-button", children ? null : "icon-button", className].filter(Boolean).join(" ")}
        aria-haspopup="menu"
        aria-expanded={open !== null}
        aria-label={children ? undefined : label}
        title={children ? undefined : label}
        onClick={() => (open ? setOpen(null) : show("first"))}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            show("first");
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            show("last");
          }
        }}
      >
        {icon && <Icon name={icon} size={20} />}
        {children}
      </button>
      {open && button.current && (
        <Menu
          items={open.items}
          anchor={button.current}
          label={label}
          initialFocus={open.focus}
          onClose={(reason) => {
            setOpen(null);
            if (reason !== "outside") button.current?.focus();
          }}
        />
      )}
    </>
  );
}

// --- lang indrukken (aanraakscherm) ---

export interface PointerHandlers {
  onPointerDown(e: ReactPointerEvent<HTMLElement>): void;
  onPointerMove(e: ReactPointerEvent<HTMLElement>): void;
  onPointerUp(e: ReactPointerEvent<HTMLElement>): void;
  onPointerCancel(e: ReactPointerEvent<HTMLElement>): void;
  /** Na een lange druk de klik die erop volgt niet doorlaten */
  onClickCapture(e: ReactMouseEvent<HTMLElement>): void;
}

interface PressState {
  timer: ReturnType<typeof setTimeout> | null;
  x: number;
  y: number;
  fired: boolean;
}

function pressHandlers(
  state: PressState,
  fire: (at: { x: number; y: number }, target: HTMLElement) => void,
  ms: number,
  tolerance: number,
): PointerHandlers {
  const stop = () => {
    if (state.timer !== null) clearTimeout(state.timer);
    state.timer = null;
  };
  return {
    onPointerDown(e) {
      if (e.pointerType === "mouse") return; // muis: rechtsklik
      stop();
      state.fired = false;
      state.x = e.clientX;
      state.y = e.clientY;
      const target = e.currentTarget;
      state.timer = setTimeout(() => {
        state.timer = null;
        state.fired = true;
        fire({ x: state.x, y: state.y }, target);
      }, ms);
    },
    onPointerMove(e) {
      if (state.timer !== null && Math.hypot(e.clientX - state.x, e.clientY - state.y) > tolerance) stop();
    },
    onPointerUp: stop,
    onPointerCancel: stop,
    onClickCapture(e) {
      if (!state.fired) return;
      state.fired = false;
      e.preventDefault();
      e.stopPropagation();
    },
  };
}

/** Lang indrukken (500 ms, hooguit 8 px bewegen) met een vinger of pen. */
export function useLongPress(
  cb: (at: { x: number; y: number }) => void,
  o?: { ms?: number; tolerance?: number },
): PointerHandlers {
  const latest = useRef(cb);
  useEffect(() => {
    latest.current = cb;
  });
  const state = useRef<PressState>({ timer: null, x: 0, y: 0, fired: false });
  useEffect(
    () => () => {
      if (state.current.timer !== null) clearTimeout(state.current.timer);
    },
    [],
  );
  return pressHandlers(state.current, (at) => latest.current(at), o?.ms ?? 500, o?.tolerance ?? 8);
}

// --- contextmenu ---

export interface DOMHandlers extends PointerHandlers {
  onContextMenu(e: ReactMouseEvent<HTMLElement>): void;
  onKeyDown(e: KeyboardEvent<HTMLElement>): void;
}

/**
 * Een contextmenu bij een rij of cel: rechtsklik, Shift+F10 of de menutoets, of lang indrukken.
 * get(t) geeft de items (of null: dan het gewone menu van de browser). element zet je ergens
 * in de pagina neer.
 */
export function useContextMenu<T>(
  get: (t: T) => readonly MenuItem[] | null,
  label: string,
): {
  bind(t: T): DOMHandlers;
  open(t: T, at: { x: number; y: number } | HTMLElement): void;
  element: ReactNode;
} {
  const [state, setState] = useState<{
    items: readonly MenuItem[];
    at: { x: number; y: number } | HTMLElement;
    restore: Element | null;
  } | null>(null);
  const latest = useRef(get);
  useEffect(() => {
    latest.current = get;
  });
  const press = useRef<PressState>({ timer: null, x: 0, y: 0, fired: false });
  const lastLongPress = useRef(0);

  const open = useCallback((t: T, at: { x: number; y: number } | HTMLElement) => {
    const items = latest.current(t);
    if (!items?.length) return false;
    setState({ items, at, restore: document.activeElement });
    return true;
  }, []);

  const bind = (t: T): DOMHandlers => {
    const handlers = pressHandlers(
      press.current,
      (at) => {
        lastLongPress.current = Date.now();
        open(t, at);
      },
      500,
      8,
    );
    return {
      ...handlers,
      onContextMenu(e) {
        // Een lange druk gaf al een menu (Android stuurt daarna ook nog contextmenu)
        if (Date.now() - lastLongPress.current < 800) {
          e.preventDefault();
          return;
        }
        if (open(t, { x: e.clientX, y: e.clientY })) e.preventDefault();
      },
      onKeyDown(e) {
        if ((e.key === "F10" && e.shiftKey) || e.key === "ContextMenu") {
          if (open(t, e.currentTarget)) {
            e.preventDefault();
            e.stopPropagation();
          }
        }
      },
    };
  };

  const element = state ? (
    <Menu
      items={state.items}
      anchor={state.at}
      label={label}
      onClose={(reason) => {
        const restore = state.restore;
        setState(null);
        if (reason !== "outside" && restore instanceof HTMLElement && restore.isConnected) restore.focus();
      }}
    />
  ) : null;

  return {
    bind,
    open: (t, at) => {
      open(t, at);
    },
    element,
  };
}
