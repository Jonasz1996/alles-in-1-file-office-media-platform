// De lijst "Plaatsen" naast de kaart (Keuze 33): de plaatsen met het aantal foto's, voor wie
// geen kaart gebruikt of met een schermlezer werkt. Een knop per plaats zet het filter place (de
// kaart toont dan alleen die foto's); nog een keer, of "Alle plaatsen", haalt het weg. Het
// aantal telt de server per verzoek (GET /photos/places).

import type { JSX } from "react";
import { t } from "../../i18n";
import { Skeleton } from "../../ui/Spinner";
import { placeWhere } from "./text";
import type { Place } from "./types";

export interface PlacesListProps {
  /** null: nog niet geladen */
  places: readonly Place[] | null;
  error: string | null;
  chosen: number | null;
  onChoose(id: number | null): void;
}

export function PlacesList({ places, error, chosen, onChoose }: PlacesListProps): JSX.Element {
  return (
    <section className="map-places" aria-labelledby="plaatsen-kop">
      <h2 id="plaatsen-kop">{t("photos_map.places.title")}</h2>
      {error ? (
        <p role="alert">{error}</p>
      ) : places === null ? (
        <Skeleton lines={3} />
      ) : places.length === 0 ? (
        <p className="muted">{t("photos_map.places.none")}</p>
      ) : (
        <>
          <p className="muted">{t("photos_map.places.hint")}</p>
          <ul>
            <li>
              <button
                type="button"
                className="map-place"
                aria-pressed={chosen === null}
                onClick={() => onChoose(null)}
              >
                <span className="map-place-name">{t("photos_map.places.all")}</span>
              </button>
            </li>
            {places.map((p) => {
              const where = placeWhere(p);
              return (
                <li key={p.id}>
                  <button
                    type="button"
                    className="map-place"
                    aria-pressed={chosen === p.id}
                    onClick={() => onChoose(chosen === p.id ? null : p.id)}
                  >
                    <span className="map-place-name">{p.name}</span>
                    {where && <span className="muted">{where}</span>}
                    <span className="map-place-count">{t("photos_map.places.count", { n: p.n })}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
