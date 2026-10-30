// Een keuzemenu van de speler (kwaliteit, audiospoor, ondertitels). Een eigen menu en niet
// ui/Menu.tsx: dat zet zich in <body>, en in volledig scherm (op de container van de speler) is
// alleen wat in de container staat te zien. Zelfde gedrag: de scope "menu" houdt de sneltoetsen
// tegen, ↑/↓/Home/End lopen langs de keuzes, Enter of Spatie kiest, Esc sluit en zet de focus
// terug op de knop, Tab sluit.

import { type JSX, type KeyboardEvent, type RefObject, useEffect, useRef } from "react";
import { pushScope } from "../../ui/keys";

export interface MenuChoice {
  key: string;
  label: string;
  checked: boolean;
  /** Een tweede regel (bijv. "kost omzetten") */
  hint?: string;
  select(): void;
}

export function PlayerMenu({
  id,
  label,
  choices,
  anchor,
  onClose,
}: {
  id: string;
  label: string;
  choices: readonly MenuChoice[];
  anchor: RefObject<HTMLButtonElement | null>;
  onClose(): void;
}): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => pushScope("menu"), []);

  useEffect(() => {
    const items = ref.current?.querySelectorAll<HTMLElement>("[role=menuitemradio]");
    const checked = ref.current?.querySelector<HTMLElement>("[aria-checked=true]");
    (checked ?? items?.[0])?.focus();
    // Klikken buiten het menu (en niet op zijn knop, die sluit hem zelf) sluit het
    const outside = (e: PointerEvent) => {
      const t = e.target as Node | null;
      if (t && (ref.current?.contains(t) || anchor.current?.contains(t))) return;
      close.current();
    };
    document.addEventListener("pointerdown", outside, true);
    return () => document.removeEventListener("pointerdown", outside, true);
  }, [anchor]);

  const done = (refocus: boolean) => {
    close.current();
    if (refocus) anchor.current?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const items = [...(ref.current?.querySelectorAll<HTMLElement>("[role=menuitemradio]") ?? [])];
    const i = items.indexOf(document.activeElement as HTMLElement);
    const go = (n: number) => items[(n + items.length) % items.length]?.focus();
    switch (e.key) {
      case "ArrowDown":
        go(i + 1);
        break;
      case "ArrowUp":
        go(i - 1);
        break;
      case "Home":
        go(0);
        break;
      case "End":
        go(items.length - 1);
        break;
      case "Escape":
        done(true);
        break;
      case "Tab":
        done(false);
        return;
      default:
        return;
    }
    e.preventDefault();
    e.stopPropagation();
  };

  return (
    <div ref={ref} id={id} role="menu" aria-label={label} className="player-menu" onKeyDown={onKeyDown}>
      {choices.map((c) => (
        <button
          key={c.key}
          type="button"
          role="menuitemradio"
          aria-checked={c.checked}
          tabIndex={-1}
          className="player-menu-item"
          onClick={() => {
            done(true);
            if (!c.checked) c.select();
          }}
        >
          <span className="player-menu-check" aria-hidden>
            {c.checked ? "✓" : ""}
          </span>
          <span>
            {c.label}
            {c.hint && <small className="player-menu-hint">{c.hint}</small>}
          </span>
        </button>
      ))}
    </div>
  );
}
