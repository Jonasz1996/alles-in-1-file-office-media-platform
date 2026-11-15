// De filters op /fotos (fase 6, van Z6; Keuze 43): personen (een combobox; meer personen: ze
// moeten er allemaal op staan), foto of video, een periode op opnamedatum, en (I1, S7) een land,
// een plaats en een camera. Een eigen chunk met het gebied photos_people. De filters blijven in de
// pagina (PhotosPage houdt ze in state), nooit in de adresbalk.
//
// Land en Plaats komen uit de plaatsen van je eigen foto's (GET /photos/places, door PhotosPage
// geladen met de maanden, zodat de balk in één keer staat); zonder plaatsen (geen places.sqlite,
// locatie "uit") staan ze er niet. Een land maakt de lijst Plaatsen korter.
//
// Toetsenbord: alles met Tab. In het veld Persoon kies je met ↓, ↑ en Enter; elke gekozen persoon
// heeft een knop om hem weer weg te halen (daarna staat de focus weer in het veld). Soort, Land en
// Plaats zijn gewone keuzelijsten (↑ en ↓). Een datum en een camera tellen bij het verlaten van
// het veld of Enter: typen geeft één vraag, niet één per teken.

import { type JSX, useEffect, useId, useMemo, useRef } from "react";
import { t } from "../../i18n";
import { Button } from "../../ui/Button";
import type { Place } from "../map/types";
import {
  cameraOrNull,
  dayOrNull,
  filterCount,
  hasFilters,
  MAX_CAMERA,
  type MediaFilter,
  NO_FILTERS,
  type PhotoFilters,
} from "./filters";
import { PersonPicker } from "./PersonPicker";
import { countryChoices, placeChoices, withCountry } from "./places";
import "./people.css";

export interface FilterBarProps {
  filters: PhotoFilters;
  onChange(f: PhotoFilters): void;
  /** De plaatsen van je eigen foto's (GET /photos/places); leeg: geen Land en Plaats */
  places?: readonly Place[];
}

const NO_PLACES: readonly Place[] = [];

export default function FilterBar({ filters, onChange, places = NO_PLACES }: FilterBarProps): JSX.Element {
  const id = useId();
  const box = useRef<HTMLFieldSetElement>(null);
  const countries = useMemo(() => countryChoices(places), [places]);
  const placeList = useMemo(() => placeChoices(places, filters.country), [places, filters.country]);

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
      {places.length > 0 && (
        <>
          <div className="field">
            <label htmlFor={`${id}-land`}>{t("photos_people.filters.country")}</label>
            <select
              id={`${id}-land`}
              value={filters.country ?? ""}
              onChange={(e) => onChange(withCountry(filters, e.target.value || null, places))}
            >
              <option value="">{t("photos_people.filters.allCountries")}</option>
              {countries.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor={`${id}-plaats`}>{t("photos_people.filters.place")}</label>
            <select
              id={`${id}-plaats`}
              value={filters.place === null ? "" : String(filters.place)}
              onChange={(e) => onChange({ ...filters, place: e.target.value ? Number(e.target.value) : null })}
            >
              <option value="">{t("photos_people.filters.allPlaces")}</option>
              {placeList.map((p) => (
                <option key={p.value} value={String(p.value)}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>
        </>
      )}
      <div className="field">
        <label htmlFor={`${id}-camera`}>{t("photos_people.filters.camera")}</label>
        <CameraInput
          id={`${id}-camera`}
          value={filters.camera}
          onCamera={(camera) => onChange({ ...filters, camera })}
        />
        <span id={`${id}-camera-hint`} className="sr-only">
          {t("photos_people.filters.cameraHint")}
        </span>
      </div>
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

/** Het veld Camera: telt bij het verlaten van het veld of Enter, zoals een datum op /fotos (één
 * vraag, niet één per teken). Wat van buiten verandert (Filters wissen), volgt het veld. */
function CameraInput({
  id,
  value,
  onCamera,
}: {
  id: string;
  value: string | null;
  onCamera(camera: string | null): void;
}): JSX.Element {
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (field.current && cameraOrNull(field.current.value) !== value) field.current.value = value ?? "";
  }, [value]);

  const commit = (v: string) => {
    const camera = cameraOrNull(v);
    if (camera !== value) onCamera(camera);
  };
  return (
    <input
      ref={field}
      id={id}
      type="text"
      defaultValue={value ?? ""}
      maxLength={MAX_CAMERA}
      autoComplete="off"
      spellCheck={false}
      aria-describedby={`${id}-hint`}
      onBlur={(e) => commit(e.currentTarget.value)}
      onKeyDown={(e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        commit(e.currentTarget.value);
      }}
    />
  );
}
