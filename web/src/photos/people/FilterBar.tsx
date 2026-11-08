// De filters op /fotos (fase 6, van Z6; Keuze 43): personen (een combobox; meer personen: ze
// moeten er allemaal op staan), foto of video, en een periode op opnamedatum. Een eigen chunk met
// het gebied photos_people. De filters blijven in de pagina (PhotosPage houdt ze in state), nooit
// in de adresbalk.
//
// Toetsenbord: alles met Tab. In het veld Persoon kies je met ↓, ↑ en Enter; elke gekozen persoon
// heeft een knop om hem weer weg te halen (daarna staat de focus weer in het veld).

import { type JSX, useId, useRef } from "react";
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

/** Soort (foto of video) en de periode; ook in de regel van een album. */
export function MediaDates({
  id,
  type,
  from,
  to,
  onChange,
}: {
  id: string;
  type: MediaFilter | null;
  from: string | null;
  to: string | null;
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
        <input
          id={`${id}-vanaf`}
          type="date"
          value={from ?? ""}
          onChange={(e) => onChange({ from: dayOrNull(e.target.value) })}
        />
      </div>
      <div className="field">
        <label htmlFor={`${id}-tot`}>{t("photos_people.filters.to")}</label>
        <input id={`${id}-tot`} type="date" value={to ?? ""} onChange={(e) => onChange({ to: dayOrNull(e.target.value) })} />
      </div>
    </>
  );
}
