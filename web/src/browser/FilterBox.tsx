// "Filter in deze map" (Ctrl/Cmd+F; een tweede keer Ctrl/Cmd+F is weer het zoeken van de
// browser): filtert wat er geladen is op naam, zonder de server.

import type { JSX } from "react";
import { t } from "../i18n";

export const FILTER_INPUT_ID = "map-filter";

export function FilterBox({
  value,
  onChange,
  shown,
  total,
  partial,
}: {
  value: string;
  onChange(v: string): void;
  /** Hoeveel er bij het filter passen */
  shown: number;
  /** Hoeveel er geladen zijn */
  total: number;
  /** Nog niet alles geladen: het filter ziet alleen wat er al is */
  partial: boolean;
}): JSX.Element {
  return (
    <div className="filter-box">
      <input
        id={FILTER_INPUT_ID}
        type="search"
        aria-label={t("browser.filter.label")}
        placeholder={t("browser.filter.label")}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      {value.trim() && (
        <span className="muted filter-count" aria-live="polite">
          {t("browser.filter.count", { n: shown, total })}
          {partial && <> · {t("browser.filter.partial")}</>}
        </span>
      )}
    </div>
  );
}
