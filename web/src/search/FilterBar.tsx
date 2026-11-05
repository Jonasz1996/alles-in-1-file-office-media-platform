// De filters op /zoeken (fase 6, Z4; Keuze 43 en 47): de chips van wat gezet is, elk met een knop
// ×, en een paneel om er een soort, een tag of een datum bij te zetten. Elke wijziging is een
// nieuwe vraag in de URL (query.ts). Met het toetsenbord: Tab naar een chip of het paneel, Enter of
// spatie op de samenvatting opent het, spatie zet een vinkje, Enter in een datum zet hem.

import { type JSX, type KeyboardEvent, useEffect, useRef } from "react";
import type { Tag } from "../api";
import { t } from "../i18n";
import { navigate } from "../router";
import { IconButton } from "../ui/Button";
import {
  type Chip,
  chips,
  isDay,
  type PageQuery,
  queryHref,
  withDay,
  withoutChip,
  withTag,
  withType,
} from "./query";
import { SEARCH_TYPES, type SearchType } from "./types";

/** De naam van een soort ("other" kan geen sleutel zijn: een object met "other" is een meervoud). */
export function typeLabel(type: SearchType): string {
  return t(type === "other" ? "search.types.rest" : `search.types.${type}`);
}

/** Wat er op een chip staat. Personen, plaatsen, een bibliotheek en een map kent de pagina (nog)
 * niet bij naam: dan het soort filter. */
export function chipLabel(chip: Chip, tags: readonly Tag[]): string {
  switch (chip.field) {
    case "type":
      return typeLabel(chip.value as SearchType);
    case "from":
      return t("search.filters.fromChip", { day: chip.value });
    case "to":
      return t("search.filters.toChip", { day: chip.value });
    case "tag":
      return tags.find((tag) => tag.id === chip.value)?.name ?? t("search.filters.tag");
    case "person":
      return t("search.filters.person");
    case "place":
      return t("search.filters.place");
    case "country":
      return t("search.filters.country", { code: chip.value });
    case "library":
      return t("search.filters.library");
    case "camera":
      return t("search.filters.camera", { name: chip.value });
    case "in":
      return t("search.filters.folder");
  }
}

/** Een nieuwe vraag, met de filters erbij of eraf: push, zodat Terug de vorige vraag geeft. */
function go(query: PageQuery): void {
  navigate(queryHref(query));
}

export function FilterBar({ query, tags }: { query: PageQuery; tags: readonly Tag[] }): JSX.Element {
  const list = chips(query);
  const chipsRef = useRef<HTMLUListElement>(null);
  // Na × de focus op de chip die nu op die plek staat (of de vorige); zonder chips naar het paneel
  const focusAt = useRef<number | null>(null);
  const panel = useRef<HTMLElement>(null);
  const fromField = useRef<HTMLInputElement>(null);
  const toField = useRef<HTMLInputElement>(null);

  // Een datum die van buiten verandert (× op de chip, Terug): het veld volgt, behalve terwijl je
  // erin typt
  useEffect(() => {
    for (const [el, value] of [
      [fromField.current, query.from],
      [toField.current, query.to],
    ] as const) {
      if (el && document.activeElement !== el) el.value = value ?? "";
    }
  }, [query.from, query.to]);

  useEffect(() => {
    const at = focusAt.current;
    if (at === null) return;
    focusAt.current = null;
    const buttons = chipsRef.current?.querySelectorAll<HTMLButtonElement>("button") ?? [];
    const target = buttons[Math.min(at, buttons.length - 1)] ?? panel.current;
    target?.focus();
  });

  function remove(chip: Chip, index: number): void {
    focusAt.current = index;
    go(withoutChip(query, chip));
  }

  function day(field: "from" | "to", value: string): void {
    if (value === (query[field] ?? "")) return;
    if (value === "" || isDay(value)) go(withDay(query, field, value));
  }

  function dayKey(field: "from" | "to") {
    return (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter") {
        e.preventDefault();
        day(field, e.currentTarget.value);
      }
    };
  }

  return (
    <div className="search-filters">
      {list.length > 0 && (
        <ul ref={chipsRef} className="search-chips" aria-label={t("search.filters.label")}>
          {list.map((chip, i) => {
            const label = chipLabel(chip, tags);
            return (
              <li key={`${chip.field}:${chip.value}`} className="search-chip">
                <span>{label}</span>
                <IconButton
                  icon="close"
                  className="ghost"
                  label={t("search.filters.remove", { name: label })}
                  onClick={() => remove(chip, i)}
                />
              </li>
            );
          })}
        </ul>
      )}
      <details className="search-filter-panel">
        <summary ref={panel}>{t("search.filters.label")}</summary>
        <div className="search-filter-body">
          <fieldset>
            <legend>{t("search.filters.addType")}</legend>
            {SEARCH_TYPES.map((type) => {
              const on = query.type?.includes(type) ?? false;
              return (
                <label key={type} className="search-check">
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() => go(on ? withoutChip(query, { field: "type", value: type }) : withType(query, type))}
                  />
                  {typeLabel(type)}
                </label>
              );
            })}
          </fieldset>
          {tags.length > 0 && (
            <fieldset>
              <legend>{t("search.filters.addTag")}</legend>
              {tags.map((tag) => {
                const on = query.tag?.includes(tag.id) ?? false;
                return (
                  <label key={tag.id} className="search-check">
                    <input
                      type="checkbox"
                      checked={on}
                      onChange={() => go(on ? withoutChip(query, { field: "tag", value: tag.id }) : withTag(query, tag.id))}
                    />
                    {t("search.filters.tagCount", { name: tag.name, n: tag.count })}
                  </label>
                );
              })}
            </fieldset>
          )}
          <div className="search-days">
            <label>
              {t("search.filters.from")}
              <input
                ref={fromField}
                type="date"
                defaultValue={query.from ?? ""}
                onBlur={(e) => day("from", e.currentTarget.value)}
                onKeyDown={dayKey("from")}
              />
            </label>
            <label>
              {t("search.filters.to")}
              <input
                ref={toField}
                type="date"
                defaultValue={query.to ?? ""}
                onBlur={(e) => day("to", e.currentTarget.value)}
                onKeyDown={dayKey("to")}
              />
            </label>
          </div>
        </div>
      </details>
    </div>
  );
}
