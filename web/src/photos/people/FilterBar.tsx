// De filters op /fotos (fase 6, van Z6; Keuze 43): personen (een combobox; meer personen: ze
// moeten er allemaal op staan), foto of video, en een periode op opnamedatum. Een eigen chunk met
// het gebied photos_people. De filters blijven in de pagina (PhotosPage houdt ze in state), nooit
// in de adresbalk.
//
// Toetsenbord: alles met Tab. In het veld Persoon kies je met ↓, ↑ en Enter; elke gekozen persoon
// heeft een knop om hem weer weg te halen (daarna staat de focus weer in het veld). Een datum telt
// bij het verlaten van het veld of Enter (MediaDates).

import { type JSX, useEffect, useId, useRef } from "react";
import { t } from "../../i18n";
import { Button } from "../../ui/Button";
import { dayOrNull, filterCount, hasFilters, type MediaFilter, NO_FILTERS, type PhotoFilters } from "./filters";
import { PersonPicker } from "./PersonPicker";
import "./people.css";

export interface FilterBarProps {
  filters: PhotoFilters;
  onChange(f: PhotoFilters): void;
}

export default function FilterBar({ filters, onChange }: FilterBarProps): JSX.Element {
  const id = useId();
  const box = useRef<HTMLFieldSetElement>(null);

  return (
    <fieldset ref={box} className="photo-filters">
      <legend>{t("photos_people.filters.title")}</legend>
      <PersonPicker
        label={t("photos_people.filters.person")}
        hint={t("photos_people.filters.personHint")}
        selected={filters.person}
        onChange={(person) => onChange({ ...filters, person })}
      />
      <MediaDates
        id={id}
        type={filters.type}
        from={filters.from}
        to={filters.to}
        onChange={(c) => onChange({ ...filters, ...c })}
      />
      {hasFilters(filters) && (
        <>
          <span className="muted filter-count" role="status">
            {t("photos_people.filters.active", { n: filterCount(filters) })}
          </span>
          <Button
            variant="link"
            onClick={() => {
              onChange(NO_FILTERS);
              requestAnimationFrame(() => box.current?.querySelector<HTMLInputElement>("[role=combobox]")?.focus());
            }}
          >
            {t("photos_people.filters.clear")}
          </Button>
        </>
      )}
    </fieldset>
  );
}

/**
 * Soort (foto of video) en de periode; ook in de regel van een album. Op /fotos telt een datum pas
 * bij het verlaten van het veld of Enter, zoals op /zoeken: typen geeft dan één vraag en niet één
 * per cijfer van het jaar (review golf 2, web B). live: elke geldige datum meteen, voor een
 * formulier met Opslaan (de regel van een album; Enter slaat daar op).
 */
export function MediaDates({
  id,
  type,
  from,
  to,
  live = false,
  onChange,
}: {
  id: string;
  type: MediaFilter | null;
  from: string | null;
  to: string | null;
  live?: boolean;
  onChange(c: { type?: MediaFilter | null; from?: string | null; to?: string | null }): void;
}): JSX.Element {
  return (
    <>
      <div className="field">
        <label htmlFor={`${id}-soort`}>{t("photos_people.filters.type")}</label>
        <select
          id={`${id}-soort`}
          value={type ?? ""}
          onChange={(e) => onChange({ type: (e.target.value || null) as MediaFilter | null })}
        >
          <option value="">{t("photos_people.filters.all")}</option>
          <option value="image">{t("photos_people.filters.image")}</option>
          <option value="video">{t("photos_people.filters.video")}</option>
        </select>
      </div>
      <div className="field">
        <label htmlFor={`${id}-vanaf`}>{t("photos_people.filters.from")}</label>
        <DayInput id={`${id}-vanaf`} value={from} live={live} onDay={(d) => onChange({ from: d })} />
      </div>
      <div className="field">
        <label htmlFor={`${id}-tot`}>{t("photos_people.filters.to")}</label>
        <DayInput id={`${id}-tot`} value={to} live={live} onDay={(d) => onChange({ to: d })} />
      </div>
    </>
  );
}

function DayInput({
  id,
  value,
  live,
  onDay,
}: {
  id: string;
  value: string | null;
  live: boolean;
  onDay(d: string | null): void;
}): JSX.Element {
  const field = useRef<HTMLInputElement>(null);
  // Een datum die van buiten verandert (Filters wissen): het veld volgt, ook met de focus erin
  useEffect(() => {
    if (!live && field.current) field.current.value = value ?? "";
  }, [value, live]);

  if (live) {
    return <input id={id} type="date" value={value ?? ""} onChange={(e) => onDay(dayOrNull(e.target.value))} />;
  }
  const commit = (v: string) => {
    const d = dayOrNull(v);
    if (d !== value) onDay(d);
  };
  return (
    <input
      ref={field}
      id={id}
      type="date"
      defaultValue={value ?? ""}
      onBlur={(e) => commit(e.currentTarget.value)}
      onKeyDown={(e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        commit(e.currentTarget.value);
      }}
    />
  );
}
