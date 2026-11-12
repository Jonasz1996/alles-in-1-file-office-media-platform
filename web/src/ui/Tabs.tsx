import { type JSX, type KeyboardEvent, useId, useRef } from "react";
import { Link } from "../links";

export interface TabDef {
  id: string;
  label: string;
  /** Met href is elk tabblad een eigen adres (bijv. /beheer/gebruikers): dan links */
  href?: string;
  badge?: string;
}

function tabId(prefix: string, id: string): string {
  return `${prefix}-tab-${id}`;
}

function panelId(prefix: string, id: string): string {
  return `${prefix}-panel-${id}`;
}

/** De props voor het paneel van een tabblad (WAI-ARIA tabs): geef Tabs hetzelfde idPrefix. */
export function tabPanelProps(
  idPrefix: string,
  id: string,
): { id: string; role: "tabpanel"; "aria-labelledby": string; tabIndex: 0 } {
  return { id: panelId(idPrefix, id), role: "tabpanel", "aria-labelledby": tabId(idPrefix, id), tabIndex: 0 };
}

/**
 * Tabbladen. Met href zijn het links in een nav (elk een eigen pagina, aria-current); zonder
 * href een tablist met de pijltjes (WAI-ARIA tabs, automatisch activeren). Met idPrefix wijst elk
 * tabblad naar zijn paneel (aria-controls); render het paneel dan met tabPanelProps(idPrefix, id).
 */
export function Tabs({
  label,
  tabs,
  current,
  onSelect,
  idPrefix,
}: {
  label: string;
  tabs: TabDef[];
  current: string;
  onSelect?(id: string): void;
  idPrefix?: string;
}): JSX.Element {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const ownId = useId();
  const prefix = idPrefix ?? ownId;

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
          id={tabId(prefix, tab.id)}
          aria-controls={idPrefix ? panelId(idPrefix, tab.id) : undefined}
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
