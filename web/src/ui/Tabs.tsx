import { type JSX, type KeyboardEvent, useRef } from "react";
import { Link } from "../links";

export interface TabDef {
  id: string;
  label: string;
  /** Met href is elk tabblad een eigen adres (bijv. /beheer/gebruikers): dan links */
  href?: string;
  badge?: string;
}

/**
 * Tabbladen. Met href zijn het links in een nav (elk een eigen pagina, aria-current); zonder
 * href een tablist met de pijltjes (WAI-ARIA tabs, automatisch activeren).
 */
export function Tabs({
  label,
  tabs,
  current,
  onSelect,
}: {
  label: string;
  tabs: TabDef[];
  current: string;
  onSelect?(id: string): void;
}): JSX.Element {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  if (tabs.every((tab) => tab.href)) {
    return (
      <nav className="tabs" aria-label={label}>
        {tabs.map((tab) => (
          <Link
            key={tab.id}
            to={tab.href!}
            className={tab.id === current ? "tab current" : "tab"}
            aria-current={tab.id === current ? "page" : undefined}
            onClick={() => onSelect?.(tab.id)}
          >
            {tab.label}
            {tab.badge && <span className="tab-badge">{tab.badge}</span>}
          </Link>
        ))}
      </nav>
    );
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const i = tabs.findIndex((tab) => tab.id === current);
    let next = -1;
    if (e.key === "ArrowRight") next = (i + 1) % tabs.length;
    else if (e.key === "ArrowLeft") next = (i - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = tabs.length - 1;
    if (next < 0) return;
    e.preventDefault();
    onSelect?.(tabs[next].id);
    refs.current[next]?.focus();
  }

  return (
    <div className="tabs" role="tablist" aria-label={label} onKeyDown={onKeyDown}>
      {tabs.map((tab, i) => (
        <button
          key={tab.id}
          ref={(el) => {
            refs.current[i] = el;
          }}
          type="button"
          role="tab"
          className={tab.id === current ? "tab current" : "tab"}
          aria-selected={tab.id === current}
          tabIndex={tab.id === current ? 0 : -1}
          onClick={() => onSelect?.(tab.id)}
        >
          {tab.label}
          {tab.badge && <span className="tab-badge">{tab.badge}</span>}
        </button>
      ))}
    </div>
  );
}
